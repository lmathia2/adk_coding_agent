"""Declarative coding-harness composition contracts."""

from .loader import (
    DEFAULT_COMPOSITION_PATH,
    DEFAULT_HARNESS_CONFIG_MODELS,
    load_harness_composition,
    parse_harness_composition,
)
from .models import (
    FOUR_CODING_TOOLS,
    AgentConfig,
    HarnessCapability,
    HarnessComposition,
    HarnessSelectionConfig,
    ModelConfig,
    PersistenceConfig,
    PiCodingConfig,
    PromptConfig,
    RuntimeBindings,
    SandboxConfig,
    SecretRef,
    ServerConfig,
    ToolSurfaceConfig,
    WorkflowConfig,
)

__all__ = [
    "DEFAULT_COMPOSITION_PATH",
    "DEFAULT_HARNESS_CONFIG_MODELS",
    "FOUR_CODING_TOOLS",
    "AgentConfig",
    "HarnessCapability",
    "HarnessComposition",
    "HarnessSelectionConfig",
    "ModelConfig",
    "PersistenceConfig",
    "PiCodingConfig",
    "PromptConfig",
    "RuntimeBindings",
    "SandboxConfig",
    "SecretRef",
    "ServerConfig",
    "ToolSurfaceConfig",
    "WorkflowConfig",
    "load_harness_composition",
    "parse_harness_composition",
]
