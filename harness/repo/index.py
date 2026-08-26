"""Small, incremental structural index for repository orientation.

The index stores signatures and relationships, never complete source bodies. Lexical
search remains the primary precision mechanism; this map helps the agent choose where
to search and read next.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

_EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
}
_SUPPORTED_EXTENSIONS = {
    ".py",
    ".pyi",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
}
_MAX_FILE_BYTES = 1_000_000


@dataclass(slots=True)
class SymbolRecord:
    symbol_id: str
    path: str
    qualified_name: str
    name: str
    kind: str
    signature: str
    start_line: int
    end_line: int
    edges: dict[str, list[str]] = field(default_factory=dict)


@dataclass(slots=True)
class FileRecord:
    path: str
    content_sha256: str
    language: str
    size_bytes: int
    parse_status: str
    symbols: list[SymbolRecord] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SearchHit:
    path: str
    symbol: str | None
    score: float
    reason: str


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _symbol_id(path: str, qualified_name: str, kind: str) -> str:
    return hashlib.sha256(f"{path}\0{qualified_name}\0{kind}".encode()).hexdigest()[:20]


def _annotation(node: ast.expr | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except (AttributeError, ValueError):
        return ""


def _python_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    arguments: list[str] = []
    positional = [*node.args.posonlyargs, *node.args.args]
    defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)
    for argument, default in zip(positional, defaults, strict=True):
        text = argument.arg
        annotation = _annotation(argument.annotation)
        if annotation:
            text += f": {annotation}"
        if default is not None:
            try:
                text += f" = {ast.unparse(default)}"
            except (AttributeError, ValueError):
                text += " = ..."
        arguments.append(text)
    if node.args.vararg:
        arguments.append(f"*{node.args.vararg.arg}")
    for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
        text = argument.arg
        annotation = _annotation(argument.annotation)
        if annotation:
            text += f": {annotation}"
        if default is not None:
            text += " = ..."
        arguments.append(text)
    if node.args.kwarg:
        arguments.append(f"**{node.args.kwarg.arg}")
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    result = f"{prefix} {node.name}({', '.join(arguments)})"
    returns = _annotation(node.returns)
    if returns:
        result += f" -> {returns}"
    return result


def _dotted_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.scope: list[str] = []
        self.symbols: list[SymbolRecord] = []
        self.imports: list[str] = []
        self.calls: dict[str, set[str]] = {}

    def _add(self, node: ast.AST, name: str, kind: str, signature: str) -> SymbolRecord:
        qualified = ".".join([*self.scope, name])
        record = SymbolRecord(
            symbol_id=_symbol_id(self.path, qualified, kind),
            path=self.path,
            qualified_name=qualified,
            name=name,
            kind=kind,
            signature=signature,
            start_line=getattr(node, "lineno", 1),
            end_line=getattr(node, "end_lineno", getattr(node, "lineno", 1)),
        )
        if self.scope:
            record.edges.setdefault("contained_by", []).append(".".join(self.scope))
        self.symbols.append(record)
        return record

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = []
        for base in node.bases:
            try:
                bases.append(ast.unparse(base))
            except (AttributeError, ValueError):
                pass
        signature = f"class {node.name}" + (f"({', '.join(bases)})" if bases else "")
        record = self._add(node, node.name, "class", signature)
        if bases:
            record.edges["inherits"] = bases
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        kind = "method" if self.scope else "function"
        record = self._add(node, node.name, kind, _python_signature(node))
        self.scope.append(node.name)
        self.calls[record.symbol_id] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                name = _dotted_name(child.func)
                if name:
                    self.calls[record.symbol_id].add(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.extend(alias.name for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            self.imports.append(f"{module}.{alias.name}".strip("."))


def _parse_python(path: str, text: str) -> tuple[list[SymbolRecord], list[str], dict[str, set[str]]]:
    tree = ast.parse(text)
    visitor = _PythonVisitor(path)
    visitor.visit(tree)
    return visitor.symbols, sorted(set(visitor.imports)), visitor.calls


_TS_CLASS = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)[^\n{]*", re.MULTILINE)
_TS_INTERFACE = re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)[^\n{]*", re.MULTILINE)
_TS_FUNCTION = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*(\([^\n{;]*\))",
    re.MULTILINE,
)
_TS_ARROW = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(\([^\n=;]*\)|[A-Za-z_$][\w$]*)\s*=>",
    re.MULTILINE,
)
_TS_IMPORT = re.compile(r"(?:from\s+|require\s*\(\s*)['\"]([^'\"]+)['\"]")


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _parse_typescript(path: str, text: str) -> tuple[list[SymbolRecord], list[str]]:
    records: list[SymbolRecord] = []
    patterns = (
        (_TS_CLASS, "class"),
        (_TS_INTERFACE, "interface"),
        (_TS_FUNCTION, "function"),
        (_TS_ARROW, "function"),
    )
    for pattern, kind in patterns:
        for match in pattern.finditer(text):
            name = match.group(1)
            suffix = match.group(2) if match.lastindex and match.lastindex >= 2 else ""
            line = _line_number(text, match.start())
            records.append(
                SymbolRecord(
                    symbol_id=_symbol_id(path, name, kind),
                    path=path,
                    qualified_name=name,
                    name=name,
                    kind=kind,
                    signature=(f"{kind} {name}{suffix}" if suffix else f"{kind} {name}"),
                    start_line=line,
                    end_line=line,
                )
            )
    records.sort(key=lambda item: (item.start_line, item.qualified_name, item.kind))
    return records, sorted(set(_TS_IMPORT.findall(text)))


def _language_for(path: Path) -> str | None:
    if path.suffix.lower() in {".py", ".pyi"}:
        return "python"
    if path.suffix.lower() in {".ts", ".tsx"}:
        return "typescript"
    if path.suffix.lower() in {".js", ".jsx", ".mjs", ".cjs"}:
        return "javascript"
    return None


def _tokenize(query: str) -> list[str]:
    return [token for token in re.findall(r"[A-Za-z0-9_.$/-]+", query.lower()) if token]


class StructuralIndex:
    """Content-hash incremental symbol index with optional JSON persistence."""

    def __init__(self, root: Path, storage_path: Path | None = None) -> None:
        self.root = root.resolve()
        self.storage_path = storage_path
        self.files: dict[str, FileRecord] = {}
        self._calls: dict[str, set[str]] = {}
        if storage_path and storage_path.exists():
            self.load()

    def _relative(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise ValueError(f"path {resolved} is outside repository {self.root}") from exc

    def _iter_source_files(self) -> Iterable[Path]:
        for directory, names, filenames in os.walk(self.root):
            names[:] = sorted(name for name in names if name not in _EXCLUDED_DIRS)
            base = Path(directory)
            for filename in sorted(filenames):
                path = base / filename
                if path.suffix.lower() in _SUPPORTED_EXTENSIONS:
                    yield path

    def update_file(self, path: Path) -> bool:
        relative = self._relative(path)
        if not path.exists():
            changed = relative in self.files
            self.files.pop(relative, None)
            return changed
        if path.suffix.lower() not in _SUPPORTED_EXTENSIONS or path.stat().st_size > _MAX_FILE_BYTES:
            return False
        data = path.read_bytes()
        digest = _sha256(data)
        previous = self.files.get(relative)
        if previous and previous.content_sha256 == digest:
            return False
        language = _language_for(path)
        if language is None:
            return False
        text = data.decode("utf-8", errors="replace")
        try:
            if language == "python":
                symbols, imports, calls = _parse_python(relative, text)
                self._calls.update(calls)
            else:
                symbols, imports = _parse_typescript(relative, text)
            status = "ok"
        except (SyntaxError, ValueError):
            symbols, imports, status = [], [], "parse_error"
        self.files[relative] = FileRecord(
            path=relative,
            content_sha256=digest,
            language=language,
            size_bytes=len(data),
            parse_status=status,
            symbols=symbols,
            imports=imports,
        )
        return True

    def index_repository(self) -> int:
        current = {self._relative(path): path for path in self._iter_source_files()}
        changed = 0
        for relative in sorted(set(self.files) - set(current)):
            del self.files[relative]
            changed += 1
        for path in current.values():
            changed += int(self.update_file(path))
        self._resolve_edges()
        if self.storage_path:
            self.save()
        return changed

    def _resolve_edges(self) -> None:
        symbols = [symbol for file in self.files.values() for symbol in file.symbols]
        by_id = {symbol.symbol_id: symbol for symbol in symbols}
        lookup: dict[str, list[str]] = {}
        for symbol in symbols:
            lookup.setdefault(symbol.qualified_name.lower(), []).append(symbol.symbol_id)
            lookup.setdefault(symbol.name.lower(), []).append(symbol.symbol_id)
        for symbol in symbols:
            for values in symbol.edges.values():
                values[:] = list(dict.fromkeys(values))
            for called in sorted(self._calls.get(symbol.symbol_id, ())):
                candidates = lookup.get(called.lower()) or lookup.get(called.rsplit(".", 1)[-1].lower()) or []
                targets = [target for target in candidates if target != symbol.symbol_id and target in by_id]
                if targets:
                    symbol.edges.setdefault("calls", []).extend(targets)
                    symbol.edges["calls"] = list(dict.fromkeys(symbol.edges["calls"]))
        module_lookup = {Path(path).with_suffix("").as_posix().replace("/", "."): path for path in self.files}
        for file in self.files.values():
            for imported in file.imports:
                for module, target_path in module_lookup.items():
                    if imported == module or imported.startswith(module + "."):
                        for symbol in file.symbols:
                            symbol.edges.setdefault("imports", []).append(target_path)
                            symbol.edges["imports"] = list(dict.fromkeys(symbol.edges["imports"]))
                        break

    def search(
        self,
        query: str,
        *,
        changed_paths: Iterable[str] = (),
        recent_paths: Iterable[str] = (),
        limit: int = 20,
    ) -> list[SearchHit]:
        tokens = _tokenize(query)
        changed = set(changed_paths)
        recent = set(recent_paths)
        reverse_degree: dict[str, int] = {}
        for file in self.files.values():
            for symbol in file.symbols:
                for values in symbol.edges.values():
                    for target in values:
                        reverse_degree[target] = reverse_degree.get(target, 0) + 1
        hits: list[SearchHit] = []
        for file in self.files.values():
            file_text = file.path.lower()
            file_lexical = sum(1 for token in tokens if token in file_text)
            if file_lexical or not tokens:
                score = file_lexical * 0.35 + (0.15 if file.path in changed else 0) + (0.05 if file.path in recent else 0)
                hits.append(SearchHit(file.path, None, score, "file path match"))
            for symbol in file.symbols:
                haystack = f"{symbol.qualified_name} {symbol.signature} {file.path}".lower()
                lexical = sum(1 for token in tokens if token in haystack)
                if not lexical and tokens:
                    continue
                centrality = min(reverse_degree.get(symbol.symbol_id, 0), 10) / 10
                test_adjacency = 1.0 if "test" in file.path.lower() else 0.0
                score = (
                    lexical * 0.35
                    + centrality * 0.15
                    + (0.15 if file.path in changed else 0)
                    + test_adjacency * 0.10
                    + (0.05 if file.path in recent else 0)
                )
                hits.append(
                    SearchHit(
                        path=file.path,
                        symbol=symbol.qualified_name,
                        score=score,
                        reason=f"{symbol.kind} signature match",
                    )
                )
        hits.sort(key=lambda item: (-item.score, item.path, item.symbol or ""))
        return hits[:limit]

    def render_map(
        self,
        query: str,
        *,
        changed_paths: Iterable[str] = (),
        recent_paths: Iterable[str] = (),
        max_tokens: int = 1_500,
    ) -> str:
        """Render ranked signatures under a deterministic character budget."""

        max_chars = max_tokens * 4
        hits = self.search(query, changed_paths=changed_paths, recent_paths=recent_paths, limit=200)
        by_path: dict[str, list[SymbolRecord]] = {}
        symbol_lookup = {
            (symbol.path, symbol.qualified_name): symbol
            for file in self.files.values()
            for symbol in file.symbols
        }
        for hit in hits:
            if hit.symbol:
                symbol = symbol_lookup.get((hit.path, hit.symbol))
                if symbol and symbol not in by_path.setdefault(hit.path, []):
                    by_path[hit.path].append(symbol)
            else:
                by_path.setdefault(hit.path, [])
        lines = ["Repository map (signatures only):"]
        for path in by_path:
            candidate = [path]
            for symbol in sorted(by_path[path], key=lambda item: (item.start_line, item.qualified_name)):
                edge_parts = []
                if symbol.edges.get("calls"):
                    edge_parts.append(f"calls {len(symbol.edges['calls'])}")
                if symbol.edges.get("imports"):
                    edge_parts.append(f"imports {len(symbol.edges['imports'])}")
                suffix = f" [{', '.join(edge_parts)}]" if edge_parts else ""
                candidate.append(f"  L{symbol.start_line}: {symbol.signature}{suffix}")
            for line in candidate:
                if len("\n".join([*lines, line])) > max_chars:
                    return "\n".join(lines) + "\n[repository map truncated to budget]"
                lines.append(line)
        return "\n".join(lines)

    def save(self) -> None:
        if self.storage_path is None:
            return
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "root": self.root.as_posix(),
            "files": [asdict(self.files[path]) for path in sorted(self.files)],
        }
        temporary = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
        temporary.replace(self.storage_path)

    def load(self) -> None:
        if self.storage_path is None:
            return
        payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        loaded: dict[str, FileRecord] = {}
        for item in payload.get("files", []):
            symbols = [SymbolRecord(**symbol) for symbol in item.pop("symbols", [])]
            record = FileRecord(**item, symbols=symbols)
            loaded[record.path] = record
        self.files = loaded
        self._resolve_edges()
