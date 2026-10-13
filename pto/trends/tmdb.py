"""Movies & TV from TMDB: upcoming and now-playing movies (US) plus this week's trending shows. Needs TMDB_API_KEY."""
from __future__ import annotations

import datetime as dt

import httpx

from . import TrendItem

API = "https://api.themoviedb.org/3"


def _client(key: str) -> tuple[httpx.Client, dict]:
    if key.startswith("eyJ"):  # v4 read-access token
        return httpx.Client(base_url=API, timeout=20, headers={"Authorization": f"Bearer {key}"}), {}
    return httpx.Client(base_url=API, timeout=20), {"api_key": key}


def fetch(key: str, lead_days: int, recent_days: int, region: str = "US") -> list[TrendItem]:
    today = dt.date.today()
    c, auth = _client(key)
    out: list[TrendItem] = []
    with c:
        def get(path: str, **params) -> dict:
            r = c.get(path, params={**auth, "language": "en-US", **params})
            r.raise_for_status()
            return r.json()

        genres = {g["id"]: g["name"] for g in get("/genre/movie/list").get("genres", [])}
        genres.update({g["id"]: g["name"] for g in get("/genre/tv/list").get("genres", [])})
        seen: set[int] = set()
        movies = []
        for path in ("/movie/upcoming", "/movie/now_playing"):
            for page in (1, 2):
                movies += get(path, region=region, page=page).get("results", [])
        for m in sorted(movies, key=lambda m: m.get("popularity", 0), reverse=True):
            if m["id"] in seen or m.get("adult"):
                continue
            seen.add(m["id"])
            try:
                rd = dt.date.fromisoformat(m.get("release_date") or "")
            except ValueError:
                continue
            days = (rd - today).days
            if not (-recent_days <= days <= lead_days):
                continue
            when = f"movie releasing in {days} days ({rd.isoformat()})" if days >= 0 else f"movie in theaters now (released {-days} days ago)"
            g = ", ".join(genres.get(i, "") for i in m.get("genre_ids", [])[:4])
            out.append(TrendItem(
                source="tmdb", title=m.get("title", ""), region=region, traffic=int(m.get("popularity", 0) * 10),
                context=[when, f"genres: {g}", (m.get("overview") or "")[:220]],
                url=f"https://www.themoviedb.org/movie/{m['id']}", dedupe_period="week",
                meta={"kind": "movie", "release": rd.isoformat(), "days_until": days},
            ))
        for s in get("/trending/tv/week").get("results", [])[:12]:
            g = ", ".join(genres.get(i, "") for i in s.get("genre_ids", [])[:4])
            out.append(TrendItem(
                source="tmdb", title=s.get("name", ""), traffic=int(s.get("popularity", 0) * 10),
                context=["TV series trending this week", f"genres: {g}", (s.get("overview") or "")[:220]],
                url=f"https://www.themoviedb.org/tv/{s['id']}", dedupe_period="week", meta={"kind": "tv"},
            ))
    return out
