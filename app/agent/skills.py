"""Runtime skill discovery, progressive disclosure, and candidate assignment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness.learning import (
    LearningStore,
    PromotionPolicy,
    TraceSkillLearningController,
)
from harness.learning import (
    SkillRegistry as LearnedSkillRegistry,
)
from harness.skills import SkillRegistry, SkillRoot, learned_skill_roots

from .learning import workflow_kind_for

if TYPE_CHECKING:
    from .config import HarnessSettings


@dataclass(frozen=True, slots=True)
class SkillRuntimeContext:
    text: str = ""
    selected_names: tuple[str, ...] = ()
    selected_hashes: tuple[str, ...] = ()
    candidate_name: str | None = None
    experiment_id: str | None = None
    variant: str | None = None


def build_learning_controller(settings: HarnessSettings) -> TraceSkillLearningController:
    return TraceSkillLearningController(
        store=LearningStore(settings.state_root / "learning.db"),
        registry=LearnedSkillRegistry(settings.learned_skill_root),
        policy=PromotionPolicy(minimum_support=settings.learning_min_support),
    )


def build_skill_registry(settings: HarnessSettings) -> SkillRegistry:
    roots = [
        SkillRoot(
            path=path,
            origin=("project" if index == 0 else f"configured:{index}"),
            precedence=index * 10,
        )
        for index, path in enumerate(settings.skill_roots)
    ]
    roots.extend(
        learned_skill_roots(
            settings.learned_skill_root,
            precedence=1_000,
        )
    )
    return SkillRegistry(roots, allow_missing_roots=True)


def _matching_candidate(registry: SkillRegistry, workflow_kind: str) -> str | None:
    needle = workflow_kind.replace("_", "-")
    matching = [
        skill.name
        for skill in registry.skills
        if skill.lifecycle == "candidate"
        and (
            needle in skill.name
            or workflow_kind in skill.description.lower().replace("_", "-")
        )
    ]
    return min(matching) if matching else None


def _bound_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode()
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _join_sections(parts: list[str]) -> str:
    return "\n\n".join(part for part in parts if part)


def _remaining_section_bytes(parts: list[str], heading: str, budget: int) -> int:
    separator = "\n\n" if parts else ""
    return max(
        budget
        - len(_join_sections(parts).encode())
        - len(separator.encode())
        - len(heading.encode()),
        0,
    )


def build_skill_context(
    *,
    task_id: str,
    goal: str,
    next_action: str,
    workflow_kind: str | None = None,
    settings: HarnessSettings,
    controller: TraceSkillLearningController | None = None,
) -> SkillRuntimeContext:
    """Build bounded dynamic skill context; never alter the stable prefix."""

    if settings.skill_context_bytes <= 0:
        return SkillRuntimeContext()
    active_controller = controller
    if active_controller is None:
        active_controller = build_learning_controller(settings)
    registry = build_skill_registry(settings)
    budget = settings.skill_context_bytes
    catalog_heading = "Available skill catalog:\n"
    selected_heading = "Selected skill instructions:\n"
    parts: list[str] = []
    catalog_payload_budget = min(
        4_096,
        _remaining_section_bytes(parts, catalog_heading, budget),
    )
    catalog = None
    if catalog_payload_budget > 0:
        catalog = registry.build_catalog(
            max_bytes=catalog_payload_budget,
            max_tokens=max(1, catalog_payload_budget // 4),
        )
        if catalog.text:
            parts.append(catalog_heading + catalog.text.rstrip())

    selection_text = ""
    selected_names: list[str] = []
    selected_hashes: list[str] = []
    unmatched: tuple[str, ...] = ()
    selection_budget = _remaining_section_bytes(parts, selected_heading, budget)
    if selection_budget > 0 and settings.skill_max_selected > 0:
        selection = registry.select(
            goal=goal,
            next_action=next_action,
            top_n=settings.skill_max_selected,
            max_bytes=selection_budget,
            max_tokens=max(1, selection_budget // 4),
        )
        selection_text = selection.text
        selected_names.extend(skill.name for skill in selection.skills)
        selected_hashes.extend(skill.content_hash for skill in selection.skills)
        unmatched = selection.unmatched_explicit_names

    candidate_name: str | None = None
    experiment_id: str | None = None
    variant: str | None = None
    if (
        settings.learning_enabled
        and settings.trace_mode != "off"
        and _remaining_section_bytes(parts, selected_heading, budget)
        - len(selection_text.encode())
        - (1 if selection_text else 0)
        >= 512
    ):
        candidate_name = _matching_candidate(
            registry,
            workflow_kind or workflow_kind_for(goal),
        )
        if candidate_name:
            lifecycle = active_controller.registry.load(candidate_name)
            if lifecycle is not None and lifecycle.status == "candidate":
                candidate_budget = (
                    _remaining_section_bytes(parts, selected_heading, budget)
                    - len(selection_text.encode())
                    - (1 if selection_text else 0)
                )
                candidate = registry.select_candidate(
                    candidate_name,
                    goal=goal,
                    next_action=next_action,
                    max_bytes=candidate_budget,
                    max_tokens=max(1, candidate_budget // 4),
                )
                if candidate.truncated or not candidate.skills:
                    candidate_name = None
                    return SkillRuntimeContext(
                        text=_join_sections(
                            parts
                            + (
                                [selected_heading + selection_text.rstrip()]
                                if selection_text
                                else []
                            )
                        ),
                        selected_names=tuple(selected_names),
                        selected_hashes=tuple(selected_hashes),
                    )
                experiment_id = (
                    f"skill:{lifecycle.name}:v{lifecycle.version}"
                )
                try:
                    assignment = active_controller.assign(
                        experiment_id=experiment_id,
                        unit_id=task_id,
                        skill_name=lifecycle.name,
                        skill_version=lifecycle.version,
                        candidate_content_hash=candidate.skills[0].content_hash,
                        candidate_percent=settings.learning_trial_percent,
                    )
                except (KeyError, ValueError):
                    candidate_name = None
                    experiment_id = None
                    assignment = None
                variant = None if assignment is None else assignment.variant
                if assignment is not None and assignment.variant == "candidate":
                    if selection_text and candidate.text:
                        selection_text += "\n"
                    selection_text += candidate.text
                    selected_names.append(lifecycle.name)
                    selected_hashes.append(candidate.skills[0].content_hash)

    if selection_text:
        parts.append(selected_heading + selection_text.rstrip())
    if unmatched:
        unmatched_text = "Unmatched explicit skill requests: " + ", ".join(
            f"${name}" for name in unmatched
        )
        separator = "\n\n" if parts else ""
        remaining = budget - len(_join_sections(parts).encode()) - len(
            separator.encode()
        )
        bounded_unmatched = _bound_utf8(unmatched_text, max(remaining, 0))
        if bounded_unmatched:
            parts.append(bounded_unmatched)
    text = _join_sections(parts)
    if len(text.encode()) > budget:
        raise AssertionError("skill context exceeded its exact byte budget")
    return SkillRuntimeContext(
        text=text,
        selected_names=tuple(selected_names),
        selected_hashes=tuple(selected_hashes),
        candidate_name=candidate_name,
        experiment_id=experiment_id,
        variant=variant,
    )


__all__ = [
    "SkillRuntimeContext",
    "build_learning_controller",
    "build_skill_context",
    "build_skill_registry",
]
