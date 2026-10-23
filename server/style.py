"""Learn my style: presets made from a photographer's past edits (engine/style.py).

The photographer uploads the develop settings of photos they edited before: Lightroom .xmp
sidecar files, a folder or zip of them, or a CSV / JSON export of develop settings. Every edit
is read, the edits are grouped into one to five styles, and each style becomes a preset
"My style 1…" in the pack "My style", saved like a preset made in the app (with a Lightroom
.xmp file to download). A summary says in plain words what the edits have in common.

With the original camera previews uploaded too (JPEGs named like the sidecars), the edits
are also grouped by the light they were shot in (bright outdoor, indoor tungsten, dusk),
one preset per kind of light. Costs nothing.
"""
from __future__ import annotations

import csv
import io
import json
import re
import uuid as _uuid
import zipfile
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from engine import style as _style
from engine.presets import parse_xmp, write_xmp
from . import limits
from .looks import preset_tags

PACK = "My style"
MAX_UPLOAD = 60 * 1024 * 1024          # all files together
MAX_EDITS = 3000
MAX_PREVIEWS = 3000
_ISO = re.compile(r"exif:ISOSpeedRatings[^>]*?<rdf:li>(\d+)</rdf:li>|exif:ISOSpeedRatings=\"(\d+)\"|exif:ISO=\"(\d+)\"", re.S)
_ROW_KEYS = {"name", "file", "filename", "file_name", "photo", "image"}


def _edit_from_xmp(text: str, name: str) -> dict | None:
    try:
        p = parse_xmp(text, name)
    except Exception:
        return None
    settings = {k: v for k, v in p.settings.items() if isinstance(v, (int, float, bool))}
    if not settings and not p.curves:
        return None
    iso = None
    m = _ISO.search(text)
    if m:
        iso = int(next(g for g in m.groups() if g))
    kelvin = p.settings.get("Temperature") if isinstance(p.settings.get("Temperature"), (int, float)) else None
    return {"name": name, "settings": settings, "curves": p.curves, "iso": iso,
            "kelvin": float(kelvin) if kelvin and kelvin > 1000 else None}


