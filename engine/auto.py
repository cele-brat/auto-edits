"""Automatic corrections for the toggles: exposure, highlights, shadows, whites,
blacks, white balance, noise, straighten and lens fix.

Each function looks at one photo (float32 sRGB 0..1, preview size) and returns
values in Lightroom's own slider units, so the same numbers drive our preview
and are later sent to Lightroom by the plugin.

These are first-version rules. They are meant to be tuned against photos whose
correct edit is known (for example Bharat's own Lightroom sidecar files).
"""
from __future__ import annotations

import cv2
import numpy as np

from .render import _luma, srgb_to_linear, LUMA

TOGGLES = ("exposure", "highlights", "shadows", "whites", "blacks",
           "white_balance", "noise", "straighten", "lens")

AUTO_PARAMS = {
    "target_mid": 0.56,        # where the photo's middle brightness should sit (display scale 0..1)
    "exposure_strength": 0.6,  # how much of the measured gap is corrected
    "night_strength": 0.45,    # same, for scenes that are mostly dark on purpose
    "face_target": 0.52,       # brightness aimed for on faces
    "face_weight": 0.5,        # how much faces count against the whole frame
    "max_ev": 2.0,
    "wb_strength": 0.85,       # most of the cast is removed; a little of the room's warmth is kept
}


def _small(img, side=512):
    h, w = img.shape[:2]
    s = side / max(h, w)
    return img if s >= 1 else cv2.resize(img, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)


def measure(img) -> dict:
    """Brightness statistics used by the tone rules."""
    y = _luma(_small(img))
    p = np.percentile(y, [0.5, 5, 25, 50, 75, 95, 99, 99.9])
    return {
        "p0_5": float(p[0]), "p5": float(p[1]), "p25": float(p[2]), "median": float(p[3]),
        "p75": float(p[4]), "p95": float(p[5]), "p99": float(p[6]), "p99_9": float(p[7]),
        "dark_fraction": float((y < 0.12).mean()), "bright_fraction": float((y > 0.92).mean()),
        "clipped_fraction": float((y > 0.985).mean()),
    }


def auto_exposure(img, faces=None, P=AUTO_PARAMS) -> float:
    """Exposure change in EV (stops)."""
    small = _small(img)
    y = _luma(small)
    lin = srgb_to_linear(y)
    h, w = y.shape
    yy, xx = np.mgrid[0:h, 0:w]
    centre = np.exp(-(((xx - w / 2) / (0.45 * w)) ** 2 + ((yy - h / 2) / (0.45 * h)) ** 2))   # centre counts more
    lo, hi = np.percentile(y, [3, 97])
    keep = (y >= lo) & (y <= hi)                              # ignore lamps and pure black
    wts = (centre * keep).ravel()
    order = np.argsort(y.ravel())
    cum = np.cumsum(wts[order])
    med = float(y.ravel()[order][np.searchsorted(cum, 0.5 * cum[-1])]) if cum[-1] > 0 else float(np.median(y))
    mid = float(srgb_to_linear(np.array([med], np.float32))[0])   # centre-weighted middle brightness
    target = float(srgb_to_linear(np.array([P["target_mid"]], np.float32))[0])
    ev = np.log2(target / max(mid, 1e-4))

    night = float((y < 0.12).mean()) > 0.5 and float((y > 0.8).mean()) < 0.06   # mostly dark, with small lights
    strength = P["night_strength"] if night else P["exposure_strength"]
    ev *= strength

    if faces:                                                 # faces pull the result toward a good skin level
        vals = []
        for (x0, y0, x1, y1) in faces:
            patch = y[int(y0 * h):max(int(y1 * h), int(y0 * h) + 1), int(x0 * w):max(int(x1 * w), int(x0 * w) + 1)]
            if patch.size:
                vals.append(float(np.median(patch)))
        if vals:
            face_lin = float(srgb_to_linear(np.array([np.median(vals)], np.float32))[0])
            face_t = float(srgb_to_linear(np.array([P["face_target"]], np.float32))[0])
            ev_face = np.log2(face_t / max(face_lin, 1e-4)) * (0.7 if night else 0.9)
            ev = (1 - P["face_weight"]) * ev + P["face_weight"] * ev_face

    # never push large areas into clipping
    p97 = float(np.percentile(lin, 97))
    if ev > 0:
        ev = min(ev, max(0.0, np.log2(0.92 / max(p97, 1e-4))))
    return float(np.clip(round(ev * 20) / 20, -P["max_ev"], P["max_ev"]))      # steps of 0.05 EV


