"""Walk-through guide: shown on every visit until Close, then never again, per account."""
import re

from fastapi.testclient import TestClient

from server.main import create_app

KEY = "test-key-0123456789"


class Mailbox:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, text, html="", wait=False):
        self.sent.append((to, subject, text))


def photographer(app, box, email):
    c = TestClient(app)
    assert c.post("/api/signup", json={"email": email, "password": "a good password", "name": "Ravi", "agree": True}).status_code == 200
    token = next(re.search(r"/verify/([\w-]+)", t).group(1) for who, _, t in reversed(box.sent) if who == email)
    c.get(f"/verify/{token}", follow_redirects=False)
    return c


XR = {"X-Requested-With": "AutoEdits"}


def login(app, email):
    c = TestClient(app)
    assert c.post("/api/login", json={"email": email, "password": "a good password"}).status_code == 200
    return c


def test_close_hides_for_this_login_only(tmp_path):
    box = Mailbox()
    app = create_app(str(tmp_path), KEY, admin_email="studio@cele-brat.com", mailer=box)
    ravi = photographer(app, box, "ravi@example.com")
    assert ravi.get("/api/guide").json() == {"show": True}
    assert ravi.get("/api/guide").json() == {"show": True}            # still there on the next visit
    assert ravi.post("/api/guide/close", headers=XR, json={}).json() == {"show": False, "forever": False}
    assert ravi.get("/api/guide").json() == {"show": False}           # hidden for the rest of this login
    assert login(app, "ravi@example.com").get("/api/guide").json() == {"show": True}   # back on the next login
    assert TestClient(app).get("/api/guide").status_code == 401


def test_dont_show_again_is_for_good_per_account(tmp_path):
    box = Mailbox()
    app = create_app(str(tmp_path), KEY, admin_email="studio@cele-brat.com", mailer=box)
    ravi = photographer(app, box, "ravi@example.com")
    asha = photographer(app, box, "asha@example.com")
    assert ravi.post("/api/guide/close", headers=XR, json={"forever": True}).json() == {"show": False, "forever": True}
    assert ravi.get("/api/guide").json() == {"show": False}
    assert ravi.post("/api/guide/close", headers=XR, json={"forever": True}).status_code == 200   # twice is fine
    assert asha.get("/api/guide").json() == {"show": True}            # her own choice
    # a new login, even after a restart, remembers it
    again = create_app(str(tmp_path), KEY, admin_email="studio@cele-brat.com", mailer=box)
    assert login(again, "ravi@example.com").get("/api/guide").json() == {"show": False}
