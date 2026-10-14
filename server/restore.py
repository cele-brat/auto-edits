"""Bring back a database copy made by server/backups.py.

On the VPS, from /opt/auto-edits/deploy:
    docker compose exec server python -m server.restore --list
    docker compose exec server python -m server.restore --day 2026-10-14

--day only unpacks that copy next to the live one as /data/restore-<day>.db and checks it.
It never touches the live database. To switch over (a few minutes of downtime):
    docker compose stop server worker
    docker compose run --rm --no-deps server sh -c \\
      "cp /data/autoedits.db /data/autoedits.before-restore.db && rm -f /data/autoedits.db-wal /data/autoedits.db-shm \\
       && mv /data/restore-<day>.db /data/autoedits.db"
    docker compose up -d
"""
from __future__ import annotations

import argparse
import gzip
import os
import shutil
import sqlite3
from pathlib import Path

from . import blobs as blob_store
from .backups import Backups
from .store import Store


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="show the copies that can be brought back")
    ap.add_argument("--day", help="unpack the copy from this day (YYYY-MM-DD)")
    a = ap.parse_args(argv)
    store = Store(os.environ.get("AUTOEDITS_DATA", "data/server"), blob_store.from_environment())
    backups = Backups(store)
    copies = backups.list()
    if a.list or not a.day:
        for c in copies:
            print(f"{c['day']}  {c['where']}")
        if not copies:
            print("No copies yet.")
        return 0
    pick = next((c for c in copies if c["day"] == a.day), None)
    if pick is None:
        print(f"No copy from {a.day}. Use --list to see them.")
        return 1
    packed = Path(store.root) / f"restore-{a.day}.db.gz"
    out = packed.with_suffix("")
    if "path" in pick:
        shutil.copyfile(pick["path"], packed)
    elif not store.blobs.fetch(pick["key"], packed):
        print("That copy is missing from the bucket.")
        return 1
    with gzip.open(packed, "rb") as g, open(out, "wb") as f:
        shutil.copyfileobj(g, f, 1 << 20)
    packed.unlink()
    db = sqlite3.connect(out)
    try:
        ok = db.execute("PRAGMA integrity_check").fetchone()[0]
        users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        weddings = db.execute("SELECT COUNT(*) FROM weddings").fetchone()[0]
    finally:
        db.close()
    print(f"Unpacked to {out}  (check: {ok}; {users} accounts, {weddings} weddings)")
    print("The live database is unchanged. See the top of server/restore.py to switch over.")
    return 0 if ok == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
