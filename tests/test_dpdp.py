"""Account deletion and change of email (DPDP)."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server.main import create_app
from test_accounts import ADMIN, KEY, Mailbox, sign_up, wedding_with


@pytest.fixture
def box():
    return Mailbox()


@pytest.fixture
def app(tmp_path, box):
    return create_app(str(tmp_path), KEY, admin_email=ADMIN, mailer=box)


def test_delete_my_account(app, box, tmp_path):
    c = sign_up(app, box, "ravi@example.com")
    w = wedding_with(c, 2)
    c.post(f"/api/weddings/{w['id']}/exports", json={"size": 2560})
    uid = c.get("/api/me").json()["user"]["id"]
    # password and the word DELETE are both needed
    assert c.post("/api/account/delete", json={"password": "a good password", "confirm": "delete"}).status_code == 422
    assert c.post("/api/account/delete", json={"password": "wrong", "confirm": "DELETE"}).status_code == 403
    assert len([m for m in box.sent if "delete" in m[1].lower()]) == 0
    r = c.post("/api/account/delete", json={"password": "a good password", "confirm": "DELETE"})
    assert r.status_code == 200 and r.json()["hours"] == 24
    token = box.link("ravi@example.com", "account/delete")
    assert c.get("/api/me").status_code == 200                        # nothing happened yet
    assert app.state.store.wedding_dir(w["id"]).exists()
    r = c.get(f"/account/delete/{token}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?deleted=1"
    assert c.get("/api/me").status_code == 401                         # signed out
    assert not app.state.store.wedding_dir(w["id"]).exists()
    db = sqlite3.connect(tmp_path / "autoedits.db")
    u = db.execute("SELECT email, name, password, plugin_key, verified FROM users WHERE id=?", (uid,)).fetchone()
    assert u == (f"deleted-{uid}@invalid", "", "", "", 0)
    assert db.execute("SELECT COUNT(*) FROM weddings WHERE owner=?", (uid,)).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM photos").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM ledger WHERE user_id=?", (uid,)).fetchone()[0] >= 2   # welcome + export kept
    assert db.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (uid,)).fetchone()[0] == 0
    assert db.execute("SELECT action FROM admin_log ORDER BY id DESC LIMIT 1").fetchone()[0] == "account deleted by its owner"
    assert box.sent[-1][0] == "ravi@example.com" and "deleted" in box.sent[-1][1]
    assert TestClient(app).get(f"/account/delete/{token}", follow_redirects=False).headers["location"] == "/?link=used"
    assert TestClient(app).post("/api/login", json={"email": "ravi@example.com", "password": "a good password"}).status_code == 401
    # the email can be used for a new account
    assert TestClient(app).post("/api/signup", json={"email": "ravi@example.com", "password": "a good password", "agree": True}).status_code == 200


def test_studio_cannot_be_deleted_here_and_plugin_key_cannot_ask(app, box):
    studio = TestClient(app)
    studio.post("/api/login", json={"key": KEY})
    assert studio.post("/api/account/delete", json={"password": "x", "confirm": "DELETE"}).status_code == 403
    c = sign_up(app, box, "ravi@example.com")
    key = c.post("/api/plugin-key").json()["key"]
    plugin = TestClient(app)
    assert plugin.post("/api/account/delete", headers={"X-Auto-Edits-Key": key},
                       json={"password": "a good password", "confirm": "DELETE"}).status_code == 401


def test_change_email(app, box):
    c = sign_up(app, box, "ravi@example.com")
    assert c.post("/api/account/email", json={"email": "ravi@example.com", "password": "a good password"}).status_code == 422
    assert c.post("/api/account/email", json={"email": "not an email", "password": "a good password"}).status_code == 422
    assert c.post("/api/account/email", json={"email": "new@example.com", "password": "wrong"}).status_code == 403
    r = c.post("/api/account/email", json={"email": "New@Example.com", "password": "a good password"})
    assert r.status_code == 200 and r.json()["check_email"]
    assert box.sent[-1][0] == "new@example.com" and "ravi@example.com" in box.sent[-1][2]
    token = box.link("new@example.com", "account/email")
    assert c.get("/api/me").json()["user"]["email"] == "ravi@example.com"        # not yet
    r = TestClient(app).get(f"/account/email/{token}", follow_redirects=False)
    assert r.headers["location"] == "/?email=changed"
    assert c.get("/api/me").json()["user"]["email"] == "new@example.com"
    assert box.sent[-1][0] == "ravi@example.com" and "new@example.com" in box.sent[-1][2]    # the old address is told
    assert TestClient(app).post("/api/login", json={"email": "new@example.com", "password": "a good password"}).status_code == 200
    assert TestClient(app).get(f"/account/email/{token}", follow_redirects=False).headers["location"] == "/?link=used"


def test_change_email_to_a_taken_address_says_nothing(app, box):
    sign_up(app, box, "taken@example.com")
    c = sign_up(app, box, "ravi@example.com")
    before = len(box.sent)
    r = c.post("/api/account/email", json={"email": "taken@example.com", "password": "a good password"})
    assert r.status_code == 200 and r.json() == {"ok": True, "check_email": True, "hours": 24}
    assert len(box.sent) == before + 1 and box.sent[-1][0] == "taken@example.com" and "already have" in box.sent[-1][1]
    assert "/account/email/" not in box.sent[-1][2]
    assert c.get("/api/me").json()["user"]["email"] == "ravi@example.com"
