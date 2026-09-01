"""Composition-driven assembly of the durable local agent server."""

from __future__ import annotations

import ipaddress
import os
import secrets
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from app.agent.factory import default_harness_registry
from harness.agent import ModelReadiness, PublicModelStatus
from harness.ai.codex_auth import CodexAuthenticationError, CodexCredentialStore
from harness.ai.controls import LocalProviderControls
from harness.config import (
    DEFAULT_COMPOSITION_PATH,
    HarnessComposition,
    RuntimeBindings,
    load_harness_composition,
)
from harness.ledger import DuckDbLedgerStore
from harness.ledger.importers import import_public_event, import_run, import_session_record
from harness.persistence import build_service_bundle, settings_from_composition
from harness.safety import SecretRedactor
from harness.tools.adk_adapter import discover_known_secrets

from .registry import RunEventBroker, SqliteRunEventStore
from .runtime import AdkRunExecutionFactory, RunCoordinator, RunLivenessPolicy
from .websocket import (
    LocalBearerAuthenticator,
    WebSocketServerSettings,
    create_websocket_app,
)

LOCAL_TOKEN_ENV = "ADK_CODING_AGENT_TOKEN"


def _startup_coding_model_status(
    composition: HarnessComposition,
    state_root: Path,
) -> PublicModelStatus | None:
    config = composition.harness.config
    agents = getattr(config, "agents", None)
    models = getattr(config, "models", None)
    if not isinstance(agents, dict) or not isinstance(models, dict):
        return None
    coding_agent = agents.get("coding_worker")
    model_key = getattr(coding_agent, "model", None)
    model = models.get(model_key)
    provider = getattr(model, "provider", None)
    name = getattr(model, "name", None)
    if not isinstance(provider, str) or not isinstance(name, str):
        return None
    readiness = ModelReadiness.ADAPTER_INITIALIZED
    if provider == "openai_codex":
        try:
            credential = CodexCredentialStore(state_root).load()
        except CodexAuthenticationError:
            credential = None
        if credential is None:
            readiness = ModelReadiness.AUTHENTICATION_REQUIRED
    return PublicModelStatus(provider=provider, name=name, readiness=readiness)


@dataclass(frozen=True, slots=True)
class ServerAssembly:
    """Resolved server components, exposed for embedding and deterministic tests."""

    app: FastAPI
    composition: HarnessComposition
    coordinator: RunCoordinator
    workspace: Path
    state_root: Path
    auth_token_path: Path | None


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


def _validate_local_token(token: str) -> str:
    if len(token.encode("utf-8")) < 32:
        raise ValueError(f"{LOCAL_TOKEN_ENV} must contain at least 32 UTF-8 bytes")
    return token


def load_or_create_local_auth_token(
    state_root: Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> tuple[str, Path | None]:
    """Resolve an explicit token or securely create a stable local token file."""

    resolved_environment = os.environ if environment is None else environment
    explicit = resolved_environment.get(LOCAL_TOKEN_ENV)
    if explicit is not None:
        return _validate_local_token(explicit), None

    server_root = state_root.expanduser().resolve() / "server"
    server_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_path = server_root / "auth-token"
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(token_path, flags)
    except FileNotFoundError:
        token = secrets.token_urlsafe(32)
        create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(token_path, create_flags, 0o600)
        except FileExistsError:
            descriptor = os.open(token_path, flags)
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(token + "\n")
            return token, token_path
    with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
        file_stat = os.fstat(stream.fileno())
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError("local bearer token path must be a regular file")
        if stat.S_IMODE(file_stat.st_mode) & 0o077:
            raise ValueError("local bearer token file must not be accessible by group or others")
        token = stream.read().strip()
    return _validate_local_token(token), token_path


def build_server_assembly(
    *,
    workspace: Path,
    state_root: Path,
    config_path: Path = DEFAULT_COMPOSITION_PATH,
    production: bool = False,
    trust_project: bool = False,
) -> ServerAssembly:
    """Build the configured harness behind the protocol-only WebSocket app."""

    resolved_workspace = workspace.expanduser().resolve()
    resolved_state_root = state_root.expanduser().resolve()
    resolved_config = config_path.expanduser().resolve()
    if not resolved_workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {resolved_workspace}")
    registry = default_harness_registry()
    composition = load_harness_composition(
        resolved_config,
        config_models=registry.config_models(),
    )
    sandbox = getattr(composition.harness.config, "sandbox", None)
    sandbox_kind = str(getattr(sandbox, "kind", "unknown"))
    if production and sandbox_kind == "local":
        raise ValueError(
            "production mode requires the docker sandbox; "
            "the local adapter is not an OS security boundary"
        )
    resolved_state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    auth_token, auth_token_path = load_or_create_local_auth_token(resolved_state_root)
    require_loopback_host(composition.server.host)
    bindings = RuntimeBindings(
        workspace=resolved_workspace,
        state_root=resolved_state_root,
        auth_state_root=resolved_state_root,
        configuration_root=resolved_config.parent,
        source_repository=resolved_workspace,
        project_trusted=trust_project,
    )
    canonical_ledger = DuckDbLedgerStore(resolved_state_root / "ledger.duckdb")
    session_redactor = SecretRedactor(
        known_secrets=discover_known_secrets(), redact_high_entropy_values=True
    )
    services = build_service_bundle(
        settings_from_composition(
            composition.persistence,
            state_root=resolved_state_root,
        ),
        session_sink=lambda session_id, kind, payload: import_session_record(
            canonical_ledger,
            session_id,
            kind,
            session_redactor.redact(payload),
        ),
    )
    coordinator = RunCoordinator(
        provider_controls=LocalProviderControls(resolved_state_root),
        store=SqliteRunEventStore(
            resolved_state_root / "server" / "runs.db",
            run_sink=lambda run: import_run(canonical_ledger, run),
            event_sink=lambda event: import_public_event(canonical_ledger, event),
        ),
        broker=RunEventBroker(
            queue_capacity=composition.server.outbound_queue_size,
        ),
        execution_factory=AdkRunExecutionFactory(
            composition=composition,
            bindings=bindings,
            registry=registry,
            services=services,
            startup_coding_model_status=_startup_coding_model_status(
                composition,
                resolved_state_root,
            ),
        ),
        liveness=RunLivenessPolicy(
            first_event_timeout=composition.server.first_event_timeout_seconds,
            idle_timeout=composition.server.idle_timeout_seconds,
            total_timeout=composition.server.total_timeout_seconds,
            first_event_retries=composition.server.first_event_retries,
            close_timeout=composition.server.close_timeout_seconds,
        ),
    )
    app = create_websocket_app(
        coordinator,
        authenticator=LocalBearerAuthenticator(auth_token),
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
        auth_token_path=auth_token_path,
    )


__all__ = [
    "LOCAL_TOKEN_ENV",
    "ServerAssembly",
    "build_server_assembly",
    "load_or_create_local_auth_token",
    "require_loopback_host",
]
