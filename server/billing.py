"""Payments phase 2 and the hub rules (Bharat, Oct 2026): a router added to main.py with one line.

  GET  /api/billing/prices            live prices (settings table), the Season pack, subscriptions and grants
  POST /api/storage/subscribe         auto-renew storage: Razorpay Subscription, Checkout gets subscription_id
  POST /api/storage/subscribe/verify  Checkout says the first charge went through (signature checked)
  POST /api/storage/cancel            stop auto-renew at the end of the month already paid
  (webhook)                           main.py's /api/razorpay/webhook hands subscription.* events to handle_event();
                                      payment.failed never marks anything paid
  GET  /credit-note/<id>              printable credit note (buyer or admin)
  GET  /api/admin/prices  POST        the "Prices" box on /admin (logged)
  POST /api/admin/purchases/<id>/refund   full or partial refund with a reason
  GET  /api/admin/purchases/<id>/refunds
  POST /api/presets/bulk              a zip or a dropped folder of .xmp presets, packs from the folder names

Invoices are emailed as a PDF (wkhtmltopdf through pdfkit when installed; otherwise the invoice HTML file)
from Payments.on_paid, which fulfil() calls once per order.
"""
from __future__ import annotations

import io
import json
import os
import posixpath
import shutil
import subprocess
import threading
import zipfile

from fastapi import APIRouter, Body, Cookie, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from . import limits, mail, payments as pay
from .looks import MY_PACK
from engine.presets import parse_xmp

MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_BULK_FILES = 1000
MAX_PRESET_BYTES = 4 * 1024 * 1024


class Subscribe(BaseModel):
    blocks: int = 1
    state: str = ""
    gstin: str = ""
    name: str = ""


class SubPaid(BaseModel):
    razorpay_subscription_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class Refund(BaseModel):
    amount: int = 0           # paise; 0 means everything still refundable
    reason: str = ""


# ---- invoice as PDF -------------------------------------------------------------------------
def html_to_pdf(html: str) -> bytes | None:
    """The invoice page as a PDF with wkhtmltopdf when the machine has it (pip package pdfkit wraps it;
    the binary is what matters). None when it is not there: the HTML file is attached instead."""
    exe = shutil.which("wkhtmltopdf")
    if not exe or os.environ.get("AUTOEDITS_NO_PDF") == "1":
        return None
    try:
        r = subprocess.run([exe, "--quiet", "--encoding", "utf-8", "--page-size", "A4", "-", "-"],
                           input=html.encode("utf-8"), capture_output=True, timeout=40)
        return r.stdout if r.returncode == 0 and r.stdout.startswith(b"%PDF") else None
    except Exception:
        return None


def attachment_for(name: str, html: str) -> tuple[str, bytes, str]:
    pdf = html_to_pdf(html.replace('<div class="print">', '<div class="print" style="display:none">'))
    if pdf:
        return f"{name}.pdf", pdf, "application/pdf"
    return f"{name}.html", html.encode("utf-8"), "text/html"


def safe_name(number: str) -> str:
    return number.replace("/", "-")


# ---- bulk preset import ----------------------------------------------------------------------
def _clean_path(path: str) -> str | None:
    """A relative path inside the upload: no absolute paths, no "..", only .xmp files."""
    path = (path or "").replace("\\", "/").lstrip("/")
    parts = [p for p in posixpath.normpath(path).split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or p.startswith("__MACOSX") for p in parts) or ":" in parts[0]:
        return None
    if not parts[-1].lower().endswith(".xmp") or parts[-1].startswith("."):
        return None
    return "/".join(parts)


def pack_of(path: str) -> str:
    """Top-level folder = pack; nested = "Pack / Sub". A file with no folder goes to My presets."""
    parts = path.split("/")[:-1]
    parts = [p.replace(" - Presets", "").strip() for p in parts if p.strip()]
    if parts and parts[0].lower() in ("presets", "develop presets", "user presets", "settings"):
        parts = parts[1:]
    return " / ".join(parts[:2]) if parts else MY_PACK


