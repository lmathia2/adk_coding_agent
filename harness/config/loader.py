"""Side-effect-free YAML loading for portable declarative composition."""

from __future__ import annotations

from pathlib import Path

import yaml

from .models import HarnessComposition

DEFAULT_COMPOSITION_PATH = Path(__file__).with_name("default.yaml")


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mappings."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    loader.flatten_mapping(node)
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_harness_composition(path: Path = DEFAULT_COMPOSITION_PATH) -> HarnessComposition:
    source = path.expanduser().resolve()
    payload = yaml.load(
        source.read_text(encoding="utf-8"),
        Loader=_UniqueKeySafeLoader,
    )
    if not isinstance(payload, dict):
        raise ValueError("harness composition must be a YAML mapping")
    return HarnessComposition.model_validate(payload)


__all__ = ["DEFAULT_COMPOSITION_PATH", "load_harness_composition"]
