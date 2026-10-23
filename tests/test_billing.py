"""Payments phase 2 and hub rules: retried payments, the Season pack, auto-renew storage, refunds and credit notes,
invoice emails with attachments, storage reminders, low-credit warnings, prices from the settings table, the
footer line and BCC on emails, and bulk preset import."""
import hashlib
import hmac
import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from server import mail, nudges, payments as pay
from server.main import create_app
from test_accounts import KEY, ADMIN, sign_up
from test_payments import FakeRazorpay as _Fake


class FakeRazorpay(_Fake):
    """Plans, subscriptions and refunds too."""
    def __init__(self):
        super().__init__()
        self.plans, self.subs, self.refunds, self.cancelled = [], [], [], []

    def create_plan(self, amount, name, notes):
        p = {"id": f"plan_{len(self.plans) + 1}", "amount": amount, "name": name}
        self.plans.append(p)
        return p

    def create_subscription(self, plan_id, notes, total_count=120):
        s = {"id": f"sub_{len(self.subs) + 1}", "plan_id": plan_id}
        self.subs.append(s)
        return s

    def cancel_subscription(self, sub_id, at_cycle_end=True):
        self.cancelled.append((sub_id, at_cycle_end))
        return {"id": sub_id, "status": "cancelled"}

    def refund(self, payment_id, amount, notes):
        r = {"id": f"rfnd_{len(self.refunds) + 1}", "payment_id": payment_id, "amount": amount}
        self.refunds.append(r)
        return r

    def sign_sub(self, sub_id, payment_id):
        return hmac.new(b"secret123", f"{payment_id}|{sub_id}".encode(), hashlib.sha256).hexdigest()


class Box:
    """A mailer that keeps attachments and the BCC choice."""
    def __init__(self):
        self.sent = []

    def send(self, to, subject, text, html="", wait=False, attachments=None, bcc=None):
        self.sent.append({"to": to, "subject": subject, "text": text, "html": html, "attachments": attachments or [], "bcc": bcc})

    def link(self, to, kind):
        import re
        for m in reversed(self.sent):
            if m["to"] == to:
                found = re.search(rf"https?://[^\s]+/{kind}/([\w-]+)", m["text"])
                if found:
                    return found.group(1)
        raise AssertionError(f"no {kind} email to {to}")


@pytest.fixture
def rzp():
    return FakeRazorpay()


@pytest.fixture
def box():
    return Box()


@pytest.fixture
def app(tmp_path, box, rzp, monkeypatch):
    monkeypatch.setenv("AUTOEDITS_NO_PDF", "1")             # the HTML file is attached: no wkhtmltopdf needed here
    return create_app(str(tmp_path), KEY, admin_email=ADMIN, mailer=box, razorpay=rzp)


def admin_client(app):
    a = TestClient(app)
    a.post("/api/login", json={"key": KEY})
    return a


def webhook(app, event):
    raw = json.dumps(event).encode()
    sig = hmac.new(b"hooksecret", raw, hashlib.sha256).hexdigest()
    return TestClient(app).post("/api/razorpay/webhook", content=raw, headers={"X-Razorpay-Signature": sig})


def pay_order(c, rzp, o, payment="pay_1"):
    return c.post("/api/buy/verify", json={"razorpay_order_id": o["rzp_order"], "razorpay_payment_id": payment,
                                            "razorpay_signature": rzp.sign(o["rzp_order"], payment)})


