"""Platform registry and the upload tick that posts ready designs within each platform's limits."""
from __future__ import annotations

import datetime as dt
import random
import threading

from .. import config, db as dbmod, extjobs, log, notify
from .base import Uploader, UploadResult

_log = log.get("uploads")
_lock = threading.Lock()
PLATFORMS = ("redbubble", "teepublic", "displate", "printify")
BROWSER_PLATFORMS = ("redbubble", "teepublic", "displate")


def get(name: str, cfg: config.Config | None = None) -> Uploader:
    if name == "redbubble":
        from .redbubble import RedbubbleUploader as C
    elif name == "teepublic":
        from .teepublic import TeePublicUploader as C
    elif name == "displate":
        from .displate import DisplateUploader as C
    elif name == "printify":
        from .printify import PrintifyUploader as C
    else:
        raise KeyError(f"unknown platform {name!r}")
    return C(cfg)


def uses_extension(name: str, cfg: config.Config | None = None) -> bool:
    cfg = cfg or config.load()
    return name in BROWSER_PLATFORMS and cfg.browser.mode == "extension"


def record(platform: str, design_id: int, status: str, *, url: str | None = None, error: str | None = None,
           screenshot: str | None = None) -> None:
    """Store an upload outcome, alert on problems, and schedule the platform's next slot."""
    cfg = config.load()
    db = dbmod.get()
    db.insert("uploads", {"design_id": design_id, "platform": platform, "status": status, "url": url, "error": error,
                          "screenshot": screenshot, "created_at": dbmod.now_iso()})
    _log.info("%s design %s -> %s %s", platform, design_id, status, url or error or "")
    pc = getattr(cfg.platforms, platform)
    gap = pc.min_gap_minutes + random.uniform(0, pc.jitter_minutes)
    if status == "needs_login":
        how = "log in to it in your Chrome" if uses_extension(platform, cfg) else f"run `pto login {platform}`"
        notify.toast(f"{platform.title()} logged out", f"Uploads are paused for it: {how}.")
        gap = 120
    elif status in ("failed", "captcha_timeout"):
        notify.toast(f"{platform.title()} upload failed", (error or "")[:200])
        gap = max(gap, 45)
    db.set_kv(f"next_upload:{platform}", (dt.datetime.now() + dt.timedelta(minutes=gap)).isoformat(timespec="seconds"))


def upload_one(platform: str, design: dict, *, dry_run: bool | None = None, cfg: config.Config | None = None) -> UploadResult:
    """Upload right now in this process (Printify API, or Playwright browser mode)."""
    cfg = cfg or config.load()
    up = get(platform, cfg)
    dry = (not up.pcfg.live) if dry_run is None else dry_run
    _log.info("uploading design %d to %s%s", design["id"], platform, " (dry run)" if dry else "")
    try:
        res = up.upload(design, dry_run=dry)
    except Exception as e:  # noqa: BLE001 - record and keep the scheduler alive
        _log.exception("upload crashed")
        res = UploadResult("failed", error=f"{type(e).__name__}: {e}")
    record(platform, design["id"], res.status, url=res.url, error=res.error, screenshot=res.screenshot)
    return res


def queue_for_extension(platform: str, design: dict | None, *, dry_run: bool, kind: str = "upload",
                        cfg: config.Config | None = None) -> int:
    payload = get(platform, cfg).extension_payload(design, dry_run=dry_run, kind=kind)
    return extjobs.enqueue(platform, design["id"] if design else None, payload, kind=kind)


def tick(*, force: bool = False, cfg: config.Config | None = None) -> list[str]:
    """Post (or queue for the Chrome extension) at most one design per platform, within limits and windows."""
    cfg = cfg or config.load()
    db = dbmod.get()
    if db.get_kv("paused") == "1" and not force:
        return []
    extjobs.expire_stale(cfg)
    now = dt.datetime.now()
    if not force and not cfg.schedule.upload_window.contains(now.time()):
        return []
    if not _lock.acquire(blocking=False):
        return []
    done = []
    try:
        for name in PLATFORMS:
            pc = getattr(cfg.platforms, name)
            if not pc.enabled or db.uploads_today(name) >= pc.daily_limit:
                continue
            nxt = db.get_kv(f"next_upload:{name}")
            if nxt and not force and now < dt.datetime.fromisoformat(nxt):
                continue
            if uses_extension(name, cfg) and extjobs.pending(name):
                continue  # one job in flight per platform
            kind = "poster" if name == "displate" else "sticker"
            design = db.next_for_platform(name, kind, cfg.licenses_for(name))
            if not design:
                continue
            if uses_extension(name, cfg):
                queue_for_extension(name, design, dry_run=not pc.live, cfg=cfg)
                done.append(f"{name}:queued")
            else:
                done.append(f"{name}:{upload_one(name, design, cfg=cfg).status}")
        return done
    finally:
        _lock.release()
