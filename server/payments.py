"""Buying credits and extra storage with Razorpay, with GST invoices (Bharat, Oct 2026).

Prices (GST 18% on top):
  - credits: Rs 1 each until 31 December 2026 (India time), Rs 3 after; at least 500, then in steps of 100
  - extra storage: 50 GB for Rs 150 a month, 1 to 10 blocks, prepaid for 1, 3, 6 or 12 months
GST: Celebrat Ventures is registered in Rajasthan (state code 08). A buyer in Rajasthan pays CGST 9% + SGST 9%,
anyone else IGST 18%. The buyer's state comes from their GSTIN when they give one, otherwise from the state they pick.

Flow: /api/buy makes a Razorpay order and a row here ('created'). The browser opens Razorpay Checkout; when it
reports success, /api/buy/verify checks Razorpay's signature and fulfils the order. Razorpay's webhook does the same,
so a closed browser tab never loses a payment. Fulfilling is done once only: credits go into the ledger (kind
'purchase'), storage becomes a storage grant, and the order gets the next invoice number of the financial year.

Prices can be changed without code: the `settings` table (key/value) holds credit_price_paise, launch_price_until
and the pack prices (see PRICE_KEYS); the constants below are the defaults. /admin has a "Prices" box for them.

Phase 2 (Oct 2026, server/billing.py):
  - the "Season pack": one-time pack of credits at a lower price (5,000 for Rs 4,000 + GST until 31 Dec 2026,
    10,000 for Rs 27,500 + GST after), fulfilled like a credits order (kind 'season')
  - auto-renew storage with Razorpay Subscriptions: a plan per block count, each monthly charge becomes an order
    (kind 'storage', sub_id set) fulfilled the usual way, so it gets a storage grant and an invoice
  - refunds from /admin: Razorpay refund, a `refunds` row, credits taken back (ledger kind 'refund'), and a
    credit note numbered CN/2026-27/00001 (TEST/CN/... in test mode), printable at /credit-note/<id>
  - the invoice (and credit note) is emailed when fulfilled; Payments.on_paid is the hook billing.py sets

Settings (deploy/.env on the VPS; never in the code):
  RAZORPAY_KEY_ID  RAZORPAY_KEY_SECRET  RAZORPAY_WEBHOOK_SECRET
Without them payments stay switched off and the app says "coming soon".
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
GST_RATE = 18
SELLER_STATE = "08"                          # Rajasthan
LAUNCH_PRICE_PAISE, PRICE_PAISE = 100, 300   # per credit
LAUNCH_ENDS = dt.datetime(2027, 1, 1, tzinfo=IST)
MIN_CREDITS, CREDIT_STEP, MAX_CREDITS = 500, 100, 1_000_000
BLOCK_GB, BLOCK_PAISE_MONTH, MAX_BLOCKS = 50, 15_000, 10
MONTHS = (1, 3, 6, 12)
SAC = "998383"                               # photographic and videographic processing services
INVOICE_PREFIX = "AE"
CREDIT_NOTE_PREFIX = "CN"
SEASON_CREDITS, SEASON_PAISE = 5_000, 400_000           # the Season pack until the launch price ends
LATER_CREDITS, LATER_PAISE = 10_000, 2_750_000          # ... and after
SUB_MONTHS = 120                                        # how many monthly charges a Razorpay subscription may run
# Keys in the `settings` table that change prices without code, with their defaults (paise, or a date YYYY-MM-DD).
PRICE_KEYS = {
    "credit_price_paise": LAUNCH_PRICE_PAISE, "credit_price_after_paise": PRICE_PAISE, "launch_price_until": "2026-12-31",
    "season_pack_credits": SEASON_CREDITS, "season_pack_paise": SEASON_PAISE,
    "later_pack_credits": LATER_CREDITS, "later_pack_paise": LATER_PAISE, "block_paise_month": BLOCK_PAISE_MONTH,
}
PRICES_FROM = None                           # set by Payments: a function giving the live settings (None: defaults)
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")

STATES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh", "05": "Uttarakhand",
    "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura", "17": "Meghalaya",
    "18": "Assam", "19": "West Bengal", "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL, credits INTEGER NOT NULL DEFAULT 0,
    blocks INTEGER NOT NULL DEFAULT 0, months INTEGER NOT NULL DEFAULT 0,
    taxable INTEGER NOT NULL, cgst INTEGER NOT NULL DEFAULT 0, sgst INTEGER NOT NULL DEFAULT 0, igst INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL, state TEXT NOT NULL, buyer_name TEXT NOT NULL DEFAULT '', buyer_gstin TEXT NOT NULL DEFAULT '',
    buyer_email TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'created', rzp_order TEXT NOT NULL DEFAULT '',
    rzp_payment TEXT NOT NULL DEFAULT '', invoice_no TEXT NOT NULL DEFAULT '', created REAL NOT NULL, paid REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS orders_by_user ON orders (user_id, created);
CREATE UNIQUE INDEX IF NOT EXISTS orders_by_rzp ON orders (rzp_order);
CREATE TABLE IF NOT EXISTS storage_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, gb INTEGER NOT NULL,
    start REAL NOT NULL, until REAL NOT NULL, order_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS grants_by_user ON storage_grants (user_id, until);
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, blocks INTEGER NOT NULL, amount INTEGER NOT NULL,
    state TEXT NOT NULL, buyer_name TEXT NOT NULL DEFAULT '', buyer_gstin TEXT NOT NULL DEFAULT '',
    buyer_email TEXT NOT NULL DEFAULT '', plan_id TEXT NOT NULL DEFAULT '', rzp_sub TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'created', created REAL NOT NULL, charges INTEGER NOT NULL DEFAULT 0,
    next_charge REAL NOT NULL DEFAULT 0, ended REAL NOT NULL DEFAULT 0, test INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS subscriptions_by_rzp ON subscriptions (rzp_sub);
CREATE INDEX IF NOT EXISTS subscriptions_by_user ON subscriptions (user_id, created);
CREATE TABLE IF NOT EXISTS refunds (
    id TEXT PRIMARY KEY, order_id TEXT NOT NULL, user_id TEXT NOT NULL, amount INTEGER NOT NULL,
    taxable INTEGER NOT NULL, cgst INTEGER NOT NULL DEFAULT 0, sgst INTEGER NOT NULL DEFAULT 0, igst INTEGER NOT NULL DEFAULT 0,
    credits INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '', rzp_refund TEXT NOT NULL DEFAULT '',
    note_no TEXT NOT NULL DEFAULT '', created REAL NOT NULL, test INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS refunds_by_order ON refunds (order_id);
"""


