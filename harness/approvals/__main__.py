"""Review durable command approvals with ``python -m harness.approvals``."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from .interactive import InteractiveApprovalTransport
from .store import ApprovalStore


def _database(value: Path | None) -> Path:
    if value is not None:
        return value.expanduser().resolve()
    state = os.getenv("ADK_CODING_STATE_DIR")
    if not state:
        raise SystemExit(
            "set ADK_CODING_STATE_DIR or pass --database to locate approvals"
        )
    return Path(state).expanduser().resolve() / "approvals.db"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m harness.approvals",
        description="List and decide exact command approval requests.",
    )
    parser.add_argument("--database", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_command = subparsers.add_parser("list")
    list_command.add_argument("--task-id")
    list_command.add_argument(
        "--status",
        choices=["pending", "approved", "denied", "expired"],
    )
    list_command.add_argument("--limit", type=int, default=100)

    show = subparsers.add_parser("show")
    show.add_argument("request_id")

    review = subparsers.add_parser("review")
    review.add_argument("request_id", nargs="?")
    review.add_argument("--task-id")
    review.add_argument("--actor", required=True)

    for decision in ("approve", "deny"):
        command = subparsers.add_parser(decision)
        command.add_argument("request_id")
        command.add_argument("--actor", required=True)
        command.add_argument("--note")
    return parser


def _print(value: object) -> None:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    elif isinstance(value, list):
        value = [
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for item in value
        ]
    print(json.dumps(value, sort_keys=True, indent=2))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    store = ApprovalStore(_database(args.database))
    if args.command == "list":
        _print(
            store.list(
                task_id=args.task_id,
                status=args.status,
                limit=max(args.limit, 1),
            )
        )
        return 0
    if args.command == "show":
        request = store.get(args.request_id)
        if request is None:
            raise SystemExit(f"unknown approval request: {args.request_id}")
        _print(request)
        return 0
    if args.command == "review":
        transport = InteractiveApprovalTransport(store)
        request = (
            transport.review(args.request_id, actor=args.actor)
            if args.request_id
            else transport.review_next(actor=args.actor, task_id=args.task_id)
        )
        if request is None:
            raise SystemExit("no pending approval requests")
        _print(request)
        return 0

    decision = "approved" if args.command == "approve" else "denied"
    _print(
        store.decide(
            args.request_id,
            decision=decision,
            actor=args.actor,
            note=args.note,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
