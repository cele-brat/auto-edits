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


def test_own_adjustments_add_on_top(client, tmp_path):
    from pathlib import Path
    from server.looks import Renderer, clean_look, straighten
    import numpy as np
    r = Renderer()
    photo = tmp_path / "p.jpg"
    Image.effect_noise((320, 240), 40).convert("RGB").save(photo, "JPEG")
    preset = next(p for p in r.presets.values() if p["settings"].get("Highlights2012"))
    base = preset["settings"]["Highlights2012"]
    d = r.develop(photo, {"mode": "preset", "preset": preset["id"], "hl": -20, "noise": 30, "angle": 2.5})
    assert d["settings"]["Highlights2012"] == max(-100, base - 20)          # preset value plus your own change
    assert d["settings"]["LuminanceSmoothing"] == min(100, (preset["settings"].get("LuminanceSmoothing") or 0) + 30)
    assert d["angle"] == 2.5
    auto = r.develop(photo, {"mode": "preset_auto", "preset": preset["id"], "toggles": ["highlights"], "hl": 10})
    assert auto["settings"]["Highlights2012"] == min(100, auto["auto"]["Highlights2012"] + 10)   # auto value plus your own change
    assert clean_look({"hl": 900, "angle": -99})["hl"] == 100 and clean_look({"angle": -99})["angle"] == -15
    img = np.zeros((60, 90, 3), np.float32); img[:, :45] = 1
    turned = straighten(img, 5)
    assert turned.shape == img.shape and not np.array_equal(turned, img) and straighten(img, 0) is img
    assert r.render(photo, {"mode": "preset", "angle": 3}, 360) != r.render(photo, {"mode": "preset"}, 360)


def test_masks_change_only_their_part(tmp_path):
    import numpy as np
    from server.looks import Renderer, clean_look, local_adjust, show_mask
    look = clean_look({"masks": {"face_skin": {"texture": -60, "clarity": 500, "junk": 3}, "hair": {"ev": 0},
                                 "sky": {"sat": 40}, "made_up": {"ev": 1}}})
    assert look["masks"] == {"face_skin": {"texture": -60, "clarity": 100}, "sky": {"sat": 40}}

    img = np.full((40, 60, 3), 0.4, np.float32)
    left = np.zeros((20, 30), np.float32); left[:, :15] = 1            # masks are smaller than the picture
    out = local_adjust(img, {"subject": left, "sky": np.zeros((20, 30), np.float32)}, {"subject": {"ev": 1.0}, "sky": {"ev": 1.0}})
    assert out[:, :25].mean() > 0.5 and np.allclose(out[:, 35:], 0.4, atol=1e-3)   # brighter inside, untouched outside
    red = show_mask(img, left)
    assert red[20, 5, 0] > red[20, 5, 1] and np.allclose(red[20, 55], 0.4)

    photo = tmp_path / "p.jpg"
    Image.effect_noise((320, 240), 40).convert("RGB").save(photo, "JPEG")
    r = Renderer()
    masks = r.masks(photo)                                             # made once, then kept beside the preview
    assert set(masks) >= {"subject", "background", "face_skin", "body_skin", "hair", "clothes", "eyes", "sky",
                          "iris_pupil", "eye_sclera", "eyebrows", "lips", "teeth", "facial_hair", "whole_person"}
    assert all(m.ndim == 2 and 0 <= m.min() and m.max() <= 1 for m in masks.values())
    assert photo.with_suffix(".masks.npz").exists()
    assert r.render(photo, {"masks": {"background": {"ev": 1}}}, 360) != r.render(photo, {}, 360)


