"""Trend scoring and trend -> design concept generation, including the IP/sensitivity guardrails."""
from __future__ import annotations

import datetime as dt
import re

from .. import config, log, research
from ..trends import normalize
from .ollama import Ollama

_log = log.get("concepts")

SCORE_SYSTEM = """You are a print-on-demand merch strategist. You rate how well trending topics can be turned into
sticker / t-shirt designs that buyers in the US, UK, Canada and Australia would actually purchase.

Score each trend 0-10:
- 0: tragedies, deaths, disasters, accidents, crime, violence, illness, war, scandals, lawsuits, sports matches and
  fixtures (anything like "team A vs team B"), sports leagues and clubs, stock/crypto prices, weather alerts, product
  recalls, TV channels and news programs, adult content{politics_rule}.
- 1-2: a real person's name on its own (celebrity, athlete, executive): we can't put real people on products.
- 1-3: news with no visual or emotional hook, very local topics, things nobody wants on a sticker.
- 4-6: decent hook but narrow or short-lived.
- 7-10: memes, catchy phrases, cute animals, hobbies, aesthetics, seasonal moments, relatable humor, broad fandom
  *vibes* that can be expressed generically.
Trends about trademarked shows, games, brands or celebrities are scored only on whether a GENERIC design inspired by
their mood/genre could sell, because we will never use their names, logos, characters or likenesses.
Exception: trends marked [LICENSED fan art: X] belong to a franchise we are officially allowed to make fan art of;
score those on how well fan art of its characters would sell right now.
Return JSON: {{"scores": [{{"id": <trend id>, "score": <0-10>, "reason": "<max 12 words>"}}]}}"""

SCORE_SCHEMA = {
    "type": "object",
    "properties": {"scores": {"type": "array", "items": {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "score": {"type": "integer"}, "reason": {"type": "string"}},
        "required": ["id", "score", "reason"]}}},
    "required": ["scores"],
}

CONCEPT_SYSTEM = """You are a senior designer for a print-on-demand shop that sells die-cut stickers, t-shirts, hoodies,
mugs and phone cases. You turn a trending topic into original, sellable design concepts.

HARD RULES (a design that breaks these is thrown away):
{ip_rules}
3. Nothing hateful, sexual, violent, or mocking real tragedies.
4. The illustration itself contains NO text. Any words go in "slogan" and are typeset separately.

FIELDS for each design:
- style: one of {styles}
- subject: 15-40 word image-generator prompt describing ONLY the illustration: the subject, pose, expression, key props
  and colors.{subject_rule} Do not mention stickers, backgrounds, borders, text or art style keywords (those are added automatically).
- slogan: short original phrase, max 6 words / 32 characters. REQUIRED for meme_text, optional otherwise ("" for none).
  Funny, punny or relatable beats generic.
- text_layout: "none" if no slogan, otherwise "top", "bottom" or "top_bottom" (top_bottom splits the slogan in two).
- title: natural, search-friendly listing title, max 60 characters, no product words like "sticker" or "shirt".
- tags: 12-15 lowercase search tags (1-3 words each): niche, subject, style, audience, occasion, gift ideas.
- description: 1-2 friendly sentences for the listing.
- poster_subject: 20-45 word prompt for a separate full-background portrait wall-art version of the same idea
  (include an environment/scene).

Make {n_hint}. Make them clearly different from each other (different style and angle) and different from these
recent listings: {recent}.
Return JSON matching the schema."""

IP_RULES_GENERIC = """1. Never use or depict: brand / company / product names, sports teams or leagues, real people (celebrities, athletes,
   politicians, influencers), fictional character names, titles of shows / movies / games / songs / books, trademarked
   catchphrases, song lyrics, logos, or recognizable copyrighted characters and likenesses.
2. If the trend involves any of those, list every protected name in "protected_terms" and PIVOT to a generic theme that
   captures the mood, genre or aesthetic instead (e.g. a trending 80s sci-fi show -> "retro 80s small-town monster mystery,
   synthwave neon, kids on bikes"). The protected names must not appear anywhere else in your output.
   For games, movies and shows, design for their fans without copying the work: capture the genre, setting, era,
   mood and the fan experience (e.g. "cozy farming-sim life", "cosmic-horror deep sea", "heist crew getaway van",
   "one more run roguelike humor", "midnight premiere popcorn"). Never draw their characters, creatures, costumes,
   vehicles, logos or quote their lines; invent original archetypes instead.
   When research notes are given, use them to understand the world, its themes and what fans love, then invent
   ORIGINAL archetypes: never reuse a listed character's name, signature look, outfit or color scheme."""

