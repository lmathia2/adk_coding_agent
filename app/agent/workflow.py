"""Durable ADK 2.x workflow around the bounded coding worker."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.adk import Context, Event, Workflow
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps import App, EventsCompactionConfig, ResumabilityConfig
from google.adk.workflow import node

from harness.models.agent_step import AgentStep
from harness.models.checkpoint import Checkpoint
from harness.models.ledger import TaskLedger
from harness.models.task import TaskRequest
from harness.orchestration import (
    HarnessRoute,
    build_work_packet,
    changed_paths,
    create_initial_ledger,
    decide_route,
    parse_agent_step,
    parse_task_request,
    reduce_agent_step,
    replan_ledger,
    task_id_for,
)
from harness.repo import StructuralIndex, build_repository_manifest
from harness.state import (
    CheckpointStore,
    EventKind,
    JsonlEventStore,
    SteeringQueue,
    rebuild_ledger,
)
from harness.telemetry import MetricsStore, TaskOutcomeSample
from harness.verification import (
    ValidationCommand,
    discover_validation_plan,
    run_validation_plan,
)
from harness.workspace import GitWorktreeManager

from .config import SETTINGS
from .worker import coding_worker

_EVENT_STORE = JsonlEventStore(SETTINGS.state_root / "events")
_STEERING_QUEUE = SteeringQueue(SETTINGS.state_root / "state.db")
_CHECKPOINT_STORE = CheckpointStore(SETTINGS.state_root / "state.db")
_METRICS_STORE = MetricsStore(SETTINGS.state_root / "metrics.db")
_REPOSITORY_INDEX = StructuralIndex(
    SETTINGS.workspace,
    SETTINGS.state_root / "repo-index.json",
)
_WORKSPACE_MANAGER = (
    GitWorktreeManager(SETTINGS.source_repository, SETTINGS.state_root)
    if SETTINGS.source_repository
    else None
)
_STATIC_PREFIX_HASH = hashlib.sha256(
    SETTINGS.static_instruction.encode()
).hexdigest()
_STATIC_PREFIX_TOKEN_ESTIMATE = len(SETTINGS.static_instruction) // 4


def _session_id(ctx: Context) -> str | None:
    direct = getattr(ctx, "session_id", None)
    if direct:
        return str(direct)
    session = getattr(ctx, "session", None)
    identifier = getattr(session, "id", None)
    return str(identifier) if identifier else None


def _git_output(*args: str) -> str:
    completed = subprocess.run(
        args,
        cwd=SETTINGS.workspace,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout if completed.returncode == 0 else ""


def _fallback_workspace_fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(_git_output("git", "rev-parse", "HEAD").encode())
    digest.update(_git_output("git", "diff", "--binary", "HEAD").encode())
    untracked = _git_output(
        "git",
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    for relative in sorted(path for path in untracked.split("\0") if path):
        target = SETTINGS.workspace / relative
        digest.update(relative.encode())
        if target.is_file():
            digest.update(target.read_bytes())
    return digest.hexdigest()


def _workspace_fingerprint(task_id: str) -> str:
    if _WORKSPACE_MANAGER is not None and _WORKSPACE_MANAGER.load(task_id) is not None:
        return _WORKSPACE_MANAGER.fingerprint(task_id)
    return _fallback_workspace_fingerprint()


def _render_recent_events(task_id: str) -> list[str]:
    rendered: list[str] = []
    events = _EVENT_STORE.read(task_id)[-SETTINGS.recent_event_limit :]
    for event in events:
        payload = json.dumps(event.payload, sort_keys=True, default=str)
        if len(payload) > 1_000:
            payload = payload[:1_000] + "…"
        rendered.append(f"{event.sequence}. {event.kind}: {payload}")
    return rendered


def _latest_compaction(task_id: str) -> tuple[str, str | None]:
    for event in reversed(_EVENT_STORE.read(task_id)):
        if event.kind == EventKind.COMPACTION_CREATED:
            return str(event.payload.get("summary", "")), event.event_id
    return "", None


def _ledger_patch(before: TaskLedger, after: TaskLedger) -> dict[str, Any]:
    previous = before.model_dump(mode="json")
    current = after.model_dump(mode="json")
    return {
        "set_fields": {
            key: value
            for key, value in current.items()
            if previous.get(key) != value
        }
    }


def _with_workspace_observations(
    ledger: TaskLedger,
    step: AgentStep,
    modified: list[str],
) -> TaskLedger:
    data = ledger.model_dump(mode="python")
    data["files_modified"] = modified
    data["files_read"] = list(
        dict.fromkeys([*data.get("files_read", []), *step.files_in_focus])
    )
    if not step.progress:
        data["no_progress_count"] = int(data.get("no_progress_count", 0)) + 1
    return TaskLedger.model_validate(data)


def _save_checkpoint(
    *,
    task_id: str,
    ledger: TaskLedger,
    session_id: str | None,
    compaction_id: str | None,
) -> Checkpoint:
    fingerprint = _workspace_fingerprint(task_id)
    ledger_json = ledger.model_dump_json()
    latest = _CHECKPOINT_STORE.latest(task_id)
    checkpoint_id = hashlib.sha256(
        f"{task_id}\0{ledger.iteration}\0{fingerprint}\0{ledger_json}".encode()
    ).hexdigest()[:32]
    event_stream = _EVENT_STORE.read(task_id)
    checkpoint = Checkpoint(
        checkpoint_id=checkpoint_id,
        task_id=task_id,
        session_id=session_id or "unknown",
        invocation_id=f"iteration-{ledger.iteration}",
        branch_id=ledger.branch_id,
        parent_checkpoint_id=(latest.checkpoint_id if latest else None),
        workspace_id=ledger.workspace_id,
        base_revision=ledger.base_revision,
        git_tree_hash=fingerprint,
        ledger_version=event_stream[-1].sequence,
        ledger_hash=hashlib.sha256(ledger_json.encode()).hexdigest(),
        compaction_id=compaction_id,
        created_at=datetime.now(timezone.utc),
    )
    _CHECKPOINT_STORE.save(checkpoint)
    _EVENT_STORE.append(
        task_id,
        EventKind.CHECKPOINT_CREATED,
        {
            "checkpoint_id": checkpoint.checkpoint_id,
            "workspace_fingerprint": fingerprint,
            "ledger_hash": checkpoint.ledger_hash,
        },
        idempotency_key=f"checkpoint:{checkpoint.checkpoint_id}",
    )
    return checkpoint


def _event_count(task_id: str, kind: EventKind) -> int:
    return sum(1 for event in _EVENT_STORE.read(task_id) if event.kind == kind)


def _record_outcome(
    *,
    task_id: str,
    ledger: TaskLedger,
    status: str,
    passed: bool,
    started: float,
    tests_passed: int = 0,
    tests_failed: int = 0,
) -> None:
    events = _EVENT_STORE.read(task_id)
    _METRICS_STORE.record_outcome(
        TaskOutcomeSample(
            task_id=task_id,
            status=status,
            passed=passed,
            iterations=ledger.iteration,
            compactions=sum(
                1 for event in events if event.kind == EventKind.COMPACTION_CREATED
            ),
            replans=sum(
                1
                for event in events
                if event.idempotency_key
                and event.idempotency_key.startswith("replan:")
            ),
            user_interventions=sum(
                1 for event in events if event.kind == EventKind.STEERING_RECEIVED
            ),
            changed_files=len(ledger.files_modified),
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            wall_time_ms=int((time.monotonic() - started) * 1000),
        )
    )


def _malformed_step(error: ValueError) -> AgentStep:
    return AgentStep(
        status="continue",
        progress=[],
        next_action=(
            "Continue the task, then return exactly one valid AgentStep JSON object"
        ),
        decisions=[],
        questions=[],
        discovered_constraints=[str(error)[:1_000]],
        files_in_focus=[],
        completion_claims=[],
    )


@node
async def verify_task(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
    """Run deterministic checks; model claims are evidence, not verdicts."""

    request = TaskRequest.model_validate(node_input["request"])
    ledger = TaskLedger.model_validate(node_input["ledger"])
    claims = node_input.get("claims", [])
    manifest = build_repository_manifest(SETTINGS.workspace)
    modified = changed_paths(SETTINGS.workspace, ledger.base_revision)
    plan = discover_validation_plan(
        manifest,
        modified,
        allowed_paths=getattr(request, "permitted_paths", None),
        forbidden_paths=getattr(request, "forbidden_paths", []),
    )
    for command in getattr(request, "verification_requirements", []):
        plan.commands.append(
            ValidationCommand(
                category="custom",
                command=command,
                source="task verification requirement",
            )
        )
    evidence_map: dict[str, list[str]] = {
        claim["criterion"]: list(claim.get("evidence", [])) for claim in claims
    }
    report, command_results = run_validation_plan(
        SETTINGS.workspace,
        plan,
        acceptance_criteria=ledger.acceptance_criteria,
        criterion_evidence=evidence_map,
    )
    return {
        "report": report.model_dump(mode="json"),
        "commands": [
            result.model_dump(mode="json") for result in command_results
        ],
        "changed_paths": modified,
    }


@node(rerun_on_resume=True)
async def orchestrate(
    ctx: Context,
    node_input: str | dict[str, Any],
) -> AsyncGenerator[Event | str, None]:
    started = time.monotonic()
    request = parse_task_request(node_input)
    session_id = _session_id(ctx)
    task_id = SETTINGS.task_id_override or task_id_for(request, session_id)
    manifest = build_repository_manifest(SETTINGS.workspace)

    events = _EVENT_STORE.read(task_id)
    if events:
        ledger = rebuild_ledger(events)
    else:
        ledger = create_initial_ledger(
            request,
            task_id=task_id,
            base_revision=(
                SETTINGS.base_revision_override
                or manifest.base_revision
                or "unknown"
            ),
            workspace_id=(
                SETTINGS.workspace_id_override or SETTINGS.workspace.as_posix()
            ),
            branch_id=manifest.branch or "detached",
        )
        _EVENT_STORE.append(
            task_id,
            EventKind.TASK_CREATED,
            {"ledger": ledger.model_dump(mode="json")},
            idempotency_key="task-created",
        )

    latest_checkpoint = _CHECKPOINT_STORE.latest(task_id)
    current_fingerprint = _workspace_fingerprint(task_id)
    if (
        latest_checkpoint is not None
        and latest_checkpoint.git_tree_hash != current_fingerprint
    ):
        previous = ledger
        data = ledger.model_dump(mode="python")
        data["next_action"] = (
            "Reconcile workspace changes made after the latest checkpoint before "
            "continuing implementation"
        )
        ledger = TaskLedger.model_validate(data)
        _EVENT_STORE.append(
            task_id,
            EventKind.WORKSPACE_INITIALIZED,
            {
                "checkpoint_id": latest_checkpoint.checkpoint_id,
                "expected_fingerprint": latest_checkpoint.git_tree_hash,
                "actual_fingerprint": current_fingerprint,
                "requires_reconciliation": True,
            },
            idempotency_key=f"workspace-reconcile:{current_fingerprint}",
        )
        _EVENT_STORE.append(
            task_id,
            EventKind.LEDGER_PATCHED,
            _ledger_patch(previous, ledger),
            idempotency_key=f"workspace-reconcile-ledger:{current_fingerprint}",
        )

    _REPOSITORY_INDEX.index_repository()
    compaction_summary, compaction_id = _latest_compaction(task_id)
    owner = f"adk:{session_id or task_id}"
    max_iterations = min(
        SETTINGS.max_iterations,
        int(getattr(request, "max_iterations", None) or SETTINGS.max_iterations),
    )

    if latest_checkpoint is None:
        _save_checkpoint(
            task_id=task_id,
            ledger=ledger,
            session_id=session_id,
            compaction_id=compaction_id,
        )

    while ledger.iteration < max_iterations:
        leased = _STEERING_QUEUE.lease(task_id, owner, limit=20)
        steering = [message.content for message in leased]
        for message in leased:
            _EVENT_STORE.append(
                task_id,
                EventKind.STEERING_RECEIVED,
                {"message_id": message.message_id, "content": message.content},
                idempotency_key=f"steering:{message.message_id}",
            )

        manifest = build_repository_manifest(SETTINGS.workspace)
        _REPOSITORY_INDEX.index_repository()
        query = " ".join(
            part for part in (ledger.goal, ledger.next_action or "") if part
        )
        repository_map = _REPOSITORY_INDEX.render_map(
            query,
            changed_paths=ledger.files_modified,
            recent_paths=ledger.files_read,
            max_tokens=1_200,
        )
        packet = build_work_packet(
            ledger,
            project_instructions="",
            repository_manifest=manifest.to_compact_text(),
            repository_map=repository_map,
            compaction_summary=compaction_summary,
            recent_events=_render_recent_events(task_id),
            steering_messages=steering,
        )
        dynamic_tokens = len(packet) // 4
        total_context_estimate = _STATIC_PREFIX_TOKEN_ESTIMATE + dynamic_tokens
        should_compact = total_context_estimate >= SETTINGS.compact_at_tokens

        raw_step = await ctx.run_node(coding_worker, node_input=packet)
        try:
            step = parse_agent_step(raw_step)
        except ValueError as error:
            _EVENT_STORE.append(
                task_id,
                EventKind.ACTION_RECORDED,
                {"kind": "malformed_agent_step", "error": str(error)[:2_000]},
                idempotency_key=f"malformed-step:{ledger.iteration + 1}",
            )
            step = _malformed_step(error)

        previous = ledger
        ledger = reduce_agent_step(ledger, step)
        ledger = _with_workspace_observations(
            ledger,
            step,
            changed_paths(SETTINGS.workspace, ledger.base_revision),
        )
        _EVENT_STORE.append(
            task_id,
            EventKind.LEDGER_PATCHED,
            _ledger_patch(previous, ledger),
            idempotency_key=f"agent-step:{ledger.iteration}",
        )
        if leased:
            _STEERING_QUEUE.ack(
                [message.message_id for message in leased],
                owner,
            )

        route = decide_route(
            ledger,
            step,
            should_compact=should_compact,
        )
        checkpoint = _save_checkpoint(
            task_id=task_id,
            ledger=ledger,
            session_id=session_id,
            compaction_id=compaction_id,
        )
        yield Event(
            state={
                "task_id": task_id,
                "task_ledger": ledger.model_dump(mode="json"),
                "task_route": route.value,
                "checkpoint_id": checkpoint.checkpoint_id,
                "workspace_fingerprint": checkpoint.git_tree_hash,
                "stable_instruction_sha256": _STATIC_PREFIX_HASH,
                "static_prefix_tokens_estimate": _STATIC_PREFIX_TOKEN_ESTIMATE,
                "dynamic_context_tokens_estimate": dynamic_tokens,
            }
        )

        if route == HarnessRoute.BLOCKED:
            _EVENT_STORE.append(
                task_id,
                EventKind.TASK_BLOCKED,
                {
                    "reason": (
                        ledger.blockers[-1]
                        if ledger.blockers
                        else "Human input required"
                    )
                },
                idempotency_key=f"blocked:{ledger.iteration}",
            )
            _record_outcome(
                task_id=task_id,
                ledger=ledger,
                status="blocked",
                passed=False,
                started=started,
            )
            yield json.dumps(
                {
                    "status": "blocked",
                    "task_id": task_id,
                    "questions": ledger.open_questions,
                    "blockers": ledger.blockers,
                    "metrics": _METRICS_STORE.task_summary(task_id),
                },
                sort_keys=True,
            )
            return

        if route == HarnessRoute.REPLAN:
            previous = ledger
            ledger = replan_ledger(ledger)
            _EVENT_STORE.append(
                task_id,
                EventKind.LEDGER_PATCHED,
                _ledger_patch(previous, ledger),
                idempotency_key=f"replan:{ledger.iteration}",
            )
            _save_checkpoint(
                task_id=task_id,
                ledger=ledger,
                session_id=session_id,
                compaction_id=compaction_id,
            )
            continue

        if route == HarnessRoute.COMPACT:
            compaction_summary = json.dumps(
                ledger.compact_projection(),
                sort_keys=True,
                indent=2,
            )
            event = _EVENT_STORE.append(
                task_id,
                EventKind.COMPACTION_CREATED,
                {
                    "summary": compaction_summary,
                    "tokens_before_estimate": total_context_estimate,
                },
                idempotency_key=f"compact:{ledger.iteration}",
            )
            compaction_id = event.event_id
            _save_checkpoint(
                task_id=task_id,
                ledger=ledger,
                session_id=session_id,
                compaction_id=compaction_id,
            )
            continue

        if route == HarnessRoute.VERIFY:
            verification = await ctx.run_node(
                verify_task,
                node_input={
                    "request": request.model_dump(mode="json"),
                    "ledger": ledger.model_dump(mode="json"),
                    "claims": [
                        claim.model_dump(mode="json")
                        for claim in step.completion_claims
                    ],
                },
            )
            report = verification["report"]
            _EVENT_STORE.append(
                task_id,
                EventKind.VERIFICATION_COMPLETED,
                verification,
                idempotency_key=f"verify:{ledger.iteration}",
            )
            if report["passed"]:
                _EVENT_STORE.append(
                    task_id,
                    EventKind.TASK_FINISHED,
                    {"verification": report},
                    idempotency_key="task-finished",
                )
                data = ledger.model_dump(mode="python")
                data["phase"] = "complete"
                data["status"] = "complete"
                ledger = TaskLedger.model_validate(data)
                _save_checkpoint(
                    task_id=task_id,
                    ledger=ledger,
                    session_id=session_id,
                    compaction_id=compaction_id,
                )
                _record_outcome(
                    task_id=task_id,
                    ledger=ledger,
                    status="complete",
                    passed=True,
                    started=started,
                    tests_passed=int(report.get("tests_passed", 0)),
                    tests_failed=int(report.get("tests_failed", 0)),
                )
                yield json.dumps(
                    {
                        "status": "complete",
                        "task_id": task_id,
                        "changed_paths": verification["changed_paths"],
                        "verification": report,
                        "metrics": _METRICS_STORE.task_summary(task_id),
                    },
                    sort_keys=True,
                )
                return

            data = ledger.model_dump(mode="python")
            data["phase"] = "implement"
            data["status"] = "active"
            data["next_action"] = report.get("recommended_next_action")
            previous = ledger
            ledger = TaskLedger.model_validate(data)
            _EVENT_STORE.append(
                task_id,
                EventKind.LEDGER_PATCHED,
                _ledger_patch(previous, ledger),
                idempotency_key=f"verification-failed:{ledger.iteration}",
            )
            _save_checkpoint(
                task_id=task_id,
                ledger=ledger,
                session_id=session_id,
                compaction_id=compaction_id,
            )

    _EVENT_STORE.append(
        task_id,
        EventKind.TASK_BLOCKED,
        {"reason": f"Iteration limit {max_iterations} reached"},
        idempotency_key="iteration-limit",
    )
    _record_outcome(
        task_id=task_id,
        ledger=ledger,
        status="blocked",
        passed=False,
        started=started,
    )
    yield json.dumps(
        {
            "status": "blocked",
            "task_id": task_id,
            "reason": f"Iteration limit {max_iterations} reached",
            "metrics": _METRICS_STORE.task_summary(task_id),
        },
        sort_keys=True,
    )


root_agent = Workflow(
    name="coding_harness",
    edges=[("START", orchestrate)],
)

app = App(
    name=SETTINGS.app_name,
    root_agent=root_agent,
    context_cache_config=ContextCacheConfig(
        min_tokens=int(os.getenv("ADK_CODING_CACHE_MIN_TOKENS", "4096")),
        ttl_seconds=int(os.getenv("ADK_CODING_CACHE_TTL_SECONDS", "1800")),
        cache_intervals=int(os.getenv("ADK_CODING_CACHE_INTERVALS", "10")),
    ),
    events_compaction_config=EventsCompactionConfig(
        compaction_interval=int(
            os.getenv("ADK_CODING_COMPACTION_INTERVAL", "8")
        ),
        overlap_size=int(os.getenv("ADK_CODING_COMPACTION_OVERLAP", "2")),
        token_threshold=int(
            os.getenv("ADK_CODING_ADK_COMPACT_TOKENS", "96000")
        ),
        event_retention_size=int(
            os.getenv("ADK_CODING_EVENT_RETENTION", "20")
        ),
    ),
    resumability_config=ResumabilityConfig(is_resumable=True),
)

__all__ = ["app", "orchestrate", "root_agent", "verify_task"]
