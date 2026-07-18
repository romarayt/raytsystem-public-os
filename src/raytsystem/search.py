from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import time
import tomllib
import unicodedata
from contextlib import closing
from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Protocol

from raytsystem.contracts import canonical_json_bytes, sha256_hex
from raytsystem.corpus import ActiveCorpus
from raytsystem.derived import assert_safe_sqlite_family
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.storage import fsync_directory, read_current_generation


class SearchError(RuntimeError):
    """Base class for bounded local retrieval failures."""


class SearchUnavailable(SearchError):
    """Requested backend is absent or failed its local capability gate."""


class StaleIndexError(SearchError):
    """The derived index does not match ledger/CURRENT."""


class SearchQueryError(SearchError):
    """User query exceeds the deliberately small literal grammar."""


@dataclass(frozen=True)
class SearchHit:
    generation_id: str
    generation_sha256: str
    kind: str
    logical_id: str
    object_sha256: str | None
    title: str
    body: str
    status: str
    rank: int
    score: str


@dataclass(frozen=True)
class IndexBuildResult:
    generation_id: str
    generation_sha256: str
    projection_input_sha256: str
    logical_index_sha256: str
    document_count: int
    path: str


@dataclass(frozen=True)
class SearchBenchmarkCase:
    query: str
    expected_ids: tuple[str, ...]


@dataclass(frozen=True)
class SearchBenchmarkReport:
    backend: str
    case_count: int
    recall_at_5: str
    recall_at_10: str
    mrr_at_10: str
    latency_status: str = "pending_m5b_measurement"

    def to_dict(self) -> dict[str, str | int]:
        return {
            "backend": self.backend,
            "case_count": self.case_count,
            "recall_at_5": self.recall_at_5,
            "recall_at_10": self.recall_at_10,
            "mrr_at_10": self.mrr_at_10,
            "latency_status": self.latency_status,
        }


class SearchAdapter(Protocol):
    name: str

    def search(
        self,
        query: str,
        *,
        kinds: tuple[str, ...] = (),
        limit: int = 10,
    ) -> tuple[SearchHit, ...]: ...


_TOKEN = re.compile(r"[^\W_]+", flags=re.UNICODE)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "does",
        "is",
        "of",
        "the",
        "to",
        "what",
        "which",
        "who",
        "как",
        "какая",
        "какие",
        "какой",
        "кто",
        "на",
        "что",
        "это",
    }
)
_SEARCHABLE_KINDS = frozenset({"claim", "entity", "source", "run"})


def literal_search_tokens(query: str) -> tuple[str, ...]:
    if "\x00" in query or any(
        ord(character) < 32 and character not in "\t\r\n" for character in query
    ):
        raise SearchQueryError("Query contains forbidden control characters")
    encoded = query.encode("utf-8")
    if not encoded or len(encoded) > 2048:
        raise SearchQueryError("Query exceeds retrieval limits")
    normalized = unicodedata.normalize("NFC", query).casefold()
    tokens = [token for token in _TOKEN.findall(normalized) if token not in _STOPWORDS]
    if len(tokens) > 24 or any(len(token.encode("utf-8")) > 96 for token in tokens):
        raise SearchQueryError("Query exceeds retrieval limits")
    return tuple(dict.fromkeys(tokens))


