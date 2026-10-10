"""Photographer accounts, sign-in sessions and credits.

Everything lives in the same SQLite file as the weddings:

    users         one row per photographer; Bharat's studio account is the admin
    sessions      browser sign-ins (only a hash of the cookie is kept)
    email_tokens  links sent by email to confirm an address or set a new password
    ledger        every credit added or spent; the balance is the sum

Pricing (Bharat, Oct 2026): exporting a photo costs 1 credit every time it is exported,
including when its edit is applied in Lightroom by the plug-in; culling costs 1 credit
per 10 photos every time it runs. A new photographer gets free credits once their email
is confirmed. The admin (studio) account is never stopped for credits; its use is still
written down.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL DEFAULT '',
    password TEXT NOT NULL DEFAULT '', verified INTEGER NOT NULL DEFAULT 0, admin INTEGER NOT NULL DEFAULT 0,
    plugin_key TEXT NOT NULL DEFAULT '', created REAL NOT NULL, last_seen REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created REAL NOT NULL, expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS email_tokens (
    token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    delta INTEGER NOT NULL, kind TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', ref TEXT NOT NULL DEFAULT '',
    photos INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_by_user ON ledger (user_id, id);
CREATE INDEX IF NOT EXISTS users_by_plugin_key ON users (plugin_key);
"""

SESSION_DAYS = 30
VERIFY_HOURS = 48
RESET_HOURS = 2
FREE_CREDITS = 50                 # given once, when the email address is confirmed
FREE_PER_IP_DAYS = 30             # free credits go to one account per internet address in this time
PHOTOS_PER_CULL_CREDIT = 10
MIN_PASSWORD = 8
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class NotEnoughCredits(Exception):
    def __init__(self, needed: int, balance: int):
        self.needed, self.balance = needed, balance
        super().__init__(f"This needs {needed} credits and the balance is {balance}. Buy credits to carry on.")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        kind, salt, digest = stored.split("$")
    except ValueError:
        return False
    if kind != "scrypt":
        return False
    got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
    return hmac.compare_digest(got.hex(), digest)


def cull_cost(photos: int) -> int:
    return math.ceil(photos / PHOTOS_PER_CULL_CREDIT) if photos > 0 else 0


def public(user: dict | None) -> dict | None:
    """A user as the app may see it: no password or key hashes."""
    if user is None:
        return None
    return {"id": user["id"], "email": user["email"], "name": user["name"], "verified": bool(user["verified"]),
            "admin": bool(user["admin"]), "has_password": bool(user["password"]), "has_plugin_key": bool(user["plugin_key"])}


