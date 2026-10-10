"""Runs the plug-in's Lua code against a stand-in for Lightroom and the real server code."""
import io
import json
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

lupa = pytest.importorskip("lupa")

from server.main import create_app

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "AutoEdits.lrplugin"
KEY = "test-key-0123456789"


class Rig:
    def __init__(self, tmp_path):
        self.client = TestClient(create_app(str(tmp_path / "server"), KEY))
        self.offline = False
        self.calls = []
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        temp = tmp_path / "lrtemp"
        temp.mkdir()
        g = self.lua.globals()
        g.py = self.lua.table_from({
            "http": self.http, "temp": str(temp),
            "writeJpeg": lambda p: Image.new("RGB", (80, 60), (90, 120, 150)).save(p, "JPEG"),
            "exists": lambda p: os.path.exists(p),
            "remove": lambda p: shutil.rmtree(p) if os.path.isdir(p) else os.path.exists(p) and os.remove(p),
            "mkdir": lambda p: os.makedirs(p, exist_ok=True),
        })
        self.lua.execute(f"package.path = '{PLUGIN}/?.lua;{ROOT}/tests/?.lua;' .. package.path")
        self.mock = self.lua.eval("(require 'lr_mock')")
        self.mock.prefs.serverUrl = "https://edits.test/"
        self.mock.prefs.serverKey = KEY
        self.temp = temp

    def http(self, method, url, body, headers, file_path):
        self.calls.append((method, url))
        if self.offline:
            return 0, ""
        assert url.startswith("https://edits.test/api/"), url
        path = url[len("https://edits.test"):]
        h = dict(headers.items())
        if method == "GET":
            r = self.client.get(path, headers=h)
        elif method == "POST":
            r = self.client.post(path, content=body, headers=h)
        else:
            r = self.client.post(path, headers=h, data={"meta": body},
                                 files={"preview": ("preview.jpg", Path(file_path).read_bytes(), "image/jpeg")})
        return r.status_code, r.text

    def add_photo(self, uuid, name, folder, develop=None, broken=False):
        spec = self.lua.table_from({
            "broken": broken,
            "raw": self.lua.table_from({"uuid": uuid, "path": f"/Volumes/X/{folder}/{name}", "rating": 0,
                                        "isoSpeedRating": 800, "aperture": 2.8, "shutterSpeed": 0.004,
                                        "focalLength": 85, "dateTimeOriginal": 700000000 + len(self.mock.photos),
                                        "dimensions": self.lua.table_from({"width": 6000, "height": 4000}),
                                        "fileFormat": "RAW", "isVirtualCopy": False}),
            "formatted": self.lua.table_from({"fileName": name, "folderName": folder, "cameraModel": "NIKON D810",
                                              "lens": "85mm f/1.4"}),
            "develop": self.lua.table_from({"Temperature": 5200, "Tint": 8, "WhiteBalance": "As Shot",
                                            "Exposure2012": 0, **(develop or {})}),
        })
        photo = self.mock.photo(spec)
        self.lua.eval("function(m, p) m.photos[#m.photos + 1] = p end")(self.mock, photo)
        return photo

    def run(self, file="SendToServer.lua"):
        self.lua.execute(f"dofile('{PLUGIN / file}')")

    def messages(self):
        return [(m.title, m.text) for m in self.mock.messages.values()]

    def weddings(self):
        return self.client.get("/api/weddings", headers={"X-Auto-Edits-Key": KEY}).json()["weddings"]

    def photos(self, wid):
        return self.client.get(f"/api/weddings/{wid}/photos", headers={"X-Auto-Edits-Key": KEY}).json()["photos"]


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_every_plugin_file_is_valid_lua():
    lua = lupa.LuaRuntime()
    for f in sorted(PLUGIN.glob("*.lua")):
        ok, err = lua.eval("function(src, name) local f, e = load(src, name); return f ~= nil, e end")(f.read_text(), f.name)
        assert ok, err


def test_json_round_trip():
    lua = lupa.LuaRuntime(unpack_returned_tuples=True)
    lua.execute(f"package.path = '{PLUGIN}/?.lua;' .. package.path")
    J = lua.eval("(require 'Json')")
    value = {"name": 'Asha & "Rohan"\n\tदिल', "n": 5200, "x": -0.25, "big": 123456789012, "yes": True, "no": False,
             "list": [1, 2.5, "three"], "deep": {"empty": {}, "k": [{"a": 1}]}}
    text = J.encode(lua.eval("function(s) return (require 'Json').decode(s) end")(json.dumps(value)))
    back = json.loads(text)
    assert back == value
    assert json.loads(J.encode(lua.eval("{ a = 0/0, b = 1/0, c = 400.0 }"))) == {"a": None, "b": None, "c": 400}
    assert lua.eval("(require 'Json').decode('{broken')")[0] is None


