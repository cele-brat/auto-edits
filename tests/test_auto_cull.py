import cv2
import numpy as np
from engine.auto import auto_correct, auto_exposure, auto_tone, auto_white_balance, auto_noise, auto_straighten, combine, TOGGLES
from engine.presets import effective_settings
from engine.render import render, _luma
from engine.cull import measure_photo, judge, cull, sharpness_dirs, CHECKS


def big(photo):
    return photo          # 512 px test photo at its native size: enlarging it would make it soft


# ---- auto corrections -----------------------------------------------------
def test_auto_exposure_direction(photo):
    dark = render(photo, {"Exposure2012": -1.5}); bright = render(photo, {"Exposure2012": 1.0})
    assert auto_exposure(dark) > 0.3 and auto_exposure(bright) < 0
    assert abs(auto_exposure(photo)) < 0.6                     # a normal photo is left nearly alone
    fixed = render(dark, {"Exposure2012": auto_exposure(dark)})
    assert abs(_luma(fixed).mean() - _luma(photo).mean()) < abs(_luma(dark).mean() - _luma(photo).mean())


def test_auto_tone_ranges(photo):
    t = auto_tone(photo)
    assert set(t) == {"Highlights2012", "Shadows2012", "Whites2012", "Blacks2012"}
    assert all(isinstance(v, int) and -100 <= v <= 100 for v in t.values())
    assert t["Highlights2012"] <= 0 and t["Shadows2012"] >= 0
    dark = render(photo, {"Exposure2012": -2}); assert auto_tone(dark)["Shadows2012"] > t["Shadows2012"]


def test_auto_white_balance(photo):
    warm = render(photo, {}, manual={"temp": 60})
    cool = render(photo, {}, manual={"temp": -60})
    w, c = auto_white_balance(warm), auto_white_balance(cool)
    assert w["temp"] < c["temp"]                               # a warm cast is cooled, a cool cast is warmed
    assert c["temp"] - w["temp"] >= 60                         # and by most of the cast, not a token amount
    assert abs(w["temp"]) <= 100 and abs(w["tint"]) <= 60
    grey = np.full((200, 300, 3), 0.5, np.float32)
    assert auto_white_balance(grey)["temp"] == 0 and auto_white_balance(grey)["tint"] == 0
    # a face with a green cast and nothing neutral in the frame: skin alone asks for magenta
    scene = np.empty((200, 300, 3), np.float32); scene[:] = (0.20, 0.55, 0.20)
    scene[50:150, 100:200] = (0.74, 0.64, 0.50)                # skin gone greenish
    skin = np.zeros((200, 300), np.float32); skin[50:150, 100:200] = 1
    assert auto_white_balance(scene, skin=skin)["tint"] > 10
    scene[50:150, 100:200] = (0.80, 0.58, 0.48)                # healthy skin is left alone
    assert abs(auto_white_balance(scene, skin=skin)["tint"]) <= 8


def test_auto_noise(photo):
    clean = cv2.GaussianBlur(cv2.resize(photo, (1536, 1536), interpolation=cv2.INTER_CUBIC), (0, 0), 1.5)
    noisy = np.clip(clean + np.random.default_rng(1).normal(0, 0.05, clean.shape).astype(np.float32), 0, 1)
    a, b = auto_noise(clean), auto_noise(noisy)
    assert b["LuminanceSmoothing"] > a["LuminanceSmoothing"] and b["ai_denoise"] and not a["ai_denoise"]
    assert auto_noise(clean, iso=12800)["ai_denoise"]          # high ISO alone is enough


