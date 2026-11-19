import base64
import io
import json
import time

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from scenegen.app import create_app
from scenegen.config import Settings, SettingsStore, StepConfig
from scenegen.pipeline import Entry, estimate_cost, run_scene, tile_boxes
from scenegen.providers.comfyui import fill_placeholders
from scenegen.providers.fal import FalProvider
from scenegen.providers.google import GoogleProvider


def mock_settings(**scene) -> Settings:
    s = Settings()
    s.steps = {k: StepConfig(provider="mock", model="mock", resolution="1K", price=0.1)
               for k in ("character", "scene", "place", "detail", "upscale")}
    for k, v in scene.items():
        setattr(s.scene, k, v)
    return s


def png_bytes(color=(10, 120, 200), size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_tile_boxes_cover_image():
    for w, h, g in [(1024, 683, 3), (683, 1024, 2), (1000, 1000, 1), (4096, 2731, 4)]:
        boxes = tile_boxes(w, h, g)
        covered = Image.new("L", (w, h), 0)
        for b in boxes:
            covered.paste(255, b)
        assert covered.getextrema() == (255, 255)


def test_run_scene_with_mock(tmp_path):
    s = mock_settings(detail_grid=2, detail_scale=2, upscale_factor=2)
    sheet = Image.new("RGB", (100, 150), (200, 30, 30))
    entries = [Entry("a", "Anna", sheet), Entry("b", "Ben", sheet, 0.1, 0.9)]
    res = run_scene(s, "a busy beach", entries, tmp_path, aspect_ratio="3:2", seed=1)
    final = Image.open(tmp_path / res["files"]["final"])
    assert final.size == (1024 * 4, 683 * 4)
    assert {f["name"] for f in res["found"]} == {"Anna", "Ben"}
    ben = next(f for f in res["found"] if f["name"] == "Ben")
    assert ben["x"] < 0.3 and ben["y"] > 0.7
    assert (tmp_path / "answer_key.jpg").exists()


def test_estimate_cost():
    s = mock_settings(detail_grid=3, upscale_factor=1)
    e = estimate_cost(s, 4, "3:2")
    assert e["tiles"] == 6
    assert e["total"] == round(0.1 + 0.4 + 0.6, 2)


def test_settings_keys_masked_and_kept(tmp_path):
    store = SettingsStore(tmp_path / "s.json")
    store.update({"fal_api_key": "secret-1234"})
    pub = store.public(store.load())
    assert pub["fal_api_key"] == "••••1234"
    store.update({"fal_api_key": pub["fal_api_key"], "style_prompt": "x"})  # masked value keeps key
    assert store.load().fal_api_key == "secret-1234"
    assert store.load().style_prompt == "x"


def test_fal_payload_and_download():
    seen = {}

    def handler(req: httpx.Request):
        if req.url.host == "fal.run":
            seen["url"] = str(req.url)
            seen["auth"] = req.headers["authorization"]
            seen["body"] = json.loads(req.content)
            return httpx.Response(200, json={"images": [{"url": "https://cdn.example/out.png"}]})
        return httpx.Response(200, content=png_bytes())

    p = FalProvider("k", client=httpx.Client(transport=httpx.MockTransport(handler)))
    img = p.edit("hi", model="fal-ai/nano-banana-pro/edit", image=Image.new("RGB", (32, 32)),
                 refs=[Image.new("RGB", (8, 8))], resolution="2K", aspect_ratio="1:1")
    assert img.size == (64, 64)
    assert seen["url"] == "https://fal.run/fal-ai/nano-banana-pro/edit"
    assert seen["auth"] == "Key k"
    assert len(seen["body"]["image_urls"]) == 2 and seen["body"]["resolution"] == "2K"


def test_google_payload():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["body"] = json.loads(req.content)
        data = base64.b64encode(png_bytes()).decode()
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"inlineData": {"data": data}}]}}]})

    p = GoogleProvider("k", client=httpx.Client(transport=httpx.MockTransport(handler)))
    img = p.generate("hi", model="gemini-3-pro-image-preview", aspect_ratio="3:2", resolution="4K")
    assert img.size == (64, 64)
    assert seen["url"].endswith("gemini-3-pro-image-preview:generateContent")
    assert seen["body"]["generationConfig"]["imageConfig"] == {"imageSize": "4K", "aspectRatio": "3:2"}


def test_comfy_placeholders():
    wf = {"1": {"inputs": {"text": "{{PROMPT}}", "seed": "{{SEED}}", "image": "{{REF_1}}", "x": "a {{WIDTH}}"}}}
    out = fill_placeholders(wf, {"PROMPT": "p", "SEED": 7, "REF_1": "r.png", "WIDTH": 10})
    assert out["1"]["inputs"] == {"text": "p", "seed": 7, "image": "r.png", "x": "a 10"}


