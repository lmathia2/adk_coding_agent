"""Optional no-tool agent for a narrow, advisory final-diff review."""

from __future__ import annotations

import json
from typing import Any

from google.adk import Agent
from google.adk.models import Gemini
from google.genai import types

from harness.context import build_static_prefix
from harness.review import DiffReviewPacket, FinalDiffReview

from .config import SETTINGS

FINAL_REVIEW_INSTRUCTION = """
Review only the supplied final diff and deterministic verification summary. Identify
concrete correctness, security, reliability, maintainability, or scope defects that
were introduced by the diff. Do not speculate about code that is not shown. Prefer a
small number of actionable findings with exact paths and lines. Return `clear` when
there are no material findings. This review is advisory and has no tools.
""".strip()

FINAL_REVIEW_STATIC_PREFIX = build_static_prefix(
    model_name=SETTINGS.review_model,
    tool_names=(),
    instruction=FINAL_REVIEW_INSTRUCTION,
)


def build_review_input(
    packet: DiffReviewPacket,
    verification: dict[str, Any],
) -> str:
    """Serialize mutable review evidence as a deterministic node input."""

    return json.dumps(
        {
            "diff_packet": packet.model_dump(mode="json"),
            "verification": verification,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def parse_final_diff_review(value: Any) -> FinalDiffReview:
    if isinstance(value, FinalDiffReview):
        return value
    if isinstance(value, str):
        return FinalDiffReview.model_validate_json(value)
    return FinalDiffReview.model_validate(value)


final_diff_reviewer = Agent(
    name="final_diff_reviewer",
    model=Gemini(
        model=SETTINGS.review_model,
        retry_options=types.HttpRetryOptions(
            attempts=3,
            exp_base=2,
            initial_delay=1,
            http_status_codes=[429, 500, 502, 503, 504],
        ),
    ),
    description="Advisory, bounded review of a deterministically verified final diff.",
    static_instruction=FINAL_REVIEW_INSTRUCTION,
    instruction="",
    tools=[],
    include_contents="none",
    mode="single_turn",
    output_schema=FinalDiffReview,
)

__all__ = [
    "FINAL_REVIEW_STATIC_PREFIX",
    "build_review_input",
    "final_diff_reviewer",
    "parse_final_diff_review",
]
