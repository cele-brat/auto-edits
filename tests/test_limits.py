"""Abuse and cost limits: photos per wedding, storage per photographer, taking turns, failed logins."""
import time

import pytest
from fastapi.testclient import TestClient

from server import limits
from test_accounts import KEY, ADMIN, Mailbox, send, sign_up
from server.main import create_app
from server.exports import Exports
from server.culling import Culls


@pytest.fixture
def box():
    return Mailbox()


@pytest.fixture
def app(tmp_path, box):
    return create_app(str(tmp_path), KEY, admin_email=ADMIN, mailer=box)


def test_photos_per_wedding_limit(app, box, monkeypatch):
    monkeypatch.setattr(limits, "PHOTOS_PER_WEDDING", 3)
    c = sign_up(app, box, "a@example.com")
    w = c.post("/api/weddings", json={"name": "Big day"}).json()
    for i in range(3):
        assert send(c, w["id"], f"u{i}").status_code == 200
    r = send(c, w["id"], "u3")
    assert r.status_code == 403 and "3 photos" in r.json()["detail"] and "second wedding" in r.json()["detail"]
    assert send(c, w["id"], "u1").status_code == 200              # sending one again is fine
    studio = TestClient(app)
    sw = studio.post("/api/weddings", headers={"X-Auto-Edits-Key": KEY}, json={"name": "Studio"}).json()
    for i in range(5):                                             # the studio has no limit
        assert send(studio, sw["id"], f"s{i}", headers={"X-Auto-Edits-Key": KEY}).status_code == 200


def test_storage_limit_and_admin_can_raise_it(app, box, monkeypatch):
    monkeypatch.setattr(limits, "GB", 100)                          # 1 "GB" = 100 bytes for the test
    monkeypatch.setattr(limits, "STORAGE_GB", 1)
    c = sign_up(app, box, "a@example.com")
    w = c.post("/api/weddings", json={"name": "W"}).json()
    r = send(c, w["id"], "u0")
    assert r.status_code == 403 and "storage is full" in r.json()["detail"]
    me = c.get("/api/me").json()["storage"]
    assert me["storage_gb"] == 1 and me["photos_per_wedding"] == limits.PHOTOS_PER_WEDDING

    admin = TestClient(app)
    admin.post("/api/login", json={"key": KEY})
    uid = c.get("/api/me").json()["user"]["id"]
    assert admin.post(f"/api/admin/users/{uid}/limits", json={"photos": 0, "gb": 100}).status_code == 200
    assert send(c, w["id"], "u0").status_code == 200
    d = admin.get(f"/api/admin/users/{uid}").json()
    assert d["limits"]["gb"] == 100 and d["storage"]["used_bytes"] > 0
    assert any(a["action"] == "limits changed" for a in admin.get("/api/admin/activity").json()["activity"])


def test_exports_take_turns_between_photographers(app, box):
    a = sign_up(app, box, "a@example.com")
    b = sign_up(app, box, "b@example.com")
    wa = a.post("/api/weddings", json={"name": "A"}).json()
    wb = b.post("/api/weddings", json={"name": "B"}).json()
    for i in range(6):
        send(a, wa["id"], f"a{i}")
    for i in range(2):
        send(b, wb["id"], f"b{i}")
    app.state.accounts.add(b.get("/api/me").json()["user"]["id"], 10, "admin", "test")   # one free trial per address
    ea = a.post(f"/api/weddings/{wa['id']}/exports", json={"size": 2560}).json()   # A asked first, for more
    time.sleep(0.01)
    eb = b.post(f"/api/weddings/{wb['id']}/exports", json={"size": 2560}).json()
    taken = Exports(app.state.store).claim(4)
    owners = [t["export"] for t in taken]
    assert owners == [ea["id"], eb["id"], ea["id"], eb["id"]]                       # alternating, B not left waiting


def test_culls_go_to_whoever_has_fewest_running(app, box):
    store = app.state.store
    a = sign_up(app, box, "a@example.com")
    b = sign_up(app, box, "b@example.com")
    wa = a.post("/api/weddings", json={"name": "A"}).json()
    wb = b.post("/api/weddings", json={"name": "B"}).json()
    for folder in ("Haldi", "Sangeet"):
        send(a, wa["id"], f"a-{folder}", folder=folder)
    send(b, wb["id"], "b1")
    culls = Culls(store)
    fa = [f["id"] for f in store.wedding(wa["id"])["folders"]]
    culls.start(wa["id"], fa, "normal", False)                                      # A queues two folders first
    culls.start(wb["id"], [store.wedding(wb["id"])["folders"][0]["id"]], "normal", False)
    first, second = culls.claim(), culls.claim()
    assert first["wedding_id"] == wa["id"] and second["wedding_id"] == wb["id"]    # B's goes before A's second


def test_failed_logins_per_account_from_many_addresses(app, box):
    sign_up(app, box, "a@example.com")
    for i in range(10):
        c = TestClient(app)
        r = c.post("/api/login", headers={"X-Forwarded-For": f"10.0.0.{i}"}, json={"email": "a@example.com", "password": "wrong one!"})
        assert r.status_code == 401
    r = TestClient(app).post("/api/login", headers={"X-Forwarded-For": "10.0.1.1"},
                             json={"email": "a@example.com", "password": "a good password"})
    assert r.status_code == 429 and "Forgot password" in r.json()["detail"]
