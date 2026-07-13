from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

SCHEMA_VERSION = "1.4.0"
ID_SCHEME_VERSION = "1"

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_:.@/-]{1,255}$")]


def _canonical_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical_value(value.model_dump(mode="python", exclude_none=False))
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Canonical timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Decimal):
        normalized = value.normalize()
        return format(normalized, "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("NaN and Infinity are not canonical JSON")
        raise TypeError("Binary floats are forbidden in canonical identity material")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, set | frozenset):
        canonical_items = [_canonical_value(item) for item in value]
        return sorted(canonical_items, key=lambda item: canonical_json_bytes(item))
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        return [_canonical_value(item) for item in value]
    if value is None or isinstance(value, bool | int):
        return value
    raise TypeError(f"Unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize deterministic UTF-8 JSON for hashes and immutable records."""

    normalized = _canonical_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def derive_id(prefix: str, identity: Any) -> str:
    material = {
        "id_scheme_version": ID_SCHEME_VERSION,
        "kind": prefix,
        "identity": identity,
    }
    return f"{prefix}_{sha256_hex(canonical_json_bytes(material))}"


def validate_relative_path(value: str) -> str:
    if not value or "\x00" in value or "\\" in value:
        raise ValueError("Path must be a non-empty POSIX workspace-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError("Path must stay within the workspace")
    if ":" in path.parts[0]:
        raise ValueError("Windows drive paths are forbidden")
    return path.as_posix()


RelativePath = Annotated[str, Field(min_length=1), AfterValidator(validate_relative_path)]


def _validate_non_negative_decimal(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError("Decimal value must be non-negative")
    return value


def _validate_positive_decimal(value: Decimal) -> Decimal:
    if value <= 0:
        raise ValueError("Decimal value must be positive")
    return value


# Validator-based bounds keep generated JSON Schemas free of binary floats,
# which are forbidden in canonical identity material.
NonNegativeDecimal = Annotated[Decimal, AfterValidator(_validate_non_negative_decimal)]
PositiveDecimal = Annotated[Decimal, AfterValidator(_validate_positive_decimal)]


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class VersionedModel(FrozenModel):
    schema_name: NonEmptyStr
    schema_version: str = SCHEMA_VERSION
    id_scheme_version: str = ID_SCHEME_VERSION
    extensions: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def _current_schema_major(cls, value: str) -> str:
        if value.split(".", maxsplit=1)[0] != SCHEMA_VERSION.split(".", maxsplit=1)[0]:
            raise ValueError("Unsupported schema major version")
        return value


class TrustClass(StrEnum):
    PRIMARY = "primary"
    OFFICIAL = "official"
    RESEARCH = "research"
    USER = "user"
    COMMUNITY = "community"
    GENERATED = "generated"
    UNTRUSTED = "untrusted"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    SECRET = "secret"


class ProducerKind(StrEnum):
    KERNEL = "kernel"
    HUMAN = "human"
    MODEL = "model"
    SKILL = "skill"
    ADAPTER = "adapter"


class HashRef(FrozenModel):
    algorithm: Literal["sha256"] = "sha256"
    hex: Sha256


class RecordRef(FrozenModel):
    kind: Identifier
    id: Identifier
    object_sha256: Sha256


class ComponentRef(FrozenModel):
    name: Identifier
    version: NonEmptyStr
    config_sha256: Sha256
    artifact_sha256: Sha256 | None = None


class ProducerRef(FrozenModel):
    kind: ProducerKind
    component: ComponentRef
    destination: NonEmptyStr | None = None


class TimeRange(FrozenModel):
    valid_at: AwareDatetime | None = None
    invalid_at: AwareDatetime | None = None

    @field_validator("valid_at", "invalid_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)


class ErrorCategory(StrEnum):
    TRANSIENT = "transient"
    VALIDATION = "validation"
    POLICY = "policy"
    DATA = "data"
    DEPENDENCY = "dependency"
    TERMINAL = "terminal"


class ErrorRecord(FrozenModel):
    category: ErrorCategory
    code: Identifier
    message: NonEmptyStr
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
