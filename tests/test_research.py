import httpx
import pytest

from pto import config, db as dbmod, research
from pto.db import DB, now_iso
from pto.llm import concepts

CFG = config.load()

ARTICLE = """{{Short description|2026 video game}}
{{Infobox video game
| title = Moth Knight
| developer = [[Lamp Games|Lamp]]
| series = [[Moth Knight (franchise)|''Moth Knight'']]
}}
'''''Moth Knight''''' is a 2026 [[Metroidvania]] game by Lamp.<ref>{{cite web|url=https://example.com|title=x}}</ref>
The story follows [[Lumen (Moth Knight)|Lumen]], a moth who guards the last lantern of a sunken city.

==Gameplay==
Players glide between [[lantern]]s and duel {{nowrap|ink beetles}} in a flooded clockwork city.
[[File:Moth.png|thumb|Lumen in the [[Undercity]]]]

==Plot==
Lumen wakes in the drowned city of Wickmere and must relight its seven beacons before the tide swallows them.
===Ending===
The beacons reveal the city's lost queen.

==Cast==
{{cast listing|
* [[Jane Doe (actress)|Jane Doe]] as [[Lumen (Moth Knight)|Lumen]], a lantern-bearing moth knight<ref name="a" />
* John Roe as the Tidewarden, a barnacled crab tyrant
}}

==Reception==
Critics praised its art. This section should not be picked.
"""

FANDOM_LUMEN = """{{Infobox character
|image = Lumen.png
|species = [[Moth]]
|eyes = Glowing amber
}}
'''Lumen''' is the protagonist.

==Appearance==
A small grey moth in a patched blue cloak, carrying a brass lantern on a hooked staff.

==Lore==
Long story.
"""


def _client(seen: list[str]) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.path == "/w/rest.php/v1/search/page":
            assert req.url.params["q"] == "Moth Knight video game"
            return httpx.Response(200, json={"pages": [
                {"title": "Moth Knight (album)", "description": "2020 album by a band"},
                {"title": "Moth Knight", "description": "2026 single-player video game"}]})
        if req.url.path.startswith("/w/rest.php/v1/page/"):
            return httpx.Response(200, json={"source": ARTICLE})
        if req.url.host == "mothknight.fandom.com":
            q = req.url.params
            if q.get("action") == "parse" and q.get("page") == "Lumen":
                return httpx.Response(200, json={"parse": {"wikitext": FANDOM_LUMEN}})
            if q.get("action") == "parse":
                return httpx.Response(200, json={"error": {"code": "missingtitle"}})
            return httpx.Response(200, json={"query": {"search": []}})
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


class FakeLLM:
    def __init__(self, reply: dict):
        self.reply, self.calls = reply, []

    def chat_json(self, system, user, **kw):
        self.calls.append((system, user))
        return self.reply


EXTRACTED = {
    "franchise": "Moth Knight", "kind": "game", "premise": "A moth relights the beacons of a drowned city.",
    "setting": "flooded clockwork city of Wickmere", "tone": "melancholy, hopeful", "motifs": ["lanterns", "tides"],
    "characters": [{"name": "Lumen", "role": "protagonist", "look": "grey moth"},
                   {"name": "Tidewarden", "role": "villain", "look": "barnacled crab"}],
    "names": ["Wickmere"], "real_people": ["Jane Doe", "John Roe"],
}


def _licensed_cfg(**kw) -> config.Config:
    cfg = CFG.model_copy(deep=True)
    cfg.research.licensed_franchises = [config.LicensedFranchise(
        name="Moth Knight", match=["moth knight"], required_tags=["mothknightfanart"], wiki="mothknight.fandom.com", **kw)]
    return cfg


def test_strip_wikitext_and_pick_sections():
    text = research.pick_sections(ARTICLE)
    assert text.startswith("Moth Knight is a 2026 Metroidvania game by Lamp.")
    assert "## Gameplay" in text and "ink beetles" in text and "### Ending" in text
    assert "- Jane Doe as Lumen, a lantern-bearing moth knight" in text  # piped links inside {{cast listing}}
    assert "Reception" not in text and "cite web" not in text and "Moth.png" not in text and "[[" not in text


