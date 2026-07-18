from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import unicodedata
from contextlib import suppress
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Any, ClassVar, Protocol

from raytsystem.contracts import (
    ImageLocator,
    JsonLocator,
    PdfLocator,
    TableLocator,
    TextLocator,
    sha256_hex,
)
from raytsystem.contracts.evidence import SegmentLocator

try:
    import resource
except ImportError:  # Windows: POSIX rlimits are unavailable; workers rely on timeout/size caps.
    resource = None  # type: ignore[assignment]


class ExtractionError(RuntimeError):
    """Input is malformed for the selected deterministic extractor."""


class DependencyUnavailable(ExtractionError):
    """An optional parser is not installed; no lazy download is attempted."""


@dataclass(frozen=True)
class ExtractedSpan:
    excerpt: str
    locator: SegmentLocator
    modality: str = "text"


@dataclass(frozen=True)
class Extraction:
    document: str
    spans: tuple[ExtractedSpan, ...]


class Extractor(Protocol):
    name: str
    version: str
    media_type: str

    def extract(self, data: bytes, *, source_path: str) -> Extraction: ...


def _normalized_text(data: bytes) -> str:
    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ExtractionError("Extractor accepts UTF-8 input only") from error
    return unicodedata.normalize("NFC", decoded.replace("\r\n", "\n").replace("\r", "\n"))


class NativeTextExtractor:
    name = "native_text"
    version = "1.0.0"
    media_type = "text/markdown"

    def extract(self, data: bytes, *, source_path: str) -> Extraction:
        del source_path
        text = _normalized_text(data)
        spans: list[ExtractedSpan] = []
        character_offset = 0
        for line_number, line_with_ending in enumerate(text.splitlines(keepends=True), 1):
            excerpt = line_with_ending.rstrip("\n")
            if excerpt.strip():
                spans.append(
                    ExtractedSpan(
                        excerpt=excerpt,
                        locator=TextLocator(
                            line_start=line_number,
                            line_end=line_number,
                            char_start=character_offset,
                            char_end=character_offset + len(excerpt),
                        ),
                    )
                )
            character_offset += len(line_with_ending)
        if not spans:
            raise ExtractionError("Cannot extract evidence from an empty text source")
        return Extraction(document=text, spans=tuple(spans))


class _VisibleHtmlParser(HTMLParser):
    _blocked_tags = frozenset({"script", "style", "noscript", "template", "svg"})
    _void_tags = frozenset(
        {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }
    )
    _max_chunks = 50_000
    _max_text_characters = 2_000_000
    _max_depth = 512

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.chunks: list[str] = []
        self._stack: list[tuple[str, bool]] = []
        self._blocked_depth = 0
        self._text_characters = 0

    @staticmethod
    def _attributes_hide_content(attrs: list[tuple[str, str | None]]) -> bool:
        values = {name.casefold(): (value or "").casefold() for name, value in attrs}
        if "hidden" in values or values.get("aria-hidden") in {"true", "1", "yes"}:
            return True
        style = "".join(values.get("style", "").split())
        return any(
            declaration in style
            for declaration in ("display:none", "visibility:hidden", "content-visibility:hidden")
        )

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        normalized_tag = tag.casefold()
        blocked = (
            self._blocked_depth > 0
            or normalized_tag in self._blocked_tags
            or self._attributes_hide_content(attrs)
        )
        if normalized_tag in self._void_tags:
            return
        if len(self._stack) >= self._max_depth:
            raise ExtractionError("Captured HTML nesting exceeds deterministic limits")
        self._stack.append((normalized_tag, blocked))
        if blocked:
            self._blocked_depth += 1

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del tag, attrs

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        matching_index = next(
            (
                index
                for index in range(len(self._stack) - 1, -1, -1)
                if self._stack[index][0] == normalized_tag
            ),
            None,
        )
        if matching_index is None:
            return
        removed = self._stack[matching_index:]
        del self._stack[matching_index:]
        self._blocked_depth -= sum(1 for _, blocked in removed if blocked)

    def handle_data(self, data: str) -> None:
        if self._blocked_depth:
            return
        value = " ".join(unicodedata.normalize("NFC", data).split())
        if not value:
            return
        self._text_characters += len(value)
        if (
            len(self.chunks) >= self._max_chunks
            or self._text_characters > self._max_text_characters
        ):
            raise ExtractionError("Captured HTML visible text exceeds deterministic limits")
        self.chunks.append(value)


