"""Face finding for culling, auto exposure and (later) masks.

Uses Google's MediaPipe Face Landmarker (Apache 2.0 licence). For each face it
gives a box, how closed each eye is, a smile score and where the eyes look.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

MODEL = Path(__file__).resolve().parent.parent / "models" / "face_landmarker.task"
_landmarker = None


def _get():
    global _landmarker
    if _landmarker is None:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision
        opts = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(MODEL)),
            output_face_blendshapes=True, num_faces=12,
            min_face_detection_confidence=0.4, min_face_presence_confidence=0.4)
        _landmarker = (vision.FaceLandmarker.create_from_options(opts), mp)
    return _landmarker


def _run(img8):
    lm, mp = _get()
    res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(img8)))
    out = []
    for i, pts in enumerate(res.face_landmarks):
        xs = np.array([p.x for p in pts]); ys = np.array([p.y for p in pts])
        bs = {c.category_name: c.score for c in res.face_blendshapes[i]} if res.face_blendshapes else {}
        out.append({
            "box": [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())],
            "blink_left": float(bs.get("eyeBlinkLeft", 0)), "blink_right": float(bs.get("eyeBlinkRight", 0)),
            "smile": float((bs.get("mouthSmileLeft", 0) + bs.get("mouthSmileRight", 0)) / 2),
            "look_away": float(max(bs.get("eyeLookOutLeft", 0), bs.get("eyeLookOutRight", 0),
                                   bs.get("eyeLookInLeft", 0), bs.get("eyeLookInRight", 0))),
            "look_down": float((bs.get("eyeLookDownLeft", 0) + bs.get("eyeLookDownRight", 0)) / 2),
            "jaw_open": float(bs.get("jawOpen", 0)),
        })
    return out


DETECTOR = MODEL.parent / "face_detection_yunet.onnx"      # OpenCV YuNet, MIT licence: finds small faces


def _detect_boxes(img8, score=0.6, max_side=2560):
    """Face boxes in pixels: (x0, y0, x1, y1, score), largest first."""
    h, w = img8.shape[:2]
    s = min(1.0, max_side / max(h, w))
    small = cv2.resize(img8, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA) if s < 1 else img8
    det = cv2.FaceDetectorYN.create(str(DETECTOR), "", (small.shape[1], small.shape[0]), score, 0.3, 200)
    _, res = det.detect(cv2.cvtColor(small, cv2.COLOR_RGB2BGR))
    boxes = []
    for r in (res if res is not None else []):
        x, y, bw, bh = [float(v) / s for v in r[:4]]
        boxes.append((x, y, x + bw, y + bh, float(r[-1])))
    return sorted(boxes, key=lambda b: -(b[2] - b[0]) * (b[3] - b[1]))


def find_faces(img, details: bool = True, max_faces: int = 40) -> list[dict]:
    """Faces in a photo (float32 sRGB 0..1). Boxes are fractions of width / height.

    Step 1 finds every face, including small ones in group and full-length
    shots. Step 2 looks closely at each face for closed eyes, smile and gaze;
    faces too small to judge get "judged": False.
    """
    h, w = img.shape[:2]
    img8 = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    faces = []
    for (x0, y0, x1, y1, sc) in _detect_boxes(img8)[:max_faces]:
        bw, bh = x1 - x0, y1 - y0
        f = {"box": [max(0.0, x0 / w), max(0.0, y0 / h), min(1.0, x1 / w), min(1.0, y1 / h)],
             "score": sc, "pixels": float(min(bw, bh)), "judged": False}
        f["size"] = float((f["box"][2] - f["box"][0]) * (f["box"][3] - f["box"][1]))
        if details and min(bw, bh) >= 36:                        # below this the eyes are only a few pixels
            m = 0.45                                             # margin so the whole head is in the crop
            cx0, cy0 = int(max(0, x0 - m * bw)), int(max(0, y0 - m * bh))
            cx1, cy1 = int(min(w, x1 + m * bw)), int(min(h, y1 + m * bh))
            crop = img8[cy0:cy1, cx0:cx1]
            k = 384 / max(crop.shape[:2])
            crop = cv2.resize(crop, (max(1, round(crop.shape[1] * k)), max(1, round(crop.shape[0] * k))),
                              interpolation=cv2.INTER_CUBIC if k > 1 else cv2.INTER_AREA)
            got = _run(crop)
            if got:
                g = max(got, key=lambda q: (q["box"][2] - q["box"][0]) * (q["box"][3] - q["box"][1]))
                f.update({k2: v for k2, v in g.items() if k2 != "box"})
                f["judged"] = True
        faces.append(f)
    return faces
