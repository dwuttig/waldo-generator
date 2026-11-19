"""Google Gemini API backend (Nano Banana models billed directly by Google)."""

from __future__ import annotations

import base64

import httpx
from PIL import Image

from ..imaging import fit_long_side, load, to_bytes
from .base import ImageProvider, ProviderError

API = "https://generativelanguage.googleapis.com/v1beta/models"


def _part(img: Image.Image, max_side: int) -> dict:
    if max(img.size) > max_side:
        img = fit_long_side(img, max_side)
    return {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(to_bytes(img, "JPEG")).decode()}}


class GoogleProvider(ImageProvider):
    name = "google"

    def __init__(self, api_key: str, client: httpx.Client | None = None, timeout: float = 600):
        if not api_key:
            raise ProviderError("No Google API key set. Add it in Settings or as GEMINI_API_KEY.")
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=timeout)

    def _call(self, model: str, parts: list[dict], aspect_ratio: str | None, resolution: str) -> Image.Image:
        image_config = {"imageSize": resolution}
        if aspect_ratio:
            image_config["aspectRatio"] = aspect_ratio
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": image_config},
        }
        r = self.client.post(f"{API}/{model}:generateContent", json=body,
                             headers={"x-goog-api-key": self.api_key})
        if r.status_code >= 400:
            raise ProviderError(f"Gemini {model} failed ({r.status_code}): {r.text[:500]}")
        for cand in r.json().get("candidates", []):
            for p in cand.get("content", {}).get("parts", []):
                inline = p.get("inline_data") or p.get("inlineData")
                if inline and inline.get("data"):
                    return load(base64.b64decode(inline["data"]))
        raise ProviderError(f"Gemini {model} returned no image: {r.text[:300]}")

    def generate(self, prompt, *, model, aspect_ratio, resolution, refs=None):
        parts = [{"text": prompt}] + [_part(r, 1536) for r in (refs or [])]
        return self._call(model, parts, aspect_ratio, resolution)

    def edit(self, prompt, *, model, image, resolution, refs=None, aspect_ratio=None):
        parts = [{"text": prompt}, _part(image, 4096)] + [_part(r, 1536) for r in (refs or [])]
        return self._call(model, parts, aspect_ratio, resolution)
