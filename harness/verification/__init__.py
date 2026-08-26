"""Deterministic validation and evidence-based completion."""

from .discovery import discover_validation_plan, find_adjacent_tests
from .managed import ManagedValidationExecutor, managed_executor_from_env
from .models import CommandResult, ValidationCommand, ValidationPlan
from .runner import build_report, local_executor, run_validation_plan
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
    "local_executor",
    "managed_executor_from_env",
    "run_validation_plan",
]
