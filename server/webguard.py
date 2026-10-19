"""Browser safety for every response: security headers, a content policy, and a check that
stops other websites from sending forms in a signed-in photographer's name.

Kept out of main.py so it is one line there (app.add_middleware). Pure ASGI, so streamed
zips pass straight through.

The content policy lists what the pages really load:
  - our own scripts and styles, plus the few inline <script> blocks in our own pages (allowed by hash,
    worked out from the files when the server starts, so editing them needs no change here)
  - Google Fonts (styles from fonts.googleapis.com, files from fonts.gstatic.com)
  - Razorpay Checkout (its script, its windows and its calls, all on *.razorpay.com)
  - images from here, data: and blob: (the two-step QR code is inline SVG)
Inline style attributes stay allowed: the home page uses a few.

The form check: a POST that carries our session cookie (and not the Lightroom plug-in's key) must
also carry X-Requested-With. Another website can make a browser send a form or a "simple" fetch
with the cookie, but it cannot add that header without our permission (CORS), so such requests
are refused. JSON requests are covered too, as a second net. The plug-in signs in with the
X-Auto-Edits-Key header and never has the cookie; Razorpay's webhook has no cookie either;
email links are GETs. All of those pass untouched.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

COOKIE = b"autoedits="
CSRF_HEADER = b"x-requested-with"
KEY_HEADER = b"x-auto-edits-key"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
NO_CHECK = ("/api/razorpay/webhook",)

_INLINE = re.compile(r"<script(?![^>]*\bsrc=)(?![^>]*application/ld\+json)[^>]*>(.*?)</script>", re.S | re.I)


def _sha(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'"


def inline_hashes(static: Path) -> list[str]:
    """The hash of every inline script in our own HTML pages."""
    out = []
    for page in sorted(static.glob("*.html")):
        for body in _INLINE.findall(page.read_text(encoding="utf-8")):
            out.append(_sha(body))
    return out


def policy(static: Path) -> str:
    scripts = " ".join(inline_hashes(static))
    rzp = "https://*.razorpay.com"
    return "; ".join([
        "default-src 'self'",
        # 'unsafe-hashes' with the hash of print(): the "Print or save as PDF" button on invoices
        f"script-src 'self' {scripts} 'unsafe-hashes' {_sha('print()')} {rzp}",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com data:",
        f"img-src 'self' data: blob: {rzp}",
        f"connect-src 'self' {rzp}",
        f"frame-src {rzp}",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


class Guard:
    def __init__(self, app, static: Path):
        self.app = app
        self.headers = [
            (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
            (b"x-content-type-options", b"nosniff"),
            (b"x-frame-options", b"DENY"),
            (b"referrer-policy", b"strict-origin-when-cross-origin"),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(self \"https://checkout.razorpay.com\")"),
            (b"content-security-policy", policy(static).encode()),
        ]

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["method"] not in SAFE_METHODS and not scope["path"].startswith(NO_CHECK) and self.forged(scope):
            return await self.refuse(scope, send)

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                have = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [(k, v) for k, v in self.headers if k not in have]
            await send(message)

        await self.app(scope, receive, send_with_headers)

    @staticmethod
    def forged(scope) -> bool:
        """A cookie-signed change without our own header: possibly sent by another website."""
        headers = dict((k.lower(), v) for k, v in scope.get("headers", []))
        if headers.get(KEY_HEADER):                     # the Lightroom plug-in: no cookie involved
            return False
        if COOKIE not in headers.get(b"cookie", b""):   # nothing to misuse (log in, sign up, forgot password)
            return False
        return not headers.get(CSRF_HEADER, b"").strip()

    async def refuse(self, scope, send):
        body = json.dumps({"detail": "This request was refused for your safety. Reload the page and try again."}).encode()
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())] + self.headers})
        await send({"type": "http.response.body", "body": body})
