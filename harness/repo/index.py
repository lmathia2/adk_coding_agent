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
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

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
_MAX_PARSE_BYTES = 1_000_000
_OUTLINE_SLICE_BYTES = 128_000
_INDEX_VERSION = 2


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
    parser_name: str = "unknown"
    parser_mode: str = "fallback"
    calls: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SearchHit:
    path: str
    symbol: str | None
    score: float
    reason: str


@dataclass(frozen=True, slots=True)
class IndexSnapshot:
    """Immutable readiness view for one atomically published index generation."""

    generation: int
    fingerprint: str
    ready: bool
    stale_paths: tuple[str, ...]
    indexed_files: int


@dataclass(slots=True)
class ParseResult:
    """Provider-neutral structural parse result without source bodies."""

    symbols: list[SymbolRecord]
    imports: list[str]
    calls: dict[str, set[str]] = field(default_factory=dict)
    status: str = "ok"


class StructuralParser(Protocol):
    """Pluggable parser contract used by the structural index."""

    name: str
    mode: str
    languages: frozenset[str]

    def parse(self, path: str, text: str) -> ParseResult:
        """Extract a bounded structural projection from complete source text."""

        ...


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
            with suppress(AttributeError, ValueError):
                bases.append(ast.unparse(base))
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


def _parse_python(path: str, text: str) -> ParseResult:
    tree = ast.parse(text)
    visitor = _PythonVisitor(path)
    visitor.visit(tree)
    return ParseResult(visitor.symbols, sorted(set(visitor.imports)), visitor.calls)


class PythonAstParser:
    """Standard-library, syntax-backed Python parser."""

    name = "python-ast-v1"
    mode = "syntax"
    languages = frozenset({"python"})

    def parse(self, path: str, text: str) -> ParseResult:
        return _parse_python(path, text)


_TS_CLASS = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)[^\n{]*", re.MULTILINE)
_TS_INTERFACE = re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)[^\n{]*", re.MULTILINE)
_TS_TYPE = re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)[^\n=]*=", re.MULTILINE)
_TS_ENUM = re.compile(
    r"^\s*(?:export\s+)?(?:const\s+)?enum\s+([A-Za-z_$][\w$]*)[^\n{]*",
    re.MULTILINE,
)
_TS_FUNCTION = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*(\([^\n{;]*\))",
    re.MULTILINE,
)
_TS_ARROW = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(\([^\n=;]*\)|[A-Za-z_$][\w$]*)\s*=>",
    re.MULTILINE,
)
_TS_IMPORT = re.compile(r"(?:from\s+|require\s*\(\s*)['\"]([^'\"]+)['\"]")


def _mask_javascript_non_code(text: str, *, mask_literals: bool = True) -> str:
    """Blank comments and optionally literals while retaining offsets and newlines."""

    output = list(text)
    index = 0
    state = "code"
    quote = ""
    while index < len(text):
        current = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "code":
            if current == "/" and following == "/":
                output[index] = output[index + 1] = " "
                index += 2
                state = "line_comment"
                continue
            if current == "/" and following == "*":
                output[index] = output[index + 1] = " "
                index += 2
                state = "block_comment"
                continue
            if current in {"'", '"', "`"}:
                quote = current
                if mask_literals:
                    output[index] = " "
                index += 1
                state = "string"
                continue
        elif state == "line_comment":
            if current == "\n":
                state = "code"
            else:
                output[index] = " "
            index += 1
            continue
        elif state == "block_comment":
            if current == "*" and following == "/":
                output[index] = output[index + 1] = " "
                index += 2
                state = "code"
                continue
            if current != "\n":
                output[index] = " "
            index += 1
            continue
        else:
            if current == "\\":
                if mask_literals:
                    output[index] = " "
                if index + 1 < len(text):
                    if mask_literals and output[index + 1] != "\n":
                        output[index + 1] = " "
                    index += 2
                    continue
            if current == quote:
                if mask_literals:
                    output[index] = " "
                index += 1
                state = "code"
                continue
            if mask_literals and current != "\n":
                output[index] = " "
            index += 1
            continue
        index += 1
    return "".join(output)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _parse_typescript_outline(path: str, text: str, *, line_offset: int = 0) -> ParseResult:
    masked = _mask_javascript_non_code(text)
    records: list[SymbolRecord] = []
    patterns = (
        (_TS_CLASS, "class"),
        (_TS_INTERFACE, "interface"),
        (_TS_TYPE, "type"),
        (_TS_ENUM, "enum"),
        (_TS_FUNCTION, "function"),
        (_TS_ARROW, "function"),
    )
    for pattern, kind in patterns:
        for match in pattern.finditer(masked):
            name = match.group(1)
            suffix = match.group(2) if match.lastindex and match.lastindex >= 2 else ""
            line = _line_number(masked, match.start()) + line_offset
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
    comments_masked = _mask_javascript_non_code(text, mask_literals=False)
    imports = sorted(set(_TS_IMPORT.findall(comments_masked)))
    return ParseResult(records, imports)


