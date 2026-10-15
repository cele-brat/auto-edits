import io

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from engine import crop
from server.main import create_app
from tests.test_server import H, KEY, send


def test_clean_keeps_a_crop_inside_the_picture():
    assert crop.clean(None) is None and crop.clean({"x": "a"}) is None
    assert crop.clean({"x": 0, "y": 0, "w": 1, "h": 1}) is None                    # the whole picture is no crop
    c = crop.clean({"x": 0.9, "y": -1, "w": 0.5, "h": 0.01, "ratio": "nonsense", "auto": "yes"})
    assert c == {"x": 0.5, "y": 0.0, "w": 0.5, "h": crop.MIN_SIDE, "ratio": "free", "auto": False}
    img = np.zeros((100, 200, 3), np.uint8)
    assert crop.apply(img, crop.clean({"x": 0.25, "y": 0.1, "w": 0.5, "h": 0.5})).shape == (50, 100, 3)


def test_framing_rules():
    W, H_ = 1500, 1000
    # one person to the right, small in a wide scene: tightened, original shape, eyes on the upper third, on the right third
    face = (1050, 520, 1150, 640)
    got, why = crop.frame({"size": (W, H_), "faces": [face], "people": (980, 500, 1300, 1000)})
    assert got and got["auto"] and got["ratio"] == "original" and why == "framed on 1 face"
    x0, y0, cw, ch = got["x"] * W, got["y"] * H_, got["w"] * W, got["h"] * H_
    assert abs(cw / ch - W / H_) < 0.01                                          # the photo's own shape
    assert got["w"] >= crop.MIN_SCALE - 1e-6                                   # never zoomed in too far
    assert x0 <= face[0] and x0 + cw >= face[2] and y0 <= face[1] - 40 and y0 + ch >= face[3]    # face whole, with headroom
    eyes = face[1] + 0.42 * (face[3] - face[1])
    assert abs((eyes - y0) / ch - 1 / 3) < 0.05
    assert (1110 - x0) / cw > 0.55                                               # sits right of centre
    # a person who already fills the frame, and a picture with nobody in it, are left alone
    assert crop.frame({"size": (W, H_), "faces": [(500, 100, 900, 600)], "people": (100, 50, 1400, 1000)})[0] is None
    assert crop.frame({"size": (W, H_), "faces": [], "people": None}) == (None, "no people found")
    # a shape asked for is kept
    sq, _ = crop.frame({"size": (W, H_), "faces": [face], "people": (980, 500, 1300, 1000)}, 1.0)
    assert sq["ratio"] == "1:1" and abs(sq["w"] * W - sq["h"] * H_) < 2


