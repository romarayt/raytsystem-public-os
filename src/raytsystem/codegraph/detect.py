from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from raytsystem.codegraph.security import (
    CodeGraphSecurityError,
    is_denied_code_path,
    safe_code_read_result,
    validate_code_path,
)
from raytsystem.contracts import canonical_json_bytes, sha256_hex
from raytsystem.security.paths import PathPolicyError, read_regular_file

SUPPORTED_SUFFIXES = frozenset(
    {
        ".js",
        ".jsx",
        ".json",
        ".md",
        ".mjs",
        ".py",
        ".pyi",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".yaml",
        ".yml",
    }
)
LANGUAGE_BY_SUFFIX = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".json": "json",
    ".md": "markdown",
    ".mjs": "javascript",
    ".py": "python",
    ".pyi": "python",
    ".sql": "sql",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".yaml": "yaml",
    ".yml": "yaml",
}
DEFAULT_ROOTS = ("src", "web/src", "tests", "config", "ops/decisions", "docs")
DEFAULT_FILES = ("AGENTS.md", "README.md", "pyproject.toml", ".pre-commit-config.yaml")


@dataclass(frozen=True)
class CodeGraphConfig:
    enabled: bool
    graph_first_query_enabled: bool
    graph_dir: str
    roots: tuple[str, ...]
    files: tuple[str, ...]
    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    max_nodes: int
    max_edges: int
    universe_max_nodes: int
    universe_max_edges: int
    query_max_nodes: int
    query_max_edges: int
    query_max_bytes: int
    parser_timeout_seconds: int

    def fingerprint(self) -> str:
        return sha256_hex(canonical_json_bytes(self.__dict__))


@dataclass(frozen=True)
class DetectedFile:
    path: str
    data: bytes
    content_sha256: str
    size_bytes: int
    mtime_ns: int
    language: str


def _positive_int(payload: dict[str, Any], key: str, default: int, *, maximum: int) -> int:
    value = payload.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise CodeGraphSecurityError(f"Invalid code_graph.{key} limit")
    return value