def test_appearance_from_fandom_wikitext():
    look = research.appearance_from_wikitext(FANDOM_LUMEN)
    assert look.startswith("species: Moth; eyes: Glowing amber.")
    assert "patched blue cloak" in look and "Long story" not in look
    lore_only = "{{Infobox character\n|gender = Female\n}}\n'''Hornet''' must be defeated twice.\n==Lore==\nOld wars."
    assert research.appearance_from_wikitext(lore_only) == "gender: Female"  # lore is not a look


def test_media_and_title_checks():
    assert research.is_media("2026 single-player video game") and research.is_media("2025 American fantasy film")
    assert research.is_media("American television series (2016–2025)")
    assert not research.is_media("American actor (born 1980)") and not research.is_media("2020 album by a band")
    assert research.title_overlap("Moth Knight", "Moth Knight (2026 video game)") == 1.0
    assert research.title_overlap("Moth Knight", "Knight Rider") == 0.5


def test_lookup_finds_page_extracts_characters_and_fandom_looks():
    seen: list[str] = []
    trend = {"title": "Moth Knight", "source": "steam", "context": ["video game releasing in 20 days"],
             "meta": {"kind": "game"}}
    llm = FakeLLM(EXTRACTED)
    notes = research.lookup(trend, _licensed_cfg(), llm, client=_client(seen))
    assert notes["page"] == "Moth Knight" and notes["license"] == "Moth Knight"
    assert "Plot" in llm.calls[0][1] and "Reception" not in llm.calls[0][1]
    lumen, warden = notes["characters"]
    assert "patched blue cloak" in lumen["look"] and lumen["look_source"] == "mothknight.fandom.com"
    assert lumen["look"].endswith("; grey moth")  # the model's description is kept after the wiki's
    assert warden["look"] == "barnacled crab"  # no fandom page: keeps the article's description
    assert notes["real_people"] == ["Jane Doe", "John Roe"]

    seen.clear()
    unlicensed = research.lookup(trend, CFG, FakeLLM(EXTRACTED), client=_client(seen))
    assert unlicensed["license"] is None and seen and not any("fandom" in u for u in seen)


def test_lookup_skips_curated_and_non_media():
    llm = FakeLLM(EXTRACTED)
    assert research.lookup({"title": "Halloween 2026", "source": "seasonal"}, CFG, llm) == {}
    assert research.lookup({"title": "Capybara", "source": "wikipedia", "context": ["Species of rodent"]}, CFG, llm,
                           client=_client([])) == {}
    assert not llm.calls


def test_license_matching():
    cfg = _licensed_cfg()
    assert research.license_for({"title": "Moth Knight II trailer"}, None, cfg).name == "Moth Knight"
    assert research.license_for({"title": "Lantern game"}, {"page": "Lantern", "franchise": "Moth Knight"}, cfg)
    assert research.license_for({"title": "Mothman sightings"}, None, cfg) is None
    assert research.license_for({"title": "Moth Knight"}, None, CFG) is None
    assert cfg.licenses_for("redbubble") == ["Moth Knight"] and cfg.licenses_for("teepublic") == []


def _raw(*designs):
    return {"protected_terms": [], "theme": "t", "designs": [
        {"style": "kawaii", "subject": s, "slogan": "", "text_layout": "none", "title": t, "tags": tags,
         "description": "", "poster_subject": ""} for t, s, tags in designs]}


NOTES = {**EXTRACTED, "page": "Moth Knight (2026 video game)", "characters": EXTRACTED["characters"]}


def test_unlicensed_research_names_are_filtered():
    raw = _raw(("Lumen Lantern Moth", "a moth with a lantern", ["moth"]),
               ("Sleepy Lantern Moth", "a sleepy Lumen moth holding a lantern, Lumens glowing",
                ["moth", "lumen", "jane doe", "wickmere", "flat_vector"]))
    designs, protected = concepts.sanitize(raw, {"title": "Moth Knight", "context": []}, CFG, NOTES)
    assert [d["title"] for d in designs] == ["Sleepy Lantern Moth"]
    d = designs[0]
    assert "Lumen " not in d["subject"] and "Lumens" in d["subject"]  # whole words only
    assert d["tags"] == ["moth"] and d["license"] is None
    assert {"Moth Knight", "Lumen", "Tidewarden", "Wickmere", "Jane Doe"} <= set(protected)


