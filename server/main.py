"""The Auto Edits server.

Start it with:  AUTOEDITS_KEY=<secret> AUTOEDITS_DATA=/data uvicorn server.main:app

Every /api call must carry the key in the X-Auto-Edits-Key header. The server
refuses to start without a key, so it can never run open by mistake.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import Body, Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import VERSION
from .looks import SIZES, Renderer, clean_look, effective_look
from .store import Store

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "autoedits"
LINK_SECONDS = 120                        # how long a "open in browser" link from the plug-in works

MAX_PREVIEW_BYTES = 12 * 1024 * 1024      # a 2560 px JPEG is 1 to 3 MB
MIN_FREE_BYTES = 2 * 1024 ** 3            # stop taking photos before the disk is full


class NewWedding(BaseModel):
    name: str
    catalog: str = ""


class Login(BaseModel):
    key: str


def create_app(data_dir: str | None = None, key: str | None = None) -> FastAPI:
    key = key if key is not None else os.environ.get("AUTOEDITS_KEY", "")
    if len(key) < 16:
        raise RuntimeError("set AUTOEDITS_KEY to a secret of at least 16 characters")
    store = Store(data_dir or os.environ.get("AUTOEDITS_DATA", "data/server"))
    app = FastAPI(title="Auto Edits", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store

    renderer = Renderer()
    session = hmac.new(key.encode(), b"auto-edits browser session", hashlib.sha256).hexdigest()
    links: dict[str, float] = {}             # one-time sign-in links -> when they stop working

    def key_ok(value: str) -> bool:
        return hmac.compare_digest(value.encode(), key.encode())

    def signed_in(x_auto_edits_key: str = Header(default=""), autoedits: str = Cookie(default="")):
        """The plug-in sends the key in a header; the browser holds a sign-in cookie."""
        if key_ok(x_auto_edits_key) or hmac.compare_digest(autoedits.encode(), session.encode()):
            return
        raise HTTPException(401, "wrong or missing key")

    def give_cookie(response: Response, request: Request):
        response.set_cookie(COOKIE, session, max_age=30 * 24 * 3600, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")

    @app.post("/api/login")
    def login(body: Login, request: Request, response: Response):
        if not key_ok(body.key.strip()):
            time.sleep(1)                    # slows down guessing
            raise HTTPException(401, "That key is not right.")
        give_cookie(response, request)
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.post("/api/login-link", dependencies=[Depends(signed_in)])
    def login_link(request: Request):
        """For the plug-in: a link that signs the browser in once, without typing the key."""
        now = time.time()
        for t in [t for t, until in links.items() if until < now]:
            del links[t]
        token = secrets.token_urlsafe(32)
        links[token] = now + LINK_SECONDS
        return {"path": f"/enter/{token}", "seconds": LINK_SECONDS}

    @app.get("/enter/{token}")
    def enter(token: str, request: Request):
        until = links.pop(token, 0)
        response = RedirectResponse("/", status_code=303)
        if until >= time.time():
            give_cookie(response, request)
        return response

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
    def photos(wedding_id: str, folder: int | None = None, compact: bool = False):
        wedding_or_404(wedding_id)
        rows = store.photos(wedding_id, folder)
        if compact:                          # what the app's grid needs, without every detail
            rows = [{"id": r["id"], "name": r["file_name"], "folder": r["folder_id"], "look": r["settings"],
                     "v": int(r["received"]), "w": r["meta"].get("width"), "h": r["meta"].get("height"),
                     "iso": r["meta"].get("iso"), "taken": r["taken"], "camera": r["meta"].get("camera"),
                     "edited": len(r["meta"].get("edited") or [])} for r in rows]
        return {"photos": rows}

    def photo_or_404(wedding_id: str, photo_id: str) -> dict:
        p = store.photo(wedding_id, photo_id)
        if p is None:
            raise HTTPException(404, "no such photo")
        return p

    @app.get("/api/weddings/{wedding_id}/photos/{photo_id}/preview", dependencies=api)
    def preview(wedding_id: str, photo_id: str):
        photo_or_404(wedding_id, photo_id)
        return FileResponse(store.preview_path(wedding_id, photo_id), media_type="image/jpeg")

    # ---- the app: presets, looks, rendered previews ------------------------
    @app.get("/api/session", dependencies=api)
    def session_info():
        return {"ok": True, "version": VERSION}

    @app.get("/api/presets", dependencies=api)
    def presets():
        return {"presets": renderer.catalogue()}

    @app.put("/api/weddings/{wedding_id}/folders/{folder_id}/look", dependencies=api)
    def set_folder_look(wedding_id: str, folder_id: int, look: dict = Body(...)):
        look = clean_look(look, renderer.presets)
        if not store.set_folder_settings(wedding_id, folder_id, look):
            raise HTTPException(404, "no such folder")
        return {"look": look}

    @app.put("/api/weddings/{wedding_id}/photos/{photo_id}/look", dependencies=api)
    def set_photo_look(wedding_id: str, photo_id: str, look: dict | None = Body(default=None)):
        """Give one photo its own look, or send null to put it back on its folder's look."""
        look = clean_look(look, renderer.presets) if look else None
        if not store.set_photo_settings(wedding_id, photo_id, look):
            raise HTTPException(404, "no such photo")
        return {"look": look}

    def read_look(text: str) -> dict:
        try:
            return clean_look(json.loads(text), renderer.presets)
        except ValueError:
            raise HTTPException(422, "the look could not be read")

    @app.get("/api/weddings/{wedding_id}/photos/{photo_id}/image", dependencies=api)
    def image(wedding_id: str, photo_id: str, size: int = 1600, look: str = "", base: bool = False):
        """The photo as the app shows it: untouched (base), with the look given,
        or, with neither, with the look saved for it."""
        if size not in SIZES:
            raise HTTPException(422, f"size must be one of {SIZES}")
        p = photo_or_404(wedding_id, photo_id)
        path = store.preview_path(wedding_id, photo_id)
        iso = p["meta"].get("iso") if isinstance(p["meta"].get("iso"), (int, float)) else None
        headers = {"Cache-Control": "private, max-age=86400"}
        if base:
            return Response(renderer.base(path, size), media_type="image/jpeg", headers=headers)
        chosen = read_look(look) if look else effective_look(p["settings"], p["folder_settings"])
        if size > 360:
            return Response(renderer.render(path, chosen, size, iso), media_type="image/jpeg", headers=headers)
        # small pictures for the grid are kept on disk, one per photo and look
        tag = hashlib.sha1(json.dumps([photo_id, p["received"], size, chosen], sort_keys=True).encode()).hexdigest()[:20]
        cached = store.cache_dir(wedding_id) / f"{tag}.jpg"
        if not cached.exists():
            data = renderer.render(path, chosen, size, iso)
            part = cached.with_suffix(f".{secrets.token_hex(4)}.part")
            part.write_bytes(data)
            part.replace(cached)
        return FileResponse(cached, media_type="image/jpeg", headers=headers)

    @app.get("/api/weddings/{wedding_id}/photos/{photo_id}/develop", dependencies=api)
    def develop(wedding_id: str, photo_id: str, look: str = ""):
        """What a look works out to for this photo, including the automatic values."""
        p = photo_or_404(wedding_id, photo_id)
        chosen = read_look(look) if look else effective_look(p["settings"], p["folder_settings"])
        iso = p["meta"].get("iso") if isinstance(p["meta"].get("iso"), (int, float)) else None
        d = renderer.develop(store.preview_path(wedding_id, photo_id), chosen, iso)
        return {"look": chosen, "preset": d["preset"], "auto": d["auto"], "auto_wb": d["auto_wb"]}

    @app.get("/")
    def home():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def app():                                   # uvicorn server.main:app --factory
    return create_app()
