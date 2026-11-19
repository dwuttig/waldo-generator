from __future__ import annotations

from abc import ABC, abstractmethod

from PIL import Image


class ProviderError(RuntimeError):
    pass


class NotSupported(ProviderError):
    pass


class ImageProvider(ABC):
    """One image backend. All images in and out are RGB PIL images."""

    name: str = "base"

    @abstractmethod
    def generate(self, prompt: str, *, model: str, aspect_ratio: str, resolution: str,
                 refs: list[Image.Image] | None = None) -> Image.Image:
        """Text (plus optional reference images) to a new image."""

    @abstractmethod
    def edit(self, prompt: str, *, model: str, image: Image.Image, resolution: str,
             refs: list[Image.Image] | None = None, aspect_ratio: str | None = None) -> Image.Image:
        """Change `image` according to `prompt`, using `refs` (e.g. character sheets)."""

    def upscale(self, image: Image.Image, *, model: str, factor: int) -> Image.Image:
        raise NotSupported(f"{self.name} has no upscaler")
