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
import re
import threading
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np

from engine import render as _r
from engine import crop as _crop
from engine import shapes as _shapes
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
            "preset_masks": True, "adj": {}, "crop": None, "shapes": []}


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
    look["angle"] = _number(raw.get("angle"), -45.0, 45.0)
    look["adj"] = adjustments.clean_adj(raw.get("adj"))                     # every other Lightroom slider
    look["preset_masks"] = raw.get("preset_masks", True) is not False       # use the masks inside the preset
    masks = raw.get("masks") if isinstance(raw.get("masks"), dict) else {}
    for kind in MASK_KINDS:                              # keep only masks that change something
        given = masks.get(kind) if isinstance(masks.get(kind), dict) else {}
        kept = {k: _number(given.get(k), lo, hi) for k, (lo, hi) in MASK_ADJUST.items()}
        kept = {k: v for k, v in kept.items() if v}
        if kept:
            look["masks"][kind] = kept
    look["crop"] = _crop.clean(raw.get("crop"))          # per photo; never changes the photo itself
    look["shapes"] = _shapes.clean(raw.get("shapes"), MASK_ADJUST)   # radial, linear and brush masks you draw
    if look["mode"] == "auto":
        look["preset"], look["toggles"] = None, list(TOGGLES)
    elif look["mode"] == "preset":
        look["toggles"] = []
    return look


def effective_look(photo_settings, folder_settings, crop=None) -> dict:
    """The look a photo is shown with: its own if it has one, otherwise its folder's, with the
    photo's own crop (kept apart from the look, so cropping one photo does not detach it from its folder)."""
    look = clean_look(photo_settings if photo_settings else folder_settings)
    look["crop"] = _crop.clean(crop)
    return look


def straighten(img: np.ndarray, angle: float) -> np.ndarray:
    """Turn the picture by a few degrees and zoom just enough that no empty corners show."""
    if abs(angle) < 0.05:
        return img
    h, w = img.shape[:2]
    a = np.radians(abs(angle))
    zoom = float(np.cos(a) + max(w / h, h / w) * np.sin(a))
    m = cv2.getRotationMatrix2D((w / 2, h / 2), -angle, zoom)      # positive angle turns the picture clockwise
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def _detail(img: np.ndarray, texture: float, clarity: float, span: float | None = None) -> np.ndarray:
    """Texture (fine detail) and clarity (wider, mid-tone contrast) for a masked area.

    Uses an edge-keeping blur, so smoothing skin does not smear across the edge of
    the face or leave coloured halos. The strengths are set by eye, not yet
    measured against Lightroom."""
    if not texture and not clarity:
        return img
    y = np.ascontiguousarray(_r._luma(np.clip(img, 0, 1)), np.float32)
    span = span or float(np.hypot(*y.shape))             # for a cut-out piece, the size of the whole picture

    def smooth(radius_frac, eps):                           # guided filter: blurs flat areas, keeps real edges
        k = 2 * max(2, int(radius_frac * span)) + 1
        mean = cv2.blur(y, (k, k))
        var = cv2.blur(y * y, (k, k)) - mean * mean
        gain = var / (var + eps)
        return cv2.blur(gain, (k, k)) * y + cv2.blur(mean - gain * mean, (k, k))

    change = np.zeros_like(y)
    if texture:
        change += texture / 100.0 * 2.6 * (y - smooth(0.004, 0.004))      # was 1.0: barely visible
    if clarity:
        change += clarity / 100.0 * 0.7 * (y - smooth(0.02, 0.02)) * (4 * y * (1 - y))
    return np.clip(img + change[..., None], 0, 1)          # brightness only: colour stays where it was


def _dehaze(img: np.ndarray, amount: float) -> np.ndarray:
    """Dehaze (-100..100): plus takes away the grey veil of haze (darker, deeper colour where the
    picture is washed out), minus adds a soft haze. Works on the darkest channel, smoothed, as an
    estimate of how much haze lies over each area."""
    if not amount:
        return img
    h, w = img.shape[:2]
    k = 2 * max(3, int(0.02 * max(h, w))) + 1
    dark = cv2.blur(cv2.erode(img.min(axis=2), np.ones((5, 5), np.uint8)), (k, k))
    if amount > 0:
        haze = np.clip(dark * (amount / 100.0) * 0.75, 0, 0.85)[..., None]
        out = (img - haze * 0.95) / (1 - haze * 0.95)
        grey = _r._luma(np.clip(out, 0, 1))[..., None]
        out = grey + (out - grey) * (1 + 0.25 * amount / 100.0)         # a little colour back, as Lightroom does
    else:
        veil = (-amount / 100.0) * 0.55
        out = img + (0.9 - img) * veil * (0.4 + 0.6 * dark[..., None])
    return np.clip(out, 0, 1)


