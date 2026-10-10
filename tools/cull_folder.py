"""Cull a folder of RAW files using the camera previews stored inside them.

usage: python -I tools/cull_folder.py RAW_FOLDER OUT.csv [relaxed|normal|strict]
"""
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.images import load_embedded_preview, load_image, RAW_EXT  # noqa: E402
from engine.cull import measure_photo, cull  # noqa: E402

if __name__ == "__main__":
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    level = sys.argv[3] if len(sys.argv) > 3 else "normal"
    items, t0 = [], time.perf_counter()
    for f in sorted(src.iterdir()):
        ext = f.suffix.lower()
        if ext in RAW_EXT:
            img, info = load_embedded_preview(f, 2560)
            if img is None:
                img, info = load_image(f, 2560), {}
        elif ext in (".jpg", ".jpeg"):
            img, info = load_image(f, 2560), {}
        else:
            continue
        items.append((f.name, measure_photo(img, info)))
    results = cull(items, strictness=level)
    dt = time.perf_counter() - t0
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "stars", "result", "reasons", "score", "group", "best_in_group"])
        for r in results:
            w.writerow([r["name"], r["stars"], r["result"], "; ".join(r["reasons"]), r["score"],
                        r.get("group", ""), r.get("best_in_group", "")])
    counts = {k: sum(1 for r in results if r["result"] == k) for k in ("keep", "maybe", "reject")}
    print(f"{len(results)} photos in {dt:.1f}s ({dt / max(len(results), 1):.2f}s each): {counts}")
    for r in results:
        if r["result"] != "keep":
            print(f"  {r['stars']} stars  {r['name']}: {'; '.join(r['reasons'])}")
