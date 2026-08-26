from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest

from harness.learning import (
    EpisodeQuality,
    LearningStore,
    PromotionPolicy,
    RepeatedActionSequence,
    SkillRegistry,
    TraceSkillLearningController,
    TrialAssignment,
    TrialOutcome,
    assign_trial,
    evaluate_promotion,
    summarize_quality,
)
from harness.learning.skills import HeuristicSkillSynthesizer
from harness.skills import SkillRegistry as DiscoveredSkillRegistry
from harness.skills import learned_skill_roots


def _quality(
    *,
    passed: bool = True,
    cost: float = 0.1,
    uncached: int = 1_000,
    cache_ratio: float = 0.8,
    tools: int = 4,
    wall: int = 2_000,
) -> EpisodeQuality:
    return EpisodeQuality(
        passed=passed,
        cost_usd=cost,
        uncached_input_tokens=uncached,
        cache_read_ratio=cache_ratio,
        tool_calls=tools,
        wall_time_ms=wall,
    )


def _outcome(
    experiment_id: str,
    unit_id: str,
    skill_name: str,
    variant: Literal["baseline", "candidate"],
    quality: EpisodeQuality,
) -> TrialOutcome:
    return TrialOutcome(
        experiment_id=experiment_id,
        unit_id=unit_id,
        skill_name=skill_name,
        variant=variant,
        quality=quality,
    )


def _candidate(registry: SkillRegistry) -> str:
    sequence = RepeatedActionSequence(
        tokens=("read:source:ok", "bash:test:ok"),
        support=3,
        source_trace_ids=("trace-1", "trace-2", "trace-3"),
    )
    draft = HeuristicSkillSynthesizer().synthesize(
        workflow_kind="python_bugfix",
        sequence=sequence,
    )
    return registry.emit_candidate(draft).name


def test_trial_assignment_is_deterministic_and_persisted(tmp_path: Path) -> None:
    store = LearningStore(tmp_path / "learning.db")
    first = assign_trial(
        store,
        experiment_id="experiment",
        unit_id="task-1",
        candidate_percent=50,
    )
    replay = assign_trial(
        store,
        experiment_id="experiment",
        unit_id="task-1",
        candidate_percent=0,
    )
    same_in_fresh_store = assign_trial(
        LearningStore(tmp_path / "other.db"),
        experiment_id="experiment",
        unit_id="task-1",
        candidate_percent=50,
    )

    assert replay == first
    assert same_in_fresh_store == first


def test_quality_summary_reports_required_metrics() -> None:
    outcomes = [
        _outcome("exp", "one", "skill", "baseline", _quality()),
        _outcome(
            "exp",
            "two",
            "skill",
            "baseline",
            _quality(passed=False, cost=0.2),
        ),
    ]

    summary = summarize_quality(outcomes)

    assert summary.support == 2
    assert summary.pass_rate == 0.5
    assert summary.cost_per_passed_task == pytest.approx(0.3)
    assert summary.uncached_input_tokens == 1_000
    assert summary.cache_read_ratio == 0.8
    assert summary.tool_calls == 4
    assert summary.wall_time_ms == 2_000


def test_promotion_requires_support_and_non_regression() -> None:
    baseline = summarize_quality(
        [
            _outcome("exp", f"b-{index}", "skill", "baseline", _quality())
            for index in range(3)
        ]
    )
    candidate = summarize_quality(
        [
            _outcome(
                "exp",
                f"c-{index}",
                "skill",
                "candidate",
                _quality(cost=0.09, uncached=900, cache_ratio=0.85, tools=3, wall=1_800),
            )
            for index in range(3)
        ]
    )
    assert evaluate_promotion(baseline, candidate).promote

    regressed = candidate.model_copy(
        update={"pass_rate": 0.5, "cost_per_passed_task": 0.2}
    )
    decision = evaluate_promotion(baseline, regressed)
    assert not decision.promote
    assert "candidate pass rate regressed" in decision.reasons
    assert "candidate cost per passed task regressed" in decision.reasons

    insufficient = candidate.model_copy(update={"support": 2})
    assert not evaluate_promotion(baseline, insufficient).promote


