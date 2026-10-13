"""Shared uploader plumbing: results, persistent browser-profile sessions, selector lookup, CAPTCHA hand-off."""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

from .. import config, log, notify

_log = log.get("upload")

CAPTCHA_MARKERS = [
    "iframe[title*='challenge' i]",
    "iframe[src*='hcaptcha.com']",
    "iframe[src*='challenges.cloudflare.com']",
    "#challenge-form",
    "#px-captcha",
    "text=/verify (that )?you are (a )?human/i",
    "text=/press (&|and) hold/i",
]


def open_in_chrome(url: str, cfg: config.Config | None = None) -> None:
    """Open `url` as a new tab in the user's own Chrome profile (joins the already-running Chrome)."""
    import subprocess

    cfg = cfg or config.load()
    exe = config.BROWSER_EXES["chrome"]
    subprocess.Popen([str(exe), f"--profile-directory={cfg.browser.chrome_profile}", url])


@dataclass
class UploadResult:
    status: str  # success | dry_run | failed | needs_login | captcha_timeout
    url: str | None = None
    error: str | None = None
    screenshot: str | None = None


class Uploader:
    name = "base"
    kind = "sticker"  # which design kind this platform takes

    def __init__(self, cfg: config.Config | None = None):
        self.cfg = cfg or config.load()
        self.pcfg: config.PlatformCfg = getattr(self.cfg.platforms, self.name)

    def asset_path(self, design: dict) -> Path:
        return self.cfg.paths.designs / design["files"]["final"]

    def upload(self, design: dict, *, dry_run: bool = False) -> UploadResult:
        raise NotImplementedError


