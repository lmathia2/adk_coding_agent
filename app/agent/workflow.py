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
from google.adk.events import EventActions
from google.adk.workflow import node

from harness.context import build_compaction_snapshot, prefix_hash
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
    task_id_for,
)
from harness.repo import StructuralIndex, build_repository_manifest
from harness.review import build_diff_review_packet
from harness.state import (
    CheckpointStore,
    EventKind,
    SteeringQueue,
    rebuild_ledger,
)
from harness.state.factory import (
    ControlStateBackend,
    TaskLeaseStore,
    create_control_state_backend,
)
from harness.state.postgres import TaskLease
from harness.telemetry import MetricsStore, TaskOutcomeSample
from harness.verification import (
    ValidationCommand,
    discover_validation_plan,
    run_validation_plan,
)
from harness.workspace import GitWorktreeManager

from .config import SETTINGS, HarnessSettings
from .reviewer import (
    FINAL_REVIEW_STATIC_PREFIX,
    build_review_input,
    final_diff_reviewer,
    parse_final_diff_review,
)
from .skills import SkillRuntimeContext, build_skill_context
from .worker import coding_worker

ControlStateFactory = Callable[..., ControlStateBackend]


def _build_control_state(
    settings: HarnessSettings,
    *,
    factory: ControlStateFactory = create_control_state_backend,
) -> ControlStateBackend:
    return factory(
        state_root=settings.state_root,
        database_url=settings.control_database_url,
    )


_CONTROL_STATE = _build_control_state(SETTINGS)
_EVENT_STORE = _CONTROL_STATE.event_store
_TASK_LEASE_STORE = _CONTROL_STATE.task_lease_store
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
_STATIC_PREFIX_HASH = prefix_hash(SETTINGS.static_prefix)
_STATIC_PREFIX_TOKEN_ESTIMATE = len(SETTINGS.static_prefix) // 4
_REVIEW_PREFIX_HASH = prefix_hash(FINAL_REVIEW_STATIC_PREFIX)
_REVIEW_PREFIX_TOKEN_ESTIMATE = len(FINAL_REVIEW_STATIC_PREFIX) // 4


@dataclass(slots=True)
class _TaskLeaseGuard:
    store: TaskLeaseStore | None
    task_id: str
    owner: str
    lease_seconds: int
    lease: TaskLease | None = None

    @classmethod
    def acquire(
        cls,
        store: TaskLeaseStore | None,
        *,
        task_id: str,
        owner: str,
        lease_seconds: int,
    ) -> _TaskLeaseGuard:
        guard = cls(
            store=store,
            task_id=task_id,
            owner=owner,
            lease_seconds=lease_seconds,
        )
        if store is not None:
            guard.lease = store.acquire(
                task_id,
                owner,
                lease_seconds=lease_seconds,
            )
        return guard

    @property
    def acquired(self) -> bool:
        return self.store is None or self.lease is not None

    def renew(self) -> bool:
        if self.store is None:
            return True
        if self.lease is None:
            return False
        self.lease = self.store.renew(
            self.lease,
            lease_seconds=self.lease_seconds,
        )
        return self.lease is not None

    def release(self) -> bool:
        if self.store is None:
            return True
        if self.lease is None:
            return False
        released = self.store.release(self.lease)
        self.lease = None
        return released


def _lease_blocked_result(task_id: str, reason: str) -> str:
    return json.dumps(
        {
            "status": "blocked",
            "task_id": task_id,
            "reason": reason,
        },
        sort_keys=True,
    )


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


def _prepare_compaction(
    task_id: str,
    *,
    ledger: TaskLedger,
    tokens_before: int,
) -> CompactionSnapshot:
    """Build one deterministic snapshot from the uncompacted event suffix."""

    events = _EVENT_STORE.read(task_id)
    previous: CompactionSnapshot | str | None = None
    boundary = 0
    for index in range(len(events) - 1, -1, -1):
        event = events[index]
        if event.kind != EventKind.COMPACTION_CREATED:
            continue
        boundary = index + 1
        snapshot_payload = event.payload.get("snapshot")
        if isinstance(snapshot_payload, dict):
            previous = CompactionSnapshot.model_validate(snapshot_payload)
        else:
            previous = str(event.payload.get("summary", ""))
        break

    uncompacted = events[boundary:]
    retained_count = min(SETTINGS.recent_event_limit, len(uncompacted))
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
        created_at=datetime.now(UTC),
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
    status: Literal["complete", "blocked", "failed"],
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


