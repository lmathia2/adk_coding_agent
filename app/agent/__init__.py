"""Agents CLI entrypoint for the Pi-inspired ADK 2.x coding harness.

A package is used intentionally so ``import app.agent`` resolves this implementation
while the original scaffold module remains available as a migration breadcrumb.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from google.adk import Agent, Context, Event, Workflow
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps import App, EventsCompactionConfig, ResumabilityConfig
from google.adk.models import Gemini
from google.adk.workflow import node
from google.genai import types

from harness.models.agent_step import AgentStep
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
from harness.repo import (
    StructuralIndex,
    build_repository_manifest,
    collect_project_instructions,
)
from harness.state import EventKind, JsonlEventStore, SteeringQueue, rebuild_ledger
from harness.tools.adk_adapter import create_adk_tools
from harness.verification import discover_validation_plan, run_validation_plan

APP_NAME = "pi_inspired_adk_coding_agent"
MODEL = os.getenv("ADK_CODING_MODEL", "gemini-3.7-flash")
WORKSPACE = Path(os.getenv("ADK_CODING_WORKSPACE", os.getcwd())).resolve()
MAX_ITERATIONS = int(os.getenv("ADK_CODING_MAX_ITERATIONS", "40"))
COMPACT_AT_TOKENS = int(os.getenv("ADK_CODING_COMPACT_AT_TOKENS", "80000"))
RECENT_EVENT_LIMIT = int(os.getenv("ADK_CODING_RECENT_EVENTS", "12"))

STATIC_INSTRUCTION = """
You are an expert coding agent operating in an isolated repository workspace.

Work only toward the supplied goal and acceptance criteria. Inspect relevant code
before editing. Make the smallest coherent change that solves the task. Use read for
targeted line ranges and bash for rg, git, builds, and tests. Use edit for exact atomic
replacements and write for complete new or replaced files. Keep tool output and prose
concise. Do not claim completion without concrete evidence and deterministic
verification. At the end of each bounded work batch, return the required structured
AgentStep with one explicit next action.
""".strip()


def _state_root(workspace: Path) -> Path:
    configured = os.getenv("ADK_CODING_STATE_DIR")
    if configured:
        root = Path(configured).expanduser().resolve()
    else:
        digest = hashlib.sha256(workspace.as_posix().encode()).hexdigest()[:16]
        root = Path.home() / ".cache" / "adk-coding-agent" / digest
    root.mkdir(parents=True, exist_ok=True)
    return root


STATE_ROOT = _state_root(WORKSPACE)
EVENT_STORE = JsonlEventStore(STATE_ROOT / "events")
STEERING_QUEUE = SteeringQueue(STATE_ROOT / "state.db")
REPOSITORY_INDEX = StructuralIndex(WORKSPACE, STATE_ROOT / "repo-index.json")
TOOLS = create_adk_tools(WORKSPACE)


def read(path: str, offset: int = 1, limit: int = 400) -> dict[str, Any]:
    """Read a bounded, line-numbered range from a workspace file."""

    return TOOLS.read(path=path, offset=offset, limit=limit)


def bash(command: str, timeout_seconds: int = 120) -> dict[str, Any]:
    """Run a bounded command in the isolated workspace under command policy."""

    return TOOLS.bash(command=command, timeout_seconds=timeout_seconds)


def edit(
    path: str,
    old_text: str,
    new_text: str,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Atomically replace one exact, unique preimage in a workspace file."""

    return TOOLS.edit(
        path=path,
        old_text=old_text,
        new_text=new_text,
        expected_sha256=expected_sha256,
    )


def write(
    path: str,
    content: str,
    expected_sha256: str | None = None,
    expected_absent: bool = False,
) -> dict[str, Any]:
    """Atomically write a complete workspace file with optimistic concurrency."""

    return TOOLS.write(
        path=path,
        content=content,
        expected_sha256=expected_sha256,
        expected_absent=expected_absent,
    )


coding_worker = Agent(
    name="coding_worker",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(
            attempts=3,
            exp_base=2,
            initial_delay=1,
            http_status_codes=[429, 500, 502, 503, 504],
        ),
    ),
    description="Executes one bounded coding work batch with four composable tools.",
    static_instruction=STATIC_INSTRUCTION,
    instruction="",
    tools=[read, bash, edit, write],
    output_schema=AgentStep,
)


def _session_id(ctx: Context) -> str | None:
    direct = getattr(ctx, "session_id", None)
    if direct:
        return str(direct)
    session = getattr(ctx, "session", None)
    identifier = getattr(session, "id", None)
    return str(identifier) if identifier else None


