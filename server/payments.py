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
"""


class PaymentError(Exception):
    pass


def credit_price(now: dt.datetime | None = None) -> int:
    now = now or dt.datetime.now(IST)
    return LAUNCH_PRICE_PAISE if now < LAUNCH_ENDS else PRICE_PAISE


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
    elif kind == "storage":
        if not 1 <= blocks <= MAX_BLOCKS or months not in MONTHS:
            raise PaymentError(f"Choose 1 to {MAX_BLOCKS} blocks of {BLOCK_GB} GB, for 1, 3, 6 or 12 months.")
        unit = BLOCK_PAISE_MONTH
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

    def payment_ok(self, order_id: str, payment_id: str, signature: str) -> bool:
        good = hmac.new(self.secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
        return bool(signature) and hmac.compare_digest(good, signature)

    def webhook_ok(self, body: bytes, signature: str) -> bool:
        if not self.webhook_secret or not signature:
            return False
        good = hmac.new(self.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(good, signature)


def from_environment() -> Razorpay:
    e = os.environ
    return Razorpay(e.get("RAZORPAY_KEY_ID", ""), e.get("RAZORPAY_KEY_SECRET", ""), e.get("RAZORPAY_WEBHOOK_SECRET", ""))


# ---- orders ---------------------------------------------------------------------------
class Payments:
    def __init__(self, store, accounts, razorpay: Razorpay | None = None):
        self.store, self.accounts = store, accounts
        self.rzp = razorpay or from_environment()
        with store._db() as db:
            db.executescript(SCHEMA)

    def _db(self):
        return self.store._db()

    def start(self, user: dict, q: dict, buyer_name: str = "") -> dict:
        """Make the Razorpay order for a checked quote. Returns what Checkout needs."""
        if not self.rzp.on:
            raise PaymentError("Payments are being switched on. Please try again soon.")
        oid = uuid.uuid4().hex[:16]
        what = (f"{q['credits']} credits" if q["kind"] == "credits"
                else f"{q['blocks'] * BLOCK_GB} GB extra storage for {q['months']} month{'s' if q['months'] > 1 else ''}")
        rz = self.rzp.create_order(q["total"], oid, {"account": user["email"], "what": what})
        with self._db() as db:
            db.execute("INSERT INTO orders (id, user_id, kind, credits, blocks, months, taxable, cgst, sgst, igst, total, state, "
                       "buyer_name, buyer_gstin, buyer_email, rzp_order, created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (oid, user["id"], q["kind"], q["credits"], q["blocks"], q["months"], q["taxable"], q["cgst"], q["sgst"],
                        q["igst"], q["total"], q["state"], (buyer_name.strip() or user.get("name") or "")[:120], q["gstin"],
                        user["email"], rz["id"], time.time()))
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
        """Mark paid and deliver, exactly once. Returns the order (None if unknown)."""
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
            invoice = self._next_invoice(db, now)
            db.execute("UPDATE orders SET status='paid', paid=?, rzp_payment=?, invoice_no=? WHERE id=?",
                       (now, rzp_payment, invoice, o["id"]))
            if o["kind"] == "credits":
                db.execute("INSERT INTO ledger (user_id, delta, kind, note, ref, photos, created) VALUES (?,?,?,?,?,?,?)",
                           (o["user_id"], o["credits"], "purchase", f"Bought {o['credits']:,} credits (invoice {invoice})",
                            o["id"], 0, now))
            else:
                start = now
                until = start + o["months"] * 30.4375 * 86400
                db.execute("INSERT INTO storage_grants (user_id, gb, start, until, order_id) VALUES (?,?,?,?,?)",
                           (o["user_id"], o["blocks"] * BLOCK_GB, start, until, o["id"]))
        return self.get(o["id"])

    @staticmethod
    def _next_invoice(db, now: float) -> str:
        d = dt.datetime.fromtimestamp(now, IST)
        fy = d.year if d.month >= 4 else d.year - 1
        prefix = f"{INVOICE_PREFIX}/{fy}-{str(fy + 1)[2:]}/"
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


def invoice_html(o: dict, seller: dict) -> str:
    import html as h
    paid = dt.datetime.fromtimestamp(o["paid"], IST)
    if o["kind"] == "credits":
        what = f"Auto Edits credits ({o['credits']:,} × {rupees(o['taxable'] // max(1, o['credits']))})"
        qty = f"{o['credits']:,}"
    else:
        what = f"Auto Edits extra storage, {o['blocks'] * BLOCK_GB} GB for {o['months']} month{'s' if o['months'] > 1 else ''}"
        qty = f"{o['blocks']} × {o['months']}"
    tax_rows = (f"<tr><td colspan=3>CGST 9%</td><td>{rupees(o['cgst'])}</td></tr><tr><td colspan=3>SGST 9%</td><td>{rupees(o['sgst'])}</td></tr>"
                if o["cgst"] else f"<tr><td colspan=3>IGST 18%</td><td>{rupees(o['igst'])}</td></tr>")
    r, p = divmod(o["total"], 100)
    words = f"Rupees {_words(r)}" + (f" and {_words(p)} paise" if p else "") + " only"
    buyer = (h.escape(o["buyer_name"]) + "<br>") if o["buyer_name"] else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tax invoice {h.escape(o['invoice_no'])}</title>
<style>
body {{ font: 14px/1.5 -apple-system, "Segoe UI", Roboto, Arial, sans-serif; color: #17201f; background: #fff; margin: 0; }}
.page {{ max-width: 760px; margin: 24px auto; padding: 32px; border: 1px solid #cbd3d0; }}
h1 {{ font-size: 22px; margin: 0 0 4px; }} .muted {{ color: #55605e; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin: 22px 0; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
th, td {{ text-align: left; padding: 8px 6px; border-bottom: 1px solid #e3e8e6; }} td:last-child, th:last-child {{ text-align: right; }}
tr.total td {{ font-weight: 700; border-top: 2px solid #17201f; }}
.print {{ margin: 16px auto; max-width: 760px; text-align: right; }}
@media print {{ .print {{ display: none; }} .page {{ border: 0; margin: 0; }} }}
@media (max-width: 600px) {{ .grid {{ grid-template-columns: 1fr; }} .page {{ padding: 18px; margin: 0; }} }}
</style></head><body>
<div class="print"><button onclick="print()">Print or save as PDF</button></div>
<div class="page">
<h1>Tax invoice</h1>
<div class="muted">Invoice {h.escape(o['invoice_no'])} · {paid.strftime('%d %b %Y')} · Payment {h.escape(o['rzp_payment'])}</div>
<div class="grid">
<div><b>From</b><br>{h.escape(seller['name'])}<br>{h.escape(seller['address']).replace(chr(10), '<br>')}<br>GSTIN {h.escape(seller['gstin'])}<br>{h.escape(seller['email'])}</div>
<div><b>To</b><br>{buyer}{h.escape(o['buyer_email'])}<br>{('GSTIN ' + h.escape(o['buyer_gstin']) + '<br>') if o['buyer_gstin'] else ''}Place of supply: {h.escape(STATES.get(o['state'], o['state']))} ({o['state']})</div>
</div>
<table>
<thead><tr><th>Description</th><th>SAC</th><th>Quantity</th><th>Amount</th></tr></thead>
<tbody>
<tr><td>{h.escape(what)}</td><td>{SAC}</td><td>{qty}</td><td>{rupees(o['taxable'])}</td></tr>
<tr><td colspan=3>Taxable value</td><td>{rupees(o['taxable'])}</td></tr>
{tax_rows}
<tr class="total"><td colspan=3>Total paid</td><td>{rupees(o['total'])}</td></tr>
</tbody></table>
<p class="muted">{h.escape(words.capitalize())}. Paid online via Razorpay. Credits and storage are delivered instantly in the app; see the Refund policy at {h.escape(seller['site'])}/refunds.</p>
</div></body></html>"""
