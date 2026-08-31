"""Real credential store and HTTP client; synthetic device flow, no network."""
from __future__ import annotations

import asyncio
import base64
import json
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from harness.ai.codex_auth import (
    CODEX_DEVICE_TOKEN_URL,
    CODEX_DEVICE_USER_CODE_URL,
    CODEX_TOKEN_URL,
    CodexAuthenticationError,
    CodexOAuthClient,
)
from harness.ai.controls import LocalProviderControls, ProviderControlError, ProviderControlRequest


class DeviceFlow:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: list[str] = []
        self.clients: list[httpx.Client] = []
        self.fail = False

    def client(self) -> CodexOAuthClient:
        client = httpx.Client(transport=httpx.MockTransport(self.respond))
        self.clients.append(client)
        return CodexOAuthClient(client=client)

    def respond(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        if self.fail:
            return httpx.Response(500, text="private-provider-response refresh-secret")
        if url == CODEX_DEVICE_USER_CODE_URL:
            return httpx.Response(200, json={"device_auth_id": "private-device", "user_code": "TEST-CODE", "interval": 1})
        if url == CODEX_DEVICE_TOKEN_URL:
            self.started.set()
            assert self.release.wait(5), "test did not release OAuth polling"
            return httpx.Response(200, json={"authorization_code": "private-code", "code_verifier": "private-verifier"})
        assert url == CODEX_TOKEN_URL
        payload = base64.urlsafe_b64encode(json.dumps({"https://api.openai.com/auth": {"chatgpt_account_id": "acct-test-123456"}}).encode()).decode().rstrip("=")
        return httpx.Response(200, json={"access_token": f"header.{payload}.signature", "refresh_token": "refresh-secret", "expires_in": 3600})

    def close(self) -> None:
        for client in self.clients:
            client.close()


async def command(controls: LocalProviderControls, operation: str, request_id: str = "request", **fields):
    return await controls.request(ProviderControlRequest.model_validate({"request_id": request_id,
        "operation": operation, "provider": "openai_codex", **fields}), user_id="local-user")


async def terminal_status(controls: LocalProviderControls, login_id: str) -> dict:
    async with asyncio.timeout(5):
        while True:
            result = await command(controls, "status", login_id=login_id)
            if result["login"]["status"] not in {"starting", "waiting"}:
                return result
            await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_login_is_nonblocking_idempotent_and_tokens_stay_server_side(tmp_path: Path) -> None:
    flow = DeviceFlow()
    controls = LocalProviderControls(tmp_path, oauth_factory=flow.client)
    try:
        start = await command(controls, "login")
        assert start == await command(controls, "login")
        assert await asyncio.to_thread(flow.started.wait, 5)
        waiting = await command(controls, "status")
        assert waiting["login"]["user_code"] == "TEST-CODE"
        assert waiting["authenticated"] is False
        alias = await command(controls, "login", "another-client")
        assert alias["login"]["login_id"] == start["login"]["login_id"]
        flow.release.set()
        finished = await terminal_status(controls, start["login"]["login_id"])
        assert finished["authenticated"] is True
        assert finished["login"]["status"] == "authenticated"
        assert finished["credential_path"] == str(tmp_path / "auth/openai-codex.json")
        assert "verification_url" not in finished["login"]
        assert controls.store.load() is not None
        assert alias == await command(controls, "login", "another-client")
        public = json.dumps([start, waiting, finished])
        for secret in ["access_token", "refresh_token", "refresh-secret", "private-device", "private-code", "private-verifier"]:
            assert secret not in public
        assert flow.calls.count(CODEX_DEVICE_USER_CODE_URL) == 1
        with pytest.raises(ProviderControlError, match="different content"):
            await command(controls, "logout")
    finally:
        flow.release.set()
        await controls.aclose()
        flow.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["cancel_login", "logout", "shutdown"])
async def test_cancel_logout_shutdown_prevent_late_exchange_and_save(tmp_path: Path, operation: str) -> None:
    flow = DeviceFlow()
    controls = LocalProviderControls(tmp_path, oauth_factory=flow.client)
    shutdown = None
    try:
        start = await command(controls, "login")
        assert await asyncio.to_thread(flow.started.wait, 5)
        if operation == "shutdown":
            shutdown = asyncio.create_task(controls.aclose())
            await asyncio.sleep(0)
        else:
            fields = {"login_id": start["login"]["login_id"]} if operation == "cancel_login" else {}
            await command(controls, operation, "stop", **fields)
        flow.release.set()
        await controls.aclose()
        if shutdown is not None:
            await shutdown
        assert controls.store.load() is None
        assert CODEX_TOKEN_URL not in flow.calls
    finally:
        flow.release.set()
        await controls.aclose()
        flow.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("factory_failure", [False, True])
