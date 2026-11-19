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
