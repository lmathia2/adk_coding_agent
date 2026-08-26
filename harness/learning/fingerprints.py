"""Stable workflow fingerprints and repeated normalized action sequences."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable

from .models import RepeatedActionSequence, WorkflowEpisode


def workflow_fingerprint(episode: WorkflowEpisode) -> str:
    canonical = json.dumps(
        {
            "workflow_kind": episode.workflow_kind,
            "actions": [action.token for action in episode.actions],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def repeated_action_sequences(
    episodes: Iterable[WorkflowEpisode],
    *,
    minimum_support: int = 2,
    minimum_length: int = 2,
    maximum_length: int = 8,
) -> list[RepeatedActionSequence]:
    if minimum_support < 1:
        raise ValueError("minimum_support must be at least 1")
    if minimum_length < 1 or maximum_length < minimum_length:
        raise ValueError("invalid action sequence length bounds")

    traces_by_sequence: dict[tuple[str, ...], set[str]] = defaultdict(set)
    for episode in episodes:
        episode.require_eligible()
        tokens = tuple(action.token for action in episode.actions)
        seen: set[tuple[str, ...]] = set()
        upper = min(maximum_length, len(tokens))
        for length in range(minimum_length, upper + 1):
            for start in range(0, len(tokens) - length + 1):
                seen.add(tokens[start : start + length])
        for sequence in seen:
            traces_by_sequence[sequence].add(episode.trace_id)

    repeated = [
        RepeatedActionSequence(
            tokens=sequence,
            support=len(trace_ids),
            source_trace_ids=tuple(sorted(trace_ids)),
        )
        for sequence, trace_ids in traces_by_sequence.items()
        if len(trace_ids) >= minimum_support
    ]
    return sorted(
        repeated,
        key=lambda item: (-item.support, -len(item.tokens), item.tokens),
    )


__all__ = ["repeated_action_sequences", "workflow_fingerprint"]
