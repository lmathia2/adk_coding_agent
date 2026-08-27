"""Deterministic trace-to-skill learning control plane."""

from __future__ import annotations

from .fingerprints import repeated_action_sequences
from .models import (
    SkillLifecycle,
    TrialAssignment,
    TrialOutcome,
    WorkflowEpisode,
    WorkflowObservation,
)
from .skills import HeuristicSkillSynthesizer, SkillRegistry, SkillSynthesizer
from .store import LearningStore
from .trials import (
    PromotionDecision,
    PromotionPolicy,
    assign_trial,
    evaluate_promotion,
    trial_summaries,
)


class TraceSkillLearningController:
    def __init__(
        self,
        *,
        store: LearningStore,
        registry: SkillRegistry,
        synthesizer: SkillSynthesizer | None = None,
        policy: PromotionPolicy | None = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.synthesizer = synthesizer or HeuristicSkillSynthesizer()
        self.policy = policy or PromotionPolicy()

    def observe(self, episode: WorkflowEpisode) -> WorkflowObservation:
        return self.store.observe(episode)

    def propose_candidate(
        self,
        workflow_kind: str,
        *,
        minimum_support: int | None = None,
    ) -> SkillLifecycle | None:
        support = minimum_support or self.policy.minimum_support
        sequences = repeated_action_sequences(
            self.store.episodes(workflow_kind=workflow_kind),
            minimum_support=support,
        )
        if not sequences:
            return None
        draft = self.synthesizer.synthesize(
            workflow_kind=workflow_kind,
            sequence=sequences[0],
        )
        return self.registry.emit_candidate(draft)

    def assign(
        self,
        *,
        experiment_id: str,
        unit_id: str,
        skill_name: str,
        skill_version: int,
        candidate_content_hash: str,
        candidate_percent: int = 50,
    ) -> TrialAssignment:
        lifecycle = self.registry.load(skill_name)
        if lifecycle is None or lifecycle.status != "candidate":
            raise ValueError("trial assignment requires a candidate skill")
        if lifecycle.version != skill_version:
            raise ValueError("trial assignment skill version is stale")
        if self.registry.content_hash(skill_name) != candidate_content_hash:
            raise ValueError("trial assignment candidate content hash is stale")
        return assign_trial(
            self.store,
            experiment_id=experiment_id,
            unit_id=unit_id,
            skill_name=skill_name,
            skill_version=skill_version,
            candidate_content_hash=candidate_content_hash,
            candidate_percent=candidate_percent,
        )

    def record_outcome(self, outcome: TrialOutcome) -> TrialOutcome:
        persisted = self.store.record_outcome(outcome)
        lifecycle = self.registry.load(outcome.skill_name)
        if (
            lifecycle is not None
            and lifecycle.status in {"candidate", "enabled"}
            and lifecycle.version == outcome.skill_version
            and self.registry.content_hash(outcome.skill_name)
            == outcome.candidate_content_hash
            and outcome.variant == "candidate"
            and self.store.consecutive_candidate_failures(
                outcome.skill_name,
                skill_version=outcome.skill_version,
                candidate_content_hash=outcome.candidate_content_hash,
            )
            >= self.policy.rollback_after_failures
        ):
            self.registry.rollback(outcome.skill_name)
        return persisted

    def evaluate_and_promote(
        self,
        *,
        skill_name: str,
        experiment_id: str,
    ) -> PromotionDecision:
        lifecycle = self.registry.load(skill_name)
        if lifecycle is None:
            raise KeyError(skill_name)
        expected_experiment = (
            f"skill:{lifecycle.name}:v{lifecycle.version}"
        )
        if experiment_id != expected_experiment:
            raise ValueError(
                f"skill trials must use experiment {expected_experiment!r}"
            )
        baseline, candidate = trial_summaries(self.store, experiment_id)
        decision = evaluate_promotion(
            baseline,
            candidate,
            policy=self.policy,
        )
        if decision.promote and lifecycle.status == "candidate":
            self.registry.promote(skill_name, decision)
        elif decision.promote:
            return PromotionDecision(
                promote=False,
                reasons=(
                    f"skill lifecycle is {lifecycle.status}; only candidates may promote",
                ),
                baseline=decision.baseline,
                candidate=decision.candidate,
            )
        return decision


__all__ = ["TraceSkillLearningController"]
