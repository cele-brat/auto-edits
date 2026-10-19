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

from . import security

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


class AlreadyThere(ValueError):
    """Sign-up with an email that already has an account (never shown as such: see /api/signup)."""


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
            "admin": bool(user["admin"]), "has_password": bool(user["password"]), "has_plugin_key": bool(user["plugin_key"]),
            "two_step": bool(user.get("totp_on"))}


class Accounts:
    def __init__(self, store, admin_email: str = ""):
        self.store = store
        with store._db() as db:
            db.executescript(SCHEMA)
            if "signup_ip" not in {r["name"] for r in db.execute("PRAGMA table_info(users)")}:
                db.execute("ALTER TABLE users ADD COLUMN signup_ip TEXT NOT NULL DEFAULT ''")
            db.execute("CREATE INDEX IF NOT EXISTS users_by_ip ON users (signup_ip)")
            cols = {r["name"] for r in db.execute("PRAGMA table_info(users)")}
            for col, kind in (("totp_secret", "TEXT NOT NULL DEFAULT ''"), ("totp_pending", "TEXT NOT NULL DEFAULT ''"),
                              ("totp_on", "INTEGER NOT NULL DEFAULT 0"), ("totp_last", "INTEGER NOT NULL DEFAULT 0"),
                              ("backup_codes", "TEXT NOT NULL DEFAULT ''"),
                              ("agreed", "REAL NOT NULL DEFAULT 0"), ("terms_version", "TEXT NOT NULL DEFAULT ''"),
                              ("limit_photos", "INTEGER NOT NULL DEFAULT 0"), ("limit_gb", "INTEGER NOT NULL DEFAULT 0")):
                if col not in cols:              # two-step login (see security.py)
                    db.execute(f"ALTER TABLE users ADD COLUMN {col} {kind}")
            if "checked" not in {r["name"] for r in db.execute("PRAGMA table_info(sessions)")}:
                db.execute("ALTER TABLE sessions ADD COLUMN checked REAL NOT NULL DEFAULT 0")   # when a 2-step code was last given
            db.executescript(security.SCHEMA)
            if "wedding" not in {r["name"] for r in db.execute("PRAGMA table_info(ledger)")}:
                db.execute("ALTER TABLE ledger ADD COLUMN wedding TEXT NOT NULL DEFAULT ''")   # which wedding a charge was for
                db.execute("UPDATE ledger SET wedding=ref WHERE kind IN ('cull','lightroom')")
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='exports'").fetchone():
                    db.execute("UPDATE ledger SET wedding=COALESCE((SELECT e.wedding_id FROM exports e WHERE e.id=ledger.ref), '') "
                               "WHERE kind IN ('export','refund')")
                # exports removed since: the note ends with ": <wedding name>", unique for each photographer
                db.execute("UPDATE ledger SET wedding=COALESCE((SELECT w.id FROM weddings w WHERE w.owner=ledger.user_id "
                           "AND substr(ledger.note, -length(w.name) - 2) = ': ' || w.name LIMIT 1), '') "
                           "WHERE wedding='' AND kind IN ('export','refund','cull','lightroom')")
            # the ledger is a record: rows are only ever added, never changed or removed
            db.execute("CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger "
                       "BEGIN SELECT RAISE(ABORT, 'the credit ledger cannot be changed'); END")
            db.execute("CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger "
                       "BEGIN SELECT RAISE(ABORT, 'the credit ledger cannot be changed'); END")
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

    def sign_up(self, email: str, password: str, name: str = "", ip: str = "", terms: str = "") -> dict:
        email = email.strip().lower()
        if not EMAIL_RE.match(email) or len(email) > 200:
            raise ValueError("Please enter a valid email address.")
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"The password needs at least {MIN_PASSWORD} characters.")
        if len(password) > 200:
            raise ValueError("The password is too long.")
        if self.by_email(email):
            raise AlreadyThere("There is already an account with this email. Log in, or use \"Forgot password\".")
        uid = uuid.uuid4().hex[:12]
        with self._db() as db:
            now = time.time()
            db.execute("INSERT INTO users (id, email, name, password, created, signup_ip, agreed, terms_version) "
                       "VALUES (?,?,?,?,?,?,?,?)", (uid, email, name.strip()[:80], hash_password(password), now, ip[:64],
                                                   now if terms else 0, terms))
        return self.user(uid)

    def log_in(self, email: str, password: str) -> dict | None:
        u = self.by_email(email)
        if u is None or not u["password"] or not check_password(password, u["password"]):
            return None
        return u

    def set_password(self, user_id: str, password: str) -> None:
        u = self.user(user_id)
        if u and u["admin"]:
            problem = security.admin_password_problem(password, u["email"])
            if problem:
                raise ValueError(problem)
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
    def new_session(self, user_id: str, checked: bool = False) -> str:
        """checked: a two-step code was given for this sign-in just now."""
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE expires<?", (now,))
            db.execute("INSERT INTO sessions (token, user_id, created, expires, checked) VALUES (?,?,?,?,?)",
                       (_hash_token(token), user_id, now, now + SESSION_DAYS * 86400, now if checked else 0))
        return token

    def mark_checked(self, token: str) -> None:
        with self._db() as db:
            db.execute("UPDATE sessions SET checked=? WHERE token=?", (time.time(), _hash_token(token)))

    def session_user(self, token: str) -> dict | None:
        if not token:
            return None
        with self._db() as db:
            r = db.execute("SELECT u.*, s.checked AS session_checked FROM sessions s JOIN users u ON u.id=s.user_id "
                           "WHERE s.token=? AND s.expires>?",
                           (_hash_token(token), time.time())).fetchone()
            if r and time.time() - r["last_seen"] > 300:
                db.execute("UPDATE users SET last_seen=? WHERE id=?", (time.time(), r["id"]))
        return dict(r) if r else None

    def end_session(self, token: str) -> None:
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (_hash_token(token),))

    # ---- two-step login ---------------------------------------------------------
    def two_step_start(self, user_id: str) -> str:
        """A new secret for the authenticator app. Not in use until a code from it is confirmed."""
        secret = security.new_secret()
        with self._db() as db:
            db.execute("UPDATE users SET totp_pending=? WHERE id=?", (secret, user_id))
        return secret

    def two_step_confirm(self, user_id: str, code: str) -> list[str] | None:
        """Turn two-step login on once the app shows the right code. Returns the backup codes (shown once)."""
        u = self.user(user_id)
        step = security.check_code(u["totp_pending"], code) if u and u["totp_pending"] else 0
        if not step:
            return None
        codes, stored = security.new_backup_codes()
        with self._db() as db:
            db.execute("UPDATE users SET totp_secret=totp_pending, totp_pending='', totp_on=1, totp_last=?, backup_codes=? "
                       "WHERE id=?", (step, stored, user_id))
        return codes

    def two_step_off(self, user_id: str) -> None:
        with self._db() as db:
            db.execute("UPDATE users SET totp_secret='', totp_pending='', totp_on=0, totp_last=0, backup_codes='' WHERE id=?",
                       (user_id,))

    def check_second_step(self, user_id: str, code: str) -> str | None:
        """'app' or 'backup' when the code is right (each works once), else None."""
        u = self.user(user_id)
        if not u or not u["totp_on"]:
            return None
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            r = db.execute("SELECT totp_secret, totp_last, backup_codes FROM users WHERE id=?", (user_id,)).fetchone()
            step = security.check_code(r["totp_secret"], code, r["totp_last"])
            if step:
                db.execute("UPDATE users SET totp_last=? WHERE id=?", (step, user_id))
                return "app"
            left = security.use_backup_code(r["backup_codes"], code)
            if left is not None:
                db.execute("UPDATE users SET backup_codes=? WHERE id=?", (left, user_id))
                return "backup"
        return None

    def new_backup_codes(self, user_id: str) -> list[str]:
        codes, stored = security.new_backup_codes()
        with self._db() as db:
            db.execute("UPDATE users SET backup_codes=? WHERE id=?", (stored, user_id))
        return codes

    # ---- the old shared studio key ------------------------------------------------
    def setting(self, k: str) -> str:
        with self._db() as db:
            r = db.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
        return r["v"] if r else ""

    def studio_key_retired(self) -> bool:
        return bool(self.setting("studio_key_retired"))

    def studio_setup(self) -> dict:
        """What the studio account still needs before the old shared key retires."""
        u = self.user(self.admin_id) if self.admin_id else None
        return {"password": bool(u and u["password"]), "two_step": bool(u and u["totp_on"]),
                "lightroom_key": bool(u and u["plugin_key"]), "studio_key_retired": self.studio_key_retired()}

    def retire_studio_key_if_ready(self, ip: str = "") -> bool:
        """For good, once the studio account has a password, two-step login and its own Lightroom key."""
        if self.studio_key_retired():
            return True
        s = self.studio_setup()
        if not (s["password"] and s["two_step"] and s["lightroom_key"]):
            return False
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO settings (k, v) VALUES ('studio_key_retired', ?)", (str(time.time()),))
        self.log(self.admin_id, "old studio key retired", detail="password, two-step and own Lightroom key are set", ip=ip)
        return True

    def set_limits(self, user_id: str, photos: int, gb: int) -> None:
        """0 means the default (limits.py)."""
        with self._db() as db:
            db.execute("UPDATE users SET limit_photos=?, limit_gb=? WHERE id=?", (max(0, int(photos)), max(0, int(gb)), user_id))

    # ---- admin activity log ------------------------------------------------------
    def log(self, admin_id: str, action: str, target: str = "", detail: str = "", ip: str = "") -> None:
        with self._db() as db:
            db.execute("INSERT INTO admin_log (at, admin_id, action, target, detail, ip) VALUES (?,?,?,?,?,?)",
                       (time.time(), admin_id, action, target, detail[:300], ip[:64]))

    def activity(self, limit: int = 200) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT l.at, l.action, l.detail, l.ip, COALESCE(u.email, l.target) AS target FROM admin_log l "
                "LEFT JOIN users u ON u.id=l.target ORDER BY l.id DESC LIMIT ?", (limit,))]

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

    def peek_email_token(self, token: str, kind: str) -> str | None:
        """The user the link belongs to, without using it up; None if it is wrong or too old."""
        with self._db() as db:
            r = db.execute("SELECT user_id FROM email_tokens WHERE token=? AND kind=? AND expires>?",
                           (_hash_token(token), kind, time.time())).fetchone()
        return r["user_id"] if r else None

    # ---- credits -------------------------------------------------------------
    def balance(self, user_id: str) -> int:
        with self._db() as db:
            return int(db.execute("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE user_id=?", (user_id,)).fetchone()[0])

    def add(self, user_id: str, credits: int, kind: str, note: str = "", ref: str = "", photos: int = 0,
            wedding: str = "") -> None:
        with self._db() as db:
            db.execute("INSERT INTO ledger (user_id, delta, kind, note, ref, photos, created, wedding) VALUES (?,?,?,?,?,?,?,?)",
                       (user_id, int(credits), kind, note[:200], ref, photos, time.time(), wedding))

    def charge(self, user: dict, credits: int, kind: str, note: str, ref: str = "", photos: int = 0,
               wedding: str = "") -> None:
        """Take credits, or raise NotEnoughCredits and take nothing. The studio account is never stopped."""
        if credits <= 0:
            return
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")                  # two charges at once cannot both spend the same credits
            have = int(db.execute("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE user_id=?", (user["id"],)).fetchone()[0])
            if have < credits and not user["admin"]:
                raise NotEnoughCredits(credits, have)
            db.execute("INSERT INTO ledger (user_id, delta, kind, note, ref, photos, created, wedding) VALUES (?,?,?,?,?,?,?,?)",
                       (user["id"], -int(credits), kind, note[:200], ref, photos, time.time(), wedding))

    def must_have(self, user: dict, credits: int) -> None:
        if user["admin"] or credits <= 0:
            return
        have = self.balance(user["id"])
        if have < credits:
            raise NotEnoughCredits(credits, have)

    def history(self, user_id: str, limit: int | None = 100) -> list[dict]:
        """Newest first, each with the balance straight after it and the wedding it was for."""
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM (SELECT l.id, l.delta, l.kind, l.note, l.photos, l.created, l.ref, l.wedding, "
                "COALESCE(w.name, '') AS wedding_name, SUM(l.delta) OVER (ORDER BY l.id) AS balance "
                "FROM ledger l LEFT JOIN weddings w ON w.id=l.wedding WHERE l.user_id=?) ORDER BY id DESC LIMIT ?",
                (user_id, -1 if limit is None else limit))]

    # ---- for the admin dashboard ---------------------------------------------
    def overview(self) -> list[dict]:
        """Every account with its numbers, newest first."""
        with self._db() as db:
            rows = db.execute("""
                SELECT u.id, u.email, u.name, u.verified, u.admin, u.created, u.last_seen, u.signup_ip,
                  COALESCE((SELECT SUM(delta) FROM ledger l WHERE l.user_id=u.id), 0) AS balance,
                  COALESCE((SELECT -SUM(delta) FROM ledger l WHERE l.user_id=u.id AND delta<0 AND kind<>'expired'), 0)
                    - COALESCE((SELECT SUM(delta) FROM ledger l WHERE l.user_id=u.id AND kind='refund'), 0) AS used,
                  COALESCE((SELECT SUM(delta) FROM ledger l WHERE l.user_id=u.id AND kind='purchase'), 0) AS bought,
                  (SELECT COUNT(*) FROM weddings w WHERE w.owner=u.id) AS weddings,
                  (SELECT COUNT(*) FROM photos p JOIN weddings w ON w.id=p.wedding_id WHERE w.owner=u.id) AS photos,
                  (SELECT COUNT(*) FROM users o WHERE o.signup_ip=u.signup_ip AND o.signup_ip<>'' ) AS same_ip
                FROM users u ORDER BY u.admin DESC, u.created DESC""").fetchall()
        return [{**dict(r), "verified": bool(r["verified"]), "admin": bool(r["admin"])} for r in rows]
