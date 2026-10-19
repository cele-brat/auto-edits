"""Automatic culling: rate each photo keep (3 stars), maybe (2) or rejected (1).

Every check can be switched off, and nothing is ever deleted. Doubtful cases
go to "maybe" so a person decides; only clear failures are rejected.
"""
from __future__ import annotations

import cv2
import numpy as np

from .render import _luma
from .faces import find_faces

CHECKS = ("focus", "motion_blur", "exposure", "closed_eyes", "group_eyes", "expression",
          "framing", "accidental", "similar")
STARS = {"keep": 3, "maybe": 2, "reject": 1}

# thresholds per strictness; sharpness numbers come from sharpness() below
LEVELS = {
    "relaxed": {"sharp_reject": 0.040, "sharp_maybe": 0.060, "blink": 0.80, "clip_reject": 0.60, "clip_maybe": 0.40},
    "normal":  {"sharp_reject": 0.050, "sharp_maybe": 0.075, "blink": 0.70, "clip_reject": 0.50, "clip_maybe": 0.30},
    "strict":  {"sharp_reject": 0.065, "sharp_maybe": 0.095, "blink": 0.60, "clip_reject": 0.40, "clip_maybe": 0.20},
}


def _region(y, box):
    if box is None:
        return y
    h, w = y.shape
    x0, y0, x1, y1 = box
    return y[int(y0 * h):max(int(y1 * h), int(y0 * h) + 8), int(x0 * w):max(int(x1 * w), int(x0 * w) + 8)]


def sharpness_dirs(y, box=None) -> tuple[float, float]:
    """(crispness, smear) of a region.

    crispness: strength of the finest edges relative to the region's contrast,
    taken in the weakest of four directions, so a photo smeared one way by
    movement scores low even though edges along the smear stay sharp.
    Roughly 0.02 blurred .. 0.2 crisp.
    smear: strongest direction divided by weakest; near 1 for focus blur or a
    sharp photo, clearly above 1 for movement.
    """
    y = _region(y, box)
    if y.size < 64:
        return 0.0, 1.0
    g = cv2.GaussianBlur(y, (0, 0), 0.7)                       # tame pixel noise
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    lo, hi = np.percentile(g, [2, 98])
    scale = hi - lo + 0.08
    r = 0.70710678
    vals = [float(np.percentile(np.abs(d), 99.5)) / scale
            for d in (gx, gy, (gx + gy) * r, (gx - gy) * r)]
    return min(vals), max(vals) / max(min(vals), 1e-6)


def sharpness(y, box=None) -> float:
    return sharpness_dirs(y, box)[0]


