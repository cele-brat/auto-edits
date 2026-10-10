import time
import numpy as np
from engine.render import render, _luma
from engine.presets import effective_settings

ID_CURVE = {"ToneCurvePV2012": [[0, 0], [255, 255]]}


def chroma(img):
    return float((img.max(-1) - img.min(-1)).mean())


def test_identity(photo):
    out = render(photo, {}, ID_CURVE)
    assert np.abs(out - photo).max() < 1e-5
    zero = {"Contrast2012": 0, "Vibrance": 0, "GrainAmount": 0, "GrainSize": 25, "ColorGradeBlending": 50,
            "PostCropVignetteAmount": 0, "SplitToningShadowHue": 240, "SplitToningShadowSaturation": 0}
    assert np.abs(render(photo, zero) - photo).max() < 1e-5


def test_exposure_direction(photo):
    up = render(photo, {"Exposure2012": 0.5}); down = render(photo, {"Exposure2012": -0.5})
    assert up.mean() > photo.mean() > down.mean()
    # +1 EV about doubles linear light in the midtones (the exact curve is Lightroom's measured one)
    grey = np.full((8, 8, 3), 0.3, np.float32)
    from engine.render import srgb_to_linear
    r = srgb_to_linear(render(grey, {"Exposure2012": 1.0}))[0, 0, 0] / srgb_to_linear(grey)[0, 0, 0]
    assert 1.6 < r < 3.0


def test_tone_directions(photo, chart):
    y = _luma(photo)
    assert _luma(render(photo, {"Contrast2012": 30})).std() > y.std() > _luma(render(photo, {"Contrast2012": -30})).std()
    bright, dark = y > 0.75, y < 0.2
    assert _luma(render(photo, {"Highlights2012": -50}))[bright].mean() < y[bright].mean()
    assert _luma(render(photo, {"Shadows2012": 40}))[dark].mean() > y[dark].mean()
    assert _luma(render(photo, {"Blacks2012": -20}))[dark].mean() < y[dark].mean()
    assert _luma(render(photo, {"Whites2012": 12}))[bright].mean() > y[bright].mean()
    # highlights slider leaves deep shadows alone, shadows slider leaves bright areas alone
    assert abs(_luma(render(photo, {"Highlights2012": -50}))[y < 0.05].mean() - y[y < 0.05].mean()) < 0.01
    # tone curve stays monotonic on a grey ramp
    ramp = _luma(render(chart, {"Contrast2012": 28, "Highlights2012": -50, "Shadows2012": 28,
                                "Whites2012": -30, "Blacks2012": -20}))[10]
    assert np.all(np.diff(ramp) > -1e-4)


def test_neutral_stays_neutral(chart):
    s = {"SaturationAdjustmentRed": 50, "HueAdjustmentBlue": 40, "LuminanceAdjustmentOrange": 30, "Vibrance": 30,
         "Saturation": 12, "RedHue": 4, "RedSaturation": 10, "GreenHue": -4, "BlueSaturation": 16,
         "Contrast2012": 20, "Clarity2012": 14}
    ramp = render(chart, s)[:100]
    assert (ramp.max(-1) - ramp.min(-1)).max() < 0.004


def test_colour_directions(photo, chart):
    assert chroma(render(photo, {"Saturation": 12})) > chroma(photo) > chroma(render(photo, {"Saturation": -16}))
    assert chroma(render(photo, {"Vibrance": 30})) > chroma(photo)
    red, blue = chart[180, 30], chart[180, 330]
    out = render(chart, {"SaturationAdjustmentRed": -80})
    r2, b2 = out[180, 30], out[180, 330]
    assert (r2.max() - r2.min()) < 0.5 * (red.max() - red.min())      # red patch desaturated
    assert np.abs(b2 - blue).max() < 1e-4                              # blue patch untouched
    darker = render(chart, {"LuminanceAdjustmentBlue": -60})[180, 330]
    assert darker @ np.array([.2126, .7152, .0722]) < blue @ np.array([.2126, .7152, .0722])


def test_grading_tints(chart):
    out = render(chart, {"SplitToningShadowHue": 240, "SplitToningShadowSaturation": 22, "ColorGradeBlending": 50})
    dark, light = out[10, 60], out[10, 440]
    assert dark[2] > dark[0]                       # blue shadows
    assert abs(light[2] - light[0]) < 0.02         # highlights nearly untouched
    out = render(chart, {"SplitToningHighlightHue": 50, "SplitToningHighlightSaturation": 20})
    assert out[10, 440][0] > out[10, 440][2]       # warm highlights


def test_curve(chart):
    crv = {"ToneCurvePV2012": [[0, 0], [32, 22], [128, 122], [205, 208], [255, 246]]}
    ramp = render(chart, {}, crv)[10, :, 0]
    assert abs(ramp[-1] - 246 / 255) < 0.005 and ramp[0] < 0.002
    i = int(round(128 / 255 * 479))
    assert abs(ramp[i] - 122 / 255) < 0.01
    assert np.all(np.diff(ramp) >= -1e-5)
    red_only = render(chart, {}, {"ToneCurvePV2012": [[0, 0], [255, 255]],
                                  "ToneCurvePV2012Red": [[0, 0], [128, 150], [255, 255]]})[10, 240]
    assert red_only[0] > red_only[1] and abs(red_only[1] - red_only[2]) < 1e-5


def test_vignette_and_grain(photo):
    v = render(photo, {"PostCropVignetteAmount": -14, "PostCropVignetteMidpoint": 50, "PostCropVignetteFeather": 60})
    h, w = photo.shape[:2]
    assert np.abs(v[h // 2 - 20:h // 2 + 20, w // 2 - 20:w // 2 + 20] - photo[h // 2 - 20:h // 2 + 20, w // 2 - 20:w // 2 + 20]).max() < 1e-4
    assert v[:20, :20].mean() < photo[:20, :20].mean()
    g1 = render(photo, {"GrainAmount": 14, "GrainSize": 25}, seed=7)
    g2 = render(photo, {"GrainAmount": 14, "GrainSize": 25}, seed=7)
    assert np.array_equal(g1, g2) and not np.allclose(g1, photo)
    assert abs(g1.mean() - photo.mean()) < 0.002


def test_manual_and_amount(photo, library):
    warm = render(photo, {}, manual={"temp": 40})
    assert warm[..., 0].mean() > photo[..., 0].mean() and warm[..., 2].mean() < photo[..., 2].mean()
    assert abs(_luma(warm).mean() - _luma(photo).mean()) < 0.02
    p = library[0]
    s, c = effective_settings(p)
    full, half, none = render(photo, s, c), render(photo, s, c, amount=0.5), render(photo, s, c, amount=0.0)
    assert np.abs(half - photo).mean() < np.abs(full - photo).mean()
    assert np.abs(none - render(photo, {"Exposure2012": s.get("Exposure2012", 0)})).max() < 1e-4


def test_all_presets_render(photo, library):
    small = photo[::2, ::2]
    worst = 0.0
    t = time.time()
    for p in library:
        s, c = effective_settings(p)
        out = render(small, s, c)
        assert out.shape == small.shape and np.isfinite(out).all()
        assert 0.0 <= out.min() and out.max() <= 1.0
        d = float(np.abs(out - small).mean())
        assert 0.002 < d < 0.25, (p.id, d)           # every preset changes the photo, none wrecks it
        worst = max(worst, d)
    print(f"\n500 presets in {time.time() - t:.1f}s, largest mean change {worst:.3f}")
