"""Account deletion and change of email address (DPDP Act, 2023; Bharat, Oct 2026).

Delete my account (Account window, Security): password + the word DELETE typed.
  1. POST /api/account/delete  checks both, then emails a link (valid DELETE_HOURS = 24 h).
  2. GET  /account/delete/<token>  performs the deletion and signs the browser out:
     - photos, exports and uploaded presets removed from storage (R2) and this disk;
     - weddings, photos, folders, exports, presets rows deleted;
     - the user row anonymised: email -> deleted-<id>@invalid, name blank, password, plug-in key,
       two-step secrets and backup codes emptied, every session and email link removed;
     - ledger rows and orders stay (tax law; the ledger is never changed);
     - a line in the admin log ("account deleted by its owner").
  The studio (admin) account cannot be deleted from here.

Change email: POST /api/account/email with the new address and the password. The NEW address gets a
confirm link (valid EMAIL_HOURS = 24 h); GET /account/email/<token> makes the change, and the OLD
address gets a notice. Pending changes live in the ephemeral table (state.py), which keeps the token hashed.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .accounts import EMAIL_RE, check_password

DELETE_HOURS = 24
EMAIL_HOURS = 24


class DeleteMe(BaseModel):
    password: str = ""
    confirm: str = ""                                # the word DELETE, typed


class NewEmail(BaseModel):
    email: str
    password: str = ""


# ---- emails ----------------------------------------------------------------------------------
def delete_email(base: str, token: str) -> tuple[str, str, str]:
    from .mail import _page
    text, html = _page("Delete your Auto Edits account?",
                       "You asked to delete your Auto Edits account. Clicking the button removes your uploaded photos, "
                       "exports and presets for good, and your name and email from the account. Credit and invoice "
                       "records are kept as tax law requires. There is no undo.",
                       "Yes, delete my account", f"{base}/account/delete/{token}",
                       f"The link works for {DELETE_HOURS} hours. If this was not you, ignore this email: nothing is deleted "
                       "without the link, and you may want to change your password.")
    return "Confirm: delete your Auto Edits account", text, html


def deleted_email() -> tuple[str, str]:
    return ("Your Auto Edits account has been deleted",
            "Your Auto Edits account has been deleted, with its photos, exports and presets. Credit and invoice records are "
            "kept as tax law requires. Thank you for trying Auto Edits.\n\nAuto Edits by Cele-Brat")


def change_email(base: str, token: str, old: str) -> tuple[str, str, str]:
    from .mail import _page
    text, html = _page("Confirm your new email", f"Someone asked to move the Auto Edits account {old} to this address. "
                       "Confirm, and this becomes the address you log in with.", "Use this email", f"{base}/account/email/{token}",
                       f"The link works for {EMAIL_HOURS} hours. If this was not you, ignore this email: nothing changes.")
    return "Confirm your new Auto Edits email", text, html


def changed_email(new: str) -> tuple[str, str]:
    return ("Your Auto Edits email has changed",
            f"The email address of your Auto Edits account has been changed to {new}. If this was not you, write to "
            "support@cele-brat.com straight away.\n\nAuto Edits by Cele-Brat")


# ---- the work --------------------------------------------------------------------------------
def delete_account(store, accounts, renderer, user_id: str, ip: str = "") -> dict:
    """Everything of this photographer's gone; the anonymised user row, the ledger and orders stay."""
    u = accounts.user(user_id)
    if u is None or u["admin"]:
        raise ValueError("no such account")
    weddings = [w["id"] for w in store.weddings(user_id, archived=True)]
    for wid in weddings:
        store.delete_wedding(wid)                  # rows (folders, photos, exports, culls cascade) + disk + bucket
    presets = [p["id"] for p in store.uploaded_presets() if p.get("owner") == user_id]
    for pid in presets:
        if renderer is not None:
            renderer.remove_uploaded(pid)
        store.delete_preset(pid)
    with store._db() as db:
        db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        db.execute("DELETE FROM email_tokens WHERE user_id=?", (user_id,))
        db.execute("UPDATE users SET email=?, name='', password='', plugin_key='', verified=0, totp_secret='', totp_pending='', "
                   "totp_on=0, totp_last=0, backup_codes='', signup_ip='' WHERE id=?", (f"deleted-{user_id}@invalid", user_id))
        db.execute("DELETE FROM ephemeral WHERE kind IN ('delete-account','change-email') AND v LIKE ?", (f'%"{user_id}"%',))
    accounts.log("", "account deleted by its owner", target=user_id,
                 detail=f"{len(weddings)} weddings, {len(presets)} presets removed; ledger and orders kept", ip=ip)
    return {"weddings": len(weddings), "presets": len(presets)}