class CapturedHtmlExtractor:
    """Offline visible-text extraction for already captured web response bytes."""

    name = "captured_html"
    version = "1.0.0"
    media_type = "text/html"

    def extract(self, data: bytes, *, source_path: str) -> Extraction:
        del source_path
        text = _normalized_text(data)
        parser = _VisibleHtmlParser()
        try:
            parser.feed(text)
            parser.close()
        except (UnicodeError, ValueError) as error:
            raise ExtractionError("Captured HTML is malformed") from error
        if not parser.chunks:
            raise ExtractionError("Captured HTML contains no visible evidence text")
        lines = tuple(parser.chunks)
        document = "\n".join(lines) + "\n"
        spans: list[ExtractedSpan] = []
        character_offset = 0
        for line_number, excerpt in enumerate(lines, 1):
            spans.append(
                ExtractedSpan(
                    excerpt=excerpt,
                    locator=TextLocator(
                        line_start=line_number,
                        line_end=line_number,
                        char_start=character_offset,
                        char_end=character_offset + len(excerpt),
                    ),
                )
            )
            character_offset += len(excerpt) + 1
        return Extraction(document=document, spans=tuple(spans))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ExtractionError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _json_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


class NativeJsonExtractor:
    name = "native_json"
    version = "1.0.0"
    media_type = "application/json"

    def extract(self, data: bytes, *, source_path: str) -> Extraction:
        text = _normalized_text(data)
        try:
            if PurePosixPath(source_path).suffix == ".jsonl":
                values = [
                    json.loads(line, object_pairs_hook=_reject_duplicate_keys)
                    for line in text.splitlines()
                    if line.strip()
                ]
                value: Any = values
            else:
                value = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ExtractionError("Input is not valid JSON/JSONL") from error
        document = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        spans: list[ExtractedSpan] = []
        if isinstance(value, dict):
            iterable = [(_json_pointer_token(str(key)), item) for key, item in value.items()]
        elif isinstance(value, list):
            iterable = [(str(index), item) for index, item in enumerate(value)]
        else:
            iterable = [("", value)]
        for token, item in iterable:
            excerpt = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            spans.append(
                ExtractedSpan(
                    excerpt=excerpt,
                    locator=JsonLocator(pointer="" if token == "" else f"/{token}"),
                    modality="structured_text",
                )
            )
        if not spans:
            raise ExtractionError("JSON input contains no evidence values")
        return Extraction(document=document, spans=tuple(spans))


class NativeTableExtractor:
    name = "native_delimited_table"
    version = "1.0.0"
    media_type = "text/csv"

    def extract(self, data: bytes, *, source_path: str) -> Extraction:
        text = _normalized_text(data)
        delimiter = "\t" if PurePosixPath(source_path.lower()).suffix == ".tsv" else ","
        try:
            rows = list(csv.reader(io.StringIO(text), delimiter=delimiter, strict=True))
        except csv.Error as error:
            raise ExtractionError("Delimited table is malformed") from error
        if not rows or not rows[0] or len(rows) > 100_001 or len(rows[0]) > 1000:
            raise ExtractionError("Delimited table shape is empty or exceeds limits")
        header = rows[0]
        if len(set(header)) != len(header) or any(not cell or len(cell) > 256 for cell in header):
            raise ExtractionError("Delimited table header is invalid or duplicated")
        records: list[dict[str, str]] = []
        spans: list[ExtractedSpan] = []
        for row_index, row in enumerate(rows[1:], 1):
            if len(row) != len(header) or any(len(cell) > 4096 for cell in row):
                raise ExtractionError("Delimited table row shape exceeds limits")
            record = dict(zip(header, row, strict=True))
            excerpt = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if len(excerpt) > 4096:
                raise ExtractionError("Delimited table evidence row is too large")
            records.append(record)
            spans.append(
                ExtractedSpan(
                    excerpt=excerpt,
                    locator=TableLocator(
                        table=PurePosixPath(source_path).name,
                        row_start=row_index,
                        row_end=row_index,
                        column_start=0,
                        column_end=len(header) - 1,
                    ),
                    modality="table_row",
                )
            )
        if not spans:
            raise ExtractionError("Delimited table has no data rows")
        document = json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        return Extraction(document=document, spans=tuple(spans))


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    if not data.startswith(b"\xff\xd8"):
        raise ExtractionError("JPEG signature is invalid")
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            raise ExtractionError("JPEG marker stream is malformed")
        marker = data[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(data):
            break
        length = int.from_bytes(data[offset : offset + 2], "big")
        if length < 2 or offset + length > len(data):
            raise ExtractionError("JPEG segment length is invalid")
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB}:
            if length < 7:
                raise ExtractionError("JPEG frame header is invalid")
            height = int.from_bytes(data[offset + 3 : offset + 5], "big")
            width = int.from_bytes(data[offset + 5 : offset + 7], "big")
            return width, height
        offset += length
    raise ExtractionError("JPEG dimensions were not found")


