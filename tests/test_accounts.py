"""Accounts, keeping each photographer's work apart, and credits."""
import io
import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server import mail
from server.main import create_app
from server.worker import work

KEY = "test-key-0123456789"
ADMIN = "studio@cele-brat.com"


class Mailbox:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, text, html="", wait=False):
        self.sent.append((to, subject, text))

    def link(self, to, kind):
        for who, _, text in reversed(self.sent):
            if who == to:
                m = re.search(rf"https?://[^\s]+/{kind}/([\w-]+)", text)
                if m:
                    return m.group(1)
        raise AssertionError(f"no {kind} email to {to}")


def jpeg(color=(120, 90, 60), size=(64, 48)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def box():
    return Mailbox()


@pytest.fixture
def app(tmp_path, box):
    return create_app(str(tmp_path), KEY, admin_email=ADMIN, mailer=box)


def browser(app):
    return TestClient(app)


def sign_up(app, box, email, verify=True):
    c = browser(app)
    r = c.post("/api/signup", json={"email": email, "password": "a good password", "name": "Ravi", "agree": True})
    assert r.status_code == 200, r.text
    if verify:
        assert c.get(f"/verify/{box.link(email, 'verify')}", follow_redirects=False).status_code == 303
    return c


def send(c, wid, uuid, folder="Haldi", name="A.NEF", headers=None):
    meta = {"uuid": uuid, "folder": folder, "fileName": name, "taken": "2026-01-01T10:00:00", "iso": 400}
    return c.post(f"/api/weddings/{wid}/photos", headers=headers or {}, data={"meta": json.dumps(meta)},
                  files={"preview": (name + ".jpg", jpeg(), "image/jpeg")})


def wedding_with(c, n, name="Asha & Rohan", folder="Haldi"):
    w = c.post("/api/weddings", json={"name": name}).json()
    for i in range(n):
        assert send(c, w["id"], f"{name}-{folder}-{i}", folder, f"IMG_{i}.NEF").status_code == 200
    return w


def test_sign_up_confirm_email_and_get_free_credits(app, box):
    c = sign_up(app, box, "Ravi@Example.com", verify=False)
    me = c.get("/api/me").json()
    assert me["user"]["email"] == "ravi@example.com" and not me["user"]["verified"] and me["credits"] == 0
    w = wedding_with(c, 1)
    assert c.post(f"/api/weddings/{w['id']}/cull", json={}).status_code == 403          # confirm the email first
    token = box.link("ravi@example.com", "verify")
    r = c.get(f"/verify/{token}", follow_redirects=False)
    assert r.headers["location"] == "/?verified=1"
    me = c.get("/api/me").json()
    assert me["user"]["verified"] and me["credits"] == 50
    assert c.get(f"/verify/{token}", follow_redirects=False).headers["location"] == "/?link=expired"   # once only
    assert c.get("/api/me").json()["credits"] == 50                                   # free credits only once


def test_sign_up_rules(app, box):
    c = browser(app)
    assert c.post("/api/signup", json={"email": "not-an-email", "password": "a good password"}).status_code == 422
    assert c.post("/api/signup", json={"email": "a@b.co", "password": "short"}).status_code == 422
    assert c.post("/api/signup", json={"email": "a@b.co", "password": "a good password", "agree": True}).status_code == 200
    assert c.post("/api/signup", json={"email": "A@B.co", "password": "a good password", "agree": True}).status_code == 422


def test_log_in_and_out(app, box):
    sign_up(app, box, "ravi@example.com")
    c = browser(app)
    assert c.get("/api/me").status_code == 401
    assert c.post("/api/login", json={"email": "ravi@example.com", "password": "wrong password"}).status_code == 401
    assert c.post("/api/login", json={"email": "RAVI@example.com", "password": "a good password"}).status_code == 200
    assert c.get("/api/me").json()["user"]["email"] == "ravi@example.com"
    c.post("/api/logout")
    assert c.get("/api/me").status_code == 401


def test_forgot_password(app, box):
    sign_up(app, box, "ravi@example.com")
    c = browser(app)
    assert c.post("/api/forgot", json={"email": "nobody@example.com"}).json()["ok"]       # same answer for anyone
    assert c.post("/api/forgot", json={"email": "ravi@example.com"}).json()["ok"]
    token = box.link("ravi@example.com", "reset")
    assert c.get(f"/reset/{token}").status_code == 200
    assert c.post("/api/reset", json={"token": token, "password": "short"}).status_code == 422
    assert c.post("/api/reset", json={"token": token, "password": "a new password"}).status_code == 200
    assert c.get("/api/me").status_code == 200                                         # signed in afterwards
    assert c.post("/api/reset", json={"token": token, "password": "another one"}).status_code == 410
    fresh = browser(app)
    assert fresh.post("/api/login", json={"email": "ravi@example.com", "password": "a good password"}).status_code == 401
    assert fresh.post("/api/login", json={"email": "ravi@example.com", "password": "a new password"}).status_code == 200


def test_the_studio_key_is_the_admin_account(app, box):
    c = browser(app)
    me = c.get("/api/me", headers={"X-Auto-Edits-Key": KEY}).json()
    assert me["user"]["email"] == ADMIN and me["user"]["admin"]
    assert c.post("/api/login", json={"key": KEY}).status_code == 200
    assert c.get("/api/me").json()["user"]["admin"]


def test_each_photographer_sees_only_their_own_work(app, box):
    a = sign_up(app, box, "a@example.com")
    b = sign_up(app, box, "b@example.com")
    wa = wedding_with(a, 2, "Same name")
    wb = b.post("/api/weddings", json={"name": "Same name"}).json()
    assert wb["id"] != wa["id"] and wb["is_new"]                          # the same name is a separate wedding
    assert [w["id"] for w in b.get("/api/weddings").json()["weddings"]] == [wb["id"]]
    photo = a.get(f"/api/weddings/{wa['id']}/photos").json()["photos"][0]["id"]
    for path in (f"/api/weddings/{wa['id']}", f"/api/weddings/{wa['id']}/photos",
                 f"/api/weddings/{wa['id']}/photos/{photo}/preview", f"/api/weddings/{wa['id']}/photos/{photo}/image",
                 f"/api/weddings/{wa['id']}/exports", f"/api/weddings/{wa['id']}/cull"):
        assert b.get(path).status_code == 404, path
    assert send(b, wa["id"], "x").status_code == 404
    assert b.put(f"/api/weddings/{wa['id']}/photos/{photo}/pick", json={"pick": "keep"}).status_code == 404
    assert b.delete(f"/api/weddings/{wa['id']}").status_code == 404
    assert a.get(f"/api/weddings/{wa['id']}").json()["photos"] == 2
    admin = browser(app)
    assert admin.get("/api/weddings", headers={"X-Auto-Edits-Key": KEY}).json()["weddings"] == []


def test_plugin_key_per_photographer(app, box):
    a = sign_up(app, box, "a@example.com")
    key = a.post("/api/plugin-key").json()["key"]
    plugin = browser(app)
    H = {"X-Auto-Edits-Key": key}
    assert plugin.get("/api/ping", headers=H).json()["email"] == "a@example.com"
    w = plugin.post("/api/weddings", headers=H, json={"name": "From Lightroom"}).json()
    assert send(plugin, w["id"], "u1", headers=H).status_code == 200
    assert [x["name"] for x in a.get("/api/weddings").json()["weddings"]] == ["From Lightroom"]
    link = plugin.post("/api/login-link", headers=H).json()["path"]          # "open the app" from Lightroom
    opened = browser(app)
    opened.get(link, follow_redirects=False)
    assert opened.get("/api/me").json()["user"]["email"] == "a@example.com"
    a.post("/api/plugin-key")                                                 # a new key: the old one stops
    assert plugin.get("/api/ping", headers=H).status_code == 401


def test_presets_are_kept_apart(app, box):
    from pathlib import Path
    xmp = next(Path(__file__).resolve().parent.parent.joinpath("presets", "library").rglob("*.xmp")).read_bytes()
    a = sign_up(app, box, "a@example.com")
    b = sign_up(app, box, "b@example.com")
    got = a.post("/api/presets", files=[("files", ("mine.xmp", xmp, "application/octet-stream"))]).json()["results"][0]
    assert got["ok"]
    assert any(p["id"] == got["id"] for p in a.get("/api/presets").json()["presets"])
    assert not any(p["id"] == got["id"] for p in b.get("/api/presets").json()["presets"])
    assert b.delete(f"/api/presets/{got['id']}").status_code == 404
    wb = wedding_with(b, 1)
    folder = b.get(f"/api/weddings/{wb['id']}").json()["folders"][0]["id"]
    look = b.put(f"/api/weddings/{wb['id']}/folders/{folder}/look", json={"preset": got["id"]}).json()["look"]
    assert look["preset"] is None                                             # someone else's preset is not taken
    mine = b.post("/api/presets", files=[("files", ("mine.xmp", xmp, "application/octet-stream"))]).json()["results"][0]
    assert mine["ok"] and mine["id"] != got["id"]                             # the same file, a copy of b's own


def test_culling_costs_one_credit_per_ten_photos_every_time(app, box, tmp_path):
    c = sign_up(app, box, "a@example.com")
    w = wedding_with(c, 12)
    r = c.post(f"/api/weddings/{w['id']}/cull", json={"strictness": "normal"})
    assert r.status_code == 200 and r.json()["charged"] == 2
    assert c.get("/api/me").json()["credits"] == 48
    work(idle_exit=True, data_dir=str(tmp_path))
    c.post(f"/api/weddings/{w['id']}/cull", json={"strictness": "strict"})
    assert c.get("/api/me").json()["credits"] == 46
    history = c.get("/api/credits").json()["history"]
    assert [h["kind"] for h in history] == ["cull", "cull", "welcome"] and history[0]["delta"] == -2


def test_exports_cost_one_credit_per_photo_and_give_back_what_was_not_made(app, box, tmp_path):
    c = sign_up(app, box, "a@example.com")
    w = wedding_with(c, 5)
    e = c.post(f"/api/weddings/{w['id']}/exports", json={"size": 2560}).json()
    assert c.get("/api/me").json()["credits"] == 45
    assert work(idle_exit=True, data_dir=str(tmp_path)) == 5
    c.post(f"/api/weddings/{w['id']}/exports", json={"size": 2560})            # the same photos again: charged again
    assert c.get("/api/me").json()["credits"] == 40
    third = c.post(f"/api/weddings/{w['id']}/exports", json={"size": 2560}).json()
    assert c.get("/api/me").json()["credits"] == 35
    stopped = c.post(f"/api/exports/{third['id']}/stop").json()
    assert stopped["state"] == "stopped" and stopped["skipped"] == 5
    assert c.get("/api/me").json()["credits"] == 40                          # stopped before starting: all back
    assert c.get(f"/api/exports/{e['id']}/zip").status_code == 200


def test_not_enough_credits(app, box, tmp_path):
    c = sign_up(app, box, "a@example.com")
    w = wedding_with(c, 51)
    r = c.post(f"/api/weddings/{w['id']}/exports", json={"size": 2560})
    assert r.status_code == 402 and "51" in r.json()["detail"]
    assert c.get(f"/api/weddings/{w['id']}/exports").json()["exports"] == []   # nothing was made
    assert c.get("/api/me").json()["credits"] == 50


def test_the_studio_account_is_never_stopped_for_credits(app, box, tmp_path):
    H = {"X-Auto-Edits-Key": KEY}
    c = browser(app)
    w = c.post("/api/weddings", headers=H, json={"name": "Studio"}).json()
    for i in range(3):
        send(c, w["id"], f"s{i}", headers=H)
    assert c.post(f"/api/weddings/{w['id']}/exports", headers=H, json={"size": 2560}).status_code == 200
    assert c.get("/api/credits", headers=H).json()["unlimited"]


def test_edits_applied_in_lightroom_cost_one_credit_each(app, box):
    c = sign_up(app, box, "a@example.com")
    w = wedding_with(c, 3)
    folder = c.get(f"/api/weddings/{w['id']}").json()["folders"][0]["id"]
    c.put(f"/api/weddings/{w['id']}/folders/{folder}/look", json={"mode": "manual", "ev": 0.5})
    items = c.get(f"/api/weddings/{w['id']}/apply").json()["photos"]
    assert len(items) == 3 and all(i["edit"] for i in items)
    stamps = {i["uuid"]: i["stamp"] for i in items}
    assert c.post(f"/api/weddings/{w['id']}/applied", json=stamps).json()["marked"] == 3
    assert c.get("/api/me").json()["credits"] == 47


def test_edited_previews_carry_a_mark_until_exported(app, box):
    import numpy as np
    c = sign_up(app, box, "a@example.com")
    w = c.post("/api/weddings", json={"name": "W"}).json()
    meta = {"uuid": "u1", "folder": "Haldi", "fileName": "A.NEF"}
    c.post(f"/api/weddings/{w['id']}/photos", data={"meta": json.dumps(meta)},
           files={"preview": ("a.jpg", jpeg((60, 60, 60), (1600, 1067)), "image/jpeg")})
    photo = c.get(f"/api/weddings/{w['id']}/photos").json()["photos"][0]["id"]

    def mean(r):
        return np.asarray(Image.open(io.BytesIO(r.content))).astype(float).mean()

    marked = mean(c.get(f"/api/weddings/{w['id']}/photos/{photo}/image?size=1600"))
    base = mean(c.get(f"/api/weddings/{w['id']}/photos/{photo}/image?size=1600&base=1"))
    assert marked > base + 2                                                   # light lettering over the picture
    H = {"X-Auto-Edits-Key": KEY}
    ws = c.post("/api/weddings", headers=H, json={"name": "Studio"}).json()
    c.post(f"/api/weddings/{ws['id']}/photos", headers=H, data={"meta": json.dumps(meta)},
           files={"preview": ("a.jpg", jpeg((60, 60, 60), (1600, 1067)), "image/jpeg")})
    sp = c.get(f"/api/weddings/{ws['id']}/photos", headers=H).json()["photos"][0]["id"]
    clean = mean(c.get(f"/api/weddings/{ws['id']}/photos/{sp}/image?size=1600", headers=H))
    assert abs(clean - base) < 2                                               # the studio sees it clean


def test_weddings_from_before_accounts_go_to_the_studio(tmp_path, box):
    db = sqlite3.connect(tmp_path / "autoedits.db")
    db.executescript("""
        CREATE TABLE weddings (id TEXT PRIMARY KEY, name TEXT NOT NULL, catalog TEXT NOT NULL DEFAULT '',
            created REAL NOT NULL, UNIQUE (name, catalog));
        CREATE TABLE folders (id INTEGER PRIMARY KEY, wedding_id TEXT NOT NULL REFERENCES weddings(id) ON DELETE CASCADE,
            name TEXT NOT NULL, settings TEXT NOT NULL DEFAULT '{}', UNIQUE (wedding_id, name));
        INSERT INTO weddings VALUES ('old1', 'Old wedding', '', 1.0);
        INSERT INTO folders (wedding_id, name) VALUES ('old1', 'Haldi');
    """)
    db.commit()
    db.close()
    app = create_app(str(tmp_path), KEY, admin_email=ADMIN, mailer=box)
    c = browser(app)
    got = c.get("/api/weddings", headers={"X-Auto-Edits-Key": KEY}).json()["weddings"]
    assert [w["id"] for w in got] == ["old1"] and got[0]["folders"][0]["name"] == "Haldi"   # folders survived
    other = sign_up(app, box, "a@example.com")
    assert other.get("/api/weddings").json()["weddings"] == []


def test_mail_without_settings_goes_to_the_log(capsys):
    m = mail.Mailer({})
    assert not m.on
    m.send("a@example.com", "Hello", "Body")
    assert "a@example.com" in capsys.readouterr().out


def test_free_credits_once_per_internet_address(app, box):
    def from_ip(email, ip):
        c = browser(app)
        H = {"X-Forwarded-For": f"10.0.0.9, {ip}"}           # Traefik adds the real address last
        assert c.post("/api/signup", headers=H, json={"email": email, "password": "a good password", "agree": True}).status_code == 200
        r = c.get(f"/verify/{box.link(email, 'verify')}", follow_redirects=False)
        return c, r.headers["location"]

    a, where = from_ip("a@example.com", "203.0.113.5")
    assert where == "/?verified=1" and a.get("/api/me").json()["credits"] == 50
    b, where = from_ip("b@example.com", "203.0.113.5")                # same address: account works, no free credits
    assert where == "/?verified=0" and b.get("/api/me").json()["credits"] == 0
    assert b.get("/api/me").json()["user"]["verified"]
    c, where = from_ip("c@example.com", "198.51.100.7")               # another address: free credits
    assert c.get("/api/me").json()["credits"] == 50
    spoof = browser(app)                                               # a made-up first entry does not help
    spoof.post("/api/signup", headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.5"},
               json={"email": "d@example.com", "password": "a good password", "agree": True})
    spoof.get(f"/verify/{box.link('d@example.com', 'verify')}")
    assert spoof.get("/api/me").json()["credits"] == 0


def admin_browser(app):
    c = browser(app)
    assert c.post("/api/login", json={"key": KEY}).status_code == 200
    return c


def test_admin_dashboard_lists_photographers_and_adds_credits(app, box):
    a = sign_up(app, box, "a@example.com")
    wedding_with(a, 3)
    admin = admin_browser(app)
    assert admin.get("/admin").status_code == 200
    assert a.get("/admin").status_code == 404 and a.get("/api/admin/users").status_code == 404     # not for photographers
    users = {u["email"]: u for u in admin.get("/api/admin/users").json()["users"]}
    assert users["a@example.com"]["photos"] == 3 and users["a@example.com"]["balance"] == 50 and users[ADMIN]["admin"]
    uid = users["a@example.com"]["id"]
    assert admin.post(f"/api/admin/users/{uid}/credits", json={"credits": 25, "note": "Thank you"}).json()["credits"] == 75
    assert a.get("/api/credits").json()["history"][0]["note"] == "Thank you"


def test_view_as_a_photographer_read_only_and_download_their_work(app, box, tmp_path):
    import zipfile
    a = sign_up(app, box, "a@example.com")
    w = wedding_with(a, 2)
    e = a.post(f"/api/weddings/{w['id']}/exports", json={"size": 2560}).json()
    work(idle_exit=True, data_dir=str(tmp_path))
    admin = admin_browser(app)
    uid = next(u["id"] for u in admin.get("/api/admin/users").json()["users"] if u["email"] == "a@example.com")
    assert admin.post(f"/api/admin/view-as/{uid}").status_code == 200
    s = admin.get("/api/session").json()
    assert s["user"]["email"] == "a@example.com" and s["viewing_as"] == ADMIN
    assert [x["id"] for x in admin.get("/api/weddings").json()["weddings"]] == [w["id"]]
    assert admin.post("/api/weddings", json={"name": "Not allowed"}).status_code == 403             # read-only
    assert admin.post(f"/api/weddings/{w['id']}/exports", json={"size": 2560}).status_code == 403
    z = admin.get(f"/api/weddings/{w['id']}/originals.zip")
    assert z.status_code == 200 and len(zipfile.ZipFile(io.BytesIO(z.content)).namelist()) == 2
    assert admin.get(f"/api/exports/{e['id']}/zip").status_code == 200
    assert admin.post("/api/admin/view-as-stop").status_code == 200
    assert admin.get("/api/session").json()["viewing_as"] is None
    assert admin.get("/api/weddings").json()["weddings"] == []                                       # back to the studio
    d = admin.get(f"/api/admin/users/{uid}").json()
    assert d["weddings"][0]["exports"][0]["done"] == 2
    assert admin.get(f"/api/admin/weddings/{w['id']}/originals.zip").status_code == 200
    assert admin.get(f"/api/admin/exports/{e['id']}/zip").status_code == 200
    assert a.get(f"/api/admin/exports/{e['id']}/zip").status_code == 404
    other = sign_up(app, box, "b@example.com")                                                       # a photographer cannot view as
    other.cookies.set("autoedits_as", uid)
    assert other.get("/api/weddings").json()["weddings"] == []


def test_photographers_can_download_their_own_uploads(app, box):
    import zipfile
    a = sign_up(app, box, "a@example.com")
    w = wedding_with(a, 3)
    names = zipfile.ZipFile(io.BytesIO(a.get(f"/api/weddings/{w['id']}/originals.zip").content)).namelist()
    assert sorted(names) == ["Haldi/IMG_0.jpg", "Haldi/IMG_1.jpg", "Haldi/IMG_2.jpg"]
    b = sign_up(app, box, "b@example.com")
    assert b.get(f"/api/weddings/{w['id']}/originals.zip").status_code == 404


def test_credit_ledger_is_a_record_with_weddings_balances_and_a_statement(app, box, tmp_path):
    import csv
    c = sign_up(app, box, "ledger@example.com")
    w = wedding_with(c, 4, name="Meera & Kabir")
    assert c.post(f"/api/weddings/{w['id']}/cull", json={"strictness": "normal"}).status_code == 200
    e = c.post(f"/api/weddings/{w['id']}/exports", json={"size": 2560}).json()
    c.delete(f"/api/exports/{e['id']}")                       # removed before it finished: credits given back
    h = c.get("/api/credits").json()["history"]
    kinds = [x["kind"] for x in h]
    assert kinds[-1] == "welcome" and "cull" in kinds and "export" in kinds and "refund" in kinds
    assert all(x["wedding_name"] == "Meera & Kabir" for x in h if x["kind"] != "welcome")
    assert h[0]["balance"] == c.get("/api/credits").json()["balance"]          # running balance, newest first
    assert [x["balance"] for x in reversed(h)] == list(__import__("itertools").accumulate(x["delta"] for x in reversed(h)))

    r = c.get("/api/credits/statement.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(r.text.splitlines()))
    assert rows[0][0].startswith("Date") and rows[1][1] == "Free credits" and len(rows) == len(h) + 1
    assert rows[-1][6] == str(h[0]["balance"])

    db = sqlite3.connect(tmp_path / "autoedits.db")              # rows can only be added
    for sql in ("UPDATE ledger SET delta=1000", "DELETE FROM ledger"):
        with pytest.raises(sqlite3.DatabaseError, match="cannot be changed"):
            db.execute(sql)
