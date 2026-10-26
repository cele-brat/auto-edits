"""Fixes from the independent review of 19 Oct 2026 (sections A and B). Each test failed before its fix."""
import io
import inspect
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.main import create_app
from server.worker import work

KEY = "test-key-0123456789"
ADMIN = "studio@cele-brat.com"
H = {"X-Auto-Edits-Key": KEY}
ROOT = Path(__file__).resolve().parents[1]


class Mailbox:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, text, html="", wait=False):
        self.sent.append((to, subject, text))


def jpeg(color=(120, 90, 60), size=(64, 48), noise=False):
    buf = io.BytesIO()
    (Image.effect_noise(size, 40).convert("RGB") if noise else Image.new("RGB", size, color)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def box():
    return Mailbox()


@pytest.fixture
def app(tmp_path, box):
    return create_app(str(tmp_path), KEY, admin_email=ADMIN, mailer=box)


def photographer(app, email="a@example.com", credits=None):
    """A confirmed photographer (50 free credits, or exactly `credits`), signed in through the plug-in key."""
    acc = app.state.accounts
    u = acc.sign_up(email, "a good password", "Ravi", "", "test")
    acc.verify(u["id"])
    if credits is not None:
        acc.add(u["id"], credits - acc.balance(u["id"]), "admin", "test")
    c = TestClient(app)
    c.headers.update({"X-Auto-Edits-Key": acc.new_plugin_key(u["id"])})
    return c, u["id"]


def send(c, wid, uuid, folder="Haldi", name=None, data=None, taken="2026-01-01T10:00:00", develop=None):
    meta = {"uuid": uuid, "folder": folder, "fileName": name or uuid + ".NEF", "taken": taken, "iso": 400}
    if develop:
        meta["develop"] = develop
    return c.post(f"/api/weddings/{wid}/photos", data={"meta": json.dumps(meta)},
                  files={"preview": ("p.jpg", data or jpeg(), "image/jpeg")})


def wedding(c, n, name="W", look=None, folder="Haldi"):
    w = c.post("/api/weddings", json={"name": name}).json()
    for i in range(n):
        assert send(c, w["id"], f"{name}-{i}", folder).status_code == 200
    f = c.get(f"/api/weddings/{w['id']}").json()["folders"][0]["id"]
    if look is not None:
        c.put(f"/api/weddings/{w['id']}/folders/{f}/look", json=look)
    return w, f


EV = {"mode": "manual", "ev": 0.5}


# ---- 1. Lightroom edits are paid when handed out ------------------------------------------
def test_apply_list_charges_when_it_hands_edits_out(app):
    c, uid = photographer(app)
    acc = app.state.accounts
    w, f = wedding(c, 3, look=EV)
    got = c.get(f"/api/weddings/{w['id']}/apply").json()["photos"]
    assert all(p["edit"] for p in got)
    assert acc.balance(uid) == 47                                  # paid now, without ever calling /applied
    again = c.get(f"/api/weddings/{w['id']}/apply").json()["photos"]
    assert [p["stamp"] for p in again] == [p["stamp"] for p in got] and acc.balance(uid) == 47   # a retry is free
    stamps = {p["uuid"]: p["stamp"] for p in got}
    assert c.post(f"/api/weddings/{w['id']}/applied", json=stamps).json()["marked"] == 3
    assert acc.balance(uid) == 47                                  # /applied only remembers
    assert c.get(f"/api/weddings/{w['id']}/apply?again=1").json()["photos"][0]["edit"]
    assert acc.balance(uid) == 44                                  # again=1 pays again
    c.put(f"/api/weddings/{w['id']}/folders/{f}/look", json={"mode": "manual", "ev": 0.8})
    c.get(f"/api/weddings/{w['id']}/apply")
    assert acc.balance(uid) == 41                                  # a changed edit pays again
    assert c.post(f"/api/weddings/{w['id']}/applied", json={p["uuid"]: "made-up" for p in got}).json()["marked"] == 0


def test_apply_never_below_zero_across_weddings_and_dry_runs_are_free(app):
    c, uid = photographer(app, credits=5)
    acc = app.state.accounts
    given = 0
    for i in range(4):                                             # 4 weddings, 5 credits: 5 edits in all, not 20
        w, _ = wedding(c, 5, name=f"W{i}", look=EV)
        dry = c.get(f"/api/weddings/{w['id']}/apply?dry=1&paged=1").json()
        assert all(p["due"] and p["edit"] is None for p in dry["photos"])
        r = c.get(f"/api/weddings/{w['id']}/apply")
        if r.status_code == 402:
            continue
        given += sum(1 for p in r.json()["photos"] if p["edit"])
        assert all(p["no_credits"] for p in r.json()["photos"] if not p["edit"])
    assert given == 5 and acc.balance(uid) == 0


def test_apply_charges_are_atomic(app):
    c, uid = photographer(app, credits=10)
    w, _ = wedding(c, 10, look=EV)
    results = []
    threads = [threading.Thread(target=lambda: results.append(c.get(f"/api/weddings/{w['id']}/apply").json()))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert app.state.accounts.balance(uid) == 0                   # 10 edits paid once, not once per listing
    assert all(sum(1 for p in r["photos"] if p["edit"]) == 10 for r in results)


def test_the_studio_is_never_stopped_for_lightroom(app):
    """Like its exports: always handed out at 0 credits; the use is written down as before."""
    c = TestClient(app)
    c.headers.update(H)
    w, _ = wedding(c, 2, look=EV)
    assert all(p["edit"] for p in c.get(f"/api/weddings/{w['id']}/apply").json()["photos"])
    assert all(p["edit"] for p in c.get(f"/api/weddings/{w['id']}/apply?again=1").json()["photos"])


# ---- 1, 3. the plug-ins --------------------------------------------------------------------
@pytest.fixture
def rig(tmp_path):
    pytest.importorskip("lupa")
    from tests.test_plugin import Rig
    return Rig(tmp_path)


def _plugin_photographer(rig, credits):
    from tests.test_plugin import _setup_looks  # noqa: F401  (same set-up style)
    acc = rig.client.app.state.accounts
    u = acc.sign_up("ravi@example.com", "a good password", "Ravi", "", "test")
    acc.verify(u["id"])
    key = acc.new_plugin_key(u["id"])
    rig.mock.prefs.serverKey = key
    for i in range(3):
        rig.add_photo(f"p{i}", f"IMG_{i}.NEF", "Haldi")
    rig.mock.dialogEdits.wedding = "Meera & Kabir"
    rig.run()
    hk = {"X-Auto-Edits-Key": key}
    (w,) = rig.weddings(headers=hk)
    rig.client.put(f"/api/weddings/{w['id']}/folders/{w['folders'][0]['id']}/look", headers=hk,
                   json={"mode": "manual", "ev": 0.4, "temp": 20})
    acc.add(u["id"], credits - acc.balance(u["id"]), "admin", "test")
    rig.mock.dialogEdits.wedding = w["id"]
    return acc, u["id"]


def test_plugin_07_still_works_and_pays_as_handed_out(rig):
    acc, uid = _plugin_photographer(rig, 10)
    rig.lua.execute(f"dofile('{ROOT / 'tests' / 'fixtures' / 'ApplyEdits_v07.lua'}')")      # the plug-in photographers have now
    text = rig.messages()[-1][1]
    assert "Edited now: 3 of 3" in text and acc.balance(uid) == 7
    rig.lua.execute(f"dofile('{ROOT / 'tests' / 'fixtures' / 'ApplyEdits_v07.lua'}')")
    assert "3 photos already have their edit" in rig.messages()[-1][1] and acc.balance(uid) == 7


def test_plugin_08_counts_first_and_pays_only_what_it_applies(rig):
    acc, uid = _plugin_photographer(rig, 10)
    rig.mock.confirmAnswer = "cancel"
    rig.run("ApplyEdits.lua")
    assert "This uses 3 credits" in rig.mock.confirms[len(rig.mock.confirms)] and acc.balance(uid) == 10   # asked, not paid
    rig.mock.confirmAnswer = "ok"
    rig.run("ApplyEdits.lua")
    assert "Edited now: 3 of 3" in rig.messages()[-1][1] and acc.balance(uid) == 7


def test_plugin_white_balance_does_not_stack_and_snapshot_is_kept(rig):
    from tests.test_plugin import _setup_looks
    w, a, b, c, d, preset, pid = _setup_looks(rig)
    rig.run("ApplyEdits.lua")
    first = a.develop.Temperature
    assert a.develop.WhiteBalance == "Custom" and first > 5200
    rig.mock.dialogEdits.again = True                             # "apply again to photos already done"
    rig.run("ApplyEdits.lua")
    rig.run("ApplyEdits.lua")
    assert a.develop.Temperature == first                         # 5200 K stays e.g. 5650 K, never 10150 K
    assert b.develop.IncrementalTemperature == 30                 # a JPEG: the relative slider, also not stacked
    assert list(a.snapshots.values()) == ["Before Auto Edits"]
    assert a.snapshotDevelop[1].Temperature == 5200 and a.snapshotDevelop[1].WhiteBalance == "As Shot"
    rig.client.delete(f"/api/presets/{pid}", headers=H)


def test_server_sends_absolute_white_balance_from_the_as_shot_values(app):
    c = TestClient(app)
    c.headers.update(H)
    w = c.post("/api/weddings", json={"name": "W"}).json()
    send(c, w["id"], "raw", develop={"Temperature": 5200, "Tint": 8, "WhiteBalance": "As Shot"})
    send(c, w["id"], "jpg", develop={"WhiteBalance": "As Shot"})
    f = c.get(f"/api/weddings/{w['id']}").json()["folders"][0]["id"]
    c.put(f"/api/weddings/{w['id']}/folders/{f}/look", json={"mode": "manual", "temp": 30, "tint": 10})
    by = {p["uuid"]: p["edit"]["wb"] for p in c.get(f"/api/weddings/{w['id']}/apply").json()["photos"]}
    assert by["raw"]["kelvin"] > 5200 and by["raw"]["kelvin"] % 50 == 0 and by["raw"]["abs_tint"] > 8
    assert "kelvin" not in by["jpg"] and by["jpg"]["incremental_temp"] == 30
    info = (ROOT / "plugin" / "AutoEdits.lrplugin" / "Info.lua").read_text()
    assert "minor = 9" in info and "'0.9'" in (ROOT / "plugin" / "AutoEdits.lrplugin" / "SendToServer.lua").read_text()


# ---- 2. credits given back -------------------------------------------------------------
def test_deleting_a_wedding_gives_back_its_waiting_exports(app):
    c, uid = photographer(app)
    w, _ = wedding(c, 4)
    c.post(f"/api/weddings/{w['id']}/exports", json={"size": 2560})
    assert app.state.accounts.balance(uid) == 46
    assert c.delete(f"/api/weddings/{w['id']}").json()["deleted"]
    assert app.state.accounts.balance(uid) == 50


def test_a_failed_cull_gives_its_credits_back(app, tmp_path):
    c, uid = photographer(app)
    w, _ = wedding(c, 12)
    c.post(f"/api/weddings/{w['id']}/cull", json={"strictness": "normal"})
    assert app.state.accounts.balance(uid) == 48
    for p in (tmp_path / "weddings" / w["id"] / "previews").glob("*.jpg"):
        p.write_bytes(b"not a picture")                           # every photo unreadable: the cull fails
    work(idle_exit=True, data_dir=str(tmp_path))
    (job,) = c.get(f"/api/weddings/{w['id']}/cull").json()["culls"]
    assert job["state"] == "failed" and app.state.accounts.balance(uid) == 50
    assert app.state.accounts.history(uid)[0]["kind"] == "refund"


def test_a_cull_rates_only_the_photos_it_was_paid_for(app, tmp_path):
    c, uid = photographer(app)
    w, _ = wedding(c, 10)
    assert c.post(f"/api/weddings/{w['id']}/cull", json={}).json()["charged"] == 1
    for i in range(30):                                           # added after paying, before the worker ran
        send(c, w["id"], f"late-{i}")
    work(idle_exit=True, data_dir=str(tmp_path))
    rated = [p for p in c.get(f"/api/weddings/{w['id']}/photos").json()["photos"] if p["cull"]]
    assert len(rated) == 10 and not any(p["lr_uuid"].startswith("late") for p in rated)


def test_a_photo_that_keeps_crashing_the_worker_is_given_up_and_refunded(app):
    from server.exports import Exports, MAX_ATTEMPTS, STALE_SECONDS
    c, uid = photographer(app)
    w, _ = wedding(c, 1)
    e = c.post(f"/api/weddings/{w['id']}/exports", json={"size": 2560}).json()
    ex = Exports(app.state.store)
    for _ in range(MAX_ATTEMPTS):
        assert len(ex.claim(1)) == 1                              # claimed, and the worker dies on it
        with sqlite3.connect(app.state.store.db_path) as db:
            db.execute("UPDATE export_items SET claimed=?", (time.time() - STALE_SECONDS - 1,))
    assert ex.claim(1) == []                                      # not handed out a fourth time
    got = c.get(f"/api/exports/{e['id']}").json()
    assert got["failed"] == 1 and got["state"] == "done" and app.state.accounts.balance(uid) == 50


# ---- 4. presets changed after use -------------------------------------------------------
def test_worker_and_grid_see_an_updated_preset(app, tmp_path):
    from server.looks import Renderer
    from server.worker import reload_presets
    c = TestClient(app)
    c.headers.update(H)
    w = c.post("/api/weddings", json={"name": "W"}).json()
    send(c, w["id"], "u1", data=jpeg((150, 110, 90), (320, 240)))
    pid = c.post("/api/presets/new", json={"name": "Mine", "look": {"mode": "manual", "ev": 0.3}}).json()["id"]
    f = c.get(f"/api/weddings/{w['id']}").json()["folders"][0]["id"]
    c.put(f"/api/weddings/{w['id']}/folders/{f}/look", json={"mode": "preset", "preset": pid})
    photo = c.get(f"/api/weddings/{w['id']}/photos").json()["photos"][0]["id"]
    thumb = lambda: c.get(f"/api/weddings/{w['id']}/photos/{photo}/image?size=360").content
    before = thumb()

    worker = Renderer()
    seen: dict = {}
    reload_presets(app.state.store, worker, seen)
    old = worker.presets[pid]["settings"].get("Exposure2012")
    time.sleep(0.01)
    c.post("/api/presets/new", json={"name": "Mine", "replace": pid, "look": {"mode": "manual", "ev": -1.5}})
    reload_presets(app.state.store, worker, seen)                 # same number of presets, new content
    assert worker.presets[pid]["settings"].get("Exposure2012") != old
    assert thumb() != before                                      # the grid picture is made again


# ---- 5. capture times as the browser and the plug-in send them ---------------------------------
def test_burst_grouping_reads_iso_times():
    from engine.cull import _seconds, group_similar
    assert _seconds("2026-01-01T10:00:00") - _seconds("2026-01-01T09:59:58") == 2
    assert _seconds("2026-01-01T10:00:00") == _seconds("2026:01:01 10:00:00")
    assert _seconds("2026-01-01T10:00:00+05:30") == _seconds("2026-01-01T04:30:00Z")
    assert _seconds("2026-01-01T10:00:00.250") - _seconds("2026-01-01T10:00:00") == 0.25
    assert _seconds("") is None and _seconds("soon") is None
    h = np.zeros(64, bool)
    m = [{"taken": "2026-01-01T10:00:00", "hash": h}, {"taken": "2026-01-01T10:00:02", "hash": h},
         {"taken": "2026-01-01T11:00:00", "hash": h}]
    g = group_similar(m)
    assert g[0] == g[1] != g[2]


# ---- 7, 8. uploads off the event loop; memory ---------------------------------------------------
def test_uploads_run_in_the_thread_pool(app):
    routes = {r.path + r.methods.__repr__(): r.endpoint for r in app.routes if hasattr(r, "endpoint")}
    for path in ("/api/weddings/{wedding_id}/photos{'POST'}", "/api/presets{'POST'}"):
        assert not inspect.iscoroutinefunction(routes[path]), path


def test_huge_pixel_counts_are_refused_before_decoding(app):
    c = TestClient(app)
    c.headers.update(H)
    w = c.post("/api/weddings", json={"name": "W"}).json()
    data = bytearray(jpeg())
    i = data.index(b"\xff\xc0")                                   # the frame header: claim 20000 x 20000
    data[i + 5:i + 9] = (20000).to_bytes(2, "big") + (20000).to_bytes(2, "big")
    r = send(c, w["id"], "big", data=bytes(data))
    assert r.status_code == 413 and "megapixels" in r.json()["detail"]
    assert send(c, w["id"], "ok").status_code == 200


def test_at_most_two_big_previews_render_at_once(app):
    c = TestClient(app)
    c.headers.update(H)
    w = c.post("/api/weddings", json={"name": "W"}).json()
    send(c, w["id"], "u1", data=jpeg(size=(1600, 1067)))
    photo = c.get(f"/api/weddings/{w['id']}/photos").json()["photos"][0]["id"]
    r = app.state.renderer
    now, most, lock = [0], [0], threading.Lock()
    real = r.render

    def slow(*a, **k):
        with lock:
            now[0] += 1
            most[0] = max(most[0], now[0])
        time.sleep(0.2)
        try:
            return real(*a, **k)
        finally:
            with lock:
                now[0] -= 1
    r.render = slow
    threads = [threading.Thread(target=lambda: c.get(f"/api/weddings/{w['id']}/photos/{photo}/image?size=1600"))
               for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert most[0] == 2


def test_one_full_size_export_at_a_time(tmp_path):
    from server.store import Store
    from server.worker import big_export_lock
    store = Store(tmp_path)
    for size, expected in ((0, 1), (4000, 1), (2560, 3)):
        now, most, lock = [0], [0], threading.Lock()

        def job():
            with big_export_lock(store, size):
                with lock:
                    now[0] += 1
                    most[0] = max(most[0], now[0])
                time.sleep(0.1)
                with lock:
                    now[0] -= 1
        threads = [threading.Thread(target=job) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert most[0] == expected, size


# ---- 9. the Lightroom list stays quick -------------------------------------------------------
def test_auto_values_are_worked_out_after_upload_and_kept(app, tmp_path):
    c = TestClient(app)
    c.headers.update(H)
    w = c.post("/api/weddings", json={"name": "W"}).json()
    for i in range(3):
        send(c, w["id"], f"u{i}", data=jpeg(size=(320, 240), noise=True))
    f = c.get(f"/api/weddings/{w['id']}").json()["folders"][0]["id"]
    c.put(f"/api/weddings/{w['id']}/folders/{f}/look", json={"mode": "auto"})
    work(idle_exit=True, data_dir=str(tmp_path))
    with sqlite3.connect(app.state.store.db_path) as db:
        kept = [json.loads(r[0]) for r in db.execute("SELECT autos FROM photos")]
    assert all("tone2:11" in k and "noise:400" in k for k in kept)
    before = {p["uuid"]: p["edit"] for p in c.get(f"/api/weddings/{w['id']}/apply").json()["photos"]}
    for p in (tmp_path / "weddings" / w["id"] / "previews").glob("*.jpg"):
        p.unlink()                                                # working copies gone (trimmed): kept values serve
    fresh = create_app(str(tmp_path), KEY, admin_email=ADMIN, mailer=Mailbox())
    after = {p["uuid"]: p["edit"] for p in TestClient(fresh).get(f"/api/weddings/{w['id']}/apply", headers=H).json()["photos"]}
    assert after == before and after["u0"]["settings"]["Exposure2012"] is not None


def test_apply_pages_end_early_for_the_plugins_time_limit(app, monkeypatch):
    import server.main as m
    c = TestClient(app)
    c.headers.update(H)
    w, _ = wedding(c, 5, look=EV)
    monkeypatch.setattr(m, "APPLY_SECONDS", 0.0)
    page = c.get(f"/api/weddings/{w['id']}/apply?dry=1&paged=1&limit=200").json()
    assert len(page["photos"]) == 1 and page["next"] == 1 and page["total"] == 5
    assert len(c.get(f"/api/weddings/{w['id']}/apply?limit=200").json()["photos"]) == 5    # plug-in 0.7: the whole page


# ---- 10, 11. culls take turns with exports; indexes ------------------------------------------
def test_culls_go_in_chunks_so_exports_get_turns(app, monkeypatch):
    import server.culling as culling
    from server.culling import Culls
    monkeypatch.setattr(culling, "CHUNK", 2)
    c, uid = photographer(app)
    w, _ = wedding(c, 5)
    c.post(f"/api/weddings/{w['id']}/cull", json={})
    culls = Culls(app.state.store)
    renderer = app.state.renderer
    job = culls.claim()
    assert culls.run(job, renderer) == 0                          # two photos measured, then back on the list
    (state,) = culls.for_wedding(w["id"])
    assert state["state"] == "waiting" and state["done"] == 2
    while (job := culls.claim()):
        culls.run(job, renderer)
    (state,) = culls.for_wedding(w["id"])
    assert state["state"] == "done" and state["done"] == 5


def test_export_indexes_exist(app):
    with sqlite3.connect(app.state.store.db_path) as db:
        names = {r[1] for r in db.execute("SELECT * FROM sqlite_master WHERE type='index'")}
    assert {"exports_by_wedding", "export_items_by_status"} <= names


# ---- 12. downloads named in Hindi ----------------------------------------------------------------
def test_downloads_with_non_latin_names(app):
    c, uid = photographer(app)
    w, _ = wedding(c, 1, name="आशा और रोहन")
    r = c.get(f"/api/weddings/{w['id']}/originals.zip")
    assert r.status_code == 200 and "filename*=UTF-8''" in r.headers["content-disposition"]
    assert "%E0%A4" in r.headers["content-disposition"]
    pid = c.post("/api/presets/new", json={"name": "सुनहरा", "look": {"mode": "manual", "ev": 0.3}}).json()["id"]
    assert c.get(f"/api/presets/{pid}/xmp").status_code == 200


# ---- 13. nothing important lost on restart -------------------------------------------------------
def test_state_survives_a_restart(tmp_path):
    a = create_app(str(tmp_path), KEY, admin_email=ADMIN, mailer=Mailbox())
    c = TestClient(a)
    path = c.post("/api/login-link", headers=H).json()["path"]
    key = c.post("/api/looks", headers=H, json={"mode": "manual", "ev": 0.2}).json()["key"]
    for i in range(6):
        c.post("/api/forgot", json={"email": f"x{i}@example.com"})
    b = create_app(str(tmp_path), KEY, admin_email=ADMIN, mailer=Mailbox())         # a deploy restarts the server
    d = TestClient(b)
    assert "autoedits=" in d.get(path, follow_redirects=False).headers.get("set-cookie", "")
    assert d.post("/api/forgot", json={"email": "y@example.com"}).status_code == 429     # the limit is remembered
    w = d.post("/api/weddings", headers=H, json={"name": "W"}).json()
    send(d.__class__(b, headers=H), w["id"], "u1")
    photo = d.get(f"/api/weddings/{w['id']}/photos", headers=H).json()["photos"][0]["id"]
    assert d.get(f"/api/weddings/{w['id']}/photos/{photo}/develop?lk={key}", headers=H).status_code == 200


def test_rate_limits_expire_per_key(tmp_path):
    from server.state import State
    from server.store import Store
    s = State(Store(tmp_path))
    assert [s.hit("try", "a", 0.2) for _ in range(3)] == [0, 1, 2]
    for i in range(50):
        s.hit("try", f"other-{i}", 60)                            # many other addresses do not reset "a"
    assert s.hit("try", "a", 0.2) == 3
    time.sleep(0.25)
    assert s.hit("try", "a", 0.2) == 0                            # expired on its own


# ---- 14. health check -------------------------------------------------------------------------------
def test_health_emails_only_when_something_is_wrong(tmp_path, monkeypatch):
    import datetime as dt
    import shutil
    from collections import namedtuple
    from server import health
    from server.backups import Backups, IST
    from server.store import Store
    store = Store(tmp_path)
    from server.exports import Exports
    Exports(store)
    box = Mailbox()
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: usage(100, 40, 60))
    monkeypatch.setattr(health, "HOUR", 0)
    day = dt.datetime.now(IST) - dt.timedelta(days=1)
    found = health.run_if_due(store, box, "studio@example.com", now=day)
    assert "no database backup has been made" in found and "no worker heartbeat yet" in found
    assert len(box.sent) == 1 and box.sent[0][0] == "studio@example.com" and "problem" in box.sent[0][1]
    assert health.run_if_due(store, box, "studio@example.com", now=day) is None          # once a day

    Backups(store).run()
    health.beat(store)
    assert health.problems(store) == []
    assert health.run_if_due(store, box, "studio@example.com", now=day + dt.timedelta(days=1)) == []
    assert len(box.sent) == 1                                     # all well: no email
    monkeypatch.setattr(shutil, "disk_usage", lambda p: usage(100, 90, 10))
    assert health.problems(store) == ["disk 90% full"]
    assert "health" in (ROOT / "deploy" / "remote.sh").read_text()


# ---- 15. small things ---------------------------------------------------------------------------------
def test_strip_images_do_not_crash_the_masks():
    from engine import masks
    for shape in ((1, 3000, 3), (3000, 1, 3)):
        m = masks.compute(np.full(shape, 0.5, np.float32))
        assert all(min(v.shape) >= 1 for v in m.values())


def test_old_grid_pictures_are_pruned(tmp_path):
    from server.store import Store
    store = Store(tmp_path)
    d = store.cache_dir("w1")
    old, new = d / "old.jpg", d / "new.jpg"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    then = time.time() - 31 * 86400
    os.utime(old, (then, then))
    store.trim_working_copies(10 ** 12)
    assert not old.exists() and new.exists()
