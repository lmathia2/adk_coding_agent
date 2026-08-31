"""Context economy: stable prefixes, bounded packets, and compaction."""

from .compaction import CompactionPolicy, build_compaction_snapshot, safe_artifact_uri
from .compiler import estimate_tokens, truncate_to_tokens
from .prompt import STATIC_CODING_INSTRUCTION, build_static_prefix, prefix_hash

__all__ = [
    "STATIC_CODING_INSTRUCTION",
    "CompactionPolicy",
    "build_compaction_snapshot",
    "build_static_prefix",
    "estimate_tokens",
    "prefix_hash",
    "safe_artifact_uri",
    "truncate_to_tokens",
]
