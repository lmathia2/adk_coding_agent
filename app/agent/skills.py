"""Runtime skill discovery, progressive disclosure, and candidate assignment."""

from __future__ import annotations

from dataclasses import dataclass

from harness.learning import (
    LearningStore,
    PromotionPolicy,
    TraceSkillLearningController,
)
from harness.learning import (
    SkillRegistry as LearnedSkillRegistry,
)
from harness.skills import SkillRegistry, SkillRoot, learned_skill_roots

from .config import SETTINGS, HarnessSettings
from .learning import workflow_kind_for

_LEARNING_STORE = LearningStore(SETTINGS.state_root / "learning.db")
_LEARNED_SKILLS = LearnedSkillRegistry(SETTINGS.learned_skill_root)
_LEARNING_CONTROLLER = TraceSkillLearningController(
    store=_LEARNING_STORE,
    registry=_LEARNED_SKILLS,
    policy=PromotionPolicy(minimum_support=SETTINGS.learning_min_support),
)


@dataclass(frozen=True, slots=True)
class SkillRuntimeContext:
    text: str = ""
    selected_names: tuple[str, ...] = ()
    selected_hashes: tuple[str, ...] = ()
    candidate_name: str | None = None
    experiment_id: str | None = None
    variant: str | None = None


def build_skill_registry(settings: HarnessSettings = SETTINGS) -> SkillRegistry:
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


def build_skill_context(
    *,
    task_id: str,
    goal: str,
    next_action: str,
    workflow_kind: str | None = None,
    settings: HarnessSettings = SETTINGS,
    controller: TraceSkillLearningController | None = None,
) -> SkillRuntimeContext:
    """Build bounded dynamic skill context; never alter the stable prefix."""

    if settings.skill_context_bytes <= 0:
        return SkillRuntimeContext()
    active_controller = controller
    if active_controller is None:
        if settings.learned_skill_root == SETTINGS.learned_skill_root:
            active_controller = _LEARNING_CONTROLLER
        else:
            active_controller = TraceSkillLearningController(
                store=LearningStore(settings.state_root / "learning.db"),
                registry=LearnedSkillRegistry(settings.learned_skill_root),
                policy=PromotionPolicy(
                    minimum_support=settings.learning_min_support
                ),
            )
    registry = build_skill_registry(settings)
    catalog_bytes = min(
        4_096,
        max(1, settings.skill_context_bytes // 4),
    )
    catalog = registry.build_catalog(
        max_bytes=catalog_bytes,
        max_tokens=max(1, catalog_bytes // 4),
    )
    remaining = max(settings.skill_context_bytes - catalog.byte_count, 0)
    selection_text = ""
    selected_names: list[str] = []
    selected_hashes: list[str] = []
    unmatched: tuple[str, ...] = ()
    if remaining > 0 and settings.skill_max_selected > 0:
        selection = registry.select(
            goal=goal,
            next_action=next_action,
            top_n=settings.skill_max_selected,
            max_bytes=remaining,
            max_tokens=max(1, remaining // 4),
        )
        selection_text = selection.text
        selected_names.extend(skill.name for skill in selection.skills)
        selected_hashes.extend(skill.content_hash for skill in selection.skills)
        unmatched = selection.unmatched_explicit_names
        remaining -= selection.byte_count

    candidate_name: str | None = None
    experiment_id: str | None = None
    variant: str | None = None
    if (
        settings.learning_enabled
        and settings.trace_mode != "off"
        and remaining >= 512
    ):
        candidate_name = _matching_candidate(
            registry,
            workflow_kind or workflow_kind_for(goal),
        )
        if candidate_name:
            lifecycle = active_controller.registry.load(candidate_name)
            if lifecycle is not None and lifecycle.status == "candidate":
                experiment_id = (
                    f"skill:{lifecycle.name}:v{lifecycle.version}"
                )
                assignment = active_controller.assign(
                    experiment_id=experiment_id,
                    unit_id=task_id,
                    candidate_percent=settings.learning_trial_percent,
                )
                variant = assignment.variant
                if assignment.variant == "candidate":
                    candidate = registry.select_candidate(
                        candidate_name,
                        goal=goal,
                        next_action=next_action,
                        max_bytes=remaining,
                        max_tokens=max(1, remaining // 4),
                    )
                    if candidate.truncated:
                        candidate_name = None
                        experiment_id = None
                        variant = None
                    else:
                        if selection_text and candidate.text:
                            selection_text += "\n"
                        selection_text += candidate.text
                        selected_names.append(candidate_name)
                        selected_hashes.append(candidate.skills[0].content_hash)

    parts: list[str] = []
    if catalog.text:
        parts.append("Available skill catalog:\n" + catalog.text.rstrip())
    if selection_text:
        parts.append("Selected skill instructions:\n" + selection_text.rstrip())
    if unmatched:
        parts.append(
            "Unmatched explicit skill requests: "
            + ", ".join(f"${name}" for name in unmatched)
        )
    return SkillRuntimeContext(
        text=_bound_utf8("\n\n".join(parts), settings.skill_context_bytes),
        selected_names=tuple(selected_names),
        selected_hashes=tuple(selected_hashes),
        candidate_name=candidate_name,
        experiment_id=experiment_id,
        variant=variant,
    )


__all__ = [
    "SkillRuntimeContext",
    "build_skill_context",
    "build_skill_registry",
]
