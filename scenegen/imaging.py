"""Small Pillow helpers shared by providers and the pipeline."""

from __future__ import annotations

import base64
import io

from PIL import Image, ImageDraw, ImageFilter, ImageOps

try:  # HEIC/HEIF photos from iPhones
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_SUPPORT = True
except ImportError:  # pragma: no cover
    HEIF_SUPPORT = False

RES_PX = {"1K": 1024, "2K": 2048, "4K": 4096}


def load(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def to_bytes(img: Image.Image, fmt: str = "PNG", quality: int = 95) -> bytes:
    buf = io.BytesIO()
    if fmt.upper() in ("JPG", "JPEG"):
        img.convert("RGB").save(buf, "JPEG", quality=quality)
    else:
        img.save(buf, fmt)
    return buf.getvalue()


def data_uri(img: Image.Image, max_side: int | None = None) -> str:
    """JPEG data URI, optionally downscaled, used to send images to APIs."""
    if max_side and max(img.size) > max_side:
        img = fit_long_side(img, max_side)
    return "data:image/jpeg;base64," + base64.b64encode(to_bytes(img, "JPEG")).decode()


def fit_long_side(img: Image.Image, side: int) -> Image.Image:
    w, h = img.size
    s = side / max(w, h)
    return img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)


def size_for(aspect_ratio: str, resolution: str) -> tuple[int, int]:
    """Pixel size for an aspect ratio like '3:2' at a resolution tier like '4K'."""
    a, b = (int(x) for x in aspect_ratio.split(":"))
    long_side = RES_PX.get(resolution, 2048)
    if a >= b:
        return long_side, round(long_side * b / a)
    return round(long_side * a / b), long_side


def feather_ellipse_mask(size: int, inner: float = 0.55, outer: float = 0.95) -> Image.Image:
    """Square L-mask: 255 inside an ellipse of `inner` * size, fading to 0 at `outer` * size."""
    big = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(big)
    r = size * (inner + outer) / 4  # radius halfway through the fade band
    c = size / 2
    d.ellipse((c - r, c - r, c + r, c + r), fill=255)
    blur = max(1, size * (outer - inner) / 4)
    return big.filter(ImageFilter.GaussianBlur(blur))


def edge_ramp_mask(w: int, h: int, left: int, top: int, right: int = 0, bottom: int = 0) -> Image.Image:
    """L-mask that is 255 in the middle and ramps from 0 on the given edges (in pixels)."""
    import numpy as np

    xs = np.ones(w, dtype=np.float32)
    ys = np.ones(h, dtype=np.float32)
    if left:
        xs[:left] = np.linspace(0, 1, left, endpoint=False)
    if right:
        xs[w - right:] = np.minimum(xs[w - right:], np.linspace(1, 0, right, endpoint=False))
    if top:
        ys[:top] = np.linspace(0, 1, top, endpoint=False)
    if bottom:
        ys[h - bottom:] = np.minimum(ys[h - bottom:], np.linspace(1, 0, bottom, endpoint=False))
    m = np.outer(ys, xs) * 255
    return Image.fromarray(m.astype("uint8"), "L")
