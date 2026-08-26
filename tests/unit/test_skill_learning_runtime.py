from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault(
    "ADK_CODING_STATE_DIR",
    str(Path(tempfile.gettempdir()) / "adk-coding-agent-runtime-tests"),
)

from app.agent.config import SETTINGS
from app.agent.learning import (
    VerifiedTraceLearningPlugin,
    episode_for_verified_task,
    workflow_kind_for,
)
from app.agent.skills import build_skill_context, build_skill_registry
from harness.learning import (
    LearningStore,
    PromotionPolicy,
    SkillDraft,
    TraceSkillLearningController,
)
from harness.learning import (
    SkillRegistry as LearnedSkillRegistry,
)
from harness.models import TaskLedger, TaskRequest, VerificationReport
from harness.state import EventKind, JsonlEventStore
from harness.telemetry import MetricsStore, TaskOutcomeSample, ToolUsageSample
from harness.tracing import TraceSpan, TraceStore


def _write_skill(root: Path, name: str, description: str, body: str) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )


def test_runtime_loads_explicit_skill_and_controlled_candidate(tmp_path: Path) -> None:
    project = tmp_path / "project-skills"
    _write_skill(
        project,
        "python-review",
        "Review Python changes and run focused tests.",
        "Inspect the diff, then run the focused Python tests.",
    )
    learned_root = tmp_path / "learned"
    lifecycle = LearnedSkillRegistry(learned_root)
    lifecycle.emit_candidate(
        SkillDraft(
            name="learned-python-change-example",
            description="Candidate python-change workflow from verified traces.",
            instructions="Read, edit, then verify the Python change.",
            source_trace_ids=("trace-1", "trace-2", "trace-3"),
        )
    )
    controller = TraceSkillLearningController(
        store=LearningStore(tmp_path / "learning.db"),
        registry=lifecycle,
        policy=PromotionPolicy(minimum_support=2),
    )
    settings = replace(
        SETTINGS,
        skill_roots=(project,),
        learned_skill_root=learned_root,
        learning_enabled=True,
        learning_trial_percent=100,
        skill_context_bytes=12_000,
    )
    discovered_candidate = build_skill_registry(settings).get(
        "learned-python-change-example"
    )
    assert discovered_candidate is not None
    assert discovered_candidate.metadata["source_trace_ids"] == (
        "trace-1",
        "trace-2",
        "trace-3",
    )

    runtime = build_skill_context(
        task_id="task-1",
        goal="Use $python-review to fix this Python parser",
        next_action="Repair parsing and run pytest",
        settings=settings,
        controller=controller,
    )

    assert runtime.variant == "candidate"
    assert runtime.experiment_id == "skill:learned-python-change-example:v1"
    assert runtime.selected_names == (
        "python-review",
        "learned-python-change-example",
    )
    assert "Available skill catalog:" in runtime.text
    assert "Inspect the diff" in runtime.text
    assert "Read, edit, then verify" in runtime.text
    assert len(runtime.text.encode()) <= settings.skill_context_bytes

    tiny = build_skill_context(
        task_id="task-tiny",
        goal="Use $python-review",
        next_action="Review",
        settings=replace(
            settings,
            skill_context_bytes=100,
            learning_enabled=False,
        ),
        controller=controller,
    )
    assert len(tiny.text.encode()) <= 100

    tracing_off = build_skill_context(
        task_id="task-off",
        goal="Fix this Python parser",
        next_action="Run pytest",
        settings=replace(settings, trace_mode="off"),
        controller=controller,
    )
    assert tracing_off.candidate_name is None
    assert tracing_off.variant is None


