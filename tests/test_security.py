"""Locking down the studio account: strong password, two-step login, the old key retiring, admin activity log."""
import re
import types

import pytest
from fastapi.testclient import TestClient

from server import security
from server.main import create_app

KEY = "test-key-0123456789"
ADMIN = "studio@cele-brat.com"
GOOD = "marigold tea pushkar lake"


class Mailbox:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, text, html="", wait=False):
        self.sent.append((to, subject, text))

    def link(self, to, kind):
        for who, _, text in reversed(self.sent):
            if who == to and (m := re.search(rf"https?://[^\s]+/{kind}/([\w-]+)", text)):
                return m.group(1)
        raise AssertionError(f"no {kind} email to {to}")


@pytest.fixture
def clock(monkeypatch):
    """The two-step codes' clock, moved by hand (30 seconds a step)."""
    now = [1_800_000_000.0]
    monkeypatch.setattr(security, "time", types.SimpleNamespace(time=lambda: now[0]))
    return now


@pytest.fixture
def box():
    return Mailbox()


@pytest.fixture
def app(tmp_path, box):
    return create_app(str(tmp_path), KEY, admin_email=ADMIN, mailer=box)


def studio(app):
    c = TestClient(app)
    assert c.post("/api/login", json={"key": KEY}).status_code == 200
    return c


def turn_on_two_step(c, clock):
    assert c.post("/api/password", json={"password": GOOD}).status_code == 200
    c = TestClient(c.app)                                     # a new password signs every browser out
    assert c.post("/api/login", json={"email": ADMIN, "password": GOOD}).json()["user"]["admin"]
    secret = c.post("/api/two-step/start").json()["secret"]
    r = c.post("/api/two-step/confirm", json={"code": security.code_now(secret)})
    assert r.status_code == 200, r.text
    return c, secret, r.json()["backup_codes"]


def log_in(app, secret, clock, code=None):
    c = TestClient(app)
    r = c.post("/api/login", json={"email": ADMIN, "password": GOOD}).json()
    assert r["need_code"] and "user" not in r
    clock[0] += 30
    r = c.post("/api/login/code", json={"ticket": r["ticket"], "code": code or security.code_now(secret)})
    assert r.status_code == 200, r.text
    return c


def test_authenticator_codes_match_the_standard():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"              # RFC 6238 test key, 6-digit codes
    assert security.code_now(secret, 59) == "287082"
    assert security.code_now(secret, 1111111109) == "081804"
    step = security.check_code(secret, "287 082", at=59)
    assert step == 1 and not security.check_code(secret, "287082", last_step=step, at=59)   # works once
    assert security.check_code(secret, "287082", at=59 + 30)          # a step late is fine (clock drift)
    assert not security.check_code(secret, "287082", at=59 + 90)


def test_studio_password_must_be_strong(app):
    c = studio(app)
    for weak in ("short1", "password12345", "studio-studio-studio"[:11], "aaaaaaaaaaaaaaa"):
        r = c.post("/api/password", json={"password": weak})
        assert r.status_code == 422, weak
    assert c.post("/api/password", json={"password": GOOD}).status_code == 200


def test_two_step_login_and_backup_codes(app, clock):
    c, secret, codes = turn_on_two_step(studio(app), clock)
    assert len(codes) == 10 and len(set(codes)) == 10
    me = c.get("/api/me").json()["user"]
    assert me["two_step"]

    # the password alone no longer signs in; a wrong code does not either
    t = TestClient(app)
    r = t.post("/api/login", json={"email": ADMIN, "password": GOOD}).json()
    assert r["need_code"] and t.get("/api/me").status_code == 401
    assert t.post("/api/login/code", json={"ticket": r["ticket"], "code": "000000"}).status_code == 422
    assert t.post("/api/login/code", json={"ticket": "made-up", "code": "123456"}).status_code == 410
    clock[0] += 30
    used = security.code_now(secret)
    assert t.post("/api/login/code", json={"ticket": r["ticket"], "code": used}).status_code == 200
    assert t.get("/api/me").json()["user"]["admin"]

    # the same code cannot be used twice
    t2 = TestClient(app)
    r2 = t2.post("/api/login", json={"email": ADMIN, "password": GOOD}).json()
    assert t2.post("/api/login/code", json={"ticket": r2["ticket"], "code": used}).status_code == 422

    # a backup code works once
    t3 = TestClient(app)
    r3 = t3.post("/api/login", json={"email": ADMIN, "password": GOOD}).json()
    assert t3.post("/api/login/code", json={"ticket": r3["ticket"], "code": codes[0].upper()}).status_code == 200
    r4 = TestClient(app).post("/api/login", json={"email": ADMIN, "password": GOOD}).json()
    assert TestClient(app).post("/api/login/code", json={"ticket": r4["ticket"], "code": codes[0]}).status_code == 422
    assert t3.get("/api/two-step").json()["backup_codes_left"] == 9