# ---- 1c. a failed then retried payment fulfils once --------------------------------------------
def test_failed_then_retried_payment_fulfils_once(app, box, rzp):
    c = sign_up(app, box, "a@example.com")
    o = c.post("/api/buy", json={"kind": "credits", "credits": 500, "state": "08"}).json()
    failed = {"event": "payment.failed", "payload": {"payment": {"entity": {
        "id": "pay_bad", "order_id": o["rzp_order"], "amount": o["amount"], "status": "failed"}}}}
    assert webhook(app, failed).status_code == 200
    assert c.get("/api/me").json()["credits"] == 50                                   # nothing paid
    assert c.get("/api/purchases").json()["purchases"] == []
    # the retry: same order, a new payment id, Checkout and the webhook both report it
    assert pay_order(c, rzp, o, "pay_good").status_code == 200
    captured = {"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": "pay_good", "order_id": o["rzp_order"], "amount": o["amount"], "status": "captured"}}}}
    assert webhook(app, captured).status_code == 200
    assert webhook(app, captured).status_code == 200
    assert c.get("/api/me").json()["credits"] == 550
    p = c.get("/api/purchases").json()["purchases"]
    assert len(p) == 1 and p[0]["invoice_no"].endswith("/00001")
    h = [x for x in c.get("/api/credits").json()["history"] if x["kind"] == "purchase"]
    assert len(h) == 1                                                                 # credits granted once
    # a captured event for the bad payment id after the good one changes nothing either
    late = {"event": "payment.captured", "payload": {"payment": {"entity": {
        "id": "pay_bad", "order_id": o["rzp_order"], "amount": o["amount"], "status": "captured"}}}}
    assert webhook(app, late).status_code == 200
    assert c.get("/api/me").json()["credits"] == 550


# ---- 1a, 1b, 3c. emails: footer line, BCC, invoice attached -----------------------------------
def test_emails_carry_the_footer_and_bcc_but_not_to_the_studio():
    m = mail.Mailer({"AUTOEDITS_ADMIN_EMAIL": "studio@example.com"})
    m.send("ravi@example.com", "Hi", "hello", "<div><p>hello</p></div>")
    msg = mail.SENT[-1]
    assert msg["Bcc"] == "celebratweddings@gmail.com"
    assert mail.POWERED_BY in msg.get_body(("plain",)).get_content()
    assert mail.POWERED_BY in msg.get_body(("html",)).get_content()
    m.send("studio@example.com", "Health", "all fine")
    assert mail.SENT[-1]["Bcc"] is None and mail.POWERED_BY in mail.SENT[-1].get_content()
    off = mail.Mailer({"AUTOEDITS_BCC": ""})
    off.send("ravi@example.com", "Hi", "hello")
    assert mail.SENT[-1]["Bcc"] is None
    other = mail.Mailer({"AUTOEDITS_BCC": "books@example.com"})
    other.send("ravi@example.com", "Hi", "hello")
    assert mail.SENT[-1]["Bcc"] == "books@example.com"
    for kind in (mail.verify_email("https://x", "t"), mail.storage_ending("https://x", 50, "1 May", True, "₹177.00"),
                 mail.low_credits("https://x", 12, "Credits cost ₹1.")):
        assert mail.POWERED_BY in kind[1] and mail.POWERED_BY in kind[2]


def test_invoice_is_emailed_with_an_attachment_when_fulfilled(app, box, rzp):
    c = sign_up(app, box, "a@example.com")
    o = c.post("/api/buy", json={"kind": "credits", "credits": 500, "state": "27"}).json()
    before = len(box.sent)
    pay_order(c, rzp, o)
    m = [x for x in box.sent[before:] if "invoice" in x["subject"].lower()]
    assert len(m) == 1 and m[0]["to"] == "a@example.com"
    name, data, mime = m[0]["attachments"][0]
    assert name.startswith("Auto Edits invoice TEST-20") and mime in ("application/pdf", "text/html")
    assert b"IGST 18%" in data and f"/invoice/{o['order']}" in m[0]["text"]
    pay_order(c, rzp, o)                                                              # a second verify: no second email
    assert len([x for x in box.sent if "invoice" in x["subject"].lower()]) == 1


def test_invoice_pdf_when_wkhtmltopdf_is_there(monkeypatch):
    from server import billing
    monkeypatch.delenv("AUTOEDITS_NO_PDF", raising=False)
    pdf = billing.html_to_pdf("<html><body><h1>Tax invoice</h1></body></html>")
    if pdf is None:                                                                   # no wkhtmltopdf on this machine
        name, data, mime = billing.attachment_for("x", "<html>hi</html>")
        assert name == "x.html" and mime == "text/html"
    else:
        assert pdf.startswith(b"%PDF")
        assert billing.attachment_for("x", "<html>hi</html>")[0] == "x.pdf"