def auto_tone(img, exposure_ev=0.0) -> dict:
    """Highlights, shadows, whites and blacks sliders (-100..100)."""
    if exposure_ev:
        from .render import exposure as _expo, DEFAULT_PARAMS
        img = _expo(_small(img), exposure_ev, DEFAULT_PARAMS)
    m = measure(img)
    # highlights: pull back when a lot of the frame is very bright
    highlights = -min(80.0, 25.0 + 500.0 * m["bright_fraction"]) if m["bright_fraction"] > 0.01 else -min(25.0, 60.0 * max(m["p99"] - 0.8, 0))
    # shadows: open up when a lot of the frame is very dark, less so for night scenes
    night = m["dark_fraction"] > 0.5
    shadows = min(60.0, 140.0 * m["dark_fraction"]) * (0.5 if night else 1.0) if m["dark_fraction"] > 0.05 else 0.0
    # whites: bring the brightest real detail close to white without clipping
    whites = float(np.clip((0.96 - m["p99_9"]) * 160.0, -40, 40)) if m["clipped_fraction"] < 0.01 else -15.0
    # blacks: bring the darkest tones close to black
    blacks = float(np.clip((0.03 - m["p0_5"]) * 400.0, -25, 30))
    r = lambda v: int(round(v))
    return {"Highlights2012": r(highlights), "Shadows2012": r(shadows), "Whites2012": r(whites), "Blacks2012": r(blacks)}


def _light_colour(lin):
    """The colour of the light, as (red gain, blue gain, share of the frame that is near-neutral).

    Things that should be grey or white (shirts, walls, paper, metal) show the light's colour.
    Under a strong cast they do not look grey at all, so one look is not enough: estimate,
    correct, look again for near-neutral areas, and repeat until the estimate settles.
    """
    y = lin @ LUMA
    usable = (y > 0.04) & (lin.max(-1) < 0.92)                # not black, no clipped channel
    if usable.mean() < 0.05:
        return 1.0, 1.0, 0.0
    px = lin[usable].astype(np.float64)
    # first guess from the whole frame, held back because a coloured scene (red lehenga, green lawn) pulls it
    avg = np.power(np.mean(np.power(px, 4), 0), 0.25)
    gain = np.array([(avg[1] / max(avg[0], 1e-5)) ** 0.6, 1.0, (avg[1] / max(avg[2], 1e-5)) ** 0.6])
    share = 0.0
    for limit in (0.30, 0.24, 0.20, 0.18, 0.18):
        fixed = px * gain
        mx, mn = fixed.max(-1), fixed.min(-1)
        sat = (mx - mn) / np.maximum(mx, 1e-4)
        w = np.clip(1 - sat / limit, 0, 1) ** 2               # the closer to neutral, the more it counts
        share = float((sat < limit).mean())
        if share < 0.01:
            break
        w = w * np.sqrt(px @ LUMA)                            # brighter neutrals are cleaner than dark ones
        avg = (px * w[:, None]).sum(0) / w.sum()
        gain = np.array([avg[1] / max(avg[0], 1e-5), 1.0, avg[1] / max(avg[2], 1e-5)])
    return float(gain[0]), float(gain[2]), share


# Skin of every tone sits close to one line when plotted as ln(red/green) against ln(blue/green):
# darker skin is further along it, a green or magenta cast pushes it off to the side. Measured on
# face skin in correctly balanced wedding photos.
_SKIN_SLOPE, _SKIN_AT_ZERO = -1.25, 0.45
_SKIN_SCATTER = 0.03            # how far off the line normal skin can be, either way
_SKIN_WARMEST, _SKIN_COOLEST = 1.0, 0.38      # ln(red/green) of skin stays inside this range in good light


def _skin_colour(lin, skin):
    """Where the face skin sits: (ln red/green, ln blue/green, how sure 0..1). None without faces."""
    if skin is None:
        return None
    if skin.shape[:2] != lin.shape[:2]:
        skin = cv2.resize(skin.astype(np.float32), (lin.shape[1], lin.shape[0]), interpolation=cv2.INTER_AREA)
    px = lin[skin > 0.6]
    if len(px) < 60:
        return None
    y = px @ LUMA
    px = px[(y > np.percentile(y, 25)) & (px.max(-1) < 0.95) & (px.min(-1) > 1e-3)]   # lit skin, not shadow or shine
    if len(px) < 40:
        return None
    x = float(np.median(np.log(px[:, 0] / px[:, 1])))
    z = float(np.median(np.log(px[:, 2] / px[:, 1])))
    return x, z, float(np.clip(len(px) / 400.0, 0.3, 1.0))


