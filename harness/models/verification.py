"""Verification evidence contracts."""

from __future__ import annotations

from pydantic import Field

from .base import StrictModel
from .task import ValidationResult


class CriterionEvidence(StrictModel):
    criterion: str
    satisfied: bool
    evidence: list[str] = Field(default_factory=list)
    notes: str | None = None


class VerificationReport(StrictModel):
    passed: bool
    criteria: list[CriterionEvidence] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)
    validations: list[ValidationResult] = Field(default_factory=list)
    tests_passed: int = Field(default=0, ge=0)
    tests_failed: int = Field(default=0, ge=0)
    scope_violations: list[str] = Field(default_factory=list)
    unresolved_diagnostics: list[str] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    recommended_next_action: str | None = None