# ---- 5, 6. the Season pack and prices without code --------------------------------------------
def test_season_pack_and_live_prices(app, box, rzp):
    c = sign_up(app, box, "a@example.com")
    p = c.get("/api/billing/prices").json()
    assert p["season_credits"] == 5000 and p["season_paise"] == 400_000 and p["credit_paise"] == 100
    q = c.post("/api/buy/quote", json={"kind": "season", "state": "27"}).json()
    assert q["credits"] == 5000 and q["taxable"] == 400_000 and q["total"] == 472_000
    o = c.post("/api/buy", json={"kind": "season", "state": "27"}).json()
    assert o["amount"] == 472_000 and "Season pack" in o["what"]
    pay_order(c, rzp, o, "pay_s")
    assert c.get("/api/me").json()["credits"] == 5050
    inv = c.get(f"/invoice/{o['order']}").text
    assert "Season pack" in inv and "5,000 credits" in inv and "Powered by Cele-Brat.Tech" in inv
    # the public pricing feed, for the home page
    pub = TestClient(app).get("/api/prices")
    assert pub.status_code == 200 and pub.json()["season_paise"] == 400_000 and "settings" not in pub.json()
    # after the launch price ends: Rs 3 and the 10,000 pack
    jan = pay.dt.datetime(2027, 1, 2, tzinfo=pay.IST)
    later = pay.prices(jan)
    assert later["credit_paise"] == 300 and later["season_credits"] == 10_000 and later["season_paise"] == 2_750_000
    assert pay.quote("season", state="08", now=jan)["taxable"] == 2_750_000
    # the admin changes prices without code; the change is logged and live at once
    a = admin_client(app)
    assert a.get("/api/admin/prices").json()["settings"]["credit_price_paise"] == "100"
    r = a.post("/api/admin/prices", json={"credit_price_paise": "150", "season_pack_paise": "350000", "launch_price_until": "2027-03-31"})
    assert r.status_code == 200 and set(r.json()["changed"]) == {"credit_price_paise", "season_pack_paise", "launch_price_until"}
    assert a.post("/api/admin/prices", json={"launch_price_until": "soon"}).status_code == 422
    assert a.post("/api/admin/prices", json={"credit_price_paise": "-5"}).status_code == 422
    p = c.get("/api/billing/prices").json()
    assert p["credit_paise"] == 150 and p["season_paise"] == 350_000 and p["launch_ends"] == "31 March 2027"
    assert pay.prices(jan)["credit_paise"] == 150                                  # launch runs to March now
    assert c.post("/api/buy/quote", json={"kind": "credits", "credits": 500, "state": "08"}).json()["taxable"] == 75_000
    assert TestClient(app).get("/api/prices").json()["credit_paise"] == 150
    log = a.get("/api/admin/activity").json()["activity"][0]
    assert log["action"] == "prices changed" and "credit_price_paise: 100 → 150" in log["detail"]
    assert sign_up(app, box, "b@example.com").get("/api/admin/prices").status_code == 404


