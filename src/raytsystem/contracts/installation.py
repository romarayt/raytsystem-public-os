"""Installer contracts for the raytsystem ``bootstrap`` flow.

These records model the state the installer persists into a user's repository
(``.raytsystem/installation.json`` and ``.raytsystem/source-map.json``) plus the
read-only dry-run plan the ``bootstrap`` command emits.

They are :class:`VersionedModel` subclasses so they inherit the frozen,
``extra="forbid"`` envelope and the ``RelativePath`` validator that structurally
forbids absolute paths on every OS — this is what keeps host absolute paths out
of persisted and exported installer JSON. They are intentionally **not** part of
``contracts.SCHEMA_MODELS``: the frozen public schema registry (``v1.4.0``) binds
existing ledger generations, so new public schemas belong to a deliberate version
bump, not to internal installer state.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from raytsystem.contracts.base import (
    Identifier,
    NonEmptyStr,
    RelativePath,
    Sensitivity,
    Sha256,
    TrustClass,
    VersionedModel,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)

Confidence = Literal["low", "medium", "high"]


class InstallationMode(StrEnum):
    """How the raytsystem engine relates to the target repository."""

    MANAGED = "managed"  # engine is an external pinned dependency; workspace only
    VENDORED = "vendored"  # engine copied into a dedicated in-repo directory


class SourceType(StrEnum):
    """Detected shape of a user's source repository."""

    EMPTY = "empty"
    OBSIDIAN = "obsidian"
    MARKDOWN = "markdown"
    GRAPHIFY = "graphify"
    SOFTWARE = "software"
    MIXED = "mixed"


class SourceRootPolicy(StrEnum):
    """What the installer is allowed to do with a source root."""

    INDEX_AND_GRAPH = "index_and_graph"
    INDEX_ONLY = "index_only"
    OBSERVE_ONLY = "observe_only"
    IGNORE = "ignore"


def _hash_identity(payload: dict[str, Any]) -> str:
    return sha256_hex(canonical_json_bytes(payload))


class SourceSignalRecord(VersionedModel):
    """One deterministic evidence signal the classifier observed."""

    schema_name: Literal["SourceSignalV1"] = "SourceSignalV1"
    kind: NonEmptyStr
    source_type: SourceType
    weight: int = Field(ge=0, le=100)
    evidence: NonEmptyStr


class RankedSourceType(VersionedModel):
    schema_name: Literal["RankedSourceTypeV1"] = "RankedSourceTypeV1"
    source_type: SourceType
    score: int = Field(ge=0, le=100)
    contributing_signals: tuple[NonEmptyStr, ...] = ()


class SourceClassification(VersionedModel):
    """Read-only, reproducible classification of a repository root."""

    schema_name: Literal["SourceClassificationV1"] = "SourceClassificationV1"
    classification_id: Identifier
    root_fingerprint: Sha256
    primary_type: SourceType
    is_mixed: bool
    ranked_types: tuple[RankedSourceType, ...]
    signals: tuple[SourceSignalRecord, ...]
    classifier_version: Literal["1.0.0"] = "1.0.0"

    def identity_payload(self) -> dict[str, Any]:
        return {
            "root_fingerprint": self.root_fingerprint,
            "primary_type": self.primary_type.value,
            "is_mixed": self.is_mixed,
            "ranked_types": [
                {"source_type": r.source_type.value, "score": r.score} for r in self.ranked_types
            ],
        }

    def verify_id(self) -> bool:
        return self.classification_id == derive_id("srcclass", self.identity_payload())


class SourceRoot(VersionedModel):
    """A single registered source root inside the user's repository."""

    schema_name: Literal["SourceRootV1"] = "SourceRootV1"
    source_root_id: Identifier
    relative_path: RelativePath
    source_type: SourceType
    adapter: Identifier
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    trust_class: TrustClass = TrustClass.USER
    policy: SourceRootPolicy = SourceRootPolicy.INDEX_ONLY
    provenance: tuple[Identifier, ...] = ()
    confidence: Confidence = "high"

    def identity_payload(self) -> dict[str, Any]:
        return {"relative_path": self.relative_path, "source_type": self.source_type.value}

    def verify_id(self) -> bool:
        return self.source_root_id == derive_id("srcroot", self.identity_payload())

    @model_validator(mode="after")
    def _graph_only_for_code(self) -> SourceRoot:
        if self.policy is SourceRootPolicy.INDEX_AND_GRAPH and self.source_type not in {
            SourceType.SOFTWARE,
            SourceType.MIXED,
        }:
            raise ValueError("Only code roots may enter the code graph")
        return self


