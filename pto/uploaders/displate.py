"""Displate uploader (no public API; requires an approved artist account). UNTESTED against a live account:
log in, run `pto inspect displate` and adjust urls/selectors in config.yaml before enabling."""
from __future__ import annotations

from .base import BrowserUploader


class DisplateUploader(BrowserUploader):
    name = "displate"
    kind = "poster"
    default_urls = {
        "login": "https://displate.com/auth/signin",
        "new": "https://displate.com/upload",
    }
    default_selectors = {
        "file": ["input[type=file]"],
        "title": ["input[name*='title' i]", "label:Title"],
        "description": ["textarea[name*='description' i]", "label:Description"],
        "tags": ["input[name*='tag' i]", "label:Tags"],
        "upload_done": ["img[src^='blob:']", "img[class*='preview' i]", "[class*='uploaded' i]"],
        "submit": ["button[type=submit]", "button:has-text('Publish')", "button:has-text('Submit')"],
    }

    def steps(self, design: dict) -> list[dict]:
        return [
            {"op": "file", "key": "file"},
            {"op": "wait", "key": "upload_done", "timeout_s": 180, "fallback_s": 30},
            {"op": "fill", "key": "title", "value": design["title"]},
            {"op": "fill", "key": "description", "value": design.get("description") or design["title"], "optional": True},
            {"op": "tags_enter", "key": "tags", "values": design["tags"][: self.pcfg.max_tags]},
        ]