async def test_login_failures_are_terminal_and_do_not_expose_response_bodies(tmp_path: Path, factory_failure: bool) -> None:
    flow = DeviceFlow()
    flow.fail = True
    def factory() -> CodexOAuthClient:
        if factory_failure:
            raise RuntimeError("private-provider-response refresh-secret")
        return flow.client()
    controls = LocalProviderControls(tmp_path, oauth_factory=factory)
    try:
        start = await command(controls, "login")
        result = await terminal_status(controls, start["login"]["login_id"])
        assert result["login"]["status"] == "failed"
        assert "private-provider-response" not in json.dumps(result)
        assert "refresh-secret" not in json.dumps(result)
    finally:
        await controls.aclose()
        flow.close()


@pytest.mark.asyncio
async def test_auth_ownership_and_logout_retry_do_not_remove_a_newer_login(tmp_path: Path) -> None:
    flow = DeviceFlow()
    flow.release.set()
    controls = LocalProviderControls(tmp_path, oauth_factory=flow.client)
    try:
        with pytest.raises(PermissionError):
            await controls.request(ProviderControlRequest(request_id="x", operation="status"), user_id="other-user")
        assert not flow.calls
        await command(controls, "logout", "logout-first")
        start = await command(controls, "login", "login-after-logout")
        await terminal_status(controls, start["login"]["login_id"])
        await command(controls, "logout", "logout-first")
        assert controls.store.load() is not None
        await command(controls, "logout", "logout-second")
        assert controls.store.load() is None
    finally:
        await controls.aclose()
        flow.close()


def test_low_level_cancellation_does_not_close_an_injected_client() -> None:
    flow = DeviceFlow()
    oauth = flow.client()
    try:
        authorization = oauth.start_device_authorization()
        cancelled = threading.Event()
        cancelled.set()
        with pytest.raises(CodexAuthenticationError, match="cancelled"):
            oauth.complete_device_authorization(authorization, cancelled=cancelled)
        oauth.close()
        assert not flow.clients[0].is_closed
        assert flow.calls == [CODEX_DEVICE_USER_CODE_URL]
    finally:
        flow.close()


def test_real_server_login_controls_are_authenticated_responsive_and_not_run_events(tmp_path: Path, monkeypatch) -> None:
    from harness.server.bootstrap import build_server_assembly
    from harness.server.protocol import parse_client_message, parse_server_message

    flow = DeviceFlow()
    controls = LocalProviderControls(tmp_path / "state", oauth_factory=flow.client)
    monkeypatch.setattr("harness.server.bootstrap.LocalProviderControls", lambda _root: controls)
    assembly = build_server_assembly(workspace=tmp_path, state_root=tmp_path / "state")
    token = assembly.auth_token_path.read_text().strip()
    frames = []
    try:
        with TestClient(assembly.app, client=("127.0.0.1", 12345)) as client:
            with client.websocket_connect("/v1/agent") as socket:
                assert socket.receive_json()["code"] == "authentication_failed"
                with pytest.raises(WebSocketDisconnect) as rejected:
                    socket.receive_json()
                assert rejected.value.code == 1008
            with client.websocket_connect("/v1/agent", headers={"authorization": f"Bearer {token}"}) as socket:
                socket.send_json({"type": "client.hello", "protocol_versions": [1], "client_name": "auth-fixture"})
                hello = socket.receive_json()
                assert "provider_controls" in hello["harness"]["capabilities"]
                def request(operation, request_id, **fields):
                    payload = {"type": "provider.request", "protocol_version": 1, "request_id": request_id,
                               "operation": operation, "provider": "openai_codex", **fields}
                    parse_client_message(payload)
                    socket.send_json(payload)
                    frame = socket.receive_json()
                    parse_server_message(frame)
                    frames.append(frame)
                    assert frame["request_id"] == request_id
                    return frame
                started = request("login", "begin")
                # TestClient runs the application on its own event-loop thread.
                assert flow.started.wait(5)
                socket.send_json({"type": "ping", "nonce": "responsive"})
                assert socket.receive_json()["nonce"] == "responsive"
                waiting = request("status", "status")
                assert waiting["data"]["login"]["user_code"] == "TEST-CODE"
                login_id = started["data"]["login"]["login_id"]
                assert request("cancel_login", "stop", login_id=login_id)["data"]["login"]["status"] == "cancelled"
                flow.release.set()
                # Malformed or reused operations fail without exposing provider details.
                assert request("logout", "begin")["code"] == "provider_request_failed"
        assert controls.store.load() is None
        assert all(frame["type"] in {"provider.result", "error"} for frame in frames)
        assert "refresh-secret" not in json.dumps(frames)
    finally:
        flow.release.set()
        flow.close()


@pytest.mark.asyncio
async def test_corrupt_credentials_do_not_prevent_discovery_or_logout(tmp_path: Path) -> None:
    controls = LocalProviderControls(tmp_path)
    controls.store.root.mkdir()
    controls.store.path.write_text("private corrupt token")
    controls.store.path.chmod(0o600)
    try:
        status = await command(controls, "status")
        assert status["authenticated"] is False
        assert "invalid" in status["error"]
        assert "private corrupt token" not in json.dumps(status)
        assert (await command(controls, "logout"))["removed"] is True
    finally:
        await controls.aclose()