def _set_model_call_state(
    ctx: Context,
    *,
    task_id: str,
    dynamic_tokens: int,
    stable_prefix_hash: str = _STATIC_PREFIX_HASH,
    static_prefix_tokens: int = _STATIC_PREFIX_TOKEN_ESTIMATE,
) -> None:
    """Expose current packet identity to ADK callbacks before the model call."""

    ctx.state.update(
        {
            "task_id": task_id,
            "stable_instruction_sha256": stable_prefix_hash,
            "static_prefix_tokens_estimate": static_prefix_tokens,
            "dynamic_context_tokens_estimate": dynamic_tokens,
        }
    )


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
            "learning_skill_name": runtime.candidate_name,
            "learning_experiment_id": runtime.experiment_id,
            "learning_variant": runtime.variant,
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
        candidate_name=state.get("learning_skill_name"),
        experiment_id=state.get("learning_experiment_id"),
        variant=state.get("learning_variant"),
    )


def _workflow_kind_hint(goal: str, languages: list[str]) -> str | None:
    lowered = goal.lower()
    if any(term in lowered for term in ("python", "pytest", "typescript", "rust", "golang")):
        return None
    normalized = {language.lower() for language in languages}
    if "python" in normalized and not normalized & {"typescript", "javascript"}:
        return "python-change"
    if normalized & {"typescript", "javascript"} and "python" not in normalized:
        return "javascript-change"
    if normalized == {"rust"}:
        return "rust-change"
    if normalized == {"go"}:
        return "go-change"
    return None


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


