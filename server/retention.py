"""Storage retention (Bharat, Oct 2026): a wedding nobody has touched for KEEP_DAYS (180) days has its
uploaded previews and exports removed. The wedding row stays, marked archived, with its name, how many
photos it had, and every ledger row that points to it.

"Touched" is any upload, edit (a look saved), export or view of its photos: store.touch keeps
weddings.last_used. The Terms promise at least 14 days' notice by email, so:

    day 166   first warning  ("will be removed on <date>, open it or click Keep to keep it 180 more days")
    day 179   second warning (one day before)
    day 180   previews and exports removed from storage and this disk; the row marked archived

Each email is written down in the `nudges` table (kind "retain", step = KEEP_DAYS - days before), so it
goes once per wedding per cycle; opening the wedding resets the cycle. The studio (admin) account is
exempt. The worker calls run_if_due every few minutes; it does real work once an hour.

The Account window lists every wedding with its "expires on" date and a "Keep for 180 more days"
button (POST /api/weddings/{id}/keep), which only touches last_used.
"""
from __future__ import annotations

import datetime as dt
import time

from fastapi import APIRouter, Depends, HTTPException

KEEP_DAYS = 180
WARN_DAYS = (14, 1)                      # days before removal on which an email goes
DAY = 86400
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

SCHEMA = """
CREATE TABLE IF NOT EXISTS nudges (
    user_id TEXT NOT NULL, kind TEXT NOT NULL, step INTEGER NOT NULL, sent REAL NOT NULL,
    PRIMARY KEY (user_id, kind, step)
);
"""


def day_of(t: float) -> str:
    return dt.datetime.fromtimestamp(t, IST).strftime("%d %B %Y").lstrip("0")


def expires_at(last_used: float) -> float:
    return float(last_used) + KEEP_DAYS * DAY


def warning_email(base: str, wedding: str, photos: int, ends: float, last: bool) -> tuple[str, str, str]:
    """Subject, text and HTML of the warning (mail._page gives the house style)."""
    from .mail import _page
    when = day_of(ends)
    line = (f"The wedding “{wedding}” ({photos:,} photos) in Auto Edits has not been opened, edited or exported "
            f"for a while. Its uploaded photos and exports will be removed on {when}. The wedding's name and its credit "
            "history stay; the photos on your own computer are never touched.")
    after = ("This is the last reminder: the photos go tomorrow. " if last else "") + \
        "To keep them, open the wedding in Auto Edits, or click “Keep for 180 more days” next to it in the Account window."
    text, html = _page("Photos will be removed soon" if not last else "Photos will be removed tomorrow", line,
                       "Keep this wedding", f"{base}/?account=weddings", after)
    subject = (f"Tomorrow: “{wedding}” is removed from Auto Edits" if last
               else f"“{wedding}” will be removed from Auto Edits on {when}")
    return subject, text, html


def for_account(store, user: dict) -> list[dict]:
    """Every wedding of this photographer with its expiry date (None for the studio account)."""
    exempt = bool(user.get("admin"))
    with store._db() as db:
        rows = db.execute("SELECT w.id, w.name, w.last_used, w.created, w.archived, w.photos_kept, "
                          "(SELECT COUNT(*) FROM photos p WHERE p.wedding_id=w.id) AS photos "
                          "FROM weddings w WHERE w.owner=? ORDER BY w.created DESC", (user["id"],)).fetchall()
    out = []
    for r in rows:
        last = float(r["last_used"] or r["created"])
        out.append({"id": r["id"], "name": r["name"], "photos": int(r["photos_kept"] if r["archived"] else r["photos"]),
                    "last_used": last, "archived": bool(r["archived"]),
                    "expires": None if exempt or r["archived"] else expires_at(last)})
    return out


class Retention:
    def __init__(self, store, accounts, exports, mailer, base_url: str):
        self.store, self.accounts, self.exports, self.mailer = store, accounts, exports, mailer
        self.base = base_url.rstrip("/")
        self.last = 0.0
        with store._db() as db:
            db.executescript(SCHEMA)

    def run_if_due(self, now: float | None = None) -> dict | None:
        now = now or time.time()
        if now - self.last < 3600:
            return None
        self.last = now
        try:
            return self.run(now)
        except Exception as e:                       # never stop the worker
            print(f"[retention] {type(e).__name__}: {e}", flush=True)
            return None

    @staticmethod
    def _key(wedding_id: str, last_used: float) -> str:
        """One cycle per (wedding, last use): opening the wedding starts a fresh cycle with fresh emails."""
        return f"retain:{wedding_id}:{int(last_used)}"

    def run(self, now: float) -> dict:
        done = {"warned": 0, "archived": 0}
        with self.store._db() as db:
            rows = [dict(r) for r in db.execute(
                "SELECT w.id, w.name, w.owner, w.last_used, w.created, u.email, u.admin, "
                "(SELECT COUNT(*) FROM photos p WHERE p.wedding_id=w.id) AS photos "
                "FROM weddings w JOIN users u ON u.id=w.owner WHERE w.archived=0 AND u.admin=0")]
        for w in rows:
            last = float(w["last_used"] or w["created"])
            ends = expires_at(last)
            left = (ends - now) / DAY                 # days until removal
            if left > WARN_DAYS[0]:
                continue
            key = self._key(w["id"], last)
            if left <= 0:
                if w["photos"]:
                    self.exports.refund_wedding(w["id"])        # exports still to be made: their credits come back
                self.store.archive_wedding(w["id"], now)
                with self.store._db() as db:
                    db.execute("INSERT OR IGNORE INTO nudges (user_id, kind, step, sent) VALUES (?,?,?,?)", (w["owner"], key, 0, now))
                done["archived"] += 1
                continue
            due = [d for d in WARN_DAYS if left <= d]            # 14 and 1 days before; the nearest one is sent
            if not due:
                continue
            step = due[-1]
            with self.store._db() as db:
                sent = {r[0] for r in db.execute("SELECT step FROM nudges WHERE user_id=? AND kind=?", (w["owner"], key))}
                if step in sent:
                    continue
                for s in due:                                   # earlier ones missed (the worker was down) are not sent late
                    db.execute("INSERT OR IGNORE INTO nudges (user_id, kind, step, sent) VALUES (?,?,?,?)", (w["owner"], key, s, now))
            subject, text, html = warning_email(self.base, w["name"], int(w["photos"]), ends, step == WARN_DAYS[-1])
            self.mailer.send(w["email"], subject, text, html)
            done["warned"] += 1
        return done


def router(ctx: dict) -> APIRouter:
    r = APIRouter()
    store, signed_in = ctx["store"], ctx["signed_in"]

    @r.get("/api/account/weddings")
    def account_weddings(user: dict = Depends(signed_in)):
        """For the Account window: each wedding, when it was last used and when its photos go."""
        return {"weddings": for_account(store, user), "keep_days": KEEP_DAYS, "exempt": bool(user.get("admin"))}

    @r.post("/api/weddings/{wedding_id}/keep")
    def keep(wedding_id: str, user: dict = Depends(signed_in)):
        """Keep for 180 more days: counts from now."""
        if user.get("viewing_as"):
            raise HTTPException(403, "Not while viewing someone else's account.")
        if store.owner_of(wedding_id) != user["id"]:
            raise HTTPException(404, "no such wedding")
        w = store.wedding(wedding_id)
        if w is None or w.get("archived"):
            raise HTTPException(409, "This wedding's photos were already removed. Send them again from Lightroom to start over.")
        now = time.time()
        store.touch(wedding_id, now, force=True)
        return {"ok": True, "expires": None if user.get("admin") else expires_at(now)}

    return r
