"""Edit history, snapshots and star ratings / colour labels, kept per photo (and per folder) like Lightroom.

    history    one row per saved change: who (photo or folder), when, a label ("Exposure",
               "Preset: Velvet Court", "Crop", "Paste settings"), and the whole state after the change
               (the look and the crop). At most HISTORY_CAP rows per photo; the oldest go.
    history_head  which row a photo (or folder) is at after an undo or a restore: later rows stay
               until a new change truncates them, as in Lightroom.
    snapshots  named states of one photo, kept until deleted.

A row is written by `mark` after any save: it reads the current state from the photos / folders
tables and works out the label from what changed since the last row, so every endpoint that saves
something only has to call mark() (the app does, after each save). The bulk endpoints give their
own label ("Paste settings", "Sync settings").
"""
from __future__ import annotations

import json
import time

from . import adjustments
from .looks import clean_look

HISTORY_CAP = 100
STARS = (0, 1, 2, 3, 4, 5)
LABELS = ("", "red", "yellow", "green", "blue", "purple")
LABEL_NAMES = {"red": "Red", "yellow": "Yellow", "green": "Green", "blue": "Blue", "purple": "Purple"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY, wedding_id TEXT NOT NULL, photo_id TEXT, folder_id INTEGER,
    ts REAL NOT NULL, label TEXT NOT NULL, look TEXT, crop TEXT
);
CREATE INDEX IF NOT EXISTS history_by_photo ON history (photo_id, id);
CREATE INDEX IF NOT EXISTS history_by_folder ON history (folder_id, id);
CREATE TABLE IF NOT EXISTS history_head (
    who TEXT PRIMARY KEY, row_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY, photo_id TEXT NOT NULL, name TEXT NOT NULL, ts REAL NOT NULL, look TEXT, crop TEXT
);
CREATE INDEX IF NOT EXISTS snapshots_by_photo ON snapshots (photo_id, id);
"""

MODE_NAMES = {"preset": "Preset", "preset_auto": "Preset + auto", "auto": "Auto"}
TOGGLE_NAMES = {"exposure": "Auto exposure", "highlights": "Auto highlights", "shadows": "Auto shadows",
                "whites": "Auto whites", "blacks": "Auto blacks", "white_balance": "Auto white balance",
                "noise": "Auto noise", "straighten": "Auto straighten", "lens": "Lens corrections"}
MASK_NAMES = {"subject": "Subject", "background": "Background", "face_skin": "Face skin", "body_skin": "Body skin",
              "eyes": "Eyes", "hair": "Hair", "clothes": "Clothes", "sky": "Sky"}
_PREFIXED = {"Color Mixer", "Color Grading", "Calibration"}


def _slider_name(key: str, by_field: bool = False) -> str:
    """"Exposure", "Hue: Red", "Grain amount"... for one slider, found by its Lightroom key or its look field."""
    for panel in adjustments.PANELS:
        for group in panel["groups"]:
            for s in group["sliders"]:
                if (s.get("field") == key) if by_field else (s["key"] == key):
                    if panel["name"] in _PREFIXED and group["name"]:
                        return f"{group['name']}: {s['label']}"
                    if panel["name"] == "Effects" and group["name"]:
                        return f"{group['name'].split(' ')[-1].capitalize()} {s['label'].lower()}"
                    return s["label"]
    return key


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def _rect(crop) -> dict | None:
    return {k: crop[k] for k in ("x", "y", "w", "h")} if crop and "w" in crop else None


def label_for(prev_look, new_look, prev_crop, new_crop, preset_name=lambda pid: None) -> str:
    """What changed between two states, in Lightroom's words. Each state is a look (None: the folder's)
    and a crop (None: the whole picture)."""
    changes: list[str] = []
    if new_look is None and prev_look is not None:
        changes.append("Folder settings")
    else:
        a, b = clean_look(prev_look or {}), clean_look(new_look or {})
        if a["preset"] != b["preset"] or a["mode"] != b["mode"]:
            if b["mode"] == "auto":
                changes.append("Edit with: Auto")
            elif b["preset"] != a["preset"]:
                changes.append(f"Preset: {preset_name(b['preset']) or b['preset']}" if b["preset"] else "No preset")
            else:
                changes.append(f"Edit with: {MODE_NAMES[b['mode']]}")
        if a["amount"] != b["amount"]:
            changes.append("Amount")
        for key in ("ev", "temp", "tint", "hl", "sh", "wh", "bl", "noise", "angle"):
            if a[key] != b[key]:
                changes.append(_slider_name(key, by_field=True))
        for key in sorted(set(a["adj"]) | set(b["adj"])):
            if a["adj"].get(key) != b["adj"].get(key):
                changes.append(_slider_name(key))
        if a["mode"] != "auto" and b["mode"] != "auto":
            for t in sorted(set(a["toggles"]) ^ set(b["toggles"])):
                changes.append(TOGGLE_NAMES.get(t, t) + (" on" if t in b["toggles"] else " off"))
        for kind in sorted(set(a["masks"]) | set(b["masks"])):
            if not _same(a["masks"].get(kind), b["masks"].get(kind)):
                changes.append(f"Mask: {MASK_NAMES.get(kind, kind)}")
        if not _same(a["shapes"], b["shapes"]):
            changes.append("Drawn mask")
        if a["preset_masks"] != b["preset_masks"]:
            changes.append("Preset masks " + ("on" if b["preset_masks"] else "off"))
    pr, nr = _rect(prev_crop), _rect(new_crop)
    if int((prev_crop or {}).get("turn") or 0) != int((new_crop or {}).get("turn") or 0):
        changes.append("Rotate")                       # the crop turns with the photo: that is not a new crop
    elif not _same(pr, nr):
        changes.append("Crop" if nr else "Remove crop")
    if not changes:
        return "Saved"
    if len(changes) > 3:
        return "Several changes"
    return ", ".join(changes)


class History:
    def __init__(self, store, preset_name=lambda pid: None):
        self.store = store
        self.preset_name = preset_name
        with store._db() as db:
            db.executescript(SCHEMA)               # photos.stars and photos.label are added in store.py

    # ---- the current state of a photo or folder ------------------------------------------------
    def _photo_state(self, db, photo_id: str):
        r = db.execute("SELECT wedding_id, folder_id, settings, crop FROM photos WHERE id=?", (photo_id,)).fetchone()
        if r is None:
            return None
        return {"wedding_id": r["wedding_id"], "folder_id": r["folder_id"],
                "look": json.loads(r["settings"]) if r["settings"] else None, "crop": json.loads(r["crop"]) if r["crop"] else None}

    def _folder_state(self, db, folder_id: int):
        r = db.execute("SELECT wedding_id, settings FROM folders WHERE id=?", (folder_id,)).fetchone()
        if r is None:
            return None
        look = json.loads(r["settings"] or "{}")
        return {"wedding_id": r["wedding_id"], "look": look if look.get("mode") else None, "crop": None}

    @staticmethod
    def _who(photo_id: str | None, folder_id: int | None) -> str:
        return f"p:{photo_id}" if photo_id else f"f:{folder_id}"

    def _last(self, db, photo_id: str | None, folder_id: int | None):
        """The row the photo/folder is at (after an undo: not the newest one)."""
        head = db.execute("SELECT row_id FROM history_head WHERE who=?", (self._who(photo_id, folder_id),)).fetchone()
        if head:
            r = db.execute("SELECT * FROM history WHERE id=?", (head["row_id"],)).fetchone()
            if r:
                return r
        if photo_id:
            return db.execute("SELECT * FROM history WHERE photo_id=? ORDER BY id DESC LIMIT 1", (photo_id,)).fetchone()
        return db.execute("SELECT * FROM history WHERE folder_id=? AND photo_id IS NULL ORDER BY id DESC LIMIT 1", (folder_id,)).fetchone()

    def _write(self, db, wedding_id: str, photo_id: str | None, folder_id: int | None, label: str, look, crop) -> int:
        who = self._who(photo_id, folder_id)
        head = db.execute("SELECT row_id FROM history_head WHERE who=?", (who,)).fetchone()
        if head:                                      # a new change after an undo: the later steps go, as in Lightroom
            if photo_id:
                db.execute("DELETE FROM history WHERE photo_id=? AND id>?", (photo_id, head["row_id"]))
            else:
                db.execute("DELETE FROM history WHERE folder_id=? AND photo_id IS NULL AND id>?", (folder_id, head["row_id"]))
            db.execute("DELETE FROM history_head WHERE who=?", (who,))
        row = db.execute("INSERT INTO history (wedding_id, photo_id, folder_id, ts, label, look, crop) VALUES (?,?,?,?,?,?,?)",
                         (wedding_id, photo_id, folder_id, time.time(), label[:80],
                          json.dumps(look) if look is not None else None, json.dumps(crop) if crop else None)).lastrowid
        if photo_id:
            db.execute("DELETE FROM history WHERE photo_id=? AND id NOT IN (SELECT id FROM history WHERE photo_id=? ORDER BY id DESC LIMIT ?)",
                       (photo_id, photo_id, HISTORY_CAP))
        else:
            db.execute("DELETE FROM history WHERE folder_id=? AND photo_id IS NULL AND id NOT IN "
                       "(SELECT id FROM history WHERE folder_id=? AND photo_id IS NULL ORDER BY id DESC LIMIT ?)",
                       (folder_id, folder_id, HISTORY_CAP))
        return row

    # ---- marking a change ------------------------------------------------------------------------
    def mark_photo(self, photo_id: str, label: str | None = None) -> dict | None:
        """A row for the photo's current state, labelled by what changed since its last row
        (or by `label`). Nothing is written when nothing changed, unless a label is given."""
        with self.store._db() as db:
            db.execute("BEGIN IMMEDIATE")
            now = self._photo_state(db, photo_id)
            if now is None:
                return None
            last = self._last(db, photo_id, None)
            if last is None:
                self._write(db, now["wedding_id"], photo_id, None, "Uploaded", None, None)
                prev_look, prev_crop = None, None
                # the first change is read against what the photo showed: its folder's look
                folder = self._folder_state(db, now["folder_id"])
                base_look = folder["look"] if folder else None
            else:
                prev_look = json.loads(last["look"]) if last["look"] else None
                prev_crop = json.loads(last["crop"]) if last["crop"] else None
                base_look = prev_look
                if not label and _same(prev_look, now["look"]) and _same(prev_crop, now["crop"]):
                    return None
            text = label or label_for(base_look if prev_look is None else prev_look, now["look"], prev_crop, now["crop"], self.preset_name)
            row = self._write(db, now["wedding_id"], photo_id, None, text, now["look"], now["crop"])
        return {"id": row, "label": text}

    def mark_folder(self, folder_id: int, label: str | None = None) -> dict | None:
        """A folder row for the folder's look, plus a photo row for each photo in it whose own state
        changed (a folder reset takes every photo's own settings and crop away)."""
        with self.store._db() as db:
            db.execute("BEGIN IMMEDIATE")
            now = self._folder_state(db, folder_id)
            if now is None:
                return None
            last = self._last(db, None, folder_id)
            prev_look = json.loads(last["look"]) if last and last["look"] else None
            out = None
            if last is None or not _same(prev_look, now["look"]):
                if last is None:
                    self._write(db, now["wedding_id"], None, folder_id, "Uploaded", None, None)
                text = label or label_for(prev_look, now["look"], None, None, self.preset_name)
                out = {"id": self._write(db, now["wedding_id"], None, folder_id, text, now["look"], None), "label": text}
            # photos with their own settings or crop whose state changed (a reset takes them away): a row each
            for t in db.execute("SELECT id FROM photos WHERE folder_id=?", (folder_id,)).fetchall():
                p = self._photo_state(db, t["id"])
                lastp = self._last(db, t["id"], None)
                if lastp is None:
                    if p["look"] is None and not p["crop"]:
                        continue                                  # never had anything of its own: nothing to record
                    self._write(db, p["wedding_id"], t["id"], None, "Uploaded", None, None)
                pl = json.loads(lastp["look"]) if lastp and lastp["look"] else None
                pc = json.loads(lastp["crop"]) if lastp and lastp["crop"] else None
                if not (_same(pl, p["look"]) and _same(pc, p["crop"])):
                    self._write(db, p["wedding_id"], t["id"], None, label or label_for(pl, p["look"], pc, p["crop"], self.preset_name),
                                p["look"], p["crop"])
        return out

    # ---- reading, restoring ---------------------------------------------------------------------
    def rows(self, photo_id: str | None, folder_id: int | None) -> dict:
        with self.store._db() as db:
            if photo_id:
                rs = db.execute("SELECT id, ts, label FROM history WHERE photo_id=? ORDER BY id", (photo_id,)).fetchall()
            else:
                rs = db.execute("SELECT id, ts, label FROM history WHERE folder_id=? AND photo_id IS NULL ORDER BY id", (folder_id,)).fetchall()
            head = db.execute("SELECT row_id FROM history_head WHERE who=?", (self._who(photo_id, folder_id),)).fetchone()
        at = head["row_id"] if head else (rs[-1]["id"] if rs else None)
        return {"steps": [{"id": r["id"], "ts": r["ts"], "label": r["label"]} for r in rs], "at": at}

    def restore(self, row_id: int, photo_id: str | None, folder_id: int | None) -> dict | None:
        """Put the photo (or folder) back to this step. Later steps stay until the next change."""
        with self.store._db() as db:
            db.execute("BEGIN IMMEDIATE")
            r = db.execute("SELECT * FROM history WHERE id=?", (row_id,)).fetchone()
            if r is None or (photo_id and r["photo_id"] != photo_id) or (not photo_id and (r["folder_id"] != folder_id or r["photo_id"])):
                return None
            look = json.loads(r["look"]) if r["look"] else None
            crop = json.loads(r["crop"]) if r["crop"] else None
            if photo_id:
                db.execute("UPDATE photos SET settings=?, crop=? WHERE id=?",
                           (json.dumps(look) if look is not None else "", json.dumps(crop) if crop else "", photo_id))
            else:
                db.execute("UPDATE folders SET settings=? WHERE id=?", (json.dumps(look or {}), folder_id))
            db.execute("INSERT OR REPLACE INTO history_head (who, row_id) VALUES (?,?)", (self._who(photo_id, folder_id), row_id))
        return {"id": row_id, "label": r["label"], "look": look, "crop": crop}

    def step(self, by: int, photo_id: str | None, folder_id: int | None) -> dict | None:
        """Undo (by=-1) or redo (by=+1): the step before or after the one the photo is at."""
        h = self.rows(photo_id, folder_id)
        ids = [s["id"] for s in h["steps"]]
        if h["at"] not in ids:
            return None
        i = ids.index(h["at"]) + by
        if i < 0 or i >= len(ids):
            return None
        return self.restore(ids[i], photo_id, folder_id)

    # ---- snapshots ------------------------------------------------------------------------------
    def snapshots(self, photo_id: str) -> list[dict]:
        with self.store._db() as db:
            return [dict(r) for r in db.execute("SELECT id, name, ts FROM snapshots WHERE photo_id=? ORDER BY id", (photo_id,))]

    def snapshot(self, photo_id: str, name: str) -> dict | None:
        name = " ".join(name.split())[:60] or time.strftime("%d %b %Y %H:%M")
        with self.store._db() as db:
            now = self._photo_state(db, photo_id)
            if now is None:
                return None
            if len(db.execute("SELECT id FROM snapshots WHERE photo_id=?", (photo_id,)).fetchall()) >= 50:
                raise ValueError("This photo has 50 snapshots, the most it can keep. Delete one first.")
            row = db.execute("INSERT INTO snapshots (photo_id, name, ts, look, crop) VALUES (?,?,?,?,?)",
                             (photo_id, name, time.time(), json.dumps(now["look"]) if now["look"] is not None else None,
                              json.dumps(now["crop"]) if now["crop"] else None)).lastrowid
        return {"id": row, "name": name}

    def delete_snapshot(self, photo_id: str, snap_id: int) -> bool:
        with self.store._db() as db:
            return bool(db.execute("DELETE FROM snapshots WHERE id=? AND photo_id=?", (snap_id, photo_id)).rowcount)

    def restore_snapshot(self, photo_id: str, snap_id: int) -> dict | None:
        with self.store._db() as db:
            r = db.execute("SELECT * FROM snapshots WHERE id=? AND photo_id=?", (snap_id, photo_id)).fetchone()
            if r is None:
                return None
            look = json.loads(r["look"]) if r["look"] else None
            crop = json.loads(r["crop"]) if r["crop"] else None
            db.execute("UPDATE photos SET settings=?, crop=? WHERE id=?",
                       (json.dumps(look) if look is not None else "", json.dumps(crop) if crop else "", photo_id))
        self.mark_photo(photo_id, f"Snapshot: {r['name']}")
        return {"look": look, "crop": crop, "name": r["name"]}

    # ---- stars and colour labels -----------------------------------------------------------------
    def set_marks(self, wedding_id: str, photo_id: str, stars: int | None, label: str | None) -> dict | None:
        """One photo's star rating and / or colour label (None leaves that one as it is)."""
        with self.store._db() as db:
            r = db.execute("SELECT stars, label FROM photos WHERE wedding_id=? AND id=?", (wedding_id, photo_id)).fetchone()
            if r is None:
                return None
            stars = r["stars"] if stars is None else stars
            label = r["label"] if label is None else label
            db.execute("UPDATE photos SET stars=?, label=? WHERE id=?", (stars, label, photo_id))
        return {"stars": stars, "label": label}

    def marks(self, wedding_id: str) -> dict:
        """Lightroom id -> stars and label, for every photo of the wedding that has either (for the plug-in)."""
        with self.store._db() as db:
            rows = db.execute("SELECT lr_uuid, stars, label FROM photos WHERE wedding_id=? AND (stars>0 OR label<>'')",
                              (wedding_id,)).fetchall()
        return {r["lr_uuid"]: {"stars": r["stars"], "label": r["label"]} for r in rows}
