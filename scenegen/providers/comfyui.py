"""Local ComfyUI backend.

Bundled workflows (see ``scenegen/workflows``) are used by default: Qwen-Image for
the base scene and detail refinement, Qwen-Image-Edit-2509 for characters and for
hiding people, Real-ESRGAN for upscaling. For a ComfyUI step the *model* setting
names the workflow: ``builtin:qwen_edit`` or a path to your own JSON file.

Own workflows: export from ComfyUI in *API format* and use these placeholders:

    {{PROMPT}}  {{SEED}}  {{WIDTH}}  {{HEIGHT}}  {{SCALE}}
    {{IMAGE}}   main input image (LoadImage node "image" field)
    {{REF_1}} .. {{REF_n}}  reference images

A placeholder that is the whole value of a field is replaced with the right type
(numbers stay numbers). Nodes whose placeholder has no value (e.g. ``{{REF_2}}``
when only one reference was given) are removed together with links to them, so
optional image inputs just work. The first image output of the workflow is returned.
"""

from __future__ import annotations

import json
import math
import random
import re
import time
import uuid
from pathlib import Path

import httpx
from PIL import Image

from ..imaging import load, to_bytes
from .base import ImageProvider, NotSupported, ProviderError

WORKFLOW_DIR = Path(__file__).resolve().parent.parent / "workflows"
PLACEHOLDER = re.compile(r"\{\{[A-Z0-9_]+\}\}")
# Pixel budget per resolution tier. Qwen-Image works best around 1-2 megapixels;
# the tiled detail pass and upscaler take the scene to full print resolution.
MEGAPIXELS = {"1K": 1.0, "2K": 1.6, "4K": 2.4}


