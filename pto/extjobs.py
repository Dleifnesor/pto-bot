"""Job queue between the bot and the PTO Uploader Chrome extension (which runs in your everyday Chrome)."""
from __future__ import annotations

import datetime as dt
import json
import secrets
import time

from . import config, db as dbmod, log, notify

_log = log.get("extjobs")


def token(cfg: config.Config | None = None) -> str:
    """Shared secret the extension sends as X-PTO-Token (stops web pages from talking to the bot's API)."""
    cfg = cfg or config.load()
    p = cfg.paths.data_dir / "ext_token.txt"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    return p.read_text(encoding="utf-8").strip()


def pending(platform: str) -> dict | None:
    return dbmod.get().one("SELECT * FROM ext_jobs WHERE platform = ? AND status IN ('queued', 'claimed') ORDER BY id LIMIT 1",
                           (platform,))


def enqueue(platform: str, design_id: int | None, payload: dict, kind: str = "upload") -> int:
    jid = dbmod.get().insert("ext_jobs", {"platform": platform, "kind": kind, "design_id": design_id, "payload": payload,
                                          "status": "queued", "created_at": dbmod.now_iso()})
    _log.info("queued %s job %d for %s (design %s)", kind, jid, platform, design_id)
    return jid


def claim() -> dict | None:
    """Hand the oldest queued job to the extension."""
    db = dbmod.get()
    db.set_kv("ext_last_seen", dbmod.now_iso())
    job = db.one("SELECT * FROM ext_jobs WHERE status = 'queued' ORDER BY id LIMIT 1")
    if not job:
        return None
    db.update("ext_jobs", job["id"], {"status": "claimed", "claimed_at": dbmod.now_iso()})
    return {"id": job["id"], "kind": job["kind"], **job["payload"]}


def finish(job_id: int, status: str, url: str | None = None, error: str | None = None, data=None) -> dict | None:
    """Record the extension's report. Upload jobs become a row in `uploads`; inspect jobs save a field dump."""
    db = dbmod.get()
    job = db.one("SELECT * FROM ext_jobs WHERE id = ?", (job_id,))
    if not job or job["status"] not in ("queued", "claimed"):
        return None
    result: dict = {"status": status, "url": url, "error": error}
    if job["kind"] == "inspect":
        cfg = config.load()
        out = cfg.paths.data_dir / "screenshots" / job["platform"] / f"inspect-ext-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"url": url, "error": error, "fields": data}, indent=1), encoding="utf-8")
        result["file"] = str(out)
    else:
        from . import uploaders

        uploaders.record(job["platform"], job["design_id"], status, url=url, error=error)
    db.update("ext_jobs", job_id, {"status": "done", "result": result, "finished_at": dbmod.now_iso()})
    _log.info("extension job %d (%s %s) -> %s %s", job_id, job["platform"], job["kind"], status, url or error or "")
    return result


def expire_stale(cfg: config.Config | None = None) -> int:
    cfg = cfg or config.load()
    db = dbmod.get()
    now = dt.datetime.now()
    n = 0
    for job in db.all("SELECT * FROM ext_jobs WHERE status IN ('queued', 'claimed')"):
        if job["status"] == "queued":
            age = now - dt.datetime.fromisoformat(job["created_at"])
            if age > dt.timedelta(minutes=cfg.browser.queue_timeout_minutes):
                db.update("ext_jobs", job["id"], {"status": "expired", "finished_at": dbmod.now_iso()})
                notify.toast("Uploads waiting for Chrome",
                             f"{job['platform'].title()} upload wasn't picked up. Is Chrome ({cfg.browser.chrome_profile}) open with the PTO extension?")
                n += 1
        else:
            age = now - dt.datetime.fromisoformat(job["claimed_at"])
            if age > dt.timedelta(minutes=cfg.browser.job_timeout_minutes):
                finish(job["id"], "failed", error="the Chrome extension stopped responding mid-upload")
                db.update("ext_jobs", job["id"], {"status": "expired"})
                n += 1
    return n


def wait(job_id: int, timeout_s: float) -> dict | None:
    """Block until a job finishes (used by CLI commands). Returns the job row, or None on timeout."""
    db = dbmod.get()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = db.one("SELECT * FROM ext_jobs WHERE id = ?", (job_id,))
        if job and job["status"] in ("done", "expired"):
            return job
        time.sleep(2)
    return None
