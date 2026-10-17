"""Create preset: what the sliders show becomes a preset of your own, also as a Lightroom file."""
import io
import json

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from engine.presets import parse_xmp
from server.main import create_app

KEY = "test-key-0123456789"
H = {"X-Auto-Edits-Key": KEY}


def photo_jpeg():
    y, x = np.mgrid[0:240, 0:360]
    img = np.stack([x * 255 // 360, y * 255 // 240, (x + y) * 255 // 600], -1).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "JPEG", quality=95)
    return buf.getvalue()


def setup(tmp_path):
    c = TestClient(create_app(str(tmp_path), KEY))
    w = c.post("/api/weddings", headers=H, json={"name": "W"}).json()
    meta = {"uuid": "u1", "folder": "Haldi", "fileName": "A.NEF", "taken": "2026-01-01T10:00:00", "iso": 400}
    c.post(f"/api/weddings/{w['id']}/photos", headers=H, data={"meta": json.dumps(meta)},
           files={"preview": ("a.jpg", photo_jpeg(), "image/jpeg")})
    p = c.get(f"/api/weddings/{w['id']}/photos", headers=H).json()["photos"][0]
    return c, w["id"], p["id"]


def pixels(c, wid, pid, look):
    r = c.get(f"/api/weddings/{wid}/photos/{pid}/image", headers=H,
              params={"size": 800, "look": json.dumps(look)})
    assert r.status_code == 200
    return np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB")).astype(int)


def test_create_preset_matches_the_look(tmp_path):
    c, wid, pid = setup(tmp_path)
    base = next(p for p in c.get("/api/presets", headers=H).json()["presets"] if not p["masks"])
    look = {"mode": "preset", "preset": base["id"], "amount": 1, "ev": 0.4, "hl": -20, "temp": 8,
            "adj": {"Contrast2012": 12, "Vibrance": 10}}
    r = c.post("/api/presets/new", headers=H, json={"name": "Haldi warm", "group": "Bharat", "look": look})
    assert r.status_code == 200, r.text
    made = r.json()["id"]
    listed = {p["id"]: p for p in c.get("/api/presets", headers=H).json()["presets"]}
    assert listed[made]["pack"] == "My presets" and listed[made]["made"] and listed[made]["look"] == "Bharat"
    assert listed[made]["s"]["Contrast2012"] == base["s"].get("Contrast2012", 0) + 12
    before = pixels(c, wid, pid, look)
    after = pixels(c, wid, pid, {"mode": "preset", "preset": made})
    assert np.abs(before - after).mean() < 1.0             # the new preset alone gives the same picture

    x = c.get(f"/api/presets/{made}/xmp", headers=H)
    assert x.status_code == 200 and "attachment" in x.headers["content-disposition"]
    p = parse_xmp(x.text, "x.xmp")
    assert p.name == "Haldi warm" and p.group == "Bharat"
    assert abs(p.settings["Exposure2012"] - (base["s"].get("Exposure2012", 0) + 0.4)) < 1e-6


def test_update_and_limits(tmp_path):
    c, wid, pid = setup(tmp_path)
    assert c.post("/api/presets/new", headers=H, json={"name": "  ", "look": {}}).status_code == 422
    made = c.post("/api/presets/new", headers=H, json={"name": "Mine", "look": {"ev": 0.5}}).json()["id"]
    r = c.post("/api/presets/new", headers=H, json={"name": "Mine v2", "look": {"ev": 1.0}, "replace": made})
    assert r.json() == {"id": made, "name": "Mine v2", "replaced": True}
    listed = {p["id"]: p for p in c.get("/api/presets", headers=H).json()["presets"]}
    assert listed[made]["name"] == "Mine v2" and listed[made]["s"]["Exposure2012"] == 1.0
    built_in = next(iter(listed))
    assert c.post("/api/presets/new", headers=H, json={"name": "X", "look": {}, "replace": built_in}).status_code == 404
    assert c.get(f"/api/presets/{built_in}/xmp", headers=H).status_code == 404


def test_masks_and_white_balance_travel_with_the_preset(tmp_path):
    c, wid, pid = setup(tmp_path)
    look = {"mode": "preset", "temp": 20, "masks": {"background": {"ev": -0.5}}}
    made = c.post("/api/presets/new", headers=H, json={"name": "Moody", "look": look}).json()["id"]
    listed = {p["id"]: p for p in c.get("/api/presets", headers=H).json()["presets"]}
    assert listed[made]["own_masks"] == ["background"] and listed[made]["wb"] == {"temp": 20.0, "tint": 0.0}
    plain = c.post("/api/presets/new", headers=H,
                   json={"name": "Plain", "look": look, "masks": False, "white_balance": False}).json()["id"]
    listed = {p["id"]: p for p in c.get("/api/presets", headers=H).json()["presets"]}
    assert listed[plain]["own_masks"] == [] and listed[plain]["wb"] is None
