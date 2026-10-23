"""Skin retouching: faces found once and kept, smoothing keeps texture, spots healed, previews and exports changed."""
import io
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from engine import retouch
from engine.presets import parse_xmp
from server.looks import clean_look, default_look
from server.main import create_app

KEY = "test-key-0123456789"
H = {"X-Auto-Edits-Key": KEY}


@pytest.fixture(scope="module")
def face8():
    from skimage import data
    return data.astronaut()                          # a real face, 512 px


def test_clean_keeps_only_sane_values():
    r = retouch.clean({"smooth": 250, "blemish": -5, "eyes": "40", "teeth": None,
                       "spots": [{"x": 0.5, "y": 0.5, "r": 0.5}, {"x": 2, "y": 0}, "junk"]})
    assert r["smooth"] == 100 and r["blemish"] == 0 and r["eyes"] == 40 and r["teeth"] == 0
    assert r["spots"] == [{"x": 0.5, "y": 0.5, "r": 0.08}]
    assert retouch.is_off(retouch.clean({})) and retouch.is_off(retouch.clean({"smooth": 0}))
    assert not retouch.is_off(retouch.clean({"spots": [{"x": 0.1, "y": 0.1}]}))
    look = clean_look({"retouch": {"smooth": 30}})
    assert look["retouch"] == {"smooth": 30, "blemish": 0, "eyes": 0, "teeth": 0, "spots": []}
    assert clean_look({})["retouch"] == {} and "retouch" in default_look()


def test_landmarks_and_masks(face8):
    faces = retouch.landmarks(face8)
    assert len(faces) == 1 and len(faces[0]["pts"]) >= 468
    assert all(0 <= x <= 1 and 0 <= y <= 1 for x, y in faces[0]["pts"])
    h, w = face8.shape[:2]
    skin = retouch.skin_mask(faces[0], h, w)
    iris, whites = retouch.eye_masks(faces[0], h, w)
    assert skin.shape == (h, w) and 0.005 < skin.mean() < 0.2
    assert iris.max() > 0.5 and whites.max() > 0.5
    assert (skin * iris).max() < 0.3                 # the eyes are cut out of the skin


def test_smoothing_evens_skin_but_keeps_texture(face8):
    faces = retouch.landmarks(face8)
    h, w = face8.shape[:2]
    skin = retouch.skin_mask(faces[0], h, w)
    out = retouch.apply(face8, {"smooth": 80}, faces)
    inside = skin > 0.9
    outside = skin < 0.01
    assert np.abs(out[outside].astype(int) - face8[outside].astype(int)).max() == 0        # nothing outside the skin moves
    assert np.abs(out[inside].astype(int) - face8[inside].astype(int)).mean() > 0.3
    # the local variation (texture) remains, only lessened
    def texture(img):
        g = img.astype(np.float32).mean(2)
        import cv2
        return float(np.abs(g - cv2.GaussianBlur(g, (0, 0), 1.0))[inside].mean())
    assert 0.3 * texture(face8) < texture(out) < texture(face8)
    assert retouch.apply(face8, {"smooth": 0}, faces) is face8


def test_heal_spots_and_blemishes(face8):
    faces = retouch.landmarks(face8)
    skin = retouch.skin_mask(faces[0], 512, 512)
    ys, xs = np.where(skin > 0.95)
    cy, cx = int(np.median(ys)), int(np.median(xs))              # well inside the cheek
    img = face8.copy()
    before = img[cy, cx].astype(int).sum()
    img[cy - 1:cy + 2, cx - 1:cx + 2] = (40, 20, 20)             # a dark spot on the skin
    out = retouch.heal_spots(img, [{"x": cx / 512, "y": cy / 512, "r": 0.012}])
    assert abs(out[cy, cx].astype(int).sum() - before) < abs(80 - before)   # back near the skin's own colour
    found = retouch.find_blemishes(img, skin, 80, retouch.face_width(faces[0], 512))
    assert found.shape == (512, 512) and found[cy, cx] == 255           # the spot is found by itself
    auto = retouch.apply(img, {"blemish": 80}, faces)
    assert auto[cy, cx].astype(int).sum() > 80 + 100


