"""Masks for local adjustments: where the skin, hair, clothes, eyes, people and sky are.

The kinds match Lightroom's own AI masks, so a setting made here can be handed
to Lightroom as the same kind of mask. The masks made here are only for the
app's preview; Lightroom makes its own, finer ones on the full-size photo.

People and their parts: Google's MediaPipe "selfie multiclass" segmenter (Apache 2.0 licence),
run on the whole photo and again close-up around every face, so small people
in wide shots are found too. Eyes, brows, lips, teeth and facial hair: MediaPipe
face landmarks. Sky: a simple
colour-and-smoothness rule (approximate).
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from . import faces as _faces

MODEL = Path(__file__).resolve().parent.parent / "models" / "selfie_multiclass_256x256.tflite"
KINDS = ("subject", "background", "whole_person", "face_skin", "body_skin", "hair", "clothes", "eyes",
         "iris_pupil", "eye_sclera", "eyebrows", "lips", "teeth", "facial_hair", "sky")
_CLASSES = ("background", "hair", "body_skin", "face_skin", "clothes", "others")
WORK_SIDE = 768                 # masks are made and kept at this size (long edge)
# landmark rings (MediaPipe face mesh numbering)
_EYES = ([33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246],
         [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398])
_IRISES = ([468, 469, 470, 471, 472], [473, 474, 475, 476, 477])           # centre, then four rim points
_BROWS = ([70, 63, 105, 66, 107, 55, 65, 52, 53, 46], [300, 293, 334, 296, 336, 285, 295, 282, 283, 276])
_LIPS_OUT = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185]
_LIPS_IN = [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308, 415, 310, 311, 312, 13, 82, 81, 80, 191]
_LOWER_FACE = [234, 93, 132, 58, 172, 136, 150, 149, 176, 148, 152, 377, 400, 378, 379, 365, 397, 288, 361, 323, 454,
               411, 426, 322, 164, 92, 206, 187]                             # jaw line, back across the upper lip
_FACE_PARTS = ("iris_pupil", "eye_sclera", "eyebrows", "lips", "teeth", "facial_hair")

_segmenter = None


def _get():
    global _segmenter
    if _segmenter is None:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
        opts = vision.ImageSegmenterOptions(base_options=mp_python.BaseOptions(model_asset_path=str(MODEL)),
                                            output_confidence_masks=True, output_category_mask=False)
        _segmenter = (vision.ImageSegmenter.create_from_options(opts), mp)
    return _segmenter


def _classes(img8) -> np.ndarray:
    """(H, W, 6) class confidences for one picture."""
    seg, mp = _get()
    res = seg.segment(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(img8)))
    h, w = img8.shape[:2]
    out = np.stack([np.asarray(m.numpy_view(), np.float32).reshape(m.height, m.width) for m in res.confidence_masks], -1)
    if out.shape[:2] != (h, w):
        out = cv2.resize(out, (w, h), interpolation=cv2.INTER_LINEAR)
    return out


def _people(img8, boxes) -> np.ndarray:
    """Class confidences for the whole photo, sharpened by a close-up pass around each face."""
    h, w = img8.shape[:2]
    conf = _classes(img8)
    for (x0, y0, x1, y1, _score) in boxes[:12]:
        fw, fh = x1 - x0, y1 - y0
        if max(fw, fh) > 0.45 * min(h, w):                  # already a close-up
            continue
        cx = (x0 + x1) / 2
        ax0, ax1 = int(max(0, cx - 2.6 * fw)), int(min(w, cx + 2.6 * fw))
        ay0, ay1 = int(max(0, y0 - 1.3 * fh)), int(min(h, y1 + 7.0 * fh))
        if ax1 - ax0 < 24 or ay1 - ay0 < 24:
            continue
        close = _classes(img8[ay0:ay1, ax0:ax1])
        region = conf[ay0:ay1, ax0:ax1]
        # fade the close-up in towards its middle so its edges leave no seam
        yy = np.minimum(np.arange(close.shape[0]), np.arange(close.shape[0])[::-1])[:, None] / max(1, close.shape[0] * 0.12)
        xx = np.minimum(np.arange(close.shape[1]), np.arange(close.shape[1])[::-1])[None, :] / max(1, close.shape[1] * 0.12)
        wgt = np.clip(np.minimum(yy, xx), 0, 1)[..., None].astype(np.float32)
        conf[ay0:ay1, ax0:ax1] = region * (1 - wgt) + close * wgt
    return conf


def _face_parts(img8, boxes, conf) -> dict[str, np.ndarray]:
    """Eyes, brows, lips, teeth and facial hair, drawn from the face landmarks."""
    h, w = img8.shape[:2]
    parts = {k: np.zeros((h, w), np.uint8) for k in _FACE_PARTS}
    hsv = cv2.cvtColor(img8, cv2.COLOR_RGB2HSV)
    lm, mp = _faces._get()
    for (x0, y0, x1, y1, _score) in boxes[:12]:
        bw, bh = x1 - x0, y1 - y0
        if min(bw, bh) < 28:
            continue
        m = 0.45
        cx0, cy0 = int(max(0, x0 - m * bw)), int(max(0, y0 - m * bh))
        cx1, cy1 = int(min(w, x1 + m * bw)), int(min(h, y1 + m * bh))
        crop = img8[cy0:cy1, cx0:cx1]
        k = 384 / max(crop.shape[:2])
        crop = cv2.resize(crop, (max(1, round(crop.shape[1] * k)), max(1, round(crop.shape[0] * k))),
                          interpolation=cv2.INTER_CUBIC if k > 1 else cv2.INTER_AREA)
        res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(crop)))
        for pts in res.face_landmarks[:1]:
            def ring(ids, grow=1.0):
                poly = np.array([[cx0 + pts[i].x * (cx1 - cx0), cy0 + pts[i].y * (cy1 - cy0)] for i in ids], np.float32)
                centre = poly.mean(0)
                return np.round(centre + (poly - centre) * grow).astype(np.int32)

            def fill(name, ids, grow=1.0):
                cv2.fillPoly(parts[name], [ring(ids, grow)], 255, lineType=cv2.LINE_AA)

            one = np.zeros((h, w), np.uint8)                    # this face only, so faces do not cancel each other
            for eye in _EYES:
                cv2.fillPoly(one, [ring(eye, 1.15)], 255, lineType=cv2.LINE_AA)   # a touch larger: takes in the lash line
            iris = np.zeros((h, w), np.uint8)
            if len(pts) > 477:
                for ids in _IRISES:
                    c = ring(ids)
                    radius = float(np.linalg.norm(c[1:] - c[0], axis=1).mean())
                    cv2.circle(iris, (int(c[0][0]), int(c[0][1])), max(1, round(radius)), 255, -1, lineType=cv2.LINE_AA)
            iris = cv2.min(iris, one)                            # the lids cover part of the iris
            parts["iris_pupil"] = cv2.max(parts["iris_pupil"], iris)
            parts["eye_sclera"] = cv2.max(parts["eye_sclera"], cv2.subtract(one, iris))
            for brow in _BROWS:
                fill("eyebrows", brow, 1.1)
            lips = np.zeros((h, w), np.uint8)
            cv2.fillPoly(lips, [ring(_LIPS_OUT)], 255, lineType=cv2.LINE_AA)
            mouth = np.zeros((h, w), np.uint8)
            cv2.fillPoly(mouth, [ring(_LIPS_IN)], 255, lineType=cv2.LINE_AA)
            parts["lips"] = cv2.max(parts["lips"], cv2.subtract(lips, mouth))
            pale = ((hsv[..., 2] > 105) & (hsv[..., 1] < 110)).astype(np.uint8) * 255      # teeth are bright and not red
            parts["teeth"] = cv2.max(parts["teeth"], cv2.min(mouth, pale))
            lower = np.zeros((h, w), np.uint8)                   # rough: hair found inside the lower face
            cv2.fillPoly(lower, [ring(_LOWER_FACE, 0.9)], 255, lineType=cv2.LINE_AA)
            lower = cv2.subtract(lower, lips)
            beard = (conf[..., 1] > 0.45).astype(np.uint8) * 255
            parts["facial_hair"] = cv2.max(parts["facial_hair"], cv2.min(lower, beard))
    return {k: v.astype(np.float32) / 255 for k, v in parts.items()}


def _sky(img, subject) -> np.ndarray:
    """Approximate sky: bright, smooth, blue or pale, and reaching the top of the frame."""
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)                  # hue 0..360, s and v 0..1 for float input
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    grey = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_RGB2GRAY), (0, 0), 1.5)
    rough = cv2.GaussianBlur(np.hypot(cv2.Sobel(grey, cv2.CV_32F, 1, 0), cv2.Sobel(grey, cv2.CV_32F, 0, 1)), (0, 0), 3)
    blue = (hue > 170) & (hue < 265) & (sat > 0.10) & (val > 0.35)
    pale = (sat < 0.16) & (val > 0.78)
    cand = ((blue | pale) & (rough < 0.045) & (subject < 0.3)).astype(np.uint8)
    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    keep = np.zeros((h, w), np.float32)
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if y <= 2 and area > 0.03 * h * w:                      # touches the top edge and is a real area
            keep[labels == i] = 1
    return keep


def _soft(mask, px) -> np.ndarray:
    return cv2.GaussianBlur(mask.astype(np.float32), (0, 0), px) if px > 0 else mask.astype(np.float32)


def compute(img: np.ndarray) -> dict[str, np.ndarray]:
    """Every mask for one photo (float32 sRGB 0..1 in), each float32 0..1 at WORK_SIDE."""
    h, w = img.shape[:2]
    s = min(1.0, WORK_SIDE / max(h, w))
    small = cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else img
    img8 = (np.clip(small, 0, 1) * 255).astype(np.uint8)
    full8 = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    boxes = [(x0 * s, y0 * s, x1 * s, y1 * s, sc) for (x0, y0, x1, y1, sc) in _faces._detect_boxes(full8)]
    conf = _people(img8, boxes)
    firm = np.clip((conf - 0.35) / 0.3, 0, 1)                   # drop faint "maybe" areas that bleed onto neighbours
    firm = firm * firm * (3 - 2 * firm)
    out = {name: firm[..., i] for i, name in enumerate(_CLASSES)}
    subject = np.clip(1 - conf[..., 0], 0, 1)
    face = _face_parts(img8, boxes, conf)
    eyes = np.clip(face["iris_pupil"] + face["eye_sclera"], 0, 1)
    beard = face["facial_hair"]
    masks = {
        "subject": subject, "background": 1 - subject, "whole_person": subject,
        "face_skin": np.clip(out["face_skin"] - eyes - face["lips"] - face["teeth"] - face["eyebrows"] - beard, 0, 1),
        "body_skin": out["body_skin"], "hair": np.clip(out["hair"] - beard, 0, 1),
        "clothes": out["clothes"],
        "eyes": eyes, "sky": _sky(small, subject), **face,
    }
    fine = ("eyes", "iris_pupil", "eye_sclera", "eyebrows", "lips", "teeth")
    edge = max(1.0, 0.0035 * max(img8.shape[:2]))
    return {k: np.clip(_soft(v, edge * (0.4 if k in fine else 1.0)), 0, 1) for k, v in masks.items()}
