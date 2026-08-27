"""Verified-trace projection into the guarded skill-learning control plane."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from google.adk.plugins.base_plugin import BasePlugin

from harness.learning import (
    EpisodeQuality,
    NormalizedAction,
    TraceSkillLearningController,
    TrialAssignment,
    TrialOutcome,
    WorkflowEpisode,
)
from harness.models.verification import VerificationReport
from harness.state import EventKind, rebuild_ledger
from harness.telemetry import MetricsStore
from harness.tracing import TraceStore

LOGGER = logging.getLogger(__name__)
TrialStatus = Literal["passed", "failed", "blocked", "error"]
_TOOL_CATEGORY = {
    "read": "inspect",
    "bash": "shell",
    "search.grep": "inspect",
    "search.find": "inspect",
    "search.health": "inspect",
    "edit": "mutate",
    "write": "mutate",
}


def _context_value(context: Any, name: str, default: Any = None) -> Any:
    value = getattr(context, name, None)
    if value is not None:
        return value
    state = getattr(context, "state", None)
    getter = getattr(state, "get", None)
    if callable(getter):
        return getter(name, default)
    session = getattr(context, "session", None)
    session_state = getattr(session, "state", None)
    getter = getattr(session_state, "get", None)
    if callable(getter):
        return getter(name, default)
    return default


def workflow_kind_for(goal: str, changed_paths: Sequence[str] = ()) -> str:
    """Classify a task coarsely without retaining its prompt or source bodies."""

    suffixes = {Path(path).suffix.lower() for path in changed_paths}
    lowered = goal.lower()
    if changed_paths and suffixes <= {".md", ".rst", ".txt"}:
        return "documentation"
    if ".py" in suffixes or "python" in lowered or "pytest" in lowered:
        return "python-change"
    if suffixes & {".js", ".jsx", ".ts", ".tsx"} or "typescript" in lowered:
        return "javascript-change"
    if ".rs" in suffixes or "rust" in lowered:
        return "rust-change"
    if ".go" in suffixes or "golang" in lowered:
        return "go-change"
    if "document" in lowered or "readme" in lowered:
        return "documentation"
    return "coding-change"


def _quality_for_task(
    metrics_store: MetricsStore,
    task_id: str,
    *,
    passed: bool,
    tool_calls: int | None = None,
) -> EpisodeQuality:
    metrics = metrics_store.task_summary(task_id)
    return EpisodeQuality(
        passed=passed,
        cost_usd=float(metrics.get("cost_usd") or 0),
        uncached_input_tokens=int(metrics.get("uncached_input_tokens") or 0),
        cache_read_ratio=float(metrics.get("cache_read_ratio") or 0),
        tool_calls=(
            tool_calls
            if tool_calls is not None
            else int(metrics.get("tool_calls") or 0)
        ),
        wall_time_ms=int(metrics.get("outcome_wall_time_ms") or 0),
    )


def _trial_outcome(
    assignment: TrialAssignment,
    *,
    status: TrialStatus,
    quality: EpisodeQuality,
) -> TrialOutcome:
    return TrialOutcome(
        experiment_id=assignment.experiment_id,
        unit_id=assignment.unit_id,
        skill_name=assignment.skill_name,
        skill_version=assignment.skill_version,
        candidate_content_hash=assignment.candidate_content_hash,
        variant=assignment.variant,
        status=status,
        quality=quality.model_copy(update={"passed": status == "passed"}),
    )


def _terminal_trial_status(
    *,
    task_id: str,
    events: Sequence[Any],
    metrics: dict[str, float | int | str | None],
    trace_store: TraceStore,
    trace_task_id: str,
    trace_correlation_id: str | None = None,
) -> TrialStatus | None:
    if any(event.kind == EventKind.TASK_BLOCKED for event in events):
        return "blocked"
    if any(event.kind == EventKind.TASK_FINISHED for event in events):
        verification = next(
            (
                event
                for event in reversed(events)
                if event.kind == EventKind.VERIFICATION_COMPLETED
            ),
            None,
        )
        if verification is None:
            return "failed"
        report = VerificationReport.model_validate(
            verification.payload.get("report", {})
        )
        return "passed" if report.passed else "failed"
    run_errors = trace_store.query(
        task_id,
        categories=["run"],
        phases=["error"],
    )
    if trace_task_id != task_id and trace_correlation_id is not None:
        run_errors.extend(
            trace_store.query(
                trace_task_id,
                categories=["run"],
                phases=["error"],
                correlation_id=trace_correlation_id,
            )
        )
    if run_errors:
        return "error"
    outcome_status = metrics.get("outcome_status")
    if outcome_status == "blocked":
        return "blocked"
    if outcome_status == "failed":
        return "failed"
    return None


def episode_for_verified_task(
    *,
    task_id: str,
    event_store: Any,
    trace_store: TraceStore,
    metrics_store: MetricsStore,
    trace_task_id: str | None = None,
    trace_correlation_id: str | None = None,
) -> WorkflowEpisode | None:
    """Reduce durable facts into one privacy-safe learning episode."""

    events = event_store.read(task_id)
    if not events or not any(event.kind == EventKind.TASK_FINISHED for event in events):
        return None
    verification_event = next(
        (
            event
            for event in reversed(events)
            if event.kind == EventKind.VERIFICATION_COMPLETED
        ),
        None,
    )
    if verification_event is None:
        return None
    report = VerificationReport.model_validate(
        verification_event.payload.get("report", {})
    )
    if not report.passed:
        return None
    ledger = rebuild_ledger(events)
    tool_spans = trace_store.query(
        task_id,
        categories=["tool"],
        phases=["success", "error", "blocked"],
    )
    fallback_task_id = trace_task_id or task_id
    if fallback_task_id != task_id and trace_correlation_id is not None:
        fallback_spans = trace_store.query(
            fallback_task_id,
            categories=["tool"],
            phases=["success", "error", "blocked"],
            correlation_id=trace_correlation_id,
        )
        known_span_ids = {span.span_id for span in tool_spans}
        tool_spans.extend(
            span for span in fallback_spans if span.span_id not in known_span_ids
        )
    actions = tuple(
        NormalizedAction(
            action=span.name if span.name in _TOOL_CATEGORY else "tool",
            category=_TOOL_CATEGORY.get(span.name, "other"),
            outcome=(
                "blocked"
                if span.phase == "blocked"
                else "error"
                if span.phase == "error"
                else "ok"
            ),
        )
        for span in tool_spans
    )
    if not actions:
        actions = (
            NormalizedAction(
                action="verify",
                category="task",
                outcome="ok",
            ),
        )
    blocked = any(action.outcome == "blocked" for action in actions)
    return WorkflowEpisode(
        trace_id=f"task:{hashlib.sha256(task_id.encode()).hexdigest()}",
        workflow_kind=workflow_kind_for(ledger.goal, ledger.files_modified),
        actions=actions,
        verified_completed=True,
        blocked=blocked,
        security_risks=(
            ("policy-blocked-tool-call",) if blocked else ()
        ),
        quality=_quality_for_task(
            metrics_store,
            task_id,
            passed=True,
            tool_calls=len(tool_spans),
        ),
    )


class VerifiedTraceLearningPlugin(BasePlugin):
    """Learn only after the workflow has recorded deterministic success."""

    def __init__(
        self,
        *,
        event_store: Any,
        trace_store: TraceStore,
        metrics_store: MetricsStore,
        controller: TraceSkillLearningController,
        minimum_support: int,
        default_task_id: str | None = None,
    ) -> None:
        super().__init__(name="verified_trace_learning")
        self.event_store = event_store
        self.trace_store = trace_store
        self.metrics_store = metrics_store
        self.controller = controller
        self.minimum_support = minimum_support
        self.default_task_id = default_task_id

    async def after_run_callback(self, *, invocation_context: Any) -> None:
        task_id = _context_value(
            invocation_context,
            "task_id",
            self.default_task_id,
        )
        if not task_id:
            return None
        try:
            events = self.event_store.read(str(task_id))
            experiment_id = _context_value(
                invocation_context,
                "learning_experiment_id",
            )
            skill_name = _context_value(
                invocation_context,
                "learning_skill_name",
            )
            variant = _context_value(invocation_context, "learning_variant")
            assignment = (
                self.controller.store.assignment(str(experiment_id), str(task_id))
                if experiment_id
                else None
            )
            assigned = bool(
                experiment_id
                and skill_name
                and variant in {"baseline", "candidate"}
                and assignment is not None
                and assignment.skill_name == str(skill_name)
                and assignment.variant == variant
            )
            session = getattr(invocation_context, "session", None)
            trace_task_id = (
                self.default_task_id
                or getattr(session, "id", None)
                or str(task_id)
            )
            trace_correlation_id = _context_value(
                invocation_context,
                "invocation_id",
            )
            episode = episode_for_verified_task(
                task_id=str(task_id),
                event_store=self.event_store,
                trace_store=self.trace_store,
                metrics_store=self.metrics_store,
                trace_task_id=str(trace_task_id),
                trace_correlation_id=(
                    str(trace_correlation_id) if trace_correlation_id else None
                ),
            )
            metrics = self.metrics_store.task_summary(str(task_id))
            if episode is None:
                status = _terminal_trial_status(
                    task_id=str(task_id),
                    events=events,
                    metrics=metrics,
                    trace_store=self.trace_store,
                    trace_task_id=str(trace_task_id),
                    trace_correlation_id=(
                        str(trace_correlation_id) if trace_correlation_id else None
                    ),
                )
                if assigned and status is not None:
                    assert assignment is not None
                    self.controller.record_outcome(
                        _trial_outcome(
                            assignment,
                            status=status,
                            quality=_quality_for_task(
                                self.metrics_store,
                                str(task_id),
                                passed=False,
                            ),
                        )
                    )
                    self.controller.evaluate_and_promote(
                        skill_name=assignment.skill_name,
                        experiment_id=assignment.experiment_id,
                    )
                return None
            if episode.blocked or episode.security_risks:
                if assigned:
                    assert assignment is not None
                    self.controller.record_outcome(
                        _trial_outcome(
                            assignment,
                            status="blocked",
                            quality=episode.quality,
                        )
                    )
                    self.controller.evaluate_and_promote(
                        skill_name=assignment.skill_name,
                        experiment_id=assignment.experiment_id,
                    )
                return None
            persisted_episode = self.controller.store.episode(episode.trace_id)
            observation = self.controller.observe(persisted_episode or episode)
            if assigned:
                assert assignment is not None
                self.controller.record_outcome(
                    _trial_outcome(
                        assignment,
                        status="passed",
                        quality=observation.quality,
                    )
                )
                self.controller.evaluate_and_promote(
                    skill_name=assignment.skill_name,
                    experiment_id=assignment.experiment_id,
                )
            self.controller.propose_candidate(
                episode.workflow_kind,
                minimum_support=self.minimum_support,
            )
        except Exception:
            # A learning projection must never invalidate an already verified task.
            LOGGER.exception("verified trace learning failed for task %s", task_id)
        return None

    async def on_run_error_callback(
        self,
        *,
        invocation_context: Any,
        error: Exception,
    ) -> None:
        del error
        # The trace plugin is registered before this plugin and has already
        # persisted the error classification. Reuse the idempotent projection
        # so failed invocations count against their assigned trial cohort.
        await self.after_run_callback(invocation_context=invocation_context)


__all__ = [
    "VerifiedTraceLearningPlugin",
    "episode_for_verified_task",
    "workflow_kind_for",
]
