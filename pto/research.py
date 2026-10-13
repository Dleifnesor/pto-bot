"""Looks up the media behind a trend: what it is, its world, and its characters (Wikipedia, plus a franchise's Fandom
wiki when it's licensed). The notes make concept prompts specific; for unlicensed media they also tell the IP filter
exactly which character names, titles and actors to keep out of listings."""
from __future__ import annotations

import re
import time
from urllib.parse import quote

import httpx

from . import config, log
from .llm.ollama import Ollama
from .trends import normalize
from .trends.wikipedia import PEOPLE, UA

_log = log.get("research")

WP_SEARCH = "https://en.wikipedia.org/w/rest.php/v1/search/page"
WP_PAGE = "https://en.wikipedia.org/w/rest.php/v1/page/{t}"
MEDIA = re.compile(r"\b(film|movie|series|miniseries|television|tv|sitcom|anime|manga|manhwa|webtoon|video game|game|"
                   r"novel|comics?|cartoon|franchise|musical|animated|character)\b", re.I)
SEARCH_HINT = {"steam": "video game", "game": "video game", "movie": "film", "tv": "TV series"}
KIND_WORDS = {"steam": re.compile(r"\bgame\b", re.I), "game": re.compile(r"\bgame\b", re.I),
              "movie": re.compile(r"\bfilm\b", re.I), "tv": re.compile(r"\b(series|television|sitcom|anime)\b", re.I)}
NO_RESEARCH = ("seasonal", "evergreen", "events")  # curated generic topics, not media
SECTION = re.compile(r"^(premise|plot|synopsis|story|setting|overview|characters?|main characters|cast|main cast|"
                     r"voice cast|cast and characters|characters and cast|gameplay|premise and setting)$", re.I)
APPEARANCE = re.compile(r"^(appearance|physical appearance|description|design|appearance and personality)$", re.I)
LOOK_FIELDS = re.compile(r"^\s*\|\s*(species|race|gender|sex|hair|hair colou?r|eyes|eye colou?r|skin|fur|height|"
                         r"clothing|attire|outfit|weapons?|colou?rs?)\s*=\s*(.+)$", re.I | re.M)
STOPWORDS = {"the", "a", "an", "of", "and", "season", "part", "film", "movie", "game", "series", "edition", "tv"}
LEAD_CHARS, SECTION_CHARS, TOTAL_CHARS = 1200, 1800, 5500

EXTRACT_SYSTEM = """You read an encyclopedia article about a game, film, show, book or other media and pull out what a
merch designer needs to know. Use only the article text, plus widely known facts about how its characters look.
Never invent characters. If you don't know what a character looks like, leave "look" empty.
Keep it short: premise = one sentence; setting = under 25 words; tone = a few words; motifs = 3-8 short noun phrases
(objects, creatures, places, symbols); characters = the main individual named characters (not groups or factions),
role in 1-3 words, look = under 30 words of visual detail (species, build, hair, outfit, colors, props);
names = other proper nouns unique to this work (places, factions, creatures, items, e.g. "Hogwarts", "Quidditch");
real_people = actors, voice actors, directors and creators named in the text.
Return JSON matching the schema."""


