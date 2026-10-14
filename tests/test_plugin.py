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

    def weddings(self, headers=None):
        return self.client.get("/api/weddings", headers=headers or {"X-Auto-Edits-Key": KEY}).json()["weddings"]

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


def _setup_looks(rig):
    """Three photos sent, a look on the Haldi folder, a mask preset on Sangeet."""
    a = rig.add_photo("u1", "A.NEF", "Haldi")
    b = rig.add_photo("u2", "B.JPG", "Haldi", develop={"Temperature": None, "IncrementalTemperature": 0, "IncrementalTint": 0})
    c = rig.add_photo("u3", "C.NEF", "Sangeet", develop={"Contrast2012": 40})
    d = rig.add_photo("u4", "D.NEF", "Mehndi")
    rig.mock.dialogEdits.wedding = "Asha & Rohan"
    rig.run()
    (w,) = rig.weddings()
    folders = {f["name"]: f["id"] for f in w["folders"]}
    H = {"X-Auto-Edits-Key": KEY}
    presets = rig.client.get("/api/presets", headers=H).json()["presets"]
    rig.client.put(f"/api/weddings/{w['id']}/folders/{folders['Haldi']}/look", headers=H,
                   json={"mode": "preset_auto", "preset": presets[0]["id"], "toggles": ["exposure"], "temp": 30, "tint": 10, "sh": 12})
    xmp = (ROOT / "tests" / "fixtures" / "polished_bright_2.xmp").read_bytes()
    pid = rig.client.post("/api/presets", headers=H, files=[("files", ("p.xmp", xmp))]).json()["results"][0]["id"]
    rig.client.put(f"/api/weddings/{w['id']}/folders/{folders['Sangeet']}/look", headers=H,
                   json={"mode": "preset_auto", "preset": pid, "toggles": ["exposure"]})
    rig.mock.dialogEdits.wedding = w["id"]                      # the apply dialog chooses by id
    return w, a, b, c, d, presets[0], pid


def test_bring_back_and_apply(rig):
    w, a, b, c, d, preset, pid = _setup_looks(rig)
    lua = rig.lua
    rig.mock.installed["8EF01EEA28314F66B346C52D6E20C54A"] = lua.table_from(
        {"name": "Polished Bright 2", "settings": lua.table_from({"Contrast2012": 11, "Vibrance": 7})})
    rig.run("ApplyEdits.lua")

    assert "3 photos" in rig.mock.confirms[1] and "1 have nothing chosen" in rig.mock.confirms[1]
    title, text = rig.messages()[-1]
    assert "edits applied" in title and "Edited now: 3 of 3" in text and "Snapshots" in text

    # a normal preset: laid over a no-edit start, then white balance shifted from the photo's own Kelvin
    assert list(a.snapshots.values()) == ["Before Auto Edits"]
    assert list(a.steps.values()) == ["Auto Edits", "Auto Edits: per photo"]
    assert a.develop.WhiteBalance == "Custom" and a.develop.Temperature > 5200 and a.develop.Temperature % 50 == 0
    assert a.develop.Tint == 8 + 3                                   # app tint 10 is 3 in Lightroom
    assert a.develop.Vibrance is not None and a.develop.ParametricDarks is not None
    assert len(list(a.develop.ToneCurvePV2012.values())) >= 4
    # a JPEG has no Kelvin: its relative sliders move instead
    assert b.develop.IncrementalTemperature == 30 and b.develop.IncrementalTint == 10 and b.develop.Temperature is None
    # a preset of his own with AI masks: Lightroom applies the preset, then only the per-photo values go on top
    assert list(c.steps.values())[0] == "preset Polished Bright 2" and c.develop.Contrast2012 == 11
    assert "Exposure2012" in dict(c.develop.items()) and list(c.steps.values())[-1] == "Auto Edits: per photo"
    assert "preset with AI masks" in text and "Update AI Settings" in text
    assert list(d.steps.values()) == [] and list(d.snapshots.values()) == []      # nothing chosen: untouched

    # run again: everything is done already
    before = (len(list(a.steps.values())), len(rig.mock.confirms))
    rig.run("ApplyEdits.lua")
    assert "Nothing to apply" in rig.messages()[-1][1] and "3 photos already have their edit" in rig.messages()[-1][1]
    assert (len(list(a.steps.values())), len(rig.mock.confirms)) == before
    # change the look in the app: only that folder is due again
    H = {"X-Auto-Edits-Key": KEY}
    haldi = [f["id"] for f in rig.weddings()[0]["folders"] if f["name"] == "Haldi"][0]
    rig.client.put(f"/api/weddings/{w['id']}/folders/{haldi}/look", headers=H, json={"mode": "auto"})
    rig.run("ApplyEdits.lua")
    assert "Edited now: 2 of 2" in rig.messages()[-1][1] and "Already done, skipped: 1" in rig.messages()[-1][1]
    rig.client.delete(f"/api/presets/{pid}", headers=H)


