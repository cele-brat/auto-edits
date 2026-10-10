"""The Auto Edits server.

Start it with:  AUTOEDITS_KEY=<secret> AUTOEDITS_DATA=/data uvicorn server.main:app

Every /api call must carry the key in the X-Auto-Edits-Key header. The server
refuses to start without a key, so it can never run open by mistake.
"""
from __future__ import annotations

import hmac
import json
import os

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import VERSION
from .store import Store

MAX_PREVIEW_BYTES = 12 * 1024 * 1024      # a 2560 px JPEG is 1 to 3 MB
MIN_FREE_BYTES = 2 * 1024 ** 3            # stop taking photos before the disk is full


class NewWedding(BaseModel):
    name: str
    catalog: str = ""


def create_app(data_dir: str | None = None, key: str | None = None) -> FastAPI:
    key = key if key is not None else os.environ.get("AUTOEDITS_KEY", "")
    if len(key) < 16:
        raise RuntimeError("set AUTOEDITS_KEY to a secret of at least 16 characters")
    store = Store(data_dir or os.environ.get("AUTOEDITS_DATA", "data/server"))
    app = FastAPI(title="Auto Edits", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store

    def signed_in(x_auto_edits_key: str = Header(default="")):
        if not hmac.compare_digest(x_auto_edits_key.encode(), key.encode()):
            raise HTTPException(401, "wrong or missing key")

    def wedding_or_404(wedding_id: str) -> dict:
        w = store.wedding(wedding_id)
        if w is None:
            raise HTTPException(404, "no such wedding")
        return w

    api = [Depends(signed_in)]

    @app.get("/api/ping", dependencies=api)
    def ping():
        return {"ok": True, "version": VERSION, "free_gb": round(store.free_space() / 1024 ** 3, 1)}

    @app.get("/api/weddings", dependencies=api)
    def weddings():
        return {"weddings": store.weddings()}

    @app.post("/api/weddings", dependencies=api)
    def open_wedding(body: NewWedding):
        try:
            w = store.open_wedding(body.name, body.catalog)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {**store.wedding(w["id"]), "is_new": w["is_new"]}

    @app.get("/api/weddings/{wedding_id}", dependencies=api)
    def wedding(wedding_id: str):
        return wedding_or_404(wedding_id)

    @app.delete("/api/weddings/{wedding_id}", dependencies=api)
    def delete_wedding(wedding_id: str):
        wedding_or_404(wedding_id)
        store.delete_wedding(wedding_id)
        return {"deleted": True}

    @app.get("/api/weddings/{wedding_id}/received", dependencies=api)
    def received(wedding_id: str):
        """Lightroom ids already on the server, so an interrupted send can carry on."""
        wedding_or_404(wedding_id)
        return {"uuids": store.received_uuids(wedding_id)}

    @app.post("/api/weddings/{wedding_id}/photos", dependencies=api)
    async def add_photo(wedding_id: str, meta: str = Form(...), preview: UploadFile = File(...)):
        wedding_or_404(wedding_id)
        try:
            info = json.loads(meta)
            if not isinstance(info, dict):
                raise ValueError
        except ValueError:
            raise HTTPException(422, "meta is not valid")
        if not info.get("edited"):               # the plug-in writes an empty list as {}
            info["edited"] = []
        data = await preview.read(MAX_PREVIEW_BYTES + 1)
        if len(data) > MAX_PREVIEW_BYTES:
            raise HTTPException(413, "preview is too large")
        if data[:3] != b"\xff\xd8\xff":
            raise HTTPException(415, "preview must be a JPEG")
        if store.free_space() < MIN_FREE_BYTES:
            raise HTTPException(507, "the server disk is nearly full")
        try:
            return store.add_photo(wedding_id, info, data)
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/api/weddings/{wedding_id}/photos", dependencies=api)
    def photos(wedding_id: str, folder: int | None = None):
        wedding_or_404(wedding_id)
        return {"photos": store.photos(wedding_id, folder)}

    @app.get("/api/weddings/{wedding_id}/photos/{photo_id}/preview", dependencies=api)
    def preview(wedding_id: str, photo_id: str):
        if store.photo(wedding_id, photo_id) is None:
            raise HTTPException(404, "no such photo")
        return FileResponse(store.preview_path(wedding_id, photo_id), media_type="image/jpeg")

    return app


def app():                                   # uvicorn server.main:app --factory
    return create_app()