def test_crop_is_a_setting_on_the_photo(tmp_path):
    c = TestClient(create_app(str(tmp_path), KEY))
    w = c.post("/api/weddings", headers=H, json={"name": "W"}).json()
    buf = io.BytesIO(); Image.effect_noise((2000, 1500), 40).convert("RGB").save(buf, "JPEG")
    for u in ("u1", "u2"):
        send(c, w["id"], u, "Haldi", u + ".NEF", buf.getvalue())
    photos = c.get(f"/api/weddings/{w['id']}/photos?compact=1", headers=H).json()["photos"]
    pid = photos[0]["id"]
    base = f"/api/weddings/{w['id']}/photos/{pid}"
    assert photos[0]["crop"] is None
    saved = c.put(f"{base}/crop", headers=H, json={"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.25, "ratio": "free"}).json()["crop"]
    assert saved["w"] == 0.5
    assert [p for p in c.get(f"/api/weddings/{w['id']}/photos?compact=1", headers=H).json()["photos"] if p["id"] == pid][0]["crop"] == saved
    look = {"mode": "preset", "preset": None}
    img = Image.open(io.BytesIO(c.get(f"{base}/image?size=800&look=%7B%7D", headers=H).content))
    assert img.size == (800, 300)                                              # cropped preview
    whole = Image.open(io.BytesIO(c.get(f"{base}/image?size=800&uncropped=1&look=%7B%7D", headers=H).content))
    assert whole.size == (800, 600)                                            # the crop tool sees the whole picture
    # a look saved for the photo does not carry or lose the crop
    c.put(f"{base}/look", headers=H, json={**look, "ev": 0.2, "crop": {"x": 0, "y": 0, "w": 0.1, "h": 0.1}})
    assert c.get(f"{base}/develop", headers=H).json()["look"]["crop"]["w"] == 0.5
    # Lightroom gets an ordinary crop
    edit = {p["uuid"]: p for p in c.get(f"/api/weddings/{w['id']}/apply", headers=H).json()["photos"]}["u1"]["edit"]
    s = edit["settings"]
    assert s["HasCrop"] is True and (s["CropLeft"], s["CropTop"], s["CropRight"], s["CropBottom"]) == (0.25, 0.25, 0.75, 0.5)
    # AI crop on a picture with nobody in it leaves it alone; a hand-made crop is kept for a whole folder
    r = c.post(f"/api/weddings/{w['id']}/photos/{photos[1]['id']}/auto-crop?save=1", headers=H).json()
    assert r["crop"] is None and r["why"] == "no people found"
    r = c.post(f"{base}/auto-crop?save=1&keep_own=1", headers=H).json()
    assert r["changed"] is False and r["crop"]["w"] == 0.5
    # clearing a folder puts every photo back to whole; the uploaded photo was never changed
    folder = c.get(f"/api/weddings/{w['id']}", headers=H).json()["folders"][0]["id"]
    assert c.delete(f"/api/weddings/{w['id']}/folders/{folder}/crops", headers=H).json()["cleared"] == 1
    assert Image.open(io.BytesIO(c.get(f"{base}/image?size=800&look=%7B%7D", headers=H).content)).size == (800, 600)
    assert c.put(f"{base}/crop", headers=H, json=None).json()["crop"] is None


def test_export_is_cropped(tmp_path):
    from server.worker import work
    c = TestClient(create_app(str(tmp_path), KEY))
    w = c.post("/api/weddings", headers=H, json={"name": "W"}).json()
    buf = io.BytesIO(); Image.effect_noise((900, 600), 40).convert("RGB").save(buf, "JPEG")
    send(c, w["id"], "u1", "Haldi", "A.NEF", buf.getvalue())
    pid = c.get(f"/api/weddings/{w['id']}/photos?compact=1", headers=H).json()["photos"][0]["id"]
    c.put(f"/api/weddings/{w['id']}/photos/{pid}/crop", headers=H, json={"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5})
    e = c.post(f"/api/weddings/{w['id']}/exports", headers=H, json={"size": 0}).json()
    assert work(idle_exit=True, data_dir=str(tmp_path)) == 1
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/api/exports/{e['id']}/zip", headers=H).content))
    assert Image.open(io.BytesIO(z.read(z.namelist()[0]))).size == (450, 300)


def test_reset_a_folder_to_original(tmp_path):
    c = TestClient(create_app(str(tmp_path), KEY))
    w = c.post("/api/weddings", headers=H, json={"name": "W"}).json()
    for u in ("u1", "u2"):
        send(c, w["id"], u, "Haldi", u + ".NEF")
    folder = c.get(f"/api/weddings/{w['id']}", headers=H).json()["folders"][0]["id"]
    photos = c.get(f"/api/weddings/{w['id']}/photos?compact=1", headers=H).json()["photos"]
    c.put(f"/api/weddings/{w['id']}/folders/{folder}/look", headers=H, json={"mode": "auto"})
    c.put(f"/api/weddings/{w['id']}/photos/{photos[0]['id']}/look", headers=H, json={"mode": "preset", "ev": 1})
    c.put(f"/api/weddings/{w['id']}/photos/{photos[1]['id']}/crop", headers=H, json={"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5})
    assert c.post(f"/api/weddings/{w['id']}/folders/{folder}/reset", headers=H).json()["photos_with_own"] == 2
    after = c.get(f"/api/weddings/{w['id']}/photos?compact=1", headers=H).json()["photos"]
    assert all(p["look"] is None and p["crop"] is None for p in after)
    assert c.get(f"/api/weddings/{w['id']}", headers=H).json()["folders"][0]["settings"] in ({}, None)
