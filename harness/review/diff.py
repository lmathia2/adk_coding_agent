"""Deterministic and secret-redacted final-diff packet construction."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from harness.safety.redaction import SecretRedactor
from harness.tools.output import bound_output

from .models import DiffReviewPacket


def _git(root: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ("git", *args),
        cwd=root,
        check=False,
        capture_output=True,
        timeout=30,
    )
    if completed.returncode != 0:
        diagnostic = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(diagnostic or f"git {' '.join(args)} failed")
    return completed.stdout


def _untracked_paths(root: Path) -> list[str]:
    raw = _git(root, "ls-files", "--others", "--exclude-standard", "-z")
    return sorted(item.decode("utf-8", errors="surrogateescape") for item in raw.split(b"\0") if item)


def _render_untracked(root: Path, relative: str) -> str:
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ValueError(f"untracked path escapes workspace: {relative}") from error
    data = target.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    header = (
        f"diff --git a/{relative} b/{relative}\n"
        "new file mode 100644\n"
        f"untracked-sha256 {digest}\n"
    )
    if b"\0" in data:
        return header + f"Binary file ({len(data)} bytes) omitted\n"
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    added = "\n".join(f"+{line}" for line in text.splitlines())
    return header + "--- /dev/null\n" + f"+++ b/{relative}\n" + added + "\n"


def build_diff_review_packet(
    root: Path,
    base_revision: str,
    *,
    max_chars: int = 60_000,
    max_lines: int = 1_500,
    redactor: SecretRedactor | None = None,
) -> DiffReviewPacket:
    """Build a bounded packet covering tracked, staged, renamed, and untracked changes."""

    workspace = root.resolve()
    tracked = _git(
        workspace,
        "diff",
        "--no-ext-diff",
        "--no-color",
        "--binary",
        base_revision,
        "--",
    ).decode("utf-8", errors="replace")
    untracked_paths = _untracked_paths(workspace)
    untracked = "".join(_render_untracked(workspace, path) for path in untracked_paths)
    complete = tracked + untracked
    digest = hashlib.sha256(complete.encode("utf-8")).hexdigest()
    safe = (redactor or SecretRedactor()).redact_text(complete)
    bounded = bound_output(safe, max_chars=max_chars, max_lines=max_lines)

    names = _git(
        workspace,
        "diff",
        "--name-only",
        "-z",
        base_revision,
        "--",
    )
    tracked_paths = [
        item.decode("utf-8", errors="surrogateescape")
        for item in names.split(b"\0")
        if item
    ]
    return DiffReviewPacket(
        base_revision=base_revision,
        changed_paths=sorted(set([*tracked_paths, *untracked_paths])),
        diff_sha256=digest,
        diff=bounded.text,
        truncated=bounded.truncated,
        omitted_bytes=bounded.omitted_bytes,
    )

