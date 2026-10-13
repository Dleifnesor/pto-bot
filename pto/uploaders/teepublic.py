"""TeePublic uploader (no public API). UNTESTED against a live account: log in, run `pto inspect teepublic`
and adjust selectors in config.yaml before enabling."""
from __future__ import annotations

from .base import BrowserUploader


class TeePublicUploader(BrowserUploader):
    name = "teepublic"
    default_urls = {
        "login": "https://www.teepublic.com/login",
        "new": "https://www.teepublic.com/design/quick_create",
    }
    default_selectors = {
        "file": ["input[type=file]"],
        "title": ["#design_design_title", "input[name*='title' i]", "label:Title"],
        "description": ["#design_design_description", "textarea[name*='description' i]", "label:Description"],
        "main_tag": ["#design_primary_tag", "input[name*='primary_tag' i]", "label:Main Tag"],
        "tags": ["#design_secondary_tags", "input[name*='secondary_tags' i]", "label:Supporting Tags"],
        "not_mature": ["#design_content_flag_false", "input[name*='content_flag' i][value='false']"],
        "terms": ["#terms", "input[type=checkbox][name*='terms' i]", "input[type=checkbox][id*='agree' i]"],
        "upload_done": [".upload-complete", "img.design-preview", ".m-uploader__preview img"],
        "submit": ["#submit-design", "button[type=submit]", "input[type=submit]"],
    }

    def steps(self, design: dict) -> list[dict]:
        tags = design["tags"][: self.pcfg.max_tags + 1]
        main, rest = (tags[0], tags[1:]) if tags else (design["title"].split()[0].lower(), [])
        return [
            {"op": "file", "key": "file"},
            {"op": "wait", "key": "upload_done", "timeout_s": 180, "fallback_s": 30},
            {"op": "fill", "key": "title", "value": design["title"]},
            {"op": "fill", "key": "description", "value": design.get("description") or design["title"], "optional": True},
            {"op": "fill", "key": "main_tag", "value": main},
            {"op": "fill", "key": "tags", "value": ", ".join(rest), "optional": True},
            {"op": "check", "key": "not_mature", "optional": True},
            {"op": "check", "key": "terms"},
        ]
