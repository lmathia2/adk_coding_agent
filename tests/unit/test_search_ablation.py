from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from harness.context.prompt import DEFAULT_TOOL_NAMES
from harness.evals.search_ablation import (
    SearchAblationObservation,
    SearchAblationPlan,
    load_search_ablation_plan,
    run_search_ablation,
    score_search_observation,
    search_fixture_content_hash,
)
from harness.evals.skill_ablation import ablation_harness_content_hash

ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = ROOT / "tests" / "eval" / "ablations" / "fff_search.json"
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "search_fanout"


def test_committed_search_ablation_is_a_controlled_four_tool_pair() -> None:
    plan = load_search_ablation_plan(PLAN_PATH)

    assert plan.backends == ("rg", "fff")
    assert plan.model_visible_tools == DEFAULT_TOOL_NAMES
    assert plan.fixture_root == "tests/fixtures/search_fanout"
    assert plan.expected_total_matches == 33
    assert len(plan.relevant_paths) == 3
    assert plan.fff_version == "0.10.5"
    assert plan.fixture_content_hash == search_fixture_content_hash(FIXTURE_ROOT)
    assert plan.harness_content_hash == ablation_harness_content_hash(ROOT)


def test_search_ablation_measures_ranking_pagination_context_and_safety(
    tmp_path: Path,
) -> None:
    if shutil.which("rg") is None:
        pytest.skip("rg is not installed")
    workspace = tmp_path / "workspace"
    shutil.copytree(FIXTURE_ROOT, workspace)
    outside = tmp_path / "outside.ts"
    outside.write_text("// TODO outside workspace\n", encoding="utf-8")
    (workspace / "outside-link.ts").symlink_to(outside)
    (workspace / "ignored").mkdir(exist_ok=True)
    (workspace / "ignored" / "ignored.ts").write_text(
        "// TODO fix ignored content\n", encoding="utf-8"
    )
    (workspace / ".artifacts").mkdir(exist_ok=True)
    (workspace / ".artifacts" / "internal.txt").write_text(
        "TODO fix harness-internal artifact\n", encoding="utf-8"
    )
    (workspace / "binary.bin").write_bytes(b"\x00TODO fix binary decoy\n")
    plan = load_search_ablation_plan(PLAN_PATH)

    report = run_search_ablation(plan, workspace, tmp_path / "state")

    assert report.rg.total_matches == 33
    assert report.fff.total_matches == 33
    assert report.rg.complete_and_safe
    assert report.fff.complete_and_safe
    assert report.rg.pages == 1
    assert report.fff.pages > 1
    assert report.rg.first_window_relevant_path_recall == 0
    assert report.fff.first_window_relevant_path_recall == 1
    assert report.fff.first_window_noise_ratio < report.rg.first_window_noise_ratio
    assert report.fff.reciprocal_relevant_match_rank > report.rg.reciprocal_relevant_match_rank
    assert report.fff.match_ndcg_at_20 > report.rg.match_ndcg_at_20
    assert report.fff.initial_visible_bytes < report.rg.initial_visible_bytes
    assert report.rg.backend_version.startswith("ripgrep ")
    assert report.fff.backend_version == "fff-search/0.10.5"
    assert len(report.rg.ordered_match_hash) == 64
    assert len(report.fff.ordered_match_hash) == 64
    assert report.rg.duration_ms >= 0
    assert report.fff.duration_ms >= 0


def test_search_ablation_flags_duplicates_unexpected_matches_and_unsafe_paths(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("TODO fix\n", encoding="utf-8")
    plan = SearchAblationPlan(
        ablation_id="metric-contract",
        fixture_root="fixture",
        query="TODO fix",
        page_size=20,
        relevant_paths=("src/app.ts",),
        expected_total_matches=1,
        fixture_content_hash="0" * 64,
        harness_content_hash="1" * 64,
    )
    observation = SearchAblationObservation(
        backend="fff",
        matches=(("src/app.ts", 1), ("src/app.ts", 1), ("ignored/leak.ts", 1)),
        first_window=(("src/app.ts", 1),),
        pages=2,
        initial_visible_bytes=20,
        all_pages_visible_bytes=40,
        incomplete=False,
        duration_ms=1,
        backend_version="fff-search/0.10.5",
    )

    metrics = score_search_observation(plan, tmp_path, observation)

    assert metrics.duplicate_matches == 1
    assert metrics.unexpected_matches == 1
    assert metrics.unsafe_paths == ("ignored/leak.ts",)
    assert not metrics.complete_and_safe


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("backends", ["fff", "rg"]),
        ("model_visible_tools", ["read", "bash", "edit", "write", "search"]),
        ("relevant_paths", ["src/app.ts", "../outside.ts"]),
    ],
)
def test_search_ablation_rejects_confounded_or_unsafe_plans(
    field: str,
    value: list[str],
) -> None:
    payload = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    payload[field] = value

    with pytest.raises(ValidationError):
        SearchAblationPlan.model_validate(payload)
