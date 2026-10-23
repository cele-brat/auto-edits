"""Learn my style: edits grouped into styles, one preset per style, a plain summary."""
import io
import json
import zipfile

import numpy as np
from fastapi.testclient import TestClient

from engine import style
from engine.presets import parse_xmp, write_xmp
from server.main import create_app

KEY = "test-key-0123456789"
H = {"X-Auto-Edits-Key": KEY}


def xmp(name, **settings):
    return write_xmp(name, "", settings, {}, "A" * 32).encode()


WARM = dict(Contrast2012=15, Highlights2012=-30, Shadows2012=25, Vibrance=20, Saturation=-5, ColorGradeMidtoneHue=40, ColorGradeMidtoneSat=20)
MATTE = dict(Contrast2012=-20, Highlights2012=10, Shadows2012=40, Blacks2012=30, Saturation=-35, Clarity2012=-10)


def edits(kind, n, jitter=3):
    rng = np.random.default_rng(len(kind))
    out = []
    for i in range(n):
        s = {k: v + int(rng.integers(-jitter, jitter + 1)) for k, v in kind.items()}
        out.append({"name": f"{kind['Contrast2012']}-{i}", "settings": s, "curves": {}})
    return out


def test_kmeans_and_silhouette_find_two_clear_groups():
    x = np.vstack([np.random.default_rng(1).normal(0, 0.05, (12, 4)), np.random.default_rng(2).normal(1, 0.05, (12, 4))]).astype(np.float32)
    labels = style.best_grouping(x)
    assert len(set(labels)) == 2
    assert len(set(labels[:12])) == 1 and len(set(labels[12:])) == 1
    assert style.silhouette(x, labels) > 0.8
    # noise alone stays one group
    noise = np.random.default_rng(3).normal(0, 1, (20, 55)).astype(np.float32)       # as many numbers as a real edit vector
    assert len(set(style.best_grouping(noise))) == 1


def test_learn_makes_one_preset_per_style_and_averages():
    groups = style.learn(edits(WARM, 10) + edits(MATTE, 8))
    assert len(groups) == 2
    assert [len(g["members"]) for g in groups] == [10, 8]                 # largest first
    warm = groups[0]["settings"]
    assert abs(warm["Highlights2012"] + 30) <= 3 and abs(warm["Shadows2012"] - 25) <= 3
    assert 30 <= warm["ColorGradeMidtoneHue"] <= 50                       # a hue averages on the circle
    assert groups[1]["settings"]["Saturation"] < -25
    one = style.learn(edits(WARM, 4))                                      # too few for groups: one style
    assert len(one) == 1 and one[0]["share"] == 1


def test_hue_average_wraps_around_zero():
    e = [{"name": str(i), "settings": {"ColorGradeMidtoneHue": h, "ColorGradeMidtoneSat": 30}, "curves": {}} for i, h in enumerate((350, 355, 5, 10))]
    s, _ = style.average_edit(e)
    assert s["ColorGradeMidtoneHue"] in (0, 1, 359, 2, 358)


def test_scene_of():
    assert style.scene_of(200, 0.5, 0.0) == "bright outdoor"
    assert style.scene_of(3200, 0.3, 0.1) == "indoor tungsten"
    assert style.scene_of(800, 0.2, 0.0, kelvin=3200) == "indoor tungsten"
    assert style.scene_of(1600, 0.15, -0.05) == "dusk"