@node
async def review_final_diff(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
    """Run the optional bounded reviewer after deterministic verification passes."""

    packet = build_diff_review_packet(
        SETTINGS.workspace,
        str(node_input["base_revision"]),
        max_chars=SETTINGS.review_max_chars,
    )
    reviewer_input = build_review_input(packet, dict(node_input["verification"]))
    _set_model_call_state(
        ctx,
        task_id=str(node_input["task_id"]),
        dynamic_tokens=len(reviewer_input) // 4,
        stable_prefix_hash=_REVIEW_PREFIX_HASH,
        static_prefix_tokens=_REVIEW_PREFIX_TOKEN_ESTIMATE,
    )
    raw_review = await ctx.run_node(final_diff_reviewer, node_input=reviewer_input)
    review = parse_final_diff_review(raw_review)
    return {
        "review": review.model_dump(mode="json"),
        "diff_sha256": packet.diff_sha256,
        "changed_paths": packet.changed_paths,
        "truncated": packet.truncated,
        "omitted_bytes": packet.omitted_bytes,
    }


async def _orchestrate_owned(
    ctx: Context,
    node_input: str | dict[str, Any],
    lease_guard: _TaskLeaseGuard,
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
    owner = f"{SETTINGS.worker_id}:{session_id or task_id}"
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

    skill_runtime = _skill_runtime_from_state(ctx)
    if skill_runtime is None:
        try:
            skill_runtime = build_skill_context(
                task_id=task_id,
                goal=ledger.goal,
                next_action=ledger.next_action or "",
                workflow_kind=_workflow_kind_hint(ledger.goal, manifest.languages),
            )
        except Exception as error:
            skill_runtime = SkillRuntimeContext()
            _EVENT_STORE.append(
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
        "candidate": skill_runtime.candidate_name,
        "experiment_id": skill_runtime.experiment_id,
        "variant": skill_runtime.variant,
    }
    skill_event_hash = hashlib.sha256(
        json.dumps(skill_event, sort_keys=True).encode()
    ).hexdigest()[:16]
    _EVENT_STORE.append(
        task_id,
        EventKind.ACTION_RECORDED,
        skill_event,
        idempotency_key=f"skills-selected:{skill_event_hash}",
    )

    while ledger.iteration < max_iterations:
        if not lease_guard.renew():
            yield _lease_blocked_result(
                task_id,
                "distributed task lease was lost; another worker may own the task",
            )
            return
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
            project_instructions=skill_runtime.text,
            repository_manifest=manifest.to_compact_text(),
            repository_map=repository_map,
            compaction_summary=compaction_summary,
            recent_events=_render_recent_events(task_id),
            steering_messages=steering,
        )
        dynamic_tokens = len(packet) // 4
        total_context_estimate = _STATIC_PREFIX_TOKEN_ESTIMATE + dynamic_tokens
        should_compact = total_context_estimate >= SETTINGS.compact_at_tokens

        _set_model_call_state(ctx, task_id=task_id, dynamic_tokens=dynamic_tokens)
        raw_step = await ctx.run_node(coding_worker, node_input=packet)
        if not lease_guard.renew():
            yield _lease_blocked_result(
                task_id,
                "distributed task lease expired during model execution",
            )
            return
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
            actions=EventActions(state_delta={
                "task_id": task_id,
                "task_ledger": ledger.model_dump(mode="json"),
                "task_route": route.value,
                "checkpoint_id": checkpoint.checkpoint_id,
                "workspace_fingerprint": checkpoint.git_tree_hash,
                "stable_instruction_sha256": _STATIC_PREFIX_HASH,
                "static_prefix_tokens_estimate": _STATIC_PREFIX_TOKEN_ESTIMATE,
                "dynamic_context_tokens_estimate": dynamic_tokens,
            })
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
            snapshot = _prepare_compaction(
                task_id,
                ledger=ledger,
                tokens_before=total_context_estimate,
            )
            compaction_summary = snapshot.summary_markdown
            event = _EVENT_STORE.append(
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
            if not lease_guard.renew():
                yield _lease_blocked_result(
                    task_id,
                    "distributed task lease expired during verification",
                )
                return
            report = verification["report"]
            _EVENT_STORE.append(
                task_id,
                EventKind.VERIFICATION_COMPLETED,
                verification,
                idempotency_key=f"verify:{ledger.iteration}",
            )
            if report["passed"]:
                review_result: dict[str, Any] | None = None
                if SETTINGS.final_reviewer_enabled:
                    try:
                        review_result = await ctx.run_node(
                            review_final_diff,
                            node_input={
                                "task_id": task_id,
                                "base_revision": ledger.base_revision,
                                "verification": report,
                            },
                        )
                    except Exception as error:
                        review_result = {
                            "status": "unavailable",
                            "error_type": type(error).__name__,
                        }
                    if not lease_guard.renew():
                        yield _lease_blocked_result(
                            task_id,
                            "distributed task lease expired during final review",
                        )
                        return
                    _EVENT_STORE.append(
                        task_id,
                        EventKind.REVIEW_COMPLETED,
                        review_result,
                        idempotency_key="final-diff-review",
                    )
                _EVENT_STORE.append(
                    task_id,
                    EventKind.TASK_FINISHED,
                    {"verification": report, "review": review_result},
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
                        "review": review_result,
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


@node(rerun_on_resume=True)
async def orchestrate(
    ctx: Context,
    node_input: str | dict[str, Any],
) -> AsyncGenerator[Event | str, None]:
    request = parse_task_request(node_input)
    session_id = _session_id(ctx)
    task_id = SETTINGS.task_id_override or task_id_for(request, session_id)
    lease_guard = _TaskLeaseGuard.acquire(
        _TASK_LEASE_STORE,
        task_id=task_id,
        owner=SETTINGS.worker_id,
        lease_seconds=SETTINGS.task_lease_seconds,
    )
    if not lease_guard.acquired:
        yield _lease_blocked_result(
            task_id,
            "another worker owns the distributed task lease",
        )
        return

    try:
        async for event in _orchestrate_owned(ctx, node_input, lease_guard):
            yield event
    finally:
        lease_guard.release()


root_agent = Workflow(
    name="coding_harness",
    edges=[("START", orchestrate)],
)

__all__ = ["orchestrate", "review_final_diff", "root_agent", "verify_task"]
