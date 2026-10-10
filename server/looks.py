"""Turning a "look" (preset, auto toggles, manual sliders) into a rendered preview.

A look is what the app stores for an event folder, or for one photo that has
its own settings:

    {"mode": "preset" | "preset_auto" | "auto",
     "preset": preset id or None, "amount": 0..2,
     "toggles": ["exposure", ...],          # used in mode "preset_auto"
     "ev": -5..5, "temp": -100..100, "tint": -100..100,       # your own adjustment, added on top
     "hl", "sh", "wh", "bl": -100..100, "noise": -100..100, "angle": -15..15}

     "masks": {"face_skin": {"texture": -30, "clarity": -10}, "sky": {...}}}   # only part of the photo

Mask kinds are Lightroom's own (subject, background, face_skin, body_skin, hair,
clothes, eyes, sky); each takes ev, contrast, hl, sh, texture, clarity, sat,
temp and tint.

Every "own adjustment" is added to whatever sets that slider: the preset's value,
or the automatic value when its toggle is on.

Rule: a switched-on toggle is set automatically per photo and the preset's own
value for it is skipped; everything else comes from the preset. Auto Correct
uses no preset and switches every toggle on.
"""
from __future__ import annotations

import json
import threading
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np

from engine import render as _r
from engine.auto import TOGGLES, auto_correct, combine
from engine.presets import effective_settings, local_corrections

from . import adjustments
from engine.render import render

cv2.setNumThreads(2)

MODES = ("preset", "preset_auto", "auto")
SIZES = (360, 800, 1600, 2560)
PRESETS_FILE = Path(__file__).resolve().parent.parent / "data" / "presets.json"
# toggles whose result is visible in the app's preview; the rest are applied in Lightroom
PREVIEWED = ("exposure", "highlights", "shadows", "whites", "blacks", "white_balance")


# own adjustment -> (Lightroom setting it is added to, lowest, highest value of that setting)
ADJUST = {"hl": ("Highlights2012", -100, 100), "sh": ("Shadows2012", -100, 100), "wh": ("Whites2012", -100, 100),
          "bl": ("Blacks2012", -100, 100), "noise": ("LuminanceSmoothing", 0, 100), "angle": ("CropAngle", -45, 45)}


# the parts of a photo the app's own Masks section offers
MASK_KINDS = ("subject", "background", "face_skin", "body_skin", "hair", "clothes", "eyes", "sky")
MY_PACK = "My presets"                    # presets uploaded from Lightroom
# what a mask can change -> (lowest, highest)
MASK_ADJUST = {"ev": (-2.0, 2.0), "contrast": (-100, 100), "hl": (-100, 100), "sh": (-100, 100),
               "texture": (-100, 100), "clarity": (-100, 100), "sat": (-100, 100), "temp": (-100, 100),
               "tint": (-100, 100)}


def default_look() -> dict:
    return {"mode": "preset", "preset": None, "amount": 1.0, "toggles": [], "ev": 0.0, "temp": 0.0, "tint": 0.0,
            "hl": 0.0, "sh": 0.0, "wh": 0.0, "bl": 0.0, "noise": 0.0, "angle": 0.0, "masks": {},
            "preset_masks": True, "adj": {}}


