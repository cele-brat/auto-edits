"""Render one photo in several presets on a single sheet.

usage: python -m tools.contact_sheet PHOTO OUT.jpg [preset-id-or-name-fragment ...]
"""
import sys
import cv2
import numpy as np
from engine.images import load_image, save_jpeg
from engine.presets import load_folder, effective_settings
from engine.render import render


def label(img, text):
    out = (np.clip(img, 0, 1) * 255).astype(np.uint8)[..., ::-1].copy()
    cv2.rectangle(out, (0, 0), (out.shape[1], 26), (0, 0, 0), -1)
    cv2.putText(out, text[:60], (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return out[..., ::-1].astype(np.float32) / 255


def sheet(photo, presets, cols=3, tile=420):
    img = load_image(photo, tile) if isinstance(photo, str) else photo
    tiles = [label(img, "BEFORE")]
    for p in presets:
        s, c = effective_settings(p)
        tiles.append(label(render(img, s, c), p.display_name + " | " + p.pack))
    h, w = tiles[0].shape[:2]
    rows = -(-len(tiles) // cols)
    canvas = np.zeros((rows * h, cols * w, 3), np.float32)
    for i, t in enumerate(tiles):
        canvas[(i // cols) * h:(i // cols + 1) * h, (i % cols) * w:(i % cols + 1) * w] = t
    return canvas


if __name__ == "__main__":
    lib = load_folder("presets/library")
    wanted = sys.argv[3:]
    chosen = [p for w in wanted for p in lib if w in (p.id, ) or w.lower() in p.display_name.lower()][:11] or lib[:5]
    save_jpeg(sheet(sys.argv[1], chosen), sys.argv[2])
    print(len(chosen), "presets ->", sys.argv[2])
