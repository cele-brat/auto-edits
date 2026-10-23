"""Client galleries: a public page where the couple sees the edited previews, picks favourites and
leaves comments, and the photographer sees the picks in the app.

A gallery is a list of photos of one wedding (Keep only by default) behind an unguessable link
/g/<token>, with an optional password, an expiry (30 days by default) and a switch for downloads.
The public page shows the couple's name and the edited previews only: no account details, no
file names beyond the photo's own, nothing of the photographer but what they chose to show.

Tables (all in the one SQLite file):
    galleries        id, token (hashed), wedding, owner, name, password hash, expiry, downloads, views
    gallery_photos   which photos, in order; the client's heart and comment per photo
    gallery_sends    each "Send selection" (client email optional) -> the photographer gets an email

Password tries are limited through the ephemeral table (server/state.py). Costs nothing.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

from fastapi import APIRouter, Body, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse

from .accounts import hash_password, check_password
from .looks import effective_look

SCHEMA = """
CREATE TABLE IF NOT EXISTS galleries (
    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, wedding_id TEXT NOT NULL REFERENCES weddings(id) ON DELETE CASCADE,
    owner TEXT NOT NULL, name TEXT NOT NULL, password TEXT NOT NULL DEFAULT '', downloads INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL, expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, views INTEGER NOT NULL DEFAULT 0,
    last_view REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS gallery_photos (
    gallery_id TEXT NOT NULL REFERENCES galleries(id) ON DELETE CASCADE, photo_id TEXT NOT NULL,
    position INTEGER NOT NULL, heart INTEGER NOT NULL DEFAULT 0, comment TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (gallery_id, photo_id)
);
CREATE TABLE IF NOT EXISTS gallery_sends (
    id INTEGER PRIMARY KEY AUTOINCREMENT, gallery_id TEXT NOT NULL REFERENCES galleries(id) ON DELETE CASCADE,
    email TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', hearts INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS galleries_by_owner ON galleries (owner, created);
"""
DEFAULT_DAYS = 30
MAX_DAYS = 365
MAX_PHOTOS = 5000
MAX_COMMENT = 500
PASSWORD_TRIES = 10              # per address per 15 minutes
GALLERY_COOKIE = "autoedits_g"   # proof that the password was given, per gallery


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Galleries:
    def __init__(self, store):
        self.store = store
        with store._db() as db:
            db.executescript(SCHEMA)

    def create(self, wedding_id: str, owner: str, name: str, photo_ids: list[str], password: str, days: int,
               downloads: bool) -> tuple[dict, str]:
        token = secrets.token_urlsafe(24)
        gid = secrets.token_hex(6)
        now = time.time()
        with self.store._db() as db:
            db.execute("INSERT INTO galleries (id, token_hash, wedding_id, owner, name, password, downloads, created, expires) "
                       "VALUES (?,?,?,?,?,?,?,?,?)",
                       (gid, _h(token), wedding_id, owner, name, hash_password(password) if password else "", int(downloads),
                        now, now + days * 86400))
            db.executemany("INSERT INTO gallery_photos (gallery_id, photo_id, position) VALUES (?,?,?)",
                           [(gid, pid, i) for i, pid in enumerate(photo_ids)])
        return self.get(gid), token

    def get(self, gallery_id: str) -> dict | None:
        with self.store._db() as db:
            g = db.execute("SELECT * FROM galleries WHERE id=?", (gallery_id,)).fetchone()
            if g is None:
                return None
            n = db.execute("SELECT COUNT(*), COALESCE(SUM(heart),0), SUM(comment<>'') FROM gallery_photos WHERE gallery_id=?",
                           (gallery_id,)).fetchone()
            sends = db.execute("SELECT COUNT(*) FROM gallery_sends WHERE gallery_id=?", (gallery_id,)).fetchone()[0]
        out = {k: g[k] for k in ("id", "wedding_id", "owner", "name", "downloads", "created", "expires", "revoked", "views", "last_view")}
        out.update({"has_password": bool(g["password"]), "photos": n[0], "hearts": int(n[1]), "comments": int(n[2] or 0),
                    "sends": sends, "open": not g["revoked"] and g["expires"] > time.time()})
        return out

    def by_token(self, token: str) -> dict | None:
        with self.store._db() as db:
            g = db.execute("SELECT id, password FROM galleries WHERE token_hash=?", (_h(token),)).fetchone()
        if g is None:
            return None
        return {**self.get(g["id"]), "password_hash": g["password"]}

    def for_owner(self, owner: str) -> list[dict]:
        with self.store._db() as db:
            ids = [r["id"] for r in db.execute("SELECT id FROM galleries WHERE owner=? ORDER BY created DESC", (owner,))]
        return [self.get(i) for i in ids]

    def revoke(self, gallery_id: str) -> bool:
        with self.store._db() as db:
            return bool(db.execute("UPDATE galleries SET revoked=1 WHERE id=?", (gallery_id,)).rowcount)

    def viewed(self, gallery_id: str) -> None:
        with self.store._db() as db:
            db.execute("UPDATE galleries SET views=views+1, last_view=? WHERE id=?", (time.time(), gallery_id))

    def photos(self, gallery: dict) -> list[dict]:
        """The gallery's photos in order, with what the client has said about each."""
        rows = {p["id"]: p for p in self.store.photos(gallery["wedding_id"])}
        with self.store._db() as db:
            items = db.execute("SELECT photo_id, heart, comment FROM gallery_photos WHERE gallery_id=? ORDER BY position",
                               (gallery["id"],)).fetchall()
        out = []
        for i in items:
            p = rows.get(i["photo_id"])
            if p is None:
                continue
            out.append({**p, "heart": bool(i["heart"]), "comment": i["comment"]})
        return out

    def set_heart(self, gallery_id: str, photo_id: str, on: bool) -> bool:
        with self.store._db() as db:
            n = db.execute("UPDATE gallery_photos SET heart=? WHERE gallery_id=? AND photo_id=?", (int(on), gallery_id, photo_id)).rowcount
        if n:
            self.store.set_client_pick(photo_id, on)
        return bool(n)

    def set_comment(self, gallery_id: str, photo_id: str, text: str) -> bool:
        with self.store._db() as db:
            return bool(db.execute("UPDATE gallery_photos SET comment=? WHERE gallery_id=? AND photo_id=?",
                                   (text[:MAX_COMMENT], gallery_id, photo_id)).rowcount)

    def send(self, gallery_id: str, email: str, note: str) -> dict:
        with self.store._db() as db:
            hearts = db.execute("SELECT COUNT(*) FROM gallery_photos WHERE gallery_id=? AND heart=1", (gallery_id,)).fetchone()[0]
            db.execute("INSERT INTO gallery_sends (gallery_id, email, note, hearts, created) VALUES (?,?,?,?,?)",
                       (gallery_id, email[:200], note[:MAX_COMMENT], hearts, time.time()))
        return {"hearts": hearts}


def selection_email(base: str, gallery: dict, hearts: int, comments: int, email: str, note: str) -> tuple[str, str, str]:
    """The note to the photographer when a client sends their selection."""
    from .mail import _page
    who = f" from {email}" if email else ""
    line = (f"{gallery['name']}: the couple sent their selection{who}: {hearts} favourite{'s' if hearts != 1 else ''}"
            f"{f' and {comments} comment{chr(115) if comments != 1 else chr(0)}'.replace(chr(0), '') if comments else ''}. "
            f"Open the wedding and use the Client picks filter to see them.")
    if note:
        line += f" Their note: “{note}”"
    text, html = _page("Your client sent their picks", line, "Open Auto Edits", f"{base}/?wedding={gallery['wedding_id']}",
                       "Hearts and comments are kept in the gallery and shown in the app.")
    return f"Client picks for {gallery['name']}", text, html


def make_router(ctx: dict) -> APIRouter:
    signed_in, store, renderer, state = ctx["signed_in"], ctx["store"], ctx["renderer"], ctx["state"]
    wedding_or_404, mailer, link_base, client_ip = ctx["wedding_or_404"], ctx["mailer"], ctx["link_base"], ctx["client_ip"]
    accounts, drawing, STATIC, PREVIEW_CAP, SIZES = ctx["accounts"], ctx["drawing"], ctx["static"], ctx["preview_cap"], ctx["sizes"]
    galleries = Galleries(store)
    ctx["galleries"] = galleries
    r = APIRouter()
    no_store = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}

    # ---- the photographer's side ---------------------------------------------------------------
    @r.post("/api/weddings/{wedding_id}/galleries")
    def make_gallery(wedding_id: str, request: Request, body: dict = Body(...), user: dict = Depends(signed_in)):
        w = wedding_or_404(wedding_id, user)
        if user.get("viewing_as"):
            raise HTTPException(403, "Not while viewing someone else's account.")
        name = " ".join(str(body.get("name") or w["name"]).split())[:120] or w["name"]
        ratings = body.get("ratings") if isinstance(body.get("ratings"), list) else ["keep"]
        folder = body.get("folder")
        rows = store.photos(wedding_id, int(folder) if isinstance(folder, int) else None)
        if ratings:
            rows = [p for p in rows if (p["pick"] or (p["cull"] or {}).get("r")) in ratings]
        wanted = body.get("photos")
        if isinstance(wanted, list) and wanted:
            keep = {str(x) for x in wanted}
            rows = [p for p in rows if p["id"] in keep]
        if not rows:
            raise HTTPException(422, "There are no photos to share. Choose “Every photo”, or rate some Keep first.")
        if len(rows) > MAX_PHOTOS:
            raise HTTPException(422, f"A gallery can hold up to {MAX_PHOTOS:,} photos.")
        try:
            days = int(body.get("days") or DEFAULT_DAYS)
        except (TypeError, ValueError):
            days = DEFAULT_DAYS
        days = min(MAX_DAYS, max(1, days))
        password = str(body.get("password") or "").strip()[:100]
        g, token = galleries.create(wedding_id, user["id"], name, [p["id"] for p in rows], password, days, bool(body.get("downloads")))
        return {"gallery": g, "url": f"{link_base(request)}/g/{token}"}

    @r.get("/api/galleries")
    def my_galleries(user: dict = Depends(signed_in)):
        return {"galleries": galleries.for_owner(user["id"])}

    @r.get("/api/weddings/{wedding_id}/galleries")
    def wedding_galleries(wedding_id: str, user: dict = Depends(signed_in)):
        wedding_or_404(wedding_id, user)
        return {"galleries": [g for g in galleries.for_owner(user["id"]) if g["wedding_id"] == wedding_id]}

    @r.get("/api/galleries/{gallery_id}/picks")
    def picks(gallery_id: str, user: dict = Depends(signed_in)):
        g = galleries.get(gallery_id)
        if g is None or g["owner"] != user["id"]:
            raise HTTPException(404, "no such gallery")
        return {"photos": [{"id": p["id"], "name": p["file_name"], "folder": p["folder"], "heart": p["heart"], "comment": p["comment"]}
                           for p in galleries.photos(g) if p["heart"] or p["comment"]]}

    @r.delete("/api/galleries/{gallery_id}")
    def revoke(gallery_id: str, user: dict = Depends(signed_in)):
        g = galleries.get(gallery_id)
        if g is None or g["owner"] != user["id"]:
            raise HTTPException(404, "no such gallery")
        galleries.revoke(gallery_id)
        return {"revoked": True}

    # ---- the public side -----------------------------------------------------------------------
    def open_gallery(token: str) -> dict:
        g = galleries.by_token(token) if token and len(token) < 100 else None
        if g is None or not g["open"]:
            raise HTTPException(404, "This gallery link is not valid any more.")
        return g

    def unlocked(g: dict, cookie: str) -> bool:
        if not g["password_hash"]:
            return True
        want = state.get("gallery-ok", f"{g['id']}|{cookie}") if cookie else None
        return bool(want)

    @r.get("/g/{token}")
    def gallery_page(token: str):
        open_gallery(token)                                   # an unknown, expired or revoked link is a plain 404
        return FileResponse(STATIC / "gallery.html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                                               "X-Robots-Tag": "noindex"})

    @r.get("/api/g/{token}")
    def gallery_data(token: str, request: Request, autoedits_g: str = Cookie(default="")):
        """What the public page shows: the couple's name and the photos, once the password is given."""
        g = open_gallery(token)
        if not unlocked(g, autoedits_g):
            return {"name": g["name"], "locked": True}
        galleries.viewed(g["id"])
        photos = galleries.photos(g)
        return {"name": g["name"], "locked": False, "downloads": bool(g["downloads"]), "expires": g["expires"],
                "photos": [{"id": p["id"], "name": p["file_name"], "folder": p["folder"], "heart": p["heart"], "comment": p["comment"],
                            "w": p["meta"].get("width"), "h": p["meta"].get("height")} for p in photos]}

    @r.post("/api/g/{token}/unlock")
    def unlock(token: str, request: Request, response: Response, body: dict = Body(...)):
        g = open_gallery(token)
        if not g["password_hash"]:
            return {"ok": True}
        if state.hit("try", f"gallery|{client_ip(request)}", 900) >= PASSWORD_TRIES:
            raise HTTPException(429, "Too many tries. Please wait 15 minutes.")
        if not check_password(str(body.get("password") or ""), g["password_hash"]):
            time.sleep(1)
            raise HTTPException(401, "That password is not right.")
        proof = secrets.token_urlsafe(24)
        state.put("gallery-ok", f"{g['id']}|{proof}", True, 30 * 86400)
        response.set_cookie(GALLERY_COOKIE, proof, max_age=30 * 86400, httponly=True, samesite="lax", path="/",
                            secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
        return {"ok": True}

    def gallery_photo(g: dict, photo_id: str) -> dict:
        with store._db() as db:
            if db.execute("SELECT 1 FROM gallery_photos WHERE gallery_id=? AND photo_id=?", (g["id"], photo_id)).fetchone() is None:
                raise HTTPException(404, "no such photo")
        p = store.photo(g["wedding_id"], photo_id)
        if p is None:
            raise HTTPException(404, "no such photo")
        return p

    @r.get("/api/g/{token}/photos/{photo_id}/image")
    def gallery_image(token: str, photo_id: str, size: int = 1600, autoedits_g: str = Cookie(default="")):
        """The edited preview, marked and capped exactly as the photographer's own previews are
        (PREVIEW_CAP; the studio's galleries are unmarked), never larger than the gallery allows."""
        from engine.crop import clean as clean_crop
        g = open_gallery(token)
        if not unlocked(g, autoedits_g):
            raise HTTPException(401, "password needed")
        if size not in SIZES:
            raise HTTPException(422, f"size must be one of {SIZES}")
        p = gallery_photo(g, photo_id)
        owner = accounts.user(g["owner"])
        admin = bool(owner and owner["admin"])
        if not admin:
            size = min(size, PREVIEW_CAP)
        look = effective_look(p["settings"], p["folder_settings"], clean_crop(p.get("crop")))
        iso = p["meta"].get("iso") if isinstance(p["meta"].get("iso"), (int, float)) else None
        path = store.preview_path(g["wedding_id"], photo_id)
        headers = {"Cache-Control": "private, max-age=86400"}
        with drawing:
            data = renderer.render(path, look, size, iso, mark=not admin)
        return Response(data, media_type="image/jpeg", headers=headers)

    @r.get("/api/g/{token}/photos/{photo_id}/download")
    def gallery_download(token: str, photo_id: str, autoedits_g: str = Cookie(default="")):
        """The edited photo at the largest preview size, when the photographer allowed downloads."""
        from .main import attachment
        g = open_gallery(token)
        if not unlocked(g, autoedits_g):
            raise HTTPException(401, "password needed")
        if not g["downloads"]:
            raise HTTPException(403, "Downloads are not allowed in this gallery.")
        p = gallery_photo(g, photo_id)
        owner = accounts.user(g["owner"])
        admin = bool(owner and owner["admin"])
        from engine.crop import clean as clean_crop
        look = effective_look(p["settings"], p["folder_settings"], clean_crop(p.get("crop")))
        iso = p["meta"].get("iso") if isinstance(p["meta"].get("iso"), (int, float)) else None
        with drawing:
            data = renderer.render(store.preview_path(g["wedding_id"], photo_id), look, max(SIZES) if admin else PREVIEW_CAP, iso,
                                   mark=not admin)
        stem = p["file_name"].rsplit(".", 1)[0] or "photo"
        return Response(data, media_type="image/jpeg", headers={"Content-Disposition": attachment(f"{stem}.jpg"), **no_store})

    @r.put("/api/g/{token}/photos/{photo_id}/heart")
    def heart(token: str, photo_id: str, body: dict = Body(...), autoedits_g: str = Cookie(default="")):
        g = open_gallery(token)
        if not unlocked(g, autoedits_g):
            raise HTTPException(401, "password needed")
        on = bool(body.get("on"))
        if not galleries.set_heart(g["id"], photo_id, on):
            raise HTTPException(404, "no such photo")
        return {"heart": on}

    @r.put("/api/g/{token}/photos/{photo_id}/comment")
    def comment(token: str, photo_id: str, body: dict = Body(...), autoedits_g: str = Cookie(default="")):
        g = open_gallery(token)
        if not unlocked(g, autoedits_g):
            raise HTTPException(401, "password needed")
        text = " ".join(str(body.get("text") or "").split())[:MAX_COMMENT]
        if not galleries.set_comment(g["id"], photo_id, text):
            raise HTTPException(404, "no such photo")
        return {"comment": text}

    @r.post("/api/g/{token}/send")
    def send_selection(token: str, request: Request, body: dict = Body(...), autoedits_g: str = Cookie(default="")):
        """The couple is done choosing: the photographer gets an email, and the picks are in the app."""
        g = open_gallery(token)
        if not unlocked(g, autoedits_g):
            raise HTTPException(401, "password needed")
        if state.hit("try", f"gallery-send|{g['id']}", 3600) >= 20:
            raise HTTPException(429, "That was sent already. Please try again later.")
        email = str(body.get("email") or "").strip()[:200]
        note = " ".join(str(body.get("note") or "").split())[:MAX_COMMENT]
        sent = galleries.send(g["id"], email, note)
        owner = accounts.user(g["owner"])
        if owner:
            subject, text, html = selection_email(link_base(request), g, sent["hearts"], g["comments"], email, note)
            mailer.send(owner["email"], subject, text, html)
        return {"ok": True, "hearts": sent["hearts"]}

    return r
