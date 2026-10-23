"""Plug-in 0.9: the settings window's "Connected as" / "Credits" / new version lines, and star ratings
and colour labels brought back from the app (runs the Lua against the mock Lightroom, as test_plugin.py)."""
import pytest

lupa = pytest.importorskip("lupa")

from tests.test_plugin import KEY, PLUGIN, Rig  # noqa: E402


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def _settings(rig):
    return rig.lua.eval("(require 'Settings')")


def test_version_words(rig):
    S = _settings(rig)
    assert S.myVersion() == "0.9.0"
    assert S.isNewer("0.10.0", "0.9.0") and S.isNewer("1.0", "0.9.0") and not S.isNewer("0.9.0", "0.9.0")
    assert not S.isNewer("0.8.5", "0.9.0") and not S.isNewer("nonsense", "0.9.0")
    who, credits, version, newer = S.describe(rig.lua.table_from({"email": "a@b.c", "credits": 12, "plugin_version": "0.9.0"}), "0.9.0")
    assert (who, credits, version, newer) == ("Connected as a@b.c", "Credits: 12", "Plug-in 0.9.0 is up to date.", False)
    who, credits, version, newer = S.describe(rig.lua.table_from({"email": "s@b.c", "unlimited": True, "plugin_version": "0.10.0"}), "0.9.0")
    assert credits == "Credits: unlimited (studio account)" and newer and "0.10.0" in version and "this is 0.9.0" in version


def test_settings_window_shows_who_and_credits(rig):
    acc = rig.client.app.state.accounts
    u = acc.sign_up("ravi@example.com", "a good password", "Ravi", "", "test")
    acc.verify(u["id"])
    rig.mock.prefs.serverKey = acc.new_plugin_key(u["id"])
    rig.run("Settings.lua")
    assert ("GET", "https://edits.test/api/me/plugin") in rig.calls
    props = rig.mock.props
    assert props.who == "Connected as ravi@example.com" and props.credits == f"Credits: {acc.balance(u['id'])}"
    assert props.newer is False and props.status == "Connected."
    # a wrong key: the plain message, no "Connected as"
    rig.mock.prefs.serverKey = "wrong-key-0123456789"
    rig.run("Settings.lua")
    assert rig.mock.props.who == "" and "did not accept the key" in rig.mock.props.status


def test_stars_and_labels_come_back_to_lightroom(rig):
    a = rig.add_photo("u1", "A.NEF", "Haldi")
    b = rig.add_photo("u2", "B.NEF", "Haldi")
    rig.mock.dialogEdits.wedding = "Asha & Rohan"
    rig.run()
    (w,) = rig.weddings()
    H = {"X-Auto-Edits-Key": KEY}
    photos = rig.photos(w["id"])
    pa = [p for p in photos if p["lr_uuid"] == "u1"][0]["id"]
    rig.client.put(f"/api/weddings/{w['id']}/photos/{pa}/marks", headers=H, json={"stars": 5, "label": "green"})
    rig.mock.dialogEdits.wedding = w["id"]
    rig.run("ApplyEdits.lua")
    assert a.spec.raw.rating == 5 and a.spec.raw.colorNameForLabel == "green"
    assert b.spec.raw.rating == 0 and b.spec.raw.colorNameForLabel is None
    assert "ratings and colour labels from the app: 1 photos" in rig.messages()[-1][1]
    assert "Auto Edits: ratings and labels" in list(rig.mock.history.values())