def _sharpen(img: np.ndarray, settings: dict, amount_scale: float = 1.0) -> np.ndarray:
    """Lightroom's Sharpening: Amount, Radius, Detail and Masking. The previews and exports start
    from the camera's own JPEG, which is already sharpened about as much as Lightroom's default
    (Amount 40), so only what is above or below that default is added."""
    def num(key, default):
        v = settings.get(key)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default
    extra = (num("Sharpness", 40.0) - 40.0) * amount_scale
    if abs(extra) < 0.5:
        return img
    radius, detail, masking = num("SharpenRadius", 1.0), num("SharpenDetail", 25.0), num("SharpenEdgeMasking", 0.0)
    y = np.ascontiguousarray(_r._luma(np.clip(img, 0, 1)), np.float32)
    high = y - cv2.GaussianBlur(y, (0, 0), max(0.3, radius))
    limit = 0.03 + 0.25 * detail / 100.0                 # low Detail keeps only the larger edges (fewer halos)
    high = limit * np.tanh(high / limit)
    gain = extra / 100.0 * 2.4
    if extra > 0 and masking > 0:                        # Masking: sharpen edges only, leave smooth skin and sky
        g = cv2.GaussianBlur(y, (0, 0), 1.5)
        edge = np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3))
        t = masking / 100.0 * 0.35
        high *= np.clip((edge - t * 0.5) / max(1e-4, t * 0.5), 0, 1)
    return np.clip(img + (gain * high)[..., None], 0, 1)


def finish_detail(img: np.ndarray, settings: dict, amount: float = 1.0) -> np.ndarray:
    """Dehaze and sharpening for previews and exports (the engine's render leaves them to Lightroom)."""
    d = settings.get("Dehaze")
    if isinstance(d, (int, float)) and not isinstance(d, bool) and d:
        img = _dehaze(img, float(d) * amount)
    return _sharpen(img, settings, amount)


def _apply(img: np.ndarray, a: dict, curve=None, span: float | None = None) -> np.ndarray:
    """One set of local adjustments on the whole picture (the mask is laid over it afterwards)."""
    P = _r.DEFAULT_PARAMS
    part = _r.white_balance(img, a.get("temp", 0), a.get("tint", 0), P)
    part = _r.exposure(part, a.get("ev", 0), P)
    part = _r.tone(part, {"Contrast2012": a.get("contrast", 0), "Highlights2012": a.get("hl", 0),
                          "Shadows2012": a.get("sh", 0), "Whites2012": a.get("wh", 0), "Blacks2012": a.get("bl", 0)}, P)
    part = _detail(part, a.get("texture", 0), a.get("clarity", 0), span)
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
    span = float(np.hypot(h, w))
    for m, a, curve, amount in jobs:
        if m is None or float(m.max()) < 0.02:
            continue
        # Work only on the part of the picture the mask covers (a face is a small part of a
        # photo), with room around it so clarity still sees past the mask's edge.
        ys, xs = np.where(m > 0.01)
        mh, mw = m.shape
        pad = max(2, int(0.03 * max(mh, mw)))
        y0, y1 = max(0, ys.min() - pad), min(mh, ys.max() + 1 + pad)
        x0, x1 = max(0, xs.min() - pad), min(mw, xs.max() + 1 + pad)
        Y0, Y1, X0, X1 = y0 * h // mh, min(h, -(-y1 * h // mh)), x0 * w // mw, min(w, -(-x1 * w // mw))
        if out is img:
            out = img.copy()
        part = out[Y0:Y1, X0:X1]
        cover = cv2.resize(m[y0:y1, x0:x1], (X1 - X0, Y1 - Y0), interpolation=cv2.INTER_LINEAR)[..., None] * float(amount)
        out[Y0:Y1, X0:X1] = part + (_apply(part, a, curve, span) - part) * cover
    return out


def apply_shapes8(img8: np.ndarray, shapes: list, show: str = "") -> np.ndarray:
    """The masks you drew (radial, linear, brush), each with its own changes, on an 8-bit picture
    that is straightened but not yet cropped (the frame the shapes were drawn on).
    show = "shape<n>" tints that shape's mask red."""
    h, w = img8.shape[:2]
    out = img8
    for i, shape in enumerate(shapes):
        want_red = show == f"shape{i}"
        if not shape.get("adj") and not want_red:
            continue
        m = _shapes.mask(shape, h, w)
        if shape.get("adj"):
            out = local_adjust8(out, {"s": m}, {"s": shape["adj"]})
        if want_red:
            out = (np.clip(show_mask(out.astype(np.float32) / 255, m), 0, 1) * 255 + 0.5).astype(np.uint8)
    return out


