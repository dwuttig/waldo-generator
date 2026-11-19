# Hidden Family

Turn photos of your family into cartoon characters and hide them in a huge, crowded,
high-resolution hidden-object picture that you describe in plain text.

The art style is original (storybook ink and pastel by default, editable in Settings).
It does not copy any existing book or character.

## How it works

1. **Family**: add a person, upload 1 to 3 photos, click *Make cartoon versions*.
   The app draws several variations (optionally with an outfit you type); pick the one to use.
   Already have cartoon images of someone? Use *Upload ready-made characters* instead of
   photos. They are used as-is; if you upload several (e.g. front and side view), all of
   them are sent as references when that person is hidden in a scene.
2. **Scene**: describe the place ("a snowy Christmas market with an ice rink"), tick who to
   hide, optionally click a rough spot for each person, and generate.
3. The pipeline then:
   - paints a crowded **base scene** from your description (4K by default),
   - **hides each person** by redrawing only a small square patch around their spot with
     their character sheet as reference, blended back with a soft mask,
   - runs a **tiled detail pass**: the scene is cut into overlapping tiles, each tile is
     redrawn at higher resolution and the tiles are feathered together,
   - **upscales** the result (Real-ESRGAN 2x by default, roughly 9300 x 6200 px for 3:2),
   - saves the final PNG/JPG and an **answer key** with every hidden person circled.

## Backends

By default everything runs **locally on ComfyUI** with bundled workflows, so there are no
per-image costs. Pick the hardware profile that fits your PC with `COMFYUI_PROFILE` in
`.env` (it decides which models are downloaded and which workflows are pre-selected);
you can also switch in Settings.

**`lite` (default): NVIDIA GPU with about 8 GB VRAM, 16 GB RAM, ~19 GB of models**

| Step | Workflow | Model |
|---|---|---|
| Cartoon characters | `builtin:lite_edit` | FLUX.1 Kontext dev, 4-bit GGUF, 20 steps |
| Base scene | `builtin:lite_scene` | FLUX.1 schnell, 4-bit GGUF, 4 steps |
| Hide people | `builtin:lite_edit` | FLUX.1 Kontext dev, character images as reference latents |
| Detail pass | `builtin:lite_refine` | FLUX.1 schnell img2img at denoise 0.3 per tile |
| Upscale | `builtin:esrgan_upscale` | Real-ESRGAN x2plus |

**`full`: 16 to 24 GB VRAM, 64 GB RAM, ~45 GB of models, best likeness**

| Step | Workflow | Model |
|---|---|---|
| Cartoon characters | `builtin:qwen_edit` | Qwen-Image-Edit-2509 (fp8) + 4-step Lightning LoRA |
| Base scene | `builtin:qwen_image` | Qwen-Image (fp8) + 8-step Lightning LoRA |
| Hide people | `builtin:qwen_edit` | Qwen-Image-Edit-2509, character images as references |
| Detail pass | `builtin:qwen_refine` | Qwen-Image img2img at denoise 0.35 per tile |
| Upscale | `builtin:esrgan_upscale` | Real-ESRGAN x2plus |

The lite profile needs the ComfyUI-GGUF custom node, which the bundled ComfyUI image
installs. FLUX.1 Kontext dev has a non-commercial licence, which is fine for family use.
Resolution tiers for ComfyUI mean pixel budgets: 1K = 1 MP, 2K = 1.6 MP, 4K = 2.4 MP.
The models work best at 1 to 2 MP; the detail pass and the upscaler produce the final
print size. On a lite-class GPU expect roughly 15 to 30 minutes for a full scene with
the detail pass; turn the detail pass off in Settings for a quick preview.

Each step can be switched in **Settings** to another provider:

| Provider | What you need | Notes |
|---|---|---|
| `comfyui` (default) | Local ComfyUI | Free per image. The model field names a workflow: `builtin:…` or a path to your own API-format JSON |
| `fal` | fal.ai API key | Nano Banana Pro / 2 (best face likeness), FLUX.2, upscalers. About $0.15 per character, $1 to $3.50 per scene |
| `google` | Gemini API key | Same Nano Banana models billed by Google; no upscaler (falls back to Lanczos) |
| `mock` | nothing | Draws placeholders offline, to try the app |

When you switch a step to fal, the app fills in recommended models and prices. Also lower
*Detail tiles across* to 3 and *Detail scale* to 2, otherwise the detail pass makes 20
paid calls.

### Own ComfyUI workflows

Export a workflow in API format and use these placeholders in node inputs:
`{{PROMPT}}`, `{{SEED}}`, `{{WIDTH}}`, `{{HEIGHT}}`, `{{SCALE}}`, `{{IMAGE}}` (main input
image) and `{{REF_1}}`, `{{REF_2}}`… (reference images). Nodes whose placeholder gets no
value (for example `{{REF_2}}` when a person has only one character image) are removed
automatically, along with the links to them. Put the file path in a step's model field.

## Run it with Docker (recommended)

You need Docker with NVIDIA GPU support. On Windows that is Docker Desktop with the WSL 2
backend and a current NVIDIA driver; check it with
`docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu24.04 nvidia-smi`.

```bash
cp .env.example .env      # optional
docker compose up -d --build
docker compose logs -f models    # first start: downloads the models (~19 GB lite, ~45 GB full)
```

Open http://localhost:8000, go to **Settings** and click **Check ComfyUI**. It should
report your GPU and "All workflows and model files found". ComfyUI's own UI is at
http://localhost:8188 if you want to look at or tweak the workflows.

Photos, characters, scenes and settings live in the `scenegen-data` volume and models in
`comfyui-models`, so both survive restarts and updates.

- **ComfyUI already installed on this computer**: set
  `COMFYUI_URL=http://host.docker.internal:8188` in `.env`, start only the app with
  `docker compose up -d --build scenegen`, and get the models with
  `python comfyui/download_models.py <your ComfyUI>/models` (list in `comfyui/models.json`).
- **Other devices on your network** (phone, tablet): change the port line in
  `docker-compose.yml` to `"8000:8000"` and set `SCENEGEN_PASSWORD` in `.env`, then open
  `http://<your-computer>:8000` and log in as `family` (or `SCENEGEN_USER`).
- **On the internet**: put it behind a reverse proxy with HTTPS (Caddy, Traefik, nginx) and
  always set `SCENEGEN_PASSWORD`.
- **Update**: `git pull && docker compose up -d --build`.
- **Backup**: `docker run --rm -v waldo-generator_scenegen-data:/data -v "$PWD":/out alpine tar czf /out/backup.tgz -C /data .`

## Run it without Docker

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
python -m scenegen        # opens on http://127.0.0.1:8000, expects ComfyUI on 127.0.0.1:8188
```

Data (photos, characters, scenes) is stored in `./data` (change with `--data` or
`SCENEGEN_DATA`). It only listens on localhost by default; set `SCENEGEN_PASSWORD` to require a login.

## Tips

- JPEG, PNG, WebP and iPhone HEIC/HEIF photos all work (they are stored as JPEG).
- Clear, well-lit photos with the face visible give the best likeness; add a full-body photo for proportions.
- A smaller *hidden person patch size* makes people smaller and harder to find.
- More *detail tiles* means sharper backgrounds but more cost; if tiles show seams, try the
  Nano Banana Pro model for the detail step or reduce the tile count.
- Image models change quickly; model ids in Settings are free text, so you can switch to newer ones.
- If tiles show visible seams or the detail pass changes faces, lower the denoise in
  `scenegen/workflows/qwen_refine.json` (node 30) or turn the detail pass off.

## Development

```bash
pip install -e '.[dev]'
pytest
```
