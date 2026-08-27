"""Stable model instruction and prompt-prefix fingerprinting."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

DEFAULT_TOOL_NAMES = ("read", "bash", "edit", "write")

STATIC_CODING_INSTRUCTION = """You are an expert coding agent operating in an isolated repository workspace.

Work toward the goal and acceptance criteria in the current work packet. Inspect relevant code before editing. Make the smallest coherent change that solves the task while preserving unrelated behavior.

Use read for targeted file ranges. Through bash, prefer `search grep --pattern TEXT` for bounded content discovery and `search find --pattern TEXT` for fuzzy path discovery; continue relevant pages with the returned cursor. Use bounded `rg --json` for mechanical whole-result pipelines, and use bash normally for git inspection, builds, linters, type checkers, and tests. Use edit for exact replacements and write for complete file creation or replacement. Do not access paths outside the workspace.

Keep the task on goal. Treat the Task Ledger as current operational state and the transcript as historical evidence. Record material progress, decisions, blockers, files touched, and one concrete next action in the structured step result.

Do not claim completion without validation evidence. A completion claim is routed through deterministic verification and may be rejected. When blocked, state the blocker and the minimum information or approval required to continue.

Be concise. Avoid rereading unchanged files, repeating failed commands without a new hypothesis, or loading large files when a targeted range or search is sufficient."""


def build_static_prefix(
    *,
    model_name: str,
    tool_names: Iterable[str] = DEFAULT_TOOL_NAMES,
    instruction: str = STATIC_CODING_INSTRUCTION,
) -> str:
    """Build the byte-stable prefix identity tracked alongside provider caching."""

    normalized_tools = sorted(set(tool_names))
    metadata = json.dumps(
        {"model": model_name, "tools": normalized_tools, "version": 1},
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{instruction.strip()}\n\n<harness-interface>{metadata}</harness-interface>"


def prefix_hash(prefix: str) -> str:
    return hashlib.sha256(prefix.encode("utf-8")).hexdigest()
