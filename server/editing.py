"""More of Lightroom's Develop module, as one router (registered in main.py with one line):

    GET  /api/weddings/{w}/photos/{p}/tile           a piece of the photo at full preview size (zoom 1:1, 2:1)
    PUT  /api/photos/look-bulk                       paste or sync chosen panels onto many photos at once
    GET  /api/weddings/{w}/photos/{p}/history        the photo's edit history (and /folders/{f}/history)
    POST .../history/{row}/restore, .../history/undo, .../history/redo, POST .../history/mark
    GET/POST/DELETE .../snapshots                    named states of one photo
    PUT  /api/weddings/{w}/photos/{p}/marks          star rating and colour label; GET /api/weddings/{w}/marks for the plug-in
    GET  /api/me/plugin                              for the plug-in's settings window: who, credits, newest version

The helpers it needs from main.py (signed_in, the store, the renderer...) come in through router(**deps),
so main.py is not refactored.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response

from . import adjustments
from .history import History, LABELS, STARS
from .looks import SIZES, _Cache, clean_look, effective_look

TILE_MAX = 1600                       # the most pixels a tile has on a side
PLUGIN_INFO = Path(__file__).resolve().parent.parent / "plugin" / "AutoEdits.lrplugin" / "Info.lua"

# which panels Copy / Paste / Sync can carry, and the look fields each one holds
PANEL_FIELDS = {"Preset": ("mode", "preset", "amount", "toggles", "preset_masks"), "Masks": ("masks", "shapes")}
COPY_PANELS = ["Preset"] + [p["name"] for p in adjustments.PANELS] + ["Masks", "Crop", "Rotate"]


def plugin_version(path: Path = PLUGIN_INFO) -> str:
    """The plug-in's version as "0.9.0", read from Info.lua (the one the download gives out)."""
    try:
        m = re.search(r"VERSION\s*=\s*\{\s*major\s*=\s*(\d+)\s*,\s*minor\s*=\s*(\d+)\s*,\s*revision\s*=\s*(\d+)", path.read_text())
    except OSError:
        return ""
    return ".".join(m.groups()) if m else ""


def merge_panels(target: dict, source: dict, panels: list[str], crop_target: dict | None) -> tuple[dict, dict | None]:
    """The target look with the chosen panels taken from the source. Both looks are clean. Returns the
    new look and the new crop (the crop lives on the photo, apart from its look)."""
    out = clean_look(target)
    src = clean_look(source)
    wanted = set(panels)
    for name, fields in PANEL_FIELDS.items():
        if name in wanted:
            for f in fields:
                out[f] = json.loads(json.dumps(src[f]))
    toggles = set(out["toggles"])
    for panel in adjustments.PANELS:
        if panel["name"] not in wanted:
            continue
        for group in panel["groups"]:
            for s in group["sliders"]:
                if s.get("field"):
                    out[s["field"]] = src[s["field"]]
                elif not s.get("switch"):
                    if src["adj"].get(s["key"]):
                        out["adj"][s["key"]] = src["adj"][s["key"]]
                    else:
                        out["adj"].pop(s["key"], None)
                if s.get("toggle") and "Preset" not in wanted:
                    toggles.discard(s["toggle"])
                    if s["toggle"] in src["toggles"]:
                        toggles.add(s["toggle"])
    if "Preset" not in wanted and out["mode"] != "auto":
        out["toggles"] = [t for t in clean_look({"mode": "preset_auto", "toggles": list(toggles)})["toggles"]]
        out["mode"] = "preset_auto" if out["toggles"] else "preset"
    from engine.crop import clean, rect, turn_of
    crop = clean(crop_target)
    src_crop = clean(src.get("crop"))
    new = {"turn": turn_of(crop)}
    r = rect(crop)
    if r:
        new.update(r)
    if "Crop" in wanted:
        for k in ("x", "y", "w", "h", "ratio", "auto"):
            new.pop(k, None)
        sr = rect(src_crop)
        if sr:
            new.update({k: sr[k] for k in ("x", "y", "w", "h", "ratio", "auto")})
    if "Rotate" in wanted:
        new["turn"] = turn_of(src_crop)
    out = clean_look(out)
    out["crop"] = None
    return out, clean(new)