def local_adjust8(img8: np.ndarray, masks: dict, adjustments: dict, corrections: list | None = None) -> np.ndarray:
    """local_adjust for a full-size 8-bit picture: each mask's change is worked out only
    on the part of the picture the mask covers, which keeps large exports quick."""
    h, w = img8.shape[:2]
    span = float(np.hypot(h, w))
    jobs = []
    for c in corrections or []:
        if c.get("shown") and (c.get("adjust") or c.get("curve")):
            jobs.append((correction_mask(masks, c["parts"]), c["adjust"], c.get("curve"), c.get("amount", 1.0)))
    for kind, a in adjustments.items():
        jobs.append((masks.get(kind), a, None, 1.0))
    out = img8
    for m, a, curve, amount in jobs:
        if m is None or float(m.max()) < 0.02:
            continue
        ys, xs = np.where(m > 0.01)
        mh, mw = m.shape
        pad = max(2, int(0.03 * max(mh, mw)))                       # room for clarity to see past the mask's edge
        y0, y1 = max(0, ys.min() - pad), min(mh, ys.max() + 1 + pad)
        x0, x1 = max(0, xs.min() - pad), min(mw, xs.max() + 1 + pad)
        Y0, Y1, X0, X1 = y0 * h // mh, min(h, -(-y1 * h // mh)), x0 * w // mw, min(w, -(-x1 * w // mw))
        if out is img8:
            out = img8.copy()
        part = out[Y0:Y1, X0:X1].astype(np.float32) / 255
        cover = cv2.resize(m[y0:y1, x0:x1], (X1 - X0, Y1 - Y0), interpolation=cv2.INTER_LINEAR)[..., None] * float(amount)
        part = part + (_apply(part, a, curve, span) - part) * cover
        out[Y0:Y1, X0:X1] = (np.clip(part, 0, 1) * 255 + 0.5).astype(np.uint8)
    return out


def watermark8(img8: np.ndarray, text: str) -> np.ndarray:
    """The text in the bottom right corner, white with a soft dark edge so it reads on any picture."""
    text = (text or "").strip()
    if not text:
        return img8
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    h, w = img8.shape[:2]
    size = max(14, round(0.022 * max(h, w)))
    font = ImageFont.load_default(size)
    layer = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(layer)
    box = draw.textbbox((0, 0), text, font=font)
    margin = round(size * 1.1)
    at = (w - (box[2] - box[0]) - margin - box[0], h - (box[3] - box[1]) - margin - box[1])
    draw.text(at, text, font=font, fill=255)
    y0, x0 = max(0, at[1] - size), max(0, at[0] - size)
    letters = np.asarray(layer)[y0:, x0:].astype(np.float32) / 255
    shadow = np.asarray(layer.crop((x0, y0, w, h)).filter(ImageFilter.GaussianBlur(size * 0.12))).astype(np.float32) / 255
    out = img8.copy()
    part = out[y0:, x0:].astype(np.float32)
    part *= (1 - 0.45 * np.clip(shadow * 1.6, 0, 1))[..., None]
    part += (255 - part) * (0.82 * letters)[..., None]
    out[y0:, x0:] = np.clip(part + 0.5, 0, 255).astype(np.uint8)
    return out


_MARKS: dict = {}


