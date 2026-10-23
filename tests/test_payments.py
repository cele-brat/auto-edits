"""Buying credits and storage: prices, GST, Razorpay signatures, webhook, invoices, storage grants."""
import datetime as dt
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from server import limits, payments as pay
from server.main import create_app
from test_accounts import KEY, ADMIN, Mailbox, send, sign_up


class FakeRazorpay(pay.Razorpay):
    def __init__(self):
        super().__init__("rzp_test_abc", "secret123", "hooksecret")
        self.orders = []

    def create_order(self, amount, receipt, notes):
        o = {"id": f"order_{len(self.orders) + 1}", "amount": amount}
        self.orders.append(o)
        return o

    def sign(self, order_id, payment_id):
        return hmac.new(b"secret123", f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


@pytest.fixture
def rzp():
    return FakeRazorpay()


@pytest.fixture
def box():
    return Mailbox()


@pytest.fixture
def app(tmp_path, box, rzp):
    return create_app(str(tmp_path), KEY, admin_email=ADMIN, mailer=box, razorpay=rzp)


def test_prices_and_gst():
    jan = dt.datetime(2027, 1, 1, tzinfo=pay.IST)
    q = pay.quote("credits", credits=500, state="27")                       # Maharashtra: IGST
    assert (q["taxable"], q["igst"], q["cgst"], q["total"]) == (50_000, 9_000, 0, 59_000)
    q = pay.quote("credits", credits=600, state="08")                       # Rajasthan: CGST + SGST
    assert (q["cgst"], q["sgst"], q["igst"], q["total"]) == (5_400, 5_400, 0, 70_800)
    assert pay.quote("credits", credits=500, state="07", now=jan)["taxable"] == 150_000      # Rs 3 from 2027
    q = pay.quote("storage", blocks=2, months=3, state="06")
    assert (q["taxable"], q["total"]) == (90_000, 106_200)                  # 2 x 3 x Rs 150 + 18%
    assert pay.quote("credits", credits=500, gstin="08ABCDE1234F1Z5")["state"] == "08"   # state from the GSTIN
    for bad in (dict(credits=400), dict(credits=550), dict(credits=500, state="99")):
        with pytest.raises(pay.PaymentError):
            pay.quote("credits", **{"state": "08", **bad})
    with pytest.raises(pay.PaymentError):
        pay.quote("credits", credits=500, gstin="NOT-A-GSTIN")
    with pytest.raises(pay.PaymentError):
        pay.quote("storage", blocks=1, months=2, state="08")
    assert pay.rupees(12_345_678) == "₹1,23,456.78"
    assert pay._words(123_456) == "one lakh twenty-three thousand four hundred fifty-six"


def test_buy_credits_through_checkout(app, box, rzp):
    c = sign_up(app, box, "a@example.com")
    assert c.get("/api/buy/prices").json()["on"]
    r = c.post("/api/buy", json={"kind": "credits", "credits": 700, "state": "27"})
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["amount"] == 82_600 and o["key"] == "rzp_test_abc" and rzp.orders[-1]["amount"] == 82_600
    bad = {"razorpay_order_id": o["rzp_order"], "razorpay_payment_id": "pay_1", "razorpay_signature": "nope"}
    assert c.post("/api/buy/verify", json=bad).status_code == 400
    assert c.get("/api/me").json()["credits"] == 50
    good = {**bad, "razorpay_signature": rzp.sign(o["rzp_order"], "pay_1")}
    done = c.post("/api/buy/verify", json=good).json()
    assert done["credits"] == 750 and done["invoice"].startswith("TEST/20") and done["invoice"].endswith("/00001")
    assert c.post("/api/buy/verify", json=good).json()["credits"] == 750           # only once
    h = c.get("/api/credits").json()["history"][0]
    assert h["kind"] == "purchase" and h["delta"] == 700
    inv = c.get(f"/invoice/{o['order']}")
    assert inv.status_code == 200 and "Tax invoice" in inv.text and "IGST 18%" in inv.text and "08ARTPK7888A1Z5" in inv.text
    assert "₹826.00" in inv.text and "998383" in inv.text and "Maharashtra" in inv.text
    other = sign_up(app, box, "b@example.com")
    assert other.get(f"/invoice/{o['order']}").status_code == 404                   # not someone else's
    admin = TestClient(app); admin.post("/api/login", json={"key": KEY})
    assert admin.get(f"/invoice/{o['order']}").status_code == 200
    assert admin.get("/api/admin/purchases").json()["purchases"][0]["total"] == 82_600


def test_webhook_delivers_when_the_tab_was_closed(app, box, rzp):
    c = sign_up(app, box, "a@example.com")
    o = c.post("/api/buy", json={"kind": "credits", "credits": 500, "state": "08"}).json()
    event = {"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": "pay_9", "order_id": o["rzp_order"], "amount": o["amount"], "status": "captured"}}}}
    raw = json.dumps(event).encode()
    w = TestClient(app)
    assert w.post("/api/razorpay/webhook", content=raw, headers={"X-Razorpay-Signature": "forged"}).status_code == 400
    sig = hmac.new(b"hooksecret", raw, hashlib.sha256).hexdigest()
    assert w.post("/api/razorpay/webhook", content=raw, headers={"X-Razorpay-Signature": sig}).status_code == 200
    assert w.post("/api/razorpay/webhook", content=raw, headers={"X-Razorpay-Signature": sig}).status_code == 200
    assert c.get("/api/me").json()["credits"] == 550                                # delivered once
    p = c.get("/api/purchases").json()["purchases"]
    assert len(p) == 1 and p[0]["invoice_no"].endswith("/00001")
    o2 = c.post("/api/buy", json={"kind": "credits", "credits": 500, "state": "08"}).json()
    c.post("/api/buy/verify", json={"razorpay_order_id": o2["rzp_order"], "razorpay_payment_id": "pay_10",
                                     "razorpay_signature": rzp.sign(o2["rzp_order"], "pay_10")})
    assert c.get("/api/purchases").json()["purchases"][0]["invoice_no"].endswith("/00002")   # numbers follow on