def best_tile(y, n=6) -> tuple[float, float]:
    """Crispness and smear of the crispest part of the frame (the subject is rarely the whole frame)."""
    h, w = y.shape
    vals = [sharpness_dirs(y[iy * h // n:(iy + 1) * h // n, ix * w // n:(ix + 1) * w // n])
            for iy in range(n) for ix in range(n)]
    vals.sort(key=lambda v: v[0])
    return vals[-3]                                            # third best: one freak tile cannot decide


def dhash(img, size=16) -> np.ndarray:
    g = cv2.resize(_luma(img), (size + 1, size), interpolation=cv2.INTER_AREA)
    return (g[:, 1:] > g[:, :-1]).flatten()


def measure_photo(img, info: dict | None = None) -> dict:
    """Everything the checks need to know about one photo (float32 sRGB 0..1, preview size)."""
    y = _luma(img)
    faces = find_faces(img)
    judged = [f for f in faces if f["judged"]]
    main = [f for f in judged if f["size"] >= 0.35 * judged[0]["size"]] if judged else []
    for f in main:
        f["sharpness"], f["smear"] = sharpness_dirs(y, f["box"])
    small = cv2.resize(y, (256, max(1, round(256 * y.shape[0] / y.shape[1]))), interpolation=cv2.INTER_AREA)
    return {
        "faces": faces, "main_faces": main,
        "sharp_frame": best_tile(y)[0], "smear": best_tile(y)[1],
        "median": float(np.median(small)), "p99": float(np.percentile(small, 99)), "p1": float(np.percentile(small, 1)),
        "clipped": float((small > 0.985).mean()), "crushed": float((small < 0.012).mean()),
        "detail": float(small.std()),
        "hash": dhash(img), "taken": (info or {}).get("taken", ""), "iso": (info or {}).get("iso"),
    }


def quality_score(m: dict) -> float:
    """One number for ranking similar frames: sharper, eyes open and smiling is better."""
    if m["main_faces"]:
        sharp = max(f["sharpness"] for f in m["main_faces"])
        blink = max(min(f["blink_left"], f["blink_right"]) for f in m["main_faces"])
        smile = float(np.mean([f["smile"] for f in m["main_faces"]]))
        return sharp * 4 - blink * 0.6 + smile * 0.15
    return m["sharp_frame"] * 4


def judge(m: dict, checks=CHECKS, strictness: str = "normal") -> dict:
    """Result for one photo from its measurements. Similar-frame grouping is added by cull()."""
    T = LEVELS[strictness]
    checks = set(checks)
    reject, maybe = [], []
    main = m["main_faces"]

    if "accidental" in checks and not m["faces"]:
        if m["detail"] < 0.03 or m["p99"] < 0.08 or m["p1"] > 0.92:
            reject.append("accidental or blank frame")

    if "exposure" in checks:
        if m["clipped"] > T["clip_reject"]:
            reject.append("badly overexposed")
        elif m["clipped"] > T["clip_maybe"]:
            maybe.append("large burnt-out areas")
        if m["p99"] < 0.20 and m["median"] < 0.04:
            reject.append("badly underexposed")
        elif m["p99"] < 0.35 and m["median"] < 0.06:
            maybe.append("very dark")

    if "focus" in checks or "motion_blur" in checks:
        if main:
            bestf = max(main, key=lambda f: f["sharpness"])
            sharp, smear = bestf["sharpness"], bestf["smear"]
        else:
            sharp, smear = m["sharp_frame"], m["smear"]
        where = "faces" if main else "frame"
        moved = smear > 1.7
        if sharp < T["sharp_reject"]:
            if moved and "motion_blur" in checks:
                reject.append(f"motion blur ({where})")
            elif "focus" in checks:
                reject.append(f"out of focus ({where})")
        elif sharp < T["sharp_maybe"]:
            if moved and "motion_blur" in checks:
                maybe.append(f"slight motion blur ({where})")
            elif "focus" in checks:
                maybe.append(f"soft focus ({where})")

    closed = [f for f in main if min(f["blink_left"], f["blink_right"]) > T["blink"]]
    if closed:
        if len(main) >= 3 and "group_eyes" in checks:
            maybe.append(f"eyes closed: {len(closed)} of {len(main)} people")
        elif len(main) < 3 and "closed_eyes" in checks:
            down = all(f["look_down"] > 0.5 for f in closed)
            maybe.append("eyes closed or looking down" if down else "eyes closed")

    if "expression" in checks and main:
        awkward = [f for f in main if f["jaw_open"] > 0.45 and f["smile"] < 0.15]
        if awkward and len(awkward) >= max(1, len(main) // 2):
            maybe.append("mid-speech expression")

    if "framing" in checks and main:
        b = main[0]["box"]
        if b[0] < 0.004 or b[1] < 0.004 or b[2] > 0.996 or b[3] > 0.996:
            maybe.append("main face cut by the edge")

    result = "reject" if reject else "maybe" if maybe else "keep"
    return {"result": result, "stars": STARS[result], "reasons": reject + maybe, "score": round(quality_score(m), 4)}


def _seconds(taken: str):
    """Seconds since 1970 for a capture time, or None. Reads the camera's "2026:01:01 10:00:00" and the
    ISO form the browser and the plug-in send ("2026-01-01T10:00:00", with fractions of a second and a
    time zone such as "Z" or "+05:30" or none). Without a zone the clock time is taken as it is, so
    frames from the same camera compare correctly either way."""
    import datetime as _dt
    if not isinstance(taken, str) or not taken.strip():
        return None
    s = taken.strip()
    if len(s) >= 10 and s[4] == ":" and s[7] == ":":                 # camera (EXIF) date
        s = s[:4] + "-" + s[5:7] + "-" + s[8:]
    s = s.replace(" ", "T", 1)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.timestamp()


def group_similar(measures: list[dict], max_gap: float = 4.0, max_dist: int = 44) -> list[int]:
    """Group number per photo: frames taken moments apart that look alike share a group."""
    order = sorted(range(len(measures)), key=lambda i: (_seconds(measures[i]["taken"]) or 0, i))
    groups = [0] * len(measures)
    g, prev = -1, None
    for i in order:
        same = False
        if prev is not None:
            t0, t1 = _seconds(measures[prev]["taken"]), _seconds(measures[i]["taken"])
            near = t0 is not None and t1 is not None and abs(t1 - t0) <= max_gap
            dist = int((measures[prev]["hash"] != measures[i]["hash"]).sum())
            same = near and dist <= max_dist
        if not same:
            g += 1
        groups[i] = g
        prev = i
    return groups


def cull(items: list[tuple[str, dict]], checks=CHECKS, strictness: str = "normal",
         similar_keeps_best_only: bool = False) -> list[dict]:
    """Rate a folder. items = [(name, measurements from measure_photo), ...]."""
    out = []
    for name, m in items:
        r = judge(m, checks, strictness)
        r["name"] = name
        out.append(r)
    if "similar" in set(checks) and items:
        groups = group_similar([m for _, m in items])
        for r, g in zip(out, groups):
            r["group"] = g
        for g in set(groups):
            members = [r for r in out if r["group"] == g]
            if len(members) < 2:
                continue
            rank = {"keep": 2, "maybe": 1, "reject": 0}
            best = max(members, key=lambda r: (rank[r["result"]], r["score"]))
            for r in members:
                r["group_size"] = len(members)
                r["best_in_group"] = r is best
                # eyes closed is a clear failure when a twin frame has them open
                if r is not best and r["result"] == "maybe" and any("eyes closed" in x for x in r["reasons"]) \
                        and best["result"] == "keep":
                    r["result"], r["stars"] = "reject", STARS["reject"]
                    r["reasons"].append(f"better twin frame: {best['name']}")
                elif r is not best and similar_keeps_best_only and r["result"] == "keep":
                    r["result"], r["stars"] = "maybe", STARS["maybe"]
                    r["reasons"].append(f"similar to {best['name']}")
    return out
