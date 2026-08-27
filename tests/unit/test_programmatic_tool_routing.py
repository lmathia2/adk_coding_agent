from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from harness.context.prompt import DEFAULT_TOOL_NAMES
from harness.evals import (
    REQUIRED_SKILL_ABLATION_METRICS,
    SkillAblationPlan,
    load_real_repository_suite,
    load_skill_ablation_plan,
)
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

    assert plan.ablation_id == "programmatic-tool-routing-v1"
    assert plan.baseline.selected_skills == ()
    assert plan.candidate.selected_skills == ("programmatic-tool-routing",)
    assert plan.baseline.model_visible_tools == DEFAULT_TOOL_NAMES
    assert plan.candidate.model_visible_tools == DEFAULT_TOOL_NAMES
    assert plan.baseline.model_visible_tools == plan.candidate.model_visible_tools
    assert plan.required_metrics == REQUIRED_SKILL_ABLATION_METRICS
    suite = load_real_repository_suite(ROOT / plan.suite_path)
    assert set(plan.case_ids) <= {case.case_id for case in suite.cases}


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
