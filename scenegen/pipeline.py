"""The generation pipeline: character sheets, then base scene -> place people -> detail -> upscale."""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from . import prompts
from .config import Settings
from .imaging import edge_ramp_mask, feather_ellipse_mask, fit_long_side
from .providers import NotSupported, get_provider

log = logging.getLogger(__name__)
Progress = Callable[[float, str], None]


def _noop(_f: float, _m: str) -> None:
    pass


# --------------------------------------------------------------------------- characters

def make_character_variations(settings: Settings, photos: list[Image.Image], outfit: str, n: int,
                              progress: Progress = _noop) -> list[Image.Image]:
    if not photos:
        raise ValueError("Upload at least one photo first.")
    step = settings.step("character")
    provider = get_provider(step.provider, settings)
    out = []
    for i in range(n):
        progress(i / n, f"Drawing variation {i + 1} of {n}")
        prompt = prompts.character(settings.style_prompt, outfit, i + 1, len(photos))
        out.append(provider.edit(prompt, model=step.model, image=photos[0], refs=photos[1:3],
                                 resolution=step.resolution, aspect_ratio="2:3"))
    progress(1.0, "Done")
    return out


# --------------------------------------------------------------------------- scene helpers

@dataclass
class Entry:
    person_id: str
    name: str
    sheet: Image.Image
    x: float | None = None  # normalised 0..1 or None for random
    y: float | None = None


def choose_spots(entries: list[Entry], w: int, h: int, patch: int, rng: random.Random) -> list[tuple[int, int]]:
    """Pixel centres for each entry. Fixed spots are kept; random ones avoid edges and each other."""
    half = patch // 2
    spots: list[tuple[int, int] | None] = [None] * len(entries)
    for i, e in enumerate(entries):
        if e.x is not None and e.y is not None:
            spots[i] = (min(max(int(e.x * w), half), w - half), min(max(int(e.y * h), half), h - half))
    min_dist = patch * 1.1
    for i, e in enumerate(entries):
        if spots[i] is not None:
            continue
        taken = [s for s in spots if s is not None]
        best, best_d = None, -1.0
        for _ in range(200):
            c = (rng.randint(half, max(half, w - half)), rng.randint(half, max(half, h - half)))
            d = min((math.dist(c, s) for s in taken), default=1e9)
            if d >= min_dist:
                best = c
                break
            if d > best_d:
                best, best_d = c, d
        spots[i] = best
    return spots  # type: ignore[return-value]


def tile_boxes(w: int, h: int, grid: int, overlap: float = 0.15) -> list[tuple[int, int, int, int]]:
    """Overlapping crop boxes covering the image, `grid` tiles along the longer side."""
    core = math.ceil(max(w, h) / grid)
    nx, ny = math.ceil(w / core), math.ceil(h / core)
    cw, ch = math.ceil(w / nx), math.ceil(h / ny)
    ov = int(core * overlap)
    boxes = []
    for j in range(ny):
        for i in range(nx):
            x0, y0 = i * cw, j * ch
            x1, y1 = min(w, x0 + cw), min(h, y0 + ch)
            boxes.append((max(0, x0 - ov), max(0, y0 - ov), min(w, x1 + ov), min(h, y1 + ov)))
    return boxes


def estimate_cost(settings: Settings, n_people: int, aspect_ratio: str | None = None) -> dict:
    from .imaging import size_for

    sc = settings.scene
    w, h = size_for(aspect_ratio or sc.aspect_ratio, settings.step("scene").resolution)
    tiles = len(tile_boxes(w, h, sc.detail_grid)) if sc.detail_enabled else 0
    parts = {
        "scene": settings.step("scene").price,
        "place": settings.step("place").price * n_people,
        "detail": settings.step("detail").price * tiles,
        "upscale": settings.step("upscale").price if sc.upscale_factor > 1 else 0.0,
    }
    return {"total": round(sum(parts.values()), 2), "parts": parts, "tiles": tiles}


# --------------------------------------------------------------------------- scene stages