def test_licensed_designs_name_characters_with_required_tags():
    cfg = _licensed_cfg()
    cfg.trends.ip_blocklist = [*cfg.trends.ip_blocklist, "moth knight"]
    raw = _raw(("Lumen the Moth Knight", "Lumen, a grey moth in a blue cloak with a brass lantern", ["lumen", "moth knight"]),
               ("Lumen Meets Pokemon", "Lumen and a pokemon trainer", ["pokemon"]),
               ("Tidewarden Portrait", "Tidewarden the crab, drawn like Jane Doe", ["crab", "jane doe"]))
    raw["protected_terms"] = ["Moth Knight: Part Two", "Lamp Games"]
    designs, protected = concepts.sanitize(raw, {"title": "Moth Knight", "context": []}, cfg, NOTES)
    titles = [d["title"] for d in designs]
    assert titles == ["Lumen the Moth Knight", "Tidewarden Portrait"]  # other franchises still dropped
    assert designs[0]["tags"][:3] == ["mothknightfanart", "lumen", "moth knight"]
    assert designs[0]["license"] == "Moth Knight" and "Lumen" in designs[0]["subject"]
    assert "Jane Doe" not in designs[1]["subject"] and "jane doe" not in designs[1]["tags"]  # actors never licensed
    assert "Jane Doe" in protected and "Lamp Games" in protected and "Moth Knight: Part Two" not in protected


def test_make_concepts_prompts_with_research():
    llm = FakeLLM(_raw(("Lantern Moth", "a moth with a lantern", ["moth"])))
    concepts.make_concepts({"title": "Moth Knight", "source": "steam", "context": []}, cfg=_licensed_cfg(), llm=llm,
                           notes=NOTES)
    system, user = llm.calls[0]
    assert "officially licensed to make fan art of" in system and "spell out their look" in system
    assert "- Lumen (protagonist): grey moth" in user and "drowned city" in user
    llm = FakeLLM(_raw(("Lantern Moth", "a moth with a lantern", ["moth"])))
    concepts.make_concepts({"title": "Moth Knight", "source": "steam", "context": []}, cfg=CFG, llm=llm, notes=NOTES)
    assert "invent original archetypes" in llm.calls[0][0].lower() and "officially licensed" not in llm.calls[0][0]


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    d = DB(tmp_path / "t.sqlite3")
    monkeypatch.setattr(dbmod, "_db", d)
    return d


def test_licensed_designs_only_go_to_licensed_platforms(tmpdb):
    now = now_iso()
    base = {"kind": "sticker", "style": "kawaii", "status": "ready", "title": "x", "created_at": now, "updated_at": now,
            "files": {"final": "000001/sticker.png"}}
    fan = tmpdb.insert("designs", {**base, "license": "Moth Knight", "ready_at": "2026-01-01T00:00:00"})
    plain = tmpdb.insert("designs", {**base, "ready_at": "2026-01-02T00:00:00"})
    assert tmpdb.next_for_platform("redbubble", "sticker", ["Moth Knight"])["id"] == fan
    assert tmpdb.next_for_platform("redbubble", "sticker")["id"] == plain  # license revoked / not configured
    assert tmpdb.next_for_platform("teepublic", "sticker", [])["id"] == plain
    assert tmpdb.ready_backlog(["redbubble", "teepublic"]) == 1
    assert tmpdb.ready_backlog(["redbubble"], license="Moth Knight") == 1

    from pto.pipeline import Pipeline

    cfg = _licensed_cfg()
    cfg.platforms.teepublic.enabled = True
    tmpdb.insert("uploads", {"design_id": fan, "platform": "redbubble", "status": "success", "created_at": now})
    assert Pipeline(cfg).backlog() == 1  # the fan art is done: it never goes to TeePublic
