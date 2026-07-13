from __future__ import annotations

import json
import os
import re
import signal
import sqlite3
import threading
import time
import tomllib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from raytsystem.contracts import (
    SCHEMA_VERSION,
    ApprovalRecord,
    Claim,
    ClaimStatus,
    ComponentRef,
    EvidenceItem,
    EvidencePack,
    GenerationEntry,
    LedgerGeneration,
    Normalization,
    ProducerRef,
    ProposalItem,
    ProposalPurpose,
    ProposalRequest,
    ProposalResponse,
    RecordRef,
    Segment,
    Sensitivity,
    Source,
    SourceRevision,
    TextLocator,
    TrustClass,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.base import ProducerKind, validate_relative_path
from raytsystem.contracts.evidence import Origin
from raytsystem.contracts.operations import PromotionEvent, PromotionState, PromotionTxn
from raytsystem.control import ControlDB, LeaseBusy, LeaseToken
from raytsystem.extractors import (
    Extraction,
    ExtractionError,
    Extractor,
    ExtractorRegistry,
    PdfExtractor,
)
from raytsystem.fetchers import LocalFileFetcher
from raytsystem.git_checkpoint import CheckpointRejected, GitCheckpoint
from raytsystem.io import write_bytes_atomic
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.security.sensitivity import SecretScanner, SensitivityDecision
from raytsystem.storage import (
    IntegrityError,
    publish_content_addressed,
    publish_immutable,
    publish_model,
    read_current_generation,
    read_json,
    rebuild_jsonl,
    replace_current_generation,
    validate_generation_id,
)


class InjectedFault(RuntimeError):
    """Deterministic test-only fault at a named durability boundary."""


class ApprovalRequired(RuntimeError):
    """Raised when a non-fixture promotion lacks a hash-bound approval."""


class UnsupportedInput(RuntimeError):
    """Raised when an adapter is unavailable or an input cannot be normalized safely."""


class QuarantinedInput(RuntimeError):
    """Raised after restricted bytes are isolated without derived processing."""


class ApprovalVerifier(Protocol):
    def verify(self, payload: bytes) -> ApprovalRecord: ...


class ApprovalVerifierUnavailable:
    def verify(self, payload: bytes) -> ApprovalRecord:
        del payload
        raise ApprovalRequired(
            "Workspace approval JSON is not trusted authority; configure an external verifier"
        )


@dataclass(frozen=True)
class IngestResult:
    status: str
    noop: bool
    run_id: str
    operation_key: str
    source_id: str
    source_revision_id: str
    raw_path: str
    normalization_id: str
    normalized_path: str
    segment_id: str
    generation_id: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, noop: bool) -> IngestResult:
        return cls(
            status=str(payload["status"]),
            noop=noop,
            run_id=str(payload["run_id"]),
            operation_key=str(payload["operation_key"]),
            source_id=str(payload["source_id"]),
            source_revision_id=str(payload["source_revision_id"]),
            raw_path=str(payload["raw_path"]),
            normalization_id=str(payload["normalization_id"]),
            normalized_path=str(payload["normalized_path"]),
            segment_id=str(payload["segment_id"]),
            generation_id=str(payload["generation_id"]),
        )


@dataclass(frozen=True)
class _Prepared:
    result: IngestResult
    claim: Claim
    claim_object_sha256: str
    generation: LedgerGeneration
    txn: PromotionTxn
    event: PromotionEvent
    run_created_at: datetime


