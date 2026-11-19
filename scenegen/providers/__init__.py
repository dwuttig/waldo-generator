from __future__ import annotations

from ..config import Settings
from .base import ImageProvider, NotSupported, ProviderError


def get_provider(name: str, settings: Settings) -> ImageProvider:
    if name == "fal":
        from .fal import FalProvider
        return FalProvider(settings.key_for("fal"))
    if name == "google":
        from .google import GoogleProvider
        return GoogleProvider(settings.key_for("google"))
    if name == "comfyui":
        from .comfyui import ComfyUIProvider
        return ComfyUIProvider(settings.comfyui_url, settings.comfyui_workflows)
    if name == "mock":
        from .mock import MockProvider
        return MockProvider()
    raise ProviderError(f"Unknown provider: {name}")


__all__ = ["ImageProvider", "NotSupported", "ProviderError", "get_provider"]
