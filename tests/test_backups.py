"""Daily database copies, kept in the bucket for 30 days, and bringing one back."""
import datetime as dt
import gzip
import shutil
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from server import restore
from server.backups import IST, Backups
from server.main import create_app
from server.store import Store

KEY = "test-key-0123456789"


class FolderBlobs:
    """Stands in for the R2 bucket."""
    on = True

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, key, path, content_type="image/jpeg"):
        dst = self.root / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dst)

    def keys(self, prefix):
        return sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*") if p.is_file()
                      and str(p.relative_to(self.root)).startswith(prefix))

    def delete(self, key):
        (self.root / key).unlink(missing_ok=True)

    def fetch(self, key, path):
        src = self.root / key
        if not src.exists():
            return False
        shutil.copyfile(src, path)
        return True

    def delete_prefix(self, prefix):
        return 0

    def stream(self, key):
        return iter(())


def at(day, hour=4):
    return dt.datetime(2026, 10, day, hour, 0, tzinfo=IST)


def make(tmp_path, blobs=None):
    app = create_app(str(tmp_path / "data"), KEY, admin_email="studio@cele-brat.com",
                     blobs=blobs, mailer=type("M", (), {"send": lambda *a, **k: None})())
    return app, Store(tmp_path / "data", blobs)


def test_copy_goes_to_the_bucket_and_opens(tmp_path):
    bucket = FolderBlobs(tmp_path / "bucket")
    _, store = make(tmp_path, bucket)
    info = Backups(store).run(at(14))
    assert info["where"] == "R2 bucket" and info["users"] == 1
    key = "backups/db/autoedits-2026-10-14.db.gz"
    assert bucket.keys("backups/") == [key]
    raw = tmp_path / "copy.db"
    raw.write_bytes(gzip.decompress((bucket.root / key).read_bytes()))
    db = sqlite3.connect(raw)
    assert db.execute("SELECT email FROM users").fetchone()[0] == "studio@cele-brat.com"


def test_once_a_day_after_three_in_the_morning(tmp_path):
    _, store = make(tmp_path, FolderBlobs(tmp_path / "bucket"))
    b = Backups(store)
    assert b.due(at(14, 1))                       # never backed up: straight away
    b.run(at(14, 1))
    assert not b.due(at(14, 23))                  # already done today
    assert not b.due(at(15, 2))                   # tomorrow, but before 3 am
    assert b.due(at(15, 3))
    assert b.run_if_due(at(15, 3)) and not b.run_if_due(at(15, 4))


def test_old_copies_are_removed_after_thirty_days(tmp_path):
    bucket = FolderBlobs(tmp_path / "bucket")
    _, store = make(tmp_path, bucket)
    b = Backups(store)
    start = dt.datetime(2026, 9, 1, 4, tzinfo=IST)
    for i in range(40):
        b.run(start + dt.timedelta(days=i))
    keys = bucket.keys("backups/db/")
    assert len(keys) == 30
    assert keys[0].endswith("2026-09-11.db.gz") and keys[-1].endswith("2026-10-10.db.gz")
    assert len(list((Path(store.root) / "backups").glob("*.db.gz"))) == 2   # two kept on the server too
    assert len(b.list()) == 30


def test_without_the_bucket_copies_stay_on_the_server(tmp_path):
    _, store = make(tmp_path)
    b = Backups(store)
    info = b.run(at(14))
    assert info["where"] == "this server only" and not b.status()["off_server"]


def test_restore_unpacks_beside_the_live_database(tmp_path, monkeypatch, capsys):
    bucket = FolderBlobs(tmp_path / "bucket")
    _, store = make(tmp_path, bucket)
    Backups(store).run(at(13))
    for p in (Path(store.root) / "backups").glob("*.db.gz"):
        p.unlink()                                # only the bucket has it now
    monkeypatch.setenv("AUTOEDITS_DATA", str(store.root))
    monkeypatch.setattr(restore.blob_store, "from_environment", lambda: bucket)
    assert restore.main(["--day", "2026-10-13"]) == 0
    assert "1 accounts" in capsys.readouterr().out
    assert (Path(store.root) / "restore-2026-10-13.db").exists()
    assert restore.main(["--day", "2026-01-01"]) == 1


def test_admin_sees_backups_and_can_back_up_now(tmp_path):
    app, _ = make(tmp_path, FolderBlobs(tmp_path / "bucket"))
    c = TestClient(app)
    assert c.get("/api/admin/backups").status_code in (401, 403, 404)
    assert c.post("/api/login", json={"key": KEY}).status_code == 200
    first = c.get("/api/admin/backups").json()
    assert first["copies"] == [] and first["status"]["off_server"]
    after = c.post("/api/admin/backups/run").json()
    assert len(after["copies"]) == 1 and after["status"]["where"] == "R2 bucket"
