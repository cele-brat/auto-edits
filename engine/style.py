"""Learn a photographer's style from their past edits.

Every edited photo gives one vector of Lightroom sliders (normalised to -1..1 by each slider's
own span). The vectors are grouped with k-means (numpy only), the number of groups (1 to 5)
chosen by the silhouette score, and each group becomes one preset: the average of its members.
A second, optional grouping goes by scene (bright outdoor, indoor tungsten, dusk) using what
the camera recorded, so the photographer gets one preset per kind of light.
"""
from __future__ import annotations

import numpy as np

# The sliders that make a style, with the span each is scaled by. Exposure is kept out: it is set per
# photo (and the app's Auto handles it), so it would only pull unrelated edits apart.
FEATURES = {
    "Contrast2012": 100, "Highlights2012": 100, "Shadows2012": 100, "Whites2012": 100, "Blacks2012": 100,
    "Texture": 100, "Clarity2012": 100, "Dehaze": 100, "Vibrance": 100, "Saturation": 100,
    "ParametricHighlights": 100, "ParametricLights": 100, "ParametricDarks": 100, "ParametricShadows": 100,
    "ColorGradeMidtoneSat": 100, "ColorGradeShadowLum": 100, "ColorGradeHighlightLum": 100,
    "SplitToningShadowSaturation": 100, "SplitToningHighlightSaturation": 100, "ColorGradeGlobalSat": 100,
    "GrainAmount": 100, "PostCropVignetteAmount": 100, "Sharpness": 150,
    **{f"{kind}Adjustment{c}": 100 for kind in ("Hue", "Saturation", "Luminance")
       for c in ("Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta")},
}
# hues go in as a point on the colour circle, so 350 and 10 are near each other
HUES = {"ColorGradeMidtoneHue": "ColorGradeMidtoneSat", "SplitToningShadowHue": "SplitToningShadowSaturation",
        "SplitToningHighlightHue": "SplitToningHighlightSaturation", "ColorGradeGlobalHue": "ColorGradeGlobalSat"}
# kept in the preset as the average of the members (rounded), but not clustered on
CARRIED = ("Exposure2012", "ColorGradeBlending", "SplitToningBalance", "SharpenRadius", "SharpenDetail",
           "SharpenEdgeMasking", "LuminanceSmoothing", "ColorNoiseReduction", "ShadowTint",
           "RedHue", "RedSaturation", "GreenHue", "GreenSaturation", "BlueHue", "BlueSaturation",
           "PostCropVignetteMidpoint", "PostCropVignetteFeather", "GrainSize", "GrainFrequency")
MAX_GROUPS = 5
MIN_PER_GROUP = 3                # a group needs this many edits, or it is noise
MIN_SILHOUETTE = 0.22            # below this the edits do not really fall into groups: one style (noise scores about 0.1)


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def vector(settings: dict, curves: dict | None = None) -> np.ndarray:
    """One edit as numbers in about -1..1: the sliders, each hue as a point on the circle (scaled by
    its saturation, so an unused hue counts for nothing), the lift of the tone curve, and black and white."""
    out = []
    for key, span in FEATURES.items():
        v = _num(settings.get(key))
        if v is None:
            v = 40.0 if key == "Sharpness" else 0.0
        if key == "Sharpness":
            v -= 40.0
        out.append(v / span)
    for hue, sat in HUES.items():
        h, s = _num(settings.get(hue)), _num(settings.get(sat))
        strength = (s or 0.0) / 100.0
        a = np.radians(h or 0.0)
        out += [np.cos(a) * strength, np.sin(a) * strength]
    pts = (curves or {}).get("ToneCurvePV2012") or []
    lift = float(pts[0][1]) / 255.0 if pts and len(pts[0]) == 2 else 0.0        # a matte, lifted black point
    out.append(lift * 2)
    out.append(1.0 if settings.get("ConvertToGrayscale") is True else 0.0)
    return np.array(out, np.float32)


def kmeans(x: np.ndarray, k: int, seed: int = 0, rounds: int = 60) -> tuple[np.ndarray, np.ndarray]:
    """Plain k-means with k-means++ starts. Returns (labels, centres)."""
    rng = np.random.default_rng(seed)
    n = len(x)
    centres = x[[rng.integers(n)]]
    while len(centres) < k:                                        # k-means++: far points are likelier starts
        d = ((x[:, None, :] - centres[None]) ** 2).sum(-1).min(1)
        p = d / d.sum() if d.sum() > 0 else np.full(n, 1.0 / n)
        centres = np.vstack([centres, x[[rng.choice(n, p=p)]]])
    labels = np.zeros(n, int)
    for _ in range(rounds):
        new = ((x[:, None, :] - centres[None]) ** 2).sum(-1).argmin(1)
        if np.array_equal(new, labels) and _:
            break
        labels = new
        for j in range(k):
            members = x[labels == j]
            if len(members):
                centres[j] = members.mean(0)
    return labels, centres


def silhouette(x: np.ndarray, labels: np.ndarray) -> float:
    """How well separated the groups are: 1 is perfect, 0 is no structure, below 0 is wrong."""
    n = len(x)
    groups = np.unique(labels)
    if len(groups) < 2 or n < 3:
        return 0.0
    d = np.sqrt(((x[:, None, :] - x[None]) ** 2).sum(-1))
    s = np.zeros(n)
    for i in range(n):
        own = labels[i]
        mine = labels == own
        if mine.sum() <= 1:
            continue
        a = d[i, mine].sum() / (mine.sum() - 1)
        b = min(d[i, labels == g].mean() for g in groups if g != own)
        s[i] = (b - a) / max(a, b, 1e-9)
    return float(s.mean())


