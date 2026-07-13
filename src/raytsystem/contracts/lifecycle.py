from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import AwareDatetime, Field, field_validator, model_validator

from raytsystem.contracts.base import (
    Identifier,
    NonEmptyStr,
    RelativePath,
    Sha256,
    VersionedModel,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)


class PackageLifecycleState(StrEnum):
    DISCOVERED = "discovered"
    INSPECTED = "inspected"
    QUARANTINED = "quarantined"
    VALIDATED = "validated"
    EVALUATED = "evaluated"
    APPROVED = "approved"
    INSTALLED = "installed"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ROLLED_BACK = "rolled_back"
    BLOCKED = "blocked"


class PackageManifest(VersionedModel):
    schema_name: Literal["PackageManifestV1"] = "PackageManifestV1"
    package_id: Identifier
    name: NonEmptyStr
    version: NonEmptyStr
    publisher: NonEmptyStr
    source_url: NonEmptyStr | None = None
    source_commit: NonEmptyStr | None = None
    content_sha256: Sha256
    signature: NonEmptyStr | None = None
    license_expression: NonEmptyStr
    raytsystem_compatibility: NonEmptyStr
    dependencies: dict[Identifier, NonEmptyStr] = Field(default_factory=dict)
    permissions: tuple[Identifier, ...] = ()
    runtime_requirements: tuple[Identifier, ...] = ()
    tool_ids: tuple[Identifier, ...] = ()
    skill_ids: tuple[Identifier, ...] = ()
    agent_ids: tuple[Identifier, ...] = ()
    workflow_ids: tuple[Identifier, ...] = ()
    template_ids: tuple[Identifier, ...] = ()
    fixture_ids: tuple[Identifier, ...] = ()
    eval_suite_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def _manifest_invariants(self) -> PackageManifest:
        collections = (
            self.permissions,
            self.runtime_requirements,
            self.tool_ids,
            self.skill_ids,
            self.agent_ids,
            self.workflow_ids,
            self.template_ids,
            self.fixture_ids,
            self.eval_suite_ids,
        )
        if any(len(values) != len(set(values)) for values in collections):
            raise ValueError("Package manifest references must be unique")
        if any(not version.strip() for version in self.dependencies.values()):
            raise ValueError("Package dependencies must be pinned")
        return self


class PackageRevision(VersionedModel):
    schema_name: Literal["PackageRevisionV1"] = "PackageRevisionV1"
    revision_id: Identifier
    package_id: Identifier
    manifest_sha256: Sha256
    content_sha256: Sha256
    state: PackageLifecycleState
    previous_revision_id: Identifier | None = None
    validation_report_sha256: Sha256 | None = None
    eval_run_ids: tuple[Identifier, ...] = ()
    approval_id: Identifier | None = None
    activated_by: Identifier | None = None
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _revision_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _revision_state(self) -> PackageRevision:
        if (
            self.state in {PackageLifecycleState.ACTIVE, PackageLifecycleState.APPROVED}
            and self.approval_id is None
        ):
            raise ValueError("Approved and active package revisions require approval")
        if self.state is PackageLifecycleState.ACTIVE and self.activated_by is None:
            raise ValueError("Active package revisions require an actor")
        return self


class WorkspaceTemplate(VersionedModel):
    schema_name: Literal["WorkspaceTemplateV1"] = "WorkspaceTemplateV1"
    template_id: Literal["software", "content", "research", "youtube"]
    name: NonEmptyStr
    version: NonEmptyStr
    description: NonEmptyStr
    pack_ids: tuple[Identifier, ...]
    agent_ids: tuple[Identifier, ...]
    skill_ids: tuple[Identifier, ...]
    workflow_ids: tuple[Identifier, ...]
    task_template_ids: tuple[Identifier, ...]
    policy_profile_id: Identifier
    eval_suite_ids: tuple[Identifier, ...]
    ui_defaults: dict[str, str] = Field(default_factory=dict)
    manifest_sha256: Sha256


class WorkspaceInitPlan(VersionedModel):
    schema_name: Literal["WorkspaceInitPlanV1"] = "WorkspaceInitPlanV1"
    init_plan_id: Identifier
    template_id: Literal["software", "content", "research", "youtube"]
    template_version: NonEmptyStr
    files_to_create: tuple[RelativePath, ...]
    conflicts: tuple[RelativePath, ...] = ()
    existing_repository: bool
    confirmation_required: bool
    manifest_sha256: Sha256
    dry_run: bool = True

    @model_validator(mode="after")
    def _init_plan_invariants(self) -> WorkspaceInitPlan:
        if set(self.files_to_create) & set(self.conflicts):
            raise ValueError("Conflicting files cannot be scheduled for creation")
        if self.existing_repository and self.files_to_create and not self.confirmation_required:
            raise ValueError("Existing repositories require explicit confirmation")
        return self


