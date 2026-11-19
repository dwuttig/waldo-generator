"""User-editable settings, stored as JSON in the data directory.

Every pipeline step (character sheets, base scene, placing people, detail pass,
upscale) has its own provider + model, so the user can mix and match backends.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Literal

from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator

ProviderName = Literal["fal", "google", "comfyui", "mock"]
StepName = Literal["character", "scene", "place", "detail", "upscale"]
STEPS: tuple[StepName, ...] = ("character", "scene", "place", "detail", "upscale")

DEFAULT_STYLE = (
    "hand-drawn storybook illustration, clean bold black ink outlines, flat bright "
    "pastel colour fills with soft watercolour texture, cheerful, characters with "
    "slightly oversized heads and simple friendly faces, rich small details. "
    "Original art style; do not depict any existing copyrighted character."
)


class StepConfig(BaseModel):
    provider: ProviderName = "fal"
    model: str = ""
    resolution: Literal["1K", "2K", "4K"] = "2K"
    # Estimated USD per call, only used for the cost preview in the UI.
    price: float = 0.0


def _comfy(character, scene, place, detail, upscale="builtin:esrgan_upscale") -> dict[str, StepConfig]:
    return {
        "character": StepConfig(provider="comfyui", model=character, resolution="1K", price=0),
        "scene": StepConfig(provider="comfyui", model=scene, resolution="2K", price=0),
        "place": StepConfig(provider="comfyui", model=place, resolution="1K", price=0),
        "detail": StepConfig(provider="comfyui", model=detail, resolution="1K", price=0),
        "upscale": StepConfig(provider="comfyui", model=upscale, resolution="4K", price=0),
    }


# Local hardware profiles for the bundled ComfyUI workflows.
COMFY_PROFILES = {
    # ~8 GB VRAM, 16 GB RAM: FLUX.1 schnell + FLUX.1 Kontext dev as 4-bit GGUF.
    "lite": _comfy("builtin:lite_edit", "builtin:lite_scene", "builtin:lite_edit", "builtin:lite_refine"),
    # 16-24 GB VRAM, 64 GB RAM: Qwen-Image + Qwen-Image-Edit-2509 (fp8). Best likeness.
    "full": _comfy("builtin:qwen_edit", "builtin:qwen_image", "builtin:qwen_edit", "builtin:qwen_refine"),
}


def _default_steps() -> dict[str, StepConfig]:
    """Default: everything on the local ComfyUI with the bundled workflows."""
    name = os.environ.get("COMFYUI_PROFILE", "lite")
    return {k: v.model_copy() for k, v in COMFY_PROFILES.get(name, COMFY_PROFILES["lite"]).items()}


# Suggested settings when switching a step to fal.ai (prices in USD, Nov 2026).
FAL_STEPS = {
    "character": StepConfig(provider="fal", model="fal-ai/nano-banana-pro/edit", resolution="2K", price=0.15),
    "scene": StepConfig(provider="fal", model="fal-ai/nano-banana-pro", resolution="4K", price=0.30),
    "place": StepConfig(provider="fal", model="fal-ai/nano-banana-pro/edit", resolution="1K", price=0.15),
    "detail": StepConfig(provider="fal", model="fal-ai/nano-banana-2/edit", resolution="2K", price=0.12),
    "upscale": StepConfig(provider="fal", model="fal-ai/esrgan", resolution="4K", price=0.02),
}


# Suggested models per provider, shown as presets in the settings UI.
MODEL_PRESETS: dict[str, dict[str, list[str]]] = {
    "fal": {
        "generate": ["fal-ai/nano-banana-pro", "fal-ai/nano-banana-2", "fal-ai/flux-2-pro"],
        "edit": ["fal-ai/nano-banana-pro/edit", "fal-ai/nano-banana-2/edit", "fal-ai/flux-2-pro/edit"],
        "upscale": ["fal-ai/esrgan", "fal-ai/clarity-upscaler"],
    },
    "google": {
        "generate": ["gemini-3-pro-image-preview", "gemini-3.1-flash-image-preview"],
        "edit": ["gemini-3-pro-image-preview", "gemini-3.1-flash-image-preview"],
        "upscale": [],
    },
    "comfyui": {
        "generate": ["builtin:lite_scene", "builtin:qwen_image"],
        "edit": ["builtin:lite_edit", "builtin:lite_refine", "builtin:qwen_edit", "builtin:qwen_refine"],
        "upscale": ["builtin:esrgan_upscale"],
    },
    "mock": {"generate": ["mock"], "edit": ["mock"], "upscale": ["mock"]},
}


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _is_local_or_empty(url: str) -> bool:
    url = (url or "").strip()
    if not url:
        return True
    host = urlparse(url if "://" in url else "http://" + url).hostname or ""
    return host.lower() in LOCAL_HOSTS


class SceneDefaults(BaseModel):
    aspect_ratio: Literal["1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16"] = "3:2"
    # Side of the square patch redrawn around each hidden person, as a fraction
    # of the scene's longer side. Smaller patch = smaller, better hidden person.
    place_patch: float = Field(0.22, ge=0.08, le=0.5)
    detail_enabled: bool = True
    detail_grid: int = Field(5, ge=1, le=8)   # tiles along the longer side
    detail_scale: int = Field(3, ge=1, le=4)  # output pixels per base pixel
    upscale_factor: int = Field(2, ge=1, le=4)  # extra upscale after detail (1 = off)
    character_variations: int = Field(4, ge=1, le=8)


class Settings(BaseModel):
    fal_api_key: str = ""
    google_api_key: str = ""
    comfyui_url: str = Field(default_factory=lambda: os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188"))
    # Fallback workflows per operation, used when a ComfyUI step's model is not a workflow.
    comfyui_workflows: dict[str, str] = Field(
        default_factory=lambda: {"generate": "", "edit": "", "upscale": ""}
    )
    style_prompt: str = DEFAULT_STYLE
    steps: dict[str, StepConfig] = Field(default_factory=_default_steps)
    scene: SceneDefaults = Field(default_factory=SceneDefaults)

    @model_validator(mode="after")
    def _prefer_env_comfyui_url(self) -> "Settings":
        # A URL saved by a run outside Docker (empty or localhost) cannot reach
        # ComfyUI from inside a container, so the deployment's COMFYUI_URL wins.
        env_url = os.environ.get("COMFYUI_URL", "").strip()
        if env_url and _is_local_or_empty(self.comfyui_url):
            self.comfyui_url = env_url
        return self

    def step(self, name: StepName) -> StepConfig:
        cfg = self.steps.get(name)
        if cfg is None:
            return _default_steps()[name]
        if cfg.provider == "comfyui" and not cfg.model.strip():
            # No model chosen: use the bundled workflow of the current profile.
            default = _default_steps()[name]
            return cfg.model_copy(update={"model": default.model, "resolution": default.resolution})
        return cfg

    def key_for(self, provider: str) -> str:
        if provider == "fal":
            return self.fal_api_key or os.environ.get("FAL_KEY", "")
        if provider == "google":
            return self.google_api_key or os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
        return ""


SECRET_FIELDS = ("fal_api_key", "google_api_key")


def mask(value: str) -> str:
    return "" if not value else "••••" + value[-4:]


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> Settings:
        if self.path.exists():
            return Settings.model_validate_json(self.path.read_text())
        return Settings()

    def save(self, settings: Settings) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(settings.model_dump_json(indent=2))
            tmp.replace(self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def public(self, settings: Settings) -> dict:
        """Settings as sent to the browser: API keys masked."""
        data = json.loads(settings.model_dump_json())
        for f in SECRET_FIELDS:
            data[f] = mask(data[f])
            data[f + "_from_env"] = bool(not getattr(settings, f) and settings.key_for(f.split("_")[0]))
        return data

    def update(self, incoming: dict) -> Settings:
        """Merge a settings update. Masked or empty key values keep the stored key."""
        current = self.load()
        data = json.loads(current.model_dump_json())
        for k, v in incoming.items():
            if k.endswith("_from_env"):
                continue
            if k in SECRET_FIELDS and (not isinstance(v, str) or v.startswith("••••")):
                continue
            data[k] = v
        new = Settings.model_validate(data)
        self.save(new)
        return new
