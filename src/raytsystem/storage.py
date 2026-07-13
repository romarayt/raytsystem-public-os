from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from raytsystem.contracts import canonical_json_bytes, sha256_hex
from raytsystem.io import ensure_safe_parent, write_bytes_atomic
from raytsystem.security.paths import PathPolicyError, read_regular_file


class IntegrityError(RuntimeError):
    """Raised when immutable content or a canonical pointer fails verification."""


_GENERATION_ID = re.compile(r"^gen_[0-9a-f]{64}$")


def validate_generation_id(value: str, *, allow_genesis: bool = True) -> str:
    if (allow_genesis and value == "genesis") or _GENERATION_ID.fullmatch(value):
        return value
    raise IntegrityError("Malformed generation identifier")


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _recover_owned_temp_links(path: Path, metadata: os.stat_result) -> None:
    cleaned = False
    for candidate in path.parent.glob(f".{path.name}.*"):
        try:
            candidate_metadata = candidate.lstat()
        except OSError:
            continue
        if (
            stat.S_ISREG(candidate_metadata.st_mode)
            and candidate_metadata.st_dev == metadata.st_dev
            and candidate_metadata.st_ino == metadata.st_ino
        ):
            candidate.unlink(missing_ok=True)
            cleaned = True
    if cleaned:
        fsync_directory(path.parent)


def publish_immutable(path: Path, data: bytes, *, mode: int = 0o644) -> bool:
    """Publish bytes once with hard-link no-replace semantics.

    Returns True when this call created the object and False when an identical object existed.
    """

    ensure_safe_parent(path)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temp_path, path)
        except FileExistsError:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            try:
                existing_fd = os.open(path, flags)
            except OSError as error:
                raise IntegrityError(f"Unsafe immutable path: {path}") from error
            try:
                metadata = os.fstat(existing_fd)
                if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink > 1:
                    _recover_owned_temp_links(path, metadata)
                    metadata = os.fstat(existing_fd)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    raise IntegrityError(f"Unsafe immutable object type: {path}")
                chunks: list[bytes] = []
                while chunk := os.read(existing_fd, 1024 * 1024):
                    chunks.append(chunk)
                existing = b"".join(chunks)
            finally:
                os.close(existing_fd)
            if existing != data:
                raise IntegrityError(f"Immutable path collision: {path}") from None
            return False
        temp_path.unlink()
        fsync_directory(path.parent)
        return True
    finally:
        with suppress(OSError):
            os.close(fd)
        temp_path.unlink(missing_ok=True)


def publish_content_addressed(
    root: Path,
    data: bytes,
    *,
    suffix: str = "",
    mode: int = 0o644,
) -> tuple[str, Path, bool]:
    digest = sha256_hex(data)
    path = root / digest[:2] / f"{digest}{suffix}"
    created = publish_immutable(path, data, mode=mode)
    return digest, path, created


def publish_model(root: Path, model: Any) -> tuple[str, Path, bool]:
    data = canonical_json_bytes(model)
    return publish_content_addressed(root, data, suffix=".json")


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IntegrityError(f"Invalid JSON object: {path}") from error
    if not isinstance(payload, dict):
        raise IntegrityError(f"Expected JSON object: {path}")
    return payload


def rebuild_jsonl(path: Path, records: list[dict[str, Any]], *, id_field: str) -> None:
    seen: set[str] = set()
    rendered: list[bytes] = []
    for record in sorted(records, key=lambda item: str(item[id_field])):
        record_id = str(record[id_field])
        if record_id in seen:
            raise IntegrityError(f"Duplicate record ID in projection: {record_id}")
        seen.add(record_id)
        rendered.append(canonical_json_bytes(record))
    data = b"" if not rendered else b"\n".join(rendered) + b"\n"
    write_bytes_atomic(path, data)


def read_current_generation(root: Path) -> str:
    try:
        data = read_regular_file(root, "ledger/CURRENT", max_bytes=512).data
        pointer = data.decode("ascii")
    except (OSError, PathPolicyError, UnicodeDecodeError) as error:
        raise IntegrityError("Missing ledger/CURRENT") from error
    value = pointer.strip()
    if pointer != f"{value}\n":
        raise IntegrityError("Malformed ledger/CURRENT")
    if not value or any(character.isspace() for character in value):
        raise IntegrityError("Malformed ledger/CURRENT")
    validate_generation_id(value)
    try:
        read_regular_file(
            root,
            f"ledger/generations/{value}.json",
            max_bytes=16 * 1024 * 1024,
        )
    except (OSError, PathPolicyError) as error:
        raise IntegrityError(f"Active generation does not exist: {value}") from error
    return value


def replace_current_generation(root: Path, generation_id: str) -> None:
    validate_generation_id(generation_id, allow_genesis=False)
    generation = root / "ledger" / "generations" / f"{generation_id}.json"
    if not generation.is_file():
        raise IntegrityError(f"Cannot activate missing generation: {generation_id}")
    write_bytes_atomic(root / "ledger" / "CURRENT", f"{generation_id}\n".encode("ascii"))
