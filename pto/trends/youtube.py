"""YouTube most-popular videos per region (YouTube Data API v3, free key)."""
from __future__ import annotations

import httpx

from . import TrendItem


def fetch(regions: list[str], api_key: str) -> list[TrendItem]:
    out = []
    with httpx.Client(timeout=20) as c:
        for region in regions:
            r = c.get("https://www.googleapis.com/youtube/v3/videos", params={
                "part": "snippet,statistics", "chart": "mostPopular", "regionCode": region,
                "maxResults": 25, "key": api_key,
            })
            r.raise_for_status()
            for v in r.json().get("items", []):
                sn = v.get("snippet", {})
                out.append(TrendItem(
                    source="youtube", region=region, title=sn.get("title", ""),
                    traffic=int(v.get("statistics", {}).get("viewCount", 0)),
                    context=[*(sn.get("tags") or [])[:6], sn.get("channelTitle", "")],
                    url=f"https://www.youtube.com/watch?v={v.get('id')}",
                ))
    return out