def test_upload_a_lightroom_preset_with_ai_masks(client):
    from pathlib import Path
    xmp = (Path(__file__).parent / "fixtures" / "polished_bright_2.xmp").read_bytes()
    assert client.post("/api/presets", files=[("files", ("a.xmp", xmp))]).status_code == 401
    r = client.post("/api/presets", headers=H, files=[("files", ("Polished Bright 2.xmp", xmp, "application/xml")),
                                                      ("files", ("holiday.jpg", jpeg(), "image/jpeg"))]).json()["results"]
    good, bad = r
    assert good["ok"] and good["name"] == "Polished Bright 2" and good["masks"] == 10 and not good["replaced"]
    assert not bad["ok"] and "not a Lightroom preset" in bad["why"]
    pid = good["id"]
    mine = [p for p in client.get("/api/presets", headers=H).json()["presets"] if p["mine"]]
    assert len(mine) == 1 and mine[0]["pack"] == "My presets" and mine[0]["id"] == pid
    kinds = {k for m in mine[0]["masks"] for k in m["parts"]}
    assert kinds == {"subject", "background", "clothes", "face_skin", "eye_sclera", "iris_pupil", "teeth"}
    skin = next(m for m in mine[0]["masks"] if m["name"] == "Facial Skin")
    assert skin["adjust"] == {"clarity": -10.0, "texture": -20.0} and skin["shown"]          # Lightroom's slider numbers
    iris = next(m for m in mine[0]["masks"] if m["name"] == "Iris and Pupil")
    assert iris["adjust"]["ev"] == 0.35

    w = client.post("/api/weddings", headers=H, json={"name": "W"}).json()
    buf = io.BytesIO(); Image.effect_noise((320, 240), 40).convert("RGB").save(buf, "JPEG")
    send(client, w["id"], "u1", "Haldi", "A.NEF", buf.getvalue())
    photo = client.get(f"/api/weddings/{w['id']}/photos?compact=1", headers=H).json()["photos"][0]
    url = f"/api/weddings/{w['id']}/photos/{photo['id']}"
    look = lambda **more: {"look": json.dumps({"mode": "preset", "preset": pid, **more})}
    with_masks = client.get(url + "/image?size=360", headers=H, params=look()).content
    without = client.get(url + "/image?size=360", headers=H, params=look(preset_masks=False)).content
    assert with_masks != without                                 # the background mask alone changes this picture
    d = client.get(url + "/develop", headers=H, params=look()).json()
    assert len(d["preset_masks"]) == 10 and d["look"]["preset_masks"] is True
    assert client.get(url + "/develop", headers=H, params=look(preset_masks=False)).json()["preset_masks"] == []

    again = client.post("/api/presets", headers=H, files=[("files", ("x.xmp", xmp))]).json()["results"][0]
    assert again["replaced"] and again["id"] == pid              # same preset again: replaced, not doubled
    library = next(p["id"] for p in client.get("/api/presets", headers=H).json()["presets"] if not p["mine"])
    assert client.delete(f"/api/presets/{library}", headers=H).status_code == 404   # the built-in ones stay
    assert client.delete(f"/api/presets/{pid}", headers=H).json()["deleted"]
    assert not any(p["mine"] for p in client.get("/api/presets", headers=H).json()["presets"])


def test_edits_for_lightroom(client):
    from pathlib import Path
    w = client.post("/api/weddings", headers=H, json={"name": "W"}).json()
    buf = io.BytesIO(); Image.effect_noise((320, 240), 40).convert("RGB").save(buf, "JPEG")
    for u, folder in (("u1", "Haldi"), ("u2", "Haldi"), ("u3", "Sangeet")):
        send(client, w["id"], u, folder, u + ".NEF", buf.getvalue())
    folders = {f["name"]: f["id"] for f in client.get(f"/api/weddings/{w['id']}", headers=H).json()["folders"]}
    presets = client.get("/api/presets", headers=H).json()["presets"]
    preset = presets[0]["id"]
    client.put(f"/api/weddings/{w['id']}/folders/{folders['Haldi']}/look", headers=H,
               json={"mode": "preset_auto", "preset": preset, "toggles": ["exposure", "white_balance"], "ev": 0.3,
                     "hl": -10, "temp": 20, "angle": 2})
    got = client.get(f"/api/weddings/{w['id']}/apply", headers=H).json()
    assert got["total"] == 3
    by = {p["uuid"]: p for p in got["photos"]}
    assert by["u3"]["edit"] is None                                  # a folder with no look is left alone
    e = by["u1"]["edit"]
    assert e["preset"] == presets[0]["name"] and e["preset_uuid"] is None and e["from_no_edit"]
    d = client.get(f"/api/weddings/{w['id']}/photos/{[p for p in client.get(f'/api/weddings/{w['id']}/photos?compact=1', headers=H).json()['photos'] if p['name']=='u1.NEF'][0]['id']}/develop", headers=H).json()
    assert abs(e["settings"]["Exposure2012"] - (d["auto"]["Exposure2012"] + 0.3)) < 0.002     # auto value plus your own change
    assert "CropAngle" not in e["settings"] and e["not_yet"] == ["straighten"]
    assert e["wb"]["mired"] < 0 and e["wb"]["relative_temp"] == d["auto_wb"]["temp"] + 20      # warmer = fewer mired
    assert all(len(v) % 2 == 0 and len(v) >= 4 for v in e["curves"].values())
    assert by["u1"]["done"] is False and len(by["u1"]["stamp"]) == 16

    assert client.post(f"/api/weddings/{w['id']}/applied", headers=H, json={"u1": by["u1"]["stamp"]}).json()["marked"] == 1
    again = {p["uuid"]: p for p in client.get(f"/api/weddings/{w['id']}/apply", headers=H).json()["photos"]}
    assert again["u1"]["done"] is True and again["u2"]["done"] is False
    client.put(f"/api/weddings/{w['id']}/folders/{folders['Haldi']}/look", headers=H, json={"mode": "preset", "preset": preset})
    changed = {p["uuid"]: p for p in client.get(f"/api/weddings/{w['id']}/apply", headers=H).json()["photos"]}
    assert changed["u1"]["done"] is False                            # the look changed, so it is due again
    assert len(client.get(f"/api/weddings/{w['id']}/apply?offset=1&limit=1", headers=H).json()["photos"]) == 1

    # a preset uploaded from Lightroom with AI masks is applied by Lightroom itself
    xmp = (Path(__file__).parent / "fixtures" / "polished_bright_2.xmp").read_bytes()
    pid = client.post("/api/presets", headers=H, files=[("files", ("p.xmp", xmp))]).json()["results"][0]["id"]
    client.put(f"/api/weddings/{w['id']}/folders/{folders['Sangeet']}/look", headers=H,
               json={"mode": "preset_auto", "preset": pid, "toggles": ["exposure"], "sh": 5})
    m = {p["uuid"]: p for p in client.get(f"/api/weddings/{w['id']}/apply", headers=H).json()["photos"]}["u3"]["edit"]
    assert m["preset_uuid"] == "8EF01EEA28314F66B346C52D6E20C54A" and not m["from_no_edit"] and m["curves"] == {}
    assert set(m["settings"]) <= {"Exposure2012", "Shadows2012"} and "Exposure2012" in m["settings"]   # only what differs
    client.delete(f"/api/presets/{pid}", headers=H)


