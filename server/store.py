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
    owner TEXT NOT NULL DEFAULT '',
    UNIQUE (owner, name, catalog)
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
    id TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL, xmp TEXT NOT NULL, added REAL NOT NULL,
    owner TEXT NOT NULL DEFAULT ''
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
        self._give_weddings_owners()
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
            if "crop" not in columns:                # the photo's own crop (JSON), kept apart from its look
                db.execute("ALTER TABLE photos ADD COLUMN crop TEXT NOT NULL DEFAULT ''")
            if "paid_stamp" not in columns:          # stamp of the edit last paid for when handed to Lightroom
                db.execute("ALTER TABLE photos ADD COLUMN paid_stamp TEXT NOT NULL DEFAULT ''")
            if "autos" not in columns:               # automatic values worked out after upload (JSON); '' = still to do.
                # Photos from before this column are not queued (DEFAULT '{}'): theirs are worked out when first needed.
                db.execute("ALTER TABLE photos ADD COLUMN autos TEXT NOT NULL DEFAULT '{}'")
            db.execute("CREATE INDEX IF NOT EXISTS photos_autos_due ON photos (id) WHERE autos=''")
            if "faces" not in columns:               # face boxes and per-face scores (JSON), for Compare view and retouching
                db.execute("ALTER TABLE photos ADD COLUMN faces TEXT NOT NULL DEFAULT ''")
            if "client_pick" not in columns:         # 1 once a client chose it in a shared gallery (server/gallery.py)
                db.execute("ALTER TABLE photos ADD COLUMN client_pick INTEGER NOT NULL DEFAULT 0")
            if "owner" not in {r["name"] for r in db.execute("PRAGMA table_info(presets)")}:
                db.execute("ALTER TABLE presets ADD COLUMN owner TEXT NOT NULL DEFAULT ''")
            db.execute("CREATE INDEX IF NOT EXISTS weddings_by_owner ON weddings (owner, created)")

    def _give_weddings_owners(self):
        """Weddings made before there were accounts had one name per catalogue for everyone.
        Rebuild that table once so each photographer has their own names (rows kept as they are)."""
        if not self.db_path.exists():
            return
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            cols = {r["name"] for r in db.execute("PRAGMA table_info(weddings)")}
            if not cols or "owner" in cols:
                return
            db.execute("PRAGMA foreign_keys = OFF")        # so the rebuild cannot remove folders and photos
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE weddings_new (id TEXT PRIMARY KEY, name TEXT NOT NULL, catalog TEXT NOT NULL DEFAULT '', "
                       "created REAL NOT NULL, owner TEXT NOT NULL DEFAULT '', UNIQUE (owner, name, catalog))")
            db.execute("INSERT INTO weddings_new (id, name, catalog, created) SELECT id, name, catalog, created FROM weddings")
            db.execute("DROP TABLE weddings")
            db.execute("ALTER TABLE weddings_new RENAME TO weddings")
            db.execute("COMMIT")
        finally:
            db.close()

    def adopt_orphans(self, user_id: str) -> None:
        """Everything made before there were accounts belongs to the studio (admin) account."""
        with self._db() as db:
            db.execute("UPDATE weddings SET owner=? WHERE owner=''", (user_id,))
            db.execute("UPDATE presets SET owner=? WHERE owner=''", (user_id,))

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

    def open_wedding(self, name: str, catalog: str = "", owner: str = "") -> dict:
        """This photographer's wedding with this name (and catalogue); made if it does not exist yet."""
        name, catalog = name.strip(), catalog.strip()
        if not name:
            raise ValueError("a wedding needs a name")
        with self._db() as db:
            row = db.execute("SELECT * FROM weddings WHERE owner=? AND name=? AND catalog=?", (owner, name, catalog)).fetchone()
            created = row is None
            if created:
                wid = uuid.uuid4().hex[:12]
                db.execute("INSERT INTO weddings (id, name, catalog, created, owner) VALUES (?,?,?,?,?)",
                           (wid, name, catalog, time.time(), owner))
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

    def weddings(self, owner: str | None = None) -> list[dict]:
        """One photographer's weddings, newest first (None: everyone's)."""
        with self._db() as db:
            if owner is None:
                ids = [r["id"] for r in db.execute("SELECT id FROM weddings ORDER BY created DESC")]
            else:
                ids = [r["id"] for r in db.execute("SELECT id FROM weddings WHERE owner=? ORDER BY created DESC", (owner,))]
        return [self.wedding(i) for i in ids]

    # ---- how much a photographer holds (limits.py) -----------------------------------
    def photo_count(self, wedding_id: str) -> int:
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM photos WHERE wedding_id=?", (wedding_id,)).fetchone()[0]

    def has_photo(self, wedding_id: str, lr_uuid: str) -> bool:
        with self._db() as db:
            return db.execute("SELECT 1 FROM photos WHERE wedding_id=? AND lr_uuid=?", (wedding_id, lr_uuid)).fetchone() is not None

    def used_bytes(self, owner: str) -> int:
        """Uploaded previews plus finished export pictures, across all of one photographer's weddings."""
        with self._db() as db:
            up = db.execute("SELECT COALESCE(SUM(p.bytes),0) FROM photos p JOIN weddings w ON w.id=p.wedding_id WHERE w.owner=?",
                            (owner,)).fetchone()[0]
            made = 0
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='export_items'").fetchone():
                made = db.execute("SELECT COALESCE(SUM(i.bytes),0) FROM export_items i JOIN exports e ON e.id=i.export_id "
                                  "JOIN weddings w ON w.id=e.wedding_id WHERE w.owner=?", (owner,)).fetchone()[0]
        return int(up) + int(made)

    def owner_of(self, wedding_id: str) -> str | None:
        with self._db() as db:
            r = db.execute("SELECT owner FROM weddings WHERE id=?", (wedding_id,)).fetchone()
        return r["owner"] if r else None

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

    def health(self, wedding_id: str) -> dict:
        """For the admin page: photos per event folder, and how many have no preview anywhere
        (not on this disk and never copied to storage), so they cannot be shown."""
        with self._db() as db:
            rows = db.execute("SELECT p.id, p.stored, f.name AS folder FROM photos p JOIN folders f ON f.id=p.folder_id "
                              "WHERE p.wedding_id=?", (wedding_id,)).fetchall()
        folders: dict[str, dict] = {}
        missing = 0
        for r in rows:
            f = folders.setdefault(r["folder"], {"name": r["folder"], "photos": 0, "missing": 0})
            f["photos"] += 1
            if not r["stored"] and not self._local(wedding_id, r["id"]).exists():
                f["missing"] += 1
                missing += 1
        return {"folders": sorted(folders.values(), key=lambda f: f["name"].lower()), "missing": missing}

    def preview_path(self, wedding_id: str, photo_id: str, fetch: bool = True) -> Path:
        """The preview as a file on this disk, fetched from storage first if the working copy is gone
        (fetch=False: only where it would be, for work that may not need the picture itself)."""
        path = self._local(wedding_id, photo_id)
        if fetch and not path.exists() and self.blobs.on:
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
        that are safely in storage, until what is left fits in keep_bytes. Grid pictures not
        looked at for CACHE_DAYS are removed too (they are made again when needed)."""
        self.prune_cache()
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
                db.execute("UPDATE photos SET folder_id=?, file_name=?, taken=?, meta=?, bytes=?, received=?, stored=0, autos='' "
                           "WHERE id=?", values + (photo_id,))
            else:
                db.execute("INSERT INTO photos (folder_id, file_name, taken, meta, bytes, received, id, wedding_id, "
                           "lr_uuid, autos) VALUES (?,?,?,?,?,?,?,?,?,'')", values + (photo_id, wedding_id, lr_uuid))
        if self.blobs.on:                           # into storage straight away; this disk keeps a working copy
            try:
                self.blobs.put(self._key(wedding_id, photo_id), path)
                with self._db() as db:
                    db.execute("UPDATE photos SET stored=1 WHERE id=?", (photo_id,))
            except Exception:
                pass                                # stays on this disk; the regular tidy-up tries again
        return {"id": photo_id, "folder": folder, "replaced": replaced}

    def photos(self, wedding_id: str, folder_id: int | None = None) -> list[dict]:
        q = ("SELECT p.id, p.lr_uuid, p.file_name, p.taken, p.meta, p.bytes, p.settings, p.received, p.applied, p.paid_stamp, "
             "p.cull, p.pick, p.crop, p.client_pick, "
             "f.name AS folder, f.id AS folder_id "
             "FROM photos p JOIN folders f ON f.id=p.folder_id WHERE p.wedding_id=?")
        args: list = [wedding_id]
        if folder_id is not None:
            q += " AND p.folder_id=?"
            args.append(folder_id)
        with self._db() as db:
            rows = db.execute(q + " ORDER BY f.name, p.taken, p.file_name", args).fetchall()
        return [{**dict(r), "meta": json.loads(r["meta"]), "cull": json.loads(r["cull"]) if r["cull"] else None,
                 "settings": json.loads(r["settings"]) if r["settings"] else None,
                 "crop": json.loads(r["crop"]) if r["crop"] else None} for r in rows]

    def photo(self, wedding_id: str, photo_id: str) -> dict | None:
        with self._db() as db:
            r = db.execute("SELECT p.*, f.settings AS folder_settings FROM photos p JOIN folders f ON f.id=p.folder_id "
                           "WHERE p.wedding_id=? AND p.id=?", (wedding_id, photo_id)).fetchone()
        if r is None:
            return None
        return {**dict(r), "meta": json.loads(r["meta"]),
                "settings": json.loads(r["settings"]) if r["settings"] else None,
                "crop": json.loads(r["crop"]) if r["crop"] else None,
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

    def set_crop(self, wedding_id: str, photo_id: str, crop: dict | None) -> bool:
        """A photo's own crop, or None for the whole picture. Only a setting: the photo is never changed."""
        with self._db() as db:
            return bool(db.execute("UPDATE photos SET crop=? WHERE wedding_id=? AND id=?",
                                   (json.dumps(crop) if crop else "", wedding_id, photo_id)).rowcount)

    # ---- faces, kept per photo for Compare view and retouching ---------------------------------
    def get_faces(self, photo_id: str, received: float, part: str = "faces"):
        """One kept piece ("faces": boxes and scores; "mesh": landmarks) of this copy of the photo, or None."""
        with self._db() as db:
            r = db.execute("SELECT faces FROM photos WHERE id=?", (photo_id,)).fetchone()
        if not r or not r["faces"]:
            return None
        try:
            kept = json.loads(r["faces"])
        except ValueError:
            return None
        if not isinstance(kept, dict) or abs(float(kept.get("received") or 0) - received) >= 1:
            return None
        return kept.get(part)

    def put_faces(self, photo_id: str, value, received: float, part: str = "faces") -> None:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            r = db.execute("SELECT faces FROM photos WHERE id=?", (photo_id,)).fetchone()
            if r is None:
                return
            try:
                kept = json.loads(r["faces"]) if r["faces"] else {}
            except ValueError:
                kept = {}
            if not isinstance(kept, dict) or abs(float(kept.get("received") or 0) - received) >= 1:
                kept = {"received": received}
            kept[part] = value
            db.execute("UPDATE photos SET faces=? WHERE id=?", (json.dumps(kept), photo_id))

    def set_client_pick(self, photo_id: str, on: bool) -> bool:
        with self._db() as db:
            return bool(db.execute("UPDATE photos SET client_pick=? WHERE id=?", (1 if on else 0, photo_id)).rowcount)

    def set_pick(self, wedding_id: str, photo_id: str, pick: str) -> bool:
        """The photographer's own rating: keep, maybe, reject, or '' to go back to the automatic one."""
        with self._db() as db:
            return bool(db.execute("UPDATE photos SET pick=? WHERE wedding_id=? AND id=?", (pick, wedding_id, photo_id)).rowcount)

    # ---- presets uploaded from Lightroom --------------------------------------
    def save_preset(self, preset: dict, xmp: str, owner: str = "") -> bool:
        """Keep an uploaded preset, with its original file for sending back to Lightroom. True if it replaced one."""
        with self._db() as db:
            had = db.execute("SELECT 1 FROM presets WHERE id=?", (preset["id"],)).fetchone() is not None
            db.execute("INSERT OR REPLACE INTO presets (id, name, data, xmp, added, owner) VALUES (?,?,?,?,?,?)",
                       (preset["id"], preset["display_name"], json.dumps(preset, ensure_ascii=False), xmp, time.time(), owner))
        return had

    def uploaded_presets(self) -> list[dict]:
        """Every uploaded preset, each marked with who uploaded it."""
        with self._db() as db:
            return [{**json.loads(r["data"]), "owner": r["owner"]}
                    for r in db.execute("SELECT data, owner FROM presets ORDER BY name")]

    def preset_versions(self) -> dict[str, float]:
        """Uploaded preset id -> when it was last saved, to notice changes without reading every preset."""
        with self._db() as db:
            return {r["id"]: r["added"] for r in db.execute("SELECT id, added FROM presets")}

    def preset_file(self, preset_id: str) -> str | None:
        with self._db() as db:
            r = db.execute("SELECT xmp FROM presets WHERE id=?", (preset_id,)).fetchone()
        return r["xmp"] if r else None

    def delete_preset(self, preset_id: str) -> bool:
        with self._db() as db:
            return bool(db.execute("DELETE FROM presets WHERE id=?", (preset_id,)).rowcount)

    def mark_applied(self, wedding_id: str, stamps: dict) -> int:
        """Remember which edit Lightroom has applied to each photo (Lightroom id -> stamp).
        Only an edit that was handed out (and so paid for) can be marked."""
        with self._db() as db:
            return sum(db.execute("UPDATE photos SET applied=? WHERE wedding_id=? AND lr_uuid=? AND paid_stamp=?",
                                  (str(stamp), wedding_id, str(uuid), str(stamp))).rowcount for uuid, stamp in stamps.items())

    def zip_previews(self, wedding_id: str, rows: list[dict]):
        """The uploaded previews as one zip, made piece by piece (from this disk or from storage)."""
        import re
        import zipfile

        class Pieces:
            def __init__(self): self.parts = []
            def write(self, b): self.parts.append(bytes(b)); return len(b)
            def flush(self): pass
            def take(self):
                parts, self.parts = self.parts, []
                return parts

        def safe(x: str) -> str:
            return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", x).strip(" .") or "untitled"

        out, seen = Pieces(), set()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
            for r in rows:
                stem, n = safe(Path(r["file_name"]).stem), 2
                name = f"{safe(r['folder'])}/{stem}.jpg"
                while name.lower() in seen:
                    name, n = f"{safe(r['folder'])}/{stem}-{n}.jpg", n + 1
                seen.add(name.lower())
                local = self._local(wedding_id, r["id"])
                with z.open(zipfile.ZipInfo(name, time.localtime()[:6]), "w", force_zip64=True) as dest:
                    if local.exists():
                        with open(local, "rb") as f:
                            while chunk := f.read(1 << 20):
                                dest.write(chunk)
                                yield from out.take()
                    else:
                        for chunk in self.blobs.stream(self._key(wedding_id, r["id"])):
                            dest.write(chunk)
                            yield from out.take()
                yield from out.take()
        yield from out.take()

    def prune_cache(self, days: float = 30) -> int:
        """Remove grid pictures not used (read or made) for this many days."""
        old = time.time() - days * 86400
        removed = 0
        for f in (self.root / "weddings").glob("*/cache/*"):
            try:
                st = f.stat()
                if max(st.st_atime, st.st_mtime) < old:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
        return removed

    # ---- automatic values, worked out once per photo and kept ---------------------------
    def autos_due(self, limit: int = 10) -> list[dict]:
        """Photos received since their automatic values were last worked out."""
        with self._db() as db:
            rows = db.execute("SELECT id, wedding_id FROM photos WHERE autos='' LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_autos(self, photo_id: str) -> dict:
        with self._db() as db:
            r = db.execute("SELECT autos FROM photos WHERE id=?", (photo_id,)).fetchone()
        try:
            return json.loads(r["autos"]) if r and r["autos"] else {}
        except ValueError:
            return {}

    def put_autos(self, photo_id: str, values: dict, received: float | None = None) -> None:
        """values: {piece: result} merged into what is kept for the preview received at that time
        (an empty dict just marks the photo as done)."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            r = db.execute("SELECT autos FROM photos WHERE id=?", (photo_id,)).fetchone()
            if r is None:
                return
            try:
                kept = json.loads(r["autos"]) if r["autos"] else {}
            except ValueError:
                kept = {}
            if received is not None and abs(float(kept.get("_received") or 0) - received) >= 1:
                kept = {"_received": received}          # worked out on an earlier copy of the photo: start again
            kept.update(values)
            db.execute("UPDATE photos SET autos=? WHERE id=?",
                       (json.dumps(kept, default=lambda o: o.item() if hasattr(o, "item") else str(o)), photo_id))

    def cache_dir(self, wedding_id: str) -> Path:
        d = self.wedding_dir(wedding_id) / "cache"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def free_space(self) -> int:
        return shutil.disk_usage(self.root).free
