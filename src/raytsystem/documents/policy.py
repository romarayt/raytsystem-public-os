from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from raytsystem.contracts.base import validate_relative_path
from raytsystem.documents.contracts import (
    DocumentConfig,
    DocumentMode,
    DocumentPolicyError,
    DocumentRoot,
)

_PUBLIC_ID = re.compile(r"^(?:[a-z][a-z0-9_-]{1,63}|droot_[0-9a-f]{64})$")
_BIDI_CONTROLS = frozenset(
    {
        "\u061c",
        "\u200e",
        "\u200f",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",
        "\u2067",
        "\u2068",
        "\u2069",
    }
)
_SECRET_FILE = re.compile(
    r"(?:^|/)(?:\.env(?:\..*)?|id_(?:rsa|dsa|ecdsa|ed25519)|"
    r"(?:credentials?|secrets?|tokens?)(?:\.[^/]*)?|[^/]+\.(?:pem|p12|pfx|key|keystore))$",
    re.IGNORECASE,
)
_PACKAGE_LOCKS = frozenset(
    {
        "bun.lock",
        "bun.lockb",
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "yarn.lock",
    }
)
_HIDDEN_PARTS = frozenset(
    {
        ".raytsystem",
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".qmd",
        ".ruff_cache",
        ".svn",
        ".venv",
        ".vite",
        "__pycache__",
        "_raw",
        "artifacts",
        "build",
        "config",
        "dist",
        "inbox",
        "ledger",
        "node_modules",
        "normalized",
        "ops",
        "packs",
        "skills",
    }
)


@dataclass(frozen=True)
class PathDecision:
    relative_path: str
    root: DocumentRoot | None
    mode: DocumentMode
    reason: str

    @property
    def visible(self) -> bool:
        return self.root is not None and self.mode is not DocumentMode.HIDDEN

    @property
    def editable(self) -> bool:
        return self.visible and self.mode is DocumentMode.READ_WRITE


class DocumentPolicy:
    """Resolve configured roots under an invariant, non-overridable safety floor."""

    def __init__(self, config: DocumentConfig) -> None:
        self.config = config
        self._roots = tuple(
            sorted(config.roots, key=lambda item: (-len(PurePosixPath(item.path).parts), item.path))
        )

    @staticmethod
    def validate_root(
        path: str,
        mode: DocumentMode,
        *,
        allow_maintainer_docs_write: bool,
    ) -> str:
        try:
            normalized = validate_relative_path(path)
        except ValueError as error:
            raise DocumentPolicyError("Document root must stay inside the workspace") from error
        if _has_unsafe_path_text(normalized):
            raise DocumentPolicyError("Document root contains unsafe control characters")
        if normalized == "." or not PurePosixPath(normalized).parts:
            raise DocumentPolicyError("Workspace root cannot be a document root")
        pure = PurePosixPath(normalized)
        folded_parts = tuple(part.casefold() for part in pure.parts)
        if any(part.startswith(".") or part in _HIDDEN_PARTS for part in folded_parts):
            raise DocumentPolicyError("Document root enters an invariant hidden area")
        if _SECRET_FILE.search(normalized) or pure.name.casefold() in _PACKAGE_LOCKS:
            raise DocumentPolicyError("Document root has a protected filename")
        folded = "/".join(folded_parts)
        if folded == "knowledge" or folded.startswith("knowledge/"):
            if pure.parts[0] != "knowledge" or (
                len(pure.parts) > 1 and folded_parts[1] == "manual" and pure.parts[1] != "manual"
            ):
                raise DocumentPolicyError("Protected root case aliases are forbidden")
            manual = folded == "knowledge/manual" or folded.startswith("knowledge/manual/")
            if not manual and mode not in {
                DocumentMode.PROTECTED_READ_ONLY,
                DocumentMode.HIDDEN,
            }:
                raise DocumentPolicyError("Generated knowledge must remain protected read-only")
        maintainer_docs = (
            folded == "docs"
            or folded.startswith("docs/")
            or folded == "website/docs"
            or folded.startswith("website/docs/")
        )
        if maintainer_docs and (
            (folded_parts[0] == "docs" and pure.parts[0] != "docs")
            or (folded_parts[:2] == ("website", "docs") and pure.parts[:2] != ("website", "docs"))
        ):
            raise DocumentPolicyError("Maintainer root case aliases are forbidden")
        if maintainer_docs and mode is DocumentMode.READ_WRITE and not allow_maintainer_docs_write:
            raise DocumentPolicyError("Writable public documentation requires maintainer policy")
        return normalized

    @staticmethod
    def validate_root_id(value: str) -> str:
        if _PUBLIC_ID.fullmatch(value) is None:
            raise DocumentPolicyError("Document root ID is malformed")
        return value

    def decide(self, relative_path: str) -> PathDecision:
        try:
            normalized = validate_relative_path(relative_path)
        except ValueError as error:
            raise DocumentPolicyError("Document path must stay inside the workspace") from error
        if _has_unsafe_path_text(normalized):
            raise DocumentPolicyError("Document path contains unsafe control characters")
        pure = PurePosixPath(normalized)
        folded_parts = tuple(part.casefold() for part in pure.parts)
        if any(part.startswith(".") or part in _HIDDEN_PARTS for part in folded_parts):
            return PathDecision(normalized, None, DocumentMode.HIDDEN, "invariant_hidden")
        if pure.name.casefold() in _PACKAGE_LOCKS or _SECRET_FILE.search(normalized):
            return PathDecision(normalized, None, DocumentMode.HIDDEN, "sensitive_filename")
        selected = next(
            (
                root
                for root in self._roots
                if self._within_casefolded(pure, PurePosixPath(root.path))
            ),
            None,
        )
        if selected is None:
            return PathDecision(normalized, None, DocumentMode.HIDDEN, "outside_document_roots")
        mode = selected.mode
        folded = "/".join(folded_parts)
        if folded == "knowledge" or folded.startswith("knowledge/"):
            manual = folded == "knowledge/manual" or folded.startswith("knowledge/manual/")
            if not manual:
                mode = DocumentMode.PROTECTED_READ_ONLY
        return PathDecision(normalized, selected, mode, "configured_root")

    @staticmethod
    def _within_casefolded(candidate: PurePosixPath, root: PurePosixPath) -> bool:
        candidate_parts = tuple(part.casefold() for part in candidate.parts)
        root_parts = tuple(part.casefold() for part in root.parts)
        return (
            len(candidate_parts) >= len(root_parts)
            and candidate_parts[: len(root_parts)] == root_parts
        )

    def require_visible(self, relative_path: str) -> PathDecision:
        decision = self.decide(relative_path)
        if not decision.visible:
            raise DocumentPolicyError("Document is outside the visible workspace policy")
        return decision

    def require_write(self, relative_path: str) -> PathDecision:
        decision = self.require_visible(relative_path)
        if not decision.editable:
            raise DocumentPolicyError("Document root is read-only")
        return decision

    def root(self, root_id: str) -> DocumentRoot:
        safe_id = self.validate_root_id(root_id)
        root = next((item for item in self._roots if item.root_id == safe_id), None)
        if root is None or root.mode is DocumentMode.HIDDEN:
            raise DocumentPolicyError("Document root is unavailable")
        return root


def is_secret_filename(relative_path: str) -> bool:
    return _SECRET_FILE.search(relative_path) is not None


def _has_unsafe_path_text(value: str) -> bool:
    return any(
        ord(character) < 32 or ord(character) == 127 or character in _BIDI_CONTROLS
        for character in value
    )
