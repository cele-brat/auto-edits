"""Walk-through guide for new photographers: shown each time the app opens until they press Close.
Kept per account on the server (not in the browser), so once closed it stays closed on every device."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException

SCHEMA = """
CREATE TABLE IF NOT EXISTS guide_closed (
    user_id TEXT PRIMARY KEY, closed REAL NOT NULL
);
"""


class Guide:
    def __init__(self, store):
        self.store = store
        with store._db() as db:
            db.executescript(SCHEMA)

    def closed(self, user_id: str) -> bool:
        with self.store._db() as db:
            return db.execute("SELECT 1 FROM guide_closed WHERE user_id=?", (user_id,)).fetchone() is not None

    def close(self, user_id: str) -> None:
        with self.store._db() as db:
            db.execute("INSERT OR IGNORE INTO guide_closed (user_id, closed) VALUES (?,?)", (user_id, time.time()))


def make_router(ctx: dict) -> APIRouter:
    signed_in, store = ctx["signed_in"], ctx["store"]
    guide = Guide(store)
    r = APIRouter()

    @r.get("/api/guide")
    def guide_state(user: dict = Depends(signed_in)):
        # An admin looking at someone's account never sees (or closes) their guide.
        return {"show": not user.get("viewing_as") and not guide.closed(user["id"])}

    @r.post("/api/guide/close")
    def guide_close(user: dict = Depends(signed_in)):
        if user.get("viewing_as"):
            raise HTTPException(403, "Not while viewing someone else's account.")
        guide.close(user["id"])
        return {"show": False}

    return r
