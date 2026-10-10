"""Where the server keeps things: one SQLite file plus the preview images on disk.

    <data>/autoedits.db
    <data>/weddings/<wedding id>/previews/<photo id>.jpg

A photo is identified by the id Lightroom gives it (its uuid), so sending the
same photo twice replaces it instead of making a second copy.
"""
from __future__ import annotations

import json
import os
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
CREATE TABLE IF NOT EXISTS presets (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL, xmp TEXT NOT NULL, added REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS photos_by_folder ON photos (folder_id, taken, file_name);
"""


class Store:
    def __init__(self, data_dir: str | Path, blobs=None):
        from .blobs import NoBlobs
        self.blobs = blobs or NoBlobs()          # where the photos are kept when not on this disk
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "autoedits.db"
        with self._db() as db:
            db.executescript(SCHEMA)
            columns = {r["name"] for r in db.execute("PRAGMA table_info(photos)")}
            if "settings" not in columns:            # a photo's own look; empty means "as the folder"
                db.execute("ALTER TABLE photos ADD COLUMN settings TEXT NOT NULL DEFAULT ''")
            if "stored" not in columns:              # 1 once the preview is safely in the storage bucket
                db.execute("ALTER TABLE photos ADD COLUMN stored INTEGER NOT NULL DEFAULT 0")
            if "cull" not in columns:                # the automatic rating (JSON) and the photographer's own
                db.execute("ALTER TABLE photos ADD COLUMN cull TEXT NOT NULL DEFAULT ''")
                db.execute("ALTER TABLE photos ADD COLUMN pick TEXT NOT NULL DEFAULT ''")
            if "applied" not in columns:             # stamp of the edit last applied to this photo in Lightroom
                db.execute("ALTER TABLE photos ADD COLUMN applied TEXT NOT NULL DEFAULT ''")

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
                "SELECT f.id, f.name, f.settings, COUNT(p.id) AS photos, COALESCE(SUM(p.bytes),0) AS bytes "
                "FROM folders f LEFT JOIN photos p ON p.folder_id=f.id "
                "WHERE f.wedding_id=? GROUP BY f.id ORDER BY f.name", (wedding_id,)).fetchall()
        folders = [{**dict(f), "settings": json.loads(f["settings"] or "{}")} for f in folders]
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
            self.blobs.delete_prefix(f"weddings/{wedding_id}/")
        return bool(n)

    # ---- photos ---------------------------------------------------------
    def received_uuids(self, wedding_id: str) -> list[str]:
        with self._db() as db:
            return [r["lr_uuid"] for r in db.execute("SELECT lr_uuid FROM photos WHERE wedding_id=?", (wedding_id,))]

    def _local(self, wedding_id: str, photo_id: str) -> Path:
        return self.wedding_dir(wedding_id) / "previews" / f"{photo_id}.jpg"

    @staticmethod
    def _key(wedding_id: str, photo_id: str) -> str:
        return f"weddings/{wedding_id}/previews/{photo_id}.jpg"

    def preview_path(self, wedding_id: str, photo_id: str) -> Path:
        """The preview as a file on this disk, fetched from storage first if the working copy is gone."""
        path = self._local(wedding_id, photo_id)
        if not path.exists() and self.blobs.on:
            path.parent.mkdir(parents=True, exist_ok=True)
            if self.blobs.fetch(self._key(wedding_id, photo_id), path):
                with self._db() as db:
                    r = db.execute("SELECT received FROM photos WHERE id=?", (photo_id,)).fetchone()
                if r:                              # same date as before, so kept masks still belong to it
                    os.utime(path, (r["received"], r["received"]))
        return path

    def store_pending(self, limit: int = 500) -> int:
        """Copy previews that are only on this disk into storage. Returns how many were copied."""
        if not self.blobs.on:
            return 0
        with self._db() as db:
            rows = db.execute("SELECT id, wedding_id FROM photos WHERE stored=0 LIMIT ?", (limit,)).fetchall()
        done = 0
        for r in rows:
            path = self._local(r["wedding_id"], r["id"])
            if not path.exists():
                continue
            self.blobs.put(self._key(r["wedding_id"], r["id"]), path)
            with self._db() as db:
                db.execute("UPDATE photos SET stored=1 WHERE id=?", (r["id"],))
            done += 1
        return done

    def trim_working_copies(self, keep_bytes: int) -> int:
        """Free this disk: remove the least recently used working copies of previews
        that are safely in storage, until what is left fits in keep_bytes."""
        if not self.blobs.on:
            return 0
        with self._db() as db:
            safe = {r["id"] for r in db.execute("SELECT id FROM photos WHERE stored=1")}
        files = []
        for f in (self.root / "weddings").glob("*/previews/*.jpg"):
            st = f.stat()
            files.append((st.st_atime, st.st_size, f))
        total = sum(size for _, size, _ in files)
        removed = 0
        for _, size, f in sorted(files, key=lambda x: x[0]):
            if total <= keep_bytes:
                break
            if f.stem in safe:
                f.unlink(missing_ok=True)
                f.with_suffix(".masks.npz").unlink(missing_ok=True)
                total -= size
                removed += 1
        return removed

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
            path = self._local(wedding_id, photo_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".part")
            tmp.write_bytes(preview)
            tmp.replace(path)                       # the file is complete or absent, never half written
            now = float(int(time.time()))
            os.utime(path, (now, now))
            path.with_suffix(".masks.npz").unlink(missing_ok=True)
            values = (folder_id, file_name, str(meta.get("taken") or ""), json.dumps(meta, ensure_ascii=False),
                      len(preview), now)
            if replaced:
                db.execute("UPDATE photos SET folder_id=?, file_name=?, taken=?, meta=?, bytes=?, received=?, stored=0 "
                           "WHERE id=?", values + (photo_id,))
            else:
                db.execute("INSERT INTO photos (folder_id, file_name, taken, meta, bytes, received, id, wedding_id, "
                           "lr_uuid) VALUES (?,?,?,?,?,?,?,?,?)", values + (photo_id, wedding_id, lr_uuid))
        if self.blobs.on:                           # into storage straight away; this disk keeps a working copy
            try:
                self.blobs.put(self._key(wedding_id, photo_id), path)
                with self._db() as db:
                    db.execute("UPDATE photos SET stored=1 WHERE id=?", (photo_id,))
            except Exception:
                pass                                # stays on this disk; the regular tidy-up tries again
        return {"id": photo_id, "folder": folder, "replaced": replaced}

    def photos(self, wedding_id: str, folder_id: int | None = None) -> list[dict]:
        q = ("SELECT p.id, p.lr_uuid, p.file_name, p.taken, p.meta, p.bytes, p.settings, p.received, p.applied, p.cull, p.pick, "
             "f.name AS folder, f.id AS folder_id "
             "FROM photos p JOIN folders f ON f.id=p.folder_id WHERE p.wedding_id=?")
        args: list = [wedding_id]
        if folder_id is not None:
            q += " AND p.folder_id=?"
            args.append(folder_id)
        with self._db() as db:
            rows = db.execute(q + " ORDER BY f.name, p.taken, p.file_name", args).fetchall()
        return [{**dict(r), "meta": json.loads(r["meta"]), "cull": json.loads(r["cull"]) if r["cull"] else None,
                 "settings": json.loads(r["settings"]) if r["settings"] else None} for r in rows]

    def photo(self, wedding_id: str, photo_id: str) -> dict | None:
        with self._db() as db:
            r = db.execute("SELECT p.*, f.settings AS folder_settings FROM photos p JOIN folders f ON f.id=p.folder_id "
                           "WHERE p.wedding_id=? AND p.id=?", (wedding_id, photo_id)).fetchone()
        if r is None:
            return None
        return {**dict(r), "meta": json.loads(r["meta"]),
                "settings": json.loads(r["settings"]) if r["settings"] else None,
                "folder_settings": json.loads(r["folder_settings"] or "{}")}

    # ---- looks ----------------------------------------------------------
    def set_folder_settings(self, wedding_id: str, folder_id: int, settings: dict) -> bool:
        with self._db() as db:
            return bool(db.execute("UPDATE folders SET settings=? WHERE wedding_id=? AND id=?",
                                   (json.dumps(settings), wedding_id, folder_id)).rowcount)

    def set_photo_settings(self, wedding_id: str, photo_id: str, settings: dict | None) -> bool:
        """A photo's own look. None puts it back on its folder's look."""
        value = json.dumps(settings) if settings is not None else ""
        with self._db() as db:
            return bool(db.execute("UPDATE photos SET settings=? WHERE wedding_id=? AND id=?",
                                   (value, wedding_id, photo_id)).rowcount)

    def set_pick(self, wedding_id: str, photo_id: str, pick: str) -> bool:
        """The photographer's own rating: keep, maybe, reject, or '' to go back to the automatic one."""
        with self._db() as db:
            return bool(db.execute("UPDATE photos SET pick=? WHERE wedding_id=? AND id=?", (pick, wedding_id, photo_id)).rowcount)

    # ---- presets uploaded from Lightroom --------------------------------------
    def save_preset(self, preset: dict, xmp: str) -> bool:
        """Keep an uploaded preset, with its original file for sending back to Lightroom. True if it replaced one."""
        with self._db() as db:
            had = db.execute("SELECT 1 FROM presets WHERE id=?", (preset["id"],)).fetchone() is not None
            db.execute("INSERT OR REPLACE INTO presets (id, name, data, xmp, added) VALUES (?,?,?,?,?)",
                       (preset["id"], preset["display_name"], json.dumps(preset, ensure_ascii=False), xmp, time.time()))
        return had

    def uploaded_presets(self) -> list[dict]:
        with self._db() as db:
            return [json.loads(r["data"]) for r in db.execute("SELECT data FROM presets ORDER BY name")]

    def preset_file(self, preset_id: str) -> str | None:
        with self._db() as db:
            r = db.execute("SELECT xmp FROM presets WHERE id=?", (preset_id,)).fetchone()
        return r["xmp"] if r else None

    def delete_preset(self, preset_id: str) -> bool:
        with self._db() as db:
            return bool(db.execute("DELETE FROM presets WHERE id=?", (preset_id,)).rowcount)

    def mark_applied(self, wedding_id: str, stamps: dict) -> int:
        """Remember which edit Lightroom has applied to each photo (Lightroom id -> stamp)."""
        with self._db() as db:
            return sum(db.execute("UPDATE photos SET applied=? WHERE wedding_id=? AND lr_uuid=?",
                                  (str(stamp), wedding_id, str(uuid))).rowcount for uuid, stamp in stamps.items())

    def cache_dir(self, wedding_id: str) -> Path:
        d = self.wedding_dir(wedding_id) / "cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def free_space(self) -> int:
        return shutil.disk_usage(self.root).free
