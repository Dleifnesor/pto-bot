"""API used by the PTO Uploader Chrome extension. Every call must carry the X-PTO-Token header."""
from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import config, db as dbmod, extjobs


def require_token(x_pto_token: str | None = Header(default=None)) -> None:
    if not x_pto_token or not secrets.compare_digest(x_pto_token, extjobs.token()):
        raise HTTPException(403, "bad or missing extension token")


router = APIRouter(prefix="/api/ext", dependencies=[Depends(require_token)])


class Result(BaseModel):
    job_id: int
    status: str
    url: str | None = None
    error: str | None = None
    data: Any = None


@router.post("/next")
def next_job():
    job = extjobs.claim()
    if not job:
        return Response(status_code=204)
    return job


@router.get("/file/{job_id}")
def job_file(job_id: int):
    job = dbmod.get().one("SELECT * FROM ext_jobs WHERE id = ?", (job_id,))
    if not job or not job["design_id"]:
        raise HTTPException(404)
    design = dbmod.get().design(job["design_id"])
    path = config.load().paths.designs / design["files"]["final"]
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path)


@router.post("/result")
def job_result(r: Result):
    allowed = {"success", "dry_run", "failed", "needs_login", "captcha_timeout", "inspect"}
    status = r.status if r.status in allowed else "failed"
    res = extjobs.finish(r.job_id, status, url=r.url, error=(r.error or None) and r.error[:1500], data=r.data)
    return {"recorded": res is not None}
