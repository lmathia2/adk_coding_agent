"""Deterministic progress accounting and no-progress routing."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from harness.models.ledger import TaskLedger


class ProgressRoute(StrEnum):
    CONTINUE = "continue"
    REPLAN = "replan"
    NEEDS_INPUT = "needs_input"
    VERIFY = "verify"


def action_fingerprint(
    tool_name: str,
    arguments: dict[str, Any],
    result_hash: str | None = None,
) -> str:
    payload = {
        "tool": tool_name,
        "arguments": arguments,
        "result_hash": result_hash,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def register_action(
    ledger: TaskLedger,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    result_hash: str | None = None,
    history_limit: int = 20,
) -> TaskLedger:
    fingerprint = action_fingerprint(tool_name, arguments, result_hash)
    data = ledger.model_dump(mode="python")
    history = list(data.get("recent_action_fingerprints", []))
    repeated = bool(history and history[-1] == fingerprint)
    data["no_progress_count"] = int(data.get("no_progress_count", 0)) + 1 if repeated else 0
    history.append(fingerprint)
    data["recent_action_fingerprints"] = history[-history_limit:]
    data["iteration"] = int(data.get("iteration", 0)) + 1
    return TaskLedger.model_validate(data)


def route_for_progress(
    ledger: TaskLedger,
    *,
    replan_threshold: int = 2,
    human_threshold: int = 4,
) -> ProgressRoute:
    if ledger.status == "needs_input" or (
        getattr(ledger, "blockers", None) and not getattr(ledger, "next_action", None)
    ):
        return ProgressRoute.NEEDS_INPUT
    if ledger.status == "verifying" or ledger.phase == "verify":
        return ProgressRoute.VERIFY
    if ledger.no_progress_count >= human_threshold:
        return ProgressRoute.NEEDS_INPUT
    if ledger.no_progress_count >= replan_threshold:
        return ProgressRoute.REPLAN
    return ProgressRoute.CONTINUE
