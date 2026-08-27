from __future__ import annotations

import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from harness.repo.discovery import (
    build_repository_manifest,
    collect_project_instructions,
    discover_instruction_files,
)
from harness.repo.index import ParseResult, StructuralIndex, SymbolRecord


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


def test_typescript_fallback_masks_comments_and_literals_and_records_provenance(
    tmp_path: Path,
) -> None:
    source = tmp_path / "service.ts"
    source.write_text(
        "// export class FalseComment {}\n"
        "const example = 'function falseString() {}';\n"
        "/* import { fake } from 'not-real'; */\n"
        "import { normalize } from './normalize';\n"
        "export interface Request { value: string }\n"
        "export type Result = { ok: boolean };\n"
        "export const enum Status { Ready }\n"
        "export async function execute(value: string): Promise<Result> {\n"
        "  return { ok: Boolean(value) };\n"
        "}\n",
        encoding="utf-8",
    )
    index = StructuralIndex(tmp_path)

    assert index.index_repository() == 1

    record = index.files["service.ts"]
    assert record.parser_name == "typescript-outline-v2"
    assert record.parser_mode == "fallback"
    assert record.imports == ["./normalize"]
    assert {symbol.name for symbol in record.symbols} == {
        "Request",
        "Result",
        "Status",
        "execute",
    }


def test_custom_syntax_parser_is_incremental_and_atomically_published(tmp_path: Path) -> None:
    class CountingParser:
        name = "test-syntax-v1"
        mode = "syntax"
        languages = frozenset({"typescript"})

        def __init__(self) -> None:
            self.calls = 0
            self.fail = False

        def parse(self, path: str, text: str) -> ParseResult:
            self.calls += 1
            if self.fail:
                raise RuntimeError("parser provider failed")
            name = text.split()[1]
            return ParseResult(
                [
                    SymbolRecord(
                        symbol_id=f"symbol-{name}",
                        path=path,
                        qualified_name=name,
                        name=name,
                        kind="function",
                        signature=f"function {name}()",
                        start_line=1,
                        end_line=1,
                    )
                ],
                [],
            )

    parser = CountingParser()
    source = tmp_path / "worker.ts"
    source.write_text("function first() {}\n", encoding="utf-8")
    index = StructuralIndex(tmp_path, parsers=(parser,))

    assert index.index_repository() == 1
    assert index.index_repository() == 0
    assert parser.calls == 1
    assert index.files["worker.ts"].parser_name == "test-syntax-v1"
    published = index.snapshot

    parser.fail = True
    source.write_text("function second() {}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="parser provider failed"):
        index.index_repository()

    assert index.snapshot == published
    assert index.files["worker.ts"].symbols[0].name == "first()"


def test_large_files_get_bounded_head_and_tail_outlines(tmp_path: Path) -> None:
    source = tmp_path / "generated.py"
    source.write_text(
        "def first_entry() -> None:\n    pass\n"
        + ("# generated padding\n" * 70_000)
        + "def last_entry() -> None:\n    pass\n",
        encoding="utf-8",
    )
    index = StructuralIndex(tmp_path)

    assert index.index_repository() == 1

    record = index.files["generated.py"]
    assert record.size_bytes > 1_000_000
    assert record.parse_status == "outline"
    assert record.parser_name == "bounded-outline-v1"
    assert {symbol.name for symbol in record.symbols} == {"first_entry", "last_entry"}
    assert next(symbol for symbol in record.symbols if symbol.name == "last_entry").start_line > 70_000


def test_snapshot_readiness_staleness_and_persistence_are_explicit(tmp_path: Path) -> None:
    source = tmp_path / "module.py"
    source.write_text("def initial():\n    pass\n", encoding="utf-8")
    storage = tmp_path / ".index" / "symbols.json"
    index = StructuralIndex(tmp_path, storage)

    assert not index.ready
    assert index.snapshot.stale_paths == ("*",)
    assert index.index_repository() == 1
    ready = index.snapshot
    assert ready.ready
    assert ready.generation == 1
    with pytest.raises(FrozenInstanceError):
        ready.ready = False  # type: ignore[misc]

    index.mark_stale([source])
    assert not index.ready
    assert index.snapshot.stale_paths == ("module.py",)
    source.write_text("def changed():\n    pass\n", encoding="utf-8")
    assert index.update_file(source)
    assert index.ready
    assert index.snapshot.generation == 2

    index.save()
    assert not list(storage.parent.glob("*.tmp"))
    restored = StructuralIndex(tmp_path, storage)
    assert not restored.ready
    assert restored.snapshot.stale_paths == ("*",)
    assert restored.index_repository() == 0
    assert restored.ready
    assert restored.snapshot.generation == 2
