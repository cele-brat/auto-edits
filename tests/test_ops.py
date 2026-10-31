"""/healthz for uptime monitors, python -m server.mailcheck, the staging guard, and the deploy files."""
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import health, mailcheck, staging, uptime
from server.main import create_app
from server.store import Store

ROOT = Path(__file__).resolve().parent.parent
KEY = "test-key-0123456789"


class Box:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, text, html="", wait=False):
        self.sent.append((to, subject, text))


@pytest.fixture
def app(tmp_path):
    return create_app(str(tmp_path), KEY, admin_email="studio@cele-brat.com", mailer=Box())


# ---- /healthz -----------------------------------------------------------------------------
def test_healthz_is_public_and_follows_the_worker_heartbeat(app):
    c = TestClient(app)
    c.headers.pop("X-Requested-With", None)
    r = c.get("/healthz")
    assert r.status_code == 503 and r.json()["ok"] is False and r.json()["db"] == "ok"
    assert r.json()["worker_heartbeat_age_s"] is None and "version" in r.json()
    health.beat(app.state.store)
    r = c.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] and r.json()["worker_heartbeat_age_s"] <= 1
    assert r.headers["cache-control"] == "no-store"
    assert set(r.json()) == {"ok", "version", "db", "worker_heartbeat_age_s"}        # no secrets, nothing else
    for _ in range(30):                                                               # not rate limited
        assert c.get("/healthz").status_code == 200
    assert c.head("/healthz").status_code == 200


def test_healthz_says_503_when_the_worker_went_quiet(tmp_path):
    store = Store(tmp_path)
    health.beat(store)
    assert uptime.status(store)["ok"]
    old = uptime.status(store, now=time.time() + (health.HEARTBEAT_MINUTES + 1) * 60)
    assert not old["ok"] and old["worker_heartbeat_age_s"] > health.HEARTBEAT_MINUTES * 60


def test_healthz_says_503_when_the_database_fails(tmp_path):
    class Broken:
        def _db(self):
            raise OSError("disk gone")
    s = uptime.status(Broken())
    assert s["ok"] is False and s["db"].startswith("error")


# ---- mailcheck ----------------------------------------------------------------------------
def fake_dns(records):
    return lambda name: records.get(name, [])


def test_mailcheck_reports_pass_and_fail_with_the_values_to_add():
    dns = fake_dns({"cele-brat.solutions": ["v=spf1 include:_spf.google.com ~all", "google-site-verification=abc"],
                    "google._domainkey.cele-brat.solutions": ["v=DKIM1; k=rsa; p=MIIB..."]})
    out = mailcheck.check("cele-brat.solutions", dns)
    assert [(c["record"], c["ok"]) for c in out] == [("SPF", True), ("DKIM", True), ("DMARC", False)]
    assert mailcheck.missing("cele-brat.solutions", dns) == ["DMARC"]
    text = mailcheck.report("cele-brat.solutions", dns)
    assert "SPF   PASS" in text and "DMARC FAIL" in text
    assert "_dmarc.cele-brat.solutions" in text and "v=DMARC1; p=quarantine; rua=mailto:dmarc@cele-brat.solutions" in text
    assert "google._domainkey.cele-brat.solutions" in text


def test_mailcheck_spf_without_google_fails_and_lookup_failure_is_said():
    dns = fake_dns({"x.in": ["v=spf1 include:spf.protection.outlook.com -all"]})
    out = mailcheck.check("x.in", dns)
    assert not out[0]["ok"] and "_spf.google.com" in out[0]["want"]
    text = mailcheck.report("x.in", lambda name: None)
    assert "FAIL (lookup failed)" in text


def test_mailcheck_domain_from_mail_from_and_main(capsys):
    assert mailcheck.sending_domain({"MAIL_FROM": "Auto Edits <no-reply@cele-brat.solutions>"}) == "cele-brat.solutions"
    assert mailcheck.sending_domain({"SMTP_USER": "bharat@cele-brat.com"}) == "cele-brat.com"
    assert mailcheck.sending_domain({}) == ""
    assert mailcheck.main([]) == 2 or os.environ.get("MAIL_FROM")       # no domain: says so, exit 2


def test_health_email_mentions_missing_email_records_only_when_missing(tmp_path, monkeypatch):
    import datetime as dt
    import shutil
    from collections import namedtuple
    from server.backups import Backups
    monkeypatch.setattr(shutil, "disk_usage", lambda p: namedtuple("u", "total used free")(100, 10, 90))
    store = Store(tmp_path)
    Backups(store).run()
    health.beat(store)
    monkeypatch.setattr(health, "HOUR", 0)
    monkeypatch.setenv("MAIL_FROM", "Auto Edits <no-reply@cele-brat.solutions>")
    monkeypatch.setattr(mailcheck, "lookup_txt", lambda name, resolver=None: [])
    box = Box()
    found = health.run_if_due(store, box, "studio@example.com", now=dt.datetime.now(health.IST))
    assert found == ["email records missing for the sending domain: SPF, DKIM, DMARC (run: python -m server.mailcheck)"]
    assert len(box.sent) == 1 and "SPF, DKIM, DMARC" in box.sent[0][2]
    good = {"spf": ["v=spf1 include:_spf.google.com ~all"], "dkim": ["v=DKIM1; p=x"], "dmarc": ["v=DMARC1; p=none"]}
    monkeypatch.setattr(mailcheck, "lookup_txt", lambda name, resolver=None: good["dkim" if "_domainkey" in name else "dmarc" if "_dmarc" in name else "spf"])
    with store._db() as db:
        db.execute("DELETE FROM settings WHERE k='health_last_day'")       # due again today
    assert health.run_if_due(store, box, "studio@example.com", now=dt.datetime.now(health.IST)) == []
    assert len(box.sent) == 1                                          # nothing to say: no email


