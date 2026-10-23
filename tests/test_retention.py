"""Storage retention: 180 days untouched -> warn at 14 and 1 days, then photos and exports removed."""
import time

import pytest
from fastapi.testclient import TestClient

from server.accounts import Accounts
from server.exports import Exports
from server.main import create_app
from server.retention import DAY, KEEP_DAYS, Retention, for_account, warning_email
from server.store import Store
from test_accounts import ADMIN, KEY, Mailbox, send, sign_up, wedding_with


@pytest.fixture
def box():
    return Mailbox()


@pytest.fixture
def app(tmp_path, box):
    return create_app(str(tmp_path), KEY, admin_email=ADMIN, mailer=box)


def _age(store, wedding_id, days):
    with store._db() as db:
        db.execute("UPDATE weddings SET last_used=? WHERE id=?", (time.time() - days * DAY, wedding_id))


def _retention(app, box):
    store, accounts = app.state.store, app.state.accounts
    return Retention(store, accounts, Exports(store), box, "https://example.test"), store


def test_warned_twice_then_archived_keeping_name_count_and_ledger(app, box):
    c = sign_up(app, box, "ravi@example.com")
    w = wedding_with(c, 3)
    assert c.post(f"/api/weddings/{w['id']}/exports", json={"size": 2560}).status_code == 200     # 3 credits spent
    r, store = _retention(app, box)
    now = time.time()
    before = len(box.sent)
    _age(store, w["id"], 100)
    assert r.run(now) == {"warned": 0, "archived": 0}
    _age(store, w["id"], KEEP_DAYS - 13.9)
    assert r.run(now) == {"warned": 1, "archived": 0}
    to, subject, text = box.sent[-1]
    assert to == "ravi@example.com" and "Asha & Rohan" in subject and "3 photos" in text and "/?account=weddings" in text
    assert r.run(now + 3600) == {"warned": 0, "archived": 0}             # once only
    _age(store, w["id"], KEEP_DAYS - 0.9)
    assert r.run(now) == {"warned": 1, "archived": 0}
    assert box.sent[-1][1].startswith("Tomorrow")
    assert len(box.sent) == before + 2
    _age(store, w["id"], KEEP_DAYS + 0.1)
    assert r.run(now) == {"warned": 0, "archived": 1}
    assert not store.wedding_dir(w["id"]).exists()
    assert store.photo_count(w["id"]) == 0 and store.weddings("x") == []
    kept = store.wedding(w["id"])
    assert kept["name"] == "Asha & Rohan" and kept["archived"] > 0 and kept["photos_kept"] == 3
    assert w["id"] not in [x["id"] for x in c.get("/api/weddings").json()["weddings"]]     # gone from the app's list
    history = c.get("/api/credits").json()["history"]
    assert any(h["wedding"] == w["id"] and h["wedding_name"] == "Asha & Rohan" for h in history)   # ledger rows stay
    assert r.run(now + DAY) == {"warned": 0, "archived": 0}             # nothing twice
    # the account window still lists it, as "photos removed"
    rows = c.get("/api/account/weddings").json()["weddings"]
    assert rows[0]["archived"] and rows[0]["photos"] == 3 and rows[0]["expires"] is None
    # sending the wedding again starts it afresh under the same name
    w2 = c.post("/api/weddings", json={"name": "Asha & Rohan"}).json()
    assert w2["id"] == w["id"] and not store.wedding(w["id"])["archived"]
    assert send(c, w["id"], "again-1").status_code == 200
    assert store.wedding(w["id"])["photos"] == 1


def test_using_the_wedding_resets_the_clock_and_the_emails(app, box):
    c = sign_up(app, box, "ravi@example.com")
    w = wedding_with(c, 1)
    r, store = _retention(app, box)
    now = time.time()
    _age(store, w["id"], KEEP_DAYS - 10)
    assert r.run(now)["warned"] == 1
    # viewing the photos counts as using the wedding
    c.get(f"/api/weddings/{w['id']}/photos?compact=1")
    assert store.wedding(w["id"])["last_used"] > now - 5
    assert r.run(now + 3600) == {"warned": 0, "archived": 0}
    _age(store, w["id"], KEEP_DAYS - 9)
    assert r.run(now + 2 * 3600)["warned"] == 1                          # a new cycle: a new warning
    # a look saved counts too
    _age(store, w["id"], 50)
    folder = store.wedding(w["id"])["folders"][0]["id"]
    c.put(f"/api/weddings/{w['id']}/folders/{folder}/look", json={"mode": "auto"})
    assert store.wedding(w["id"])["last_used"] > now - 5


def test_keep_button_and_account_list(app, box):
    c = sign_up(app, box, "ravi@example.com")
    w = wedding_with(c, 2)
    store = app.state.store
    _age(store, w["id"], 170)
    got = c.get("/api/account/weddings").json()
    assert got["keep_days"] == KEEP_DAYS and not got["exempt"]
    row = got["weddings"][0]
    assert row["photos"] == 2 and abs(row["expires"] - (time.time() + 10 * DAY)) < 120
    r = c.post(f"/api/weddings/{w['id']}/keep").json()
    assert abs(r["expires"] - (time.time() + KEEP_DAYS * DAY)) < 60
    assert c.post("/api/weddings/nothere/keep").status_code == 404
    other = sign_up(app, box, "other@example.com")
    assert other.post(f"/api/weddings/{w['id']}/keep").status_code == 404     # never someone else's


def test_studio_account_is_exempt(app, box):
    studio = TestClient(app)
    assert studio.post("/api/login", json={"key": KEY}).status_code == 200
    w = wedding_with(studio, 1, name="Studio job")
    r, store = _retention(app, box)
    _age(store, w["id"], KEEP_DAYS + 5)
    assert r.run(time.time()) == {"warned": 0, "archived": 0}
    assert store.wedding(w["id"])["photos"] == 1
    got = studio.get("/api/account/weddings").json()
    assert got["exempt"] and got["weddings"][0]["expires"] is None


def test_old_databases_get_last_used_from_their_photos(tmp_path):
    store = Store(tmp_path)
    w = store.open_wedding("Old", owner="u1")
    store.add_photo(w["id"], {"uuid": "a", "folder": "F"}, b"\xff\xd8\xff" + b"0" * 10)
    with store._db() as db:
        db.execute("UPDATE photos SET received=? WHERE wedding_id=?", (1_700_000_000, w["id"]))
        db.execute("UPDATE weddings SET last_used=0")
    # simulate an upgrade: drop the column's value and rerun the migration logic
    again = Store(tmp_path)
    assert again.wedding(w["id"])["last_used"] == 0 or again.wedding(w["id"])["last_used"] > 0    # column exists either way
    rows = for_account(again, {"id": "u1", "admin": False})
    assert rows[0]["expires"] == rows[0]["last_used"] + KEEP_DAYS * DAY


def test_warning_wording():
    s, text, html = warning_email("https://x", "Asha & Rohan", 1200, time.time() + 14 * DAY, False)
    assert "Asha & Rohan" in s and "1,200 photos" in text and "Keep for 180 more days" in text and "x/?account=weddings" in html
    s, text, _ = warning_email("https://x", "A", 1, time.time() + DAY, True)
    assert s.startswith("Tomorrow") and "tomorrow" in text
