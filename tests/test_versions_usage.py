"""Two tabs editing the same folder (look versions), the sign-up "check your email" step, and the Usage tab."""
import csv
import io

import pytest
from fastapi.testclient import TestClient

from server.main import create_app
from test_accounts import ADMIN, KEY, Mailbox, browser, sign_up, wedding_with


@pytest.fixture
def box():
    return Mailbox()


@pytest.fixture
def app(tmp_path, box):
    return create_app(str(tmp_path), KEY, admin_email=ADMIN, mailer=box)


# ---- 6. versions ----------------------------------------------------------------------------
def test_look_saves_carry_a_version_and_a_stale_save_gets_409_with_the_current_look(app, box):
    c = sign_up(app, box, "ravi@example.com")
    w = wedding_with(c, 2)
    folder = c.get(f"/api/weddings/{w['id']}").json()["folders"][0]["id"]
    url = f"/api/weddings/{w['id']}/folders/{folder}/look"
    assert c.get(f"/api/weddings/{w['id']}").json()["folders"][0]["version"] == 0
    tab_a = c.put(url + "?version=0", json={"mode": "manual", "ev": 0.5}).json()
    assert tab_a["version"] == 1 and tab_a["look"]["ev"] == 0.5
    # tab B still thinks it is at 0
    r = c.put(url + "?version=0", json={"mode": "manual", "ev": -1})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["version"] == 1 and d["look"]["ev"] == 0.5 and "another tab" in d["message"]
    assert c.get(f"/api/weddings/{w['id']}").json()["folders"][0]["settings"]["ev"] == 0.5     # B's save did not land
    # If-Match works the same way; "Keep mine" sends the current version
    assert c.put(url, headers={"If-Match": "0"}, json={"mode": "manual", "ev": -1}).status_code == 409
    assert c.put(url, headers={"If-Match": '"1"'}, json={"mode": "manual", "ev": -1}).json()["version"] == 2
    # no version given: saves as before (older callers, the plug-in)
    assert c.put(url, json={"mode": "auto"}).json()["version"] == 3
    # photos
    photos = c.get(f"/api/weddings/{w['id']}/photos?compact=1").json()["photos"]
    assert all(p["ver"] == 0 for p in photos)
    purl = f"/api/weddings/{w['id']}/photos/{photos[0]['id']}/look"
    assert c.put(purl + "?version=0", json={"mode": "manual", "ev": 1}).json()["version"] == 1
    r = c.put(purl + "?version=0", json={"mode": "manual", "ev": 2})
    assert r.status_code == 409 and r.json()["detail"]["look"]["ev"] == 1 and r.json()["detail"]["version"] == 1
    r = c.request("PUT", purl + "?version=1", headers={"Content-Type": "application/json"}, content="null")   # back to the folder
    assert r.json() == {"look": None, "version": 2}
    r = c.put(purl + "?version=1", json={"mode": "manual", "ev": 2})
    assert r.status_code == 409 and r.json()["detail"]["look"] is None
    assert c.get(f"/api/weddings/{w['id']}/photos?compact=1").json()["photos"][0]["ver"] == 2
    assert c.put(f"/api/weddings/{w['id']}/folders/999/look?version=0", json={}).status_code == 404


# ---- 7. sign-up: check your email -------------------------------------------------------------
def test_sign_up_gives_no_session_and_the_link_signs_in_with_free_credits(app, box):
    c = browser(app)
    r = c.post("/api/signup", json={"email": "new@example.com", "password": "a good password", "agree": True})
    assert r.json() == {"ok": True, "check_email": True} and "autoedits" not in c.cookies
    assert c.get("/api/me").status_code == 401
    again = browser(app).post("/api/signup", json={"email": "new@example.com", "password": "another one", "agree": True})
    assert again.json() == r.json()                                    # the same message for an existing email
    token = box.link("new@example.com", "verify")
    other = browser(app)
    r = other.get(f"/verify/{token}", follow_redirects=False)
    assert r.headers["location"] == "/?verified=1" and "autoedits" in other.cookies
    me = other.get("/api/me").json()
    assert me["user"]["verified"] and me["credits"] == 50 and me["free_given"]
    # a password login works before the email is confirmed, but spending needs the confirmation
    c2 = browser(app)
    c2.post("/api/signup", json={"email": "two@example.com", "password": "a good password", "agree": True})
    assert c2.post("/api/login", json={"email": "two@example.com", "password": "a good password"}).status_code == 200
    assert not c2.get("/api/me").json()["user"]["verified"]


# ---- 8. usage tab --------------------------------------------------------------------------------
def test_usage_per_wedding_with_totals_and_csv(app, box):
    c = sign_up(app, box, "ravi@example.com")
    a = wedding_with(c, 3, name="Asha & Rohan")
    b = wedding_with(c, 2, name="Bela & Kabir", folder="Sangeet")
    assert c.post(f"/api/weddings/{a['id']}/exports", json={"size": 2560}).status_code == 200      # 3 credits
    assert c.post(f"/api/weddings/{b['id']}/cull", json={}).status_code == 200                      # 1 credit
    from server.worker import work
    work(idle_exit=True, data_dir=str(app.state.store.root))
    u = c.get("/api/usage").json()
    by = {w["name"]: w for w in u["weddings"]}
    assert by["Asha & Rohan"]["photos"] == 3 and by["Asha & Rohan"]["exports"] == 3 and by["Asha & Rohan"]["credits"] == 3
    assert by["Asha & Rohan"]["bytes"] > 0 and by["Asha & Rohan"]["last_activity"] > 0
    assert by["Bela & Kabir"]["culls"] == 1 and by["Bela & Kabir"]["credits"] == 1 and by["Bela & Kabir"]["exports"] == 0
    assert u["totals"]["weddings"] == 2 and u["totals"]["photos"] == 5 and u["totals"]["credits"] == 4
    r = c.get("/api/usage.csv")
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0][0] == "Wedding" and rows[-1][0] == "Total" and rows[-1][1] == "5" and rows[-1][5] == "4"
    # never someone else's
    other = sign_up(app, box, "other@example.com")
    assert other.get("/api/usage").json()["weddings"] == []


def test_usage_lightroom_applies_count_photos_handed_out(app, box):
    c = sign_up(app, box, "ravi@example.com")
    w = wedding_with(c, 2)
    key = c.post("/api/plugin-key").json()["key"]
    plugin = TestClient(app)
    H = {"X-Auto-Edits-Key": key}
    folder = plugin.get(f"/api/weddings/{w['id']}", headers=H).json()["folders"][0]["id"]
    plugin.put(f"/api/weddings/{w['id']}/folders/{folder}/look", headers=H, json={"mode": "manual", "ev": 0.3})
    r = plugin.get(f"/api/weddings/{w['id']}/apply", headers=H).json()
    assert len([p for p in r["photos"] if p.get("edit")]) == 2
    u = c.get("/api/usage").json()["weddings"][0]
    assert u["lightroom"] == 2 and u["credits"] == 2