class Accounts:
    def __init__(self, store, admin_email: str = ""):
        self.store = store
        with store._db() as db:
            db.executescript(SCHEMA)
            if "signup_ip" not in {r["name"] for r in db.execute("PRAGMA table_info(users)")}:
                db.execute("ALTER TABLE users ADD COLUMN signup_ip TEXT NOT NULL DEFAULT ''")
            db.execute("CREATE INDEX IF NOT EXISTS users_by_ip ON users (signup_ip)")
        self.admin_email = admin_email.strip().lower()
        self.admin_id = self._ensure_admin() if self.admin_email else ""

    def _db(self):
        return self.store._db()

    # ---- users ---------------------------------------------------------------
    def _ensure_admin(self) -> str:
        """The studio account: made on first start (no password yet; set one with "Forgot password"
        or from inside the app), and owner of everything made before there were accounts."""
        with self._db() as db:
            row = db.execute("SELECT id FROM users WHERE email=?", (self.admin_email,)).fetchone()
            if row is None:
                uid = uuid.uuid4().hex[:12]
                db.execute("INSERT INTO users (id, email, name, verified, admin, created) VALUES (?,?,?,?,?,?)",
                           (uid, self.admin_email, "Cele-Brat", 1, 1, time.time()))
            else:
                uid = row["id"]
                db.execute("UPDATE users SET admin=1, verified=1 WHERE id=?", (uid,))
        self.store.adopt_orphans(uid)
        return uid

    def user(self, user_id: str) -> dict | None:
        with self._db() as db:
            r = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(r) if r else None

    def by_email(self, email: str) -> dict | None:
        with self._db() as db:
            r = db.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
        return dict(r) if r else None

    def sign_up(self, email: str, password: str, name: str = "", ip: str = "") -> dict:
        email = email.strip().lower()
        if not EMAIL_RE.match(email) or len(email) > 200:
            raise ValueError("Please enter a valid email address.")
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"The password needs at least {MIN_PASSWORD} characters.")
        if len(password) > 200:
            raise ValueError("The password is too long.")
        if self.by_email(email):
            raise ValueError("There is already an account with this email. Log in, or use \"Forgot password\".")
        uid = uuid.uuid4().hex[:12]
        with self._db() as db:
            db.execute("INSERT INTO users (id, email, name, password, created, signup_ip) VALUES (?,?,?,?,?,?)",
                       (uid, email, name.strip()[:80], hash_password(password), time.time(), ip[:64]))
        return self.user(uid)

    def log_in(self, email: str, password: str) -> dict | None:
        u = self.by_email(email)
        if u is None or not u["password"] or not check_password(password, u["password"]):
            return None
        return u

    def set_password(self, user_id: str, password: str) -> None:
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"The password needs at least {MIN_PASSWORD} characters.")
        if len(password) > 200:
            raise ValueError("The password is too long.")
        with self._db() as db:
            db.execute("UPDATE users SET password=? WHERE id=?", (hash_password(password), user_id))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))      # signed out everywhere else

    def verify(self, user_id: str) -> bool:
        """Mark the email as confirmed. The free credits come with the first confirmation only,
        and only to the first account signed up from the same internet address within
        FREE_PER_IP_DAYS (Bharat, Oct 2026): a second account from there works but starts at 0."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            n = db.execute("UPDATE users SET verified=1 WHERE id=? AND verified=0", (user_id,)).rowcount
            if n and FREE_CREDITS and self._free_allowed(db, user_id):
                db.execute("INSERT INTO ledger (user_id, delta, kind, note, created) VALUES (?,?,?,?,?)",
                           (user_id, FREE_CREDITS, "welcome", "Free credits for a new account", time.time()))
        return bool(n)

    @staticmethod
    def _free_allowed(db, user_id: str) -> bool:
        ip = db.execute("SELECT signup_ip FROM users WHERE id=?", (user_id,)).fetchone()["signup_ip"]
        if not ip:
            return True
        since = time.time() - FREE_PER_IP_DAYS * 86400
        return db.execute("SELECT 1 FROM ledger l JOIN users u ON u.id=l.user_id WHERE l.kind='welcome' "
                          "AND u.signup_ip=? AND u.id<>? AND l.created>? LIMIT 1", (ip, user_id, since)).fetchone() is None

    def got_free(self, user_id: str) -> bool:
        with self._db() as db:
            return db.execute("SELECT 1 FROM ledger WHERE user_id=? AND kind='welcome' LIMIT 1", (user_id,)).fetchone() is not None

    # ---- sessions ------------------------------------------------------------
    def new_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE expires<?", (now,))
            db.execute("INSERT INTO sessions (token, user_id, created, expires) VALUES (?,?,?,?)",
                       (_hash_token(token), user_id, now, now + SESSION_DAYS * 86400))
        return token

    def session_user(self, token: str) -> dict | None:
        if not token:
            return None
        with self._db() as db:
            r = db.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?",
                           (_hash_token(token), time.time())).fetchone()
            if r and time.time() - r["last_seen"] > 300:
                db.execute("UPDATE users SET last_seen=? WHERE id=?", (time.time(), r["id"]))
        return dict(r) if r else None

    def end_session(self, token: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (_hash_token(token),))

    # ---- the plug-in's key ---------------------------------------------------
    def new_plugin_key(self, user_id: str) -> str:
        """A fresh key for the Lightroom plug-in. Only its hash is kept; the old key stops working."""
        key = "ae_" + secrets.token_urlsafe(30)
        with self._db() as db:
            db.execute("UPDATE users SET plugin_key=? WHERE id=?", (_hash_token(key), user_id))
        return key

    def plugin_user(self, key: str) -> dict | None:
        if not key:
            return None
        with self._db() as db:
            r = db.execute("SELECT * FROM users WHERE plugin_key=?", (_hash_token(key),)).fetchone()
        return dict(r) if r else None

    # ---- links sent by email -------------------------------------------------
    def email_token(self, user_id: str, kind: str) -> str:
        hours = VERIFY_HOURS if kind == "verify" else RESET_HOURS
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._db() as db:
            db.execute("DELETE FROM email_tokens WHERE expires<? OR (user_id=? AND kind=?)", (now, user_id, kind))
            db.execute("INSERT INTO email_tokens (token, user_id, kind, expires) VALUES (?,?,?,?)",
                       (_hash_token(token), user_id, kind, now + hours * 3600))
        return token

    def use_email_token(self, token: str, kind: str) -> str | None:
        """The user the link belongs to, once; None if it is wrong, used or too old."""
        with self._db() as db:
            r = db.execute("SELECT user_id FROM email_tokens WHERE token=? AND kind=? AND expires>?",
                           (_hash_token(token), kind, time.time())).fetchone()
            if r:
                db.execute("DELETE FROM email_tokens WHERE token=?", (_hash_token(token),))
        return r["user_id"] if r else None

    def peek_email_token(self, token: str, kind: str) -> bool:
        with self._db() as db:
            return db.execute("SELECT 1 FROM email_tokens WHERE token=? AND kind=? AND expires>?",
                              (_hash_token(token), kind, time.time())).fetchone() is not None

    # ---- credits -------------------------------------------------------------
    def balance(self, user_id: str) -> int:
        with self._db() as db:
            return int(db.execute("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE user_id=?", (user_id,)).fetchone()[0])

    def add(self, user_id: str, credits: int, kind: str, note: str = "", ref: str = "", photos: int = 0) -> None:
        with self._db() as db:
            db.execute("INSERT INTO ledger (user_id, delta, kind, note, ref, photos, created) VALUES (?,?,?,?,?,?,?)",
                       (user_id, int(credits), kind, note[:200], ref, photos, time.time()))

    def charge(self, user: dict, credits: int, kind: str, note: str, ref: str = "", photos: int = 0) -> None:
        """Take credits, or raise NotEnoughCredits and take nothing. The studio account is never stopped."""
        if credits <= 0:
            return
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")                  # two charges at once cannot both spend the same credits
            have = int(db.execute("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE user_id=?", (user["id"],)).fetchone()[0])
            if have < credits and not user["admin"]:
                raise NotEnoughCredits(credits, have)
            db.execute("INSERT INTO ledger (user_id, delta, kind, note, ref, photos, created) VALUES (?,?,?,?,?,?,?)",
                       (user["id"], -int(credits), kind, note[:200], ref, photos, time.time()))

    def must_have(self, user: dict, credits: int) -> None:
        if user["admin"] or credits <= 0:
            return
        have = self.balance(user["id"])
        if have < credits:
            raise NotEnoughCredits(credits, have)

    def history(self, user_id: str, limit: int = 100) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT delta, kind, note, photos, created FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit))]
