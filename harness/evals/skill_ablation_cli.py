"""Compare pinned programmatic-routing ablation samples from JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .skill_ablation import (
    SkillAblationSample,
    compare_skill_ablation,
    load_skill_ablation_plan,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    parser.add_argument("samples", type=Path)
    arguments = parser.parse_args(argv)
    plan = load_skill_ablation_plan(arguments.plan)
    payload = json.loads(arguments.samples.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("skill ablation samples must be a JSON array")
    samples = [SkillAblationSample.model_validate(item) for item in payload]
    report = compare_skill_ablation(plan, samples)
    print(report.canonical_json())
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main
    raise SystemExit(main())
