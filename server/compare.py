"""Compare view: the faces of a photo with their scores, for picking the best frame of a burst."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException


def make_router(ctx: dict) -> APIRouter:
    signed_in, store, renderer, culls, drawing = ctx["signed_in"], ctx["store"], ctx["renderer"], ctx["culls"], ctx["drawing"]
    r = APIRouter()

    @r.get("/api/photos/{photo_id}/faces")
    def photo_faces(photo_id: str, user: dict = Depends(signed_in)):
        """Face boxes (fractions of the uploaded preview) and per-face scores: eyes open, sharpness, smile.
        Worked out once per photo and kept (photos.faces)."""
        with store._db() as db:
            row = db.execute("SELECT wedding_id FROM photos WHERE id=?", (photo_id,)).fetchone()
        if row is None or store.owner_of(row["wedding_id"]) != user["id"]:
            raise HTTPException(404, "no such photo")
        p = store.photo(row["wedding_id"], photo_id)
        if p is None:
            raise HTTPException(404, "no such photo")
        try:
            with drawing:
                faces = culls.faces(row["wedding_id"], p, renderer)
        except FileNotFoundError:
            raise HTTPException(404, "no such photo")
        return {"faces": faces, "w": p["meta"].get("width"), "h": p["meta"].get("height")}

    return r
