"""Zoom tiles, copy / paste / sync, history and snapshots, stars and labels, the plug-in's settings call (server/editing.py)."""
import io

from fastapi.testclient import TestClient
from PIL import Image

from server.editing import merge_panels, plugin_version
from server.history import label_for
from server.main import create_app
from tests.test_server import H, KEY, send


def _wedding(c, n=3, size=(2000, 1500)):
    w = c.post("/api/weddings", headers=H, json={"name": "W"}).json()
    buf = io.BytesIO(); Image.effect_noise(size, 40).convert("RGB").save(buf, "JPEG")
    for i in range(n):
        send(c, w["id"], f"u{i}", "Haldi", f"u{i}.NEF", buf.getvalue())
    photos = c.get(f"/api/weddings/{w['id']}/photos?compact=1", headers=H).json()["photos"]
    return w, photos


def _client(tmp_path, admin_email="studio@test.local"):
    return TestClient(create_app(str(tmp_path), KEY, admin_email=admin_email))


def test_tile_is_a_piece_of_the_full_preview(tmp_path):
    c = _client(tmp_path)
    w, photos = _wedding(c, 1, (4000, 3000))
    base = f"/api/weddings/{w['id']}/photos/{photos[0]['id']}"
    r = c.get(f"{base}/tile?x=0.25&y=0.25&w=0.5&h=0.5", headers=H)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["x-full-size"] == "2560,1920"                               # the studio: full preview size
    assert r.headers["x-tile-rect"] == "640,480,1280,960"
    assert Image.open(io.BytesIO(r.content)).size == (1280, 960)
    # a piece larger than 1600 a side comes down to 1600
    r = c.get(f"{base}/tile?x=0&y=0&w=1&h=1", headers=H)
    assert Image.open(io.BytesIO(r.content)).size == (1600, 1200)
    # outside the picture: refused; a look in the address is honoured; the crop is applied
    assert c.get(f"{base}/tile?x=1&y=1&w=0.5&h=0.5", headers=H).status_code == 422
    c.put(f"{base}/crop", headers=H, json={"x": 0, "y": 0, "w": 0.5, "h": 0.5})
    r = c.get(f"{base}/tile?x=0&y=0&w=1&h=1&look=%7B%7D", headers=H)
    assert r.headers["x-full-size"] == "1280,960"                                # the cropped half of the 2560 px preview
    # a photographer's tile is held to PREVIEW_CAP
    acc = c.app.state.accounts
    u = acc.sign_up("ravi@example.com", "a good password", "Ravi", "", "test")
    acc.verify(u["id"])
    k = {"X-Auto-Edits-Key": acc.new_plugin_key(u["id"])}
    w2 = c.post("/api/weddings", headers=k, json={"name": "W2"}).json()
    buf = io.BytesIO(); Image.effect_noise((4000, 3000), 40).convert("RGB").save(buf, "JPEG")
    c.post(f"/api/weddings/{w2['id']}/photos", headers=k, data={"meta": '{"uuid": "a", "folder": "F", "fileName": "a.NEF"}'},
           files={"preview": ("a.jpg", buf.getvalue(), "image/jpeg")})
    pid = c.get(f"/api/weddings/{w2['id']}/photos?compact=1", headers=k).json()["photos"][0]["id"]
    r = c.get(f"/api/weddings/{w2['id']}/photos/{pid}/tile?x=0&y=0&w=0.5&h=0.5", headers=k)
    assert r.headers["x-full-size"] == "1600,1200"
    assert c.get(f"{base}/tile", headers=k).status_code == 404                   # someone else's photo


def test_merge_panels_takes_only_what_was_ticked():
    src = {"mode": "preset_auto", "preset": None, "toggles": ["exposure"], "ev": 0.5, "hl": -20, "adj": {"Contrast2012": 15, "GrainAmount": 30},
           "masks": {"sky": {"ev": -0.5}}, "crop": {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5, "turn": 90}}
    tgt = {"mode": "preset", "adj": {"Contrast2012": 5, "Vibrance": 10}, "sh": 10}
    look, crop = merge_panels(tgt, src, ["Basic"], {"x": 0, "y": 0, "w": 0.8, "h": 0.8})
    assert look["ev"] == 0.5 and look["hl"] == -20 and look["sh"] == 0                     # Basic: Shadows from the source (0)
    assert look["adj"] == {"Contrast2012": 15}                                               # Vibrance is Basic too: the source has none
    assert look["mode"] == "preset_auto" and look["toggles"] == ["exposure"]                # the Auto tick travels with its slider
    assert crop == {"x": 0, "y": 0, "w": 0.8, "h": 0.8, "ratio": "free", "auto": False}      # crop untouched
    look, crop = merge_panels(tgt, src, ["Effects", "Crop"], None)
    assert look["adj"] == {"Contrast2012": 5, "Vibrance": 10, "GrainAmount": 30} and look["ev"] == 0
    assert crop == {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5, "ratio": "free", "auto": False}  # the crop, not the turn
    look, crop = merge_panels(tgt, src, ["Rotate"], None)
    assert crop == {"turn": 90}
    look, crop = merge_panels(tgt, src, ["Masks"], None)
    assert look["masks"] == {"sky": {"ev": -0.5}}


