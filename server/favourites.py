"""Preset favourites: a star on a preset, kept per account (not in the browser), with the Amount
the photographer last used with it, so a favourite comes back at the strength they like."""
from __future__ import annotations

import time

from fastapi import APIRouter, Body, Depends, HTTPException

SCHEMA = """
CREATE TABLE IF NOT EXISTS favourites (
    user_id TEXT NOT NULL, preset_id TEXT NOT NULL, amount REAL NOT NULL DEFAULT 1.0, created REAL NOT NULL,
    PRIMARY KEY (user_id, preset_id)
);
"""


class Favourites:
    def __init__(self, store):
        self.store = store
        with store._db() as db:
            db.executescript(SCHEMA)

    def for_user(self, user_id: str) -> dict[str, float]:
        with self.store._db() as db:
            return {r["preset_id"]: float(r["amount"]) for r in db.execute(
                "SELECT preset_id, amount FROM favourites WHERE user_id=? ORDER BY created", (user_id,))}

    def set(self, user_id: str, preset_id: str, on: bool, amount: float | None = None) -> None:
        with self.store._db() as db:
            if not on:
                db.execute("DELETE FROM favourites WHERE user_id=? AND preset_id=?", (user_id, preset_id))
                return
            r = db.execute("SELECT amount FROM favourites WHERE user_id=? AND preset_id=?", (user_id, preset_id)).fetchone()
            if r is None:
                db.execute("INSERT INTO favourites (user_id, preset_id, amount, created) VALUES (?,?,?,?)",
                           (user_id, preset_id, 1.0 if amount is None else amount, time.time()))
            elif amount is not None:
                db.execute("UPDATE favourites SET amount=? WHERE user_id=? AND preset_id=?", (amount, user_id, preset_id))

    def forget_preset(self, preset_id: str) -> None:
        with self.store._db() as db:
            db.execute("DELETE FROM favourites WHERE preset_id=?", (preset_id,))


def make_router(ctx: dict) -> APIRouter:
    signed_in, store, renderer = ctx["signed_in"], ctx["store"], ctx["renderer"]
    favs = Favourites(store)
    ctx["favourites"] = favs
    r = APIRouter()

    @r.get("/api/presets/favourites")
    def list_favourites(user: dict = Depends(signed_in)):
        allowed = renderer.allowed(user["id"])
        return {"favourites": {k: v for k, v in favs.for_user(user["id"]).items() if k in allowed}}

    @r.put("/api/presets/{preset_id}/favourite")
    def set_favourite(preset_id: str, body: dict = Body(default={}), user: dict = Depends(signed_in)):
        """{"on": true/false, "amount": 0..2}: star or unstar, and remember the Amount for this favourite."""
        if user.get("viewing_as"):
            raise HTTPException(403, "Not while viewing someone else's account.")
        if preset_id not in renderer.allowed(user["id"]):
            raise HTTPException(404, "no such preset")
        on = body.get("on", True) is not False
        amount = None
        if body.get("amount") is not None:
            try:
                amount = round(min(2.0, max(0.0, float(body["amount"]))), 3)
            except (TypeError, ValueError):
                raise HTTPException(422, "amount must be a number from 0 to 2")
        favs.set(user["id"], preset_id, on, amount)
        return {"on": on, "amount": favs.for_user(user["id"]).get(preset_id)}

    return r
