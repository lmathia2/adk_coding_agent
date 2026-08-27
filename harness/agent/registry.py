"""Explicit harness registry used by declarative composition."""

from __future__ import annotations

from harness.config import HarnessComposition, RuntimeBindings

from .contracts import AdkHarnessAssembly, HarnessFactory, RuntimeCapability


class HarnessRegistry:
    """Resolve code-owned factories without accepting YAML import paths."""

    def __init__(self) -> None:
        self._factories: dict[str, HarnessFactory] = {}

    def register(self, factory: HarnessFactory) -> None:
        implementation = factory.descriptor.implementation
        if implementation in self._factories:
            raise ValueError(f"harness implementation already registered: {implementation}")
        self._factories[implementation] = factory

    def build(
        self,
        composition: HarnessComposition,
        bindings: RuntimeBindings,
    ) -> AdkHarnessAssembly:
        implementation = composition.harness.implementation
        try:
            factory = self._factories[implementation]
        except KeyError as error:
            raise LookupError(f"harness implementation is not registered: {implementation}") from error
        if factory.descriptor.api_version != composition.harness.api_version:
            raise ValueError(
                "harness API version mismatch: "
                f"configured {composition.harness.api_version}, "
                f"registered {factory.descriptor.api_version}"
            )
        required = {RuntimeCapability(value) for value in composition.harness.required_capabilities}
        missing = required - factory.descriptor.capabilities
        if missing:
            values = ", ".join(sorted(capability.value for capability in missing))
            raise ValueError(f"harness implementation lacks required capabilities: {values}")
        return factory.build(composition, bindings)

    def available(self) -> tuple[str, ...]:
        return tuple(sorted(self._factories))


__all__ = ["HarnessRegistry"]
