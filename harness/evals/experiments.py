"""Deterministic fixed-intelligence experiment planning and paired analysis."""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from statistics import median
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .manifests import (
    BENCHMARKS,
    Benchmark,
    BenchmarkTask,
    EvaluationManifest,
    load_evaluation_manifest,
)

EXPERIMENT_SCHEMA_VERSION = "skein-fixed-intelligence-experiment-v1"
MATRIX_SCHEMA_VERSION = "skein-evaluation-matrix-v1"


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class FixedModelContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["openai_codex"] = "openai_codex"
    model: Literal["gpt-5.6-luna"] = "gpt-5.6-luna"
    reasoning: Literal["max"] = "max"
    auth_mode: Literal["chatgpt_subscription"] = "chatgpt_subscription"
    authorization: Literal["pending", "approved"] = "pending"
    account_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{16}$")
    client_version: str | None = None
    model_snapshot: str | None = None
    max_iterations: int = Field(default=24, ge=1)
    max_task_input_tokens: int = Field(default=2_000_000, ge=8_000)
    wall_time_seconds: int = Field(default=1_800, gt=0)
    concurrency: Literal[1] = 1
    stable_local_host: Literal[True] = True
    browser_bridge: Literal[False] = False
    account_or_network_rotation: Literal[False] = False
    usage_limit_bypass: Literal[False] = False
    distillation: Literal[False] = False

    @property
    def contract_sha256(self) -> str:
        return _hash(self.model_dump(mode="json"))

    @property
    def blockers(self) -> tuple[str, ...]:
        missing: list[str] = []
        if self.authorization != "approved":
            missing.append("subscription authorization is pending")
        if self.account_fingerprint is None:
            missing.append("redacted account/workspace fingerprint is not frozen")
        if self.client_version is None:
            missing.append("Codex client version is not frozen")
        if self.model_snapshot is None:
            missing.append("account-enabled model snapshot is not frozen")
        return tuple(missing)


class HarnessCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    adapter: str = Field(min_length=1)
    revision: str = Field(pattern=r"^(PENDING|[0-9a-f]{40})$")
    config_path: str | None = None
    config_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    behavior_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    agent_kwargs: dict[str, str | int] = Field(default_factory=dict)

    @property
    def blockers(self) -> tuple[str, ...]:
        return (f"{self.id} revision is not frozen",) if self.revision == "PENDING" else ()


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["skein-fixed-intelligence-experiment-v1"] = (
        EXPERIMENT_SCHEMA_VERSION
    )
    name: str = Field(pattern=r"^evaluation-(ablation|pilot|confirm)-v[0-9]+$")
    status: Literal["planning", "frozen"] = "planning"
    manifest_path: Path
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: str = Field(min_length=8)
    attempts: Literal[1, 2]
    concurrency: Literal[1] = 1
    model: FixedModelContract
    candidates: tuple[HarnessCandidate, ...] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_phase(self) -> ExperimentSpec:
        expected_attempts = 2 if "confirm" in self.name else 1
        if self.attempts != expected_attempts:
            raise ValueError(f"{self.name} requires {expected_attempts} attempt(s)")
        if len({candidate.id for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("candidate IDs must be unique")
        return self

    @property
    def blockers(self) -> tuple[str, ...]:
        blockers = list(self.model.blockers)
        for candidate in self.candidates:
            blockers.extend(candidate.blockers)
        if self.status != "frozen":
            blockers.append("experiment is not frozen")
        return tuple(blockers)


class TrialAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trial_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    order: int = Field(ge=1)
    block: int = Field(ge=1)
    benchmark: Benchmark
    task_id: str
    task_artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    harbor_task: str
    attempt: int = Field(ge=1)
    candidate_id: str
    candidate_revision: str
    adapter: str
    agent_kwargs: dict[str, str | int]
    model_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExperimentMatrix(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["skein-evaluation-matrix-v1"] = MATRIX_SCHEMA_VERSION
    experiment: str
    experiment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model: FixedModelContract
    live_ready: bool
    blockers: tuple[str, ...]
    assignments: tuple[TrialAssignment, ...]


def load_experiment(path: Path) -> ExperimentSpec:
    return ExperimentSpec.model_validate_json(path.read_text(encoding="utf-8"))


def build_matrix(spec: ExperimentSpec, manifest: EvaluationManifest) -> ExperimentMatrix:
    if manifest.name != spec.name:
        raise ValueError(f"experiment {spec.name} cannot use {manifest.name}")
    if manifest.manifest_sha256 != spec.manifest_sha256:
        raise ValueError("experiment manifest hash is stale")
    model_hash = spec.model.contract_sha256
    spec_hash = _hash(spec.model_dump(mode="json"))
    blocks: list[tuple[str, int, BenchmarkTask]] = []
    for attempt in range(1, spec.attempts + 1):
        for task in manifest.tasks:
            rank = _hash([spec.seed, task.benchmark, task.task_id, attempt])
            blocks.append((rank, attempt, task))
    assignments: list[TrialAssignment] = []
    for block_index, (_, attempt, raw_task) in enumerate(sorted(blocks), start=1):
        task = raw_task
        candidates = sorted(
            spec.candidates,
            key=lambda candidate: _hash(
                [spec.seed, task.benchmark, task.task_id, attempt, candidate.id]
            ),
        )
        for candidate in candidates:
            identity = {
                "manifest": manifest.manifest_sha256,
                "benchmark": task.benchmark,
                "task": task.task_id,
                "artifact": task.artifact_sha256,
                "attempt": attempt,
                "candidate": candidate.id,
                "revision": candidate.revision,
                "model": model_hash,
            }
            assignments.append(
                TrialAssignment(
                    trial_key=_hash(identity),
                    order=len(assignments) + 1,
                    block=block_index,
                    benchmark=task.benchmark,
                    task_id=task.task_id,
                    task_artifact_sha256=task.artifact_sha256,
                    harbor_task=task.harbor_task,
                    attempt=attempt,
                    candidate_id=candidate.id,
                    candidate_revision=candidate.revision,
                    adapter=candidate.adapter,
                    agent_kwargs=candidate.agent_kwargs,
                    model_contract_sha256=model_hash,
                )
            )
    blockers = spec.blockers
    return ExperimentMatrix(
        experiment=spec.name,
        experiment_sha256=spec_hash,
        manifest_sha256=manifest.manifest_sha256,
        model=spec.model,
        live_ready=not blockers,
        blockers=blockers,
        assignments=tuple(assignments),
    )


def build_matrix_from_file(path: Path) -> ExperimentMatrix:
    spec = load_experiment(path)
    manifest_path = spec.manifest_path
    if not manifest_path.is_absolute():
        manifest_path = (path.parent / manifest_path).resolve()
    return build_matrix(spec, load_evaluation_manifest(manifest_path))


TrialStatus = Literal[
    "pass",
    "task_failure",
    "agent_timeout",
    "provider_interruption",
    "subscription_interruption",
    "verifier_error",
    "infrastructure_error",
    "safety_failure",
]
INFRASTRUCTURE_STATUSES = {
    "provider_interruption",
    "subscription_interruption",
    "verifier_error",
    "infrastructure_error",
}


class TrialMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    active_wall_time_seconds: float = Field(ge=0)
    end_to_end_wall_time_seconds: float = Field(ge=0)
    api_equivalent_cost_usd: float = Field(ge=0)
    uncached_input_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(ge=0)
    cache_write_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    model_turns: int = Field(ge=0)
    steps: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    shell_calls: int = Field(ge=0)
    verification_runs: int = Field(ge=0)
    changed_files: int = Field(ge=0)
    compactions: int = Field(ge=0)
    duplicate_actions: int = Field(ge=0)
    operator_interventions: int = Field(ge=0)
    subscription_limit_interruptions: int = Field(ge=0)


class TrialRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    trial_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    benchmark: Benchmark
    task_id: str
    attempt: int = Field(ge=1)
    candidate_id: str
    status: TrialStatus
    official_reward: Literal[0, 1] | None
    metrics: TrialMetrics | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_reward(self) -> TrialRecord:
        if self.status in INFRASTRUCTURE_STATUSES:
            if self.official_reward is not None:
                raise ValueError("infrastructure interruptions do not receive capability rewards")
        elif self.official_reward is None:
            raise ValueError("capability outcomes require an official binary reward")
        return self


class MetricSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    median: float
    p90: float


class CandidateSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    assigned: int
    capability_trials: int
    infrastructure_interruptions: int
    clean_completion_rate: float
    intent_to_run_benchmark_scores: dict[Benchmark, float]
    intent_to_run_composite: float
    capability_benchmark_scores: dict[Benchmark, float]
    capability_composite: float
    metric_summaries: dict[str, MetricSummary]
    api_equivalent_cost_per_pass: float | None
    active_seconds_per_pass: float | None


class PairedComparison(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_a: str
    candidate_b: str
    difference: float
    bootstrap_95: tuple[float, float]
    wins: int
    losses: int
    ties: int
    mcnemar_p: float


class ExperimentAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates: tuple[CandidateSummary, ...]
    comparisons: tuple[PairedComparison, ...]


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def _metric(values: list[float]) -> MetricSummary | None:
    return MetricSummary(median=median(values), p90=_percentile(values, 0.9)) if values else None


def _scores(records: list[TrialRecord]) -> tuple[dict[Benchmark, float], float]:
    by_task: dict[tuple[Benchmark, str], list[int]] = defaultdict(list)
    for record in records:
        if record.official_reward is not None:
            by_task[(record.benchmark, record.task_id)].append(record.official_reward)
    scores: dict[Benchmark, float] = {}
    for benchmark in BENCHMARKS:
        tasks = [sum(values) / len(values) for (kind, _), values in by_task.items() if kind == benchmark]
        if not tasks:
            raise ValueError(f"no scored {benchmark} tasks")
        scores[benchmark] = sum(tasks) / len(tasks)
    return scores, sum(scores.values()) / len(BENCHMARKS)


def _mcnemar(wins: int, losses: int) -> float:
    discordant = wins + losses
    if discordant == 0:
        return 1.0
    tail = sum(math.comb(discordant, index) for index in range(min(wins, losses) + 1))
    return min(1.0, 2 * tail / (2**discordant))


def _paired_task_values(
    records: list[TrialRecord], candidate_a: str, candidate_b: str
) -> dict[Benchmark, dict[str, tuple[tuple[int, int], ...]]]:
    values: dict[tuple[str, Benchmark, str], dict[int, int]] = defaultdict(dict)
    for record in records:
        if record.official_reward is not None:
            values[(record.candidate_id, record.benchmark, record.task_id)][record.attempt] = (
                record.official_reward
            )
    paired: dict[Benchmark, dict[str, tuple[tuple[int, int], ...]]] = defaultdict(dict)
    for benchmark in BENCHMARKS:
        task_ids = {
            task_id
            for candidate, kind, task_id in values
            if candidate == candidate_a and kind == benchmark
        } & {
            task_id
            for candidate, kind, task_id in values
            if candidate == candidate_b and kind == benchmark
        }
        for task_id in task_ids:
            left = values[(candidate_a, benchmark, task_id)]
            right = values[(candidate_b, benchmark, task_id)]
            attempts = sorted(set(left) & set(right))
            if attempts:
                paired[benchmark][task_id] = tuple(
                    (left[attempt], right[attempt]) for attempt in attempts
                )
    return paired


def _bootstrap(
    paired: dict[Benchmark, dict[str, tuple[tuple[int, int], ...]]],
    *,
    seed: str,
    samples: int,
) -> tuple[float, float]:
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest(), 16))
    differences: list[float] = []
    for _ in range(samples):
        benchmark_differences: list[float] = []
        for benchmark in BENCHMARKS:
            tasks = sorted(paired[benchmark])
            if not tasks:
                raise ValueError(f"no paired {benchmark} tasks")
            sampled: list[float] = []
            for _ in tasks:
                task_id = rng.choice(tasks)
                attempts = paired[benchmark][task_id]
                resampled = [rng.choice(attempts) for _ in attempts]
                sampled.append(
                    sum(left - right for left, right in resampled) / len(resampled)
                )
            benchmark_differences.append(sum(sampled) / len(sampled))
        differences.append(sum(benchmark_differences) / len(BENCHMARKS))
    return (_percentile(differences, 0.025), _percentile(differences, 0.975))


def analyze_trials(
    matrix: ExperimentMatrix,
    records: tuple[TrialRecord, ...],
    *,
    bootstrap_samples: int = 10_000,
) -> ExperimentAnalysis:
    assignments = {assignment.trial_key: assignment for assignment in matrix.assignments}
    if len({record.trial_key for record in records}) != len(records):
        raise ValueError("duplicate trial record")
    unknown = {record.trial_key for record in records} - set(assignments)
    if unknown:
        raise ValueError(f"results contain unknown trial keys: {sorted(unknown)}")
    missing = set(assignments) - {record.trial_key for record in records}
    if missing:
        raise ValueError(f"results are incomplete: {len(missing)} assigned trials are missing")
    by_candidate: dict[str, list[TrialRecord]] = defaultdict(list)
    for record in records:
        assignment = assignments[record.trial_key]
        if (
            assignment.candidate_id != record.candidate_id
            or assignment.benchmark != record.benchmark
            or assignment.task_id != record.task_id
            or assignment.attempt != record.attempt
        ):
            raise ValueError(f"trial identity mismatch: {record.trial_key}")
        by_candidate[record.candidate_id].append(record)
    candidate_ids = sorted({assignment.candidate_id for assignment in matrix.assignments})
    summaries: list[CandidateSummary] = []
    for candidate_id in candidate_ids:
        candidate_records = by_candidate[candidate_id]
        assigned = sum(a.candidate_id == candidate_id for a in matrix.assignments)
        capability = [r for r in candidate_records if r.official_reward is not None]
        capability_scores, capability_composite = _scores(capability)
        intent_records = [
            record
            if record.official_reward is not None
            else record.model_copy(update={"official_reward": 0})
            for record in candidate_records
        ]
        intent_scores, intent_composite = _scores(intent_records)
        metrics = [r.metrics for r in candidate_records if r.metrics is not None]
        clean = sum(r.status in {"pass", "task_failure"} for r in candidate_records)
        passed = sum(r.official_reward == 1 for r in capability)
        metric_values: dict[str, list[float]] = defaultdict(list)
        for item in metrics:
            for name, value in item.model_dump().items():
                metric_values[name].append(float(value))
        summaries.append(
            CandidateSummary(
                candidate_id=candidate_id,
                assigned=assigned,
                capability_trials=len(capability),
                infrastructure_interruptions=sum(
                    r.status in INFRASTRUCTURE_STATUSES for r in candidate_records
                ),
                clean_completion_rate=clean / assigned,
                intent_to_run_benchmark_scores=intent_scores,
                intent_to_run_composite=intent_composite,
                capability_benchmark_scores=capability_scores,
                capability_composite=capability_composite,
                metric_summaries={
                    name: summary
                    for name, values in sorted(metric_values.items())
                    if (summary := _metric(values)) is not None
                },
                api_equivalent_cost_per_pass=(
                    sum(m.api_equivalent_cost_usd for m in metrics) / passed
                    if passed
                    else None
                ),
                active_seconds_per_pass=(
                    sum(m.active_wall_time_seconds for m in metrics) / passed
                    if passed
                    else None
                ),
            )
        )
    comparisons: list[PairedComparison] = []
    summary_by_id = {summary.candidate_id: summary for summary in summaries}
    for candidate_a, candidate_b in combinations(candidate_ids, 2):
        paired = _paired_task_values(list(records), candidate_a, candidate_b)
        wins = losses = ties = 0
        for tasks in paired.values():
            for attempts in tasks.values():
                left_mean = sum(left for left, _ in attempts) / len(attempts)
                right_mean = sum(right for _, right in attempts) / len(attempts)
                if left_mean > right_mean:
                    wins += 1
                elif left_mean < right_mean:
                    losses += 1
                else:
                    ties += 1
        comparisons.append(
            PairedComparison(
                candidate_a=candidate_a,
                candidate_b=candidate_b,
                difference=(
                    summary_by_id[candidate_a].capability_composite
                    - summary_by_id[candidate_b].capability_composite
                ),
                bootstrap_95=_bootstrap(
                    paired,
                    seed=f"{matrix.experiment_sha256}:{candidate_a}:{candidate_b}",
                    samples=bootstrap_samples,
                ),
                wins=wins,
                losses=losses,
                ties=ties,
                mcnemar_p=_mcnemar(wins, losses),
            )
        )
    return ExperimentAnalysis(candidates=tuple(summaries), comparisons=tuple(comparisons))


def load_trial_records(path: Path) -> tuple[TrialRecord, ...]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                records.append(TrialRecord.model_validate_json(line))
            except ValueError as error:
                raise ValueError(f"invalid trial record on line {line_number}: {error}") from error
    return tuple(records)


def next_assignment(
    matrix: ExperimentMatrix, records: tuple[TrialRecord, ...]
) -> TrialAssignment | None:
    completed = {record.trial_key for record in records}
    return next(
        (assignment for assignment in matrix.assignments if assignment.trial_key not in completed),
        None,
    )


def harbor_command(assignment: TrialAssignment, model: FixedModelContract) -> tuple[str, ...]:
    """Return argv for one trial; credentials stay in the host's normal Skein state."""

    command = [
        "harbor",
        "run",
        "--task",
        f"{assignment.harbor_task}@{assignment.task_artifact_sha256}",
        "--agent",
        assignment.adapter,
        "--model",
        model.model,
        "--agent-kwarg",
        f"provider={model.provider}",
        "--agent-kwarg",
        f"reasoning={model.reasoning}",
        "--n-attempts",
        "1",
        "--n-concurrent",
        "1",
        "--job-name",
        assignment.trial_key,
    ]
    for name, value in sorted(assignment.agent_kwargs.items()):
        command.extend(("--agent-kwarg", f"{name}={value}"))
    return tuple(command)
