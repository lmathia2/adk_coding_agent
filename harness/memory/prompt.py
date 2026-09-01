"""Cache-aware prompt assembly from versioned ledger views."""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict

from harness.ledger.models import canonical_json

from .models import ViewRequest, ViewResult
from .runtime import MemoryProgramRuntime


class PromptComponent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tier: str
    content: str
    content_hash: str
    source_view_ids: tuple[str, ...] = ()


class PromptManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    context_epoch: str
    task_id: str
    watermark: int
    components: tuple[PromptComponent, ...]
    prompt_hash: str

    def render(self) -> str:
        return "\n\n".join(component.content for component in self.components)


def _component(tier: str, content: str, *views: ViewResult) -> PromptComponent:
    return PromptComponent(
        tier=tier,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        source_view_ids=tuple(view.view_id for view in views),
    )


def compile_prompt(
    runtime: MemoryProgramRuntime,
    *,
    task_id: str,
    static_prefix: str,
    query: str,
    recent_event_limit: int = 12,
    context_epoch_reason: str = "initial",
) -> PromptManifest:
    """Render stable-to-volatile P0/P1/P2/P3 components with source receipts."""

    history = runtime.compute(ViewRequest(task_id=task_id, program="history.model"))
    progress = runtime.compute(ViewRequest(task_id=task_id, program="task.progress"))
    memory = runtime.compute(
        ViewRequest(task_id=task_id, program="task.memory", query=query, max_bytes=8_000)
    )
    recent = history.data.get("events", [])[-recent_event_limit:]
    components = (
        _component("P0", static_prefix),
        _component("P1", canonical_json({"history_watermark": history.watermark}), history),
        _component(
            "P2",
            canonical_json({"progress": progress.data, "memory": memory.data}),
            progress,
            memory,
        ),
        _component("P3", canonical_json({"query": query, "recent": recent}), history),
    )
    epoch_source = canonical_json(
        {
            "p0": components[0].content_hash,
            "reason": context_epoch_reason,
            "history_floor": max(0, history.watermark - recent_event_limit),
        }
    )
    rendered = "\n\n".join(component.content for component in components)
    return PromptManifest(
        context_epoch=hashlib.sha256(epoch_source.encode()).hexdigest(),
        task_id=task_id,
        watermark=history.watermark,
        components=components,
        prompt_hash=hashlib.sha256(rendered.encode()).hexdigest(),
    )
