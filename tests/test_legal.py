"""Policy pages for Razorpay and the law, and agreeing to them at sign-up."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from server import legal
from server.main import create_app


@pytest.fixture
def client(tmp_path):
    mail = type("M", (), {"send": lambda *a, **k: None})()
    return TestClient(create_app(str(tmp_path), "test-key-0123456789", admin_email="studio@cele-brat.com", mailer=mail)), tmp_path


@pytest.mark.parametrize("slug,words", [
    ("terms", ["Celebrat Ventures", "can view your weddings and download", "Ajmer", "Refund policy"]),
    ("privacy", ["Digital Personal Data Protection", "Grievance officer", "access log", "8 years"]),
    ("refunds", ["within 7 days", "free credits, cannot be refunded", "5 to 7 working days"]),
    ("shipping", ["Nothing is shipped", "within a minute"]),
    ("contact", ["support@cele-brat.com", "Celebrat Ventures"]),
])
def test_policy_pages(client, slug, words):
    c, _ = client
    r = c.get(f"/{slug}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    for w in words:
        assert w in r.text, (slug, w)
    assert c.head(f"/{slug}").status_code == 200
    assert f"/{slug}</loc>" in c.get("/sitemap.xml").text and f"Allow: /{slug}" in c.get("/robots.txt").text


def test_no_postal_address_on_the_site(client):
    c, _ = client
    for slug in legal.PAGES:
        assert "Pushkar" not in c.get(f"/{slug}").text and "GSTIN" not in c.get(f"/{slug}").text
    assert c.get("/contact").text.count("Phone") == (1 if legal.SUPPORT_PHONE else 0)
    assert c.get("/no-such-page").status_code == 404


def test_sign_up_needs_the_tick_and_records_it(client):
    c, tmp = client
    r = c.post("/api/signup", json={"email": "a@example.com", "password": "a good password"})
    assert r.status_code == 422 and "Terms" in r.json()["detail"]
    assert c.post("/api/signup", json={"email": "a@example.com", "password": "a good password", "agree": True}).status_code == 200
    row = sqlite3.connect(tmp / "autoedits.db").execute("SELECT agreed, terms_version FROM users WHERE email='a@example.com'").fetchone()
    assert row[0] > 0 and row[1] == legal.TERMS_VERSION
