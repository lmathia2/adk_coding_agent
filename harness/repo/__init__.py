"""Repository discovery and lightweight structural indexing."""

from .discovery import (
    BuildCommand,
    RepositoryManifest,
    build_repository_manifest,
    collect_project_instructions,
    discover_instruction_files,
)
from .fff_search import (
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_CONTEXT,
    MAX_SEARCH_LIMIT,
    FffSearchService,
    SearchBackend,
    SearchCursorError,
    SearchError,
    SearchMode,
    SearchPage,
    SearchUnavailableError,
)
from .index import (
    FileRecord,
    IndexSnapshot,
    ParseResult,
    SearchHit,
    StructuralIndex,
    StructuralParser,
    SymbolRecord,
)

__all__ = [
    "DEFAULT_SEARCH_LIMIT",
    "MAX_SEARCH_CONTEXT",
    "MAX_SEARCH_LIMIT",
    "BuildCommand",
    "FffSearchService",
    "FileRecord",
    "IndexSnapshot",
    "ParseResult",
    "RepositoryManifest",
    "SearchBackend",
    "SearchCursorError",
    "SearchError",
    "SearchHit",
    "SearchMode",
    "SearchPage",
    "SearchUnavailableError",
    "StructuralIndex",
    "StructuralParser",
    "SymbolRecord",
    "build_repository_manifest",
    "collect_project_instructions",
    "discover_instruction_files",
]