def test_verified_task_reduces_to_privacy_safe_episode(tmp_path: Path) -> None:
    task_id = "task-1"
    events = JsonlEventStore(tmp_path / "events")
    ledger = TaskLedger.from_request(
        TaskRequest(goal="Fix the parser", acceptance_criteria=["Tests pass"]),
        task_id=task_id,
        workspace_id="workspace",
        base_revision="abc123",
    )
    ledger.files_modified = ["parser.py"]
    events.append(
        task_id,
        EventKind.TASK_CREATED,
        {"ledger": ledger.model_dump(mode="json")},
    )
    report = VerificationReport(passed=True, changed_paths=["parser.py"])
    events.append(
        task_id,
        EventKind.VERIFICATION_COMPLETED,
        {"report": report.model_dump(mode="json")},
    )
    events.append(task_id, EventKind.TASK_FINISHED, {"verification": {}})

    traces = TraceStore(tmp_path / "traces.db")
    traces.append(
        TraceSpan(
            span_id="span-1",
            task_id=task_id,
            sequence=1,
            correlation_id="invocation-1",
            category="tool",
            phase="success",
            name="read",
            timestamp=datetime.now(UTC).isoformat(),
            content_hash="a" * 64,
            payload_json='{"secret":"<redacted>"}',
            idempotency_key="read-1",
        )
    )
    metrics = MetricsStore(tmp_path / "metrics.db")
    metrics.record_tool_usage(
        ToolUsageSample(
            task_id=task_id,
            invocation_id="invocation-1",
            tool_name="read",
            status="ok",
            arguments_hash="a" * 64,
        )
    )
    metrics.record_outcome(
        TaskOutcomeSample(
            task_id=task_id,
            status="complete",
            passed=True,
            iterations=1,
            wall_time_ms=250,
        )
    )

    episode = episode_for_verified_task(
        task_id=task_id,
        event_store=events,
        trace_store=traces,
        metrics_store=metrics,
    )

    assert episode is not None
    assert episode.workflow_kind == "python-change"
    assert [action.token for action in episode.actions] == ["read:inspect:ok"]
    assert episode.quality.tool_calls == 1
    serialized = episode.model_dump_json()
    assert "Fix the parser" not in serialized
    assert "secret" not in serialized

    traces.append(
        TraceSpan(
            span_id="span-2",
            task_id=task_id,
            sequence=1,
            correlation_id="invocation-1",
            category="tool",
            phase="blocked",
            name="bash",
            timestamp=datetime.now(UTC).isoformat(),
            content_hash="b" * 64,
            payload_json='{"type":"object"}',
            idempotency_key="bash-blocked",
        )
    )
    blocked_episode = episode_for_verified_task(
        task_id=task_id,
        event_store=events,
        trace_store=traces,
        metrics_store=metrics,
    )
    assert blocked_episode is not None
    assert blocked_episode.blocked
    assert blocked_episode.security_risks == ("policy-blocked-tool-call",)
    assert blocked_episode.quality.tool_calls == 2


def test_workflow_kind_is_coarse_and_deterministic() -> None:
    assert workflow_kind_for("Update the README", ["README.md"]) == "documentation"
    assert workflow_kind_for("Repair it", ["src/index.ts"]) == "javascript-change"


def test_blocked_candidate_task_records_failed_trial(tmp_path: Path) -> None:
    task_id = "blocked-task"
    events = JsonlEventStore(tmp_path / "events")
    ledger = TaskLedger.from_request(
        TaskRequest(goal="Fix it", acceptance_criteria=["It works"]),
        task_id=task_id,
        workspace_id="workspace",
        base_revision="abc123",
    )
    events.append(
        task_id,
        EventKind.TASK_CREATED,
        {"ledger": ledger.model_dump(mode="json")},
    )
    events.append(task_id, EventKind.TASK_BLOCKED, {"reason": "needs input"})
    metrics = MetricsStore(tmp_path / "metrics.db")
    metrics.record_outcome(
        TaskOutcomeSample(
            task_id=task_id,
            status="blocked",
            passed=False,
            iterations=1,
        )
    )
    learned = LearnedSkillRegistry(tmp_path / "learned")
    lifecycle = learned.emit_candidate(
        SkillDraft(
            name="learned-coding-change-blocked",
            description="Candidate coding-change workflow.",
            instructions="Inspect and verify.",
            source_trace_ids=("trace-1", "trace-2"),
        )
    )
    store = LearningStore(tmp_path / "learning.db")
    controller = TraceSkillLearningController(
        store=store,
        registry=learned,
        policy=PromotionPolicy(minimum_support=2),
    )
    experiment = f"skill:{lifecycle.name}:v{lifecycle.version}"
    assignment = controller.assign(
        experiment_id=experiment,
        unit_id=task_id,
        candidate_percent=100,
    )
    plugin = VerifiedTraceLearningPlugin(
        event_store=events,
        trace_store=TraceStore(tmp_path / "traces.db"),
        metrics_store=metrics,
        controller=controller,
        minimum_support=2,
    )
    context = SimpleNamespace(
        state={
            "task_id": task_id,
            "learning_experiment_id": experiment,
            "learning_skill_name": lifecycle.name,
            "learning_variant": assignment.variant,
        },
        session=SimpleNamespace(id="session-1", state={}),
    )

    asyncio.run(plugin.after_run_callback(invocation_context=context))

    outcomes = store.outcomes(experiment_id=experiment)
    assert len(outcomes) == 1
    assert not outcomes[0].quality.passed
