"""Local dashboard + JSON API (http://127.0.0.1:8765)."""
from __future__ import annotations

import datetime as dt
import shutil
import threading
from pathlib import Path

import httpx
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .. import config, db as dbmod, log, pipeline, scheduler, uploaders
from . import ext

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="PTO Bot", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.include_router(ext.router)


@app.middleware("http")
async def same_origin_only(request: Request, call_next):
    """Websites open in your browser can send requests to localhost; only the dashboard and the extension may."""
    origin = request.headers.get("origin")
    if origin and not origin.startswith("chrome-extension://"):
        port = _cfg().web.port
        if origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
            return JSONResponse({"detail": "cross-origin request blocked"}, status_code=403)
    return await call_next(request)


def _cfg() -> config.Config:
    return config.load()


def _safe_file(base: Path, rel: str) -> Path:
    p = (base / rel).resolve()
    if not p.is_relative_to(base.resolve()) or not p.is_file():
        raise HTTPException(404)
    return p


def _alive(url: str) -> bool:
    try:
        return httpx.get(url, timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/files/{rel:path}")
def files(rel: str):
    return FileResponse(_safe_file(_cfg().paths.designs, rel))


@app.get("/shots/{rel:path}")
def shots(rel: str):
    return FileResponse(_safe_file(_cfg().paths.data_dir / "screenshots", rel))


@app.get("/api/status")
def status():
    cfg, db = _cfg(), dbmod.get()
    counts = {f"{r['kind']}:{r['status']}": r["n"] for r in db.all("SELECT kind, status, COUNT(*) n FROM designs GROUP BY kind, status")}
    plats = {}
    for name in uploaders.PLATFORMS:
        pc = getattr(cfg.platforms, name)
        plats[name] = {"enabled": pc.enabled, "live": pc.live, "today": db.uploads_today(name), "limit": pc.daily_limit,
                       "next": db.get_kv(f"next_upload:{name}")}
    usage = shutil.disk_usage(cfg.paths.data_dir)
    return {
        "paused": db.get_kv("paused") == "1",
        "generating": pipeline._run_lock.locked(),
        "jobs": scheduler.jobs(),
        "comfyui": _alive(cfg.comfyui.url.rstrip("/") + "/system_stats"),
        "ollama": _alive(cfg.ollama.url.rstrip("/") + "/api/tags"),
        "counts": counts,
        "platforms": plats,
        "trends_last_fetch": db.get_kv("trends_last_fetch"),
        "last_night_run": db.get_kv("last_night_run"),
        "generation_window": cfg.schedule.generation_window.model_dump(),
        "upload_window": cfg.schedule.upload_window.model_dump(),
        "disk_free_gb": round(usage.free / 1e9, 1),
        "browser": {"mode": cfg.browser.mode, "profile": cfg.browser.chrome_profile,
                    "extension_last_seen": db.get_kv("ext_last_seen"),
                    "jobs_in_flight": db.scalar("SELECT COUNT(*) FROM ext_jobs WHERE status IN ('queued', 'claimed')")},
        "now": dt.datetime.now().isoformat(timespec="seconds"),
    }


@app.post("/api/pause")
def pause(paused: bool = Body(..., embed=True)):
    dbmod.get().set_kv("paused", "1" if paused else "0")
    if paused:
        pipeline.stop_event.set()
    return {"paused": paused}


@app.post("/api/run/{job}")
def run(job: str):
    if job not in ("trends", "night", "uploads"):
        raise HTTPException(400, "job must be trends | night | uploads")
    scheduler.run_now(job)
    return {"started": job}


@app.post("/api/stop")
def stop():
    pipeline.stop_event.set()
    return {"stopping": True}


@app.get("/api/designs")
def designs(status: str | None = None, kind: str | None = None, limit: int = 60, offset: int = 0):
    where, params = [], []
    if status:
        where.append("d.status = ?")
        params.append(status)
    if kind:
        where.append("d.kind = ?")
        params.append(kind)
    sql = f"""SELECT d.id, d.kind, d.style, d.status, d.title, d.slogan, d.tags, d.files, d.attempts, d.error, d.created_at,
              d.ready_at, t.title AS trend, t.source AS trend_source,
              (SELECT GROUP_CONCAT(u.platform || ':' || u.status) FROM uploads u WHERE u.design_id = d.id) AS posted
              FROM designs d LEFT JOIN trends t ON t.id = d.trend_id
              {"WHERE " + " AND ".join(where) if where else ""} ORDER BY d.id DESC LIMIT ? OFFSET ?"""
    return dbmod.get().all(sql, (*params, limit, offset))


@app.get("/api/designs/{design_id}")
def design(design_id: int):
    db = dbmod.get()
    d = db.design(design_id)
    if not d:
        raise HTTPException(404)
    d["trend"] = db.one("SELECT * FROM trends WHERE id = ?", (d["trend_id"],)) if d["trend_id"] else None
    d["uploads"] = db.all("SELECT * FROM uploads WHERE design_id = ? ORDER BY id DESC", (design_id,))
    return d


@app.post("/api/designs/{design_id}/{action}")
def design_action(design_id: int, action: str):
    db = dbmod.get()
    if action == "retire":
        db.update("designs", design_id, {"status": "retired"})
    elif action == "retry":
        db.update("designs", design_id, {"status": "planned", "attempts": 0})
    else:
        raise HTTPException(400, "action must be retire | retry")
    return {"ok": True}


@app.get("/api/trends")
def trend_list(limit: int = 150):
    return dbmod.get().all("SELECT id, source, region, title, traffic, status, score, score_reason, fetched_at, context "
                           "FROM trends ORDER BY id DESC LIMIT ?", (limit,))


@app.get("/api/uploads")
def upload_list(limit: int = 150):
    return dbmod.get().all("SELECT u.*, d.title FROM uploads u JOIN designs d ON d.id = u.design_id ORDER BY u.id DESC LIMIT ?", (limit,))


@app.get("/api/logs", response_class=PlainTextResponse)
def logs(n: int = 400):
    return "\n".join(log.tail(n))


@app.get("/api/config", response_class=PlainTextResponse)
def get_config():
    return config.CONFIG_PATH.read_text(encoding="utf-8")


@app.put("/api/config")
def put_config(text: str = Body(..., media_type="text/plain")):
    try:
        config.validate_text(text)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"invalid config: {e}") from e
    backup = config.CONFIG_PATH.with_suffix(f".bak-{dt.datetime.now():%Y%m%d-%H%M%S}.yaml")
    shutil.copy2(config.CONFIG_PATH, backup)
    config.CONFIG_PATH.write_text(text, encoding="utf-8")
    config.load(reload=True)
    return {"saved": True, "backup": backup.name, "note": "schedule changes apply after restarting the bot"}


@app.post("/api/login/{platform}")
def login(platform: str):
    if platform not in uploaders.PLATFORMS or platform == "printify":
        raise HTTPException(400, "browser login is for redbubble | teepublic | displate")
    threading.Thread(target=lambda: uploaders.get(platform).login(), daemon=True).start()
    return {"opened": platform, "mode": _cfg().browser.mode}


@app.post("/api/inspect/{platform}")
def inspect(platform: str):
    """Extension mode: queue a read-only field dump of the platform's upload form."""
    if platform not in uploaders.BROWSER_PLATFORMS or not uploaders.uses_extension(platform):
        raise HTTPException(400, "inspect via the dashboard needs browser.mode: extension")
    jid = uploaders.queue_for_extension(platform, None, dry_run=True, kind="inspect")
    return {"queued": jid}
