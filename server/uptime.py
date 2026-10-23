"""GET /healthz: for an outside uptime monitor (deploy/UPTIME.md).

200 with {"ok": true, "version", "db": "ok", "worker_heartbeat_age_s"} when the database answers and a
worker has written its heartbeat in the last HEARTBEAT_MINUTES; otherwise 503 with the same shape and
ok false. Public, no secrets, no rate limit: a monitor asks every minute.
"""
from __future__ import annotations

import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from . import VERSION, health


def status(store, now: float | None = None) -> dict:
    now = now or time.time()
    out = {"ok": True, "version": VERSION, "db": "ok", "worker_heartbeat_age_s": None}
    try:
        with store._db() as db:
            db.executescript(health.SCHEMA)
            beats = [float(r[0]) for r in db.execute("SELECT v FROM settings WHERE k LIKE 'heartbeat:%'")]
    except Exception as e:
        out.update(ok=False, db=f"error: {type(e).__name__}")
        return out
    if not beats:
        out["ok"] = False
        return out
    age = max(0, int(now - max(beats)))
    out["worker_heartbeat_age_s"] = age
    if age > health.HEARTBEAT_MINUTES * 60:
        out["ok"] = False
    return out


def router(ctx: dict) -> APIRouter:
    r = APIRouter()
    store = ctx["store"]

    @r.api_route("/healthz", methods=["GET", "HEAD"])
    def healthz():
        s = status(store)
        return JSONResponse(s, status_code=200 if s["ok"] else 503, headers={"Cache-Control": "no-store"})

    return r
