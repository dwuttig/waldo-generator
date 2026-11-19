"""fal.ai backend (Nano Banana Pro / 2, FLUX.2, upscalers) via the synchronous REST endpoint."""

from __future__ import annotations

import httpx
from PIL import Image

from ..imaging import data_uri, load
from .base import ImageProvider, ProviderError

FAL_RUN = "https://fal.run"


class FalProvider(ImageProvider):
    name = "fal"

    def __init__(self, api_key: str, client: httpx.Client | None = None, timeout: float = 600):
        if not api_key:
            raise ProviderError("No fal.ai API key set. Add it in Settings or as FAL_KEY.")
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=timeout)

    def _run(self, model: str, payload: dict) -> Image.Image:
        if not model:
            raise ProviderError("No fal model configured for this step.")
        r = self.client.post(
            f"{FAL_RUN}/{model.strip('/')}",
            json=payload,
            headers={"Authorization": f"Key {self.api_key}"},
        )
        if r.status_code >= 400:
            raise ProviderError(f"fal {model} failed ({r.status_code}): {r.text[:500]}")
        body = r.json()
        url = None
        if body.get("images"):
            url = body["images"][0].get("url")
        elif isinstance(body.get("image"), dict):
            url = body["image"].get("url")
        if not url:
            raise ProviderError(f"fal {model} returned no image: {str(body)[:300]}")
        if url.startswith("data:"):
            import base64
            return load(base64.b64decode(url.split(",", 1)[1]))
        img = self.client.get(url)
        img.raise_for_status()
        return load(img.content)

    def generate(self, prompt, *, model, aspect_ratio, resolution, refs=None):
        payload = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "num_images": 1,
            "output_format": "png",
        }
        if refs:
            # Text-to-image with references: use the model's edit endpoint.
            m = model.rstrip("/")
            edit_model = m if m.endswith("/edit") else m + "/edit"
            return self.edit(prompt, model=edit_model, image=refs[0],
                             refs=refs[1:], resolution=resolution, aspect_ratio=aspect_ratio)
        return self._run(model, payload)

    def edit(self, prompt, *, model, image, resolution, refs=None, aspect_ratio=None):
        urls = [data_uri(image, 4096)] + [data_uri(r, 1536) for r in (refs or [])]
        payload = {
            "prompt": prompt,
            "image_urls": urls,
            "resolution": resolution,
            "num_images": 1,
            "output_format": "png",
        }
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        return self._run(model, payload)

    def upscale(self, image, *, model, factor):
        return self._run(model, {"image_url": data_uri(image, 8192), "scale": factor})
