"""Offline provider that draws placeholder images. Free; for trying the app and for tests."""

from __future__ import annotations

import hashlib
import random

from PIL import Image, ImageDraw

from ..imaging import RES_PX, size_for
from .base import ImageProvider


def _rng(*parts) -> random.Random:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def _figure(d: ImageDraw.ImageDraw, x: float, y: float, h: float, color, outline=(20, 20, 20)):
    head = h * 0.18
    d.ellipse((x - head / 2, y, x + head / 2, y + head), fill=(250, 220, 190), outline=outline)
    d.rectangle((x - h * 0.13, y + head, x + h * 0.13, y + h * 0.65), fill=color, outline=outline)
    d.rectangle((x - h * 0.1, y + h * 0.65, x + h * 0.1, y + h), fill=(60, 60, 110), outline=outline)


class MockProvider(ImageProvider):
    name = "mock"
    max_side = 1024  # keep tests fast regardless of requested resolution

    def _canvas(self, size, rng, crowd=True):
        w, h = size
        img = Image.new("RGB", size, (200 + rng.randint(0, 40), 225, 200))
        d = ImageDraw.Draw(img)
        if crowd:
            n = int(w * h / 900)
            for _ in range(n):
                fh = rng.uniform(0.03, 0.06) * min(w, h)
                col = tuple(rng.randint(60, 240) for _ in range(3))
                _figure(d, rng.uniform(0, w), rng.uniform(-fh / 2, h - fh / 2), fh, col)
        return img

    def _cap(self, w, h):
        s = min(1.0, self.max_side / max(w, h))
        return max(1, round(w * s)), max(1, round(h * s))

    def generate(self, prompt, *, model, aspect_ratio, resolution, refs=None):
        w, h = self._cap(*size_for(aspect_ratio, resolution))
        return self._canvas((w, h), _rng(prompt, aspect_ratio))

    def edit(self, prompt, *, model, image, resolution, refs=None, aspect_ratio=None):
        long_side = min(RES_PX.get(resolution, 2048), self.max_side)
        s = long_side / max(image.size)
        out = image.resize((max(1, round(image.width * s)), max(1, round(image.height * s))))
        d = ImageDraw.Draw(out)
        w, h = out.size
        rng = _rng(prompt, image.size)
        if prompt.startswith("Create a cartoon character"):
            out = Image.new("RGB", (w, h), (255, 255, 255))
            d = ImageDraw.Draw(out)
            src = image.resize((1, 1)).getpixel((0, 0))
            _figure(d, w / 2, h * 0.1, h * 0.8, src)
            d.text((10, 10), f"variation {rng.randint(1, 999)}", fill=(0, 0, 0))
        elif refs:
            # "Insert" the referenced character: a small red-outlined figure in the middle.
            col = refs[0].resize((1, 1)).getpixel((0, 0))
            _figure(d, w / 2, h / 2 - h * 0.07, h * 0.14, col, outline=(200, 0, 0))
        return out

    def upscale(self, image, *, model, factor):
        return image.resize((image.width * factor, image.height * factor), Image.LANCZOS)
