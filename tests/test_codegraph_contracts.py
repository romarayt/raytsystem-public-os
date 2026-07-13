from __future__ import annotations

from raytsystem.codegraph.contracts import CodeNodeKind, CodeRelation, EdgeConfidence
from raytsystem.codegraph.detect import DetectedFile
from raytsystem.codegraph.extract import extract_file, stable_node_id
from raytsystem.contracts import sha256_hex


def _detected(path: str, text: str, language: str) -> DetectedFile:
    data = text.encode("utf-8")
    return DetectedFile(
        path=path,
        data=data,
        content_sha256=sha256_hex(data),
        size_bytes=len(data),
        mtime_ns=1,
        language=language,
    )


def test_code_node_ids_are_stable_and_content_independent() -> None:
    first = stable_node_id(
        CodeNodeKind.FUNCTION,
        path="src/example.py",
        qualified_name="build",
    )
    second = stable_node_id(
        CodeNodeKind.FUNCTION,
        path="src/example.py",
        qualified_name="build",
    )

    assert first == second
    assert first.startswith("cnode_")
    assert first != stable_node_id(
        CodeNodeKind.FUNCTION,
        path="src/other.py",
        qualified_name="build",
    )


def test_python_ast_extraction_is_deterministic_and_labels_confidence() -> None:
    source = _detected(
        "src/service.py",
        """
from fastapi import FastAPI
from helpers import persist

app = FastAPI()

class Service(BaseService):
    # WHY: keep writes behind one boundary
    def save(self):
        return persist()

@app.get("/health")
def health():
    return Service().save()
""".strip()
        + "\n",
        "python",
    )

    first = extract_file(source)
    second = extract_file(source)

    assert first.to_dict() == second.to_dict()
    assert {node.kind for node in first.nodes}.issuperset(
        {
            CodeNodeKind.FILE,
            CodeNodeKind.MODULE,
            CodeNodeKind.CLASS,
            CodeNodeKind.METHOD,
            CodeNodeKind.FUNCTION,
            CodeNodeKind.API_ENDPOINT,
            CodeNodeKind.RATIONALE,
        }
    )
    assert all(edge.confidence is EdgeConfidence.EXTRACTED for edge in first.edges)
    assert any(edge.relation is CodeRelation.CALLS for edge in first.pending_edges)
    assert all(edge.confidence is EdgeConfidence.INFERRED for edge in first.pending_edges)


def test_typescript_tree_sitter_extraction_finds_structure_and_imports() -> None:
    source = _detected(
        "web/src/client.ts",
        'import { request } from "./api";\nexport function load(){ return request(); }\n',
        "typescript",
    )

    extraction = extract_file(source)

    assert any(
        node.kind is CodeNodeKind.FUNCTION and node.label == "load" for node in extraction.nodes
    )
    assert any(edge.relation is CodeRelation.IMPORTS for edge in extraction.pending_edges)
    assert any(edge.relation is CodeRelation.CALLS for edge in extraction.pending_edges)


def test_syntax_error_is_explicit_without_inventing_symbols() -> None:
    source = _detected("src/broken.py", "def broken(:\n", "python")

    extraction = extract_file(source)

    assert extraction.diagnostics == ("syntax_error",)
    assert {node.kind for node in extraction.nodes} == {
        CodeNodeKind.FILE,
        CodeNodeKind.MODULE,
    }
