"""Skin retouching: smoothing that keeps texture, blemish removal, brighter eyes, whiter teeth.

Works on an 8-bit RGB picture (the straightened, uncropped frame the app draws on). The faces
come from engine/faces.py (YuNet to find them, MediaPipe's face mesh for the landmarks); the
landmarks are kept as fractions of the frame so one detection serves every size.

Settings (look["retouch"]):
    smooth   0..100  frequency separation: the low frequencies of the skin are blurred, the fine
                     texture (pores, hair) is put back, so skin looks even, not plastic
    blemish  0..100  small dark or bright spots inside the face skin are found and inpainted
    eyes     0..100  iris and whites brightened a little
    teeth    0..100  the yellow taken out of teeth, a touch brighter
    spots    [{"x", "y", "r"}]  places clicked by hand (fractions of the frame), each inpainted
"""
from __future__ import annotations

import cv2
import numpy as np

from . import faces as _faces

KEYS = ("smooth", "blemish", "eyes", "teeth")
MAX_SPOTS = 60

# MediaPipe face mesh rings
_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148, 176, 149,
         150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109]
_EYES = ([33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246],
         [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398])
_IRISES = ([468, 469, 470, 471, 472], [473, 474, 475, 476, 477])
_BROWS = ([70, 63, 105, 66, 107, 55, 65, 52, 53, 46], [300, 293, 334, 296, 336, 285, 295, 282, 283, 276])
_LIPS_OUT = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185]
_LIPS_IN = [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308, 415, 310, 311, 312, 13, 82, 81, 80, 191]
_NOSTRILS = [218, 438]


def clean(raw) -> dict:
    """A retouch setting with every field present and in range."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for k in KEYS:
        try:
            v = float(raw.get(k) or 0)
        except (TypeError, ValueError):
            v = 0.0
        out[k] = int(round(min(100, max(0, v)))) if v == v else 0
    spots = []
    for s in (raw.get("spots") if isinstance(raw.get("spots"), list) else [])[:MAX_SPOTS]:
        if not isinstance(s, dict):
            continue
        try:
            x, y, r = float(s.get("x")), float(s.get("y")), float(s.get("r", 0.01))
        except (TypeError, ValueError):
            continue
        if not (0 <= x <= 1 and 0 <= y <= 1) or x != x or y != y or r != r:
            continue
        spots.append({"x": round(x, 4), "y": round(y, 4), "r": round(min(0.08, max(0.002, r)), 4)})
    out["spots"] = spots
    return out


def is_off(r: dict | None) -> bool:
    return not r or (not any(r.get(k) for k in KEYS) and not r.get("spots"))


# ---- faces ------------------------------------------------------------------------------
def landmarks(img8: np.ndarray, max_faces: int = 8) -> list[dict]:
    """Every face with its 478 mesh points as fractions of the picture, largest first. Faces too
    small for a mesh (under 36 px) are left out: there is nothing to retouch at that size."""
    h, w = img8.shape[:2]
    out = []
    lm, mp = _faces._get()
    for (x0, y0, x1, y1, score) in _faces._detect_boxes(img8)[:max_faces]:
        bw, bh = x1 - x0, y1 - y0
        if min(bw, bh) < 36:
            continue
        m = 0.45
        cx0, cy0 = int(max(0, x0 - m * bw)), int(max(0, y0 - m * bh))
        cx1, cy1 = int(min(w, x1 + m * bw)), int(min(h, y1 + m * bh))
        crop = img8[cy0:cy1, cx0:cx1]
        k = 384 / max(crop.shape[:2])
        crop = cv2.resize(crop, (max(1, round(crop.shape[1] * k)), max(1, round(crop.shape[0] * k))),
                          interpolation=cv2.INTER_CUBIC if k > 1 else cv2.INTER_AREA)
        res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(crop)))
        if not res.face_landmarks:
            continue
        pts = res.face_landmarks[0]
        xy = [[round((cx0 + p.x * (cx1 - cx0)) / w, 5), round((cy0 + p.y * (cy1 - cy0)) / h, 5)] for p in pts]
        out.append({"box": [x0 / w, y0 / h, x1 / w, y1 / h], "score": float(score), "pts": xy})
    return out


def _ring(face: dict, ids, h: int, w: int, grow: float = 1.0) -> np.ndarray:
    pts = np.array([[face["pts"][i][0] * w, face["pts"][i][1] * h] for i in ids], np.float32)
    centre = pts.mean(0)
    return np.round(centre + (pts - centre) * grow).astype(np.int32)


def _fill(mask: np.ndarray, poly: np.ndarray) -> None:
    cv2.fillPoly(mask, [poly], 255, lineType=cv2.LINE_AA)


def face_width(face: dict, w: int) -> float:
    return max(8.0, (face["box"][2] - face["box"][0]) * w)


def skin_mask(face: dict, h: int, w: int) -> np.ndarray:
    """The face's skin (0..1): the face oval without eyes, brows, lips and nostrils, soft at the edge."""
    m = np.zeros((h, w), np.uint8)
    if len(face["pts"]) < 468:
        return m.astype(np.float32)
    _fill(m, _ring(face, _OVAL, h, w, 1.02))
    cut = np.zeros((h, w), np.uint8)
    for eye in _EYES:
        _fill(cut, _ring(face, eye, h, w, 1.6))
    for brow in _BROWS:
        _fill(cut, _ring(face, brow, h, w, 1.3))
    _fill(cut, _ring(face, _LIPS_OUT, h, w, 1.15))
    for i in _NOSTRILS:
        x, y = face["pts"][i]
        cv2.circle(cut, (int(x * w), int(y * h)), max(2, int(face_width(face, w) * 0.035)), 255, -1)
    m = cv2.subtract(m, cut)
    k = max(3, int(face_width(face, w) * 0.06)) | 1
    return cv2.GaussianBlur(m, (k, k), 0).astype(np.float32) / 255


