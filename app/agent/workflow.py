"""Durable ADK 2.x workflow around the bounded coding worker."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from google.adk import Context, Event, Workflow
from google.adk.agents import BaseAgent
from google.adk.events import EventActions
from google.adk.workflow import BaseNode, node

from harness.context import build_compaction_snapshot, estimate_tokens
from harness.models import CompactionSnapshot
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
    resume_for_steering,
    task_id_for,
)
from harness.repo import StructuralIndex, build_repository_manifest
from harness.state import (
    CheckpointStore,
    EventKind,
    EventStore,
    SteeringQueue,
    rebuild_ledger,
    register_action_batch,
)
from harness.telemetry import MetricsStore, TaskOutcomeSample
from harness.verification import (
    ManagedValidationExecutor,
    ValidationCommand,
    discover_validation_plan,
    run_validation_plan,
)
from harness.workspace import GitWorktreeManager

from .config import HarnessSettings
from .skills import SkillRuntimeContext, build_skill_context


@dataclass(frozen=True, slots=True)
class PiWorkflowDependencies:
    settings: HarnessSettings
    event_store: EventStore
    steering_queue: SteeringQueue
    checkpoint_store: CheckpointStore
    metrics_store: MetricsStore
    repository_index: StructuralIndex
    workspace_manager: GitWorktreeManager | None
    coding_worker: BaseAgent
    validation_executor: Callable[[str], ManagedValidationExecutor]
    static_prefix_hash: str
    static_prefix_tokens: int
    repository_map_tokens: int
    work_packet_tokens: int
    max_task_input_tokens: int
    work_packet_section_tokens: dict[str, int]
    steering_batch_limit: int
    steering_enabled: bool
    steering_at_work_batch_boundary: bool

def _session_id(ctx: Context) -> str | None:
    direct = getattr(ctx, "session_id", None)
    if direct:
        return str(direct)
    session = getattr(ctx, "session", None)
    identifier = getattr(session, "id", None)
    return str(identifier) if identifier else None


def _git_output(deps: PiWorkflowDependencies, *args: str) -> str:
    completed = subprocess.run(
        args,
        cwd=deps.settings.workspace,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout if completed.returncode == 0 else ""


def _fallback_workspace_fingerprint(deps: PiWorkflowDependencies) -> str:
    digest = hashlib.sha256()
    digest.update(_git_output(deps, "git", "rev-parse", "HEAD").encode())
    digest.update(_git_output(deps, "git", "diff", "--binary", "HEAD").encode())
    untracked = _git_output(
        deps,
        "git",
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    for relative in sorted(path for path in untracked.split("\0") if path):
        target = deps.settings.workspace / relative
        digest.update(relative.encode())
        if target.is_file():
            digest.update(target.read_bytes())
    return digest.hexdigest()


def _workspace_fingerprint(deps: PiWorkflowDependencies, task_id: str) -> str:
    manager = deps.workspace_manager
    if manager is not None and manager.load(task_id) is not None:
        return manager.fingerprint(task_id)
    return _fallback_workspace_fingerprint(deps)


_LEDGER_DUPLICATE_EVENT_KINDS = {
    EventKind.TASK_CREATED,
    EventKind.LEDGER_PATCHED,
    EventKind.CHECKPOINT_CREATED,
}


def _render_recent_events(deps: PiWorkflowDependencies, task_id: str) -> list[str]:
    rendered: list[str] = []
    events = [
        event
        for event in deps.event_store.read(task_id)
        if event.kind not in _LEDGER_DUPLICATE_EVENT_KINDS
    ][-deps.settings.recent_event_limit :]
    for event in events:
        payload = json.dumps(event.payload, sort_keys=True, default=str)
        if len(payload) > 1_000:
            payload = payload[:1_000] + "…"
        rendered.append(f"{event.sequence}. {event.kind}: {payload}")
    return rendered


def _latest_compaction(
    deps: PiWorkflowDependencies,
    task_id: str,
) -> tuple[str, str | None]:
    for event in reversed(deps.event_store.read(task_id)):
        if event.kind == EventKind.COMPACTION_CREATED:
            return str(event.payload.get("summary", "")), event.event_id
    return "", None


def _prepare_compaction(
    task_id: str,
    *,
    event_store: EventStore,
    recent_event_limit: int,
    ledger: TaskLedger,
    tokens_before: int,
) -> CompactionSnapshot:
    """Build one deterministic snapshot from the uncompacted event suffix."""

    events = event_store.read(task_id)
    previous: CompactionSnapshot | str | None = None
    boundary = 0
    for index in range(len(events) - 1, -1, -1):
        event = events[index]
        if event.kind != EventKind.COMPACTION_CREATED:
            continue
        snapshot_payload = event.payload.get("snapshot")
        if isinstance(snapshot_payload, dict):
            previous = CompactionSnapshot.model_validate(snapshot_payload)
            cursor = previous.first_retained_event_id
            if cursor is not None:
                for cursor_index, candidate in enumerate(events):
                    if candidate.event_id == cursor:
                        boundary = cursor_index
                        break
                else:
                    boundary = index + 1
            elif previous.last_summarized_event_id is not None:
                for cursor_index, candidate in enumerate(events):
                    if candidate.event_id == previous.last_summarized_event_id:
                        boundary = cursor_index + 1
                        break
                else:
                    boundary = index + 1
            else:
                boundary = index + 1
        else:
            previous = str(event.payload.get("summary", ""))
            boundary = index + 1
        break

    uncompacted = [
        event
        for event in events[boundary:]
        if event.kind != EventKind.COMPACTION_CREATED
    ]
    retained_count = min(recent_event_limit, len(uncompacted))
    if retained_count:
        events_to_summarize = uncompacted[:-retained_count]
        retained_events = uncompacted[-retained_count:]
    else:
        events_to_summarize = uncompacted
        retained_events = []
    return build_compaction_snapshot(
        ledger=ledger,
        previous_summary=previous,
        events_to_summarize=events_to_summarize,
        retained_events=retained_events,
        tokens_before=tokens_before,
    )


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
    action_fingerprints: list[str],
) -> TaskLedger:
    data = ledger.model_dump(mode="python")
    data["files_modified"] = modified
    data["files_read"] = list(
        dict.fromkeys([*data.get("files_read", []), *step.files_in_focus])
    )
    observed = TaskLedger.model_validate(data)
    return register_action_batch(observed, action_fingerprints)


def _consume_tool_action_fingerprints(ctx: Context) -> list[str]:
    """Consume monotonic tool observations written by the metrics plugin."""

    raw_history = ctx.state.get("tool_action_fingerprints", [])
    last_sequence = int(ctx.state.get("workflow_tool_action_sequence", 0) or 0)
    observed: list[tuple[int, str]] = []
    if isinstance(raw_history, list):
        for item in raw_history:
            if not isinstance(item, dict):
                continue
            try:
                sequence = int(item.get("sequence", 0))
            except (TypeError, ValueError):
                continue
            fingerprint = item.get("fingerprint")
            if sequence > last_sequence and isinstance(fingerprint, str):
                observed.append((sequence, fingerprint))
    observed.sort()
    if observed:
        ctx.state["workflow_tool_action_sequence"] = observed[-1][0]
    return [fingerprint for _, fingerprint in observed]


def _reserve_task_input_budget(
    state: Any,
    *,
    projected_tokens: int,
    limit: int,
) -> tuple[bool, int]:
    """Atomically reserve an estimated model-call input budget in ADK state."""

    used = int(state.get("estimated_task_input_tokens", 0) or 0)
    if used + projected_tokens > limit:
        return False, used
    state["estimated_task_input_tokens"] = used + projected_tokens
    return True, used


def _save_checkpoint(
    deps: PiWorkflowDependencies,
    *,
    task_id: str,
    ledger: TaskLedger,
    session_id: str | None,
    compaction_id: str | None,
) -> Checkpoint:
    fingerprint = _workspace_fingerprint(deps, task_id)
    ledger_json = ledger.model_dump_json()
    latest = deps.checkpoint_store.latest(task_id)
    checkpoint_id = hashlib.sha256(
        f"{task_id}\0{ledger.iteration}\0{fingerprint}\0{ledger_json}".encode()
    ).hexdigest()[:32]
    event_stream = deps.event_store.read(task_id)
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
        created_at=datetime.now(UTC),
    )
    deps.checkpoint_store.save(checkpoint)
    deps.event_store.append(
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


def _event_count(deps: PiWorkflowDependencies, task_id: str, kind: EventKind) -> int:
    return sum(1 for event in deps.event_store.read(task_id) if event.kind == kind)


def _record_outcome(
    deps: PiWorkflowDependencies,
    *,
    task_id: str,
    ledger: TaskLedger,
    status: Literal["complete", "blocked", "failed"],
    passed: bool,
    started: float,
    tests_passed: int = 0,
    tests_failed: int = 0,
) -> None:
    events = deps.event_store.read(task_id)
    deps.metrics_store.record_outcome(
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


def _set_model_call_state(
    ctx: Context,
    *,
    task_id: str,
    dynamic_tokens: int,
    stable_prefix_hash: str,
    static_prefix_tokens: int,
    steering_owner: str | None = None,
    steering_packet_message_ids: tuple[str, ...] = (),
) -> None:
    """Expose current packet identity to ADK callbacks before the model call."""

    state_delta: dict[str, Any] = {
        "task_id": task_id,
        "stable_instruction_sha256": stable_prefix_hash,
        "static_prefix_tokens_estimate": static_prefix_tokens,
        "dynamic_context_tokens_estimate": dynamic_tokens,
    }
    if steering_owner is not None:
        state_delta["steering_owner"] = steering_owner
        state_delta["steering_packet_message_ids"] = list(
            steering_packet_message_ids
        )
    ctx.state.update(state_delta)


def _set_skill_state(
    ctx: Context,
    runtime: SkillRuntimeContext,
) -> None:
    ctx.state.update(
        {
            "skill_selection_initialized": True,
            "skill_context_text": runtime.text,
            "selected_skill_names": list(runtime.selected_names),
            "selected_skill_hashes": list(runtime.selected_hashes),
        }
    )


def _skill_runtime_from_state(ctx: Context) -> SkillRuntimeContext | None:
    state = ctx.state
    if not bool(state.get("skill_selection_initialized", False)):
        return None
    return SkillRuntimeContext(
        text=str(state.get("skill_context_text", "")),
        selected_names=tuple(state.get("selected_skill_names", ())),
        selected_hashes=tuple(state.get("selected_skill_hashes", ())),
    )


async def _verify_task(
    deps: PiWorkflowDependencies,
    ctx: Context,
    node_input: dict[str, Any],
) -> dict[str, Any]:
    """Run deterministic checks; model claims are evidence, not verdicts."""

    request = TaskRequest.model_validate(node_input["request"])
    ledger = TaskLedger.model_validate(node_input["ledger"])
    claims = node_input.get("claims", [])
    manifest = build_repository_manifest(deps.settings.workspace)
    modified = changed_paths(deps.settings.workspace, ledger.base_revision)
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
                strength="behavioral",
            )
        )
    evidence_map: dict[str, list[str]] = {
        claim["criterion"]: list(claim.get("evidence", [])) for claim in claims
    }
    report, command_results = run_validation_plan(
        deps.settings.workspace,
        plan,
        acceptance_criteria=ledger.acceptance_criteria,
        criterion_evidence=evidence_map,
        required_strength=request.verification_level,
        executor=deps.validation_executor(ledger.task_id),
    )
    return {
        "report": report.model_dump(mode="json"),
        "commands": [
            result.model_dump(mode="json") for result in command_results
        ],
        "changed_paths": modified,
    }


async def _orchestrate_owned(
    deps: PiWorkflowDependencies,
    ctx: Context,
    node_input: str | dict[str, Any],
    *,
    verify_node: BaseNode,
) -> AsyncGenerator[Event | str, None]:
    started = time.monotonic()
    request = parse_task_request(node_input)
    session_id = _session_id(ctx)
    settings = deps.settings
    task_id = settings.task_id_override or task_id_for(request, session_id)
    manifest = build_repository_manifest(settings.workspace)

    events = deps.event_store.read(task_id)
    if events:
        ledger = rebuild_ledger(events)
    else:
        ledger = create_initial_ledger(
            request,
            task_id=task_id,
            base_revision=(
                settings.base_revision_override
                or manifest.base_revision
                or "unknown"
            ),
            workspace_id=(
                settings.workspace_id_override or settings.workspace.as_posix()
            ),
            branch_id=manifest.branch or "detached",
        )
        deps.event_store.append(
            task_id,
            EventKind.TASK_CREATED,
            {"ledger": ledger.model_dump(mode="json")},
            idempotency_key="task-created",
        )

    latest_checkpoint = deps.checkpoint_store.latest(task_id)
    current_fingerprint = _workspace_fingerprint(deps, task_id)
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
        deps.event_store.append(
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
        deps.event_store.append(
            task_id,
            EventKind.LEDGER_PATCHED,
            _ledger_patch(previous, ledger),
            idempotency_key=f"workspace-reconcile-ledger:{current_fingerprint}",
        )

    deps.repository_index.index_repository()
    compaction_summary, compaction_id = _latest_compaction(deps, task_id)
    owner = f"{settings.worker_id}:{session_id or task_id}"
    max_iterations = min(
        settings.max_iterations,
        int(getattr(request, "max_iterations", None) or settings.max_iterations),
    )

    if latest_checkpoint is None:
        _save_checkpoint(
            deps,
            task_id=task_id,
            ledger=ledger,
            session_id=session_id,
            compaction_id=compaction_id,
        )

    skill_runtime = _skill_runtime_from_state(ctx)
    if skill_runtime is None:
        try:
            skill_runtime = build_skill_context(
                goal=ledger.goal,
                next_action=ledger.next_action or "",
                settings=settings,
            )
        except Exception as error:
            skill_runtime = SkillRuntimeContext()
            deps.event_store.append(
                task_id,
                EventKind.ACTION_RECORDED,
                {
                    "kind": "skill_loading_failed",
                    "error_type": type(error).__name__,
                },
                idempotency_key=f"skill-loading-failed:{type(error).__name__}",
            )
        _set_skill_state(ctx, skill_runtime)
    skill_event = {
        "kind": "skills_selected",
        "names": list(skill_runtime.selected_names),
        "hashes": list(skill_runtime.selected_hashes),
    }
    skill_event_hash = hashlib.sha256(
        json.dumps(skill_event, sort_keys=True).encode()
    ).hexdigest()[:16]
    deps.event_store.append(
        task_id,
        EventKind.ACTION_RECORDED,
        skill_event,
        idempotency_key=f"skills-selected:{skill_event_hash}",
    )

    while ledger.iteration < max_iterations:
        leased = (
            deps.steering_queue.lease(
                task_id,
                owner,
                limit=deps.steering_batch_limit,
                lease_seconds=settings.task_lease_seconds,
            )
            if deps.steering_enabled and deps.steering_at_work_batch_boundary
            else []
        )
        steering = [message.content for message in leased]
        for message in leased:
            deps.event_store.append(
                task_id,
                EventKind.STEERING_RECEIVED,
                {"message_id": message.message_id, "content": message.content},
                idempotency_key=f"steering:{message.message_id}",
            )

        manifest = build_repository_manifest(settings.workspace)
        deps.repository_index.index_repository()
        query = " ".join(
            part for part in (ledger.goal, ledger.next_action or "") if part
        )
        repository_map = deps.repository_index.render_map(
            query,
            changed_paths=ledger.files_modified,
            recent_paths=ledger.files_read,
            max_tokens=deps.repository_map_tokens,
        )
        packet = build_work_packet(
            ledger,
            selected_skills=skill_runtime.text,
            repository_manifest=manifest.to_compact_text(),
            repository_map=repository_map,
            compaction_summary=compaction_summary,
            recent_events=_render_recent_events(deps, task_id),
            steering_messages=steering,
            max_tokens=deps.work_packet_tokens,
            section_token_limits=deps.work_packet_section_tokens,
        )
        dynamic_tokens = estimate_tokens(packet)
        total_context_estimate = deps.static_prefix_tokens + dynamic_tokens
        should_compact = total_context_estimate >= settings.compact_at_tokens

        task_input_budget = min(
            deps.max_task_input_tokens,
            request.max_input_tokens or deps.max_task_input_tokens,
        )
        budget_reserved, estimated_task_input = _reserve_task_input_budget(
            ctx.state,
            projected_tokens=total_context_estimate,
            limit=task_input_budget,
        )
        if not budget_reserved:
            reason = (
                "Task input-token budget exhausted before the next model call "
                f"({estimated_task_input} used, {total_context_estimate} projected, "
                f"{task_input_budget} allowed)"
            )
            deps.event_store.append(
                task_id,
                EventKind.TASK_BLOCKED,
                {"reason": reason, "kind": "input_token_budget"},
                idempotency_key=f"input-budget:{ledger.iteration}",
            )
            _record_outcome(
                deps,
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
                    "blockers": [reason],
                    "metrics": deps.metrics_store.task_summary(task_id),
                },
                sort_keys=True,
            )
            return

        _set_model_call_state(
            ctx,
            task_id=task_id,
            dynamic_tokens=dynamic_tokens,
            stable_prefix_hash=deps.static_prefix_hash,
            static_prefix_tokens=deps.static_prefix_tokens,
            steering_owner=owner,
            steering_packet_message_ids=tuple(
                message.message_id for message in leased
            ),
        )
        try:
            raw_step = await ctx.run_node(deps.coding_worker, node_input=packet)
        except BaseException:
            owned = deps.steering_queue.leased_by(task_id, owner)
            deps.steering_queue.release(
                [message.message_id for message in owned],
                owner,
            )
            raise
        try:
            step = parse_agent_step(raw_step)
        except ValueError as error:
            deps.event_store.append(
                task_id,
                EventKind.ACTION_RECORDED,
                {"kind": "malformed_agent_step", "error": str(error)[:2_000]},
                idempotency_key=f"malformed-step:{ledger.iteration + 1}",
            )
            step = _malformed_step(error)

        previous = ledger
        ledger = reduce_agent_step(ledger, step)
        action_fingerprints = _consume_tool_action_fingerprints(ctx)
        ledger = _with_workspace_observations(
            ledger,
            step,
            changed_paths(settings.workspace, ledger.base_revision),
            action_fingerprints,
        )
        deps.event_store.append(
            task_id,
            EventKind.LEDGER_PATCHED,
            _ledger_patch(previous, ledger),
            idempotency_key=f"agent-step:{ledger.iteration}",
        )
        delivered = deps.steering_queue.leased_by(task_id, owner)
        if delivered:
            deps.steering_queue.ack(
                [message.message_id for message in delivered],
                owner,
            )

        pending_steering = (
            deps.steering_enabled
            and deps.steering_at_work_batch_boundary
            and deps.steering_queue.has_pending(task_id)
        )
        if pending_steering:
            previous = ledger
            ledger = resume_for_steering(ledger)
            deps.event_store.append(
                task_id,
                EventKind.LEDGER_PATCHED,
                _ledger_patch(previous, ledger),
                idempotency_key=f"steering-safe-point:{ledger.iteration}",
            )
        route = decide_route(
            ledger,
            step,
            should_compact=should_compact,
            pending_steering=pending_steering,
        )
        checkpoint = _save_checkpoint(
            deps,
            task_id=task_id,
            ledger=ledger,
            session_id=session_id,
            compaction_id=compaction_id,
        )
        yield Event(
            actions=EventActions(state_delta={
                "task_id": task_id,
                "task_ledger": ledger.model_dump(mode="json"),
                "task_route": route.value,
                "checkpoint_id": checkpoint.checkpoint_id,
                "workspace_fingerprint": checkpoint.git_tree_hash,
                "stable_instruction_sha256": deps.static_prefix_hash,
                "static_prefix_tokens_estimate": deps.static_prefix_tokens,
                "dynamic_context_tokens_estimate": dynamic_tokens,
            })
        )

        if route == HarnessRoute.BLOCKED:
            deps.event_store.append(
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
                deps,
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
                    "metrics": deps.metrics_store.task_summary(task_id),
                },
                sort_keys=True,
            )
            return

        if route == HarnessRoute.REPLAN:
            previous = ledger
            ledger = replan_ledger(ledger)
            deps.event_store.append(
                task_id,
                EventKind.LEDGER_PATCHED,
                _ledger_patch(previous, ledger),
                idempotency_key=f"replan:{ledger.iteration}",
            )
            _save_checkpoint(
                deps,
                task_id=task_id,
                ledger=ledger,
                session_id=session_id,
                compaction_id=compaction_id,
            )
            continue

        if route == HarnessRoute.COMPACT:
            snapshot = _prepare_compaction(
                task_id,
                event_store=deps.event_store,
                recent_event_limit=deps.settings.recent_event_limit,
                ledger=ledger,
                tokens_before=total_context_estimate,
            )
            compaction_summary = snapshot.summary_markdown
            event = deps.event_store.append(
                task_id,
                EventKind.COMPACTION_CREATED,
                {
                    "summary": compaction_summary,
                    "tokens_before_estimate": total_context_estimate,
                    "snapshot": snapshot.model_dump(mode="json"),
                },
                idempotency_key=f"compact:{ledger.iteration}",
            )
            compaction_id = event.event_id
            _save_checkpoint(
                deps,
                task_id=task_id,
                ledger=ledger,
                session_id=session_id,
                compaction_id=compaction_id,
            )
            continue

        if route == HarnessRoute.VERIFY:
            verification = await ctx.run_node(
                verify_node,
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
            deps.event_store.append(
                task_id,
                EventKind.VERIFICATION_COMPLETED,
                verification,
                idempotency_key=f"verify:{ledger.iteration}",
            )
            if report["passed"]:
                if (
                    deps.steering_enabled
                    and deps.steering_at_work_batch_boundary
                    and deps.steering_queue.has_pending(task_id)
                ):
                    previous = ledger
                    ledger = resume_for_steering(ledger)
                    deps.event_store.append(
                        task_id,
                        EventKind.LEDGER_PATCHED,
                        _ledger_patch(previous, ledger),
                        idempotency_key=(
                            f"steering-completion-fence:{ledger.iteration}"
                        ),
                    )
                    _save_checkpoint(
                        deps,
                        task_id=task_id,
                        ledger=ledger,
                        session_id=session_id,
                        compaction_id=compaction_id,
                    )
                    continue
                deps.event_store.append(
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
                    deps,
                    task_id=task_id,
                    ledger=ledger,
                    session_id=session_id,
                    compaction_id=compaction_id,
                )
                _record_outcome(
                    deps,
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
                        "metrics": deps.metrics_store.task_summary(task_id),
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
            deps.event_store.append(
                task_id,
                EventKind.LEDGER_PATCHED,
                _ledger_patch(previous, ledger),
                idempotency_key=f"verification-failed:{ledger.iteration}",
            )
            _save_checkpoint(
                deps,
                task_id=task_id,
                ledger=ledger,
                session_id=session_id,
                compaction_id=compaction_id,
            )

    deps.event_store.append(
        task_id,
        EventKind.TASK_BLOCKED,
        {"reason": f"Iteration limit {max_iterations} reached"},
        idempotency_key="iteration-limit",
    )
    _record_outcome(
        deps,
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
            "metrics": deps.metrics_store.task_summary(task_id),
        },
        sort_keys=True,
    )


def build_root_agent(deps: PiWorkflowDependencies) -> Workflow:
    """Build closure-bound nodes so concurrent assemblies cannot share state."""

    @node
    async def verify_task(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
        return await _verify_task(deps, ctx, node_input)

    @node(rerun_on_resume=True)
    async def orchestrate(
        ctx: Context,
        node_input: str,
    ) -> AsyncGenerator[Event | str, None]:
        # ADK Runner supplies the root user message as ``types.Content``. Its
        # FunctionNode adapter unwraps that message only when this boundary
        # directly expects ``str``; structured requests remain supported as
        # JSON text by ``parse_task_request``.
        async for event in _orchestrate_owned(
            deps, ctx, node_input, verify_node=verify_task,
        ):
            yield event

    return Workflow(
        name="coding_harness",
        edges=[("START", orchestrate)],
    )


__all__ = ["PiWorkflowDependencies", "build_root_agent"]
