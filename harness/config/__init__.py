"""Declarative coding-harness composition contracts."""

from .loader import DEFAULT_COMPOSITION_PATH, load_harness_composition
from .models import (
    FOUR_CODING_TOOLS,
    AgentConfig,
    HarnessCapability,
    HarnessComposition,
    HarnessSelectionConfig,
    ModelConfig,
    PiCodingConfig,
    RuntimeBindings,
    SecretRef,
    ServerConfig,
    WorkflowConfig,
    WorkflowNodeConfig,
)

__all__ = [
    "DEFAULT_COMPOSITION_PATH",
    "FOUR_CODING_TOOLS",
    "AgentConfig",
    "HarnessCapability",
    "HarnessComposition",
    "HarnessSelectionConfig",
    "ModelConfig",
    "PiCodingConfig",
    "RuntimeBindings",
    "SecretRef",
    "ServerConfig",
    "WorkflowConfig",
    "WorkflowNodeConfig",
    "load_harness_composition",
]
