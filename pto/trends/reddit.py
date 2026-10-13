"""Reddit hot/rising posts. Uses the official API when REDDIT_CLIENT_ID/SECRET are set, else public RSS."""
from __future__ import annotations

import time

import httpx
from defusedxml import ElementTree as ET

from .. import config, log
from . import TrendItem

_log = log.get("reddit")

UA = {"User-Agent": "windows:pto-trend-bot:0.1 (personal trend research)"}
ATOM = {"a": "http://www.w3.org/2005/Atom"}


def _token(client_id: str, secret: str) -> str:
    r = httpx.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=(client_id, secret), data={"grant_type": "client_credentials"}, headers=UA, timeout=20,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _via_api(cfg: config.RedditCfg, client_id: str, secret: str) -> list[TrendItem]:
    tok = _token(client_id, secret)
    out = []
    with httpx.Client(timeout=20, headers={**UA, "Authorization": f"bearer {tok}"}) as c:
        for sub in cfg.subreddits:
            r = c.get(f"https://oauth.reddit.com/r/{sub}/{cfg.sort}", params={"limit": cfg.per_sub})
            if r.status_code != 200:
                continue
            for ch in r.json().get("data", {}).get("children", []):
                d = ch.get("data", {})
                if d.get("over_18") or d.get("stickied"):
                    continue
                out.append(TrendItem(
                    source="reddit", title=d.get("title", ""), traffic=int(d.get("ups", 0)),
                    context=[f"r/{d.get('subreddit', sub)}"], url="https://www.reddit.com" + d.get("permalink", ""),
                ))
    return out


def parse_atom(xml_text: str, sub: str) -> list[TrendItem]:
    root = ET.fromstring(xml_text)
    out = []
    for e in root.findall("a:entry", ATOM):
        title = (e.findtext("a:title", default="", namespaces=ATOM) or "").strip()
        link = e.find("a:link", ATOM)
        if title:
            out.append(TrendItem(source="reddit", title=title, context=[f"r/{sub}"],
                                 url=link.get("href") if link is not None else None))
    return out


def _via_rss(cfg: config.RedditCfg) -> list[TrendItem]:
    out = []
    with httpx.Client(timeout=20, headers=UA, follow_redirects=True) as c:
        for sub in cfg.subreddits:
            for attempt in range(3):
                r = c.get(f"https://www.reddit.com/r/{sub}/{cfg.sort}/.rss", params={"limit": cfg.per_sub})
                if r.status_code != 429:
                    break
                time.sleep(15 * (attempt + 1))  # unauthenticated RSS is heavily rate limited
            if r.status_code == 200:
                out.extend(parse_atom(r.text, sub)[: cfg.per_sub])
            else:
                _log.info("reddit r/%s -> HTTP %s", sub, r.status_code)
            time.sleep(6)
    return out


def fetch(cfg: config.RedditCfg) -> list[TrendItem]:
    cid, sec = config.secret("REDDIT_CLIENT_ID"), config.secret("REDDIT_CLIENT_SECRET")
    if cid and sec:
        return _via_api(cfg, cid, sec)
    return _via_rss(cfg)
