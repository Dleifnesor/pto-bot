"""Trend collection from all enabled sources into the trends table."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from .. import config, db as dbmod, log

_log = log.get("trends")


@dataclass
class TrendItem:
    source: str
    title: str
    region: str = "global"
    traffic: int = 0
    context: list[str] = field(default_factory=list)
    url: str | None = None
    dedupe_period: str = "day"  # day | week  (seasonal/evergreen/release ideas repeat at most weekly)
    meta: dict = field(default_factory=dict)  # e.g. {"kind": "movie", "release": "2026-11-03", "days_until": 24}


POPCULTURE_SOURCES = ("steam", "tmdb", "wikipedia", "events")


def normalize(title: str) -> str:
    t = re.sub(r"[^\w\s]", " ", title.lower())
    return re.sub(r"\s+", " ", t).strip()


def sensitive_hit(text: str, keywords: list[str]) -> str | None:
    low = f" {normalize(text)} "
    for kw in keywords:
        if f" {normalize(kw)} " in low:
            return kw
    return None


def skip_hit(text: str, patterns: list[str]) -> str | None:
    for p in patterns:
        if re.search(p, text, flags=re.I):
            return p
    return None


def _period_key(period: str) -> str:
    today = dt.date.today()
    if period == "week":
        y, w, _ = today.isocalendar()
        return f"{y}-W{w:02d}"
    return today.isoformat()


def collect(cfg: config.Config | None = None) -> dict[str, int]:
    from . import google, reddit, seasonal, steam, tmdb, wikipedia, youtube

    cfg = cfg or config.load()
    db = dbmod.get()
    tc = cfg.trends
    lead, recent = tc.popculture_lead_days, tc.popculture_recent_days
    sources = []
    if tc.steam:
        sources.append(("steam", lambda: steam.fetch(lead, recent)))
    if tc.tmdb and config.secret("TMDB_API_KEY"):
        sources.append(("tmdb", lambda: tmdb.fetch(config.secret("TMDB_API_KEY"), lead, recent)))
    if tc.wikipedia:
        sources.append(("wikipedia", lambda: wikipedia.fetch()))
    if tc.events:
        sources.append(("events", lambda: seasonal.events(dt.date.today(), lead)))
    if tc.google_trends:
        sources.append(("google", lambda: google.fetch(tc.regions)))
    if tc.reddit.enabled:
        sources.append(("reddit", lambda: reddit.fetch(tc.reddit)))
    if tc.youtube and config.secret("YOUTUBE_API_KEY"):
        sources.append(("youtube", lambda: youtube.fetch(tc.regions, config.secret("YOUTUBE_API_KEY"))))
    if tc.seasonal:
        sources.append(("seasonal", lambda: seasonal.upcoming(dt.date.today(), tc.seasonal_lead_days)))
    if tc.evergreen:
        sources.append(("evergreen", lambda: seasonal.evergreen(tc.evergreen_niches, tc.evergreen_per_run)))

    stats: dict[str, int] = {}
    for name, fn in sources:
        try:
            items = fn()
        except Exception as e:  # noqa: BLE001 - one broken source shouldn't stop the others
            _log.warning("trend source %s failed: %s", name, e)
            stats[name] = -1
            continue
        added = 0
        for it in items:
            # seasonal/evergreen ideas are curated by us; only external feeds go through the tragedy filter
            curated = it.source in ("seasonal", "evergreen", "events")
            # game/movie titles and plots are fiction ("Death ...", "killed") - the LLM judges those instead
            fiction = it.source in ("steam", "tmdb")
            reason = None
            if not curated and not fiction:
                text = " ".join([it.title, *it.context[:1 if it.source == "wikipedia" else 3]])
                if kw := sensitive_hit(text, tc.sensitive_keywords):
                    reason = f"sensitive keyword: {kw}"
                elif it.source != "reddit" and (pat := skip_hit(it.title, tc.skip_patterns)):
                    reason = f"not merch-worthy ({pat})"
            hit = reason is not None
            row = {
                "source": it.source, "region": it.region, "title": it.title[:300], "norm": normalize(it.title)[:300],
                "day": _period_key(it.dedupe_period), "traffic": it.traffic, "context": [c for c in it.context[:8] if c],
                "url": it.url, "meta": it.meta,
                "fetched_at": dbmod.now_iso(),
                "status": "rejected" if hit else "new",
                "score_reason": reason,
            }
            if db.add_trend(row) is not None and not hit:
                added += 1
        stats[name] = added
        _log.info("trends: %s -> %d new (of %d fetched)", name, added, len(items))
    db.set_kv("trends_last_fetch", dbmod.now_iso())
    return stats