def test_admin_work_needs_a_recent_code(app, clock, monkeypatch):
    c, secret, _ = turn_on_two_step(studio(app), clock)
    assert c.get("/api/admin/users").status_code == 200          # code given on this sign-in just now
    import server.main as m
    later = m.time.time() + 13 * 3600
    monkeypatch.setattr(m.time, "time", lambda: later)
    assert c.get("/api/admin/users").status_code == 428
    assert c.get("/admin").status_code == 200                     # the page itself opens, then asks for the code
    assert c.get("/api/admin/setup").json()["needs_code"]
    clock[0] += 30
    assert c.post("/api/admin/confirm", json={"code": "111111"}).status_code == 422
    assert c.post("/api/admin/confirm", json={"code": security.code_now(secret)}).status_code == 200
    assert c.get("/api/admin/users").status_code == 200


def test_old_studio_key_retires_once_set_up(app, clock):
    c = studio(app)
    assert c.get("/api/me", headers={"X-Auto-Edits-Key": KEY}).status_code == 200
    c, secret, _ = turn_on_two_step(c, clock)
    # with two-step on, the key can no longer open a browser session, but the plug-in still works
    assert TestClient(app).post("/api/login", json={"key": KEY}).status_code == 401
    assert TestClient(app).get("/api/me", headers={"X-Auto-Edits-Key": KEY}).status_code == 200
    assert not c.get("/api/admin/setup").json()["studio_key_retired"]

    new_key = c.post("/api/plugin-key").json()["key"]             # the studio's own Lightroom key
    st = c.get("/api/admin/setup").json()
    assert st["studio_key_retired"] and st["password"] and st["two_step"] and st["lightroom_key"]
    assert TestClient(app).get("/api/me", headers={"X-Auto-Edits-Key": KEY}).status_code == 401
    assert TestClient(app).get("/api/me", headers={"X-Auto-Edits-Key": new_key}).json()["user"]["admin"]


def test_studio_lightroom_key_only_from_a_checked_browser(app, clock):
    c, secret, _ = turn_on_two_step(studio(app), clock)
    key = c.post("/api/plugin-key").json()["key"]
    plugin = TestClient(app)
    assert plugin.post("/api/plugin-key", headers={"X-Auto-Edits-Key": key}).status_code == 403
    # a browser opened from the plug-in's link has no code yet: admin work asks for one
    path = plugin.post("/api/login-link", headers={"X-Auto-Edits-Key": key}).json()["path"]
    b = TestClient(app)
    b.get(path, follow_redirects=False)
    assert b.get("/api/me").json()["user"]["admin"]
    assert b.get("/api/admin/users").status_code == 428
    assert b.post("/api/plugin-key").status_code == 428


def test_only_the_studio_is_offered_two_step(app, box):
    c = TestClient(app)
    c.post("/api/signup", json={"email": "ravi@example.com", "password": "a good password", "agree": True})
    assert c.post("/api/two-step/start").status_code == 404
    assert c.get("/api/admin/activity").status_code == 404


def test_reset_link_never_skips_the_code(app, box, clock):
    c, secret, _ = turn_on_two_step(studio(app), clock)
    t = TestClient(app)
    t.post("/api/forgot", json={"email": ADMIN})
    token = box.link(ADMIN, "reset")
    assert t.post("/api/reset", json={"token": token, "password": "weakpass1"}).status_code == 422
    r = t.post("/api/reset", json={"token": token, "password": "another long river walk"})   # link still usable
    assert r.status_code == 200 and r.json()["log_in"]
    assert t.get("/api/me").status_code == 401


def test_admin_activity_is_logged(app, box, clock):
    from test_accounts import sign_up, wedding_with
    c, secret, _ = turn_on_two_step(studio(app), clock)
    p = sign_up(app, box, "ravi@example.com")
    wid = wedding_with(p, 2)["id"]
    rid = [u for u in c.get("/api/admin/users").json()["users"] if u["email"] == "ravi@example.com"][0]["id"]
    assert c.post(f"/api/admin/view-as/{rid}").status_code == 200
    assert c.get(f"/api/weddings/{wid}/originals.zip").status_code == 200     # while viewing as Ravi
    c.post("/api/admin/view-as-stop")
    assert c.get(f"/api/admin/weddings/{wid}/originals.zip").status_code == 200
    assert c.post(f"/api/admin/users/{rid}/credits", json={"credits": 20, "note": "launch gift"}).status_code == 200
    acts = c.get("/api/admin/activity").json()["activity"]
    what = [a["action"] for a in acts]
    for expected in ("two-step login turned on", "view as started", "view as ended", "downloaded uploaded photos", "credits added"):
        assert expected in what, expected
    assert sum(a["action"] == "downloaded uploaded photos" for a in acts) == 2
    assert all(a["target"] == "ravi@example.com" for a in acts if a["action"].startswith(("view as", "downloaded", "credits")))
