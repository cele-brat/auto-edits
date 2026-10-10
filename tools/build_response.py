"""Build Lightroom response curves from a calibration export.

For the tone sliders and exposure, the engine does not guess a formula: it uses
the brightness change Lightroom itself produced in the calibration exports
(one setting changed at a time), stored as curves in data/lr_response.json.

usage: python -m tools.build_response CALIB_FOLDER data/lr_response.json
"""
from __future__ import annotations

import json
import sys

import numpy as np
from scipy.ndimage import gaussian_filter1d

from engine.render import _luma
from tools.calibrate import load

BINS = 64
CENTRES = (np.arange(BINS) + 0.5) / BINS

# slider -> (positive variation, negative variation, how it is applied for +, for -)
TONE = {
    "Contrast2012": ("contrast_p40", "contrast_m40", "rgb", "rgb"),
    "Highlights2012": ("highlights_p40", "highlights_m60", "luma", "luma"),
    "Shadows2012": ("shadows_p50", "shadows_m40", "luma", "luma"),
    "Whites2012": ("whites_p40", "whites_m40", "luma", "luma"),
    "Blacks2012": ("blacks_p40", "blacks_m40", "luma", "rgb"),
}


def pooled_curve(items, name, mode):
    """Median output for each input level, pooled over the photos. mode: 'luma' or 'rgb'."""
    its = [i for i in items if i["name"] == name]
    if mode == "luma":
        a = np.concatenate([_luma(i["base"]).ravel() for i in its])
        b = np.concatenate([_luma(i["ref"]).ravel() for i in its])
    else:
        a = np.concatenate([i["base"].ravel() for i in its])
        b = np.concatenate([i["ref"].ravel() for i in its])
    idx = np.clip((a * BINS).astype(int), 0, BINS - 1)
    med = np.full(BINS, np.nan)
    for k in range(BINS):
        m = idx == k
        if m.sum() > 400:
            med[k] = np.median(b[m])
    return med


def clean_delta(med):
    """Smooth change curve (output - input), with the unreliable ends rebuilt.

    Near pure white and pure black the photos are clipped, so the measured
    medians there are not trustworthy; those ends follow the trend just inside.
    """
    delta = med - CENTRES
    ok = ~np.isnan(delta) & (CENTRES > 0.035) & (CENTRES < 0.90)
    x, d = CENTRES[ok], delta[ok]
    d = gaussian_filter1d(d, 1.2, mode="nearest")
    full = np.interp(CENTRES, x, d)
    hi = CENTRES >= 0.90                                   # top end: keep the level reached, never beyond white
    full[hi] = np.minimum(d[-1], 1.0 - CENTRES[hi])
    lo = CENTRES <= 0.035                                  # bottom end: keep the level reached, never below black
    full[lo] = np.maximum(d[0], -CENTRES[lo])
    return full


def exposure_axis(items):
    """An 'exposure position' for every brightness level, from the +1 and -1 EV exports.

    u(x) is built so that Lightroom's +1 EV moves every level exactly one unit
    along it. Any exposure e is then x -> u_inverse(u(x) + e), which carries
    Lightroom's own highlight roll-off instead of a guessed one.
    """
    up = CENTRES + clean_delta(pooled_curve(items, "exposure_p100", "rgb"))
    down = CENTRES + clean_delta(pooled_curve(items, "exposure_m100", "rgb"))
    f_up = lambda v: float(np.interp(v, CENTRES, up))
    f_down = lambda v: float(np.interp(v, CENTRES, down))
    xs, us = [0.45], [0.0]
    x = 0.45
    for k in range(1, 8):                                  # walk up one stop at a time
        nx = f_up(x)
        if nx <= x + 0.004 or nx > 0.985:
            break
        xs.append(nx); us.append(float(k)); x = nx
    x = 0.45
    for k in range(1, 12):                                 # walk down one stop at a time
        nx = f_down(x)
        if nx >= x - 0.002 or nx < 0.004:
            break
        xs.insert(0, nx); us.insert(0, float(-k)); x = nx
    # pin the ends so the axis covers 0..1
    xs = [0.0] + xs + [1.0]
    us = [us[0] - 3.0] + us + [us[-1] + 1.5]
    return {"x": [round(v, 5) for v in xs], "u": us}


def build(folder, size=512):
    items = load(folder, size)
    resp = {"source": "Lightroom calibration export", "bins": BINS, "tone": {}, "photos": sorted({i["photo"] for i in items})}
    for slider, (pos, neg, mode_p, mode_n) in TONE.items():
        resp["tone"][slider] = {
            "pos": {"value": int(pos.split("_p")[-1]), "mode": mode_p,
                    "delta": [round(float(v), 5) for v in clean_delta(pooled_curve(items, pos, mode_p))]},
            "neg": {"value": -int(neg.split("_m")[-1]), "mode": mode_n,
                    "delta": [round(float(v), 5) for v in clean_delta(pooled_curve(items, neg, mode_n))]},
        }
    resp["exposure"] = exposure_axis(items)
    return resp


if __name__ == "__main__":
    r = build(sys.argv[1])
    open(sys.argv[2], "w").write(json.dumps(r))
    print("exposure axis:", list(zip(r["exposure"]["x"], r["exposure"]["u"])))
    for k, v in r["tone"].items():
        print(k, "max |delta| +", round(max(map(abs, v["pos"]["delta"])), 3), "-", round(max(map(abs, v["neg"]["delta"])), 3))
