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


def _default_shared_state_root() -> Path:
    return Path.home() / ".local" / "state" / "adk-coding-agent"


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

    ledger_backfill = subparsers.add_parser(
        "ledger-backfill",
        help="Idempotently import recognized local stores and audit source counts",
    )
    ledger_backfill.add_argument("--state-root", type=Path, required=True)
    ledger_backfill.add_argument("--database", type=Path)

    def add_steering_target(command: argparse.ArgumentParser) -> None:
        target = command.add_mutually_exclusive_group(required=True)
        target.add_argument("--repository", type=Path)
        target.add_argument("--state-root", type=Path)
        command.add_argument("--task-id", required=True)

    # Minimal command for simple connectivity checks.
    subparsers.add_parser(
        "hello",
        help="Print a friendly greeting",
    )

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

    codex_serve = subparsers.add_parser(
        "serve-codex",
        help="Serve the harness with a ChatGPT subscription Codex model",
    )
    codex_serve.add_argument("--workspace", type=Path, default=Path.cwd())
    codex_serve.add_argument("--state-root", type=Path)
    codex_serve.add_argument("--model", help="Override the saved Codex model selection")
    codex_serve.add_argument(
        "--reasoning",
        choices=("none", "minimal", "low", "medium", "high", "xhigh", "max"),
        help="Override the saved reasoning effort",
    )
    codex_serve.add_argument("--client-version")
    codex_serve.add_argument("--production", action="store_true")
    codex_serve.add_argument("--trust-project", action="store_true")
    codex_serve.add_argument(
        "--notebook-ptc",
        action="store_true",
        help="Expose the local-only persistent Python tool instead of four coding tools",
    )
    codex_serve.add_argument("--print-config", action="store_true")

    codex = subparsers.add_parser(
        "codex",
        help="Manage ChatGPT subscription authentication and model selection",
    )
    codex.add_argument("--state-root", type=Path, default=_default_shared_state_root())
    codex_commands = codex.add_subparsers(dest="codex_command", required=True)
    login = codex_commands.add_parser("login", help="Login with a ChatGPT subscription")
    login.add_argument("--no-browser", action="store_true")
    login.add_argument(
        "--jsonl",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    codex_commands.add_parser("status", help="Show redacted subscription status")
    codex_commands.add_parser("logout", help="Remove the stored subscription credential")
    codex_commands.add_parser("models", help="List models enabled for this account")
    select = codex_commands.add_parser(
        "select",
        help="Save an account-enabled model for the next server start",
    )
    select.add_argument("model")
    select.add_argument(
        "--reasoning",
        choices=("none", "minimal", "low", "medium", "high", "xhigh", "max"),
        default="low",
    )
    benchmark = codex_commands.add_parser(
        "benchmark",
        help="Measure enabled low-latency models and save the fastest selection",
    )
    benchmark.add_argument("--model", action="append", dest="models")
    benchmark.add_argument("--runs", type=int, choices=range(1, 6), default=2)
    benchmark.add_argument(
        "--reasoning",
        choices=("none", "minimal", "low", "medium", "high"),
        default="low",
    )
    benchmark.add_argument("--limit", type=int, choices=range(1, 11), default=6)
    benchmark.add_argument("--no-save", action="store_true")
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
                    "coding_model": (
                        assembly.coordinator.coding_model_status.model_dump(mode="json")
                        if assembly.coordinator.coding_model_status is not None
                        else None
                    ),
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


def _serve_codex(args: argparse.Namespace) -> int:
    from harness.codex import prepare_codex_config

    workspace = args.workspace.expanduser().resolve()
    state_root = (
        args.state_root.expanduser().resolve()
        if args.state_root is not None
        else _default_shared_state_root().resolve()
    )
    config_path, selection = prepare_codex_config(
        state_root,
        model=args.model,
        reasoning=args.reasoning,
        client_version=args.client_version,
        notebook_ptc=args.notebook_ptc,
    )
    print(
        "ChatGPT subscription coding model:\n"
        f"  Model: {selection.model}\n"
        f"  Reasoning: {selection.reasoning}\n"
        "  Authentication: resolved lazily from private harness state\n"
        f"  Credential: {state_root / 'auth' / 'openai-codex.json'}",
        file=sys.stderr,
        flush=True,
    )
    args.config = config_path
    args.workspace = workspace
    args.state_root = state_root
    return _serve(args)


def _codex_command(args: argparse.Namespace) -> int:
    import time

    import httpx

    from harness.ai.codex_auth import (
        CodexAuthenticationError,
        CodexCredentialStore,
        CodexOAuthClient,
    )
    from harness.codex import (
        CodexModelError,
        CodexSelection,
        benchmark_codex_models,
        credential_manager,
        discover_codex_models,
        fastest_candidates,
        load_codex_selection,
        save_codex_selection,
    )

    state_root = args.state_root.expanduser().resolve()
    store = CodexCredentialStore(state_root)
    try:
        if args.codex_command == "login":
            oauth = CodexOAuthClient()
            authorization = oauth.start_device_authorization()
            if args.jsonl:
                print(
                    json.dumps(
                        {
                            "type": "device_code",
                            "user_code": authorization.user_code,
                            "verification_url": authorization.verification_url,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
            else:
                print(
                    "Open this URL and enter the code:\n"
                    f"  {authorization.verification_url}\n"
                    f"  Code: {authorization.user_code}\n\n"
                    "Waiting for authorization (Ctrl+C to cancel)...",
                    flush=True,
                )
            if not args.no_browser:
                import webbrowser

                webbrowser.open(authorization.verification_url)
            credential = oauth.complete_device_authorization(authorization)
            with store.locked():
                store.save(credential)
            payload = {
                "credential_path": store.path.as_posix(),
                "provider": "openai_codex",
                "status": "authenticated",
            }
            if args.jsonl:
                print(
                    json.dumps(
                        {
                            "provider": "openai_codex",
                            "status": "authenticated",
                            "type": "authenticated",
                        },
                        sort_keys=True,
                    )
                )
            else:
                print(json.dumps(payload, sort_keys=True, indent=2))
            return 0
        if args.codex_command == "status":
            credential = store.load()
            payload = (
                credential.public_status(now_ms=int(time.time() * 1000))
                if credential is not None
                else {"authenticated": False}
            )
            print(json.dumps({"provider": "openai_codex", **payload}, sort_keys=True, indent=2))
            return 0
        if args.codex_command == "logout":
            with store.locked():
                removed = store.delete()
            print(json.dumps({"provider": "openai_codex", "removed": removed}, sort_keys=True))
            return 0

        manager = credential_manager(state_root)
        catalog = discover_codex_models(manager)
        if args.codex_command == "models":
            saved = load_codex_selection(state_root)
            print(
                json.dumps(
                    {
                        "models": [
                            {
                                "client_version": model.client_version,
                                "display_name": model.display_name,
                                "id": model.id,
                            }
                            for model in catalog
                        ],
                        "provider": "openai_codex",
                        "selected_model": saved.model if saved is not None else None,
                    },
                    sort_keys=True,
                    indent=2,
                )
            )
            return 0

        if args.codex_command == "select":
            selected = next((model for model in catalog if model.id == args.model), None)
            if selected is None:
                raise CodexModelError(f"model is not enabled for this account: {args.model}")
            path = save_codex_selection(
                state_root,
                CodexSelection(
                    model=selected.id,
                    reasoning=args.reasoning,
                    client_version=selected.client_version,
                ),
            )
            print(
                json.dumps(
                    {
                        "model": selected.id,
                        "reasoning": args.reasoning,
                        "restart_required": True,
                        "selection_path": path.as_posix(),
                    },
                    sort_keys=True,
                    indent=2,
                )
            )
            return 0

        by_id = {model.id: model for model in catalog}
        if args.models:
            unknown = sorted(set(args.models) - set(by_id))
            if unknown:
                raise CodexModelError(
                    "models are not enabled for this account: " + ", ".join(unknown)
                )
            candidates = tuple(by_id[model_id] for model_id in dict.fromkeys(args.models))
        else:
            candidates = fastest_candidates(catalog, limit=args.limit)
        results = benchmark_codex_models(
            manager,
            candidates,
            reasoning=args.reasoning,
            runs=args.runs,
        )
        winner = next((result for result in results if result.successful_runs == args.runs), None)
        saved_path: str | None = None
        if winner is not None and not args.no_save:
            saved_path = save_codex_selection(
                state_root,
                CodexSelection(
                    model=winner.model,
                    reasoning=args.reasoning,
                    client_version=winner.client_version,
                ),
            ).as_posix()
        print(
            json.dumps(
                {
                    "criterion": "median_time_to_first_token_ms",
                    "results": [asdict(result) for result in results],
                    "runs_per_model": args.runs,
                    "selection_path": saved_path,
                    "winner": winner.model if winner is not None else None,
                },
                sort_keys=True,
                indent=2,
            )
        )
        return 0 if winner is not None else 1
    except (CodexAuthenticationError, CodexModelError, httpx.HTTPError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Codex operation cancelled.", file=sys.stderr)
        return 130


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "serve":
        return _serve(args)
    if args.command == "serve-codex":
        return _serve_codex(args)
    if args.command == "codex":
        return _codex_command(args)
    if args.command == "hello":
        print(json.dumps({"message": "hello"}))
        return 0
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
    if args.command == "ledger-backfill":
        from harness.ledger.backfill import audit_backfill
        from harness.ledger.store import DuckDbLedgerStore

        state_root = args.state_root.expanduser().resolve()
        database = (
            args.database.expanduser().resolve()
            if args.database is not None
            else state_root / "ledger.duckdb"
        )
        audit = audit_backfill(state_root, DuckDbLedgerStore(database))
        print(audit.model_dump_json(indent=2))
        return 0 if audit.matched else 1
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
