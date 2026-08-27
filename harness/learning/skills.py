"""Conservative skill synthesis and atomic Agent Skills-compatible publishing."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from .models import RepeatedActionSequence, SkillDraft, SkillLifecycle
from .trials import PromotionDecision


class SkillSynthesizer(Protocol):
    def synthesize(
        self,
        *,
        workflow_kind: str,
        sequence: RepeatedActionSequence,
    ) -> SkillDraft: ...


class HeuristicSkillSynthesizer:
    """Create a narrow draft from normalized labels without model calls."""

    def synthesize(
        self,
        *,
        workflow_kind: str,
        sequence: RepeatedActionSequence,
    ) -> SkillDraft:
        canonical = json.dumps(
            {
                "workflow_kind": workflow_kind,
                "tokens": sequence.tokens,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        suffix = hashlib.sha256(canonical.encode()).hexdigest()[:12]
        safe_kind = workflow_kind.replace("_", "-").replace(".", "-")[:40]
        name = f"learned-{safe_kind}-{suffix}"[:64].rstrip("-")
        description = (
            f"Candidate {workflow_kind} workflow observed in "
            f"{sequence.support} independently verified traces."
        )
        steps = []
        for index, token in enumerate(sequence.tokens, start=1):
            action, category, outcome = token.split(":", 2)
            steps.append(
                f"{index}. Perform the normalized `{action}` action for "
                f"`{category}` work and require a `{outcome}` result."
            )
        instructions = "\n".join(
            [
                "Use this workflow only when its description matches the task.",
                "",
                *steps,
                "",
                "Stop on blockers or security-policy concerns. Verify completion ",
                "deterministically before reporting success.",
            ]
        )
        return SkillDraft(
            name=name,
            description=description,
            instructions=instructions,
            source_trace_ids=sequence.source_trace_ids,
        )


PublishHook = Callable[[Path], None]


def _skill_markdown(draft: SkillDraft) -> str:
    description = json.dumps(draft.description, ensure_ascii=False)
    trace_ids = json.dumps(list(draft.source_trace_ids), ensure_ascii=False)
    return (
        "---\n"
        f"name: {draft.name}\n"
        f"description: {description}\n"
        "metadata:\n"
        "  status: candidate\n"
        "  version: 1\n"
        f"  source_trace_ids: {trace_ids}\n"
        "---\n\n"
        f"# {draft.name}\n\n"
        f"{draft.instructions.rstrip()}\n"
    )


def _atomic_text(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    with temporary.open("rb") as stream:
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class SkillRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.active_root = self.root / "active"
        self.candidate_root = self.root / "candidates"
        self.disabled_root = self.root / "disabled"
        for directory in (
            self.active_root,
            self.candidate_root,
            self.disabled_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.root / ".lifecycle.lock"
        self._lock_path.touch(exist_ok=True)

    @contextmanager
    def _lifecycle_lock(self):
        with self._lock_path.open("rb") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _validate_name(self, name: str) -> None:
        SkillLifecycle(
            name=name,
            description="validation",
            status="candidate",
            version=1,
            source_trace_ids=(),
        )

    def _root_for_status(self, status: str) -> Path:
        return {
            "enabled": self.active_root,
            "candidate": self.candidate_root,
            "disabled": self.disabled_root,
        }[status]

    def _find_directory(self, name: str) -> Path | None:
        self._validate_name(name)
        matches = [
            root / name
            for root in (self.active_root, self.candidate_root, self.disabled_root)
            if (root / name).is_dir()
        ]
        if len(matches) > 1:
            raise ValueError("skill exists in multiple lifecycle roots")
        return matches[0] if matches else None

    def load(self, name: str) -> SkillLifecycle | None:
        directory = self._find_directory(name)
        if directory is None:
            return None
        path = directory / "lifecycle.json"
        lifecycle = SkillLifecycle.model_validate_json(path.read_text(encoding="utf-8"))
        status_by_root = {
            self.active_root: "enabled",
            self.candidate_root: "candidate",
            self.disabled_root: "disabled",
        }
        canonical_status = status_by_root[directory.parent]
        if lifecycle.status != canonical_status:
            lifecycle = lifecycle.model_copy(update={"status": canonical_status})
        return lifecycle

    def content_hash(self, name: str) -> str:
        directory = self._find_directory(name)
        if directory is None:
            raise KeyError(name)
        return hashlib.sha256((directory / "SKILL.md").read_bytes()).hexdigest()

    def emit_candidate(
        self,
        draft: SkillDraft,
        *,
        before_publish: PublishHook | None = None,
    ) -> SkillLifecycle:
        lifecycle = SkillLifecycle(
            name=draft.name,
            description=draft.description,
            status="candidate",
            version=1,
            source_trace_ids=tuple(sorted(set(draft.source_trace_ids))),
        )
        destination = self.candidate_root / draft.name
        with self._lifecycle_lock():
            existing = self.load(draft.name)
            if existing is not None:
                if (
                    existing.name != lifecycle.name
                    or existing.description != lifecycle.description
                    or existing.version != lifecycle.version
                    or existing.source_trace_ids != lifecycle.source_trace_ids
                ):
                    raise ValueError(
                        "skill name already exists with different provenance"
                    )
                return existing

            temporary = Path(
                tempfile.mkdtemp(prefix=f".{draft.name}-", dir=self.root)
            )
            try:
                (temporary / "SKILL.md").write_text(
                    _skill_markdown(draft),
                    encoding="utf-8",
                )
                (temporary / "lifecycle.json").write_text(
                    json.dumps(
                        lifecycle.model_dump(mode="json"),
                        sort_keys=True,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                for path in (
                    temporary / "SKILL.md",
                    temporary / "lifecycle.json",
                ):
                    with path.open("rb") as stream:
                        os.fsync(stream.fileno())
                _fsync_directory(temporary)
                if before_publish is not None:
                    before_publish(temporary)
                os.replace(temporary, destination)
                _fsync_directory(self.candidate_root)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        return lifecycle

    @staticmethod
    def _update_manifest_status(directory: Path, status: str) -> None:
        path = directory / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        for previous in ("enabled", "candidate", "disabled"):
            marker = f"  status: {previous}\n"
            if marker in text:
                _atomic_text(path, text.replace(marker, f"  status: {status}\n", 1))
                return
        raise ValueError("generated SKILL.md is missing lifecycle metadata")

    def _set_status(
        self,
        name: str,
        status: str,
    ) -> SkillLifecycle:
        with self._lifecycle_lock():
            current = self.load(name)
            if current is None:
                raise KeyError(name)
            if current.status == status:
                return current
            updated = SkillLifecycle.model_validate(
                {**current.model_dump(mode="python"), "status": status}
            )
            source = self._find_directory(name)
            assert source is not None
            destination = self._root_for_status(status) / name

            # Prepare the complete destination contents before the atomic rename.
            # Lifecycle behavior is derived from the parent directory, so a crash
            # before the rename leaves the old lifecycle safely authoritative.
            self._update_manifest_status(source, status)
            _atomic_text(
                source / "lifecycle.json",
                json.dumps(
                    updated.model_dump(mode="json"),
                    sort_keys=True,
                    indent=2,
                )
                + "\n",
            )
            os.replace(source, destination)
            _fsync_directory(source.parent)
            if destination.parent != source.parent:
                _fsync_directory(destination.parent)
            return updated

    def promote(
        self,
        name: str,
        decision: PromotionDecision,
    ) -> SkillLifecycle:
        if not decision.promote:
            raise ValueError("candidate did not pass promotion gates")
        return self._set_status(name, "enabled")

    def disable(self, name: str) -> SkillLifecycle:
        return self._set_status(name, "disabled")

    def rollback(self, name: str) -> SkillLifecycle:
        """Remove a learned skill from active use after quality failures."""

        return self.disable(name)


__all__ = [
    "HeuristicSkillSynthesizer",
    "SkillRegistry",
    "SkillSynthesizer",
]
