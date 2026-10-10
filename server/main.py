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
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import VERSION, adjustments, blobs as blob_store
from engine.presets import parse_xmp

from .exports import SIZES as EXPORT_SIZES, Exports
from .culling import RATINGS, Culls
from .looks import MY_PACK, SIZES, Renderer, clean_look, effective_look, lightroom_edit
from .store import Store

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "autoedits"
LINK_SECONDS = 120                        # how long a "open in browser" link from the plug-in works

MAX_PREVIEW_BYTES = 30 * 1024 * 1024      # a 2560 px JPEG is 1 to 3 MB; a full-size camera preview up to about 20
MAX_PRESET_BYTES = 4 * 1024 * 1024         # presets with tone tables can reach a megabyte or two
MIN_FREE_BYTES = 2 * 1024 ** 3            # stop taking photos before the disk is full


class NewWedding(BaseModel):
    name: str
    catalog: str = ""


class Login(BaseModel):
    key: str


class NewExport(BaseModel):
    size: int = 2560
    quality: int = 90
    watermark: str = ""
    folder: int | None = None
    ratings: list[str] = []


class NewCull(BaseModel):
    folder: int | None = None
    strictness: str = "normal"
    best_only: bool = False


class Pick(BaseModel):
    pick: str | None = None


def create_app(data_dir: str | None = None, key: str | None = None, blobs=None) -> FastAPI:
    key = key if key is not None else os.environ.get("AUTOEDITS_KEY", "")
    if len(key) < 16:
        raise RuntimeError("set AUTOEDITS_KEY to a secret of at least 16 characters")
    store = Store(data_dir or os.environ.get("AUTOEDITS_DATA", "data/server"),
                  blobs if blobs is not None else blob_store.from_environment())
    app = FastAPI(title="Auto Edits", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    exports = Exports(store)
    app.state.exports = exports
    culls = Culls(store)

    if store.blobs.on and os.environ.get("AUTOEDITS_WARM_UP") == "1":
        import threading

        def tidy():                              # photos into storage, and this disk kept within its allowance
            keep = int(float(os.environ.get("AUTOEDITS_WORKING_GB", "8")) * 1024 ** 3)
            while True:
                try:
                    while store.store_pending():
                        pass
                    store.trim_working_copies(keep)
                except Exception:
                    pass
                time.sleep(600)
        threading.Thread(target=tidy, daemon=True).start()

    renderer = Renderer()
    for uploaded in store.uploaded_presets():
        renderer.add_uploaded(uploaded)
    if os.environ.get("AUTOEDITS_WARM_UP") == "1":
        import threading
        threading.Thread(target=renderer.warm_up, daemon=True).start()
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
        return {"ok": True, "version": VERSION, "free_gb": round(store.free_space() / 1024 ** 3, 1),
                "storage": "cloud" if store.blobs.on else "this server"}

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
                     "edited": len(r["meta"].get("edited") or []), "cull": r["cull"], "pick": r["pick"] or None} for r in rows]
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

    @app.get("/api/adjustments", dependencies=api)
    def adjustment_panels():
        return {"panels": adjustments.PANELS}

    @app.get("/api/presets", dependencies=api)
    def presets():
        return {"presets": renderer.catalogue()}

    @app.post("/api/presets", dependencies=api)
    async def upload_presets(files: list[UploadFile] = File(...)):
        """Presets exported from Lightroom (.xmp), masks included. The original file is
        kept, so the very same preset can be applied when the edits go back to Lightroom."""
        results = []
        for f in files[:200]:
            name = f.filename or "preset.xmp"
            raw = await f.read(MAX_PRESET_BYTES + 1)
            if len(raw) > MAX_PRESET_BYTES:
                results.append({"file": name, "ok": False, "why": "The file is too large to be a preset."})
                continue
            try:
                text = raw.decode("utf-8", errors="replace")
                p = parse_xmp(text, name, MY_PACK)
                if not p.name or not (p.settings or p.masks or p.curves):
                    raise ValueError
            except Exception:
                results.append({"file": name, "ok": False, "why": "This is not a Lightroom preset (.xmp) file."})
                continue
            d = p.to_dict()
            d["id"] = "my-" + (p.uuid or p.id)                # never collides with the presets that ship with the app
            d["display_name"] = p.name
            d["look"], d["variant"] = p.group or "No group", p.name
            replaced = store.save_preset(d, text)
            renderer.add_uploaded(d)
            results.append({"file": name, "ok": True, "id": d["id"], "name": p.name, "replaced": replaced,
                            "masks": len(p.masks)})
        return {"results": results}

    @app.delete("/api/presets/{preset_id}", dependencies=api)
    def delete_preset(preset_id: str):
        if not renderer.remove_uploaded(preset_id):
            raise HTTPException(404, "Only presets you uploaded can be removed.")
        store.delete_preset(preset_id)
        return {"deleted": True}

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
    def image(wedding_id: str, photo_id: str, size: int = 1600, look: str = "", base: bool = False, show: str = ""):
        """The photo as the app shows it: untouched (base), with the look given,
        or, with neither, with the look saved for it."""
        if size not in SIZES:
            raise HTTPException(422, f"size must be one of {SIZES}")
        if show and not show.replace("_", "").isalpha():
            raise HTTPException(422, "show must name a mask")
        p = photo_or_404(wedding_id, photo_id)
        path = store.preview_path(wedding_id, photo_id)
        iso = p["meta"].get("iso") if isinstance(p["meta"].get("iso"), (int, float)) else None
        headers = {"Cache-Control": "private, max-age=86400"}
        if base:
            return Response(renderer.base(path, size), media_type="image/jpeg", headers=headers)
        chosen = read_look(look) if look else effective_look(p["settings"], p["folder_settings"])
        if size > 360:
            return Response(renderer.render(path, chosen, size, iso, show), media_type="image/jpeg", headers=headers)
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
        return {"look": chosen, "preset": d["preset"], "auto": d["auto"], "auto_wb": d["auto_wb"], "masks": d["masks"],
                "preset_masks": d["corrections"]}

    # ---- culling ----------------------------------------------------------------
    @app.post("/api/weddings/{wedding_id}/cull", dependencies=api)
    def start_cull(wedding_id: str, body: NewCull):
        w = wedding_or_404(wedding_id)
        ids = [f["id"] for f in w["folders"] if body.folder is None or f["id"] == body.folder]
        if not ids:
            raise HTTPException(404, "no such folder")
        try:
            return {"culls": culls.start(wedding_id, ids, body.strictness, body.best_only)}
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/api/weddings/{wedding_id}/cull", dependencies=api)
    def cull_progress(wedding_id: str):
        wedding_or_404(wedding_id)
        return {"culls": culls.for_wedding(wedding_id)}

    @app.put("/api/weddings/{wedding_id}/photos/{photo_id}/pick", dependencies=api)
    def set_pick(wedding_id: str, photo_id: str, body: Pick):
        """The photographer's own rating; null goes back to the automatic one."""
        if body.pick is not None and body.pick not in RATINGS:
            raise HTTPException(422, "pick must be keep, maybe, reject or null")
        if not store.set_pick(wedding_id, photo_id, body.pick or ""):
            raise HTTPException(404, "no such photo")
        return {"pick": body.pick}

    # ---- exports made on the server ---------------------------------------------
    @app.post("/api/weddings/{wedding_id}/exports", dependencies=api)
    def start_export(wedding_id: str, body: NewExport):
        wedding_or_404(wedding_id)
        if body.size not in EXPORT_SIZES:
            raise HTTPException(422, f"size must be one of {EXPORT_SIZES}")
        try:
            return exports.create(wedding_id, body.size, min(100, max(40, body.quality)), body.watermark, body.folder,
                                  tuple(r for r in RATINGS if r in body.ratings))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/api/weddings/{wedding_id}/exports", dependencies=api)
    def list_exports(wedding_id: str):
        wedding_or_404(wedding_id)
        return {"exports": exports.for_wedding(wedding_id)}

    def export_or_404(export_id: str) -> dict:
        e = exports.get(export_id)
        if e is None:
            raise HTTPException(404, "no such export")
        return e

    @app.get("/api/exports/{export_id}", dependencies=api)
    def one_export(export_id: str):
        return {**export_or_404(export_id), "failures": exports.failures(export_id)}

    @app.post("/api/exports/{export_id}/stop", dependencies=api)
    def stop_export(export_id: str):
        export_or_404(export_id)
        exports.stop(export_id)
        return exports.get(export_id)

    @app.delete("/api/exports/{export_id}", dependencies=api)
    def delete_export(export_id: str):
        export_or_404(export_id)
        exports.delete(export_id)
        return {"deleted": True}

    @app.get("/api/exports/{export_id}/zip", dependencies=api)
    def export_zip(export_id: str):
        e = export_or_404(export_id)
        if not e["done"]:
            raise HTTPException(409, "no pictures are finished yet")
        name = "".join(c if c.isalnum() or c in " -_" else "_" for c in wedding_or_404(e["wedding_id"])["name"]).strip() or "export"
        return StreamingResponse(exports.zip_stream(export_id), media_type="application/zip",
                                 headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})

    # ---- back to Lightroom ---------------------------------------------------
    @app.get("/api/weddings/{wedding_id}/apply", dependencies=api)
    def apply_list(wedding_id: str, offset: int = 0, limit: int = 200):
        """For the plug-in: what Lightroom should do to each photo, a page at a time.
        "done" is true when Lightroom has already applied exactly this edit."""
        wedding_or_404(wedding_id)
        rows = store.photos(wedding_id)
        page = rows[max(0, offset):max(0, offset) + max(1, min(500, limit))]
        folders = {f["id"]: f["settings"] for f in store.wedding(wedding_id)["folders"]}
        out = []
        for r in page:
            look = effective_look(r["settings"], folders.get(r["folder_id"]))
            iso = r["meta"].get("iso") if isinstance(r["meta"].get("iso"), (int, float)) else None
            edit = lightroom_edit(renderer, store.preview_path(wedding_id, r["id"]), look, iso)
            item = {"uuid": r["lr_uuid"], "name": r["file_name"], "folder": r["folder"], "edit": edit}
            if edit:
                item["stamp"] = hashlib.sha1(json.dumps(edit, sort_keys=True).encode()).hexdigest()[:16]
                item["done"] = r["applied"] == item["stamp"]
            out.append(item)
        return {"total": len(rows), "offset": offset, "photos": out}

    @app.post("/api/weddings/{wedding_id}/applied", dependencies=api)
    def applied(wedding_id: str, stamps: dict = Body(...)):
        wedding_or_404(wedding_id)
        return {"marked": store.mark_applied(wedding_id, stamps)}

    @app.get("/")
    def home():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def app():                                   # uvicorn server.main:app --factory
    return create_app()
