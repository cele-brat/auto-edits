"""Masks you draw yourself, as in Lightroom: Radial Gradient, Linear Gradient and Brush.

A shape is a plain dictionary kept in the look (so it follows "This photo" / "Whole folder"
like every other setting). Positions and sizes are fractions of the picture as it is shown
before cropping (after straightening), so a shape stays on the same spot whatever the
size of the preview or the export.

  radial: {"type": "radial", "cx", "cy", "rx", "ry"  (fractions of width / height),
           "angle" (degrees), "feather" (0..100), "invert", "adj": {...}}
  linear: {"type": "linear", "x0", "y0" (full effect), "x1", "y1" (no effect), "invert", "adj"}
  brush:  {"type": "brush", "strokes": [{"size" (fraction of width), "feather", "flow" (0..100),
           "erase", "pts": [x0, y0, x1, y1, ...]}], "invert", "adj"}
"""
from __future__ import annotations

import math

import cv2
import numpy as np

TYPES = ("radial", "linear", "brush")
MAX_SHAPES = 12
MAX_POINTS = 6000                     # brush points over all strokes of one shape


def _num(v, lo, hi, default=0.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    if v != v:
        return default
    return round(min(hi, max(lo, v)), 4)


def clean(raw, adjust_ranges: dict) -> list[dict]:
    """The drawn masks of a look, every field present and in range, whatever was sent."""
    out = []
    if not isinstance(raw, list):
        return out
    for s in raw[:MAX_SHAPES]:
        if not isinstance(s, dict) or s.get("type") not in TYPES:
            continue
        adj_in = s.get("adj") if isinstance(s.get("adj"), dict) else {}
        adj = {k: _num(adj_in.get(k), lo, hi) for k, (lo, hi) in adjust_ranges.items()}
        shape = {"type": s["type"], "id": str(s.get("id") or "")[:24], "invert": s.get("invert") is True,
                 "adj": {k: v for k, v in adj.items() if v}}
        if s["type"] == "radial":
            shape.update(cx=_num(s.get("cx"), -0.5, 1.5, 0.5), cy=_num(s.get("cy"), -0.5, 1.5, 0.5),
                         rx=_num(s.get("rx"), 0.01, 2, 0.25), ry=_num(s.get("ry"), 0.01, 2, 0.25),
                         angle=_num(s.get("angle"), -180, 180), feather=_num(s.get("feather"), 0, 100, 50))
        elif s["type"] == "linear":
            shape.update(x0=_num(s.get("x0"), -1, 2, 0.5), y0=_num(s.get("y0"), -1, 2, 0.3),
                         x1=_num(s.get("x1"), -1, 2, 0.5), y1=_num(s.get("y1"), -1, 2, 0.6))
        else:
            strokes, total = [], 0
            for st in (s.get("strokes") if isinstance(s.get("strokes"), list) else [])[:200]:
                if not isinstance(st, dict) or not isinstance(st.get("pts"), list):
                    continue
                pts = [_num(v, -0.5, 1.5) for v in st["pts"][: 2 * (MAX_POINTS - total)]]
                pts = pts[: len(pts) // 2 * 2]
                if not pts:
                    continue
                total += len(pts) // 2
                strokes.append({"size": _num(st.get("size"), 0.002, 0.5, 0.05), "feather": _num(st.get("feather"), 0, 100, 50),
                                "flow": _num(st.get("flow"), 1, 100, 100), "erase": st.get("erase") is True, "pts": pts})
                if total >= MAX_POINTS:
                    break
            shape["strokes"] = strokes
        out.append(shape)
    return out


def mask(shape: dict, h: int, w: int) -> np.ndarray:
    """The shape as a 0..1 mask of h x w pixels."""
    t = shape["type"]
    if t == "radial":
        y, x = np.mgrid[0:h, 0:w].astype(np.float32)
        a = math.radians(shape.get("angle", 0.0))
        dx, dy = x / max(1, w - 1) - shape["cx"], y / max(1, h - 1) - shape["cy"]
        # turn into the ellipse's own axes; widths are fractions of the picture's width and height
        u = (dx * math.cos(a) + dy * math.sin(a) * h / w) / shape["rx"]
        v = (-dx * math.sin(a) * w / h + dy * math.cos(a)) / shape["ry"]
        d = np.sqrt(u * u + v * v)
        f = max(0.001, shape.get("feather", 50) / 100.0)
        m = np.clip((1.0 - d) / f, 0, 1)
        m = m * m * (3 - 2 * m)                                  # soft edge
    elif t == "linear":
        y, x = np.mgrid[0:h, 0:w].astype(np.float32)
        px, py = x / max(1, w - 1), y / max(1, h - 1)
        vx, vy = (shape["x1"] - shape["x0"]), (shape["y1"] - shape["y0"])
        vx_px, vy_px = vx * w, vy * h                            # measured in pixels, so the angle is true
        length2 = vx_px * vx_px + vy_px * vy_px or 1.0
        tt = ((px - shape["x0"]) * w * vx_px + (py - shape["y0"]) * h * vy_px) / length2
        m = 1.0 - np.clip(tt, 0, 1)
        m = m * m * (3 - 2 * m)
    else:
        paint = np.zeros((h, w), np.float32)
        erase = np.zeros((h, w), np.float32)
        for st in shape.get("strokes", []):
            layer = np.zeros((h, w), np.float32)
            r = max(1, int(round(st["size"] * w / 2)))
            pts = np.array(st["pts"], np.float32).reshape(-1, 2) * np.array([w - 1, h - 1], np.float32)
            pts = np.round(pts).astype(np.int32)
            hard = max(1, int(round(r * (1 - 0.6 * st["feather"] / 100))))
            if len(pts) == 1:
                cv2.circle(layer, tuple(int(v) for v in pts[0]), hard, 1.0, -1, cv2.LINE_AA)
            else:
                cv2.polylines(layer, [pts.reshape(-1, 1, 2)], False, 1.0, 2 * hard, cv2.LINE_AA)
                for p in (pts[0], pts[-1]):
                    cv2.circle(layer, tuple(int(v) for v in p), hard, 1.0, -1, cv2.LINE_AA)
            soft = r - hard
            if soft > 0:
                layer = cv2.GaussianBlur(layer, (0, 0), soft / 2.0)
                layer = np.clip(layer * 1.25, 0, 1)
            layer *= st["flow"] / 100.0
            target = erase if st["erase"] else paint
            np.maximum(target, layer, out=target)
        m = paint * (1 - erase)
    if shape.get("invert"):
        m = 1.0 - m
    return m.astype(np.float32)