def _txt_reply(query: bytes, values: list[str], rcode: int = 0) -> bytes:
    import struct
    qid = struct.unpack(">H", query[:2])[0]
    i = 12
    while query[i]:
        i += query[i] + 1
    question = query[12:i + 5]
    answers = b""
    for v in values:
        chunks = [v[k:k + 255].encode() for k in range(0, len(v), 255)]
        rdata = b"".join(bytes([len(c)]) + c for c in chunks)
        answers += b"\xc0\x0c" + struct.pack(">HHIH", 16, 1, 300, len(rdata)) + rdata
    return struct.pack(">HHHHHH", qid, 0x8180 | rcode, 1, len(values), 0, 0) + question + answers


def test_mailcheck_plain_python_lookup_reads_long_txt_records(monkeypatch):
    import socket
    import threading
    long_key = "v=DKIM1; k=rsa; p=" + "A" * 400                        # split across two TXT strings
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]

    def serve():
        for _ in range(2):
            q, addr = srv.recvfrom(4096)
            nx = b"_dmarc" in q
            srv.sendto(_txt_reply(q, [] if nx else [long_key], rcode=3 if nx else 0), addr)
    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setattr(mailcheck, "DNS_PORT", port)
    monkeypatch.setattr(mailcheck, "_nameservers", lambda: ["127.0.0.1"])
    assert mailcheck._txt_socket("google._domainkey.cele-brat.solutions") == [long_key]
    assert mailcheck._txt_socket("_dmarc.cele-brat.solutions") == []             # NXDOMAIN: no record
    srv.close()


def test_mailcheck_failed_lookup_is_not_a_missing_record():
    assert mailcheck.missing("cele-brat.solutions", lambda name: None) == []
    assert mailcheck.status("cele-brat.solutions", lambda name: None) == ([], ["SPF", "DKIM", "DMARC"])
    assert mailcheck.parse_txt_answer(b"\x00" * 4, 1) is None


def test_health_email_says_when_email_records_could_not_be_checked(tmp_path, monkeypatch):
    import datetime as dt
    import shutil
    from collections import namedtuple
    from server.backups import Backups
    monkeypatch.setattr(shutil, "disk_usage", lambda p: namedtuple("u", "total used free")(100, 10, 90))
    store = Store(tmp_path)
    Backups(store).run()
    health.beat(store)
    monkeypatch.setattr(health, "HOUR", 0)
    monkeypatch.setenv("MAIL_FROM", "Auto Edits <no-reply@cele-brat.solutions>")
    monkeypatch.setattr(mailcheck, "lookup_txt", lambda name, resolver=None: None)
    found = health.run_if_due(store, Box(), "studio@example.com", now=dt.datetime.now(health.IST))
    assert len(found) == 1 and found[0].startswith("could not look up the sending domain's email records: SPF, DKIM, DMARC")


# ---- staging ------------------------------------------------------------------------------
class Rzp:
    def __init__(self, key):
        self.key_id = key
    on = property(lambda self: bool(self.key_id))
    test_mode = property(lambda self: self.key_id.startswith("rzp_test_"))


def test_staging_refuses_live_keys_and_holds_email():
    staging.check(Rzp("rzp_test_abc"), env="staging")
    staging.check(Rzp(""), env="staging")
    staging.check(Rzp("rzp_live_abc"), env="production")
    with pytest.raises(RuntimeError):
        staging.check(Rzp("rzp_live_abc"), env="staging")
    box = Box()
    m = staging.only_studio(box, "Studio@cele-brat.com", env="staging")
    m.send("ravi@example.com", "Hello", "text")
    m.send("studio@cele-brat.com", "Health", "text")
    assert box.sent == [("studio@cele-brat.com", "Health", "text")] and m.held == [("ravi@example.com", "Hello")]
    assert staging.only_studio(box, "studio@cele-brat.com", env="production") is box


def test_staging_app_refuses_to_start_with_a_live_key(tmp_path, monkeypatch):
    from server.payments import Razorpay
    monkeypatch.setattr(staging, "ENV", "staging")
    with pytest.raises(RuntimeError):
        create_app(str(tmp_path), KEY, admin_email="studio@cele-brat.com", mailer=Box(), razorpay=Razorpay("rzp_live_x", "s", "w"))
    app = create_app(str(tmp_path), KEY, admin_email="studio@cele-brat.com", mailer=Box(), razorpay=Razorpay("rzp_test_x", "s", "w"))
    c = TestClient(app)
    c.post("/api/login", json={"key": KEY})
    assert c.get("/api/session").json()["env"] == "staging"


def test_deploy_files_for_staging_and_uptime():
    compose = (ROOT / "deploy" / "docker-compose.staging.yml").read_text()
    assert "server-staging" in compose and "worker-staging" in compose and "8432" in compose
    assert "AUTOEDITS_ENV: staging" in compose and "data-staging:/data" in compose
    assert "staging.edits.cele-brat.solutions" in compose and "traefik.http.routers.autoedits-staging" in compose
    assert "autoedits-staging" in compose
    sh = (ROOT / "deploy" / "staging.sh").read_text()
    assert "docker-compose.staging.yml" in sh and "rzp_live_" in sh and os.access(ROOT / "deploy" / "staging.sh", os.X_OK)
    assert "./deploy/staging.sh up" in (ROOT / "deploy" / "STAGING.md").read_text()
    assert "https://edits.cele-brat.solutions/healthz" in (ROOT / "deploy" / "UPTIME.md").read_text()
    remote = (ROOT / "deploy" / "remote.sh").read_text()
    assert "staging" not in remote                                      # allowed jobs unchanged
    assert "deploy/.env.staging" in (ROOT / ".gitignore").read_text()
