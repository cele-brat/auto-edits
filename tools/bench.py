"""Speed test for the preview engine on this machine.

usage: python -m tools.bench [PHOTO]
"""
import sys
import time
import numpy as np
import cv2
from engine.presets import load_folder, effective_settings
from engine.render import render

if __name__ == "__main__":
    lib = load_folder("presets/library")
    if len(sys.argv) > 1:
        from engine.images import load_image
        src = load_image(sys.argv[1])
    else:
        from skimage import data
        src = data.astronaut().astype(np.float32) / 255
    heavy = max(lib, key=lambda p: sum(1 for v in p.settings.values() if isinstance(v, (int, float)) and v))
    s, c = effective_settings(heavy)
    for w, h, what in ((2560, 1707, "proof export"), (1600, 1067, "viewer"), (420, 280, "grid tile")):
        img = cv2.resize(src, (w, h), interpolation=cv2.INTER_AREA)
        render(img, s, c)
        t = time.perf_counter()
        for _ in range(3):
            render(img, s, c)
        dt = (time.perf_counter() - t) / 3
        print(f"{what:13s} {w}x{h}: {dt * 1000:7.0f} ms per render")
