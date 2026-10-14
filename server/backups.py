"""Daily copies of the database, kept off this server.

Everything that is not a photo lives in one SQLite file (accounts, credits, weddings,
edits, culls, uploaded presets). Once a day the worker makes a safe copy of it while the
app keeps running, checks the copy opens cleanly, compresses it and puts it in the R2
bucket under  backups/db/autoedits-YYYY-MM-DD.db.gz . Copies older than KEEP_DAYS are
removed. The last two copies are also kept on this disk for a quick restore.

To bring one back:  python -m server.restore --list   /   --day 2026-10-14
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import shutil
import sqlite3
import time
import traceback
from pathlib import Path

PREFIX = "backups/db/"
KEEP_DAYS = int(os.environ.get("AUTOEDITS_BACKUP_DAYS", "30"))
LOCAL_KEEP = 2
HOUR = int(os.environ.get("AUTOEDITS_BACKUP_HOUR", "3"))        # India time; the quietest hour
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _key(day: dt.date) -> str:
    return f"{PREFIX}autoedits-{day.isoformat()}.db.gz"


def _day_of(key: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(key.rsplit("autoedits-", 1)[1][:10])
    except (IndexError, ValueError):
        return None


class Backups:
    def __init__(self, store):
        self.store = store
        self.dir = Path(store.root) / "backups"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.status_file = self.dir / "status.json"

    # ---- status (shown on the admin page) -----------------------------------------
    def status(self) -> dict:
        try:
            s = json.loads(self.status_file.read_text())
        except (OSError, ValueError):
            s = {}
        s["off_server"] = bool(self.store.blobs.on)
        s["keep_days"] = KEEP_DAYS
        return s

    def _save_status(self, **changes) -> None:
        s = self.status()
        s.update(changes)
        part = self.status_file.with_suffix(".part")
        part.write_text(json.dumps(s))
        part.replace(self.status_file)

    # ---- making one -----------------------------------------------------------
    def run(self, now: dt.datetime | None = None) -> dict:
        """Make today's copy now (replacing an earlier one from the same day). Returns its details."""
        now = now or dt.datetime.now(IST)
        day = now.date()
        started = time.time()
        raw = self.dir / f"autoedits-{day.isoformat()}.db"
        packed = raw.with_suffix(".db.gz")
        try:
            src = sqlite3.connect(self.store.db_path, timeout=60)
            dst = sqlite3.connect(raw)
            try:
                src.backup(dst)                     # consistent copy while the app keeps writing
            finally:
                dst.close()
                src.close()
            check = sqlite3.connect(raw)
            try:
                ok = check.execute("PRAGMA integrity_check").fetchone()[0]
                users = check.execute("SELECT COUNT(*) FROM users").fetchone()[0] if \
                    check.execute("SELECT 1 FROM sqlite_master WHERE name='users'").fetchone() else 0
            finally:
                check.close()
            if ok != "ok":
                raise RuntimeError(f"the copy did not pass its check: {ok}")
            with open(raw, "rb") as f, gzip.open(packed, "wb", compresslevel=6) as g:
                shutil.copyfileobj(f, g, 1 << 20)
            raw.unlink()
            size = packed.stat().st_size
            where = "this server only"
            removed = 0
            if self.store.blobs.on:
                self.store.blobs.put(_key(day), packed, "application/gzip")
                where = "R2 bucket"
                removed = self._prune_remote(day)
            self._prune_local()
            info = {"last_ok": now.isoformat(timespec="seconds"), "last_day": day.isoformat(), "size": size,
                    "users": users, "where": where, "seconds": round(time.time() - started, 1),
                    "removed": removed, "last_error": "", "last_error_at": ""}
            self._save_status(**info)
            return info
        except Exception as e:
            raw.unlink(missing_ok=True)
            self._save_status(last_error=f"{type(e).__name__}: {e}", last_error_at=now.isoformat(timespec="seconds"))
            raise

    def due(self, now: dt.datetime | None = None) -> bool:
        """True once a day, at or after the backup hour, until today's copy is made.
        Also true straight away when there has been no copy for more than a day."""
        now = now or dt.datetime.now(IST)
        s = self.status()
        last = s.get("last_day")
        if last == now.date().isoformat():
            return False
        if s.get("last_error_at"):                  # after a failure, wait an hour before trying again
            try:
                if (now - dt.datetime.fromisoformat(s["last_error_at"])).total_seconds() < 3600:
                    return False
            except ValueError:
                pass
        if not last or (now.date() - dt.date.fromisoformat(last)).days > 1:
            return True
        return now.hour >= HOUR

    def run_if_due(self, now: dt.datetime | None = None) -> dict | None:
        if not self.due(now):
            return None
        try:
            info = self.run(now)
            print(f"database backup made: {info['size']:,} bytes, {info['where']}", flush=True)
            return info
        except Exception:
            traceback.print_exc()
            return None

    # ---- keeping only so many -----------------------------------------------------
    def _prune_remote(self, today: dt.date) -> int:
        old = [k for k in self.store.blobs.keys(PREFIX)
               if (d := _day_of(k)) and (today - d).days >= KEEP_DAYS]
        for k in old:
            self.store.blobs.delete(k)
        return len(old)

    def _prune_local(self) -> None:
        copies = sorted(self.dir.glob("autoedits-*.db.gz"))
        for p in copies[:-LOCAL_KEEP]:
            p.unlink(missing_ok=True)

    def list(self) -> list[dict]:
        """Every copy that can be brought back, newest first."""
        found = {}
        if self.store.blobs.on:
            for k in self.store.blobs.keys(PREFIX):
                if (d := _day_of(k)):
                    found[d] = {"day": d.isoformat(), "where": "R2 bucket", "key": k}
        for p in self.dir.glob("autoedits-*.db.gz"):
            if (d := _day_of(p.name)) and d not in found:
                found[d] = {"day": d.isoformat(), "where": "this server", "path": str(p)}
        return [found[d] for d in sorted(found, reverse=True)]