class PaymentError(Exception):
    pass


def _settings() -> dict:
    """The live price settings: defaults, overridden by whatever the settings table holds."""
    out = dict(PRICE_KEYS)
    if PRICES_FROM is not None:
        for k, v in PRICES_FROM().items():
            if k in PRICE_KEYS and v not in ("", None):
                out[k] = v if k == "launch_price_until" else int(v)
    return out


def launch_ends(settings: dict | None = None) -> dt.datetime:
    """Midnight (India time) after the last day of the launch price."""
    s = settings or _settings()
    try:
        day = dt.datetime.strptime(str(s["launch_price_until"]), "%Y-%m-%d")
    except ValueError:
        day = LAUNCH_ENDS - dt.timedelta(days=1)
    return dt.datetime(day.year, day.month, day.day, tzinfo=IST) + dt.timedelta(days=1)


def prices(now: dt.datetime | None = None) -> dict:
    """Every live price in one place, for /api/billing/prices, the quote and the pages."""
    now = now or dt.datetime.now(IST)
    s = _settings()
    ends = launch_ends(s)
    launch = now < ends
    return {"credit_paise": int(s["credit_price_paise"] if launch else s["credit_price_after_paise"]),
            "launch": launch, "launch_ends": (ends - dt.timedelta(days=1)).strftime("%-d %B %Y"),
            "launch_ends_date": (ends - dt.timedelta(days=1)).strftime("%Y-%m-%d"),
            "credit_after_paise": int(s["credit_price_after_paise"]),
            "season_credits": int(s["season_pack_credits"] if launch else s["later_pack_credits"]),
            "season_paise": int(s["season_pack_paise"] if launch else s["later_pack_paise"]),
            "block_paise_month": int(s["block_paise_month"]), "block_gb": BLOCK_GB, "max_blocks": MAX_BLOCKS,
            "months": MONTHS, "min_credits": MIN_CREDITS, "credit_step": CREDIT_STEP, "gst": GST_RATE,
            "settings": {k: str(v) for k, v in s.items()}}


def credit_price(now: dt.datetime | None = None) -> int:
    return prices(now)["credit_paise"]


def taxes(taxable: int, state: str) -> dict:
    if state == SELLER_STATE:
        half = round(taxable * GST_RATE / 200)
        return {"cgst": half, "sgst": half, "igst": 0, "total": taxable + 2 * half}
    igst = round(taxable * GST_RATE / 100)
    return {"cgst": 0, "sgst": 0, "igst": igst, "total": taxable + igst}


def quote(kind: str, credits: int = 0, blocks: int = 0, months: int = 0, state: str = "", gstin: str = "",
          now: dt.datetime | None = None) -> dict:
    """What it costs, checked. Raises PaymentError with a message to show."""
    gstin = (gstin or "").strip().upper()
    if gstin:
        if not GSTIN_RE.match(gstin):
            raise PaymentError("That GSTIN does not look right (15 characters, like 08ABCDE1234F1Z5).")
        state = gstin[:2]
    if state not in STATES:
        raise PaymentError("Please choose your state (needed for GST).")
    if kind == "credits":
        if credits < MIN_CREDITS or credits % CREDIT_STEP or credits > MAX_CREDITS:
            raise PaymentError(f"Credits are sold from {MIN_CREDITS} upwards, in steps of {CREDIT_STEP}.")
        unit = credit_price(now)
        taxable = credits * unit
        blocks = months = 0
    elif kind == "season":
        p = prices(now)
        credits, unit, taxable = p["season_credits"], 0, p["season_paise"]
        blocks = months = 0
    elif kind == "storage":
        if not 1 <= blocks <= MAX_BLOCKS or months not in MONTHS:
            raise PaymentError(f"Choose 1 to {MAX_BLOCKS} blocks of {BLOCK_GB} GB, for 1, 3, 6 or 12 months.")
        unit = prices(now)["block_paise_month"]
        taxable = blocks * months * unit
        credits = 0
    else:
        raise PaymentError("Unknown purchase.")
    return {"kind": kind, "credits": credits, "blocks": blocks, "months": months, "unit": unit, "taxable": taxable,
            "state": state, "gstin": gstin, **taxes(taxable, state)}


