"""Repository discovery and structural-index contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from .base import StrictModel


class SymbolKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    TYPE = "type"
    INTERFACE = "interface"
    CONSTANT = "constant"
    TEST = "test"
    UNKNOWN = "unknown"


class RepositoryManifest(StrictModel):
    root: str
    base_revision: str = "unknown"
    branch: str = "unknown"
    dirty: bool = False
    languages: list[str] = Field(default_factory=list)
    build_systems: list[str] = Field(default_factory=list)
    commands: dict[str, str] = Field(default_factory=dict)
    instruction_files: list[str] = Field(default_factory=list)
    top_level: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    tracked_file_count: int = Field(default=0, ge=0)


class RepositorySymbol(StrictModel):
    symbol_id: str
    path: str
    qualified_name: str
    kind: SymbolKind
    signature: str = ""
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    references: list[str] = Field(default_factory=list)
    score: float = 0.0


class RepositoryMap(StrictModel):
    query: str
    symbols: list[RepositorySymbol] = Field(default_factory=list)
    rendered: str = ""
    estimated_tokens: int = Field(default=0, ge=0)
    truncated: bool = False
