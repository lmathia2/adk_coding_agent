from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from harness.context.prompt import DEFAULT_TOOL_NAMES
from harness.evals import (
    REQUIRED_SKILL_ABLATION_METRICS,
    SkillAblationPlan,
    SkillAblationSample,
    ablation_harness_content_hash,
    compare_skill_ablation,
    load_evaluation_suite,
    load_skill_ablation_plan,
)
from harness.evals.skill_ablation_cli import main as skill_ablation_main
from harness.skills import SkillRegistry, SkillRoot

ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = ROOT / ".agents" / "skills"
PLAN_PATH = ROOT / "tests" / "eval" / "ablations" / "programmatic_tool_routing.json"


def test_programmatic_routing_skill_uses_progressive_disclosure() -> None:
    registry = SkillRegistry([SkillRoot(SKILLS_ROOT, origin="project")])

    definition = registry.get("programmatic-tool-routing")
    catalog = registry.build_catalog()
    unrelated = registry.select(goal="Fix a localized arithmetic regression")
    selected = registry.select(goal="Use $programmatic-tool-routing for these trace files")

    assert definition is not None
    assert "programmatic-tool-routing" in catalog.included_names
    assert "Do not read environment variables" not in catalog.text
    assert all(skill.name != "programmatic-tool-routing" for skill in unrelated.skills)
    assert [skill.name for skill in selected.skills] == ["programmatic-tool-routing"]
    assert selected.skills[0].explicit is True
    assert "Do not read environment variables" in selected.text
    assert selected.skills[0].included_references == ()


def test_committed_ablation_changes_only_skill_disclosure() -> None:
    plan = load_skill_ablation_plan(PLAN_PATH)

    assert plan.ablation_id == "programmatic-tool-routing-v3"
    assert plan.baseline.selected_skills == ()
    assert plan.candidate.selected_skills == ("programmatic-tool-routing",)
    assert plan.baseline.model_visible_tools == DEFAULT_TOOL_NAMES
    assert plan.candidate.model_visible_tools == DEFAULT_TOOL_NAMES
    assert plan.baseline.model_visible_tools == plan.candidate.model_visible_tools
    assert plan.required_metrics == REQUIRED_SKILL_ABLATION_METRICS
    assert plan.execution.skill_content_hash == definition_hash()
    assert plan.execution.harness_content_hash == ablation_harness_content_hash(ROOT)
    suite = load_evaluation_suite(ROOT / plan.suite_path)
    assert set(plan.case_ids) <= {case.case_id for case in suite.cases}
    tags = {case.case_id: set(case.tags) for case in suite.cases}
    assert all("routing-positive" in tags[case_id] for case_id in plan.positive_case_ids)
    assert all("routing-negative" in tags[case_id] for case_id in plan.negative_case_ids)


def definition_hash() -> str:
    registry = SkillRegistry([SkillRoot(SKILLS_ROOT, origin="project")])
    definition = registry.get("programmatic-tool-routing")
    assert definition is not None
    return definition.content_hash


def _sample(
    plan: SkillAblationPlan,
    case_id: str,
    variant: Literal["baseline", "routing-skill"],
    *,
    selected_skills: tuple[str, ...] | None = None,
    selected_hashes: tuple[str, ...] | None = None,
) -> SkillAblationSample:
    candidate = variant == "routing-skill"
    return SkillAblationSample(
        ablation_id=plan.ablation_id,
        case_id=case_id,
        variant=variant,
        execution=plan.execution,
        actual_selected_skills=(
            selected_skills
            if selected_skills is not None
            else ((plan.execution.skill_name,) if candidate else ())
        ),
        actual_selected_skill_hashes=(
            selected_hashes
            if selected_hashes is not None
            else ((plan.execution.skill_content_hash,) if candidate else ())
        ),
        model_visible_tools=DEFAULT_TOOL_NAMES,
        passed=True,
        cost_usd=0.1,
        uncached_input_tokens=100,
        cache_read_ratio=0.8,
        prefix_versions=1,
        tool_calls=4,
        wall_time_ms=1000,
    )


def test_paired_ablation_comparator_requires_actual_selection_evidence() -> None:
    plan = load_skill_ablation_plan(PLAN_PATH)
    samples = [
        _sample(plan, case_id, variant)
        for case_id in plan.case_ids
        for variant in ("baseline", "routing-skill")
    ]

    report = compare_skill_ablation(plan, samples)

    assert report.positive_case_ids == plan.positive_case_ids
    assert report.negative_case_ids == plan.negative_case_ids
    assert report.baseline.cases == len(plan.case_ids)
    assert report.candidate.cases == len(plan.case_ids)
    assert report.pass_rate_delta == 0

    samples[-1] = _sample(
        plan,
        plan.case_ids[-1],
        "routing-skill",
        selected_hashes=("0" * 64,),
    )
    with pytest.raises(ValueError, match="unpinned skill revision"):
        compare_skill_ablation(plan, samples)


def test_ablation_cli_emits_machine_readable_report(
    tmp_path: Path,
    capsys,
) -> None:
    plan = load_skill_ablation_plan(PLAN_PATH)
    samples = [
        _sample(plan, case_id, variant).model_dump(mode="json")
        for case_id in plan.case_ids
        for variant in ("baseline", "routing-skill")
    ]
    samples_path = tmp_path / "samples.json"
    samples_path.write_text(json.dumps(samples), encoding="utf-8")

    assert skill_ablation_main([str(PLAN_PATH), str(samples_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ablation_id"] == plan.ablation_id
    assert report["baseline"]["cases"] == len(plan.case_ids)


@pytest.mark.parametrize(
    ("side", "field", "value"),
    [
        ("baseline", "selected_skills", ["programmatic-tool-routing"]),
        ("candidate", "selected_skills", ["different-skill"]),
        ("candidate", "model_visible_tools", ["read", "bash", "edit", "write", "search"]),
        ("plan", "required_metrics", ["pass_rate"]),
    ],
)
def test_ablation_rejects_confounded_skill_or_tool_variants(
    side: str,
    field: str,
    value: list[str],
) -> None:
    payload = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    if side == "plan":
        payload[field] = value
    else:
        payload[side][field] = value

    with pytest.raises(ValidationError):
        SkillAblationPlan.model_validate(payload)