def _num(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip()
        if s in ("True", "true"):
            return True
        if s in ("False", "false"):
            return False
        try:
            f = float(s)
            return int(f) if f.is_integer() else f
        except ValueError:
            return None
    return None


def _edit_from_row(row: dict, name: str) -> dict | None:
    """One edit from a CSV row or a JSON object whose keys are Lightroom setting names."""
    settings = {}
    curves = {}
    if isinstance(row.get("settings"), dict):
        row = {**row, **row["settings"]}
    if isinstance(row.get("curves"), dict):
        curves = {k: v for k, v in row["curves"].items() if isinstance(v, list)}
    for k, v in row.items():
        if not isinstance(k, str) or k.lower() in _ROW_KEYS or k in ("settings", "curves"):
            continue
        n = _num(v)
        if n is not None and re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", k):
            settings[k] = n
    if not settings and not curves:
        return None
    iso = _num(row.get("ISO") or row.get("iso") or row.get("ISOSpeedRatings"))
    return {"name": str(row.get("name") or row.get("file") or row.get("fileName") or row.get("file_name") or name),
            "settings": settings, "curves": curves, "iso": iso if isinstance(iso, (int, float)) else None,
            "kelvin": float(settings["Temperature"]) if isinstance(settings.get("Temperature"), (int, float)) and settings["Temperature"] > 1000 else None}


def read_edits(name: str, data: bytes, previews: dict) -> list[dict]:
    """Every edit found in one uploaded file (an .xmp, .csv, .json or a zip of those).
    JPEG previews met on the way go in previews (by file stem)."""
    low = name.lower()
    stem = PurePosixPath(name).stem.lower()
    if low.endswith(".zip"):
        out = []
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for info in z.infolist()[:5000]:
                    if info.is_dir() or info.file_size > 40 * 1024 * 1024 or info.filename.startswith("__MACOSX"):
                        continue
                    out += read_edits(PurePosixPath(info.filename).name, z.read(info), previews)
        except zipfile.BadZipFile:
            pass
        return out
    if low.endswith((".jpg", ".jpeg")):
        if len(previews) < MAX_PREVIEWS and data[:3] == b"\xff\xd8\xff":
            previews[stem] = data
        return []
    if low.endswith(".xmp"):
        e = _edit_from_xmp(data.decode("utf-8", errors="replace"), name)
        return [e] if e else []
    if low.endswith(".csv"):
        try:
            rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig", errors="replace"))))
        except csv.Error:
            return []
        return [e for i, r in enumerate(rows[:MAX_EDITS]) if (e := _edit_from_row(r, f"{stem} row {i + 1}"))]
    if low.endswith(".json"):
        try:
            doc = json.loads(data.decode("utf-8", errors="replace"))
        except ValueError:
            return []
        if isinstance(doc, dict) and isinstance(doc.get("photos"), list):
            doc = doc["photos"]
        if isinstance(doc, dict):
            doc = [{"name": k, **v} if isinstance(v, dict) else {} for k, v in doc.items()]
        if not isinstance(doc, list):
            return []
        return [e for i, r in enumerate(doc[:MAX_EDITS]) if isinstance(r, dict) and (e := _edit_from_row(r, f"{stem} item {i + 1}"))]
    return []


def summary_words(settings: dict, curves: dict, edits: list[dict]) -> str:
    """"Your edits: warm, lifted shadows, −8 highlights on average…" from the averaged settings and
    the plain mood words of server/looks.py."""
    tags = set(preset_tags({"settings": settings, "curves": curves, "description": ""}))
    words = [w for w in ("warm", "cool", "black and white", "matte", "film", "vibrant", "muted", "bright", "dark") if w in tags]
    n = lambda k: float(settings.get(k) or 0)
    if n("Shadows2012") >= 10:
        words.append("lifted shadows")
    elif n("Shadows2012") <= -10:
        words.append("deep shadows")
    if n("Contrast2012") >= 10:
        words.append("punchy contrast")
    elif n("Contrast2012") <= -10:
        words.append("soft contrast")
    if n("Clarity2012") >= 10 or n("Texture") >= 10:
        words.append("crisp detail")
    elif n("Clarity2012") <= -10 or n("Texture") <= -10:
        words.append("softened skin")
    if n("Vibrance") + n("Saturation") <= -15 and "muted" not in words:
        words.append("muted")
    bits = []
    for key, label in (("Highlights2012", "highlights"), ("Shadows2012", "shadows"), ("Whites2012", "whites"),
                       ("Blacks2012", "blacks"), ("Contrast2012", "contrast"), ("Vibrance", "vibrance"), ("Saturation", "saturation")):
        v = n(key)
        if abs(v) >= 3:
            bits.append(f"{'−' if v < 0 else '+'}{abs(int(round(v)))} {label}")
    ev = [e["settings"].get("Exposure2012") for e in edits]
    ev = [float(v) for v in ev if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if ev and abs(sum(ev) / len(ev)) >= 0.15:
        a = sum(ev) / len(ev)
        bits.append(f"exposure {'−' if a < 0 else '+'}{abs(a):.1f}")
    text = ", ".join(words) if words else "natural"
    if bits:
        text += f"; {', '.join(bits[:5])} on average"
    return f"Your edits: {text}."


def make_router(ctx: dict) -> APIRouter:
    signed_in, store, renderer = ctx["signed_in"], ctx["store"], ctx["renderer"]
    r = APIRouter()

    def save_preset(name: str, group: str, settings: dict, curves: dict, user: dict, about: str) -> dict:
        uid = _uuid.uuid4().hex.upper()
        pid = "my-" + uid if user["admin"] else f"my-{user['id']}-{uid}"
        preset = {"id": pid, "uuid": "", "name": name, "display_name": name, "look": group, "variant": name, "group": group,
                  "pack": PACK, "description": about, "process_version": "11.0", "camera_profile": "",
                  "settings": settings, "curves": curves, "has_masks": False, "masks": [], "source_file": "",
                  "owner": user["id"], "made": True, "learned": True}
        store.save_preset(preset, write_xmp(name, group, settings, curves, uid), user["id"])
        renderer.add_uploaded(preset)
        return preset

    @r.post("/api/style/learn")
    def learn(files: list[UploadFile] = File(...), scenes: str = Form(default=""), replace: str = Form(default="1"),
              user: dict = Depends(signed_in)):
        """Read every edit in the uploaded files, group them into styles, and save one preset per style."""
        if user.get("viewing_as"):
            raise HTTPException(403, "Not while viewing someone else's account.")
        total, edits, previews = 0, [], {}
        for f in files[:4000]:
            data = f.file.read(MAX_UPLOAD + 1)
            total += len(data)
            if total > MAX_UPLOAD:
                raise HTTPException(413, "That is more than 60 MB. Send the .xmp files without the photos, or fewer at a time.")
            edits += read_edits(f.filename or "file", data, previews)
            if len(edits) >= MAX_EDITS:
                edits = edits[:MAX_EDITS]
                break
        if len(edits) < 3:
            raise HTTPException(422, "At least 3 edited photos are needed. Send their Lightroom .xmp sidecar files "
                                     "(Metadata › Save Metadata to File in Lightroom), or a CSV/JSON of develop settings."
                                + (f" Found {len(edits)}." if edits else ""))
        mine = sum(1 for p in store.uploaded_presets() if p.get("owner") == user["id"] and not p.get("learned"))
        if not user["admin"] and mine + 8 > limits.PRESETS_PER_ACCOUNT:
            raise HTTPException(403, f"You have {limits.PRESETS_PER_ACCOUNT:,} presets, the most one account can keep.")
        if replace != "0":                                        # the last "Learn my style" is replaced
            for p in store.uploaded_presets():
                if p.get("owner") == user["id"] and p.get("learned"):
                    renderer.remove_uploaded(p["id"])
                    store.delete_preset(p["id"])
        made = []
        groups = _style.learn(edits)
        whole_settings, whole_curves = _style.average_edit(edits)
        about = summary_words(whole_settings, whole_curves, edits)
        for i, g in enumerate(groups, 1):
            name = f"My style {i}" if len(groups) > 1 else "My style"
            line = summary_words(g["settings"], g["curves"], [edits[j] for j in g["members"]])
            p = save_preset(name, PACK, g["settings"], g["curves"], user, f"Learned from {len(g['members'])} of your edits. {line}")
            made.append({"id": p["id"], "name": name, "photos": len(g["members"]), "share": round(g["share"], 2), "about": line,
                         "examples": [edits[j]["name"] for j in g["members"][:5]]})
        by_scene = {}
        if scenes and scenes != "0" and previews:
            import cv2
            import numpy as np
            matched = 0
            for e in edits:
                stem = PurePosixPath(e["name"]).stem.lower()
                data = previews.get(stem)
                median = warmth = None
                if data is not None:
                    bgr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_REDUCED_COLOR_8)
                    if bgr is not None:
                        median, warmth = _style.preview_facts(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                        matched += 1
                if data is None and e.get("iso") is None:
                    continue
                by_scene.setdefault(_style.scene_of(e.get("iso"), median, warmth, e.get("kelvin")), []).append(e)
            for scene in _style.SCENES:
                members = by_scene.get(scene) or []
                if len(members) < _style.MIN_PER_GROUP:
                    continue
                s, c = _style.average_edit(members)
                line = summary_words(s, c, members)
                name = f"My style · {scene}"
                p = save_preset(name, "My style by light", s, c, user, f"Learned from {len(members)} edits shot in {scene}. {line}")
                made.append({"id": p["id"], "name": name, "photos": len(members), "scene": scene, "about": line,
                             "share": round(len(members) / len(edits), 2), "examples": [m["name"] for m in members[:5]]})
        return {"presets": made, "edits": len(edits), "previews": len(previews), "summary": about,
                "scenes": {k: len(v) for k, v in by_scene.items()}}

    return r