def eye_masks(face: dict, h: int, w: int) -> tuple[np.ndarray, np.ndarray]:
    """(iris, whites) of both eyes, 0..1."""
    both = np.zeros((h, w), np.uint8)
    iris = np.zeros((h, w), np.uint8)
    if len(face["pts"]) < 468:
        return iris.astype(np.float32), both.astype(np.float32)
    for eye in _EYES:
        _fill(both, _ring(face, eye, h, w, 1.05))
    if len(face["pts"]) > 477:
        for ids in _IRISES:
            c = _ring(face, ids, h, w)
            radius = float(np.linalg.norm(c[1:] - c[0], axis=1).mean())
            cv2.circle(iris, (int(c[0][0]), int(c[0][1])), max(1, round(radius)), 255, -1, lineType=cv2.LINE_AA)
    iris = cv2.min(iris, both)
    whites = cv2.subtract(both, iris)
    k = max(3, int(face_width(face, w) * 0.012)) | 1
    soft = lambda m: cv2.GaussianBlur(m, (k, k), 0).astype(np.float32) / 255
    return soft(iris), soft(whites)


def teeth_mask(face: dict, img8: np.ndarray) -> np.ndarray:
    """Teeth: the inside of the mouth where it is bright and not red (0..1)."""
    h, w = img8.shape[:2]
    if len(face["pts"]) < 468:
        return np.zeros((h, w), np.float32)
    mouth = np.zeros((h, w), np.uint8)
    _fill(mouth, _ring(face, _LIPS_IN, h, w, 1.0))
    if not mouth.any():
        return np.zeros((h, w), np.float32)
    ys, xs = np.where(mouth > 0)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    hsv = cv2.cvtColor(img8[y0:y1, x0:x1], cv2.COLOR_RGB2HSV)
    pale = ((hsv[..., 2] > 90) & (hsv[..., 1] < 120)).astype(np.uint8) * 255
    out = np.zeros((h, w), np.uint8)
    out[y0:y1, x0:x1] = cv2.min(mouth[y0:y1, x0:x1], pale)
    k = max(3, int(face_width(face, w) * 0.01)) | 1
    return cv2.GaussianBlur(out, (k, k), 0).astype(np.float32) / 255


# ---- the retouches ----------------------------------------------------------------------
def _box_of(mask: np.ndarray, pad: int):
    ys, xs = np.where(mask > 0.02)
    if not len(ys):
        return None
    h, w = mask.shape
    return max(0, ys.min() - pad), min(h, ys.max() + 1 + pad), max(0, xs.min() - pad), min(w, xs.max() + 1 + pad)


