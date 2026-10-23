"""Compare view faces, preset favourites and client galleries."""
import io
import json
import re

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.main import create_app

KEY = "test-key-0123456789"
H = {"X-Auto-Edits-Key": KEY}


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


def jpeg(size=(96, 64), color=(120, 90, 60)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def face_jpeg():
    from skimage import data
    buf = io.BytesIO()
    Image.fromarray(data.astronaut()).save(buf, "JPEG", quality=92)
    return buf.getvalue()


def send(c, wid, uuid, name="A.NEF", data=None, headers=H):
    meta = {"uuid": uuid, "folder": "Haldi", "fileName": name, "taken": "2026-01-01T10:00:00", "iso": 400, "width": 96, "height": 64}
    return c.post(f"/api/weddings/{wid}/photos", headers=headers, data={"meta": json.dumps(meta)},
                  files={"preview": (name + ".jpg", data or jpeg(), "image/jpeg")}).json()["id"]


@pytest.fixture
def box():
    return Mailbox()


@pytest.fixture
def app(tmp_path, box):
    return create_app(str(tmp_path), KEY, admin_email="studio@cele-brat.com", mailer=box)


def photographer(app, box, email="ravi@example.com"):
    c = TestClient(app)
    assert c.post("/api/signup", json={"email": email, "password": "a good password", "name": "Ravi", "agree": True}).status_code == 200
    assert c.get(f"/verify/{box.link(email, 'verify')}", follow_redirects=False).status_code == 303
    return c


# ---- compare view: faces per photo ---------------------------------------------------------
def test_faces_endpoint_scores_and_caches(app):
    c = TestClient(app)
    w = c.post("/api/weddings", headers=H, json={"name": "W"}).json()
    pid = send(c, w["id"], "u1", data=face_jpeg())
    r = c.get(f"/api/photos/{pid}/faces", headers=H)
    assert r.status_code == 200
    faces = r.json()["faces"]
    assert len(faces) == 1 and faces[0]["judged"]
    f = faces[0]
    assert 0 <= f["eyes_open"] <= 1 and f["sharpness"] > 0 and all(0 <= v <= 1 for v in f["box"])
    assert f["eyes_open"] > 0.6                                     # the astronaut's eyes are open
    p = app.state.store.photo(w["id"], pid)
    assert app.state.store.get_faces(pid, float(p["received"]), "faces") == faces     # kept in photos.faces
    assert c.get(f"/api/photos/{pid}/faces", headers=H).json()["faces"] == faces
    blank = send(c, w["id"], "u2", name="B.NEF")
    assert c.get(f"/api/photos/{blank}/faces", headers=H).json()["faces"] == []
    assert c.get("/api/photos/nope/faces", headers=H).status_code == 404


def test_faces_are_private_to_the_owner(app, box):
    c = TestClient(app)
    w = c.post("/api/weddings", headers=H, json={"name": "W"}).json()
    pid = send(c, w["id"], "u1")
    other = photographer(app, box)
    assert other.get(f"/api/photos/{pid}/faces").status_code == 404


# ---- favourites -------------------------------------------------------------------------------
def test_favourites_per_account_with_amount(app, box):
    c = TestClient(app)
    ids = [p["id"] for p in c.get("/api/presets", headers=H).json()["presets"][:3]]
    assert c.get("/api/presets/favourites", headers=H).json()["favourites"] == {}
    assert c.put(f"/api/presets/{ids[0]}/favourite", headers=H, json={"on": True}).json() == {"on": True, "amount": 1.0}
    assert c.put(f"/api/presets/{ids[1]}/favourite", headers=H, json={"on": True, "amount": 0.6}).json()["amount"] == 0.6
    assert c.put(f"/api/presets/{ids[1]}/favourite", headers=H, json={"on": True, "amount": 1.4}).json()["amount"] == 1.4
    favs = c.get("/api/presets/favourites", headers=H).json()["favourites"]
    assert favs == {ids[0]: 1.0, ids[1]: 1.4}
    assert c.put(f"/api/presets/{ids[0]}/favourite", headers=H, json={"on": False}).json()["on"] is False
    assert list(c.get("/api/presets/favourites", headers=H).json()["favourites"]) == [ids[1]]
    assert c.put("/api/presets/nope/favourite", headers=H, json={"on": True}).status_code == 404
    assert c.put(f"/api/presets/{ids[2]}/favourite", headers=H, json={"on": True, "amount": "x"}).status_code == 422
    # another account has its own stars
    other = photographer(app, box)
    assert other.get("/api/presets/favourites").json()["favourites"] == {}
    other.put(f"/api/presets/{ids[2]}/favourite", json={"on": True})
    assert list(other.get("/api/presets/favourites").json()["favourites"]) == [ids[2]]
    assert ids[2] not in c.get("/api/presets/favourites", headers=H).json()["favourites"]
    # a removed preset of one's own leaves the list
    made = c.post("/api/presets/new", headers=H, json={"name": "Mine", "look": {"mode": "preset", "ev": 0.3}}).json()
    c.put(f"/api/presets/{made['id']}/favourite", headers=H, json={"on": True})
    assert made["id"] in c.get("/api/presets/favourites", headers=H).json()["favourites"]
    c.delete(f"/api/presets/{made['id']}", headers=H)
    assert made["id"] not in c.get("/api/presets/favourites", headers=H).json()["favourites"]


# ---- client galleries --------------------------------------------------------------------------
def test_gallery_end_to_end(app, box):
    c = photographer(app, box)
    w = c.post("/api/weddings", json={"name": "Asha & Rohan"}).json()
    keep = send(c, w["id"], "u1", "A.NEF", headers={})
    maybe = send(c, w["id"], "u2", "B.NEF", headers={})
    c.put(f"/api/weddings/{w['id']}/photos/{keep}/pick", json={"pick": "keep"})
    c.put(f"/api/weddings/{w['id']}/photos/{maybe}/pick", json={"pick": "maybe"})
    # Keep only by default; nothing to share when nothing is Keep
    r = c.post(f"/api/weddings/{w['id']}/galleries", json={"ratings": ["reject"]})
    assert r.status_code == 422
    r = c.post(f"/api/weddings/{w['id']}/galleries", json={"password": "sangeet", "downloads": True, "days": 10})
    assert r.status_code == 200, r.text
    g, url = r.json()["gallery"], r.json()["url"]
    assert g["photos"] == 1 and g["has_password"] and g["name"] == "Asha & Rohan"
    assert 10 * 86400 - 5 < g["expires"] - g["created"] < 10 * 86400 + 5
    token = url.rsplit("/", 1)[1]
    assert len(token) >= 32

    client = TestClient(app)                                    # the couple: no account, no cookie
    assert client.get(f"/g/{token}").status_code == 200
    assert "noindex" in client.get(f"/g/{token}").headers.get("x-robots-tag", "")
    assert client.get(f"/g/{token}x").status_code == 404
    data = client.get(f"/api/g/{token}").json()
    assert data == {"name": "Asha & Rohan", "locked": True}
    assert client.get(f"/api/g/{token}/photos/{keep}/image").status_code == 401
    assert client.post(f"/api/g/{token}/unlock", json={"password": "wrong"}).status_code == 401
    assert client.post(f"/api/g/{token}/unlock", json={"password": "sangeet"}).json()["ok"]
    data = client.get(f"/api/g/{token}").json()
    assert not data["locked"] and data["downloads"] and [p["id"] for p in data["photos"]] == [keep]
    assert "email" not in json.dumps(data) and "owner" not in data           # nothing about the account
    assert client.get(f"/api/g/{token}/photos/{keep}/image?size=800").status_code == 200
    assert client.get(f"/api/g/{token}/photos/{maybe}/image?size=800").status_code == 404   # not in the gallery
    assert client.get(f"/api/g/{token}/photos/{keep}/download").status_code == 200
    assert client.put(f"/api/g/{token}/photos/{keep}/heart", json={"on": True}).json()["heart"]
    assert client.put(f"/api/g/{token}/photos/{keep}/comment", json={"text": "  Love   this one "}).json()["comment"] == "Love this one"
    before = len(box.sent)
    assert client.post(f"/api/g/{token}/send", json={"email": "couple@example.com", "note": "Album please"}).json()["hearts"] == 1
    to, subject, text = box.sent[-1]
    assert len(box.sent) == before + 1 and to == "ravi@example.com" and "Asha & Rohan" in subject
    assert "couple@example.com" in text and "Album please" in text

    # the photographer sees it all
    photos = {p["id"]: p for p in c.get(f"/api/weddings/{w['id']}/photos?compact=1").json()["photos"]}
    assert photos[keep]["client_pick"] is True and photos[maybe]["client_pick"] is False
    mine = c.get("/api/galleries").json()["galleries"]
    assert len(mine) == 1 and mine[0]["views"] >= 1 and mine[0]["hearts"] == 1 and mine[0]["comments"] == 1 and mine[0]["sends"] == 1
    picks = c.get(f"/api/galleries/{g['id']}/picks").json()["photos"]
    assert picks == [{"id": keep, "name": "A.NEF", "folder": "Haldi", "heart": True, "comment": "Love this one"}]
    # un-hearting clears the client pick
    client.put(f"/api/g/{token}/photos/{keep}/heart", json={"on": False})
    assert c.get(f"/api/weddings/{w['id']}/photos?compact=1").json()["photos"][0]["client_pick"] is False
    # revoke: the link dies
    assert c.delete(f"/api/galleries/{g['id']}").json()["revoked"]
    assert client.get(f"/api/g/{token}").status_code == 404
    assert client.get(f"/g/{token}").status_code == 404


def test_gallery_without_password_and_no_downloads(app, box):
    c = photographer(app, box)
    w = c.post("/api/weddings", json={"name": "W"}).json()
    pid = send(c, w["id"], "u1", headers={})
    r = c.post(f"/api/weddings/{w['id']}/galleries", json={"ratings": [], "name": "Priya & Dev"})
    token = r.json()["url"].rsplit("/", 1)[1]
    client = TestClient(app)
    data = client.get(f"/api/g/{token}").json()
    assert data["name"] == "Priya & Dev" and not data["locked"] and not data["downloads"]
    assert client.get(f"/api/g/{token}/photos/{pid}/download").status_code == 403
    assert client.get(f"/api/g/{token}/photos/{pid}/image?size=360").status_code == 200
    # a stranger's account cannot read or revoke it
    other = photographer(app, box, "other@example.com")
    assert other.get(f"/api/galleries/{r.json()['gallery']['id']}/picks").status_code == 404
    assert other.delete(f"/api/galleries/{r.json()['gallery']['id']}").status_code == 404
    assert other.get("/api/galleries").json()["galleries"] == []


def test_gallery_password_tries_are_limited(app, box, monkeypatch):
    c = photographer(app, box)
    w = c.post("/api/weddings", json={"name": "W"}).json()
    send(c, w["id"], "u1", headers={})
    r = c.post(f"/api/weddings/{w['id']}/galleries", json={"ratings": [], "password": "secret"})
    token = r.json()["url"].rsplit("/", 1)[1]
    client = TestClient(app)
    from server import gallery as gallery_mod
    monkeypatch.setattr(gallery_mod.time, "sleep", lambda s: None)      # the 1 s pause after a wrong password
    codes = [client.post(f"/api/g/{token}/unlock", json={"password": "no"}).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_gallery_expires(app, box):
    c = photographer(app, box)
    w = c.post("/api/weddings", json={"name": "W"}).json()
    send(c, w["id"], "u1", headers={})
    r = c.post(f"/api/weddings/{w['id']}/galleries", json={"ratings": [], "days": 1})
    gid, token = r.json()["gallery"]["id"], r.json()["url"].rsplit("/", 1)[1]
    with app.state.store._db() as db:
        db.execute("UPDATE galleries SET expires=? WHERE id=?", (1.0, gid))
    assert TestClient(app).get(f"/api/g/{token}").status_code == 404
    assert c.get("/api/galleries").json()["galleries"][0]["open"] is False
