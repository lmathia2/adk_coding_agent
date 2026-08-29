from __future__ import annotations

from pathlib import Path

from harness.memory import ProjectMemoryStore, extract_verified_memories
from harness.models.ledger import TaskLedger
from harness.models.task import Decision
from harness.models.verification import (
    CriterionEvidence,
    EvidenceReference,
    VerificationReport,
)
from harness.repo import BuildCommand, RepositoryManifest


def _ledger() -> TaskLedger:
    return TaskLedger(
        task_id="task-1",
        goal="Fix login",
        acceptance_criteria=["Login succeeds"],
        base_revision="abc",
        workspace_id="workspace",
        branch_id="main",
    )


def _report(passed: bool = True) -> VerificationReport:
    return VerificationReport(
        passed=passed,
        criteria=[
            CriterionEvidence(
                criterion="Login succeeds",
                satisfied=passed,
                claimed_evidence=["pytest"] if passed else [],
                evidence=(
                    [
                        EvidenceReference(
                            kind="command_result",
                            reference="validation:0",
                            command_sha256="a" * 64,
                            validation_index=0,
                            category="test",
                            strength="behavioral",
                        )
                    ]
                    if passed
                    else []
                ),
            )
        ],
        commands_run=["pytest"],
        tests_passed=1 if passed else 0,
        tests_failed=0 if passed else 1,
    )


def test_memory_is_extracted_only_from_verified_tasks(tmp_path: Path) -> None:
    instructions = tmp_path / "AGENTS.md"
    instructions.write_text("Use pytest", encoding="utf-8")
    manifest = RepositoryManifest(
        root=tmp_path,
        commands=[BuildCommand("test", "pytest -q", "pyproject.toml")],
        instruction_files=[instructions],
    )

    assert (
        extract_verified_memories(
            project_id="project",
            manifest=manifest,
            ledger=_ledger(),
            verification=_report(False),
        )
        == []
    )

    memories = extract_verified_memories(
        project_id="project",
        manifest=manifest,
        ledger=_ledger(),
        verification=_report(True),
        source_event_ids=["event-1"],
    )
    assert {memory.kind for memory in memories} == {"command", "convention"}
    assert all(memory.source_event_ids == ["event-1"] for memory in memories)


def test_store_confirms_duplicate_facts_and_renders_bounded_context(
    tmp_path: Path,
) -> None:
    manifest = RepositoryManifest(
        root=tmp_path,
        commands=[BuildCommand("test", "pytest -q", "pyproject.toml")],
    )
    memories = extract_verified_memories(
        project_id="project",
        manifest=manifest,
        ledger=_ledger(),
        verification=_report(),
    )
    store = ProjectMemoryStore(tmp_path / "memory.db")
    first = store.upsert(memories[0])
    second = store.upsert(memories[0])

    assert second.memory_id == first.memory_id
    assert second.last_confirmed_at >= first.last_confirmed_at
    context = store.render_context("project", "run tests", max_tokens=100)
    assert "Canonical test command: pytest -q" in context
    assert len(context) <= 500


def test_typed_decision_uses_summary_and_rationale() -> None:
    ledger = _ledger()
    ledger.decisions.append(
        Decision(
            summary="Keep the model-visible tool surface narrow",
            rationale="Protect prompt stability",
            affected_paths=["harness/tools"],
        )
    )

    memories = extract_verified_memories(
        project_id="project",
        manifest=RepositoryManifest(root=Path(".")),
        ledger=ledger,
        verification=_report(),
    )

    decisions = [memory for memory in memories if memory.kind == "decision"]
    assert len(decisions) == 1
    assert decisions[0].content == (
        "Keep the model-visible tool surface narrow: Protect prompt stability"
    )