def test_every_lightroom_slider_in_lightroom_order(client, tmp_path):
    from server import adjustments
    from server.looks import Renderer, clean_look
    panels = client.get("/api/adjustments", headers=H).json()["panels"]
    assert [p["name"] for p in panels] == ["Basic", "Tone Curve", "Color Mixer", "Color Grading", "Detail",
                                           "Lens Corrections", "Transform", "Effects", "Calibration"]
    basic = [s["label"] for g in panels[0]["groups"] for s in g["sliders"]]
    assert basic == ["Temp", "Tint", "Exposure", "Contrast", "Highlights", "Shadows", "Whites", "Blacks",
                     "Texture", "Clarity", "Dehaze", "Vibrance", "Saturation"]
    assert len(adjustments.SLIDERS) > 85 and len({s["key"] for s in adjustments.SLIDERS.values()}) == len(adjustments.SLIDERS)

    look = clean_look({"adj": {"Contrast2012": 20, "Vibrance": 9999, "Made": 5, "Exposure2012": 1, "Sharpness": "x"}})
    assert look["adj"] == {"Contrast2012": 20, "Vibrance": 200}          # unknown and Auto-row keys are not kept here
    r = Renderer()
    photo = tmp_path / "p.jpg"
    Image.effect_noise((320, 240), 40).convert("RGB").save(photo, "JPEG")
    preset = next(p for p in r.presets.values() if p["settings"].get("Contrast2012") and p["settings"].get("SplitToningShadowHue"))
    own = preset["settings"]
    d = r.develop(photo, {"preset": preset["id"], "adj": {"Contrast2012": 20, "Vibrance": 200, "SplitToningShadowHue": 300,
                                                          "Sharpness": 30, "PerspectiveScale": -10}})["settings"]
    assert d["Contrast2012"] == min(100, own["Contrast2012"] + 20) and d["Vibrance"] == 100       # stays in Lightroom's range
    assert d["SplitToningShadowHue"] == (own["SplitToningShadowHue"] + 300) % 360               # hue goes round the wheel
    assert d["Sharpness"] == own.get("Sharpness", 40) + 30 and d["PerspectiveScale"] == 90     # starts from Lightroom's default
    assert r.render(photo, {"adj": {"Texture": 80}}, 360) != r.render(photo, {}, 360)
    mine = next(p for p in client.get("/api/presets", headers=H).json()["presets"] if p["id"] == preset["id"])
    assert mine["s"]["Contrast2012"] == own["Contrast2012"]                # the app can show the preset's own values
