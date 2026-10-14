"""Locking down the studio (admin) account (Bharat, Oct 2026).

Two-step login: a 6-digit code from an authenticator app (Google / Microsoft Authenticator),
standard TOTP (RFC 6238: 30 seconds, SHA-1, 6 digits), plus 10 one-time backup codes for a lost
phone. Asked at every new password login; the admin dashboard and its actions also need a
code checked in the last ADMIN_FRESH_HOURS on this sign-in.

The old shared studio key (AUTOEDITS_KEY) retires itself for good once the studio account has
a password, two-step login and its own Lightroom key.

Admin activity log: every "View as", every download of a photographer's photos, credit changes,
backups run by hand and security changes, shown on /admin (admin only).

Lost phone and backup codes? On the VPS:
    docker compose exec server python -m server.security --reset-two-step
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time

STEP = 30
DIGITS = 6
ADMIN_FRESH_HOURS = 12
ADMIN_MIN_PASSWORD = 12
BACKUP_CODES = 10
ISSUER = "Auto Edits"

SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, admin_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL, target TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '', ip TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS admin_log_by_time ON admin_log (id);
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


# ---- authenticator codes ------------------------------------------------------------
def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    n = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 10 ** DIGITS
    return str(n).zfill(DIGITS)


def code_now(secret: str, at: float | None = None) -> str:
    return _code(secret, int((at or time.time()) // STEP))


def check_code(secret: str, code: str, last_step: int = 0, at: float | None = None) -> int:
    """The time step the code belongs to (one step either side allowed for clock drift), or 0.
    A step at or before last_step was already used and is refused, so a code works only once."""
    code = "".join(c for c in str(code) if c.isdigit())
    if len(code) != DIGITS or not secret:
        return 0
    now = int((at or time.time()) // STEP)
    for step in (now - 1, now, now + 1):
        if step > last_step and hmac.compare_digest(_code(secret, step), code):
            return step
    return 0


def setup_link(secret: str, email: str) -> str:
    from urllib.parse import quote
    return (f"otpauth://totp/{quote(ISSUER)}:{quote(email)}?secret={secret}&issuer={quote(ISSUER)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def qr_svg(text: str) -> str:
    import segno
    import io
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=5, border=2, dark="#111", light="#fff", xmldecl=False)
    return buf.getvalue().decode()


# ---- backup codes -------------------------------------------------------------------
def _hash(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").replace(" ", "").lower().encode()).hexdigest()


def new_backup_codes() -> tuple[list[str], str]:
    """Ten codes to show once, and what to keep (their hashes, as JSON)."""
    codes = []
    for _ in range(BACKUP_CODES):
        raw = secrets.token_hex(5)
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes, json.dumps([_hash(c) for c in codes])


def use_backup_code(stored: str, code: str) -> str | None:
    """What to keep after using one code (it is crossed off), or None when it is not one of them."""
    try:
        hashes = json.loads(stored or "[]")
    except ValueError:
        return None
    h = _hash(code)
    if h not in hashes:
        return None
    hashes.remove(h)
    return json.dumps(hashes)


def backup_codes_left(stored: str) -> int:
    try:
        return len(json.loads(stored or "[]"))
    except ValueError:
        return 0


# ---- strong password ------------------------------------------------------------------
COMMON = {"password", "passw0rd", "12345678", "123456789", "1234567890", "qwerty123", "iloveyou",
          "celebrat", "cele-brat", "autoedits", "admin123", "welcome1"}


def admin_password_problem(password: str, email: str) -> str | None:
    if len(password) < ADMIN_MIN_PASSWORD:
        return f"The studio account needs a password of at least {ADMIN_MIN_PASSWORD} characters."
    low = password.lower()
    if low in COMMON or any(w in low for w in ("password", "123456", "qwerty")):
        return "That password is too easy to guess. Try a few unrelated words together."
    if email and email.split("@")[0].lower() in low:
        return "The password should not contain your email name."
    if len(set(password)) < 6:
        return "That password repeats too few different characters."
    return None


def main(argv=None) -> int:
    """Emergency: turn two-step login off for the studio account (lost phone and backup codes)."""
    import argparse
    import os
    from . import blobs as blob_store
    from .accounts import Accounts
    from .store import Store
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reset-two-step", action="store_true", help="turn two-step login off for the studio account")
    a = ap.parse_args(argv)
    if not a.reset_two_step:
        ap.print_help()
        return 0
    store = Store(os.environ.get("AUTOEDITS_DATA", "data/server"), blob_store.NoBlobs())
    accounts = Accounts(store, os.environ.get("AUTOEDITS_ADMIN_EMAIL", ""))
    if not accounts.admin_id:
        print("AUTOEDITS_ADMIN_EMAIL is not set.")
        return 1
    accounts.two_step_off(accounts.admin_id)
    accounts.log(accounts.admin_id, "two-step reset", detail="from the server command line")
    print("Two-step login is off for the studio account. Sign in with your password and turn it on again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