def test_controller_promotes_then_rolls_back_after_repeated_failures(
    tmp_path: Path,
) -> None:
    store = LearningStore(tmp_path / "learning.db")
    registry = SkillRegistry(tmp_path / "skills")
    skill_name = _candidate(registry)
    policy = PromotionPolicy(minimum_support=2, rollback_after_failures=2)
    controller = TraceSkillLearningController(
        store=store,
        registry=registry,
        policy=policy,
    )
    experiment = f"skill:{skill_name}:v1"
    for variant in ("baseline", "candidate"):
        for index in range(2):
            unit = f"{variant}-{index}"
            store.save_assignment(
                TrialAssignment(
                    experiment_id=experiment,
                    unit_id=unit,
                    variant=variant,
                )
            )
            controller.record_outcome(
                _outcome(
                    experiment,
                    unit,
                    skill_name,
                    variant,
                    _quality(),
                )
            )

    decision = controller.evaluate_and_promote(
        skill_name=skill_name,
        experiment_id=experiment,
    )
    assert decision.promote
    assert registry.load(skill_name).status == "enabled"  # type: ignore[union-attr]
    discovered = DiscoveredSkillRegistry(
        learned_skill_roots(tmp_path / "skills")
    )
    assert discovered.get(skill_name).lifecycle == "enabled"  # type: ignore[union-attr]
    assert len(discovered.select(goal=f"${skill_name}").skills) == 1

    for index in range(2):
        unit = f"failure-{index}"
        store.save_assignment(
            TrialAssignment(
                experiment_id="rollback",
                unit_id=unit,
                variant="candidate",
            )
        )
        failure = _outcome(
            "rollback",
            unit,
            skill_name,
            "candidate",
            _quality(passed=False),
        )
        assert controller.record_outcome(failure) == failure
        assert controller.record_outcome(failure) == failure

    assert registry.load(skill_name).status == "disabled"  # type: ignore[union-attr]
    rediscovered = DiscoveredSkillRegistry(
        learned_skill_roots(tmp_path / "skills")
    )
    assert rediscovered.get(skill_name) is None


def test_failed_candidate_trials_disable_before_promotion(tmp_path: Path) -> None:
    store = LearningStore(tmp_path / "learning.db")
    registry = SkillRegistry(tmp_path / "skills")
    skill_name = _candidate(registry)
    controller = TraceSkillLearningController(
        store=store,
        registry=registry,
        policy=PromotionPolicy(minimum_support=2, rollback_after_failures=2),
    )

    for index in range(2):
        unit = f"candidate-failure-{index}"
        assignment = TrialAssignment(
            experiment_id="failed-candidate",
            unit_id=unit,
            variant="candidate",
        )
        store.save_assignment(assignment)
        controller.record_outcome(
            _outcome(
                assignment.experiment_id,
                unit,
                skill_name,
                "candidate",
                _quality(passed=False),
            )
        )

    lifecycle = registry.load(skill_name)
    assert lifecycle is not None
    assert lifecycle.status == "disabled"
    assert (tmp_path / "skills" / "disabled" / skill_name / "SKILL.md").exists()


def test_disabled_skill_cannot_be_repromoted_by_stale_trial_results(
    tmp_path: Path,
) -> None:
    store = LearningStore(tmp_path / "learning.db")
    registry = SkillRegistry(tmp_path / "skills")
    skill_name = _candidate(registry)
    controller = TraceSkillLearningController(
        store=store,
        registry=registry,
        policy=PromotionPolicy(minimum_support=2),
    )
    experiment = f"skill:{skill_name}:v1"
    for variant in ("baseline", "candidate"):
        for index in range(2):
            unit = f"{variant}-{index}"
            store.save_assignment(
                TrialAssignment(
                    experiment_id=experiment,
                    unit_id=unit,
                    variant=variant,
                )
            )
            controller.record_outcome(
                _outcome(experiment, unit, skill_name, variant, _quality())
            )
    registry.disable(skill_name)

    decision = controller.evaluate_and_promote(
        skill_name=skill_name,
        experiment_id=experiment,
    )

    assert not decision.promote
    assert "only candidates may promote" in decision.reasons[0]
    assert registry.load(skill_name).status == "disabled"  # type: ignore[union-attr]