def test_send_photos_then_send_again_skips_them(rig):
    rig.add_photo("u1", "A.NEF", "Haldi")
    rig.add_photo("u2", "B.NEF", "Haldi")
    rig.add_photo("u3", "A.NEF", "Sangeet", develop={"Exposure2012": 0.5, "Contrast2012": 20})
    rig.mock.dialogEdits.wedding = "Asha & Rohan"
    rig.run()

    (w,) = rig.weddings()
    assert w["name"] == "Asha & Rohan" and w["catalog"] == "Weddings 2026"
    assert {f["name"]: f["photos"] for f in w["folders"]} == {"Haldi": 2, "Sangeet": 1}
    photos = {p["lr_uuid"]: p for p in rig.photos(w["id"])}
    m = photos["u1"]["meta"]
    assert (m["iso"], m["camera"], m["width"], m["develop"]["Temperature"], m["edited"]) == (800, "NIKON D810", 6000, 5200, [])
    assert photos["u3"]["meta"]["edited"] == ["Contrast2012", "Exposure2012"]
    title, text = rig.messages()[-1]
    assert "photos sent" in title and "Sent now: 3 of 3" in text and "1 of the sent photos already carry edits" in text
    assert not any(rig.temp.rglob("*.jpg"))                      # nothing left behind on the Mac
    assert rig.mock.exportSettings.LR_size_maxWidth == 2560

    rig.add_photo("u4", "C.NEF", "Sangeet")
    rig.calls.clear()
    rig.run()
    assert sum(1 for c in rig.calls if c[0] == "MULTI") == 1     # only the new one travels
    assert "Already on the server, skipped: 3" in rig.messages()[-1][1]
    assert rig.weddings()[0]["photos"] == 4

    rig.run()
    assert "Nothing was sent" in rig.messages()[-1][1]


def test_one_folder_and_reset_to_no_edit(rig):
    p = rig.add_photo("u1", "A.NEF", "Card 1", develop={"Exposure2012": 0.7, "Vibrance": 30})
    for k, v in {"wedding": "W", "folderMode": "one", "oneFolder": "Baraat", "resetFirst": True}.items():
        rig.mock.dialogEdits[k] = v
    rig.run()
    (w,) = rig.weddings()
    assert [f["name"] for f in w["folders"]] == ["Baraat"]
    meta = rig.photos(w["id"])[0]["meta"]
    assert meta["edited"] == [] and meta["lrFolder"] == "Card 1"
    assert list(p.snapshots.values()) == ["Before Auto Edits"]
    assert p.develop.Temperature == 5200 and p.develop.Exposure2012 == 0      # white balance kept, edit reset


def test_problems_are_reported_plainly(rig):
    rig.mock.prefs.serverKey = ""
    rig.add_photo("u1", "A.NEF", "Haldi")
    rig.run()
    assert "server key is not set" in rig.messages()[-1][1]

    rig.mock.prefs.serverKey = "wrong-key-0123456789"
    rig.run()
    assert "did not accept the key" in rig.messages()[-1][1]

    rig.mock.prefs.serverKey = KEY
    rig.offline = True
    rig.run()
    assert "could not be reached" in rig.messages()[-1][1]

    rig.offline = False
    rig.add_photo("u2", "BAD.NEF", "Haldi", broken=True)
    rig.run()
    title, text = rig.messages()[-1]
    assert "did not finish" in title and "Sent now: 1 of 2" in text and "BAD.NEF" in text
    assert rig.weddings()[0]["photos"] == 1

    rig.mock.dialogAnswer = "cancel"
    before = len(rig.messages())
    rig.run()
    assert len(rig.messages()) == before


def test_open_the_app_signs_the_browser_in_once(rig):
    rig.run("OpenApp.lua")
    url = rig.mock.opened
    assert url.startswith("https://edits.test/enter/")
    path = url[len("https://edits.test"):]
    first = rig.client.get(path, follow_redirects=False)
    assert first.status_code == 303 and "autoedits=" in first.headers["set-cookie"]
    rig.client.cookies.clear()
    again = rig.client.get(path, follow_redirects=False)                 # the link works once
    assert "set-cookie" not in again.headers

    rig.mock.prefs.serverKey = ""
    rig.run("OpenApp.lua")
    assert "server key is not set" in rig.messages()[-1][1]