def test_auto_straighten():
    img = np.full((600, 900, 3), 0.8, np.float32)
    for y in (150, 300, 450):
        img[y:y + 4] = 0.1                                      # level lines
    for x in (200, 450, 700):
        img[:, x:x + 4] = 0.1
    assert auto_straighten(img)["CropAngle"] == 0.0
    M = cv2.getRotationMatrix2D((450, 300), 2.0, 1.0)           # tilt the picture by 2 degrees
    tilted = cv2.warpAffine(img, M, (900, 600), borderValue=(0.8, 0.8, 0.8))
    r = auto_straighten(tilted)
    assert r["confident"] and 1.4 < abs(r["CropAngle"]) < 2.6
    back = cv2.warpAffine(tilted, cv2.getRotationMatrix2D((450, 300), -r["CropAngle"], 1.0), (900, 600),
                          borderValue=(0.8, 0.8, 0.8))
    assert abs(auto_straighten(back).get("measured_tilt", 0)) < 0.5   # applying it levels the picture


def test_toggle_rule_end_to_end(photo, library):
    p = next(x for x in library if "Exposure2012" in x.settings)
    on = {"exposure", "shadows"}
    preset_part, curves = effective_settings(p, on)
    auto = auto_correct(photo, on)
    assert set(auto["settings"]) == {"Exposure2012", "Shadows2012"}       # only the switched-on toggles
    merged, manual = combine(preset_part, auto)
    assert merged["Highlights2012"] == p.settings["Highlights2012"]      # toggle off: preset value
    assert merged["Exposure2012"] == auto["settings"]["Exposure2012"]    # toggle on: automatic value
    assert merged["Contrast2012"] == p.settings["Contrast2012"]
    assert np.isfinite(render(photo, merged, curves, manual=manual)).all()
    everything = auto_correct(photo)                                     # Auto Correct: every toggle, no preset
    assert {"Exposure2012", "Highlights2012", "Shadows2012", "Whites2012", "Blacks2012", "LuminanceSmoothing",
            "CropAngle", "LensProfileEnable"} <= set(everything["settings"]) and "temp" in everything["manual"]
    assert len(TOGGLES) == 9


# ---- culling ---------------------------------------------------------------
def test_sharpness_measure(photo):
    y = _luma(big(photo))
    sharp, smear = sharpness_dirs(y)
    soft, _ = sharpness_dirs(cv2.GaussianBlur(y, (0, 0), 4))
    ker = np.zeros((21, 21), np.float32); ker[10, :] = 1 / 21
    moved, moved_smear = sharpness_dirs(cv2.filter2D(y, -1, ker))
    assert sharp > 2 * soft and sharp > 2 * moved
    assert moved_smear > 1.7 > smear                            # movement smears one way, focus blur does not


def test_judge_blur_and_blank(photo):
    img = big(photo)
    good = judge(measure_photo(img))
    assert good["result"] == "keep" and good["stars"] == 3
    blurred = judge(measure_photo(cv2.GaussianBlur(img, (0, 0), 7)))
    assert blurred["result"] == "reject" and blurred["stars"] == 1 and "focus" in blurred["reasons"][0]
    assert judge(measure_photo(cv2.GaussianBlur(img, (0, 0), 7)), checks=set(CHECKS) - {"focus", "motion_blur"})["result"] == "keep"
    black = judge(measure_photo(np.full((800, 1200, 3), 0.01, np.float32)))
    assert black["result"] == "reject" and any("blank" in r or "underexposed" in r for r in black["reasons"])
    white = judge(measure_photo(np.clip(img * 6, 0, 1)))
    assert white["result"] in ("maybe", "reject") and any("burnt" in r or "overexposed" in r for r in white["reasons"])


def test_similar_groups(photo):
    img = big(photo)
    a = measure_photo(img, {"taken": "2025:01:17 17:14:21"})
    b = measure_photo(cv2.GaussianBlur(img, (0, 0), 2.5), {"taken": "2025:01:17 17:14:22"})
    c = measure_photo(img[:, ::-1].copy(), {"taken": "2025:01:17 18:30:00"})
    res = cull([("a", a), ("b", b), ("c", c)], similar_keeps_best_only=True)
    ra, rb, rc = res
    assert ra["group"] == rb["group"] != rc["group"]
    assert ra["best_in_group"] and not rb["best_in_group"] and "best_in_group" not in rc
    assert rb["stars"] <= 2
    alone = cull([("a", a), ("b", b)], checks=set(CHECKS) - {"similar"})
    assert "group" not in alone[0]