def fill_placeholders(obj, values: dict):
    if isinstance(obj, dict):
        return {k: fill_placeholders(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fill_placeholders(v, values) for v in obj]
    if isinstance(obj, str):
        for key, val in values.items():
            token = "{{" + key + "}}"
            if obj == token:
                return val
            if token in obj:
                obj = obj.replace(token, str(val))
        return obj
    return obj


def _has_placeholder(obj) -> bool:
    if isinstance(obj, dict):
        return any(_has_placeholder(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_placeholder(v) for v in obj)
    return isinstance(obj, str) and bool(PLACEHOLDER.search(obj))


def prune_unfilled(wf: dict) -> dict:
    """Drop nodes that still contain placeholders, and inputs linking to dropped nodes."""
    wf = {k: v for k, v in wf.items() if not _has_placeholder(v.get("inputs", {}))}
    changed = True
    while changed:
        changed = False
        for nid, node in list(wf.items()):
            inputs = node.get("inputs", {})
            for name, val in list(inputs.items()):
                if isinstance(val, list) and len(val) == 2 and isinstance(val[0], str) and val[0] not in wf:
                    del inputs[name]
                    changed = True
    return wf


def resolve_workflow_path(ref: str) -> Path:
    if ref.startswith("builtin:"):
        name = ref.split(":", 1)[1]
        path = WORKFLOW_DIR / (name if name.endswith(".json") else name + ".json")
    else:
        path = Path(ref).expanduser()
    if not path.exists():
        raise ProviderError(f"ComfyUI workflow not found: {ref}")
    return path


def dims_for_megapixels(w: int, h: int, mp: float, multiple: int = 16) -> tuple[int, int]:
    s = math.sqrt(mp * 1_000_000 / (w * h))
    return (max(multiple, round(w * s / multiple) * multiple),
            max(multiple, round(h * s / multiple) * multiple))


class ComfyUIProvider(ImageProvider):
    name = "comfyui"

    def __init__(self, base_url: str, workflows: dict[str, str] | None = None,
                 client: httpx.Client | None = None, poll_interval: float = 1.0, timeout: float = 1800):
        self.base = base_url.rstrip("/")
        self.workflows = workflows or {}
        self.client = client or httpx.Client(timeout=120)
        self.poll_interval = poll_interval
        self.timeout = timeout

    def _workflow(self, op: str, model: str) -> dict:
        ref = model if (model.startswith("builtin:") or model.endswith(".json")) else self.workflows.get(op, "")
        if not ref:
            raise NotSupported(f"No ComfyUI workflow configured for '{op}'.")
        return json.loads(resolve_workflow_path(ref).read_text())

    def _upload(self, img: Image.Image) -> str:
        name = f"scenegen_{uuid.uuid4().hex}.png"
        try:
            r = self.client.post(f"{self.base}/upload/image",
                                 files={"image": (name, to_bytes(img), "image/png")},
                                 data={"overwrite": "true"})
        except httpx.ConnectError:
            raise ProviderError(f"Cannot reach ComfyUI at {self.base}. Is it running?")
        if r.status_code >= 400:
            raise ProviderError(f"ComfyUI upload failed ({r.status_code}): {r.text[:300]}")
        return r.json().get("name", name)

    def _run(self, op: str, model: str, values: dict, image: Image.Image | None,
             refs: list[Image.Image]) -> Image.Image:
        wf = self._workflow(op, model)
        if image is not None:
            values["IMAGE"] = self._upload(image)
        for i, ref in enumerate(refs, 1):
            values[f"REF_{i}"] = self._upload(ref)
        values.setdefault("SEED", random.randint(0, 2**31 - 1))
        wf = prune_unfilled(fill_placeholders(wf, values))
        try:
            r = self.client.post(f"{self.base}/prompt", json={"prompt": wf, "client_id": "scenegen"})
        except httpx.ConnectError:
            raise ProviderError(f"Cannot reach ComfyUI at {self.base}. Is it running?")
        if r.status_code >= 400:
            raise ProviderError(f"ComfyUI rejected the workflow ({r.status_code}): {r.text[:800]}")
        prompt_id = r.json()["prompt_id"]
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            h = self.client.get(f"{self.base}/history/{prompt_id}").json()
            entry = h.get(prompt_id)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise ProviderError(f"ComfyUI workflow error: {json.dumps(status)[:800]}")
                for node in entry.get("outputs", {}).values():
                    for im in node.get("images", []):
                        v = self.client.get(f"{self.base}/view", params={
                            "filename": im["filename"], "subfolder": im.get("subfolder", ""),
                            "type": im.get("type", "output")})
                        v.raise_for_status()
                        return load(v.content)
                if status.get("completed"):
                    raise ProviderError("ComfyUI workflow finished without an image output.")
            time.sleep(self.poll_interval)
        raise ProviderError("ComfyUI timed out.")

    def generate(self, prompt, *, model, aspect_ratio, resolution, refs=None):
        a, b = (int(x) for x in aspect_ratio.split(":"))
        w, h = dims_for_megapixels(a * 1000, b * 1000, MEGAPIXELS.get(resolution, 1.6))
        return self._run("generate", model, {"PROMPT": prompt, "WIDTH": w, "HEIGHT": h}, None, refs or [])

    def edit(self, prompt, *, model, image, resolution, refs=None, aspect_ratio=None):
        w, h = dims_for_megapixels(image.width, image.height, MEGAPIXELS.get(resolution, 1.0))
        image = image.resize((w, h), Image.LANCZOS)
        return self._run("edit", model, {"PROMPT": prompt, "WIDTH": w, "HEIGHT": h}, image, refs or [])

    def upscale(self, image, *, model, factor):
        return self._run("upscale", model, {"SCALE": factor, "WIDTH": image.width * factor,
                                            "HEIGHT": image.height * factor}, image, [])

    # ---------------------------------------------------------------- diagnostics
    def check(self, workflow_refs: list[str]) -> dict:
        """Ask ComfyUI which nodes and model files it has, and compare with the workflows."""
        report = {"url": self.base, "reachable": False, "missing_nodes": [], "missing_files": [], "errors": []}
        try:
            stats = self.client.get(f"{self.base}/system_stats", timeout=10).json()
            info = self.client.get(f"{self.base}/object_info", timeout=60).json()
        except Exception as e:  # noqa: BLE001
            report["errors"].append(f"Cannot reach ComfyUI at {self.base}: {e}")
            return report
        report["reachable"] = True
        devs = stats.get("devices") or []
        report["device"] = devs[0].get("name") if devs else "unknown"
        report["vram_gb"] = round(devs[0].get("vram_total", 0) / 1e9, 1) if devs else 0
        for ref in dict.fromkeys(workflow_refs):
            try:
                wf = json.loads(resolve_workflow_path(ref).read_text())
            except Exception as e:  # noqa: BLE001
                report["errors"].append(str(e))
                continue
            for node in wf.values():
                cls = node.get("class_type")
                if cls not in info:
                    report["missing_nodes"].append(f"{cls} ({ref})")
                    continue
                spec = {**info[cls].get("input", {}).get("required", {}),
                        **info[cls].get("input", {}).get("optional", {})}
                for name, val in node.get("inputs", {}).items():
                    if not isinstance(val, str) or PLACEHOLDER.search(val) or cls == "LoadImage":
                        continue
                    options = _combo_options(spec.get(name))
                    if options is not None and val not in options:
                        report["missing_files"].append(f"{val} ({cls}.{name})")
        report["missing_nodes"] = sorted(set(report["missing_nodes"]))
        report["missing_files"] = sorted(set(report["missing_files"]))
        report["ok"] = report["reachable"] and not (report["missing_nodes"] or report["missing_files"] or report["errors"])
        return report


def _combo_options(spec) -> list | None:
    """Option list of a COMBO input in either old ([[...], {}]) or new ("COMBO", {options}) format."""
    if not isinstance(spec, list) or not spec:
        return None
    if isinstance(spec[0], list):
        return spec[0]
    if spec[0] == "COMBO" and len(spec) > 1 and isinstance(spec[1], dict):
        return spec[1].get("options")
    return None