def router(ctx: dict) -> APIRouter:
    r = APIRouter()
    store, accounts, renderer, state, mailer = ctx["store"], ctx["accounts"], ctx["renderer"], ctx["state"], ctx["mailer"]
    own_session, link_base, client_ip, too_many = ctx["own_session"], ctx["link_base"], ctx["client_ip"], ctx["too_many"]
    cookie = ctx["cookie"]

    def password_ok(user: dict, password: str) -> bool:
        return bool(user["password"]) and check_password(password, user["password"])

    @r.post("/api/account/delete")
    def ask_delete(body: DeleteMe, request: Request, user: dict = Depends(own_session)):
        if user["admin"]:
            raise HTTPException(403, "The studio account cannot be deleted from here.")
        if too_many(f"delete-{user['id']}", request, 5, 3600):
            raise HTTPException(429, "Too many tries. Please wait an hour.")
        if body.confirm.strip() != "DELETE":
            raise HTTPException(422, "Type DELETE (in capitals) to confirm.")
        if not password_ok(user, body.password):
            time.sleep(1)
            raise HTTPException(403, "The password is not right.")
        import secrets
        token = secrets.token_urlsafe(32)
        state.put("delete-account", token, user["id"], DELETE_HOURS * 3600)
        subject, text, html = delete_email(link_base(request), token)
        mailer.send(user["email"], subject, text, html)
        return {"ok": True, "check_email": True, "hours": DELETE_HOURS}

    @r.get("/account/delete/{token}")
    def do_delete(token: str, request: Request):
        user_id = state.take("delete-account", token)
        if not user_id:
            return RedirectResponse("/?link=used", status_code=303)
        u = accounts.user(user_id)
        if u is None or u["admin"] or u["email"].endswith("@invalid"):
            return RedirectResponse("/?link=used", status_code=303)
        email = u["email"]
        delete_account(store, accounts, renderer, user_id, client_ip(request))
        subject, text = deleted_email()
        mailer.send(email, subject, text)
        response = RedirectResponse("/?deleted=1", status_code=303)
        response.delete_cookie(cookie)
        response.delete_cookie("autoedits_as")
        return response

    @r.post("/api/account/email")
    def ask_change(body: NewEmail, request: Request, user: dict = Depends(own_session)):
        if too_many(f"email-{user['id']}", request, 5, 3600):
            raise HTTPException(429, "Too many tries. Please wait an hour.")
        new = body.email.strip().lower()
        if not EMAIL_RE.match(new) or len(new) > 200:
            raise HTTPException(422, "Please enter a valid email address.")
        if new == user["email"]:
            raise HTTPException(422, "That is already the address of this account.")
        if not password_ok(user, body.password):
            time.sleep(1)
            raise HTTPException(403, "The password is not right.")
        # Always the same answer, so this cannot be used to find out who has an account. An address that
        # already has one gets the usual "you already have an account" note instead of a link.
        if accounts.by_email(new):
            from . import mail
            subject, text, html = mail.signup_again(link_base(request))
            mailer.send(new, subject, text, html)
            return {"ok": True, "check_email": True, "hours": EMAIL_HOURS}
        import secrets
        token = secrets.token_urlsafe(32)
        state.put("change-email", token, {"user": user["id"], "email": new}, EMAIL_HOURS * 3600)
        subject, text, html = change_email(link_base(request), token, user["email"])
        mailer.send(new, subject, text, html)
        return {"ok": True, "check_email": True, "hours": EMAIL_HOURS}

    @r.get("/account/email/{token}")
    def do_change(token: str, request: Request):
        pending = state.take("change-email", token)
        if not pending:
            return RedirectResponse("/?link=used", status_code=303)
        u = accounts.user(pending["user"])
        if u is None or accounts.by_email(pending["email"]):
            return RedirectResponse("/?link=used", status_code=303)
        old = u["email"]
        with store._db() as db:
            db.execute("UPDATE users SET email=?, verified=1 WHERE id=?", (pending["email"], u["id"]))
        if u["admin"]:
            accounts.log(u["id"], "studio email changed", detail="by email link", ip=client_ip(request))
        subject, text = changed_email(pending["email"])
        mailer.send(old, subject, text)
        return RedirectResponse("/?email=changed", status_code=303)

    return r