IP_RULES_LICENSED = """1. This trend belongs to {franchise}, which our shop is officially licensed to make fan art of. You MAY feature
   its characters from the research notes by name (in subject, title, tags and description) and draw them in their
   canonical look. Brand guidelines: {guidelines}
2. Still never use or depict: logos or wordmarks, real people (actors, voice actors, creators) or their likenesses,
   other franchises, brands or their characters, quoted dialogue or song lyrics. List any such protected names in
   "protected_terms" (never the licensed franchise or its characters) and keep them out of everything else."""

SUBJECT_RULE_LICENSED = (" Name the character and spell out their look (species, build, hair, outfit, colors, props),"
                         " because the image model may not know them.")


def concept_schema(styles: list[str]) -> dict:
    design = {
        "type": "object",
        "properties": {
            "style": {"type": "string", "enum": styles},
            "subject": {"type": "string"},
            "slogan": {"type": "string"},
            "text_layout": {"type": "string", "enum": ["none", "top", "bottom", "top_bottom"]},
            "title": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "description": {"type": "string"},
            "poster_subject": {"type": "string"},
        },
        "required": ["style", "subject", "slogan", "text_layout", "title", "tags", "description", "poster_subject"],
    }
    return {
        "type": "object",
        "properties": {
            "protected_terms": {"type": "array", "items": {"type": "string"}},
            "theme": {"type": "string"},
            "designs": {"type": "array", "items": design},
        },
        "required": ["protected_terms", "theme", "designs"],
    }


def score_trends(trends: list[dict], cfg: config.Config | None = None, llm: Ollama | None = None) -> dict[int, tuple[int, str]]:
    cfg = cfg or config.load()
    llm = llm or Ollama(cfg.ollama)
    politics = "" if cfg.trends.allow_politics else ", elections, politicians and partisan politics"
    system = SCORE_SYSTEM.format(politics_rule=politics)
    out: dict[int, tuple[int, str]] = {}
    for i in range(0, len(trends), 25):
        batch = trends[i:i + 25]
        lines = []
        for t in batch:
            ctx = "; ".join(str(c) for c in (t.get("context") or [])[:3])
            lic = research.license_for(t, None, cfg)
            mark = f" [LICENSED fan art: {lic.name}]" if lic else ""
            lines.append(f'{t["id"]}: "{t["title"]}" [{t["source"]}/{t.get("region") or "global"}]{mark} {ctx}')
        res = llm.chat_json(system, "Trends:\n" + "\n".join(lines), schema=SCORE_SCHEMA, temperature=0.2)
        valid = {t["id"] for t in batch}
        for s in res.get("scores", []):
            if isinstance(s, dict) and s.get("id") in valid:
                out[int(s["id"])] = (max(0, min(10, int(s.get("score", 0)))), str(s.get("reason", ""))[:200])
    return out


_WORD_SPLIT = re.compile(r"\s+")


def _contains(text: str, term: str) -> bool:
    t = normalize(term)
    return bool(t) and f" {t} " in f" {normalize(text)} "


def _strip_term(text: str, term: str) -> str:
    return re.sub(rf"(?<!\w){re.escape(term)}(?!\w)", "", text, flags=re.I).replace("  ", " ").strip(" ,.-")


def clean_tags(tags: list, max_tags: int, banned: list[str]) -> list[str]:
    out: list[str] = []
    for t in tags or []:
        t = re.sub(r"[^\w\s'-]", "", str(t).lower()).strip()
        t = _WORD_SPLIT.sub(" ", t)
        if not t or "_" in t or len(t) > 30 or len(t.split()) > 3 or t in out:  # "_": internal names like flat_vector
            continue
        if any(_contains(t, b) for b in banned):
            continue
        out.append(t)
    return out[:max_tags]