def test_bring_back_reports_what_it_could_not_do(rig):
    w, a, b, c, d, preset, pid = _setup_looks(rig)
    a.develop.Temperature = None                                  # Lightroom sometimes gives no Kelvin value
    rig.run("ApplyEdits.lua")                                     # and the mask preset is not installed here
    text = rig.messages()[-1][1]
    assert "not installed in this Lightroom" in text and "Polished Bright 2" in text
    assert "White balance was left as shot on 1 photos" in text
    assert list(c.steps.values()) == [] and list(c.snapshots.values()) == []     # its preset is missing: untouched
    H = {"X-Auto-Edits-Key": KEY}
    due = {p["uuid"]: p["done"] for p in rig.client.get(f"/api/weddings/{w['id']}/apply", headers=H).json()["photos"] if p["edit"]}
    assert due == {"u1": True, "u2": True, "u3": False}          # the one without its preset stays due

    rig.mock.confirmAnswer = "cancel"
    rig.client.put(f"/api/weddings/{w['id']}/photos/{rig.photos(w['id'])[0]['id']}/look", headers=H, json={"mode": "auto"})
    steps = len(list(a.steps.values())) + len(list(b.steps.values()))
    rig.run("ApplyEdits.lua")
    assert len(list(a.steps.values())) + len(list(b.steps.values())) == steps    # said Cancel: nothing touched
    rig.client.delete(f"/api/presets/{pid}", headers=H)


def test_apply_never_spends_below_zero_credits(rig):
    """A photographer with 2 credits and 3 photos to do: 2 are applied, 1 is left out with a clear message."""
    acc = rig.client.app.state.accounts
    u = acc.sign_up("ravi@example.com", "a good password", "Ravi", "", "test")
    acc.verify(u["id"])                                               # 50 free credits
    key = acc.new_plugin_key(u["id"])
    rig.mock.prefs.serverKey = key
    for i in range(3):
        rig.add_photo(f"p{i}", f"IMG_{i}.NEF", "Haldi")
    rig.mock.dialogEdits.wedding = "Meera & Kabir"
    rig.run()
    (w,) = rig.weddings(headers={"X-Auto-Edits-Key": key})
    H = {"X-Auto-Edits-Key": key}
    presets = rig.client.get("/api/presets", headers=H).json()["presets"]
    rig.client.put(f"/api/weddings/{w['id']}/folders/{w['folders'][0]['id']}/look", headers=H,
                   json={"mode": "preset_auto", "preset": presets[0]["id"], "toggles": ["exposure"]})
    acc.add(u["id"], -(acc.balance(u["id"]) - 2), "admin", "test: leave 2")
    rig.mock.dialogEdits.wedding = w["id"]
    rig.run("ApplyEdits.lua")
    assert "2 photos" in rig.mock.confirms[len(rig.mock.confirms)] and "This uses 2 credits" in rig.mock.confirms[len(rig.mock.confirms)]
    text = rig.messages()[-1][1]
    assert "Edited now: 2 of 2" in text and "Out of credits: 1 photos were left out" in text
    assert acc.balance(u["id"]) == 0

    rig.run("ApplyEdits.lua")                                          # nothing left to pay with
    text = rig.messages()[-1][1]
    assert "needs 1 credits" in text and acc.balance(u["id"]) == 0
