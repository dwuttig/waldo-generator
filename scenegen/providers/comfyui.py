"""Local ComfyUI backend.

You export a workflow from ComfyUI in *API format* (Settings > enable dev mode >
"Save (API Format)") and put these placeholders into its node inputs:

    {{PROMPT}}  {{SEED}}  {{WIDTH}}  {{HEIGHT}}  {{MODEL}}  {{SCALE}}
    {{IMAGE}}   main input image (LoadImage node "image" field)
    {{REF_1}} .. {{REF_n}}  reference images (e.g. for IP-Adapter / PuLID / Kontext)

A placeholder that is the whole value of a field is replaced with the right type
(numbers stay numbers). The first image output of the workflow is returned.
"""

from __future__ import annotations

import json
import random
import time
import uuid
from pathlib import Path

import httpx
from PIL import Image

from ..imaging import RES_PX, load, size_for, to_bytes
from .base import ImageProvider, NotSupported, ProviderError


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


class ComfyUIProvider(ImageProvider):
    name = "comfyui"

    def __init__(self, base_url: str, workflows: dict[str, str], client: httpx.Client | None = None,
                 poll_interval: float = 1.0, timeout: float = 1800):
        self.base = base_url.rstrip("/")
        self.workflows = workflows
        self.client = client or httpx.Client(timeout=120)
        self.poll_interval = poll_interval
        self.timeout = timeout

    def _workflow(self, op: str) -> dict:
        path = self.workflows.get(op)
        if not path:
            raise NotSupported(f"No ComfyUI workflow configured for '{op}'.")
        p = Path(path).expanduser()
        if not p.exists():
            raise ProviderError(f"ComfyUI workflow file not found: {p}")
        return json.loads(p.read_text())

    def _upload(self, img: Image.Image) -> str:
        name = f"scenegen_{uuid.uuid4().hex}.png"
        r = self.client.post(f"{self.base}/upload/image",
                             files={"image": (name, to_bytes(img), "image/png")},
                             data={"overwrite": "true"})
        if r.status_code >= 400:
            raise ProviderError(f"ComfyUI upload failed ({r.status_code}): {r.text[:300]}")
        return r.json().get("name", name)

    def _run(self, op: str, values: dict, image: Image.Image | None, refs: list[Image.Image]) -> Image.Image:
        wf = self._workflow(op)
        if image is not None:
            values["IMAGE"] = self._upload(image)
        for i, ref in enumerate(refs, 1):
            values[f"REF_{i}"] = self._upload(ref)
        values.setdefault("SEED", random.randint(0, 2**31 - 1))
        wf = fill_placeholders(wf, values)
        r = self.client.post(f"{self.base}/prompt", json={"prompt": wf, "client_id": "scenegen"})
        if r.status_code >= 400:
            raise ProviderError(f"ComfyUI rejected workflow ({r.status_code}): {r.text[:500]}")
        prompt_id = r.json()["prompt_id"]
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            h = self.client.get(f"{self.base}/history/{prompt_id}").json()
            entry = h.get(prompt_id)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise ProviderError(f"ComfyUI workflow error: {json.dumps(status)[:500]}")
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
        w, h = size_for(aspect_ratio, resolution)
        return self._run("generate", {"PROMPT": prompt, "WIDTH": w, "HEIGHT": h, "MODEL": model},
                         None, refs or [])

    def edit(self, prompt, *, model, image, resolution, refs=None, aspect_ratio=None):
        long_side = RES_PX.get(resolution, 2048)
        s = long_side / max(image.size)
        w, h = round(image.width * s), round(image.height * s)
        return self._run("edit", {"PROMPT": prompt, "WIDTH": w, "HEIGHT": h, "MODEL": model},
                         image, refs or [])

    def upscale(self, image, *, model, factor):
        return self._run("upscale", {"SCALE": factor, "MODEL": model,
                                     "WIDTH": image.width * factor, "HEIGHT": image.height * factor},
                         image, [])
