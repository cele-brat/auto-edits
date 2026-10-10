from engine.presets import effective_settings


def test_library_complete(library):
    assert len(library) == 500
    assert len({p.id for p in library}) == 500          # Product IDs are unique
    assert len({p.source_file.split("/")[-1] for p in library}) < 500   # file names are not
    assert all(p.id.startswith("CBV-") for p in library)
    assert all(len(p.curves) == 4 for p in library)
    assert not any(p.has_masks for p in library)


def test_values_parsed(library):
    p = next(x for x in library if x.id == "CBV-PT-MG-01-P-F467EB66")
    assert p.name == "Cele-Brat MG 01 Signature"
    assert p.group == "Cele-Brat - Marco Grob Look"
    assert p.pack == "Portrait Looks"
    s = p.settings
    assert s["Contrast2012"] == 22 and s["Highlights2012"] == -26 and s["Blacks2012"] == -10
    assert s["SplitToningShadowHue"] == 240 and s["PostCropVignetteAmount"] == -14
    assert s["ConvertToGrayscale"] is False
    assert p.curves["ToneCurvePV2012"] == [[0, 0], [32, 22], [128, 122], [205, 208], [255, 246]]


def test_toggles_skip_preset_values(library):
    p = next(x for x in library if "Exposure2012" in x.settings)
    full, _ = effective_settings(p)
    cut, curves = effective_settings(p, {"exposure", "highlights", "blacks"})
    assert "Exposure2012" in full and "Exposure2012" not in cut
    assert "Highlights2012" not in cut and "Blacks2012" not in cut
    assert cut["Shadows2012"] == full["Shadows2012"] and cut["Whites2012"] == full["Whites2012"]
    assert cut["Contrast2012"] == full["Contrast2012"]
    assert curves == p.curves
