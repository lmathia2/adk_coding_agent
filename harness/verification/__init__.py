"""Deterministic validation and evidence-based completion."""

from .discovery import discover_validation_plan, find_adjacent_tests
from .models import CommandResult, ValidationCommand, ValidationPlan
from .runner import build_report, local_executor, run_validation_plan
from .scope import check_scope

__all__ = [
    "CommandResult",
    "ValidationCommand",
    "ValidationPlan",
    "build_report",
    "check_scope",
    "discover_validation_plan",
    "find_adjacent_tests",
    "local_executor",
    "run_validation_plan",
]