# ---- Razorpay -----------------------------------------------------------------------
class Razorpay:
    API = "https://api.razorpay.com/v1"

    def __init__(self, key_id: str, secret: str, webhook_secret: str):
        self.key_id, self.secret, self.webhook_secret = key_id, secret, webhook_secret

    @property
    def on(self) -> bool:
        return bool(self.key_id and self.secret)

    @property
    def test_mode(self) -> bool:
        return self.key_id.startswith("rzp_test_")

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        auth = base64.b64encode(f"{self.key_id}:{self.secret}".encode()).decode()
        req = urllib.request.Request(self.API + path, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                why = json.loads(e.read()).get("error", {}).get("description", "")
            except Exception:
                why = ""
            raise PaymentError(f"Razorpay said: {why or e.code}")
        except urllib.error.URLError:
            raise PaymentError("Razorpay could not be reached. Please try again in a minute.")

    def create_order(self, amount: int, receipt: str, notes: dict) -> dict:
        return self._call("POST", "/orders", {"amount": amount, "currency": "INR", "receipt": receipt, "notes": notes})

    # ---- subscriptions (auto-renew storage) ----
    def create_plan(self, amount: int, name: str, notes: dict) -> dict:
        return self._call("POST", "/plans", {"period": "monthly", "interval": 1, "notes": notes,
                                             "item": {"name": name, "amount": amount, "currency": "INR"}})

    def create_subscription(self, plan_id: str, notes: dict, total_count: int = SUB_MONTHS) -> dict:
        return self._call("POST", "/subscriptions", {"plan_id": plan_id, "total_count": total_count, "quantity": 1,
                                                     "customer_notify": 1, "notes": notes})

    def cancel_subscription(self, sub_id: str, at_cycle_end: bool = True) -> dict:
        return self._call("POST", f"/subscriptions/{sub_id}/cancel", {"cancel_at_cycle_end": 1 if at_cycle_end else 0})

    def refund(self, payment_id: str, amount: int, notes: dict) -> dict:
        return self._call("POST", f"/payments/{payment_id}/refund", {"amount": amount, "notes": notes})

    def payment_ok(self, order_id: str, payment_id: str, signature: str) -> bool:
        good = hmac.new(self.secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
        return bool(signature) and hmac.compare_digest(good, signature)

    def subscription_ok(self, sub_id: str, payment_id: str, signature: str) -> bool:
        """Checkout's signature for a subscription payment is over payment id | subscription id."""
        good = hmac.new(self.secret.encode(), f"{payment_id}|{sub_id}".encode(), hashlib.sha256).hexdigest()
        return bool(signature) and hmac.compare_digest(good, signature)

    def webhook_ok(self, body: bytes, signature: str) -> bool:
        if not self.webhook_secret or not signature:
            return False
        good = hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(good, signature)


def _clean(value: str) -> str:
    """Keys pasted into a terminal can pick up spaces, quotes or invisible paste markers (ESC[200~ … ESC[201~)."""
    value = re.sub(r"\x1b\[20[01]~", "", value or "")
    return re.sub(r"[\x00-\x20\x7f\"']", "", value)


def from_environment() -> Razorpay:
    e = os.environ
    return Razorpay(_clean(e.get("RAZORPAY_KEY_ID", "")), _clean(e.get("RAZORPAY_KEY_SECRET", "")),
                    _clean(e.get("RAZORPAY_WEBHOOK_SECRET", "")))


# ---- orders ---------------------------------------------------------------------------
class Payments:
    def __init__(self, store, accounts, razorpay: Razorpay | None = None):
        global PRICES_FROM
        self.store, self.accounts = store, accounts
        self.rzp = razorpay or from_environment()
        self.on_paid = None                       # set by billing.py: called with the order once it is fulfilled
        with store._db() as db:
            db.executescript(SCHEMA)
            columns = {r["name"] for r in db.execute("PRAGMA table_info(orders)")}
            if "test" not in columns:
                # Test payments are kept apart so real tax invoices are numbered without gaps (GST).
                # Before this column existed only test keys had ever been used (14 Oct 2026), so every
                # earlier order is a test one: it moves to the TEST/ series.
                db.execute("ALTER TABLE orders ADD COLUMN test INTEGER NOT NULL DEFAULT 0")
                db.execute("UPDATE orders SET test=1, invoice_no=CASE WHEN invoice_no='' THEN '' "
                           "ELSE 'TEST/' || substr(invoice_no, 4) END")
            if "sub_id" not in columns:            # a monthly charge of an auto-renew storage subscription
                db.execute("ALTER TABLE orders ADD COLUMN sub_id TEXT NOT NULL DEFAULT ''")
            if "refunded" not in columns:          # paise given back so far (refunds table has the detail)
                db.execute("ALTER TABLE orders ADD COLUMN refunded INTEGER NOT NULL DEFAULT 0")
        PRICES_FROM = self.settings

    def _db(self):
        return self.store._db()

    # ---- prices without code (settings table) ----
    def settings(self) -> dict:
        with self._db() as db:
            marks = ",".join("?" * len(PRICE_KEYS))
            return {r["k"]: r["v"] for r in db.execute(f"SELECT k, v FROM settings WHERE k IN ({marks})", tuple(PRICE_KEYS))}

    def set_prices(self, values: dict) -> dict:
        """Checked and saved; returns what changed (old -> new) for the admin log."""
        changed = {}
        before = {**{k: str(v) for k, v in PRICE_KEYS.items()}, **self.settings()}
        with self._db() as db:
            for k, v in values.items():
                if k not in PRICE_KEYS:
                    continue
                v = str(v).strip()
                if k == "launch_price_until":
                    try:
                        dt.datetime.strptime(v, "%Y-%m-%d")
                    except ValueError:
                        raise PaymentError("The launch price end date must look like 2026-12-31.")
                else:
                    if not v.isdigit() or not 1 <= int(v) <= 100_000_000:
                        raise PaymentError(f"{k} must be a whole number (paise, or credits).")
                    v = str(int(v))
                if before.get(k) != v:
                    changed[k] = (before.get(k), v)
                db.execute("INSERT OR REPLACE INTO settings (k, v) VALUES (?,?)", (k, v))
        return changed

    def start(self, user: dict, q: dict, buyer_name: str = "") -> dict:
        """Make the Razorpay order for a checked quote. Returns what Checkout needs."""
        if not self.rzp.on:
            raise PaymentError("Payments are being switched on. Please try again soon.")
        oid = uuid.uuid4().hex[:16]
        what = describe(q)
        rz = self.rzp.create_order(q["total"], oid, {"account": user["email"], "what": what})
        with self._db() as db:
            db.execute("INSERT INTO orders (id, user_id, kind, credits, blocks, months, taxable, cgst, sgst, igst, total, state, "
                       "buyer_name, buyer_gstin, buyer_email, rzp_order, created, test) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (oid, user["id"], q["kind"], q["credits"], q["blocks"], q["months"], q["taxable"], q["cgst"], q["sgst"],
                        q["igst"], q["total"], q["state"], (buyer_name.strip() or user.get("name") or "")[:120], q["gstin"],
                        user["email"], rz["id"], time.time(), int(not self.rzp.key_id.startswith("rzp_live_"))))
        return {"order": oid, "key": self.rzp.key_id, "rzp_order": rz["id"], "amount": q["total"], "what": what,
                "email": user["email"], "name": user.get("name") or ""}

    def by_rzp(self, rzp_order: str) -> dict | None:
        with self._db() as db:
            r = db.execute("SELECT * FROM orders WHERE rzp_order=?", (rzp_order,)).fetchone()
        return dict(r) if r else None

    def get(self, order_id: str) -> dict | None:
        with self._db() as db:
            r = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        return dict(r) if r else None

    def fulfil(self, rzp_order: str, rzp_payment: str, amount: int | None = None) -> dict | None:
        """Mark paid and deliver, exactly once. Returns the order (None if unknown).
        A retry after a failed payment is the same Razorpay order with a new payment id: the first
        successful one wins, later calls (Checkout and the webhook) find the order paid and change nothing."""
        o = self.by_rzp(rzp_order)
        if o is None:
            return None
        if amount is not None and amount != o["total"]:
            raise PaymentError("The amount paid does not match the order.")
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT status FROM orders WHERE id=?", (o["id"],)).fetchone()["status"] == "paid":
                return self.get(o["id"])
            invoice = self._next_invoice(db, now, bool(o["test"]))
            db.execute("UPDATE orders SET status='paid', paid=?, rzp_payment=?, invoice_no=? WHERE id=?",
                       (now, rzp_payment, invoice, o["id"]))
            if o["kind"] in ("credits", "season"):
                what = "the Season pack" if o["kind"] == "season" else f"{o['credits']:,} credits"
                db.execute("INSERT INTO ledger (user_id, delta, kind, note, ref, photos, created) VALUES (?,?,?,?,?,?,?)",
                           (o["user_id"], o["credits"], "purchase", f"Bought {what} (invoice {invoice})", o["id"], 0, now))
            else:
                start = now
                if o.get("sub_id"):              # a renewal starts when the previous month of this subscription ends
                    last = db.execute("SELECT MAX(until) FROM storage_grants WHERE order_id IN "
                                      "(SELECT id FROM orders WHERE sub_id=? AND id<>?)", (o["sub_id"], o["id"])).fetchone()[0]
                    if last and last > now:
                        start = float(last)
                until = start + o["months"] * 30.4375 * 86400
                db.execute("INSERT INTO storage_grants (user_id, gb, start, until, order_id) VALUES (?,?,?,?,?)",
                           (o["user_id"], o["blocks"] * BLOCK_GB, start, until, o["id"]))
        done = self.get(o["id"])
        if self.on_paid is not None:
            try:
                self.on_paid(done)
            except Exception as e:               # the email must never undo a delivered payment
                print(f"[payments] after-payment hook failed: {type(e).__name__}: {e}", flush=True)
        return done

    # ---- auto-renew storage (Razorpay Subscriptions) ----
    def plan_for(self, blocks: int, amount: int) -> str:
        """The Razorpay plan for this many blocks at this monthly price (taxable + GST), made once."""
        key = f"rzp_plan_{self.rzp.key_id}_{blocks}_{amount}"
        with self._db() as db:
            r = db.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
        if r:
            return r["v"]
        plan = self.rzp.create_plan(amount, f"Auto Edits extra storage, {blocks * BLOCK_GB} GB a month",
                                    {"blocks": str(blocks), "gb": str(blocks * BLOCK_GB)})
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO settings (k, v) VALUES (?,?)", (key, plan["id"]))
        return plan["id"]

    def subscribe(self, user: dict, blocks: int, state: str, gstin: str = "", buyer_name: str = "") -> dict:
        """Start an auto-renew storage subscription. Returns what Checkout needs (subscription_id)."""
        if not self.rzp.on:
            raise PaymentError("Payments are being switched on. Please try again soon.")
        q = quote("storage", blocks=blocks, months=1, state=state, gstin=gstin)
        for s in self.subscriptions(user["id"]):
            if s["status"] in ("created", "active", "cancelling"):
                raise PaymentError("You already have auto-renew storage. Cancel it first to change the size.")
        plan = self.plan_for(blocks, q["total"])
        sid = uuid.uuid4().hex[:16]
        rz = self.rzp.create_subscription(plan, {"account": user["email"], "sub": sid})
        with self._db() as db:
            db.execute("INSERT INTO subscriptions (id, user_id, blocks, amount, state, buyer_name, buyer_gstin, buyer_email, "
                       "plan_id, rzp_sub, status, created, test) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (sid, user["id"], blocks, q["total"], q["state"], (buyer_name.strip() or user.get("name") or "")[:120],
                        q["gstin"], user["email"], plan, rz["id"], "created", time.time(),
                        int(not self.rzp.key_id.startswith("rzp_live_"))))
        return {"sub": sid, "key": self.rzp.key_id, "subscription_id": rz["id"], "amount": q["total"],
                "what": f"{blocks * BLOCK_GB} GB extra storage, renewed monthly", "email": user["email"],
                "name": user.get("name") or ""}

    def sub_by_rzp(self, rzp_sub: str) -> dict | None:
        with self._db() as db:
            r = db.execute("SELECT * FROM subscriptions WHERE rzp_sub=?", (rzp_sub,)).fetchone()
        return dict(r) if r else None

    def subscriptions(self, user_id: str) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM subscriptions WHERE user_id=? ORDER BY created DESC", (user_id,))]

    def charged(self, rzp_sub: str, rzp_payment: str, amount: int | None = None, next_charge: float = 0) -> dict | None:
        """A monthly charge of a subscription: one order (kind storage, 1 month) per payment, fulfilled the usual
        way, so it gets its storage grant and invoice. The same payment id never makes a second order."""
        s = self.sub_by_rzp(rzp_sub)
        if s is None:
            return None
        if amount is not None and amount != s["amount"]:
            raise PaymentError("The amount paid does not match the subscription.")
        rzp_order = f"sub:{rzp_sub}:{rzp_payment}"
        if self.by_rzp(rzp_order) is None:
            q = quote("storage", blocks=s["blocks"], months=1, state=s["state"], gstin=s["buyer_gstin"])
            oid = uuid.uuid4().hex[:16]
            with self._db() as db:
                db.execute("INSERT OR IGNORE INTO orders (id, user_id, kind, credits, blocks, months, taxable, cgst, sgst, igst, "
                           "total, state, buyer_name, buyer_gstin, buyer_email, rzp_order, created, test, sub_id) "
                           "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (oid, s["user_id"], "storage", 0, s["blocks"], 1, q["taxable"], q["cgst"], q["sgst"], q["igst"],
                            q["total"], q["state"], s["buyer_name"], s["buyer_gstin"], s["buyer_email"], rzp_order,
                            time.time(), s["test"], s["id"]))
                db.execute("UPDATE subscriptions SET status=CASE WHEN status='cancelling' THEN status ELSE 'active' END, "
                           "charges=charges+1, next_charge=? WHERE id=?",
                           (next_charge or time.time() + 30.4375 * 86400, s["id"]))
        return self.fulfil(rzp_order, rzp_payment)

    def sub_status(self, rzp_sub: str, status: str) -> None:
        with self._db() as db:
            db.execute("UPDATE subscriptions SET status=?, ended=CASE WHEN ? IN ('cancelled','halted','completed','expired') "
                       "THEN ? ELSE ended END WHERE rzp_sub=?", (status, status, time.time(), rzp_sub))

    def cancel(self, user_id: str, sub_id: str) -> dict:
        """Stop auto-renew at the end of the month already paid for."""
        with self._db() as db:
            r = db.execute("SELECT * FROM subscriptions WHERE id=? AND user_id=?", (sub_id, user_id)).fetchone()
        if r is None:
            raise PaymentError("No such subscription.")
        s = dict(r)
        if s["status"] in ("cancelled", "cancelling", "halted", "completed", "expired"):
            return s
        if s["status"] == "active":
            self.rzp.cancel_subscription(s["rzp_sub"], at_cycle_end=True)
            new = "cancelling"
        else:                                    # never charged: nothing to keep until the cycle ends
            self.rzp.cancel_subscription(s["rzp_sub"], at_cycle_end=False)
            new = "cancelled"
        with self._db() as db:
            db.execute("UPDATE subscriptions SET status=?, ended=? WHERE id=?", (new, time.time() if new == "cancelled" else 0, s["id"]))
        return {**s, "status": new}

    # ---- refunds and credit notes ----
    def refundable(self, o: dict) -> dict:
        """How much money, and how many credits, can still be given back on this order."""
        money = o["total"] - o["refunded"]
        balance = self.accounts.balance(o["user_id"])
        if o["kind"] in ("credits", "season"):
            with self._db() as db:
                back = int(db.execute("SELECT COALESCE(SUM(credits),0) FROM refunds WHERE order_id=?", (o["id"],)).fetchone()[0])
            left = o["credits"] - back
            return {"money": money, "credits": left, "balance": balance, "spent": max(0, left - balance)}
        return {"money": money, "credits": 0, "balance": balance, "spent": 0}

    def refund(self, order_id: str, amount: int, reason: str) -> dict:
        """Refund (part of) a paid order: Razorpay first, then the row, the credits back and a credit note.
        Credits come back in the same share as the money; refused when they were already spent."""
        o = self.get(order_id)
        if o is None or o["status"] != "paid":
            raise PaymentError("No such paid order.")
        can = self.refundable(o)
        if not 1 <= amount <= can["money"]:
            raise PaymentError(f"Up to {rupees(can['money'])} can be refunded on this invoice.")
        credits = 0
        if o["kind"] in ("credits", "season"):
            credits = can["credits"] if amount == can["money"] else round(o["credits"] * amount / o["total"])
            if credits > can["balance"]:
                most = o["total"] * can["balance"] // max(1, o["credits"])
                raise PaymentError(f"Only {can['balance']:,} of these credits are still unused, but {credits:,} would be taken "
                                   f"back. Refund at most {rupees(most)}, or take the credits back by hand first.")
        share = amount / o["total"]
        taxable = round(o["taxable"] * share)
        t = taxes(taxable, o["state"])
        if t["total"] != amount:                 # rounding: keep the money exact, adjust the taxable value
            taxable += amount - t["total"]
            t = taxes(taxable, o["state"])
            diff = amount - t["total"]
            t["total"] = amount
            if t["igst"]:
                t["igst"] += diff
            else:
                t["cgst"] += diff
        rz = self.rzp.refund(o["rzp_payment"], amount, {"invoice": o["invoice_no"], "reason": reason[:120]})
        rid = uuid.uuid4().hex[:16]
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            note = self._next_note(db, now, bool(o["test"]))
            db.execute("INSERT INTO refunds (id, order_id, user_id, amount, taxable, cgst, sgst, igst, credits, reason, rzp_refund, "
                       "note_no, created, test) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (rid, o["id"], o["user_id"], amount, taxable, t["cgst"], t["sgst"], t["igst"], credits, reason[:300],
                        rz.get("id", ""), note, now, o["test"]))
            db.execute("UPDATE orders SET refunded=refunded+? WHERE id=?", (amount, o["id"]))
            if credits:
                db.execute("INSERT INTO ledger (user_id, delta, kind, note, ref, photos, created) VALUES (?,?,?,?,?,?,?)",
                           (o["user_id"], -credits, "refund", f"Refunded {rupees(amount)} of invoice {o['invoice_no']} (credit note {note})",
                            rid, 0, now))
        return self.get_refund(rid)

    def get_refund(self, refund_id: str) -> dict | None:
        with self._db() as db:
            r = db.execute("SELECT r.*, o.invoice_no, o.kind, o.state, o.buyer_name, o.buyer_gstin, o.buyer_email, o.rzp_payment, "
                           "o.paid, o.credits AS order_credits, o.blocks, o.months, o.sub_id FROM refunds r JOIN orders o ON o.id=r.order_id "
                           "WHERE r.id=?", (refund_id,)).fetchone()
        return dict(r) if r else None

    def refunds_for(self, order_id: str) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM refunds WHERE order_id=? ORDER BY created", (order_id,))]

    @staticmethod
    def _next_note(db, now: float, test: bool = False) -> str:
        """Credit notes: CN/2026-27/00001 per financial year; TEST/CN/2026-27/… for test payments."""
        d = dt.datetime.fromtimestamp(now, IST)
        fy = d.year if d.month >= 4 else d.year - 1
        prefix = f"{'TEST/' if test else ''}{CREDIT_NOTE_PREFIX}/{fy}-{str(fy + 1)[2:]}/"
        last = db.execute("SELECT note_no FROM refunds WHERE note_no LIKE ? ORDER BY note_no DESC LIMIT 1",
                          (prefix + "%",)).fetchone()
        n = int(last["note_no"].rsplit("/", 1)[1]) + 1 if last else 1
        return f"{prefix}{n:05d}"

    @staticmethod
    def _next_invoice(db, now: float, test: bool = False) -> str:
        """Real invoices: AE/2026-27/00001, 00002… per financial year. Test payments: TEST/2026-27/…"""
        d = dt.datetime.fromtimestamp(now, IST)
        fy = d.year if d.month >= 4 else d.year - 1
        prefix = f"{'TEST' if test else INVOICE_PREFIX}/{fy}-{str(fy + 1)[2:]}/"
        last = db.execute("SELECT invoice_no FROM orders WHERE invoice_no LIKE ? ORDER BY invoice_no DESC LIMIT 1",
                          (prefix + "%",)).fetchone()
        n = int(last["invoice_no"].rsplit("/", 1)[1]) + 1 if last else 1
        return f"{prefix}{n:05d}"

    def for_user(self, user_id: str) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM orders WHERE user_id=? AND status='paid' ORDER BY paid DESC",
                                                (user_id,))]

    def recent(self, limit: int = 200) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT o.*, u.email FROM orders o LEFT JOIN users u ON u.id=o.user_id WHERE o.status='paid' "
                "ORDER BY o.paid DESC LIMIT ?", (limit,))]

    def extra_gb(self, user_id: str, at: float | None = None) -> int:
        at = at or time.time()
        with self._db() as db:
            return int(db.execute("SELECT COALESCE(SUM(gb),0) FROM storage_grants WHERE user_id=? AND start<=? AND until>?",
                                  (user_id, at, at)).fetchone()[0])

    def grants(self, user_id: str) -> list[dict]:
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT gb, start, until FROM storage_grants WHERE user_id=? AND until>? "
                                                "ORDER BY until", (user_id, time.time()))]


