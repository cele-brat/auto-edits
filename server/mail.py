"""Emails to photographers: confirm the address, set a new password.

Sent through Bharat's Google Workspace (Gmail SMTP) with these in deploy/.env:

    SMTP_HOST=smtp.gmail.com   SMTP_PORT=587
    SMTP_USER=<workspace account>   SMTP_PASSWORD=<app password>
    MAIL_FROM=Auto Edits <no-reply@cele-brat.solutions>     (an alias of that account)

Without SMTP_USER nothing is sent: the message is written to the server log instead, so
the app still works on a test machine.
"""
from __future__ import annotations

import os
import smtplib
import threading
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

SENT: list[EmailMessage] = []          # kept in memory too, for the tests


class Mailer:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.host = env.get("SMTP_HOST", "smtp.gmail.com")
        self.port = int(env.get("SMTP_PORT", "587") or 587)
        self.user = env.get("SMTP_USER", "")
        self.password = env.get("SMTP_PASSWORD", "")
        self.sender = env.get("MAIL_FROM", "") or (formataddr(("Auto Edits", self.user)) if self.user else "Auto Edits <no-reply@localhost>")

    @property
    def on(self) -> bool:
        return bool(self.user and self.password)

    def send(self, to: str, subject: str, text: str, html: str = "", wait: bool = False) -> None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        name, addr = parseaddr(self.sender)
        msg["Reply-To"] = addr
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
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
    text = f"{title}\n\n{line}\n\n{link}\n\n{after}\n\nAuto Edits by Cele-Brat"
    html = f"""<div style="font-family:Georgia,serif;max-width:520px;margin:auto;padding:24px;color:#1d1a16">
<h2 style="font-weight:normal">{title}</h2><p>{line}</p>
<p style="margin:28px 0"><a href="{link}" style="background:#1f5f5b;color:#fff;padding:12px 22px;border-radius:6px;text-decoration:none">{button}</a></p>
<p style="color:#6b645a;font-size:14px">{after}</p><p style="color:#6b645a;font-size:13px">Auto Edits by Cele-Brat</p></div>"""
    return text, html


def verify_email(base: str, token: str) -> tuple[str, str, str]:
    text, html = _page("Confirm your email", "Welcome to Auto Edits. Confirm this address to start culling and editing; "
                       "your free credits are added when you do.", "Confirm email", f"{base}/verify/{token}",
                       "The link works for 48 hours. If you did not sign up, ignore this email.")
    return "Confirm your Auto Edits email", text, html


def reset_password(base: str, token: str) -> tuple[str, str, str]:
    text, html = _page("Set a new password", "Someone asked to set a new password for your Auto Edits account.",
                       "Set a new password", f"{base}/reset/{token}",
                       "The link works for 2 hours. If this was not you, ignore this email; your password stays as it is.")
    return "Set a new Auto Edits password", text, html
