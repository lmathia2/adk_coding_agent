"""Temporary scaffold agent; replaced by the coding workflow during implementation."""

from __future__ import annotations

import os

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types

MODEL = os.getenv("ADK_CODING_MODEL", "gemini-3.7-flash")

root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=(
        "This project scaffold is being replaced by a deterministic coding workflow. "
        "For now, explain that implementation is in progress."
    ),
    tools=[],
)

app = App(name="app", root_agent=root_agent)
