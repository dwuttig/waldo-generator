import json

import pytest

from scenegen.config import COMFY_PROFILES, Settings, SettingsStore, StepConfig


@pytest.mark.parametrize("saved", ["", "http://127.0.0.1:8188", "http://localhost:8188", "127.0.0.1:8188"])
def test_env_comfyui_url_replaces_local_saved_url(tmp_path, monkeypatch, saved):
    monkeypatch.setenv("COMFYUI_URL", "http://comfyui:8188")
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"comfyui_url": saved}))
    assert SettingsStore(path).load().comfyui_url == "http://comfyui:8188"


def test_env_comfyui_url_keeps_remote_saved_url(tmp_path, monkeypatch):
    monkeypatch.setenv("COMFYUI_URL", "http://comfyui:8188")
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"comfyui_url": "http://192.168.1.50:8188"}))
    assert SettingsStore(path).load().comfyui_url == "http://192.168.1.50:8188"


def test_saved_local_url_kept_without_env(monkeypatch):
    monkeypatch.delenv("COMFYUI_URL", raising=False)
    assert Settings(comfyui_url="http://127.0.0.1:8188").comfyui_url == "http://127.0.0.1:8188"


def test_empty_comfyui_model_falls_back_to_profile(monkeypatch):
    monkeypatch.setenv("COMFYUI_PROFILE", "lite")
    s = Settings(steps={"scene": StepConfig(provider="comfyui", model="", resolution="4K", price=0.3)})
    step = s.step("scene")
    lite = COMFY_PROFILES["lite"]["scene"]
    assert (step.provider, step.model, step.resolution) == ("comfyui", lite.model, lite.resolution)
    assert s.steps["scene"].model == ""  # stored settings are not changed


def test_explicit_model_and_other_providers_untouched(monkeypatch):
    monkeypatch.setenv("COMFYUI_PROFILE", "lite")
    s = Settings(steps={
        "scene": StepConfig(provider="comfyui", model="builtin:qwen_image", resolution="4K"),
        "place": StepConfig(provider="fal", model="", resolution="2K"),
    })
    assert s.step("scene").model == "builtin:qwen_image"
    assert s.step("scene").resolution == "4K"
    assert s.step("place").model == ""
