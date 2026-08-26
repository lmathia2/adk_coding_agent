from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from harness.learning import (
    EpisodeQuality,
    HeuristicSkillSynthesizer,
    LearningStore,
    NormalizedAction,
    SkillRegistry,
    TraceSkillLearningController,
    WorkflowEpisode,
    repeated_action_sequences,
    workflow_fingerprint,
)
from harness.skills import SkillRegistry as DiscoveredSkillRegistry
from harness.skills import learned_skill_roots


def _episode(
    trace_id: str,
    *,
    passed: bool = True,
    verified: bool = True,
    blocked: bool = False,
    security_risks: tuple[str, ...] = (),
    cost: float = 0.1,
) -> WorkflowEpisode:
    return WorkflowEpisode(
        trace_id=trace_id,
        workflow_kind="python_bugfix",
        actions=(
            NormalizedAction(action="read", category="source"),
            NormalizedAction(action="edit", category="source"),
            NormalizedAction(action="bash", category="test"),
        ),
        verified_completed=verified,
        blocked=blocked,
        security_risks=security_risks,
        quality=EpisodeQuality(
            passed=passed,
            cost_usd=cost,
            uncached_input_tokens=1_000,
            cache_read_ratio=0.75,
            tool_calls=3,
            wall_time_ms=2_000,
        ),
    )


def test_fingerprint_and_repeated_sequences_are_stable() -> None:
    first = _episode("trace-1", cost=0.1)
    second = _episode("trace-2", cost=0.9)

    assert workflow_fingerprint(first) == workflow_fingerprint(second)
    repeated = repeated_action_sequences(
        [second, first],
        minimum_support=2,
    )
    assert repeated[0].tokens == tuple(action.token for action in first.actions)
    assert repeated[0].support == 2
    assert repeated[0].source_trace_ids == ("trace-1", "trace-2")


@pytest.mark.parametrize(
    "episode, message",
    [
        (_episode("unverified", verified=False), "verified completion"),
        (_episode("failed", passed=False), "verified completion"),
        (_episode("blocked", blocked=True), "blocked workflow"),
        (_episode("risk", security_risks=("network_access",)), "security-risk"),
    ],
)
def test_store_rejects_ineligible_traces(
    tmp_path: Path,
    episode: WorkflowEpisode,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        LearningStore(tmp_path / "learning.db").observe(episode)


def test_episode_schema_rejects_prompt_or_source_bodies() -> None:
    payload = _episode("trace").model_dump(mode="python")
    payload["prompt"] = "SECRET PROMPT BODY"
    with pytest.raises(ValidationError):
        WorkflowEpisode.model_validate(payload)

    action = NormalizedAction(action="read", category="source").model_dump()
    action["source_body"] = "SECRET SOURCE BODY"
    with pytest.raises(ValidationError):
        NormalizedAction.model_validate(action)


def test_observation_is_idempotent_and_privacy_safe(tmp_path: Path) -> None:
    database = tmp_path / "learning.db"
    store = LearningStore(database)
    episode = _episode("trace-1")

    first = store.observe(episode)
    replay = store.observe(episode)

    assert replay == first
    assert store.episode("trace-1") == episode
    assert store.episode("missing") is None
    with pytest.raises(ValueError, match="different content"):
        store.observe(_episode("trace-1", cost=0.2))
    with sqlite3.connect(database) as connection:
        serialized = str(
            connection.execute(
                "SELECT episode_json FROM workflow_observations"
            ).fetchone()[0]
        )
    assert "prompt" not in serialized
    assert "source_body" not in serialized
    assert "tool_output" not in serialized


def test_candidate_publish_is_atomic_idempotent_and_has_provenance(
    tmp_path: Path,
) -> None:
    sequence = repeated_action_sequences(
        [_episode("trace-1"), _episode("trace-2")],
        minimum_support=2,
    )[0]
    draft = HeuristicSkillSynthesizer().synthesize(
        workflow_kind="python_bugfix",
        sequence=sequence,
    )
    registry = SkillRegistry(tmp_path / "skills")

    def interrupt(_temporary: Path) -> None:
        raise RuntimeError("simulated process interruption")

    with pytest.raises(RuntimeError, match="interruption"):
        registry.emit_candidate(draft, before_publish=interrupt)
    assert not (tmp_path / "skills" / "candidates" / draft.name).exists()

    lifecycle = registry.emit_candidate(draft)
    replay = registry.emit_candidate(draft)

    assert replay == lifecycle
    skill_path = tmp_path / "skills" / "candidates" / draft.name / "SKILL.md"
    metadata_path = (
        tmp_path / "skills" / "candidates" / draft.name / "lifecycle.json"
    )
    skill_text = skill_path.read_text(encoding="utf-8")
    metadata_text = metadata_path.read_text(encoding="utf-8")
    assert skill_text.startswith("---\nname:")
    assert "source_trace_ids" in skill_text
    assert lifecycle.status == "candidate"
    assert lifecycle.version == 1
    assert lifecycle.source_trace_ids == ("trace-1", "trace-2")
    for forbidden in ("SECRET PROMPT BODY", "SECRET SOURCE BODY", "tool_output"):
        assert forbidden not in skill_text
        assert forbidden not in metadata_text

    discovered = DiscoveredSkillRegistry(
        learned_skill_roots(tmp_path / "skills")
    )
    definition = discovered.get(draft.name)
    assert definition is not None
    assert definition.lifecycle == "candidate"
    assert discovered.select(goal=f"${draft.name}").skills == ()


def test_controller_requires_repeated_support_before_candidate(tmp_path: Path) -> None:
    controller = TraceSkillLearningController(
        store=LearningStore(tmp_path / "learning.db"),
        registry=SkillRegistry(tmp_path / "skills"),
    )
    controller.observe(_episode("trace-1"))
    assert (
        controller.propose_candidate("python_bugfix", minimum_support=2) is None
    )

    controller.observe(_episode("trace-2"))
    candidate = controller.propose_candidate(
        "python_bugfix",
        minimum_support=2,
    )
    assert candidate is not None
    assert candidate.status == "candidate"
