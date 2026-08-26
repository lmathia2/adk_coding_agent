"""ADK lifecycle plugin that writes memory only after verified completion."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from google.adk.plugins.base_plugin import BasePlugin

from harness.models.verification import VerificationReport
from harness.repo import build_repository_manifest
from harness.state import EventKind, JsonlEventStore, rebuild_ledger

from .identity import project_id_for
from .project import ProjectMemoryStore, extract_verified_memories


def _context_value(context: Any, name: str, default: Any = None) -> Any:
    value = getattr(context, name, None)
    if value is not None:
        return value
    state = getattr(context, "state", None)
    if isinstance(state, Mapping):
        return state.get(name, default)
    return default


class VerifiedProjectMemoryPlugin(BasePlugin):
    """Project verified task facts into a small cross-session memory store."""

    def __init__(
        self,
        *,
        workspace: Path,
        state_root: Path,
        project_root: Path | None = None,
        default_task_id: str | None = None,
    ) -> None:
        super().__init__(name="verified_project_memory")
        self.workspace = workspace.resolve()
        self.state_root = state_root.resolve()
        self.project_id = project_id_for(project_root or workspace)
        self.default_task_id = default_task_id
        self.events = JsonlEventStore(self.state_root / "events")
        self.memories = ProjectMemoryStore(self.state_root / "project-memory.db")

    def _task_id(self, context: Any) -> str | None:
        value = _context_value(context, "task_id")
        if value:
            return str(value)
        return self.default_task_id or os.getenv("ADK_CODING_TASK_ID")

    async def after_run_callback(self, *, invocation_context: Any) -> None:
        task_id = self._task_id(invocation_context)
        if not task_id:
            return None
        events = self.events.read(task_id)
        if not events or not any(
            event.kind == EventKind.TASK_FINISHED for event in events
        ):
            return None
        verification_event = next(
            (
                event
                for event in reversed(events)
                if event.kind == EventKind.VERIFICATION_COMPLETED
            ),
            None,
        )
        if verification_event is None:
            return None
        report = VerificationReport.model_validate(
            verification_event.payload.get("report", {})
        )
        if not report.passed:
            return None
        ledger = rebuild_ledger(events)
        manifest = build_repository_manifest(self.workspace)
        source_ids = [
            event.event_id
            for event in events
            if event.kind
            in {
                EventKind.TASK_CREATED,
                EventKind.LEDGER_PATCHED,
                EventKind.VERIFICATION_COMPLETED,
                EventKind.TASK_FINISHED,
            }
        ]
        for memory in extract_verified_memories(
            project_id=self.project_id,
            manifest=manifest,
            ledger=ledger,
            verification=report,
            source_event_ids=source_ids,
        ):
            self.memories.upsert(memory)
        return None


__all__ = ["VerifiedProjectMemoryPlugin"]
