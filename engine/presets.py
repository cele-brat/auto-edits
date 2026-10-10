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

# Attributes that are not develop settings.
_SKIP_KEYS = {"Name", "Group", "Copyright", "ContactInfo", "CompatibleVersion", "RequiresRGBTables",
              "ShowInPresets", "ShowInQuickActions", "CameraModelRestriction", "SupportsAmount2",
              "AllowFilters", "HDREditMode"}

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
    name: str            # name stored in the file, e.g. "Cele-Brat MG 01 Signature"
    display_name: str    # full name shown in the app, e.g. "Marco Grob 01 Signature"
    look: str            # "Marco Grob"
    variant: str         # "01 Signature"
    group: str
    pack: str
    description: str
    process_version: str
    camera_profile: str
    settings: dict = field(default_factory=dict)   # numeric / bool / string sliders
    curves: dict = field(default_factory=dict)     # name -> [[x, y], ...]
    has_masks: bool = False
    masks: list = field(default_factory=list)      # AI masks: name, adjustments, mask kinds
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


def split_name(name: str, group: str) -> tuple[str, str, str]:
    """Full look name and variant from the stored name and group.

    "Cele-Brat MG 01 Signature" in group "Cele-Brat - Marco Grob Look"
    -> ("Marco Grob", "01 Signature", "Marco Grob 01 Signature")
    """
    g = re.match(r"^Cele-Brat\s*-\s*(.+?)\s+Look$", group.strip())
    look = g.group(1) if g else ""             # only the "Cele-Brat - <Look> Look" packs carry a look name
    m = re.search(r"(?:^|\s)(\d{2}\s.+)$", name)
    variant = m.group(1).strip() if m else name
    if not look:                       # presets from elsewhere: keep their own name
        return "", variant, name
    return look, variant, f"{look} {variant}"


# Lightroom AI mask types, as stored in crs:CorrectionMasks.
# MaskSubType 1 is Select Subject; 3 is a part of a person, named by MaskSubCategoryID.
# IDs marked "seen" were read from a real Cele-Brat preset; the others follow Adobe's
# usual numbering and still need to be confirmed against a preset that uses them.
MASK_SUBTYPES = {0: "sky", 1: "subject", 2: "background", 3: "person"}
PERSON_PARTS = {
    1: "whole_person", 2: "face_skin",      # 2 seen
    3: "iris_pupil",                        # seen
    4: "body_skin", 5: "eyebrows", 6: "lips", 7: "hair",
    8: "eye_sclera",                        # seen
    9: "facial_hair",
    11: "clothes", 12: "teeth",             # seen
}
CONFIRMED_PERSON_PARTS = {2, 3, 8, 11, 12}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attrs(el, ns=CRS) -> dict:
    """Attributes of one element in the given namespace, without the namespace."""
    pre = "{%s}" % ns
    return {k[len(pre):]: v for k, v in el.attrib.items() if k.startswith(pre)}


def _li_text(desc, tag: str) -> str:
    el = desc.find("{%s}%s" % (CRS if tag != "description" else DC, tag))
    if el is None:
        return ""
    li = el.find(".//{%s}li" % RDF)
    return (li.text or "").strip() if li is not None else ""


def _points(desc, tag: str):
    el = desc.find("{%s}%s" % (CRS, tag))
    if el is None:
        return None
    pts = []
    for li in el.iter("{%s}li" % RDF):
        if li.text and "," in li.text:
            x, y = li.text.split(",")[:2]
            pts.append([float(x), float(y)])
    return pts or None


def _mask_kind(m: dict) -> str:
    """Our name for a Lightroom mask: subject, background, sky, face_skin, clothes ..."""
    what = m.get("What", "")
    if what != "Mask/Image":
        return {"Mask/Gradient": "linear_gradient", "Mask/CircularGradient": "radial_gradient",
                "Mask/Paint": "brush", "Mask/RangeMask": "range"}.get(what, what.replace("Mask/", "").lower())
    sub = int(float(m.get("MaskSubType", -1)))
    kind = MASK_SUBTYPES.get(sub, f"subtype_{sub}")
    if kind == "person":
        cat = int(float(m.get("MaskSubCategoryID", 1)))
        kind = PERSON_PARTS.get(cat, f"person_part_{cat}")
    if kind == "subject" and m.get("MaskInverted") == "true":
        kind = "background"
    return kind


