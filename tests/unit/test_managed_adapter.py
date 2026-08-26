from __future__ import annotations

from pathlib import Path

from harness.tools.adk_adapter import create_adk_tools


def test_managed_adapter_blocks_unapproved_network_and_redacts(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MY_API_KEY", "super-secret-api-key-value")
    monkeypatch.setenv("ADK_CODING_REDACT_ENV_VARS", "MY_API_KEY")
    tools = create_adk_tools(tmp_path)

    tools.write(
        "secret.txt",
        "token=super-secret-api-key-value\n",
        expected_absent=True,
    )
    read_result = tools.read("secret.txt")
    assert "super-secret-api-key-value" not in read_result["model_text"]
    assert "<redacted>" in read_result["model_text"]

    blocked = tools.bash("curl https://example.com")
    assert blocked["status"] == "blocked"
    assert blocked["approval_required"] is True
    assert blocked["risk"] == "network_access"


def test_exact_write_replay_uses_receipt_without_repeating_side_effect(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ADK_CODING_STATE_DIR", str(tmp_path / "state"))
    tools = create_adk_tools(tmp_path)

    first = tools.write("result.txt", "stable\n", expected_absent=True)
    second = tools.write("result.txt", "stable\n", expected_absent=True)

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert second["replayed"] is True
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "stable\n"