class TypeScriptOutlineParser:
    """Deterministic ctags-like fallback used until a syntax provider is installed.

    The parser masks strings and comments before extracting declarations, preventing
    the most damaging false positives of a raw source regex. Its fallback provenance
    is retained in every ``FileRecord`` so it cannot be mistaken for a semantic AST.
    """

    name = "typescript-outline-v2"
    mode = "fallback"
    languages = frozenset({"typescript", "javascript"})

    def parse(self, path: str, text: str) -> ParseResult:
        return _parse_typescript_outline(path, text)


_PY_OUTLINE_DECLARATION = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?P<async>async)\s+)?(?P<kind>class|def)\s+"
    r"(?P<name>[A-Za-z_]\w*)(?P<suffix>[^:#\n]*(?:\([^\n]*\))?)\s*:"
)
_PY_OUTLINE_IMPORT = re.compile(
    r"^\s*(?:from\s+(?P<from>[A-Za-z0-9_.]+)\s+import\s+(?P<names>[^#]+)|"
    r"import\s+(?P<imports>[^#]+))"
)


def _python_outline(path: str, text: str, *, line_offset: int) -> ParseResult:
    records: list[SymbolRecord] = []
    imports: list[str] = []
    scopes: list[tuple[int, str, str]] = []
    for local_line, line in enumerate(text.splitlines(), start=1):
        import_match = _PY_OUTLINE_IMPORT.match(line)
        if import_match:
            if import_match.group("from"):
                module = import_match.group("from")
                for imported in import_match.group("names").split(","):
                    name = imported.strip().split(" as ", 1)[0].strip()
                    if name and name != "*":
                        imports.append(f"{module}.{name}")
            else:
                for imported in import_match.group("imports").split(","):
                    name = imported.strip().split(" as ", 1)[0].strip()
                    if name:
                        imports.append(name)
        match = _PY_OUTLINE_DECLARATION.match(line)
        if not match:
            continue
        indent_text = match.group("indent").replace("\t", "    ")
        indent = len(indent_text)
        while scopes and scopes[-1][0] >= indent:
            scopes.pop()
        name = match.group("name")
        declaration = match.group("kind")
        if declaration == "class":
            kind = "class"
            signature = f"class {name}{match.group('suffix').strip()}"
        else:
            kind = "method" if any(scope[2] == "class" for scope in scopes) else "function"
            prefix = "async def" if match.group("async") else "def"
            signature = f"{prefix} {name}{match.group('suffix').strip()}"
        qualified = ".".join([*(scope[1] for scope in scopes), name])
        absolute_line = local_line + line_offset
        records.append(
            SymbolRecord(
                symbol_id=_symbol_id(path, qualified, kind),
                path=path,
                qualified_name=qualified,
                name=name,
                kind=kind,
                signature=signature[:400],
                start_line=absolute_line,
                end_line=absolute_line,
                edges={"contained_by": [".".join(scope[1] for scope in scopes)]} if scopes else {},
            )
        )
        scopes.append((indent, name, declaration))
    return ParseResult(records, sorted(set(imports)), status="outline")