def test_paste_onto_many_photos_and_history(tmp_path):
    c = _client(tmp_path)
    w, photos = _wedding(c, 3)
    ids = [p["id"] for p in photos]
    bulk = {"photo_ids": ids[1:], "panels": ["Basic", "Crop"],
            "look": {"mode": "preset", "ev": 0.3, "crop": {"x": 0.1, "y": 0.1, "w": 0.6, "h": 0.6}}}
    r = c.put("/api/photos/look-bulk", headers=H, json=bulk).json()
    assert len(r["photos"]) == 2 and all(p["look"]["ev"] == 0.3 and p["crop"]["w"] == 0.6 for p in r["photos"])
    got = {p["id"]: p for p in c.get(f"/api/weddings/{w['id']}/photos?compact=1", headers=H).json()["photos"]}
    assert got[ids[0]]["look"] is None and got[ids[1]]["look"]["ev"] == 0.3 and got[ids[2]]["crop"]["w"] == 0.6
    h = c.get(f"/api/weddings/{w['id']}/photos/{ids[1]}/history", headers=H).json()
    assert [s["label"] for s in h["steps"]] == ["Uploaded", "Paste settings"] and h["at"] == h["steps"][-1]["id"]
    # a label of its own ("Sync settings"), and bad requests
    r = c.put("/api/photos/look-bulk", headers=H, json={**bulk, "label": "Sync settings"}).json()
    assert c.get(f"/api/weddings/{w['id']}/photos/{ids[1]}/history", headers=H).json()["steps"][-1]["label"] == "Sync settings"
    assert c.put("/api/photos/look-bulk", headers=H, json={"photo_ids": [], "panels": ["Basic"], "look": {}}).status_code == 422
    assert c.put("/api/photos/look-bulk", headers=H, json={"photo_ids": ids, "panels": ["Nope"], "look": {}}).status_code == 422
    # another account's photos are not touched (and look like they do not exist)
    acc = c.app.state.accounts
    u = acc.sign_up("ravi@example.com", "a good password", "Ravi", "", "test")
    k = {"X-Auto-Edits-Key": acc.new_plugin_key(u["id"])}
    assert c.put("/api/photos/look-bulk", headers=k, json=bulk).status_code == 404
    assert c.get(f"/api/weddings/{w['id']}/photos?compact=1", headers=H).json()["photos"][1]["look"]["ev"] == 0.3


