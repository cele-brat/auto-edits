"""Reminder emails and the end of free trial credits (Bharat, Oct 2026).

* Free trial credits (the 50 given when the email is confirmed) last FREE_DAYS (15) days.
  Bought credits never end. On day 15 whatever is left of the free credits is taken off,
  as one ledger row of kind "expired" (credits used count against the free ones first).
* Reminders every 3 days until the photographer starts using credits, the last on day 14:
    - email not confirmed yet: "confirm your email to get your free credits", counted from sign-up;
    - confirmed but no credit used yet: "your free credits end on <date>", counted from when they were given.
  They stop as soon as a credit is used (or bought).

* Storage ending (Oct 2026): 7 days before a prepaid storage grant ends, and 3 days before an auto-renew
  charge, one email each (kind "storage", step = the grant id; kind "renew", step = the charge number).
* Low credits: when the balance is below LOW_CREDITS (or 10% of the last purchase, whichever is larger),
  one email in 7 days with a Buy link (kind "low", step = the week number).

Run by the worker every few minutes; does real work at most once an hour.
"""
from __future__ import annotations

import time

FREE_DAYS = 15
REMIND_DAYS = (3, 6, 9, 12, 14)
DAY = 86400
USE_KINDS = ("cull", "export", "lightroom")          # kinds that spend credits
LOW_CREDITS = 50
LOW_SHARE = 0.10                                     # ... or 10% of the last purchase, whichever is larger
LOW_EVERY_DAYS = 7
STORAGE_WARN_DAYS, RENEW_WARN_DAYS = 7, 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS nudges (
    user_id TEXT NOT NULL, kind TEXT NOT NULL, step INTEGER NOT NULL, sent REAL NOT NULL,
    PRIMARY KEY (user_id, kind, step)
);
"""


def free_grant(db, user_id: str):
    """The free trial row (created, credits), or None."""
    r = db.execute("SELECT created, delta FROM ledger WHERE user_id=? AND kind='welcome' ORDER BY id LIMIT 1", (user_id,)).fetchone()
    return (float(r[0]), int(r[1])) if r else None


def free_left(db, user_id: str) -> int:
    """How many of the free credits are still unused (and not yet ended)."""
    g = free_grant(db, user_id)
    if not g:
        return 0
    if db.execute("SELECT 1 FROM ledger WHERE user_id=? AND kind='expired' LIMIT 1", (user_id,)).fetchone():
        return 0
    marks = ",".join("?" * len(USE_KINDS))
    used = -int(db.execute(f"SELECT COALESCE(SUM(delta),0) FROM ledger WHERE user_id=? AND kind IN ({marks}) AND created>=?",
                           (user_id, *USE_KINDS, g[0])).fetchone()[0])
    back = int(db.execute("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE user_id=? AND kind='refund' AND created>=?",
                          (user_id, g[0])).fetchone()[0])
    balance = int(db.execute("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE user_id=?", (user_id,)).fetchone()[0])
    return max(0, min(balance, g[1] - max(0, used - back)))


def free_status(accounts, user_id: str) -> dict | None:
    """For the account window: free credits left and when they end."""
    with accounts._db() as db:
        g = free_grant(db, user_id)
        left = free_left(db, user_id) if g else 0
    if not g or not left:
        return None
    return {"left": left, "ends": g[0] + FREE_DAYS * DAY}


def _used_any(db, user_id: str) -> bool:
    marks = ",".join("?" * len(USE_KINDS))
    return db.execute(f"SELECT 1 FROM ledger WHERE user_id=? AND (kind IN ({marks}) OR kind='purchase') LIMIT 1",
                      (user_id, *USE_KINDS)).fetchone() is not None


def _due_step(start: float, now: float, sent: set[int]) -> tuple[int | None, list[int]]:
    """The reminder to send now (the latest one due), and earlier ones to skip quietly."""
    days = (now - start) / DAY
    due = [d for d in REMIND_DAYS if days >= d and d not in sent]
    if not due or days >= FREE_DAYS:
        return None, due
    return due[-1], due[:-1]


def is_low(balance: int, last_purchase: int) -> bool:
    """Below 50, or below 10% of the last purchase, whichever is larger."""
    return balance < max(LOW_CREDITS, int(last_purchase * LOW_SHARE))


def _day(t: float) -> str:
    import datetime
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    return datetime.datetime.fromtimestamp(t, ist).strftime("%d %B %Y").lstrip("0")


class Nudges:
    def __init__(self, accounts, mailer, base_url: str):
        self.accounts, self.mailer, self.base = accounts, mailer, base_url.rstrip("/")
        self.last = 0.0
        with accounts._db() as db:
            db.executescript(SCHEMA)

    def run_if_due(self, now: float | None = None) -> dict | None:
        now = now or time.time()
        if now - self.last < 3600:
            return None
        self.last = now
        try:
            return self.run(now)
        except Exception as e:                       # never stop the worker
            print(f"[nudges] {type(e).__name__}: {e}", flush=True)
            return None

    def _sent(self, db, user_id: str, kind: str) -> set[int]:
        return {r[0] for r in db.execute("SELECT step FROM nudges WHERE user_id=? AND kind=?", (user_id, kind))}

    def _mark(self, db, user_id: str, kind: str, steps, now: float) -> None:
        for s in steps:
            db.execute("INSERT OR IGNORE INTO nudges (user_id, kind, step, sent) VALUES (?,?,?,?)", (user_id, kind, s, now))

    def run(self, now: float) -> dict:
        from . import mail
        done = {"expired": 0, "confirm": 0, "use": 0}
        with self.accounts._db() as db:
            users = [dict(r) for r in db.execute("SELECT id, email, name, verified, admin, created FROM users WHERE admin=0")]
        for u in users:
            with self.accounts._db() as db:
                g = free_grant(db, u["id"])
                # 1. free credits end after FREE_DAYS
                if g and now - g[0] >= FREE_DAYS * DAY and not db.execute(
                        "SELECT 1 FROM nudges WHERE user_id=? AND kind='expired'", (u["id"],)).fetchone():
                    left = free_left(db, u["id"])
                    if left > 0:
                        db.execute("INSERT INTO ledger (user_id, delta, kind, note, ref, photos, created, wedding) "
                                   "VALUES (?,?,?,?,?,?,?,?)", (u["id"], -left, "expired",
                                   f"Free trial credits ended ({FREE_DAYS} days)", "", 0, now, ""))
                        done["expired"] += 1
                    self._mark(db, u["id"], "expired", [0], now)
                    continue
                # 2. reminders
                if not u["verified"]:
                    kind, start = "confirm", float(u["created"])
                elif g and not _used_any(db, u["id"]):
                    kind, start = "use", g[0]
                else:
                    continue
                step, skip = _due_step(start, now, self._sent(db, u["id"], kind))
                self._mark(db, u["id"], kind, skip, now)
                if step is None:
                    continue
                self._mark(db, u["id"], kind, [step], now)
            last = step == REMIND_DAYS[-1]
            if kind == "confirm":
                token = self.accounts.email_token(u["id"], "verify")
                subject, text, html = mail.confirm_reminder(self.base, token, last)
            else:
                with self.accounts._db() as db:
                    left = free_left(db, u["id"])
                if not left:
                    continue
                subject, text, html = mail.use_reminder(self.base, left, _day(g[0] + FREE_DAYS * DAY), last)
            self.mailer.send(u["email"], subject, text, html)
            done[kind] += 1
        done.update(self.storage_and_credits(now))
        return done

    def storage_and_credits(self, now: float) -> dict:
        """Storage ending or renewing soon, and low credits (see the module notes). Each sent once."""
        from . import mail, payments as pay
        done = {"storage": 0, "renew": 0, "low": 0}
        with self.accounts._db() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "storage_grants" not in tables or "subscriptions" not in tables:
                return done                          # the server has not run with payments phase 2 yet
            users = {r["id"]: dict(r) for r in db.execute("SELECT id, email, admin, verified FROM users WHERE admin=0")}
            grants = [dict(r) for r in db.execute(
                "SELECT g.id, g.user_id, g.gb, g.until, COALESCE(o.sub_id, '') AS sub_id FROM storage_grants g "
                "LEFT JOIN orders o ON o.id=g.order_id WHERE g.until>? AND g.until<=?",
                (now, now + STORAGE_WARN_DAYS * DAY))]
            subs = [dict(r) for r in db.execute(
                "SELECT * FROM subscriptions WHERE status='active' AND next_charge>? AND next_charge<=?",
                (now, now + RENEW_WARN_DAYS * DAY))]
            renewing = {r[0] for r in db.execute("SELECT user_id FROM subscriptions WHERE status IN ('active','created')")}
        # 1. prepaid storage ending within 7 days (not when auto-renew keeps it going)
        for g in grants:
            u = users.get(g["user_id"])
            if not u or g["sub_id"] or u["id"] in renewing:
                continue
            with self.accounts._db() as db:
                if g["id"] in self._sent(db, u["id"], "storage"):
                    continue
                self._mark(db, u["id"], "storage", [g["id"]], now)
            subject, text, html = mail.storage_ending(self.base, g["gb"], _day(g["until"]), renews=False)
            self.mailer.send(u["email"], subject, text, html)
            done["storage"] += 1
        # 2. an auto-renew charge within 3 days
        for s in subs:
            u = users.get(s["user_id"])
            if not u:
                continue
            step = s["charges"] + 1
            with self.accounts._db() as db:
                if step in self._sent(db, u["id"], "renew"):
                    continue
                self._mark(db, u["id"], "renew", [step], now)
            subject, text, html = mail.storage_ending(self.base, s["blocks"] * pay.BLOCK_GB, _day(s["next_charge"]),
                                                      renews=True, amount=pay.rupees(s["amount"]))
            self.mailer.send(u["email"], subject, text, html)
            done["renew"] += 1
        # 3. low credits, once in 7 days, only once the account has spent or bought credits
        week = int(now // (LOW_EVERY_DAYS * DAY))
        for u in users.values():
            if not u["verified"]:
                continue
            with self.accounts._db() as db:
                balance = int(db.execute("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE user_id=?", (u["id"],)).fetchone()[0])
                last = db.execute("SELECT delta FROM ledger WHERE user_id=? AND kind='purchase' ORDER BY id DESC LIMIT 1",
                                  (u["id"],)).fetchone()
                spent = db.execute("SELECT 1 FROM ledger WHERE user_id=? AND delta<0 AND kind<>'expired' LIMIT 1", (u["id"],)).fetchone()
                if not (last or spent) or not is_low(balance, int(last[0]) if last else 0):
                    continue
                last_sent = db.execute("SELECT MAX(sent) FROM nudges WHERE user_id=? AND kind='low'", (u["id"],)).fetchone()[0]
                if last_sent is not None and now - float(last_sent) < LOW_EVERY_DAYS * DAY:   # 7 days since the last one, not calendar weeks
                    continue
                self._mark(db, u["id"], "low", [week], now)
            price = pay.prices()
            line = (f"Credits cost {pay.rupees(price['credit_paise'])} each + 18% GST (from {pay.MIN_CREDITS}), or the Season pack: "
                    f"{price['season_credits']:,} credits for {pay.rupees(price['season_paise'])} + GST.")
            subject, text, html = mail.low_credits(self.base, balance, line)
            self.mailer.send(u["email"], subject, text, html)
            done["low"] += 1
        return done
