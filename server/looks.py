"""Turning a "look" (preset, auto toggles, manual sliders) into a rendered preview.

A look is what the app stores for an event folder, or for one photo that has
its own settings:

    {"mode": "preset" | "preset_auto" | "auto",
     "preset": preset id or None, "amount": 0..2,
     "toggles": ["exposure", ...],          # used in mode "preset_auto"
     "ev": -5..5, "temp": -100..100, "tint": -100..100}

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

from engine.auto import TOGGLES, auto_correct, combine
from engine.presets import effective_settings
from engine.render import render

cv2.setNumThreads(2)

MODES = ("preset", "preset_auto", "auto")
SIZES = (360, 800, 1600, 2560)
PRESETS_FILE = Path(__file__).resolve().parent.parent / "data" / "presets.json"
# toggles whose result is visible in the app's preview; the rest are applied in Lightroom
PREVIEWED = ("exposure", "highlights", "shadows", "whites", "blacks", "white_balance")


def default_look() -> dict:
    return {"mode": "preset", "preset": None, "amount": 1.0, "toggles": [], "ev": 0.0, "temp": 0.0, "tint": 0.0}


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
    if look["mode"] == "auto":
        look["preset"], look["toggles"] = None, list(TOGGLES)
    elif look["mode"] == "preset":
        look["toggles"] = []
    return look


def effective_look(photo_settings, folder_settings) -> dict:
    """The look a photo is shown with: its own if it has one, otherwise its folder's."""
    return clean_look(photo_settings if photo_settings else folder_settings)


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
        self._images = _Cache(24)
        self._autos = _Cache(4000)

    def catalogue(self) -> list[dict]:
        """The presets as the app's picker needs them (no slider values)."""
        return [{"id": p["id"], "name": p["display_name"], "look": p["look"], "variant": p["variant"],
                 "pack": p["pack"], "about": p["description"], "masks": p["has_masks"]}
                for p in self.presets.values()]

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
        manual = {"temp": float(manual.get("temp", 0.0)) + look["temp"],
                  "tint": float(manual.get("tint", 0.0)) + look["tint"], "exposure": look["ev"]}
        return {"settings": settings, "curves": curves, "manual": manual, "auto": auto["settings"],
                "auto_wb": auto["manual"], "amount": look["amount"], "preset": preset["display_name"] if preset else None}

    def base(self, path: Path, size: int) -> bytes:
        return self._jpeg(self._load(path, size))

    def render(self, path: Path, look: dict, size: int, iso=None) -> bytes:
        d = self.develop(path, look, iso)
        img = self._load(path, size)
        out = render(img, d["settings"], d["curves"], d["manual"], amount=d["amount"])
        return self._jpeg(out)