def test_history_labels_undo_redo_and_truncation(tmp_path):
    c = _client(tmp_path)
    w, photos = _wedding(c, 1)
    pid = photos[0]["id"]
    base = f"/api/weddings/{w['id']}/photos/{pid}"
    presets = c.get("/api/presets", headers=H).json()["presets"]
    mark = lambda label=None: c.post(f"{base}/history/mark", headers=H, json={"label": label} if label else {}).json()
    c.put(f"{base}/look", headers=H, json={"mode": "preset", "ev": 0.5})
    assert mark()["row"]["label"] == "Exposure"
    c.put(f"{base}/look", headers=H, json={"mode": "preset", "ev": 0.5, "preset": presets[0]["id"]})
    assert mark()["row"]["label"] == f"Preset: {presets[0]['name']}"
    c.put(f"{base}/look", headers=H, json={"mode": "preset", "ev": 0.5, "preset": presets[0]["id"], "adj": {"GrainAmount": 20}})
    assert mark()["row"]["label"] == "Grain amount"
    c.put(f"{base}/crop", headers=H, json={"x": 0, "y": 0, "w": 0.5, "h": 0.5})
    assert mark()["row"]["label"] == "Crop"
    c.put(f"{base}/turn", headers=H, json={"by": 90})
    assert mark()["row"]["label"] == "Rotate"
    assert mark()["row"] is None                                                  # nothing changed: no row
    h = c.get(f"{base}/history", headers=H).json()
    labels = [s["label"] for s in h["steps"]]
    assert labels == ["Uploaded", "Exposure", f"Preset: {presets[0]['name']}", "Grain amount", "Crop", "Rotate"]
    # undo twice: the crop is still there, the turn is gone; redo once brings the turn back
    r = c.post(f"{base}/history/undo", headers=H).json()
    assert r["label"] == "Crop" and r["crop"]["w"] == 0.5 and "turn" not in r["crop"] and r["at"] == h["steps"][4]["id"]
    r = c.post(f"{base}/history/undo", headers=H).json()
    assert r["label"] == "Grain amount" and r["crop"] is None and r["look"]["adj"] == {"GrainAmount": 20}
    assert c.get(f"{base}/develop", headers=H).json()["look"]["crop"] is None
    r = c.post(f"{base}/history/redo", headers=H).json()
    assert r["label"] == "Crop" and len(r["steps"]) == 6                           # later steps stay
    # clicking a step restores it; a new change after that truncates what came later
    first = h["steps"][1]["id"]
    r = c.post(f"{base}/history/{first}/restore", headers=H).json()
    assert r["look"]["ev"] == 0.5 and r["look"]["preset"] is None and r["at"] == first
    c.put(f"{base}/look", headers=H, json={"mode": "preset", "ev": 0.5, "hl": -30})
    assert mark()["row"]["label"] == "Highlights"
    labels = [s["label"] for s in c.get(f"{base}/history", headers=H).json()["steps"]]
    assert labels == ["Uploaded", "Exposure", "Highlights"]
    assert c.post(f"{base}/history/redo", headers=H).status_code == 404            # nothing after the newest
    assert c.post(f"{base}/history/{h['steps'][3]['id']}/restore", headers=H).status_code == 404
    # at most 100 rows a photo
    for i in range(1, 120):
        c.put(f"{base}/look", headers=H, json={"mode": "preset", "ev": i / 100})
        mark()
    steps = c.get(f"{base}/history", headers=H).json()["steps"]
    assert len(steps) == 100 and steps[0]["label"] == "Exposure"


def test_folder_history_and_photos_with_their_own_settings(tmp_path):
    c = _client(tmp_path)
    w, photos = _wedding(c, 2)
    fid = w["id"]
    folder = c.get(f"/api/weddings/{fid}", headers=H).json()["folders"][0]["id"]
    fbase = f"/api/weddings/{fid}/folders/{folder}"
    c.put(f"{fbase}/look", headers=H, json={"mode": "auto"})
    assert c.post(f"{fbase}/history/mark", headers=H).json()["row"]["label"] == "Edit with: Auto"
    # a folder change writes no row for photos without their own settings
    assert c.get(f"/api/weddings/{fid}/photos/{photos[0]['id']}/history", headers=H).json()["steps"] == []
    # one photo gets its own look, then the folder is reset: the photo gets a row, the other does not
    pbase = f"/api/weddings/{fid}/photos/{photos[1]['id']}"
    c.put(f"{pbase}/look", headers=H, json={"mode": "preset", "ev": 1})
    c.post(f"{pbase}/history/mark", headers=H)
    c.post(f"{fbase}/reset", headers=H)
    r = c.post(f"{fbase}/history/mark", headers=H, json={"label": "Reset to original"}).json()
    assert r["row"]["label"] == "Reset to original"
    assert [s["label"] for s in c.get(f"{pbase}/history", headers=H).json()["steps"]] == ["Uploaded", "Edit with: Preset, Exposure", "Reset to original"]
    assert c.get(f"/api/weddings/{fid}/photos/{photos[0]['id']}/history", headers=H).json()["steps"] == []
    # undo on the folder puts its look back
    r = c.post(f"{fbase}/history/undo", headers=H).json()
    assert r["look"]["mode"] == "auto"
    assert c.get(f"/api/weddings/{fid}", headers=H).json()["folders"][0]["settings"]["mode"] == "auto"