def auto_white_balance(img, P=AUTO_PARAMS, skin=None) -> dict:
    """White balance as a relative shift.

    Two witnesses are used. Neutral things (white shirts, walls, metal) give the colour of the
    light. Face skin (skin: the face-skin mask, any size) gives the green-magenta balance and
    stops the result from turning faces too orange or too blue. Where both speak they are
    averaged by how sure each is.

    Returns our preview sliders (temp, tint, -100..100) and the red/blue gains
    behind them; the plugin converts the gains to Kelvin from the as-shot value.
    """
    small = srgb_to_linear(_small(img, 384))
    r_gain, b_gain, share = _light_colour(small)
    sure = float(np.clip(share / 0.08, 0, 1)) if share >= 0.01 else 0.0
    # in logs: warm is red up and blue down; tint is red and blue both up (green down)
    warm = 0.5 * (np.log(r_gain) - np.log(b_gain)) * (0.5 + 0.5 * sure) if sure else 0.0
    tint = 0.5 * (np.log(r_gain) + np.log(b_gain)) if sure else 0.0
    face = _skin_colour(small, skin)
    if face is not None:
        x, z, face_sure = face
        off = (_SKIN_AT_ZERO + _SKIN_SLOPE * x - z) / (1 - _SKIN_SLOPE)       # tint that puts skin back on its line
        off = float(np.sign(off) * max(abs(off) - _SKIN_SCATTER, 0.0))
        w_face, w_neutral = 1.5 * face_sure, sure
        tint = (w_face * off + w_neutral * tint) / (w_face + w_neutral)
        after = x + warm + tint                                # skin's ln(red/green) once corrected
        if after > _SKIN_WARMEST:
            warm -= 0.7 * (after - _SKIN_WARMEST)
        elif after < _SKIN_COOLEST:
            warm += 0.7 * (_SKIN_COOLEST - after)
    elif not sure:                                             # nothing to judge by: leave the photo alone
        return {"temp": 0, "tint": 0, "r_gain": 1.0, "b_gain": 1.0, "confident": False}
    s = P["wb_strength"]
    warm, tint = warm * s, tint * s
    from .render import DEFAULT_PARAMS as RP
    ln2 = float(np.log(2))
    temp_slider = np.clip(warm / ln2 / RP["wb_temp"] * 100, -100, 100)
    tint_slider = np.clip(tint / ln2 / RP["wb_tint"] * 100, -60, 60)
    return {"temp": int(round(temp_slider)), "tint": int(round(tint_slider)),
            "r_gain": round(float(np.exp(warm + tint)), 4), "b_gain": round(float(np.exp(tint - warm)), 4),
            "confident": bool(share > 0.08 or face is not None)}


