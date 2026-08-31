"""Deterministic validation and evidence-based completion."""

from .discovery import discover_validation_plan, find_adjacent_tests
from .managed import ManagedValidationExecutor
from .models import CommandResult, ValidationCommand, ValidationPlan
from .runner import build_report, run_validation_plan
from .scope import check_scope

__all__ = [
    "CommandResult",
    "ManagedValidationExecutor",
    "ValidationCommand",
    "ValidationPlan",
    "build_report",
    "check_scope",
    "discover_validation_plan",
    "find_adjacent_tests",
    "run_validation_plan",
]
