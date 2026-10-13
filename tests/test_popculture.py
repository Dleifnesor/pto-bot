import datetime as dt
import sqlite3

import pytest

from pto import config, db as dbmod
from pto.db import DB, now_iso
from pto.trends import seasonal, steam, wikipedia


def test_steam_release_dates():
    assert steam.parse_release("Oct 9, 2026") == dt.date(2026, 10, 9)
    assert steam.parse_release("9 Oct, 2026") == dt.date(2026, 10, 9)
    assert steam.parse_release("November 2026") == dt.date(2026, 11, 1)
    assert steam.parse_release("Coming soon") is None and steam.parse_release("Q4 2026") is None


def test_wikipedia_keeps_media_drops_people_and_tragedies():
    assert wikipedia.keep("2026 film directed by Jane Doe")
    assert wikipedia.keep("2026 American television miniseries")
    assert wikipedia.keep("2026 video game")
    assert not wikipedia.keep("American football player, coach, and commentator (1939–2026)")
    assert not wikipedia.keep("2009 mass shooting in Texas")
    assert not wikipedia.keep("Indian actor (born 1951)")
    assert not wikipedia.keep("")


def test_events_calendar_lead_window():
    names = [i.title for i in seasonal.events(dt.date(2026, 10, 10), 28)]
    assert "Orionid meteor shower 2026" in names and "National Cat Day 2026" in names
    assert "Black Friday 2026" not in names  # 48 days out
    assert dt.date(2026, 11, 13) in seasonal.friday_13ths(2026)
    bf = next(i for i in seasonal.events(dt.date(2026, 11, 1), 28) if i.title.startswith("Black Friday"))
    assert bf.meta["release"] == "2026-11-27"


def test_trends_meta_column_migrates(tmp_path):
    p = tmp_path / "old.sqlite3"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE trends (id INTEGER PRIMARY KEY, source TEXT NOT NULL, region TEXT, title TEXT NOT NULL, "
                "norm TEXT NOT NULL, day TEXT NOT NULL, traffic INTEGER DEFAULT 0, context TEXT DEFAULT '[]', url TEXT, "
                "fetched_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new', score REAL, score_reason TEXT, "
                "UNIQUE(source, norm, day))")
    con.commit()
    con.close()
    d = DB(p)
    assert "meta" in {r["name"] for r in d.all("PRAGMA table_info(trends)")}


@pytest.fixture()
def pipe(tmp_path, monkeypatch):
    monkeypatch.setattr(dbmod, "_db", DB(tmp_path / "t.sqlite3"))
    from pto.pipeline import Pipeline

    return Pipeline(config.load())


def _trend(d, source, title, score=8, meta=None):
    d.insert("trends", {"source": source, "region": "US", "title": title, "norm": title.lower(), "day": "x",
                        "fetched_at": now_iso(), "status": "scored", "score": score, "meta": meta or {}})


def test_pick_mix_favors_pop_culture(pipe):
    d = dbmod.get()
    for i in range(8):
        _trend(d, "steam" if i % 2 else "tmdb", f"pop {i}")
        _trend(d, "google" if i % 2 else "reddit", f"news {i}", score=9)
    picked = pipe.pick_trends(10)
    pop = sum(1 for r in picked if r["source"] in ("steam", "tmdb"))
    assert len(picked) == 10 and pop == 7


def test_timing_boost_prefers_three_to_four_weeks_out(pipe):
    d = dbmod.get()
    _trend(d, "tmdb", "far", meta={"days_until": 60})
    _trend(d, "tmdb", "sweet spot", meta={"days_until": 24})
    _trend(d, "tmdb", "tomorrow", meta={"days_until": 1})
    order = [r["title"] for r in pipe.pick_trends(3)]
    assert order[0] == "sweet spot"
