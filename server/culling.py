"""Culling: every photo of a folder is rated keep, maybe or reject, with the reasons.

A cull is a job in the database, done by the export worker. The measurements of a photo
(sharpness, faces, eyes, exposure) are kept beside its preview, so running the cull again
with another strictness takes seconds. A rating the photographer sets by hand ("pick")
always wins over the automatic one and is never touched by a new cull.

The photos of a job are fixed when it is started (and paid for): photos added to the folder
later are not culled by it. A worker measures CHUNK photos per turn and then lets the job go
back on the list, so a big cull never keeps exports waiting for long. A job that fails gives
its credits back.
"""
from __future__ import annotations

import json
import pickle
import time

STRICTNESS = ("relaxed", "normal", "strict")
RATINGS = ("keep", "maybe", "reject")
CHUNK = 200                     # photos measured per turn before exports get theirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS cull_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, wedding_id TEXT NOT NULL REFERENCES weddings(id) ON DELETE CASCADE,
    folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE, strictness TEXT NOT NULL,
    best_only INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'waiting', done INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', created REAL NOT NULL, claimed REAL NOT NULL DEFAULT 0
);
"""
STALE_SECONDS = 10 * 60


class Culls:
    def __init__(self, store):
        self.store = store
        with store._db() as db:
            db.executescript(SCHEMA)
            cols = {r["name"] for r in db.execute("PRAGMA table_info(cull_jobs)")}
            if "photo_ids" not in cols:          # the photos paid for (JSON list); '' = the whole folder (older jobs)
                db.execute("ALTER TABLE cull_jobs ADD COLUMN photo_ids TEXT NOT NULL DEFAULT ''")
            if "credits" not in cols:            # what this job cost, given back if it fails
                db.execute("ALTER TABLE cull_jobs ADD COLUMN credits INTEGER NOT NULL DEFAULT 0")
                db.execute("ALTER TABLE cull_jobs ADD COLUMN ref TEXT NOT NULL DEFAULT ''")
            db.execute("CREATE INDEX IF NOT EXISTS cull_jobs_by_state ON cull_jobs (state, id)")

    def photo_ids(self, folder_ids: list[int]) -> dict[int, list[str]]:
        """The photos in these folders now, to be fixed for a cull before it is paid for."""
        with self.store._db() as db:
            return {f: [r["id"] for r in db.execute("SELECT id FROM photos WHERE folder_id=? ORDER BY taken, file_name", (f,))]
                    for f in folder_ids}

    def start(self, wedding_id: str, folder_ids: list[int], strictness: str, best_only: bool,
              photos: dict[int, list[str]] | None = None, credits: dict[int, int] | None = None, ref: str = "") -> list[dict]:
        """photos: the photo ids per folder that were paid for (photo_ids() above); credits: the cost per folder."""
        if strictness not in STRICTNESS:
            raise ValueError("strictness must be relaxed, normal or strict")
        photos = photos if photos is not None else self.photo_ids(folder_ids)
        now = time.time()
        with self.store._db() as db:
            for f in folder_ids:
                ids = photos.get(f) or []
                if not ids:
                    continue
                db.execute("DELETE FROM cull_jobs WHERE folder_id=?", (f,))          # one cull per folder: the newest
                db.execute("INSERT INTO cull_jobs (wedding_id, folder_id, strictness, best_only, total, created, photo_ids, "
                           "credits, ref) VALUES (?,?,?,?,?,?,?,?,?)",
                           (wedding_id, f, strictness, int(best_only), len(ids), now, json.dumps(ids),
                            int((credits or {}).get(f, 0)), ref))
        return self.for_wedding(wedding_id)

    def for_wedding(self, wedding_id: str) -> list[dict]:
        with self.store._db() as db:
            return [dict(r) for r in db.execute(
                "SELECT j.id, j.folder_id, f.name AS folder, j.strictness, j.best_only, j.state, j.done, j.total, j.error "
                "FROM cull_jobs j JOIN folders f ON f.id=j.folder_id WHERE j.wedding_id=? ORDER BY f.name", (wedding_id,))]

    def claim(self) -> dict | None:
        now = time.time()
        with self.store._db() as db:
            db.execute("BEGIN IMMEDIATE")
            # Take turns: the photographer with the fewest culls running goes first, then the oldest job.
            r = db.execute(
                "SELECT j.* FROM cull_jobs j JOIN weddings w ON w.id=j.wedding_id "
                "WHERE j.state='waiting' OR (j.state='working' AND j.claimed<?) "
                "ORDER BY (SELECT COUNT(*) FROM cull_jobs k JOIN weddings v ON v.id=k.wedding_id "
                "          WHERE v.owner=w.owner AND k.state='working' AND k.claimed>=?), j.id LIMIT 1",
                (now - STALE_SECONDS, now - STALE_SECONDS)).fetchone()
            if r is None:
                return None
            db.execute("UPDATE cull_jobs SET state='working', claimed=? WHERE id=?", (now, r["id"]))
            return dict(r)

    def _progress(self, job_id: int, done: int) -> bool:
        """Note progress. False when the job was replaced or removed meanwhile."""
        with self.store._db() as db:
            return bool(db.execute("UPDATE cull_jobs SET done=?, claimed=? WHERE id=? AND state='working'",
                                   (done, time.time(), job_id)).rowcount)

    def _photos(self, job: dict) -> list[dict]:
        """The photos this job was paid for that are still there (all of the folder for older jobs)."""
        photos = self.store.photos(job["wedding_id"], job["folder_id"])
        if job.get("photo_ids"):
            paid = set(json.loads(job["photo_ids"]))
            photos = [p for p in photos if p["id"] in paid]
        return photos

    def run(self, job: dict, renderer, chunk: int | None = None) -> int:
        """One turn of a cull job: measure up to chunk photos not measured yet, then put the job back
        on the list; once every photo is measured, rate them all. Returns how many photos were rated
        (0 while it is still measuring)."""
        from engine.cull import cull, measure_photo
        store = self.store
        chunk = CHUNK if chunk is None else chunk          # read when run, so a test can make the turns smaller
        try:
            photos = self._photos(job)
            items, measured_now = [], 0
            for n, p in enumerate(photos, 1):
                path = store.preview_path(job["wedding_id"], p["id"])
                kept = path.with_suffix(".cull.pkl")
                m = None
                if kept.exists():
                    try:
                        stamp, m = pickle.loads(kept.read_bytes())
                        if stamp != path.stat().st_mtime_ns:
                            m = None
                    except Exception:
                        m = None
                if m is None:
                    if measured_now >= chunk:                    # this turn is over: let an export have one
                        with store._db() as db:
                            db.execute("UPDATE cull_jobs SET state='waiting', done=?, claimed=0 WHERE id=? AND state='working'",
                                       (n - 1, job["id"]))
                        return 0
                    m = measure_photo(renderer._load(path, 1600), {"taken": p["taken"], "iso": p["meta"].get("iso")})
                    kept.write_bytes(pickle.dumps((path.stat().st_mtime_ns, m)))
                    measured_now += 1
                items.append((p["id"], m))
                if (n % 5 == 0 or n == len(photos)) and not self._progress(job["id"], n):
                    return 0
            names = {p["id"]: p["file_name"] for p in photos}
            results = cull(items, strictness=job["strictness"], similar_keeps_best_only=bool(job["best_only"]))
            with store._db() as db:
                if db.execute("SELECT 1 FROM cull_jobs WHERE id=? AND state='working'", (job["id"],)).fetchone() is None:
                    return 0                                     # replaced by a newer cull meanwhile
                for r in results:
                    reasons = [_named(x, names) for x in r["reasons"]]
                    db.execute("UPDATE photos SET cull=? WHERE id=?", (json.dumps(
                        {"r": r["result"], "why": reasons, "group": r.get("group") if r.get("group_size") else None,
                         "best": bool(r.get("best_in_group")), "score": r["score"]}), r["name"]))
                db.execute("UPDATE cull_jobs SET state='done', done=total WHERE id=?", (job["id"],))
            return len(results)
        except Exception as e:
            self.fail(job, f"{type(e).__name__}: {e}")
            raise

    def fail(self, job: dict, error: str) -> None:
        """Mark the job failed and give its credits back (once)."""
        with self.store._db() as db:
            db.execute("BEGIN IMMEDIATE")
            n = db.execute("UPDATE cull_jobs SET state='failed', error=? WHERE id=? AND state<>'failed'",
                           (error[:300], job["id"])).rowcount
            if not n or not job.get("credits"):
                return
            w = db.execute("SELECT owner, name FROM weddings WHERE id=?", (job["wedding_id"],)).fetchone()
            if w is None or not w["owner"]:
                return
            db.execute("INSERT INTO ledger (user_id, delta, kind, note, ref, photos, created, wedding) VALUES (?,?,?,?,?,?,?,?)",
                       (w["owner"], int(job["credits"]), "refund", f"Cull could not finish: {w['name']}"[:200],
                        job.get("ref") or job["wedding_id"], int(job.get("total") or 0), time.time(), job["wedding_id"]))


    def faces(self, wedding_id: str, photo: dict, renderer) -> list[dict]:
        """Face boxes and per-face scores of one photo (engine/cull.face_report), worked out once per copy of
        the photo and kept in photos.faces. Uses the cull's own measurements when they are there."""
        from engine.cull import face_report
        store = self.store
        received = float(photo["received"])
        kept = store.get_faces(photo["id"], received, "faces")
        if kept is not None:
            return kept
        path = store.preview_path(wedding_id, photo["id"])
        img = renderer._load(path, 1600)
        measured = None
        pkl = path.with_suffix(".cull.pkl")
        if pkl.exists():
            try:
                stamp, m = pickle.loads(pkl.read_bytes())
                if stamp == path.stat().st_mtime_ns:
                    measured = m["faces"]
            except Exception:
                measured = None
        report = face_report(img, measured)
        store.put_faces(photo["id"], report, received, "faces")
        return report


def _named(reason: str, names: dict) -> str:
    """Reasons name a twin frame by photo id; show its file name instead."""
    for pid, name in names.items():
        if pid in reason:
            return reason.replace(pid, name)
    return reason
