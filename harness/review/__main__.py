"""Compare paired final-reviewer ablation samples from a JSON file."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter

from .ablation import ReviewAblationSample, compare_reviewer_ablation


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m harness.review",
        description="Compare paired baseline and final-reviewer evaluation samples.",
    )
    parser.add_argument("samples", type=Path)
    arguments = parser.parse_args(argv)
    samples = TypeAdapter(list[ReviewAblationSample]).validate_json(
        arguments.samples.read_text(encoding="utf-8")
    )
    report = compare_reviewer_ablation(samples)
    print(json.dumps(report.model_dump(mode="json"), sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
