"""The same look as engine/render.py, made for full-size exports: fast and light on memory.

Every stage that treats each pixel on its own (white balance, exposure, calibration,
tone, curves, colour mixer, vibrance, colour grading) is folded into one colour table
by running the table's points through the real stages, so the two renderers cannot
drift apart. The table is then applied to the 8-bit picture in one pass. Stages that
look at neighbouring pixels or at position (clarity, vignette, grain) are done on
strips of the picture, so a 45-megapixel photo needs a few hundred megabytes instead
of several gigabytes.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

from . import render as R

TABLE = 49                      # points per side of the colour table
STRIP = 384                     # rows handled at a time


def _table(stages) -> ImageFilter.Color3DLUT:
    n = TABLE
    axis = np.linspace(0, 1, n, dtype=np.float32)
    b, g, r = np.meshgrid(axis, axis, axis, indexing="ij")          # red changes fastest, as Pillow expects
    points = np.stack([r, g, b], -1).reshape(n * n, n, 3)
    for stage in stages:
        points = stage(points)
    return ImageFilter.Color3DLUT(n, np.clip(points, 0, 1).astype(np.float32).ravel())


def _apply(img8: np.ndarray, stages) -> np.ndarray:
    return np.asarray(Image.fromarray(img8).filter(_table(stages)))


def _strips(h):
    for y in range(0, h, STRIP):
        yield slice(y, min(h, y + STRIP))


def _to8(x):
    return (np.clip(x, 0, 1) * 255 + 0.5).astype(np.uint8)


def render8(img8: np.ndarray, settings=None, curves=None, manual=None, params=None, seed=0, amount=1.0) -> np.ndarray:
    """render() for an 8-bit picture (H, W, 3 uint8), returning 8 bits."""
    P = dict(R.DEFAULT_PARAMS, **(params or {}))
    settings = dict(settings or {})
    manual = manual or {}
    curves = curves or {}
    ev = R._g(settings, "Exposure2012") + float(manual.get("exposure", 0.0))
    first = [lambda x: R.white_balance(x, manual.get("temp", 0.0), manual.get("tint", 0.0), P),
             lambda x: R.exposure(x, ev, P)]
    before = [lambda x: R.calibration(x, settings, P), lambda x: R.tone(x, settings, P)]
    after = [lambda x: R.parametric(x, settings), lambda x: R.point_curves(x, curves), lambda x: R.colour_mixer(x, settings, P),
             lambda x: R.vibrance_saturation(x, settings, P), lambda x: R.colour_grading(x, settings, P)]
    local = bool(R._g(settings, "Clarity2012") or R._g(settings, "Texture"))
    h, w = img8.shape[:2]
    start = _apply(img8, first) if amount != 1.0 else None

    if not local:
        out = _apply(img8, first + before + after)
    else:
        mid = _apply(img8, first + before)
        # clarity and texture compare each pixel's brightness with its surroundings
        cl, tx = R._g(settings, "Clarity2012") / 100.0, R._g(settings, "Texture") / 100.0
        y = np.empty((h, w), np.float32)
        for s in _strips(h):
            y[s] = R._luma(mid[s].astype(np.float32) / 255)
        change = np.zeros((h, w), np.float32)
        if cl:
            change += cl * P["clarity"] * (y - R._blur(y, P["clarity_radius"]))
        if tx:
            change += tx * P["texture"] * (y - R._blur(y, P["texture_radius"]))
        table = _table(after)
        out = np.empty_like(mid)
        for s in _strips(h):
            ys = y[s]
            y2 = np.clip(ys + change[s] * (4 * np.clip(ys, 0, 1) * (1 - np.clip(ys, 0, 1))), 0, None)
            part = R._apply_luma(mid[s].astype(np.float32) / 255, ys, y2)
            out[s] = np.asarray(Image.fromarray(_to8(part)).filter(table))
        del y, change, mid

    vig = R._g(settings, "PostCropVignetteAmount") / 100.0
    grain = R._g(settings, "GrainAmount") / 100.0
    if not (vig or grain or start is not None):
        return out
    out = np.array(out)                                             # a copy we may write into
    noise = None
    if grain:
        size = R._g(settings, "GrainSize", 25.0) / 100.0
        noise = np.random.default_rng(seed).standard_normal((h, w), dtype=np.float32)
        noise = R.cv2.GaussianBlur(noise, (0, 0), 0.4 + 1.6 * size)
        noise /= max(float(noise.std()), 1e-6)
    if vig:
        midpoint = R._g(settings, "PostCropVignetteMidpoint", 50.0) / 100.0
        feather = R._g(settings, "PostCropVignetteFeather", 50.0) / 100.0
        xx = ((np.arange(w, dtype=np.float32) - (w - 1) / 2) / (w / 2)) ** 2
        yy = ((np.arange(h, dtype=np.float32) - (h - 1) / 2) / (h / 2)) ** 2
        edge = 0.15 + 0.7 * midpoint
    for s in _strips(h):
        part = out[s].astype(np.float32) / 255
        if vig:
            r = np.sqrt((yy[s, None] + xx[None, :]) / 2)
            fall = R._smoothstep(edge - 0.6 * feather - 0.02, edge + 0.6 * feather + 0.02, r)
            part = part * np.clip(1 + vig * P["vignette"] * fall, 0, None)[..., None]
        if grain:
            yl = np.clip(R._luma(part), 0, 1)
            part = part + (noise[s] * (grain * P["grain"] * (0.4 + 2.4 * yl * (1 - yl))))[..., None]
        if start is not None:                                       # preset strength scales the look, not exposure
            base = start[s].astype(np.float32) / 255
            part = base + (part - base) * float(amount)
        out[s] = _to8(part)
    return out
