"""Accuracy test: our engine against real Lightroom exports.

Folder layout expected (JPEG exports from Lightroom, same file names in every folder):

    TESTSET/
      base/                         exported with NO preset (Lightroom default rendering)
      Cele-Brat MG 01 Signature/    exported with that preset applied
      CBV-GL-AL-03-P-5DCF7631/      a folder may also be named by Product ID

usage: python -m tools.accuracy TESTSET OUT_DIR [--params params.json] [--size 1024]

Reports CIEDE2000 colour difference (dE). Rough guide: under 2 is hard to see,
2 to 5 is visible side by side, over 5 is clearly different.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np
from skimage.color import rgb2lab, deltaE_ciede2000

from engine.images import load_image, save_jpeg
from engine.presets import load_folder, effective_settings
from engine.render import render

IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
LIB = Path(__file__).resolve().parent.parent / "presets" / "library"


def find_preset(folder_name, library):
    key = folder_name.strip().lower()
    key = key[len("cele-brat "):] if key.startswith("cele-brat ") else key
    hits = [p for p in library if folder_name == p.id or key == p.display_name.lower()]
    if not hits:
        hits = [p for p in library if folder_name == p.name]
    if not hits:
        hits = [p for p in library if key in p.display_name.lower() or key in p.name.lower()]
    if len(hits) != 1:
        raise SystemExit(f"folder '{folder_name}' matches {len(hits)} presets; name it by Product ID (CBV-...)")
    return hits[0]


def delta_e(a, b):
    return deltaE_ciede2000(rgb2lab(np.clip(a, 0, 1)), rgb2lab(np.clip(b, 0, 1)))


def no_grain(settings):
    s = dict(settings)
    s["GrainAmount"] = 0          # grain is random, so it is left out of the comparison
    return s


def load_pairs(testset, size, library):
    """Yield (preset, file name, base image, Lightroom image)."""
    testset = Path(testset)
    base_dir = testset / "base"
    if not base_dir.is_dir():
        raise SystemExit("missing 'base' folder (Lightroom exports with no preset)")
    for folder in sorted(p for p in testset.iterdir() if p.is_dir() and p.name != "base"):
        preset = find_preset(folder.name, library)
        for f in sorted(folder.iterdir()):
            if f.suffix.lower() not in IMG_EXT:
                continue
            b = next((x for x in base_dir.iterdir() if x.stem == f.stem and x.suffix.lower() in IMG_EXT), None)
            if b is None:
                print("no base image for", f.name)
                continue
            base = load_image(b, size)
            ref = load_image(f, size)
            if ref.shape != base.shape:
                ref = cv2.resize(ref, (base.shape[1], base.shape[0]), interpolation=cv2.INTER_AREA)
            yield preset, f.name, base, ref


def side_by_side(base, ref, ours, de, title):
    heat = cv2.applyColorMap((np.clip(de / 10.0, 0, 1) * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)[..., ::-1] / 255.0
    row = np.concatenate([base, ref, ours, heat.astype(np.float32)], 1)
    bar = np.zeros((30, row.shape[1], 3), np.uint8)
    w = base.shape[1]
    for i, t in enumerate(("BEFORE", "LIGHTROOM", "AUTO EDITS", "DIFFERENCE (bright = dE 10+)")):
        cv2.putText(bar, t, (i * w + 8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(bar, title, (w + 160, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1, cv2.LINE_AA)
    return np.concatenate([bar.astype(np.float32) / 255, row], 0)


def run(testset, out_dir, params=None, size=1024, sheets=True):
    library = load_folder(LIB)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for preset, name, base, ref in load_pairs(testset, size, library):
        s, c = effective_settings(preset)
        ours = render(base, no_grain(s), c, params=params)
        de = delta_e(ours, ref)
        edit = delta_e(base, ref)                 # how big the Lightroom edit itself is
        rows.append({
            "preset_id": preset.id, "preset": preset.name, "photo": name,
            "dE_mean": round(float(de.mean()), 2), "dE_p95": round(float(np.percentile(de, 95)), 2),
            "edit_size_dE": round(float(edit.mean()), 2),
            "explained_pct": round(100 * (1 - float(de.mean()) / max(float(edit.mean()), 1e-6)), 1),
            "brightness_diff": round(float(ours.mean() - ref.mean()), 4),
        })
        if sheets:
            save_jpeg(side_by_side(base, ref, ours, de, f"{preset.name} | {name} | dE {de.mean():.1f}"),
                      out_dir / f"{preset.id}__{Path(name).stem}.jpg", 88)
    if not rows:
        raise SystemExit("no image pairs found")
    with open(out_dir / "accuracy.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    m = np.array([r["dE_mean"] for r in rows])
    summary = {
        "pairs": len(rows), "presets": len({r["preset_id"] for r in rows}),
        "dE_mean": round(float(m.mean()), 2), "dE_median": round(float(np.median(m)), 2),
        "dE_worst": round(float(m.max()), 2),
        "pairs_under_2": int((m < 2).sum()), "pairs_2_to_5": int(((m >= 2) & (m < 5)).sum()),
        "pairs_over_5": int((m >= 5).sum()),
        "edit_size_dE_mean": round(float(np.mean([r["edit_size_dE"] for r in rows])), 2),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary, rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("testset"); ap.add_argument("out_dir")
    ap.add_argument("--params"); ap.add_argument("--size", type=int, default=1024)
    a = ap.parse_args()
    params = json.loads(Path(a.params).read_text()) if a.params else None
    summary, _ = run(a.testset, a.out_dir, params, a.size)
    print(json.dumps(summary, indent=1))