class IngestPipeline:
    PIPELINE_VERSION = "1.2.0"

    def __init__(
        self,
        root: Path,
        *,
        fail_at: str | None = None,
        hard_fail_at: str | None = None,
        scanner: Any | None = None,
        approval_verifier: ApprovalVerifier | None = None,
    ) -> None:
        self.root = root.resolve()
        self.fail_at = fail_at
        self.hard_fail_at = hard_fail_at
        self.scanner = scanner or SecretScanner()
        self.approval_verifier = approval_verifier or ApprovalVerifierUnavailable()
        self.worker_id = f"worker_{uuid.uuid4().hex}"
        self.config = self._load_config()
        self.control = ControlDB(self.root / str(self.config["control_db"]))
        self.extractors = ExtractorRegistry()

    def _load_config(self) -> dict[str, Any]:
        path = self.root / "config" / "raytsystem.toml"
        if not path.is_file():
            raise IntegrityError("Missing config/raytsystem.toml")
        with path.open("rb") as handle:
            config = tomllib.load(handle)
        config.setdefault("control_db", "ops/control.sqlite")
        try:
            config["control_db"] = validate_relative_path(str(config["control_db"]))
        except ValueError as error:
            raise IntegrityError("Configured control DB path escapes the workspace") from error
        config.setdefault("limits", {})
        config["limits"].setdefault("max_input_bytes", 25 * 1024 * 1024)
        config["limits"].setdefault("lease_ttl_seconds", 60)
        return config

    def _fault(self, checkpoint: str) -> None:
        if self.fail_at == checkpoint:
            raise InjectedFault(checkpoint)
        if (
            self.hard_fail_at == checkpoint
            and os.environ.get("RAYTSYSTEM_ENABLE_TEST_HARD_FAULTS") == "1"
        ):
            os.kill(os.getpid(), signal.SIGKILL)

    def ingest(
        self,
        source: str | Path,
        *,
        fixture: bool = False,
        prepare_only: bool = False,
    ) -> IngestResult:
        read = LocalFileFetcher(
            self.root,
            max_bytes=int(self.config["limits"]["max_input_bytes"]),
        ).fetch(source)
        sensitivity = self._classify_or_quarantine(read.data, read.relative_path)
        fixture_policy_sha256 = self._authorize_fixture(
            read.relative_path,
            read.data,
            requested=fixture,
        )
        content_sha256 = sha256_hex(read.data)
        try:
            extractor = self.extractors.select(read.relative_path)
        except ExtractionError as error:
            raise UnsupportedInput(str(error)) from error
        self._assert_extractor_authorized(extractor, fixture=fixture)
        try:
            extraction = extractor.extract(read.data, source_path=read.relative_path)
        except ExtractionError as error:
            raise UnsupportedInput(str(error)) from error
        self._classify_derived_or_quarantine(
            raw=read.data,
            relative_path=read.relative_path,
            extraction=extraction,
        )
        source_id = derive_id(
            "src",
            {
                "identity_scheme": "workspace_relative_path_v1",
                "identity_key": read.relative_path,
            },
        )
        adapter_config_sha256 = self._adapter_config_sha256(extractor, extraction)
        operation_key = self._derive_operation_key(
            source_id=source_id,
            content_sha256=content_sha256,
            adapter_config_sha256=adapter_config_sha256,
            fixture=fixture,
            fixture_policy_sha256=fixture_policy_sha256,
            sensitivity=sensitivity,
        )
        proposed_run_id = f"run_{uuid.uuid4().hex}"
        claim = self.control.claim_operation(
            operation_key=operation_key,
            run_id=proposed_run_id,
            stage="ingest",
            partition_key=f"source:{source_id}",
            now_ms=self._now_ms(),
        )
        if claim.state == "succeeded" and claim.result is not None:
            return self.recover_run(claim.run_id, fixture=fixture)
        with self._hold_preparation(operation_key, source_id) as preparation_leases:
            operation = self.control.connection.execute(
                "SELECT run_id, state, result_json FROM operations WHERE operation_key = ?",
                (operation_key,),
            ).fetchone()
            if operation is None:
                raise IntegrityError("Claimed preparation operation disappeared")
            if str(operation["state"]) == "succeeded" and operation["result_json"] is not None:
                return self.recover_run(str(operation["run_id"]), fixture=fixture)
            return self._continue_claimed_ingest(
                run_id=str(operation["run_id"]),
                operation_key=operation_key,
                source_id=source_id,
                relative_path=read.relative_path,
                content=read.data,
                content_sha256=content_sha256,
                adapter_config_sha256=adapter_config_sha256,
                extractor=extractor,
                extraction=extraction,
                fixture=fixture,
                fixture_policy_sha256=fixture_policy_sha256,
                prepare_only=prepare_only,
                preparation_leases=preparation_leases,
            )

    @contextmanager
    def _hold_preparation(
        self,
        operation_key: str,
        source_id: str,
    ) -> Iterator[tuple[LeaseToken, ...]]:
        ttl_ms = int(self.config["limits"]["lease_ttl_seconds"]) * 1000
        wait_seconds = max(10.0, min((ttl_ms / 1000) * 2, 60.0))
        deadline = time.monotonic() + wait_seconds
        partitions = (
            f"prepare:operation:{operation_key}",
            f"prepare:source:{source_id}",
        )
        leases: list[LeaseToken] = []
        while True:
            candidate_leases: list[LeaseToken] = []
            try:
                now_ms = self._now_ms()
                for partition in partitions:
                    candidate_leases.append(
                        self.control.acquire_lease(
                            partition,
                            self.worker_id,
                            ttl_ms=ttl_ms,
                            now_ms=now_ms,
                        )
                    )
                leases = candidate_leases
                break
            except LeaseBusy:
                for lease in reversed(candidate_leases):
                    self.control.release_lease(lease)
                if time.monotonic() >= deadline:
                    raise LeaseBusy("Timed out waiting for preparation ownership") from None
                time.sleep(0.01)
        try:
            stop_heartbeat = threading.Event()
            lost_ownership = threading.Event()
            heartbeat = threading.Thread(
                target=self._heartbeat_preparation_leases,
                args=(
                    tuple(leases),
                    ttl_ms,
                    stop_heartbeat,
                    lost_ownership,
                ),
                name=f"raytsystem-prepare-{self.worker_id[-8:]}",
                daemon=True,
            )
            heartbeat.start()
            yield tuple(leases)
        finally:
            if "stop_heartbeat" in locals():
                stop_heartbeat.set()
                heartbeat.join(timeout=2)
            for lease in reversed(leases):
                self.control.release_lease(lease)

    def _heartbeat_preparation_leases(
        self,
        leases: tuple[LeaseToken, ...],
        ttl_ms: int,
        stop: threading.Event,
        lost: threading.Event,
    ) -> None:
        interval = max(0.05, min(ttl_ms / 3000, 5.0))
        try:
            connection = sqlite3.connect(
                self.control.path,
                isolation_level=None,
                timeout=1.0,
            )
            connection.execute("PRAGMA busy_timeout=1000")
        except sqlite3.Error:
            lost.set()
            return
        try:
            while not stop.wait(interval):
                now_ms = self._now_ms()
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    valid = True
                    for lease in leases:
                        cursor = connection.execute(
                            "UPDATE leases SET expires_at_ms = ?, renewed_at_ms = ? "
                            "WHERE partition_key = ? AND control_epoch = ? "
                            "AND owner_run_id = ? AND fencing_token = ? "
                            "AND expires_at_ms > ?",
                            (
                                now_ms + ttl_ms,
                                now_ms,
                                lease.partition_key,
                                lease.control_epoch,
                                lease.owner_run_id,
                                lease.fencing_token,
                                now_ms,
                            ),
                        )
                        valid = valid and cursor.rowcount == 1
                    connection.execute("COMMIT")
                    if not valid:
                        lost.set()
                        return
                except sqlite3.OperationalError:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    continue
        finally:
            connection.close()

    def _continue_claimed_ingest(
        self,
        *,
        run_id: str,
        operation_key: str,
        source_id: str,
        relative_path: str,
        content: bytes,
        content_sha256: str,
        adapter_config_sha256: str,
        extractor: Extractor,
        extraction: Extraction,
        fixture: bool,
        fixture_policy_sha256: str | None,
        prepare_only: bool,
        preparation_leases: tuple[LeaseToken, ...],
    ) -> IngestResult:
        run_created_at = self._load_or_create_run_manifest(
            run_id=run_id,
            operation_key=operation_key,
            source_id=source_id,
            input_sha256=content_sha256,
            input_path=relative_path,
            fixture_authorized=fixture,
            fixture_policy_sha256=fixture_policy_sha256,
        )
        prepared = self._load_prepared(run_id)
        if prepared is None:
            prepared = self._load_from_wal(operation_key, run_id)
        if prepared is None:
            prepared = self._prepare(
                run_id=run_id,
                run_created_at=run_created_at,
                operation_key=operation_key,
                source_id=source_id,
                relative_source_path=relative_path,
                content=content,
                content_sha256=content_sha256,
                adapter_config_sha256=adapter_config_sha256,
                extractor=extractor,
                extraction=extraction,
                fixture=fixture,
            )
        self._validate_prepared(prepared)
        if not all(
            self.control.verify_lease(lease, now_ms=self._now_ms()) for lease in preparation_leases
        ):
            raise LeaseBusy("Preparation ownership expired before its final gate")
        if prepare_only:
            return prepared.result
        if not fixture:
            self._update_run_manifest(run_id, state="awaiting_approval")
            self.control.update_operation(
                operation_key,
                state="awaiting_approval",
                now_ms=self._now_ms(),
            )
            raise ApprovalRequired(
                "Real-corpus promotion requires an ApprovalRecord bound to the staged hash"
            )
        return self._promote(
            prepared,
            fixture_authorized=True,
            fixture_policy_sha256=fixture_policy_sha256,
        )

    def validate_run(self, run_id: str) -> IngestResult:
        if re.fullmatch(r"run_[0-9a-f]{32}", run_id) is None:
            raise IntegrityError("Malformed run identifier")
        manifest = read_json(self.root / "ops" / "runs" / run_id / "manifest.json")
        operation_key = str(manifest["operation_key"])
        prepared = self._load_prepared(run_id) or self._load_from_wal(operation_key, run_id)
        if prepared is None:
            raise IntegrityError("Run has no recoverable prepared bundle")
        self._validate_prepared(prepared)
        return prepared.result

    def promote_run(
        self,
        run_id: str,
        *,
        fixture: bool = False,
        approval_path: str | Path | None = None,
    ) -> IngestResult:
        return self.recover_run(
            run_id,
            fixture=fixture,
            approval_path=approval_path,
        )

    def recover_run(
        self,
        run_id: str,
        *,
        fixture: bool = False,
        approval_path: str | Path | None = None,
    ) -> IngestResult:
        if re.fullmatch(r"run_[0-9a-f]{32}", run_id) is None:
            raise IntegrityError("Malformed run identifier")
        manifest = read_json(self.root / "ops" / "runs" / run_id / "manifest.json")
        operation_key = str(manifest["operation_key"])
        operation = self.control.connection.execute(
            "SELECT run_id, state, result_json FROM operations WHERE operation_key = ?",
            (operation_key,),
        ).fetchone()
        if operation is None:
            raise IntegrityError("Recovery operation is missing from the durable control DB")
        if str(operation["run_id"]) != run_id:
            raise IntegrityError("Recovery operation run ownership mismatch")
        wal = self.control.promotion_for_operation(operation_key)
        run_state = str(manifest.get("state", ""))
        terminal_operation = bool(
            str(operation["state"]) == "succeeded"
            and operation["result_json"] is not None
            and run_state == "succeeded"
            and (wal is None or str(wal["state"]) == "completed")
        )
        if terminal_operation:
            result = IngestResult.from_dict(
                json.loads(str(operation["result_json"])),
                noop=True,
            )
            if result.run_id != run_id or result.operation_key != operation_key:
                raise IntegrityError("Successful operation result provenance mismatch")
            active_id = read_current_generation(self.root)
            active_manifest = LedgerGeneration.model_validate(
                read_json(self.root / "ledger" / "generations" / f"{active_id}.json")
            )
            try:
                self._assert_active_generation_reconciled(active_manifest)
            except IntegrityError:
                if wal is None or active_id != str(wal["next_generation_id"]):
                    raise
            else:
                return result

        if wal is not None and read_current_generation(self.root) == str(wal["next_generation_id"]):
            prepared = self._load_from_wal(operation_key, run_id)
            if prepared is None:
                raise IntegrityError("Committed recovery WAL disappeared")
            return self._recover_committed(prepared, wal)

        prepared = self._load_prepared(run_id) or self._load_from_wal(operation_key, run_id)
        if prepared is None:
            raise IntegrityError("Run has no recoverable prepared bundle")
        self._validate_prepared(prepared)
        fixture_authorized, fixture_policy_sha256 = self._derive_fixture_authority(
            prepared,
            manifest,
        )
        recorded_fixture = manifest.get("fixture_authorized")
        if not isinstance(recorded_fixture, bool) or recorded_fixture is not fixture_authorized:
            raise IntegrityError("Run fixture authority audit fields were changed")
        if manifest.get("fixture_policy_sha256") != fixture_policy_sha256:
            raise IntegrityError("Run fixture policy audit hash was changed")

        approval: ApprovalRecord | None = None
        if fixture_authorized:
            if not fixture or approval_path is not None:
                raise ApprovalRequired(
                    "Fixture recovery requires the authority recorded at preparation"
                )
        elif fixture:
            raise ApprovalRequired("A real prepared run cannot be relabeled as a fixture")
        if not fixture_authorized:
            approval = self._load_and_validate_approval(prepared, approval_path)
        return self._promote(
            prepared,
            approval=approval,
            fixture_authorized=fixture_authorized,
            fixture_policy_sha256=fixture_policy_sha256,
        )

    def _derive_operation_key(
        self,
        *,
        source_id: str,
        content_sha256: str,
        adapter_config_sha256: str,
        fixture: bool,
        fixture_policy_sha256: str | None,
        sensitivity: SensitivityDecision,
    ) -> str:
        return derive_id(
            "op",
            {
                "operation": "ingest",
                "pipeline_version": self.PIPELINE_VERSION,
                "source_id": source_id,
                "content_sha256": content_sha256,
                "adapter_config_sha256": adapter_config_sha256,
                "fixture": fixture,
                "fixture_policy_sha256": fixture_policy_sha256,
                "schema_registry_sha256": self._schema_registry_sha256(),
                "proposal_adapter": "raytsystem_fake_proposal@1.0.0",
                "policy_sha256": self._policy_sha256(),
                "sensitivity_scanner": {
                    "name": sensitivity.scanner_name,
                    "version": sensitivity.scanner_version,
                },
            },
        )

    @staticmethod
    def _adapter_config_sha256(extractor: Extractor, extraction: Extraction) -> str:
        runtime: dict[str, str] = {}
        if isinstance(extractor, PdfExtractor):
            runtime = extractor.operation_config()
        material: dict[str, Any] = {
            "adapter": extractor.name,
            "version": extractor.version,
            "segmenter": "extractor_native_spans_v1",
            "extraction_sha256": sha256_hex(extraction.document.encode()),
        }
        if runtime:
            material["runtime"] = runtime
        return sha256_hex(canonical_json_bytes(material))

    @staticmethod
    def _assert_extractor_authorized(extractor: Extractor, *, fixture: bool) -> None:
        if (
            isinstance(extractor, PdfExtractor)
            and extractor.containment_profile() != "macos_restricted_v1"
            and not fixture
        ):
            raise UnsupportedInput(
                "Real PDF parsing requires the restricted OS sandbox; "
                "the Python-only fallback is fixture-only"
            )

    def _derive_fixture_authority(
        self,
        prepared: _Prepared,
        run_manifest: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Re-derive fixture authority from evidence and policy, never audit booleans."""
        result = prepared.result
        source = self._load_source(result.source_id)
        if source.origin.kind != "workspace_file" or source.origin.locator is None:
            raise IntegrityError("Logical source has no recoverable workspace locator")
        source_path = source.origin.locator
        revision = self._load_source_revision(result.source_revision_id)
        raw = read_regular_file(
            self.root,
            revision.raw_path,
            max_bytes=int(self.config["limits"]["max_input_bytes"]),
        ).data
        content_sha256 = sha256_hex(raw)
        expected_manifest_fields = {
            "run_id": result.run_id,
            "operation_type": "ingest",
            "operation_key": result.operation_key,
            "source_id": result.source_id,
            "input_sha256": content_sha256,
            "input_path": source_path,
        }
        for field, expected in expected_manifest_fields.items():
            if run_manifest.get(field) != expected:
                raise IntegrityError(f"Run manifest {field} provenance mismatch")
        expected_source_id = derive_id(
            "src",
            {
                "identity_scheme": "workspace_relative_path_v1",
                "identity_key": source_path,
            },
        )
        if expected_source_id != result.source_id:
            raise IntegrityError("Run input path does not bind the logical source")
        if revision.content_sha256 != content_sha256:
            raise IntegrityError("Raw evidence hash mismatch during authority derivation")

        sensitivity = self._classify_or_quarantine(raw, source_path)
        try:
            extractor = self.extractors.select(source_path)
            if (
                isinstance(extractor, PdfExtractor)
                and extractor.containment_profile() != "macos_restricted_v1"
            ):
                # A weaker fallback is permitted only after the exact bytes have
                # independently passed fixture policy; it can never parse a real run.
                self._authorize_fixture(source_path, raw, requested=True)
            extraction = extractor.extract(raw, source_path=source_path)
        except ExtractionError as error:
            raise IntegrityError("Raw evidence cannot re-derive operation authority") from error
        self._classify_derived_or_quarantine(
            raw=raw,
            relative_path=source_path,
            extraction=extraction,
        )
        adapter_config_sha256 = self._adapter_config_sha256(extractor, extraction)
        real_key = self._derive_operation_key(
            source_id=result.source_id,
            content_sha256=content_sha256,
            adapter_config_sha256=adapter_config_sha256,
            fixture=False,
            fixture_policy_sha256=None,
            sensitivity=sensitivity,
        )
        if real_key == result.operation_key:
            return False, None

        try:
            fixture_policy_sha256 = self._authorize_fixture(
                source_path,
                raw,
                requested=True,
            )
        except ApprovalRequired as error:
            raise IntegrityError(
                "Prepared operation no longer has verifiable fixture authority"
            ) from error
        fixture_key = self._derive_operation_key(
            source_id=result.source_id,
            content_sha256=content_sha256,
            adapter_config_sha256=adapter_config_sha256,
            fixture=True,
            fixture_policy_sha256=fixture_policy_sha256,
            sensitivity=sensitivity,
        )
        if fixture_key != result.operation_key:
            raise IntegrityError("Prepared operation fingerprint cannot be re-derived")
        return True, fixture_policy_sha256

    def export_proposal(self, run_id: str) -> dict[str, str]:
        self.validate_run(run_id)
        staging = self.root / "ops" / "staging" / run_id
        destination = self.root / "artifacts" / "drafts" / "proposals" / run_id
        exported: dict[str, str] = {}
        for name in ("evidence_pack.json", "proposal_request.json", "proposal_response.json"):
            source = staging / name
            if not source.is_file():
                raise IntegrityError(f"Prepared proposal artifact is missing: {name}")
            target = destination / name
            write_bytes_atomic(target, source.read_bytes(), mode=0o600)
            exported[name] = target.relative_to(self.root).as_posix()
        return exported

    def _load_and_validate_approval(
        self,
        prepared: _Prepared,
        approval_path: str | Path | None,
    ) -> ApprovalRecord:
        if approval_path is None:
            raise ApprovalRequired(
                "Real-corpus promotion requires an ApprovalRecord file bound to the candidate"
            )
        read = read_regular_file(self.root, approval_path, max_bytes=1024 * 1024)
        incoming_root = PurePosixPath("ops/approvals/incoming")
        incoming_path = PurePosixPath(read.relative_path)
        if incoming_root not in incoming_path.parents:
            raise ApprovalRequired("Approval must come from the isolated incoming approval zone")
        self._classify_or_quarantine(read.data, read.relative_path)
        try:
            approval = self.approval_verifier.verify(read.data)
        except ApprovalRequired:
            raise
        except Exception as error:
            raise ApprovalRequired("External approval verifier rejected the record") from error
        self._assert_approval_valid(prepared, approval, at=datetime.now(UTC))
        publish_immutable(
            self.root / "ops" / "approvals" / "accepted" / f"{approval.approval_id}.json",
            canonical_json_bytes(approval),
        )
        verifier_name = str(
            getattr(self.approval_verifier, "name", type(self.approval_verifier).__name__)
        )
        verifier_version = str(getattr(self.approval_verifier, "version", "unknown"))
        verifier_key_id = str(getattr(self.approval_verifier, "key_id", "unknown"))
        approval_sha256 = sha256_hex(canonical_json_bytes(approval))
        verification_material = {
            "approval_id": approval.approval_id,
            "approval_sha256": approval_sha256,
            "verifier": {
                "name": verifier_name,
                "version": verifier_version,
                "key_id": verifier_key_id,
            },
        }
        verification_path = (
            self.root
            / "ops"
            / "approvals"
            / "accepted"
            / f"{approval.approval_id}.verification.json"
        )
        if verification_path.is_file():
            existing = read_json(verification_path)
            if any(existing.get(key) != value for key, value in verification_material.items()):
                raise IntegrityError("Accepted approval verification metadata changed")
        else:
            verification = {
                "schema_version": "1.0.0",
                "verification_id": derive_id("aver", verification_material),
                **verification_material,
                "verified_at": datetime.now(UTC),
            }
            publish_immutable(verification_path, canonical_json_bytes(verification))
        return approval

    def _assert_approval_valid(
        self,
        prepared: _Prepared,
        approval: ApprovalRecord,
        *,
        at: datetime,
    ) -> None:
        artifact_sha256 = prepared.txn.candidate_manifest_sha256
        if artifact_sha256 is None:
            raise IntegrityError("Prepared candidate has no manifest hash")
        expected_identity = ApprovalRecord.create(
            action="promote",
            target_id=prepared.txn.txn_id,
            artifact_sha256=artifact_sha256,
            destination=approval.destination,
            scope=approval.scope,
            policy_version=approval.policy_version,
            policy_sha256=approval.policy_sha256,
            approver=approval.approver,
            approved_at=approval.approved_at,
            expires_at=approval.expires_at,
            conditions=approval.conditions,
        )
        if (
            approval.approval_id != expected_identity.approval_id
            or not approval.is_valid_for(
                action="promote",
                target_id=prepared.txn.txn_id,
                artifact_sha256=artifact_sha256,
                at=at,
            )
            or approval.policy_version != "1.0.0"
            or approval.policy_sha256 != self._policy_sha256()
            or approval.scope != ("real_corpus",)
            or approval.conditions
        ):
            raise ApprovalRequired("ApprovalRecord does not match the exact promotion candidate")

    def import_proposal(self, run_id: str, response_path: str | Path) -> IngestResult:
        prepared = self.validate_run(run_id)
        manifest = read_json(self.root / "ops" / "runs" / run_id / "manifest.json")
        operation_key = str(manifest["operation_key"])
        operation = self.control.connection.execute(
            "SELECT state FROM operations WHERE operation_key = ?",
            (operation_key,),
        ).fetchone()
        if operation is None or str(operation["state"]) in {"promoted", "reconciling", "succeeded"}:
            raise IntegrityError("A promoted operation cannot accept a replacement proposal")
        response_read = read_regular_file(
            self.root,
            response_path,
            max_bytes=int(self.config["limits"]["max_input_bytes"]),
        )
        self._classify_or_quarantine(response_read.data, response_read.relative_path)
        try:
            response = ProposalResponse.model_validate(json.loads(response_read.data))
        except (json.JSONDecodeError, ValueError) as error:
            raise IntegrityError("Imported ProposalResponse is invalid") from error
        staging = self.root / "ops" / "staging" / run_id
        request = ProposalRequest.model_validate(read_json(staging / "proposal_request.json"))
        pack = EvidencePack.model_validate(read_json(staging / "evidence_pack.json"))
        request_sha256 = sha256_hex(canonical_json_bytes(request))
        if (
            response.request_ref.kind != "proposal_request"
            or response.request_ref.id != request.proposal_request_id
            or response.request_ref.object_sha256 != request_sha256
            or response.allowed_evidence_ids != request.allowed_evidence_ids
        ):
            raise IntegrityError("Imported proposal does not bind the prepared request")
        claim, claim_sha256 = self._claim_from_response(response, request, pack)
        self._classify_derived_payloads_or_quarantine(
            raw=response_read.data,
            relative_path=response_read.relative_path,
            payloads=(canonical_json_bytes(response), canonical_json_bytes(claim)),
            blocked_message="Decoded proposal content was classified restricted",
        )
        old = self._load_prepared(run_id)
        if old is None:
            raise IntegrityError("Imported proposal requires its prepared staging bundle")
        candidate = _Prepared(
            result=replace(prepared, segment_id=claim.evidence_ids[0]),
            claim=claim,
            claim_object_sha256=claim_sha256,
            generation=old.generation,
            txn=old.txn,
            event=old.event,
            run_created_at=old.run_created_at,
        )
        write_bytes_atomic(staging / "proposal_response.json", canonical_json_bytes(response))
        rebased = self._rebase_prepared(candidate, read_current_generation(self.root))
        self._validate_prepared(rebased)
        return rebased.result

    def _authorize_fixture(
        self,
        relative_path: str,
        data: bytes,
        *,
        requested: bool,
    ) -> str | None:
        if not requested:
            return None
        fixture_config = self.config.get("fixtures", {})
        configured = str(fixture_config.get("root", "tests/fixtures"))
        fixture_root = PurePosixPath(configured)
        candidate = PurePosixPath(relative_path)
        if fixture_root.is_absolute() or ".." in fixture_root.parts:
            raise IntegrityError("Configured fixture namespace is unsafe")
        if candidate != fixture_root and fixture_root not in candidate.parents:
            raise ApprovalRequired(
                "Autonomous fixture promotion is limited to the configured fixture namespace"
            )
        if not bool(fixture_config.get("require_manifest", True)):
            if str(self.config.get("environment", "development")) != "test":
                raise IntegrityError(
                    "Fixture manifest may be disabled only in an isolated test environment"
                )
            return sha256_hex(
                canonical_json_bytes({"root": fixture_root.as_posix(), "test_mode": True})
            )
        manifest_path = str(fixture_config.get("manifest", "tests/fixtures/manifest.json"))
        manifest_read = read_regular_file(self.root, manifest_path, max_bytes=1024 * 1024)
        try:
            manifest = json.loads(manifest_read.data)
        except json.JSONDecodeError as error:
            raise IntegrityError("Fixture manifest is invalid JSON") from error
        files = manifest.get("files") if isinstance(manifest, dict) else None
        expected = files.get(relative_path) if isinstance(files, dict) else None
        if expected != sha256_hex(data):
            raise ApprovalRequired("Fixture bytes are not registered in the trusted manifest")
        return sha256_hex(canonical_json_bytes(manifest))

    def _quarantine(
        self,
        data: bytes,
        relative_source_path: str,
        decision: SensitivityDecision,
    ) -> None:
        quarantine_id = f"q_{uuid.uuid4().hex}"
        root = self.root / "_raw" / "restricted" / quarantine_id
        from raytsystem.io import ensure_safe_directory

        ensure_safe_directory(root, mode=0o700)
        root.chmod(0o700)
        publish_immutable(root / "raw.bin", data, mode=0o600)
        report = {
            "quarantine_id": quarantine_id,
            "source_path": relative_source_path,
            "byte_length": len(data),
            "sensitivity": decision.sensitivity,
            "disposition": decision.disposition,
            "reason_codes": decision.reason_codes,
            "scanner": {
                "name": decision.scanner_name,
                "version": decision.scanner_version,
            },
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }
        publish_immutable(root / "decision.json", canonical_json_bytes(report), mode=0o600)

    def _classify_or_quarantine(self, data: bytes, relative_path: str) -> SensitivityDecision:
        try:
            decision = self.scanner.scan(data, path=relative_path)
            if not isinstance(decision, SensitivityDecision):
                raise TypeError("Scanner returned an invalid decision")
            allowed = decision.disposition == "allow" and decision.sensitivity in {
                "public",
                "internal",
            }
            quarantined = decision.disposition == "quarantine"
            if not allowed and not quarantined:
                raise ValueError("Scanner returned an unsafe decision")
        except Exception:
            fallback = SensitivityDecision(
                sensitivity="restricted",
                disposition="quarantine",
                reason_codes=("scanner_failure",),
                scanner_name=str(getattr(self.scanner, "name", "unknown")),
                scanner_version=str(getattr(self.scanner, "version", "unknown")),
            )
            self._quarantine(data, relative_path, fallback)
            raise QuarantinedInput(
                "Sensitivity scanner failed closed; input was isolated without normalization"
            ) from None
        if decision.blocks_processing:
            self._quarantine(data, relative_path, decision)
            raise QuarantinedInput(
                "Input was classified restricted and isolated without normalization"
            )
        return decision

    def _classify_derived_or_quarantine(
        self,
        *,
        raw: bytes,
        relative_path: str,
        extraction: Extraction,
    ) -> None:
        self._classify_derived_payloads_or_quarantine(
            raw=raw,
            relative_path=relative_path,
            payloads=tuple(
                value.encode()
                for value in (extraction.document, *(span.excerpt for span in extraction.spans))
            ),
            blocked_message="Extracted content was classified restricted before normalization",
        )

    def _classify_derived_payloads_or_quarantine(
        self,
        *,
        raw: bytes,
        relative_path: str,
        payloads: tuple[bytes, ...],
        blocked_message: str,
    ) -> None:
        try:
            for index, value in enumerate(payloads):
                decision = self.scanner.scan(
                    value,
                    path=f"{relative_path}.derived-{index}.txt",
                )
                if not isinstance(decision, SensitivityDecision):
                    raise TypeError("Scanner returned an invalid derived decision")
                allowed = decision.disposition == "allow" and decision.sensitivity in {
                    "public",
                    "internal",
                }
                if not allowed:
                    if decision.disposition != "quarantine":
                        raise ValueError("Scanner returned an unsafe derived decision")
                    derived_decision = SensitivityDecision(
                        sensitivity="restricted",
                        disposition="quarantine",
                        reason_codes=tuple(f"derived_{code}" for code in decision.reason_codes),
                        scanner_name=decision.scanner_name,
                        scanner_version=decision.scanner_version,
                    )
                    self._quarantine(raw, relative_path, derived_decision)
                    raise QuarantinedInput(blocked_message)
        except QuarantinedInput:
            raise
        except Exception:
            fallback = SensitivityDecision(
                sensitivity="restricted",
                disposition="quarantine",
                reason_codes=("derived_scanner_failure",),
                scanner_name=str(getattr(self.scanner, "name", "unknown")),
                scanner_version=str(getattr(self.scanner, "version", "unknown")),
            )
            self._quarantine(raw, relative_path, fallback)
            raise QuarantinedInput(
                "Derived sensitivity scan failed closed before normalization"
            ) from None

    def _prepare(
        self,
        *,
        run_id: str,
        run_created_at: datetime,
        operation_key: str,
        source_id: str,
        relative_source_path: str,
        content: bytes,
        content_sha256: str,
        adapter_config_sha256: str,
        extractor: Extractor,
        extraction: Extraction,
        fixture: bool,
    ) -> _Prepared:
        raw_digest, raw_absolute_path, _ = publish_content_addressed(
            self.root / "_raw" / "blobs" / "sha256",
            content,
            mode=0o600,
        )
        if raw_digest != content_sha256:
            raise IntegrityError("Raw content hash changed during capture")
        raw_path = raw_absolute_path.relative_to(self.root).as_posix()
        self._load_or_create_source(
            source_id=source_id,
            relative_source_path=relative_source_path,
            extractor=extractor,
            fixture=fixture,
            created_at=run_created_at,
        )
        revision_id = derive_id(
            "srev",
            {"source_id": source_id, "content_sha256": content_sha256},
        )
        try:
            revision = self._load_source_revision(revision_id)
        except IntegrityError as error:
            if str(error) != "Source revision record is missing":
                raise
            revision = SourceRevision.create(
                source_id=source_id,
                content_sha256=content_sha256,
                raw_path=raw_path,
                retrieved_at=run_created_at,
                byte_length=len(content),
                media_type=extractor.media_type,
                sensitivity=Sensitivity.INTERNAL,
            )
            _, _, _ = publish_model(
                self.root / "_raw" / "revisions" / "sha256",
                revision,
            )
        self._rebuild_source_projection()
        self._fault("after_raw_capture")
        self._update_run_manifest(
            run_id,
            state="normalizing",
            source_revision_id=revision.source_revision_id,
            raw_path=raw_path,
        )

        normalization, normalized_path, segments = self._normalize(
            revision=revision,
            content=content,
            adapter_config_sha256=adapter_config_sha256,
            created_at=run_created_at,
            extractor=extractor,
            source_path=relative_source_path,
            extraction=extraction,
        )
        self._fault("after_normalization_publish")
        self._update_run_manifest(
            run_id,
            state="staging",
            normalization_id=normalization.normalization_id,
            normalized_path=normalized_path,
        )

        claim, claim_object_sha256 = self._create_and_validate_proposal(
            run_id=run_id,
            operation_key=operation_key,
            revision=revision,
            normalization=normalization,
            segments=segments,
            created_at=run_created_at,
        )
        parent_generation_id = read_current_generation(self.root)
        parent = LedgerGeneration.model_validate(
            read_json(self.root / "ledger" / "generations" / f"{parent_generation_id}.json")
        )
        if not parent.verify_id():
            raise IntegrityError("Parent generation ID does not match its manifest")
        claim, claim_object_sha256 = self._merge_claim_with_generation(parent, claim)

        txn_id = derive_id(
            "ptxn",
            {
                "operation_key": operation_key,
                "parent_generation_id": parent_generation_id,
                "claim_object_sha256": claim_object_sha256,
            },
        )
        event_id = derive_id("evt", {"txn_id": txn_id})
        records = dict(parent.records)
        records[f"claim:{claim.claim_id}"] = GenerationEntry(
            kind="claim",
            logical_id=claim.claim_id,
            object_sha256=claim_object_sha256,
        )
        generation_seed = LedgerGeneration(
            generation_id="gen_pending",
            parent_generation_id=parent_generation_id,
            records=records,
            schema_registry_sha256=self._schema_registry_sha256(),
            created_at=run_created_at,
            promotion_txn_id=txn_id,
            promotion_event_id=event_id,
        )
        generation_id = derive_id("gen", generation_seed.identity_payload())
        generation = generation_seed.model_copy(update={"generation_id": generation_id})
        event = PromotionEvent(
            event_id=event_id,
            txn_id=txn_id,
            run_id=run_id,
            operation_key=operation_key,
            parent_generation_id=parent_generation_id,
            new_generation_id=generation_id,
            committed_at=run_created_at,
        )
        result = IngestResult(
            status="prepared",
            noop=False,
            run_id=run_id,
            operation_key=operation_key,
            source_id=source_id,
            source_revision_id=revision.source_revision_id,
            raw_path=raw_path,
            normalization_id=normalization.normalization_id,
            normalized_path=normalized_path,
            segment_id=segments[0].segment_id,
            generation_id=generation_id,
        )
        txn = PromotionTxn(
            txn_id=txn_id,
            run_id=run_id,
            operation_key=operation_key,
            parent_generation_id=parent_generation_id,
            next_generation_id=generation_id,
            candidate_manifest_sha256=sha256_hex(canonical_json_bytes(generation)),
            event_id=event_id,
            partition_fencing_token=1,
            global_fencing_token=1,
            output_hashes={f"claim:{claim.claim_id}": claim_object_sha256},
            state=PromotionState.PREPARED,
            created_at=run_created_at,
            updated_at=run_created_at,
            extensions={"raytsystem.result": asdict(result)},
        )
        staging = self.root / "ops" / "staging" / run_id
        self._write_staging_bundle(
            staging,
            claim=claim,
            generation=generation,
            txn=txn,
            event=event,
        )
        self._fault("after_proposal_validation")
        self._update_run_manifest(
            run_id,
            state="prepared",
            generation_id=generation_id,
            event_id=event_id,
            txn_id=txn_id,
            segment_id=segments[0].segment_id,
        )
        return _Prepared(
            result=result,
            claim=claim,
            claim_object_sha256=claim_object_sha256,
            generation=generation,
            txn=txn,
            event=event,
            run_created_at=run_created_at,
        )

    def _load_prepared(self, run_id: str) -> _Prepared | None:
        staging = self.root / "ops" / "staging" / run_id
        marker_path = staging / "bundle.json"
        required = {
            "claim": staging / "claim.json",
            "generation": staging / "generation.json",
            "txn": staging / "promotion_txn.json",
            "event": staging / "event.json",
        }
        if not marker_path.is_file():
            return None
        try:
            marker = read_json(marker_path)
        except (OSError, ValueError) as error:
            raise IntegrityError("Staged transaction bundle marker is invalid") from error
        files = marker.get("files")
        if (
            marker.get("run_id") != run_id
            or not isinstance(files, dict)
            or set(files) != set(required)
        ):
            raise IntegrityError("Staged transaction bundle marker is malformed")
        for name, path in required.items():
            if not path.is_file() or path.is_symlink():
                raise IntegrityError("Staged transaction bundle is incomplete")
            expected_hash = files.get(name)
            if not isinstance(expected_hash, str) or sha256_hex(path.read_bytes()) != expected_hash:
                raise IntegrityError("Staged transaction bundle hash mismatch")
        expected_bundle_id = derive_id(
            "bundle",
            {"run_id": run_id, "files": files},
        )
        if marker.get("bundle_id") != expected_bundle_id:
            raise IntegrityError("Staged transaction bundle identity mismatch")
        manifest = read_json(self.root / "ops" / "runs" / run_id / "manifest.json")
        claim = Claim.model_validate(read_json(required["claim"]))
        generation = LedgerGeneration.model_validate(read_json(required["generation"]))
        txn = PromotionTxn.model_validate(read_json(required["txn"]))
        event = PromotionEvent.model_validate(read_json(required["event"]))
        if txn.run_id != run_id or event.txn_id != txn.txn_id:
            raise IntegrityError("Staged transaction ownership mismatch")
        if generation.generation_id != txn.next_generation_id:
            raise IntegrityError("Staged generation does not match transaction")
        expected_claim_sha256 = txn.output_hashes.get(f"claim:{claim.claim_id}")
        if expected_claim_sha256 is None:
            raise IntegrityError("Staged transaction does not bind its claim")
        result = IngestResult(
            status="prepared",
            noop=False,
            run_id=run_id,
            operation_key=str(manifest["operation_key"]),
            source_id=str(manifest["source_id"]),
            source_revision_id=str(manifest["source_revision_id"]),
            raw_path=str(manifest["raw_path"]),
            normalization_id=str(manifest["normalization_id"]),
            normalized_path=str(manifest["normalized_path"]),
            segment_id=str(manifest.get("segment_id") or claim.evidence_ids[0]),
            generation_id=generation.generation_id,
        )
        created_at = datetime.fromisoformat(str(manifest["created_at"]).replace("Z", "+00:00"))
        return _Prepared(
            result=result,
            claim=claim,
            claim_object_sha256=expected_claim_sha256,
            generation=generation,
            txn=txn,
            event=event,
            run_created_at=created_at,
        )

    def _write_staging_bundle(
        self,
        staging: Path,
        *,
        claim: Claim,
        generation: LedgerGeneration,
        txn: PromotionTxn,
        event: PromotionEvent,
    ) -> None:
        marker_path = staging / "bundle.json"
        if marker_path.is_symlink():
            raise IntegrityError("Staged transaction marker cannot be a symlink")
        marker_path.unlink(missing_ok=True)
        payloads = {
            "claim": canonical_json_bytes(claim),
            "generation": canonical_json_bytes(generation),
            "txn": canonical_json_bytes(txn),
            "event": canonical_json_bytes(event),
        }
        filenames = {
            "claim": "claim.json",
            "generation": "generation.json",
            "txn": "promotion_txn.json",
            "event": "event.json",
        }
        for index, name in enumerate(("claim", "generation", "txn", "event")):
            write_bytes_atomic(staging / filenames[name], payloads[name])
            if index == 0:
                self._fault("after_staging_bundle_first_file")
        hashes = {name: sha256_hex(payload) for name, payload in payloads.items()}
        marker = {
            "schema_version": "1.0.0",
            "run_id": txn.run_id,
            "bundle_id": derive_id(
                "bundle",
                {"run_id": txn.run_id, "files": hashes},
            ),
            "files": hashes,
        }
        write_bytes_atomic(marker_path, canonical_json_bytes(marker))

    def _load_from_wal(self, operation_key: str, run_id: str) -> _Prepared | None:
        row = self.control.promotion_for_operation(operation_key)
        if row is None:
            return None
        if str(row["run_id"]) != run_id:
            raise IntegrityError("Promotion WAL run ownership mismatch")
        try:
            txn = PromotionTxn.model_validate(json.loads(str(row["payload_json"])))
        except (json.JSONDecodeError, ValueError) as error:
            raise IntegrityError("Promotion WAL payload is invalid") from error
        generation_id = validate_generation_id(txn.next_generation_id, allow_genesis=False)
        generation = LedgerGeneration.model_validate(
            read_json(self.root / "ledger" / "generations" / f"{generation_id}.json")
        )
        event_row = self.control.event_outbox_record(txn.event_id)
        if event_row is None:
            raise IntegrityError("Promotion WAL event outbox is missing")
        try:
            event = PromotionEvent.model_validate(json.loads(str(event_row["payload_json"])))
        except (json.JSONDecodeError, ValueError) as error:
            raise IntegrityError("Promotion event WAL payload is invalid") from error
        claim_outputs = [
            (logical_id, object_hash)
            for logical_id, object_hash in txn.output_hashes.items()
            if logical_id.startswith("claim:")
        ]
        if len(claim_outputs) != 1:
            raise IntegrityError("M1 promotion WAL must reference exactly one claim")
        _, claim_sha256 = claim_outputs[0]
        claim = Claim.model_validate(
            read_json(
                self.root
                / "ledger"
                / "objects"
                / "sha256"
                / claim_sha256[:2]
                / f"{claim_sha256}.json"
            )
        )
        snapshot = txn.extensions.get("raytsystem.result") or txn.extensions.get("agentos.result")
        if isinstance(snapshot, dict):
            result = IngestResult.from_dict(snapshot, noop=False)
            if (
                result.run_id != run_id
                or result.operation_key != operation_key
                or result.generation_id != generation_id
            ):
                raise IntegrityError("Promotion WAL result snapshot disagrees")
            created_at = txn.created_at
        else:
            manifest = read_json(self.root / "ops" / "runs" / run_id / "manifest.json")
            result = IngestResult(
                status="prepared",
                noop=False,
                run_id=run_id,
                operation_key=operation_key,
                source_id=str(manifest["source_id"]),
                source_revision_id=str(manifest["source_revision_id"]),
                raw_path=str(manifest["raw_path"]),
                normalization_id=str(manifest["normalization_id"]),
                normalized_path=str(manifest["normalized_path"]),
                segment_id=str(manifest["segment_id"]),
                generation_id=generation_id,
            )
            created_at = datetime.fromisoformat(str(manifest["created_at"]).replace("Z", "+00:00"))
        return _Prepared(
            result=result,
            claim=claim,
            claim_object_sha256=claim_sha256,
            generation=generation,
            txn=txn,
            event=event,
            run_created_at=created_at,
        )

    def _validate_prepared(self, prepared: _Prepared) -> None:
        result = prepared.result
        claim = prepared.claim
        generation = prepared.generation
        txn = prepared.txn
        event = prepared.event

        result_snapshot = txn.extensions.get("raytsystem.result") or txn.extensions.get(
            "agentos.result"
        )
        if isinstance(result_snapshot, dict) and result_snapshot != asdict(result):
            raise IntegrityError("Staged transaction result snapshot mismatch")

        identifier_patterns = {
            "run": r"run_[0-9a-f]{32}",
            "operation": r"op_[0-9a-f]{64}",
            "source": r"src_[0-9a-f]{64}",
            "revision": r"srev_[0-9a-f]{64}",
            "normalization": r"norm_[0-9a-f]{64}",
            "segment": r"seg_[0-9a-f]{64}",
            "claim": r"clm_[0-9a-f]{64}",
            "transaction": r"ptxn_[0-9a-f]{64}",
            "event": r"evt_[0-9a-f]{64}",
        }
        identifiers = {
            "run": result.run_id,
            "operation": result.operation_key,
            "source": result.source_id,
            "revision": result.source_revision_id,
            "normalization": result.normalization_id,
            "segment": result.segment_id,
            "claim": claim.claim_id,
            "transaction": txn.txn_id,
            "event": event.event_id,
        }
        for kind, value in identifiers.items():
            if re.fullmatch(identifier_patterns[kind], value) is None:
                raise IntegrityError(f"Malformed {kind} identifier")
        validate_generation_id(generation.generation_id, allow_genesis=False)
        validate_generation_id(txn.parent_generation_id)

        claim_bytes = canonical_json_bytes(claim)
        self._classify_derived_payloads_or_quarantine(
            raw=claim_bytes,
            relative_path=f"ops/staging/{result.run_id}/claim.json",
            payloads=(claim_bytes,),
            blocked_message="Canonical claim candidate was classified restricted before promotion",
        )
        claim_sha256 = sha256_hex(claim_bytes)
        if claim_sha256 != prepared.claim_object_sha256:
            raise IntegrityError("Staged claim hash mismatch")
        expected_claim_id = derive_id(
            "clm",
            {"statement": claim.statement, "language": claim.language, "scope": {}},
        )
        if claim.claim_id != expected_claim_id:
            raise IntegrityError("Staged claim logical ID mismatch")
        claim_key = f"claim:{claim.claim_id}"
        entry = generation.records.get(claim_key)
        if (
            entry is None
            or entry.kind != "claim"
            or entry.logical_id != claim.claim_id
            or entry.object_sha256 != claim_sha256
            or txn.output_hashes != {claim_key: claim_sha256}
        ):
            raise IntegrityError("Staged claim is not closed by generation and transaction")

        if not generation.verify_id() or generation.generation_id != txn.next_generation_id:
            raise IntegrityError("Staged generation identity mismatch")
        if generation.schema_registry_sha256 != self._schema_registry_sha256():
            raise IntegrityError("Candidate generation uses a stale schema registry")
        parent = LedgerGeneration.model_validate(
            read_json(self.root / "ledger" / "generations" / f"{txn.parent_generation_id}.json")
        )
        if not parent.verify_id():
            raise IntegrityError("Candidate parent generation is invalid")
        expected_records = dict(parent.records)
        expected_records[claim_key] = entry
        if generation.records != expected_records:
            raise IntegrityError("Candidate generation does not exactly extend its parent")
        self._validate_generation_objects(
            generation,
            staged_claim=claim,
            reextract_normalization_id=result.normalization_id,
        )
        generation_sha256 = sha256_hex(canonical_json_bytes(generation))
        if txn.candidate_manifest_sha256 != generation_sha256:
            raise IntegrityError("Staged generation manifest hash mismatch")
        expected_txn_id = derive_id(
            "ptxn",
            {
                "operation_key": result.operation_key,
                "parent_generation_id": txn.parent_generation_id,
                "claim_object_sha256": claim_sha256,
            },
        )
        if txn.txn_id != expected_txn_id:
            raise IntegrityError("Staged transaction identity mismatch")
        expected_event_id = derive_id("evt", {"txn_id": txn.txn_id})
        if (
            txn.run_id != result.run_id
            or txn.operation_key != result.operation_key
            or txn.event_id != expected_event_id
            or generation.parent_generation_id != txn.parent_generation_id
            or generation.promotion_txn_id != txn.txn_id
            or generation.promotion_event_id != txn.event_id
        ):
            raise IntegrityError("Staged transaction cross-reference mismatch")
        if (
            event.event_id != expected_event_id
            or event.txn_id != txn.txn_id
            or event.run_id != result.run_id
            or event.operation_key != result.operation_key
            or event.parent_generation_id != txn.parent_generation_id
            or event.new_generation_id != txn.next_generation_id
        ):
            raise IntegrityError("Staged event cross-reference mismatch")

        revision = self._load_source_revision(result.source_revision_id)
        if revision.source_id != result.source_id or revision.raw_path != result.raw_path:
            raise IntegrityError("Source revision ownership mismatch")
        raw = read_regular_file(
            self.root,
            revision.raw_path,
            max_bytes=int(self.config["limits"]["max_input_bytes"]),
        ).data
        if sha256_hex(raw) != revision.content_sha256:
            raise IntegrityError("Raw evidence hash mismatch")
        raw_name = PurePosixPath(revision.raw_path).name
        if raw_name != revision.content_sha256:
            raise IntegrityError("Raw evidence path is not content-addressed")

        expected_normalized = PurePosixPath(
            "normalized",
            result.source_revision_id,
            result.normalization_id,
        )
        if PurePosixPath(result.normalized_path) != expected_normalized:
            raise IntegrityError("Normalization path does not match its identities")
        normalization_bytes = read_regular_file(
            self.root,
            (expected_normalized / "normalization.json").as_posix(),
            max_bytes=1024 * 1024,
        ).data
        try:
            normalization = Normalization.model_validate(json.loads(normalization_bytes))
        except (json.JSONDecodeError, ValueError) as error:
            raise IntegrityError("Normalization manifest is invalid") from error
        if normalization_bytes != canonical_json_bytes(normalization):
            raise IntegrityError("Normalization manifest is non-canonical or changed")
        expected_norm_id = Normalization.create(
            source_revision_id=revision.source_revision_id,
            adapter=normalization.extractor_ref.name,
            parser_version=normalization.extractor_ref.version,
            config_sha256=normalization.config_sha256,
            document_sha256=normalization.document_sha256,
            created_at=normalization.created_at,
        ).normalization_id
        if (
            normalization.normalization_id != result.normalization_id
            or normalization.normalization_id != expected_norm_id
            or normalization.source_revision_id != revision.source_revision_id
        ):
            raise IntegrityError("Normalization identity mismatch")
        if normalization.document_path is None or normalization.segments_path is None:
            raise IntegrityError("Normalization artifacts are missing")
        expected_document_path = (expected_normalized / "document.txt").as_posix()
        expected_segments_path = (expected_normalized / "segments.jsonl").as_posix()
        excerpt_binding = normalization.extensions.get(
            "raytsystem.excerpts"
        ) or normalization.extensions.get("agentos.excerpts")
        expected_excerpt_path = (expected_normalized / "excerpts.jsonl").as_posix()
        if (
            normalization.document_path != expected_document_path
            or normalization.segments_path != expected_segments_path
            or not isinstance(excerpt_binding, dict)
            or excerpt_binding.get("path") != expected_excerpt_path
        ):
            raise IntegrityError("Normalization artifacts escape their immutable snapshot")
        document = read_regular_file(
            self.root,
            normalization.document_path,
            max_bytes=int(self.config["limits"]["max_input_bytes"]),
        ).data
        segments_bytes = read_regular_file(
            self.root,
            normalization.segments_path,
            max_bytes=int(self.config["limits"]["max_input_bytes"]),
        ).data
        if sha256_hex(document) != normalization.document_sha256:
            raise IntegrityError("Normalized document hash mismatch")
        if sha256_hex(segments_bytes) != normalization.segments_sha256:
            raise IntegrityError("Normalized segments hash mismatch")

        segments: dict[str, Segment] = {}
        ordered_segments: list[Segment] = []
        for line in segments_bytes.splitlines():
            try:
                segment = Segment.model_validate(json.loads(line))
            except (json.JSONDecodeError, ValueError) as error:
                raise IntegrityError("Normalized segment is invalid") from error
            recreated = Segment.create(
                source_revision_id=segment.source_revision_id,
                normalization_id=segment.normalization_id,
                ordinal=segment.ordinal,
                locator=segment.locator,
                excerpt_sha256=segment.excerpt_sha256,
                language=segment.language,
                modality=segment.modality,
            )
            if segment.segment_id != recreated.segment_id:
                raise IntegrityError("Normalized segment identity mismatch")
            if (
                segment.source_revision_id != revision.source_revision_id
                or segment.normalization_id != normalization.normalization_id
                or segment.ordinal != len(ordered_segments)
            ):
                raise IntegrityError("Normalized segment ownership or order mismatch")
            segments[segment.segment_id] = segment
            ordered_segments.append(segment)
        if len(segments) != normalization.segment_count:
            raise IntegrityError("Normalization segment count mismatch")
        if result.segment_id not in segments:
            raise IntegrityError("Result references unresolved segment")
        if result.segment_id not in claim.evidence_ids:
            raise IntegrityError("Claim lost the current proposal evidence during merge")
        excerpts = self._load_excerpts(normalization)
        if set(excerpts) != set(segments):
            raise IntegrityError("Normalized excerpts do not close the segment set")
        text_lines = document.decode("utf-8").splitlines()
        for segment_id, segment in segments.items():
            excerpt = excerpts[segment_id]
            if sha256_hex(excerpt.encode()) != segment.excerpt_sha256:
                raise IntegrityError("Evidence excerpt hash mismatch")
            locator = segment.locator
            if isinstance(locator, TextLocator):
                if locator.line_start is None or locator.line_start > len(text_lines):
                    raise IntegrityError("Evidence line is outside normalized document")
                if text_lines[locator.line_start - 1] != excerpt:
                    raise IntegrityError("Text locator does not resolve to its excerpt")

        source = self._load_source(result.source_id)
        if source.origin.kind != "workspace_file" or source.origin.locator is None:
            raise IntegrityError("Logical source has no recoverable workspace locator")
        source_path = source.origin.locator
        run_manifest = read_json(self.root / "ops" / "runs" / result.run_id / "manifest.json")
        if run_manifest.get("input_path") != source_path:
            raise IntegrityError("Run input path disagrees with immutable source provenance")
        try:
            extractor = self.extractors.select(source_path)
            if (
                isinstance(extractor, PdfExtractor)
                and extractor.containment_profile() != "macos_restricted_v1"
            ):
                self._authorize_fixture(source_path, raw, requested=True)
            extraction = extractor.extract(raw, source_path=source_path)
        except ExtractionError as error:
            raise IntegrityError("Raw evidence cannot be deterministically re-extracted") from error
        self._classify_derived_or_quarantine(
            raw=raw,
            relative_path=source_path,
            extraction=extraction,
        )
        if (
            extractor.name != normalization.extractor_ref.name
            or extractor.version != normalization.extractor_ref.version
            or extraction.document.encode() != document
            or len(extraction.spans) != len(ordered_segments)
        ):
            raise IntegrityError("Normalization does not match deterministic raw extraction")
        expected_config_sha256 = self._adapter_config_sha256(extractor, extraction)
        if normalization.config_sha256 != expected_config_sha256:
            raise IntegrityError("Normalization adapter fingerprint mismatch")
        for ordinal, (span, segment) in enumerate(
            zip(extraction.spans, ordered_segments, strict=True)
        ):
            expected_segment = Segment.create(
                source_revision_id=revision.source_revision_id,
                normalization_id=normalization.normalization_id,
                ordinal=ordinal,
                locator=span.locator,
                excerpt_sha256=sha256_hex(span.excerpt.encode()),
                modality=span.modality,
            )
            if expected_segment != segment or excerpts.get(segment.segment_id) != span.excerpt:
                raise IntegrityError("Typed locator/excerpt does not resolve to raw extraction")

    def _validate_generation_objects(
        self,
        generation: LedgerGeneration,
        *,
        staged_claim: Claim,
        reextract_normalization_id: str | None,
    ) -> None:
        for key, entry in generation.records.items():
            if entry.tombstone:
                continue
            if entry.kind != "claim" or key != f"claim:{entry.logical_id}":
                raise IntegrityError("M1 generation contains an unsupported record entry")
            if re.fullmatch(r"clm_[0-9a-f]{64}", entry.logical_id) is None:
                raise IntegrityError("Generation contains a malformed claim ID")
            if entry.logical_id == staged_claim.claim_id:
                data = canonical_json_bytes(staged_claim)
            else:
                relative = (
                    PurePosixPath("ledger")
                    / "objects"
                    / "sha256"
                    / entry.object_sha256[:2]
                    / f"{entry.object_sha256}.json"
                )
                data = read_regular_file(
                    self.root,
                    relative.as_posix(),
                    max_bytes=4 * 1024 * 1024,
                ).data
            if sha256_hex(data) != entry.object_sha256:
                raise IntegrityError("Generation object hash mismatch")
            try:
                active_claim = Claim.model_validate(json.loads(data))
            except (json.JSONDecodeError, ValueError) as error:
                raise IntegrityError("Generation claim object is invalid") from error
            expected_id = derive_id(
                "clm",
                {
                    "statement": active_claim.statement,
                    "language": active_claim.language,
                    "scope": {},
                },
            )
            if active_claim.claim_id != entry.logical_id or active_claim.claim_id != expected_id:
                raise IntegrityError("Generation claim logical ID mismatch")
            self._validate_claim_evidence(
                active_claim,
                reextract_normalization_id=reextract_normalization_id,
            )

    def _validate_claim_evidence(
        self,
        claim: Claim,
        *,
        reextract_normalization_id: str | None,
    ) -> None:
        for evidence_id in claim.evidence_ids:
            matches: list[Path] = []
            for segments_path in self.root.glob("normalized/*/*/segments.jsonl"):
                relative = segments_path.relative_to(self.root).as_posix()
                try:
                    segment_bytes = read_regular_file(
                        self.root,
                        relative,
                        max_bytes=int(self.config["limits"]["max_input_bytes"]),
                    ).data
                except PathPolicyError:
                    continue
                if evidence_id.encode() not in segment_bytes:
                    continue
                try:
                    segments = [
                        Segment.model_validate(json.loads(line))
                        for line in segment_bytes.splitlines()
                    ]
                except (json.JSONDecodeError, ValueError):
                    continue
                if any(segment.segment_id == evidence_id for segment in segments):
                    matches.append(segments_path.parent)
            if len(matches) != 1:
                raise IntegrityError(
                    "Active claim evidence does not resolve to exactly one immutable span"
                )
            self._validate_evidence_snapshot(
                matches[0],
                evidence_id,
                reextract=matches[0].name == reextract_normalization_id,
            )

    def _validate_evidence_snapshot(
        self,
        snapshot: Path,
        evidence_id: str,
        *,
        reextract: bool,
    ) -> None:
        try:
            relative_snapshot = snapshot.relative_to(self.root)
        except ValueError as error:
            raise IntegrityError("Evidence snapshot escapes the workspace") from error
        if len(relative_snapshot.parts) != 3 or relative_snapshot.parts[0] != "normalized":
            raise IntegrityError("Evidence snapshot path is malformed")
        source_revision_id = relative_snapshot.parts[1]
        normalization_id = relative_snapshot.parts[2]
        normalization_bytes = read_regular_file(
            self.root,
            (relative_snapshot / "normalization.json").as_posix(),
            max_bytes=1024 * 1024,
        ).data
        try:
            normalization = Normalization.model_validate(json.loads(normalization_bytes))
        except (json.JSONDecodeError, ValueError) as error:
            raise IntegrityError("Inherited normalization manifest is invalid") from error
        recreated_normalization = Normalization.create(
            source_revision_id=normalization.source_revision_id,
            adapter=normalization.extractor_ref.name,
            parser_version=normalization.extractor_ref.version,
            config_sha256=normalization.config_sha256,
            document_sha256=normalization.document_sha256,
            created_at=normalization.created_at,
        )
        expected_document = (relative_snapshot / "document.txt").as_posix()
        expected_segments = (relative_snapshot / "segments.jsonl").as_posix()
        expected_excerpts = (relative_snapshot / "excerpts.jsonl").as_posix()
        excerpt_binding = normalization.extensions.get(
            "raytsystem.excerpts"
        ) or normalization.extensions.get("agentos.excerpts")
        runtime_binding = normalization.extensions.get(
            "raytsystem.extractor_runtime"
        ) or normalization.extensions.get("agentos.extractor_runtime", {})
        if (
            normalization_bytes != canonical_json_bytes(normalization)
            or normalization.source_revision_id != source_revision_id
            or normalization.normalization_id != normalization_id
            or recreated_normalization.normalization_id != normalization_id
            or normalization.document_path != expected_document
            or normalization.segments_path != expected_segments
            or not isinstance(excerpt_binding, dict)
            or excerpt_binding.get("path") != expected_excerpts
            or not isinstance(runtime_binding, dict)
        ):
            raise IntegrityError("Inherited normalization identity or paths changed")
        try:
            document = read_regular_file(
                self.root,
                expected_document,
                max_bytes=int(self.config["limits"]["max_input_bytes"]),
            ).data
            segment_bytes = read_regular_file(
                self.root,
                expected_segments,
                max_bytes=int(self.config["limits"]["max_input_bytes"]),
            ).data
            excerpt_bytes = read_regular_file(
                self.root,
                expected_excerpts,
                max_bytes=int(self.config["limits"]["max_input_bytes"]),
            ).data
        except PathPolicyError as error:
            raise IntegrityError("Inherited evidence artifact is missing") from error
        if (
            sha256_hex(document) != normalization.document_sha256
            or sha256_hex(segment_bytes) != normalization.segments_sha256
            or excerpt_binding.get("sha256") != sha256_hex(excerpt_bytes)
        ):
            raise IntegrityError("Inherited normalization artifact hash changed")
        expected_config_material: dict[str, Any] = {
            "adapter": normalization.extractor_ref.name,
            "version": normalization.extractor_ref.version,
            "segmenter": "extractor_native_spans_v1",
            "extraction_sha256": normalization.document_sha256,
        }
        if runtime_binding:
            expected_config_material["runtime"] = runtime_binding
        if (
            sha256_hex(canonical_json_bytes(expected_config_material))
            != normalization.config_sha256
        ):
            raise IntegrityError("Inherited extractor runtime fingerprint changed")
        try:
            segments = [
                Segment.model_validate(json.loads(line)) for line in segment_bytes.splitlines()
            ]
            excerpt_records = [json.loads(line) for line in excerpt_bytes.splitlines()]
        except (json.JSONDecodeError, ValueError) as error:
            raise IntegrityError("Inherited evidence records are invalid") from error
        if len(segments) != normalization.segment_count:
            raise IntegrityError("Inherited normalization segment count changed")
        excerpts: dict[str, str] = {}
        for record in excerpt_records:
            if not isinstance(record, dict) or set(record) != {"segment_id", "excerpt"}:
                raise IntegrityError("Inherited excerpt record is malformed")
            segment_id = record["segment_id"]
            excerpt = record["excerpt"]
            if (
                not isinstance(segment_id, str)
                or not isinstance(excerpt, str)
                or not excerpt
                or segment_id in excerpts
            ):
                raise IntegrityError("Inherited excerpt record values are invalid")
            excerpts[segment_id] = excerpt
        if set(excerpts) != {segment.segment_id for segment in segments}:
            raise IntegrityError("Inherited excerpt set does not close its segments")
        target: Segment | None = None
        text_lines = document.decode("utf-8").splitlines()
        for ordinal, segment in enumerate(segments):
            recreated_segment = Segment.create(
                source_revision_id=segment.source_revision_id,
                normalization_id=segment.normalization_id,
                ordinal=segment.ordinal,
                locator=segment.locator,
                excerpt_sha256=segment.excerpt_sha256,
                language=segment.language,
                modality=segment.modality,
            )
            excerpt = excerpts[segment.segment_id]
            if (
                segment.segment_id != recreated_segment.segment_id
                or segment.source_revision_id != source_revision_id
                or segment.normalization_id != normalization_id
                or segment.ordinal != ordinal
                or sha256_hex(excerpt.encode()) != segment.excerpt_sha256
            ):
                raise IntegrityError("Inherited evidence segment identity changed")
            if isinstance(segment.locator, TextLocator):
                line_start = segment.locator.line_start
                if (
                    line_start is None
                    or line_start > len(text_lines)
                    or text_lines[line_start - 1] != excerpt
                ):
                    raise IntegrityError("Inherited text locator no longer resolves")
            if segment.segment_id == evidence_id:
                target = segment
        if target is None:
            raise IntegrityError("Inherited evidence target disappeared")
        revision = self._load_source_revision(source_revision_id)
        raw = read_regular_file(
            self.root,
            revision.raw_path,
            max_bytes=int(self.config["limits"]["max_input_bytes"]),
        ).data
        if (
            revision.source_revision_id != source_revision_id
            or sha256_hex(raw) != revision.content_sha256
            or PurePosixPath(revision.raw_path).name != revision.content_sha256
        ):
            raise IntegrityError("Raw evidence hash mismatch for active claim")
        source: Source | None = None
        extractor: Extractor | None = None
        if normalization.extractor_ref.name == PdfExtractor.name:
            source = self._load_source(revision.source_id)
            if source.origin.locator is None:
                raise IntegrityError("Inherited evidence source locator is missing")
            try:
                extractor = self.extractors.select(source.origin.locator)
            except ExtractionError as error:
                raise IntegrityError("Inherited PDF extractor is unavailable") from error
            if not isinstance(extractor, PdfExtractor) or (
                runtime_binding.get("containment_profile") == "macos_restricted_v1"
                and extractor.containment_profile() != "macos_restricted_v1"
            ):
                raise IntegrityError("Inherited PDF requires its original OS containment profile")
        if not reextract:
            return
        if source is None:
            source = self._load_source(revision.source_id)
        if source.origin.locator is None:
            raise IntegrityError("Inherited evidence source locator is missing")
        try:
            if extractor is None:
                extractor = self.extractors.select(source.origin.locator)
            if (
                isinstance(extractor, PdfExtractor)
                and extractor.containment_profile() != "macos_restricted_v1"
                and runtime_binding.get("containment_profile") != "fixture_python_guard_v1"
            ):
                raise IntegrityError("Inherited PDF requires its original OS containment profile")
            extraction = extractor.extract(raw, source_path=source.origin.locator)
        except ExtractionError as error:
            raise IntegrityError("Inherited raw evidence cannot be re-extracted") from error
        if (
            extractor.name != normalization.extractor_ref.name
            or extractor.version != normalization.extractor_ref.version
            or extraction.document.encode() != document
            or len(extraction.spans) != len(segments)
        ):
            raise IntegrityError("Inherited normalization differs from raw extraction")
        for ordinal, (span, segment) in enumerate(zip(extraction.spans, segments, strict=True)):
            expected_segment = Segment.create(
                source_revision_id=revision.source_revision_id,
                normalization_id=normalization.normalization_id,
                ordinal=ordinal,
                locator=span.locator,
                excerpt_sha256=sha256_hex(span.excerpt.encode()),
                modality=span.modality,
            )
            if expected_segment != segment or excerpts.get(segment.segment_id) != span.excerpt:
                raise IntegrityError("Inherited typed span differs from raw extraction")

    def _load_source(self, source_id: str) -> Source:
        root = self.root / "_raw" / "sources" / "sha256"
        match: Source | None = None
        for path in root.glob("*/*.json"):
            data = path.read_bytes()
            if path.stem != sha256_hex(data):
                raise IntegrityError("Source object filename hash mismatch")
            try:
                source = Source.model_validate(json.loads(data))
            except (json.JSONDecodeError, ValueError) as error:
                raise IntegrityError("Source object is invalid") from error
            if data != canonical_json_bytes(source):
                raise IntegrityError("Source object is non-canonical or changed")
            if source.source_id != source_id:
                continue
            if match is not None:
                raise IntegrityError("Logical source has multiple immutable definitions")
            locator = source.origin.locator
            if (
                source.identity_scheme != "workspace_relative_path_v1"
                or source.origin.kind != "workspace_file"
                or locator is None
                or source.identity_key_sha256 != sha256_hex(locator.encode())
                or source.source_id
                != derive_id(
                    "src",
                    {
                        "identity_scheme": "workspace_relative_path_v1",
                        "identity_key": locator,
                    },
                )
            ):
                raise IntegrityError("Source identity does not match immutable origin")
            match = source
        if match is None:
            raise IntegrityError("Source record is missing")
        return match

    def _load_source_revision(self, source_revision_id: str) -> SourceRevision:
        root = self.root / "_raw" / "revisions" / "sha256"
        match: SourceRevision | None = None
        for path in root.glob("*/*.json"):
            data = path.read_bytes()
            if path.stem != sha256_hex(data):
                raise IntegrityError("Source revision object filename hash mismatch")
            try:
                revision = SourceRevision.model_validate(json.loads(data))
            except (json.JSONDecodeError, ValueError) as error:
                raise IntegrityError("Source revision object is invalid") from error
            if data != canonical_json_bytes(revision):
                raise IntegrityError("Source revision object is non-canonical or changed")
            expected = SourceRevision.create(
                source_id=revision.source_id,
                content_sha256=revision.content_sha256,
                raw_path=revision.raw_path,
                retrieved_at=revision.captured_at,
                byte_length=revision.byte_length,
                media_type=revision.media_type,
                sensitivity=revision.sensitivity,
            )
            if revision.source_revision_id != expected.source_revision_id:
                raise IntegrityError("Source revision logical ID mismatch")
            if revision.source_revision_id == source_revision_id:
                if match is not None:
                    raise IntegrityError(
                        "Logical source revision has multiple immutable definitions"
                    )
                match = revision
        if match is None:
            raise IntegrityError("Source revision record is missing")
        return match

    def _load_or_create_source(
        self,
        *,
        source_id: str,
        relative_source_path: str,
        extractor: Extractor,
        fixture: bool,
        created_at: datetime,
    ) -> Source:
        root = self.root / "_raw" / "sources" / "sha256"
        matches: list[Source] = []
        for path in root.glob("*/*.json"):
            data = path.read_bytes()
            if path.stem != sha256_hex(data):
                raise IntegrityError("Source object filename hash mismatch")
            try:
                source = Source.model_validate(json.loads(data))
            except (json.JSONDecodeError, ValueError) as error:
                raise IntegrityError("Source object is invalid") from error
            if data != canonical_json_bytes(source):
                raise IntegrityError("Source object is non-canonical or changed")
            if source.source_id == source_id:
                matches.append(source)
        if len(matches) > 1:
            raise IntegrityError("Logical source has multiple immutable definitions")
        if matches:
            return matches[0]
        identity_hash = sha256_hex(relative_source_path.encode())
        source = Source(
            source_id=source_id,
            identity_scheme="workspace_relative_path_v1",
            identity_key_sha256=identity_hash,
            origin=Origin(kind="workspace_file", locator=relative_source_path),
            source_type=extractor.name,
            display_name=PurePosixPath(relative_source_path).name,
            trust_class=TrustClass.USER,
            rights="synthetic_fixture" if fixture else "unknown",
            sensitivity=Sensitivity.INTERNAL,
            created_at=created_at,
        )
        _, _, _ = publish_model(root, source)
        return source

    def _normalize(
        self,
        *,
        revision: SourceRevision,
        content: bytes,
        adapter_config_sha256: str,
        created_at: datetime,
        extractor: Extractor,
        source_path: str,
        extraction: Extraction,
    ) -> tuple[Normalization, str, list[Segment]]:
        del content, source_path
        document = extraction.document.encode()
        preliminary = Normalization.create(
            source_revision_id=revision.source_revision_id,
            adapter=extractor.name,
            parser_version=extractor.version,
            config_sha256=adapter_config_sha256,
            document_sha256=sha256_hex(document),
            created_at=created_at,
        )
        relative = Path("normalized") / revision.source_revision_id / preliminary.normalization_id
        absolute = self.root / relative
        existing_manifest = absolute / "normalization.json"
        if existing_manifest.is_file():
            normalization = Normalization.model_validate(read_json(existing_manifest))
            if (
                normalization.normalization_id != preliminary.normalization_id
                or normalization.source_revision_id != revision.source_revision_id
                or normalization.extractor_ref.name != extractor.name
                or normalization.extractor_ref.version != extractor.version
                or normalization.config_sha256 != adapter_config_sha256
                or normalization.document_sha256 != sha256_hex(document)
                or normalization.segments_path is None
            ):
                raise IntegrityError("Existing normalization snapshot does not match its identity")
            segment_data = read_regular_file(
                self.root,
                normalization.segments_path,
                max_bytes=int(self.config["limits"]["max_input_bytes"]),
            ).data
            if sha256_hex(segment_data) != normalization.segments_sha256:
                raise IntegrityError("Existing normalization segment hash mismatch")
            existing_segments = [
                Segment.model_validate(json.loads(line)) for line in segment_data.splitlines()
            ]
            self._load_excerpts(normalization)
            return normalization, relative.as_posix(), existing_segments
        segments: list[Segment] = []
        excerpts: list[dict[str, str]] = []
        for ordinal, span in enumerate(extraction.spans):
            segment = Segment.create(
                source_revision_id=revision.source_revision_id,
                normalization_id=preliminary.normalization_id,
                ordinal=ordinal,
                locator=span.locator,
                excerpt_sha256=sha256_hex(span.excerpt.encode()),
                modality=span.modality,
            )
            segments.append(segment)
            excerpts.append({"segment_id": segment.segment_id, "excerpt": span.excerpt})
        if not segments:
            raise UnsupportedInput("Cannot extract evidence from an empty text source")
        segments_bytes = b"\n".join(canonical_json_bytes(segment) for segment in segments) + b"\n"
        excerpts_bytes = b"\n".join(canonical_json_bytes(item) for item in excerpts) + b"\n"
        excerpt_path = (relative / "excerpts.jsonl").as_posix()
        normalization = preliminary.model_copy(
            update={
                "segments_sha256": sha256_hex(segments_bytes),
                "document_path": (relative / "document.txt").as_posix(),
                "segments_path": (relative / "segments.jsonl").as_posix(),
                "segment_count": len(segments),
                "extensions": {
                    "raytsystem.excerpts": {
                        "path": excerpt_path,
                        "sha256": sha256_hex(excerpts_bytes),
                    },
                    "raytsystem.extractor_runtime": (
                        extractor.operation_config() if isinstance(extractor, PdfExtractor) else {}
                    ),
                },
            }
        )
        publish_immutable(absolute / "document.txt", document)
        publish_immutable(absolute / "segments.jsonl", segments_bytes)
        publish_immutable(absolute / "excerpts.jsonl", excerpts_bytes)
        publish_immutable(absolute / "normalization.json", canonical_json_bytes(normalization))
        return normalization, relative.as_posix(), segments

    def _create_and_validate_proposal(
        self,
        *,
        run_id: str,
        operation_key: str,
        revision: SourceRevision,
        normalization: Normalization,
        segments: list[Segment],
        created_at: datetime,
    ) -> tuple[Claim, str]:
        evidence_items: list[EvidenceItem] = []
        excerpts = self._load_excerpts(normalization)
        for segment in segments:
            excerpt = excerpts.get(segment.segment_id)
            if excerpt is None:
                raise IntegrityError("Normalized segment excerpt is missing")
            evidence_items.append(
                EvidenceItem(
                    source_revision_id=revision.source_revision_id,
                    normalization_id=normalization.normalization_id,
                    segment_id=segment.segment_id,
                    locator=segment.locator,
                    excerpt=excerpt,
                    excerpt_sha256=segment.excerpt_sha256,
                    trust_class=TrustClass.USER,
                    captured_at=revision.captured_at,
                )
            )
        pack_material = {
            "run_id": run_id,
            "purpose": "extract_knowledge",
            "items": evidence_items,
        }
        pack_sha256 = sha256_hex(canonical_json_bytes(pack_material))
        pack = EvidencePack(
            evidence_pack_id=derive_id("pack", pack_material),
            run_id=run_id,
            purpose="extract_knowledge",
            items=tuple(evidence_items),
            classification=Sensitivity.INTERNAL,
            pack_sha256=pack_sha256,
            created_at=created_at,
        )
        pack_object_sha = sha256_hex(canonical_json_bytes(pack))
        component = ComponentRef(
            name="raytsystem_fake_proposal",
            version="1.0.0",
            config_sha256=sha256_hex(canonical_json_bytes({"mode": "fixture"})),
        )
        request_material = {
            "run_id": run_id,
            "operation_key": operation_key,
            "pack_sha256": pack_object_sha,
        }
        request = ProposalRequest(
            proposal_request_id=derive_id("preq", request_material),
            run_id=run_id,
            operation_key=operation_key,
            purpose=ProposalPurpose.EXTRACT_KNOWLEDGE,
            evidence_pack_ref=RecordRef(
                kind="evidence_pack",
                id=pack.evidence_pack_id,
                object_sha256=pack_object_sha,
            ),
            allowed_evidence_ids=tuple(item.segment_id for item in evidence_items),
            target_schema_refs=(),
            prompt_or_skill_ref=component,
            policy_constraints=("proposal_only", "evidence_subset"),
            created_at=created_at,
        )
        first = evidence_items[0]
        item = ProposalItem(
            proposal_item_id=derive_id(
                "pitem",
                {"request_id": request.proposal_request_id, "segment_id": first.segment_id},
            ),
            kind="claim",
            payload={"statement": first.excerpt, "language": "und"},
            evidence_ids=(first.segment_id,),
        )
        request_sha = sha256_hex(canonical_json_bytes(request))
        response = ProposalResponse(
            proposal_response_id=derive_id(
                "pres",
                {"request_sha256": request_sha, "items": [item]},
            ),
            request_ref=RecordRef(
                kind="proposal_request",
                id=request.proposal_request_id,
                object_sha256=request_sha,
            ),
            producer=ProducerRef(kind=ProducerKind.KERNEL, component=component),
            allowed_evidence_ids=request.allowed_evidence_ids,
            proposed_items=(item,),
            created_at=revision.captured_at,
        )
        staging = self.root / "ops" / "staging" / run_id
        write_bytes_atomic(staging / "evidence_pack.json", canonical_json_bytes(pack))
        write_bytes_atomic(staging / "proposal_request.json", canonical_json_bytes(request))
        write_bytes_atomic(staging / "proposal_response.json", canonical_json_bytes(response))

        return self._claim_from_response(response, request, pack)

    def _claim_from_response(
        self,
        response: ProposalResponse,
        request: ProposalRequest,
        pack: EvidencePack,
    ) -> tuple[Claim, str]:
        expected_response_id = derive_id(
            "pres",
            {
                "request_sha256": sha256_hex(canonical_json_bytes(request)),
                "items": list(response.proposed_items),
            },
        )
        if response.proposal_response_id != expected_response_id:
            raise IntegrityError("Proposal response identity mismatch")
        if len(response.proposed_items) != 1:
            raise UnsupportedInput("M1 accepts exactly one claim proposal per response")
        if response.allowed_evidence_ids != request.allowed_evidence_ids:
            raise UnsupportedInput("Proposal response evidence allowlist changed")
        pack_ids = {evidence.segment_id for evidence in pack.items}
        if set(request.allowed_evidence_ids) != pack_ids:
            raise IntegrityError("Proposal request evidence pack binding is incomplete")
        item = response.proposed_items[0]
        if not item.evidence_ids:
            raise UnsupportedInput("Claim proposal requires evidence")
        if not set(item.evidence_ids).issubset(pack_ids):
            raise UnsupportedInput("Claim proposal references evidence outside its pack")
        if item.kind != "claim":
            raise UnsupportedInput(f"Unsupported proposal item kind: {item.kind}")
        statement = item.payload.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            raise UnsupportedInput("Claim proposal requires a statement")
        language = item.payload.get("language", "und")
        if not isinstance(language, str):
            raise UnsupportedInput("Claim language must be a string")
        claim_id = derive_id(
            "clm",
            {"statement": statement, "language": language, "scope": {}},
        )
        claim = Claim(
            claim_id=claim_id,
            proposition_key=sha256_hex(
                canonical_json_bytes({"statement": statement, "language": language, "scope": {}})
            ),
            statement=statement,
            language=language,
            evidence_ids=item.evidence_ids,
            status=ClaimStatus.SUPPORTED,
            recorded_at=response.created_at,
        )
        claim_object_sha256 = sha256_hex(canonical_json_bytes(claim))
        return claim, claim_object_sha256

    def _load_excerpts(self, normalization: Normalization) -> dict[str, str]:
        binding = normalization.extensions.get(
            "raytsystem.excerpts"
        ) or normalization.extensions.get("agentos.excerpts")
        if not isinstance(binding, dict):
            raise IntegrityError("Normalization excerpt binding is missing")
        path = binding.get("path")
        expected_sha256 = binding.get("sha256")
        if not isinstance(path, str) or not isinstance(expected_sha256, str):
            raise IntegrityError("Normalization excerpt binding is malformed")
        data = read_regular_file(
            self.root,
            path,
            max_bytes=int(self.config["limits"]["max_input_bytes"]),
        ).data
        if sha256_hex(data) != expected_sha256:
            raise IntegrityError("Normalized excerpts hash mismatch")
        excerpts: dict[str, str] = {}
        for line in data.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise IntegrityError("Normalized excerpt record is invalid") from error
            if not isinstance(record, dict) or set(record) != {"segment_id", "excerpt"}:
                raise IntegrityError("Normalized excerpt record has invalid fields")
            segment_id = record["segment_id"]
            excerpt = record["excerpt"]
            if not isinstance(segment_id, str) or not isinstance(excerpt, str) or not excerpt:
                raise IntegrityError("Normalized excerpt record has invalid values")
            if segment_id in excerpts:
                raise IntegrityError("Normalized excerpt ID is duplicated")
            excerpts[segment_id] = excerpt
        return excerpts

    def _promote(
        self,
        prepared: _Prepared,
        *,
        approval: ApprovalRecord | None = None,
        fixture_authorized: bool = False,
        fixture_policy_sha256: str | None = None,
    ) -> IngestResult:
        if fixture_authorized:
            if approval is not None or fixture_policy_sha256 is None:
                raise IntegrityError("Fixture promotion authority is internally inconsistent")
        elif approval is None:
            raise ApprovalRequired("Real promotion requires externally verified approval")
        self._validate_prepared(prepared)
        run_id = prepared.result.run_id
        operation_key = prepared.result.operation_key
        now_ms = self._now_ms()
        ttl_ms = int(self.config["limits"]["lease_ttl_seconds"]) * 1000
        source_lease = self.control.acquire_lease(
            f"source:{prepared.result.source_id}",
            self.worker_id,
            ttl_ms=ttl_ms,
            now_ms=now_ms,
        )
        global_lease: LeaseToken | None = None
        try:
            global_lease = self.control.acquire_lease(
                "ledger:current",
                self.worker_id,
                ttl_ms=ttl_ms,
                now_ms=now_ms,
            )
            active_generation = read_current_generation(self.root)
            active_manifest = LedgerGeneration.model_validate(
                read_json(self.root / "ledger" / "generations" / f"{active_generation}.json")
            )
            claim_key = f"claim:{prepared.claim.claim_id}"
            active_entry = active_manifest.records.get(claim_key)
            existing_wal = self.control.promotion_for_operation(operation_key)
            own_committed_recovery = bool(
                existing_wal is not None
                and str(existing_wal["txn_id"]) == prepared.txn.txn_id
                and active_generation == prepared.txn.next_generation_id
            )
            if not own_committed_recovery:
                self._assert_active_generation_reconciled(active_manifest)
            superseded_wal_txn_id: str | None = None
            if (
                existing_wal is None
                and active_entry is not None
                and not active_entry.tombstone
                and active_entry.object_sha256 == prepared.claim_object_sha256
            ):
                final = replace(
                    prepared.result,
                    status="succeeded",
                    noop=True,
                    generation_id=active_generation,
                )
                self.control.update_operation(
                    operation_key,
                    state="succeeded",
                    result=asdict(final),
                    now_ms=self._now_ms(),
                )
                self._update_run_manifest(
                    run_id,
                    state="succeeded",
                    generation_id=active_generation,
                    event_id=active_manifest.promotion_event_id,
                    txn_id=active_manifest.promotion_txn_id,
                    semantic_noop=True,
                )
                return final
            if active_generation not in {
                prepared.txn.parent_generation_id,
                prepared.txn.next_generation_id,
            }:
                if existing_wal is not None and str(existing_wal["txn_id"]) == prepared.txn.txn_id:
                    wal_state = PromotionState(str(existing_wal["state"]))
                    if wal_state in {PromotionState.PREPARED, PromotionState.COMMITTING}:
                        superseded_wal_txn_id = prepared.txn.txn_id
                    else:
                        raise IntegrityError(
                            "Committed promotion WAL disagrees with the active generation"
                        )
                prepared = self._rebase_prepared(prepared, active_generation)
                self._validate_prepared(prepared)
                if approval is not None:
                    raise ApprovalRequired(
                        "Concurrent promotion changed the candidate; issue a new exact approval"
                    )
            elif (
                existing_wal is not None
                and str(existing_wal["txn_id"]) != prepared.txn.txn_id
                and PromotionState(str(existing_wal["state"]))
                in {PromotionState.PREPARED, PromotionState.COMMITTING}
            ):
                superseded_wal_txn_id = str(existing_wal["txn_id"])
            authority_extension: dict[str, str]
            if fixture_authorized:
                authority_extension = {
                    "kind": "fixture",
                    "fixture_policy_sha256": str(fixture_policy_sha256),
                    "authority_hash": self._fixture_authority_hash(
                        operation_key,
                        fixture_policy_sha256,
                    ),
                }
            else:
                if approval is None:
                    raise ApprovalRequired("Real promotion approval disappeared")
                authority_extension = {
                    "kind": "external_approval",
                    "approval_id": approval.approval_id,
                    "authority_hash": sha256_hex(canonical_json_bytes(approval)),
                }
            txn = prepared.txn.model_copy(
                update={
                    "partition_fencing_token": source_lease.fencing_token,
                    "global_fencing_token": global_lease.fencing_token,
                    "approval_id": None if approval is None else approval.approval_id,
                    "extensions": {
                        **prepared.txn.extensions,
                        "raytsystem.authority": authority_extension,
                    },
                }
            )
            approval_hash = (
                self._fixture_authority_hash(operation_key, fixture_policy_sha256)
                if fixture_authorized
                else sha256_hex(canonical_json_bytes(approval))
            )
            if (
                existing_wal is not None
                and str(existing_wal["txn_id"]) == txn.txn_id
                and existing_wal.get("approval_hash") is not None
                and str(existing_wal["approval_hash"]) != approval_hash
            ):
                old_authority_hash = str(existing_wal["approval_hash"])
                refresh_id = derive_id(
                    "arefresh",
                    {
                        "txn_id": txn.txn_id,
                        "old_authority_hash": old_authority_hash,
                        "new_authority_hash": approval_hash,
                    },
                )
                refresh = {
                    "schema_version": "1.0.0",
                    "refresh_id": refresh_id,
                    "txn_id": txn.txn_id,
                    "old_authority_hash": old_authority_hash,
                    "new_authority_hash": approval_hash,
                    "replacement_approval_id": txn.approval_id,
                    "replacement_issued_at": (None if approval is None else approval.approved_at),
                }
                publish_immutable(
                    self.root / "ops" / "approvals" / "supersessions" / f"{refresh_id}.json",
                    canonical_json_bytes(refresh),
                )
            claim_bytes = canonical_json_bytes(prepared.claim)
            claim_path = (
                self.root
                / "ledger"
                / "objects"
                / "sha256"
                / prepared.claim_object_sha256[:2]
                / f"{prepared.claim_object_sha256}.json"
            )
            publish_immutable(claim_path, claim_bytes)
            generation_path = (
                self.root / "ledger" / "generations" / f"{prepared.generation.generation_id}.json"
            )
            publish_immutable(generation_path, canonical_json_bytes(prepared.generation))
            self._fault("after_generation_publish")

            event_bytes = canonical_json_bytes(prepared.event)
            txn_bytes = canonical_json_bytes(txn)
            self.control.store_promotion(
                txn_id=txn.txn_id,
                operation_key=operation_key,
                run_id=run_id,
                partition_fencing_token=source_lease.fencing_token,
                global_fencing_token=global_lease.fencing_token,
                parent_generation_id=txn.parent_generation_id,
                next_generation_id=txn.next_generation_id,
                manifest_sha256=str(txn.candidate_manifest_sha256),
                event_id=txn.event_id,
                approval_hash=approval_hash,
                payload_json=txn_bytes.decode("utf-8"),
                state=PromotionState.PREPARED.value,
                now_ms=now_ms,
            )
            self.control.store_event_outbox(
                event_id=prepared.event.event_id,
                txn_id=txn.txn_id,
                payload_json=event_bytes.decode("utf-8"),
                payload_sha256=sha256_hex(event_bytes),
            )
            if superseded_wal_txn_id is not None:
                self.control.update_promotion_state(
                    superseded_wal_txn_id,
                    PromotionState.ABORTED.value,
                    now_ms=self._now_ms(),
                )
            self._fault("after_promotion_wal")
            current = read_current_generation(self.root)
            if current == txn.parent_generation_id:
                self._advance_promotion_state(txn.txn_id, PromotionState.COMMITTING)
                with self.control.hold_valid_leases(
                    (source_lease, global_lease),
                    now_ms=self._now_ms(),
                    renew_ttl_ms=ttl_ms,
                ) as renewed_leases:
                    source_lease, global_lease = renewed_leases
                    self._validate_prepared(prepared)
                    authority_manifest = read_json(
                        self.root / "ops" / "runs" / run_id / "manifest.json"
                    )
                    derived_fixture, derived_policy_sha256 = self._derive_fixture_authority(
                        prepared,
                        authority_manifest,
                    )
                    if (
                        derived_fixture is not fixture_authorized
                        or derived_policy_sha256 != fixture_policy_sha256
                    ):
                        raise ApprovalRequired(
                            "Promotion authority changed before the fenced commit"
                        )
                    if approval is not None:
                        self._assert_approval_valid(
                            prepared,
                            approval,
                            at=datetime.now(UTC),
                        )
                    source_lease, global_lease = self.control.renew_held_leases(
                        (source_lease, global_lease),
                        ttl_ms=ttl_ms,
                        now_ms=self._now_ms(),
                    )
                    guarded_current = read_current_generation(self.root)
                    if guarded_current != txn.parent_generation_id:
                        raise IntegrityError(
                            "Parent generation changed before fenced pointer commit"
                        )
                    replace_current_generation(self.root, txn.next_generation_id)
                self._fault("after_current_swap")
            elif current != txn.next_generation_id:
                raise IntegrityError(
                    f"Parent generation changed: expected {txn.parent_generation_id}, got {current}"
                )

            self._advance_promotion_state(txn.txn_id, PromotionState.COMMITTED)
            self._fault("after_db_committed")
            self.control.update_operation(
                operation_key,
                state="reconciling",
                now_ms=self._now_ms(),
            )
            self._reconcile(
                prepared,
                txn,
                leases=(source_lease, global_lease),
                ttl_ms=ttl_ms,
            )
            self._fault("before_succeeded")
            final = replace(prepared.result, status="succeeded")
            result_payload = asdict(final)
            self.control.update_operation(
                operation_key,
                state="succeeded",
                result=result_payload,
                now_ms=self._now_ms(),
            )
            self._advance_promotion_state(txn.txn_id, PromotionState.COMPLETED)
            self._update_run_manifest(
                run_id,
                state="succeeded",
                generation_id=final.generation_id,
                event_id=prepared.event.event_id,
                txn_id=txn.txn_id,
            )
            return final
        finally:
            if global_lease is not None:
                self.control.release_lease(global_lease)
            self.control.release_lease(source_lease)

    @staticmethod
    def _fixture_authority_hash(
        operation_key: str,
        fixture_policy_sha256: str | None,
    ) -> str:
        if fixture_policy_sha256 is None:
            raise IntegrityError("Fixture authority has no policy hash")
        return sha256_hex(
            canonical_json_bytes(
                {
                    "kind": "fixture",
                    "operation_key": operation_key,
                    "fixture_policy_sha256": fixture_policy_sha256,
                }
            )
        )

    def _assert_active_generation_reconciled(
        self,
        generation: LedgerGeneration,
    ) -> None:
        if generation.generation_id == "genesis":
            return
        event_path = self.root / "ops" / "events" / f"{generation.promotion_event_id}.json"
        marker_path = self.root / "knowledge" / ".materialized-generation"
        if not event_path.is_file() or not marker_path.is_file():
            raise IntegrityError(
                "Active generation has incomplete reconciliation; recover its run first"
            )
        try:
            event_bytes = event_path.read_bytes()
            event = PromotionEvent.model_validate(json.loads(event_bytes))
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise IntegrityError("Active generation event is invalid") from error
        if (
            event_bytes != canonical_json_bytes(event)
            or event.event_id != generation.promotion_event_id
            or event.txn_id != generation.promotion_txn_id
            or event.parent_generation_id != generation.parent_generation_id
            or event.new_generation_id != generation.generation_id
            or marker_path.read_text(encoding="ascii").strip() != generation.generation_id
        ):
            raise IntegrityError("Active generation reconciliation provenance disagrees")
        run_manifest = read_json(self.root / "ops" / "runs" / event.run_id / "manifest.json")
        expected_run = {
            "run_id": event.run_id,
            "operation_key": event.operation_key,
            "txn_id": event.txn_id,
            "event_id": event.event_id,
            "generation_id": generation.generation_id,
            "state": "succeeded",
        }
        if any(run_manifest.get(key) != value for key, value in expected_run.items()):
            raise IntegrityError(
                "Active generation run has not reached durable terminal convergence"
            )
        wal = self.control.connection.execute(
            "SELECT state FROM promotion_txns WHERE txn_id = ?",
            (event.txn_id,),
        ).fetchone()
        if wal is not None:
            operation = self.control.connection.execute(
                "SELECT state, result_json FROM operations WHERE operation_key = ?",
                (event.operation_key,),
            ).fetchone()
            run = self.control.connection.execute(
                "SELECT state FROM runs WHERE run_id = ?",
                (event.run_id,),
            ).fetchone()
            outbox = self.control.event_outbox_record(event.event_id)
            if (
                str(wal["state"]) != "completed"
                or operation is None
                or str(operation["state"]) != "succeeded"
                or operation["result_json"] is None
                or run is None
                or str(run["state"]) != "succeeded"
                or outbox is None
                or str(outbox["state"]) != "appended"
            ):
                raise IntegrityError(
                    "Active generation durable state has not converged; recover its run first"
                )
        if bool(self.config.get("git", {}).get("checkpoint_on_promotion", False)):
            try:
                GitCheckpoint(self.root).verify(
                    event_id=generation.promotion_event_id,
                    generation_id=generation.generation_id,
                )
            except CheckpointRejected as error:
                raise IntegrityError(
                    "Active generation Git checkpoint is incomplete; recover its run first"
                ) from error

    def _recover_committed(
        self,
        prepared: _Prepared,
        wal: dict[str, Any],
    ) -> IngestResult:
        """Finish derived side effects after the canonical pointer already committed."""
        self._validate_committed_prepared(prepared, wal)
        run_id = prepared.result.run_id
        now_ms = self._now_ms()
        ttl_ms = int(self.config["limits"]["lease_ttl_seconds"]) * 1000
        source_lease = self.control.acquire_lease(
            f"source:{prepared.result.source_id}",
            self.worker_id,
            ttl_ms=ttl_ms,
            now_ms=now_ms,
        )
        global_lease: LeaseToken | None = None
        try:
            global_lease = self.control.acquire_lease(
                "ledger:current",
                self.worker_id,
                ttl_ms=ttl_ms,
                now_ms=now_ms,
            )
            with self.control.hold_valid_leases(
                (source_lease, global_lease),
                now_ms=self._now_ms(),
                renew_ttl_ms=ttl_ms,
            ) as renewed_leases:
                source_lease, global_lease = renewed_leases
                latest = self.control.promotion_for_operation(prepared.result.operation_key)
                if latest is None or str(latest["txn_id"]) != prepared.txn.txn_id:
                    raise IntegrityError("Committed recovery WAL changed under its lease")
                self._validate_committed_prepared(prepared, latest)
                if read_current_generation(self.root) != prepared.generation.generation_id:
                    raise IntegrityError("Committed generation pointer changed during recovery")
                source_lease, global_lease = self.control.renew_held_leases(
                    (source_lease, global_lease),
                    ttl_ms=ttl_ms,
                    now_ms=self._now_ms(),
                )
            self._advance_promotion_state(
                prepared.txn.txn_id,
                PromotionState.COMMITTED,
            )
            self.control.update_operation(
                prepared.result.operation_key,
                state="reconciling",
                now_ms=self._now_ms(),
            )
            self._reconcile(
                prepared,
                prepared.txn,
                leases=(source_lease, global_lease),
                ttl_ms=ttl_ms,
            )
            self._fault("before_succeeded")
            final = replace(prepared.result, status="succeeded")
            self.control.update_operation(
                prepared.result.operation_key,
                state="succeeded",
                result=asdict(final),
                now_ms=self._now_ms(),
            )
            self._advance_promotion_state(
                prepared.txn.txn_id,
                PromotionState.COMPLETED,
            )
            self._update_run_manifest(
                run_id,
                state="succeeded",
                generation_id=final.generation_id,
                event_id=prepared.event.event_id,
                txn_id=prepared.txn.txn_id,
            )
            return final
        finally:
            if global_lease is not None:
                self.control.release_lease(global_lease)
            self.control.release_lease(source_lease)

    def _validate_committed_prepared(
        self,
        prepared: _Prepared,
        wal: dict[str, Any],
    ) -> None:
        txn = prepared.txn
        generation = prepared.generation
        event = prepared.event
        claim = prepared.claim
        if read_current_generation(self.root) != generation.generation_id:
            raise IntegrityError("Committed recovery does not own ledger/CURRENT")
        generation_path = self.root / "ledger" / "generations" / f"{generation.generation_id}.json"
        generation_bytes = generation_path.read_bytes()
        claim_path = (
            self.root
            / "ledger"
            / "objects"
            / "sha256"
            / prepared.claim_object_sha256[:2]
            / f"{prepared.claim_object_sha256}.json"
        )
        claim_bytes = claim_path.read_bytes()
        if (
            generation_bytes != canonical_json_bytes(generation)
            or not generation.verify_id()
            or sha256_hex(generation_bytes) != txn.candidate_manifest_sha256
            or claim_bytes != canonical_json_bytes(claim)
            or sha256_hex(claim_bytes) != prepared.claim_object_sha256
        ):
            raise IntegrityError("Committed generation or claim object failed hash closure")
        claim_key = f"claim:{claim.claim_id}"
        entry = generation.records.get(claim_key)
        if (
            entry is None
            or entry.object_sha256 != prepared.claim_object_sha256
            or txn.output_hashes != {claim_key: prepared.claim_object_sha256}
            or generation.promotion_txn_id != txn.txn_id
            or generation.promotion_event_id != event.event_id
            or generation.parent_generation_id != txn.parent_generation_id
        ):
            raise IntegrityError("Committed generation transaction closure failed")
        self._validate_generation_objects(
            generation,
            staged_claim=claim,
            reextract_normalization_id=None,
        )

        txn_bytes = canonical_json_bytes(txn)
        if str(wal.get("payload_json", "")).encode() != txn_bytes:
            raise IntegrityError("Committed promotion WAL payload changed")
        expected_wal = {
            "txn_id": txn.txn_id,
            "run_id": txn.run_id,
            "operation_key": txn.operation_key,
            "parent_generation_id": txn.parent_generation_id,
            "next_generation_id": txn.next_generation_id,
            "manifest_sha256": str(txn.candidate_manifest_sha256),
            "event_id": txn.event_id,
        }
        if any(str(wal.get(key, "")) != value for key, value in expected_wal.items()):
            raise IntegrityError("Committed promotion WAL columns disagree")
        if (
            int(wal["partition_fencing_token"]) != txn.partition_fencing_token
            or int(wal["global_fencing_token"]) != txn.global_fencing_token
        ):
            raise IntegrityError("Committed promotion fencing record disagrees")

        event_row = self.control.event_outbox_record(event.event_id)
        event_bytes = canonical_json_bytes(event)
        if (
            event_row is None
            or str(event_row["txn_id"]) != txn.txn_id
            or str(event_row["payload_json"]).encode() != event_bytes
            or str(event_row["payload_sha256"]) != sha256_hex(event_bytes)
            or event.event_id != derive_id("evt", {"txn_id": txn.txn_id})
            or event.run_id != txn.run_id
            or event.operation_key != txn.operation_key
            or event.parent_generation_id != txn.parent_generation_id
            or event.new_generation_id != txn.next_generation_id
        ):
            raise IntegrityError("Committed event outbox closure failed")

        result_snapshot = txn.extensions.get("raytsystem.result") or txn.extensions.get(
            "agentos.result"
        )
        if not isinstance(result_snapshot, dict) or result_snapshot != asdict(prepared.result):
            raise IntegrityError("Committed result snapshot is missing or changed")
        authority = txn.extensions.get("raytsystem.authority") or txn.extensions.get(
            "agentos.authority"
        )
        if not isinstance(authority, dict):
            raise IntegrityError("Committed authority snapshot is missing")
        recorded_authority_hash = wal.get("approval_hash")
        if authority.get("kind") == "fixture":
            policy_sha256 = authority.get("fixture_policy_sha256")
            expected_authority_hash = self._fixture_authority_hash(
                txn.operation_key,
                policy_sha256 if isinstance(policy_sha256, str) else None,
            )
            if (
                txn.approval_id is not None
                or authority.get("authority_hash") != expected_authority_hash
                or recorded_authority_hash != expected_authority_hash
            ):
                raise IntegrityError("Committed fixture authority snapshot changed")
        elif authority.get("kind") == "external_approval":
            if txn.approval_id is None:
                raise IntegrityError("Committed approval ID is missing")
            try:
                accepted = read_regular_file(
                    self.root,
                    f"ops/approvals/accepted/{txn.approval_id}.json",
                    max_bytes=1024 * 1024,
                ).data
            except PathPolicyError as error:
                raise IntegrityError("Accepted approval record is missing") from error
            try:
                verification = read_json(
                    self.root
                    / "ops"
                    / "approvals"
                    / "accepted"
                    / f"{txn.approval_id}.verification.json"
                )
            except IntegrityError as error:
                raise IntegrityError(
                    "Accepted approval verification metadata is missing"
                ) from error
            try:
                approval = ApprovalRecord.model_validate(json.loads(accepted))
            except (json.JSONDecodeError, ValueError) as error:
                raise IntegrityError("Accepted approval record is invalid") from error
            approval_hash = sha256_hex(accepted)
            verification_material = {
                "approval_id": txn.approval_id,
                "approval_sha256": approval_hash,
                "verifier": verification.get("verifier"),
            }
            if (
                accepted != canonical_json_bytes(approval)
                or approval.approval_id != txn.approval_id
                or authority.get("approval_id") != txn.approval_id
                or authority.get("authority_hash") != approval_hash
                or recorded_authority_hash != approval_hash
                or not isinstance(verification.get("verifier"), dict)
                or verification.get("verification_id") != derive_id("aver", verification_material)
            ):
                raise IntegrityError("Committed accepted approval changed")
        else:
            raise IntegrityError("Committed authority kind is unsupported")

    def _merge_claim_with_generation(
        self,
        generation: LedgerGeneration,
        claim: Claim,
    ) -> tuple[Claim, str]:
        prior_entry = generation.records.get(f"claim:{claim.claim_id}")
        if prior_entry is None or prior_entry.tombstone:
            return claim, sha256_hex(canonical_json_bytes(claim))
        prior_path = (
            self.root
            / "ledger"
            / "objects"
            / "sha256"
            / prior_entry.object_sha256[:2]
            / f"{prior_entry.object_sha256}.json"
        )
        prior_bytes = prior_path.read_bytes()
        if sha256_hex(prior_bytes) != prior_entry.object_sha256:
            raise IntegrityError("Prior claim object hash changed before evidence merge")
        try:
            prior_claim = Claim.model_validate(json.loads(prior_bytes))
        except (json.JSONDecodeError, ValueError) as error:
            raise IntegrityError("Prior claim object is invalid") from error
        if (
            prior_claim.claim_id != claim.claim_id
            or prior_claim.statement != claim.statement
            or prior_claim.language != claim.language
            or prior_claim.scope != claim.scope
        ):
            raise IntegrityError("Same claim ID has incompatible proposition content")
        merged = prior_claim.model_copy(
            update={
                "evidence_ids": tuple(
                    sorted(set(prior_claim.evidence_ids) | set(claim.evidence_ids))
                ),
                "recorded_at": min(prior_claim.recorded_at, claim.recorded_at),
            }
        )
        return merged, sha256_hex(canonical_json_bytes(merged))

    def _rebase_prepared(self, prepared: _Prepared, parent_generation_id: str) -> _Prepared:
        parent_generation_id = validate_generation_id(parent_generation_id)
        parent = LedgerGeneration.model_validate(
            read_json(self.root / "ledger" / "generations" / f"{parent_generation_id}.json")
        )
        if not parent.verify_id():
            raise IntegrityError("Cannot rebase onto an invalid generation")
        claim, claim_sha256 = self._merge_claim_with_generation(parent, prepared.claim)
        txn_id = derive_id(
            "ptxn",
            {
                "operation_key": prepared.result.operation_key,
                "parent_generation_id": parent_generation_id,
                "claim_object_sha256": claim_sha256,
            },
        )
        event_id = derive_id("evt", {"txn_id": txn_id})
        records = dict(parent.records)
        records[f"claim:{claim.claim_id}"] = GenerationEntry(
            kind="claim",
            logical_id=claim.claim_id,
            object_sha256=claim_sha256,
        )
        seed = LedgerGeneration(
            generation_id="gen_pending",
            parent_generation_id=parent_generation_id,
            records=records,
            schema_registry_sha256=self._schema_registry_sha256(),
            created_at=prepared.run_created_at,
            promotion_txn_id=txn_id,
            promotion_event_id=event_id,
        )
        generation = seed.model_copy(
            update={"generation_id": derive_id("gen", seed.identity_payload())}
        )
        result = replace(prepared.result, generation_id=generation.generation_id)
        txn = PromotionTxn(
            txn_id=txn_id,
            run_id=prepared.result.run_id,
            operation_key=prepared.result.operation_key,
            parent_generation_id=parent_generation_id,
            next_generation_id=generation.generation_id,
            candidate_manifest_sha256=sha256_hex(canonical_json_bytes(generation)),
            event_id=event_id,
            partition_fencing_token=prepared.txn.partition_fencing_token,
            global_fencing_token=prepared.txn.global_fencing_token,
            output_hashes={f"claim:{claim.claim_id}": claim_sha256},
            state=PromotionState.PREPARED,
            created_at=prepared.run_created_at,
            updated_at=datetime.now(UTC),
            extensions={"raytsystem.result": asdict(result)},
        )
        event = PromotionEvent(
            event_id=event_id,
            txn_id=txn_id,
            run_id=prepared.result.run_id,
            operation_key=prepared.result.operation_key,
            parent_generation_id=parent_generation_id,
            new_generation_id=generation.generation_id,
            committed_at=prepared.run_created_at,
        )
        staging = self.root / "ops" / "staging" / prepared.result.run_id
        self._write_staging_bundle(
            staging,
            claim=claim,
            generation=generation,
            txn=txn,
            event=event,
        )
        self._update_run_manifest(
            result.run_id,
            state="prepared",
            generation_id=generation.generation_id,
            event_id=event_id,
            txn_id=txn_id,
        )
        return _Prepared(
            result=result,
            claim=claim,
            claim_object_sha256=claim_sha256,
            generation=generation,
            txn=txn,
            event=event,
            run_created_at=prepared.run_created_at,
        )

    def _reconcile(
        self,
        prepared: _Prepared,
        txn: PromotionTxn,
        *,
        leases: tuple[LeaseToken, LeaseToken],
        ttl_ms: int,
    ) -> None:
        event_root = self.root / "ops" / "events"
        event_root.mkdir(parents=True, exist_ok=True)
        event_path = event_root / f"{prepared.event.event_id}.json"
        publish_immutable(event_path, canonical_json_bytes(prepared.event))
        self._fault("after_event_publish")
        event_records = [read_json(path) for path in event_root.glob("evt_*.json")]
        rebuild_jsonl(event_root / "events.jsonl", event_records, id_field="event_id")
        self.control.mark_event_appended(prepared.event.event_id)
        self._advance_promotion_state(txn.txn_id, PromotionState.RECONCILING)
        with self.control.hold_valid_leases(
            leases,
            now_ms=self._now_ms(),
            renew_ttl_ms=ttl_ms,
        ):
            if read_current_generation(self.root) != prepared.generation.generation_id:
                raise IntegrityError("Derived reconciliation no longer owns ledger/CURRENT")
            self._materialize(prepared)
            self._fault("after_materialization")
            if bool(self.config.get("git", {}).get("checkpoint_on_promotion", False)):
                GitCheckpoint(self.root).create(
                    event_id=prepared.event.event_id,
                    generation_id=prepared.generation.generation_id,
                    paths=self._checkpoint_paths(prepared),
                )
            self._fault("after_git_checkpoint")
            if read_current_generation(self.root) != prepared.generation.generation_id:
                raise IntegrityError("Derived reconciliation pointer changed unexpectedly")

    def _checkpoint_paths(self, prepared: _Prepared) -> tuple[str, ...]:
        paths: set[str] = {
            "ledger/CURRENT",
            f"ops/runs/{prepared.result.run_id}/manifest.json",
            "ops/events/events.jsonl",
            "knowledge/index.md",
            "knowledge/hot.md",
            "knowledge/graph.json",
            "knowledge/.projection.json",
            "knowledge/.materialized-generation",
        }
        normalized_root = self.root / prepared.result.normalized_path
        paths.update(
            path.relative_to(self.root).as_posix()
            for path in normalized_root.iterdir()
            if path.is_file()
        )
        for entry in prepared.generation.records.values():
            if entry.tombstone:
                continue
            paths.add(
                (
                    PurePosixPath("ledger")
                    / "objects"
                    / "sha256"
                    / entry.object_sha256[:2]
                    / f"{entry.object_sha256}.json"
                ).as_posix()
            )
        for relative_root in (
            "knowledge/claims",
            "knowledge/entities",
            "knowledge/sources",
        ):
            generated_root = self.root / relative_root
            if generated_root.is_dir():
                paths.update(
                    path.relative_to(self.root).as_posix()
                    for path in generated_root.glob("*.md")
                    if path.is_file()
                )
        for root in (
            self.root / "_raw" / "manifests",
            self.root / "_raw" / "sources" / "sha256",
            self.root / "_raw" / "revisions" / "sha256",
        ):
            if root.is_dir():
                paths.update(
                    path.relative_to(self.root).as_posix()
                    for path in root.rglob("*")
                    if path.is_file()
                )
        return tuple(sorted(paths))

    def _advance_promotion_state(self, txn_id: str, target: PromotionState) -> None:
        order = (
            PromotionState.PREPARED,
            PromotionState.COMMITTING,
            PromotionState.COMMITTED,
            PromotionState.RECONCILING,
            PromotionState.COMPLETED,
        )
        row = self.control.connection.execute(
            "SELECT state FROM promotion_txns WHERE txn_id = ?",
            (txn_id,),
        ).fetchone()
        if row is None:
            raise IntegrityError("Promotion transaction is missing from WAL")
        try:
            current = PromotionState(str(row["state"]))
        except ValueError as error:
            raise IntegrityError("Promotion transaction has an unknown state") from error
        if current is PromotionState.ABORTED:
            raise IntegrityError("Aborted promotion cannot advance")
        current_index = order.index(current)
        target_index = order.index(target)
        if current_index > target_index:
            return
        for next_state in order[current_index + 1 : target_index + 1]:
            self.control.update_promotion_state(
                txn_id,
                next_state.value,
                now_ms=self._now_ms(),
            )

    def _materialize(self, prepared: _Prepared) -> None:
        from raytsystem.projections import ProjectionService

        result = ProjectionService(self.root, scanner=self.scanner).rebuild()
        if result.generation_id != prepared.generation.generation_id:
            raise IntegrityError("Derived projection rebuilt a different active generation")

    def _rebuild_source_projection(self) -> None:
        source_root = self.root / "_raw" / "sources" / "sha256"
        sources = [read_json(path) for path in source_root.glob("*/*.json")]
        rebuild_jsonl(
            self.root / "_raw" / "manifests" / "sources.jsonl",
            sources,
            id_field="source_id",
        )
        revision_root = self.root / "_raw" / "revisions" / "sha256"
        records = [read_json(path) for path in revision_root.glob("*/*.json")]
        rebuild_jsonl(
            self.root / "_raw" / "manifests" / "source_revisions.jsonl",
            records,
            id_field="source_revision_id",
        )

    def _load_or_create_run_manifest(
        self,
        *,
        run_id: str,
        operation_key: str,
        source_id: str,
        input_sha256: str,
        input_path: str,
        fixture_authorized: bool,
        fixture_policy_sha256: str | None,
    ) -> datetime:
        path = self.root / "ops" / "runs" / run_id / "manifest.json"
        if path.is_file():
            payload = read_json(path)
            return datetime.fromisoformat(str(payload["created_at"]).replace("Z", "+00:00"))
        created_at = datetime.now(UTC)
        payload = {
            "run_id": run_id,
            "operation_type": "ingest",
            "operation_key": operation_key,
            "source_id": source_id,
            "input_sha256": input_sha256,
            "input_path": input_path,
            "fixture_authorized": fixture_authorized,
            "fixture_policy_sha256": fixture_policy_sha256,
            "state": "running",
            "created_at": created_at.isoformat().replace("+00:00", "Z"),
        }
        write_bytes_atomic(path, canonical_json_bytes(payload))
        return created_at

    def _update_run_manifest(self, run_id: str, *, state: str, **updates: Any) -> None:
        path = self.root / "ops" / "runs" / run_id / "manifest.json"
        payload = read_json(path)
        payload.update(updates)
        payload["state"] = state
        payload["updated_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        write_bytes_atomic(path, canonical_json_bytes(payload))

    def _schema_registry_sha256(self) -> str | None:
        registry = self.root / "config" / "schemas" / f"v{SCHEMA_VERSION}" / "registry.json"
        if not registry.is_file():
            return None
        payload = read_json(registry)
        value = payload.get("registry_sha256")
        return str(value) if value is not None else None

    def _policy_sha256(self) -> str | None:
        path = self.root / "config" / "policies.yaml"
        return sha256_hex(path.read_bytes()) if path.is_file() else None

    @staticmethod
    def _media_type(relative_path: str) -> str:
        suffix = Path(relative_path).suffix.lower()
        if suffix in {".md", ".markdown"}:
            return "text/markdown"
        if suffix in {".txt", ".text"}:
            return "text/plain"
        unsupported = suffix or "extensionless"
        raise UnsupportedInput(f"M1 native adapter does not support {unsupported} input")

    @staticmethod
    def _now_ms() -> int:
        return time.time_ns() // 1_000_000
