"""Explicit publication of workflow prose; never interpret raw JSON in the UI."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from google.adk.events import Event
from google.genai import types


def message_event(message: str) -> Event:
    return Event(
        content=types.Content(role="model", parts=[types.Part(text=message)]),
        custom_metadata={"coding.public_message": True},
    )


def result_events(result: Mapping[str, Any]) -> tuple[Event, Event]:
    """Publish one reply and a small result, after the workflow decides its outcome."""
    status = str(result.get("status", "blocked"))
    if status == "complete":
        reply = result.get("message") or "Completed; deterministic verification passed."
    else:
        details = result.get("questions") or result.get("blockers") or [
            result.get("reason") or "Human input is required to continue."
        ]
        reply = "\n\n".join(str(item) for item in details)
    report = result.get("verification", {})
    public = {
        "status": status,
        "verified": report.get("passed") is True,
        "changed_paths": result.get("changed_paths", []),
    }
    return message_event(str(reply)), Event(
        output=public, custom_metadata={"coding.public_result": True}
    )