def _number(value, lo, hi, default=0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:
        return default
    return round(min(hi, max(lo, v)), 3)


def clean_look(raw, preset_ids=None) -> dict:
    """A look with every field present and in range, whatever was sent."""
    raw = raw if isinstance(raw, dict) else {}
    look = default_look()
    if raw.get("mode") in MODES:
        look["mode"] = raw["mode"]
    preset = raw.get("preset")
    if isinstance(preset, str) and preset and (preset_ids is None or preset in preset_ids):
        look["preset"] = preset
    toggles = raw.get("toggles")
    if isinstance(toggles, (list, tuple)):
        look["toggles"] = [t for t in TOGGLES if t in toggles]
    look["amount"] = _number(raw.get("amount", 1.0), 0.0, 2.0, 1.0)
    look["ev"] = _number(raw.get("ev"), -5.0, 5.0)
    look["temp"] = _number(raw.get("temp"), -100.0, 100.0)
    look["tint"] = _number(raw.get("tint"), -100.0, 100.0)
    for key in ("hl", "sh", "wh", "bl", "noise"):
        look[key] = _number(raw.get(key), -100.0, 100.0)
    look["angle"] = _number(raw.get("angle"), -15.0, 15.0)
    look["adj"] = adjustments.clean_adj(raw.get("adj"))                     # every other Lightroom slider
    look["preset_masks"] = raw.get("preset_masks", True) is not False       # use the masks inside the preset
    masks = raw.get("masks") if isinstance(raw.get("masks"), dict) else {}
    for kind in MASK_KINDS:                              # keep only masks that change something
        given = masks.get(kind) if isinstance(masks.get(kind), dict) else {}
        kept = {k: _number(given.get(k), lo, hi) for k, (lo, hi) in MASK_ADJUST.items()}
        kept = {k: v for k, v in kept.items() if v}
        if kept:
            look["masks"][kind] = kept
    if look["mode"] == "auto":
        look["preset"], look["toggles"] = None, list(TOGGLES)
    elif look["mode"] == "preset":
        look["toggles"] = []
    return look


def effective_look(photo_settings, folder_settings) -> dict:
    """The look a photo is shown with: its own if it has one, otherwise its folder's."""
    return clean_look(photo_settings if photo_settings else folder_settings)


def straighten(img: np.ndarray, angle: float) -> np.ndarray:
    """Turn the picture by a few degrees and zoom just enough that no empty corners show."""
    if abs(angle) < 0.05:
        return img
    h, w = img.shape[:2]
    a = np.radians(abs(angle))
    zoom = float(np.cos(a) + max(w / h, h / w) * np.sin(a))
    m = cv2.getRotationMatrix2D((w / 2, h / 2), -angle, zoom)      # positive angle turns the picture clockwise
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def _detail(img: np.ndarray, texture: float, clarity: float) -> np.ndarray:
    """Texture (fine detail) and clarity (wider, mid-tone contrast) for a masked area.

    Uses an edge-keeping blur, so smoothing skin does not smear across the edge of
    the face or leave coloured halos. The strengths are set by eye, not yet
    measured against Lightroom."""
    if not texture and not clarity:
        return img
    y = np.ascontiguousarray(_r._luma(np.clip(img, 0, 1)), np.float32)
    span = float(np.hypot(*y.shape))

    def smooth(radius_frac, eps):                           # guided filter: blurs flat areas, keeps real edges
        k = 2 * max(2, int(radius_frac * span)) + 1
        mean = cv2.blur(y, (k, k))
        var = cv2.blur(y * y, (k, k)) - mean * mean
        gain = var / (var + eps)
        return cv2.blur(gain, (k, k)) * y + cv2.blur(mean - gain * mean, (k, k))

    change = np.zeros_like(y)
    if texture:
        change += texture / 100.0 * 1.0 * (y - smooth(0.004, 0.004))
    if clarity:
        change += clarity / 100.0 * 0.7 * (y - smooth(0.02, 0.02)) * (4 * y * (1 - y))
    return np.clip(img + change[..., None], 0, 1)          # brightness only: colour stays where it was


def _apply(img: np.ndarray, a: dict, curve=None) -> np.ndarray:
    """One set of local adjustments on the whole picture (the mask is laid over it afterwards)."""
    P = _r.DEFAULT_PARAMS
    part = _r.white_balance(img, a.get("temp", 0), a.get("tint", 0), P)
    part = _r.exposure(part, a.get("ev", 0), P)
    part = _r.tone(part, {"Contrast2012": a.get("contrast", 0), "Highlights2012": a.get("hl", 0),
                          "Shadows2012": a.get("sh", 0), "Whites2012": a.get("wh", 0), "Blacks2012": a.get("bl", 0)}, P)
    part = _detail(part, a.get("texture", 0), a.get("clarity", 0))
    if curve:
        part = _r.point_curves(part, {"ToneCurvePV2012": curve})
    part = _r.vibrance_saturation(part, {"Saturation": a.get("sat", 0)}, P)
    return np.clip(part, 0, 1)


def correction_mask(masks: dict, parts: list) -> np.ndarray | None:
    """One mask from a preset's parts: added parts joined, "subtract" parts taken away."""
    total = None
    for part in parts:
        m = masks.get(part["kind"])
        if m is None:
            return None
        m = (1 - m if part.get("inverted") else m) * float(part.get("value", 1))
        if part.get("subtract"):
            total = (total if total is not None else np.ones_like(m)) * (1 - m)
        else:
            total = m if total is None else np.maximum(total, m)
    return total


def local_adjust(img: np.ndarray, masks: dict, adjustments: dict, corrections: list | None = None) -> np.ndarray:
    """Apply local adjustments to their part of the picture only: first the masks
    that came inside the preset (corrections), then the app's own (adjustments)."""
    h, w = img.shape[:2]
    jobs = []
    for c in corrections or []:
        if c.get("shown") and (c.get("adjust") or c.get("curve")):
            jobs.append((correction_mask(masks, c["parts"]), c["adjust"], c.get("curve"), c.get("amount", 1.0)))
    for kind, a in adjustments.items():
        jobs.append((masks.get(kind), a, None, 1.0))
    out = img
    for m, a, curve, amount in jobs:
        if m is None or float(m.max()) < 0.02:
            continue
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)[..., None] * float(amount)
        out = out + (_apply(out, a, curve) - out) * m
    return out


