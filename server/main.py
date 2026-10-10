"""The Auto Edits server.

Start it with:  AUTOEDITS_KEY=<secret> AUTOEDITS_ADMIN_EMAIL=<studio email> AUTOEDITS_DATA=/data uvicorn server.main:app

Photographers sign up with email and password; the browser then holds a session cookie.
The Lightroom plug-in sends a key in the X-Auto-Edits-Key header: each photographer makes
their own key in the app. AUTOEDITS_KEY is the studio's key from before there were accounts
and signs in as the admin account. The server refuses to start without it.
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

from . import VERSION, adjustments, blobs as blob_store, mail
from .accounts import Accounts, NotEnoughCredits, cull_cost, public
from engine.presets import parse_xmp

from .exports import SIZES as EXPORT_SIZES, Exports
from .culling import RATINGS, Culls
from .looks import MY_PACK, SIZES, Renderer, clean_look, effective_look, lightroom_edit
from .store import Store

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "autoedits"
VIEW_AS = "autoedits_as"                  # the admin is looking at a photographer's account (read-only)
LINK_SECONDS = 120                        # how long a "open in browser" link from the plug-in works

MAX_PREVIEW_BYTES = 30 * 1024 * 1024      # a 2560 px JPEG is 1 to 3 MB; a full-size camera preview up to about 20
MAX_PRESET_BYTES = 4 * 1024 * 1024         # presets with tone tables can reach a megabyte or two
MIN_FREE_BYTES = 2 * 1024 ** 3            # stop taking photos before the disk is full


class NewWedding(BaseModel):
    name: str
    catalog: str = ""


class Login(BaseModel):
    email: str = ""
    password: str = ""
    key: str = ""                              # the studio key, from before there were accounts


class SignUp(BaseModel):
    email: str
    password: str
    name: str = ""


class Forgot(BaseModel):
    email: str


class Reset(BaseModel):
    token: str
    password: str


class NewPassword(BaseModel):
    current: str = ""
    password: str


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


def create_app(data_dir: str | None = None, key: str | None = None, blobs=None, admin_email: str | None = None,
               mailer=None) -> FastAPI:
    key = key if key is not None else os.environ.get("AUTOEDITS_KEY", "")
    if len(key) < 16:
        raise RuntimeError("set AUTOEDITS_KEY to a secret of at least 16 characters")
    store = Store(data_dir or os.environ.get("AUTOEDITS_DATA", "data/server"),
                  blobs if blobs is not None else blob_store.from_environment())
    admin_email = admin_email if admin_email is not None else os.environ.get("AUTOEDITS_ADMIN_EMAIL", "")
    accounts = Accounts(store, admin_email or "studio@auto-edits.local")
    mailer = mailer or mail.Mailer()
    base_url = os.environ.get("AUTOEDITS_BASE_URL", "").rstrip("/")
    app = FastAPI(title="Auto Edits", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.accounts = accounts
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
    links: dict[str, tuple[float, str]] = {}   # one-time sign-in links -> (when they stop working, user)
    tries: dict[str, list[float]] = {}         # recent attempts per address, to slow down guessing

    def client_ip(request: Request) -> str:
        """The visitor's address. Traefik adds the address it was reached from as the last entry of
        X-Forwarded-For; earlier entries come from the visitor and could be made up, so they are ignored."""
        forwarded = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",") if x.strip()]
        return forwarded[-1] if forwarded else (request.client.host if request.client else "")

    def too_many(what: str, request: Request, limit: int, seconds: int) -> bool:
        who = f"{what}|{client_ip(request)}"
        now = time.time()
        recent = [t for t in tries.get(who, []) if t > now - seconds]
        tries[who] = recent + [now]
        if len(tries) > 5000:
            tries.clear()
        return len(recent) >= limit

    def key_ok(value: str) -> bool:
        return bool(value) and hmac.compare_digest(value.encode(), key.encode())

    def signed_in(request: Request, x_auto_edits_key: str = Header(default=""), autoedits: str = Cookie(default=""),
                  autoedits_as: str = Cookie(default="")) -> dict:
        """The plug-in sends its key in a header; the browser holds a session cookie.
        When the admin chose "View as", the app works as that photographer, read-only."""
        user = None
        if x_auto_edits_key:
            user = accounts.user(accounts.admin_id) if key_ok(x_auto_edits_key) else accounts.plugin_user(x_auto_edits_key)
        if user is None and autoedits:
            user = accounts.session_user(autoedits)
            if user and user["admin"] and autoedits_as and autoedits_as != user["id"]:
                target = accounts.user(autoedits_as)
                if target:
                    if request.method not in ("GET", "HEAD"):
                        raise HTTPException(403, f"You are viewing {target['email']}'s account: nothing can be changed from here.")
                    return {**target, "viewing_as": user["email"]}
        if user is None:
            raise HTTPException(401, "Please log in again.")
        return user

    def the_admin(autoedits: str = Cookie(default="")) -> dict:
        """The admin's own session, never a "View as" one."""
        user = accounts.session_user(autoedits)
        if user is None or not user["admin"]:
            raise HTTPException(404, "Not found")
        return user

    def confirmed(user: dict = Depends(signed_in)) -> dict:
        """Culling and exporting spend credits: they need a confirmed email address."""
        if not user["verified"]:
            raise HTTPException(403, "Confirm your email address first: open the link we sent you.")
        return user

    def give_cookie(response: Response, request: Request, user_id: str):
        response.set_cookie(COOKIE, accounts.new_session(user_id), max_age=30 * 24 * 3600, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")

    def link_base(request: Request) -> str:
        if base_url:
            return base_url
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        return f"{proto}://{request.headers.get('host') or request.url.netloc}"

    def send_verify(request: Request, user: dict):
        subject, text, html = mail.verify_email(link_base(request), accounts.email_token(user["id"], "verify"))
        mailer.send(user["email"], subject, text, html)

    def no_credits(e: NotEnoughCredits):
        return HTTPException(402, str(e))

    # ---- accounts -----------------------------------------------------------------
    @app.post("/api/signup")
    def sign_up(body: SignUp, request: Request, response: Response):
        if too_many("signup", request, 8, 3600):
            raise HTTPException(429, "Too many sign-ups from here. Please try again in an hour.")
        try:
            user = accounts.sign_up(body.email, body.password, body.name, client_ip(request))
        except ValueError as e:
            raise HTTPException(422, str(e))
        send_verify(request, user)
        give_cookie(response, request, user["id"])
        return {"user": public(user)}

    @app.post("/api/login")
    def login(body: Login, request: Request, response: Response):
        if too_many("login", request, 10, 900):
            raise HTTPException(429, "Too many tries. Please wait 15 minutes.")
        user = None
        if body.key.strip():
            user = accounts.user(accounts.admin_id) if key_ok(body.key.strip()) else None
        elif body.email and body.password:
            user = accounts.log_in(body.email, body.password)
        if user is None:
            time.sleep(1)                    # slows down guessing
            raise HTTPException(401, "That email and password do not match.")
        tries.pop(f"login|{client_ip(request)}", None)
        give_cookie(response, request, user["id"])
        return {"user": public(user)}

    @app.post("/api/logout")
    def logout(response: Response, autoedits: str = Cookie(default="")):
        if autoedits:
            accounts.end_session(autoedits)
        response.delete_cookie(COOKIE)
        response.delete_cookie(VIEW_AS)
        return {"ok": True}

    @app.post("/api/forgot")
    def forgot(body: Forgot, request: Request):
        """Always the same answer, so it cannot be used to find out who has an account."""
        if too_many("forgot", request, 6, 3600):
            raise HTTPException(429, "Too many requests. Please try again in an hour.")
        user = accounts.by_email(body.email)
        if user:
            subject, text, html = mail.reset_password(link_base(request), accounts.email_token(user["id"], "reset"))
            mailer.send(user["email"], subject, text, html)
        return {"ok": True}

    @app.post("/api/reset")
    def reset(body: Reset, request: Request, response: Response):
        if not accounts.peek_email_token(body.token, "reset"):
            raise HTTPException(410, "This link has expired or was already used. Ask for a new one.")
        if not 8 <= len(body.password) <= 200:
            raise HTTPException(422, "The password needs at least 8 characters.")
        user_id = accounts.use_email_token(body.token, "reset")
        if not user_id:
            raise HTTPException(410, "This link has expired or was already used. Ask for a new one.")
        accounts.set_password(user_id, body.password)
        accounts.verify(user_id)                   # the link came by email, so the address is theirs
        give_cookie(response, request, user_id)
        return {"user": public(accounts.user(user_id))}

    @app.get("/verify/{token}")
    def verify(token: str, request: Request):
        user_id = accounts.use_email_token(token, "verify")
        if not user_id:
            return RedirectResponse("/?link=expired", status_code=303)
        accounts.verify(user_id)
        response = RedirectResponse("/?verified=1" if accounts.got_free(user_id) else "/?verified=0", status_code=303)
        give_cookie(response, request, user_id)
        return response

    @app.get("/reset/{token}")
    def reset_page(token: str):
        return FileResponse(STATIC / "reset.html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @app.post("/api/verify/again")
    def verify_again(request: Request, user: dict = Depends(signed_in)):
        if user["verified"]:
            return {"ok": True, "verified": True}
        if too_many(f"verify-{user['id']}", request, 3, 3600):
            raise HTTPException(429, "We sent a few already. Check your spam folder, or try again in an hour.")
        send_verify(request, user)
        return {"ok": True, "verified": False}

    @app.get("/api/me")
    def me(user: dict = Depends(signed_in)):
        return {"user": public(user), "credits": accounts.balance(user["id"]), "free_given": accounts.got_free(user["id"]),
                "prices": {"export": 1, "cull_photos_per_credit": 10}}

    @app.get("/api/credits")
    def credits(user: dict = Depends(signed_in)):
        return {"balance": accounts.balance(user["id"]), "unlimited": bool(user["admin"]),
                "history": accounts.history(user["id"])}

    @app.post("/api/plugin-key")
    def plugin_key(user: dict = Depends(signed_in)):
        """A new key for the Lightroom plug-in, shown once. The old one stops working."""
        return {"key": accounts.new_plugin_key(user["id"])}

    @app.post("/api/password")
    def change_password(body: NewPassword, user: dict = Depends(signed_in)):
        if user["password"] and not accounts.log_in(user["email"], body.current):
            time.sleep(1)
            raise HTTPException(403, "The current password is not right.")
        try:
            accounts.set_password(user["id"], body.password)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"ok": True, "signed_out": True}

    @app.post("/api/login-link")
    def login_link(request: Request, user: dict = Depends(signed_in)):
        """For the plug-in: a link that signs the browser in once, without typing a password."""
        now = time.time()
        for t in [t for t, (until, _) in links.items() if until < now]:
            del links[t]
        token = secrets.token_urlsafe(32)
        links[token] = (now + LINK_SECONDS, user["id"])
        return {"path": f"/enter/{token}", "seconds": LINK_SECONDS}

    @app.get("/enter/{token}")
    def enter(token: str, request: Request):
        until, user_id = links.pop(token, (0, ""))
        response = RedirectResponse("/", status_code=303)
        if until >= time.time():
            give_cookie(response, request, user_id)
        return response

    def wedding_or_404(wedding_id: str, user: dict) -> dict:
        """A wedding of this photographer's; anyone else's looks exactly like one that does not exist."""
        if store.owner_of(wedding_id) != user["id"]:
            raise HTTPException(404, "no such wedding")
        w = store.wedding(wedding_id)
        if w is None:
            raise HTTPException(404, "no such wedding")
        return w

    def allowed_presets(user: dict) -> set:
        return renderer.allowed(user["id"])

    @app.get("/api/ping")
    def ping(user: dict = Depends(signed_in)):
        return {"ok": True, "version": VERSION, "free_gb": round(store.free_space() / 1024 ** 3, 1),
                "storage": "cloud" if store.blobs.on else "this server", "email": user["email"],
                "credits": accounts.balance(user["id"])}

    @app.get("/api/weddings")
    def weddings(user: dict = Depends(signed_in)):
        return {"weddings": store.weddings(user["id"])}

    @app.post("/api/weddings")
    def open_wedding(body: NewWedding, user: dict = Depends(signed_in)):
        try:
            w = store.open_wedding(body.name, body.catalog, user["id"])
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {**store.wedding(w["id"]), "is_new": w["is_new"]}

    @app.get("/api/weddings/{wedding_id}")
    def wedding(wedding_id: str, user: dict = Depends(signed_in)):
        return wedding_or_404(wedding_id, user)

    @app.delete("/api/weddings/{wedding_id}")
    def delete_wedding(wedding_id: str, user: dict = Depends(signed_in)):
        wedding_or_404(wedding_id, user)
        store.delete_wedding(wedding_id)
        return {"deleted": True}

    @app.get("/api/weddings/{wedding_id}/received")
    def received(wedding_id: str, user: dict = Depends(signed_in)):
        """Lightroom ids already on the server, so an interrupted send can carry on."""
        wedding_or_404(wedding_id, user)
        return {"uuids": store.received_uuids(wedding_id)}

    @app.post("/api/weddings/{wedding_id}/photos")
    async def add_photo(wedding_id: str, meta: str = Form(...), preview: UploadFile = File(...),
                        user: dict = Depends(signed_in)):
        wedding_or_404(wedding_id, user)
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

    @app.get("/api/weddings/{wedding_id}/photos")
    def photos(wedding_id: str, folder: int | None = None, compact: bool = False, user: dict = Depends(signed_in)):
        wedding_or_404(wedding_id, user)
        rows = store.photos(wedding_id, folder)
        if compact:                          # what the app's grid needs, without every detail
            rows = [{"id": r["id"], "name": r["file_name"], "folder": r["folder_id"], "look": r["settings"],
                     "v": int(r["received"]), "w": r["meta"].get("width"), "h": r["meta"].get("height"),
                     "iso": r["meta"].get("iso"), "taken": r["taken"], "camera": r["meta"].get("camera"),
                     "edited": len(r["meta"].get("edited") or []), "cull": r["cull"], "pick": r["pick"] or None} for r in rows]
        return {"photos": rows}

    def photo_or_404(wedding_id: str, photo_id: str, user: dict) -> dict:
        if store.owner_of(wedding_id) != user["id"]:
            raise HTTPException(404, "no such photo")
        p = store.photo(wedding_id, photo_id)
        if p is None:
            raise HTTPException(404, "no such photo")
        return p

    @app.get("/api/weddings/{wedding_id}/photos/{photo_id}/preview")
    def preview(wedding_id: str, photo_id: str, user: dict = Depends(signed_in)):
        photo_or_404(wedding_id, photo_id, user)
        return FileResponse(store.preview_path(wedding_id, photo_id), media_type="image/jpeg")

    # ---- the app: presets, looks, rendered previews ------------------------
    @app.get("/api/session")
    def session_info(user: dict = Depends(signed_in)):
        return {"ok": True, "version": VERSION, "user": public(user), "credits": accounts.balance(user["id"]),
                "viewing_as": user.get("viewing_as")}

    # ---- the admin dashboard -------------------------------------------------------
    @app.get("/admin")
    def admin_page(admin: dict = Depends(the_admin)):
        return FileResponse(STATIC / "admin.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/admin/users")
    def admin_users(admin: dict = Depends(the_admin)):
        return {"users": accounts.overview()}

    @app.get("/api/admin/users/{user_id}")
    def admin_user(user_id: str, admin: dict = Depends(the_admin)):
        u = accounts.user(user_id)
        if u is None:
            raise HTTPException(404, "no such account")
        return {"user": {**public(u), "created": u["created"], "last_seen": u["last_seen"], "signup_ip": u["signup_ip"]},
                "credits": accounts.balance(user_id), "history": accounts.history(user_id, 300),
                "weddings": [{k: w[k] for k in ("id", "name", "created", "photos", "bytes")} |
                             {"exports": [{k: e[k] for k in ("id", "size", "done", "total", "state", "created", "scope")}
                                          for e in exports.for_wedding(w["id"])]}
                             for w in store.weddings(user_id)]}

    @app.post("/api/admin/users/{user_id}/credits")
    def admin_credits(user_id: str, body: dict = Body(...), admin: dict = Depends(the_admin)):
        """Add (or take back, with a minus) credits by hand: a gift, a correction, a payment made outside the app."""
        if accounts.user(user_id) is None:
            raise HTTPException(404, "no such account")
        try:
            n = int(body.get("credits"))
        except (TypeError, ValueError):
            raise HTTPException(422, "credits must be a whole number")
        if not n or abs(n) > 1_000_000:
            raise HTTPException(422, "credits must be a whole number, not 0")
        note = str(body.get("note") or "").strip()[:150] or ("Added by Cele-Brat" if n > 0 else "Taken back by Cele-Brat")
        accounts.add(user_id, n, "admin", note)
        return {"credits": accounts.balance(user_id)}

    @app.post("/api/admin/view-as/{user_id}")
    def view_as(user_id: str, request: Request, response: Response, admin: dict = Depends(the_admin)):
        if accounts.user(user_id) is None:
            raise HTTPException(404, "no such account")
        response.set_cookie(VIEW_AS, user_id, max_age=4 * 3600, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
        return {"ok": True}

    @app.post("/api/admin/view-as-stop")
    def view_as_stop(response: Response):
        response.delete_cookie(VIEW_AS)
        return {"ok": True}

    @app.get("/api/admin/weddings/{wedding_id}/originals.zip")
    def admin_originals(wedding_id: str, admin: dict = Depends(the_admin)):
        owner = store.owner_of(wedding_id)
        if owner is None:
            raise HTTPException(404, "no such wedding")
        return originals_zip(wedding_id, None, accounts.user(owner))

    @app.get("/api/admin/exports/{export_id}/zip")
    def admin_export_zip(export_id: str, admin: dict = Depends(the_admin)):
        e = exports.get(export_id)
        if e is None:
            raise HTTPException(404, "no such export")
        return export_zip(export_id, accounts.user(store.owner_of(e["wedding_id"])))

    @app.get("/api/weddings/{wedding_id}/originals.zip")
    def originals_zip(wedding_id: str, folder: int | None = None, user: dict = Depends(signed_in)):
        """The photos as they were uploaded (camera previews), in their event folders, as one zip."""
        w = wedding_or_404(wedding_id, user)
        rows = store.photos(wedding_id, folder)
        if not rows:
            raise HTTPException(404, "no photos")
        name = "".join(c if c.isalnum() or c in " -_" else "_" for c in w["name"]).strip() or "photos"
        return StreamingResponse(store.zip_previews(wedding_id, rows), media_type="application/zip",
                                 headers={"Content-Disposition": f'attachment; filename="{name} - uploaded photos.zip"'})

    @app.get("/api/adjustments")
    def adjustment_panels(user: dict = Depends(signed_in)):
        return {"panels": adjustments.PANELS}

    @app.get("/api/presets")
    def presets(user: dict = Depends(signed_in)):
        return {"presets": renderer.catalogue(user["id"])}

    @app.post("/api/presets")
    async def upload_presets(files: list[UploadFile] = File(...), user: dict = Depends(signed_in)):
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
            own = p.uuid or p.id
            # the studio's ids stay as they were before accounts; everyone else's carry their account
            d["id"] = "my-" + own if user["admin"] else f"my-{user['id']}-{own}"
            d["display_name"] = p.name
            d["look"], d["variant"] = p.group or "No group", p.name
            d["owner"] = user["id"]
            if renderer.owner_of(d["id"]) not in (None, user["id"]):
                results.append({"file": name, "ok": False, "why": "This preset could not be added."})
                continue
            replaced = store.save_preset(d, text, user["id"])
            renderer.add_uploaded(d)
            results.append({"file": name, "ok": True, "id": d["id"], "name": p.name, "replaced": replaced,
                            "masks": len(p.masks)})
        return {"results": results}

    @app.delete("/api/presets/{preset_id}")
    def delete_preset(preset_id: str, user: dict = Depends(signed_in)):
        if renderer.owner_of(preset_id) != user["id"] or not renderer.remove_uploaded(preset_id):
            raise HTTPException(404, "Only presets you uploaded can be removed.")
        store.delete_preset(preset_id)
        return {"deleted": True}

    @app.put("/api/weddings/{wedding_id}/folders/{folder_id}/look")
    def set_folder_look(wedding_id: str, folder_id: int, look: dict = Body(...), user: dict = Depends(signed_in)):
        wedding_or_404(wedding_id, user)
        look = clean_look(look, allowed_presets(user))
        if not store.set_folder_settings(wedding_id, folder_id, look):
            raise HTTPException(404, "no such folder")
        return {"look": look}

    @app.put("/api/weddings/{wedding_id}/photos/{photo_id}/look")
    def set_photo_look(wedding_id: str, photo_id: str, look: dict | None = Body(default=None),
                       user: dict = Depends(signed_in)):
        """Give one photo its own look, or send null to put it back on its folder's look."""
        wedding_or_404(wedding_id, user)
        look = clean_look(look, allowed_presets(user)) if look else None
        if not store.set_photo_settings(wedding_id, photo_id, look):
            raise HTTPException(404, "no such photo")
        return {"look": look}

    def read_look(text: str, user: dict) -> dict:
        try:
            return clean_look(json.loads(text), allowed_presets(user))
        except ValueError:
            raise HTTPException(422, "the look could not be read")

    @app.get("/api/weddings/{wedding_id}/photos/{photo_id}/image")
    def image(wedding_id: str, photo_id: str, size: int = 1600, look: str = "", base: bool = False, show: str = "",
              user: dict = Depends(signed_in)):
        """The photo as the app shows it: untouched (base), with the look given,
        or, with neither, with the look saved for it. Edited views larger than the grid's
        carry a light preview mark until they are exported (not on the studio account)."""
        if size not in SIZES:
            raise HTTPException(422, f"size must be one of {SIZES}")
        if show and not show.replace("_", "").isalpha():
            raise HTTPException(422, "show must name a mask")
        p = photo_or_404(wedding_id, photo_id, user)
        path = store.preview_path(wedding_id, photo_id)
        iso = p["meta"].get("iso") if isinstance(p["meta"].get("iso"), (int, float)) else None
        headers = {"Cache-Control": "private, max-age=86400"}
        if base:
            return Response(renderer.base(path, size), media_type="image/jpeg", headers=headers)
        chosen = read_look(look, user) if look else effective_look(p["settings"], p["folder_settings"])
        if size > 360:
            return Response(renderer.render(path, chosen, size, iso, show, mark=not user["admin"]),
                            media_type="image/jpeg", headers=headers)
        # small pictures for the grid are kept on disk, one per photo and look
        tag = hashlib.sha1(json.dumps([photo_id, p["received"], size, chosen], sort_keys=True).encode()).hexdigest()[:20]
        cached = store.cache_dir(wedding_id) / f"{tag}.jpg"
        if not cached.exists():
            data = renderer.render(path, chosen, size, iso)
            part = cached.with_suffix(f".{secrets.token_hex(4)}.part")
            part.write_bytes(data)
            part.replace(cached)
        return FileResponse(cached, media_type="image/jpeg", headers=headers)

    @app.get("/api/weddings/{wedding_id}/photos/{photo_id}/develop")
    def develop(wedding_id: str, photo_id: str, look: str = "", user: dict = Depends(signed_in)):
        """What a look works out to for this photo, including the automatic values."""
        p = photo_or_404(wedding_id, photo_id, user)
        chosen = read_look(look, user) if look else effective_look(p["settings"], p["folder_settings"])
        iso = p["meta"].get("iso") if isinstance(p["meta"].get("iso"), (int, float)) else None
        d = renderer.develop(store.preview_path(wedding_id, photo_id), chosen, iso)
        return {"look": chosen, "preset": d["preset"], "auto": d["auto"], "auto_wb": d["auto_wb"], "masks": d["masks"],
                "preset_masks": d["corrections"]}

    # ---- culling: 1 credit per 10 photos, every time it runs ----------------------
    @app.post("/api/weddings/{wedding_id}/cull")
    def start_cull(wedding_id: str, body: NewCull, user: dict = Depends(confirmed)):
        w = wedding_or_404(wedding_id, user)
        chosen = [f for f in w["folders"] if (body.folder is None or f["id"] == body.folder) and f["photos"]]
        if not [f for f in w["folders"] if body.folder is None or f["id"] == body.folder]:
            raise HTTPException(404, "no such folder")
        if body.strictness not in ("relaxed", "normal", "strict"):
            raise HTTPException(422, "strictness must be relaxed, normal or strict")
        photos = sum(f["photos"] for f in chosen)
        where = w["name"] if body.folder is None else f"{w['name']} / {chosen[0]['name'] if chosen else ''}"
        try:
            accounts.charge(user, cull_cost(photos), "cull", f"Culling {photos} photos: {where}", wedding_id, photos)
        except NotEnoughCredits as e:
            raise no_credits(e)
        return {"culls": culls.start(wedding_id, [f["id"] for f in chosen], body.strictness, body.best_only),
                "charged": cull_cost(photos)}

    @app.get("/api/weddings/{wedding_id}/cull")
    def cull_progress(wedding_id: str, user: dict = Depends(signed_in)):
        wedding_or_404(wedding_id, user)
        return {"culls": culls.for_wedding(wedding_id)}

    @app.put("/api/weddings/{wedding_id}/photos/{photo_id}/pick")
    def set_pick(wedding_id: str, photo_id: str, body: Pick, user: dict = Depends(signed_in)):
        """The photographer's own rating; null goes back to the automatic one."""
        wedding_or_404(wedding_id, user)
        if body.pick is not None and body.pick not in RATINGS:
            raise HTTPException(422, "pick must be keep, maybe, reject or null")
        if not store.set_pick(wedding_id, photo_id, body.pick or ""):
            raise HTTPException(404, "no such photo")
        return {"pick": body.pick}

    # ---- exports made on the server: 1 credit per photo, every export ---------------
    @app.post("/api/weddings/{wedding_id}/exports")
    def start_export(wedding_id: str, body: NewExport, user: dict = Depends(confirmed)):
        w = wedding_or_404(wedding_id, user)
        if body.size not in EXPORT_SIZES:
            raise HTTPException(422, f"size must be one of {EXPORT_SIZES}")

        def pay(export_id: str, photos: int):
            accounts.charge(user, photos, "export", f"Export of {photos} photos: {w['name']}", export_id, photos)
        try:
            return exports.create(wedding_id, body.size, min(100, max(40, body.quality)), body.watermark, body.folder,
                                  tuple(r for r in RATINGS if r in body.ratings), pay=pay)
        except NotEnoughCredits as e:
            raise no_credits(e)
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/api/weddings/{wedding_id}/exports")
    def list_exports(wedding_id: str, user: dict = Depends(signed_in)):
        wedding_or_404(wedding_id, user)
        return {"exports": exports.for_wedding(wedding_id)}

    def export_or_404(export_id: str, user: dict) -> dict:
        e = exports.get(export_id)
        if e is None or store.owner_of(e["wedding_id"]) != user["id"]:
            raise HTTPException(404, "no such export")
        return e

    @app.get("/api/exports/{export_id}")
    def one_export(export_id: str, user: dict = Depends(signed_in)):
        return {**export_or_404(export_id, user), "failures": exports.failures(export_id)}

    @app.post("/api/exports/{export_id}/stop")
    def stop_export(export_id: str, user: dict = Depends(signed_in)):
        export_or_404(export_id, user)
        exports.stop(export_id)
        return exports.get(export_id)

    @app.delete("/api/exports/{export_id}")
    def delete_export(export_id: str, user: dict = Depends(signed_in)):
        export_or_404(export_id, user)
        exports.delete(export_id)
        return {"deleted": True}

    @app.get("/api/exports/{export_id}/zip")
    def export_zip(export_id: str, user: dict = Depends(signed_in)):
        e = export_or_404(export_id, user)
        if not e["done"]:
            raise HTTPException(409, "no pictures are finished yet")
        name = "".join(c if c.isalnum() or c in " -_" else "_" for c in wedding_or_404(e["wedding_id"], user)["name"]).strip() or "export"
        return StreamingResponse(exports.zip_stream(export_id), media_type="application/zip",
                                 headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})

    # ---- back to Lightroom: 1 credit per photo applied ------------------------------
    @app.get("/api/weddings/{wedding_id}/apply")
    def apply_list(wedding_id: str, offset: int = 0, limit: int = 200, user: dict = Depends(confirmed)):
        """For the plug-in: what Lightroom should do to each photo, a page at a time.
        "done" is true when Lightroom has already applied exactly this edit."""
        wedding_or_404(wedding_id, user)
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
        try:
            accounts.must_have(user, sum(1 for i in out if i["edit"] and not i["done"]))
        except NotEnoughCredits as e:
            raise no_credits(e)
        return {"total": len(rows), "offset": offset, "photos": out}

    @app.post("/api/weddings/{wedding_id}/applied")
    def applied(wedding_id: str, stamps: dict = Body(...), user: dict = Depends(confirmed)):
        """Lightroom has applied these edits: each photo costs 1 credit (it is already done,
        so this is written down even when the balance runs short)."""
        w = wedding_or_404(wedding_id, user)
        marked = store.mark_applied(wedding_id, stamps)
        if marked:
            accounts.add(user["id"], -marked, "lightroom", f"Edits applied in Lightroom to {marked} photos: {w['name']}",
                         wedding_id, marked)
        return {"marked": marked}

    # ---- for search engines and AI assistants ------------------------------------
    SITE = os.environ.get("AUTOEDITS_SITE", "https://edits.cele-brat.solutions").rstrip("/")

    @app.api_route("/robots.txt", methods=["GET", "HEAD"])
    def robots():
        """The home page is public; everything behind the log-in stays out of search."""
        return Response("User-agent: *\nAllow: /$\nAllow: /static/\nAllow: /llms.txt\nDisallow: /api/\nDisallow: /enter/\n"
                        "Disallow: /verify/\nDisallow: /reset/\nDisallow: /admin\n\n" f"Sitemap: {SITE}/sitemap.xml\n", media_type="text/plain")

    @app.api_route("/sitemap.xml", methods=["GET", "HEAD"])
    def sitemap():
        changed = time.strftime("%Y-%m-%d", time.gmtime((STATIC / "home.html").stat().st_mtime))
        return Response('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                        f"  <url><loc>{SITE}/</loc><lastmod>{changed}</lastmod></url>\n</urlset>\n", media_type="application/xml")

    @app.api_route("/llms.txt", methods=["GET", "HEAD"])
    def llms():
        """A plain summary of the product for AI assistants that read sites on a person's behalf."""
        return FileResponse(STATIC / "llms.txt", media_type="text/markdown; charset=utf-8")

    @app.api_route("/", methods=["GET", "HEAD"])
    def home(autoedits: str = Cookie(default="")):
        """The app for someone logged in, the public home page for everyone else."""
        inside = accounts.session_user(autoedits) is not None
        return FileResponse(STATIC / ("index.html" if inside else "home.html"), headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def app():                                   # uvicorn server.main:app --factory
    return create_app()