class BrowserUploader(Uploader):
    """A platform's upload form, described as data (urls, selectors, steps).

    The same description is executed either by the PTO Uploader extension inside your everyday Chrome
    (browser.mode: extension) or by Playwright in a separate bot-only profile (browser.mode: playwright).
    Step ops: file, wait, pause, fill, check, tags_enter.
    """

    default_urls: dict[str, str] = {}
    default_selectors: dict[str, list[str]] = {}
    login_markers: list[str] = ["/login", "/signin", "/auth"]
    ready_key = "file"
    submit_timeout_s = 300

    @property
    def urls(self) -> dict[str, str]:
        return {**self.default_urls, **self.pcfg.urls}

    def sel(self, key: str) -> list[str]:
        return self.pcfg.selectors.get(key) or self.default_selectors.get(key, [])

    def url_for(self, design: dict) -> str:
        return self.urls["new"]

    def steps(self, design: dict) -> list[dict]:
        raise NotImplementedError

    def still_on(self) -> list[str]:
        """URL fragments that mean we're still on the form (anything else after submit = success)."""
        return [urlparse(self.urls["new"]).path]

    def extension_payload(self, design: dict | None, *, dry_run: bool, kind: str = "upload") -> dict:
        keys = set(self.default_selectors) | set(self.pcfg.selectors)
        return {
            "platform": self.name,
            "url": self.url_for(design) if design and kind == "upload" else self.urls["new"],
            "ready_key": self.ready_key,
            "submit_key": "submit",
            "steps": self.steps(design) if design and kind == "upload" else [],
            "selectors": {k: self.sel(k) for k in sorted(keys)},
            "still_on": self.still_on(),
            "login_markers": self.login_markers,
            "dry_run": dry_run,
            "file_name": self.asset_path(design).name if design and kind == "upload" else None,
            "captcha_wait_minutes": self.pcfg.captcha_wait_minutes,
            "submit_timeout_s": self.submit_timeout_s,
        }

    def run_steps(self, page, design: dict, steps: list[dict]) -> None:
        """Playwright executor for the step list (the extension has a JS twin in extension/page.js)."""
        for st in steps:
            op, key, optional = st["op"], st.get("key"), bool(st.get("optional"))
            if op == "pause":
                self.pause(st["seconds"], st["seconds"] + 1)
            elif op == "wait":
                if self.locate(page, key, timeout_ms=int(st.get("timeout_s", 60) * 1000)) is None and st.get("fallback_s"):
                    page.wait_for_timeout(st["fallback_s"] * 1000)
            elif op == "file":
                loc = self.locate(page, key, state="attached")
                if loc is None:
                    raise LookupError(f"{self.name}: file input not found (selectors: {self.sel(key)})")
                loc.set_input_files(str(self.asset_path(design)))
            elif op == "fill":
                self.fill(page, key, st["value"], required=not optional)
            elif op == "check":
                self.check(page, key, required=not optional)
            elif op == "tags_enter":
                loc = self.locate(page, key)
                if loc is None:
                    if optional:
                        continue
                    raise LookupError(f"{self.name}: field '{key}' not found")
                for v in st["values"]:
                    loc.fill(v)
                    loc.press("Enter")
                    self.pause(0.3, 0.7)
            self.pause(0.5, 1.5)

    def upload(self, design: dict, *, dry_run: bool = False) -> UploadResult:
        with self.session() as page:
            err = self.ensure_ready(page, self.url_for(design), self.ready_key)
            if err:
                return err
            try:
                self.run_steps(page, design, self.steps(design))
            except LookupError as e:
                return UploadResult("failed", error=str(e), screenshot=self.screenshot(page, "field"))
            if dry_run:
                return UploadResult("dry_run", url=page.url, screenshot=self.screenshot(page, f"dry-{design['id']}"))
            submit = self.locate(page, "submit", timeout_ms=10_000)
            if submit is None:
                return UploadResult("failed", error="submit button not found", screenshot=self.screenshot(page, "nosubmit"))
            submit.click()
            if not self.wait_for_success(page, self.still_on(), timeout_s=self.submit_timeout_s):
                return UploadResult("failed", error=f"no confirmation after submit (still at {page.url})",
                                    screenshot=self.screenshot(page, "nosuccess"))
            self.pause(2, 4)
            return UploadResult("success", url=page.url, screenshot=self.screenshot(page, f"done-{design['id']}"))

    @property
    def profile_dir(self) -> Path:
        p = self.cfg.paths.profiles / self.name
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def shots_dir(self) -> Path:
        p = self.cfg.paths.data_dir / "screenshots" / self.name
        p.mkdir(parents=True, exist_ok=True)
        return p

    @contextlib.contextmanager
    def session(self, *, minimized: bool | None = None) -> Iterator["Page"]:  # noqa: F821
        from playwright.sync_api import sync_playwright

        minimized = self.pcfg.start_minimized if minimized is None else minimized
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir), channel=self.cfg.browser.channel, headless=False,
                args=["--start-minimized"] if minimized else [], no_viewport=True,
            )
            ctx.set_default_timeout(30_000)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                yield page
            finally:
                with contextlib.suppress(Exception):
                    ctx.close()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def pause(lo: float = 0.6, hi: float = 1.8) -> None:
        time.sleep(random.uniform(lo, hi))

    def locate(self, page, key: str, timeout_ms: int = 10_000, state: str = "visible"):
        cands = self.sel(key)
        if not cands:
            return None
        each = max(1500, timeout_ms // len(cands))
        for s in cands:
            if s.startswith("label:"):
                loc = page.get_by_label(s[6:])
            elif s.startswith("text:"):
                loc = page.get_by_text(s[5:])
            elif s.startswith("role:"):
                role, _, nm = s[5:].partition("|")
                loc = page.get_by_role(role, name=nm or None)
            else:
                loc = page.locator(s)
            try:
                loc.first.wait_for(state=state, timeout=each)
                return loc.first
            except Exception:  # noqa: BLE001 - try the next candidate
                continue
        return None

    def fill(self, page, key: str, value: str, *, required: bool = True) -> bool:
        loc = self.locate(page, key)
        if loc is None:
            if required:
                raise LookupError(f"{self.name}: could not find field '{key}' (selectors: {self.sel(key)})")
            return False
        loc.click()
        loc.fill("")
        self.pause(0.2, 0.5)
        loc.fill(value)
        self.pause()
        return True

    def check(self, page, key: str, *, required: bool = False) -> bool:
        loc = self.locate(page, key, timeout_ms=5000, state="attached")
        if loc is None:
            if required:
                raise LookupError(f"{self.name}: could not find checkbox '{key}'")
            return False
        try:
            if not loc.is_checked():
                loc.check(force=True)
        except Exception:  # noqa: BLE001 - some "checkboxes" are styled labels
            loc.click(force=True)
        self.pause(0.3, 0.8)
        return True

    def screenshot(self, page, tag: str) -> str:
        path = self.shots_dir / f"{dt.datetime.now():%Y%m%d-%H%M%S}-{tag}.png"
        with contextlib.suppress(Exception):
            page.screenshot(path=str(path), full_page=True)
        return str(path)

    def is_login_page(self, page) -> bool:
        return any(m in page.url for m in self.login_markers)

    def captcha_present(self, page) -> bool:
        for m in CAPTCHA_MARKERS:
            try:
                loc = page.locator(m)
                if loc.count() and loc.first.is_visible():
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def wait_for_human(self, page) -> bool:
        """If a CAPTCHA is showing, ask the user to solve it and wait. Returns True when clear."""
        if not self.captcha_present(page):
            return True
        notify.toast(f"{self.name.title()} needs you", f"A CAPTCHA appeared. Solve it in the {self.cfg.browser.label} window; the upload continues by itself.")
        with contextlib.suppress(Exception):
            page.bring_to_front()
        deadline = time.monotonic() + self.pcfg.captcha_wait_minutes * 60
        while time.monotonic() < deadline:
            time.sleep(5)
            if not self.captcha_present(page):
                return True
        return False

    def ensure_ready(self, page, url: str, ready_key: str) -> UploadResult | None:
        """Navigate and make sure we're logged in, past any CAPTCHA, and the form is there."""
        page.goto(url, wait_until="domcontentloaded")
        self.pause(2, 4)
        if self.is_login_page(page):
            return UploadResult("needs_login", error=f"logged out; run: pto login {self.name}",
                                screenshot=self.screenshot(page, "login"))
        if self.locate(page, ready_key, timeout_ms=15_000, state="attached") is None:
            if not self.wait_for_human(page):
                return UploadResult("captcha_timeout", error="CAPTCHA was not solved in time", screenshot=self.screenshot(page, "captcha"))
            if self.is_login_page(page):
                return UploadResult("needs_login", error=f"logged out; run: pto login {self.name}")
            if self.locate(page, ready_key, timeout_ms=20_000, state="attached") is None:
                return UploadResult("failed", error=f"upload form not found at {page.url} (selector '{ready_key}')",
                                    screenshot=self.screenshot(page, "noform"))
        return None

    def wait_for_success(self, page, still_on: list[str], timeout_s: int = 240) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not any(s in page.url for s in still_on):
                return True
            if self.captcha_present(page) and not self.wait_for_human(page):
                return False
            time.sleep(2)
        return False

    # ---------------------------------------------------------------- commands
    def login(self) -> None:
        """Extension mode: open the login page as a tab in your everyday Chrome profile.
        Playwright mode: open it in the bot-only profile and wait until the window is closed."""
        if self.cfg.browser.mode == "extension":
            open_in_chrome(self.urls["login"], self.cfg)
            return
        with self.session(minimized=False) as page:
            page.goto(self.urls["login"])
            notify.toast(f"Log in to {self.name.title()}", f"Log in in the {self.cfg.browser.label} window, then close it. The session is saved.", open_dashboard=False)
            page.context.wait_for_event("close", timeout=0)

    def inspect(self) -> Path:
        """Dump the upload form's fields so selectors can be tuned. Does not submit anything."""
        out = self.shots_dir / f"inspect-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
        with self.session(minimized=False) as page:
            page.goto(self.urls["new"], wait_until="domcontentloaded")
            self.wait_for_human(page)
            page.wait_for_timeout(5000)
            fields = page.evaluate("""() => Array.from(document.querySelectorAll('input,textarea,select,button,label,[contenteditable=true]')).map(e => ({
                tag: e.tagName.toLowerCase(), type: e.type || null, id: e.id || null, name: e.name || null,
                text: (e.innerText || e.value || e.placeholder || '').slice(0, 80), for: e.htmlFor || null,
                aria: e.getAttribute('aria-label'), visible: !!(e.offsetWidth || e.offsetHeight)}))""")
            out.write_text(json.dumps({"url": page.url, "fields": fields}, indent=1), encoding="utf-8")
            self.screenshot(page, "inspect")
        return out
