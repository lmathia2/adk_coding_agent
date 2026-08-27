"""Deterministic context-packet and compaction models."""

from __future__ import annotations

from pydantic import Field

from .base import StrictModel


class ContextBudget(StrictModel):
    model_context_window: int = Field(default=128_000, ge=8_000)
    completion_reserve: int = Field(default=16_000, ge=1_000)
    project_instructions: int = Field(default=2_000, ge=0)
    ledger: int = Field(default=1_200, ge=100)
    repository_map: int = Field(default=1_500, ge=0)
    compaction_summary: int = Field(default=4_000, ge=0)
    recent_events: int = Field(default=20_000, ge=0)

    @property
    def usable_input_tokens(self) -> int:
        return self.model_context_window - self.completion_reserve


class ContextSection(StrictModel):
    name: str
    content: str
    estimated_tokens: int = Field(ge=0)
    truncated: bool = False


class ContextPacket(StrictModel):
    static_prefix_hash: str
    static_prefix_tokens: int = Field(ge=0)
    dynamic_suffix_tokens: int = Field(ge=0)
    total_estimated_tokens: int = Field(ge=0)
    sections: list[ContextSection]
    text: str
    should_compact: bool = False
    prefix_mutation_reason: str | None = None


class CompactionSnapshot(StrictModel):
    summary_markdown: str
    previous_summary_hash: str | None = None
    first_retained_event_id: str | None = None
    last_summarized_event_id: str | None = None
    tokens_before: int = Field(default=0, ge=0)
    estimated_tokens_after: int = Field(default=0, ge=0)
    files_read: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    artifact_uris: list[str] = Field(default_factory=list)
