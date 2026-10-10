"""The accuracy and fitting tools, checked on a made-up 'Lightroom' whose constants we know."""
import numpy as np
import cv2
from engine.images import save_jpeg
from engine.presets import effective_settings
from engine.render import render, DEFAULT_PARAMS
from tools.accuracy import run, no_grain
from tools.fit_params import fit

TRUE = dict(DEFAULT_PARAMS, contrast=0.9, highlights=0.5, saturation=1.4, vibrance=1.1, grade_strength=0.35, hsl_sat=1.5)


def make_testset(tmp_path, photo, library, n=4):
    from skimage import data
    imgs = {"a": photo, "b": data.coffee().astype(np.float32) / 255, "c": data.chelsea().astype(np.float32) / 255}
    imgs = {k: cv2.resize(v, (384, 256), interpolation=cv2.INTER_AREA) for k, v in imgs.items()}
    picks = [library[i] for i in (0, 130, 260, 390)][:n]
    for k, im in imgs.items():
        save_jpeg(im, tmp_path / "base" / f"{k}.jpg", 97)
        for p in picks:
            s, c = effective_settings(p)
            save_jpeg(render(im, no_grain(s), c, params=TRUE), tmp_path / p.id / f"{k}.jpg", 97)
    return tmp_path


def test_accuracy_and_fit(tmp_path, photo, library):
    ts = make_testset(tmp_path / "set", photo, library)
    perfect, rows = run(ts, tmp_path / "out_true", params=TRUE, size=384)
    assert perfect["pairs"] == 12 and perfect["presets"] == 4
    assert perfect["dE_mean"] < 1.5                       # same constants: only JPEG noise remains
    assert (tmp_path / "out_true" / "accuracy.csv").exists()
    assert len(list((tmp_path / "out_true").glob("*.jpg"))) == 12
    before, _ = run(ts, tmp_path / "out_default", size=384, sheets=False)
    assert before["dE_mean"] > perfect["dE_mean"] + 0.5   # default constants are measurably off

    params, start, end = fit(ts, size=128, rounds=1, log=lambda *_: None)
    assert end < start * 0.6                              # fitting closes most of the gap
    after, _ = run(ts, tmp_path / "out_fit", params=params, size=384, sheets=False)
    assert after["dE_mean"] < before["dE_mean"]
    print(f"\ndE default {before['dE_mean']}, fitted {after['dE_mean']}, perfect {perfect['dE_mean']}")