def unpack(files: list[tuple[str, bytes]]) -> tuple[list[tuple[str, bytes]], list[dict]]:
    """Expands any zip in the list into (path, bytes) items; returns the items and the skips."""
    out, skipped = [], []
    for name, raw in files:
        if name.lower().endswith(".zip"):
            if len(raw) > MAX_ZIP_BYTES:
                skipped.append({"file": name, "why": "The zip is larger than 50 MB."})
                continue
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    for info in z.infolist():
                        if info.is_dir():
                            continue
                        path = _clean_path(info.filename)
                        if path is None:
                            if info.filename.lower().endswith(".xmp"):
                                skipped.append({"file": info.filename, "why": "Unsafe path inside the zip."})
                            continue
                        if info.file_size > MAX_PRESET_BYTES:
                            skipped.append({"file": path, "why": "The file is too large to be a preset."})
                            continue
                        if len(out) >= MAX_BULK_FILES:
                            skipped.append({"file": path, "why": f"More than {MAX_BULK_FILES} files: the rest were left out."})
                            break
                        out.append((path, z.read(info)))
            except zipfile.BadZipFile:
                skipped.append({"file": name, "why": "This is not a zip file."})
        else:
            path = _clean_path(name)
            if path is None:
                skipped.append({"file": name.replace("\\", "/").split("/")[-1], "why": "Not a .xmp preset file."})
            elif len(out) >= MAX_BULK_FILES:
                skipped.append({"file": path, "why": f"More than {MAX_BULK_FILES} files: the rest were left out."})
            else:
                out.append((path, raw))
    return out, skipped


def handle_event(app, event: dict) -> dict:
    """A Razorpay webhook event (already signature-checked by main.py)."""
    return app.state.billing_event(event)


