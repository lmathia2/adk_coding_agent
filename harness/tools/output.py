"""Model-facing output normalization, truncation, and spill metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


@dataclass(frozen=True, slots=True)
class BoundedOutput:
    text: str
    truncated: bool
    omitted_bytes: int


def normalize_output(text: str) -> str:
    text = _ANSI_ESCAPE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()
    collapsed: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        run_end = index + 1
        while run_end < len(lines) and lines[run_end] == line:
            run_end += 1
        count = run_end - index
        collapsed.append(line)
        if count > 3:
            collapsed.append(f"... repeated {count - 1} additional times ...")
        elif count > 1:
            collapsed.extend([line] * (count - 1))
        index = run_end
    return "\n".join(collapsed)


def bound_output(text: str, *, max_chars: int = 16_000, max_lines: int = 400) -> BoundedOutput:
    canonical = _ANSI_ESCAPE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalize_output(text)
    summarized_repetition = normalized != canonical.rstrip("\n")
    lines = normalized.splitlines()
    over_lines = len(lines) > max_lines
    over_chars = len(normalized) > max_chars
    if not over_lines and not over_chars:
        omitted = max(0, len(canonical.encode("utf-8")) - len(normalized.encode("utf-8")))
        return BoundedOutput(normalized, summarized_repetition, omitted)

    line_limited = lines
    if over_lines:
        head_count = max_lines * 2 // 3
        tail_count = max_lines - head_count
        line_limited = [
            *lines[:head_count],
            "... [output lines omitted] ...",
            *lines[-tail_count:],
        ]
    candidate = "\n".join(line_limited)
    if len(candidate) > max_chars:
        marker = "\n... [output characters omitted] ...\n"
        available = max(0, max_chars - len(marker))
        head = available * 2 // 3
        tail = available - head
        candidate = candidate[:head] + marker + (candidate[-tail:] if tail else "")
    omitted = max(0, len(normalized.encode("utf-8")) - len(candidate.encode("utf-8")))
    return BoundedOutput(candidate, True, omitted)