def _wait(fn, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_app_end_to_end(tmp_path):
    client = TestClient(create_app(tmp_path))
    s = client.get("/api/settings").json()
    for step in s["steps"].values():
        step.update(provider="mock", model="mock", resolution="1K")
    s["scene"]["detail_grid"] = 2
    assert client.put("/api/settings", json=s).status_code == 200

    pid = client.post("/api/people", json={"name": "Oma"}).json()["id"]
    r = client.post(f"/api/people/{pid}/photos", files=[("files", ("a.png", png_bytes(), "image/png"))])
    assert len(r.json()["photos"]) == 1
    job = client.post(f"/api/people/{pid}/characters", json={"outfit": "red hat", "n": 2}).json()
    _wait(lambda: client.get(f"/api/jobs/{job['id']}").json()["status"] == "done")
    person = client.get("/api/people").json()[0]
    assert len(person["sheets"]) == 2

    scene = client.post("/api/scenes", json={"description": "zoo", "placements": [{"person_id": pid}]}).json()
    done = _wait(lambda: (lambda d: d if d["status"] in ("done", "error") else None)(
        client.get(f"/api/scenes/{scene['id']}").json()))
    assert done["status"] == "done", done["error"]
    assert client.get(f"/files/scenes/{scene['id']}/{done['files']['preview']}").status_code == 200
    assert client.get(f"/files/scenes/{scene['id']}/../settings.json").status_code == 404
    assert client.get("/").status_code == 200


def test_password_protection(tmp_path, monkeypatch):
    monkeypatch.setenv("SCENEGEN_PASSWORD", "pw")
    client = TestClient(create_app(tmp_path))
    assert client.get("/api/people").status_code == 401
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/people", auth=("family", "pw")).status_code == 200
    assert client.get("/api/people", auth=("family", "nope")).status_code == 401


def test_heic_upload(tmp_path):
    import pillow_heif

    heif = pillow_heif.from_pillow(Image.new("RGB", (80, 60), (200, 100, 50)))
    buf = io.BytesIO()
    heif.save(buf, quality=90)
    client = TestClient(create_app(tmp_path))
    pid = client.post("/api/people", json={"name": "Ben"}).json()["id"]
    r = client.post(f"/api/people/{pid}/photos", files=[("files", ("IMG_0001.HEIC", buf.getvalue(), "image/heic"))])
    assert r.status_code == 200, r.text
    photo = r.json()["photos"][0]
    assert photo.endswith(".jpg")
    img = Image.open(client.app.state.store.person_dir(pid) / photo)
    assert img.size == (80, 60)


def test_uploaded_assets_used_as_references(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path))
    s = client.get("/api/settings").json()
    for step in s["steps"].values():
        step.update(provider="mock", model="mock", resolution="1K")
    s["scene"]["detail_enabled"] = False
    client.put("/api/settings", json=s)

    pid = client.post("/api/people", json={"name": "Lea"}).json()["id"]
    buf = io.BytesIO()
    Image.new("RGBA", (40, 60), (0, 0, 0, 0)).save(buf, "PNG")  # transparent cut-out
    files = [("files", (f"lea{i}.png", buf.getvalue(), "image/png")) for i in range(3)]
    person = client.post(f"/api/people/{pid}/assets", files=files).json()
    assert [sh["source"] for sh in person["sheets"]] == ["uploaded"] * 3
    assert sum(sh["chosen"] for sh in person["sheets"]) == 1
    assert person["photos"] == []

    seen = []
    from scenegen.providers.mock import MockProvider
    orig = MockProvider.edit

    def spy(self, prompt, **kw):
        seen.append(kw.get("refs") or [])
        return orig(self, prompt, **kw)

    monkeypatch.setattr(MockProvider, "edit", spy)
    scene = client.post("/api/scenes", json={"description": "park", "placements": [{"person_id": pid}]}).json()
    done = _wait(lambda: (lambda d: d if d["status"] in ("done", "error") else None)(
        client.get(f"/api/scenes/{scene['id']}").json()))
    assert done["status"] == "done", done["error"]
    assert len(seen[0]) == 3
    # transparency is flattened to white, not black
    assert seen[0][0].getpixel((0, 0)) == (255, 255, 255)


# ---------------------------------------------------------------- ComfyUI defaults

import pathlib

from scenegen.providers.comfyui import (WORKFLOW_DIR, ComfyUIProvider, dims_for_megapixels,
                                        prune_unfilled)

REPO = pathlib.Path(__file__).resolve().parent.parent


def test_bundled_workflows_are_consistent():
    models = json.loads((REPO / "comfyui" / "models.json").read_text())
    files = {m["file"] for m in models}
    used = set()
    for path in WORKFLOW_DIR.glob("*.json"):
        wf = json.loads(path.read_text())
        assert any(n["class_type"] == "SaveImage" for n in wf.values()), path.name
        for node in wf.values():
            for name, val in node["inputs"].items():
                if isinstance(val, list):
                    assert val[0] in wf, f"{path.name}: {name} links to missing node {val[0]}"
                if isinstance(val, str) and val.endswith((".safetensors", ".pth", ".gguf")):
                    used.add(val)
    assert used <= files, f"workflows use files not in models.json: {used - files}"


