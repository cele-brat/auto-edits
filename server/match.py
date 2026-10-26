"""Folder-wide facts for matching automatic values (engine/match.py) and for recommending presets
(server/recommend.py): read from the automatic values the worker keeps beside each photo,
worked out once per folder and kept in memory until the folder's photos change."""
from __future__ import annotations

import json
import threading
from collections import Counter

from engine import match as _match

TONE_PART = "tone2:11"             # the piece every photo has: white balance + exposure + the four tone values


def _kelvin(meta: dict):
    dev = meta.get("develop") if isinstance(meta, dict) else None
    if isinstance(dev, dict):
        k = dev.get("Temperature")
        if isinstance(k, (int, float)) and not isinstance(k, bool) and 1000 < k < 20000:
            return float(k)
    return None


class FolderMatch:
    def __init__(self, store):
        self.store = store
        self._cache: dict[int, tuple[tuple, dict]] = {}
        self._lock = threading.Lock()

    # ---- reading the folder ---------------------------------------------------------
    def _signature(self, db, folder_id: int) -> tuple:
        r = db.execute("SELECT COUNT(*) AS n, MAX(received) AS r, SUM(autos='') AS due, SUM(LENGTH(autos)) AS size "
                       "FROM photos WHERE folder_id=?", (folder_id,)).fetchone()
        return (r["n"], r["r"], r["due"], r["size"])

    def _build(self, db, folder_id: int) -> dict:
        rows = db.execute("SELECT id, autos, meta FROM photos WHERE folder_id=?", (folder_id,)).fetchall()
        scenes: dict[str, list] = {}
        per_photo: dict[str, str] = {}
        facts_all = []
        pending = 0
        for r in rows:
            try:
                autos = json.loads(r["autos"]) if r["autos"] else {}
                meta = json.loads(r["meta"]) if r["meta"] else {}
            except ValueError:
                autos, meta = {}, {}
            if r["autos"] == "":
                pending += 1
            facts = autos.get("facts") if isinstance(autos.get("facts"), dict) else None
            iso = meta.get("iso") if isinstance(meta.get("iso"), (int, float)) and not isinstance(meta.get("iso"), bool) else None
            scene = _match.scene_of(iso, facts, _kelvin(meta))
            per_photo[r["id"]] = scene
            if facts:
                facts_all.append({**facts, "scene": scene, "iso": iso})
            tone = autos.get(TONE_PART)
            if isinstance(tone, dict):
                scenes.setdefault(scene, []).append(tone)
        meds = {scene: _match.medians(v) for scene, v in scenes.items()}
        everything = _match.medians([t for v in scenes.values() for t in v])
        return {"scene_of": per_photo, "medians": meds, "all": everything, "facts": facts_all,
                "n": len(rows), "pending": pending}

    def folder(self, folder_id: int) -> dict:
        with self.store._db() as db:
            sig = self._signature(db, folder_id)
            with self._lock:
                kept = self._cache.get(folder_id)
            if kept and kept[0] == sig:
                return kept[1]
            built = self._build(db, folder_id)
        with self._lock:
            self._cache[folder_id] = (sig, built)
            if len(self._cache) > 400:
                self._cache.pop(next(iter(self._cache)))
        return built

    def folder_of(self, photo_id: str) -> int | None:
        with self.store._db() as db:
            r = db.execute("SELECT folder_id FROM photos WHERE id=?", (photo_id,)).fetchone()
        return int(r["folder_id"]) if r else None

    # ---- what the renderer asks -----------------------------------------------------
    def medians_for(self, photo_id: str) -> dict | None:
        """The medians this photo is matched against: those of the photos shot in the same light,
        or of the whole folder when that group is too small. None when there is nothing to match."""
        fid = self.folder_of(photo_id)
        if fid is None:
            return None
        f = self.folder(fid)
        scene = f["scene_of"].get(photo_id)
        med = f["medians"].get(scene) if scene else None
        if not med or med.get("n", 0) < _match.MIN_GROUP:
            med = f["all"]
        return med if med.get("n", 0) >= _match.MIN_GROUP else None

    # ---- what the recommender asks --------------------------------------------------
    def summary(self, folder_id: int) -> dict:
        """The folder in a few numbers: main light, mean brightness/warmth/colour, photos counted."""
        f = self.folder(folder_id)
        facts = f["facts"]
        pending = max(f["pending"], f["n"] - len(facts))          # measured before facts were kept: being filled in
        if not facts:
            return {"n": f["n"], "measured": 0, "pending": pending, "scene": None}
        count = Counter(x["scene"] for x in facts)
        mean = lambda k: round(sum(x[k] for x in facts) / len(facts), 4)
        return {"n": f["n"], "measured": len(facts), "pending": pending, "scene": count.most_common(1)[0][0],
                "scenes": dict(count), "median": mean("median"), "warmth": mean("warmth"), "colour": mean("colour"),
                "dark": mean("dark")}
