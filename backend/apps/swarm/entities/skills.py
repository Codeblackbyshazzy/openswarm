"""SkillExportable: skills are leaves (no deps, no requirements). An installed
skill is just a markdown file plus index metadata, so this also powers the
generic "import a .md or a zip-of-SKILL.md" path. Nothing here is secret, but
the body still rides the central scrub in case someone pasted a token into it."""
from __future__ import annotations

import os

from backend.apps.skills import skills as store
from ..exportable import DepRef, ExportContext, RemapTable
from ..models import EntityType, Requirement


class SkillExportable:
    type = EntityType.skill

    def __init__(self, local_id: str, name: str, payload: dict):
        self.local_id = local_id
        self.name = name
        self._payload = payload

    @classmethod
    def load(cls, local_id: str) -> "SkillExportable | None":
        fpath = os.path.join(store.SKILLS_DIR, f"{local_id}.md")
        if not os.path.isfile(fpath):
            return None
        with open(fpath, encoding="utf-8") as f:
            content = f.read()
        meta = store._load_index().get(local_id, {})
        name = meta.get("name") or local_id.replace("-", " ").replace("_", " ").title()
        payload = {
            "slug": local_id,
            "name": name,
            "description": meta.get("description", ""),
            "command": meta.get("command", local_id),
            "content": content,
            "builtin": bool(meta.get("built_in", False)),
        }
        return cls(local_id, name, payload)

    def serialize(self, ctx: ExportContext) -> dict:
        return dict(self._payload)

    def files(self) -> dict[str, bytes]:
        return {}

    def dependencies(self) -> list[DepRef]:
        return []

    def requirements(self) -> list[Requirement]:
        return []

    @classmethod
    def conflict(cls, payload: dict) -> str | None:
        slug = payload.get("slug") or ""
        if slug and _slug_taken(slug):
            return "already exists; will be added as a copy"
        return None

    @classmethod
    def import_(cls, payload: dict, files: dict[str, bytes], remap: RemapTable) -> str:
        base = (payload.get("slug") or payload.get("name") or "skill").lower().replace(" ", "-")
        slug = _free_slug(base)
        os.makedirs(store.SKILLS_DIR, exist_ok=True)
        fpath = os.path.join(store.SKILLS_DIR, f"{slug}.md")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(payload.get("content", ""))
        index = store._load_index()
        # Imported skills are never builtin, even if the source tagged them so.
        index[slug] = {
            "name": payload.get("name", slug),
            "description": payload.get("description", ""),
            "command": payload.get("command", slug),
        }
        store._save_index(index)
        return slug


    @classmethod
    def rollback(cls, local_id: str) -> None:
        fpath = os.path.join(store.SKILLS_DIR, f"{local_id}.md")
        if os.path.exists(fpath):
            os.remove(fpath)
        index = store._load_index()
        if local_id in index:
            index.pop(local_id, None)
            store._save_index(index)


def _slug_taken(slug: str) -> bool:
    return slug in store._load_index() or os.path.isfile(
        os.path.join(store.SKILLS_DIR, f"{slug}.md")
    )


def _free_slug(base: str) -> str:
    base = base or "skill"
    if not _slug_taken(base):
        return base
    cand = f"{base}-imported"
    if not _slug_taken(cand):
        return cand
    i = 2
    while _slug_taken(f"{base}-imported-{i}"):
        i += 1
    return f"{base}-imported-{i}"
