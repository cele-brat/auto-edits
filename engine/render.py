"""Auto Edits preview engine, v0.

Applies Lightroom-style develop settings (the ones used by slider presets) to
an image. This is our own approximation of Lightroom's rendering, NOT Adobe's
engine. Every strength constant lives in DEFAULT_PARAMS so it can be fitted
against real Lightroom exports by tools/fit_params.py.

Image convention: float32 array, shape (H, W, 3), sRGB with display gamma,
values 0..1.

Order of operations:
  white balance -> exposure -> calibration -> contrast / highlights / shadows /
  whites / blacks -> texture / clarity -> point curves -> colour mixer (HSL) ->
  vibrance / saturation -> colour grading -> vignette -> grain
"""
from __future__ import annotations

import cv2
import numpy as np
from scipy.interpolate import PchipInterpolator

DEFAULT_PARAMS = {
    # tone
    "exposure_shoulder": 0.85,   # where highlight roll-off starts after an exposure push
    "contrast": 0.55,
    "highlights": 0.30,
    "shadows": 0.30,
    "whites": 0.22,
    "blacks": 0.22,
    "tone_radius": 0.02,         # blur radius for highlight/shadow masks, as a fraction of the diagonal
    # local contrast
    "clarity": 0.9,
    "clarity_radius": 0.015,
    "texture": 0.7,
    "texture_radius": 0.003,
    # colour mixer
    "hsl_hue_deg": 30.0,         # degrees of hue shift at slider 100
    "hsl_sat": 1.0,              # saturation gain at slider 100
    "hsl_lum": 0.25,             # luminance change at slider 100 (for a fully saturated colour)
    # global colour
    "vibrance": 0.7,
    "saturation": 1.0,
    # colour grading
    "grade_strength": 0.22,      # tint added at saturation 100
    "grade_lum": 0.20,           # luminance change at Lum 100
    # calibration
    "calib_hue_deg": 20.0,
    "calib_sat": 0.45,
    # effects
    "vignette": 0.75,
    "grain": 0.10,
    # manual white balance
    "wb_temp": 0.35,             # R/B gain (in stops) at manual temp slider 100
    "wb_tint": 0.25,
}

# Hue centres of the eight colour mixer bands, in degrees.
HSL_BANDS = (("Red", 0), ("Orange", 30), ("Yellow", 60), ("Green", 120),
             ("Aqua", 180), ("Blue", 240), ("Purple", 270), ("Magenta", 300))

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
_N = 65535
_GRID = np.linspace(0, 1, _N + 1, dtype=np.float64)
_DEC = np.where(_GRID <= 0.04045, _GRID / 12.92, ((_GRID + 0.055) / 1.055) ** 2.4).astype(np.float32)
_ENC = np.where(_GRID <= 0.0031308, _GRID * 12.92, 1.055 * np.power(_GRID, 1 / 2.4) - 0.055).astype(np.float32)


def _lut(x, table):
    """Look up values 0..1 in a 65536-entry table (values outside are clamped)."""
    idx = np.clip(x, 0, 1) * _N + 0.5
    return table[idx.astype(np.uint16)]


def srgb_to_linear(x):
    return _lut(x, _DEC)


def linear_to_srgb(x):
    return _lut(x, _ENC)


def _luma(img):
    return img[..., 0] * LUMA[0] + img[..., 1] * LUMA[1] + img[..., 2] * LUMA[2]


def _smoothstep(lo, hi, x):
    t = np.clip((x - lo) / (hi - lo), 0, 1)
    return t * t * (3 - 2 * t)


