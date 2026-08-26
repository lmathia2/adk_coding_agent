"""Approval policy package facade.

The facade loads the original single-file implementation under an internal module name
and pre-registers the legacy ADK adapter name. The latter is needed because the adapter
is loaded from a source file for backward compatibility and contains dataclasses with
postponed annotations; dataclasses resolve those annotations through ``sys.modules``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_ADAPTER_MODULE = "harness.tools._legacy_adk_adapter"
sys.modules.setdefault(_ADAPTER_MODULE, ModuleType(_ADAPTER_MODULE))

_SOURCE = Path(__file__).resolve().parent.parent / "approval.py"
_SPEC = importlib.util.spec_from_file_location("harness.safety._approval_implementation", _SOURCE)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"cannot load approval policy from {_SOURCE}")
_IMPLEMENTATION = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _IMPLEMENTATION
_SPEC.loader.exec_module(_IMPLEMENTATION)

ApprovalAction = _IMPLEMENTATION.ApprovalAction
ApprovalDecision = _IMPLEMENTATION.ApprovalDecision
ApprovalPolicy = _IMPLEMENTATION.ApprovalPolicy
CommandRisk = _IMPLEMENTATION.CommandRisk
classify_command = _IMPLEMENTATION.classify_command

__all__ = [
    "ApprovalAction",
    "ApprovalDecision",
    "ApprovalPolicy",
    "CommandRisk",
    "classify_command",
]
