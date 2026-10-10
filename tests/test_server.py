import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.main import create_app

KEY = "test-key-0123456789"
H = {"X-Auto-Edits-Key": KEY}


def jpeg(color=(120, 90, 60)):
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path), KEY))


def send(client, wid, uuid, folder="Haldi", name="A.NEF", data=None):
    meta = {"uuid": uuid, "folder": folder, "fileName": name, "taken": "2026-01-01T10:00:00", "iso": 400}
    return client.post(f"/api/weddings/{wid}/photos", headers=H, data={"meta": json.dumps(meta)},
                       files={"preview": (name + ".jpg", data or jpeg(), "image/jpeg")})


def test_needs_a_key(tmp_path, client):
    with pytest.raises(RuntimeError):
        create_app(str(tmp_path), "short")
    assert client.get("/api/ping").status_code == 401
    assert client.get("/api/ping", headers={"X-Auto-Edits-Key": "wrong-key-0123456789"}).status_code == 401
    assert client.get("/api/ping", headers=H).json()["ok"] is True


def test_send_photos_into_event_folders(client):
    w = client.post("/api/weddings", headers=H, json={"name": "Asha & Rohan", "catalog": "2026"}).json()
    assert w["is_new"] and w["photos"] == 0
    again = client.post("/api/weddings", headers=H, json={"name": "Asha & Rohan", "catalog": "2026"}).json()
    assert again["id"] == w["id"] and not again["is_new"]

    assert send(client, w["id"], "u1", "Haldi", "A.NEF").json()["replaced"] is False
    assert send(client, w["id"], "u2", "Haldi", "B.NEF").status_code == 200
    assert send(client, w["id"], "u3", "Sangeet", "A.NEF").status_code == 200     # same file name, other folder
    r = send(client, w["id"], "u1", "Haldi", "A.NEF", jpeg((1, 2, 3)))            # sent again: replaced
    assert r.json()["replaced"] is True

    got = client.get(f"/api/weddings/{w['id']}", headers=H).json()
    assert {f["name"]: f["photos"] for f in got["folders"]} == {"Haldi": 2, "Sangeet": 1}
    assert sorted(client.get(f"/api/weddings/{w['id']}/received", headers=H).json()["uuids"]) == ["u1", "u2", "u3"]

    photos = client.get(f"/api/weddings/{w['id']}/photos", headers=H).json()["photos"]
    assert len(photos) == 3 and photos[0]["meta"]["iso"] == 400
    haldi = [f for f in got["folders"] if f["name"] == "Haldi"][0]["id"]
    assert len(client.get(f"/api/weddings/{w['id']}/photos?folder={haldi}", headers=H).json()["photos"]) == 2
    p = client.get(f"/api/weddings/{w['id']}/photos/{photos[0]['id']}/preview", headers=H)
    assert p.status_code == 200 and p.content[:3] == b"\xff\xd8\xff"


def test_refuses_bad_uploads(client):
    w = client.post("/api/weddings", headers=H, json={"name": "W"}).json()
    assert send(client, w["id"], "u1", data=b"not a picture").status_code == 415
    assert send(client, w["id"], "").status_code == 422
    assert send(client, "nope", "u1").status_code == 404
    assert client.post("/api/weddings", headers=H, json={"name": "  "}).status_code == 422
    r = client.post(f"/api/weddings/{w['id']}/photos", headers=H, data={"meta": "{broken"},
                    files={"preview": ("a.jpg", jpeg(), "image/jpeg")})
    assert r.status_code == 422


def test_delete_removes_images_from_the_server_only(client, tmp_path):
    w = client.post("/api/weddings", headers=H, json={"name": "W"}).json()
    other = client.post("/api/weddings", headers=H, json={"name": "Other"}).json()
    send(client, w["id"], "u1")
    send(client, other["id"], "u1")
    assert (tmp_path / "weddings" / w["id"]).exists()
    assert client.delete(f"/api/weddings/{w['id']}", headers=H).json()["deleted"]
    assert not (tmp_path / "weddings" / w["id"]).exists()
    assert client.get(f"/api/weddings/{w['id']}", headers=H).status_code == 404
    assert client.get(f"/api/weddings/{other['id']}", headers=H).json()["photos"] == 1


def test_browser_signs_in_with_the_key_and_gets_a_cookie(client):
    assert client.get("/").status_code == 200                       # the page itself is public, the data is not
    assert client.get("/api/session").status_code == 401
    assert client.post("/api/login", json={"key": KEY}).status_code == 200
    assert client.get("/api/session").json()["ok"]
    assert len(client.get("/api/presets").json()["presets"]) == 500
    client.post("/api/logout")
    client.cookies.clear()
    assert client.get("/api/presets").status_code == 401


def test_looks_per_folder_and_per_photo(client):
    w = client.post("/api/weddings", headers=H, json={"name": "W"}).json()
    photo = Image.effect_noise((320, 240), 40).convert("RGB")
    buf = io.BytesIO(); photo.save(buf, "JPEG")
    send(client, w["id"], "u1", "Haldi", "A.NEF", buf.getvalue())
    send(client, w["id"], "u2", "Haldi", "B.NEF", buf.getvalue())
    folder = client.get(f"/api/weddings/{w['id']}", headers=H).json()["folders"][0]
    a, b = client.get(f"/api/weddings/{w['id']}/photos?compact=1", headers=H).json()["photos"]
    assert a["look"] is None and set(a) >= {"id", "name", "folder", "v", "iso"}
    preset = client.get("/api/presets", headers=H).json()["presets"][0]["id"]
    url = f"/api/weddings/{w['id']}/photos/{a['id']}"

    plain = client.get(url + "/image?size=360", headers=H).content
    assert plain[:3] == b"\xff\xd8\xff"
    saved = client.put(f"/api/weddings/{w['id']}/folders/{folder['id']}/look", headers=H,
                       json={"mode": "preset_auto", "preset": preset, "toggles": ["exposure", "bogus"], "ev": 99}).json()["look"]
    assert saved["toggles"] == ["exposure"] and saved["ev"] == 5 and saved["preset"] == preset
    with_look = client.get(url + "/image?size=360", headers=H).content
    assert with_look != plain                                        # the folder's look reaches its photos

    own = client.put(url + "/look", headers=H, json={"mode": "auto", "preset": preset}).json()["look"]
    assert own["preset"] is None and len(own["toggles"]) == 9        # Auto Correct: no preset, everything automatic
    assert client.get(url + "/image?size=360", headers=H).content != with_look
    d = client.get(url + "/develop", headers=H).json()
    assert "Exposure2012" in d["auto"] and "temp" in d["auto_wb"]
    assert client.get(f"/api/weddings/{w['id']}/photos/{b['id']}/image?size=360", headers=H).content == \
        client.get(f"/api/weddings/{w['id']}/photos/{b['id']}/image?size=360", headers=H).content

    r = client.request("PUT", url + "/look", headers={**H, "Content-Type": "application/json"}, content="null")
    assert r.json()["look"] is None
    assert client.get(url + "/image?size=360", headers=H).content == with_look
    assert client.get(url + "/image?size=1600&base=1", headers=H).status_code == 200
    assert client.get(url + "/image?size=123", headers=H).status_code == 422
    assert client.get(url + "/image?size=800&look=%7Bbroken", headers=H).status_code == 422
    assert client.put(f"/api/weddings/{w['id']}/folders/999/look", headers=H, json={}).status_code == 404