def _blur(channel, radius_frac):
    h, w = channel.shape[:2]
    sigma = max(0.5, radius_frac * float(np.hypot(h, w)))
    f = int(min(8, max(1, sigma // 3)))            # shrink first when the blur is wide
    if f == 1:
        return cv2.GaussianBlur(channel, (0, 0), sigma)
    small = cv2.resize(channel, (max(1, w // f), max(1, h // f)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / f)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _apply_luma(img, y_old, y_new):
    """Move the image to a new luminance while keeping hue."""
    ratio = y_new / np.maximum(y_old, 1e-4)
    scaled = img * ratio[..., None]
    shifted = img + (y_new - y_old)[..., None]
    t = _smoothstep(0.0, 0.08, y_old)[..., None]   # near black a ratio is unstable: add instead
    return shifted * (1 - t) + scaled * t


def _rgb_to_hsv(img):
    hsv = cv2.cvtColor(np.ascontiguousarray(img, np.float32), cv2.COLOR_RGB2HSV)   # H 0..360, S 0..1, V
    return hsv[..., 0], hsv[..., 1], hsv[..., 2]


def _hsv_to_rgb(h, s, v):
    hsv = np.stack([np.mod(h, 360.0), s, v], -1).astype(np.float32)
    if hsv.ndim == 2:
        return cv2.cvtColor(hsv[None], cv2.COLOR_HSV2RGB)[0]
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)


def _hue_to_rgb(hue_deg):
    one = np.ones(1, np.float32)
    return _hsv_to_rgb(np.full(1, hue_deg, np.float32), one, one)[0]


def _g(settings, key, default=0.0):
    v = settings.get(key, default)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


# --------------------------------------------------------------------------
# stages
# --------------------------------------------------------------------------
def white_balance(img, temp, tint, P):
    """Manual, relative white balance. temp and tint are sliders -100..100."""
    if not temp and not tint:
        return img
    lin = srgb_to_linear(img)
    t = temp / 100.0 * P["wb_temp"]
    g = tint / 100.0 * P["wb_tint"]
    gains = np.array([2 ** t, 2 ** (-g), 2 ** (-t)], np.float32)   # +temp warmer, +tint magenta
    gains /= gains @ LUMA                                          # keep brightness
    return linear_to_srgb(np.minimum(lin * gains, 1.0))


def exposure(img, ev, P):
    if not ev:
        return img
    lin = _DEC.astype(np.float64) * (2.0 ** ev)
    s = P["exposure_shoulder"]
    over = np.maximum(lin - s, 0)
    lin = np.where(lin > s, s + (1 - s) * (1 - np.exp(-over / (1 - s))), lin)   # soft roll-off, never clips hard
    table = np.interp(lin, _GRID, _ENC).astype(np.float32)                      # whole stage as one lookup
    return _lut(img, table)


def calibration(img, settings, P):
    """Camera calibration panel: hue and saturation of the three primaries."""
    vals = [(_g(settings, c + "Hue"), _g(settings, c + "Saturation")) for c in ("Red", "Green", "Blue")]
    if not any(h or s for h, s in vals):
        return img
    cols = []
    for (hue, sat), base in zip(vals, (0.0, 120.0, 240.0)):
        prim = _hue_to_rgb(base + hue / 100.0 * P["calib_hue_deg"])
        grey = float(prim @ LUMA)
        cols.append(grey + (prim - grey) * (1 + sat / 100.0 * P["calib_sat"]))
    M = np.stack(cols, 1)
    M = M / M.sum(1, keepdims=True)            # neutral greys stay neutral
    lin = srgb_to_linear(img)
    return linear_to_srgb(cv2.transform(lin, M.astype(np.float32)))


def tone(img, settings, P):
    c = _g(settings, "Contrast2012") / 100.0
    hi = _g(settings, "Highlights2012") / 100.0
    sh = _g(settings, "Shadows2012") / 100.0
    wh = _g(settings, "Whites2012") / 100.0
    bl = _g(settings, "Blacks2012") / 100.0
    if not any((c, hi, sh, wh, bl)):
        return img
    y = _luma(img)
    y2 = y.copy()
    if hi or sh:
        yb = _blur(y, P["tone_radius"])
        if hi:
            y2 += hi * P["highlights"] * _smoothstep(0.35, 0.9, yb) * (y * (1.3 - y) / 0.4225)
        if sh:
            y2 += sh * P["shadows"] * (1 - _smoothstep(0.1, 0.65, yb)) * ((1 - y) * (y + 0.3) / 0.4225)
    if wh:
        y2 += wh * P["whites"] * y ** 3
    if bl:
        y2 += bl * P["blacks"] * (1 - y) ** 3
    if c:
        y2 = y2 + c * P["contrast"] * (y2 - 0.46) * 4 * np.clip(y2, 0, 1) * (1 - np.clip(y2, 0, 1))
    return _apply_luma(img, y, np.clip(y2, 0, None))


def local_contrast(img, settings, P):
    cl = _g(settings, "Clarity2012") / 100.0
    tx = _g(settings, "Texture") / 100.0
    if not cl and not tx:
        return img
    y = _luma(img)
    mid = 4 * np.clip(y, 0, 1) * (1 - np.clip(y, 0, 1))      # protect deep shadows and bright highlights
    y2 = y.copy()
    if cl:
        y2 += cl * P["clarity"] * (y - _blur(y, P["clarity_radius"])) * mid
    if tx:
        y2 += tx * P["texture"] * (y - _blur(y, P["texture_radius"])) * mid
    return _apply_luma(img, y, np.clip(y2, 0, None))


def _curve_lut(points, size=_N + 1):
    pts = sorted((float(x), float(y)) for x, y in points)
    xs, ys = zip(*pts)
    if len(xs) < 2:
        return None
    if len(xs) == 2:
        f = lambda v: np.interp(v, xs, ys)
    else:
        f = PchipInterpolator(xs, ys, extrapolate=True)
    grid = np.linspace(0, 255, size)
    lut = np.asarray(f(grid), np.float32)
    lut = np.where(grid < xs[0], ys[0], np.where(grid > xs[-1], ys[-1], lut))
    return np.clip(lut / 255.0, 0, 1).astype(np.float32)


def _is_identity(points):
    return [list(map(float, p)) for p in points] == [[0.0, 0.0], [255.0, 255.0]]


def point_curves(img, curves):
    if not curves:
        return img
    out = np.clip(img, 0, 1)
    master = curves.get("ToneCurvePV2012")
    luts = [None, None, None]
    for i, key in enumerate(("ToneCurvePV2012Red", "ToneCurvePV2012Green", "ToneCurvePV2012Blue")):
        if curves.get(key) and not _is_identity(curves[key]):
            luts[i] = _curve_lut(curves[key])
    m = _curve_lut(master) if master and not _is_identity(master) else None
    if m is None and not any(l is not None for l in luts):
        return img
    if not any(l is not None for l in luts):
        return _lut(out, m)
    out = out.copy()
    for i in range(3):
        ch = out[..., i]
        if m is not None:
            ch = _lut(ch, m)
        if luts[i] is not None:
            ch = _lut(ch, luts[i])
        out[..., i] = ch
    return out


def colour_mixer(img, settings, P):
    adj = {name: (_g(settings, "HueAdjustment" + name), _g(settings, "SaturationAdjustment" + name),
                  _g(settings, "LuminanceAdjustment" + name)) for name, _ in HSL_BANDS}
    if not any(any(v) for v in adj.values()):
        return img
    h, s, v = _rgb_to_hsv(np.clip(img, 0, 1))
    centres = np.array([c for _, c in HSL_BANDS], np.float32)
    hue_grid = np.arange(0, 360, 0.25, dtype=np.float32)       # the adjustments depend on hue only
    t_h = np.zeros_like(hue_grid); t_s = np.zeros_like(hue_grid); t_l = np.zeros_like(hue_grid)
    for i, (name, centre) in enumerate(HSL_BANDS):
        hue, sat, lum = adj[name]
        if not (hue or sat or lum):
            continue
        prev_c = centres[i - 1] - (360 if i == 0 else 0)
        next_c = centres[(i + 1) % 8] + (360 if i == 7 else 0)
        d = (hue_grid - centre + 180) % 360 - 180   # signed distance to this band's centre
        w = np.where(d < 0, 1 - d / (prev_c - centre), 1 - d / (next_c - centre))
        w = np.clip(w, 0, 1)                         # triangular weights: neighbours sum to 1
        t_h += w * hue / 100.0 * P["hsl_hue_deg"]
        t_s += w * sat / 100.0 * P["hsl_sat"]
        t_l += w * lum / 100.0 * P["hsl_lum"]
    idx = np.minimum((h * 4).astype(np.int32), len(hue_grid) - 1)
    dh, ds, dl = t_h[idx], t_s[idx], t_l[idx]
    out = _hsv_to_rgb(h + dh, np.clip(s * (1 + ds), 0, 1), v)
    if np.any(dl):
        y = _luma(out)
        out = _apply_luma(out, y, np.clip(y + dl * s * (0.3 + y), 0, None))   # grey pixels are not affected
    return out


def vibrance_saturation(img, settings, P):
    vib = _g(settings, "Vibrance") / 100.0
    sat = _g(settings, "Saturation") / 100.0
    if not vib and not sat:
        return img
    y = _luma(img)[..., None]
    out = img
    if vib:
        r, g, b = out[..., 0], out[..., 1], out[..., 2]
        mx = np.maximum(np.maximum(r, g), b)[..., None]
        mn = np.minimum(np.minimum(r, g), b)[..., None]
        s = (mx - mn) / np.maximum(mx, 1e-4)
        out = y + (out - y) * (1 + vib * P["vibrance"] * (1 - s))   # weaker on already-saturated colours
    if sat:
        y2 = _luma(out)[..., None]
        out = y2 + (out - y2) * (1 + sat * P["saturation"])
    return np.clip(out, 0, None)


def colour_grading(img, settings, P):
    regions = {
        "shadow": (_g(settings, "SplitToningShadowHue"), _g(settings, "SplitToningShadowSaturation"), _g(settings, "ColorGradeShadowLum")),
        "mid": (_g(settings, "ColorGradeMidtoneHue"), _g(settings, "ColorGradeMidtoneSat"), _g(settings, "ColorGradeMidtoneLum")),
        "high": (_g(settings, "SplitToningHighlightHue"), _g(settings, "SplitToningHighlightSaturation"), _g(settings, "ColorGradeHighlightLum")),
        "global": (_g(settings, "ColorGradeGlobalHue"), _g(settings, "ColorGradeGlobalSat"), _g(settings, "ColorGradeGlobalLum")),
    }
    if not any(sat or lum for _, sat, lum in regions.values()):
        return img
    blend = _g(settings, "ColorGradeBlending", 50.0) / 100.0
    balance = _g(settings, "SplitToningBalance") / 100.0
    y_img = _luma(img)
    y = _GRID.astype(np.float32)                 # tables over luminance 0..1
    pivot = 0.5 - 0.25 * balance                 # positive balance gives more of the image to highlights
    width = 0.15 + 0.35 * blend                  # blending widens the overlap between regions
    w_high = _smoothstep(pivot - width, pivot + width, y)
    w_shadow = 1 - w_high
    w_mid = np.clip(1 - np.abs(y - pivot) / (0.25 + 0.25 * blend), 0, 1)
    weights = {"shadow": w_shadow * (1 - 0.5 * w_mid), "mid": w_mid, "high": w_high * (1 - 0.5 * w_mid),
               "global": np.ones_like(y)}
    offset = np.zeros((len(y), 3), np.float32)
    dl = np.zeros_like(y)
    for name, (hue, sat, lum) in regions.items():
        w = weights[name]
        if sat:
            tint = _hue_to_rgb(hue)
            tint = tint - float(tint @ LUMA)     # colour only, no brightness change
            offset += (w * sat / 100.0 * P["grade_strength"])[:, None] * tint
        if lum:
            dl += w * lum / 100.0 * P["grade_lum"]
    idx = (np.clip(y_img, 0, 1) * _N + 0.5).astype(np.uint16)
    out = img + offset[idx]
    if np.any(dl):
        dl_table = (dl * 4 * y * (1 - y) + dl * 0.2).astype(np.float32)
        y0 = _luma(out)
        out = _apply_luma(out, y0, np.clip(y0 + dl_table[idx], 0, None))
    return np.clip(out, 0, None)


def vignette(img, settings, P):
    amount = _g(settings, "PostCropVignetteAmount") / 100.0
    if not amount:
        return img
    midpoint = _g(settings, "PostCropVignetteMidpoint", 50.0) / 100.0
    feather = _g(settings, "PostCropVignetteFeather", 50.0) / 100.0
    h, w = img.shape[:2]
    xx = ((np.arange(w, dtype=np.float32) - (w - 1) / 2) / (w / 2)) ** 2
    yy = ((np.arange(h, dtype=np.float32) - (h - 1) / 2) / (h / 2)) ** 2
    r = np.sqrt((yy[:, None] + xx[None, :]) / 2)     # 0 at the centre, 1 in the corners
    start = 0.15 + 0.7 * midpoint
    fall = _smoothstep(start - 0.6 * feather - 0.02, start + 0.6 * feather + 0.02, r)
    gain = 1 + amount * P["vignette"] * fall
    return img * np.clip(gain, 0, None)[..., None]


def grain(img, settings, P, seed=0):
    amount = _g(settings, "GrainAmount") / 100.0
    if not amount:
        return img
    size = _g(settings, "GrainSize", 25.0) / 100.0
    h, w = img.shape[:2]
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal((h, w)).astype(np.float32)
    noise = cv2.GaussianBlur(noise, (0, 0), 0.4 + 1.6 * size)
    noise /= max(float(noise.std()), 1e-6)
    y = np.clip(_luma(img), 0, 1)
    strength = amount * P["grain"] * (0.4 + 2.4 * y * (1 - y))     # strongest in the midtones
    return img + (noise * strength)[..., None]


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
def render(img, settings=None, curves=None, manual=None, params=None, seed=0, amount=1.0):
    """Render an image with develop settings.

    img      float32 (H, W, 3) sRGB 0..1
    settings Lightroom setting names -> values (see engine/presets.py)
    curves   point curves from the preset
    manual   {"exposure": EV, "temp": -100..100, "tint": -100..100}, added on top
    amount   preset strength, 1.0 = as designed (0..2)
    """
    P = dict(DEFAULT_PARAMS, **(params or {}))
    settings = dict(settings or {})
    manual = manual or {}
    out = np.asarray(img, np.float32)
    base = out

    out = white_balance(out, manual.get("temp", 0.0), manual.get("tint", 0.0), P)
    out = exposure(out, _g(settings, "Exposure2012") + float(manual.get("exposure", 0.0)), P)
    start = out
    out = calibration(out, settings, P)
    out = tone(out, settings, P)
    out = local_contrast(out, settings, P)
    out = point_curves(out, curves or {})
    out = colour_mixer(out, settings, P)
    out = vibrance_saturation(out, settings, P)
    out = colour_grading(out, settings, P)
    out = vignette(out, settings, P)
    out = grain(out, settings, P, seed)
    if amount != 1.0:
        out = start + (out - start) * float(amount)     # strength scales the look, not exposure or white balance
    return np.clip(out, 0, 1).astype(np.float32)
