"""Deterministic paired evaluation for bounded FFF search and raw ripgrep."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from harness.context.prompt import DEFAULT_TOOL_NAMES
from harness.models.base import StrictModel
from harness.repo.fff_search import FffSearchService

SearchAblationBackend = Literal["rg", "fff"]
_MATCH_LINE = re.compile(r"^  (?P<line>[1-9][0-9]*):")
_EXCLUDED_PREFIXES = (".artifacts/", ".git/", "ignored/")
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class SearchAblationPlan(StrictModel):
    """Pinned fixture and query shared by both search variants."""

    ablation_id: str = Field(min_length=1)
    fixture_root: str = Field(min_length=1)
    query: str = Field(min_length=1)
    page_size: int = Field(ge=1, le=50)
    relevant_paths: tuple[str, ...] = Field(min_length=1)
    expected_total_matches: int = Field(ge=1)
    fixture_content_hash: str = Field(pattern=_SHA256_PATTERN)
    harness_content_hash: str = Field(pattern=_SHA256_PATTERN)
    fff_version: Literal["0.10.5"] = "0.10.5"
    backends: tuple[SearchAblationBackend, ...] = ("rg", "fff")
    model_visible_tools: tuple[str, ...] = DEFAULT_TOOL_NAMES

    @model_validator(mode="after")
    def validate_pair(self) -> SearchAblationPlan:
        if self.backends != ("rg", "fff"):
            raise ValueError("search ablation must retain the exact rg/fff pair")
        if self.model_visible_tools != tuple(DEFAULT_TOOL_NAMES):
            raise ValueError("search ablation must preserve the four-tool surface")
        if len(set(self.relevant_paths)) != len(self.relevant_paths):
            raise ValueError("relevant paths must be unique")
        fixture = Path(self.fixture_root)
        if fixture.is_absolute() or ".." in fixture.parts:
            raise ValueError("fixture root must be repository-relative")
        for path in self.relevant_paths:
            candidate = Path(path)
            if (
                candidate.is_absolute()
                or ".." in candidate.parts
                or path.startswith(_EXCLUDED_PREFIXES)
            ):
                raise ValueError("relevant paths must remain inside the fixture")
        return self


class SearchAblationMetrics(StrictModel):
    """Search-quality, context-cost, pagination, and safety observations."""

    backend: SearchAblationBackend
    total_matches: int = Field(ge=0)
    unique_matches: int = Field(ge=0)
    duplicate_matches: int = Field(ge=0)
    missing_matches: int = Field(ge=0)
    unexpected_matches: int = Field(ge=0)
    pages: int = Field(ge=1)
    initial_visible_bytes: int = Field(ge=0)
    all_pages_visible_bytes: int = Field(ge=0)
    visible_bytes_per_relevant_path: float = Field(ge=0)
    duration_ms: int = Field(ge=0)
    backend_version: str = Field(min_length=1)
    ordered_match_hash: str = Field(pattern=_SHA256_PATTERN)
    first_window_relevant_path_recall: float = Field(ge=0, le=1)
    first_window_noise_ratio: float = Field(ge=0, le=1)
    reciprocal_relevant_match_rank: float = Field(ge=0, le=1)
    match_ndcg_at_20: float = Field(ge=0, le=1)
    unsafe_paths: tuple[str, ...] = ()
    incomplete: bool = False

    @property
    def complete_and_safe(self) -> bool:
        return (
            not self.incomplete
            and self.duplicate_matches == 0
            and self.missing_matches == 0
            and self.unexpected_matches == 0
            and not self.unsafe_paths
        )


class SearchAblationReport(StrictModel):
    """Paired results without a nondeterministic latency winner."""

    ablation_id: str
    rg: SearchAblationMetrics
    fff: SearchAblationMetrics

    @model_validator(mode="after")
    def validate_backends(self) -> SearchAblationReport:
        if self.rg.backend != "rg" or self.fff.backend != "fff":
            raise ValueError("report sides must match their named backends")
        return self


@dataclass(frozen=True, slots=True)
class SearchAblationObservation:
    backend: SearchAblationBackend
    matches: tuple[tuple[str, int], ...]
    first_window: tuple[tuple[str, int], ...]
    pages: int
    initial_visible_bytes: int
    all_pages_visible_bytes: int
    incomplete: bool
    duration_ms: int
    backend_version: str


def load_search_ablation_plan(path: Path) -> SearchAblationPlan:
    """Load and validate a committed search-ablation contract."""

    return SearchAblationPlan.model_validate_json(path.read_text(encoding="utf-8"))


def search_fixture_content_hash(root: Path) -> str:
    """Hash fixture paths and bytes in stable order for reproducible comparisons."""

    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _canonical_path(value: str) -> str:
    path = value.removeprefix("./")
    candidate = Path(path)
    if not path or candidate.is_absolute() or ".." in candidate.parts:
        return f"<unsafe>/{path}"
    return candidate.as_posix()


def _unsafe_paths(paths: set[str], workspace: Path) -> tuple[str, ...]:
    unsafe: set[str] = set()
    root = workspace.resolve()
    for path in paths:
        if path.startswith("<unsafe>/") or path.startswith(_EXCLUDED_PREFIXES):
            unsafe.add(path)
            continue
        target = workspace / path
        try:
            resolved = target.resolve(strict=True)
            resolved.relative_to(root)
        except (FileNotFoundError, OSError, ValueError):
            unsafe.add(path)
    return tuple(sorted(unsafe))


def _ranking_metrics(
    matches: tuple[tuple[str, int], ...],
    first_window: tuple[tuple[str, int], ...],
    relevant_paths: set[str],
) -> tuple[float, float, float, float]:
    visible_paths = {path for path, _ in first_window if path in relevant_paths}
    recall = len(visible_paths) / len(relevant_paths)
    noise = (
        sum(path not in relevant_paths for path, _ in first_window) / len(first_window)
        if first_window
        else 0.0
    )
    first_rank = next(
        (index for index, (path, _) in enumerate(matches, start=1) if path in relevant_paths),
        None,
    )
    reciprocal_rank = 1 / first_rank if first_rank is not None else 0.0
    gains = [1.0 if path in relevant_paths else 0.0 for path, _ in first_window[:20]]
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal_relevant = min(20, sum(path in relevant_paths for path, _ in matches))
    ideal = sum(1 / math.log2(index + 2) for index in range(ideal_relevant))
    ndcg = dcg / ideal if ideal else 0.0
    return recall, noise, reciprocal_rank, ndcg


def score_search_observation(
    plan: SearchAblationPlan,
    workspace: Path,
    observation: SearchAblationObservation,
) -> SearchAblationMetrics:
    """Reduce raw match order into deterministic quality and safety metrics."""

    match_ids = tuple(f"{path}:{line}" for path, line in observation.matches)
    unique_matches = len(set(match_ids))
    relevant = set(plan.relevant_paths)
    recall, noise, reciprocal_rank, ndcg = _ranking_metrics(
        observation.matches,
        observation.first_window,
        relevant,
    )
    return SearchAblationMetrics(
        backend=observation.backend,
        total_matches=len(match_ids),
        unique_matches=unique_matches,
        duplicate_matches=len(match_ids) - unique_matches,
        missing_matches=max(0, plan.expected_total_matches - unique_matches),
        unexpected_matches=max(0, unique_matches - plan.expected_total_matches),
        pages=observation.pages,
        initial_visible_bytes=observation.initial_visible_bytes,
        all_pages_visible_bytes=observation.all_pages_visible_bytes,
        visible_bytes_per_relevant_path=(
            observation.initial_visible_bytes / len(relevant) if relevant else 0.0
        ),
        duration_ms=observation.duration_ms,
        backend_version=observation.backend_version,
        ordered_match_hash=hashlib.sha256("\n".join(match_ids).encode()).hexdigest(),
        first_window_relevant_path_recall=recall,
        first_window_noise_ratio=noise,
        reciprocal_relevant_match_rank=reciprocal_rank,
        match_ndcg_at_20=ndcg,
        unsafe_paths=_unsafe_paths({path for path, _ in observation.matches}, workspace),
        incomplete=observation.incomplete,
    )


def _run_rg(plan: SearchAblationPlan, workspace: Path) -> SearchAblationObservation:
    executable = shutil.which("rg")
    if executable is None:
        raise RuntimeError("rg is required for the baseline side of the search ablation")
    version = subprocess.run(
        [executable, "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.splitlines()[0]
    command = [
        executable,
        "--json",
        "--sort",
        "path",
        "--hidden",
        "--glob",
        "!.git/**",
        "--glob",
        "!.artifacts/**",
        "--glob",
        "!ignored/**",
        "--fixed-strings",
        "--smart-case",
        "--",
        plan.query,
        ".",
    ]
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=workspace,
        check=False,
        capture_output=True,
        timeout=10,
    )
    if completed.returncode not in {0, 1}:
        detail = completed.stderr.decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"rg search failed with exit {completed.returncode}: {detail}")
    matches: list[tuple[str, int]] = []
    for raw_line in completed.stdout.splitlines():
        payload = json.loads(raw_line)
        if payload.get("type") != "match":
            continue
        data = payload["data"]
        path = _canonical_path(str(data["path"]["text"]))
        line = int(data["line_number"])
        matches.extend((path, line) for _ in data.get("submatches", [None]))
    ordered = tuple(matches)
    return SearchAblationObservation(
        backend="rg",
        matches=ordered,
        first_window=ordered[: plan.page_size],
        pages=1,
        initial_visible_bytes=len(completed.stdout),
        all_pages_visible_bytes=len(completed.stdout),
        incomplete=False,
        duration_ms=int((time.monotonic() - started) * 1_000),
        backend_version=version,
    )


def _parse_fff_page(text: str) -> tuple[tuple[str, int], ...]:
    matches: list[tuple[str, int]] = []
    current_path: str | None = None
    for line in text.splitlines():
        match = _MATCH_LINE.match(line)
        if match is not None and current_path is not None:
            matches.append((current_path, int(match.group("line"))))
        elif line and not line.startswith((" ", "[", "FFF ")):
            current_path = _canonical_path(line.split(" [git:", maxsplit=1)[0])
    return tuple(matches)


def _run_fff(
    plan: SearchAblationPlan,
    workspace: Path,
    state_root: Path,
) -> SearchAblationObservation:
    started = time.monotonic()
    service = FffSearchService(workspace, state_root)
    page_matches: list[tuple[tuple[str, int], ...]] = []
    page_sizes: list[int] = []
    incomplete = False
    try:
        page = service.grep(pattern=plan.query, limit=plan.page_size)
        while True:
            page_matches.append(_parse_fff_page(page.text))
            page_sizes.append(len(page.text.encode("utf-8")))
            incomplete = incomplete or page.incomplete
            if page.cursor is None:
                break
            if len(page_matches) >= 1_000:
                raise RuntimeError("FFF ablation exceeded the pagination safety bound")
            page = service.grep(cursor=page.cursor)
    finally:
        service.close()
    return SearchAblationObservation(
        backend="fff",
        matches=tuple(item for page_items in page_matches for item in page_items),
        first_window=page_matches[0],
        pages=len(page_matches),
        initial_visible_bytes=page_sizes[0],
        all_pages_visible_bytes=sum(page_sizes),
        incomplete=incomplete,
        duration_ms=int((time.monotonic() - started) * 1_000),
        backend_version=f"fff-search/{plan.fff_version}",
    )


def run_search_ablation(
    plan: SearchAblationPlan,
    workspace: Path,
    state_root: Path,
) -> SearchAblationReport:
    """Run the no-model, no-credential paired fixture evaluation."""

    workspace = workspace.resolve(strict=True)
    rg = score_search_observation(plan, workspace, _run_rg(plan, workspace))
    fff = score_search_observation(plan, workspace, _run_fff(plan, workspace, state_root))
    return SearchAblationReport(ablation_id=plan.ablation_id, rg=rg, fff=fff)


__all__ = [
    "SearchAblationBackend",
    "SearchAblationMetrics",
    "SearchAblationObservation",
    "SearchAblationPlan",
    "SearchAblationReport",
    "load_search_ablation_plan",
    "run_search_ablation",
    "score_search_observation",
    "search_fixture_content_hash",
]
