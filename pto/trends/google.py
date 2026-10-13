"""Google Trends daily trending searches via the public RSS feed (no API key)."""
from __future__ import annotations

import re

import httpx
from defusedxml import ElementTree as ET

from . import TrendItem

FEED = "https://trends.google.com/trending/rss?geo={geo}"
NS = {"ht": "https://trends.google.com/trending/rss"}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) pto-trend-bot/0.1"}


def parse_traffic(s: str | None) -> int:
    if not s:
        return 0
    m = re.match(r"([\d.,]+)\s*([KkMm]?)", s.strip())
    if not m:
        return 0
    n = float(m.group(1).replace(",", ""))
    mult = {"k": 1_000, "m": 1_000_000}.get(m.group(2).lower(), 1)
    return int(n * mult)


def parse(xml_text: str, region: str) -> list[TrendItem]:
    root = ET.fromstring(xml_text)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        news = [n.findtext("ht:news_item_title", default="", namespaces=NS).strip() for n in item.findall("ht:news_item", NS)]
        url = None
        first = item.find("ht:news_item", NS)
        if first is not None:
            url = first.findtext("ht:news_item_url", default=None, namespaces=NS)
        items.append(TrendItem(
            source="google", region=region, title=title,
            traffic=parse_traffic(item.findtext("ht:approx_traffic", default="", namespaces=NS)),
            context=[n for n in news if n], url=url,
        ))
    return items


def fetch(regions: list[str]) -> list[TrendItem]:
    out: list[TrendItem] = []
    with httpx.Client(timeout=20, headers=UA, follow_redirects=True) as c:
        for geo in regions:
            r = c.get(FEED.format(geo=geo))
            r.raise_for_status()
            out.extend(parse(r.text, geo))
    return out