# ---- 3a. auto-renew storage ---------------------------------------------------------------------
def test_auto_renew_storage_with_subscriptions(app, box, rzp):
    c = sign_up(app, box, "a@example.com")
    r = c.post("/api/storage/subscribe", json={"blocks": 2, "state": "08"})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["subscription_id"] == "sub_1" and s["amount"] == 35_400 and rzp.plans[0]["amount"] == 35_400
    assert c.post("/api/storage/subscribe", json={"blocks": 1, "state": "08"}).status_code == 422   # one at a time
    # Checkout reports the first charge
    bad = {"razorpay_subscription_id": "sub_1", "razorpay_payment_id": "pay_m1", "razorpay_signature": "x"}
    assert c.post("/api/storage/subscribe/verify", json=bad).status_code == 400
    good = {**bad, "razorpay_signature": rzp.sign_sub("sub_1", "pay_m1")}
    done = c.post("/api/storage/subscribe/verify", json=good).json()
    assert done["invoice"].endswith("/00001") and done["storage"]["extra_gb"] == 100
    # the webhook for the same charge changes nothing; a second month adds a grant that follows on
    charged = lambda pid, at: {"event": "subscription.charged", "payload": {
        "subscription": {"entity": {"id": "sub_1", "status": "active", "charge_at": at}},
        "payment": {"entity": {"id": pid, "amount": 35_400, "status": "captured"}}}}
    assert webhook(app, charged("pay_m1", time.time() + 30 * 86400)).status_code == 200
    assert len(c.get("/api/purchases").json()["purchases"]) == 1
    assert webhook(app, charged("pay_m2", time.time() + 60 * 86400)).status_code == 200
    p = c.get("/api/purchases").json()
    assert len(p["purchases"]) == 2 and p["purchases"][0]["sub_id"] and p["purchases"][0]["invoice_no"].endswith("/00002")
    grants = p["grants"]
    assert len(grants) == 2 and grants[1]["start"] == pytest.approx(grants[0]["until"], abs=1)
    assert "auto-renew" in c.get(f"/invoice/{p['purchases'][0]['id']}").text
    subs = c.get("/api/billing/prices").json()["subscriptions"]
    assert subs[0]["status"] == "active" and subs[0]["charges"] == 2
    # a mismatched amount is refused
    wrong = charged("pay_m3", 0)
    wrong["payload"]["payment"]["entity"]["amount"] = 100
    assert webhook(app, wrong).status_code == 400
    # cancel: at the end of the paid month; the grants stay
    r = c.post("/api/storage/cancel", json={})
    assert r.status_code == 200 and r.json()["status"] == "cancelling" and rzp.cancelled == [("sub_1", True)]
    assert c.get("/api/me").json()["storage"]["extra_gb"] == 100
    assert webhook(app, {"event": "subscription.cancelled", "payload": {"subscription": {"entity": {"id": "sub_1"}}}}).status_code == 200
    assert c.get("/api/billing/prices").json()["subscriptions"] == []
    assert c.post("/api/storage/cancel", json={}).status_code == 404
    # prepaid storage still works beside it
    o = c.post("/api/buy", json={"kind": "storage", "blocks": 1, "months": 3, "state": "08"}).json()
    pay_order(c, rzp, o, "pay_pre")
    assert c.get("/api/me").json()["storage"]["extra_gb"] == 150
    # a halted subscription shows as such
    c2 = sign_up(app, box, "b@example.com")
    c2.post("/api/storage/subscribe", json={"blocks": 1, "state": "08"})
    assert webhook(app, {"event": "subscription.halted", "payload": {"subscription": {"entity": {"id": "sub_2"}}}}).status_code == 200
    assert c2.get("/api/billing/prices").json()["subscriptions"][0]["status"] == "halted"