def _blend(out: np.ndarray, part, cover: np.ndarray, box) -> None:
    y0, y1, x0, x1 = box
    src = out[y0:y1, x0:x1].astype(np.float32)
    c = cover[y0:y1, x0:x1][..., None]
    out[y0:y1, x0:x1] = np.clip(src + (part.astype(np.float32) - src) * c + 0.5, 0, 255).astype(np.uint8)


def smooth_skin(img8: np.ndarray, mask: np.ndarray, amount: float, face_px: float) -> np.ndarray:
    """Frequency separation on the masked skin: the low frequencies (colour, blotches) are blurred,
    the high frequencies (pores, fine hair) are kept, so the skin stays real."""
    if amount <= 0:
        return img8
    box = _box_of(mask, int(face_px * 0.1))
    if box is None:
        return img8
    y0, y1, x0, x1 = box
    part = img8[y0:y1, x0:x1].astype(np.float32)
    sigma = max(1.0, face_px * 0.012)                     # splits texture (above) from tone (below)
    low = cv2.GaussianBlur(part, (0, 0), sigma)
    high = part - low
    spread = max(2.0, face_px * (0.015 + 0.045 * amount / 100.0))
    # an edge-keeping blur of the low layer, so a cheek never smears into the hair line
    even = cv2.bilateralFilter(np.clip(low, 0, 255).astype(np.uint8), 0, 28 + 32 * amount / 100.0, spread).astype(np.float32)
    done = even + high * (1.0 - 0.25 * amount / 100.0)      # a little of the texture goes too, as in Lightroom's Texture −
    out = img8.copy()
    _blend(out, done, mask * (amount / 100.0), box)
    return out


def find_blemishes(img8: np.ndarray, mask: np.ndarray, amount: float, face_px: float) -> np.ndarray:
    """A mask (uint8 0/255) of small spots inside the skin that stand out from the skin around them."""
    h, w = img8.shape[:2]
    found = np.zeros((h, w), np.uint8)
    box = _box_of(mask, int(face_px * 0.05))
    if box is None or amount <= 0:
        return found
    y0, y1, x0, x1 = box
    part = img8[y0:y1, x0:x1]
    lab = cv2.cvtColor(part, cv2.COLOR_RGB2LAB).astype(np.float32)
    luma, red = lab[..., 0], lab[..., 1]
    k = max(5, int(face_px * 0.08)) | 1                        # the skin around a spot, with the spot itself left out (median)
    base_l = cv2.medianBlur(np.clip(luma, 0, 255).astype(np.uint8), k).astype(np.float32)
    base_a = cv2.medianBlur(np.clip(red, 0, 255).astype(np.uint8), k).astype(np.float32)
    dark = base_l - luma                                       # a pimple or mole is darker and redder than its surround
    redder = red - base_a
    stand = np.maximum(dark * 1.0 + redder * 1.2, (luma - base_l) * 0.6)    # or much brighter (a shine, a white spot)
    t = 22.0 - 10.0 * amount / 100.0                           # more amount: smaller differences count (JPEG grain stays below)
    cand = ((stand > t) & (mask[y0:y1, x0:x1] > 0.5)).astype(np.uint8)
    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(cand, 8)
    largest = (face_px * 0.06) ** 2
    smallest = max(2.0, (face_px * 0.004) ** 2)
    for i in range(1, n):
        area, bw, bh = stats[i, cv2.CC_STAT_AREA], stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        if smallest <= area <= largest and max(bw, bh) <= 2.2 * max(1, min(bw, bh)):     # roundish, small
            found[y0:y1, x0:x1][labels == i] = 255
    if found.any():
        k = max(3, int(face_px * 0.012)) | 1
        found = cv2.dilate(found, np.ones((k, k), np.uint8))
    return found


