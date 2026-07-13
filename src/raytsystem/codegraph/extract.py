from __future__ import annotations

import ast
import base64
import importlib
import io
import json
import os
import posixpath
import re
import subprocess
import sys
import tokenize
import tomllib
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raytsystem.codegraph.contracts import (
    CodeEdge,
    CodeNode,
    CodeNodeKind,
    CodeRelation,
    EdgeConfidence,
    SourceLocation,
)
from raytsystem.codegraph.detect import DetectedFile
from raytsystem.codegraph.security import (
    CodeGraphSecurityError,
    contains_sensitive_text,
    safe_source_name,
    sanitize_label,
    sanitize_metadata,
)
from raytsystem.contracts import canonical_json_bytes, derive_id, sha256_hex

EXTRACTOR_NAME = "raytsystem_codegraph"
EXTRACTOR_VERSION = "1.2.0"
_ROUTE_METHODS = frozenset({"delete", "get", "head", "options", "patch", "post", "put"})
_RATIONALE = re.compile(r"^(?:NOTE|WHY|HACK|RATIONALE|SECURITY|TODO)\s*:?\s*(.+)$", re.IGNORECASE)
_MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)#?]+)(?:#[^)]+)?\)")
_SQL_TABLE = re.compile(
    r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[\"`\[]?([A-Za-z_][\w$]*)[\"`\]]?\.)?"
    r"[\"`\[]?([A-Za-z_][\w$]*)[\"`\]]?",
    re.IGNORECASE,
)
_SQL_REFERENCE = re.compile(
    r"\bREFERENCES\s+(?:[\"`\[]?([A-Za-z_][\w$]*)[\"`\]]?\.)?"
    r"[\"`\[]?([A-Za-z_][\w$]*)[\"`\]]?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PendingEdge:
    source: str
    target_ref: str
    relation: CodeRelation
    confidence: EdgeConfidence
    source_file: str
    source_location: SourceLocation | None
    content_fingerprint: str
    metadata: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target_ref": self.target_ref,
            "relation": self.relation.value,
            "confidence": self.confidence.value,
            "source_file": self.source_file,
            "source_location": (
                None
                if self.source_location is None
                else self.source_location.model_dump(mode="json")
            ),
            "content_fingerprint": self.content_fingerprint,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PendingEdge:
        raw_location = payload.get("source_location")
        return cls(
            source=str(payload["source"]),
            target_ref=str(payload["target_ref"]),
            relation=CodeRelation(str(payload["relation"])),
            confidence=EdgeConfidence(str(payload["confidence"])),
            source_file=str(payload["source_file"]),
            source_location=(
                None if raw_location is None else SourceLocation.model_validate(raw_location)
            ),
            content_fingerprint=str(payload["content_fingerprint"]),
            metadata=sanitize_metadata(
                {str(key): str(value) for key, value in dict(payload.get("metadata", {})).items()}
            ),
        )


@dataclass(frozen=True)
class FileExtraction:
    path: str
    content_sha256: str
    language: str
    nodes: tuple[CodeNode, ...]
    edges: tuple[CodeEdge, ...]
    pending_edges: tuple[PendingEdge, ...]
    diagnostics: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "content_sha256": self.content_sha256,
            "language": self.language,
            "nodes": [node.model_dump(mode="json") for node in self.nodes],
            "edges": [edge.model_dump(mode="json") for edge in self.edges],
            "pending_edges": [edge.to_dict() for edge in self.pending_edges],
            "diagnostics": list(self.diagnostics),
            "extractor": EXTRACTOR_NAME,
            "extractor_version": EXTRACTOR_VERSION,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FileExtraction:
        if (
            payload.get("extractor") != EXTRACTOR_NAME
            or payload.get("extractor_version") != EXTRACTOR_VERSION
        ):
            raise CodeGraphSecurityError("Code graph cache uses an incompatible extractor")
        return cls(
            path=str(payload["path"]),
            content_sha256=str(payload["content_sha256"]),
            language=str(payload["language"]),
            nodes=tuple(CodeNode.model_validate(node) for node in payload.get("nodes", [])),
            edges=tuple(CodeEdge.model_validate(edge) for edge in payload.get("edges", [])),
            pending_edges=tuple(
                PendingEdge.from_dict(edge) for edge in payload.get("pending_edges", [])
            ),
            diagnostics=tuple(str(value) for value in payload.get("diagnostics", [])),
        )


def extractor_fingerprint() -> str:
    return sha256_hex(
        canonical_json_bytes(
            {
                "name": EXTRACTOR_NAME,
                "version": EXTRACTOR_VERSION,
                "languages": [
                    "javascript",
                    "json",
                    "markdown",
                    "python",
                    "sql",
                    "toml",
                    "tsx",
                    "typescript",
                    "yaml",
                ],
                "confidence": [value.value for value in EdgeConfidence],
            }
        )
    )


def stable_node_id(
    kind: CodeNodeKind,
    *,
    path: str | None,
    qualified_name: str,
    ordinal: int = 1,
) -> str:
    return derive_id(
        "cnode",
        {
            "kind": kind.value,
            "path": path,
            "qualified_name": qualified_name,
            "ordinal": ordinal,
        },
    )


def stable_edge_id(
    source: str,
    target: str,
    relation: CodeRelation,
    *,
    source_file: str,
    source_location: SourceLocation | None,
) -> str:
    return derive_id(
        "cedge",
        {
            "source": source,
            "target": target,
            "relation": relation.value,
            "source_file": source_file,
            "source_location": (
                None
                if source_location is None
                else source_location.model_dump(
                    mode="python",
                    exclude={"schema_name", "schema_version", "id_scheme_version", "extensions"},
                )
            ),
        },
    )


def _location(node: ast.AST) -> SourceLocation:
    start_line = max(1, int(getattr(node, "lineno", 1)))
    start_column = max(0, int(getattr(node, "col_offset", 0)))
    end_line = max(start_line, int(getattr(node, "end_lineno", start_line)))
    end_column = max(0, int(getattr(node, "end_col_offset", start_column)))
    return SourceLocation(
        start_line=start_line,
        start_column=start_column,
        end_line=end_line,
        end_column=end_column,
    )


def _point_location(node: Any) -> SourceLocation:
    start = node.start_point
    end = node.end_point
    return SourceLocation(
        start_line=int(start[0]) + 1,
        start_column=int(start[1]),
        end_line=int(end[0]) + 1,
        end_column=int(end[1]),
    )


def _fragment_fingerprint(data: bytes, location: SourceLocation | None, qualifier: str = "") -> str:
    if location is None:
        return sha256_hex(data + qualifier.encode("utf-8"))
    lines = data.splitlines(keepends=True)
    start = max(0, location.start_line - 1)
    end = min(len(lines), location.end_line)
    return sha256_hex(b"".join(lines[start:end]) + qualifier.encode("utf-8"))


def _module_name(path: str) -> str:
    pure = Path(path)
    parts = list(pure.with_suffix("").parts)
    if parts[:1] == ["src"]:
        parts = parts[1:]
    elif parts[:2] == ["web", "src"]:
        parts = ["web", *parts[2:]]
    if parts and parts[-1] in {"__init__", "index"}:
        parts.pop()
    return ".".join(parts) or pure.stem


def _new_node(
    kind: CodeNodeKind,
    *,
    label: str,
    path: str,
    qualified_name: str,
    fingerprint: str,
    location: SourceLocation | None = None,
    ordinal: int = 1,
    metadata: dict[str, str] | None = None,
) -> CodeNode:
    return CodeNode(
        node_id=stable_node_id(
            kind,
            path=path,
            qualified_name=qualified_name,
            ordinal=ordinal,
        ),
        kind=kind,
        label=sanitize_label(label),
        qualified_name=safe_source_name(qualified_name),
        path=path,
        location=location,
        content_fingerprint=fingerprint,
        extractor=EXTRACTOR_NAME,
        extractor_version=EXTRACTOR_VERSION,
        metadata=sanitize_metadata(metadata or {}),
    )


def _direct_edge(
    source: str,
    target: str,
    relation: CodeRelation,
    *,
    file: DetectedFile,
    location: SourceLocation | None = None,
    confidence: EdgeConfidence = EdgeConfidence.EXTRACTED,
    metadata: dict[str, str] | None = None,
) -> CodeEdge:
    fingerprint = _fragment_fingerprint(file.data, location, relation.value)
    return CodeEdge(
        edge_id=stable_edge_id(
            source,
            target,
            relation,
            source_file=file.path,
            source_location=location,
        ),
        source=source,
        target=target,
        relation=relation,
        confidence=confidence,
        source_file=file.path,
        source_location=location,
        extractor=EXTRACTOR_NAME,
        extractor_version=EXTRACTOR_VERSION,
        content_fingerprint=fingerprint,
        metadata=sanitize_metadata(metadata or {}),
    )


def _pending_edge(
    source: str,
    target_ref: str,
    relation: CodeRelation,
    *,
    file: DetectedFile,
    location: SourceLocation | None,
    confidence: EdgeConfidence,
    metadata: dict[str, str] | None = None,
) -> PendingEdge:
    return PendingEdge(
        source=source,
        target_ref=target_ref,
        relation=relation,
        confidence=confidence,
        source_file=file.path,
        source_location=location,
        content_fingerprint=_fragment_fingerprint(file.data, location, relation.value),
        metadata=sanitize_metadata(metadata or {}),
    )


def _base_nodes(file: DetectedFile) -> tuple[CodeNode, CodeNode, CodeEdge]:
    file_node = _new_node(
        CodeNodeKind.FILE,
        label=Path(file.path).name,
        path=file.path,
        qualified_name=file.path,
        fingerprint=file.content_sha256,
        metadata={"language": file.language, "resolution_key": f"file:{file.path}"},
    )
    module_name = _module_name(file.path)
    module_node = _new_node(
        CodeNodeKind.MODULE,
        label=module_name,
        path=file.path,
        qualified_name=module_name,
        fingerprint=file.content_sha256,
        metadata={"language": file.language, "resolution_key": f"module:{module_name}"},
    )
    return (
        file_node,
        module_node,
        _direct_edge(
            file_node.node_id,
            module_node.node_id,
            CodeRelation.DEFINES,
            file=file,
        ),
    )


def _ast_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _ast_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if isinstance(node, ast.Call):
        return _ast_name(node.func)
    if isinstance(node, ast.Subscript):
        return _ast_name(node.value)
    return ""


def _route_metadata(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, str] | None:
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
            continue
        method = decorator.func.attr.casefold()
        if method not in _ROUTE_METHODS or not decorator.args:
            continue
        first = decorator.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return method.upper(), first.value
    return None


class _NestedCallVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[ast.Call] = []

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return


def _python_comments(file: DetectedFile, nodes: list[CodeNode], edges: list[CodeEdge]) -> None:
    try:
        tokens = tokenize.generate_tokens(
            io.StringIO(file.data.decode("utf-8", errors="replace")).readline
        )
        comments = [token for token in tokens if token.type == tokenize.COMMENT]
    except (tokenize.TokenError, IndentationError):
        return
    source_nodes = [node for node in nodes if node.location is not None]
    module = next((node for node in nodes if node.kind is CodeNodeKind.MODULE), None)
    for token in comments:
        text = token.string.lstrip("#").strip()
        match = _RATIONALE.match(text)
        if match is None:
            continue
        location = SourceLocation(
            start_line=token.start[0],
            start_column=token.start[1],
            end_line=token.end[0],
            end_column=token.end[1],
        )
        qualified = f"{file.path}:rationale:{token.start[0]}"
        rationale = _new_node(
            CodeNodeKind.RATIONALE,
            label=match.group(1),
            path=file.path,
            qualified_name=qualified,
            fingerprint=_fragment_fingerprint(file.data, location),
            location=location,
            metadata={"tag": text.split(":", maxsplit=1)[0].casefold()},
        )
        nodes.append(rationale)
        enclosing = [
            node
            for node in source_nodes
            if node.location is not None
            and node.location.start_line <= token.start[0] <= node.location.end_line
        ]
        owner = (
            min(
                enclosing,
                key=lambda item: (
                    (item.location or location).end_line - (item.location or location).start_line
                ),
            )
            if enclosing
            else module
        )
        if owner is not None:
            edges.append(
                _direct_edge(
                    owner.node_id,
                    rationale.node_id,
                    CodeRelation.EXPLAINED_BY,
                    file=file,
                    location=location,
                )
            )


def _extract_python(file: DetectedFile) -> FileExtraction:
    file_node, module_node, file_module = _base_nodes(file)
    nodes = [file_node, module_node]
    edges = [file_module]
    pending: list[PendingEdge] = []
    diagnostics: list[str] = []
    try:
        tree = ast.parse(file.data.decode("utf-8", errors="replace"), filename=file.path)
    except (SyntaxError, ValueError):
        diagnostics.append("syntax_error")
        module_node = module_node.model_copy(
            update={"metadata": module_node.metadata | {"parse_status": "syntax_error"}}
        )
        nodes[1] = module_node
        return FileExtraction(
            path=file.path,
            content_sha256=file.content_sha256,
            language=file.language,
            nodes=tuple(sorted(nodes, key=lambda item: item.node_id)),
            edges=tuple(sorted(edges, key=lambda item: item.edge_id)),
            pending_edges=(),
            diagnostics=tuple(diagnostics),
        )

    counts: Counter[tuple[CodeNodeKind, str]] = Counter()

    def visit_body(body: Iterable[ast.stmt], parent: CodeNode, scope: tuple[str, ...]) -> None:
        for statement in body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualified = ".".join((*scope, statement.name))
                is_test = statement.name.startswith("test_") or file.path.startswith("tests/")
                kind = (
                    CodeNodeKind.TEST
                    if is_test
                    else (
                        CodeNodeKind.METHOD
                        if parent.kind is CodeNodeKind.CLASS
                        else CodeNodeKind.FUNCTION
                    )
                )
                counts[(kind, qualified)] += 1
                location = _location(statement)
                symbol = _new_node(
                    kind,
                    label=statement.name,
                    path=file.path,
                    qualified_name=qualified,
                    ordinal=counts[(kind, qualified)],
                    fingerprint=_fragment_fingerprint(file.data, location),
                    location=location,
                    metadata={
                        "module": module_node.qualified_name,
                        "symbol": statement.name,
                        "resolution_key": f"qualified:{module_node.qualified_name}.{qualified}",
                    },
                )
                nodes.append(symbol)
                edges.append(
                    _direct_edge(
                        parent.node_id,
                        symbol.node_id,
                        CodeRelation.DEFINES,
                        file=file,
                        location=location,
                    )
                )
                route = _route_metadata(statement)
                if route is not None:
                    method, route_path = route
                    endpoint_name = f"{method} {route_path}"
                    endpoint = _new_node(
                        CodeNodeKind.API_ENDPOINT,
                        label=endpoint_name,
                        path=file.path,
                        qualified_name=f"{qualified}:{method}:{route_path}",
                        fingerprint=_fragment_fingerprint(file.data, location, endpoint_name),
                        location=location,
                        metadata={"method": method, "route": route_path, "handler": symbol.node_id},
                    )
                    nodes.append(endpoint)
                    edges.append(
                        _direct_edge(
                            symbol.node_id,
                            endpoint.node_id,
                            CodeRelation.IMPLEMENTS,
                            file=file,
                            location=location,
                        )
                    )
                visitor = _NestedCallVisitor()
                for child in statement.body:
                    visitor.visit(child)
                for call in visitor.calls:
                    callee = _ast_name(call.func)
                    if not callee:
                        continue
                    relation = (
                        CodeRelation.TESTS if kind is CodeNodeKind.TEST else CodeRelation.CALLS
                    )
                    pending.append(
                        _pending_edge(
                            symbol.node_id,
                            f"symbol:{callee.rsplit('.', maxsplit=1)[-1]}",
                            relation,
                            file=file,
                            location=_location(call),
                            confidence=EdgeConfidence.INFERRED,
                            metadata={"callee": safe_source_name(callee)},
                        )
                    )
                visit_body(statement.body, symbol, (*scope, statement.name))
            elif isinstance(statement, ast.ClassDef):
                qualified = ".".join((*scope, statement.name))
                counts[(CodeNodeKind.CLASS, qualified)] += 1
                location = _location(statement)
                symbol = _new_node(
                    CodeNodeKind.CLASS,
                    label=statement.name,
                    path=file.path,
                    qualified_name=qualified,
                    ordinal=counts[(CodeNodeKind.CLASS, qualified)],
                    fingerprint=_fragment_fingerprint(file.data, location),
                    location=location,
                    metadata={
                        "module": module_node.qualified_name,
                        "symbol": statement.name,
                        "resolution_key": f"qualified:{module_node.qualified_name}.{qualified}",
                    },
                )
                nodes.append(symbol)
                edges.append(
                    _direct_edge(
                        parent.node_id,
                        symbol.node_id,
                        CodeRelation.DEFINES,
                        file=file,
                        location=location,
                    )
                )
                for base in statement.bases:
                    name = _ast_name(base)
                    if name:
                        pending.append(
                            _pending_edge(
                                symbol.node_id,
                                f"symbol:{name.rsplit('.', maxsplit=1)[-1]}",
                                CodeRelation.INHERITS,
                                file=file,
                                location=_location(base),
                                confidence=EdgeConfidence.INFERRED,
                                metadata={"base": safe_source_name(name)},
                            )
                        )
                visit_body(statement.body, symbol, (*scope, statement.name))
            elif isinstance(statement, ast.Import):
                for alias in statement.names:
                    pending.append(
                        _pending_edge(
                            parent.node_id,
                            f"module:{alias.name}",
                            CodeRelation.IMPORTS,
                            file=file,
                            location=_location(statement),
                            confidence=EdgeConfidence.INFERRED,
                            metadata={"import": safe_source_name(alias.name)},
                        )
                    )
            elif isinstance(statement, ast.ImportFrom) and statement.module:
                target = statement.module
                if statement.level:
                    module_parts = module_node.qualified_name.split(".")[: -statement.level]
                    target = ".".join((*module_parts, statement.module))
                pending.append(
                    _pending_edge(
                        parent.node_id,
                        f"module:{target}",
                        CodeRelation.IMPORTS,
                        file=file,
                        location=_location(statement),
                        confidence=EdgeConfidence.INFERRED,
                        metadata={"import": safe_source_name(target)},
                    )
                )

    visit_body(tree.body, module_node, ())
    _python_comments(file, nodes, edges)
    return FileExtraction(
        path=file.path,
        content_sha256=file.content_sha256,
        language=file.language,
        nodes=tuple(sorted(nodes, key=lambda item: item.node_id)),
        edges=tuple(
            sorted({edge.edge_id: edge for edge in edges}.values(), key=lambda item: item.edge_id)
        ),
        pending_edges=tuple(
            sorted(
                pending,
                key=lambda item: (
                    item.source,
                    item.target_ref,
                    item.relation.value,
                    0 if item.source_location is None else item.source_location.start_line,
                ),
            )
        ),
        diagnostics=tuple(sorted(set(diagnostics))),
    )


def _tree_sitter_parser(language: str) -> Any:
    try:
        tree_sitter = importlib.import_module("tree_sitter")
        if language == "typescript":
            grammar = importlib.import_module("tree_sitter_typescript").language_typescript()
        elif language == "tsx":
            grammar = importlib.import_module("tree_sitter_typescript").language_tsx()
        else:
            grammar = importlib.import_module("tree_sitter_javascript").language()
        parsed_language = tree_sitter.Language(grammar)
        return tree_sitter.Parser(parsed_language)
    except (ImportError, AttributeError, TypeError) as error:
        raise CodeGraphSecurityError("Configured tree-sitter grammar is unavailable") from error


def _node_text(data: bytes, node: Any) -> str:
    return data[int(node.start_byte) : int(node.end_byte)].decode("utf-8", errors="replace")


def _named_child(node: Any, field: str) -> Any | None:
    return node.child_by_field_name(field)


def _extract_js_like(file: DetectedFile) -> FileExtraction:
    file_node, module_node, file_module = _base_nodes(file)
    nodes = [file_node, module_node]
    edges = [file_module]
    pending: list[PendingEdge] = []
    diagnostics: list[str] = []
    parser = _tree_sitter_parser(file.language)
    tree = parser.parse(file.data)
    if tree.root_node.has_error:
        diagnostics.append("syntax_error")
    counts: Counter[tuple[CodeNodeKind, str]] = Counter()

    def walk(node: Any, parent_symbol: CodeNode, scope: tuple[str, ...]) -> None:
        current_parent = parent_symbol
        current_scope = scope
        kind: CodeNodeKind | None = None
        name_node: Any | None = None
        if node.type in {
            "class_declaration",
            "abstract_class_declaration",
            "interface_declaration",
        }:
            kind = CodeNodeKind.CLASS
            name_node = _named_child(node, "name")
        elif node.type in {"function_declaration", "generator_function_declaration"}:
            kind = CodeNodeKind.TEST if file.path.startswith("tests/") else CodeNodeKind.FUNCTION
            name_node = _named_child(node, "name")
        elif node.type in {"method_definition", "method_signature"}:
            kind = CodeNodeKind.METHOD
            name_node = _named_child(node, "name")
        if kind is not None and name_node is not None:
            name = _node_text(file.data, name_node).strip()
            if name:
                qualified = ".".join((*scope, name))
                counts[(kind, qualified)] += 1
                location = _point_location(node)
                symbol = _new_node(
                    kind,
                    label=name,
                    path=file.path,
                    qualified_name=qualified,
                    ordinal=counts[(kind, qualified)],
                    fingerprint=_fragment_fingerprint(file.data, location),
                    location=location,
                    metadata={
                        "module": module_node.qualified_name,
                        "symbol": name,
                        "resolution_key": f"qualified:{module_node.qualified_name}.{qualified}",
                    },
                )
                nodes.append(symbol)
                edges.append(
                    _direct_edge(
                        parent_symbol.node_id,
                        symbol.node_id,
                        CodeRelation.DEFINES,
                        file=file,
                        location=location,
                    )
                )
                current_parent = symbol
                current_scope = (*scope, name)
                heritage_text = _node_text(file.data, node)
                heritage = re.search(
                    r"\b(?:extends|implements)\s+([A-Za-z_$][\w$]*)", heritage_text
                )
                if heritage is not None and kind is CodeNodeKind.CLASS:
                    relation = (
                        CodeRelation.IMPLEMENTS
                        if "implements" in heritage_text[: heritage.end()]
                        else CodeRelation.INHERITS
                    )
                    pending.append(
                        _pending_edge(
                            symbol.node_id,
                            f"symbol:{heritage.group(1)}",
                            relation,
                            file=file,
                            location=location,
                            confidence=EdgeConfidence.INFERRED,
                            metadata={"base": heritage.group(1)},
                        )
                    )
        if node.type == "import_statement":
            text = _node_text(file.data, node)
            match = re.search(r"(?:from\s+)?[\"']([^\"']+)[\"']", text)
            if match is not None:
                pending.append(
                    _pending_edge(
                        current_parent.node_id,
                        f"module:{match.group(1)}",
                        CodeRelation.IMPORTS,
                        file=file,
                        location=_point_location(node),
                        confidence=EdgeConfidence.INFERRED,
                        metadata={"import": safe_source_name(match.group(1))},
                    )
                )
        elif node.type == "call_expression":
            function = _named_child(node, "function")
            callee = "" if function is None else _node_text(file.data, function).strip()
            simple = re.split(r"[.([]", callee)[-1] if callee else ""
            if simple and simple not in {"if", "for", "while"}:
                relation = (
                    CodeRelation.TESTS
                    if current_parent.kind is CodeNodeKind.TEST
                    else CodeRelation.CALLS
                )
                pending.append(
                    _pending_edge(
                        current_parent.node_id,
                        f"symbol:{simple}",
                        relation,
                        file=file,
                        location=_point_location(node),
                        confidence=EdgeConfidence.INFERRED,
                        metadata={"callee": safe_source_name(callee)},
                    )
                )
        elif node.type == "comment":
            text = _node_text(file.data, node).lstrip("/ *").rstrip("*/ ").strip()
            match = _RATIONALE.match(text)
            if match is not None:
                location = _point_location(node)
                rationale = _new_node(
                    CodeNodeKind.RATIONALE,
                    label=match.group(1),
                    path=file.path,
                    qualified_name=f"{file.path}:rationale:{location.start_line}",
                    fingerprint=_fragment_fingerprint(file.data, location),
                    location=location,
                )
                nodes.append(rationale)
                edges.append(
                    _direct_edge(
                        current_parent.node_id,
                        rationale.node_id,
                        CodeRelation.EXPLAINED_BY,
                        file=file,
                        location=location,
                    )
                )
        for child in node.children:
            walk(child, current_parent, current_scope)

    walk(tree.root_node, module_node, ())
    return FileExtraction(
        path=file.path,
        content_sha256=file.content_sha256,
        language=file.language,
        nodes=tuple(
            sorted({node.node_id: node for node in nodes}.values(), key=lambda item: item.node_id)
        ),
        edges=tuple(
            sorted({edge.edge_id: edge for edge in edges}.values(), key=lambda item: item.edge_id)
        ),
        pending_edges=tuple(
            sorted(
                pending,
                key=lambda item: (
                    item.source,
                    item.target_ref,
                    item.relation.value,
                    0 if item.source_location is None else item.source_location.start_line,
                ),
            )
        ),
        diagnostics=tuple(sorted(set(diagnostics))),
    )


def _config_node(file: DetectedFile, label: str, qualified_name: str) -> CodeNode:
    return _new_node(
        CodeNodeKind.CONFIGURATION,
        label=label,
        path=file.path,
        qualified_name=qualified_name,
        fingerprint=sha256_hex(
            canonical_json_bytes({"file": file.content_sha256, "key": qualified_name})
        ),
        metadata={"configuration": qualified_name},
    )


def _extract_configuration(file: DetectedFile) -> FileExtraction:
    file_node, module_node, file_module = _base_nodes(file)
    nodes = [file_node, module_node]
    edges = [file_module]
    pending: list[PendingEdge] = []
    diagnostics: list[str] = []
    document: Any = None
    try:
        text = file.data.decode("utf-8", errors="strict")
        if file.language == "json":
            document = json.loads(text)
        elif file.language == "toml":
            document = tomllib.loads(text)
        else:
            document = {
                match.group(1): None
                for match in re.finditer(
                    r"(?m)^([A-Za-z_][A-Za-z0-9_.-]{0,127})\s*:",
                    text,
                )
            }
    except (UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError):
        diagnostics.append("syntax_error")
    if isinstance(document, dict):
        for key in sorted(str(value) for value in document)[:256]:
            config = _config_node(file, key, f"{file.path}:{key}")
            nodes.append(config)
            edges.append(
                _direct_edge(file_node.node_id, config.node_id, CodeRelation.DEFINES, file=file)
            )
        project = document.get("project") if isinstance(document.get("project"), dict) else None
        package_name: str | None = None
        dependencies: list[str] = []
        if project is not None and isinstance(project.get("name"), str):
            package_name = project["name"]
            raw_dependencies = project.get("dependencies", [])
            if isinstance(raw_dependencies, list):
                dependencies = [
                    str(value).split(" ", maxsplit=1)[0].split("=", maxsplit=1)[0]
                    for value in raw_dependencies
                ]
        elif file.path.endswith("package.json") and isinstance(document.get("name"), str):
            package_name = str(document["name"])
            for section in ("dependencies", "devDependencies", "peerDependencies"):
                raw_dependencies = document.get(section, {})
                if isinstance(raw_dependencies, dict):
                    dependencies.extend(str(value) for value in raw_dependencies)
        if package_name:
            package = _new_node(
                CodeNodeKind.PACKAGE,
                label=package_name,
                path=file.path,
                qualified_name=package_name,
                fingerprint=sha256_hex(
                    canonical_json_bytes({"file": file.content_sha256, "package": package_name})
                ),
                metadata={"resolution_key": f"package:{package_name}"},
            )
            nodes.append(package)
            edges.append(
                _direct_edge(file_node.node_id, package.node_id, CodeRelation.DEFINES, file=file)
            )
            for dependency in sorted(set(dependencies)):
                pending.append(
                    _pending_edge(
                        package.node_id,
                        f"dependency:{dependency}",
                        CodeRelation.DEPENDS_ON,
                        file=file,
                        location=None,
                        confidence=EdgeConfidence.EXTRACTED,
                        metadata={"dependency": safe_source_name(dependency)},
                    )
                )
    return FileExtraction(
        path=file.path,
        content_sha256=file.content_sha256,
        language=file.language,
        nodes=tuple(
            sorted({node.node_id: node for node in nodes}.values(), key=lambda item: item.node_id)
        ),
        edges=tuple(
            sorted({edge.edge_id: edge for edge in edges}.values(), key=lambda item: item.edge_id)
        ),
        pending_edges=tuple(sorted(pending, key=lambda item: (item.source, item.target_ref))),
        diagnostics=tuple(sorted(set(diagnostics))),
    )


def _extract_markdown(file: DetectedFile) -> FileExtraction:
    file_node, module_node, file_module = _base_nodes(file)
    nodes = [file_node, module_node]
    edges = [file_module]
    pending: list[PendingEdge] = []
    reference_source = module_node
    text = file.data.decode("utf-8", errors="replace")
    headings = [line.lstrip("#").strip() for line in text.splitlines() if line.startswith("#")]
    if file.path.startswith("ops/decisions/") or Path(file.path).name.upper().startswith("ADR-"):
        label = headings[0] if headings else Path(file.path).stem
        adr = _new_node(
            CodeNodeKind.ADR,
            label=label,
            path=file.path,
            qualified_name=file.path,
            fingerprint=file.content_sha256,
            metadata={"resolution_key": f"adr:{file.path}"},
        )
        nodes.append(adr)
        edges.append(_direct_edge(file_node.node_id, adr.node_id, CodeRelation.DEFINES, file=file))
        reference_source = adr
    for match in _MARKDOWN_LINK.finditer(text):
        target = match.group(1).strip()
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        resolved = (Path(file.path).parent / target).as_posix()
        normalized = posixpath.normpath(resolved)
        if normalized == ".." or normalized.startswith("../") or normalized.startswith("/"):
            continue
        pending.append(
            _pending_edge(
                reference_source.node_id,
                f"file:{normalized}",
                CodeRelation.REFERENCES,
                file=file,
                location=None,
                confidence=EdgeConfidence.EXTRACTED,
                metadata={},
            )
        )
    return FileExtraction(
        path=file.path,
        content_sha256=file.content_sha256,
        language=file.language,
        nodes=tuple(sorted(nodes, key=lambda item: item.node_id)),
        edges=tuple(sorted(edges, key=lambda item: item.edge_id)),
        pending_edges=tuple(sorted(pending, key=lambda item: item.target_ref)),
    )


def _extract_sql(file: DetectedFile) -> FileExtraction:
    file_node, module_node, file_module = _base_nodes(file)
    nodes = [file_node, module_node]
    edges = [file_module]
    pending: list[PendingEdge] = []
    text = file.data.decode("utf-8", errors="replace")
    for match in _SQL_TABLE.finditer(text):
        schema, table = match.groups()
        qualified = f"{schema}.{table}" if schema else table
        line = text.count("\n", 0, match.start()) + 1
        location = SourceLocation(start_line=line, end_line=line)
        table_node = _new_node(
            CodeNodeKind.DATABASE_TABLE,
            label=qualified,
            path=file.path,
            qualified_name=qualified,
            fingerprint=_fragment_fingerprint(file.data, location, qualified),
            location=location,
            metadata={"resolution_key": f"table:{qualified}"},
        )
        nodes.append(table_node)
        edges.append(
            _direct_edge(
                module_node.node_id,
                table_node.node_id,
                CodeRelation.DEFINES,
                file=file,
                location=location,
            )
        )
        if schema:
            schema_node = _new_node(
                CodeNodeKind.DATABASE_SCHEMA,
                label=schema,
                path=file.path,
                qualified_name=schema,
                fingerprint=sha256_hex(
                    canonical_json_bytes({"file": file.content_sha256, "schema": schema})
                ),
                metadata={"resolution_key": f"schema:{schema}"},
            )
            nodes.append(schema_node)
            edges.append(
                _direct_edge(
                    schema_node.node_id,
                    table_node.node_id,
                    CodeRelation.CONTAINS,
                    file=file,
                    location=location,
                )
            )
    source = next((node for node in nodes if node.kind is CodeNodeKind.DATABASE_TABLE), module_node)
    for match in _SQL_REFERENCE.finditer(text):
        schema, table = match.groups()
        qualified = f"{schema}.{table}" if schema else table
        line = text.count("\n", 0, match.start()) + 1
        pending.append(
            _pending_edge(
                source.node_id,
                f"table:{qualified}",
                CodeRelation.REFERENCES,
                file=file,
                location=SourceLocation(start_line=line, end_line=line),
                confidence=EdgeConfidence.EXTRACTED,
            )
        )
    return FileExtraction(
        path=file.path,
        content_sha256=file.content_sha256,
        language=file.language,
        nodes=tuple(
            sorted({node.node_id: node for node in nodes}.values(), key=lambda item: item.node_id)
        ),
        edges=tuple(
            sorted({edge.edge_id: edge for edge in edges}.values(), key=lambda item: item.edge_id)
        ),
        pending_edges=tuple(sorted(pending, key=lambda item: item.target_ref)),
    )


def extract_file(file: DetectedFile) -> FileExtraction:
    if file.language == "python":
        return _extract_python(file)
    if file.language in {"javascript", "typescript", "tsx"}:
        return _extract_js_like(file)
    if file.language in {"json", "toml", "yaml"}:
        return _extract_configuration(file)
    if file.language == "markdown":
        return _extract_markdown(file)
    if file.language == "sql":
        return _extract_sql(file)
    raise CodeGraphSecurityError("Code graph language is not supported")


def validate_file_extraction(
    extraction: FileExtraction,
    file: DetectedFile,
    *,
    max_nodes: int,
    max_edges: int,
) -> None:
    """Validate cached/worker output against its exact input and deterministic IDs."""

    if (
        extraction.path != file.path
        or extraction.content_sha256 != file.content_sha256
        or extraction.language != file.language
    ):
        raise CodeGraphSecurityError("Code graph extraction does not match its input")
    if len(extraction.nodes) > max_nodes or (
        len(extraction.edges) + len(extraction.pending_edges) > max_edges
    ):
        raise CodeGraphSecurityError("Per-file code graph extraction exceeds its limits")
    node_ids = [node.node_id for node in extraction.nodes]
    edge_ids = [edge.edge_id for edge in extraction.edges]
    if node_ids != sorted(node_ids) or len(node_ids) != len(set(node_ids)):
        raise CodeGraphSecurityError("Code graph extraction node IDs are not sorted and unique")
    if edge_ids != sorted(edge_ids) or len(edge_ids) != len(set(edge_ids)):
        raise CodeGraphSecurityError("Code graph extraction edge IDs are not sorted and unique")
    expected_node_ids: set[str] = set()
    grouped = Counter((node.kind, node.path, node.qualified_name) for node in extraction.nodes)
    for (kind, path, qualified_name), count in grouped.items():
        expected_node_ids.update(
            stable_node_id(
                kind,
                path=path,
                qualified_name=qualified_name,
                ordinal=ordinal,
            )
            for ordinal in range(1, count + 1)
        )
    if set(node_ids) != expected_node_ids:
        raise CodeGraphSecurityError("Code graph extraction contains a forged node ID")
    line_count = max(1, file.data.count(b"\n") + 1)
    known = set(node_ids)
    for node in extraction.nodes:
        if node.path != file.path or node.extractor != EXTRACTOR_NAME:
            raise CodeGraphSecurityError("Code graph extraction node provenance is invalid")
        if node.extractor_version != EXTRACTOR_VERSION:
            raise CodeGraphSecurityError("Code graph extraction node version is invalid")
        if contains_sensitive_text(node.label) or contains_sensitive_text(node.qualified_name):
            raise CodeGraphSecurityError("Code graph extraction contains sensitive node text")
        if sanitize_metadata(node.metadata) != node.metadata:
            raise CodeGraphSecurityError("Code graph extraction metadata is not sanitized")
        if node.location is not None and node.location.end_line > line_count:
            raise CodeGraphSecurityError("Code graph extraction location exceeds the source")
    for edge in extraction.edges:
        if edge.source not in known or edge.target not in known:
            raise CodeGraphSecurityError("Per-file code graph extraction is not closed")
        if edge.source_file != file.path or edge.extractor != EXTRACTOR_NAME:
            raise CodeGraphSecurityError("Code graph extraction edge provenance is invalid")
        if edge.extractor_version != EXTRACTOR_VERSION:
            raise CodeGraphSecurityError("Code graph extraction edge version is invalid")
        if sanitize_metadata(edge.metadata) != edge.metadata:
            raise CodeGraphSecurityError("Code graph edge metadata is not sanitized")
        if edge.edge_id != stable_edge_id(
            edge.source,
            edge.target,
            edge.relation,
            source_file=edge.source_file,
            source_location=edge.source_location,
        ):
            raise CodeGraphSecurityError("Code graph extraction contains a forged edge ID")
        if edge.source_location is not None and edge.source_location.end_line > line_count:
            raise CodeGraphSecurityError("Code graph edge location exceeds the source")
    for pending in extraction.pending_edges:
        if (
            pending.source not in known
            or pending.source_file != file.path
            or not 1 <= len(pending.target_ref) <= 2048
        ):
            raise CodeGraphSecurityError("Code graph pending edge provenance is invalid")
        if pending.source_location is not None and pending.source_location.end_line > line_count:
            raise CodeGraphSecurityError("Code graph pending edge location exceeds the source")
        if (
            contains_sensitive_text(pending.target_ref)
            or sanitize_metadata(pending.metadata) != pending.metadata
        ):
            raise CodeGraphSecurityError("Code graph pending edge text is not sanitized")
    if len(extraction.diagnostics) > 64 or any(
        len(diagnostic) > 256 for diagnostic in extraction.diagnostics
    ):
        raise CodeGraphSecurityError("Code graph extraction diagnostics exceed their limits")


def extract_file_isolated(
    root: Path,
    file: DetectedFile,
    *,
    timeout_seconds: int,
    max_nodes: int,
    max_edges: int,
) -> FileExtraction:
    """Run untrusted-language parsers in a bounded isolated Python worker."""

    payload = {
        "path": file.path,
        "data": base64.b64encode(file.data).decode("ascii"),
        "content_sha256": file.content_sha256,
        "size_bytes": file.size_bytes,
        "mtime_ns": file.mtime_ns,
        "language": file.language,
        "max_nodes": max_nodes,
        "max_edges": max_edges,
        "timeout_seconds": timeout_seconds,
    }
    environment = {
        "LANG": "C.UTF-8",
        "PATH": os.environ.get("PATH", ""),
        "PYTHONHASHSEED": "0",
    }
    try:
        completed = subprocess.run(
            (sys.executable, "-I", "-m", "raytsystem.codegraph.worker"),
            input=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            capture_output=True,
            check=False,
            cwd=root,
            env=environment,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise CodeGraphSecurityError("Code graph parser exceeded its timeout") from error
    except OSError as error:
        raise CodeGraphSecurityError("Code graph parser worker is unavailable") from error
    if completed.returncode != 0 or len(completed.stdout) > 64 * 1024 * 1024:
        raise CodeGraphSecurityError("Code graph parser worker failed closed")
    try:
        decoded = json.loads(completed.stdout)
        if not isinstance(decoded, dict):
            raise TypeError
        extraction = FileExtraction.from_dict(decoded)
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as error:
        raise CodeGraphSecurityError("Code graph parser returned malformed output") from error
    validate_file_extraction(
        extraction,
        file,
        max_nodes=max_nodes,
        max_edges=max_edges,
    )
    return extraction