def router(signed_in, store, accounts, renderer, drawing, given_look, preview_cap: int, version: str) -> APIRouter:
    r = APIRouter()
    history = History(store, lambda pid: (renderer.presets.get(pid) or {}).get("display_name"))
    tiles = _Cache(2)                 # the last full-size renders, so panning does not draw the photo again
    tiles_lock = threading.Lock()

    def wedding_of(wedding_id: str, user: dict) -> dict:
        if store.owner_of(wedding_id) != user["id"]:
            raise HTTPException(404, "no such wedding")
        w = store.wedding(wedding_id)
        if w is None:
            raise HTTPException(404, "no such wedding")
        return w

    def photo_of(wedding_id: str, photo_id: str, user: dict) -> dict:
        if store.owner_of(wedding_id) != user["id"]:
            raise HTTPException(404, "no such photo")
        p = store.photo(wedding_id, photo_id)
        if p is None:
            raise HTTPException(404, "no such photo")
        return p

    def folder_of(wedding_id: str, folder_id: int, user: dict) -> dict:
        w = wedding_of(wedding_id, user)
        for f in w["folders"]:
            if f["id"] == folder_id:
                return f
        raise HTTPException(404, "no such folder")

    def read_look(text: str, user: dict) -> dict:
        try:
            return clean_look(json.loads(text), renderer.allowed(user["id"]))
        except ValueError:
            raise HTTPException(422, "the look could not be read")

    # ---- zoom: a piece of the photo at full preview size ----------------------------------
    @r.get("/api/weddings/{wedding_id}/photos/{photo_id}/tile")
    def tile(wedding_id: str, photo_id: str, x: float = 0, y: float = 0, w: float = 1, h: float = 1, look: str = "",
             lk: str = "", user: dict = Depends(signed_in)):
        """The part (x, y, w, h: fractions of the finished picture) of the photo at full preview size:
        2560 px for the studio, PREVIEW_CAP for everyone else, never larger than the uploaded preview.
        At most TILE_MAX pixels a side; the piece is scaled down if more was asked. Edited views carry
        the preview mark as the big preview does. Headers say the full picture's size and the piece's place."""
        from engine.crop import clean
        look = given_look(look, lk, user)
        p = photo_of(wedding_id, photo_id, user)
        chosen = read_look(look, user) if look else effective_look(p["settings"], p["folder_settings"])
        chosen["crop"] = clean(p.get("crop"))
        iso = p["meta"].get("iso") if isinstance(p["meta"].get("iso"), (int, float)) else None
        size = max(SIZES) if user["admin"] else min(max(SIZES), preview_cap)
        path = store.preview_path(wedding_id, photo_id)
        key = (photo_id, p["received"], size, json.dumps(chosen, sort_keys=True), renderer.preset_version(chosen.get("preset")), not user["admin"])

        def make():
            with drawing:
                data = renderer.render(path, chosen, size, iso, mark=not user["admin"])
            return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        with tiles_lock:
            full = tiles.get(key, make)
        H, W = full.shape[:2]
        x0, y0 = int(round(min(max(x, 0), 1) * W)), int(round(min(max(y, 0), 1) * H))
        x1, y1 = int(round(min(max(x + w, 0), 1) * W)), int(round(min(max(y + h, 0), 1) * H))
        x1, y1 = max(x0 + 1, min(W, x1)), max(y0 + 1, min(H, y1))
        if x0 >= W or y0 >= H:
            raise HTTPException(422, "the piece is outside the picture")
        piece = full[y0:y1, x0:x1]
        if max(piece.shape[:2]) > TILE_MAX:
            k = TILE_MAX / max(piece.shape[:2])
            piece = cv2.resize(piece, (max(1, round(piece.shape[1] * k)), max(1, round(piece.shape[0] * k))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", np.ascontiguousarray(piece), [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise HTTPException(500, "could not write the piece")
        return Response(buf.tobytes(), media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600", "X-Full-Size": f"{W},{H}",
                                 "X-Tile-Rect": f"{x0},{y0},{x1 - x0},{y1 - y0}"})

    # ---- copy, paste, sync -------------------------------------------------------------------
    @r.get("/api/copy-panels")
    def copy_panels(user: dict = Depends(signed_in)):
        return {"panels": COPY_PANELS}

    @r.put("/api/photos/look-bulk")
    def look_bulk(body: dict = Body(...), user: dict = Depends(signed_in)):
        """{photo_ids, panels, look, label?}: the chosen panels of the look (its crop inside it, for Crop and
        Rotate) laid onto each photo's own settings (its folder's where it has none). One request for
        many photos; each gets a history row ("Paste settings" unless a label is given)."""
        ids = body.get("photo_ids")
        panels = body.get("panels")
        if not isinstance(ids, list) or not ids or len(ids) > 2000 or not all(isinstance(i, str) for i in ids):
            raise HTTPException(422, "photo_ids must be a list of 1 to 2000 photo ids")
        if not isinstance(panels, list) or not panels:
            raise HTTPException(422, "panels must name at least one panel")
        panels = [p for p in panels if p in COPY_PANELS]
        if not panels:
            raise HTTPException(422, "panels must name panels: " + ", ".join(COPY_PANELS))
        source = clean_look(body.get("look"), renderer.allowed(user["id"]))
        label = " ".join(str(body.get("label") or "Paste settings").split())[:40] or "Paste settings"
        with store._db() as db:
            marks = ",".join("?" * len(ids))
            rows = db.execute(f"SELECT p.id, p.wedding_id, p.settings, p.crop, f.settings AS folder_settings, w.owner "
                              f"FROM photos p JOIN folders f ON f.id=p.folder_id JOIN weddings w ON w.id=p.wedding_id "
                              f"WHERE p.id IN ({marks})", ids).fetchall()
        rows = [x for x in rows if x["owner"] == user["id"]]
        if not rows:
            raise HTTPException(404, "no such photos")
        out = []
        for x in rows:
            own = json.loads(x["settings"]) if x["settings"] else None
            target = own if own else json.loads(x["folder_settings"] or "{}")
            crop = json.loads(x["crop"]) if x["crop"] else None
            look, crop = merge_panels(target, source, panels, crop)
            store.set_photo_settings(x["wedding_id"], x["id"], look)
            store.set_crop(x["wedding_id"], x["id"], crop)
            history.mark_photo(x["id"], label)
            out.append({"id": x["id"], "look": look, "crop": crop})
        return {"photos": out, "panels": panels}

    # ---- history ------------------------------------------------------------------------------
    @r.post("/api/weddings/{wedding_id}/photos/{photo_id}/history/mark")
    def mark_photo(wedding_id: str, photo_id: str, body: dict = Body(default={}), user: dict = Depends(signed_in)):
        """The app calls this after a save: a row labelled by what changed (or by "label")."""
        photo_of(wedding_id, photo_id, user)
        label = " ".join(str(body.get("label") or "").split())[:60] or None
        return {"row": history.mark_photo(photo_id, label), **history.rows(photo_id, None)}

    @r.post("/api/weddings/{wedding_id}/folders/{folder_id}/history/mark")
    def mark_folder(wedding_id: str, folder_id: int, body: dict = Body(default={}), user: dict = Depends(signed_in)):
        folder_of(wedding_id, folder_id, user)
        label = " ".join(str(body.get("label") or "").split())[:60] or None
        return {"row": history.mark_folder(folder_id, label), **history.rows(None, folder_id)}

    @r.get("/api/weddings/{wedding_id}/photos/{photo_id}/history")
    def photo_history(wedding_id: str, photo_id: str, user: dict = Depends(signed_in)):
        photo_of(wedding_id, photo_id, user)
        return history.rows(photo_id, None)

    @r.get("/api/weddings/{wedding_id}/folders/{folder_id}/history")
    def folder_history(wedding_id: str, folder_id: int, user: dict = Depends(signed_in)):
        folder_of(wedding_id, folder_id, user)
        return history.rows(None, folder_id)

    def restored(wedding_id, photo_id, folder_id, got):
        if got is None:
            raise HTTPException(404, "no such step")
        if photo_id:
            return {**got, **history.rows(photo_id, None)}
        return {**got, **history.rows(None, folder_id)}

    @r.post("/api/weddings/{wedding_id}/photos/{photo_id}/history/{row_id}/restore")
    def restore_photo(wedding_id: str, photo_id: str, row_id: int, user: dict = Depends(signed_in)):
        photo_of(wedding_id, photo_id, user)
        return restored(wedding_id, photo_id, None, history.restore(row_id, photo_id, None))

    @r.post("/api/weddings/{wedding_id}/folders/{folder_id}/history/{row_id}/restore")
    def restore_folder(wedding_id: str, folder_id: int, row_id: int, user: dict = Depends(signed_in)):
        folder_of(wedding_id, folder_id, user)
        return restored(wedding_id, None, folder_id, history.restore(row_id, None, folder_id))

    @r.post("/api/weddings/{wedding_id}/photos/{photo_id}/history/{which}")
    def step_photo(wedding_id: str, photo_id: str, which: str, user: dict = Depends(signed_in)):
        """undo or redo: one step back or forward through the history."""
        if which not in ("undo", "redo"):
            raise HTTPException(404, "Not found")
        photo_of(wedding_id, photo_id, user)
        return restored(wedding_id, photo_id, None, history.step(-1 if which == "undo" else 1, photo_id, None))

    @r.post("/api/weddings/{wedding_id}/folders/{folder_id}/history/{which}")
    def step_folder(wedding_id: str, folder_id: int, which: str, user: dict = Depends(signed_in)):
        if which not in ("undo", "redo"):
            raise HTTPException(404, "Not found")
        folder_of(wedding_id, folder_id, user)
        return restored(wedding_id, None, folder_id, history.step(-1 if which == "undo" else 1, None, folder_id))

    # ---- snapshots -----------------------------------------------------------------------------
    @r.get("/api/weddings/{wedding_id}/photos/{photo_id}/snapshots")
    def snapshots(wedding_id: str, photo_id: str, user: dict = Depends(signed_in)):
        photo_of(wedding_id, photo_id, user)
        return {"snapshots": history.snapshots(photo_id)}

    @r.post("/api/weddings/{wedding_id}/photos/{photo_id}/snapshots")
    def take_snapshot(wedding_id: str, photo_id: str, body: dict = Body(default={}), user: dict = Depends(signed_in)):
        photo_of(wedding_id, photo_id, user)
        try:
            made = history.snapshot(photo_id, str(body.get("name") or ""))
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"snapshot": made, "snapshots": history.snapshots(photo_id)}

    @r.post("/api/weddings/{wedding_id}/photos/{photo_id}/snapshots/{snap_id}/restore")
    def restore_snapshot(wedding_id: str, photo_id: str, snap_id: int, user: dict = Depends(signed_in)):
        photo_of(wedding_id, photo_id, user)
        got = history.restore_snapshot(photo_id, snap_id)
        if got is None:
            raise HTTPException(404, "no such snapshot")
        return {**got, **history.rows(photo_id, None)}

    @r.delete("/api/weddings/{wedding_id}/photos/{photo_id}/snapshots/{snap_id}")
    def delete_snapshot(wedding_id: str, photo_id: str, snap_id: int, user: dict = Depends(signed_in)):
        photo_of(wedding_id, photo_id, user)
        if not history.delete_snapshot(photo_id, snap_id):
            raise HTTPException(404, "no such snapshot")
        return {"deleted": True, "snapshots": history.snapshots(photo_id)}

    # ---- stars and colour labels -----------------------------------------------------------------
    @r.put("/api/weddings/{wedding_id}/photos/{photo_id}/marks")
    def set_marks(wedding_id: str, photo_id: str, body: dict = Body(...), user: dict = Depends(signed_in)):
        """{"stars": 0-5} and / or {"label": "red" | "yellow" | "green" | "blue" | "purple" | ""}."""
        wedding_of(wedding_id, user)
        stars = body.get("stars")
        label = body.get("label")
        if stars is not None and (not isinstance(stars, int) or isinstance(stars, bool) or stars not in STARS):
            raise HTTPException(422, "stars must be 0 to 5")
        if label is not None and (not isinstance(label, str) or label not in LABELS):
            raise HTTPException(422, "label must be red, yellow, green, blue, purple or empty")
        got = history.set_marks(wedding_id, photo_id, stars, label)
        if got is None:
            raise HTTPException(404, "no such photo")
        return got

    @r.get("/api/weddings/{wedding_id}/marks")
    def marks(wedding_id: str, user: dict = Depends(signed_in)):
        """For the plug-in: Lightroom id -> stars and label, for the photos that have either."""
        wedding_of(wedding_id, user)
        return {"marks": history.marks(wedding_id)}

    # ---- the plug-in's settings window --------------------------------------------------------------
    @r.get("/api/me/plugin")
    def me_plugin(request: Request, user: dict = Depends(signed_in)):
        return {"email": user["email"], "name": user["name"], "credits": accounts.balance(user["id"]),
                "unlimited": bool(user["admin"]), "server_version": version,
                "plugin_version": plugin_version(), "download": "/download/lightroom-plugin"}

    r.history = history
    return r
