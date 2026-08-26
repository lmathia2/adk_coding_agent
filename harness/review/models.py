"""Typed contracts for the optional advisory final-diff review."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from harness.models.base import StrictModel


class DiffReviewPacket(StrictModel):
    """Bounded, deterministic input sent to the reviewer model."""

    base_revision: str
    changed_paths: list[str] = Field(default_factory=list)
    diff_sha256: str
    diff: str
    truncated: bool = False
    omitted_bytes: int = Field(default=0, ge=0)


class ReviewFinding(StrictModel):
    """One actionable defect found in the final diff."""

    severity: Literal["critical", "high", "medium", "low"]
    category: Literal[
        "correctness",
        "security",
        "reliability",
        "maintainability",
        "scope",
    ]
    summary: str
    rationale: str
    path: str | None = None
    line: int | None = Field(default=None, ge=1)


class FinalDiffReview(StrictModel):
    """Advisory reviewer output; it never overrides deterministic verification."""

    verdict: Literal["clear", "concerns"]
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)