def place_people(settings: Settings, base: Image.Image, entries: list[Entry], rng: random.Random,
                 progress: Progress = _noop) -> tuple[Image.Image, list[dict]]:
    step = settings.step("place")
    provider = get_provider(step.provider, settings)
    img = base.copy()
    w, h = img.size
    patch = max(64, int(max(w, h) * settings.scene.place_patch))
    patch = min(patch, w, h)
    spots = choose_spots(entries, w, h, patch, rng)
    mask = feather_ellipse_mask(patch)
    found = []
    for k, (e, (cx, cy)) in enumerate(zip(entries, spots)):
        progress(k / max(1, len(entries)), f"Hiding {e.name}")
        box = (cx - patch // 2, cy - patch // 2, cx - patch // 2 + patch, cy - patch // 2 + patch)
        crop = img.crop(box)
        edited = provider.edit(prompts.place(settings.style_prompt, e.name), model=step.model,
                               image=crop, refs=[e.sheet], resolution=step.resolution, aspect_ratio="1:1")
        edited = edited.resize((patch, patch), Image.LANCZOS)
        img.paste(edited, box[:2], mask)
        found.append({"person_id": e.person_id, "name": e.name, "x": cx / w, "y": cy / h,
                      "r": patch * 0.22 / w})
    return img, found


def detail_pass(settings: Settings, img: Image.Image, found: list[dict], sheets: dict[str, Image.Image],
                progress: Progress = _noop) -> Image.Image:
    sc = settings.scene
    step = settings.step("detail")
    provider = get_provider(step.provider, settings)
    w, h = img.size
    s = sc.detail_scale
    out = img.resize((w * s, h * s), Image.LANCZOS)
    boxes = tile_boxes(w, h, sc.detail_grid)
    for k, (x0, y0, x1, y1) in enumerate(boxes):
        progress(k / len(boxes), f"Adding detail, tile {k + 1} of {len(boxes)}")
        inside = [f for f in found if x0 <= f["x"] * w < x1 and y0 <= f["y"] * h < y1]
        refs = [sheets[f["person_id"]] for f in inside if f["person_id"] in sheets]
        tile = img.crop((x0, y0, x1, y1))
        res = provider.edit(prompts.detail(settings.style_prompt, [f["name"] for f in inside]),
                            model=step.model, image=tile, refs=refs, resolution=step.resolution)
        tw, th = (x1 - x0) * s, (y1 - y0) * s
        res = res.resize((tw, th), Image.LANCZOS)
        # Feather into tiles already pasted to the left / above.
        left = min(tw, _overlap_left(boxes, k) * s)
        top = min(th, _overlap_top(boxes, k) * s)
        out.paste(res, (x0 * s, y0 * s), edge_ramp_mask(tw, th, left, top))
    return out


def _overlap_left(boxes, k) -> int:
    x0, y0, x1, y1 = boxes[k]
    prev = [b for b in boxes[:k] if b[1] == y0 and b[2] > x0 and b[0] < x0]
    return max((b[2] - x0 for b in prev), default=0)


def _overlap_top(boxes, k) -> int:
    x0, y0, x1, y1 = boxes[k]
    prev = [b for b in boxes[:k] if b[0] == x0 and b[3] > y0 and b[1] < y0]
    return max((b[3] - y0 for b in prev), default=0)


def upscale(settings: Settings, img: Image.Image, factor: int) -> Image.Image:
    if factor <= 1:
        return img
    step = settings.step("upscale")
    try:
        up = get_provider(step.provider, settings).upscale(img, model=step.model, factor=factor)
        return up.resize((img.width * factor, img.height * factor), Image.LANCZOS)
    except NotSupported:
        log.info("Provider %s has no upscaler, using Lanczos", step.provider)
        return img.resize((img.width * factor, img.height * factor), Image.LANCZOS)


def answer_key(img: Image.Image, found: list[dict]) -> Image.Image:
    key = fit_long_side(img, 4096) if max(img.size) > 4096 else img.copy()
    d = ImageDraw.Draw(key)
    w, h = key.size
    try:
        font = ImageFont.load_default(size=max(14, w // 60))
    except TypeError:  # very old Pillow
        font = ImageFont.load_default()
    lw = max(3, w // 400)
    for f in found:
        cx, cy, r = f["x"] * w, f["y"] * h, f["r"] * w
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(230, 20, 20), width=lw)
        d.text((cx - r, cy + r + lw), f["name"], fill=(230, 20, 20), font=font,
               stroke_width=lw // 2 + 1, stroke_fill=(255, 255, 255))
    return key


def run_scene(settings: Settings, description: str, entries: list[Entry], outdir: Path,
              aspect_ratio: str | None = None, seed: int | None = None,
              progress: Progress = _noop) -> dict:
    """Full pipeline. Writes images to `outdir` and returns {files, found, size}."""
    outdir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    sc = settings.scene
    aspect = aspect_ratio or sc.aspect_ratio
    files: dict[str, str] = {}

    # Weights for overall progress per stage.
    tiles = estimate_cost(settings, len(entries), aspect)["tiles"]
    weights = {"scene": 2.0, "place": 1.0 * len(entries), "detail": 1.0 * tiles,
               "upscale": 1.0 if sc.upscale_factor > 1 else 0.0}
    total = sum(weights.values()) or 1.0
    done = 0.0

    def stage(name):
        def cb(f, msg):
            progress((done + f * weights[name]) / total, msg)
        return cb

    step = settings.step("scene")
    stage("scene")(0, "Painting the base scene")
    base = get_provider(step.provider, settings).generate(
        prompts.scene(settings.style_prompt, description), model=step.model,
        aspect_ratio=aspect, resolution=step.resolution)
    base.save(outdir / "base.jpg", quality=92)
    files["base"] = "base.jpg"
    done += weights["scene"]

    img, found = place_people(settings, base, entries, rng, stage("place"))
    img.save(outdir / "placed.png")
    files["placed"] = "placed.png"
    done += weights["place"]

    if sc.detail_enabled and tiles:
        sheets = {e.person_id: e.sheet for e in entries}
        img = detail_pass(settings, img, found, sheets, stage("detail"))
        done += weights["detail"]

    if sc.upscale_factor > 1:
        stage("upscale")(0, "Upscaling")
        img = upscale(settings, img, sc.upscale_factor)
        done += weights["upscale"]

    progress(0.98, "Saving")
    img.save(outdir / "final.png")
    img.save(outdir / "final.jpg", quality=95)
    fit_long_side(img, 2048).save(outdir / "preview.jpg", quality=88)
    answer_key(img, found).save(outdir / "answer_key.jpg", quality=90)
    files.update({"final": "final.png", "final_jpg": "final.jpg", "preview": "preview.jpg",
                  "answer_key": "answer_key.jpg"})
    progress(1.0, "Done")
    return {"files": files, "found": found, "size": list(img.size)}
