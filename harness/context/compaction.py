"""Coding-aware compaction policy and structured handoff snapshots."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from harness.models import CompactionSnapshot, HarnessEvent, TaskLedger

from .compiler import estimate_tokens, truncate_to_tokens


class CompactionPolicy(BaseModel):
    """Policy used before ADK's generic event-compaction safety net."""

    model_config = ConfigDict(extra="forbid")

    context_window: int = Field(default=128_000, ge=8_000)
    completion_reserve: int = Field(default=16_000, ge=1_000)
    trigger_fraction: float = Field(default=0.80, gt=0.1, le=1.0)
    retain_recent_events: int = Field(default=20, ge=1)
    max_event_chars: int = Field(default=2_000, ge=200)

    @property
    def trigger_tokens(self) -> int:
        usable = self.context_window - self.completion_reserve
        return int(usable * self.trigger_fraction)

    def should_compact(self, estimated_prompt_tokens: int) -> bool:
        return estimated_prompt_tokens >= self.trigger_tokens


def _bullets(items: Sequence[str], *, empty: str = "- None") -> str:
    values = [item.strip() for item in items if item.strip()]
    return "\n".join(f"- {item}" for item in values) if values else empty


def _render_events(events: Sequence[HarnessEvent | str], *, max_chars: int) -> str:
    blocks: list[str] = []
    for event in events:
        text = event if isinstance(event, str) else event.canonical_json()
        bounded, truncated = truncate_to_tokens(text, max(1, max_chars // 4))
        suffix = " [truncated]" if truncated else ""
        blocks.append(f"- {bounded}{suffix}")
    return "\n".join(blocks) if blocks else "- None"


def build_compaction_snapshot(
    *,
    ledger: TaskLedger,
    previous_summary: CompactionSnapshot | str | None = None,
    events_to_summarize: Sequence[HarnessEvent | str] = (),
    retained_events: Sequence[HarnessEvent | str] = (),
    tokens_before: int = 0,
    policy: CompactionPolicy | None = None,
) -> CompactionSnapshot:
    """Build a deterministic fallback summary with Pi-compatible structure.

    A production deployment may replace this body with an LLM-generated summary,
    but the section contract and file/validation bookkeeping remain stable.
    """

    active_policy = policy or CompactionPolicy()
    previous_text = (
        previous_summary.summary_markdown
        if isinstance(previous_summary, CompactionSnapshot)
        else previous_summary or "None"
    )
    latest_validation = ledger.validations[-1].summary if ledger.validations else "No validation run yet."
    decisions = [
        f"**{decision.summary}**: {decision.rationale or 'No rationale recorded.'}"
        for decision in ledger.decisions[-12:]
    ]
    plan_completed = [step.description for step in ledger.plan if step.status.value == "complete"]
    plan_in_progress = [step.description for step in ledger.plan if step.status.value == "active"]

    summary = f"""## Goal
{ledger.goal}

## Acceptance Criteria
{_bullets(ledger.acceptance_criteria)}

## Constraints & Non-Goals
{_bullets([*ledger.constraints, *(f'Non-goal: {item}' for item in ledger.non_goals)])}

## Progress
### Done
{_bullets([*plan_completed, *ledger.progress[-12:]])}

### In Progress
{_bullets(plan_in_progress or ([ledger.next_action] if ledger.next_action else []))}

### Blocked
{_bullets(ledger.blockers)}

## Key Decisions
{_bullets(decisions)}

## Current Code State
- Base revision: {ledger.base_revision}
- Workspace: {ledger.workspace_id}
- Branch: {ledger.branch_id}
- Files modified: {', '.join(sorted(set(ledger.files_modified))) or 'None'}

## Validation
- Latest result: {latest_validation}
- Commands run: {', '.join(result.command for result in ledger.validations[-10:]) or 'None'}

## Next Action
{ledger.next_action or 'Reconstruct the next action from the goal and latest verification evidence.'}

## Critical Context
### Previous Summary
{previous_text}

### Newly Summarized Events
{_render_events(events_to_summarize, max_chars=active_policy.max_event_chars)}

<read-files>
{chr(10).join(sorted(set(ledger.files_read))) or '(none)'}
</read-files>

<modified-files>
{chr(10).join(sorted(set(ledger.files_modified))) or '(none)'}
</modified-files>
""".strip()

    retained_text = _render_events(retained_events, max_chars=active_policy.max_event_chars)
    estimated_after = estimate_tokens(summary) + estimate_tokens(retained_text)
    previous_hash = (
        previous_summary.content_hash() if isinstance(previous_summary, CompactionSnapshot) else None
    )
    return CompactionSnapshot(
        summary_markdown=summary,
        previous_summary_hash=previous_hash,
        first_retained_event_id=(
            retained_events[0].event_id if retained_events and isinstance(retained_events[0], HarnessEvent) else None
        ),
        last_summarized_event_id=(
            events_to_summarize[-1].event_id
            if events_to_summarize and isinstance(events_to_summarize[-1], HarnessEvent)
            else None
        ),
        tokens_before=tokens_before,
        estimated_tokens_after=estimated_after,
        files_read=sorted(set(ledger.files_read)),
        files_modified=sorted(set(ledger.files_modified)),
    )
