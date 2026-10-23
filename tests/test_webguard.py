"""Security headers, the cross-site form check, the plug-in download and the preview size cap."""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.main import create_app

KEY = "test-key-0123456789"
NO_HEADER = {"X-Requested-With": ""}


class Box:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, text, html="", wait=False):
        self.sent.append((to, subject, text))


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path), KEY, admin_email="studio@cele-brat.com", mailer=Box())


def jpeg(size=(2400, 1600)):
    buf = io.BytesIO()
    Image.new("RGB", size, (150, 110, 90)).save(buf, "JPEG")
    return buf.getvalue()


def photographer(app):
    c = TestClient(app)
    assert c.post("/api/signup", json={"email": "p@example.com", "password": "a good password", "agree": True}).status_code == 200
    assert c.post("/api/login", json={"email": "p@example.com", "password": "a good password"}).status_code == 200   # sign-up gives no session
    return c


def upload(c, wid, headers=None):
    meta = {"uuid": "u1", "folder": "Haldi", "fileName": "A.NEF"}
    return c.post(f"/api/weddings/{wid}/photos", headers=headers or {}, data={"meta": json.dumps(meta)},
                  files={"preview": ("a.jpg", jpeg(), "image/jpeg")})


@pytest.mark.parametrize("path", ["/", "/terms", "/api/ping", "/static/app.js", "/download/lightroom-plugin"])
def test_every_response_has_security_headers(app, path):
    r = TestClient(app).get(path)
    h = r.headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert "max-age=" in h["strict-transport-security"] and h["referrer-policy"]
    csp = h["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert "https://*.razorpay.com" in csp and "https://fonts.googleapis.com" in csp and "'unsafe-eval'" not in csp


def test_inline_scripts_of_our_pages_are_allowed_by_hash(app):
    from server import webguard
    from server.main import STATIC
    hashes = webguard.inline_hashes(STATIC)
    assert len(hashes) >= 3                                   # home, reset and admin pages
    csp = TestClient(app).get("/").headers["content-security-policy"]
    assert all(h in csp for h in hashes)


def test_cookie_requests_from_other_sites_are_refused(app):
    c = photographer(app)
    w = c.post("/api/weddings", json={"name": "Asha & Rohan"}).json()
    # a form posted by another website: the browser sends the cookie, but not our header
    r = upload(c, w["id"], headers=NO_HEADER)
    assert r.status_code == 403 and "refused" in r.json()["detail"]
    assert r.headers["x-frame-options"] == "DENY"
    assert c.post("/api/weddings", headers=NO_HEADER, json={"name": "x"}).status_code == 403
    assert c.post("/api/logout", headers=NO_HEADER).status_code == 403
    assert upload(c, w["id"]).status_code == 200              # the app itself sends the header
    assert c.get("/api/weddings", headers=NO_HEADER).status_code == 200   # reading is never blocked


def test_plugin_key_webhook_and_sign_up_need_no_header(app):
    c = photographer(app)
    key = c.post("/api/plugin-key").json()["key"]
    plugin = TestClient(app, headers={"X-Requested-With": ""})
    plugin.headers.pop("X-Requested-With", None)
    H = {"X-Auto-Edits-Key": key}
    w = plugin.post("/api/weddings", headers=H, json={"name": "From Lightroom"}).json()
    assert upload(plugin, w["id"], headers=H).status_code == 200
    stranger = TestClient(app)
    stranger.headers.pop("X-Requested-With", None)
    assert stranger.post("/api/signup", json={"email": "n@example.com", "password": "a good password", "agree": True}).status_code == 200
    stranger.cookies.clear()
    assert stranger.post("/api/login", json={"email": "n@example.com", "password": "a good password"}).status_code == 200
    hook = TestClient(app)
    hook.headers.pop("X-Requested-With", None)
    hook.cookies.set("autoedits", "anything")
    assert hook.post("/api/razorpay/webhook", content=b"{}").status_code in (400, 503)   # reaches the signature check


def test_lightroom_plugin_download(app):
    r = TestClient(app).get("/download/lightroom-plugin")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "AutoEdits-Lightroom-plugin.zip" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "AutoEdits.lrplugin/Info.lua" in names and "AutoEdits.lrplugin/Server.lua" in names
    assert all(n.startswith("AutoEdits.lrplugin/") for n in names)
    assert b"LrToolkitIdentifier" in z.read("AutoEdits.lrplugin/Info.lua")
    assert TestClient(app).head("/download/lightroom-plugin").status_code == 200


def test_marked_previews_are_capped_for_photographers(app):
    c = photographer(app)
    w = c.post("/api/weddings", json={"name": "Big"}).json()
    pid = upload(c, w["id"]).json()["id"]
    big = c.get(f"/api/weddings/{w['id']}/photos/{pid}/image?size=2560")
    assert big.status_code == 200 and max(Image.open(io.BytesIO(big.content)).size) <= 1600
    small = c.get(f"/api/weddings/{w['id']}/photos/{pid}/image?size=360")
    assert max(Image.open(io.BytesIO(small.content)).size) <= 360
    studio = TestClient(app)
    studio.post("/api/login", json={"key": KEY})
    sw = studio.post("/api/weddings", json={"name": "Studio"}).json()
    spid = upload(studio, sw["id"]).json()["id"]
    full = studio.get(f"/api/weddings/{sw['id']}/photos/{spid}/image?size=2560")
    assert max(Image.open(io.BytesIO(full.content)).size) == 2400     # not enlarged, not capped
