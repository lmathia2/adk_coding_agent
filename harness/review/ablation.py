"""Paired metrics for the optional final-diff reviewer ablation."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import Field

from harness.models.base import StrictModel


class ReviewAblationSample(StrictModel):
    """One case result from either side of a controlled reviewer ablation."""

    variant: Literal["baseline", "reviewer"]
    case_id: str
    harness_revision: str
    model: str
    reasoning: str
    passed: bool
    cost_usd: float = Field(ge=0)
    uncached_input_tokens: int = Field(ge=0)
    cache_read_ratio: float = Field(ge=0, le=1)
    prefix_versions: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    wall_time_ms: int = Field(ge=0)


class ReviewVariantSummary(StrictModel):
    variant: Literal["baseline", "reviewer"]
    cases: int = Field(ge=1)
    passed: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    cost_per_passed_task: float | None = Field(default=None, ge=0)
    uncached_input_tokens: int = Field(ge=0)
    cache_read_ratio: float = Field(ge=0, le=1)
    prefix_versions: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    wall_time_ms: int = Field(ge=0)


class ReviewAblationReport(StrictModel):
    case_ids: list[str]
    baseline: ReviewVariantSummary
    reviewer: ReviewVariantSummary
    pass_rate_delta: float
    cost_per_passed_task_delta: float | None = None
    uncached_input_tokens_delta: int
    cache_read_ratio_delta: float
    prefix_versions_delta: int
    tool_calls_delta: int
    wall_time_ms_delta: int


def _summarize(
    variant: Literal["baseline", "reviewer"],
    samples: list[ReviewAblationSample],
) -> ReviewVariantSummary:
    passed = sum(sample.passed for sample in samples)
    total_cost = sum(sample.cost_usd for sample in samples)
    total_input = sum(sample.uncached_input_tokens for sample in samples)
    return ReviewVariantSummary(
        variant=variant,
        cases=len(samples),
        passed=passed,
        pass_rate=passed / len(samples),
        cost_per_passed_task=(total_cost / passed if passed else None),
        uncached_input_tokens=total_input,
        cache_read_ratio=(
            sum(sample.cache_read_ratio for sample in samples) / len(samples)
        ),
        prefix_versions=sum(sample.prefix_versions for sample in samples),
        tool_calls=sum(sample.tool_calls for sample in samples),
        wall_time_ms=sum(sample.wall_time_ms for sample in samples),
    )


def compare_reviewer_ablation(
    samples: Iterable[ReviewAblationSample],
) -> ReviewAblationReport:
    """Compare paired variants while rejecting confounded configurations."""

    grouped: dict[str, dict[str, ReviewAblationSample]] = {}
    for sample in samples:
        variants = grouped.setdefault(sample.case_id, {})
        if sample.variant in variants:
            raise ValueError(
                f"duplicate {sample.variant} sample for case {sample.case_id}"
            )
        variants[sample.variant] = sample
    if not grouped:
        raise ValueError("reviewer ablation requires at least one paired case")

    baseline: list[ReviewAblationSample] = []
    reviewer: list[ReviewAblationSample] = []
    for case_id in sorted(grouped):
        pair = grouped[case_id]
        if set(pair) != {"baseline", "reviewer"}:
            raise ValueError(f"case {case_id} does not have both ablation variants")
        left = pair["baseline"]
        right = pair["reviewer"]
        left_config = (left.harness_revision, left.model, left.reasoning)
        right_config = (right.harness_revision, right.model, right.reasoning)
        if left_config != right_config:
            raise ValueError(f"case {case_id} has confounded ablation settings")
        baseline.append(left)
        reviewer.append(right)

    left_summary = _summarize("baseline", baseline)
    right_summary = _summarize("reviewer", reviewer)
    cost_delta = None
    if (
        left_summary.cost_per_passed_task is not None
        and right_summary.cost_per_passed_task is not None
    ):
        cost_delta = (
            right_summary.cost_per_passed_task
            - left_summary.cost_per_passed_task
        )
    return ReviewAblationReport(
        case_ids=sorted(grouped),
        baseline=left_summary,
        reviewer=right_summary,
        pass_rate_delta=right_summary.pass_rate - left_summary.pass_rate,
        cost_per_passed_task_delta=cost_delta,
        uncached_input_tokens_delta=(
            right_summary.uncached_input_tokens - left_summary.uncached_input_tokens
        ),
        cache_read_ratio_delta=(
            right_summary.cache_read_ratio - left_summary.cache_read_ratio
        ),
        prefix_versions_delta=(
            right_summary.prefix_versions - left_summary.prefix_versions
        ),
        tool_calls_delta=right_summary.tool_calls - left_summary.tool_calls,
        wall_time_ms_delta=right_summary.wall_time_ms - left_summary.wall_time_ms,
    )

