"""Very small file-based store for people and scenes."""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, Field


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Sheet(BaseModel):
    id: str
    file: str
    outfit: str = ""
    chosen: bool = False


class Person(BaseModel):
    id: str
    name: str
    photos: list[str] = Field(default_factory=list)
    sheets: list[Sheet] = Field(default_factory=list)

    def chosen_sheet(self) -> Sheet | None:
        return next((s for s in self.sheets if s.chosen), None) or (self.sheets[0] if self.sheets else None)


class Placement(BaseModel):
    person_id: str
    sheet_id: str | None = None
    x: float | None = None  # 0..1, None = random
    y: float | None = None


class Found(BaseModel):
    person_id: str
    name: str
    x: float
    y: float
    r: float  # radius as fraction of the image width


class Scene(BaseModel):
    id: str
    description: str
    created: float = Field(default_factory=time.time)
    placements: list[Placement] = Field(default_factory=list)
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    message: str = ""
    error: str = ""
    found: list[Found] = Field(default_factory=list)
    files: dict[str, str] = Field(default_factory=dict)
    size: list[int] = Field(default_factory=list)
    estimated_cost: float = 0.0


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.people_dir = root / "people"
        self.scenes_dir = root / "scenes"
        self.people_dir.mkdir(parents=True, exist_ok=True)
        self.scenes_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # people -----------------------------------------------------------------
    def person_dir(self, pid: str) -> Path:
        if not pid.isalnum():
            raise KeyError(pid)
        return self.people_dir / pid

    def list_people(self) -> list[Person]:
        out = []
        for p in sorted(self.people_dir.glob("*/person.json"), key=lambda p: p.stat().st_mtime):
            out.append(Person.model_validate_json(p.read_text()))
        return out

    def get_person(self, pid: str) -> Person:
        f = self.person_dir(pid) / "person.json"
        if not f.exists():
            raise KeyError(pid)
        return Person.model_validate_json(f.read_text())

    def save_person(self, person: Person) -> None:
        with self._lock:
            d = self.person_dir(person.id)
            d.mkdir(parents=True, exist_ok=True)
            (d / "person.json").write_text(person.model_dump_json(indent=2))

    def delete_person(self, pid: str) -> None:
        shutil.rmtree(self.person_dir(pid), ignore_errors=True)

    def add_photo(self, person: Person, img: Image.Image) -> str:
        name = f"photos/{new_id()}.jpg"
        path = self.person_dir(person.id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path, "JPEG", quality=95)
        person.photos.append(name)
        return name

    def add_sheet(self, person: Person, img: Image.Image, outfit: str) -> Sheet:
        sid = new_id()
        name = f"sheets/{sid}.png"
        path = self.person_dir(person.id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        img.save(path, "PNG")
        sheet = Sheet(id=sid, file=name, outfit=outfit)
        person.sheets.append(sheet)
        return sheet

    def person_image(self, pid: str, rel: str) -> Image.Image:
        return Image.open(self.person_dir(pid) / rel).convert("RGB")

    # scenes -----------------------------------------------------------------
    def scene_dir(self, sid: str) -> Path:
        if not sid.isalnum():
            raise KeyError(sid)
        return self.scenes_dir / sid

    def list_scenes(self) -> list[Scene]:
        out = [Scene.model_validate_json(p.read_text()) for p in self.scenes_dir.glob("*/scene.json")]
        return sorted(out, key=lambda s: s.created, reverse=True)

    def get_scene(self, sid: str) -> Scene:
        f = self.scene_dir(sid) / "scene.json"
        if not f.exists():
            raise KeyError(sid)
        return Scene.model_validate_json(f.read_text())

    def save_scene(self, scene: Scene) -> None:
        with self._lock:
            d = self.scene_dir(scene.id)
            d.mkdir(parents=True, exist_ok=True)
            tmp = d / "scene.json.tmp"
            tmp.write_text(scene.model_dump_json(indent=2))
            tmp.replace(d / "scene.json")

    def delete_scene(self, sid: str) -> None:
        shutil.rmtree(self.scene_dir(sid), ignore_errors=True)


def dump(obj) -> dict:
    return json.loads(obj.model_dump_json())
