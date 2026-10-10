"""Calibration: compare the engine with Lightroom one setting at a time.

Reads a folder made by the plug-in's "Make calibration exports" command
(base/ plus one folder per single-setting variation, and manifest.json).

usage: python -m tools.calibrate CALIB_FOLDER [--params params.json] [--size 512]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from skimage.color import rgb2lab, deltaE_ciede2000

from engine.images import load_image
from engine.render import render


def split_settings(s: dict):
    """Manifest settings -> (settings, curves) for render()."""
    s = dict(s)
    curves = {k: [[v[i], v[i + 1]] for i in range(0, len(v), 2)] for k, v in s.items() if k.startswith("ToneCurvePV2012")}
    for k in curves:
        s.pop(k)
    s.pop("ToneCurveName2012", None)
    return s, curves


def load(folder, size=512):
    folder = Path(folder)
    man = json.loads((folder / "manifest.json").read_text().replace(": nil", ": null"))
    wb = {Path(w["file"]).stem: w for w in man.get("white_balance", [])}
    base = {p.stem: load_image(p, size) for p in sorted((folder / "base").glob("*.jpg"))}
    items = []
    for name, spec in man["variations"].items():
        for stem, b in base.items():
            f = folder / name / f"{stem}.jpg"
            w = wb.get(stem)
            if "relative" in spec and (not w or w.get("Temperature") is None):
                continue                      # white balance was not numeric for this photo
            if f.exists():
                ref = load_image(f, size)
                if ref.shape == b.shape:
                    items.append({"name": name, "photo": stem, "spec": spec, "base": b, "ref": ref, "wb": wb.get(stem)})
    return items


def de(a, b):
    return float(deltaE_ciede2000(rgb2lab(np.clip(a, 0, 1)), rgb2lab(np.clip(b, 0, 1))).mean())


def render_item(it, params=None):
    spec = it["spec"]
    if "relative" in spec:
        from engine.render import kelvin_shift_to_manual
        return render(it["base"], {}, manual=kelvin_shift_to_manual(it["wb"], spec["relative"]), params=params)
    s, c = split_settings(spec)
    return render(it["base"], s, c, params=params)


def report(items, params=None):
    rows = {}
    for it in items:
        try:
            out = render_item(it, params)
        except ImportError:
            continue
        r = rows.setdefault(it["name"], {"err": [], "edit": []})
        r["err"].append(de(out, it["ref"]))
        r["edit"].append(de(it["base"], it["ref"]))
    return {k: (float(np.mean(v["err"])), float(np.mean(v["edit"]))) for k, v in rows.items()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("folder"); ap.add_argument("--params"); ap.add_argument("--size", type=int, default=512)
    a = ap.parse_args()
    params = json.loads(Path(a.params).read_text()) if a.params else None
    rep = report(load(a.folder, a.size), params)
    print(f"{'variation':26s} {'error':>6s} {'edit':>6s}  left over")
    for k, (e, ed) in sorted(rep.items(), key=lambda kv: -kv[1][0]):
        print(f"{k:26s} {e:6.2f} {ed:6.2f}  {100 * e / max(ed, 1e-6):5.0f}%")
    print(f"{'MEAN':26s} {np.mean([v[0] for v in rep.values()]):6.2f} {np.mean([v[1] for v in rep.values()]):6.2f}")


# Which engine constant each single-setting variation informs.
STAGE_FITS = {
    "saturation": (("saturation_p40", "saturation_m40"), (0.5, 1.4)),
    "vibrance_up": (("vibrance_p50",), (0.05, 1.2)),
    "vibrance_down": (("vibrance_m50",), (0.3, 1.6)),
    "exposure_luma_mix": (("exposure_p100", "exposure_m100"), (0.0, 1.0)),
    "hsl_sat": (("hsl_sat_red_m60", "hsl_sat_orange_p40", "hsl_sat_blue_m60"), (0.1, 2.0)),
    "hsl_hue_deg": (("hsl_hue_yellow_m40", "hsl_hue_orange_p30", "hsl_hue_blue_p30"), (2, 60)),
    "hsl_lum": (("hsl_lum_orange_p30", "hsl_lum_blue_m40"), (0.0, 0.8)),
    "grade_strength": (("grade_shadow_blue", "grade_highlight_warm", "grade_mid_orange", "grade_balance_p50"), (0.01, 0.6)),
    "grade_lum": (("grade_shadow_lum_m20", "grade_highlight_lum_p20"), (0.0, 0.8)),
    "calib_hue_deg": (("calib_red_hue_p20", "calib_green_hue_m20", "calib_blue_hue_p20"), (0, 90)),
    "calib_sat": (("calib_red_sat_p30", "calib_green_sat_p30", "calib_blue_sat_p40"), (0.0, 2.0)),
    "vignette": (("vignette_m30",), (0.1, 2.5)),
    "clarity": (("clarity_p40", "clarity_m40"), (0.0, 3.0)),
    "texture": (("texture_p50",), (0.0, 3.0)),
}


def fit_stages(items, params=None, log=print):
    """Fit each constant on the variations that isolate it. Returns the updated constants."""
    from scipy.optimize import minimize_scalar
    from engine.render import DEFAULT_PARAMS
    P = dict(DEFAULT_PARAMS, **(params or {}))
    for key, (names, (lo, hi)) in STAGE_FITS.items():
        sub = [it for it in items if it["name"] in names]
        if not sub:
            continue
        refs = [rgb2lab(np.clip(it["ref"], 0, 1)) for it in sub]

        def loss(v):
            q = dict(P, **{key: v})
            return float(np.mean([np.sqrt(((rgb2lab(render_item(it, q)) - r) ** 2).sum(-1)).mean() for it, r in zip(sub, refs)]))
        before = loss(P[key])
        res = minimize_scalar(loss, bounds=(lo, hi), method="bounded", options={"xatol": (hi - lo) / 300})
        if res.fun < before:
            log(f"{key:18s} {P[key]:8.3f} -> {res.x:8.3f}   error {before:.2f} -> {res.fun:.2f}")
            P[key] = round(float(res.x), 4)
        else:
            log(f"{key:18s} {P[key]:8.3f} kept            error {before:.2f}")
    return P
