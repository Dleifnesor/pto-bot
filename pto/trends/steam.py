"""Steam: popular upcoming games (with release dates) and current top sellers. Public endpoints, no key."""
from __future__ import annotations

import datetime as dt
import re
import time

import httpx

from . import TrendItem

SEARCH = "https://store.steampowered.com/search/results/"
DETAILS = "https://store.steampowered.com/api/appdetails"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) pto-trend-bot/0.1"}
_APPID = re.compile(r"/apps/(\d+)/")


def parse_release(s: str | None) -> dt.date | None:
    if not s:
        return None
    s = s.strip().replace(",", ", ").replace("  ", " ")
    for fmt in ("%b %d, %Y", "%d %b, %Y", "%B %d, %Y", "%d %B, %Y", "%b %Y", "%B %Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _search(c: httpx.Client, filt: str, count: int) -> list[tuple[int, str]]:
    r = c.get(SEARCH, params={"json": 1, "filter": filt, "count": count, "cc": "US", "l": "english", "category1": 998})
    r.raise_for_status()
    out = []
    for it in r.json().get("items", []):
        m = _APPID.search(it.get("logo", ""))
        if m:
            out.append((int(m.group(1)), it.get("name", "")))
    return out


def _details(c: httpx.Client, appid: int) -> dict:
    r = c.get(DETAILS, params={"appids": appid, "cc": "US", "l": "english"})
    if r.status_code != 200:
        return {}
    entry = (r.json() or {}).get(str(appid), {})
    return entry.get("data", {}) if entry.get("success") else {}


def fetch(lead_days: int, recent_days: int, upcoming: int = 20, top: int = 10) -> list[TrendItem]:
    today = dt.date.today()
    out: list[TrendItem] = []
    with httpx.Client(timeout=20, headers=UA, follow_redirects=True) as c:
        for kind, filt, count in (("upcoming", "popularcomingsoon", upcoming), ("top", "topsellers", top)):
            for appid, name in _search(c, filt, count):
                d = _details(c, appid)
                time.sleep(0.4)  # appdetails is rate limited
                if not d or d.get("type") != "game":
                    continue
                genres = ", ".join(g["description"] for g in d.get("genres", [])[:4])
                rd = parse_release((d.get("release_date") or {}).get("date"))
                days = (rd - today).days if rd else None
                if kind == "upcoming":
                    if days is None or not (-recent_days <= days <= lead_days):
                        continue
                    when = f"video game releasing in {days} days ({rd.isoformat()})" if days >= 0 else f"video game released {-days} days ago"
                else:
                    when = "one of the best-selling games on Steam right now"
                out.append(TrendItem(
                    source="steam", title=d.get("name") or name, traffic=1500 if kind == "upcoming" else 1200,
                    context=[when, f"genres: {genres}" if genres else "", (d.get("short_description") or "")[:220]],
                    url=f"https://store.steampowered.com/app/{appid}", dedupe_period="week",
                    meta={"kind": "game", "release": rd.isoformat() if rd else None, "days_until": days},
                ))
    return out
