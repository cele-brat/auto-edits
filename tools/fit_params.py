"""Fit the engine's strength constants to real Lightroom exports.

Uses the same folder layout as tools/accuracy.py and writes the fitted
constants to a JSON file that the engine can load.

usage: python -m tools.fit_params TESTSET data/params.json [--size 256] [--rounds 2]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar
from skimage.color import rgb2lab

from engine.presets import load_folder, effective_settings
from engine.render import render, DEFAULT_PARAMS
from tools.accuracy import load_pairs, no_grain, LIB

# constants worth fitting, with the range each may take
FIT = {
    "clarity": (0.0, 2.5), "texture": (0.0, 2.0), "exposure_luma_mix": (0.0, 1.0),
    "hsl_hue_deg": (5, 60), "hsl_sat": (0.2, 2.0), "hsl_lum": (0.02, 0.8),
    "vibrance_up": (0.05, 1.2), "vibrance_down": (0.3, 1.6), "saturation": (0.5, 1.4),
    "grade_strength": (0.02, 0.6), "grade_lum": (0.0, 0.6),
    "calib_hue_deg": (0, 60), "calib_sat": (0.0, 1.5), "vignette": (0.2, 2.0),
}


def loss(params, pairs):
    total = 0.0
    for settings, curves, base, ref_lab in pairs:
        out = rgb2lab(render(base, settings, curves, params=params))
        total += float(np.sqrt(((out - ref_lab) ** 2).sum(-1)).mean())    # plain Lab distance: fast and smooth
    return total / len(pairs)


def fit(testset, size=256, rounds=2, log=print, start=None):
    library = load_folder(LIB)
    pairs = []
    for preset, _, base, ref in load_pairs(testset, size, library):
        s, c = effective_settings(preset)
        pairs.append((no_grain(s), c, base, rgb2lab(np.clip(ref, 0, 1))))
    if not pairs:
        raise SystemExit("no image pairs found")
    params = dict(DEFAULT_PARAMS, **(start or {}))
    start = loss(params, pairs)
    log(f"{len(pairs)} pairs, starting error {start:.3f}")
    for r in range(rounds):                       # coordinate descent: one constant at a time
        for key, (lo, hi) in FIT.items():
            res = minimize_scalar(lambda v: loss(dict(params, **{key: v}), pairs), bounds=(lo, hi),
                                  method="bounded", options={"xatol": (hi - lo) / 200})
            if res.fun < loss(params, pairs):
                params[key] = round(float(res.x), 4)
        log(f"round {r + 1}: error {loss(params, pairs):.3f}")
    return params, start, loss(params, pairs)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("testset"); ap.add_argument("out")
    ap.add_argument("--size", type=int, default=256); ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--start", help="JSON file of constants to start from")
    a = ap.parse_args()
    params, start, end = fit(a.testset, a.size, a.rounds, start=json.loads(Path(a.start).read_text()) if a.start else None)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(params, indent=1))
    print(f"error {start:.3f} -> {end:.3f}; saved {a.out}")
