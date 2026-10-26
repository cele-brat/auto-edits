"""Auto matched across a folder (engine/match.py, server/match.py) and recommended presets (server/recommend.py)."""
import numpy as np

from engine import match as m
from server import recommend
from server.worker import work
from tests.test_review_fixes import app, box, jpeg, photographer, send, wedding  # noqa: F401 (fixtures)


# ---- the pure rules -------------------------------------------------------------------------
def test_medians_and_matching_pull_an_outlier_in():
    group = [{"settings": {"Exposure2012": 0.3, "Highlights2012": -20}, "manual": {"temp": 5, "tint": 0}} for _ in range(4)]
    group.append({"settings": {"Exposure2012": 2.0, "Highlights2012": -80}, "manual": {"temp": 60, "tint": 30}})
    med = m.medians(group)
    assert med["n"] == 5 and med["Exposure2012"] == 0.3 and med["temp"] == 5
    s, man = m.matched(group[-1]["settings"], group[-1]["manual"], med)
    assert s["Exposure2012"] == 0.6                     # 0.3 + half of the 0.6 band
    assert s["Highlights2012"] == -32                   # -20 + half of 25
    assert man["temp"] == 14 and man["tint"] == 5
    s2, man2 = m.matched(group[0]["settings"], group[0]["manual"], med)
    assert s2 == group[0]["settings"] and man2 == group[0]["manual"]   # on the median: unchanged


def test_small_groups_and_unasked_keys_are_left_alone():
    med = {"n": 2, "Exposure2012": 0.0}
    assert m.matched({"Exposure2012": 1.5}, {}, med) == ({"Exposure2012": 1.5}, {})
    med = {"n": 5, "Exposure2012": 0.0, "Highlights2012": -40, "temp": 0}
    s, man = m.matched({"Exposure2012": 1.5}, {}, med)
    assert s == {"Exposure2012": 0.3} and man == {}      # Highlights not ticked: not added


def test_facts_of_reads_brightness_and_warmth():
    img = np.zeros((40, 60, 3), np.uint8)
    img[..., 0] = 200; img[..., 1] = 120; img[..., 2] = 40
    f = m.facts_of(img)
    assert 0.4 < f["median"] < 0.6 and f["warmth"] > 0.5 and f["colour"] > 0.5 and f["dark"] == 0
    assert m.scene_of(200, f) == "bright outdoor"
    assert m.scene_of(3200, {"median": 0.2, "warmth": 0.1}) == "indoor tungsten"


# ---- through the app: upload, worker, matched values -----------------------------------------
def _dark_and_bright(n_dark=4, n_bright=1):
    return [jpeg((40, 30, 20), size=(160, 120), noise=True)] * n_dark + [jpeg((230, 225, 220), size=(160, 120), noise=True)] * n_bright


def test_worker_keeps_facts_and_auto_is_matched_in_the_folder(app, tmp_path):
    c, uid = photographer(app)
    w = c.post("/api/weddings", json={"name": "Match"}).json()
    for i, data in enumerate(_dark_and_bright()):
        assert send(c, w["id"], f"m-{i}", "Sangeet", data=data).status_code == 200
    f = c.get(f"/api/weddings/{w['id']}").json()["folders"][0]["id"]
    c.put(f"/api/weddings/{w['id']}/folders/{f}/look", json={"mode": "auto"})
    work(idle_exit=True, data_dir=str(tmp_path))
    store = app.state.store
    photos = store.photos(w["id"], f)
    kept = [store.get_autos(p["id"]) for p in photos]
    assert all("facts" in k and "tone:11" in k for k in kept)
    fm = app.state.renderer.match
    assert fm.folder(f)["n"] == 5 and fm.folder(f)["pending"] == 0
    med = fm.medians_for(photos[-1]["id"])
    assert med and med["n"] >= 3
    raw = kept[-1]["tone:11"]["settings"]["Exposure2012"]       # the bright odd one, on its own
    got = c.get(f"/api/weddings/{w['id']}/photos/{photos[-1]['id']}/develop").json()
    matched = got["auto"]["Exposure2012"]
    assert abs(matched - med["Exposure2012"]) <= m.BANDS["Exposure2012"] * m.PULL + 0.01
    assert abs(matched - med["Exposure2012"]) <= abs(raw - med["Exposure2012"]) + 0.01   # never further from the folder than before


def test_recommended_presets_for_a_folder(app, tmp_path):
    c, uid = photographer(app)
    w = c.post("/api/weddings", json={"name": "Rec"}).json()
    for i in range(3):
        assert send(c, w["id"], f"r-{i}", "Haldi", data=jpeg((230, 200, 90), size=(160, 120))).status_code == 200
    f = c.get(f"/api/weddings/{w['id']}").json()["folders"][0]["id"]
    before = c.get(f"/api/weddings/{w['id']}/folders/{f}/recommended").json()
    assert before["recommended"] == [] and before["pending"] == 3
    work(idle_exit=True, data_dir=str(tmp_path))
    r = c.get(f"/api/weddings/{w['id']}/folders/{f}/recommended")
    assert r.status_code == 200
    d = r.json()
    assert d["pending"] == 0 and d["measured"] == 3 and d["scene"] == "bright outdoor"
    assert len(d["recommended"]) == 3
    assert len({p["pack"] for p in d["recommended"]}) == 3                 # spread over packs
    assert all("haldi" in p["why"] for p in d["recommended"])              # the folder's name counts most
    assert all("Daylight" in p["variant"] for p in d["recommended"])       # the variant for bright light
    assert c.get(f"/api/weddings/{w['id']}/folders/999/recommended").status_code == 404


def test_recommend_rules_directly():
    cat = [{"id": "a1", "name": "A", "look": "A", "variant": "01 Signature", "pack": "P1", "tags": ["warm", "reception", "night"]},
           {"id": "a2", "name": "A night", "look": "A", "variant": "03 Reception Night", "pack": "P1", "tags": ["warm", "reception", "night"]},
           {"id": "b1", "name": "B", "look": "B", "variant": "01 Signature", "pack": "P2", "tags": ["cool", "daylight", "bright"]},
           {"id": "c1", "name": "C", "look": "C", "variant": "01 Signature", "pack": "P3", "tags": ["muted", "soft"]},
           {"id": "me", "name": "My style indoor tungsten", "look": "My style indoor tungsten", "variant": "", "pack": "My style",
            "tags": [], "mine": True, "learned": True}]
    picks = recommend.recommend(cat, "Reception_Night", {"scene": "indoor tungsten", "warmth": 0.1, "median": 0.2, "colour": 0.2, "measured": 3})
    assert [p["id"] for p in picks][:2] == ["a2", "me"]                   # reception look at its night variant, then your own style
    assert recommend.folder_words("Mehndi & Haldi 2026") == ["haldi", "mehndi", "mehendi"]