def show_mask(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Tint the masked part red, the way Lightroom shows a mask."""
    m = cv2.resize(mask, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_LINEAR)[..., None] * 0.55
    return img * (1 - m) + np.array([1.0, 0.1, 0.1], np.float32) * m


class _Cache:
    def __init__(self, size):
        self.size, self.items, self.lock = size, OrderedDict(), threading.Lock()

    def get(self, key, make):
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key)
                return self.items[key]
        value = make()
        with self.lock:
            self.items[key] = value
            while len(self.items) > self.size:
                self.items.popitem(last=False)
        return value


class Renderer:
    def __init__(self, presets_file: str | Path = PRESETS_FILE):
        data = json.loads(Path(presets_file).read_text(encoding="utf-8"))
        self.presets = {p["id"]: p for p in data["presets"]}
        self.library = set(self.presets)                 # the presets that ship with the app
        self._images = _Cache(24)
        self._autos = _Cache(4000)
        self._masks = _Cache(12)
        self._mask_lock = threading.Lock()

    def add_uploaded(self, preset: dict):
        self.presets[preset["id"]] = preset

    def remove_uploaded(self, preset_id: str) -> bool:
        if preset_id in self.library or preset_id not in self.presets:
            return False
        del self.presets[preset_id]
        return True

    def catalogue(self) -> list[dict]:
        """The presets as the app's picker needs them (no slider values)."""
        out = []
        for p in self.presets.values():
            mine = p["id"] not in self.library
            out.append({"id": p["id"], "name": p["display_name"], "look": p["look"], "variant": p["variant"],
                        "pack": p["pack"], "about": p["description"], "mine": mine,
                        "s": {k: v for k, v in p["settings"].items()
                              if k in adjustments.SLIDERS and isinstance(v, (int, float)) and not isinstance(v, bool)
                              and v != adjustments.neutral(k)},
                        "masks": [{"name": c["name"], "parts": [x["kind"] for x in c["parts"]], "adjust": c["adjust"],
                                   "curve": bool(c["curve"]), "shown": c["shown"]} for c in local_corrections(p)]})
        return out

    def warm_up(self):
        """Render once at the largest viewing size, so the first real photo is not slow."""
        y, x = np.mgrid[0:1067, 0:1600].astype(np.float32)
        img = np.stack([x / 1600, y / 1067, (x + y) / 2667], -1)
        preset = next(iter(self.presets.values()))
        for _ in range(2):
            self._jpeg(render(img, preset["settings"], preset["curves"], {"temp": 5, "tint": 5, "exposure": 0.1}))
        auto_correct(img[::2, ::2], TOGGLES, None, 800)

    # ---- images ---------------------------------------------------------
    def _load(self, path: Path, size: int) -> np.ndarray:
        def make():
            flag = cv2.IMREAD_COLOR
            if size <= 320:
                flag = cv2.IMREAD_REDUCED_COLOR_4
            elif size <= 1280:
                flag = cv2.IMREAD_REDUCED_COLOR_2
            bgr = cv2.imread(str(path), flag)
            if bgr is None:
                raise FileNotFoundError(path)
            h, w = bgr.shape[:2]
            scale = size / max(h, w)
            if scale < 1:
                bgr = cv2.resize(bgr, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
            return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        return self._images.get((str(path), path.stat().st_mtime_ns, size), make)

    @staticmethod
    def _jpeg(img: np.ndarray, quality=88) -> bytes:
        bgr = cv2.cvtColor((np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8), cv2.COLOR_RGB2BGR)
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if not ok:
            raise RuntimeError("could not write the preview")
        return buf.tobytes()

    # ---- masks ----------------------------------------------------------
    def masks(self, path: Path) -> dict:
        """The photo's masks. Made the first time they are needed, then kept beside the preview."""
        stamp = path.stat().st_mtime_ns

        def make():
            from engine import masks as maker
            kept = path.with_suffix(".masks.npz")
            if kept.exists():
                try:
                    with np.load(kept) as z:
                        if int(z["stamp"]) == stamp:
                            return {k: z[k].astype(np.float32) / 255 for k in maker.KINDS}
                except Exception:
                    pass                                     # unreadable or from an older photo: make it again
            with self._mask_lock:                            # the mask models take one photo at a time
                made = maker.compute(self._load(path, 1600))
            part = kept.with_suffix(f".{threading.get_ident()}.part.npz")
            np.savez_compressed(part, stamp=np.int64(stamp), **{k: (v * 255 + 0.5).astype(np.uint8) for k, v in made.items()})
            part.replace(kept)
            return made
        return self._masks.get((str(path), stamp), make)

    # ---- looks ----------------------------------------------------------
    def auto_values(self, path: Path, toggles, iso=None) -> dict:
        """The automatic values for one photo. Measured on a fixed-size copy, so
        the result does not depend on the size being viewed."""
        toggles = tuple(t for t in TOGGLES if t in toggles)
        if not toggles:
            return {"settings": {}, "manual": {}, "info": {}}
        key = (str(path), path.stat().st_mtime_ns, toggles, iso)
        return self._autos.get(key, lambda: auto_correct(self._load(path, 800), toggles, None, iso))

    def develop(self, path: Path, look: dict, iso=None) -> dict:
        """Everything the look sets for this photo: Lightroom settings, point
        curves, preview white balance and extra exposure."""
        look = clean_look(look, self.presets)
        preset = self.presets.get(look["preset"]) if look["preset"] else None
        settings, curves = effective_settings(preset, set(look["toggles"])) if preset else ({}, {})
        auto = self.auto_values(path, look["toggles"], iso)
        settings, manual = combine(settings, auto)
        for key, (name, lo, hi) in ADJUST.items():
            if look[key]:
                settings[name] = min(hi, max(lo, float(settings.get(name) or 0) + look[key]))
        adjustments.add(settings, look["adj"])
        manual = {"temp": float(manual.get("temp", 0.0)) + look["temp"],
                  "tint": float(manual.get("tint", 0.0)) + look["tint"], "exposure": look["ev"]}
        corrections = local_corrections(preset) if preset and look["preset_masks"] else []
        return {"settings": settings, "curves": curves, "manual": manual, "auto": auto["settings"],
                "masks": look["masks"], "corrections": corrections, "angle": float(settings.get("CropAngle") or 0.0),
                "auto_wb": auto["manual"], "amount": look["amount"], "preset": preset["display_name"] if preset else None}

    def base(self, path: Path, size: int) -> bytes:
        return self._jpeg(self._load(path, size))

    def render(self, path: Path, look: dict, size: int, iso=None, show: str = "") -> bytes:
        """The photo with the look applied. show names a mask to tint red on top."""
        d = self.develop(path, look, iso)
        img = self._load(path, size)
        out = render(img, d["settings"], d["curves"], d["manual"], amount=d["amount"])
        texture = d["settings"].get("Texture")
        if isinstance(texture, (int, float)) and texture:        # the engine's own texture is too weak to see
            out = _detail(out, float(texture) * d["amount"], 0)
        if d["masks"] or d["corrections"] or show:
            masks = self.masks(path)
            out = local_adjust(out, masks, d["masks"], d["corrections"])
            if show in masks:
                out = show_mask(out, masks[show])
        return self._jpeg(straighten(out, d["angle"]))


# ---- handing the edits to Lightroom ------------------------------------------
# Measured on Lightroom exports: one "stop" of the app's warmth equals this many mired in
# Lightroom's Temperature, and Lightroom's Tint moves this much per unit of the app's tint.
MIRED_PER_STOP = 156.0
LR_TINT_PER_UNIT = 0.30
_WB_KEYS = ("WhiteBalance", "Temperature", "Tint", "IncrementalTemperature", "IncrementalTint")


def lightroom_edit(renderer: "Renderer", path: Path, look: dict, iso=None) -> dict | None:
    """What Lightroom should do to one photo for this look, or None when the look changes nothing.

    Two ways to apply a preset:
      * a preset uploaded from Lightroom with AI masks is applied by Lightroom itself
        ("preset_uuid"), so Lightroom makes its own masks; "settings" then holds only
        what differs from the preset (automatic values, your own changes);
      * otherwise "settings" is everything, laid over a no-edit starting point.
    White balance changes travel as a shift ("wb"), because only Lightroom knows the
    photo's as-shot Kelvin value.
    """
    look = clean_look(look, renderer.presets)
    d = renderer.develop(path, look, iso)
    preset = renderer.presets.get(look["preset"]) if look["preset"] else None
    settings = dict(d["settings"])
    if d["manual"].get("exposure"):
        settings["Exposure2012"] = round(float(settings.get("Exposure2012") or 0) + d["manual"]["exposure"], 3)
    not_yet = []
    if abs(float(settings.pop("CropAngle", 0) or 0)) >= 0.05 or "straighten" in look["toggles"]:
        not_yet.append("straighten")
    settings.pop("PerspectiveUpright", None), settings.pop("UprightVersion", None)
    if look["masks"]:
        not_yet.append("your own masks")
    temp, tint = float(d["manual"].get("temp") or 0), float(d["manual"].get("tint") or 0)
    wb = None
    if temp or tint:
        stops = temp / 100.0 * _r.DEFAULT_PARAMS["wb_temp"]
        wb = {"mired": round(-MIRED_PER_STOP * stops, 2), "tint": round(LR_TINT_PER_UNIT * tint, 2),
              "relative_temp": round(temp, 1), "relative_tint": round(tint, 1)}

    by_lightroom = bool(preset and preset["id"] not in renderer.library and preset.get("masks") and look["preset_masks"])
    if by_lightroom:
        own = preset["settings"]
        settings = {k: v for k, v in settings.items() if own.get(k) != v}
        if "white_balance" in look["toggles"] and any(k in own for k in _WB_KEYS):
            settings["WhiteBalance"] = "As Shot"          # the toggle skips the preset's own white balance
    curves = {} if by_lightroom else {k: [v for point in pts for v in point] for k, pts in d["curves"].items()}
    if preset and preset.get("masks") and not by_lightroom:
        not_yet.append("the preset's masks")
    if not (preset or settings or wb or not_yet):
        return None
    return {"preset": preset["display_name"] if preset else None,
            "preset_uuid": preset.get("uuid") if by_lightroom else None,
            "from_no_edit": not by_lightroom, "settings": settings, "curves": curves, "wb": wb, "not_yet": not_yet}
