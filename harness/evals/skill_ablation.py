"""Deterministic paired-ablation plans for model-facing Agent Skills."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from harness.context.prompt import DEFAULT_TOOL_NAMES
from harness.models.base import StrictModel

SkillAblationMetric = Literal[
    "pass_rate",
    "cost_per_passed_task",
    "uncached_input_tokens",
    "cache_read_ratio",
    "prefix_versions",
    "tool_calls",
    "wall_time_ms",
]

REQUIRED_SKILL_ABLATION_METRICS: tuple[SkillAblationMetric, ...] = (
    "pass_rate",
    "cost_per_passed_task",
    "uncached_input_tokens",
    "cache_read_ratio",
    "prefix_versions",
    "tool_calls",
    "wall_time_ms",
)


class SkillAblationVariant(StrictModel):
    """One side of a skill ablation with an explicit model interface."""

    variant: Literal["baseline", "routing-skill"]
    selected_skills: tuple[str, ...] = ()
    model_visible_tools: tuple[str, ...] = DEFAULT_TOOL_NAMES


class SkillAblationPlan(StrictModel):
    """A paired plan that changes only programmatic-routing disclosure."""

    ablation_id: str
    description: str
    suite_path: str
    case_ids: tuple[str, ...] = Field(min_length=1)
    required_metrics: tuple[SkillAblationMetric, ...] = REQUIRED_SKILL_ABLATION_METRICS
    baseline: SkillAblationVariant
    candidate: SkillAblationVariant

    @model_validator(mode="after")
    def validate_pair(self) -> SkillAblationPlan:
        expected_tools = tuple(DEFAULT_TOOL_NAMES)
        if self.baseline.variant != "baseline":
            raise ValueError("baseline side must use variant='baseline'")
        if self.candidate.variant != "routing-skill":
            raise ValueError("candidate side must use variant='routing-skill'")
        if self.baseline.selected_skills:
            raise ValueError("baseline side must not force a skill")
        if self.candidate.selected_skills != ("programmatic-tool-routing",):
            raise ValueError("candidate side must select only programmatic-tool-routing")
        if self.baseline.model_visible_tools != expected_tools:
            raise ValueError("baseline must preserve the four-tool surface")
        if self.candidate.model_visible_tools != expected_tools:
            raise ValueError("candidate must preserve the four-tool surface")
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("ablation case_ids must be unique")
        if self.required_metrics != REQUIRED_SKILL_ABLATION_METRICS:
            raise ValueError("skill ablation must retain every required system metric")
        return self


def load_skill_ablation_plan(path: Path) -> SkillAblationPlan:
    """Load and validate a committed paired-ablation definition."""

    return SkillAblationPlan.model_validate_json(path.read_text(encoding="utf-8"))
