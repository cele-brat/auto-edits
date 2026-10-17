"""Masks you draw: radial, linear and brush, in previews, exports and long looks."""
import io
import json

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from engine import shapes
from server.looks import MASK_ADJUST, clean_look
from server.main import create_app

KEY = "test-key-0123456789"
H = {"X-Auto-Edits-Key": KEY}


def grey_jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (600, 400), (110, 110, 110)).save(buf, "JPEG", quality=95)
    return buf.getvalue()


def setup(tmp_path):
    c = TestClient(create_app(str(tmp_path), KEY))
    w = c.post("/api/weddings", headers=H, json={"name": "W"}).json()
    meta = {"uuid": "u1", "folder": "Haldi", "fileName": "A.NEF", "taken": "2026-01-01T10:00:00"}
    c.post(f"/api/weddings/{w['id']}/photos", headers=H, data={"meta": json.dumps(meta)},
           files={"preview": ("a.jpg", grey_jpeg(), "image/jpeg")})
    return c, w["id"], c.get(f"/api/weddings/{w['id']}/photos", headers=H).json()["photos"][0]["id"]


def view(c, wid, pid, look=None, **extra):
    params = {"size": 800, **extra}
    if look is not None:
        params["look"] = json.dumps(look)
    r = c.get(f"/api/weddings/{wid}/photos/{pid}/image", headers=H, params=params)
    assert r.status_code == 200, r.text
    return np.asarray(Image.open(io.BytesIO(r.content)).convert("L")).astype(int)


def test_masks_have_the_right_shape():
    r = shapes.mask({"type": "radial", "cx": 0.5, "cy": 0.5, "rx": 0.25, "ry": 0.25, "feather": 20}, 100, 200)
    assert r[50, 100] == 1 and r[5, 5] == 0 and 0 < r[50, 145] < 1
    lin = shapes.mask({"type": "linear", "x0": 0.5, "y0": 0.0, "x1": 0.5, "y1": 0.5}, 100, 100)
    assert lin[0, 50] == 1 and lin[80, 50] == 0 and 0 < lin[25, 50] < 1
    b = shapes.mask({"type": "brush", "strokes": [
        {"size": 0.2, "feather": 0, "flow": 100, "erase": False, "pts": [0.2, 0.5, 0.8, 0.5]},
        {"size": 0.2, "feather": 0, "flow": 100, "erase": True, "pts": [0.5, 0.5]}]}, 100, 100)
    assert b[50, 25] > 0.9 and b[50, 50] < 0.1 and b[10, 50] == 0
    inv = shapes.mask({"type": "radial", "cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.2, "feather": 0, "invert": True}, 50, 50)
    assert inv[25, 25] == 0 and inv[0, 0] == 1


def test_looks_keep_only_good_shapes():
    look = clean_look({"shapes": [{"type": "radial", "cx": 9, "adj": {"ev": 5, "nope": 3}}, {"type": "lasso"}, "x",
                                  {"type": "brush", "strokes": [{"pts": list(range(20000))}]}]})
    radial, brush = look["shapes"]
    assert radial["cx"] == 1.5 and radial["adj"] == {"ev": 2.0}
    assert len(brush["strokes"][0]["pts"]) == 2 * shapes.MAX_POINTS
    assert len(clean_look({"shapes": [{"type": "linear"}] * 50})["shapes"]) == shapes.MAX_SHAPES


def test_radial_brightens_only_inside_and_long_looks_travel_by_key(tmp_path):
    c, wid, pid = setup(tmp_path)
    look = {"shapes": [{"type": "radial", "cx": 0.5, "cy": 0.5, "rx": 0.2, "ry": 0.2, "feather": 10, "adj": {"ev": 1.0}}]}
    img = view(c, wid, pid, look)
    h, w = img.shape
    assert img[h // 2, w // 2] > img[5, 5] + 30                     # brighter in the middle, the corners untouched
    red = np.asarray(Image.open(io.BytesIO(c.get(f"/api/weddings/{wid}/photos/{pid}/image", headers=H,
                     params={"size": 800, "look": json.dumps(look), "show": "shape0"}).content)).convert("RGB")).astype(int)
    assert red[h // 2, w // 2, 0] > red[h // 2, w // 2, 1] + 40      # shown in red when asked
    key = c.post("/api/looks", headers=H, json=look).json()["key"]
    assert np.abs(view(c, wid, pid, lk=key) - img).max() <= 2        # the same picture through the short key
    assert c.get(f"/api/weddings/{wid}/photos/{pid}/image", headers=H, params={"size": 800, "lk": "nope"}).status_code == 410
    # saved on the folder, it shows in the grid picture and the export
    folder = c.get(f"/api/weddings/{wid}", headers=H).json()["folders"][0]["id"]
    assert c.put(f"/api/weddings/{wid}/folders/{folder}/look", headers=H, json=look).status_code == 200
    saved = view(c, wid, pid)
    assert saved[h // 2, w // 2] > saved[5, 5] + 30