def _bounded_outline(
    path: str,
    language: str,
    slices: Sequence[tuple[str, int]],
) -> ParseResult:
    records: list[SymbolRecord] = []
    imports: set[str] = set()
    for text, line_offset in slices:
        result = (
            _python_outline(path, text, line_offset=line_offset)
            if language == "python"
            else _parse_typescript_outline(path, text, line_offset=line_offset)
        )
        records.extend(result.symbols)
        imports.update(result.imports)
    unique: dict[str, SymbolRecord] = {}
    for record in sorted(records, key=lambda item: (item.start_line, item.symbol_id)):
        unique.setdefault(record.symbol_id, record)
    return ParseResult(list(unique.values()), sorted(imports), status="outline")


def _source_projection(path: Path) -> tuple[str, int, str | None, list[tuple[str, int]]]:
    """Hash a source file and return either full text or bounded head/tail slices."""

    size = path.stat().st_size
    digest = hashlib.sha256()
    tail_start = max(0, size - _OUTLINE_SLICE_BYTES)
    newlines_before_tail = 0
    position = 0
    with path.open("rb") as source:
        while chunk := source.read(128 * 1024):
            digest.update(chunk)
            count_end = min(len(chunk), max(0, tail_start - position))
            newlines_before_tail += chunk[:count_end].count(b"\n")
            position += len(chunk)
    if size <= _MAX_PARSE_BYTES:
        return digest.hexdigest(), size, path.read_text(encoding="utf-8", errors="replace"), []

    with path.open("rb") as source:
        head_data = source.read(_OUTLINE_SLICE_BYTES)
        source.seek(tail_start)
        tail_data = source.read(_OUTLINE_SLICE_BYTES)
    if b"\n" in head_data:
        head_data = head_data[: head_data.rfind(b"\n") + 1]
    tail_offset = newlines_before_tail
    if tail_start and b"\n" in tail_data:
        partial_end = tail_data.find(b"\n") + 1
        tail_offset += tail_data[:partial_end].count(b"\n")
        tail_data = tail_data[partial_end:]
    slices = [
        (head_data.decode("utf-8", errors="replace"), 0),
        (tail_data.decode("utf-8", errors="replace"), tail_offset),
    ]
    return digest.hexdigest(), size, None, slices


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


def _clone_file(record: FileRecord) -> FileRecord:
    return FileRecord(
        path=record.path,
        content_sha256=record.content_sha256,
        language=record.language,
        size_bytes=record.size_bytes,
        parse_status=record.parse_status,
        parser_name=record.parser_name,
        parser_mode=record.parser_mode,
        symbols=[
            SymbolRecord(
                symbol_id=symbol.symbol_id,
                path=symbol.path,
                qualified_name=symbol.qualified_name,
                name=symbol.name,
                kind=symbol.kind,
                signature=symbol.signature,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                edges={key: list(values) for key, values in symbol.edges.items()},
            )
            for symbol in record.symbols
        ],
        imports=list(record.imports),
        calls={symbol_id: list(names) for symbol_id, names in record.calls.items()},
    )


