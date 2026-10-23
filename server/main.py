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
import threading
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import VERSION, adjustments, blobs as blob_store, mail
from .backups import Backups
from .accounts import Accounts, NotEnoughCredits, cull_cost, public
from . import billing, legal, limits, payments as pay, security, webguard
from .accounts import AlreadyThere
from engine.presets import parse_xmp

from .exports import SIZES as EXPORT_SIZES, Exports
from .culling import RATINGS, Culls
from .looks import MY_PACK, SIZES, Renderer, clean_look, edit_stamp, effective_look, lightroom_edit, preset_from_look
from .state import State
from .store import Store

STATIC = Path(__file__).resolve().parent / "static"
PLUGIN = Path(__file__).resolve().parent.parent / "plugin" / "AutoEdits.lrplugin"
COOKIE = "autoedits"
VIEW_AS = "autoedits_as"                  # the admin is looking at a photographer's account (read-only)
LINK_SECONDS = 120                        # how long a "open in browser" link from the plug-in works

MAX_PREVIEW_BYTES = 30 * 1024 * 1024      # a 2560 px JPEG is 1 to 3 MB; a full-size camera preview up to about 20
MAX_PRESET_BYTES = 4 * 1024 * 1024         # presets with tone tables can reach a megabyte or two
MIN_FREE_BYTES = 2 * 1024 ** 3            # stop taking photos before the disk is full
MAX_PIXELS = 100_000_000                  # larger than any camera's preview; refused before it is decoded
LOOK_SECONDS = 2 * 86400                  # a long look handed over once ("lk=") is kept this long after last use
PREVIEW_CAP = 1600                        # largest on-screen preview for photographers (the studio gets 2560)
TICKET_SECONDS = 300                      # password given, two-step code still to come
RENDERS_AT_ONCE = 2                       # big previews drawn at the same time, so memory stays in bounds
APPLY_PAGE = 50                           # Lightroom list: most photos per page
APPLY_SECONDS = 12.0                      # ... and a page ends early once working it out took this long


def clean_name(text: str, default: str) -> str:
    """A tidy download name: odd characters become spaces, runs of spaces become one
    ("Ananya & Rohan, Jaipur" -> "Ananya Rohan Jaipur", not "Ananya _ Rohan_ Jaipur")."""
    return " ".join("".join(c if c.isalnum() or c in "-." else " " for c in text).split()) or default


def attachment(filename: str) -> str:
    """Content-Disposition for a download: a plain ASCII name for old browsers, and the real
    name (Hindi, accents...) as UTF-8 for everyone else. Header values must be Latin-1, so
    such a name sent as it is made the download fail."""
    fallback = "".join(c if c.isascii() and (c.isalnum() or c in " -_.") else "_" for c in filename).strip() or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


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
    agree: bool = False                        # ticked "I agree to the Terms of use and Privacy policy"


class Forgot(BaseModel):
    email: str


class Reset(BaseModel):
    token: str
    password: str


class Buy(BaseModel):
    kind: str
    credits: int = 0
    blocks: int = 0
    months: int = 0
    state: str = ""
    gstin: str = ""
    name: str = ""


