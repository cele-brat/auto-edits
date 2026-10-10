"""Reading and writing images for the engine."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

RAW_EXT = {".dng", ".nef", ".cr2", ".cr3", ".arw", ".raf", ".orf", ".rw2"}


def load_image(path: str | Path, max_side: int | None = None) -> np.ndarray:
    """Load a JPEG / PNG / TIFF or a RAW / smart-preview DNG as float32 sRGB 0..1."""
    path = Path(path)
    if path.suffix.lower() in RAW_EXT:
        import rawpy
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True, no_auto_bright=True, output_bps=16,
                                  output_color=rawpy.ColorSpace.sRGB)
        img = rgb.astype(np.float32) / 65535.0
    else:
        data = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if data is None:
            raise ValueError(f"cannot read image: {path}")
        if data.ndim == 2:
            data = cv2.cvtColor(data, cv2.COLOR_GRAY2BGR)
        data = data[..., :3][..., ::-1]
        scale = 65535.0 if data.dtype == np.uint16 else 255.0
        img = data.astype(np.float32) / scale
    if max_side:
        img = resize_max(img, max_side)
    return np.ascontiguousarray(img)


def resize_max(img: np.ndarray, max_side: int) -> np.ndarray:
    h, w = img.shape[:2]
    s = max_side / max(h, w)
    if s >= 1:
        return img
    return cv2.resize(img, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)


def save_jpeg(img: np.ndarray, path: str | Path, quality: int = 92) -> None:
    out = (np.clip(img, 0, 1) * 255 + 0.5).astype(np.uint8)[..., ::-1]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), out, [cv2.IMWRITE_JPEG_QUALITY, quality])


def load_embedded_preview(path: str | Path, max_side: int | None = None):
    """Camera-rendered JPEG stored inside a RAW file, plus basic shooting data.

    Most cameras store a full-size JPEG in every RAW file. It shows the photo as
    the camera rendered it, which is far closer to Lightroom's starting point
    than an unprocessed RAW decode, and it needs no RAW conversion at all.
    Returns (image or None, info dict).
    """
    import io
    import rawpy
    from PIL import Image, ImageOps
    info = {}
    with rawpy.imread(str(path)) as raw:
        try:
            thumb = raw.extract_thumb()
        except Exception:
            return None, info
    if thumb.format != rawpy.ThumbFormat.JPEG:
        return None, info
    im = Image.open(io.BytesIO(thumb.data))
    exif = im.getexif()
    sub = exif.get_ifd(0x8769)
    info = {"camera": str(exif.get(0x110, "")).strip(), "iso": sub.get(0x8827), "shutter": float(sub[0x829A]) if 0x829A in sub else None,
            "aperture": float(sub[0x829D]) if 0x829D in sub else None, "taken": str(sub.get(0x9003, "")),
            "width": im.size[0], "height": im.size[1]}
    im = ImageOps.exif_transpose(im).convert("RGB")
    if max_side:
        im.draft("RGB", (max_side, max_side))
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    img = np.asarray(im, np.float32) / 255.0
    return np.ascontiguousarray(img), info