def test_label_for_words():
    assert label_for(None, {"mode": "preset", "ev": 0.2}, None, None) == "Exposure"
    assert label_for({"mode": "preset"}, {"mode": "preset", "adj": {"HueAdjustmentRed": 5}}, None, None) == "Hue: Red"
    assert label_for({"mode": "preset"}, {"mode": "preset", "masks": {"sky": {"ev": -1}}}, None, None) == "Mask: Sky"
    assert label_for({"mode": "preset"}, {"mode": "preset_auto", "toggles": ["exposure"]}, None, None) == "Edit with: Preset + auto, Auto exposure on"
    assert label_for({"mode": "preset"}, {"mode": "preset", "ev": 1, "hl": 1, "sh": 1, "wh": 1}, None, None) == "Several changes"
    assert label_for({"mode": "preset"}, None, None, None) == "Folder settings"
    assert label_for({"mode": "preset"}, {"mode": "preset"}, {"x": 0, "y": 0, "w": 0.5, "h": 0.5}, None) == "Remove crop"


def test_snapshots(tmp_path):
    c = _client(tmp_path)
    w, photos = _wedding(c, 1)
    base = f"/api/weddings/{w['id']}/photos/{photos[0]['id']}"
    c.put(f"{base}/look", headers=H, json={"mode": "preset", "ev": 0.5})
    r = c.post(f"{base}/snapshots", headers=H, json={"name": "Bright"}).json()
    assert r["snapshot"]["name"] == "Bright" and len(r["snapshots"]) == 1
    sid = r["snapshot"]["id"]
    c.put(f"{base}/look", headers=H, json={"mode": "preset", "ev": -1})
    c.post(f"{base}/history/mark", headers=H)
    r = c.post(f"{base}/snapshots/{sid}/restore", headers=H).json()
    assert r["look"]["ev"] == 0.5 and r["steps"][-1]["label"] == "Snapshot: Bright"
    assert c.get(f"{base}/develop", headers=H).json()["look"]["ev"] == 0.5
    assert c.delete(f"{base}/snapshots/{sid}", headers=H).json()["snapshots"] == []
    assert c.post(f"{base}/snapshots/{sid}/restore", headers=H).status_code == 404
    assert c.post(f"{base}/snapshots", headers=H, json={}).json()["snapshot"]["name"]   # a name is made up


def test_stars_and_labels(tmp_path):
    c = _client(tmp_path)
    w, photos = _wedding(c, 2)
    base = f"/api/weddings/{w['id']}/photos/{photos[0]['id']}"
    assert c.put(f"{base}/marks", headers=H, json={"stars": 4}).json() == {"stars": 4, "label": ""}
    assert c.put(f"{base}/marks", headers=H, json={"label": "red"}).json() == {"stars": 4, "label": "red"}
    assert c.put(f"{base}/marks", headers=H, json={"stars": 6}).status_code == 422
    assert c.put(f"{base}/marks", headers=H, json={"label": "pink"}).status_code == 422
    assert c.put(f"{base}/marks", headers=H, json={"stars": True}).status_code == 422
    got = c.get(f"/api/weddings/{w['id']}/photos?compact=1", headers=H).json()["photos"]
    assert (got[0]["stars"], got[0]["label"], got[1]["stars"], got[1]["label"]) == (4, "red", 0, "")
    assert c.get(f"/api/weddings/{w['id']}/marks", headers=H).json()["marks"] == {"u0": {"stars": 4, "label": "red"}}
    # an export of "3 stars and up" takes only the starred one
    acc = c.app.state.accounts
    acc.verify(acc.admin_id)
    e = c.post(f"/api/weddings/{w['id']}/exports", headers=H, json={"size": 2560, "stars": 3}).json()
    assert e["total"] == 1 and "3+ stars" in e["scope"]
    assert c.post(f"/api/weddings/{w['id']}/exports", headers=H, json={"size": 2560, "stars": 5}).status_code == 422


def test_plugin_info_and_version(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/me/plugin").status_code == 401
    r = c.get("/api/me/plugin", headers=H).json()
    assert r["email"] == "studio@test.local" and r["unlimited"] is True and r["plugin_version"] == "0.9.0"
    assert r["download"] == "/download/lightroom-plugin"
    acc = c.app.state.accounts
    u = acc.sign_up("ravi@example.com", "a good password", "Ravi", "", "test")
    acc.verify(u["id"])
    r = c.get("/api/me/plugin", headers={"X-Auto-Edits-Key": acc.new_plugin_key(u["id"])}).json()
    assert r["email"] == "ravi@example.com" and r["credits"] == acc.balance(u["id"]) > 0 and r["unlimited"] is False
    assert plugin_version(tmp_path / "missing.lua") == ""
    (tmp_path / "Info.lua").write_text("return { VERSION = { major = 1, minor = 12, revision = 3 } }")
    assert plugin_version(tmp_path / "Info.lua") == "1.12.3"