def sanitize(raw: dict, trend: dict, cfg: config.Config, notes: dict | None = None) -> tuple[list[dict], list[str]]:
    """Validate LLM output; drop designs that leak protected names. Returns (designs, protected_terms).

    With research `notes`, the work's title, franchise and character names are protected too, unless the trend
    belongs to a licensed franchise: then those names are allowed and every design gets "license" = its name."""
    styles = list(cfg.sticker_styles)
    notes = notes or {}
    lic = research.license_for(trend, notes, cfg)
    protected = [str(p).strip() for p in raw.get("protected_terms") or [] if len(str(p).strip()) >= 3]
    haystack = " ".join([trend["title"], *map(str, trend.get("context") or [])])
    protected += [b for b in cfg.trends.ip_blocklist if _contains(haystack, b)]
    blocklist = cfg.trends.ip_blocklist
    if lic:
        allowed = [a for a in (*lic.terms, notes.get("franchise"), *(c["name"] for c in notes.get("characters") or []),
                               *(notes.get("names") or [])) if a]

        def licensed(term: str) -> bool:  # the franchise, its titles ("X: Part Two") and its characters
            return any(_contains(a, term) or _contains(term, a) for a in allowed)

        protected = [p for p in protected if not licensed(p)]
        blocklist = [b for b in blocklist if not licensed(b)]
    elif notes:
        protected += research.protected_names(notes)
    protected += [p.strip() for p in notes.get("real_people") or [] if len(p.strip()) >= 3]  # never licensed
    protected = list(dict.fromkeys(protected))
    banned = sorted(set(protected + blocklist), key=len, reverse=True)
    required = clean_tags(lic.required_tags, 15, []) if lic else []

    designs = []
    for d in raw.get("designs") or []:
        if not isinstance(d, dict):
            continue
        style = d.get("style") if d.get("style") in styles else styles[0]
        slogan = str(d.get("slogan") or "").strip().strip('"')[:40]
        title = str(d.get("title") or "").strip()[:60]
        if not title or any(_contains(title, b) or (slogan and _contains(slogan, b)) for b in banned):
            _log.info("dropping concept %r: leaks protected term", title)
            continue
        if style == "meme_text" and not slogan:
            style = "flat_vector"
        layout = d.get("text_layout") if d.get("text_layout") in ("none", "top", "bottom", "top_bottom") else "bottom"
        if not slogan:
            layout = "none"
        elif layout == "none":
            layout = "bottom"
        subject, poster_subject, desc = str(d.get("subject") or ""), str(d.get("poster_subject") or ""), str(d.get("description") or "")
        for b in banned:
            subject, poster_subject, desc = _strip_term(subject, b), _strip_term(poster_subject, b), _strip_term(desc, b)
        if len(subject.split()) < 3:
            continue
        designs.append({
            "style": style, "subject": subject, "slogan": slogan, "text_layout": layout, "title": title,
            "tags": clean_tags([*required, *(d.get("tags") or [])], 15, banned), "description": desc[:500],
            "poster_subject": poster_subject or subject, "license": lic.name if lic else None,
        })
    return designs, protected


def make_concepts(trend: dict, *, n_hint: str = "1 to 3 designs (more for broad, hot trends; 1 for niche ones)",
                  cfg: config.Config | None = None, llm: Ollama | None = None, recent_titles: list[str] | None = None,
                  notes: dict | None = None) -> tuple[list[dict], list[str], str]:
    """`notes` = research.lookup() for the trend: the work's world and characters (and whether it's licensed)."""
    cfg = cfg or config.load()
    llm = llm or Ollama(cfg.ollama)
    styles = list(cfg.sticker_styles)
    style_list = ", ".join(f"{k} ({v.description})" for k, v in cfg.sticker_styles.items())
    recent = "; ".join((recent_titles or [])[-40:]) or "none"
    lic = research.license_for(trend, notes, cfg)
    ip_rules = (IP_RULES_LICENSED.format(franchise=lic.name, guidelines=lic.guidelines or "none given") if lic
                else IP_RULES_GENERIC)
    system = CONCEPT_SYSTEM.format(styles=style_list, n_hint=n_hint, recent=recent, ip_rules=ip_rules,
                                   subject_rule=SUBJECT_RULE_LICENSED if lic else "")
    ctx = "\n".join(f"- {c}" for c in (trend.get("context") or [])[:6])
    user = (f"Today is {dt.date.today():%B %d, %Y}.\nTrend: \"{trend['title']}\"\nSource: {trend['source']} "
            f"({trend.get('region') or 'global'})\nContext:\n{ctx or '- (none)'}")
    if notes:
        user += "\n\n" + research.brief(notes)
    raw = llm.chat_json(system, user, schema=concept_schema(styles), temperature=0.85)
    designs, protected = sanitize(raw, trend, cfg, notes)
    return designs, protected, str(raw.get("theme") or "")[:200]
