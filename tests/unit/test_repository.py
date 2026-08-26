from __future__ import annotations

import subprocess
from pathlib import Path

from harness.repo.discovery import (
    build_repository_manifest,
    collect_project_instructions,
    discover_instruction_files,
)
from harness.repo.index import StructuralIndex


def _git(root: Path, *args: str) -> None:
    subprocess.run(("git", *args), cwd=root, check=True, capture_output=True)


def test_build_manifest_discovers_git_language_and_commands(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='demo'\n[dependency-groups]\ndev=['pytest','ruff','pyright']\n",
        encoding="utf-8",
    )
    (tmp_path / "demo.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")

    manifest = build_repository_manifest(tmp_path)

    assert manifest.base_revision
    assert manifest.tracked_file_count == 2
    assert "python" in manifest.languages
    commands = {item.kind: item.command for item in manifest.commands}
    assert commands["test"] == "uv run pytest"
    assert commands["lint"] == "uv run ruff check ."
    assert commands["typecheck"] == "uv run pyright"
    rendered = manifest.to_compact_text()
    assert "Repository manifest" in rendered
    assert "tracked files: 2" in rendered


def test_instruction_override_replaces_same_directory_default(tmp_path: Path) -> None:
    nested = tmp_path / "services" / "payments"
    nested.mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("root guidance", encoding="utf-8")
    (tmp_path / "services" / "AGENTS.md").write_text("service guidance", encoding="utf-8")
    (nested / "AGENTS.md").write_text("ignored", encoding="utf-8")
    (nested / "AGENTS.override.md").write_text("payment override", encoding="utf-8")

    discovered = discover_instruction_files(tmp_path, nested)
    assert [path.name for path in discovered] == [
        "AGENTS.md",
        "AGENTS.md",
        "AGENTS.override.md",
    ]
    combined = collect_project_instructions(tmp_path, nested)
    assert "root guidance" in combined
    assert "service guidance" in combined
    assert "payment override" in combined
    assert "ignored" not in combined


def test_structural_index_extracts_python_symbols_and_updates_incrementally(tmp_path: Path) -> None:
    source = tmp_path / "service.py"
    source.write_text(
        "from helper import normalize\n\n"
        "class Service:\n"
        "    def run(self, value: str) -> str:\n"
        "        return normalize(value)\n",
        encoding="utf-8",
    )
    helper = tmp_path / "helper.py"
    helper.write_text("def normalize(value: str) -> str:\n    return value.strip()\n", encoding="utf-8")
    storage = tmp_path / ".index" / "symbols.json"
    index = StructuralIndex(tmp_path, storage)

    assert index.index_repository() == 2
    assert index.index_repository() == 0
    assert storage.exists()
    names = {
        symbol.qualified_name
        for record in index.files.values()
        for symbol in record.symbols
    }
    assert {"Service", "Service.run", "normalize"}.issubset(names)

    source.write_text(source.read_text(encoding="utf-8").replace("run", "execute"), encoding="utf-8")
    assert index.update_file(source)
    index.index_repository()
    names = {
        symbol.qualified_name
        for record in index.files.values()
        for symbol in record.symbols
    }
    assert "Service.execute" in names
    assert "Service.run" not in names


def test_search_and_render_map_respect_budget(tmp_path: Path) -> None:
    (tmp_path / "auth.py").write_text(
        "class AuthService:\n"
        "    def login(self, username: str) -> bool:\n"
        "        return bool(username)\n",
        encoding="utf-8",
    )
    (tmp_path / "test_auth.py").write_text(
        "def test_login():\n    assert True\n",
        encoding="utf-8",
    )
    index = StructuralIndex(tmp_path)
    index.index_repository()

    hits = index.search("auth login")
    assert hits
    assert any(hit.symbol == "AuthService.login" for hit in hits)
    rendered = index.render_map("auth login", max_tokens=60)
    assert "Repository map" in rendered
    assert len(rendered) <= 60 * 4 + 80


def test_python_edges_are_resolved_after_all_files_are_indexed(tmp_path: Path) -> None:
    (tmp_path / "consumer.py").write_text(
        "from provider import normalize\n\n"
        "def use(value: str) -> str:\n"
        "    return normalize(value)\n",
        encoding="utf-8",
    )
    (tmp_path / "provider.py").write_text(
        "def normalize(value: str) -> str:\n    return value.strip()\n",
        encoding="utf-8",
    )
    index = StructuralIndex(tmp_path)
    index.index_repository()

    use = next(
        symbol
        for record in index.files.values()
        for symbol in record.symbols
        if symbol.qualified_name == "use"
    )
    assert use.edges.get("calls")
    assert use.edges.get("imports") == ["provider.py"]
