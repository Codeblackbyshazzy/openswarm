"""SessionExportable: an agent card on a shared dashboard. We carry only the
recipe (name, model, mode, system prompt, allowed tools) and deliberately DROP
the chat transcript (privacy + size), runtime state, costs, the worktree path,
and active_mcps (importing must never silently grant tool access, per the gate).
Its MCP/actions, provider, and built-in mode become import requirements so the
importer is walked through enabling them. The dashboard re-points dashboard_id
after import."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from ..exportable import DepRef, ExportContext, RemapTable
from ..models import EntityType, Requirement, RequirementKind

_BUILTIN_MODES = {"agent", "ask", "plan", "view-builder", "skill-builder"}
_KEEP = ("name", "provider", "model", "mode", "system_prompt", "allowed_tools", "max_turns", "thinking_level")


class SessionExportable:
    type = EntityType.session

    def __init__(self, sid: str, name: str, data: dict):
        self.local_id = sid
        self.name = name
        self._data = data

    @classmethod
    def load(cls, local_id: str) -> "SessionExportable | None":
        from backend.apps.agents.manager.session.session_store import _load_session_data
        d = _load_session_data(local_id)
        if d is None:
            return None
        return cls(local_id, d.get("name") or "Agent", d)

    def serialize(self, ctx: ExportContext) -> dict:
        return {k: self._data.get(k) for k in _KEEP if k in self._data}

    def files(self) -> dict[str, bytes]:
        return {}

    def dependencies(self) -> list[DepRef]:
        mode = self._data.get("mode")
        if mode and mode not in _BUILTIN_MODES:
            return [DepRef(EntityType.mode, mode, "uses_mode")]
        return []

    def requirements(self) -> list[Requirement]:
        reqs: list[Requirement] = []
        for mcp in self._data.get("active_mcps") or []:
            reqs.append(Requirement(
                kind=RequirementKind.mcp_action, key=mcp, label=mcp,
                detail="An agent here uses this action.",
            ))
        mode = self._data.get("mode") or "agent"
        if mode in _BUILTIN_MODES and mode != "agent":
            reqs.append(Requirement(
                kind=RequirementKind.builtin_mode, key=mode, label=f"{mode} mode",
                detail="A built-in mode an agent runs in.",
            ))
        provider = self._data.get("provider") or "anthropic"
        reqs.append(Requirement(
            kind=RequirementKind.api_key, key=provider, label=f"A {provider} model",
            detail="Set up this provider so the agents can run.",
        ))
        return reqs

    @classmethod
    def import_(cls, payload: dict, files: dict[str, bytes], remap: RemapTable) -> str:
        from backend.apps.agents.manager.session.session_store import _save_session
        sid = uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        doc = {
            "id": sid,
            "name": payload.get("name") or "Agent",
            "status": "completed",
            "provider": payload.get("provider") or "anthropic",
            "model": payload.get("model") or "sonnet",
            "mode": payload.get("mode") or "agent",
            "system_prompt": payload.get("system_prompt"),
            "allowed_tools": payload.get("allowed_tools") or [],
            "max_turns": payload.get("max_turns"),
            "thinking_level": payload.get("thinking_level") or "auto",
            "messages": [],
            "branches": {"main": {"id": "main", "parent_branch_id": None, "fork_point_message_id": None, "created_at": now}},
            "active_branch_id": "main",
            "active_mcps": [],
            "dashboard_id": None,  # the dashboard import re-points this
            "browser_id": None,
            "parent_session_id": None,
            "created_at": now,
            "closed_at": now,
        }
        _save_session(sid, doc)
        return sid

    @classmethod
    def rollback(cls, local_id: str) -> None:
        from backend.apps.agents.manager.session.session_store import _delete_session_file
        _delete_session_file(local_id)
