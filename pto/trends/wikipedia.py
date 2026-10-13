"""English Wikipedia's most-read pages yesterday, kept only when they're media, events or other merch-able topics."""
from __future__ import annotations

import datetime as dt
import re
import time
from urllib.parse import quote

import httpx

from . import TrendItem

TOP = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access/{d:%Y/%m/%d}"
SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{t}"
UA = {"User-Agent": "pto-trend-bot/0.1 (personal trend research)"}
SKIP_PREFIX = ("Main_Page", "Special:", "Wikipedia:", "File:", "Portal:", "Help:", "Template:", "Category:", "Talk:",
               "User:", "Draft:", "Deaths_in_", "List_of_", "-")
KEEP = re.compile(r"\b(film|series|miniseries|television|sitcom|anime|manga|video game|game|novel|book|comic|festival|"
                  r"holiday|celebration|event|tournament|championship|season|meme|internet|animal|species|breed|toy|"
                  r"franchise|cartoon|musical|play|convention|eclipse|meteor|comet)\b", re.I)
PEOPLE = re.compile(r"\(born |\(\d{3,4}\s*[–-]\s*\d{3,4}\)|\b(politician|actor|actress|singer|rapper|player|coach|"
                    r"businessman|businesswoman|journalist|presenter|footballer|cricketer|wrestler|musician|comedian|"
                    r"model|influencer|youtuber|streamer|murderer|criminal|killer|activist|judge|lawyer|officer|soldier|"
                    r"king|queen|prince|princess|president|minister|director|producer|writer|author|poet|chef|"
                    r"athlete|boxer|fighter|shooting|attack|massacre|bombing|disaster|crash)\b", re.I)


def keep(description: str) -> bool:
    return bool(description) and not PEOPLE.search(description) and bool(KEEP.search(description))


def fetch(max_items: int = 25, scan: int = 70) -> list[TrendItem]:
    out: list[TrendItem] = []
    with httpx.Client(timeout=20, headers=UA, follow_redirects=True) as c:
        arts = []
        for back in (1, 2):
            r = c.get(TOP.format(d=dt.date.today() - dt.timedelta(days=back)))
            if r.status_code == 200:
                arts = r.json()["items"][0]["articles"]
                break
        for a in arts:
            if len(out) >= max_items or scan <= 0:
                break
            name = a["article"]
            if name.startswith(SKIP_PREFIX):
                continue
            scan -= 1
            s = c.get(SUMMARY.format(t=quote(name, safe="")))
            time.sleep(0.15)
            if s.status_code != 200:
                continue
            j = s.json()
            desc = j.get("description") or ""
            if j.get("type") == "disambiguation" or not keep(desc):
                continue
            out.append(TrendItem(
                source="wikipedia", title=j.get("title") or name.replace("_", " "), traffic=int(a.get("views", 0)),
                context=[desc, (j.get("extract") or "")[:220]],
                url=(j.get("content_urls") or {}).get("desktop", {}).get("page"),
                meta={"kind": "wiki", "views": a.get("views")},
            ))
    return out
