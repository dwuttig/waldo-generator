"""FastAPI web app. Run with:  python -m scenegen  (see README)."""

from __future__ import annotations

import logging
import os
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import MODEL_PRESETS, SettingsStore
from .imaging import load
from .pipeline import Entry, estimate_cost, make_character_variations, run_scene
from .storage import Found, Person, Placement, Scene, Store, dump, new_id

log = logging.getLogger("scenegen")
STATIC = Path(__file__).parent / "static"


class NewPerson(BaseModel):
    name: str


class CharacterRequest(BaseModel):
    outfit: str = ""
    n: int | None = None


class NewScene(BaseModel):
    description: str
    placements: list[Placement]
    aspect_ratio: str | None = None
    seed: int | None = None


def create_app(data_dir: Path | str | None = None, workers: int = 2) -> FastAPI:
    root = Path(data_dir or os.environ.get("SCENEGEN_DATA", "data")).resolve()
    store = Store(root)
    settings_store = SettingsStore(root / "settings.json")
    pool = ThreadPoolExecutor(max_workers=workers)
    jobs: dict[str, dict] = {}
    jobs_lock = threading.Lock()

    app = FastAPI(title="Family hidden-object scene generator")
    app.state.store = store
    app.state.pool = pool

    def _person(pid: str) -> Person:
        try:
            return store.get_person(pid)
        except KeyError:
            raise HTTPException(404, "Person not found")

    # ------------------------------------------------------------------ settings
    @app.get("/api/settings")
    def get_settings():
        return settings_store.public(settings_store.load())

    @app.put("/api/settings")
    def put_settings(body: dict):
        try:
            s = settings_store.update(body)
        except Exception as e:  # validation errors
            raise HTTPException(400, str(e))
        return settings_store.public(s)

    @app.get("/api/presets")
    def presets():
        return MODEL_PRESETS

    @app.get("/api/estimate")
    def estimate(n: int = 1, aspect_ratio: str | None = None):
        return estimate_cost(settings_store.load(), n, aspect_ratio)

    # ------------------------------------------------------------------ people
    @app.get("/api/people")
    def list_people():
        return [dump(p) for p in store.list_people()]

    @app.post("/api/people")
    def add_person(body: NewPerson):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Name is required")
        p = Person(id=new_id(), name=name)
        store.save_person(p)
        return dump(p)

    @app.patch("/api/people/{pid}")
    def rename_person(pid: str, body: NewPerson):
        p = _person(pid)
        p.name = body.name.strip() or p.name
        store.save_person(p)
        return dump(p)

    @app.delete("/api/people/{pid}")
    def delete_person(pid: str):
        _person(pid)
        store.delete_person(pid)
        return {"ok": True}

    @app.post("/api/people/{pid}/photos")
    async def upload_photos(pid: str, files: list[UploadFile] = File(...)):
        p = _person(pid)
        for f in files:
            data = await f.read()
            try:
                img = load(data)
            except Exception:
                raise HTTPException(400, f"{f.filename} is not an image")
            store.add_photo(p, img)
        store.save_person(p)
        return dump(p)

    @app.delete("/api/people/{pid}/photos/{name}")
    def delete_photo(pid: str, name: str):
        p = _person(pid)
        rel = f"photos/{name}"
        if rel in p.photos:
            p.photos.remove(rel)
            (store.person_dir(pid) / rel).unlink(missing_ok=True)
            store.save_person(p)
        return dump(p)

    @app.post("/api/people/{pid}/characters")
    def make_characters(pid: str, body: CharacterRequest):
        p = _person(pid)
        if not p.photos:
            raise HTTPException(400, "Upload at least one photo first")
        settings = settings_store.load()
        n = body.n or settings.scene.character_variations
        job_id = new_id()
        with jobs_lock:
            jobs[job_id] = {"id": job_id, "status": "running", "progress": 0.0, "message": "Starting", "error": ""}

        def progress(f, msg):
            jobs[job_id].update(progress=f, message=msg)

        def work():
            try:
                photos = [store.person_image(pid, ph) for ph in p.photos]
                imgs = make_character_variations(settings, photos, body.outfit, n, progress)
                fresh = store.get_person(pid)
                for img in imgs:
                    store.add_sheet(fresh, img, body.outfit)
                store.save_person(fresh)
                jobs[job_id].update(status="done", progress=1.0, message="Done")
            except Exception as e:
                log.error("character job failed: %s", traceback.format_exc())
                jobs[job_id].update(status="error", error=str(e))

        pool.submit(work)
        return jobs[job_id]

    @app.post("/api/people/{pid}/sheets/{sid}/choose")
    def choose_sheet(pid: str, sid: str):
        p = _person(pid)
        if not any(s.id == sid for s in p.sheets):
            raise HTTPException(404, "Character not found")
        for s in p.sheets:
            s.chosen = s.id == sid
        store.save_person(p)
        return dump(p)

    @app.delete("/api/people/{pid}/sheets/{sid}")
    def delete_sheet(pid: str, sid: str):
        p = _person(pid)
        keep = []
        for s in p.sheets:
            if s.id == sid:
                (store.person_dir(pid) / s.file).unlink(missing_ok=True)
            else:
                keep.append(s)
        p.sheets = keep
        store.save_person(p)
        return dump(p)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        if job_id not in jobs:
            raise HTTPException(404, "Job not found")
        return jobs[job_id]

    # ------------------------------------------------------------------ scenes
    @app.get("/api/scenes")
    def list_scenes():
        return [dump(s) for s in store.list_scenes()]

    @app.get("/api/scenes/{sid}")
    def get_scene(sid: str):
        try:
            return dump(store.get_scene(sid))
        except KeyError:
            raise HTTPException(404, "Scene not found")

    @app.delete("/api/scenes/{sid}")
    def delete_scene(sid: str):
        store.delete_scene(sid)
        return {"ok": True}

    @app.post("/api/scenes")
    def create_scene(body: NewScene):
        if not body.description.strip():
            raise HTTPException(400, "Describe the scene first")
        if not body.placements:
            raise HTTPException(400, "Pick at least one person to hide")
        settings = settings_store.load()
        entries = []
        for pl in body.placements:
            p = _person(pl.person_id)
            sheet = next((s for s in p.sheets if s.id == pl.sheet_id), None) or p.chosen_sheet()
            if not sheet:
                raise HTTPException(400, f"{p.name} has no cartoon character yet")
            pl.sheet_id = sheet.id
            entries.append(Entry(p.id, p.name, store.person_image(p.id, sheet.file), pl.x, pl.y))
        scene = Scene(id=new_id(), description=body.description.strip(), placements=body.placements,
                      estimated_cost=estimate_cost(settings, len(entries), body.aspect_ratio)["total"])
        store.save_scene(scene)
        outdir = store.scene_dir(scene.id)

        def progress(f, msg):
            scene.progress, scene.message = round(f, 3), msg
            store.save_scene(scene)

        def work():
            scene.status = "running"
            store.save_scene(scene)
            try:
                res = run_scene(settings, scene.description, entries, outdir,
                                aspect_ratio=body.aspect_ratio, seed=body.seed, progress=progress)
                scene.files, scene.size = res["files"], res["size"]
                scene.found = [Found(**f) for f in res["found"]]
                scene.status, scene.progress, scene.message = "done", 1.0, "Done"
            except Exception as e:
                log.error("scene job failed: %s", traceback.format_exc())
                scene.status, scene.error = "error", str(e)
            store.save_scene(scene)

        pool.submit(work)
        return dump(scene)

    # ------------------------------------------------------------------ files
    def _safe(base: Path, rel: str) -> Path:
        path = (base / rel).resolve()
        if not path.is_relative_to(base.resolve()) or not path.is_file():
            raise HTTPException(404, "Not found")
        return path

    @app.get("/files/people/{pid}/{rel:path}")
    def person_file(pid: str, rel: str):
        try:
            return FileResponse(_safe(store.person_dir(pid), rel))
        except KeyError:
            raise HTTPException(404, "Not found")

    @app.get("/files/scenes/{sid}/{rel:path}")
    def scene_file(sid: str, rel: str, download: bool = False):
        try:
            path = _safe(store.scene_dir(sid), rel)
        except KeyError:
            raise HTTPException(404, "Not found")
        return FileResponse(path, filename=f"scene-{sid}-{path.name}" if download else None)

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
