"""Repository discovery and lightweight structural indexing."""

from .discovery import (
    BuildCommand,
    RepositoryManifest,
    build_repository_manifest,
    collect_project_instructions,
    discover_instruction_files,
)
from .index import FileRecord, SearchHit, StructuralIndex, SymbolRecord

__all__ = [
    "BuildCommand",
    "FileRecord",
    "RepositoryManifest",
    "SearchHit",
    "StructuralIndex",
    "SymbolRecord",
    "build_repository_manifest",
    "collect_project_instructions",
    "discover_instruction_files",
]
