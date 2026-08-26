"""Conservative command classification and execution policy."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from harness.models import CommandClass

_RISK_ORDER = {
    CommandClass.READ_ONLY: 0,
    CommandClass.BUILD_OR_TEST: 1,
    CommandClass.WORKSPACE_MUTATION: 2,
    CommandClass.DEPENDENCY_INSTALL: 3,
    CommandClass.NETWORK_ACCESS: 4,
    CommandClass.GIT_HISTORY_MUTATION: 5,
    CommandClass.PUBLISH_OR_DEPLOY: 6,
    CommandClass.DESTRUCTIVE: 7,
    CommandClass.UNKNOWN: 8,
}

_DESTRUCTIVE_PATTERNS = (
    r"\brm\s+-[^\n]*r[^\n]*f[^\n]*(?:/|~|\$HOME)(?:\s|$)",
    r"\b(?:mkfs|shutdown|reboot|halt|poweroff)\b",
    r"\bdd\s+if=",
    r"\bgit\s+clean\s+-[^\n]*[fx]",
    r"\bgit\s+reset\s+--hard\b",
    r"\bterraform\s+destroy\b",
    r"\bkubectl\s+delete\b",
)
_PUBLISH_PATTERNS = (
    r"\bgit\s+push\b",
    r"\b(?:npm|pnpm|yarn)\s+publish\b",
    r"\btwine\s+upload\b",
    r"\bdocker\s+push\b",
    r"\bgh\s+release\b",
    r"\bgcloud\s+(?:run\s+deploy|functions\s+deploy|app\s+deploy)\b",
    r"\bterraform\s+apply\b",
    r"\bkubectl\s+(?:apply|create|patch|replace)\b",
)
_NETWORK_COMMANDS = {"curl", "wget", "ssh", "scp", "sftp", "nc", "ncat", "telnet"}
_INSTALL_COMMANDS = {"pip", "pip3", "uv", "npm", "pnpm", "yarn", "bun", "poetry"}
_READ_COMMANDS = {
    "ls",
    "pwd",
    "cat",
    "head",
    "tail",
    "grep",
    "rg",
    "find",
    "stat",
    "wc",
    "cut",
    "sort",
    "uniq",
    "file",
    "which",
    "whereis",
    "printf",
    "echo",
}
_MUTATION_COMMANDS = {"mkdir", "touch", "cp", "mv", "rm", "ln", "chmod", "chown", "install"}
_BUILD_COMMANDS = {
    "pytest",
    "ruff",
    "pyright",
    "mypy",
    "tox",
    "nox",
    "make",
    "cmake",
    "ninja",
    "gradle",
    "mvn",
    "go",
    "cargo",
    "jest",
    "vitest",
    "eslint",
    "tsc",
}


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    command_class: CommandClass
    reason: str


@dataclass(slots=True)
class CommandPolicy:
    """Default-deny policy for operations beyond local read/build/mutation."""

    allow_workspace_mutation: bool = True
    allow_build_or_test: bool = True
    allow_dependency_install: bool = False
    network_mode: str = "deny"
    allowed_network_hosts: set[str] = field(default_factory=set)
    allow_git_history_mutation: bool = False
    allow_publish_or_deploy: bool = False
    allow_destructive: bool = False
    allow_unknown: bool = False

    def evaluate(self, command: str) -> PolicyDecision:
        command_class = classify_command(command)
        if command_class is CommandClass.READ_ONLY:
            return PolicyDecision(True, command_class, "read-only command")
        if command_class is CommandClass.BUILD_OR_TEST:
            return PolicyDecision(self.allow_build_or_test, command_class, "build/test policy")
        if command_class is CommandClass.WORKSPACE_MUTATION:
            return PolicyDecision(
                self.allow_workspace_mutation,
                command_class,
                "workspace mutation policy",
            )
        if command_class is CommandClass.DEPENDENCY_INSTALL:
            return PolicyDecision(
                self.allow_dependency_install,
                command_class,
                "dependency install policy",
            )
        if command_class is CommandClass.NETWORK_ACCESS:
            allowed = self.network_mode == "allow"
            return PolicyDecision(allowed, command_class, f"network mode is {self.network_mode}")
        if command_class is CommandClass.GIT_HISTORY_MUTATION:
            return PolicyDecision(
                self.allow_git_history_mutation,
                command_class,
                "git history mutation requires approval",
            )
        if command_class is CommandClass.PUBLISH_OR_DEPLOY:
            return PolicyDecision(
                self.allow_publish_or_deploy,
                command_class,
                "publish/deploy requires explicit approval",
            )
        if command_class is CommandClass.DESTRUCTIVE:
            return PolicyDecision(self.allow_destructive, command_class, "destructive command denied")
        return PolicyDecision(self.allow_unknown, command_class, "unclassified command denied")


def _first_word(segment: str) -> tuple[str, list[str]]:
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        return "", []
    while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
        tokens.pop(0)
    if tokens and tokens[0] in {"sudo", "env", "command", "time", "timeout"}:
        tokens.pop(0)
    return (tokens[0] if tokens else ""), tokens


def _classify_segment(segment: str) -> CommandClass:
    normalized = segment.strip()
    lowered = normalized.lower()
    if not normalized:
        return CommandClass.READ_ONLY
    if any(re.search(pattern, lowered) for pattern in _DESTRUCTIVE_PATTERNS):
        return CommandClass.DESTRUCTIVE
    if any(re.search(pattern, lowered) for pattern in _PUBLISH_PATTERNS):
        return CommandClass.PUBLISH_OR_DEPLOY

    executable, tokens = _first_word(normalized)
    executable = executable.rsplit("/", 1)[-1].lower()
    if not executable:
        return CommandClass.UNKNOWN

    if executable == "git":
        subcommand = tokens[1].lower() if len(tokens) > 1 else ""
        if subcommand in {
            "status",
            "diff",
            "log",
            "show",
            "blame",
            "ls-files",
            "rev-parse",
            "branch",
        }:
            return CommandClass.READ_ONLY
        if subcommand in {"commit", "rebase", "merge", "reset", "tag", "checkout", "switch"}:
            return CommandClass.GIT_HISTORY_MUTATION
        if subcommand in {"clone", "fetch", "pull", "remote", "submodule"}:
            return CommandClass.NETWORK_ACCESS
        if subcommand in {"add", "restore", "rm", "mv"}:
            return CommandClass.WORKSPACE_MUTATION
        return CommandClass.UNKNOWN

    if executable in _NETWORK_COMMANDS:
        return CommandClass.NETWORK_ACCESS
    if executable in _INSTALL_COMMANDS:
        joined = " ".join(tokens[1:]).lower()
        if any(word in joined.split() for word in {"install", "add", "sync", "update", "upgrade"}):
            return CommandClass.DEPENDENCY_INSTALL
        if executable in {"npm", "pnpm", "yarn", "bun"} and any(
            word in joined.split() for word in {"test", "lint", "build", "check"}
        ):
            return CommandClass.BUILD_OR_TEST
        if executable == "uv" and joined.startswith("run "):
            nested = joined[4:].strip()
            return _classify_segment(nested)
        return CommandClass.UNKNOWN
    if executable in _BUILD_COMMANDS:
        if executable == "go" and len(tokens) > 1 and tokens[1] in {"get", "install"}:
            return CommandClass.DEPENDENCY_INSTALL
        if executable == "cargo" and len(tokens) > 1 and tokens[1] in {"add", "install", "update"}:
            return CommandClass.DEPENDENCY_INSTALL
        return CommandClass.BUILD_OR_TEST
    if executable in _MUTATION_COMMANDS:
        return CommandClass.WORKSPACE_MUTATION
    if executable in _READ_COMMANDS:
        if executable == "find" and any(token in {"-delete", "-exec", "-execdir"} for token in tokens):
            return CommandClass.WORKSPACE_MUTATION
        return CommandClass.READ_ONLY
    if executable in {"python", "python3"}:
        joined = " ".join(tokens[1:]).lower()
        if "-m pytest" in joined or "-m compileall" in joined:
            return CommandClass.BUILD_OR_TEST
        return CommandClass.UNKNOWN
    if executable in {"sed", "perl"}:
        return (
            CommandClass.WORKSPACE_MUTATION
            if any("i" in token for token in tokens[1:] if token.startswith("-"))
            else CommandClass.READ_ONLY
        )
    return CommandClass.UNKNOWN


def classify_command(command: str) -> CommandClass:
    """Classify every shell segment and return the highest-risk result."""

    segments = re.split(r"(?:&&|\|\||;|\n)", command)
    classes = [_classify_segment(segment) for segment in segments if segment.strip()]
    if not classes:
        return CommandClass.READ_ONLY
    return max(classes, key=lambda value: _RISK_ORDER[value])
