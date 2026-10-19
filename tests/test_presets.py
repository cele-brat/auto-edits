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


def test_full_names(library):
    names = [p.display_name for p in library]
    assert len(set(names)) == 500                       # full names never repeat
    p = next(x for x in library if x.id == "CBV-PT-MG-01-P-F467EB66")
    assert (p.look, p.variant, p.display_name) == ("Marco Grob", "01 Signature", "Marco Grob 01 Signature")
    from engine.presets import split_name
    assert split_name("Cele-Brat Bottega53 01 Signature", "Cele-Brat - Bottega53 Look")[2] == "Bottega53 01 Signature"
    assert split_name("Cele-Brat Marco Grob 01 Signature", "Cele-Brat - Marco Grob Look")[2] == "Marco Grob 01 Signature"


def test_ai_mask_preset():
    from pathlib import Path
    from engine.presets import parse_xmp, effective_settings
    p = parse_xmp((Path(__file__).parent / "fixtures" / "polished_bright_2.xmp").read_text(), "polished_bright_2.xmp")
    assert p.id == "112502V1" and p.uuid == "8EF01EEA28314F66B346C52D6E20C54A"      # not a mask's UUID
    assert p.name == p.display_name == "Polished Bright 2"
    s = p.settings
    assert s["Exposure2012"] == 0.15 and s["Highlights2012"] == -64 and s["Blacks2012"] == 24
    assert s["LuminanceSmoothing"] == 4 and s["ColorNoiseReduction"] == 7
    assert not any(k.startswith(("Local", "Table_", "Mask", "Correction")) for k in s)   # mask values stay out
    assert p.curves["ToneCurvePV2012"][0] == [0, 3] and p.curves["ToneCurvePV2012Green"][1] == [109, 108]
    assert p.has_masks and len(p.masks) == 10
    kinds = [m["masks"][0]["kind"] for m in p.masks]
    assert kinds == ["subject", "background", "clothes", "face_skin", "face_skin", "face_skin", "face_skin",
                     "eye_sclera", "iris_pupil", "teeth"]
    bg = p.masks[1]
    assert bg["name"] == "Scene Background - Filmy Fade" and bg["masks"][0]["inverted"]
    assert bg["curve"] == [[0, 26], [68, 79], [161, 181], [255, 238]]
    assert abs(bg["adjust"]["Highlights2012"] + 0.435612) < 1e-9
    assert p.masks[2]["adjust"] == {"Saturation": 0.496289}
    # the noise toggle skips the preset's own noise values, nothing else
    cut, _ = effective_settings(p, {"noise"})
    assert "LuminanceSmoothing" not in cut and "ColorNoiseReduction" not in cut and cut["Contrast2012"] == 7


def test_xmp_with_entities_is_refused():
    """defusedxml: an uploaded preset cannot use XML entity tricks."""
    import pytest
    bomb = ('<x:xmpmeta xmlns:x="adobe:ns:meta/"><!DOCTYPE a [<!ENTITY e "xxxxxxxxxx">'
            '<!ENTITY f "&e;&e;&e;&e;&e;">]><r>&f;</r></x:xmpmeta>')
    with pytest.raises(Exception):
        parse_xmp(bomb, "bomb.xmp")


def test_built_in_presets_have_plain_words_and_a_plain_line():
    import json
    from server.looks import PRESETS_FILE, plain_about, preset_tags
    presets = json.loads(PRESETS_FILE.read_text())["presets"]
    tags = [set(preset_tags(p)) for p in presets]
    for word in ("warm", "cool", "moody", "bright", "dark", "matte", "film", "vibrant", "soft", "haldi", "sangeet"):
        assert any(word in t for t in tags), word
    for p in presets:
        line = plain_about(p)
        assert line and "median" not in line and "p1 ~" not in line and len(line) < 260
    assert all(not ({"bright", "dark"} <= t) for t in tags)          # never both
