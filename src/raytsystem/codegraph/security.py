from __future__ import annotations

import html
import re
import unicodedata
from pathlib import Path, PurePosixPath

from raytsystem.contracts.base import validate_relative_path
from raytsystem.security.paths import PathPolicyError, ReadResult, read_regular_file
from raytsystem.security.sensitivity import SecretScanner
from raytsystem.storage import IntegrityError


class CodeGraphSecurityError(IntegrityError):
    """A code-graph input violated the local derived-plane policy."""


_DENIED_PARTS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".raytsystem",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".vite",
        ".qmd",
        "__pycache__",
        "_raw",
        "artifacts",
        "build",
        "dist",
        "inbox",
        "knowledge",
        "ledger",
        "node_modules",
        "normalized",
    }
)
_DENIED_PREFIXES = (
    "config/schemas/",
    "ops/approvals/",
    "ops/checkpoints/",
    "ops/events/",
    "ops/locks/",
    "ops/runs/",
    "ops/staging/",
    "ops/task-ledger/",
    "src/raytsystem/webapp/static/",
    "web/src/test/__screenshots__/",
)
_SECRET_FILE = re.compile(
    r"(?:^|/)(?:\.env(?:\..*)?|id_(?:rsa|dsa|ecdsa|ed25519)|"
    r"(?:credentials?|secrets?|tokens?)(?:\.[^/]*)?|[^/]+\.(?:pem|p12|pfx|key|keystore))$",
    re.IGNORECASE,
)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SCANNER = SecretScanner()
_REDACTED = "[redacted sensitive value]"


def validate_code_path(value: str) -> str:
    try:
        relative = validate_relative_path(value)
    except ValueError as error:
        raise CodeGraphSecurityError("Code graph path escapes the workspace") from error
    pure = PurePosixPath(relative)
    if any(part in _DENIED_PARTS for part in pure.parts):
        raise CodeGraphSecurityError("Code graph path enters a protected zone")
    if any(relative.startswith(prefix) for prefix in _DENIED_PREFIXES):
        raise CodeGraphSecurityError("Code graph path enters a generated or operational zone")
    if _SECRET_FILE.search(relative):
        raise CodeGraphSecurityError("Code graph path has a secret-bearing filename")
    return relative


def is_denied_code_path(value: str) -> bool:
    try:
        validate_code_path(value)
    except CodeGraphSecurityError:
        return True
    return False


def safe_code_read(root: Path, relative: str, *, max_bytes: int) -> bytes:
    return safe_code_read_result(root, relative, max_bytes=max_bytes).data


def safe_code_read_result(root: Path, relative: str, *, max_bytes: int) -> ReadResult:
    safe = validate_code_path(relative)
    try:
        return read_regular_file(root, safe, max_bytes=max_bytes)
    except (OSError, PathPolicyError) as error:
        raise CodeGraphSecurityError("Code graph input failed no-follow validation") from error


def sanitize_label(value: str, *, limit: int = 256) -> str:
    normalized = unicodedata.normalize("NFC", value)
    normalized = _CONTROL.sub("", normalized).strip()
    if not normalized:
        raise CodeGraphSecurityError("Code graph label is empty after sanitization")
    if _SCANNER.scan(normalized.encode("utf-8"), path=None).blocks_processing:
        normalized = _REDACTED
    if len(normalized) > limit:
        normalized = normalized[: limit - 1].rstrip() + "…"
    return html.escape(normalized, quote=True)


def safe_source_name(value: str, *, limit: int = 1024) -> str:
    normalized = unicodedata.normalize("NFC", value)
    normalized = _CONTROL.sub("", normalized).strip()
    if not normalized:
        return "unknown"
    if _SCANNER.scan(normalized.encode("utf-8"), path=None).blocks_processing:
        return _REDACTED
    return normalized[:limit]


def sanitize_metadata(value: dict[str, str]) -> dict[str, str]:
    if len(value) > 32:
        raise CodeGraphSecurityError("Code graph metadata exceeds its field-count limit")
    result: dict[str, str] = {}
    for key, item in sorted(value.items()):
        safe_key = safe_source_name(str(key), limit=64)
        safe_value = safe_source_name(str(item), limit=1024)
        if not safe_key:
            raise CodeGraphSecurityError("Code graph metadata key is empty")
        result[safe_key] = safe_value
    return result


def contains_sensitive_text(value: str) -> bool:
    return _SCANNER.scan(value.encode("utf-8"), path=None).blocks_processing
