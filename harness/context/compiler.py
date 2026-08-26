"""Deterministic, token-bounded context compilation."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence

from harness.models import (
    CompactionSnapshot,
    ContextBudget,
    ContextPacket,
    ContextSection,
    HarnessEvent,
    RepositoryManifest,
    RepositoryMap,
    SteeringMessage,
    TaskLedger,
)

from .prompt import build_static_prefix, prefix_hash

CHARS_PER_TOKEN_ESTIMATE = 4


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + CHARS_PER_TOKEN_ESTIMATE - 1) // CHARS_PER_TOKEN_ESTIMATE)


def truncate_to_tokens(text: str, token_limit: int) -> tuple[str, bool]:
    """Deterministically retain the beginning and end of oversized context."""

    if token_limit <= 0:
        return "", bool(text)
    character_limit = token_limit * CHARS_PER_TOKEN_ESTIMATE
    if len(text) <= character_limit:
        return text, False

    marker = "\n... [section truncated by context compiler] ...\n"
    available = max(0, character_limit - len(marker))
    head = available * 2 // 3
    tail = available - head
    return f"{text[:head]}{marker}{text[-tail:] if tail else ''}", True


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False)


def _render_manifest(manifest: RepositoryManifest | None) -> str:
    if manifest is None:
        return "(repository manifest unavailable)"
    payload = manifest.model_dump(mode="json")
    return _canonical_json(payload)


def _render_instructions(instructions: Mapping[str, str] | None) -> str:
    if not instructions:
        return "(no project instruction files loaded)"
    blocks: list[str] = []
    for path in sorted(instructions):
        blocks.append(f'<project-instructions path="{path}">\n{instructions[path].strip()}\n</project-instructions>')
    return "\n\n".join(blocks)


def _render_recent_events(events: Sequence[HarnessEvent | Mapping[str, object] | str]) -> str:
    rendered: list[str] = []
    for event in events:
        if isinstance(event, str):
            rendered.append(event.strip())
        elif isinstance(event, HarnessEvent):
            rendered.append(event.canonical_json())
        else:
            rendered.append(json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    return "\n".join(item for item in rendered if item)


def _render_steering(messages: Iterable[SteeringMessage | str]) -> str:
    rendered: list[str] = []
    for message in messages:
        rendered.append(message.content.strip() if isinstance(message, SteeringMessage) else message.strip())
    return "\n".join(item for item in rendered if item)


class ContextCompiler:
    """Compile stable task state into a deterministic model work packet."""

    def __init__(
        self,
        *,
        model_name: str,
        budget: ContextBudget | None = None,
        static_instruction: str | None = None,
        tool_names: Iterable[str] = ("read", "bash", "edit", "write"),
    ) -> None:
        self.budget = budget or ContextBudget()
        kwargs: dict[str, object] = {"model_name": model_name, "tool_names": tool_names}
        if static_instruction is not None:
            kwargs["instruction"] = static_instruction
        self.static_prefix = build_static_prefix(**kwargs)  # type: ignore[arg-type]
        self.static_prefix_hash = prefix_hash(self.static_prefix)

    def _section(self, name: str, content: str, budget: int) -> ContextSection:
        bounded, truncated = truncate_to_tokens(content.strip(), budget)
        return ContextSection(
            name=name,
            content=bounded,
            estimated_tokens=estimate_tokens(bounded),
            truncated=truncated,
        )

    def compile(
        self,
        *,
        ledger: TaskLedger,
        manifest: RepositoryManifest | None = None,
        project_instructions: Mapping[str, str] | None = None,
        repository_map: RepositoryMap | str | None = None,
        compaction: CompactionSnapshot | str | None = None,
        recent_events: Sequence[HarnessEvent | Mapping[str, object] | str] = (),
        steering_messages: Sequence[SteeringMessage | str] = (),
    ) -> ContextPacket:
        raw_repo_map = (
            repository_map.rendered
            if isinstance(repository_map, RepositoryMap)
            else repository_map or "(repository map not generated)"
        )
        raw_compaction = (
            compaction.summary_markdown
            if isinstance(compaction, CompactionSnapshot)
            else compaction or "(no prior compaction)"
        )
        raw_events = _render_recent_events(recent_events) or "(no recent events)"
        raw_steering = _render_steering(steering_messages) or "(no pending steering messages)"

        raw_sections = [
            (
                "project_instructions",
                _render_instructions(project_instructions),
                self.budget.project_instructions,
            ),
            ("task_ledger", _canonical_json(ledger.compact_projection()), self.budget.ledger),
            ("repository_manifest", _render_manifest(manifest), max(300, self.budget.repository_map // 3)),
            ("repository_map", raw_repo_map, self.budget.repository_map),
            ("compaction_summary", raw_compaction, self.budget.compaction_summary),
            ("recent_events", raw_events, self.budget.recent_events),
            ("steering", raw_steering, min(1_000, self.budget.recent_events)),
        ]
        raw_dynamic_tokens = sum(estimate_tokens(content) for _, content, _ in raw_sections)
        sections = [self._section(name, content, limit) for name, content, limit in raw_sections]

        rendered_sections = [
            f'<context-section name="{section.name}">\n{section.content}\n</context-section>'
            for section in sections
        ]
        packet_text = (
            "The following work packet is volatile task context. The system instruction and tool "
            "surface remain stable.\n\n" + "\n\n".join(rendered_sections)
        )
        static_tokens = estimate_tokens(self.static_prefix)
        dynamic_tokens = estimate_tokens(packet_text)
        total_tokens = static_tokens + dynamic_tokens
        should_compact = (
            raw_dynamic_tokens + static_tokens > self.budget.usable_input_tokens
            or any(section.name == "recent_events" and section.truncated for section in sections)
        )

        return ContextPacket(
            static_prefix_hash=self.static_prefix_hash,
            static_prefix_tokens=static_tokens,
            dynamic_suffix_tokens=dynamic_tokens,
            total_estimated_tokens=total_tokens,
            sections=sections,
            text=packet_text,
            should_compact=should_compact,
        )