# ---- 3b. refunds and credit notes ---------------------------------------------------------------
def test_admin_refund_makes_a_credit_note_and_takes_credits_back(app, box, rzp):
    c = sign_up(app, box, "a@example.com")
    o = c.post("/api/buy", json={"kind": "credits", "credits": 1000, "state": "27"}).json()        # 1,000 x Rs 1 + 18% = 118,000
    pay_order(c, rzp, o)
    a = admin_client(app)
    assert c.post(f"/api/admin/purchases/{o['order']}/refund", json={"amount": 1, "reason": "x"}).status_code == 404
    assert a.post(f"/api/admin/purchases/{o['order']}/refund", json={"amount": 5900, "reason": ""}).status_code == 422
    r = a.post(f"/api/admin/purchases/{o['order']}/refund", json={"amount": 59_000, "reason": "Half not needed"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["note"] == "TEST/CN/2026-27/00001" and d["credits"] == 500 and d["balance"] == 550
    assert rzp.refunds[0] == {"id": "rfnd_1", "payment_id": "pay_1", "amount": 59_000}
    h = c.get("/api/credits").json()["history"][0]
    assert h["kind"] == "refund" and h["delta"] == -500 and "TEST/CN/2026-27/00001" in h["note"]
    note = c.get(f"/credit-note/{d['refund']}")
    assert note.status_code == 200 and "Credit note" in note.text and "IGST 18%" in note.text and "₹590.00" in note.text
    assert "₹90.00" in note.text and "₹500.00" in note.text and "Half not needed" in note.text and "Powered by Cele-Brat.Tech" in note.text
    assert sign_up(app, box, "b@example.com").get(f"/credit-note/{d['refund']}").status_code == 404
    assert "Refunded so far: ₹590.00" in c.get(f"/invoice/{o['order']}").text
    cn = [m for m in box.sent if "credit note" in m["subject"].lower()]
    assert len(cn) == 1 and cn[0]["to"] == "a@example.com" and b"Credit note" in cn[0]["attachments"][0][1]
    assert c.get("/api/refunds").json()["refunds"][0]["note_no"] == "TEST/CN/2026-27/00001"
    # the rest: credits are spent beyond what is refundable -> refused with a message
    c.post("/api/weddings", json={"name": "W"})
    app.state.accounts.add(c.get("/api/me").json()["user"]["id"], -300, "export", "Export")      # 250 left of 500 sold
    info = a.get(f"/api/admin/purchases/{o['order']}/refunds").json()
    assert info["refundable"] == {"money": 59_000, "credits": 500, "balance": 250, "spent": 250}
    r = a.post(f"/api/admin/purchases/{o['order']}/refund", json={"amount": 59_000, "reason": "Rest"})
    assert r.status_code == 422 and "250" in r.json()["detail"]
    r = a.post(f"/api/admin/purchases/{o['order']}/refund", json={"amount": 29_500, "reason": "A quarter"})
    assert r.status_code == 200 and r.json()["note"].endswith("/00002") and r.json()["credits"] == 250 and r.json()["balance"] == 0
    r = a.post(f"/api/admin/purchases/{o['order']}/refund", json={"amount": 29_500, "reason": "More"})
    assert r.status_code == 422 and "0 of these credits" in r.json()["detail"]            # never below 0 credits
    assert a.post(f"/api/admin/purchases/{o['order']}/refund", json={"amount": 100_000, "reason": "Too much"}).status_code == 422
    assert a.get("/api/admin/activity").json()["activity"][0]["action"] == "refund made"
    assert a.get("/api/admin/purchases").json()["purchases"][0]["refunded"] == 88_500
    # Rajasthan buyer: CGST + SGST split on the note; real keys give CN/ numbers
    o2 = c.post("/api/buy", json={"kind": "credits", "credits": 500, "state": "08"}).json()
    pay_order(c, rzp, o2, "pay_2")
    r = a.post(f"/api/admin/purchases/{o2['order']}/refund", json={"reason": "Everything"}).json()
    assert r["amount"] == 59_000 and r["credits"] == 500
    assert "CGST 9%" in c.get(f"/credit-note/{r['refund']}").text
    with app.state.store._db() as db:
        assert pay.Payments._next_note(db, time.time(), test=False) == "CN/2026-27/00001"


# ---- 3d, 4. reminders: storage ending, renewal due, low credits ---------------------------------
def test_storage_and_low_credit_reminders(app, box, rzp):
    c = sign_up(app, box, "a@example.com")
    uid = c.get("/api/me").json()["user"]["id"]
    o = c.post("/api/buy", json={"kind": "storage", "blocks": 1, "months": 1, "state": "08"}).json()
    pay_order(c, rzp, o, "pay_st")
    n = nudges.Nudges(app.state.accounts, box, "https://example.test")
    now = time.time()
    assert n.storage_and_credits(now)["storage"] == 0                                    # 30 days to go
    assert n.storage_and_credits(now + 24 * 86400)["storage"] == 1                       # 6 days to go
    assert n.storage_and_credits(now + 25 * 86400)["storage"] == 0                       # once
    m = box.sent[-1]
    assert "50 GB extra storage ends on" in m["subject"] and "account" in m["text"]
    # auto-renew: 3 days before the next charge, once per charge
    c.post("/api/storage/subscribe", json={"blocks": 2, "state": "08"})
    webhook(app, {"event": "subscription.charged", "payload": {
        "subscription": {"entity": {"id": "sub_1", "status": "active", "charge_at": now + 10 * 86400}},
        "payment": {"entity": {"id": "pay_r1", "amount": 35_400, "status": "captured"}}}})
    assert n.storage_and_credits(now + 5 * 86400)["renew"] == 0
    d = n.storage_and_credits(now + 8 * 86400)
    assert d["renew"] == 1 and d["storage"] == 0                                         # renewing: no "ends" email
    assert "renews on" in box.sent[-1]["subject"] and "₹354.00" in box.sent[-1]["text"]
    assert n.storage_and_credits(now + 9 * 86400)["renew"] == 0
    # low credits: 550 after a purchase of 500 -> not low; spend to 40 -> one email in 7 days with a Buy link
    o2 = c.post("/api/buy", json={"kind": "credits", "credits": 500, "state": "08"}).json()
    pay_order(c, rzp, o2, "pay_c")
    assert n.storage_and_credits(now)["low"] == 0
    app.state.accounts.add(uid, -510, "export", "Export")
    assert n.storage_and_credits(now)["low"] == 1
    assert "Only 40" in box.sent[-1]["subject"] and "account=1" in box.sent[-1]["text"] and "Season pack" in box.sent[-1]["text"]
    assert n.storage_and_credits(now + 3 * 86400)["low"] == 0
    assert n.storage_and_credits(now + 8 * 86400)["low"] == 1
    # 10% of the last purchase when that is more than 50
    assert nudges.is_low(400, 5000) and not nudges.is_low(600, 5000) and nudges.is_low(49, 0) and not nudges.is_low(50, 0)
    # the hourly run includes these
    n.last = 0
    assert "low" in n.run_if_due(now + 20 * 86400)


# ---- 2. bulk preset import ----------------------------------------------------------------------
XMP = """<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description rdf:about="" xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/" crs:PresetType="Normal"
 crs:UUID="{uuid}" crs:Exposure2012="{ev}" crs:Contrast2012="10" crs:Name="{name}" crs:Group="{group}"/>
</rdf:RDF></x:xmpmeta>"""


def xmp(name, ev=0.5, group="G"):
    return XMP.format(uuid=name.upper().replace(" ", "") + "0000", ev=ev, name=name, group=group).encode()


def test_bulk_import_folder_and_zip(app, box):
    c = sign_up(app, box, "a@example.com")
    files = [("files", ("a.xmp", xmp("Warm 1"), "application/octet-stream")), ("files", ("b.xmp", xmp("Warm 2"), "application/octet-stream")),
             ("files", ("c.xmp", xmp("Cool 1"), "application/octet-stream")), ("files", ("d.xmp", b"not a preset", "application/octet-stream")),
             ("files", ("e.txt", b"x", "text/plain"))]
    paths = ["Golden Hour - Presets/a.xmp", "Golden Hour - Presets/Soft/b.xmp", "Blue Hour/c.xmp", "Blue Hour/d.xmp", "Blue Hour/e.txt"]
    r = c.post("/api/presets/bulk", files=files, data={"paths": json.dumps(paths)})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["added"] == 3 and d["packs"] == {"Golden Hour": 1, "Golden Hour / Soft": 1, "Blue Hour": 1}
    assert {s["file"] for s in d["skipped"]} == {"d.xmp", "e.txt"}
    mine = [p for p in c.get("/api/presets").json()["presets"] if p["mine"]]
    assert {p["pack"] for p in mine} == {"Golden Hour", "Golden Hour / Soft", "Blue Hour"}
    assert c.get(f"/api/presets/{mine[0]['id']}/xmp").status_code == 200              # the file is kept, as for single uploads
    # a zip: unpacked on the server; traversal and non-xmp entries are dropped; same file again = replaced
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Vintage Pack/Film/f1.xmp", xmp("Film 1"))
        z.writestr("Vintage Pack/Film/f2.xmp", xmp("Film 2"))
        z.writestr("Vintage Pack/readme.txt", "hi")
        z.writestr("../../etc/evil.xmp", xmp("Evil"))
        z.writestr("__MACOSX/Vintage Pack/._f1.xmp", b"junk")
        z.writestr("Blue Hour/c.xmp", xmp("Cool 1", ev=0.9))
    r = c.post("/api/presets/bulk", files=[("files", ("pack.zip", buf.getvalue(), "application/zip"))])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["added"] == 2 and d["replaced"] == 1 and d["packs"] == {"Vintage Pack / Film": 2, "Blue Hour": 1}
    assert any("Unsafe" in s["why"] for s in d["skipped"])
    mine = [p for p in c.get("/api/presets").json()["presets"] if p["mine"]]
    assert len(mine) == 5
    # nobody else sees them; the admin sees only its own
    other = sign_up(app, box, "b@example.com")
    assert not [p for p in other.get("/api/presets").json()["presets"] if p["mine"]]
    # a loose .xmp with no folder lands in My presets; a bad zip is reported
    r = c.post("/api/presets/bulk", files=[("files", ("loose.xmp", xmp("Loose"), "application/octet-stream")),
                                            ("files", ("bad.zip", b"PK-not-really", "application/zip"))]).json()
    assert r["packs"] == {"My presets": 1} and r["skipped"][0]["why"] == "This is not a zip file."


def test_bulk_import_respects_the_account_limit(app, box, monkeypatch):
    from server import limits
    monkeypatch.setattr(limits, "PRESETS_PER_ACCOUNT", 2)
    c = sign_up(app, box, "a@example.com")
    files = [("files", (f"p{i}.xmp", xmp(f"P {i}"), "application/octet-stream")) for i in range(4)]
    d = c.post("/api/presets/bulk", files=files, data={"paths": json.dumps([f"Pack/p{i}.xmp" for i in range(4)])}).json()
    assert d["added"] == 2 and d["skipped_total"] == 2 and "most one account can keep" in d["skipped"][0]["why"]


def test_bulk_helpers():
    from server import billing
    assert billing.pack_of("Golden Hour - Presets/Soft/Deep/x.xmp") == "Golden Hour / Soft"
    assert billing.pack_of("x.xmp") == "My presets"
    assert billing.pack_of("Develop Presets/Film/x.xmp") == "Film"
    assert billing._clean_path("C:/Users/x/a.xmp") is None
    assert billing._clean_path("/abs/a.xmp") == "abs/a.xmp"
    assert billing._clean_path("a/../../b.xmp") is None
    assert billing._clean_path("a\\b\\c.XMP") == "a/b/c.XMP"
    assert billing._clean_path("a/.hidden.xmp") is None and billing._clean_path("a/b.jpg") is None
    items, skipped = billing.unpack([(f"p{i}.xmp", b"x") for i in range(billing.MAX_BULK_FILES + 2)])
    assert len(items) == billing.MAX_BULK_FILES and len(skipped) == 2


# ---- 1a. the footer line on every page ---------------------------------------------------------
def test_powered_by_on_every_page(app, box):
    anon = TestClient(app)
    for slug in ("terms", "privacy", "refunds", "shipping", "contact", ""):
        assert "Powered by Cele-Brat.Tech" in anon.get(f"/{slug}").text, slug
    c = sign_up(app, box, "a@example.com")
    assert "Powered by Cele-Brat.Tech" in c.get("/").text                                # the app
    assert "Powered by Cele-Brat.Tech" in admin_client(app).get("/admin").text
    assert "auto-renew" in anon.get("/terms").text and "Stop auto-renew" in anon.get("/refunds").text
    from server import legal
    assert legal.TERMS_VERSION == "2026-10-23"