# ---- the router -------------------------------------------------------------------------------
def router(app, accounts, payments: pay.Payments, mailer, store, renderer, signed_in, confirmed, the_admin,
           link_base, seller: dict, too_many) -> APIRouter:
    r = APIRouter()
    base_default = os.environ.get("AUTOEDITS_BASE_URL", "").rstrip("/") or seller.get("site", "")

    # ---- emails after a payment / refund ----
    def send_invoice(o: dict) -> None:
        total = pay.rupees(o["total"])
        subject, text, html = mail.invoice_mail(base_default, o, pay.describe(o), total)
        att = attachment_for(f"Auto Edits invoice {safe_name(o['invoice_no'])}", pay.invoice_html(o, seller))
        mailer.send(o["buyer_email"], subject, text, html, attachments=[att])

    payments.on_paid = send_invoice

    def send_credit_note(rf: dict) -> None:
        subject, text, html = mail.credit_note_mail(base_default, rf, pay.rupees(rf["amount"]))
        att = attachment_for(f"Auto Edits credit note {safe_name(rf['note_no'])}", pay.credit_note_html(rf, seller))
        mailer.send(rf["buyer_email"], subject, text, html, attachments=[att])

    # ---- prices ----
    @r.get("/api/prices")
    def public_prices():
        """For the home page pricing card: live prices, nothing personal."""
        p = pay.prices()
        p.pop("settings", None)
        return Response(json.dumps(p), media_type="application/json", headers={"Cache-Control": "public, max-age=300"})

    @r.get("/api/billing/prices")
    def billing_prices(user: dict = Depends(signed_in)):
        p = pay.prices()
        p.pop("settings", None)
        subs = [{k: s[k] for k in ("id", "blocks", "amount", "status", "charges", "next_charge", "created")}
                for s in payments.subscriptions(user["id"]) if s["status"] in ("created", "active", "cancelling", "halted")]
        return {**p, "on": payments.rzp.on, "test": payments.rzp.test_mode, "states": pay.STATES,
                "grants": payments.grants(user["id"]), "subscriptions": subs}

    # ---- auto-renew storage ----
    @r.post("/api/storage/subscribe")
    def storage_subscribe(body: Subscribe, request: Request, user: dict = Depends(confirmed)):
        if user.get("viewing_as"):
            raise HTTPException(403, "Not while viewing someone else's account.")
        if too_many(f"buy-{user['id']}", request, 20, 3600):
            raise HTTPException(429, "Too many tries. Please wait a while.")
        try:
            return payments.subscribe(user, body.blocks, body.state, body.gstin, body.name)
        except pay.PaymentError as e:
            raise HTTPException(422 if payments.rzp.on else 503, str(e))

    @r.post("/api/storage/subscribe/verify")
    def storage_subscribe_verify(body: SubPaid, user: dict = Depends(signed_in)):
        s = payments.sub_by_rzp(body.razorpay_subscription_id)
        if s is None or s["user_id"] != user["id"]:
            raise HTTPException(404, "no such subscription")
        if not payments.rzp.subscription_ok(body.razorpay_subscription_id, body.razorpay_payment_id, body.razorpay_signature):
            raise HTTPException(400, "The payment could not be confirmed. If money was taken, the storage will be added "
                                     "within minutes, or refunded by Razorpay.")
        o = payments.charged(body.razorpay_subscription_id, body.razorpay_payment_id)
        return {"order": o["id"], "invoice": o["invoice_no"], "storage": limits.usage(store, user)}

    @r.post("/api/storage/cancel")
    def storage_cancel(body: dict = Body(default={}), user: dict = Depends(signed_in)):
        if user.get("viewing_as"):
            raise HTTPException(403, "Not while viewing someone else's account.")
        sid = str(body.get("sub") or "")
        if not sid:
            live = [s for s in payments.subscriptions(user["id"]) if s["status"] in ("created", "active")]
            if not live:
                raise HTTPException(404, "There is no auto-renew storage to cancel.")
            sid = live[0]["id"]
        try:
            s = payments.cancel(user["id"], sid)
        except pay.PaymentError as e:
            raise HTTPException(422, str(e))
        return {"status": s["status"], "until": max([g["until"] for g in payments.grants(user["id"])] or [0])}

    def handle_event_(event: dict) -> dict:
        kind = event.get("event", "")
        payload = event.get("payload", {})
        p = (payload.get("payment") or {}).get("entity") or {}
        sub = (payload.get("subscription") or {}).get("entity") or {}
        try:
            if kind == "subscription.charged" and sub.get("id") and p.get("id"):
                payments.charged(sub["id"], p["id"], int(p.get("amount", 0)) or None, float(sub.get("charge_at") or 0))
            elif kind in ("subscription.cancelled", "subscription.halted", "subscription.completed",
                          "subscription.expired", "subscription.paused", "subscription.resumed", "subscription.activated"):
                status = {"activated": "active", "resumed": "active", "paused": "halted"}.get(kind.split(".")[1], kind.split(".")[1])
                if sub.get("id"):
                    payments.sub_status(sub["id"], status)
            elif kind in ("payment.captured", "order.paid"):
                if p.get("order_id") and p.get("status") == "captured":
                    payments.fulfil(p["order_id"], p.get("id", ""), int(p.get("amount", 0)))
            # payment.failed and everything else: nothing is marked paid
        except pay.PaymentError:
            raise HTTPException(400, "amount mismatch")
        return {"ok": True}

    app.state.billing_event = handle_event_

    # ---- credit notes ----
    @r.get("/credit-note/{refund_id}")
    def credit_note(refund_id: str, autoedits: str = Cookie(default="")):
        me = accounts.session_user(autoedits)
        rf = payments.get_refund(refund_id)
        if me is None or rf is None or (rf["user_id"] != me["id"] and not me["admin"]):
            raise HTTPException(404, "Not found")
        return Response(pay.credit_note_html(rf, seller), media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "private, no-store"})

    @r.get("/api/refunds")
    def my_refunds(user: dict = Depends(signed_in)):
        with payments._db() as db:
            rows = [dict(x) for x in db.execute("SELECT r.id, r.order_id, r.amount, r.credits, r.note_no, r.created, o.invoice_no "
                                                "FROM refunds r JOIN orders o ON o.id=r.order_id WHERE r.user_id=? ORDER BY r.created DESC",
                                                (user["id"],))]
        return {"refunds": rows}

    # ---- admin: refunds and prices ----
    @r.get("/api/admin/purchases/{order_id}/refunds")
    def admin_refunds(order_id: str, admin: dict = Depends(the_admin)):
        o = payments.get(order_id)
        if o is None:
            raise HTTPException(404, "no such order")
        return {"refunds": payments.refunds_for(order_id), "refundable": payments.refundable(o), "order": o}

    @r.post("/api/admin/purchases/{order_id}/refund")
    def admin_refund(order_id: str, body: Refund, admin: dict = Depends(the_admin)):
        o = payments.get(order_id)
        if o is None or o["status"] != "paid":
            raise HTTPException(404, "no such paid order")
        reason = " ".join(body.reason.split())[:300]
        if not reason:
            raise HTTPException(422, "Give a reason: it goes on the credit note.")
        amount = body.amount or payments.refundable(o)["money"]
        try:
            rf = payments.refund(order_id, int(amount), reason)
        except pay.PaymentError as e:
            raise HTTPException(422, str(e))
        accounts.log(admin["id"], "refund made", target=o["user_id"],
                     detail=f"{pay.rupees(rf['amount'])} of {o['invoice_no']} → {rf['note_no']}; {rf['credits']} credits back · {reason}",
                     ip=admin["ip"])
        try:
            send_credit_note(rf)
        except Exception as e:
            print(f"[billing] credit note email failed: {type(e).__name__}: {e}", flush=True)
        return {"refund": rf["id"], "note": rf["note_no"], "amount": rf["amount"], "credits": rf["credits"],
                "balance": accounts.balance(o["user_id"])}

    @r.get("/api/admin/prices")
    def admin_prices(admin: dict = Depends(the_admin)):
        p = pay.prices()
        return {"settings": p["settings"], "defaults": {k: str(v) for k, v in pay.PRICE_KEYS.items()}, "live": {k: v for k, v in p.items() if k != "settings"}}

    @r.post("/api/admin/prices")
    def admin_set_prices(body: dict = Body(...), admin: dict = Depends(the_admin)):
        try:
            changed = payments.set_prices(body)
        except pay.PaymentError as e:
            raise HTTPException(422, str(e))
        if changed:
            accounts.log(admin["id"], "prices changed", detail="; ".join(f"{k}: {a} → {b}" for k, (a, b) in changed.items()),
                         ip=admin["ip"])
        return {"changed": list(changed), "settings": pay.prices()["settings"]}

    # ---- bulk preset import ----
    @r.post("/api/presets/bulk")
    def presets_bulk(files: list[UploadFile] = File(...), paths: str = Form(default=""), user: dict = Depends(signed_in)):
        """Many .xmp files with their folder paths (a dropped folder), or a zip of them. Packs come from the
        folder names. Reuses the single-file rules (size, parsing, the per-account limit)."""
        if user.get("viewing_as"):
            raise HTTPException(403, "Not while viewing someone else's account.")
        try:
            given = json.loads(paths) if paths else []
        except ValueError:
            given = []
        raw_files = []
        total = 0
        for i, f in enumerate(files[:MAX_BULK_FILES + 1]):
            data = f.file.read(MAX_ZIP_BYTES + 1)
            total += len(data)
            if total > MAX_ZIP_BYTES:
                raise HTTPException(413, "Too much at once: up to 50 MB of presets in one go.")
            name = (given[i] if i < len(given) and given[i] else None) or f.filename or f"preset-{i}.xmp"
            raw_files.append((name, data))
        items, skipped = unpack(raw_files)
        packs: dict[str, int] = {}
        added = replaced = 0
        mine = sum(1 for p in store.uploaded_presets() if p.get("owner") == user["id"])
        for path, raw in items:
            short = path.split("/")[-1]
            if not user["admin"] and mine >= limits.PRESETS_PER_ACCOUNT:
                skipped.append({"file": short, "why": f"You have {limits.PRESETS_PER_ACCOUNT:,} presets, the most one account can keep."})
                continue
            if len(raw) > MAX_PRESET_BYTES:
                skipped.append({"file": short, "why": "The file is too large to be a preset."})
                continue
            pack = pack_of(path)
            try:
                text = raw.decode("utf-8", errors="replace")
                p = parse_xmp(text, short, pack)
                if not p.name or not (p.settings or p.masks or p.curves):
                    raise ValueError
            except Exception:
                skipped.append({"file": short, "why": "Not a Lightroom preset (.xmp) file."})
                continue
            d = p.to_dict()
            own = p.uuid or p.id
            d["id"] = "my-" + own if user["admin"] else f"my-{user['id']}-{own}"
            d["display_name"] = p.name
            d["look"], d["variant"] = p.group or "No group", p.name
            d["pack"] = pack
            d["owner"] = user["id"]
            if renderer.owner_of(d["id"]) not in (None, user["id"]):
                skipped.append({"file": short, "why": "This preset could not be added."})
                continue
            was = store.save_preset(d, text, user["id"])
            if was:
                replaced += 1
            else:
                added += 1
                mine += 1
            packs[pack] = packs.get(pack, 0) + 1
            renderer.add_uploaded(d)
        return {"added": added, "replaced": replaced, "packs": packs, "skipped": skipped[:50], "skipped_total": len(skipped)}

    return r