def _index_fingerprint(files: Mapping[str, FileRecord]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        record = files[path]
        digest.update(
            f"{path}\0{record.content_sha256}\0{record.parser_name}\0{record.parse_status}\n".encode()
        )
    return digest.hexdigest()


class StructuralIndex:
    """Content-hash incremental symbol index with optional JSON persistence."""

    def __init__(
        self,
        root: Path,
        storage_path: Path | None = None,
        *,
        parsers: Iterable[StructuralParser] = (),
    ) -> None:
        self.root = root.resolve()
        self.storage_path = storage_path
        self._files: dict[str, FileRecord] = {}
        parser_by_language: dict[str, StructuralParser] = {}
        for parser in (PythonAstParser(), TypeScriptOutlineParser(), *parsers):
            for language in parser.languages:
                parser_by_language[language] = parser
        self._parsers = parser_by_language
        self._snapshot = IndexSnapshot(0, _index_fingerprint({}), False, ("*",), 0)
        if storage_path and storage_path.exists():
            self.load()

    @property
    def snapshot(self) -> IndexSnapshot:
        """Return immutable generation, readiness, and staleness metadata."""

        return self._snapshot

    @property
    def files(self) -> dict[str, FileRecord]:
        """Return a defensive copy of the published structural generation."""

        return {path: _clone_file(record) for path, record in self._files.items()}

    @property
    def ready(self) -> bool:
        return self._snapshot.ready

    def mark_stale(self, paths: Iterable[str | Path]) -> IndexSnapshot:
        """Mark known workspace mutations without publishing a partial index."""

        stale = set(self._snapshot.stale_paths)
        for path in paths:
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = self.root / candidate
            stale.add(self._relative(candidate))
        self._snapshot = IndexSnapshot(
            generation=self._snapshot.generation,
            fingerprint=self._snapshot.fingerprint,
            ready=False,
            stale_paths=tuple(sorted(stale)),
            indexed_files=len(self._files),
        )
        return self._snapshot

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

    def _record_from_projection(
        self,
        relative: str,
        language: str,
        projection: tuple[str, int, str | None, list[tuple[str, int]]],
    ) -> FileRecord:
        digest, size, text, slices = projection
        parser = self._parsers[language]
        try:
            if text is None:
                result = _bounded_outline(relative, language, slices)
                parser_name = "bounded-outline-v1"
                parser_mode = "fallback"
            else:
                result = parser.parse(relative, text)
                parser_name = parser.name
                parser_mode = parser.mode
        except (SyntaxError, ValueError):
            result = ParseResult([], [], status="parse_error")
            parser_name = parser.name
            parser_mode = parser.mode
        return FileRecord(
            path=relative,
            content_sha256=digest,
            language=language,
            size_bytes=size,
            parse_status=result.status,
            parser_name=parser_name,
            parser_mode=parser_mode,
            symbols=result.symbols,
            imports=sorted(set(result.imports)),
            calls={key: sorted(values) for key, values in sorted(result.calls.items())},
        )

    def _parse_record(self, path: Path, relative: str) -> FileRecord:
        language = _language_for(path)
        if language is None:
            raise ValueError(f"unsupported source file: {relative}")
        return self._record_from_projection(
            relative,
            language,
            _source_projection(path),
        )

    def _expected_parser_name(self, language: str, size: int) -> str:
        if size > _MAX_PARSE_BYTES:
            return "bounded-outline-v1"
        return self._parsers[language].name

    def _next_snapshot(
        self,
        files: Mapping[str, FileRecord],
        *,
        ready: bool,
        stale_paths: Iterable[str] = (),
        changed: bool,
    ) -> IndexSnapshot:
        return IndexSnapshot(
            generation=self._snapshot.generation + int(changed),
            fingerprint=_index_fingerprint(files),
            ready=ready,
            stale_paths=tuple(sorted(set(stale_paths))),
            indexed_files=len(files),
        )

    def update_file(self, path: Path) -> bool:
        relative = self._relative(path)
        candidate = {key: _clone_file(value) for key, value in self._files.items()}
        try:
            if not path.exists():
                changed = relative in candidate
                candidate.pop(relative, None)
            elif path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
                return False
            else:
                record = self._parse_record(path, relative)
                previous = candidate.get(relative)
                changed = (
                    previous is None
                    or previous.content_sha256 != record.content_sha256
                    or previous.parser_name != record.parser_name
                )
                if changed:
                    candidate[relative] = record
            stale = set(self._snapshot.stale_paths)
            stale.discard(relative)
            if changed:
                self._resolve_edges(candidate)
        except Exception:
            self.mark_stale([relative])
            raise
        self._files = candidate
        self._snapshot = self._next_snapshot(
            candidate,
            ready=not stale,
            stale_paths=stale,
            changed=changed,
        )
        return changed

    def index_repository(self) -> int:
        changed_paths: set[str] = set()
        try:
            current = {self._relative(path): path for path in self._iter_source_files()}
            candidate = {key: _clone_file(value) for key, value in self._files.items()}
            changed_paths = set(candidate) - set(current)
            for relative in changed_paths:
                del candidate[relative]
            for relative, path in sorted(current.items()):
                language = _language_for(path)
                if language is None:
                    continue
                projection = _source_projection(path)
                previous = candidate.get(relative)
                expected_parser = self._expected_parser_name(language, projection[1])
                if (
                    previous is None
                    or previous.content_sha256 != projection[0]
                    or previous.parser_name != expected_parser
                ):
                    changed_paths.add(relative)
                    record = self._record_from_projection(relative, language, projection)
                    candidate[relative] = record
            self._resolve_edges(candidate)
            snapshot = self._next_snapshot(
                candidate,
                ready=True,
                changed=bool(changed_paths),
            )
            if self.storage_path:
                self._persist(candidate, snapshot)
        except Exception:
            stale_paths = changed_paths or {"*"}
            self._snapshot = IndexSnapshot(
                generation=self._snapshot.generation,
                fingerprint=self._snapshot.fingerprint,
                ready=False,
                stale_paths=tuple(sorted(stale_paths)),
                indexed_files=len(self._files),
            )
            raise
        self._files = candidate
        self._snapshot = snapshot
        return len(changed_paths)

    def _resolve_edges(self, files: Mapping[str, FileRecord]) -> None:
        symbols = [symbol for file in files.values() for symbol in file.symbols]
        by_id = {symbol.symbol_id: symbol for symbol in symbols}
        lookup: dict[str, list[str]] = {}
        for symbol in symbols:
            symbol.edges.pop("calls", None)
            symbol.edges.pop("imports", None)
            lookup.setdefault(symbol.qualified_name.lower(), []).append(symbol.symbol_id)
            lookup.setdefault(symbol.name.lower(), []).append(symbol.symbol_id)
        for symbol in symbols:
            for values in symbol.edges.values():
                values[:] = list(dict.fromkeys(values))
        for file in files.values():
            for symbol in file.symbols:
                for called in file.calls.get(symbol.symbol_id, ()):
                    candidates = (
                        lookup.get(called.lower())
                        or lookup.get(called.rsplit(".", 1)[-1].lower())
                        or []
                    )
                    targets = [
                        target
                        for target in candidates
                        if target != symbol.symbol_id and target in by_id
                    ]
                    if targets:
                        symbol.edges.setdefault("calls", []).extend(targets)
                        symbol.edges["calls"] = list(dict.fromkeys(symbol.edges["calls"]))
        module_lookup = {
            Path(path).with_suffix("").as_posix().replace("/", "."): path for path in files
        }
        for file in files.values():
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
        for file in self._files.values():
            for symbol in file.symbols:
                for values in symbol.edges.values():
                    for target in values:
                        reverse_degree[target] = reverse_degree.get(target, 0) + 1
        hits: list[SearchHit] = []
        for file in self._files.values():
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
            for file in self._files.values()
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

    def _persist(
        self,
        files: Mapping[str, FileRecord],
        snapshot: IndexSnapshot,
    ) -> None:
        assert self.storage_path is not None
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _INDEX_VERSION,
            "root": self.root.as_posix(),
            "snapshot": asdict(snapshot),
            "files": [asdict(files[path]) for path in sorted(files)],
        }
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.storage_path.parent,
                prefix=f".{self.storage_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(payload, temporary, sort_keys=True, indent=2)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self.storage_path)
        finally:
            if temporary_path is not None:
                with suppress(FileNotFoundError):
                    temporary_path.unlink()

    def save(self) -> None:
        if self.storage_path is not None:
            self._persist(self._files, self._snapshot)

    def load(self) -> None:
        if self.storage_path is None:
            return
        payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        stored_root = payload.get("root")
        if stored_root and Path(stored_root).resolve() != self.root:
            raise ValueError(f"index belongs to {stored_root}, not {self.root}")
        loaded: dict[str, FileRecord] = {}
        for raw_item in payload.get("files", []):
            item = dict(raw_item)
            symbols = [SymbolRecord(**symbol) for symbol in item.pop("symbols", [])]
            item.setdefault("parser_name", "legacy")
            item.setdefault("parser_mode", "fallback")
            item.setdefault("calls", {})
            record = FileRecord(**item, symbols=symbols)
            loaded[record.path] = record
        self._files = loaded
        self._resolve_edges(self._files)
        prior_snapshot = payload.get("snapshot", {})
        self._snapshot = IndexSnapshot(
            generation=int(prior_snapshot.get("generation", 0)),
            fingerprint=_index_fingerprint(self._files),
            ready=False,
            stale_paths=("*",),
            indexed_files=len(self._files),
        )
