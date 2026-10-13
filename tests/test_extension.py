import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from pto import config, db as dbmod, extjobs, notify, uploaders
from pto.db import DB, now_iso

CFG = config.load()


@pytest.fixture()
def tmpdb(tmp_path, monkeypatch):
    d = DB(tmp_path / "t.sqlite3")
    monkeypatch.setattr(dbmod, "_db", d)
    monkeypatch.setattr(notify, "toast", lambda *a, **k: None)
    return d


def _design(d: DB, final: str = "000002/sticker.png") -> dict:
    now = now_iso()
    did = d.insert("designs", {"kind": "sticker", "style": "meme_text", "status": "ready", "title": "Sleepy Capybara Coffee",
                               "tags": ["capybara", "coffee", "funny"], "description": "A tired capybara.",
                               "files": {"final": final}, "created_at": now, "updated_at": now, "ready_at": now})
    return d.design(did)


def test_redbubble_payload_is_complete_and_serializable(tmpdb):
    up = uploaders.get("redbubble")
    p = up.extension_payload(_design(tmpdb), dry_run=True)
    json.dumps(p)
    assert p["url"].startswith("https://www.redbubble.com/portfolio/images/")
    for st in p["steps"]:
        if "key" in st:
            assert p["selectors"].get(st["key"]), st["key"]
    assert p["selectors"]["submit"] and p["still_on"] == ["/portfolio/images/new", "/duplicate"]
    fill = {s["key"]: s.get("value") for s in p["steps"] if s["op"] == "fill"}
    assert fill["tags"] == "capybara, coffee, funny" and fill["title"] == "Sleepy Capybara Coffee"


def test_template_work_uses_copy_settings_url(tmpdb, monkeypatch):
    up = uploaders.get("redbubble")
    monkeypatch.setattr(up.pcfg, "template_work_id", "12345")
    assert up.url_for({}) == "https://www.redbubble.com/portfolio/images/12345/duplicate"


def test_extension_api_job_lifecycle(tmpdb):
    c = TestClient(__import__("pto.web.app", fromlist=["app"]).app)
    hdr = {"X-PTO-Token": extjobs.token()}
    assert c.post("/api/ext/next").status_code == 403
    assert c.post("/api/ext/next", headers={"X-PTO-Token": "wrong"}).status_code == 403
    assert c.post("/api/ext/next", headers=hdr).status_code == 204
    design = _design(tmpdb)
    jid = uploaders.queue_for_extension("redbubble", design, dry_run=False)
    assert extjobs.pending("redbubble")["id"] == jid
    job = c.post("/api/ext/next", headers=hdr).json()
    assert job["id"] == jid and job["kind"] == "upload" and job["steps"]
    assert c.post("/api/ext/next", headers=hdr).status_code == 204  # claimed, not handed out twice
    f = c.get(f"/api/ext/file/{jid}", headers=hdr)
    assert f.status_code == 200 and f.content[:4] == b"\x89PNG"
    r = c.post("/api/ext/result", headers=hdr, json={"job_id": jid, "status": "success", "url": "https://www.redbubble.com/i/sticker/x"})
    assert r.json() == {"recorded": True}
    assert c.post("/api/ext/result", headers=hdr, json={"job_id": jid, "status": "success"}).json() == {"recorded": False}
    assert tmpdb.uploads_today("redbubble") == 1
    assert tmpdb.next_for_platform("redbubble", "sticker") is None
    assert tmpdb.get_kv("next_upload:redbubble")
    assert extjobs.pending("redbubble") is None


def test_tick_queues_one_job_per_platform(tmpdb, monkeypatch):
    monkeypatch.setattr(CFG.browser, "mode", "extension")
    _design(tmpdb), _design(tmpdb)
    assert uploaders.tick(force=True, cfg=CFG) == ["redbubble:queued"]
    assert uploaders.tick(force=True, cfg=CFG) == []  # one in flight
    assert tmpdb.scalar("SELECT COUNT(*) FROM ext_jobs") == 1


def test_stale_jobs_expire(tmpdb):
    design = _design(tmpdb)
    jid = uploaders.queue_for_extension("redbubble", design, dry_run=False)
    old = (dt.datetime.now() - dt.timedelta(minutes=CFG.browser.queue_timeout_minutes + 5)).isoformat()
    tmpdb.execute("UPDATE ext_jobs SET created_at = ? WHERE id = ?", (old, jid))
    assert extjobs.expire_stale(CFG) == 1
    assert extjobs.pending("redbubble") is None
    jid2 = uploaders.queue_for_extension("redbubble", design, dry_run=False)
    extjobs.claim()
    old = (dt.datetime.now() - dt.timedelta(minutes=CFG.browser.job_timeout_minutes + 5)).isoformat()
    tmpdb.execute("UPDATE ext_jobs SET claimed_at = ? WHERE id = ?", (old, jid2))
    assert extjobs.expire_stale(CFG) == 1
    assert tmpdb.one("SELECT status FROM uploads ORDER BY id DESC")["status"] == "failed"


def test_cross_origin_requests_blocked(tmpdb):
    c = TestClient(__import__("pto.web.app", fromlist=["app"]).app)
    assert c.post("/api/stop", headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/stop", headers={"Origin": f"http://127.0.0.1:{CFG.web.port}"}).status_code == 200
    assert c.post("/api/stop").status_code == 200  # same-origin fetches / curl send no Origin
