"""Deterministic trial assignment, summaries, and promotion gates."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .models import QualitySummary, TrialAssignment, TrialOutcome
from .store import LearningStore


def assign_trial(
    store: LearningStore,
    *,
    experiment_id: str,
    unit_id: str,
    skill_name: str,
    skill_version: int,
    candidate_content_hash: str,
    candidate_percent: int = 50,
) -> TrialAssignment:
    if not 0 <= candidate_percent <= 100:
        raise ValueError("candidate_percent must be between 0 and 100")
    existing = store.assignment(experiment_id, unit_id)
    if existing is not None:
        requested_pin = (
            skill_name,
            skill_version,
            candidate_content_hash,
        )
        existing_pin = (
            existing.skill_name,
            existing.skill_version,
            existing.candidate_content_hash,
        )
        if requested_pin != existing_pin:
            raise ValueError("trial unit is already pinned to another candidate revision")
        return existing
    digest = hashlib.sha256(f"{experiment_id}\0{unit_id}".encode()).digest()
    bucket = int.from_bytes(digest[:8], "big") % 100
    assignment = TrialAssignment(
        experiment_id=experiment_id,
        unit_id=unit_id,
        skill_name=skill_name,
        skill_version=skill_version,
        candidate_content_hash=candidate_content_hash,
        variant="candidate" if bucket < candidate_percent else "baseline",
    )
    return store.save_assignment(assignment)


def summarize_quality(outcomes: list[TrialOutcome]) -> QualitySummary:
    support = len(outcomes)
    passes = sum(1 for outcome in outcomes if outcome.quality.passed)
    if not outcomes:
        return QualitySummary(
            support=0,
            passes=0,
            pass_rate=0,
            cost_per_passed_task=None,
            uncached_input_tokens=0,
            cache_read_ratio=0,
            tool_calls=0,
            wall_time_ms=0,
        )
    total_cost = sum(outcome.quality.cost_usd for outcome in outcomes)
    return QualitySummary(
        support=support,
        passes=passes,
        pass_rate=passes / support,
        cost_per_passed_task=(total_cost / passes if passes else None),
        uncached_input_tokens=(
            sum(outcome.quality.uncached_input_tokens for outcome in outcomes)
            / support
        ),
        cache_read_ratio=(
            sum(outcome.quality.cache_read_ratio for outcome in outcomes) / support
        ),
        tool_calls=sum(outcome.quality.tool_calls for outcome in outcomes) / support,
        wall_time_ms=sum(outcome.quality.wall_time_ms for outcome in outcomes) / support,
    )


@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    minimum_support: int = 3
    maximum_pass_rate_regression: float = 0.0
    maximum_cost_ratio: float = 1.0
    maximum_uncached_input_ratio: float = 1.0
    minimum_cache_ratio_delta: float = 0.0
    maximum_tool_call_ratio: float = 1.0
    maximum_wall_time_ratio: float = 1.0
    rollback_after_failures: int = 2

    def __post_init__(self) -> None:
        if self.minimum_support < 1:
            raise ValueError("minimum_support must be at least 1")
        if self.maximum_pass_rate_regression < 0:
            raise ValueError("maximum_pass_rate_regression must not be negative")
        if min(
            self.maximum_cost_ratio,
            self.maximum_uncached_input_ratio,
            self.maximum_tool_call_ratio,
            self.maximum_wall_time_ratio,
        ) < 0:
            raise ValueError("quality ratios must not be negative")
        if self.minimum_cache_ratio_delta < 0:
            raise ValueError("minimum_cache_ratio_delta must not be negative")
        if self.rollback_after_failures < 1:
            raise ValueError("rollback_after_failures must be at least 1")


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    promote: bool
    reasons: tuple[str, ...]
    baseline: QualitySummary
    candidate: QualitySummary


def _within_ratio(candidate: float, baseline: float, maximum_ratio: float) -> bool:
    if baseline == 0:
        return candidate == 0
    return candidate <= baseline * maximum_ratio


def evaluate_promotion(
    baseline: QualitySummary,
    candidate: QualitySummary,
    *,
    policy: PromotionPolicy | None = None,
) -> PromotionDecision:
    policy = policy or PromotionPolicy()
    reasons: list[str] = []
    if baseline.support < policy.minimum_support:
        reasons.append("baseline support is below the promotion minimum")
    if candidate.support < policy.minimum_support:
        reasons.append("candidate support is below the promotion minimum")
    if (
        candidate.pass_rate
        < baseline.pass_rate - policy.maximum_pass_rate_regression
    ):
        reasons.append("candidate pass rate regressed")
    if candidate.cost_per_passed_task is None:
        reasons.append("candidate has no passed task for cost measurement")
    elif baseline.cost_per_passed_task is None:
        reasons.append("baseline has no passed task for cost comparison")
    elif not _within_ratio(
        candidate.cost_per_passed_task,
        baseline.cost_per_passed_task,
        policy.maximum_cost_ratio,
    ):
        reasons.append("candidate cost per passed task regressed")
    if not _within_ratio(
        candidate.uncached_input_tokens,
        baseline.uncached_input_tokens,
        policy.maximum_uncached_input_ratio,
    ):
        reasons.append("candidate uncached input regressed")
    if (
        candidate.cache_read_ratio
        < baseline.cache_read_ratio - policy.minimum_cache_ratio_delta
    ):
        reasons.append("candidate cache-read ratio regressed")
    if not _within_ratio(
        candidate.tool_calls,
        baseline.tool_calls,
        policy.maximum_tool_call_ratio,
    ):
        reasons.append("candidate tool calls regressed")
    if not _within_ratio(
        candidate.wall_time_ms,
        baseline.wall_time_ms,
        policy.maximum_wall_time_ratio,
    ):
        reasons.append("candidate wall time regressed")
    return PromotionDecision(
        promote=not reasons,
        reasons=tuple(reasons),
        baseline=baseline,
        candidate=candidate,
    )


def trial_summaries(
    store: LearningStore,
    experiment_id: str,
) -> tuple[QualitySummary, QualitySummary]:
    outcomes = store.outcomes(experiment_id=experiment_id)
    baseline = summarize_quality(
        [outcome for outcome in outcomes if outcome.variant == "baseline"]
    )
    candidate = summarize_quality(
        [outcome for outcome in outcomes if outcome.variant == "candidate"]
    )
    return baseline, candidate


__all__ = [
    "PromotionDecision",
    "PromotionPolicy",
    "assign_trial",
    "evaluate_promotion",
    "summarize_quality",
    "trial_summaries",
]