# ---- invoice ---------------------------------------------------------------------------
_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen " \
        "eighteen nineteen".split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def _words(n: int) -> str:
    """Indian numbering: 1,23,456 -> one lakh twenty-three thousand four hundred fifty-six."""
    def small(x):
        if x < 20:
            return _ONES[x]
        if x < 100:
            return _TENS[x // 10] + ("-" + _ONES[x % 10] if x % 10 else "")
        return _ONES[x // 100] + " hundred" + (" " + small(x % 100) if x % 100 else "")
    if n == 0:
        return "zero"
    parts = []
    for size, name in ((10_000_000, "crore"), (100_000, "lakh"), (1000, "thousand")):
        if n >= size:
            parts.append(_words(n // size) + " " + name)
            n %= size
    if n:
        parts.append(small(n))
    return " ".join(parts)


def rupees(paise: int) -> str:
    r, p = divmod(int(paise), 100)
    s = f"{r:,}"
    if r >= 1000:                                   # Indian grouping: 1,23,456
        head, tail = str(r)[:-3], str(r)[-3:]
        head = re.sub(r"(\d)(?=(\d{2})+$)", r"\1,", head)
        s = f"{head},{tail}"
    return f"₹{s}.{p:02d}"


def describe(q: dict) -> str:
    """One line saying what an order (or quote) is for."""
    if q["kind"] == "credits":
        return f"{q['credits']} credits"
    if q["kind"] == "season":
        return f"Season pack: {q['credits']:,} credits"
    months = q["months"]
    return f"{q['blocks'] * BLOCK_GB} GB extra storage for {months} month{'s' if months > 1 else ''}" + \
        (", auto-renew" if q.get("sub_id") else "")


POWERED_BY = "Powered by Cele-Brat.Tech | info@cele-brat.tech"


def _line_items(o: dict) -> tuple[str, str]:
    if o["kind"] == "credits":
        return f"Auto Edits credits ({o['credits']:,} × {rupees(o['taxable'] // max(1, o['credits']))})", f"{o['credits']:,}"
    if o["kind"] == "season":
        return f"Auto Edits Season pack ({o['credits']:,} credits)", "1"
    what = f"Auto Edits extra storage, {o['blocks'] * BLOCK_GB} GB for {o['months']} month{'s' if o['months'] > 1 else ''}"
    if o.get("sub_id"):
        what += " (auto-renew)"
    return what, f"{o['blocks']} × {o['months']}"


def _tax_rows(o: dict) -> str:
    return (f"<tr><td colspan=3>CGST 9%</td><td>{rupees(o['cgst'])}</td></tr><tr><td colspan=3>SGST 9%</td><td>{rupees(o['sgst'])}</td></tr>"
            if o["cgst"] else f"<tr><td colspan=3>IGST 18%</td><td>{rupees(o['igst'])}</td></tr>")


def _in_words(paise: int) -> str:
    r, p = divmod(paise, 100)
    return (f"Rupees {_words(r)}" + (f" and {_words(p)} paise" if p else "") + " only").capitalize()


_DOC_CSS = """
body { font: 14px/1.5 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; color: #17201f; background: #fff; margin: 0; }
.page { max-width: 760px; margin: 24px auto; padding: 32px; border: 1px solid #cbd3d0; }
h1 { font-size: 22px; margin: 0 0 4px; } .muted { color: #55605e; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin: 22px 0; }
table { width: 100%; border-collapse: collapse; margin-top: 10px; }
th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid #e3e8e6; } td:last-child, th:last-child { text-align: right; }
tr.total td { font-weight: 700; border-top: 2px solid #17201f; }
.print { margin: 16px auto; max-width: 760px; text-align: right; }
.powered { margin-top: 28px; padding-top: 10px; border-top: 1px solid #e3e8e6; font-size: 12px; color: #55605e; }
@media print { .print { display: none; } .page { border: 0; margin: 0; } }
@media (max-width: 600px) { .grid { grid-template-columns: 1fr; } .page { padding: 18px; margin: 0; } }
"""


def _parties(o: dict, seller: dict) -> str:
    import html as h
    buyer = (h.escape(o["buyer_name"]) + "<br>") if o["buyer_name"] else ""
    return f"""<div class="grid">
<div><b>From</b><br>{h.escape(seller['name'])}<br>{h.escape(seller['address']).replace(chr(10), '<br>')}<br>GSTIN {h.escape(seller['gstin'])}<br>{h.escape(seller['email'])}</div>
<div><b>To</b><br>{buyer}{h.escape(o['buyer_email'])}<br>{('GSTIN ' + h.escape(o['buyer_gstin']) + '<br>') if o['buyer_gstin'] else ''}Place of supply: {h.escape(STATES.get(o['state'], o['state']))} ({o['state']})</div>
</div>"""


def credit_note_html(r: dict, seller: dict) -> str:
    """A printable credit note for a refund, with the same GST split as its invoice."""
    import html as h
    made = dt.datetime.fromtimestamp(r["created"], IST)
    what, _ = _line_items({**r, "credits": r["order_credits"]})
    taken = f" {r['credits']:,} credits were taken back from the account." if r["credits"] else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Credit note {h.escape(r['note_no'])}</title><style>{_DOC_CSS}</style></head><body>
<div class="print"><button onclick="print()">Print or save as PDF</button></div>
<div class="page">
<h1>{'TEST refund — not a credit note' if r.get('test') else 'Credit note'}</h1>
<div class="muted">Credit note {h.escape(r['note_no'])} · {made.strftime('%d %b %Y')} · against invoice {h.escape(r['invoice_no'])} of {dt.datetime.fromtimestamp(r['paid'], IST).strftime('%d %b %Y')} · Razorpay refund {h.escape(r['rzp_refund'])}</div>
{_parties(r, seller)}
<table>
<thead><tr><th>Description</th><th>SAC</th><th>Reason</th><th>Amount</th></tr></thead>
<tbody>
<tr><td>Refund: {h.escape(what)}</td><td>{SAC}</td><td>{h.escape(r['reason'])}</td><td>{rupees(r['taxable'])}</td></tr>
<tr><td colspan=3>Taxable value</td><td>{rupees(r['taxable'])}</td></tr>
{_tax_rows(r)}
<tr class="total"><td colspan=3>Total refunded</td><td>{rupees(r['amount'])}</td></tr>
</tbody></table>
<p class="muted">{h.escape(_in_words(r['amount']))}. Sent back to the original payment method through Razorpay (5 to 7 working days).{taken}</p>
<p class="powered">{POWERED_BY}</p>
</div></body></html>"""


def invoice_html(o: dict, seller: dict) -> str:
    import html as h
    paid = dt.datetime.fromtimestamp(o["paid"], IST)
    what, qty = _line_items(o)
    refunded = (f'<p class="muted">Refunded so far: {rupees(o["refunded"])} (see the credit note).</p>'
                if o.get("refunded") else "")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tax invoice {h.escape(o['invoice_no'])}</title><style>{_DOC_CSS}</style></head><body>
<div class="print"><button onclick="print()">Print or save as PDF</button></div>
<div class="page">
<h1>{'TEST payment — not a tax invoice' if o.get('test') else 'Tax invoice'}</h1>
<div class="muted">Invoice {h.escape(o['invoice_no'])} · {paid.strftime('%d %b %Y')} · Payment {h.escape(o['rzp_payment'])}</div>
{_parties(o, seller)}
<table>
<thead><tr><th>Description</th><th>SAC</th><th>Quantity</th><th>Amount</th></tr></thead>
<tbody>
<tr><td>{h.escape(what)}</td><td>{SAC}</td><td>{qty}</td><td>{rupees(o['taxable'])}</td></tr>
<tr><td colspan=3>Taxable value</td><td>{rupees(o['taxable'])}</td></tr>
{_tax_rows(o)}
<tr class="total"><td colspan=3>Total paid</td><td>{rupees(o['total'])}</td></tr>
</tbody></table>
<p class="muted">{h.escape(_in_words(o['total']))}. Paid online via Razorpay. Credits and storage are delivered instantly in the app; see the Refund policy at {h.escape(seller['site'])}/refunds.</p>
{refunded}
<p class="powered">{POWERED_BY}</p>
</div></body></html>"""


def check() -> str:
    """One line for the server status report: never prints the keys themselves."""
    r = from_environment()
    if not r.on:
        return "payments: OFF (no Razorpay keys in deploy/.env)"
    raw = os.environ.get("RAZORPAY_KEY_ID", "")
    if r.test_mode:
        mode = "TEST"
    elif r.key_id.startswith("rzp_live_"):
        mode = "LIVE"
    else:                                   # say what is wrong without showing the key
        looks = ("starts rzp_ but not rzp_live_/rzp_test_" if r.key_id.startswith("rzp_")
                 else "does NOT start with rzp_ (maybe the secret went in the Key id box)")
        mode = (f"UNKNOWN key type (Key id {looks}, {len(r.key_id)} characters; "
                f"a Key id starts rzp_live_ and is about 23 characters)")
    if raw != r.key_id:
        mode += " [stray characters were removed]"
    hook = "webhook secret set" if r.webhook_secret else "NO webhook secret"
    addr = "invoice address set" if os.environ.get("AUTOEDITS_INVOICE_ADDRESS", "Ajmer, Rajasthan") != "Ajmer, Rajasthan" else "invoice address NOT set"
    try:
        r._call("GET", "/orders?count=1")
        ok = "Razorpay accepted the keys"
    except PaymentError as e:
        ok = "Razorpay REJECTED the keys" if "uthentication" in str(e) or "401" in str(e) else f"problem: {e}"
    return f"payments: ON, {mode} mode, {ok}; {hook}; {addr}"


if __name__ == "__main__":
    print(check())
