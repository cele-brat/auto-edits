"""Walk-through guide for new photographers, shown each time the app opens.
Close hides it for this login (until they log out or the session ends); Close with "Don't show again"
ticked hides it for good, kept per account, so it stays hidden on every device."""
from __future__ import annotations

import time

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException

SESSION_SECONDS = 60 * 24 * 3600          # longer than any login session; it ends with the session anyway

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
    signed_in, store, state = ctx["signed_in"], ctx["store"], ctx["state"]
    guide = Guide(store)
    r = APIRouter()

    @r.get("/api/guide")
    def guide_state(user: dict = Depends(signed_in), autoedits: str = Cookie(default="")):
        # An admin looking at someone's account never sees (or closes) their guide.
        hidden = (user.get("viewing_as") or guide.closed(user["id"])
                  or (autoedits and state.get("guide-session", autoedits) == user["id"]))
        return {"show": not hidden}

    @r.post("/api/guide/close")
    def guide_close(body: dict = Body(default={}), user: dict = Depends(signed_in), autoedits: str = Cookie(default="")):
        """{"forever": true} when "Don't show again" is ticked; otherwise only for this login."""
        if user.get("viewing_as"):
            raise HTTPException(403, "Not while viewing someone else's account.")
        if body.get("forever"):
            guide.close(user["id"])
        elif autoedits:
            state.put("guide-session", autoedits, user["id"], SESSION_SECONDS)
        return {"show": False, "forever": bool(body.get("forever"))}

    return r