def preview_mark(img: np.ndarray, text: str = "AUTO EDITS  ·  PREVIEW") -> np.ndarray:
    """A faint, small mark for previews before export: a few short lines across the picture, light
    enough to judge skin and colour through, still there if the picture is copied. Works on a 0..1
    float image. (Previews for photographers are also capped at 1600 px: server/main.py.)"""
    h, w = img.shape[:2]
    key = (h, w, text)
    m = _MARKS.get(key)
    if m is None:
        from PIL import Image, ImageDraw, ImageFont
        size = max(12, round(0.026 * max(h, w)))
        font = ImageFont.load_default(size)
        side = int((h * h + w * w) ** 0.5) + 2 * size
        layer = Image.new("L", (side, side), 0)
        draw = ImageDraw.Draw(layer)
        box = draw.textbbox((0, 0), text, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        step_y = max(th * 4, side // 5)                 # about four or five lines down the picture
        for row, y in enumerate(range(step_y // 2, side, step_y)):
            x0 = (row % 2) * int(tw * 1.4)
            for x in range(x0, side, int(tw * 2.8)):
                draw.text((x, y), text, font=font, fill=255)
        layer = layer.rotate(28, resample=Image.BILINEAR)
        left, top = (side - w) // 2, (side - h) // 2
        m = np.asarray(layer.crop((left, top, left + w, top + h))).astype(np.float32)[..., None] / 255.0
        if len(_MARKS) > 16:
            _MARKS.clear()
        _MARKS[key] = m
    return img + (1.0 - img) * (MARK_STRENGTH * m)


MARK_STRENGTH = 0.07


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

    def peek(self, key):
        """The kept value, or None, without making it or changing what is kept."""
        with self.lock:
            return self.items.get(key)


# ---- plain words for the preset picker (search and the one-line description) ----------------------
_WORDS = ["haldi", "mehendi", "mehndi", "sangeet", "reception", "baraat", "mandap", "pheras", "wedding", "pre-wedding",
          "bride", "groom", "couple", "portrait", "candid", "night", "evening", "daylight", "sunset", "golden hour",
          "indoor", "outdoor", "street", "landscape", "beach", "temple", "palace", "heritage", "festival", "party",
          "flash", "window light", "studio", "fashion", "travel", "teal", "pastel", "matte", "film", "moody", "dramatic",
          "cinematic", "soft", "muted", "natural", "clean", "rich", "glow", "romantic", "elegant", "vintage"]


def _median(text: str):
    m = re.search(r"median\D{0,4}(\d{2,3})", text)
    return int(m.group(1)) if m else None


def preset_tags(p: dict) -> list[str]:
    """Plain search words for a built-in preset, worked out from its settings and its notes:
    warm, cool, bright, dark, moody, matte, film, vibrant, soft, pastel, black and white, and the
    wedding events and light it suits."""
    s, text = p.get("settings") or {}, (p.get("description") or "").lower()
    look, _, best = text.partition("best for:")             # how it looks / what it suits
    best = re.split(r"\. (?:avoid|not for|use |strong|dark look|very safe)", best)[0]
    num = lambda k: float(s[k]) if isinstance(s.get(k), (int, float)) and not isinstance(s.get(k), bool) else 0.0
    head = look.split(":", 1)[0]
    tags = set()
    sat_m = re.search(r"saturation \(?(0\.\d+)", look)
    sat = float(sat_m.group(1)) if sat_m else None
    if s.get("ConvertToGrayscale") is True or "monochrome" in head or (sat is not None and sat <= 0.1):
        tags |= {"black and white", "monochrome", "bw"}
    warm = look.count("warm") + (1 if 0 < num("ColorGradeMidtoneHue") < 70 and num("ColorGradeMidtoneSat") >= 4 else 0)
    cool = look.count("cool") + look.count("blue") + look.count("teal") + (1 if 160 < num("ColorGradeMidtoneHue") < 260 else 0)
    if warm > cool + 1:
        tags.add("warm")
    elif cool > warm:
        tags.add("cool")
    median = _median(look)
    if median is not None:
        if median >= 115:
            tags |= {"bright", "airy"}
        elif median <= 80:
            tags.add("dark")
    elif "bright" in head or "high-key" in head:
        tags |= {"bright", "airy"}
    elif "dark" in head or "low-key" in head:
        tags.add("dark")
    if any(w in head for w in ("moody", "dramatic", "cinematic", "dark", "noir")) or (median is not None and median <= 60):
        tags.add("moody")
    curve = (p.get("curves") or {}).get("ToneCurvePV2012") or []
    if (curve and curve[0][1] >= 15) or any(w in head for w in ("matte", "faded", "lifted")):
        tags |= {"matte", "faded"}
    if num("GrainAmount") > 0 or any(w in head for w in ("film", "kodachrome", "kodak", "analog")):
        tags.add("film")
    if (sat is not None and sat >= 0.4) or any(w in head for w in ("vivid", "bold", "pop", "saturat", "punch")):
        tags |= {"vibrant", "colourful", "colorful", "punchy"}
    if (sat is not None and sat <= 0.26) or any(w in head for w in ("muted", "restrained", "soft", "pastel")):
        tags |= {"muted", "soft"}
    if "pastel" in look:
        tags |= {"pastel", "soft"}
    for w in _WORDS:
        if w in best or w in head:
            tags.add(w)
    if "mehendi" in tags:
        tags.add("mehndi")
    return sorted(tags)


def plain_about(p: dict) -> str:
    """One plain line instead of the technical notes ("median ~75, p1 ~7..."): the look's own name for
    itself and what it is best for. The full notes stay in the data."""
    text = (p.get("description") or "").strip()
    if not text:
        return ""
    head = text.split(":", 1)[0].strip() if ":" in text.split(".", 1)[0] else ""
    best = ""
    m = re.search(r"Best for:\s*(.+?)(?:\.\s|\.$|$)", text, re.S)
    if m:
        parts = [x.strip() for x in m.group(1).split(",") if x.strip()]
        best = ", ".join(parts[:3])
        best = best[0].lower() + best[1:] if best[:2] != best[:2].upper() else best
    if head and best:
        return f"{head}. Best for {best}."
    if best:
        return f"Best for {best}."
    return head or text.split(".")[0][:140]


class Renderer:
    def __init__(self, presets_file: str | Path = PRESETS_FILE):
        data = json.loads(Path(presets_file).read_text(encoding="utf-8"))
        self.presets = {p["id"]: p for p in data["presets"]}
        self.library = set(self.presets)                 # the presets that ship with the app
        self._images = _Cache(24)
        # Grid and filmstrip pictures have their own, smaller copies, so drawing a folder of
        # thumbnails never pushes the big photo (and its masks) out of memory.
        self._small_images = _Cache(160)
        self._autos = _Cache(4000)
        self._masks = _Cache(12)
        self._small_masks = _Cache(160)
        self._mask_lock = threading.Lock()

    def add_uploaded(self, preset: dict):
        self.presets[preset["id"]] = preset

    def remove_uploaded(self, preset_id: str) -> bool:
        if preset_id in self.library or preset_id not in self.presets:
            return False
        del self.presets[preset_id]
        return True

    def allowed(self, owner: str | None) -> set:
        """Preset ids this photographer may use: the built-in ones and their own uploads."""
        return {i for i, p in self.presets.items() if i in self.library or owner is None or p.get("owner") == owner}

    def owner_of(self, preset_id: str) -> str | None:
        p = self.presets.get(preset_id)
        return None if p is None or preset_id in self.library else p.get("owner", "")

    def catalogue(self, owner: str | None = None) -> list[dict]:
        """The presets as the app's picker needs them (no slider values): built-in ones and this owner's."""
        out = []
        for p in self.presets.values():
            mine = p["id"] not in self.library
            if mine and owner is not None and p.get("owner") != owner:
                continue
            out.append({"id": p["id"], "name": p["display_name"], "look": p["look"], "variant": p["variant"],
                        "pack": p["pack"], "about": p["description"] if mine else plain_about(p),
                        "tags": [] if mine else preset_tags(p), "mine": mine, "made": bool(p.get("made")),
                        "own_masks": sorted((p.get("app_masks") or {}).keys()), "wb": p.get("app_wb") or None,
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
        cache = self._small_images if size <= 400 else self._images
        return cache.get((str(path), path.stat().st_mtime_ns, size), lambda: self._load_uncached(path, size))

    @staticmethod
    def _load_uncached(path: Path, size: int) -> np.ndarray:
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
        return self._masks.get((str(path), stamp), lambda: self._make_masks(path, stamp))

    def small_masks(self, path: Path, shape) -> dict:
        """The masks at the size of a grid picture, kept apart (as bytes) from the full ones."""
        stamp = path.stat().st_mtime_ns
        h, w = shape[:2]

        def make():
            full = self._masks.peek((str(path), stamp)) or self._make_masks(path, stamp)
            return {k: (cv2.resize(v, (w, h), interpolation=cv2.INTER_AREA) * 255 + 0.5).astype(np.uint8)
                    for k, v in full.items()}
        kept = self._small_masks.get((str(path), stamp, h, w), make)
        return {k: v.astype(np.float32) / 255 for k, v in kept.items()}

    def _make_masks(self, path: Path, stamp: int) -> dict:
        """Masks from the copy kept beside the preview, or made now (and kept) if there is none."""
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

    # ---- looks ----------------------------------------------------------
    def auto_values(self, path: Path, toggles, iso=None) -> dict:
        """The automatic values for one photo. Measured on a fixed-size copy, so
        the result does not depend on the size being viewed."""
        toggles = tuple(t for t in TOGGLES if t in toggles)
        if not toggles:
            return {"settings": {}, "manual": {}, "info": {}}
        key = (str(path), path.stat().st_mtime_ns, toggles, iso)
        def make():
            skin = None
            if "white_balance" in toggles:                    # faces keep the white balance honest
                try:
                    skin = self.masks(path)["face_skin"]
                except Exception:
                    skin = None                               # no masks: neutral areas alone decide
            return auto_correct(self._load(path, 800), toggles, None, iso, skin)
        return self._autos.get(key, make)

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
        own_wb = (preset or {}).get("app_wb") or {}             # presets made in the app (Create preset)
        manual = {"temp": float(manual.get("temp", 0.0)) + float(own_wb.get("temp", 0)) + look["temp"],
                  "tint": float(manual.get("tint", 0.0)) + float(own_wb.get("tint", 0)) + look["tint"], "exposure": look["ev"]}
        masks = {k: dict(v) for k, v in ((preset or {}).get("app_masks") or {}).items()} if look["preset_masks"] else {}
        for kind, values in look["masks"].items():              # your own changes on top of the preset's
            masks[kind] = {**masks.get(kind, {}), **values}
        corrections = local_corrections(preset) if preset and look["preset_masks"] else []
        return {"settings": settings, "curves": curves, "manual": manual, "auto": auto["settings"],
                "masks": masks, "corrections": corrections,
                "shapes": ([*((preset or {}).get("app_shapes") or [])] if look["preset_masks"] else []) + look["shapes"], "angle": float(settings.get("CropAngle") or 0.0),
                "auto_wb": auto["manual"], "amount": look["amount"], "preset": preset["display_name"] if preset else None,
                "crop": look["crop"]}

    def auto_crop(self, path: Path, look: dict, ratio: str = "original") -> tuple[dict | None, str]:
        """AI crop for one photo, measured on the straightened picture the crop applies to."""
        d = self.develop(path, look)
        img = straighten(_crop.turned(self._load(path, 1280), _crop.turn_of(d["crop"])), d["angle"])
        return _crop.auto((np.clip(img, 0, 1) * 255).astype(np.uint8), ratio)

    def base(self, path: Path, size: int, crop: dict | None = None, angle: float = 0.0, turn: int = 0) -> bytes:
        """The photo untouched, but with the same turn, crop (and straighten) as the edit, so before and after line up."""
        crop = _crop.rect(crop)
        if not crop:
            return self._jpeg(_crop.turned(self._load(path, size), turn))
        img = straighten(_crop.turned(self._load(path, min(max(SIZES), int(size / _crop.zoom(crop)) + 1)), turn), angle)
        out = _crop.apply(img, crop)
        if max(out.shape[:2]) > size:
            k = size / max(out.shape[:2])
            out = cv2.resize(out, (max(1, round(out.shape[1] * k)), max(1, round(out.shape[0] * k))), interpolation=cv2.INTER_AREA)
        return self._jpeg(out)

    def export(self, path: Path, look: dict, max_side: int = 0, iso=None, quality: int = 90, watermark: str = "") -> bytes:
        """The finished picture for delivery: the look applied at max_side (0 = as large as the
        photo that was uploaded), with the fast renderer."""
        from engine.fast import render8
        bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if bgr is None:
            raise FileNotFoundError(path)
        h, w = bgr.shape[:2]
        full = _crop.clean(look.get("crop")) if isinstance(look, dict) else None
        turn, crop = _crop.turn_of(full), _crop.rect(full)
        if max_side and crop:
            max_side_read = int(max_side / _crop.zoom(crop)) + 1
        else:
            max_side_read = max_side
        if max_side_read and max(h, w) > max_side_read:
            k = max_side_read / max(h, w)
            bgr = cv2.resize(bgr, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)
        img8 = _crop.turned(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), turn)
        del bgr
        d = self.develop(path, look, iso)
        out = render8(img8, d["settings"], d["curves"], d["manual"], amount=d["amount"])
        texture = d["settings"].get("Texture")
        if isinstance(texture, (int, float)) and texture:
            out = (_detail(out.astype(np.float32) / 255, float(texture) * d["amount"], 0) * 255 + 0.5).astype(np.uint8)
        s = d["settings"]
        if s.get("Dehaze") or (isinstance(s.get("Sharpness"), (int, float)) and abs(float(s["Sharpness"]) - 40) >= 0.5):
            out = (finish_detail(out.astype(np.float32) / 255, s, d["amount"]) * 255 + 0.5).astype(np.uint8)
        if d["masks"] or d["corrections"]:
            masks = {k: _crop.turned(v, turn) for k, v in self.masks(path).items()} if turn else self.masks(path)
            out = local_adjust8(out, masks, d["masks"], d["corrections"])
        if abs(d["angle"]) >= 0.05:
            out = straighten(out, d["angle"])
        if d["shapes"]:
            out = apply_shapes8(out, d["shapes"])
        if crop:
            out = np.ascontiguousarray(_crop.apply(out, crop))
            if max_side and max(out.shape[:2]) > max_side:       # the crop never comes out larger than asked
                k = max_side / max(out.shape[:2])
                out = cv2.resize(out, (max(1, round(out.shape[1] * k)), max(1, round(out.shape[0] * k))), interpolation=cv2.INTER_AREA)
        out = watermark8(out, watermark)
        ok, buf = cv2.imencode(".jpg", cv2.cvtColor(out, cv2.COLOR_RGB2BGR),
                               [cv2.IMWRITE_JPEG_QUALITY, int(min(100, max(40, quality))), cv2.IMWRITE_JPEG_OPTIMIZE, 1])
        if not ok:
            raise RuntimeError("could not write the picture")
        return buf.tobytes()

    def _load8(self, path: Path, size: int) -> np.ndarray:
        """The photo at this size as 8-bit RGB, for the fast renderer (a quarter of the memory of _load)."""
        cache = self._small_images if size <= 400 else self._images
        return cache.get((str(path), path.stat().st_mtime_ns, size, 8),
                         lambda: (np.clip(self._load_uncached(path, size), 0, 1) * 255 + 0.5).astype(np.uint8))

    def render(self, path: Path, look: dict, size: int, iso=None, show: str = "", mark: bool = False,
               uncropped: bool = False) -> bytes:
        """The photo with the look applied, made the same way as exports (the fast 8-bit renderer,
        about twice as quick as the float one and the same to within a level or two).
        show names a mask to tint red on top. uncropped: the whole picture (the crop tool draws its box on top)."""
        from engine.fast import render8
        d = self.develop(path, look, iso)
        turn = _crop.turn_of(d["crop"])                            # a quarter turn comes first, cropped or not
        crop = None if uncropped else _crop.rect(d["crop"])
        img8 = _crop.turned(self._load8(path, min(max(SIZES), int(size / _crop.zoom(crop)) + 1) if crop else size), turn)
        out = render8(img8, d["settings"], d["curves"], d["manual"], amount=d["amount"])
        s = d["settings"]
        texture = s.get("Texture") if isinstance(s.get("Texture"), (int, float)) else 0
        sharp = isinstance(s.get("Sharpness"), (int, float)) and abs(float(s["Sharpness"]) - 40) >= 0.5
        if texture or s.get("Dehaze") or sharp:
            f = out.astype(np.float32) / 255
            if texture:                                          # the engine's own texture is too weak to see
                f = _detail(f, float(texture) * d["amount"], 0)
            out = (finish_detail(f, s, d["amount"]) * 255 + 0.5).astype(np.uint8)
        ai_show = show if show and not show.startswith("shape") else ""
        if d["masks"] or d["corrections"] or ai_show:
            masks = self.small_masks(path, out.shape[1::-1] if turn in (90, 270) else out.shape) if size <= 400 and not ai_show else self.masks(path)
            masks = {k: _crop.turned(v, turn) for k, v in masks.items()} if turn else masks
            out = local_adjust8(out, masks, d["masks"], d["corrections"])
            if show in masks:
                out = (np.clip(show_mask(out.astype(np.float32) / 255, masks[show]), 0, 1) * 255 + 0.5).astype(np.uint8)
        out = straighten(out, d["angle"])
        if d["shapes"]:
            out = apply_shapes8(out, d["shapes"], show)
        if crop:
            out = np.ascontiguousarray(_crop.apply(out, crop))
            if max(out.shape[:2]) > size:
                k = size / max(out.shape[:2])
                out = cv2.resize(out, (max(1, round(out.shape[1] * k)), max(1, round(out.shape[0] * k))), interpolation=cv2.INTER_AREA)
        if mark:
            out = (np.clip(preview_mark(out.astype(np.float32) / 255), 0, 1) * 255 + 0.5).astype(np.uint8)
        ok, buf = cv2.imencode(".jpg", cv2.cvtColor(out, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
        if not ok:
            raise RuntimeError("could not write the preview")
        return buf.tobytes()


# ---- handing the edits to Lightroom ------------------------------------------
# Measured on Lightroom exports: one "stop" of the app's warmth equals this many mired in
# Lightroom's Temperature, and Lightroom's Tint moves this much per unit of the app's tint.
MIRED_PER_STOP = 156.0
# Measured when the preview's tint strength (wb_tint) was 0.25: Lightroom Tint 0.30 per unit. Scaled with
# wb_tint, so a stronger preview tint sends a matching Lightroom Tint.
LR_TINT_PER_UNIT = 0.30 / 0.25 * _r.DEFAULT_PARAMS["wb_tint"]
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
    if d["masks"]:
        not_yet.append("your own masks")
    if d["shapes"]:
        not_yet.append("radial, linear and brush masks")
    crop = _crop.rect(d["crop"])
    if _crop.turn_of(d["crop"]):
        not_yet.append("rotation by 90 or 180 degrees")
        crop = None
    if crop and abs(d["angle"]) < 0.05:                  # Lightroom's own crop: non-destructive there too
        settings.update({"HasCrop": True, "CropLeft": crop["x"], "CropTop": crop["y"],
                         "CropRight": round(crop["x"] + crop["w"], 5), "CropBottom": round(crop["y"] + crop["h"], 5),
                         "CropAngle": 0})
    elif crop:
        not_yet.append("crop on a straightened photo")
    temp, tint = float(d["manual"].get("temp") or 0), float(d["manual"].get("tint") or 0)
    wb = None
    if temp or tint:
        stops = temp / 100.0 * _r.DEFAULT_PARAMS["wb_temp"]
        wb = {"mired": round(-MIRED_PER_STOP * stops, 2), "tint": round(max(-150.0, min(150.0, LR_TINT_PER_UNIT * tint)), 2),
              "relative_temp": round(temp, 1), "relative_tint": round(tint, 1)}

    by_lightroom = bool(preset and preset["id"] not in renderer.library and preset.get("masks") and look["preset_masks"]
                        and preset.get("uuid") and not preset.get("made"))     # presets made in the app are sent as settings
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


def preset_from_look(renderer: "Renderer", look: dict, name: str, group: str, owner_id: str, admin: bool,
                     with_masks: bool = True, with_wb: bool = True) -> tuple[dict, str]:
    """A new preset from what the sliders show now (Lightroom's "Create Preset"): the chosen preset's
    settings and curves with your changes laid over them. Auto ticks are not part of it (they are
    worked out per photo), and the preset strength is baked into the slider values.
    Returns the preset as the app keeps it and its Lightroom .xmp file."""
    import uuid as _uuid
    from engine.presets import write_xmp
    look = clean_look(look, renderer.presets)
    base = renderer.presets.get(look["preset"]) if look["preset"] else None
    plain = {**look, "mode": "preset", "toggles": [], "masks": {}, "crop": None, "shapes": []}
    d = renderer.develop(Path("/nonexistent"), plain)            # no auto values, so no photo is read
    settings = {k: v for k, v in d["settings"].items() if k not in ("CropAngle",)}
    amount = float(look["amount"])
    if base and abs(amount - 1) > 1e-6:                          # strength: move each preset slider toward neutral
        for k, v in list(settings.items()):
            if k in adjustments.SLIDERS and isinstance(v, (int, float)) and not isinstance(v, bool) and k in base["settings"]:
                n = adjustments.neutral(k)
                settings[k] = round(n + (float(v) - n) * amount, 3)
    if look["ev"]:
        settings["Exposure2012"] = round(min(5, max(-5, float(settings.get("Exposure2012") or 0) + look["ev"])), 3)
    uid = _uuid.uuid4().hex.upper()
    pid = "my-" + uid if admin else f"my-{owner_id}-{uid}"
    preset = {"id": pid, "uuid": "", "name": name, "display_name": name,
              "look": group or name, "variant": name, "group": group, "pack": MY_PACK,
              "description": f"Made in Auto Edits{' from ' + base['display_name'] if base else ''}.",
              "process_version": "11.0", "camera_profile": (base or {}).get("camera_profile", ""),
              "settings": settings, "curves": {k: [list(p) for p in v] for k, v in d["curves"].items()},
              "has_masks": False, "masks": [], "source_file": "", "owner": owner_id, "made": True}
    if base and look["preset_masks"] and base.get("masks"):      # the chosen preset's own masks come along
        preset["masks"], preset["has_masks"] = base["masks"], True
    if with_masks:
        own = {k: dict(v) for k, v in ((base or {}).get("app_masks") or {}).items()} if look["preset_masks"] else {}
        for kind, values in look["masks"].items():
            own[kind] = {**own.get(kind, {}), **values}
        if own:
            preset["app_masks"] = own
        drawn = ([*((base or {}).get("app_shapes") or [])] if look["preset_masks"] else []) + look["shapes"]
        if drawn:
            preset["app_shapes"] = drawn
    if with_wb:
        base_wb = (base or {}).get("app_wb") or {}
        temp, tint = float(base_wb.get("temp", 0)) + look["temp"], float(base_wb.get("tint", 0)) + look["tint"]
        if temp or tint:
            preset["app_wb"] = {"temp": round(temp, 2), "tint": round(tint, 2)}
    return preset, write_xmp(name, group, settings, preset["curves"], uid)