def best_grouping(x: np.ndarray, max_groups: int = MAX_GROUPS) -> np.ndarray:
    """Labels for the best number of groups from 1 to max_groups, by silhouette. Everything is one
    group when there are too few edits or the groups are not clearly apart."""
    n = len(x)
    if n < 2 * MIN_PER_GROUP:
        return np.zeros(n, int)
    if float(x.std(0).max()) < 1e-6:                               # every edit the same
        return np.zeros(n, int)
    best, best_score = np.zeros(n, int), MIN_SILHOUETTE
    for k in range(2, min(max_groups, n // MIN_PER_GROUP) + 1):
        candidates = [kmeans(x, k, seed)[0] for seed in range(3)]
        for labels in candidates:
            if min(np.bincount(labels, minlength=k)) < MIN_PER_GROUP:
                continue
            score = silhouette(x, labels)
            if score > best_score:
                best, best_score = labels, score
    return best


def _round(key: str, v: float):
    if key in ("Exposure2012",):
        return round(v, 2)
    if key in ("SharpenRadius",):
        return round(v, 1)
    return int(round(v))


def average_edit(edits: list[dict]) -> tuple[dict, dict]:
    """The settings and curves of one preset from a group of edits: each slider's mean (hues as the
    mean direction on the circle), the tone curves averaged point by point when every edit has one."""
    settings: dict = {}
    keys = list(FEATURES) + list(CARRIED)
    for key in keys:
        vals = [_num(e["settings"].get(key)) for e in edits]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        mean = float(np.mean(vals + [40.0 if key == "Sharpness" else 0.0] * (len(edits) - len(vals))))
        if abs(mean) < 0.5 and key != "Sharpness":
            continue
        settings[key] = _round(key, mean)
    for hue, sat in HUES.items():
        pairs = [(_num(e["settings"].get(hue)), _num(e["settings"].get(sat)) or 0.0) for e in edits]
        weights = np.array([s for _, s in pairs])
        if weights.sum() <= 0 or sat not in settings:
            continue
        a = np.radians([h or 0.0 for h, _ in pairs])
        settings[hue] = int(round(np.degrees(np.arctan2((np.sin(a) * weights).sum(), (np.cos(a) * weights).sum()))) % 360)
    grey = sum(1 for e in edits if e["settings"].get("ConvertToGrayscale") is True)
    if grey > len(edits) / 2:
        settings["ConvertToGrayscale"] = True
    curves: dict = {}
    for key in ("ToneCurvePV2012", "ToneCurvePV2012Red", "ToneCurvePV2012Green", "ToneCurvePV2012Blue"):
        pts = [e.get("curves", {}).get(key) for e in edits]
        pts = [p for p in pts if p]
        if len(pts) < len(edits) / 2:
            continue
        xs = np.linspace(0, 255, 9)
        ys = np.mean([np.interp(xs, [p[0] for p in c], [p[1] for p in c]) for c in pts], 0)
        if key == "ToneCurvePV2012" or np.abs(ys - xs).max() >= 1.5:
            curves[key] = [[int(round(x)), int(round(y))] for x, y in zip(xs, ys)]
    if "ToneCurvePV2012" in curves and all(abs(y - x) < 1.5 for x, y in curves["ToneCurvePV2012"]):
        del curves["ToneCurvePV2012"]
    return settings, curves


def learn(edits: list[dict], max_groups: int = MAX_GROUPS) -> list[dict]:
    """edits: [{"settings": {...}, "curves": {...}, "name": ...}, ...]. Returns one group per style:
    {"settings", "curves", "members": [indices], "share": fraction}, largest first."""
    if not edits:
        return []
    x = np.stack([vector(e["settings"], e.get("curves")) for e in edits])
    labels = best_grouping(x, max_groups)
    out = []
    for g in np.unique(labels):
        members = [int(i) for i in np.where(labels == g)[0]]
        settings, curves = average_edit([edits[i] for i in members])
        out.append({"settings": settings, "curves": curves, "members": members, "share": len(members) / len(edits)})
    out.sort(key=lambda c: -len(c["members"]))
    return out


# ---- scenes: what light the photo was taken in ------------------------------------------------
SCENES = ("bright outdoor", "indoor tungsten", "dusk")


def scene_of(iso: float | None, median: float | None, warmth: float | None, kelvin: float | None = None) -> str:
    """A rough kind of light from the camera's facts: ISO, the preview's median brightness (0..1), how warm
    the preview is (red minus blue, -1..1) and the as-shot white balance in kelvin when known."""
    iso = iso or 0
    median = 0.4 if median is None else median
    warmth = 0.0 if warmth is None else warmth
    if kelvin and kelvin <= 3800:
        return "indoor tungsten"
    if iso >= 1600 and (warmth > 0.04 or (kelvin and kelvin <= 4500)):
        return "indoor tungsten"
    if iso <= 800 and median >= 0.3:
        return "bright outdoor"
    if iso >= 1000 and warmth > 0.06:
        return "indoor tungsten"
    return "dusk"


def preview_facts(img8) -> tuple[float, float]:
    """(median brightness 0..1, warmth -1..1) of a small picture (H, W, 3 uint8)."""
    small = img8[::4, ::4].astype(np.float32) / 255.0
    luma = 0.2126 * small[..., 0] + 0.7152 * small[..., 1] + 0.0722 * small[..., 2]
    return float(np.median(luma)), float((small[..., 0] - small[..., 2]).mean())
