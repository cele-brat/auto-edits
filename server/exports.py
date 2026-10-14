"""Exports: finished pictures made on the server from a wedding's photos and their looks.

An export is a list of photos to do. Workers take photos from the list a few at a
time ("claim"), render each at the size asked for, store the result and report
back ("finish"). Because the list lives in the database, a worker can stop and
start at any point, several can share one export, and nothing is done twice.

Finished pictures are kept at  weddings/<wedding>/exports/<export>/<folder>/<name>.jpg
in the storage bucket, or under the same path on this disk when there is no bucket.
"""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path

from .looks import effective_look

SIZES = (2560, 4000, 0)                 # long edge in pixels; 0 keeps the size that was uploaded
WAITING, DONE, FAILED, WORKING, SKIPPED = 0, 1, 2, 3, 4      # skipped: the export was stopped first
STALE_SECONDS = 15 * 60                 # a claimed photo not finished in this time goes back on the list

SCHEMA = """
CREATE TABLE IF NOT EXISTS exports (
    id TEXT PRIMARY KEY, wedding_id TEXT NOT NULL REFERENCES weddings(id) ON DELETE CASCADE,
    size INTEGER NOT NULL, quality INTEGER NOT NULL, watermark TEXT NOT NULL DEFAULT '',
    scope TEXT NOT NULL DEFAULT '', stopped INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, finished REAL
);
CREATE TABLE IF NOT EXISTS export_items (
    export_id TEXT NOT NULL REFERENCES exports(id) ON DELETE CASCADE, photo_id TEXT NOT NULL,
    name TEXT NOT NULL, folder TEXT NOT NULL, status INTEGER NOT NULL DEFAULT 0, claimed REAL NOT NULL DEFAULT 0,
    bytes INTEGER NOT NULL DEFAULT 0, seconds REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (export_id, photo_id)
);
CREATE INDEX IF NOT EXISTS export_items_waiting ON export_items (status, claimed);
"""


def _refund(db, export_id: str, photos: int, why: str) -> None:
    """Give back the credits of photos an export did not make (failed, stopped or removed)."""
    if photos <= 0:
        return
    r = db.execute("SELECT w.owner, w.name, w.id FROM exports e JOIN weddings w ON w.id=e.wedding_id WHERE e.id=?",
                   (export_id,)).fetchone()
    if r is None or not r["owner"]:
        return
    if db.execute("SELECT 1 FROM ledger WHERE ref=? AND kind='export' LIMIT 1", (export_id,)).fetchone() is None:
        return                                             # made before credits existed: nothing was taken
    db.execute("INSERT INTO ledger (user_id, delta, kind, note, ref, photos, created, wedding) VALUES (?,?,?,?,?,?,?,?)",
               (r["owner"], photos, "refund", f"{why}: {r['name']}"[:200], export_id, photos, time.time(), r["id"]))


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .") or "untitled"


