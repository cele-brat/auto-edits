"""Cropping: the crop box itself, and an AI crop that finds the people and frames them.

A crop is kept per photo as fractions of the (straightened) picture:
    {"x": 0.1, "y": 0.05, "w": 0.8, "h": 0.8, "ratio": "original", "auto": True}

AI crop (Bharat, Oct 2026: keeps the photo's own shape) follows the usual framing rules:
  * finds the faces (YuNet) and the people (MediaPipe selfie segmenter); guests far behind the
    main faces are left out of the decision;
  * never cuts through a face, keeps headroom above the heads;
  * eyes go on the upper third line; one person or a couple off to one side goes on the nearer
    vertical third, a group stays centred;
  * tightens moderately: the people fill about three quarters of the frame, and at least
    40% of the picture's area is always kept, so the setting around them survives;
  * no people found, or the photo is already framed well: it is left as it is.
"""
from __future__ import annotations

import numpy as np

RATIOS = {"original": None, "free": None, "1:1": 1.0, "4:5": 4 / 5, "5:4": 5 / 4, "2:3": 2 / 3, "3:2": 3 / 2,
          "5:7": 5 / 7, "7:5": 7 / 5, "16:9": 16 / 9, "9:16": 9 / 16}
MIN_SIDE = 0.05            # a crop narrower than this fraction is a mistake
MIN_SCALE = 0.63           # AI crop keeps at least 0.63 x 0.63 = 40% of the area
FILL = 0.76                # people fill about this much of the crop's tighter side


def _f(v, lo, hi):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return min(hi, max(lo, v))


def clean(raw) -> dict | None:
    """A crop that is safe to use, or None for no crop (or the whole picture)."""
    if not isinstance(raw, dict):
        return None
    x, y = _f(raw.get("x"), 0, 1), _f(raw.get("y"), 0, 1)
    w, h = _f(raw.get("w"), MIN_SIDE, 1), _f(raw.get("h"), MIN_SIDE, 1)
    if None in (x, y, w, h):
        return None
    x, y = min(x, 1 - w), min(y, 1 - h)
    if w >= 0.999 and h >= 0.999:
        return None
    ratio = raw.get("ratio") if raw.get("ratio") in RATIOS else "free"
    return {"x": round(x, 5), "y": round(y, 5), "w": round(w, 5), "h": round(h, 5), "ratio": ratio,
            "auto": raw.get("auto") is True}


def apply(img: np.ndarray, crop: dict | None) -> np.ndarray:
    if not crop:
        return img
    h, w = img.shape[:2]
    x0, y0 = int(round(crop["x"] * w)), int(round(crop["y"] * h))
    x1, y1 = int(round((crop["x"] + crop["w"]) * w)), int(round((crop["y"] + crop["h"]) * h))
    x1, y1 = max(x0 + 1, min(w, x1)), max(y0 + 1, min(h, y1))
    return img[y0:y1, x0:x1]


def zoom(crop: dict | None) -> float:
    """How much smaller the crop is than the picture along its longer cut (1 = no crop)."""
    return 1.0 if not crop else max(crop["w"], crop["h"])


# ---- finding the people -------------------------------------------------------------
def find_subject(img8: np.ndarray) -> dict:
    """Faces and people in a picture (uint8 RGB). Boxes in pixels of img8."""
    import cv2
    from . import faces as _faces
    from . import masks as _masks
    H, W = img8.shape[:2]
    faces = [b for b in _faces._detect_boxes(img8, score=0.6) if b[4] >= 0.6]
    if faces:                                            # main faces only: guests far behind are ignored
        big = (faces[0][2] - faces[0][0]) * (faces[0][3] - faces[0][1])
        faces = [b for b in faces if (b[2] - b[0]) * (b[3] - b[1]) >= 0.12 * big]
    s = min(1.0, 512 / max(H, W))
    small = cv2.resize(img8, (max(1, round(W * s)), max(1, round(H * s))), interpolation=cv2.INTER_AREA) if s < 1 else img8
    try:
        conf = _masks._classes(small)
        person = ((1 - conf[..., 0]) > 0.5).astype(np.uint8)
    except Exception:
        person = np.zeros(small.shape[:2], np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(person, connectivity=8)
    keep = set()
    for (x0, y0, x1, y1, _sc) in faces:                  # people whose face was found
        cx, cy = int((x0 + x1) / 2 * s), int((y0 + y1) / 2 * s)
        if 0 <= cy < labels.shape[0] and 0 <= cx < labels.shape[1] and labels[cy, cx]:
            keep.add(int(labels[cy, cx]))
    area = person.shape[0] * person.shape[1]
    if not faces:                                        # no faces: the large figures
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] >= 0.03 * area:
                keep.add(i)
    box = None
    for i in keep:
        x, y, w, h = stats[i, :4]
        b = (x / s, y / s, (x + w) / s, (y + h) / s)
        box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
    for (x0, y0, x1, y1, _sc) in faces:                  # a face the segmenter missed still counts
        box = (x0, y0, x1, y1) if box is None else (min(box[0], x0), min(box[1], y0), max(box[2], x1), max(box[3], y1))
    return {"faces": [b[:4] for b in faces], "people": box, "size": (W, H)}