def inpaint(img8: np.ndarray, mask8: np.ndarray, radius: int = 3) -> np.ndarray:
    """Fill the masked pixels from their surroundings (only the area around them is worked on)."""
    if not mask8.any():
        return img8
    ys, xs = np.where(mask8 > 0)
    h, w = img8.shape[:2]
    pad = max(8, radius * 4)
    y0, y1, x0, x1 = max(0, ys.min() - pad), min(h, ys.max() + 1 + pad), max(0, xs.min() - pad), min(w, xs.max() + 1 + pad)
    out = img8.copy()
    out[y0:y1, x0:x1] = cv2.inpaint(np.ascontiguousarray(img8[y0:y1, x0:x1]), mask8[y0:y1, x0:x1], radius, cv2.INPAINT_TELEA)
    return out


def heal_spots(img8: np.ndarray, spots: list[dict]) -> np.ndarray:
    """Spots clicked by hand, each inpainted and feathered back in."""
    if not spots:
        return img8
    h, w = img8.shape[:2]
    out = img8
    for s in spots:
        r = max(2, int(round(s["r"] * max(h, w))))
        cx, cy = int(round(s["x"] * w)), int(round(s["y"] * h))
        if not (0 <= cx < w and 0 <= cy < h):
            continue
        m = np.zeros((h, w), np.uint8)
        cv2.circle(m, (cx, cy), r, 255, -1)
        healed = inpaint(out, m, max(3, r // 2))
        soft = cv2.GaussianBlur(m, (0, 0), max(1.0, r * 0.25)).astype(np.float32) / 255
        box = _box_of(soft, 2)
        if box is None:
            continue
        y0, y1, x0, x1 = box
        if out is img8:
            out = img8.copy()
        _blend(out, healed[y0:y1, x0:x1], soft, box)
    return out


def brighten_eyes(img8: np.ndarray, iris: np.ndarray, whites: np.ndarray, amount: float) -> np.ndarray:
    if amount <= 0:
        return img8
    a = amount / 100.0
    out = img8
    for m, gain, sat in ((iris, 1.0 + 0.35 * a, 1.0 + 0.3 * a), (whites, 1.0 + 0.22 * a, 1.0 - 0.5 * a)):
        box = _box_of(m, 2)
        if box is None:
            continue
        y0, y1, x0, x1 = box
        part = img8[y0:y1, x0:x1].astype(np.float32) / 255
        grey = (0.2126 * part[..., 0] + 0.7152 * part[..., 1] + 0.0722 * part[..., 2])[..., None]
        done = (grey + (part - grey) * sat) * gain
        if out is img8:
            out = img8.copy()
        _blend(out, np.clip(done, 0, 1) * 255, m, box)
    return out


def whiten_teeth(img8: np.ndarray, mask: np.ndarray, amount: float) -> np.ndarray:
    if amount <= 0:
        return img8
    box = _box_of(mask, 2)
    if box is None:
        return img8
    a = amount / 100.0
    y0, y1, x0, x1 = box
    part = img8[y0:y1, x0:x1].astype(np.float32) / 255
    grey = (0.2126 * part[..., 0] + 0.7152 * part[..., 1] + 0.0722 * part[..., 2])[..., None]
    done = (grey + (part - grey) * (1.0 - 0.7 * a)) * (1.0 + 0.12 * a)      # less yellow, a touch brighter
    done[..., 2] += 0.03 * a                                                # a hint of blue cancels the last yellow
    out = img8.copy()
    _blend(out, np.clip(done, 0, 1) * 255, mask, box)
    return out


def apply(img8: np.ndarray, retouch: dict, faces: list[dict]) -> np.ndarray:
    """Every retouch of the look on one picture, faces given by landmarks() (any size: fractions)."""
    r = clean(retouch)
    if is_off(r):
        return img8
    h, w = img8.shape[:2]
    out = img8
    if r["spots"]:
        out = heal_spots(out, r["spots"])
    for face in faces:
        fw = face_width(face, w)
        if r["blemish"] or r["smooth"]:
            skin = skin_mask(face, h, w)
            if r["blemish"]:
                found = find_blemishes(out, skin, r["blemish"], fw)
                out = inpaint(out, found, max(3, int(fw * 0.01)))
            if r["smooth"]:
                out = smooth_skin(out, skin, r["smooth"], fw)
        if r["eyes"]:
            iris, whites = eye_masks(face, h, w)
            out = brighten_eyes(out, iris, whites, r["eyes"])
        if r["teeth"]:
            out = whiten_teeth(out, teeth_mask(face, out), r["teeth"])
    return out