def test_eyes_and_teeth(face8):
    faces = retouch.landmarks(face8)
    iris, whites = retouch.eye_masks(faces[0], *face8.shape[:2])
    out = retouch.apply(face8, {"eyes": 100}, faces)
    where = whites > 0.8
    assert out[where].mean() > face8[where].mean()
    teeth = retouch.teeth_mask(faces[0], face8)
    assert teeth.shape == face8.shape[:2]
    assert retouch.whiten_teeth(face8, teeth, 100).shape == face8.shape


def send(c, wid, face8):
    buf = io.BytesIO()
    Image.fromarray(face8).save(buf, "JPEG", quality=95)
    meta = {"uuid": "u1", "folder": "Haldi", "fileName": "A.NEF", "taken": "2026-01-01T10:00:00", "iso": 400}
    return c.post(f"/api/weddings/{wid}/photos", headers=H, data={"meta": json.dumps(meta)},
                  files={"preview": ("a.jpg", buf.getvalue(), "image/jpeg")}).json()["id"]


def test_retouch_in_preview_export_and_presets(tmp_path, face8):
    app = create_app(str(tmp_path), KEY)
    c = TestClient(app)
    w = c.post("/api/weddings", headers=H, json={"name": "W"}).json()
    pid = send(c, w["id"], face8)
    url = f"/api/weddings/{w['id']}/photos/{pid}"
    plain = c.get(url + "/image", headers=H, params={"size": 800, "look": json.dumps({"mode": "preset"})}).content
    look = {"mode": "preset", "retouch": {"smooth": 70, "eyes": 50, "spots": [{"x": 0.45, "y": 0.4, "r": 0.01}]}}
    done = c.get(url + "/image", headers=H, params={"size": 800, "look": json.dumps(look)}).content
    assert done != plain
    small_a = c.get(url + "/image", headers=H, params={"size": 360, "look": json.dumps({"mode": "preset"})}).content
    small_b = c.get(url + "/image", headers=H, params={"size": 360, "look": json.dumps(look)}).content
    assert len(small_a) == len(small_b)                   # grid size: retouch is skipped for speed
    # the landmarks are kept in the database after the first render
    assert app.state.store.get_faces(pid, float(app.state.store.photo(w["id"], pid)["received"]), "mesh:0:0.0")
    # saved on the photo, and in the Lightroom edit as "not yet"
    saved = c.put(url + "/look", headers=H, json=look).json()["look"]
    assert saved["retouch"]["smooth"] == 70 and saved["retouch"]["spots"] == [{"x": 0.45, "y": 0.4, "r": 0.01}]
    item = c.get(f"/api/weddings/{w['id']}/apply", headers=H).json()["photos"][0]
    assert "skin retouching" in item["edit"]["not_yet"]
    # the export renderer applies it too
    r = app.state.renderer
    path = app.state.store.preview_path(w["id"], pid)
    a = r.export(path, clean_look({"mode": "preset"}), 800)
    b = r.export(path, clean_look(look), 800)
    assert a != b
    # a preset made from the look carries the sliders, not the healed spots
    made = c.post("/api/presets/new", headers=H, json={"name": "Soft skin", "look": look}).json()
    cat = {p["id"]: p for p in c.get("/api/presets", headers=H).json()["presets"]}
    assert cat[made["id"]]["retouch"] == {"smooth": 70, "blemish": 0, "eyes": 50, "teeth": 0, "spots": []}
    via_preset = c.get(url + "/image", headers=H, params={"size": 800, "look": json.dumps({"mode": "preset", "preset": made["id"]})}).content
    assert via_preset != plain
    assert parse_xmp(c.get(f"/api/presets/{made['id']}/xmp", headers=H).text).name == "Soft skin"
