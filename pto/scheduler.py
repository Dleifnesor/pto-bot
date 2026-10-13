"""Always-on scheduling: trend refresh, the overnight GPU window, and spaced-out uploads."""
from __future__ import annotations

import datetime as dt
import threading

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import config, db as dbmod, log, notify, pipeline, trends, uploaders

_log = log.get("scheduler")
_sched: BackgroundScheduler | None = None


def paused() -> bool:
    return dbmod.get().get_kv("paused") == "1"


def job_trends() -> None:
    if paused():
        return
    try:
        trends.collect()
    except Exception:  # noqa: BLE001
        _log.exception("trend refresh failed")


def job_night(force: bool = False) -> None:
    cfg = config.load()
    win = cfg.schedule.generation_window
    now = dt.datetime.now()
    if paused() and not force:
        return
    if not force and not win.contains(now.time()):
        _log.info("outside generation window; skipping")
        return
    deadline = win.end_after(now) if win.contains(now.time()) else now + dt.timedelta(hours=2)
    dbmod.get().set_kv("last_night_run", now.isoformat(timespec="seconds"))
    try:
        res = pipeline.Pipeline(cfg).run(deadline=deadline, reason="manual" if force else "night run")
        _log.info("night run finished: %s", res)
    except Exception as e:  # noqa: BLE001
        _log.exception("night run failed")
        notify.toast("Night run failed", str(e)[:200])


def job_uploads() -> None:
    try:
        done = uploaders.tick()
        if done:
            _log.info("upload tick: %s", ", ".join(done))
    except Exception:  # noqa: BLE001
        _log.exception("upload tick failed")


def _missed_tonight(cfg: config.Config) -> bool:
    """True if we're inside the window and tonight's run hasn't started (e.g. the PC was off at 01:00)."""
    win = cfg.schedule.generation_window
    now = dt.datetime.now()
    if not win.contains(now.time()):
        return False
    last = dbmod.get().get_kv("last_night_run")
    if not last:
        return True
    return now - dt.datetime.fromisoformat(last) > dt.timedelta(hours=12)


def start() -> BackgroundScheduler:
    global _sched
    cfg = config.load()
    s = BackgroundScheduler(executors={"default": ThreadPoolExecutor(3)},
                            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600})
    h, m = (int(x) for x in cfg.schedule.generation_window.start.split(":"))
    now = dt.datetime.now()
    s.add_job(job_trends, IntervalTrigger(hours=cfg.schedule.trend_refresh_hours), id="trends", name="Refresh trends",
              next_run_time=now + dt.timedelta(seconds=20))
    s.add_job(job_night, CronTrigger(hour=h, minute=m), id="night", name="Overnight generation")
    s.add_job(job_uploads, IntervalTrigger(minutes=cfg.schedule.upload_tick_minutes), id="uploads", name="Upload tick",
              next_run_time=now + dt.timedelta(minutes=1))
    if _missed_tonight(cfg):
        s.add_job(job_night, id="night_catchup", name="Overnight generation (catch-up)", next_run_time=now + dt.timedelta(seconds=45))
    s.start()
    _sched = s
    _log.info("scheduler started")
    return s


def jobs() -> list[dict]:
    if not _sched:
        return []
    return [{"id": j.id, "name": j.name, "next_run": j.next_run_time.isoformat(timespec="seconds") if j.next_run_time else None}
            for j in _sched.get_jobs()]


def run_now(job: str) -> None:
    fn = {"trends": job_trends, "night": lambda: job_night(force=True), "uploads": lambda: uploaders.tick(force=True)}[job]
    threading.Thread(target=fn, name=f"manual-{job}", daemon=True).start()


def shutdown() -> None:
    pipeline.stop_event.set()
    if _sched:
        _sched.shutdown(wait=False)