def _parse_masks(desc) -> list[dict]:
    group = desc.find("{%s}MaskGroupBasedCorrections" % CRS)
    if group is None:
        return []
    out = []
    for corr in group.iter("{%s}Description" % RDF):
        a = _attrs(corr)
        if a.get("What") != "Correction":
            continue
        adjust = {k[len("Local"):]: _num(v) for k, v in a.items() if k.startswith("Local")}
        adjust = {k: v for k, v in adjust.items()
                  if isinstance(v, (int, float)) and v and k not in ("CurveRefineSaturation", "ToningHue")
                  or (k == "ToningHue" and a.get("LocalToningSaturation", "0") not in ("0", "0.0"))}
        masks = []
        cm = corr.find("{%s}CorrectionMasks" % CRS)
        for li in (cm.iter("{%s}li" % RDF) if cm is not None else []):
            m = _attrs(li)
            if not m.get("What", "").startswith("Mask/"):
                continue
            sub = m.get("MaskSubType")
            cat = m.get("MaskSubCategoryID")
            kind = _mask_kind(m)
            masks.append({
                "kind": kind, "name": m.get("MaskName", ""), "inverted": m.get("MaskInverted") == "true",
                "subtract": m.get("MaskBlendMode") == "1", "value": float(m.get("MaskValue", 1)),
                "lr_subtype": int(float(sub)) if sub is not None else None,
                "lr_category": int(float(cat)) if cat is not None else None,
                "confirmed": m.get("What") != "Mask/Image" or kind in ("subject", "background")
                             or (cat is not None and int(float(cat)) in CONFIRMED_PERSON_PARTS),
            })
        curve = _points(corr, "MainCurve")
        out.append({
            "name": a.get("CorrectionName", ""), "active": a.get("CorrectionActive", "true") == "true",
            "amount": float(a.get("CorrectionAmount", 1)), "adjust": adjust, "curve": curve, "masks": masks,
        })
    return out


def parse_xmp(text: str, source_file: str = "", pack: str = "") -> Preset:
    import xml.etree.ElementTree as ET
    m = re.search(r"<x:xmpmeta.*</x:xmpmeta>", text, re.S)
    if not m:
        raise ValueError(f"not a preset file: {source_file}")
    root = ET.fromstring(m.group(0))
    desc = root.find(".//{%s}Description" % RDF)          # the top-level description only
    attrs = _attrs(desc)
    settings = {k: _num(v) for k, v in attrs.items()
                if k not in _META_KEYS and k not in _SKIP_KEYS and not k.startswith("Table_")}

    curves = {}
    for key in CURVE_KEYS:
        pts = _points(desc, key)
        if pts:
            curves[key] = pts

    ident = desc.attrib.get("{%s}identifier" % DC)
    if not ident:
        el = desc.find("{%s}identifier" % DC)
        ident = (el.text or "").strip() if el is not None else ""
    uuid = attrs.get("UUID", "")
    # Lightroom shows the crs:Name attribute when present, otherwise the crs:Name element
    name = attrs.get("Name") or _li_text(desc, "Name")
    group = attrs.get("Group") or _li_text(desc, "Group")
    look, variant, display = split_name(name, group)
    masks = _parse_masks(desc)
    return Preset(
        id=ident or uuid,
        uuid=uuid,
        name=name,
        display_name=display,
        look=look,
        variant=variant,
        group=group,
        pack=pack,
        description=_li_text(desc, "description") or _li_text(desc, "Description"),
        process_version=attrs.get("ProcessVersion", ""),
        camera_profile=attrs.get("CameraProfile", ""),
        settings=settings,
        curves=curves,
        has_masks=bool(masks),
        masks=masks,
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