class NativeImageExtractor:
    name = "native_image_metadata"
    version = "1.0.0"
    media_type = "image/png"

    def extract(self, data: bytes, *, source_path: str) -> Extraction:
        suffix = PurePosixPath(source_path.lower()).suffix
        if suffix == ".png":
            if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
                raise ExtractionError("PNG header is invalid")
            width = int.from_bytes(data[16:20], "big")
            height = int.from_bytes(data[20:24], "big")
            media_type = "image/png"
        elif suffix in {".jpg", ".jpeg"}:
            width, height = _jpeg_dimensions(data)
            media_type = "image/jpeg"
        else:
            raise ExtractionError("Native image adapter supports PNG and JPEG only")
        if width <= 0 or height <= 0 or width * height > 100_000_000:
            raise ExtractionError("Image dimensions are invalid or exceed limits")
        self.media_type = media_type
        excerpt = f"Image {width}x{height} pixels ({media_type}); sha256={sha256_hex(data)}"
        document = excerpt + "\n"
        return Extraction(
            document=document,
            spans=(
                ExtractedSpan(
                    excerpt=excerpt,
                    locator=ImageLocator(bbox=(0, 0, width, height)),
                    modality="image_metadata",
                ),
            ),
        )


class PdfExtractor:
    name = "pypdf_text"
    version = "1.0.0"
    media_type = "application/pdf"
    _process_containment: ClassVar[str | None] = None

    def __init__(self) -> None:
        self._selected_containment: str | None = None

    @staticmethod
    def _sandbox_policy(temporary_directory: str) -> str:
        readable_roots = tuple(
            dict.fromkeys(
                (
                    "/System",
                    "/usr",
                    "/Library",
                    sys.base_prefix,
                    sys.prefix,
                )
            )
        )
        read_rules = " ".join(f"(subpath {json.dumps(root)})" for root in readable_roots)
        return (
            "(version 1) "
            "(deny default) "
            "(allow process*) "
            "(allow sysctl-read) "
            "(allow mach-lookup) "
            "(allow ipc-posix-shm) "
            f"(allow file-read* {read_rules}) "
            f"(allow file-write* (subpath {json.dumps(temporary_directory)})) "
            "(deny network*)"
        )

    def containment_profile(self) -> str:
        if self._selected_containment is not None:
            return self._selected_containment
        if self._process_containment is not None:
            self._selected_containment = self._process_containment
            return self._selected_containment
        selected = "fixture_python_guard_v1"
        if sys.platform == "darwin" and os.path.isfile("/usr/bin/sandbox-exec"):
            with tempfile.TemporaryDirectory(prefix="raytsystem-pdf-preflight-") as temporary:
                try:
                    capability = subprocess.run(
                        (
                            "/usr/bin/sandbox-exec",
                            "-p",
                            self._sandbox_policy(temporary),
                            "/usr/bin/true",
                        ),
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        cwd=temporary,
                        env={"PATH": os.defpath},
                        timeout=3,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    capability = None
                if capability is not None and capability.returncode == 0:
                    selected = "macos_restricted_v1"
        type(self)._process_containment = selected
        self._selected_containment = selected
        return selected

    def operation_config(self) -> dict[str, str]:
        return {"containment_profile": self.containment_profile()}

    @staticmethod
    def _limit_worker() -> None:
        if resource is None:
            return
        limits = (
            (resource.RLIMIT_CPU, 8),
            (resource.RLIMIT_AS, 768 * 1024 * 1024),
            (resource.RLIMIT_FSIZE, 16 * 1024 * 1024),
            (resource.RLIMIT_NOFILE, 64),
        )
        for resource_id, requested in limits:
            with suppress(OSError, ValueError):
                _, hard = resource.getrlimit(resource_id)
                effective = requested if hard == resource.RLIM_INFINITY else min(requested, hard)
                resource.setrlimit(resource_id, (effective, effective))

    def extract(self, data: bytes, *, source_path: str) -> Extraction:
        del source_path
        environment = {
            "PATH": os.defpath,
            "PYTHONHASHSEED": "0",
            "PYTHONIOENCODING": "utf-8",
            "NO_PROXY": "*",
        }
        worker_command = [sys.executable, "-I", "-m", "raytsystem.pdf_worker"]
        with tempfile.TemporaryDirectory(prefix="raytsystem-pdf-") as temporary_directory:
            if self.containment_profile() == "macos_restricted_v1":
                worker_command = [
                    "/usr/bin/sandbox-exec",
                    "-p",
                    self._sandbox_policy(temporary_directory),
                    *worker_command,
                ]
            try:
                completed = subprocess.run(
                    worker_command,
                    input=data,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    cwd=temporary_directory,
                    env=environment,
                    timeout=15,
                    check=False,
                    preexec_fn=self._limit_worker if os.name == "posix" else None,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ExtractionError("PDF parser worker failed safely") from error
        if completed.returncode != 0 or len(completed.stdout) > 12 * 1024 * 1024:
            raise ExtractionError("PDF parser rejected the local document")
        try:
            payload = json.loads(completed.stdout)
            pages = payload["pages"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ExtractionError("PDF parser returned an invalid result") from error
        if not isinstance(pages, list) or not all(isinstance(page, str) for page in pages):
            raise ExtractionError("PDF parser returned invalid page text")
        document_parts: list[str] = []
        spans: list[ExtractedSpan] = []
        for page_index, page_text in enumerate(pages):
            normalized = unicodedata.normalize(
                "NFC",
                page_text.replace("\r\n", "\n").replace("\r", "\n"),
            ).strip()
            if not normalized:
                continue
            document_parts.append(f"## Page {page_index + 1}\n\n{normalized}")
            spans.append(
                ExtractedSpan(
                    excerpt=normalized,
                    locator=PdfLocator(page_index=page_index, bbox=("0", "0", "1", "1")),
                    modality="pdf_text",
                )
            )
        if not spans:
            raise ExtractionError("PDF contains no extractable text; OCR is unavailable")
        return Extraction(document="\n\n".join(document_parts) + "\n", spans=tuple(spans))


class ExtractorRegistry:
    def select(self, source_path: str) -> Extractor:
        lower = source_path.lower()
        suffix = PurePosixPath(lower).suffix
        if suffix in {".md", ".markdown", ".txt", ".text"}:
            text_extractor = NativeTextExtractor()
            text_extractor.media_type = (
                "text/markdown" if suffix in {".md", ".markdown"} else "text/plain"
            )
            return text_extractor
        if suffix in {".html", ".htm"}:
            return CapturedHtmlExtractor()
        if suffix in {".json", ".jsonl"}:
            json_extractor = NativeJsonExtractor()
            json_extractor.media_type = (
                "application/x-ndjson" if suffix == ".jsonl" else "application/json"
            )
            return json_extractor
        if suffix in {".csv", ".tsv"}:
            table_extractor = NativeTableExtractor()
            table_extractor.media_type = (
                "text/tab-separated-values" if suffix == ".tsv" else "text/csv"
            )
            return table_extractor
        if suffix in {".png", ".jpg", ".jpeg"}:
            return NativeImageExtractor()
        if suffix == ".pdf":
            return PdfExtractor()
        raise ExtractionError(f"No offline extractor is registered for {suffix or 'extensionless'}")
