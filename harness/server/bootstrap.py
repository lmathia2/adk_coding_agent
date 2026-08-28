"""Composition-driven assembly of the durable local agent server."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from app.agent.factory import default_harness_registry
from harness.config import (
    DEFAULT_COMPOSITION_PATH,
    HarnessComposition,
    RuntimeBindings,
    load_harness_composition,
)
from harness.persistence import build_service_bundle, settings_from_composition

from .registry import RunEventBroker, SqliteRunEventStore
from .runtime import AdkRunExecutionFactory, RunCoordinator
from .websocket import WebSocketServerSettings, create_websocket_app


@dataclass(frozen=True, slots=True)
class ServerAssembly:
    """Resolved server components, exposed for embedding and deterministic tests."""

    app: FastAPI
    composition: HarnessComposition
    coordinator: RunCoordinator
    workspace: Path
    state_root: Path


def require_loopback_host(host: str) -> str:
    """Fail closed while the built-in server uses local-only authentication."""

    normalized = host.strip().strip("[]").split("%", 1)[0]
    if normalized == "localhost":
        return host
    try:
        loopback = ipaddress.ip_address(normalized).is_loopback
    except ValueError as error:
        raise ValueError(
            "the built-in server requires a loopback host until remote authentication "
            "and TLS are configured"
        ) from error
    if not loopback:
        raise ValueError(
            "the built-in server requires a loopback host until remote authentication "
            "and TLS are configured"
        )
    return host


def build_server_assembly(
    *,
    workspace: Path,
    state_root: Path,
    config_path: Path = DEFAULT_COMPOSITION_PATH,
) -> ServerAssembly:
    """Build the configured harness behind the protocol-only WebSocket app."""

    resolved_workspace = workspace.expanduser().resolve()
    resolved_state_root = state_root.expanduser().resolve()
    resolved_config = config_path.expanduser().resolve()
    if not resolved_workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {resolved_workspace}")
    resolved_state_root.mkdir(parents=True, exist_ok=True, mode=0o700)

    registry = default_harness_registry()
    composition = load_harness_composition(
        resolved_config,
        config_models=registry.config_models(),
    )
    require_loopback_host(composition.server.host)
    bindings = RuntimeBindings(
        workspace=resolved_workspace,
        state_root=resolved_state_root,
        configuration_root=resolved_config.parent,
        source_repository=resolved_workspace,
    )
    services = build_service_bundle(
        settings_from_composition(
            composition.persistence,
            state_root=resolved_state_root,
        )
    )
    coordinator = RunCoordinator(
        store=SqliteRunEventStore(resolved_state_root / "server" / "runs.db"),
        broker=RunEventBroker(
            queue_capacity=composition.server.outbound_queue_size,
        ),
        execution_factory=AdkRunExecutionFactory(
            composition=composition,
            bindings=bindings,
            registry=registry,
            services=services,
        ),
    )
    app = create_websocket_app(
        coordinator,
        settings=WebSocketServerSettings(
            path=composition.server.websocket_path,
            outbound_queue_capacity=composition.server.outbound_queue_size,
            max_connections=composition.server.max_connections,
        ),
    )
    return ServerAssembly(
        app=app,
        composition=composition,
        coordinator=coordinator,
        workspace=resolved_workspace,
        state_root=resolved_state_root,
    )


__all__ = ["ServerAssembly", "build_server_assembly", "require_loopback_host"]