class FTS5SearchAdapter:
    name = "fts5"
    version = "1.0.0"

    def __init__(self, root: Path, *, fail_at: str | None = None) -> None:
        self.root = root.resolve()
        self.path = _configured_index_path(self.root)
        self.fail_at = fail_at

    def rebuild(self, corpus: ActiveCorpus | None = None) -> IndexBuildResult:
        snapshot = corpus or ActiveCorpus.load(self.root)
        assert_safe_sqlite_family(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        os.chmod(temporary, 0o600)
        connection: sqlite3.Connection | None = None
        try:
            if self.fail_at == "after_temp_create":
                raise RuntimeError("injected index failure after temp create")
            connection = sqlite3.connect(temporary)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA temp_store=MEMORY")
            self._create_schema(connection)
            rows = self._populate(connection, snapshot)
            if self.fail_at == "during_population":
                raise RuntimeError("injected index failure during population")
            logical_sha256 = self._logical_fingerprint(connection)
            metadata = {
                "backend": self.name,
                "backend_version": self.version,
                "generation_id": snapshot.generation.generation_id,
                "generation_sha256": snapshot.generation_sha256,
                "projection_input_sha256": snapshot.projection_input_sha256,
                "logical_index_sha256": logical_sha256,
                "document_count": str(len(rows)),
                "built_at": snapshot.generation.created_at.isoformat(),
            }
            connection.executemany(
                "INSERT INTO meta(key, value) VALUES (?, ?)",
                sorted(metadata.items()),
            )
            connection.commit()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]) != "ok":
                raise SearchUnavailable("FTS5 index integrity check failed")
            connection.close()
            connection = None
            # "rb+" instead of "rb": Windows FlushFileBuffers requires a
            # writable handle, while POSIX fsync accepts either.
            with temporary.open("rb+") as handle:
                os.fsync(handle.fileno())
            if self.fail_at == "before_replace":
                raise RuntimeError("injected index failure before atomic replace")
            if read_current_generation(self.root) != snapshot.generation.generation_id:
                raise StaleIndexError("ledger/CURRENT changed during index rebuild")
            assert_safe_sqlite_family(self.path)
            os.replace(temporary, self.path)
            fsync_directory(self.path.parent)
            return IndexBuildResult(
                generation_id=snapshot.generation.generation_id,
                generation_sha256=snapshot.generation_sha256,
                projection_input_sha256=snapshot.projection_input_sha256,
                logical_index_sha256=logical_sha256,
                document_count=len(rows),
                path=self.path.relative_to(self.root).as_posix(),
            )
        except sqlite3.Error as error:
            raise SearchUnavailable("SQLite FTS5 index build failed") from error
        finally:
            if connection is not None:
                connection.close()
            temporary.unlink(missing_ok=True)
            for suffix in ("-journal", "-wal", "-shm"):
                Path(f"{temporary}{suffix}").unlink(missing_ok=True)

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;
            CREATE TABLE claims (
                claim_id TEXT PRIMARY KEY,
                statement TEXT NOT NULL,
                status TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                object_sha256 TEXT NOT NULL,
                generation_id TEXT NOT NULL
            ) STRICT;
            CREATE TABLE entities (
                entity_id TEXT PRIMARY KEY,
                canonical_label TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                lifecycle_status TEXT NOT NULL,
                object_sha256 TEXT NOT NULL,
                generation_id TEXT NOT NULL
            ) STRICT;
            CREATE TABLE sources (
                source_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                origin TEXT NOT NULL
            ) STRICT;
            CREATE TABLE aliases (
                entity_id TEXT NOT NULL,
                value TEXT NOT NULL,
                language TEXT,
                kind TEXT NOT NULL,
                PRIMARY KEY(entity_id, value, kind)
            ) STRICT;
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                operation_type TEXT NOT NULL,
                operation_key TEXT NOT NULL,
                state TEXT NOT NULL,
                input_path TEXT NOT NULL
            ) STRICT;
            CREATE TABLE documents (
                doc_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                logical_id TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                aliases TEXT NOT NULL,
                status TEXT NOT NULL,
                object_sha256 TEXT,
                generation_id TEXT NOT NULL
            ) STRICT;
            CREATE VIRTUAL TABLE documents_fts USING fts5(
                doc_id UNINDEXED,
                kind UNINDEXED,
                logical_id UNINDEXED,
                title,
                body,
                aliases,
                tokenize='unicode61 remove_diacritics 2'
            );
            """
        )

    def _populate(
        self,
        connection: sqlite3.Connection,
        corpus: ActiveCorpus,
    ) -> list[dict[str, str | None]]:
        rows: list[dict[str, str | None]] = []
        for claim_id, claim in sorted(corpus.claims.items()):
            record = corpus.records[f"claim:{claim_id}"]
            connection.execute(
                "INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?)",
                (
                    claim_id,
                    claim.statement,
                    claim.status.value,
                    json.dumps(claim.evidence_ids, ensure_ascii=False, separators=(",", ":")),
                    record.object_sha256,
                    corpus.generation.generation_id,
                ),
            )
            rows.append(
                {
                    "doc_id": f"claim:{claim_id}",
                    "kind": "claim",
                    "logical_id": claim_id,
                    "title": claim.statement,
                    "body": claim.statement,
                    "aliases": "",
                    "status": claim.status.value,
                    "object_sha256": record.object_sha256,
                    "generation_id": corpus.generation.generation_id,
                }
            )
        for entity_id, entity in sorted(corpus.entities.items()):
            record = corpus.records[f"entity:{entity_id}"]
            aliases = " ".join(alias.value for alias in entity.aliases)
            connection.execute(
                "INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?)",
                (
                    entity_id,
                    entity.canonical_label,
                    entity.entity_type,
                    entity.lifecycle_status.value,
                    record.object_sha256,
                    corpus.generation.generation_id,
                ),
            )
            connection.executemany(
                "INSERT INTO aliases VALUES (?, ?, ?, ?)",
                [(entity_id, alias.value, alias.language, alias.kind) for alias in entity.aliases],
            )
            rows.append(
                {
                    "doc_id": f"entity:{entity_id}",
                    "kind": "entity",
                    "logical_id": entity_id,
                    "title": entity.canonical_label,
                    "body": entity.canonical_label,
                    "aliases": aliases,
                    "status": entity.lifecycle_status.value,
                    "object_sha256": record.object_sha256,
                    "generation_id": corpus.generation.generation_id,
                }
            )
        for source_id, source in sorted(corpus.sources.items()):
            origin = source.origin.locator or f"sha256:{source.origin.locator_sha256}"
            display_name = source.display_name or source_id
            connection.execute(
                "INSERT INTO sources VALUES (?, ?, ?, ?)",
                (source_id, display_name, source.source_type, origin),
            )
            rows.append(
                {
                    "doc_id": f"source:{source_id}",
                    "kind": "source",
                    "logical_id": source_id,
                    "title": display_name,
                    "body": source.source_type,
                    "aliases": "",
                    "status": "active",
                    "object_sha256": None,
                    "generation_id": corpus.generation.generation_id,
                }
            )
        for manifest in sorted(corpus.run_manifests, key=lambda value: str(value["run_id"])):
            run_id = str(manifest["run_id"])
            operation_type = str(manifest.get("operation_type", "unknown"))
            operation_key = str(manifest.get("operation_key", "unknown"))
            state = "recorded"
            input_path = str(manifest.get("input_path", ""))
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?)",
                (run_id, operation_type, operation_key, state, input_path),
            )
            rows.append(
                {
                    "doc_id": f"run:{run_id}",
                    "kind": "run",
                    "logical_id": run_id,
                    "title": operation_type,
                    "body": f"{state} {input_path}",
                    "aliases": operation_key,
                    "status": state,
                    "object_sha256": None,
                    "generation_id": corpus.generation.generation_id,
                }
            )
        rows.sort(key=lambda value: str(value["doc_id"]))
        connection.executemany(
            "INSERT INTO documents VALUES (:doc_id, :kind, :logical_id, :title, :body, "
            ":aliases, :status, :object_sha256, :generation_id)",
            rows,
        )
        connection.executemany(
            "INSERT INTO documents_fts(doc_id, kind, logical_id, title, body, aliases) "
            "VALUES (:doc_id, :kind, :logical_id, :title, :body, :aliases)",
            rows,
        )
        return rows

    def metadata(self) -> dict[str, str]:
        try:
            with closing(self._read_connection()) as connection, connection:
                return self._metadata(connection)
        except sqlite3.Error as error:
            raise SearchUnavailable("FTS5 index metadata is unreadable") from error

    def logical_fingerprint(self) -> str:
        """Hash document and FTS rows instead of unstable SQLite file bytes."""

        try:
            with closing(self._read_connection()) as connection, connection:
                return self._logical_fingerprint(connection)
        except sqlite3.Error as error:
            raise SearchUnavailable("FTS5 logical index is unreadable") from error

    def is_current(self, corpus: ActiveCorpus | None = None) -> bool:
        try:
            metadata = self.metadata()
            logical_sha256 = self.logical_fingerprint()
        except SearchUnavailable:
            return False
        snapshot = corpus or ActiveCorpus.load(self.root)
        return (
            metadata.get("generation_id") == snapshot.generation.generation_id
            and metadata.get("generation_sha256") == snapshot.generation_sha256
            and metadata.get("projection_input_sha256") == snapshot.projection_input_sha256
            and metadata.get("logical_index_sha256") == logical_sha256
        )

    def search(
        self,
        query: str,
        *,
        kinds: tuple[str, ...] = (),
        limit: int = 10,
    ) -> tuple[SearchHit, ...]:
        if not 1 <= limit <= 50:
            raise SearchQueryError("Search result limit is outside 1..50")
        if kinds and (len(set(kinds)) != len(kinds) or not set(kinds).issubset(_SEARCHABLE_KINDS)):
            raise SearchQueryError("Search kind filter is invalid")
        tokens = literal_search_tokens(query)
        if not tokens:
            return ()
        expression = " AND ".join(f'"{token}"*' for token in tokens)
        parameters: list[object] = [expression]
        kind_clause = ""
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            kind_clause = f" AND d.kind IN ({placeholders})"
            parameters.extend(kinds)
        parameters.append(limit)
        sql = (
            "SELECT d.*, bm25(documents_fts, 0.0, 0.0, 0.0, 5.0, 1.0, 2.0) AS score "
            "FROM documents_fts JOIN documents d ON d.doc_id = documents_fts.doc_id "
            "WHERE documents_fts MATCH ?" + kind_clause + " ORDER BY score, d.logical_id LIMIT ?"
        )
        deadline = time.monotonic_ns() + 200_000_000
        try:
            with closing(self._read_connection()) as connection, connection:
                metadata = self._metadata(connection)
                if metadata.get("logical_index_sha256") != self._logical_fingerprint(connection):
                    raise StaleIndexError("FTS5 logical index fingerprint changed")
                current = read_current_generation(self.root)
                if metadata.get("generation_id") != current:
                    raise StaleIndexError("FTS5 index generation is stale")
                connection.set_progress_handler(
                    lambda: 1 if time.monotonic_ns() > deadline else 0,
                    1000,
                )
                result_rows = connection.execute(sql, parameters).fetchall()
        except StaleIndexError:
            raise
        except sqlite3.Error as error:
            raise SearchUnavailable("Bounded FTS5 query failed") from error
        generation_sha256 = metadata.get("generation_sha256")
        if generation_sha256 is None:
            raise SearchUnavailable("FTS5 index metadata is incomplete")
        hits = tuple(
            SearchHit(
                generation_id=current,
                generation_sha256=generation_sha256,
                kind=str(row["kind"]),
                logical_id=str(row["logical_id"]),
                object_sha256=(None if row["object_sha256"] is None else str(row["object_sha256"])),
                title=str(row["title"]),
                body=str(row["body"]),
                status=str(row["status"]),
                rank=index,
                score=f"{float(row['score']):.12f}",
            )
            for index, row in enumerate(result_rows, 1)
        )
        if read_current_generation(self.root) != current:
            raise StaleIndexError("ledger/CURRENT changed during FTS5 query")
        return hits

    def schema_fingerprint(self) -> str:
        try:
            with closing(self._read_connection()) as connection, connection:
                rows = [
                    {"type": row[0], "name": row[1], "sql": row[2]}
                    for row in connection.execute(
                        "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                    )
                ]
        except sqlite3.Error as error:
            raise SearchUnavailable("FTS5 index schema is unreadable") from error
        return sha256_hex(canonical_json_bytes(rows))

    @staticmethod
    def _metadata(connection: sqlite3.Connection) -> dict[str, str]:
        return {
            str(row["key"]): str(row["value"])
            for row in connection.execute("SELECT key, value FROM meta ORDER BY key")
        }

    @staticmethod
    def _logical_fingerprint(connection: sqlite3.Connection) -> str:
        documents = [
            {
                "doc_id": str(row["doc_id"]),
                "kind": str(row["kind"]),
                "logical_id": str(row["logical_id"]),
                "title": str(row["title"]),
                "body": str(row["body"]),
                "aliases": str(row["aliases"]),
                "status": str(row["status"]),
                "object_sha256": (
                    None if row["object_sha256"] is None else str(row["object_sha256"])
                ),
                "generation_id": str(row["generation_id"]),
            }
            for row in connection.execute("SELECT * FROM documents ORDER BY doc_id")
        ]
        fts_rows = [
            {
                "doc_id": str(row["doc_id"]),
                "kind": str(row["kind"]),
                "logical_id": str(row["logical_id"]),
                "title": str(row["title"]),
                "body": str(row["body"]),
                "aliases": str(row["aliases"]),
            }
            for row in connection.execute(
                "SELECT doc_id, kind, logical_id, title, body, aliases "
                "FROM documents_fts ORDER BY doc_id, rowid"
            )
        ]
        return sha256_hex(canonical_json_bytes({"documents": documents, "documents_fts": fts_rows}))

    def _read_connection(self) -> sqlite3.Connection:
        assert_safe_sqlite_family(self.path)
        if not self.path.is_file():
            raise SearchUnavailable("FTS5 index is unavailable")
        connection = sqlite3.connect(
            f"{self.path.resolve().as_uri()}?mode=ro&immutable=1",
            uri=True,
            timeout=1.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA busy_timeout=1000")
        return connection


class QmdSearchAdapter:
    name = "qmd"
    version = "contract-only-1.0.0"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def search(
        self,
        query: str,
        *,
        kinds: tuple[str, ...] = (),
        limit: int = 10,
    ) -> tuple[SearchHit, ...]:
        del query, kinds, limit
        raise SearchUnavailable(
            "QMD is not configured; model assets require a separate benchmark and approval"
        )


def load_benchmark_cases(root: Path, path: Path) -> tuple[SearchBenchmarkCase, ...]:
    resolved_root = root.resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(resolved_root).as_posix()
    except ValueError as error:
        raise SearchQueryError("Benchmark cases path escapes the workspace") from error
    data = read_regular_file(resolved_root, relative, max_bytes=4 * 1024 * 1024).data
    cases: list[SearchBenchmarkCase] = []
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise SearchQueryError("Benchmark case is invalid JSONL") from error
        if not isinstance(payload, dict):
            raise SearchQueryError("Benchmark case must be an object")
        query = payload.get("query")
        expected = payload.get("expected_ids")
        if (
            not isinstance(query, str)
            or not isinstance(expected, list)
            or not expected
            or not all(isinstance(value, str) for value in expected)
            or len(set(expected)) != len(expected)
        ):
            raise SearchQueryError("Benchmark case has invalid query or ground truth")
        literal_search_tokens(query)
        cases.append(SearchBenchmarkCase(query=query, expected_ids=tuple(expected)))
    if not 1 <= len(cases) <= 1000:
        raise SearchQueryError("Benchmark requires 1..1000 labeled cases")
    return tuple(cases)


def run_search_benchmark(
    adapter: SearchAdapter,
    cases: tuple[SearchBenchmarkCase, ...],
) -> SearchBenchmarkReport:
    if not cases:
        raise SearchQueryError("Benchmark requires labeled ground truth")
    recall_5 = Fraction(0)
    recall_10 = Fraction(0)
    reciprocal_rank = Fraction(0)
    for case in cases:
        hits = adapter.search(case.query, limit=10)
        ranked = [hit.logical_id for hit in hits]
        expected = set(case.expected_ids)
        recall_5 += Fraction(len(expected.intersection(ranked[:5])), len(expected))
        recall_10 += Fraction(len(expected.intersection(ranked[:10])), len(expected))
        first_rank = next(
            (index for index, logical_id in enumerate(ranked[:10], 1) if logical_id in expected),
            None,
        )
        if first_rank is not None:
            reciprocal_rank += Fraction(1, first_rank)
    count = len(cases)
    return SearchBenchmarkReport(
        backend=adapter.name,
        case_count=count,
        recall_at_5=_fraction_string(recall_5 / count),
        recall_at_10=_fraction_string(recall_10 / count),
        mrr_at_10=_fraction_string(reciprocal_rank / count),
    )


def _configured_index_path(root: Path) -> Path:
    try:
        data = read_regular_file(
            root,
            "config/raytsystem.toml",
            max_bytes=1024 * 1024,
        ).data
        config = tomllib.loads(data.decode("utf-8"))
    except (OSError, PathPolicyError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise SearchUnavailable("raytsystem config is unavailable") from error
    relative = str(config.get("index_db", ".raytsystem/index.sqlite"))
    path = _guard_relative_path(relative)
    return root / path


def _guard_relative_path(value: str) -> Path:
    if not value or "\x00" in value or "\\" in value:
        raise SearchUnavailable("Configured index path is malformed")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or not path.parts:
        raise SearchUnavailable("Configured index path escapes the workspace")
    return path


def ordered_logical_hits(hits: Iterable[SearchHit]) -> tuple[tuple[str, int], ...]:
    return tuple((hit.logical_id, hit.rank) for hit in hits)


def _fraction_string(value: Fraction) -> str:
    return f"{float(value):.6f}"
