"""Pi terminal dialogs → real authenticated server → synthetic provider HTTP."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import socket
from pathlib import Path

import httpx
import pytest
import uvicorn

from harness.ai.codex_auth import CODEX_DEVICE_USER_CODE_URL, CODEX_TOKEN_URL, CodexOAuthClient
from harness.ai.controls import LocalProviderControls
from harness.server.bootstrap import build_server_assembly


@pytest.mark.asyncio
async def test_terminal_login_cancel_logout_over_production_websocket(tmp_path: Path, monkeypatch) -> None:
    client = Path(__file__).resolve().parents[2] / "clients/terminal/dist/test/auth-client-fixture.js"
    node = shutil.which("node")
    if not node or not client.is_file():
        pytest.skip("build clients/terminal to run the cross-language authentication gate")
    attempts = 0
    exchanges = 0
    clients: list[httpx.Client] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts, exchanges
        if str(request.url) == CODEX_DEVICE_USER_CODE_URL:
            attempts += 1
            return httpx.Response(200, json={"device_auth_id": f"private-device-{attempts}", "user_code": "TEST-CODE", "interval": 1})
        if str(request.url) == CODEX_TOKEN_URL:
            exchanges += 1
            payload = base64.urlsafe_b64encode(json.dumps({"https://api.openai.com/auth": {"chatgpt_account_id": "fixture-123456"}}).encode()).decode().rstrip("=")
            return httpx.Response(200, json={"access_token": f"header.{payload}.signature", "refresh_token": "refresh-secret", "expires_in": 3600})
        if json.loads(request.content)["device_auth_id"] == "private-device-1":
            return httpx.Response(403, json={"error": "deviceauth_authorization_pending"})
        return httpx.Response(200, json={"authorization_code": "private-code", "code_verifier": "private-verifier"})

    def oauth() -> CodexOAuthClient:
        client = httpx.Client(transport=httpx.MockTransport(handler))
        clients.append(client)
        return CodexOAuthClient(client=client)

    controls = LocalProviderControls(tmp_path / "state", oauth_factory=oauth)
    monkeypatch.setattr("harness.server.bootstrap.LocalProviderControls", lambda _root: controls)
    assembly = build_server_assembly(workspace=tmp_path, state_root=tmp_path / "state")
    assert assembly.auth_token_path is not None
    token = assembly.auth_token_path.read_text().strip()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(assembly.app, log_level="error"))
    serving = asyncio.create_task(server.serve(sockets=[listener]))
    process = None
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if serving.done():
                    await serving
                await asyncio.sleep(0.01)
        process = await asyncio.create_subprocess_exec(node, str(client),
            env={"PATH": os.environ.get("PATH", ""), "ADK_TEST_TOKEN": token,
                 "ADK_TEST_URL": f"ws://127.0.0.1:{listener.getsockname()[1]}/v1/agent"},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=25)
        assert process.returncode == 0, stderr.decode()
        assert json.loads(stdout) == {"login": True, "cancel": True, "logout": True, "transcript_entries": 0}
        assert attempts == 2 and exchanges == 1
        assert controls.store.load() is None
        assert assembly.coordinator.conversations.store.threads("local-user") == []
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=10)
        listener.close()
        for client in clients:
            client.close()
