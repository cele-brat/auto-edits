"""Where the server keeps things: one SQLite file plus the preview images on disk.

    <data>/autoedits.db
    <data>/weddings/<wedding id>/previews/<photo id>.jpg

A photo is identified by the id Lightroom gives it (its uuid), so sending the
same photo twice replaces it instead of making a second copy.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS weddings (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, catalog TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
    UNIQUE (name, catalog)
);
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY, wedding_id TEXT NOT NULL REFERENCES weddings(id) ON DELETE CASCADE,
    name TEXT NOT NULL, settings TEXT NOT NULL DEFAULT '{}',
    UNIQUE (wedding_id, name)
);
CREATE TABLE IF NOT EXISTS photos (
    id TEXT PRIMARY KEY, wedding_id TEXT NOT NULL REFERENCES weddings(id) ON DELETE CASCADE,
    folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    lr_uuid TEXT NOT NULL, file_name TEXT NOT NULL, taken TEXT NOT NULL DEFAULT '',
    meta TEXT NOT NULL DEFAULT '{}', bytes INTEGER NOT NULL DEFAULT 0, received REAL NOT NULL,
    UNIQUE (wedding_id, lr_uuid)
);
CREATE INDEX IF NOT EXISTS photos_by_folder ON photos (folder_id, taken, file_name);
"""


class Store:
    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "autoedits.db"
        with self._db() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    # ---- weddings -------------------------------------------------------
    def wedding_dir(self, wedding_id: str) -> Path:
        return self.root / "weddings" / wedding_id

    def open_wedding(self, name: str, catalog: str = "") -> dict:
        """The wedding with this name (and catalogue); made if it does not exist yet."""
        name, catalog = name.strip(), catalog.strip()
        if not name:
            raise ValueError("a wedding needs a name")
        with self._db() as db:
            row = db.execute("SELECT * FROM weddings WHERE name=? AND catalog=?", (name, catalog)).fetchone()
            created = row is None
            if created:
                wid = uuid.uuid4().hex[:12]
                db.execute("INSERT INTO weddings (id, name, catalog, created) VALUES (?,?,?,?)",
                           (wid, name, catalog, time.time()))
                row = db.execute("SELECT * FROM weddings WHERE id=?", (wid,)).fetchone()
        (self.wedding_dir(row["id"]) / "previews").mkdir(parents=True, exist_ok=True)
        return {**dict(row), "is_new": created}

    def wedding(self, wedding_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM weddings WHERE id=?", (wedding_id,)).fetchone()
            if row is None:
                return None
            folders = db.execute(
                "SELECT f.id, f.name, COUNT(p.id) AS photos, COALESCE(SUM(p.bytes),0) AS bytes "
                "FROM folders f LEFT JOIN photos p ON p.folder_id=f.id "
                "WHERE f.wedding_id=? GROUP BY f.id ORDER BY f.name", (wedding_id,)).fetchall()
        folders = [dict(f) for f in folders]
        return {**dict(row), "folders": folders, "photos": sum(f["photos"] for f in folders),
                "bytes": sum(f["bytes"] for f in folders)}

    def weddings(self) -> list[dict]:
        with self._db() as db:
            ids = [r["id"] for r in db.execute("SELECT id FROM weddings ORDER BY created DESC")]
        return [self.wedding(i) for i in ids]

    def delete_wedding(self, wedding_id: str) -> bool:
        """Remove a wedding and all its images from the server. Nothing else is touched."""
        with self._db() as db:
            n = db.execute("DELETE FROM weddings WHERE id=?", (wedding_id,)).rowcount
        if n:
            shutil.rmtree(self.wedding_dir(wedding_id), ignore_errors=True)
        return bool(n)

    # ---- photos ---------------------------------------------------------
    def received_uuids(self, wedding_id: str) -> list[str]:
        with self._db() as db:
            return [r["lr_uuid"] for r in db.execute("SELECT lr_uuid FROM photos WHERE wedding_id=?", (wedding_id,))]

    def preview_path(self, wedding_id: str, photo_id: str) -> Path:
        return self.wedding_dir(wedding_id) / "previews" / f"{photo_id}.jpg"

    def add_photo(self, wedding_id: str, meta: dict, preview: bytes) -> dict:
        """Store one photo sent by the plug-in. Sending it again replaces the earlier one."""
        lr_uuid = str(meta.get("uuid") or "").strip()
        if not lr_uuid:
            raise ValueError("the photo has no Lightroom id")
        folder = str(meta.get("folder") or "").strip() or "Unsorted"
        file_name = str(meta.get("fileName") or "").strip() or lr_uuid
        with self._db() as db:
            if db.execute("SELECT 1 FROM weddings WHERE id=?", (wedding_id,)).fetchone() is None:
                raise KeyError(wedding_id)
            db.execute("INSERT OR IGNORE INTO folders (wedding_id, name) VALUES (?,?)", (wedding_id, folder))
            folder_id = db.execute("SELECT id FROM folders WHERE wedding_id=? AND name=?",
                                   (wedding_id, folder)).fetchone()["id"]
            row = db.execute("SELECT id FROM photos WHERE wedding_id=? AND lr_uuid=?", (wedding_id, lr_uuid)).fetchone()
            replaced = row is not None
            photo_id = row["id"] if replaced else uuid.uuid4().hex[:16]
            path = self.preview_path(wedding_id, photo_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            tmp.write_bytes(preview)
            tmp.replace(path)                       # the file is complete or absent, never half written
            values = (folder_id, file_name, str(meta.get("taken") or ""), json.dumps(meta, ensure_ascii=False),
                      len(preview), time.time())
            if replaced:
                db.execute("UPDATE photos SET folder_id=?, file_name=?, taken=?, meta=?, bytes=?, received=? "
                           "WHERE id=?", values + (photo_id,))
            else:
                db.execute("INSERT INTO photos (folder_id, file_name, taken, meta, bytes, received, id, wedding_id, "
                           "lr_uuid) VALUES (?,?,?,?,?,?,?,?,?)", values + (photo_id, wedding_id, lr_uuid))
        return {"id": photo_id, "folder": folder, "replaced": replaced}

    def photos(self, wedding_id: str, folder_id: int | None = None) -> list[dict]:
        q = ("SELECT p.id, p.lr_uuid, p.file_name, p.taken, p.meta, p.bytes, f.name AS folder, f.id AS folder_id "
             "FROM photos p JOIN folders f ON f.id=p.folder_id WHERE p.wedding_id=?")
        args: list = [wedding_id]
        if folder_id is not None:
            q += " AND p.folder_id=?"
            args.append(folder_id)
        with self._db() as db:
            rows = db.execute(q + " ORDER BY f.name, p.taken, p.file_name", args).fetchall()
        return [{**dict(r), "meta": json.loads(r["meta"])} for r in rows]

    def photo(self, wedding_id: str, photo_id: str) -> dict | None:
        with self._db() as db:
            r = db.execute("SELECT * FROM photos WHERE wedding_id=? AND id=?", (wedding_id, photo_id)).fetchone()
        return {**dict(r), "meta": json.loads(r["meta"])} if r else None

    def free_space(self) -> int:
        return shutil.disk_usage(self.root).free
