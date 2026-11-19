# Hidden Family

Turn photos of your family into cartoon characters and hide them in a huge, crowded,
high-resolution hidden-object picture that you describe in plain text.

The art style is original (storybook ink and pastel by default, editable in Settings).
It does not copy any existing book or character.

## How it works

1. **Family**: add a person, upload 1 to 3 photos, click *Make cartoon versions*.
   The app draws several variations (optionally with an outfit you type); pick the one to use.
2. **Scene**: describe the place ("a snowy Christmas market with an ice rink"), tick who to
   hide, optionally click a rough spot for each person, and generate.
3. The pipeline then:
   - paints a crowded **base scene** from your description (4K by default),
   - **hides each person** by redrawing only a small square patch around their spot with
     their character sheet as reference, blended back with a soft mask,
   - runs a **tiled detail pass**: the scene is cut into overlapping tiles, each tile is
     redrawn at higher resolution and the tiles are feathered together (4K becomes about 8K),
   - optionally **upscales** further,
   - saves the final PNG/JPG and an **answer key** with every hidden person circled.

## Configurable backends

Every step (characters, base scene, hiding people, detail pass, upscale) has its own
provider, model, resolution and price in **Settings**, so you can mix them:

| Provider | What you need | Notes |
|---|---|---|
| `fal` (default) | fal.ai API key | Nano Banana Pro / Nano Banana 2 (best face likeness), FLUX.2, ESRGAN / Clarity upscalers |
| `google` | Gemini API key | Same Nano Banana models billed by Google; no upscaler (falls back to Lanczos) |
| `comfyui` | Local ComfyUI + workflows | Free per image; needs a strong GPU (24 GB recommended) and your own workflows, see below |
| `mock` | nothing | Draws placeholders offline. Use it to try the app without spending money |

Keys go in Settings (saved to `data/settings.json`, never sent anywhere except to the
provider) or as environment variables `FAL_KEY` / `GEMINI_API_KEY`.

Rough costs with the defaults on fal (Nov 2026 prices): about $0.15 per character
variation, and $1 to $3.50 per finished scene depending on detail tiles and number of
people. The Scenes tab shows an estimate before you generate.

### ComfyUI workflows

Export each workflow in API format and put these placeholders into node inputs:
`{{PROMPT}}`, `{{SEED}}`, `{{WIDTH}}`, `{{HEIGHT}}`, `{{MODEL}}`, `{{SCALE}}`,
`{{IMAGE}}` (main input image) and `{{REF_1}}`, `{{REF_2}}`… (reference images).
Set the three workflow paths (generate, edit, upscale) in Settings.

## Run it with Docker (recommended)

```bash
cp .env.example .env      # optional: add FAL_KEY / GEMINI_API_KEY / SCENEGEN_PASSWORD
docker compose up -d --build
```

Open http://localhost:8000. Photos, characters, scenes and settings live in the
`scenegen-data` Docker volume, so they survive restarts and image updates.

- **Other devices on your network** (phone, tablet): change the port line in
  `docker-compose.yml` to `"8000:8000"` and set `SCENEGEN_PASSWORD` in `.env`, then open
  `http://<your-computer>:8000` and log in as `family` (or `SCENEGEN_USER`).
- **On the internet**: put it behind a reverse proxy with HTTPS (Caddy, Traefik, nginx) and
  always set `SCENEGEN_PASSWORD`; generating images spends money on your API key.
- **Local ComfyUI on the same machine**: use `http://host.docker.internal:8188` as the ComfyUI URL.
- **Update**: `git pull && docker compose up -d --build`.
- **Backup**: `docker run --rm -v waldo-generator_scenegen-data:/data -v "$PWD":/out alpine tar czf /out/backup.tgz -C /data .`

## Run it without Docker

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
export FAL_KEY=...        # or enter it in Settings
python -m scenegen        # opens on http://127.0.0.1:8000
```

Data (photos, characters, scenes) is stored in `./data` (change with `--data` or
`SCENEGEN_DATA`). It only listens on localhost by default; set `SCENEGEN_PASSWORD` to require a login.

## Tips

- Clear, well-lit photos with the face visible give the best likeness; add a full-body photo for proportions.
- A smaller *hidden person patch size* makes people smaller and harder to find.
- More *detail tiles* means sharper backgrounds but more cost; if tiles show seams, try the
  Nano Banana Pro model for the detail step or reduce the tile count.
- Image models change quickly; model ids in Settings are free text, so you can switch to newer ones.

## Development

```bash
pip install -e '.[dev]'
pytest
```
