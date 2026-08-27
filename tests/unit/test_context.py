from __future__ import annotations

from harness.context import (
    CompactionPolicy,
    ContextCompiler,
    build_compaction_snapshot,
    truncate_to_tokens,
)
from harness.models import (
    ContextBudget,
    Decision,
    RepositoryManifest,
    TaskLedger,
    TaskRequest,
    ValidationResult,
)
from harness.state.events import HarnessEvent


def _ledger() -> TaskLedger:
    ledger = TaskLedger.from_request(
        TaskRequest(goal="Fix parser", acceptance_criteria=["Parser tests pass"]),
        task_id="task-1",
        workspace_id="workspace-1",
        base_revision="abc123",
    )
    ledger.progress = ["Located the parser"]
    ledger.files_read = ["src/parser.py"]
    ledger.files_modified = ["src/parser.py"]
    ledger.decisions = [Decision(summary="Keep API stable", rationale="Avoid regressions")]
    ledger.validations = [
        ValidationResult(command="pytest tests/test_parser.py", passed=False, summary="1 failed")
    ]
    ledger.next_action = "Repair quoted-token handling"
    return ledger


def test_prefix_hash_does_not_change_with_dynamic_ledger_state() -> None:
    compiler = ContextCompiler(model_name="gemini-test")
    first = compiler.compile(ledger=_ledger())
    ledger = _ledger()
    ledger.progress.append("New progress")
    second = compiler.compile(ledger=ledger)

    assert first.static_prefix_hash == second.static_prefix_hash
    assert first.text != second.text


def test_context_compilation_is_deterministic() -> None:
    compiler = ContextCompiler(model_name="gemini-test")
    manifest = RepositoryManifest(
        root="/repo",
        base_revision="abc123",
        languages=["python"],
        commands={"test": "pytest"},
    )
    kwargs = {
        "ledger": _ledger(),
        "manifest": manifest,
        "project_instructions": {"AGENTS.md": "Keep changes focused."},
        "repository_map": "src/parser.py\n  function parse(text: str)",
        "recent_events": [{"event": "read", "path": "src/parser.py"}],
    }
    assert compiler.compile(**kwargs).model_dump() == compiler.compile(**kwargs).model_dump()


def test_oversized_recent_events_request_compaction() -> None:
    compiler = ContextCompiler(
        model_name="gemini-test",
        budget=ContextBudget(
            model_context_window=8_000,
            completion_reserve=1_000,
            recent_events=100,
            repository_map=300,
        ),
    )
    packet = compiler.compile(ledger=_ledger(), recent_events=["x" * 10_000])
    recent = next(section for section in packet.sections if section.name == "recent_events")
    assert recent.truncated is True
    assert packet.should_compact is True


def test_truncation_preserves_head_and_tail() -> None:
    text = "HEAD" + "x" * 1_000 + "TAIL"
    bounded, truncated = truncate_to_tokens(text, 50)
    assert truncated is True
    assert bounded.startswith("HEAD")
    assert bounded.endswith("TAIL")


def test_compaction_snapshot_preserves_goal_files_and_validation() -> None:
    snapshot = build_compaction_snapshot(
        ledger=_ledger(),
        events_to_summarize=["read parser", "edited parser"],
        retained_events=["run tests"],
        tokens_before=50_000,
    )
    assert "## Goal\nFix parser" in snapshot.summary_markdown
    assert "src/parser.py" in snapshot.summary_markdown
    assert "1 failed" in snapshot.summary_markdown
    assert snapshot.tokens_before == 50_000
    assert snapshot.estimated_tokens_after > 0


def test_compaction_policy_uses_reserved_window() -> None:
    policy = CompactionPolicy(
        context_window=10_000,
        completion_reserve=2_000,
        trigger_fraction=0.75,
    )
    assert policy.trigger_tokens == 6_000
    assert policy.should_compact(5_999) is False
    assert policy.should_compact(6_000) is True


def test_compaction_snapshot_accepts_state_events_without_volatile_metadata() -> None:
    summarized = HarnessEvent(
        event_id="random-event-id",
        task_id="task-1",
        sequence=3,
        kind="action.recorded",
        payload={"path": "src/parser.py"},
    )
    retained = summarized.model_copy(
        update={"event_id": "another-random-id", "sequence": 4}
    )

    snapshot = build_compaction_snapshot(
        ledger=_ledger(),
        events_to_summarize=[summarized],
        retained_events=[retained],
        tokens_before=20_000,
    )

    assert snapshot.last_summarized_event_id == "random-event-id"
    assert snapshot.first_retained_event_id == "another-random-id"
    assert '"sequence":3' in snapshot.summary_markdown
    assert "random-event-id" not in snapshot.summary_markdown