def test_default_settings_use_bundled_comfy_workflows():
    s = Settings()
    for name in ("character", "scene", "place", "detail", "upscale"):
        st = s.step(name)
        assert st.provider == "comfyui"
        assert (WORKFLOW_DIR / (st.model.split(":", 1)[1] + ".json")).exists()


def test_prune_removes_unused_reference_slots():
    wf = json.loads((WORKFLOW_DIR / "qwen_edit.json").read_text())
    out = prune_unfilled(fill_placeholders(wf, {"PROMPT": "p", "SEED": 1, "IMAGE": "a.png", "REF_1": "b.png"}))
    assert "12" not in out
    assert "image3" not in out["20"]["inputs"] and out["20"]["inputs"]["image2"] == ["11", 0]


def test_dims_for_megapixels():
    w, h = dims_for_megapixels(3000, 2000, 1.6)
    assert w % 16 == 0 and h % 16 == 0 and abs(w * h - 1.6e6) < 0.05e6


def test_comfy_provider_roundtrip():
    calls = {"uploads": 0}

    def handler(req: httpx.Request):
        p = req.url.path
        if p == "/upload/image":
            calls["uploads"] += 1
            return httpx.Response(200, json={"name": f"u{calls['uploads']}.png"})
        if p == "/prompt":
            calls["prompt"] = json.loads(req.content)["prompt"]
            return httpx.Response(200, json={"prompt_id": "abc"})
        if p == "/history/abc":
            return httpx.Response(200, json={"abc": {"status": {"completed": True}, "outputs": {
                "50": {"images": [{"filename": "o.png", "subfolder": "", "type": "output"}]}}}})
        if p == "/view":
            return httpx.Response(200, content=png_bytes(size=(96, 96)))
        return httpx.Response(404)

    prov = ComfyUIProvider("http://comfy:8188", client=httpx.Client(transport=httpx.MockTransport(handler)),
                           poll_interval=0)
    img = prov.edit("add Oma", model="builtin:qwen_edit", image=Image.new("RGB", (300, 300)),
                    refs=[Image.new("RGB", (50, 80))], resolution="1K")
    assert img.size == (96, 96)
    wf = calls["prompt"]
    assert calls["uploads"] == 2
    assert wf["10"]["inputs"]["image"] == "u1.png" and wf["11"]["inputs"]["image"] == "u2.png"
    assert "12" not in wf
    assert wf["20"]["inputs"]["prompt"] == "add Oma"
    assert isinstance(wf["30"]["inputs"]["seed"], int)


def test_comfy_check_reports_missing_files():
    info = {
        "UNETLoader": {"input": {"required": {"unet_name": [["qwen_image_fp8_e4m3fn.safetensors"]], "weight_dtype": [["default"]]}}},
        "CLIPLoader": {"input": {"required": {"clip_name": ["COMBO", {"options": []}], "type": [["qwen_image"]]},
                                 "optional": {"device": [["default"]]}}},
    }

    def handler(req: httpx.Request):
        if req.url.path == "/system_stats":
            return httpx.Response(200, json={"devices": [{"name": "RTX 4090", "vram_total": 24e9}]})
        return httpx.Response(200, json=info)

    prov = ComfyUIProvider("http://c", client=httpx.Client(transport=httpx.MockTransport(handler)))
    r = prov.check(["builtin:qwen_image"])
    assert r["reachable"] and not r["ok"]
    assert any("qwen_2.5_vl" in f for f in r["missing_files"])
    assert not any("qwen_image_fp8" in f for f in r["missing_files"])
    assert any(n.startswith("VAELoader") for n in r["missing_nodes"])



def test_lite_edit_prunes_missing_reference_chain():
    wf = json.loads((WORKFLOW_DIR / "lite_edit.json").read_text())
    out = prune_unfilled(fill_placeholders(wf, {"PROMPT": "p", "SEED": 1, "IMAGE": "a.png", "REF_1": "b.png"}))
    assert {"12", "22"}.isdisjoint(out)
    assert "latent" not in out["25"]["inputs"] and out["25"]["inputs"]["conditioning"] == ["24", 0]
    assert out["26"]["inputs"]["conditioning"] == ["25", 0] and "30" in out
    out0 = prune_unfilled(fill_placeholders(wf, {"PROMPT": "p", "SEED": 1, "IMAGE": "a.png"}))
    assert {"11", "12", "21", "22"}.isdisjoint(out0) and "30" in out0


def test_profiles_and_downloader_manifest():
    from scenegen.config import COMFY_PROFILES

    models = json.loads((REPO / "comfyui" / "models.json").read_text())
    for name, steps in COMFY_PROFILES.items():
        needed = set()
        for st in steps.values():
            wf = json.loads((WORKFLOW_DIR / (st.model.split(":", 1)[1] + ".json")).read_text())
            needed |= {v for n in wf.values() for v in n["inputs"].values()
                       if isinstance(v, str) and v.endswith((".safetensors", ".pth", ".gguf"))}
        have = {m["file"] for m in models if name in m["profiles"]}
        assert needed <= have, f"profile {name} misses {needed - have}"
