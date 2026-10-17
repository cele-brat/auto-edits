"""Every adjustment the app offers, in the order of Lightroom Classic's Develop panels.

Each slider names the Lightroom setting it moves. Your change is stored as a
difference from whatever sets that value (the preset, or the automatic value
when Auto is ticked), so it carries over when the preset changes.

  field   where the difference is kept in a look: one of the look's own fields
          ("ev", "hl" ...) for the settings that have an Auto tick, otherwise
          look["adj"][key]
  toggle  the Auto tick that goes with the slider, if it has one
  shown   False when the app's preview cannot show the change; Lightroom still applies it
"""
from __future__ import annotations

COLOURS = ("Red", "Orange", "Yellow", "Green", "Aqua", "Blue", "Purple", "Magenta")


def _s(key, label, lo=-100, hi=100, step=1, shown=True, **more):
    return {"key": key, "label": label, "min": lo, "max": hi, "step": step, "shown": shown, **more}


PANELS = [
    {"name": "Basic", "open": True, "groups": [
        {"name": "White balance", "sliders": [
            _s("Temperature", "Temp", field="temp", toggle="white_balance", relative=True),
            _s("Tint", "Tint", field="tint", relative=True)]},
        {"name": "Tone", "sliders": [
            _s("Exposure2012", "Exposure", -5, 5, 0.05, field="ev", toggle="exposure"),
            _s("Contrast2012", "Contrast"),
            _s("Highlights2012", "Highlights", field="hl", toggle="highlights"),
            _s("Shadows2012", "Shadows", field="sh", toggle="shadows"),
            _s("Whites2012", "Whites", field="wh", toggle="whites"),
            _s("Blacks2012", "Blacks", field="bl", toggle="blacks")]},
        {"name": "Presence", "sliders": [
            _s("Texture", "Texture"), _s("Clarity2012", "Clarity"), _s("Dehaze", "Dehaze"),
            _s("Vibrance", "Vibrance"), _s("Saturation", "Saturation")]},
    ]},
    {"name": "Tone Curve", "groups": [
        {"name": "Region", "sliders": [
            _s("ParametricHighlights", "Highlights"), _s("ParametricLights", "Lights"),
            _s("ParametricDarks", "Darks"), _s("ParametricShadows", "Shadows")]},
    ]},
    {"name": "Color Mixer", "groups": [
        {"name": "Hue", "sliders": [_s("HueAdjustment" + c, c) for c in COLOURS]},
        {"name": "Saturation", "sliders": [_s("SaturationAdjustment" + c, c) for c in COLOURS]},
        {"name": "Luminance", "sliders": [_s("LuminanceAdjustment" + c, c) for c in COLOURS]},
    ]},
    {"name": "Color Grading", "groups": [
        {"name": "Midtones", "sliders": [
            _s("ColorGradeMidtoneHue", "Hue", 0, 359, hue=True), _s("ColorGradeMidtoneSat", "Saturation", 0, 100),
            _s("ColorGradeMidtoneLum", "Luminance")]},
        {"name": "Shadows", "sliders": [
            _s("SplitToningShadowHue", "Hue", 0, 359, hue=True), _s("SplitToningShadowSaturation", "Saturation", 0, 100),
            _s("ColorGradeShadowLum", "Luminance")]},
        {"name": "Highlights", "sliders": [
            _s("SplitToningHighlightHue", "Hue", 0, 359, hue=True), _s("SplitToningHighlightSaturation", "Saturation", 0, 100),
            _s("ColorGradeHighlightLum", "Luminance")]},
        {"name": "Global", "sliders": [
            _s("ColorGradeGlobalHue", "Hue", 0, 359, hue=True), _s("ColorGradeGlobalSat", "Saturation", 0, 100),
            _s("ColorGradeGlobalLum", "Luminance")]},
        {"name": "", "sliders": [_s("ColorGradeBlending", "Blending", 0, 100, neutral=50), _s("SplitToningBalance", "Balance")]},
    ]},
    {"name": "Detail", "groups": [
        {"name": "Sharpening", "sliders": [
            _s("Sharpness", "Amount", 0, 150, neutral=40), _s("SharpenRadius", "Radius", 0.5, 3, 0.1, neutral=1),
            _s("SharpenDetail", "Detail", 0, 100, neutral=25), _s("SharpenEdgeMasking", "Masking", 0, 100)]},
        {"name": "Noise reduction", "sliders": [
            _s("LuminanceSmoothing", "Luminance", 0, 100, shown=False, field="noise", toggle="noise"),
            _s("LuminanceNoiseReductionDetail", "Detail", 0, 100, shown=False, neutral=50),
            _s("LuminanceNoiseReductionContrast", "Contrast", 0, 100, shown=False),
            _s("ColorNoiseReduction", "Color", 0, 100, shown=False, neutral=25),
            _s("ColorNoiseReductionDetail", "Detail", 0, 100, shown=False, neutral=50),
            _s("ColorNoiseReductionSmoothness", "Smoothness", 0, 100, shown=False, neutral=50)]},
    ]},
    {"name": "Lens Corrections", "groups": [
        {"name": "Profile", "sliders": [_s("LensProfileEnable", "Remove chromatic aberration and enable profile corrections",
                                           shown=False, toggle="lens", switch=True)]},
        {"name": "Manual", "sliders": [
            _s("LensManualDistortionAmount", "Distortion", shown=False),
            _s("DefringePurpleAmount", "Defringe purple", 0, 20, shown=False), _s("DefringeGreenAmount", "Defringe green", 0, 20, shown=False),
            _s("VignetteAmount", "Vignetting", shown=False), _s("VignetteMidpoint", "Midpoint", 0, 100, shown=False, neutral=50)]},
    ]},
    {"name": "Transform", "groups": [
        {"name": "", "sliders": [
            _s("CropAngle", "Straighten", -45, 45, 0.1, field="angle", toggle="straighten"),
            _s("PerspectiveVertical", "Vertical", shown=False), _s("PerspectiveHorizontal", "Horizontal", shown=False),
            _s("PerspectiveRotate", "Rotate", -10, 10, 0.1, shown=False), _s("PerspectiveAspect", "Aspect", shown=False),
            _s("PerspectiveScale", "Scale", 50, 150, shown=False, neutral=100),
            _s("PerspectiveX", "X offset", shown=False), _s("PerspectiveY", "Y offset", shown=False)]},
    ]},
    {"name": "Effects", "groups": [
        {"name": "Post-crop vignetting", "sliders": [
            _s("PostCropVignetteAmount", "Amount"), _s("PostCropVignetteMidpoint", "Midpoint", 0, 100, neutral=50),
            _s("PostCropVignetteRoundness", "Roundness", shown=False), _s("PostCropVignetteFeather", "Feather", 0, 100, neutral=50),
            _s("PostCropVignetteHighlightContrast", "Highlights", 0, 100, shown=False)]},
        {"name": "Grain", "sliders": [
            _s("GrainAmount", "Amount", 0, 100), _s("GrainSize", "Size", 0, 100, neutral=25),
            _s("GrainFrequency", "Roughness", 0, 100, shown=False, neutral=50)]},
    ]},
    {"name": "Calibration", "groups": [
        {"name": "Shadows", "sliders": [_s("ShadowTint", "Tint")]},
        {"name": "Red primary", "sliders": [_s("RedHue", "Hue"), _s("RedSaturation", "Saturation")]},
        {"name": "Green primary", "sliders": [_s("GreenHue", "Hue"), _s("GreenSaturation", "Saturation")]},
        {"name": "Blue primary", "sliders": [_s("BlueHue", "Hue"), _s("BlueSaturation", "Saturation")]},
    ]},
]

SLIDERS = {s["key"]: s for p in PANELS for g in p["groups"] for s in g["sliders"]}
# sliders whose difference is kept in look["adj"]
FREE = {k: s for k, s in SLIDERS.items() if "field" not in s and not s.get("switch")}


def neutral(key: str) -> float:
    return float(SLIDERS[key].get("neutral", 0))


def clean_adj(raw) -> dict:
    """Differences for the free sliders, each no larger than the slider's own span."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for key, s in FREE.items():
        try:
            v = float(raw.get(key) or 0)
        except (TypeError, ValueError):
            continue
        span = s["max"] - s["min"]
        v = max(-span, min(span, v))
        if v == v and abs(v) > 1e-9:
            out[key] = round(v, 3)
    return out


def add(settings: dict, adj: dict) -> dict:
    """Lay the differences over the settings; each result stays inside Lightroom's own range."""
    for key, delta in adj.items():
        s = FREE[key]
        value = float(settings.get(key) if isinstance(settings.get(key), (int, float)) else neutral(key)) + delta
        if s.get("hue"):
            value %= 360
        settings[key] = round(max(s["min"], min(s["max"], value)), 3)
    return settings