def test_learn_endpoint_reads_xmp_zip_csv_json_and_saves_presets(tmp_path):
    c = TestClient(create_app(str(tmp_path), KEY))
    files = [("files", (f"warm{i}.xmp", xmp(f"w{i}", **e["settings"]), "application/octet-stream")) for i, e in enumerate(edits(WARM, 6))]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, e in enumerate(edits(MATTE, 6)):
            z.writestr(f"folder/matte{i}.xmp", xmp(f"m{i}", **e["settings"]))
    files.append(("files", ("matte.zip", buf.getvalue(), "application/zip")))
    csv_text = "name,Contrast2012,Shadows2012\nx1.NEF,-18,38\nx2.NEF,-22,42\n"
    files.append(("files", ("more.csv", csv_text.encode(), "text/csv")))
    files.append(("files", ("more.json", json.dumps([{"name": "j1", "settings": {"Contrast2012": -20, "Shadows2012": 40}}]).encode(), "application/json")))
    r = c.post("/api/style/learn", headers=H, files=files)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["edits"] == 15
    assert data["summary"].startswith("Your edits:")
    assert len(data["presets"]) in (2, 3)                     # the 3 sparse CSV/JSON rows may form a group of their own
    assert [p["photos"] for p in data["presets"]][:2] == [6, 6]
    assert {p["name"] for p in data["presets"]} >= {"My style 1", "My style 2"}
    cat = {p["id"]: p for p in c.get("/api/presets", headers=H).json()["presets"]}
    for p in data["presets"]:
        assert cat[p["id"]]["pack"] == "My style" and cat[p["id"]]["mine"] and cat[p["id"]]["learned"]
        text = c.get(f"/api/presets/{p['id']}/xmp", headers=H).text                 # a Lightroom file to download
        assert parse_xmp(text).settings
    # learning again replaces the old "My style" presets instead of piling up
    r2 = c.post("/api/style/learn", headers=H, files=files[:6])
    assert r2.status_code == 200
    learned = [p for p in c.get("/api/presets", headers=H).json()["presets"] if p["pack"] == "My style"]
    assert len(learned) == 1 and learned[0]["name"] == "My style"


def test_learn_by_scene_with_previews(tmp_path):
    from PIL import Image
    c = TestClient(create_app(str(tmp_path), KEY))
    files = []

    def preview(name, level):
        b = io.BytesIO()
        Image.new("RGB", (64, 48), (level, level, level)).save(b, "JPEG")
        return ("files", (name, b.getvalue(), "image/jpeg"))
    for i, e in enumerate(edits(WARM, 4)):
        files.append(("files", (f"day{i}.xmp", xmp(f"d{i}", **e["settings"]).replace(b"crs:HasSettings", b'xmlns:exif="http://ns.adobe.com/exif/1.0/" exif:ISOSpeedRatings="200" crs:HasSettings'), "application/octet-stream")))
        files.append(preview(f"day{i}.jpg", 180))
    for i, e in enumerate(edits(MATTE, 4)):
        files.append(("files", (f"night{i}.xmp", xmp(f"n{i}", **e["settings"]).replace(b"crs:HasSettings", b'xmlns:exif="http://ns.adobe.com/exif/1.0/" exif:ISOSpeedRatings="3200" crs:HasSettings'), "application/octet-stream")))
        files.append(preview(f"night{i}.jpg", 40))
    r = c.post("/api/style/learn", headers=H, files=files, data={"scenes": "1"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["previews"] == 8
    scenes = {p["scene"] for p in data["presets"] if p.get("scene")}
    assert "bright outdoor" in scenes and "dusk" in scenes


def test_learn_needs_three_edits_and_rejects_junk(tmp_path):
    c = TestClient(create_app(str(tmp_path), KEY))
    r = c.post("/api/style/learn", headers=H, files=[("files", ("a.xmp", b"not xmp", "application/octet-stream"))])
    assert r.status_code == 422
    r = c.post("/api/style/learn", headers=H, files=[("files", ("a.txt", b"hello", "text/plain"))])
    assert r.status_code == 422


def test_summary_words():
    text = style.__doc__ and ""                       # engine module has no summary; the server one does
    from server.style import summary_words
    s = summary_words({"Shadows2012": 30, "Highlights2012": -8, "Contrast2012": 2, "ColorGradeMidtoneHue": 40, "ColorGradeMidtoneSat": 20}, {}, [])
    assert "lifted shadows" in s and "−8 highlights" in s and s.startswith("Your edits:")
