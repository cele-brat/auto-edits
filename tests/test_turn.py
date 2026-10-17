"""Turning a photo by 90 / 180 degrees, and straightening up to 45: only settings, the photo never changes."""
import io
import json

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from server.main import create_app

KEY = "test-key-0123456789"
H = {"X-Auto-Edits-Key": KEY}


def marked_jpeg():
    img = np.full((1200, 1800, 3), 90, np.uint8)
    img[:180, :180] = (240, 30, 30)                      # a red corner, top left
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG", quality=95)
    return buf.getvalue()


def test_turn_and_crop(tmp_path):
    c = TestClient(create_app(str(tmp_path), KEY))
    w = c.post("/api/weddings", headers=H, json={"name": "W"}).json()
    meta = {"uuid": "u1", "folder": "Haldi", "fileName": "A.NEF", "taken": "2026-01-01T10:00:00"}
    c.post(f"/api/weddings/{w['id']}/photos", headers=H, data={"meta": json.dumps(meta)},
           files={"preview": ("a.jpg", marked_jpeg(), "image/jpeg")})
    pid = c.get(f"/api/weddings/{w['id']}/photos", headers=H).json()["photos"][0]["id"]
    url = f"/api/weddings/{w['id']}/photos/{pid}"

    def picture(**params):
        r = c.get(url + "/image", headers=H, params={"size": 800, **params})
        return np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB")).astype(int)

    assert picture().shape[:2] == (533, 800)
    assert c.put(url + "/turn", headers=H, json={"by": 90}).json()["crop"] == {"turn": 90}
    a = picture()
    assert a.shape[:2] == (800, 533)                          # upright now
    assert a[10, -10, 0] > 180 and a[10, -10, 1] < 90         # the red corner went to the top right
    assert picture(base=1).shape[:2] == (800, 533)            # the "before" picture turns too
    small = c.get(url + "/image", headers=H, params={"size": 360}).content
    assert Image.open(io.BytesIO(small)).size == (240, 360)    # and the grid picture

    # a crop made now is of the turned photo; it keeps the turn, and turns with the photo
    kept = c.put(url + "/crop", headers=H, json={"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.4, "ratio": "free"}).json()["crop"]
    assert kept["turn"] == 90
    back = c.put(url + "/turn", headers=H, json={"by": -90}).json()["crop"]
    assert "turn" not in back and abs(back["w"] - 0.4) < 1e-6 and abs(back["h"] - 0.5) < 1e-6
    c.put(url + "/turn", headers=H, json={"by": 180})
    folder = c.get(f"/api/weddings/{w['id']}", headers=H).json()["folders"][0]["id"]
    assert c.delete(f"/api/weddings/{w['id']}/folders/{folder}/crops", headers=H).json()["cleared"] == 1
    assert c.get(url, headers=H).status_code in (200, 404, 405)
    photos = c.get(f"/api/weddings/{w['id']}/photos?compact=1", headers=H).json()["photos"]
    assert photos[0]["crop"] == {"turn": 180}                 # removing crops keeps the turn
    assert c.put(url + "/turn", headers=H, json={"by": 45}).status_code == 422


def test_straighten_goes_to_45():
    from server.looks import clean_look
    assert clean_look({"angle": 40})["angle"] == 40 and clean_look({"angle": 90})["angle"] == 45
