"""Local launcher that couples an ADK session to an isolated Git worktree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