# ---- framing ------------------------------------------------------------------------------
def frame(found: dict, ratio: float | None = None) -> tuple[dict | None, str]:
    """The crop for what find_subject found (fractions), and a short reason."""
    W, H = found["size"]
    people, faces = found["people"], found["faces"]
    if people is None:
        return None, "no people found"
    R = ratio or W / H
    # the largest box of this shape that fits; scale s shrinks it
    if R >= W / H:
        fw, fh = W, W / R
    else:
        fw, fh = H * R, H
    px0, py0, px1, py1 = people
    pw, ph = px1 - px0, py1 - py0
    # the part that must stay in: every main face with room around it, headroom above
    if faces:
        fh_ = float(np.median([b[3] - b[1] for b in faces]))
        need = (min(b[0] for b in faces) - 0.6 * fh_, min(b[1] for b in faces) - 0.7 * fh_,
                max(b[2] for b in faces) + 0.6 * fh_, max(b[3] for b in faces) + 0.5 * fh_)
    else:
        fh_ = 0.0
        need = (px0, py0 - 0.04 * H, px1, py0 + 0.35 * ph)
    need = (max(0, need[0]), max(0, need[1]), min(W, need[2]), min(H, need[3]))
    s_need = max((need[2] - need[0]) / fw, (need[3] - need[1]) / fh)
    s_fill = max(pw / fw, ph / fh) / FILL
    s = min(1.0, max(MIN_SCALE, s_need * 1.02, s_fill))
    cw, ch = fw * s, fh * s
    if ratio is None and s > 0.96:
        return None, "already well framed"
    # where the people go
    cx = (px0 + px1) / 2
    group = len(faces) >= 3 or pw > 0.55 * cw
    if group or abs(cx - W / 2) < 0.08 * W:
        x0 = cx - cw / 2
    elif cx < W / 2:
        x0 = cx - cw / 3                                 # left third
    else:
        x0 = cx - 2 * cw / 3                             # right third
    if faces:
        eyes = float(np.mean([b[1] + 0.42 * (b[3] - b[1]) for b in faces]))
        y0 = eyes - ch / 3                               # eyes on the upper third line
    else:
        y0 = (py0 + py1) / 2 - ch / 2
    # the faces must be inside, then the picture's edges win
    x0 = min(max(x0, need[2] - cw), need[0])
    y0 = min(max(y0, need[3] - ch), need[1])
    x0 = min(max(0.0, x0), W - cw)
    y0 = min(max(0.0, y0), H - ch)
    crop = clean({"x": x0 / W, "y": y0 / H, "w": cw / W, "h": ch / H, "auto": True,
                  "ratio": next((k for k, v in RATIOS.items() if v and abs(v - R) < 1e-3 and ratio), "original")})
    n = len(faces)
    why = f"framed on {n} face{'s' if n != 1 else ''}" if n else "framed on the people"
    return crop, why


def auto(img8: np.ndarray, ratio: str = "original") -> tuple[dict | None, str]:
    """AI crop for a picture (uint8 RGB): the crop, or None, and why."""
    return frame(find_subject(img8), RATIOS.get(ratio))
