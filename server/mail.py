"""Emails to photographers: confirm the address, set a new password.

Sent through Bharat's Google Workspace (Gmail SMTP) with these in deploy/.env:

    SMTP_HOST=smtp.gmail.com   SMTP_PORT=587
    SMTP_USER=<workspace account>   SMTP_PASSWORD=<app password>
    MAIL_FROM=Auto Edits <no-reply@cele-brat.solutions>     (an alias of that account)

Without SMTP_USER nothing is sent: the message is written to the server log instead, so
the app still works on a test machine.

Hub rules (Oct 2026): every email ends with "Powered by Cele-Brat.Tech | info@cele-brat.tech", and every
email to a photographer is BCC'd to the studio's Gmail (BCC_TO, or AUTOEDITS_BCC in deploy/.env; empty
turns it off). Emails to the studio itself (health reports, AUTOEDITS_ADMIN_EMAIL) are never BCC'd.
"""
from __future__ import annotations

import os
import smtplib
import threading
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

SENT: list[EmailMessage] = []          # kept in memory too, for the tests
POWERED_BY = "Powered by Cele-Brat.Tech | info@cele-brat.tech"
BCC_TO = "celebratweddings@gmail.com"


class Mailer:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.host = env.get("SMTP_HOST", "smtp.gmail.com")
        self.port = int(env.get("SMTP_PORT", "587") or 587)
        self.user = env.get("SMTP_USER", "")
        self.password = env.get("SMTP_PASSWORD", "")
        self.sender = env.get("MAIL_FROM", "") or (formataddr(("Auto Edits", self.user)) if self.user else "Auto Edits <no-reply@localhost>")
        self.bcc = env.get("AUTOEDITS_BCC", BCC_TO).strip()
        self.studio = (env.get("AUTOEDITS_ADMIN_EMAIL", "") or "").strip().lower()

    @property
    def on(self) -> bool:
        return bool(self.user and self.password)

    def send(self, to: str, subject: str, text: str, html: str = "", wait: bool = False,
             attachments: list[tuple[str, bytes, str]] | None = None, bcc: bool | None = None) -> None:
        """attachments: (file name, bytes, mime type like "application/pdf"). bcc: None means the rule
        (photographers yes, the studio's own address no)."""
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        name, addr = parseaddr(self.sender)
        msg["Reply-To"] = addr
        if bcc is None:
            bcc = bool(self.bcc) and parseaddr(to)[1].lower() not in (self.studio, self.bcc.lower())
        if bcc and self.bcc:
            msg["Bcc"] = self.bcc
        if POWERED_BY not in text:
            text = text.rstrip() + "\n\n" + POWERED_BY
        msg.set_content(text)
        if html:
            if POWERED_BY not in html:
                html = html.replace("</div>", f'<p style="color:#8a8378;font-size:12px;margin-top:18px">{POWERED_BY}</p></div>', 1) \
                    if "</div>" in html else html + f'<p style="color:#8a8378;font-size:12px">{POWERED_BY}</p>'
            msg.add_alternative(html, subtype="html")
        for filename, data, mime in attachments or []:
            maintype, _, subtype = (mime or "application/octet-stream").partition("/")
            msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=filename)
        SENT.append(msg)
        del SENT[:-50]
        if not self.on:
            print(f"[mail not set up] to {to}: {subject}\n{text}", flush=True)
            return
        t = threading.Thread(target=self._deliver, args=(msg,), daemon=True)
        t.start()
        if wait:
            t.join(30)

    def _deliver(self, msg: EmailMessage) -> None:
        try:
            with smtplib.SMTP(self.host, self.port, timeout=20) as s:
                s.starttls()
                s.login(self.user, self.password)
                s.send_message(msg)
        except Exception as e:                       # a failed email must not break the request
            print(f"[mail failed] to {msg['To']}: {type(e).__name__}: {e}", flush=True)


def _page(title: str, line: str, button: str, link: str, after: str) -> tuple[str, str]:
    text = f"{title}\n\n{line}\n\n{link}\n\n{after}\n\nAuto Edits by Cele-Brat\n{POWERED_BY}"
    html = f"""<div style="font-family:Georgia,serif;max-width:520px;margin:auto;padding:24px;color:#1d1a16">
<h2 style="font-weight:normal">{title}</h2><p>{line}</p>
<p style="margin:28px 0"><a href="{link}" style="background:#1f5f5b;color:#fff;padding:12px 22px;border-radius:6px;text-decoration:none">{button}</a></p>
<p style="color:#6b645a;font-size:14px">{after}</p><p style="color:#6b645a;font-size:13px">Auto Edits by Cele-Brat</p>
<p style="color:#8a8378;font-size:12px">{POWERED_BY}</p></div>"""
    return text, html


def verify_email(base: str, token: str) -> tuple[str, str, str]:
    text, html = _page("Confirm your email", "Welcome to Auto Edits. Confirm this address to start culling and editing; "
                       "your free credits are added when you do.", "Confirm email", f"{base}/verify/{token}",
                       "The link works for 48 hours. If you did not sign up, ignore this email.")
    return "Confirm your Auto Edits email", text, html


