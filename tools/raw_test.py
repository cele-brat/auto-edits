"""Decode RAW files, report timing, and save preview-size JPEGs.

usage: python -I tools/raw_test.py RAW_FOLDER OUT_FOLDER [max_side]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import rawpy  # noqa: E402
from engine.images import load_image, resize_max, save_jpeg, RAW_EXT  # noqa: E402

if __name__ == "__main__":
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    side = int(sys.argv[3]) if len(sys.argv) > 3 else 2560
    out.mkdir(parents=True, exist_ok=True)
    for f in sorted(src.iterdir()):
        if f.suffix.lower() not in RAW_EXT:
            continue
        try:
            with rawpy.imread(str(f)) as raw:
                cam = f"{raw.sizes.width}x{raw.sizes.height}"
                wb = [round(x, 2) for x in raw.camera_whitebalance[:3]]
            t = time.perf_counter()
            img = load_image(f)
            dt = time.perf_counter() - t
            small = resize_max(img, side)
            save_jpeg(small, out / (f.stem + ".jpg"), 92)
            print(f"{f.name:28s} {cam:11s} decode {dt:5.1f}s  mean {small.mean():.3f}  wb {wb}")
        except Exception as e:  # report and carry on
            print(f"{f.name:28s} FAILED: {type(e).__name__}: {e}")
