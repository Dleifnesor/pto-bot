"""Redbubble uploader (no public API: fills redbubble.com/portfolio/images/new in a logged-in browser).

Selectors are best-known defaults; run `pto inspect redbubble` and override them in config.yaml if Redbubble changes
its form. If `template_work_id` is set, the work's "copy settings" page is used so products, colors and markups
match a work you configured by hand.
"""
from __future__ import annotations

from .base import BrowserUploader


class RedbubbleUploader(BrowserUploader):
    name = "redbubble"
    default_urls = {
        "login": "https://www.redbubble.com/auth/login",
        "new": "https://www.redbubble.com/portfolio/images/new",
        "duplicate": "https://www.redbubble.com/portfolio/images/{id}/duplicate",
    }
    default_selectors = {
        "file": ["#select-image-base", "input[type=file][name*='image']", "input[type=file]"],
        "title": ["#work_title_en", "input[name='work[title_en]']", "label:Title"],
        "tags": ["#work_tag_field_en", "textarea[name='work[tag_field_en]']", "label:Tags"],
        "description": ["#work_description_en", "textarea[name='work[description_en]']", "label:Description"],
        "safe_for_work": ["#work_safe_for_work_true", "input[name='work[safe_for_work]'][value='true']"],
        "ai_disclosure": ["input[type=checkbox][name*='ai_generated' i]", "input[type=checkbox][id*='ai_generated' i]",
                          "input[type=checkbox][id*='aigenerated' i]"],
        "rights": ["#rightsDeclaration", "input[name='rightsDeclaration']", "input[type=checkbox][name*='rights' i]"],
        "upload_done": [".single-upload.has-image", ".image-preview img", "img[src*='ih1.redbubble.net']"],
        "submit": ["#submit-work", "button[type=submit]:has-text('Save')", "input[type=submit]"],
    }
    login_markers = ["/auth/login", "/signin", "/login"]

    def url_for(self, design: dict) -> str:
        tmpl = getattr(self.pcfg, "template_work_id", None)
        return self.urls["duplicate"].format(id=tmpl) if tmpl else self.urls["new"]

    def still_on(self) -> list[str]:
        return ["/portfolio/images/new", "/duplicate"]

    def steps(self, design: dict) -> list[dict]:
        return [
            {"op": "file", "key": "file"},
            # Redbubble processes the image before the rest of the form is usable
            {"op": "wait", "key": "upload_done", "timeout_s": 180, "fallback_s": 30},
            {"op": "pause", "seconds": 3},
            {"op": "fill", "key": "title", "value": design["title"]},
            {"op": "fill", "key": "tags", "value": ", ".join(design["tags"][: self.pcfg.max_tags])},
            {"op": "fill", "key": "description", "value": design.get("description") or design["title"], "optional": True},
            {"op": "check", "key": "safe_for_work", "optional": True},
            {"op": "check", "key": "ai_disclosure", "optional": True},
            {"op": "check", "key": "rights"},
        ]
