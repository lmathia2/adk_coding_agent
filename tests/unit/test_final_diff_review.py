from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from harness.review import (
    ReviewAblationSample,
    build_diff_review_packet,
    compare_reviewer_ablation,
)
from harness.review.__main__ import main


def _run(root: Path, *args: str) -> None:
    subprocess.run(args, cwd=root, check=True, capture_output=True)


def _repository(root: Path) -> str:
    root.mkdir()
    _run(root, "git", "init", "-q")
    _run(root, "git", "config", "user.email", "test@example.com")
    _run(root, "git", "config", "user.name", "Test")
    (root / "tracked.py").write_text("value = 1\n", encoding="utf-8")
    _run(root, "git", "add", ".")
    _run(root, "git", "commit", "-qm", "initial")
    return subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_review_packet_includes_untracked_content_and_redacts_secrets(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    base = _repository(root)
    (root / "tracked.py").write_text("value = 2\n", encoding="utf-8")
    (root / "new.txt").write_text(
        "api_key=super-secret-value-12345\n",
        encoding="utf-8",
    )

    first = build_diff_review_packet(root, base)
    second = build_diff_review_packet(root, base)

    assert first == second
    assert first.changed_paths == ["new.txt", "tracked.py"]
    assert "value = 2" in first.diff
    assert "new.txt" in first.diff
    assert "super-secret-value-12345" not in first.diff
    assert "<redacted>" in first.diff


def test_review_packet_bounds_large_diffs(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    base = _repository(root)
    (root / "large.txt").write_text("line\n" * 2_000, encoding="utf-8")

    packet = build_diff_review_packet(root, base, max_chars=800, max_lines=40)

    assert packet.truncated
    assert packet.omitted_bytes > 0
    assert len(packet.diff) <= 800


def _sample(variant: str, **overrides: object) -> ReviewAblationSample:
    values: dict[str, object] = {
        "variant": variant,
        "case_id": "case-1",
        "harness_revision": "abc123",
        "model": "gemini-test",
        "reasoning": "medium",
        "passed": True,
        "cost_usd": 1.0 if variant == "baseline" else 1.2,
        "uncached_input_tokens": 1_000 if variant == "baseline" else 1_200,
        "cache_read_ratio": 0.5,
        "prefix_versions": 1 if variant == "baseline" else 2,
        "tool_calls": 10,
        "wall_time_ms": 2_000 if variant == "baseline" else 2_300,
    }
    values.update(overrides)
    return ReviewAblationSample.model_validate(values)


def test_reviewer_ablation_reports_required_paired_metrics() -> None:
    report = compare_reviewer_ablation([_sample("reviewer"), _sample("baseline")])

    assert report.case_ids == ["case-1"]
    assert report.pass_rate_delta == 0
    assert report.cost_per_passed_task_delta == pytest.approx(0.2)
    assert report.uncached_input_tokens_delta == 200
    assert report.prefix_versions_delta == 1
    assert report.tool_calls_delta == 0
    assert report.wall_time_ms_delta == 300


def test_reviewer_ablation_rejects_missing_or_confounded_pairs() -> None:
    with pytest.raises(ValueError, match="both ablation variants"):
        compare_reviewer_ablation([_sample("baseline")])
    with pytest.raises(ValueError, match="confounded"):
        compare_reviewer_ablation(
            [_sample("baseline"), _sample("reviewer", model="different")]
        )


def test_reviewer_ablation_cli_emits_machine_readable_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "samples.json"
    path.write_text(
        json.dumps(
            [
                _sample("baseline").model_dump(mode="json"),
                _sample("reviewer").model_dump(mode="json"),
            ]
        ),
        encoding="utf-8",
    )

    assert main([str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["case_ids"] == ["case-1"]
