"""Read Lightroom / Camera Raw preset files (.xmp) into plain dictionaries.

A preset is identified by its Product ID (dc:identifier) when present, otherwise
by its crs:UUID. File names are NOT used as identifiers because they can repeat.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

CRS = "http://ns.adobe.com/camera-raw-settings/1.0/"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
DC = "http://purl.org/dc/elements/1.1/"

# Settings that describe the file rather than the edit.
_META_KEYS = {
    "Version", "ProcessVersion", "HasSettings", "PresetType", "Cluster", "UUID",
    "SupportsAmount", "SupportsColor", "SupportsMonochrome",
    "SupportsHighDynamicRange", "SupportsNormalDynamicRange",
    "SupportsSceneReferred", "SupportsOutputReferred", "CameraProfile",
    "LookTable", "ToneCurveName2012",
}

CURVE_KEYS = ("ToneCurvePV2012", "ToneCurvePV2012Red", "ToneCurvePV2012Green", "ToneCurvePV2012Blue")

# The tone settings that have an auto toggle in the app. When a toggle is on the
# preset's own value for that setting is skipped.
TOGGLE_KEYS = {
    "exposure": ("Exposure2012",),
    "highlights": ("Highlights2012",),
    "shadows": ("Shadows2012",),
    "whites": ("Whites2012",),
    "blacks": ("Blacks2012",),
    "white_balance": ("Temperature", "Tint", "WhiteBalance", "IncrementalTemperature", "IncrementalTint"),
    "noise": ("LuminanceSmoothing", "ColorNoiseReduction", "LuminanceNoiseReductionDetail",
              "LuminanceNoiseReductionContrast", "ColorNoiseReductionDetail", "ColorNoiseReductionSmoothness"),
    "straighten": ("CropAngle", "PerspectiveUpright", "UprightVersion"),
    "lens": ("LensProfileEnable", "AutoLateralCA", "LensProfileSetup"),
}


@dataclass
class Preset:
    id: str
    uuid: str
    name: str
    group: str
    pack: str
    description: str
    process_version: str
    camera_profile: str
    settings: dict = field(default_factory=dict)   # numeric / bool / string sliders
    curves: dict = field(default_factory=dict)     # name -> [[x, y], ...]
    has_masks: bool = False
    source_file: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _num(value: str):
    v = value.strip()
    if v in ("True", "False"):
        return v == "True"
    try:
        f = float(v)
        return int(f) if f.is_integer() and "." not in v else f
    except ValueError:
        return v


def _alt_text(xml: str, tag: str) -> str:
    """Text of the first rdf:li inside <prefix:tag>."""
    m = re.search(r"<%s>.*?<rdf:li[^>]*>(.*?)</rdf:li>" % re.escape(tag), xml, re.S)
    return _unescape(m.group(1).strip()) if m else ""


def _unescape(s: str) -> str:
    return (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&apos;", "'"))


def parse_xmp(text: str, source_file: str = "", pack: str = "") -> Preset:
    attrs = dict(re.findall(r'crs:(\w+)="([^"]*)"', text))
    settings = {k: _num(v) for k, v in attrs.items() if k not in _META_KEYS}

    curves = {}
    for key in CURVE_KEYS:
        m = re.search(r"<crs:%s>\s*<rdf:Seq>(.*?)</rdf:Seq>" % key, text, re.S)
        if m:
            pts = []
            for li in re.findall(r"<rdf:li>(.*?)</rdf:li>", m.group(1), re.S):
                x, y = li.split(",")
                pts.append([float(x), float(y)])
            curves[key] = pts

    ident = re.search(r'dc:identifier="([^"]+)"', text)
    uuid = attrs.get("UUID", "")
    return Preset(
        id=ident.group(1) if ident else uuid,
        uuid=uuid,
        name=_alt_text(text, "crs:Name"),
        group=_alt_text(text, "crs:Group"),
        pack=pack,
        description=_alt_text(text, "dc:description"),
        process_version=attrs.get("ProcessVersion", ""),
        camera_profile=attrs.get("CameraProfile", ""),
        settings=settings,
        curves=curves,
        has_masks="MaskGroupBasedCorrections" in text,
        source_file=source_file,
    )


def load_folder(root: str | Path) -> list[Preset]:
    """Load every .xmp under root. The top-level folder name is used as the pack."""
    root = Path(root)
    out = []
    for path in sorted(root.rglob("*.xmp")):
        rel = path.relative_to(root)
        pack = rel.parts[0].replace(" - Presets", "") if len(rel.parts) > 1 else ""
        out.append(parse_xmp(path.read_text(encoding="utf-8", errors="replace"), str(rel), pack))
    return out


def build_index(root: str | Path, out_file: str | Path) -> dict:
    presets = load_folder(root)
    ids = [p.id for p in presets]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate preset ids: {dupes[:5]}")
    index = {"count": len(presets), "presets": [p.to_dict() for p in presets]}
    Path(out_file).write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    return index


def effective_settings(preset: Preset | dict, toggles_on: set[str] | frozenset = frozenset()) -> tuple[dict, dict]:
    """Preset settings and curves with the switched-on toggles removed.

    Rule agreed for the app: a switched-on toggle is set automatically per
    photo and the preset's own value for it is skipped; everything else comes
    from the preset.
    """
    p = preset if isinstance(preset, dict) else preset.to_dict()
    skip = {k for t in toggles_on for k in TOGGLE_KEYS[t]}
    settings = {k: v for k, v in p["settings"].items() if k not in skip}
    return settings, dict(p["curves"])


if __name__ == "__main__":
    import sys
    idx = build_index(sys.argv[1], sys.argv[2])
    print(idx["count"], "presets indexed")