class SourceMap(VersionedModel):
    """The set of source roots the installer proposes or has registered."""

    schema_name: Literal["SourceMapV1"] = "SourceMapV1"
    source_map_id: Identifier
    installation_id: Identifier
    roots: tuple[SourceRoot, ...]
    map_sha256: Sha256
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _utc(cls, value: AwareDatetime) -> AwareDatetime:
        from datetime import UTC

        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _unique_roots(self) -> SourceMap:
        ids = [r.source_root_id for r in self.roots]
        paths = [r.relative_path for r in self.roots]
        if len(ids) != len(set(ids)) or len(paths) != len(set(paths)):
            raise ValueError("Source roots must be unique by id and by path")
        return self

    def content_payload(self) -> dict[str, Any]:
        return {
            "installation_id": self.installation_id,
            "roots": [r.model_dump(mode="json") for r in self.roots],
        }

    def verify_hash(self) -> bool:
        return self.map_sha256 == _hash_identity(self.content_payload())

    def verify_id(self) -> bool:
        return self.source_map_id == derive_id(
            "srcmap", {"installation_id": self.installation_id, "map_sha256": self.map_sha256}
        )


class InstallationRecord(VersionedModel):
    """Persisted record of an applied raytsystem installation (``installation.json``).

    Contains only hashes, enums, identifiers and workspace-relative paths — never
    secret values and never absolute paths.
    """

    schema_name: Literal["InstallationRecordV1"] = "InstallationRecordV1"
    installation_id: Identifier
    raytsystem_version: NonEmptyStr
    template_id: NonEmptyStr
    template_version: NonEmptyStr
    mode: InstallationMode
    source_root_paths: tuple[RelativePath, ...] = ()
    config_sha256: Sha256
    applied_migrations: tuple[Identifier, ...] = ()
    created_files: tuple[RelativePath, ...] = ()
    merged_files: tuple[RelativePath, ...] = ()
    skipped_conflicts: tuple[RelativePath, ...] = ()
    backup_id: Identifier | None = None
    record_sha256: Sha256
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def _utc(cls, value: AwareDatetime) -> AwareDatetime:
        from datetime import UTC

        return value.astimezone(UTC)

    def content_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"record_sha256", "installation_id"})

    def verify_hash(self) -> bool:
        return self.record_sha256 == _hash_identity(self.content_payload())


class PreflightReport(VersionedModel):
    """Read-only safety assessment of a bootstrap target."""

    schema_name: Literal["PreflightReportV1"] = "PreflightReportV1"
    python_ok: bool
    os: NonEmptyStr
    target_exists: bool
    writable: bool
    is_git_repo: bool
    git_clean: bool | None = None
    already_initialized: bool = False
    warnings: tuple[NonEmptyStr, ...] = ()
    blockers: tuple[NonEmptyStr, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.blockers


class BootstrapPlan(VersionedModel):
    """The dry-run plan the ``bootstrap`` command emits. Never persisted.

    Carries no absolute filesystem path: ``target_name`` is the basename only and
    every file field is a ``RelativePath``.
    """

    schema_name: Literal["BootstrapPlanV1"] = "BootstrapPlanV1"
    bootstrap_plan_id: Identifier
    action: Literal["bootstrap"] = "bootstrap"
    target_name: NonEmptyStr
    mode: InstallationMode
    template_id: NonEmptyStr
    template_version: NonEmptyStr
    context_language: NonEmptyStr
    preflight: PreflightReport
    classification: SourceClassification
    source_map: SourceMap
    init_plan_id: Identifier
    manifest_sha256: Sha256
    files_to_create: tuple[RelativePath, ...]
    conflicts: tuple[RelativePath, ...]
    existing_repository: bool
    confirmation_required: bool
    protected_collisions: tuple[RelativePath, ...] = ()
    post_init_steps: tuple[NonEmptyStr, ...]
    fingerprint: Identifier
    dry_run: Literal[True] = True

    def verify_fingerprint(self) -> bool:
        return self.fingerprint == derive_id(
            "bootstrap",
            {
                "init_plan_id": self.init_plan_id,
                "manifest_sha256": self.manifest_sha256,
                "mode": self.mode.value,
                "template_id": self.template_id,
                "context_language": self.context_language,
            },
        )
