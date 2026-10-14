"""Culling: every photo of a folder is rated keep, maybe or reject, with the reasons.

A cull is a job in the database, done by the export worker. The measurements of a photo
(sharpness, faces, eyes, exposure) are kept beside its preview, so running the cull again
with another strictness takes seconds. A rating the photographer sets by hand ("pick")
always wins over the automatic one and is never touched by a new cull.
"""
from __future__ import annotations

import json
import pickle
import time

STRICTNESS = ("relaxed", "normal", "strict")
RATINGS = ("keep", "maybe", "reject")

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

    def start(self, wedding_id: str, folder_ids: list[int], strictness: str, best_only: bool) -> list[dict]:
        if strictness not in STRICTNESS:
            raise ValueError("strictness must be relaxed, normal or strict")
        now = time.time()
        with self.store._db() as db:
            for f in folder_ids:
                total = db.execute("SELECT COUNT(*) FROM photos WHERE folder_id=?", (f,)).fetchone()[0]
                if not total:
                    continue
                db.execute("DELETE FROM cull_jobs WHERE folder_id=?", (f,))          # one cull per folder: the newest
                db.execute("INSERT INTO cull_jobs (wedding_id, folder_id, strictness, best_only, total, created) VALUES (?,?,?,?,?,?)",
                           (wedding_id, f, strictness, int(best_only), total, now))
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
            db.execute("UPDATE cull_jobs SET state='working', claimed=?, done=0 WHERE id=?", (now, r["id"]))
            return dict(r)

    def _progress(self, job_id: int, done: int) -> bool:
        """Note progress. False when the job was replaced or removed meanwhile."""
        with self.store._db() as db:
            return bool(db.execute("UPDATE cull_jobs SET done=?, claimed=? WHERE id=?", (done, time.time(), job_id)).rowcount)

    def run(self, job: dict, renderer) -> int:
        """Do one cull job. Returns how many photos were rated."""
        from engine.cull import cull, measure_photo
        store = self.store
        try:
            photos = store.photos(job["wedding_id"], job["folder_id"])
            items = []
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
                    m = measure_photo(renderer._load(path, 1600), {"taken": p["taken"], "iso": p["meta"].get("iso")})
                    kept.write_bytes(pickle.dumps((path.stat().st_mtime_ns, m)))
                items.append((p["id"], m))
                if (n % 5 == 0 or n == len(photos)) and not self._progress(job["id"], n):
                    return 0
            names = {p["id"]: p["file_name"] for p in photos}
            results = cull(items, strictness=job["strictness"], similar_keeps_best_only=bool(job["best_only"]))
            with store._db() as db:
                for r in results:
                    reasons = [_named(x, names) for x in r["reasons"]]
                    db.execute("UPDATE photos SET cull=? WHERE id=?", (json.dumps(
                        {"r": r["result"], "why": reasons, "group": r.get("group") if r.get("group_size") else None,
                         "best": bool(r.get("best_in_group")), "score": r["score"]}), r["name"]))
                db.execute("UPDATE cull_jobs SET state='done', done=total WHERE id=?", (job["id"],))
            return len(results)
        except Exception as e:
            with store._db() as db:
                db.execute("UPDATE cull_jobs SET state='failed', error=? WHERE id=?", (f"{type(e).__name__}: {e}"[:300], job["id"]))
            raise


def _named(reason: str, names: dict) -> str:
    """Reasons name a twin frame by photo id; show its file name instead."""
    for pid, name in names.items():
        if pid in reason:
            return reason.replace(pid, name)
    return reason