class Exports:
    def __init__(self, store):
        from .accounts import SCHEMA as ACCOUNTS
        self.store = store
        with store._db() as db:
            db.executescript(ACCOUNTS + SCHEMA)

    # ---- making and listing ------------------------------------------------
    def create(self, wedding_id: str, size: int, quality: int, watermark: str, folder_id: int | None,
               ratings: tuple = (), pay=None) -> dict:
        """ratings: export only photos rated so (keep / maybe / reject); empty means every photo.
        pay(export_id, photos) is called before anything is saved; if it raises, no export is made."""
        photos = self.store.photos(wedding_id, folder_id)
        scope_name = photos[0]["folder"] if photos and folder_id is not None else ""
        if ratings:
            photos = [p for p in photos if (p["pick"] or (p["cull"] or {}).get("r")) in ratings]
        if not photos:
            raise ValueError("there are no photos to export")
        export_id = uuid.uuid4().hex[:12]
        seen = set()
        rows = []
        for p in photos:
            stem = _safe(Path(p["file_name"]).stem)
            name, n = stem, 2
            while (p["folder"], name.lower()) in seen:           # two files of the same name in one folder
                name, n = f"{stem}-{n}", n + 1
            seen.add((p["folder"], name.lower()))
            rows.append((export_id, p["id"], name + ".jpg", _safe(p["folder"])))
        scope = " · ".join(x for x in (scope_name, " + ".join(ratings)) if x)
        if pay is not None:
            pay(export_id, len(rows))
        with self.store._db() as db:
            db.execute("INSERT INTO exports (id, wedding_id, size, quality, watermark, scope, created) VALUES (?,?,?,?,?,?,?)",
                       (export_id, wedding_id, size, quality, watermark.strip()[:80], scope, time.time()))
            db.executemany("INSERT INTO export_items (export_id, photo_id, name, folder) VALUES (?,?,?,?)", rows)
        return self.get(export_id)

    def get(self, export_id: str) -> dict | None:
        with self.store._db() as db:
            e = db.execute("SELECT * FROM exports WHERE id=?", (export_id,)).fetchone()
            if e is None:
                return None
            counts = dict(db.execute("SELECT status, COUNT(*) FROM export_items WHERE export_id=? GROUP BY status",
                                     (export_id,)).fetchall())
            agg = db.execute("SELECT COALESCE(SUM(bytes),0), COALESCE(AVG(NULLIF(seconds,0)),0) FROM export_items "
                             "WHERE export_id=? AND status=?", (export_id, DONE)).fetchone()
        total = sum(counts.values())
        done, failed, skipped = counts.get(DONE, 0), counts.get(FAILED, 0), counts.get(SKIPPED, 0)
        left = total - done - failed - skipped
        state = "stopped" if e["stopped"] and (left or skipped) else "done" if not left else "working" if (done or failed or counts.get(WORKING)) else "waiting"
        return {**dict(e), "total": total, "done": done, "failed": failed, "skipped": skipped, "left": left, "state": state,
                "bytes": agg[0], "seconds_each": round(agg[1] or 0, 2)}

    def for_wedding(self, wedding_id: str) -> list[dict]:
        with self.store._db() as db:
            ids = [r["id"] for r in db.execute("SELECT id FROM exports WHERE wedding_id=? ORDER BY created DESC", (wedding_id,))]
        return [self.get(i) for i in ids]

    def stop(self, export_id: str) -> bool:
        """Stop an export. Photos not started yet are taken off the list and their credits given back."""
        with self.store._db() as db:
            db.execute("BEGIN IMMEDIATE")
            n = db.execute("UPDATE exports SET stopped=1 WHERE id=? AND stopped=0", (export_id,)).rowcount
            if n:
                left = db.execute("UPDATE export_items SET status=? WHERE export_id=? AND status=?",
                                  (SKIPPED, export_id, WAITING)).rowcount
                _refund(db, export_id, left, "Export stopped")
            return bool(n)

    def failures(self, export_id: str) -> list[dict]:
        with self.store._db() as db:
            return [dict(r) for r in db.execute("SELECT name, folder, error FROM export_items WHERE export_id=? AND status=? LIMIT 50",
                                                (export_id, FAILED))]

    # ---- for workers ---------------------------------------------------------
    def claim(self, n: int = 4) -> list[dict]:
        """Take up to n waiting photos. Each comes with everything a worker needs to make it."""
        now = time.time()
        with self.store._db() as db:
            db.execute("BEGIN IMMEDIATE")                         # one worker at a time may choose
            # Take turns (Bharat, Oct 2026): the first waiting photo of every photographer, then the second of
            # every photographer, and so on, so one big export never holds up someone else's small one.
            rows = db.execute(
                "SELECT export_id, photo_id FROM (SELECT i.export_id, i.photo_id, e.created, i.rowid AS rid, "
                "ROW_NUMBER() OVER (PARTITION BY w.owner ORDER BY e.created, i.rowid) AS turn "
                "FROM export_items i JOIN exports e ON e.id=i.export_id JOIN weddings w ON w.id=e.wedding_id "
                "WHERE e.stopped=0 AND (i.status=? OR (i.status=? AND i.claimed<?))) "
                "ORDER BY turn, created, rid LIMIT ?",
                (WAITING, WORKING, now - STALE_SECONDS, n)).fetchall()
            for r in rows:
                db.execute("UPDATE export_items SET status=?, claimed=? WHERE export_id=? AND photo_id=?",
                           (WORKING, now, r["export_id"], r["photo_id"]))
        out = []
        for r in rows:
            with self.store._db() as db:
                e = db.execute("SELECT * FROM exports WHERE id=?", (r["export_id"],)).fetchone()
                i = db.execute("SELECT name, folder FROM export_items WHERE export_id=? AND photo_id=?",
                               (r["export_id"], r["photo_id"])).fetchone()
            p = self.store.photo(e["wedding_id"], r["photo_id"])
            if p is None:
                self.finish(r["export_id"], r["photo_id"], False, error="the photo was removed")
                continue
            iso = p["meta"].get("iso") if isinstance(p["meta"].get("iso"), (int, float)) else None
            out.append({"export": e["id"], "wedding": e["wedding_id"], "photo": r["photo_id"], "name": i["name"],
                        "folder": i["folder"], "size": e["size"], "quality": e["quality"], "watermark": e["watermark"],
                        "look": effective_look(p["settings"], p["folder_settings"], p.get("crop")), "iso": iso})
        return out

    def key(self, item: dict) -> str:
        return f"weddings/{item['wedding']}/exports/{item['export']}/{item['folder']}/{item['name']}"

    def save(self, item: dict, data: bytes) -> None:
        """Keep one finished picture: in the bucket when there is one, otherwise on this disk."""
        path = self.store.root / self.key(item)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if self.store.blobs.on:
            self.store.blobs.put(self.key(item), path)
            path.unlink(missing_ok=True)

    def finish(self, export_id: str, photo_id: str, ok: bool, size: int = 0, seconds: float = 0.0, error: str = "") -> None:
        with self.store._db() as db:
            n = db.execute("UPDATE export_items SET status=?, bytes=?, seconds=?, error=? WHERE export_id=? AND photo_id=? "
                           "AND status IN (?,?)", (DONE if ok else FAILED, size, seconds, error[:300], export_id, photo_id,
                                                   WAITING, WORKING)).rowcount
            if n and not ok:
                _refund(db, export_id, 1, "Photo could not be exported")
            left = db.execute("SELECT COUNT(*) FROM export_items WHERE export_id=? AND status IN (?,?)",
                              (export_id, WAITING, WORKING)).fetchone()[0]
            if not left:
                db.execute("UPDATE exports SET finished=? WHERE id=? AND finished IS NULL", (time.time(), export_id))

    # ---- handing the pictures over ---------------------------------------------
    def _read(self, wedding_id: str, export_id: str, folder: str, name: str):
        key = f"weddings/{wedding_id}/exports/{export_id}/{folder}/{name}"
        if self.store.blobs.on:
            yield from self.store.blobs.stream(key)
        else:
            with open(self.store.root / key, "rb") as f:
                while chunk := f.read(1 << 20):
                    yield chunk

    def zip_stream(self, export_id: str):
        """The finished pictures as one zip, produced piece by piece so its size does not matter."""
        e = self.get(export_id)
        with self.store._db() as db:
            items = db.execute("SELECT name, folder FROM export_items WHERE export_id=? AND status=? ORDER BY folder, name",
                               (export_id, DONE)).fetchall()

        class Pieces:
            def __init__(self): self.parts = []
            def write(self, b): self.parts.append(bytes(b)); return len(b)
            def flush(self): pass
            def take(self):
                parts, self.parts = self.parts, []
                return parts

        out = Pieces()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED, allowZip64=True) as z:        # JPEGs do not compress further
            for i in items:
                info = zipfile.ZipInfo(f"{i['folder']}/{i['name']}", time.localtime()[:6])
                with z.open(info, "w", force_zip64=True) as dest:
                    for chunk in self._read(e["wedding_id"], export_id, i["folder"], i["name"]):
                        dest.write(chunk)
                        yield from out.take()
                yield from out.take()
        yield from out.take()

    def delete(self, export_id: str) -> bool:
        e = self.get(export_id)
        if e is None:
            return False
        prefix = f"weddings/{e['wedding_id']}/exports/{export_id}/"
        with self.store._db() as db:
            db.execute("BEGIN IMMEDIATE")
            left = db.execute("SELECT COUNT(*) FROM export_items WHERE export_id=? AND status IN (?,?)",
                              (export_id, WAITING, WORKING)).fetchone()[0]
            _refund(db, export_id, left, "Export removed before it finished")
            db.execute("DELETE FROM exports WHERE id=?", (export_id,))
        self.store.blobs.delete_prefix(prefix)
        shutil.rmtree(self.store.root / prefix, ignore_errors=True)
        return True
