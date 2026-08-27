"""SQLite persistence for learned workflow observations and trials."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .fingerprints import workflow_fingerprint
from .models import (
    TrialAssignment,
    TrialOutcome,
    WorkflowEpisode,
    WorkflowObservation,
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class LearningStore:
    def __init__(self, database: Path) -> None:
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflow_observations (
                    trace_id TEXT PRIMARY KEY,
                    workflow_kind TEXT NOT NULL,
                    workflow_fingerprint TEXT NOT NULL,
                    episode_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_workflow_observation_kind
                ON workflow_observations(workflow_kind, workflow_fingerprint);

                CREATE TABLE IF NOT EXISTS learning_trial_assignments (
                    experiment_id TEXT NOT NULL,
                    unit_id TEXT NOT NULL,
                    variant TEXT NOT NULL,
                    assignment_json TEXT NOT NULL,
                    PRIMARY KEY(experiment_id, unit_id)
                );

                CREATE TABLE IF NOT EXISTS learning_trial_outcomes (
                    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    experiment_id TEXT NOT NULL,
                    unit_id TEXT NOT NULL,
                    skill_name TEXT NOT NULL,
                    variant TEXT NOT NULL,
                    outcome_json TEXT NOT NULL,
                    UNIQUE(experiment_id, unit_id)
                );
                CREATE INDEX IF NOT EXISTS ix_learning_outcome_skill
                ON learning_trial_outcomes(skill_name, result_id);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def observe(self, episode: WorkflowEpisode) -> WorkflowObservation:
        episode.require_eligible()
        fingerprint = workflow_fingerprint(episode)
        episode_json = _canonical_json(episode.model_dump(mode="json"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT episode_json FROM workflow_observations WHERE trace_id=?",
                (episode.trace_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["episode_json"]) != episode_json:
                    raise ValueError("trace_id already observed with different content")
            else:
                connection.execute(
                    """
                    INSERT INTO workflow_observations(
                        trace_id, workflow_kind, workflow_fingerprint, episode_json
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        episode.trace_id,
                        episode.workflow_kind,
                        fingerprint,
                        episode_json,
                    ),
                )
        return WorkflowObservation(
            trace_id=episode.trace_id,
            workflow_kind=episode.workflow_kind,
            workflow_fingerprint=fingerprint,
            action_tokens=tuple(action.token for action in episode.actions),
            quality=episode.quality,
        )

    def episodes(self, *, workflow_kind: str | None = None) -> list[WorkflowEpisode]:
        query = "SELECT episode_json FROM workflow_observations"
        parameters: tuple[str, ...] = ()
        if workflow_kind is not None:
            query += " WHERE workflow_kind=?"
            parameters = (workflow_kind,)
        query += " ORDER BY trace_id"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            WorkflowEpisode.model_validate_json(str(row["episode_json"]))
            for row in rows
        ]

    def episode(self, trace_id: str) -> WorkflowEpisode | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT episode_json FROM workflow_observations WHERE trace_id=?",
                (trace_id,),
            ).fetchone()
        return (
            WorkflowEpisode.model_validate_json(str(row["episode_json"]))
            if row
            else None
        )

    def save_assignment(self, assignment: TrialAssignment) -> TrialAssignment:
        serialized = _canonical_json(assignment.model_dump(mode="json"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            experiment_row = connection.execute(
                """
                SELECT assignment_json FROM learning_trial_assignments
                WHERE experiment_id=? ORDER BY unit_id LIMIT 1
                """,
                (assignment.experiment_id,),
            ).fetchone()
            if experiment_row is not None:
                experiment_assignment = TrialAssignment.model_validate_json(
                    str(experiment_row["assignment_json"])
                )
                experiment_pin = (
                    experiment_assignment.skill_name,
                    experiment_assignment.skill_version,
                    experiment_assignment.candidate_content_hash,
                )
                requested_pin = (
                    assignment.skill_name,
                    assignment.skill_version,
                    assignment.candidate_content_hash,
                )
                if experiment_pin != requested_pin:
                    raise ValueError(
                        "trial experiment is pinned to another candidate revision"
                    )
            existing = connection.execute(
                """
                SELECT assignment_json FROM learning_trial_assignments
                WHERE experiment_id=? AND unit_id=?
                """,
                (assignment.experiment_id, assignment.unit_id),
            ).fetchone()
            if existing is not None:
                persisted = TrialAssignment.model_validate_json(
                    str(existing["assignment_json"])
                )
                if persisted != assignment:
                    raise ValueError("trial unit already has a different assignment")
                return persisted
            connection.execute(
                """
                INSERT INTO learning_trial_assignments(
                    experiment_id, unit_id, variant, assignment_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    assignment.experiment_id,
                    assignment.unit_id,
                    assignment.variant,
                    serialized,
                ),
            )
        return assignment

    def assignment(
        self,
        experiment_id: str,
        unit_id: str,
    ) -> TrialAssignment | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT assignment_json FROM learning_trial_assignments
                WHERE experiment_id=? AND unit_id=?
                """,
                (experiment_id, unit_id),
            ).fetchone()
        return (
            TrialAssignment.model_validate_json(str(row["assignment_json"]))
            if row
            else None
        )

    def experiment_assignment(self, experiment_id: str) -> TrialAssignment | None:
        """Return the canonical revision pin shared by an experiment's units."""

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT assignment_json FROM learning_trial_assignments
                WHERE experiment_id=? ORDER BY unit_id LIMIT 1
                """,
                (experiment_id,),
            ).fetchone()
        return (
            TrialAssignment.model_validate_json(str(row["assignment_json"]))
            if row
            else None
        )

    def record_outcome(self, outcome: TrialOutcome) -> TrialOutcome:
        assignment = self.assignment(outcome.experiment_id, outcome.unit_id)
        if assignment is None:
            raise ValueError("trial outcome requires a persisted assignment")
        pinned_assignment = (
            assignment.skill_name,
            assignment.skill_version,
            assignment.candidate_content_hash,
            assignment.variant,
        )
        pinned_outcome = (
            outcome.skill_name,
            outcome.skill_version,
            outcome.candidate_content_hash,
            outcome.variant,
        )
        if pinned_assignment != pinned_outcome:
            raise ValueError("trial outcome does not match its pinned assignment")
        serialized = _canonical_json(outcome.model_dump(mode="json"))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT outcome_json FROM learning_trial_outcomes
                WHERE experiment_id=? AND unit_id=?
                """,
                (outcome.experiment_id, outcome.unit_id),
            ).fetchone()
            if existing is not None:
                persisted = TrialOutcome.model_validate_json(
                    str(existing["outcome_json"])
                )
                if persisted != outcome:
                    raise ValueError("trial outcome replay has different content")
                return persisted
            connection.execute(
                """
                INSERT INTO learning_trial_outcomes(
                    experiment_id, unit_id, skill_name, variant, outcome_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    outcome.experiment_id,
                    outcome.unit_id,
                    outcome.skill_name,
                    outcome.variant,
                    serialized,
                ),
            )
        return outcome

    def outcomes(
        self,
        *,
        experiment_id: str | None = None,
        skill_name: str | None = None,
        variant: str | None = None,
    ) -> list[TrialOutcome]:
        conditions: list[str] = []
        values: list[str] = []
        if experiment_id is not None:
            conditions.append("experiment_id=?")
            values.append(experiment_id)
        if skill_name is not None:
            conditions.append("skill_name=?")
            values.append(skill_name)
        if variant is not None:
            conditions.append("variant=?")
            values.append(variant)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT outcome_json FROM learning_trial_outcomes"
                + where
                + " ORDER BY result_id",
                values,
            ).fetchall()
        return [
            TrialOutcome.model_validate_json(str(row["outcome_json"]))
            for row in rows
        ]

    def consecutive_candidate_failures(
        self,
        skill_name: str,
        *,
        skill_version: int,
        candidate_content_hash: str,
    ) -> int:
        outcomes = [
            outcome
            for outcome in self.outcomes(
                skill_name=skill_name,
                variant="candidate",
            )
            if outcome.skill_version == skill_version
            and outcome.candidate_content_hash == candidate_content_hash
        ]
        failures = 0
        for outcome in reversed(outcomes):
            if outcome.quality.passed:
                break
            failures += 1
        return failures


__all__ = ["LearningStore"]