class Paid(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class Code(BaseModel):
    code: str
    ticket: str = ""                           # from the password step of a two-step login


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
               mailer=None, razorpay=None) -> FastAPI:
    key = key if key is not None else os.environ.get("AUTOEDITS_KEY", "")
    if len(key) < 16:
        raise RuntimeError("set AUTOEDITS_KEY to a secret of at least 16 characters")
    store = Store(data_dir or os.environ.get("AUTOEDITS_DATA", "data/server"),
                  blobs if blobs is not None else blob_store.from_environment())
    admin_email = admin_email if admin_email is not None else os.environ.get("AUTOEDITS_ADMIN_EMAIL", "")
    accounts = Accounts(store, admin_email or "studio@auto-edits.local")
    backups = Backups(store)
    payments = pay.Payments(store, accounts, razorpay)
    mailer = mailer or mail.Mailer()
    base_url = os.environ.get("AUTOEDITS_BASE_URL", "").rstrip("/")
    app = FastAPI(title="Auto Edits", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(webguard.Guard, static=STATIC)       # security headers, and no forms sent from other sites
    app.state.store = store
    app.state.accounts = accounts
    exports = Exports(store)
    app.state.exports = exports
    culls = Culls(store)

    if store.blobs.on and os.environ.get("AUTOEDITS_WARM_UP") == "1":

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
    renderer.autos_store = store                 # automatic values are kept in the database (worked out after upload)
    state = State(store)
    drawing = threading.BoundedSemaphore(RENDERS_AT_ONCE)
    app.state.renderer = renderer
    for uploaded in store.uploaded_presets():
        renderer.add_uploaded(uploaded)
    if os.environ.get("AUTOEDITS_WARM_UP") == "1":
        threading.Thread(target=renderer.warm_up, daemon=True).start()
    # One-time sign-in links, two-step tickets, recent attempts and long looks live in SQLite (state.py),
    # so a restart (a deploy) neither breaks a sign-in halfway nor resets the limits on guessing.

    def client_ip(request: Request) -> str:
        """The visitor's address. Traefik adds the address it was reached from as the last entry of
        X-Forwarded-For; earlier entries come from the visitor and could be made up, so they are ignored."""
        forwarded = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",") if x.strip()]
        return forwarded[-1] if forwarded else (request.client.host if request.client else "")

    def too_many(what: str, request: Request, limit: int, seconds: int) -> bool:
        return state.hit("try", f"{what}|{client_ip(request)}", seconds) >= limit

    def failed_logins(email: str, add: bool) -> int:
        """Wrong passwords for one email address in the last hour, from anywhere."""
        return state.hit("try", f"login-email|{email.strip().lower()}", 3600, add) + (1 if add else 0)

    def key_ok(value: str) -> bool:
        """The old shared studio key. Stops working for good once the studio account is set up (security.py)."""
        return bool(value) and hmac.compare_digest(value.encode(), key.encode()) and not accounts.studio_key_retired()

    def fresh(user: dict) -> bool:
        """This sign-in gave a two-step code recently enough for admin work (always true before two-step is on)."""
        if not user.get("totp_on"):
            return True
        return time.time() - (user.get("session_checked") or 0) < security.ADMIN_FRESH_HOURS * 3600

    def signed_in(request: Request, x_auto_edits_key: str = Header(default=""), autoedits: str = Cookie(default=""),
                  autoedits_as: str = Cookie(default="")) -> dict:
        """The plug-in sends its key in a header; the browser holds a session cookie.
        When the admin chose "View as", the app works as that photographer, read-only."""
        user = None
        if x_auto_edits_key:
            user = accounts.user(accounts.admin_id) if key_ok(x_auto_edits_key) else accounts.plugin_user(x_auto_edits_key)
        if user is None and autoedits:
            user = accounts.session_user(autoedits)
            if user and user["admin"] and autoedits_as and autoedits_as != user["id"] and fresh(user):
                target = accounts.user(autoedits_as)
                if target:
                    if request.method not in ("GET", "HEAD"):
                        raise HTTPException(403, f"You are viewing {target['email']}'s account: nothing can be changed from here.")
                    return {**target, "viewing_as": user["email"]}
        if user is None:
            raise HTTPException(401, "Please log in again.")
        return user

    def admin_session(request: Request, autoedits: str = Cookie(default="")) -> dict:
        """The admin's own browser session, never a "View as" one or the plug-in's key."""
        user = accounts.session_user(autoedits)
        if user is None or not user["admin"]:
            raise HTTPException(404, "Not found")
        return {**user, "ip": client_ip(request), "token": autoedits}

    def the_admin(admin: dict = Depends(admin_session)) -> dict:
        """Admin work: with two-step login on, a code given on this sign-in in the last 12 hours."""
        if not fresh(admin):
            raise HTTPException(428, "Enter the 6-digit code from your authenticator app.")
        return admin

    def own_session(request: Request, autoedits: str = Cookie(default="")) -> dict:
        """Security settings: only from the person's own browser sign-in, not the plug-in's key."""
        user = accounts.session_user(autoedits)
        if user is None:
            raise HTTPException(401, "Please log in again.")
        return {**user, "ip": client_ip(request), "token": autoedits}

    def confirmed(user: dict = Depends(signed_in)) -> dict:
        """Culling and exporting spend credits: they need a confirmed email address."""
        if not user["verified"]:
            raise HTTPException(403, "Confirm your email address first: open the link we sent you.")
        return user

    def give_cookie(response: Response, request: Request, user_id: str, checked: bool = False):
        response.set_cookie(COOKIE, accounts.new_session(user_id, checked), max_age=30 * 24 * 3600, httponly=True, samesite="lax",
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
        if not body.agree:
            raise HTTPException(422, "Please tick the box to agree to the Terms of use and Privacy policy.")
        try:
            user = accounts.sign_up(body.email, body.password, body.name, client_ip(request), legal.TERMS_VERSION)
        except AlreadyThere:
            # The owner of the address gets a short note instead of a second account.
            subject, text, html = mail.signup_again(link_base(request))
            mailer.send(body.email.strip().lower(), subject, text, html)
            return {"ok": True, "check_email": True}
        except ValueError as e:
            raise HTTPException(422, str(e))
        # The same answer either way. (A new account is also signed in straight away, so the app opens;
        # sign-ups are limited to 8 an hour from one address, which keeps that from being used to test emails.)
        send_verify(request, user)
        give_cookie(response, request, user["id"])
        return {"ok": True, "check_email": True}

    @app.post("/api/login")
    def login(body: Login, request: Request, response: Response):
        if too_many("login", request, 10, 900):
            raise HTTPException(429, "Too many tries. Please wait 15 minutes.")
        user = None
        if body.key.strip():
            user = accounts.user(accounts.admin_id) if key_ok(body.key.strip()) else None
            if user and user["totp_on"]:     # the key cannot skip the code: use email, password and code
                user = None
        elif body.email and body.password:
            if failed_logins(body.email, False) >= 10:          # one address tried from many places
                raise HTTPException(429, "Too many tries for this account. Please wait an hour, or use \"Forgot password\".")
            user = accounts.log_in(body.email, body.password)
            if user is None:
                failed_logins(body.email, True)
        if user is None:
            time.sleep(1)                    # slows down guessing
            if body.email and (u := accounts.by_email(body.email)) and u["admin"]:
                accounts.log(u["id"], "admin login failed", detail="wrong password", ip=client_ip(request))
            raise HTTPException(401, "That email and password do not match.")
        state.delete("try", f"login|{client_ip(request)}")
        if user["totp_on"]:                  # second step: the code from the authenticator app
            ticket = secrets.token_urlsafe(24)
            state.put("ticket", ticket, {"until": time.time() + TICKET_SECONDS, "user": user["id"], "tries": 0}, TICKET_SECONDS)
            return {"need_code": True, "ticket": ticket}
        if user["admin"]:
            accounts.log(user["id"], "admin login", detail="studio key" if body.key.strip() else "password (no two-step yet)",
                         ip=client_ip(request))
        give_cookie(response, request, user["id"])
        return {"user": public(user)}

    @app.post("/api/login/code")
    def login_code(body: Code, request: Request, response: Response):
        if too_many("code", request, 8, 900):
            raise HTTPException(429, "Too many tries. Please wait 15 minutes.")
        t = state.get("ticket", body.ticket) if body.ticket else None
        if not t or t["until"] < time.time():
            state.delete("ticket", body.ticket)
            raise HTTPException(410, "That took too long. Please log in again.")
        how = accounts.check_second_step(t["user"], body.code)
        if not how:
            t["tries"] += 1
            if t["tries"] >= 5:
                state.delete("ticket", body.ticket)
            else:
                state.put("ticket", body.ticket, t, max(1.0, t["until"] - time.time()))
            time.sleep(1)
            u = accounts.user(t["user"])
            if u and u["admin"]:
                accounts.log(u["id"], "admin login failed", detail="wrong code", ip=client_ip(request))
            raise HTTPException(422, "That code is not right. Codes change every 30 seconds.")
        state.delete("ticket", body.ticket)
        state.delete("try", f"code|{client_ip(request)}")
        user = accounts.user(t["user"])
        if user["admin"]:
            accounts.log(user["id"], "admin login", detail="password + backup code" if how == "backup" else "password + code",
                         ip=client_ip(request))
        give_cookie(response, request, user["id"], checked=True)
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
        owner = accounts.peek_email_token(body.token, "reset")
        if not owner:
            raise HTTPException(410, "This link has expired or was already used. Ask for a new one.")
        if not 8 <= len(body.password) <= 200:
            raise HTTPException(422, "The password needs at least 8 characters.")
        u = accounts.user(owner)
        if u and u["admin"] and (problem := security.admin_password_problem(body.password, u["email"])):
            raise HTTPException(422, problem)          # checked before the link is used up
        user_id = accounts.use_email_token(body.token, "reset")
        if not user_id:
            raise HTTPException(410, "This link has expired or was already used. Ask for a new one.")
        try:
            accounts.set_password(user_id, body.password)
        except ValueError as e:
            raise HTTPException(422, str(e))
        accounts.verify(user_id)                   # the link came by email, so the address is theirs
        u = accounts.user(user_id)
        if u["admin"]:
            accounts.log(user_id, "password reset by email link", ip=client_ip(request))
            accounts.retire_studio_key_if_ready(client_ip(request))
        if u["totp_on"]:                           # an email link alone never skips the code: log in normally
            return {"user": None, "log_in": True}
        give_cookie(response, request, user_id)
        return {"user": public(u)}

    @app.get("/verify/{token}")
    def verify(token: str, request: Request):
        user_id = accounts.use_email_token(token, "verify")
        if not user_id:
            # a link opened twice: say so kindly when this browser is already signed in to a confirmed account
            me = accounts.session_user(request.cookies.get(COOKIE, ""))
            return RedirectResponse("/?verified=again" if me and me["verified"] else "/?link=used", status_code=303)
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
        from .nudges import free_status
        return {"user": public(user), "credits": accounts.balance(user["id"]), "free_given": accounts.got_free(user["id"]),
                "free": free_status(accounts, user["id"]),
                "storage": limits.usage(store, user), "payments_on": payments.rzp.on,
                "prices": {"export": 1, "cull_photos_per_credit": 10}}

    @app.get("/api/credits")
    def credits(user: dict = Depends(signed_in)):
        from .nudges import free_status
        return {"balance": accounts.balance(user["id"]), "unlimited": bool(user["admin"]),
                "free": free_status(accounts, user["id"]), "history": accounts.history(user["id"])}

    @app.get("/api/credits/statement.csv")
    def credit_statement(user: dict = Depends(signed_in)):
        """Every credit added or spent since the account was made, with the balance after each (oldest first)."""
        import csv
        import datetime as dt
        import io
        ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
        names = {"welcome": "Free credits", "cull": "Culling", "export": "Export", "refund": "Given back",
                 "lightroom": "Sent to Lightroom", "purchase": "Bought", "admin": "Added or taken back by Cele-Brat"}
        buf = io.StringIO()
        out = csv.writer(buf)
        out.writerow(["Date (India time)", "Type", "Details", "Wedding", "Photos", "Credits", "Balance after", "Reference"])
        for h in reversed(accounts.history(user["id"], None)):
            out.writerow([dt.datetime.fromtimestamp(h["created"], ist).strftime("%Y-%m-%d %H:%M"), names.get(h["kind"], h["kind"]),
                          h["note"], h["wedding_name"], h["photos"] or "", h["delta"], h["balance"], h["ref"]])
        day = dt.datetime.now(ist).strftime("%Y-%m-%d")
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="auto-edits-credits-{day}.csv"',
                                 "Cache-Control": "no-store"})

    @app.post("/api/plugin-key")
    def plugin_key(request: Request, user: dict = Depends(signed_in), autoedits: str = Cookie(default="")):
        """A new key for the Lightroom plug-in, shown once. The old one stops working."""
        if user["admin"]:
            me = accounts.session_user(autoedits)        # the studio's key: only from its own, recently checked sign-in
            if me is None or me["id"] != user["id"]:
                raise HTTPException(403, "Make the studio's Lightroom key from the app in your browser.")
            if not fresh(me):
                raise HTTPException(428, "Enter the 6-digit code from your authenticator app.")
        new = accounts.new_plugin_key(user["id"])
        if user["admin"]:
            accounts.log(user["id"], "studio Lightroom key made", detail="the previous one stopped working", ip=client_ip(request))
            accounts.retire_studio_key_if_ready(client_ip(request))
        return {"key": new}

    # ---- buying credits and storage (payments.py) -------------------------------------------
    # The Ajmer address goes only on invoices, and lives in deploy/.env (AUTOEDITS_INVOICE_ADDRESS, lines split by " | "),
    # not in this public code.
    SELLER = {"name": legal.BUSINESS, "gstin": legal.GSTIN, "email": legal.SUPPORT_EMAIL, "site": legal.SITE,
              "address": os.environ.get("AUTOEDITS_INVOICE_ADDRESS", "Ajmer, Rajasthan").replace(" | ", "\n")}

    @app.get("/api/buy/prices")
    def buy_prices(user: dict = Depends(signed_in)):
        return {"on": payments.rzp.on, "test": payments.rzp.test_mode, "credit_paise": pay.credit_price(),
                "launch_ends": "31 December 2026", "min_credits": pay.MIN_CREDITS, "credit_step": pay.CREDIT_STEP,
                "block_gb": pay.BLOCK_GB, "block_paise_month": pay.BLOCK_PAISE_MONTH, "max_blocks": pay.MAX_BLOCKS,
                "months": pay.MONTHS, "gst": pay.GST_RATE, "states": pay.STATES,
                "grants": payments.grants(user["id"])}

    @app.post("/api/buy/quote")
    def buy_quote(body: Buy, user: dict = Depends(signed_in)):
        try:
            return pay.quote(body.kind, body.credits, body.blocks, body.months, body.state, body.gstin)
        except pay.PaymentError as e:
            raise HTTPException(422, str(e))

    @app.post("/api/buy")
    def buy(body: Buy, request: Request, user: dict = Depends(confirmed)):
        if user.get("viewing_as"):
            raise HTTPException(403, "Not while viewing someone else's account.")
        if too_many(f"buy-{user['id']}", request, 20, 3600):
            raise HTTPException(429, "Too many tries. Please wait a while.")
        try:
            q = pay.quote(body.kind, body.credits, body.blocks, body.months, body.state, body.gstin)
            return payments.start(user, q, body.name)
        except pay.PaymentError as e:
            raise HTTPException(422 if payments.rzp.on else 503, str(e))

    @app.post("/api/buy/verify")
    def buy_verify(body: Paid, user: dict = Depends(signed_in)):
        """Checkout says the payment went through: check Razorpay's signature, then deliver."""
        o = payments.by_rzp(body.razorpay_order_id)
        if o is None or o["user_id"] != user["id"]:
            raise HTTPException(404, "no such order")
        if not payments.rzp.payment_ok(body.razorpay_order_id, body.razorpay_payment_id, body.razorpay_signature):
            raise HTTPException(400, "The payment could not be confirmed. If money was taken, it will be added within minutes, "
                                     "or refunded by Razorpay.")
        o = payments.fulfil(body.razorpay_order_id, body.razorpay_payment_id)
        return {"order": o["id"], "invoice": o["invoice_no"], "credits": accounts.balance(user["id"]),
                "storage": limits.usage(store, user)}

    @app.post("/api/razorpay/webhook")
    async def razorpay_webhook(request: Request):
        """Razorpay tells us about payments even when the browser tab was closed."""
        raw = await request.body()
        if not payments.rzp.webhook_ok(raw, request.headers.get("x-razorpay-signature", "")):
            raise HTTPException(400, "bad signature")
        try:
            event = json.loads(raw)
        except ValueError:
            raise HTTPException(400, "bad body")
        if event.get("event") in ("payment.captured", "order.paid"):
            p = (event.get("payload", {}).get("payment") or {}).get("entity") or {}
            if p.get("order_id") and p.get("status") == "captured":
                try:
                    payments.fulfil(p["order_id"], p.get("id", ""), int(p.get("amount", 0)))
                except pay.PaymentError:
                    raise HTTPException(400, "amount mismatch")
        elif str(event.get("event", "")).startswith("subscription."):
            return billing.handle_event(app, event)            # auto-renew storage (server/billing.py)
        return {"ok": True}

    @app.get("/api/purchases")
    def purchases(user: dict = Depends(signed_in)):
        return {"purchases": [{k: o[k] for k in ("id", "kind", "credits", "blocks", "months", "total", "invoice_no", "paid", "refunded", "sub_id")}
                              for o in payments.for_user(user["id"])], "grants": payments.grants(user["id"])}

    @app.get("/invoice/{order_id}")
    def invoice(order_id: str, autoedits: str = Cookie(default="")):
        """A printable tax invoice: for the buyer, or the admin."""
        me = accounts.session_user(autoedits)
        o = payments.get(order_id)
        if me is None or o is None or o["status"] != "paid" or (o["user_id"] != me["id"] and not me["admin"]):
            raise HTTPException(404, "Not found")
        return Response(pay.invoice_html(o, SELLER), media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "private, no-store"})

    @app.get("/api/admin/purchases")
    def admin_purchases(admin: dict = Depends(the_admin)):
        rows = payments.recent(300)
        return {"purchases": [{k: o[k] for k in ("id", "email", "kind", "credits", "blocks", "months", "taxable", "cgst", "sgst",
                                                 "igst", "total", "invoice_no", "paid", "rzp_payment", "buyer_gstin", "state", "test", "refunded", "sub_id")}
                              for o in rows], "test": payments.rzp.test_mode}

    # ---- two-step login (offered to the studio account) ----------------------------------
    @app.get("/api/two-step")
    def two_step_state(user: dict = Depends(own_session)):
        return {"on": bool(user["totp_on"]), "backup_codes_left": security.backup_codes_left(user["backup_codes"]),
                "checked": fresh(user) and bool(user["totp_on"]),
                "studio": accounts.studio_setup() if user["admin"] else None}

    @app.post("/api/two-step/start")
    def two_step_start(user: dict = Depends(own_session)):
        """A new secret for the authenticator app, as a QR code. Nothing changes until a code is confirmed."""
        if not user["admin"]:
            raise HTTPException(404, "Not found")
        if user["totp_on"]:
            raise HTTPException(409, "Two-step login is already on.")
        if not user["password"]:
            raise HTTPException(409, "Set a password first, then turn on two-step login.")
        secret = accounts.two_step_start(user["id"])
        link = security.setup_link(secret, user["email"])
        return {"secret": secret, "qr": security.qr_svg(link)}

    @app.post("/api/two-step/confirm")
    def two_step_confirm(body: Code, request: Request, user: dict = Depends(own_session)):
        if too_many("code", request, 8, 900):
            raise HTTPException(429, "Too many tries. Please wait 15 minutes.")
        codes = accounts.two_step_confirm(user["id"], body.code)
        if codes is None:
            time.sleep(1)
            raise HTTPException(422, "That code is not right. Check the phone's clock is set automatically, and try the newest code.")
        accounts.mark_checked(user["token"])
        accounts.log(user["id"], "two-step login turned on", ip=user["ip"])
        accounts.retire_studio_key_if_ready(user["ip"])
        return {"on": True, "backup_codes": codes}

    @app.post("/api/two-step/backup-codes")
    def two_step_new_codes(body: Code, request: Request, user: dict = Depends(own_session)):
        if too_many("code", request, 8, 900):
            raise HTTPException(429, "Too many tries. Please wait 15 minutes.")
        """Ten fresh backup codes (the old ones stop working). Needs a current code."""
        if not user["totp_on"]:
            raise HTTPException(409, "Two-step login is not on.")
        if not accounts.check_second_step(user["id"], body.code):
            time.sleep(1)
            raise HTTPException(422, "That code is not right.")
        accounts.log(user["id"], "new backup codes made", ip=user["ip"])
        return {"backup_codes": accounts.new_backup_codes(user["id"])}

    @app.post("/api/password")
    def change_password(body: NewPassword, request: Request, user: dict = Depends(signed_in)):
        if user["password"] and not accounts.log_in(user["email"], body.current):
            time.sleep(1)
            raise HTTPException(403, "The current password is not right.")
        try:
            accounts.set_password(user["id"], body.password)
        except ValueError as e:
            raise HTTPException(422, str(e))
        if user["admin"]:
            accounts.log(user["id"], "password set" if not user["password"] else "password changed", ip=client_ip(request))
            accounts.retire_studio_key_if_ready(client_ip(request))
        return {"ok": True, "signed_out": True}

    @app.post("/api/login-link")
    def login_link(request: Request, user: dict = Depends(signed_in)):
        """For the plug-in: a link that signs the browser in once, without typing a password."""
        token = secrets.token_urlsafe(32)
        state.put("link", token, user["id"], LINK_SECONDS)
        return {"path": f"/enter/{token}", "seconds": LINK_SECONDS}

    @app.get("/enter/{token}")
    def enter(token: str, request: Request):
        user_id = state.take("link", token)
        response = RedirectResponse("/", status_code=303)
        if user_id:
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
        exports.refund_wedding(wedding_id)           # exports still to be made: their credits come back
        store.delete_wedding(wedding_id)
        return {"deleted": True}

    @app.get("/api/weddings/{wedding_id}/received")
    def received(wedding_id: str, user: dict = Depends(signed_in)):
        """Lightroom ids already on the server, so an interrupted send can carry on."""
        wedding_or_404(wedding_id, user)
        return {"uuids": store.received_uuids(wedding_id)}

    @app.post("/api/weddings/{wedding_id}/photos")
    def add_photo(wedding_id: str, meta: str = Form(...), preview: UploadFile = File(...),
                  user: dict = Depends(signed_in)):
        """Plain def: FastAPI runs it in its thread pool, so the database and storage work of one
        upload never holds up everyone else's requests."""
        from engine.images import jpeg_size
        wedding_or_404(wedding_id, user)
        try:
            info = json.loads(meta)
            if not isinstance(info, dict):
                raise ValueError
        except ValueError:
            raise HTTPException(422, "meta is not valid")
        if not info.get("edited"):               # the plug-in writes an empty list as {}
            info["edited"] = []
        data = preview.file.read(MAX_PREVIEW_BYTES + 1)
        if len(data) > MAX_PREVIEW_BYTES:
            raise HTTPException(413, "preview is too large")
        if data[:3] != b"\xff\xd8\xff":
            raise HTTPException(415, "preview must be a JPEG")
        dims = jpeg_size(data)
        if dims is None:
            raise HTTPException(415, "The preview could not be read as a JPEG.")
        if dims[0] * dims[1] > MAX_PIXELS or not min(dims):
            raise HTTPException(413, f"This picture is {dims[0]:,} × {dims[1]:,} pixels: the most is "
                                     f"{MAX_PIXELS // 1_000_000} megapixels. Send it at 2560 pixels (the plug-in's setting).")
        if store.free_space() < MIN_FREE_BYTES:
            raise HTTPException(507, "the server disk is nearly full")
        try:
            limits.check_upload(store, user, wedding_id, str(info.get("uuid") or ""), len(data))
        except limits.LimitReached as e:
            raise HTTPException(403, str(e))
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
                     "edited": len(r["meta"].get("edited") or []), "cull": r["cull"], "pick": r["pick"] or None,
                     "crop": r["crop"]} for r in rows]
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
        from .nudges import free_status
        return {"ok": True, "version": VERSION, "user": public(user), "credits": accounts.balance(user["id"]),
                "free": free_status(accounts, user["id"]), "viewing_as": user.get("viewing_as")}

    # ---- the admin dashboard -------------------------------------------------------
    @app.get("/admin")
    def admin_page(admin: dict = Depends(admin_session)):
        return FileResponse(STATIC / "admin.html", headers={"Cache-Control": "no-cache"})

    @app.post("/api/admin/confirm")
    def admin_confirm(body: Code, request: Request, admin: dict = Depends(admin_session)):
        """The 6-digit code again, for admin work on a sign-in that has not given one in the last 12 hours."""
        if too_many("code", request, 8, 900):
            raise HTTPException(429, "Too many tries. Please wait 15 minutes.")
        how = accounts.check_second_step(admin["id"], body.code)
        if not how:
            time.sleep(1)
            accounts.log(admin["id"], "admin code failed", ip=admin["ip"])
            raise HTTPException(422, "That code is not right. Codes change every 30 seconds.")
        accounts.mark_checked(admin["token"])
        accounts.log(admin["id"], "admin code given", detail="backup code" if how == "backup" else "", ip=admin["ip"])
        return {"ok": True}

    @app.get("/api/admin/setup")
    def admin_setup(admin: dict = Depends(admin_session)):
        """What the studio account still needs before the old shared key retires (shown on /admin)."""
        return {**accounts.studio_setup(), "backup_codes_left": security.backup_codes_left(admin["backup_codes"]),
                "needs_code": not fresh(admin)}

    @app.get("/api/admin/activity")
    def admin_activity(admin: dict = Depends(the_admin)):
        return {"activity": accounts.activity(200)}

    @app.get("/api/admin/backups")
    def admin_backups(admin: dict = Depends(the_admin)):
        return {"status": backups.status(), "copies": [{k: c[k] for k in ("day", "where")} for c in backups.list()]}

    @app.post("/api/admin/backups/run")
    def admin_backup_now(admin: dict = Depends(the_admin)):
        accounts.log(admin["id"], "backup run by hand", ip=admin["ip"])
        try:
            backups.run()
        except Exception as e:
            raise HTTPException(500, f"The backup did not finish: {e}")
        return admin_backups(admin)

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
                "storage": limits.usage(store, u), "limits": {"photos": u["limit_photos"], "gb": u["limit_gb"],
                                                               "default_photos": limits.PHOTOS_PER_WEDDING,
                                                               "default_gb": limits.STORAGE_GB},
                "weddings": [{k: w[k] for k in ("id", "name", "created", "photos", "bytes")} | store.health(w["id"]) |
                             {"exports": [{k: e[k] for k in ("id", "size", "done", "total", "state", "created", "scope")}
                                          for e in exports.for_wedding(w["id"])]}
                             for w in store.weddings(user_id)]}

    @app.post("/api/admin/users/{user_id}/limits")
    def admin_limits(user_id: str, body: dict = Body(...), admin: dict = Depends(the_admin)):
        """Raise (or reset with 0) one photographer's photos-per-wedding and storage limits."""
        if accounts.user(user_id) is None:
            raise HTTPException(404, "no such account")
        try:
            photos, gb = int(body.get("photos") or 0), int(body.get("gb") or 0)
        except (TypeError, ValueError):
            raise HTTPException(422, "limits must be whole numbers")
        if photos < 0 or gb < 0 or photos > 1_000_000 or gb > 10_000:
            raise HTTPException(422, "limits are out of range")
        accounts.set_limits(user_id, photos, gb)
        accounts.log(admin["id"], "limits changed", target=user_id,
                     detail=f"photos per wedding {photos or 'default'} · storage {f'{gb} GB' if gb else 'default'}", ip=admin["ip"])
        return {"ok": True}

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
        accounts.log(admin["id"], "credits added" if n > 0 else "credits taken back", target=user_id,
                     detail=f"{n:+d} · {note}", ip=admin["ip"])
        return {"credits": accounts.balance(user_id)}

    @app.post("/api/admin/view-as/{user_id}")
    def view_as(user_id: str, request: Request, response: Response, admin: dict = Depends(the_admin)):
        if accounts.user(user_id) is None:
            raise HTTPException(404, "no such account")
        accounts.log(admin["id"], "view as started", target=user_id, detail="read-only, up to 4 hours", ip=admin["ip"])
        response.set_cookie(VIEW_AS, user_id, max_age=4 * 3600, httponly=True, samesite="lax",
                            secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
        return {"ok": True}

    @app.post("/api/admin/view-as-stop")
    def view_as_stop(request: Request, response: Response, autoedits: str = Cookie(default=""),
                     autoedits_as: str = Cookie(default="")):
        me = accounts.session_user(autoedits)
        if autoedits_as and me and me["admin"]:
            accounts.log(me["id"], "view as ended", target=autoedits_as, ip=client_ip(request))
        response.delete_cookie(VIEW_AS)
        return {"ok": True}

    @app.get("/api/admin/weddings/{wedding_id}/originals.zip")
    def admin_originals(wedding_id: str, admin: dict = Depends(the_admin)):
        owner = store.owner_of(wedding_id)
        if owner is None:
            raise HTTPException(404, "no such wedding")
        w = store.wedding(wedding_id)
        accounts.log(admin["id"], "downloaded uploaded photos", target=owner,
                     detail=f"wedding: {w['name'] if w else wedding_id}", ip=admin["ip"])
        return originals_zip(wedding_id, None, accounts.user(owner))

    @app.get("/api/admin/exports/{export_id}/zip")
    def admin_export_zip(export_id: str, admin: dict = Depends(the_admin)):
        e = exports.get(export_id)
        if e is None:
            raise HTTPException(404, "no such export")
        owner = store.owner_of(e["wedding_id"])
        w = store.wedding(e["wedding_id"])
        accounts.log(admin["id"], "downloaded edited photos", target=owner or "",
                     detail=f"wedding: {w['name'] if w else e['wedding_id']} · export {export_id}", ip=admin["ip"])
        return export_zip(export_id, accounts.user(owner))

    @app.get("/api/weddings/{wedding_id}/originals.zip")
    def originals_zip(wedding_id: str, folder: int | None = None, user: dict = Depends(signed_in)):
        """The photos as they were uploaded (camera previews), in their event folders, as one zip."""
        w = wedding_or_404(wedding_id, user)
        rows = store.photos(wedding_id, folder)
        if not rows:
            raise HTTPException(404, "no photos")
        if user.get("viewing_as") and (a := accounts.by_email(user["viewing_as"])):
            accounts.log(a["id"], "downloaded uploaded photos", target=user["id"], detail=f"wedding: {w['name']} (in View as)")
        name = clean_name(w["name"], "photos")
        return StreamingResponse(store.zip_previews(wedding_id, rows), media_type="application/zip",
                                 headers={"Content-Disposition": attachment(f"{name} - uploaded photos.zip")})

    @app.get("/api/adjustments")
    def adjustment_panels(user: dict = Depends(signed_in)):
        return {"panels": adjustments.PANELS}

    @app.get("/api/presets")
    def presets(user: dict = Depends(signed_in)):
        return {"presets": renderer.catalogue(user["id"])}

    @app.post("/api/presets")
    def upload_presets(files: list[UploadFile] = File(...), user: dict = Depends(signed_in)):
        """Presets exported from Lightroom (.xmp), masks included. The original file is
        kept, so the very same preset can be applied when the edits go back to Lightroom."""
        results = []
        mine = sum(1 for p in store.uploaded_presets() if p.get("owner") == user["id"])
        for f in files[:200]:
            if not user["admin"] and mine >= limits.PRESETS_PER_ACCOUNT:
                results.append({"file": f.filename or "preset.xmp", "ok": False,
                                "why": f"You have {limits.PRESETS_PER_ACCOUNT:,} presets, the most one account can keep."})
                continue
            name = f.filename or "preset.xmp"
            raw = f.file.read(MAX_PRESET_BYTES + 1)
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
            mine += 0 if replaced else 1
            renderer.add_uploaded(d)
            results.append({"file": name, "ok": True, "id": d["id"], "name": p.name, "replaced": replaced,
                            "masks": len(p.masks)})
        return {"results": results}

    @app.post("/api/presets/new")
    def create_preset(body: dict = Body(...), user: dict = Depends(signed_in)):
        """Save what the sliders show now as a preset of your own (Lightroom's "Create Preset").
        With "replace": the id of one of your presets, that preset is updated instead."""
        name = " ".join(str(body.get("name") or "").split())[:80]
        group = " ".join(str(body.get("group") or "").split())[:60]
        if not name:
            raise HTTPException(422, "Give the preset a name.")
        replace = body.get("replace")
        if replace and renderer.owner_of(replace) != user["id"]:
            raise HTTPException(404, "Only presets you made or uploaded can be updated.")
        if not replace:
            mine = sum(1 for p in store.uploaded_presets() if p.get("owner") == user["id"])
            if not user["admin"] and mine >= limits.PRESETS_PER_ACCOUNT:
                raise HTTPException(403, f"You have {limits.PRESETS_PER_ACCOUNT:,} presets, the most one account can keep.")
        look = clean_look(body.get("look"), allowed_presets(user))
        preset, xmp = preset_from_look(renderer, look, name, group, user["id"], bool(user["admin"]),
                                       with_masks=body.get("masks", True) is not False,
                                       with_wb=body.get("white_balance", True) is not False)
        if replace:
            preset["id"] = replace
        store.save_preset(preset, xmp, user["id"])
        renderer.add_uploaded(preset)
        return {"id": preset["id"], "name": name, "replaced": bool(replace)}

    @app.get("/api/presets/{preset_id}/xmp")
    def preset_xmp(preset_id: str, user: dict = Depends(signed_in)):
        """One of your presets as a Lightroom file, to import in Lightroom Classic."""
        text = store.preset_file(preset_id) if renderer.owner_of(preset_id) == user["id"] else None
        if not text:
            raise HTTPException(404, "Only presets you made or uploaded can be downloaded.")
        name = renderer.presets[preset_id]["display_name"]
        safe = "".join(c if c.isalnum() or c in " -_." else "_" for c in name).strip() or "preset"
        return Response(text, media_type="application/octet-stream",
                        headers={"Content-Disposition": attachment(f"{safe}.xmp")})

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
        look["crop"] = None                      # crops belong to photos, not to looks
        if not store.set_folder_settings(wedding_id, folder_id, look):
            raise HTTPException(404, "no such folder")
        return {"look": look}

    @app.put("/api/weddings/{wedding_id}/photos/{photo_id}/look")
    def set_photo_look(wedding_id: str, photo_id: str, look: dict | None = Body(default=None),
                       user: dict = Depends(signed_in)):
        """Give one photo its own look, or send null to put it back on its folder's look."""
        wedding_or_404(wedding_id, user)
        look = clean_look(look, allowed_presets(user)) if look else None
        if look:
            look["crop"] = None
        if not store.set_photo_settings(wedding_id, photo_id, look):
            raise HTTPException(404, "no such photo")
        return {"look": look}

    def read_look(text: str, user: dict) -> dict:
        try:
            return clean_look(json.loads(text), allowed_presets(user))
        except ValueError:
            raise HTTPException(422, "the look could not be read")

    # Looks too long for a picture's address (brush masks) are handed over once and named by a short key.

    @app.post("/api/looks")
    def keep_look(look: dict = Body(...), user: dict = Depends(signed_in)):
        text = json.dumps(look, sort_keys=True, separators=(",", ":"))
        if len(text) > 400_000:
            raise HTTPException(413, "This look is too large.")
        key = hashlib.sha1((user["id"] + text).encode()).hexdigest()[:24]
        state.put("look", key, [user["id"], text], LOOK_SECONDS)
        if secrets.randbelow(50) == 0:
            state.keep_at_most("look", 3000)
        return {"key": key}

    def given_look(look: str, lk: str, user: dict) -> str:
        if not lk:
            return look
        owner, text = state.get("look", lk, ("", ""), touch=LOOK_SECONDS)
        if owner != user["id"]:
            raise HTTPException(410, "look not kept any more")         # the app hands it over again
        return text

    @app.get("/api/weddings/{wedding_id}/photos/{photo_id}/image")
    def image(wedding_id: str, photo_id: str, size: int = 1600, look: str = "", base: bool = False, show: str = "",
              uncropped: bool = False, lk: str = "", user: dict = Depends(signed_in)):
        """The photo as the app shows it: untouched (base), with the look given,
        or, with neither, with the look saved for it. Edited views larger than the grid's
        carry a light preview mark until they are exported (not on the studio account)."""
        if size not in SIZES:
            raise HTTPException(422, f"size must be one of {SIZES}")
        if not user["admin"]:
            size = min(size, PREVIEW_CAP)          # marked previews stay proof-sized; full quality comes from export
        if show and not show.replace("_", "").isalnum():
            raise HTTPException(422, "show must name a mask")
        look = given_look(look, lk, user)
        p = photo_or_404(wedding_id, photo_id, user)
        path = store.preview_path(wedding_id, photo_id)
        iso = p["meta"].get("iso") if isinstance(p["meta"].get("iso"), (int, float)) else None
        headers = {"Cache-Control": "private, max-age=86400"}
        chosen = read_look(look, user) if look else effective_look(p["settings"], p["folder_settings"])
        chosen["crop"] = crop_of(p)
        if base:
            from engine.crop import rect, turn_of
            crop = None if uncropped else rect(chosen["crop"])
            angle = renderer.develop(path, chosen, iso)["angle"] if crop else 0.0
            with drawing:
                data = renderer.base(path, size, crop, angle, turn_of(chosen["crop"]))
            return Response(data, media_type="image/jpeg", headers=headers)
        if size > 360:
            with drawing:                        # at most RENDERS_AT_ONCE big pictures in memory at a time
                data = renderer.render(path, chosen, size, iso, show, mark=not user["admin"], uncropped=uncropped)
            return Response(data, media_type="image/jpeg", headers=headers)
        # small pictures for the grid are kept on disk, one per photo, look and version of its preset
        tag = hashlib.sha1(json.dumps([photo_id, p["received"], size, chosen, renderer.preset_version(chosen.get("preset"))],
                                      sort_keys=True).encode()).hexdigest()[:20]
        cached = store.cache_dir(wedding_id) / f"{tag}.jpg"
        if not cached.exists():
            with drawing:
                data = renderer.render(path, chosen, size, iso)
            part = cached.with_suffix(f".{secrets.token_hex(4)}.part")
            part.write_bytes(data)
            part.replace(cached)
        return FileResponse(cached, media_type="image/jpeg", headers=headers)

    @app.get("/api/weddings/{wedding_id}/photos/{photo_id}/develop")
    def develop(wedding_id: str, photo_id: str, look: str = "", lk: str = "", user: dict = Depends(signed_in)):
        """What a look works out to for this photo, including the automatic values."""
        look = given_look(look, lk, user)
        p = photo_or_404(wedding_id, photo_id, user)
        chosen = read_look(look, user) if look else effective_look(p["settings"], p["folder_settings"])
        chosen["crop"] = crop_of(p)
        iso = p["meta"].get("iso") if isinstance(p["meta"].get("iso"), (int, float)) else None
        d = renderer.develop(store.preview_path(wedding_id, photo_id), chosen, iso)
        return {"look": chosen, "preset": d["preset"], "auto": d["auto"], "auto_wb": d["auto_wb"], "masks": d["masks"],
                "preset_masks": d["corrections"]}

    # ---- crop: a setting per photo, never a change to the photo itself -----------------
    def crop_of(p: dict) -> dict | None:
        from engine.crop import clean
        return clean(p.get("crop"))

    @app.put("/api/weddings/{wedding_id}/photos/{photo_id}/crop")
    def set_crop(wedding_id: str, photo_id: str, crop: dict | None = Body(default=None), user: dict = Depends(signed_in)):
        """Save one photo's crop, or send null to show the whole picture again."""
        from engine.crop import clean, turn_of
        p = photo_or_404(wedding_id, photo_id, user)
        turn = (crop or {}).get("turn", turn_of(crop_of(p)))       # a crop sent without a turn keeps the photo's turn
        kept = clean({**(crop or {}), "turn": turn})
        store.set_crop(wedding_id, photo_id, kept)
        return {"crop": kept}

    @app.put("/api/weddings/{wedding_id}/photos/{photo_id}/turn")
    def turn_photo(wedding_id: str, photo_id: str, body: dict = Body(...), user: dict = Depends(signed_in)):
        """Turn one photo by 90 degrees (by: 90 clockwise, -90 the other way, 180). Only a setting: the
        photo itself is never changed; its crop turns with it."""
        from engine.crop import turn_by
        try:
            by = int(body.get("by"))
        except (TypeError, ValueError):
            raise HTTPException(422, "by must be 90, -90 or 180")
        if by not in (90, -90, 180, 270):
            raise HTTPException(422, "by must be 90, -90 or 180")
        p = photo_or_404(wedding_id, photo_id, user)
        kept = turn_by(crop_of(p), by)
        store.set_crop(wedding_id, photo_id, kept)
        return {"crop": kept}

    @app.post("/api/weddings/{wedding_id}/photos/{photo_id}/auto-crop")
    def auto_crop(wedding_id: str, photo_id: str, save: bool = False, keep_own: bool = False, ratio: str = "original",
                  user: dict = Depends(signed_in)):
        """AI crop: finds the people and frames them (the photo's own shape unless a ratio is given).
        save=1 keeps it; keep_own=1 leaves a crop made by hand alone (for a whole folder)."""
        from engine.crop import RATIOS
        p = photo_or_404(wedding_id, photo_id, user)
        if keep_own and p.get("crop") and p["crop"].get("w") and not p["crop"].get("auto"):
            return {"crop": crop_of(p), "why": "kept your own crop", "changed": False}
        if ratio not in RATIOS or ratio == "free":
            ratio = "original"
        look = effective_look(p["settings"], p["folder_settings"], crop_of(p))
        try:
            crop, why = renderer.auto_crop(store.preview_path(wedding_id, photo_id), look, ratio)
        except FileNotFoundError:
            raise HTTPException(404, "no such photo")
        from engine.crop import clean, turn_of
        turn = turn_of(crop_of(p))
        if turn:                                                    # the AI crop is of the turned photo; keep the turn
            crop = clean({**(crop or {}), "turn": turn})
        if save:
            store.set_crop(wedding_id, photo_id, crop)
        return {"crop": crop, "why": why, "changed": True}

    @app.post("/api/weddings/{wedding_id}/folders/{folder_id}/reset")
    def reset_folder(wedding_id: str, folder_id: int, user: dict = Depends(signed_in)):
        """Every photo in the folder back to how it was uploaded: no folder look, no photo's own
        settings, no crops. Only settings change; the photos themselves never did."""
        wedding_or_404(wedding_id, user)
        if not store.set_folder_settings(wedding_id, folder_id, {}):
            raise HTTPException(404, "no such folder")
        n = 0
        for r in store.photos(wedding_id, folder_id):
            if r["settings"] or r["crop"]:
                store.set_photo_settings(wedding_id, r["id"], None)
                store.set_crop(wedding_id, r["id"], None)
                n += 1
        return {"reset": True, "photos_with_own": n}

    @app.delete("/api/weddings/{wedding_id}/folders/{folder_id}/crops")
    def clear_crops(wedding_id: str, folder_id: int, user: dict = Depends(signed_in)):
        """Every photo in the folder back to the whole picture."""
        wedding_or_404(wedding_id, user)
        n = 0
        from engine.crop import clean, rect, turn_of
        for r in store.photos(wedding_id, folder_id):
            if rect(clean(r["crop"])):                              # cut-outs go; a photo's quarter turn stays
                store.set_crop(wedding_id, r["id"], clean({"turn": turn_of(clean(r["crop"]))}))
                n += 1
        return {"cleared": n}

    # ---- culling: 1 credit per 10 photos, every time it runs ----------------------
    @app.post("/api/weddings/{wedding_id}/cull")
    def start_cull(wedding_id: str, body: NewCull, user: dict = Depends(confirmed)):
        w = wedding_or_404(wedding_id, user)
        chosen = [f for f in w["folders"] if (body.folder is None or f["id"] == body.folder) and f["photos"]]
        if not [f for f in w["folders"] if body.folder is None or f["id"] == body.folder]:
            raise HTTPException(404, "no such folder")
        if body.strictness not in ("relaxed", "normal", "strict"):
            raise HTTPException(422, "strictness must be relaxed, normal or strict")
        ids = culls.photo_ids([f["id"] for f in chosen])          # fixed now: exactly these are paid for and culled
        photos = sum(len(v) for v in ids.values())
        where = w["name"] if body.folder is None else f"{w['name']} / {chosen[0]['name'] if chosen else ''}"
        try:
            accounts.charge(user, cull_cost(photos), "cull", f"Culling {photos} photos: {where}", wedding_id, photos,
                           wedding=wedding_id)
        except NotEnoughCredits as e:
            raise no_credits(e)
        share, before = {}, 0                                    # each folder's part of the charge, given back if it fails
        for f, v in ids.items():
            share[f] = cull_cost(before + len(v)) - cull_cost(before)
            before += len(v)
        return {"culls": culls.start(wedding_id, list(ids), body.strictness, body.best_only, ids, share, wedding_id),
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
            accounts.charge(user, photos, "export", f"Export of {photos} photos: {w['name']}", export_id, photos,
                           wedding=wedding_id)
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
        if user.get("viewing_as") and (a := accounts.by_email(user["viewing_as"])):
            accounts.log(a["id"], "downloaded edited photos", target=user["id"], detail=f"export {export_id} (in View as)")
        name = clean_name(wedding_or_404(e["wedding_id"], user)["name"], "export")
        return StreamingResponse(exports.zip_stream(export_id), media_type="application/zip",
                                 headers={"Content-Disposition": attachment(f"{name}.zip")})

    # ---- back to Lightroom: 1 credit per photo, paid when its edit is handed out ----------
    def preview_for_edit(wedding_id: str, photo_id: str, look: dict, iso) -> Path:
        """Where the preview is; fetched from storage only when its automatic values are not all kept yet."""
        from .looks import auto_parts
        path = store.preview_path(wedding_id, photo_id, fetch=False)
        if path.exists():
            return path
        kept = store.get_autos(photo_id)
        if all(part in kept or part == "lens" for part in auto_parts(clean_look(look)["toggles"], iso)):
            return path
        return store.preview_path(wedding_id, photo_id)

    def edits_for(w: dict, rows: list[dict], again: bool, seconds: float | None = None) -> tuple[list, list]:
        """The Lightroom edit of each photo (stops early once this many seconds have gone). Returns every
        item, and (photo id, item) for those due: not yet applied with exactly this edit, or all with again."""
        folders = {f["id"]: f["settings"] for f in w["folders"]}
        began = time.monotonic()
        out, wanted = [], []
        for r in rows:
            if seconds is not None and out and time.monotonic() - began > seconds:
                break                                      # the rest come on the next page
            look = effective_look(r["settings"], folders.get(r["folder_id"]), r["crop"])
            iso = r["meta"].get("iso") if isinstance(r["meta"].get("iso"), (int, float)) else None
            edit = lightroom_edit(renderer, preview_for_edit(w["id"], r["id"], look, iso), look, iso, r["meta"].get("develop"))
            item = {"uuid": r["lr_uuid"], "name": r["file_name"], "folder": r["folder"], "edit": edit}
            if edit:
                item["stamp"] = edit_stamp(edit)
                item["done"] = r["applied"] == item["stamp"]
                if again or not item["done"]:
                    item["cost"] = 1 if again or r["paid_stamp"] != item["stamp"] else 0
                    wanted.append((r["id"], item))
            out.append(item)
        return out, wanted

    @app.get("/api/weddings/{wedding_id}/apply")
    def apply_list(wedding_id: str, offset: int = 0, limit: int = 200, again: int = 0, dry: int = 0, paged: int = 0,
                   user: dict = Depends(confirmed)):
        """For the plug-in: what Lightroom should do to each photo, a page at a time.
        "done" is true when Lightroom has already applied exactly this edit.

        Paid when handed out (Bharat, Oct 2026): an edit costs 1 credit the moment this list gives it.
        The same edit handed out again (a retry, a crash, listing again) costs nothing; again=1 or a
        changed edit costs again. Never below 0 credits: only as many photos as the balance covers get
        their edit; the rest come back with "no_credits" and no edit. The studio account is never charged.

        dry=1: only what it would cost: due photos are marked "due" with their "cost", nothing is handed
        out or paid (plug-in 0.8 asks this first, for its "This uses N credits" question).
        paged=1 (plug-in 0.8): a page may hold fewer photos than asked, ending after APPLY_SECONDS so it
        stays well within the plug-in's 30 s; carry on from "next". Plug-in 0.7 steps by its own 200 and
        gets every photo it asked for."""
        w = wedding_or_404(wedding_id, user)
        rows = store.photos(wedding_id)
        start = max(0, offset)
        page = rows[start:start + max(1, min(APPLY_PAGE if paged else 500, limit))]
        out, wanted = edits_for(w, page, bool(again), APPLY_SECONDS if paged else None)
        base = {"total": len(rows), "offset": offset, "next": start + len(out), "photos": out}
        if dry:
            for _, item in wanted:
                item.update(edit=None, due=True)
                item.pop("stamp")
            return {**base, "no_credits": 0, "balance": None if user["admin"] else accounts.balance(user["id"])}
        given = accounts.charge_edits(user, w, [(pid, item["stamp"]) for pid, item in wanted], bool(again))
        short = 0
        for pid, item in wanted:
            if pid not in given:
                item.update(edit=None, no_credits=True, cost=0)
                item.pop("stamp")
                short += 1
        if offset <= 0 and short and not given:            # nothing at all can be paid for (older plug-ins show this)
            raise no_credits(NotEnoughCredits(short, accounts.balance(user["id"])))
        return {**base, "no_credits": short}

    @app.post("/api/weddings/{wedding_id}/apply")
    def apply_some(wedding_id: str, body: dict = Body(...), user: dict = Depends(confirmed)):
        """Plug-in 0.8: the edits of just these photos ({"uuids": [...], "again": 0/1}, at most 100), handed
        out and paid for now as above, a chunk at a time just before Lightroom applies them. So photos
        not in this catalogue, and a run cancelled halfway, cost nothing for what was not fetched."""
        w = wedding_or_404(wedding_id, user)
        uuids = body.get("uuids")
        if not isinstance(uuids, list) or len(uuids) > 100:
            raise HTTPException(422, "uuids must be a list of at most 100 Lightroom ids")
        asked = {str(u) for u in uuids}
        again = bool(body.get("again"))
        out, wanted = edits_for(w, [r for r in store.photos(wedding_id) if r["lr_uuid"] in asked], again)
        given = accounts.charge_edits(user, w, [(pid, item["stamp"]) for pid, item in wanted], again)
        short = 0
        for pid, item in wanted:
            if pid not in given:
                item.update(edit=None, no_credits=True, cost=0)
                item.pop("stamp")
                short += 1
        return {"photos": out, "no_credits": short}

    @app.post("/api/weddings/{wedding_id}/applied")
    def applied(wedding_id: str, stamps: dict = Body(...), user: dict = Depends(confirmed)):
        """Lightroom has applied these edits. They were paid for when the list handed them out,
        so this only remembers them (and an edit never handed out cannot be marked)."""
        wedding_or_404(wedding_id, user)
        return {"marked": store.mark_applied(wedding_id, stamps)}

    # ---- the Lightroom plug-in, zipped on the fly ---------------------------------
    @app.api_route("/download/lightroom-plugin", methods=["GET", "HEAD"])
    def plugin_zip():
        """AutoEdits.lrplugin as one zip. Public: it holds no secrets (each photographer pastes their own key)."""
        import io
        import zipfile
        if not (PLUGIN / "Info.lua").exists():
            raise HTTPException(404, "The plug-in is not on this server.")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(PLUGIN.rglob("*")):
                if f.is_file() and not f.name.startswith("."):
                    z.write(f, f"AutoEdits.lrplugin/{f.relative_to(PLUGIN).as_posix()}")
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": 'attachment; filename="AutoEdits-Lightroom-plugin.zip"',
                                 "Cache-Control": "no-cache"})

    # ---- for search engines and AI assistants ------------------------------------
    SITE = os.environ.get("AUTOEDITS_SITE", "https://edits.cele-brat.solutions").rstrip("/")

    @app.api_route("/robots.txt", methods=["GET", "HEAD"])
    def robots():
        """The home page is public; everything behind the log-in stays out of search."""
        return Response("User-agent: *\nAllow: /$\nAllow: /static/\nAllow: /llms.txt\n"
                        + "".join(f"Allow: /{slug}\n" for slug in legal.PAGES) + "Disallow: /api/\nDisallow: /enter/\n"
                        "Disallow: /verify/\nDisallow: /reset/\nDisallow: /admin\n\n" f"Sitemap: {SITE}/sitemap.xml\n", media_type="text/plain")

    @app.api_route("/sitemap.xml", methods=["GET", "HEAD"])
    def sitemap():
        changed = time.strftime("%Y-%m-%d", time.gmtime((STATIC / "home.html").stat().st_mtime))
        return Response('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                        f"  <url><loc>{SITE}/</loc><lastmod>{changed}</lastmod></url>\n"
                        + "".join(f"  <url><loc>{SITE}/{slug}</loc></url>\n" for slug in legal.PAGES)
                        + "</urlset>\n", media_type="application/xml")

    @app.api_route("/{slug}", methods=["GET", "HEAD"])
    def policy_page(slug: str):
        """Terms, Privacy, Refunds, Delivery and Contact (server/legal.py)."""
        body = legal.page(slug)
        if body is None:
            raise HTTPException(404, "Not found")
        return Response(body, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=600"})

    @app.api_route("/llms.txt", methods=["GET", "HEAD"])
    def llms():
        """A plain summary of the product for AI assistants that read sites on a person's behalf."""
        return FileResponse(STATIC / "llms.txt", media_type="text/markdown; charset=utf-8")

    @app.api_route("/", methods=["GET", "HEAD"])
    def home(autoedits: str = Cookie(default="")):
        """The app for someone logged in, the public home page for everyone else."""
        inside = accounts.session_user(autoedits) is not None
        return FileResponse(STATIC / ("index.html" if inside else "home.html"), headers={"Cache-Control": "no-cache"})

    app.include_router(billing.router(app, accounts, payments, mailer, store, renderer, signed_in, confirmed, the_admin,
                                      link_base, SELLER, too_many))   # payments phase 2, bulk presets (server/billing.py)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def app():                                   # uvicorn server.main:app --factory
    return create_app()
