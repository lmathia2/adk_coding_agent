"""Context economy: stable prefixes, bounded packets, and compaction."""

from .compaction import CompactionPolicy, build_compaction_snapshot
from .compiler import ContextCompiler, estimate_tokens, truncate_to_tokens
from .prompt import STATIC_CODING_INSTRUCTION, build_static_prefix, prefix_hash

__all__ = [
    "CompactionPolicy",
    "ContextCompiler",
    "STATIC_CODING_INSTRUCTION",
    "build_compaction_snapshot",
    "build_static_prefix",
    "estimate_tokens",
    "prefix_hash",
    "truncate_to_tokens",
]
