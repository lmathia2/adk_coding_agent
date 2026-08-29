"""Local launcher that couples an ADK session to an isolated Git worktree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from harness.learning import SkillRegistry as LearnedSkillRegistry
from harness.state import SteeringMessage, SteeringQueue
from harness.tracing import TraceStore
from harness.workspace import GitWorktreeManager, WorkspaceRecord


@dataclass(frozen=True, slots=True)
class RunPreparation:
    workspace: WorkspaceRecord
    state_dir: Path
    command: tuple[str, ...]
    environment: dict[str, str]
    harness_root: Path

    def to_json(self) -> str:
        workspace = asdict(self.workspace)
        for key in ("path", "source_repository"):
            workspace[key] = Path(workspace[key]).as_posix()
        return json.dumps(
            {
                "workspace": workspace,
                "state_dir": self.state_dir.as_posix(),
                "command": list(self.command),
                "environment": self.environment,
                "harness_root": self.harness_root.as_posix(),
            },
            sort_keys=True,
            indent=2,
        )


def _default_state_root(repository: Path) -> Path:
    digest = hashlib.sha256(repository.resolve().as_posix().encode()).hexdigest()[:16]
    return Path.home() / ".cache" / "adk-coding-agent" / digest


def prepare_run(
    *,
    repository: Path,
    task_id: str,
    prompt: str,
    base_ref: str = "HEAD",
    branch: str | None = None,
    state_root: Path | None = None,
    harness_root: Path | None = None,
    agents_cli: str = "agents-cli",
) -> RunPreparation:
    repository = repository.resolve()
    state = (state_root or _default_state_root(repository)).resolve()
    manager = GitWorktreeManager(repository, state)
    workspace = manager.create(task_id, base_ref=base_ref, branch=branch)
    project_root = (harness_root or Path(__file__).resolve().parents[1]).resolve()
    environment = {
        "ADK_CODING_WORKSPACE": workspace.path.as_posix(),
        "ADK_CODING_STATE_DIR": state.as_posix(),
        "ADK_CODING_TASK_ID": task_id,
        "ADK_CODING_BASE_REVISION": workspace.base_revision,
        "ADK_CODING_WORKSPACE_ID": workspace.workspace_id,
    }
    return RunPreparation(
        workspace=workspace,
        state_dir=state,
        command=(agents_cli, "run", prompt),
        environment=environment,
        harness_root=project_root,
    )


def run_prepared(preparation: RunPreparation) -> int:
    environment = {**os.environ, **preparation.environment}
    completed = subprocess.run(
        preparation.command,
        cwd=preparation.harness_root,
        env=environment,
        check=False,
    )
    return completed.returncode


def _steering_state_root(*, repository: Path | None, state_root: Path | None) -> Path:
    if state_root is not None:
        return state_root.resolve()
    if repository is None:
        raise ValueError("repository or state root is required")
    return _default_state_root(repository.resolve()).resolve()


def _steering_record(
    message: SteeringMessage,
    *,
    include_content: bool,
) -> dict[str, object]:
    payload = message.model_dump(mode="json")
    content = str(payload.pop("content"))
    payload["content_bytes"] = len(content.encode("utf-8"))
    payload["content_sha256"] = hashlib.sha256(content.encode()).hexdigest()
    if include_content:
        payload["content"] = content
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adk-coding-agent",
        description="Run the Pi-inspired ADK coding harness in an isolated Git worktree.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_workspace_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--repository", type=Path, required=True)
        command.add_argument("--task-id", required=True)
        command.add_argument("--base-ref", default="HEAD")
        command.add_argument("--branch")
        command.add_argument("--state-root", type=Path)

    prepare = subparsers.add_parser("prepare", help="Create/reattach a task worktree")
    add_workspace_arguments(prepare)
    prepare.add_argument("prompt")
    prepare.add_argument("--agents-cli", default="agents-cli")

    run = subparsers.add_parser("run", help="Prepare the workspace and run agents-cli")
    add_workspace_arguments(run)
    run.add_argument("prompt")
    run.add_argument("--agents-cli", default="agents-cli")
    run.add_argument("--print-command", action="store_true")

    cleanup = subparsers.add_parser("cleanup", help="Remove a task worktree")
    cleanup.add_argument("--repository", type=Path, required=True)
    cleanup.add_argument("--task-id", required=True)
    cleanup.add_argument("--state-root", type=Path)
    cleanup.add_argument("--force", action="store_true")

    trace_export = subparsers.add_parser(
        "trace-export",
        help="Print an already-redacted task trace as JSONL",
    )
    trace_export.add_argument("--state-root", type=Path, required=True)
    trace_export.add_argument("--task-id", required=True)

    learned = subparsers.add_parser(
        "learned-skills",
        help="List learned skill lifecycle records",
    )
    learned.add_argument("--state-root", type=Path, required=True)

    disable = subparsers.add_parser(
        "disable-skill",
        help="Move a learned skill to the disabled lifecycle",
    )
    disable.add_argument("--state-root", type=Path, required=True)
    disable.add_argument("name")

    def add_steering_target(command: argparse.ArgumentParser) -> None:
        target = command.add_mutually_exclusive_group(required=True)
        target.add_argument("--repository", type=Path)
        target.add_argument("--state-root", type=Path)
        command.add_argument("--task-id", required=True)

    steer = subparsers.add_parser(
        "steer",
        help="Queue user guidance for an active or resumable task",
    )
    add_steering_target(steer)
    steer.add_argument("message")
    steer.add_argument("--priority", type=int, default=0)
    steer.add_argument("--idempotency-key")

    steering_status = subparsers.add_parser(
        "steering-status",
        help="Inspect durable steering delivery state",
    )
    add_steering_target(steering_status)
    steering_status.add_argument("--include-content", action="store_true")
    steering_status.add_argument("--limit", type=int, default=100)

    serve = subparsers.add_parser(
        "serve",
        help="Serve the configured harness over the bidirectional WebSocket protocol",
    )
    serve.add_argument("--workspace", type=Path, default=Path.cwd())
    serve.add_argument("--state-root", type=Path)
    serve.add_argument("--config", type=Path)
    serve.add_argument(
        "--production",
        action="store_true",
        help="Refuse the host-local sandbox and require an enforceable adapter",
    )
    serve.add_argument(
        "--trust-project",
        action="store_true",
        help="Load workspace AGENTS.md files and project-local skills as instructions",
    )
    serve.add_argument(
        "--print-config",
        action="store_true",
        help="Resolve and print server settings without starting the network listener",
    )

    magnitude = subparsers.add_parser(
        "serve-magnitude",
        help="Discover Magnitude's selected local model and serve the harness",
    )
    magnitude.add_argument("--workspace", type=Path, default=Path.cwd())
    magnitude.add_argument("--state-root", type=Path)
    magnitude.add_argument("--model", help="Use this installed Magnitude model id")
    magnitude.add_argument(
        "--reasoning",
        choices=("none", "low", "medium", "high", "xhigh"),
        help="Override local-model reasoning effort when the selected model supports it",
    )
    magnitude.add_argument(
        "--endpoint",
        default="http://127.0.0.1:10100/inference/v1",
        help="Magnitude's OpenAI-compatible base URL",
    )
    magnitude.add_argument("--magnitude-state", type=Path)
    magnitude.add_argument(
        "--production",
        action="store_true",
        help="Refuse the host-local sandbox and require an enforceable adapter",
    )
    magnitude.add_argument(
        "--trust-project",
        action="store_true",
        help="Load workspace AGENTS.md files and project-local skills as instructions",
    )
    magnitude.add_argument(
        "--no-start-magnitude",
        action="store_true",
        help="Fail instead of running `magnitude server start` when unavailable",
    )
    magnitude.add_argument(
        "--print-config",
        action="store_true",
        help="Resolve and print server settings without starting the harness listener",
    )
    return parser


def _serve(args: argparse.Namespace) -> int:
    from harness.config import DEFAULT_COMPOSITION_PATH
    from harness.server.bootstrap import build_server_assembly

    workspace = args.workspace.expanduser().resolve()
    state_root = (
        args.state_root.expanduser().resolve()
        if args.state_root is not None
        else _default_state_root(workspace)
    )
    configured_path = os.getenv("ADK_CODING_CONFIG", "").strip()
    config_path = (
        args.config
        if args.config is not None
        else Path(configured_path)
        if configured_path
        else DEFAULT_COMPOSITION_PATH
    )
    assembly = build_server_assembly(
        workspace=workspace,
        state_root=state_root,
        config_path=config_path,
        production=args.production,
        trust_project=args.trust_project,
    )
    server = assembly.composition.server
    sandbox_kind = str(
        getattr(getattr(assembly.composition.harness.config, "sandbox", None), "kind", "unknown")
    )
    if args.print_config:
        print(
            json.dumps(
                {
                    "auth_token_source": (
                        assembly.auth_token_path.as_posix()
                        if assembly.auth_token_path is not None
                        else "environment:ADK_CODING_AGENT_TOKEN"
                    ),
                    "config_sha256": assembly.composition.composition_sha256,
                    "harness": assembly.coordinator.descriptor.implementation,
                    "host": server.host,
                    "port": server.port,
                    "production_mode": bool(args.production),
                    "sandbox": sandbox_kind,
                    "project_trusted": bool(args.trust_project),
                    "state_root": assembly.state_root.as_posix(),
                    "websocket_url": (f"ws://{server.host}:{server.port}{server.websocket_path}"),
                    "workspace": assembly.workspace.as_posix(),
                },
                sort_keys=True,
                indent=2,
            )
        )
        return 0

    import uvicorn

    uvicorn.run(
        assembly.app,
        host=server.host,
        port=server.port,
        ws_max_size=64 * 1024,
    )
    return 0


def _serve_magnitude(args: argparse.Namespace) -> int:
    from harness.magnitude import (
        MAGNITUDE_API_KEY,
        MagnitudeConnectionError,
        prepare_magnitude_connection,
    )

    workspace = args.workspace.expanduser().resolve()
    state_root = (
        args.state_root.expanduser().resolve()
        if args.state_root is not None
        else _default_state_root(workspace)
    )
    try:
        connection = prepare_magnitude_connection(
            state_root=state_root,
            endpoint=args.endpoint,
            requested_model=args.model,
            reasoning=args.reasoning,
            magnitude_state_path=args.magnitude_state,
            start_service=not args.no_start_magnitude,
        )
    except MagnitudeConnectionError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(
        "Magnitude coding model:\n"
        f"  Model: {connection.model_id}\n"
        f"  Reasoning: {args.reasoning or 'model default'}\n"
        "  Status: advertised by the local Magnitude service\n"
        f"  Endpoint: {connection.endpoint}",
        file=sys.stderr,
        flush=True,
    )

    previous_token = os.environ.get("MAGNITUDE_API_KEY")
    os.environ["MAGNITUDE_API_KEY"] = MAGNITUDE_API_KEY
    try:
        args.config = connection.config_path
        args.workspace = workspace
        args.state_root = state_root
        return _serve(args)
    finally:
        if previous_token is None:
            os.environ.pop("MAGNITUDE_API_KEY", None)
        else:
            os.environ["MAGNITUDE_API_KEY"] = previous_token


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve":
        return _serve(args)
    if args.command == "serve-magnitude":
        return _serve_magnitude(args)
    if args.command in {"steer", "steering-status"}:
        state_root = _steering_state_root(
            repository=args.repository,
            state_root=args.state_root,
        )
        queue = SteeringQueue(state_root / "state.db")
        if args.command == "steer":
            message = queue.enqueue(
                args.task_id,
                args.message,
                priority=args.priority,
                idempotency_key=args.idempotency_key,
            )
            print(
                json.dumps(
                    {
                        "delivery": "next_model_boundary",
                        "message": _steering_record(message, include_content=False),
                        "state_root": state_root.as_posix(),
                    },
                    sort_keys=True,
                    indent=2,
                )
            )
            return 0
        messages = queue.list_messages(args.task_id, limit=args.limit)
        counts = {status: 0 for status in ("queued", "leased", "acked")}
        for message in messages:
            counts[message.status] += 1
        print(
            json.dumps(
                {
                    "counts": counts,
                    "messages": [
                        _steering_record(
                            message,
                            include_content=args.include_content,
                        )
                        for message in messages
                    ],
                    "pending": queue.has_pending(args.task_id),
                    "task_id": args.task_id,
                },
                sort_keys=True,
                indent=2,
            )
        )
        return 0
    if args.command == "trace-export":
        exported = TraceStore(args.state_root.resolve() / "traces.db").export_jsonl(args.task_id)
        if exported:
            print(exported)
        return 0
    if args.command in {"learned-skills", "disable-skill"}:
        registry = LearnedSkillRegistry(args.state_root.resolve() / "learned-skills")
        if args.command == "disable-skill":
            print(registry.disable(args.name).model_dump_json(indent=2))
            return 0
        lifecycles = []
        for root in (
            registry.active_root,
            registry.candidate_root,
            registry.disabled_root,
        ):
            for directory in sorted(root.iterdir(), key=lambda item: item.name):
                if not directory.is_dir():
                    continue
                lifecycle = registry.load(directory.name)
                if lifecycle is not None:
                    lifecycles.append(lifecycle.model_dump(mode="json"))
        print(json.dumps(lifecycles, sort_keys=True, indent=2))
        return 0
    if args.command == "cleanup":
        repository = args.repository.resolve()
        state = (args.state_root or _default_state_root(repository)).resolve()
        GitWorktreeManager(repository, state).remove(args.task_id, force=args.force)
        return 0

    preparation = prepare_run(
        repository=args.repository,
        task_id=args.task_id,
        prompt=args.prompt,
        base_ref=args.base_ref,
        branch=args.branch,
        state_root=args.state_root,
        agents_cli=args.agents_cli,
    )
    if args.command == "prepare" or args.print_command:
        print(preparation.to_json())
        return 0
    return run_prepared(preparation)


if __name__ == "__main__":
    raise SystemExit(main())