def _path_list(payload: dict[str, Any], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = payload.get(key, list(default))
    if not isinstance(raw, list) or not raw or not all(isinstance(item, str) for item in raw):
        raise CodeGraphSecurityError(f"Invalid code_graph.{key} path list")
    values = tuple(sorted({validate_code_path(item) for item in raw}))
    if len(values) != len(raw):
        raise CodeGraphSecurityError(f"Duplicate code_graph.{key} entries")
    return values


def load_code_graph_config(root: Path) -> CodeGraphConfig:
    try:
        data = read_regular_file(root, "config/raytsystem.toml", max_bytes=1024 * 1024).data
        document = tomllib.loads(data.decode("utf-8"))
    except (OSError, PathPolicyError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise CodeGraphSecurityError(
            "raytsystem code graph configuration is unavailable"
        ) from error
    raw = document.get("code_graph", {})
    if not isinstance(raw, dict):
        raise CodeGraphSecurityError("raytsystem code graph configuration is malformed")
    graph_dir = str(raw.get("path", ".raytsystem/graph"))
    if graph_dir != ".raytsystem/graph":
        raise CodeGraphSecurityError("Code graph path must use the protected derived zone")
    features = document.get("features", {})
    if not isinstance(features, dict):
        raise CodeGraphSecurityError("raytsystem feature configuration is malformed")
    enabled = features.get("code_graph_enabled", True)
    graph_first_enabled = features.get("graph_first_query_enabled", True)
    if not isinstance(enabled, bool) or not isinstance(graph_first_enabled, bool):
        raise CodeGraphSecurityError("Code graph feature flags must be booleans")
    if graph_first_enabled and not enabled:
        raise CodeGraphSecurityError("Graph-first query cannot be enabled without the code graph")
    return CodeGraphConfig(
        enabled=enabled,
        graph_first_query_enabled=graph_first_enabled,
        graph_dir=graph_dir,
        roots=_path_list(raw, "roots", DEFAULT_ROOTS),
        files=_path_list(raw, "files", DEFAULT_FILES),
        max_files=_positive_int(raw, "max_files", 5_000, maximum=100_000),
        max_file_bytes=_positive_int(
            raw, "max_file_bytes", 2 * 1024 * 1024, maximum=16 * 1024 * 1024
        ),
        max_total_bytes=_positive_int(
            raw, "max_total_bytes", 128 * 1024 * 1024, maximum=2 * 1024 * 1024 * 1024
        ),
        max_nodes=_positive_int(raw, "max_nodes", 50_000, maximum=1_000_000),
        max_edges=_positive_int(raw, "max_edges", 200_000, maximum=4_000_000),
        universe_max_nodes=_positive_int(raw, "universe_max_nodes", 2_500, maximum=10_000),
        universe_max_edges=_positive_int(raw, "universe_max_edges", 12_000, maximum=50_000),
        query_max_nodes=_positive_int(raw, "query_max_nodes", 120, maximum=2_000),
        query_max_edges=_positive_int(raw, "query_max_edges", 360, maximum=10_000),
        query_max_bytes=_positive_int(raw, "query_max_bytes", 48_000, maximum=2_000_000),
        parser_timeout_seconds=_positive_int(raw, "parser_timeout_seconds", 20, maximum=120),
    )


def _supported(relative: str) -> bool:
    path = Path(relative)
    return path.suffix.lower() in SUPPORTED_SUFFIXES


def _walk_root(root: Path, relative_root: str) -> list[str]:
    absolute = root / relative_root
    try:
        root_meta = os.lstat(absolute)
    except OSError:
        return []
    if stat.S_ISLNK(root_meta.st_mode):
        raise CodeGraphSecurityError("Configured code graph root is a symlink")
    if stat.S_ISREG(root_meta.st_mode):
        return [relative_root] if _supported(relative_root) else []
    if not stat.S_ISDIR(root_meta.st_mode):
        raise CodeGraphSecurityError("Configured code graph root is not a directory")

    found: list[str] = []
    for current, directories, files in os.walk(absolute, topdown=True, followlinks=False):
        current_path = Path(current)
        safe_dirs: list[str] = []
        for name in sorted(directories):
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            try:
                metadata = os.lstat(candidate)
            except OSError:
                continue
            if stat.S_ISLNK(metadata.st_mode):
                continue
            if stat.S_ISDIR(metadata.st_mode) and not is_denied_code_path(relative):
                safe_dirs.append(name)
        directories[:] = safe_dirs
        for name in sorted(files):
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            if _supported(relative) and not is_denied_code_path(relative):
                found.append(validate_code_path(relative))
    return found


def candidate_paths(root: Path, config: CodeGraphConfig) -> tuple[str, ...]:
    resolved = root.resolve()
    paths: set[str] = set()
    for configured in config.roots:
        paths.update(_walk_root(resolved, configured))
    for configured in config.files:
        absolute = resolved / configured
        if not absolute.exists() and not absolute.is_symlink():
            continue
        metadata = os.lstat(absolute)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise CodeGraphSecurityError("Configured code graph file is not a regular file")
        if _supported(configured):
            paths.add(configured)
    ordered = tuple(sorted(paths))
    if len(ordered) > config.max_files:
        raise CodeGraphSecurityError("Code graph file count exceeds the configured limit")
    return ordered


def detect_files(root: Path, config: CodeGraphConfig) -> tuple[DetectedFile, ...]:
    resolved = root.resolve()
    detected: list[DetectedFile] = []
    total = 0
    for relative in candidate_paths(resolved, config):
        result = safe_code_read_result(resolved, relative, max_bytes=config.max_file_bytes)
        data = result.data
        total += len(data)
        if total > config.max_total_bytes:
            raise CodeGraphSecurityError("Code graph corpus exceeds the configured byte limit")
        detected.append(
            DetectedFile(
                path=relative,
                data=data,
                content_sha256=sha256_hex(data),
                size_bytes=len(data),
                mtime_ns=result.mtime_ns,
                language=LANGUAGE_BY_SUFFIX[Path(relative).suffix.lower()],
            )
        )
    return tuple(detected)