def measure_noise(img) -> float:
    """Noise level in flat areas (0 = clean; about 0.02 and up is clearly noisy)."""
    y = _luma(img)
    h, w = y.shape
    if max(h, w) > 1400:                                      # noise must be measured near full preview size
        y = y[h // 2 - min(h, 1400) // 2:h // 2 + min(h, 1400) // 2, w // 2 - min(w, 1400) // 2:w // 2 + min(w, 1400) // 2]
    hp = y - cv2.GaussianBlur(y, (0, 0), 1.2)
    grad = cv2.GaussianBlur(np.abs(cv2.Sobel(cv2.GaussianBlur(y, (0, 0), 2.0), cv2.CV_32F, 1, 0)) +
                            np.abs(cv2.Sobel(cv2.GaussianBlur(y, (0, 0), 2.0), cv2.CV_32F, 0, 1)), (0, 0), 3)
    flat = (grad < np.percentile(grad, 30)) & (y > 0.05) & (y < 0.9)
    if flat.sum() < 500:
        return 0.0
    return float(1.4826 * np.median(np.abs(hp[flat] - np.median(hp[flat]))))


def auto_noise(img, iso: int | None = None) -> dict:
    """Noise reduction values. AI Denoise itself is run by Lightroom on the Mac."""
    level = measure_noise(img)
    score = level / 0.02                                       # 1.0 = clearly noisy at preview size
    if iso:
        score = max(score, np.log2(max(iso, 100) / 800.0) / 3.0)   # ISO 800 -> 0, ISO 6400 -> 1
    score = float(np.clip(score, 0, 2))
    return {
        "LuminanceSmoothing": int(round(min(45, 22 * score))),
        "ColorNoiseReduction": int(round(25 + 12 * min(score, 1.5))),
        "ai_denoise": bool(score >= 1.0),                      # worth running Lightroom's AI Denoise
        "noise_level": round(level, 4),
    }


def auto_straighten(img) -> dict:
    """Small rotation that levels the photo, when clear straight lines agree on it."""
    g = (np.clip(_luma(_small(img, 900)), 0, 1) * 255).astype(np.uint8)
    edges = cv2.Canny(cv2.GaussianBlur(g, (0, 0), 1.2), 50, 140)
    min_len = int(0.18 * max(g.shape))
    lines = cv2.HoughLinesP(edges, 1, np.pi / 720, threshold=60, minLineLength=min_len, maxLineGap=8)
    if lines is None:
        return {"CropAngle": 0.0, "confident": False}
    devs, wts = [], []
    for x0, y0, x1, y1 in np.asarray(lines).reshape(-1, 4):
        ang = np.degrees(np.arctan2(y1 - y0, x1 - x0))
        ang = (ang + 90) % 180 - 90                            # -90..90
        dev = ang if abs(ang) <= 45 else ang - np.sign(ang) * 90   # distance to horizontal or vertical
        if abs(dev) <= 6:
            devs.append(dev)
            wts.append(np.hypot(x1 - x0, y1 - y0))
    if len(devs) < 4:
        return {"CropAngle": 0.0, "confident": False}
    devs, wts = np.array(devs), np.array(wts)
    order = np.argsort(devs)
    cum = np.cumsum(wts[order])
    med = float(devs[order][np.searchsorted(cum, cum[-1] / 2)])
    agree = float(wts[np.abs(devs - med) < 0.6].sum() / wts.sum())
    confident = agree > 0.5 and abs(med) >= 0.3
    return {"CropAngle": round(-med, 2) if confident else 0.0, "confident": bool(confident),
            "measured_tilt": round(med, 2), "agreement": round(agree, 2)}


def auto_correct(img, toggles=TOGGLES, faces=None, iso=None, skin=None) -> dict:
    """Automatic values for the switched-on toggles.

    Returns {"settings": Lightroom settings, "manual": preview white balance,
             "info": measurements}. Pass all toggles for Auto Correct.
    """
    toggles = set(toggles)
    unknown = toggles - set(TOGGLES)
    if unknown:
        raise ValueError(f"unknown toggles: {sorted(unknown)}")
    settings, manual, info = {}, {}, {}
    work = img
    if "white_balance" in toggles:
        wb = auto_white_balance(img, skin=skin)
        manual.update(temp=wb["temp"], tint=wb["tint"])
        info["white_balance"] = wb
        from .render import white_balance as _wb, DEFAULT_PARAMS
        work = _wb(_small(img), wb["temp"], wb["tint"], DEFAULT_PARAMS)
    ev = 0.0
    if "exposure" in toggles:
        ev = auto_exposure(work, faces)
        settings["Exposure2012"] = ev
    tone_keys = {"highlights": "Highlights2012", "shadows": "Shadows2012", "whites": "Whites2012", "blacks": "Blacks2012"}
    if toggles & set(tone_keys):
        tone = auto_tone(work, ev)
        for t, key in tone_keys.items():
            if t in toggles:
                settings[key] = tone[key]
    if "noise" in toggles:
        n = auto_noise(img, iso)
        settings["LuminanceSmoothing"] = n["LuminanceSmoothing"]
        settings["ColorNoiseReduction"] = n["ColorNoiseReduction"]
        info["noise"] = n
    if "straighten" in toggles:
        st = auto_straighten(img)
        settings["CropAngle"] = st["CropAngle"]
        info["straighten"] = st
    if "lens" in toggles:
        settings["LensProfileEnable"] = 1
        settings["AutoLateralCA"] = 1
    return {"settings": settings, "manual": manual, "info": info}


def combine(preset_settings: dict, auto: dict) -> tuple[dict, dict]:
    """Preset settings (already stripped of toggled keys) plus the automatic values."""
    merged = dict(preset_settings)
    merged.update(auto["settings"])
    return merged, dict(auto["manual"])