def _render_recent_events(task_id: str) -> list[str]:
    rendered: list[str] = []
    for event in EVENT_STORE.read(task_id)[-RECENT_EVENT_LIMIT:]:
        payload = json.dumps(event.payload, sort_keys=True, default=str)
        if len(payload) > 1_000:
            payload = payload[:1_000] + "…"
        rendered.append(f"{event.sequence}. {event.kind}: {payload}")
    return rendered


def _ledger_patch(before: TaskLedger, after: TaskLedger) -> dict[str, Any]:
    previous = before.model_dump(mode="json")
    current = after.model_dump(mode="json")
    return {
        "set_fields": {
            key: value for key, value in current.items() if previous.get(key) != value
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


@node
async def verify_task(ctx: Context, node_input: dict[str, Any]) -> dict[str, Any]:
    """Run deterministic repository checks; model claims are evidence, not verdicts."""

    request = TaskRequest.model_validate(node_input["request"])
    ledger = TaskLedger.model_validate(node_input["ledger"])
    claims = node_input.get("claims", [])
    manifest = build_repository_manifest(WORKSPACE)
    modified = changed_paths(WORKSPACE, ledger.base_revision)
    plan = discover_validation_plan(
        manifest,
        modified,
        allowed_paths=getattr(request, "permitted_paths", None),
        forbidden_paths=getattr(request, "forbidden_paths", []),
    )
    evidence_map: dict[str, list[str]] = {
        claim["criterion"]: list(claim.get("evidence", [])) for claim in claims
    }
    report, command_results = run_validation_plan(
        WORKSPACE,
        plan,
        acceptance_criteria=ledger.acceptance_criteria,
        criterion_evidence=evidence_map,
    )
    return {
        "report": report.model_dump(mode="json"),
        "commands": [result.model_dump(mode="json") for result in command_results],
        "changed_paths": modified,
    }


@node(rerun_on_resume=True)
async def orchestrate(
    ctx: Context,
    node_input: str | dict[str, Any],
) -> AsyncGenerator[Event | str, None]:
    request = parse_task_request(node_input)
    session_id = _session_id(ctx)
    task_id = task_id_for(request, session_id)
    manifest = build_repository_manifest(WORKSPACE)

    events = EVENT_STORE.read(task_id)
    if events:
        ledger = rebuild_ledger(events)
    else:
        ledger = create_initial_ledger(
            request,
            task_id=task_id,
            base_revision=manifest.base_revision or "unknown",
            workspace_id=WORKSPACE.as_posix(),
            branch_id=manifest.branch or "detached",
        )
        EVENT_STORE.append(
            task_id,
            EventKind.TASK_CREATED,
            {"ledger": ledger.model_dump(mode="json")},
            idempotency_key="task-created",
        )

    REPOSITORY_INDEX.index_repository()
    project_instructions = collect_project_instructions(WORKSPACE)
    compaction_summary = ""
    owner = f"adk:{session_id or task_id}"
    max_iterations = min(
        MAX_ITERATIONS,
        int(getattr(request, "max_iterations", None) or MAX_ITERATIONS),
    )

    while ledger.iteration < max_iterations:
        leased = STEERING_QUEUE.lease(task_id, owner, limit=20)
        steering = [message.content for message in leased]
        for message in leased:
            EVENT_STORE.append(
                task_id,
                EventKind.STEERING_RECEIVED,
                {"message_id": message.message_id, "content": message.content},
                idempotency_key=f"steering:{message.message_id}",
            )

        manifest = build_repository_manifest(WORKSPACE)
        REPOSITORY_INDEX.index_repository()
        query = " ".join(
            part for part in (ledger.goal, ledger.next_action or "") if part
        )
        repository_map = REPOSITORY_INDEX.render_map(
            query,
            changed_paths=ledger.files_modified,
            recent_paths=ledger.files_read,
            max_tokens=1_200,
        )
        packet = build_work_packet(
            ledger,
            project_instructions=project_instructions,
            repository_manifest=manifest.to_compact_text(),
            repository_map=repository_map,
            compaction_summary=compaction_summary,
            recent_events=_render_recent_events(task_id),
            steering_messages=steering,
        )
        should_compact = len(packet) // 4 >= COMPACT_AT_TOKENS

        raw_step = await ctx.run_node(coding_worker, node_input=packet)
        step = parse_agent_step(raw_step)
        previous = ledger
        ledger = reduce_agent_step(ledger, step)
        ledger = _with_workspace_observations(
            ledger,
            step,
            changed_paths(WORKSPACE, ledger.base_revision),
        )
        EVENT_STORE.append(
            task_id,
            EventKind.LEDGER_PATCHED,
            _ledger_patch(previous, ledger),
            idempotency_key=f"agent-step:{ledger.iteration}",
        )
        if leased:
            STEERING_QUEUE.ack([message.message_id for message in leased], owner)

        route = decide_route(ledger, step, should_compact=should_compact)
        yield Event(
            state={
                "task_id": task_id,
                "task_ledger": ledger.model_dump(mode="json"),
                "task_route": route.value,
                "stable_instruction_sha256": hashlib.sha256(
                    STATIC_INSTRUCTION.encode()
                ).hexdigest(),
                "dynamic_context_tokens_estimate": len(packet) // 4,
            }
        )

        if route == HarnessRoute.BLOCKED:
            EVENT_STORE.append(
                task_id,
                EventKind.TASK_BLOCKED,
                {"reason": ledger.blockers[-1] if ledger.blockers else "Human input required"},
                idempotency_key=f"blocked:{ledger.iteration}",
            )
            yield json.dumps(
                {
                    "status": "blocked",
                    "task_id": task_id,
                    "questions": ledger.open_questions,
                    "blockers": ledger.blockers,
                },
                sort_keys=True,
            )
            return

        if route == HarnessRoute.REPLAN:
            previous = ledger
            ledger = replan_ledger(ledger)
            EVENT_STORE.append(
                task_id,
                EventKind.LEDGER_PATCHED,
                _ledger_patch(previous, ledger),
                idempotency_key=f"replan:{ledger.iteration}",
            )
            continue

        if route == HarnessRoute.COMPACT:
            compaction_summary = json.dumps(
                ledger.compact_projection(), sort_keys=True, indent=2
            )
            EVENT_STORE.append(
                task_id,
                EventKind.COMPACTION_CREATED,
                {
                    "summary": compaction_summary,
                    "tokens_before_estimate": len(packet) // 4,
                },
                idempotency_key=f"compact:{ledger.iteration}",
            )
            continue

        if route == HarnessRoute.VERIFY:
            verification = await ctx.run_node(
                verify_task,
                node_input={
                    "request": request.model_dump(mode="json"),
                    "ledger": ledger.model_dump(mode="json"),
                    "claims": [
                        claim.model_dump(mode="json") for claim in step.completion_claims
                    ],
                },
            )
            report = verification["report"]
            EVENT_STORE.append(
                task_id,
                EventKind.VERIFICATION_COMPLETED,
                verification,
                idempotency_key=f"verify:{ledger.iteration}",
            )
            if report["passed"]:
                EVENT_STORE.append(
                    task_id,
                    EventKind.TASK_FINISHED,
                    {"verification": report},
                    idempotency_key="task-finished",
                )
                yield json.dumps(
                    {
                        "status": "complete",
                        "task_id": task_id,
                        "changed_paths": verification["changed_paths"],
                        "verification": report,
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
            EVENT_STORE.append(
                task_id,
                EventKind.LEDGER_PATCHED,
                _ledger_patch(previous, ledger),
                idempotency_key=f"verification-failed:{ledger.iteration}",
            )

    EVENT_STORE.append(
        task_id,
        EventKind.TASK_BLOCKED,
        {"reason": f"Iteration limit {max_iterations} reached"},
        idempotency_key="iteration-limit",
    )
    yield json.dumps(
        {
            "status": "blocked",
            "task_id": task_id,
            "reason": f"Iteration limit {max_iterations} reached",
        },
        sort_keys=True,
    )


root_agent = Workflow(
    name="coding_harness",
    edges=[("START", orchestrate)],
)

app = App(
    name=APP_NAME,
    root_agent=root_agent,
    context_cache_config=ContextCacheConfig(
        min_tokens=int(os.getenv("ADK_CODING_CACHE_MIN_TOKENS", "4096")),
        ttl_seconds=int(os.getenv("ADK_CODING_CACHE_TTL_SECONDS", "1800")),
        cache_intervals=int(os.getenv("ADK_CODING_CACHE_INTERVALS", "10")),
    ),
    events_compaction_config=EventsCompactionConfig(
        compaction_interval=int(os.getenv("ADK_CODING_COMPACTION_INTERVAL", "8")),
        overlap_size=int(os.getenv("ADK_CODING_COMPACTION_OVERLAP", "2")),
        token_threshold=int(os.getenv("ADK_CODING_ADK_COMPACT_TOKENS", "96000")),
        event_retention_size=int(os.getenv("ADK_CODING_EVENT_RETENTION", "20")),
    ),
    resumability_config=ResumabilityConfig(is_resumable=True),
)

__all__ = ["app", "coding_worker", "root_agent"]
