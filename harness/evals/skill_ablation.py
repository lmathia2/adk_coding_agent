"""Reproducible paired-ablation contracts for model-facing Agent Skills."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
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
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_HARNESS_PIN_PATHS = (
    "app/agent/skills.py",
    "app/agent/workflow.py",
    "harness/context/prompt.py",
    "harness/policy/commands.py",
    "harness/tools/coding.py",
)


class SkillAblationSettings(StrictModel):
    max_iterations: int = Field(ge=1)
    skill_context_bytes: int = Field(ge=1)
    skill_max_selected: int = Field(ge=1)
    tool_timeout_seconds: int = Field(ge=1)
    network_mode: Literal["deny"] = "deny"
    trace_mode: Literal["metadata", "redacted"] = "metadata"


class SkillAblationExecutionPin(StrictModel):
    skill_name: Literal["programmatic-tool-routing"]
    skill_content_hash: str = Field(pattern=_SHA256_PATTERN)
    model: str = Field(min_length=1)
    reasoning: str = Field(min_length=1)
    harness_content_hash: str = Field(pattern=_SHA256_PATTERN)
    static_prefix_hash: str = Field(pattern=_SHA256_PATTERN)
    settings: SkillAblationSettings


class SkillAblationVariant(StrictModel):
    variant: Literal["baseline", "routing-skill"]
    selected_skills: tuple[str, ...] = ()
    model_visible_tools: tuple[str, ...] = DEFAULT_TOOL_NAMES


class SkillAblationPlan(StrictModel):
    ablation_id: str
    description: str
    suite_path: str
    positive_case_ids: tuple[str, ...] = Field(min_length=1)
    negative_case_ids: tuple[str, ...] = Field(min_length=1)
    required_metrics: tuple[SkillAblationMetric, ...] = REQUIRED_SKILL_ABLATION_METRICS
    execution: SkillAblationExecutionPin
    baseline: SkillAblationVariant
    candidate: SkillAblationVariant

    @property
    def case_ids(self) -> tuple[str, ...]:
        return self.positive_case_ids + self.negative_case_ids

    @model_validator(mode="after")
    def validate_pair(self) -> SkillAblationPlan:
        expected_tools = tuple(DEFAULT_TOOL_NAMES)
        if self.baseline.variant != "baseline" or self.baseline.selected_skills:
            raise ValueError("baseline must not force a skill")
        if self.candidate.variant != "routing-skill":
            raise ValueError("candidate side must use variant='routing-skill'")
        if self.candidate.selected_skills != (self.execution.skill_name,):
            raise ValueError("candidate must select only the pinned routing skill")
        if self.baseline.model_visible_tools != expected_tools:
            raise ValueError("baseline must preserve the four-tool surface")
        if self.candidate.model_visible_tools != expected_tools:
            raise ValueError("candidate must preserve the four-tool surface")
        if len(set(self.case_ids)) != len(self.case_ids):
            raise ValueError("positive and negative ablation cases must be unique")
        if self.required_metrics != REQUIRED_SKILL_ABLATION_METRICS:
            raise ValueError("skill ablation must retain every required system metric")
        return self


class SkillAblationSample(StrictModel):
    ablation_id: str
    case_id: str
    variant: Literal["baseline", "routing-skill"]
    execution: SkillAblationExecutionPin
    actual_selected_skills: tuple[str, ...]
    actual_selected_skill_hashes: tuple[str, ...]
    model_visible_tools: tuple[str, ...]
    passed: bool
    cost_usd: float = Field(ge=0)
    uncached_input_tokens: int = Field(ge=0)
    cache_read_ratio: float = Field(ge=0, le=1)
    prefix_versions: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    wall_time_ms: int = Field(ge=0)


class SkillAblationSummary(StrictModel):
    variant: Literal["baseline", "routing-skill"]
    cases: int = Field(ge=1)
    passed: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    cost_per_passed_task: float | None = Field(default=None, ge=0)
    uncached_input_tokens: int = Field(ge=0)
    cache_read_ratio: float = Field(ge=0, le=1)
    prefix_versions: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    wall_time_ms: int = Field(ge=0)


class SkillAblationReport(StrictModel):
    ablation_id: str
    positive_case_ids: tuple[str, ...]
    negative_case_ids: tuple[str, ...]
    baseline: SkillAblationSummary
    candidate: SkillAblationSummary
    pass_rate_delta: float
    cost_per_passed_task_delta: float | None = None
    uncached_input_tokens_delta: int
    cache_read_ratio_delta: float
    prefix_versions_delta: int
    tool_calls_delta: int
    wall_time_ms_delta: int


def ablation_harness_content_hash(repository_root: Path) -> str:
    """Hash the execution paths whose behavior must remain fixed across a pair."""

    digest = hashlib.sha256()
    for relative in _HARNESS_PIN_PATHS:
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update((repository_root / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def load_skill_ablation_plan(path: Path) -> SkillAblationPlan:
    return SkillAblationPlan.model_validate_json(path.read_text(encoding="utf-8"))


def _summarize(
    variant: Literal["baseline", "routing-skill"],
    samples: list[SkillAblationSample],
) -> SkillAblationSummary:
    passed = sum(sample.passed for sample in samples)
    total_cost = sum(sample.cost_usd for sample in samples)
    return SkillAblationSummary(
        variant=variant,
        cases=len(samples),
        passed=passed,
        pass_rate=passed / len(samples),
        cost_per_passed_task=(total_cost / passed if passed else None),
        uncached_input_tokens=sum(sample.uncached_input_tokens for sample in samples),
        cache_read_ratio=sum(sample.cache_read_ratio for sample in samples) / len(samples),
        prefix_versions=sum(sample.prefix_versions for sample in samples),
        tool_calls=sum(sample.tool_calls for sample in samples),
        wall_time_ms=sum(sample.wall_time_ms for sample in samples),
    )


def compare_skill_ablation(
    plan: SkillAblationPlan,
    samples: Iterable[SkillAblationSample],
) -> SkillAblationReport:
    """Compare exact pairs and reject configuration or disclosure confounds."""

    grouped: dict[str, dict[str, SkillAblationSample]] = {}
    for sample in samples:
        if sample.ablation_id != plan.ablation_id or sample.case_id not in plan.case_ids:
            raise ValueError("sample is not part of the pinned ablation plan")
        if sample.execution != plan.execution:
            raise ValueError(f"case {sample.case_id} has confounded execution pins")
        variants = grouped.setdefault(sample.case_id, {})
        if sample.variant in variants:
            raise ValueError(f"duplicate {sample.variant} sample for case {sample.case_id}")
        variants[sample.variant] = sample
    if set(grouped) != set(plan.case_ids):
        raise ValueError("samples must cover every planned positive and negative case")

    baseline: list[SkillAblationSample] = []
    candidate: list[SkillAblationSample] = []
    for case_id in sorted(grouped):
        pair = grouped[case_id]
        if set(pair) != {"baseline", "routing-skill"}:
            raise ValueError(f"case {case_id} does not have both ablation variants")
        left, right = pair["baseline"], pair["routing-skill"]
        if left.model_visible_tools != tuple(DEFAULT_TOOL_NAMES):
            raise ValueError(f"case {case_id} baseline changed the tool surface")
        if right.model_visible_tools != tuple(DEFAULT_TOOL_NAMES):
            raise ValueError(f"case {case_id} candidate changed the tool surface")
        if left.actual_selected_skills or left.actual_selected_skill_hashes:
            raise ValueError(f"case {case_id} baseline disclosed a skill body")
        if right.actual_selected_skills != (plan.execution.skill_name,):
            raise ValueError(f"case {case_id} candidate did not select the routing skill")
        if right.actual_selected_skill_hashes != (plan.execution.skill_content_hash,):
            raise ValueError(f"case {case_id} candidate selected an unpinned skill revision")
        baseline.append(left)
        candidate.append(right)

    left_summary = _summarize("baseline", baseline)
    right_summary = _summarize("routing-skill", candidate)
    cost_delta = None
    if left_summary.cost_per_passed_task is not None and right_summary.cost_per_passed_task is not None:
        cost_delta = right_summary.cost_per_passed_task - left_summary.cost_per_passed_task
    return SkillAblationReport(
        ablation_id=plan.ablation_id,
        positive_case_ids=plan.positive_case_ids,
        negative_case_ids=plan.negative_case_ids,
        baseline=left_summary,
        candidate=right_summary,
        pass_rate_delta=right_summary.pass_rate - left_summary.pass_rate,
        cost_per_passed_task_delta=cost_delta,
        uncached_input_tokens_delta=right_summary.uncached_input_tokens - left_summary.uncached_input_tokens,
        cache_read_ratio_delta=right_summary.cache_read_ratio - left_summary.cache_read_ratio,
        prefix_versions_delta=right_summary.prefix_versions - left_summary.prefix_versions,
        tool_calls_delta=right_summary.tool_calls - left_summary.tool_calls,
        wall_time_ms_delta=right_summary.wall_time_ms - left_summary.wall_time_ms,
    )