def signup_again(base: str) -> tuple[str, str, str]:
    """Someone signed up with an address that already has an account. The sign-up page says the same
    as for a new account, so this note is the only place the account is mentioned."""
    text, html = _page("You already have an account", "Someone just tried to sign up for Auto Edits with this email "
                       "address, which already has an account. If it was you, log in, or use \"Forgot password\" "
                       "on the log-in window to set a new password.", "Log in", f"{base}/?login=1",
                       "If it was not you, ignore this email: nothing has changed and your account is safe.")
    return "You already have an Auto Edits account", text, html


def reset_password(base: str, token: str) -> tuple[str, str, str]:
    text, html = _page("Set a new password", "Someone asked to set a new password for your Auto Edits account.",
                       "Set a new password", f"{base}/reset/{token}",
                       "The link works for 2 hours. If this was not you, ignore this email; your password stays as it is.")
    return "Set a new Auto Edits password", text, html


def confirm_reminder(base: str, token: str, last: bool) -> tuple[str, str, str]:
    """Every 3 days while the email is not confirmed (server/nudges.py)."""
    line = ("Your Auto Edits account is waiting. Confirm your email and 50 free credits are added straight away: "
            "enough to export 50 edited photos or cull 500. Free credits can be used for 15 days.")
    after = ("This is the last reminder we will send. " if last else "") + \
        "The link works for 48 hours. If you did not sign up, ignore this email."
    text, html = _page("Confirm your email to start", line, "Confirm email", f"{base}/verify/{token}", after)
    return ("Last reminder: confirm your Auto Edits email" if last else "Confirm your email to get 50 free credits"), text, html


def use_reminder(base: str, left: int, ends: str, last: bool) -> tuple[str, str, str]:
    """Every 3 days while free credits are unused (server/nudges.py)."""
    line = (f"You have {left} free credits in Auto Edits. They end on {ends}. "
            "Add one wedding folder, pick a preset and export: each edited photo uses 1 credit, culling uses 1 for every 10 photos.")
    after = ("Your free credits end tomorrow. " if last else "") + "Questions? Reply to this email or write to support@cele-brat.com."
    text, html = _page("Your free credits are waiting", line, "Open Auto Edits", f"{base}/", after)
    return (f"Your {left} free credits end tomorrow" if last else f"Your {left} free Auto Edits credits end on {ends}"), text, html


# ---- billing (server/billing.py) ------------------------------------------------------------
def invoice_mail(base: str, o: dict, what: str, total: str) -> tuple[str, str, str]:
    """The invoice goes along as a file; the link opens it in the app."""
    text, html = _page("Thank you for your purchase", f"Your payment of {total} for {what} went through. "
                       f"Invoice {o['invoice_no']} is attached and is in your account window.",
                       "Open the invoice", f"{base}/invoice/{o['id']}",
                       "Credits and storage are delivered straight away. Questions? Reply to this email.")
    return f"Auto Edits invoice {o['invoice_no']}: {what}", text, html


def credit_note_mail(base: str, r: dict, amount: str) -> tuple[str, str, str]:
    taken = f" {r['credits']:,} credits were taken back from your balance." if r.get("credits") else ""
    text, html = _page("Your refund is on its way", f"We have refunded {amount} of invoice {r['invoice_no']} through Razorpay; "
                       f"it reaches your original payment method in 5 to 7 working days. Credit note {r['note_no']} is attached.{taken}",
                       "Open the credit note", f"{base}/credit-note/{r['id']}",
                       "Questions? Reply to this email.")
    return f"Auto Edits credit note {r['note_no']}", text, html


def storage_ending(base: str, gb: int, ends: str, renews: bool, amount: str = "") -> tuple[str, str, str]:
    """7 days before a prepaid storage grant ends, or 3 days before an auto-renew charge (server/nudges.py)."""
    if renews:
        line = (f"Your {gb} GB of extra Auto Edits storage renews on {ends}: {amount} (incl. GST) will be charged to the "
                "same payment method. Nothing to do if you want to keep it. To stop it, cancel auto-renew in your account "
                "window before then; the storage stays until the end of the month already paid.")
        subject = f"Your {gb} GB extra storage renews on {ends}"
    else:
        line = (f"Your {gb} GB of extra Auto Edits storage ends on {ends}. If your photos and exports then take more room "
                "than your plan allows, new uploads stop until you make space or add storage again. Add storage from the "
                "account window, or turn on auto-renew so it never lapses.")
        subject = f"Your {gb} GB extra storage ends on {ends}"
    text, html = _page("About your extra storage", line, "Open your account", f"{base}/?account=1",
                       "Questions? Reply to this email.")
    return subject, text, html


def low_credits(base: str, balance: int, line_price: str) -> tuple[str, str, str]:
    """Once in 7 days when the balance falls low after a charge (server/nudges.py)."""
    line = (f"You have {balance:,} credit{'s' if balance != 1 else ''} left in Auto Edits. Each exported photo, or edit sent "
            f"to Lightroom, uses 1 credit; culling uses 1 for every 10 photos. {line_price}")
    text, html = _page("Running low on credits", line, "Buy credits", f"{base}/?account=1",
                       "Buy before the next wedding so an export never stops halfway. Questions? Reply to this email.")
    return f"Only {balance:,} Auto Edits credits left", text, html