def extract_schema(max_chars: int) -> dict:
    char = {"type": "object", "properties": {"name": {"type": "string"}, "role": {"type": "string"},
                                             "look": {"type": "string"}}, "required": ["name", "role", "look"]}
    return {
        "type": "object",
        "properties": {
            "franchise": {"type": "string"},
            "kind": {"type": "string", "enum": ["game", "film", "tv", "anime", "book", "comic", "other"]},
            "premise": {"type": "string"},
            "setting": {"type": "string"},
            "tone": {"type": "string"},
            "motifs": {"type": "array", "items": {"type": "string"}},
            "characters": {"type": "array", "items": char, "maxItems": max_chars},
            "names": {"type": "array", "items": {"type": "string"}},
            "real_people": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["franchise", "kind", "premise", "setting", "tone", "motifs", "characters", "names", "real_people"],
    }


# ------------------------------------------------------------------------------------------------ wikitext

_TEMPLATE = re.compile(r"\{\{([^{}]*)\}\}")
_KEEP_TEMPLATE = {"cast listing", "nowrap", "nobr", "ubl", "unbulleted list", "plainlist", "flatlist", "small",
                  "div col", "hlist", "vgy", "film date"}
_SUBST = {"'s": "'s", "'": "'", "ndash": "-", "mdash": "-", "snd": " - ", "spaced ndash": " - ", "nbsp": " "}


def _template(m: re.Match) -> str:
    name, _, rest = m.group(1).partition("|")
    name = name.strip().lower()
    if name in _SUBST:
        return _SUBST[name]
    if name in _KEEP_TEMPLATE:
        return " ".join(p.split("=", 1)[-1] for p in rest.split("|"))
    if name in ("lang", "transl", "nihongo"):
        parts = rest.split("|")
        return parts[1] if name == "lang" and len(parts) > 1 else parts[0]
    return ""


def _links(s: str) -> str:
    for _ in range(6):  # innermost links first; file/category links hold nested caption links
        before = s
        s = re.sub(r"\[\[(?:File|Image|Category):[^\[\]]*\]\]", "", s, flags=re.I)
        s = re.sub(r"\[\[(?!(?:File|Image|Category):)[^\[\]|]*\|([^\[\]]*)\]\]", r"\1", s, flags=re.I)
        s = re.sub(r"\[\[(?!(?:File|Image|Category):)([^\[\]|]*)\]\]", r"\1", s, flags=re.I)
        if s == before:
            break
    return s


def strip_wikitext(s: str) -> str:
    """Wiki markup -> readable plain text (good enough for an LLM, not for display)."""
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>", "", s, flags=re.S | re.I)
    s = re.sub(r"<gallery[^>]*>.*?</gallery>", "", s, flags=re.S | re.I)
    s = re.sub(r"\{\|.*?\|\}", "", s, flags=re.S)  # tables
    s = _links(s)  # before templates: piped links inside template params would split on "|"
    for _ in range(12):  # innermost templates first
        s, n = _TEMPLATE.subn(_template, s)
        if not n:
            break
    s = _links(s)
    s = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", s)
    s = re.sub(r"\[https?://\S+\]", "", s)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("'''", "").replace("''", "").replace("&nbsp;", " ").replace("&mdash;", "-").replace("&ndash;", "-")
    s = re.sub(r"^[*#:;]+\s*", "- ", s, flags=re.M)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n", s).strip()


_HEADING = re.compile(r"^(={2,6})\s*(.*?)\s*\1\s*$|^<h([2-6])[^>]*>(.*?)</h\3>\s*$", re.M | re.I)


def sections(wikitext: str) -> list[tuple[int, str, str]]:
    """Split raw wikitext into (level, heading, body); the lead is (1, "", ...)."""
    out, last, level, title = [], 0, 1, ""
    for m in _HEADING.finditer(wikitext):
        out.append((level, title, wikitext[last:m.start()]))
        level = len(m.group(1)) if m.group(1) else int(m.group(3))
        title = strip_wikitext(m.group(2) or m.group(4) or "")
        last = m.end()
    out.append((level, title, wikitext[last:]))
    return out


def pick_sections(wikitext: str, pattern: re.Pattern = SECTION, per_section: int = SECTION_CHARS,
                  total: int = TOTAL_CHARS) -> str:
    """Lead + the sections matching `pattern` (with their subsections), as plain text."""
    parts, inside = [], None
    for level, title, body in sections(wikitext):
        if inside is not None and level <= inside:
            inside = None
        if not title or pattern.match(title):
            inside = level if title else None
            text = strip_wikitext(body)
            if text:  # the lead's later paragraphs are usually development and reception history
                parts.append(f"## {title}\n" + text[:per_section] if title else text[:LEAD_CHARS])
        elif inside is not None:
            text = strip_wikitext(body)
            if text:
                parts.append(f"### {title}\n" + text[:per_section // 2])
    return "\n\n".join(parts)[:total]


# ------------------------------------------------------------------------------------------------ lookups

def _words(s: str) -> set[str]:
    return {w for w in normalize(re.sub(r"\(.*?\)", "", s)).split() if w not in STOPWORDS and len(w) > 1}


def title_overlap(query: str, page_title: str) -> float:
    q = _words(query)
    return len(q & _words(page_title)) / len(q) if q else 0.0


def is_media(description: str) -> bool:
    who = re.sub(r"\b(single|multi|two|four)[- ]?player\b", "", description or "", flags=re.I)  # not a "player"
    who = re.sub(r"\(\d{4}\s*[–-]\s*\d{0,4}\)", "", who)  # a show's run "(2016–2025)", not a lifespan
    return bool(MEDIA.search(description or "")) and not PEOPLE.search(who)


def _franchise_hit(texts: list[str], f: config.LicensedFranchise) -> bool:
    hay = " " + " ".join(normalize(t) for t in texts if t) + " "
    return any(normalize(t) and f" {normalize(t)} " in hay for t in f.terms)


def license_for(trend: dict, notes: dict | None, cfg: config.Config) -> config.LicensedFranchise | None:
    """The licensed franchise this trend (or the page its research found) belongs to, if any."""
    texts = [trend.get("title") or "", *map(str, (trend.get("context") or [])[:2])]
    if notes:
        texts += [notes.get("page") or "", notes.get("franchise") or ""]
    return next((f for f in cfg.research.licensed_franchises if _franchise_hit(texts, f)), None)


def _get(c: httpx.Client, url: str, **params) -> httpx.Response:
    """GET with a short backoff on 429: Wikimedia throttles bursts from small clients."""
    r = c.get(url, params=params or None)
    for attempt in range(3):
        if r.status_code != 429:
            break
        retry = r.headers.get("retry-after", "")
        time.sleep(min(30.0, float(retry)) if retry.isdigit() else 2.0 * (attempt + 1))
        r = c.get(url, params=params or None)
    r.raise_for_status()
    return r


def find_page(c: httpx.Client, trend: dict) -> tuple[str, str] | None:
    """(page title, short description) of the Wikipedia article about the trend's media, or None."""
    title, src = trend["title"], trend.get("source") or ""
    if src == "wikipedia":
        desc = str((trend.get("context") or [""])[0])
        return (title, desc) if is_media(desc) else None
    kind = (trend.get("meta") or {}).get("kind") or src
    query = f"{title} {SEARCH_HINT[kind]}" if kind in SEARCH_HINT else title
    best, best_score = None, 0.0
    for p in _get(c, WP_SEARCH, q=query, limit=5).json().get("pages", []):
        desc, page = p.get("description") or "", p.get("title", "")
        if not is_media(desc) or title_overlap(title, page) < 0.6:
            continue
        score = title_overlap(title, page) + title_overlap(page, title)  # exact title beats spin-offs
        score += 0.1 if kind in KIND_WORDS and KIND_WORDS[kind].search(desc) else 0  # the series, not its franchise page
        if score > best_score:
            best, best_score = (page, desc), score
    return best


def page_source(c: httpx.Client, title: str) -> str:
    return _get(c, WP_PAGE.format(t=quote(title.replace(" ", "_"), safe=""))).json().get("source") or ""


def _wiki_api(wiki: str) -> str:
    wiki = wiki.strip().rstrip("/")
    if wiki.endswith("api.php"):
        return wiki if "://" in wiki else "https://" + wiki
    return "https://" + re.sub(r"^https?://", "", wiki) + "/api.php"


def appearance_from_wikitext(wikitext: str, max_len: int = 360) -> str:
    """Infobox look fields + the Appearance section of a Fandom character page ("" if it has neither).
    The lead is skipped on purpose: it's usually lore, which makes a worse prompt than no description."""
    fields = [f"{k.strip().lower()}: {v}" for k, raw in LOOK_FIELDS.findall(wikitext)
              if (v := strip_wikitext(raw).replace("\n", ", ").strip(" ,"))]
    body = next((b for _, t, b in sections(wikitext) if APPEARANCE.match(t)), "")
    text = strip_wikitext(body).replace("\n", " ")
    look = "; ".join(fields[:6])
    return (f"{look}. {text}" if look and text else look or text)[:max_len].strip()


def fandom_look(c: httpx.Client, wiki: str, name: str) -> str:
    api = _wiki_api(wiki)
    params = {"action": "parse", "page": name, "prop": "wikitext", "redirects": 1, "format": "json", "formatversion": 2}
    j = _get(c, api, **params).json()
    if "error" in j:
        hits = _get(c, api, action="query", list="search", srsearch=name, srlimit=1, format="json", formatversion=2).json()
        found = [h for h in (hits.get("query") or {}).get("search") or [] if title_overlap(name, h["title"]) >= 0.5]
        if not found:  # a search hit about something else would describe the wrong character
            return ""
        j = _get(c, api, **{**params, "page": found[0]["title"]}).json()
    return appearance_from_wikitext((j.get("parse") or {}).get("wikitext") or "")


def _clean_list(items, n: int = 12) -> list[str]:
    return [s for s in (str(i).strip() for i in items or []) if s][:n]


def lookup(trend: dict, cfg: config.Config | None = None, llm: Ollama | None = None,
           client: httpx.Client | None = None) -> dict:
    """Research notes for a trend ({} when it isn't about a work we can find)."""
    cfg = cfg or config.load()
    if not cfg.research.enabled:
        return {}
    lic = license_for(trend, None, cfg)
    if trend.get("source") in NO_RESEARCH and not lic:
        return {}
    llm = llm or Ollama(cfg.ollama)
    c = client or httpx.Client(timeout=20, headers=UA, follow_redirects=True)
    try:
        found = find_page(c, trend)
        if not found:
            return {}
        page, desc = found
        text = pick_sections(page_source(c, page))
        if len(text) < 200:
            return {}
        n = cfg.research.max_characters
        raw = llm.chat_json(EXTRACT_SYSTEM, f'Article "{page}" ({desc}):\n\n{text}', schema=extract_schema(n),
                            temperature=0.1)
        chars = []
        for ch in (raw.get("characters") or [])[:n]:
            if isinstance(ch, dict) and str(ch.get("name") or "").strip():
                chars.append({"name": str(ch["name"]).strip()[:60], "role": str(ch.get("role") or "")[:80],
                              "look": str(ch.get("look") or "")[:300]})
        notes = {
            "page": page, "description": desc, "url": f"https://en.wikipedia.org/wiki/{quote(page.replace(' ', '_'))}",
            "franchise": str(raw.get("franchise") or "").strip()[:80], "kind": str(raw.get("kind") or "other"),
            "premise": str(raw.get("premise") or "")[:300], "setting": str(raw.get("setting") or "")[:240],
            "tone": str(raw.get("tone") or "")[:160], "motifs": _clean_list(raw.get("motifs"), 8),
            "characters": chars, "names": _clean_list(raw.get("names"), 20),
            "real_people": _clean_list(raw.get("real_people"), 20),
        }
        lic = license_for(trend, notes, cfg)
        if lic and lic.wiki:
            for ch in chars:
                try:
                    if look := fandom_look(c, lic.wiki, ch["name"]):
                        ch["look"] = "; ".join(x for x in (look, ch["look"]) if x)[:420]
                        ch["look_source"] = lic.wiki
                except (httpx.HTTPError, ValueError) as e:
                    _log.debug("fandom lookup %s/%s failed: %s", lic.wiki, ch["name"], e)
                time.sleep(0.2)
        notes["license"] = lic.name if lic else None
        _log.info("researched %r -> %s (%d characters%s)", trend["title"], page, len(chars),
                  f", licensed: {lic.name}" if lic else "")
        return notes
    finally:
        if client is None:
            c.close()


def protected_names(notes: dict) -> list[str]:
    """Names an unlicensed design must never use: the work, its franchise, characters and its own places/things."""
    names = [re.sub(r"\s*\(.*?\)\s*$", "", notes.get("page") or ""), notes.get("franchise") or ""]
    names += [ch["name"] for ch in notes.get("characters") or []] + list(notes.get("names") or [])
    return [n.strip() for n in names if len(n.strip()) >= 3]


def brief(notes: dict) -> str:
    """Research notes formatted for the concept prompt."""
    if not notes:
        return ""
    head = f'Research on "{notes.get("page")}" ({notes.get("description") or notes.get("kind")}'
    head += f', franchise: {notes["franchise"]})' if notes.get("franchise") else ")"
    lines = [head + ":"]
    for key, label in (("premise", "premise"), ("setting", "setting"), ("tone", "tone")):
        if notes.get(key):
            lines.append(f"- {label}: {notes[key]}")
    if notes.get("motifs"):
        lines.append("- iconic motifs: " + ", ".join(notes["motifs"]))
    if notes.get("characters"):
        lines.append("- characters:")
        for ch in notes["characters"]:
            role = f" ({ch['role']})" if ch.get("role") else ""
            look = f": {ch['look']}" if ch.get("look") else ""
            lines.append(f"  - {ch['name']}{role}{look}")
    return "\n".join(lines)