def test_buying_storage_raises_the_limit(app, box, rzp, monkeypatch):
    monkeypatch.setattr(limits, "GB", 100)
    monkeypatch.setattr(limits, "STORAGE_GB", 1)
    monkeypatch.setattr(pay, "BLOCK_GB", 50)
    c = sign_up(app, box, "a@example.com")
    w = c.post("/api/weddings", json={"name": "W"}).json()
    r = send(c, w["id"], "u0")
    assert r.status_code == 403 and "Add 50 GB more" in r.json()["detail"]
    o = c.post("/api/buy", json={"kind": "storage", "blocks": 1, "months": 1, "state": "08"}).json()
    c.post("/api/buy/verify", json={"razorpay_order_id": o["rzp_order"], "razorpay_payment_id": "pay_s",
                                    "razorpay_signature": rzp.sign(o["rzp_order"], "pay_s")})
    st = c.get("/api/me").json()["storage"]
    assert st["extra_gb"] == 50 and st["storage_gb"] == 51
    assert send(c, w["id"], "u0").status_code == 200
    assert c.get("/api/buy/prices").json()["grants"][0]["gb"] == 50


def test_payments_off_without_keys(tmp_path, box):
    app = create_app(str(tmp_path), KEY, admin_email=ADMIN, mailer=box, razorpay=pay.Razorpay("", "", ""))
    c = sign_up(app, box, "a@example.com")
    assert not c.get("/api/buy/prices").json()["on"] and not c.get("/api/me").json()["payments_on"]
    r = c.post("/api/buy", json={"kind": "credits", "credits": 500, "state": "08"})
    assert r.status_code == 503 and "switched on" in r.json()["detail"]


def test_unconfirmed_and_view_as_cannot_buy(app, box):
    c = TestClient(app)
    c.post("/api/signup", json={"email": "n@example.com", "password": "a good password", "agree": True})
    assert c.post("/api/buy", json={"kind": "credits", "credits": 500, "state": "08"}).status_code == 401   # not signed in yet
    c.post("/api/login", json={"email": "n@example.com", "password": "a good password"})
    assert c.post("/api/buy", json={"kind": "credits", "credits": 500, "state": "08"}).status_code == 403


def test_test_payments_never_take_real_invoice_numbers(tmp_path, box):
    test_rzp, live_rzp = FakeRazorpay(), FakeRazorpay()
    live_rzp.key_id = "rzp_live_abc"
    live_rzp.orders = [None] * 100                                    # real Razorpay ids never repeat
    app = create_app(str(tmp_path), KEY, admin_email=ADMIN, mailer=box, razorpay=test_rzp)
    c = sign_up(app, box, "a@example.com")
    o = c.post("/api/buy", json={"kind": "credits", "credits": 500, "state": "08"}).json()
    t = c.post("/api/buy/verify", json={"razorpay_order_id": o["rzp_order"], "razorpay_payment_id": "pay_t",
                                        "razorpay_signature": test_rzp.sign(o["rzp_order"], "pay_t")}).json()
    assert t["invoice"].startswith("TEST/") and t["invoice"].endswith("/00001")
    assert "not a tax invoice" in c.get(f"/invoice/{o['order']}").text
    app.state.payments = None
    live = create_app(str(tmp_path), KEY, admin_email=ADMIN, mailer=box, razorpay=live_rzp)    # keys switched to live
    c2 = TestClient(live); c2.cookies = c.cookies
    o2 = c2.post("/api/buy", json={"kind": "credits", "credits": 500, "state": "08"}).json()
    r = c2.post("/api/buy/verify", json={"razorpay_order_id": o2["rzp_order"], "razorpay_payment_id": "pay_l",
                                         "razorpay_signature": live_rzp.sign(o2["rzp_order"], "pay_l")}).json()
    assert r["invoice"].startswith("AE/") and r["invoice"].endswith("/00001")                 # first real invoice is 00001
    assert "Tax invoice" in c2.get(f"/invoice/{o2['order']}").text