class MigrationState(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    STALE = "stale"


class MigrationPlan(VersionedModel):
    schema_name: Literal["MigrationPlanV1"] = "MigrationPlanV1"
    migration_plan_id: Identifier
    from_version: NonEmptyStr
    to_version: NonEmptyStr
    migration_ids: tuple[Identifier, ...]
    backup_required: bool = True
    reversible: bool
    plan_sha256: Sha256
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _plan_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class MigrationRecord(VersionedModel):
    schema_name: Literal["MigrationRecordV1"] = "MigrationRecordV1"
    migration_record_id: Identifier
    migration_id: Identifier
    migration_sha256: Sha256
    from_version: NonEmptyStr
    to_version: NonEmptyStr
    state: MigrationState
    attempt: int = Field(ge=1)
    backup_id: Identifier | None = None
    report_sha256: Sha256 | None = None
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @field_validator("started_at", "completed_at")
    @classmethod
    def _migration_timestamp_utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)


class KeyProviderState(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"


class KeyProviderStatus(VersionedModel):
    schema_name: Literal["KeyProviderStatusV1"] = "KeyProviderStatusV1"
    provider_id: Identifier
    kind: Literal["os_keychain", "environment", "age_identity", "external_kms"]
    state: KeyProviderState
    key_id: Identifier | None = None
    algorithm: NonEmptyStr | None = None
    reason_codes: tuple[Identifier, ...] = ()
    external: bool = False


class EncryptedBlob(VersionedModel):
    schema_name: Literal["EncryptedBlobV1"] = "EncryptedBlobV1"
    blob_id: Identifier
    key_provider_id: Identifier
    key_id: Identifier
    algorithm: Literal["aes-256-gcm"]
    algorithm_version: Literal["1"] = "1"
    encrypted_data_key: NonEmptyStr
    nonce: NonEmptyStr
    ciphertext: NonEmptyStr
    authentication_tag: NonEmptyStr
    plaintext_sha256: Sha256
    associated_data_sha256: Sha256
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _blob_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class BackupKind(StrEnum):
    PRIVATE = "private_backup"
    PUBLIC = "public_release_export"
    DIAGNOSTIC = "diagnostic_export"
    TRANSFER = "workspace_transfer"


class BackupManifest(VersionedModel):
    schema_name: Literal["BackupManifestV1"] = "BackupManifestV1"
    backup_id: Identifier
    kind: BackupKind
    raytsystem_version: NonEmptyStr
    schema_versions: dict[Identifier, NonEmptyStr]
    file_hashes: dict[RelativePath, Sha256]
    excluded_paths: tuple[RelativePath, ...] = ()
    restricted_data_included: bool = False
    encrypted: bool = False
    redaction_report_sha256: Sha256 | None = None
    manifest_sha256: Sha256
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _backup_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    def identity_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="python", exclude={"backup_id", "manifest_sha256"})

    def verify_hash(self) -> bool:
        return self.manifest_sha256 == sha256_hex(canonical_json_bytes(self.identity_payload()))

    def verify_id(self) -> bool:
        return self.backup_id == derive_id(
            "backup", {"manifest_sha256": self.manifest_sha256, "kind": self.kind.value}
        )


class RestorePlan(VersionedModel):
    schema_name: Literal["RestorePlanV1"] = "RestorePlanV1"
    restore_plan_id: Identifier
    backup_id: Identifier
    manifest_sha256: Sha256
    compatibility: Literal["compatible", "migration_required", "incompatible"]
    files_to_restore: tuple[RelativePath, ...]
    conflicts: tuple[RelativePath, ...] = ()
    rebuild_projections: bool = True
    dry_run: bool = True
    overwrite_existing: Literal[False] = False


class RestoreReport(VersionedModel):
    schema_name: Literal["RestoreReportV1"] = "RestoreReportV1"
    restore_report_id: Identifier
    restore_plan_id: Identifier
    state: Literal["verified", "restored", "failed"]
    restored_hashes: dict[RelativePath, Sha256] = Field(default_factory=dict)
    projection_rebuild_required: bool = True
    doctor_passed: bool = False
    doctor_state: Literal["not_run", "passed", "failed"] = "not_run"
    report_sha256: Sha256
    completed_at: AwareDatetime

    @field_validator("completed_at")
    @classmethod
    def _restore_timestamp_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)
