"""Matching the automatic values across a folder, so a wedding event looks like one set.

Auto works out each photo's own exposure, tone and white balance. Taken one at a time those
values are right for each frame, but a folder of them can drift: one frame's white balance
lands on a wall colour, one dark frame is lifted far more than its neighbours. This module
pulls every photo's values toward the middle of the photos shot in the same light, so the
folder stays consistent while each frame still gets its own correction.

Pure functions, no database: the server's match store (server/match.py) collects the facts.
"""
from __future__ import annotations

import numpy as np

# Which automatic values are matched, and how far one photo may sit from its group's median.
BANDS = {
    "Exposure2012": 0.6,          # stops
    "Highlights2012": 25.0,
    "Shadows2012": 25.0,
    "Whites2012": 20.0,
    "Blacks2012": 20.0,
    "temp": 18.0,                 # preview white-balance sliders, -100..100
    "tint": 10.0,
}
PULL = 0.5                        # how much of the (clamped) difference from the median is kept
MIN_GROUP = 3                     # fewer photos in the same light: nothing to match against

SCENES = ("bright outdoor", "indoor tungsten", "dusk")


def facts_of(img) -> dict:
    """What a photo looks like, from a small copy (float32 0..1 or uint8, any size):
    median brightness, warmth (red minus blue), colourfulness and how much of it is dark."""
    a = np.asarray(img)
    if a.dtype == np.uint8:
        a = a.astype(np.float32) / 255.0
    step = max(1, max(a.shape[:2]) // 256)
    s = a[::step, ::step, :3]
    luma = 0.2126 * s[..., 0] + 0.7152 * s[..., 1] + 0.0722 * s[..., 2]
    return {"median": round(float(np.median(luma)), 4),
            "warmth": round(float((s[..., 0] - s[..., 2]).mean()), 4),
            "colour": round(float((s.max(axis=-1) - s.min(axis=-1)).mean()), 4),
            "dark": round(float((luma < 0.12).mean()), 4)}


def scene_of(iso, facts: dict | None, kelvin=None) -> str:
    """The kind of light a photo was taken in (same rules as engine/style.py)."""
    from .style import scene_of as _scene
    f = facts or {}
    return _scene(iso, f.get("median"), f.get("warmth"), kelvin)


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def medians(values: list[dict]) -> dict:
    """The middle of a group's automatic values. values: [{"settings": {...}, "manual": {...}}, ...]
    (the "tone:11" piece of each photo). Returns {key: median} for the keys in BANDS, plus "n"."""
    cols: dict[str, list[float]] = {k: [] for k in BANDS}
    for v in values:
        for k in BANDS:
            x = _num((v.get("manual") if k in ("temp", "tint") else v.get("settings") or {}).get(k)) if isinstance(v, dict) else None
            if x is not None:
                cols[k].append(x)
    out = {k: round(float(np.median(xs)), 3) for k, xs in cols.items() if xs}
    out["n"] = len(values)
    return out


def matched(settings: dict, manual: dict, med: dict | None) -> tuple[dict, dict]:
    """One photo's automatic values pulled toward its group's medians. Keys the photo did not ask
    for (not in settings/manual) are left out, so a look that ticked only Exposure gets only Exposure."""
    if not med or med.get("n", 0) < MIN_GROUP:
        return settings, manual
    settings, manual = dict(settings), dict(manual)
    for k, band in BANDS.items():
        box = manual if k in ("temp", "tint") else settings
        own, mid = _num(box.get(k)), _num(med.get(k))
        if own is None or mid is None:
            continue
        dev = max(-band, min(band, own - mid))
        value = mid + PULL * dev
        box[k] = round(value, 2) if k == "Exposure2012" else int(round(value))
    return settings, manual
