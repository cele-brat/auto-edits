"""Before / Auto Correct pairs for a folder of preview JPEGs.

usage: python -m tools.auto_sheet PREVIEW_FOLDER OUT.jpg
"""
import sys
from pathlib import Path
import cv2
import numpy as np
from engine.images import load_image, save_jpeg
from engine.auto import auto_correct
from engine.render import render


def fit(img, w=520, h=360):
    s = min(w / img.shape[1], h / img.shape[0])
    im = cv2.resize(img, (round(img.shape[1] * s), round(img.shape[0] * s)), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((h, w, 3), np.float32)
    y0, x0 = (h - im.shape[0]) // 2, (w - im.shape[1]) // 2
    canvas[y0:y0 + im.shape[0], x0:x0 + im.shape[1]] = im
    return canvas


def text(img, s):
    c = (np.clip(img, 0, 1) * 255).astype(np.uint8)[..., ::-1].copy()
    cv2.putText(c, s, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(c, s, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
    return c[..., ::-1].astype(np.float32) / 255


if __name__ == "__main__":
    rows = []
    for f in sorted(Path(sys.argv[1]).glob("*.jpg")):
        img = load_image(f, 1200)
        a = auto_correct(img)
        s = a["settings"]
        out = render(img, s, manual=a["manual"])
        label = f"AUTO EV {s['Exposure2012']:+.2f} H{s['Highlights2012']} S{s['Shadows2012']} W{s['Whites2012']} B{s['Blacks2012']} WB {a['manual']['temp']:+d}/{a['manual']['tint']:+d}"
        rows.append(np.concatenate([text(fit(img), f.stem), text(fit(out), label)], 1))
    while len(rows) % 2:
        rows.append(np.zeros_like(rows[0]))
    sheet = np.concatenate([np.concatenate(rows[i:i + 2], 1) for i in range(0, len(rows), 2)], 0)
    save_jpeg(sheet, sys.argv[2], 85)
    print(sheet.shape)
