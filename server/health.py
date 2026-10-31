"""Health of the service, checked by the worker.

* Each worker process writes a heartbeat (the time) into the database every loop.
* Once a day the worker's main process checks: a database backup in the last 26 hours,
  the disk under 85 % full, no export failures in the last day, a recent worker heartbeat.
  Only when something is wrong is an email sent to the studio address
  (AUTOEDITS_ADMIN_EMAIL, through the usual SMTP settings in deploy/.env).

`python -m server.health` prints one line for deploy/remote.sh: "OK" or the problems as
plain words and numbers. The GitHub repo is public, so it never prints emails, file paths
or settings.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import time

SCHEMA = "CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT NOT NULL);"
BACKUP_HOURS = 26
DISK_PERCENT = 85
HEARTBEAT_MINUTES = 10
HOUR = int(os.environ.get("AUTOEDITS_HEALTH_HOUR", "9"))           # India time, after the night's backup
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _setting(db, k: str) -> str:
    r = db.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    return r[0] if r else ""


def beat(store, who: str = "worker") -> None:
    """A worker process is alive and looking for work."""
    with store._db() as db:
        db.executescript(SCHEMA)
        db.execute("INSERT OR REPLACE INTO settings (k, v) VALUES (?, ?)", (f"heartbeat:{who}", str(time.time())))


def problems(store, backups=None, now: float | None = None) -> list[str]:
    """What is wrong now, as short plain lines (empty when all is well)."""
    from .backups import Backups
    now = now or time.time()
    out = []
    backups = backups or Backups(store)
    last = backups.status().get("last_ok")
    try:
        age = (now - dt.datetime.fromisoformat(last).timestamp()) / 3600 if last else None
    except ValueError:
        age = None
    if age is None:
        out.append("no database backup has been made")
    elif age > BACKUP_HOURS:
        out.append(f"last database backup {age:.0f} hours ago")
    disk = shutil.disk_usage(store.root)
    used = 100 * (disk.total - disk.free) / max(1, disk.total)
    if used > DISK_PERCENT:
        out.append(f"disk {used:.0f}% full")
    with store._db() as db:
        db.executescript(SCHEMA)
        failed = 0
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='export_items'").fetchone():
            failed = db.execute("SELECT COUNT(*) FROM export_items i JOIN exports e ON e.id=i.export_id "
                                "WHERE i.status=2 AND e.created>?", (now - 86400,)).fetchone()[0]
        beats = [float(r[0]) for r in db.execute("SELECT v FROM settings WHERE k LIKE 'heartbeat:%'")]
    if failed:
        out.append(f"{failed} photos failed to export in the last day")
    if not beats:
        out.append("no worker heartbeat yet")
    elif (now - max(beats)) / 60 > HEARTBEAT_MINUTES:
        out.append(f"worker last seen {(now - max(beats)) / 60:.0f} minutes ago")
    return out


def run_if_due(store, mailer, to: str, backups=None, now: dt.datetime | None = None) -> list[str] | None:
    """The daily check, at or after HOUR India time. Emails `to` only when something is wrong.
    Returns the problems found, or None when it was not due."""
    now = now or dt.datetime.now(IST)
    today = now.date().isoformat()
    with store._db() as db:
        db.executescript(SCHEMA)
        if _setting(db, "health_last_day") == today or now.hour < HOUR:
            return None
        db.execute("INSERT OR REPLACE INTO settings (k, v) VALUES ('health_last_day', ?)", (today,))
    found = problems(store, backups, now.timestamp())
    try:                                             # SPF / DKIM / DMARC of the sending domain (server/mailcheck.py)
        from . import mailcheck
        domain = mailcheck.sending_domain()
        gone, unchecked = mailcheck.status(domain) if domain else ([], [])
        if gone:
            found.append(f"email records missing for the sending domain: {', '.join(gone)} (run: python -m server.mailcheck)")
        if unchecked:
            found.append(f"could not look up the sending domain's email records: {', '.join(unchecked)} "
                         "(DNS lookup failed; run: python -m server.mailcheck)")
    except Exception:
        pass
    if found and to:
        text = ("The daily Auto Edits check found something to look at:\n\n" + "\n".join(f"- {p}" for p in found) +
                "\n\nOn the VPS: ./deploy/ops.sh status shows the containers; /admin shows backups.")
        mailer.send(to, f"Auto Edits health: {len(found)} problem{'s' if len(found) != 1 else ''}", text)
    return found


def main():
    from . import blobs as blob_store
    from .store import Store
    store = Store(os.environ.get("AUTOEDITS_DATA", "data/server"), blob_store.from_environment())
    found = problems(store)
    print("health: OK" if not found else "health: problem: " + "; ".join(found))


if __name__ == "__main__":
    main()
