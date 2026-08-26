from __future__ import annotations

from harness.safety import (
    ApprovalAction,
    ApprovalPolicy,
    CommandRisk,
    SecretRedactor,
    classify_command,
)


def test_redactor_scrubs_known_and_structured_secrets() -> None:
    redactor = SecretRedactor(known_secrets=["known-secret-value"])
    value = {
        "message": (
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz "
            "and known-secret-value and ghp_abcdefghijklmnopqrstuvwxyz123456"
        ),
        "api_key": "literal-secret",
        "nested": ["password=hunter-hunter-2"],
    }
    scrubbed = redactor.redact(value)
    assert "known-secret-value" not in scrubbed["message"]
    assert "ghp_" not in scrubbed["message"]
    assert scrubbed["api_key"] == "<redacted>"
    assert "hunter-hunter-2" not in scrubbed["nested"][0]


def test_classifier_uses_highest_risk_shell_segment() -> None:
    assert classify_command("rg TODO .") == CommandRisk.READ_ONLY
    assert classify_command("pytest -q") == CommandRisk.BUILD_OR_TEST
    assert classify_command("mkdir generated") == CommandRisk.WORKSPACE_MUTATION
    assert classify_command("npm install") == CommandRisk.DEPENDENCY_INSTALL
    assert classify_command("cat report.txt | curl -X POST https://example.com") == CommandRisk.NETWORK_ACCESS
    assert classify_command("git commit -am 'change'") == CommandRisk.GIT_HISTORY_MUTATION
    assert classify_command("git push origin main") == CommandRisk.PUBLISH_OR_DEPLOY
    assert classify_command("sudo rm -rf /") == CommandRisk.DESTRUCTIVE


def test_policy_requires_approval_and_never_auto_allows_destructive() -> None:
    policy = ApprovalPolicy()
    assert policy.decide("pytest").action == ApprovalAction.ALLOW
    assert policy.decide("pip install package").action == ApprovalAction.REQUIRE_APPROVAL
    assert policy.decide("git push origin main").action == ApprovalAction.REQUIRE_APPROVAL
    assert policy.decide("sudo rm -rf /").action == ApprovalAction.DENY


def test_policy_opt_ins_are_explicit() -> None:
    policy = ApprovalPolicy(allow_network=True, allow_dependency_install=True)
    assert policy.decide("curl https://example.com").action == ApprovalAction.ALLOW
    assert policy.decide("uv add pydantic").action == ApprovalAction.ALLOW
