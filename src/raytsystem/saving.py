from __future__ import annotations

import json
import sqlite3
import time
import tomllib
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from raytsystem.contracts import (
    Artifact,
    ArtifactState,
    ComponentRef,
    EvidenceItem,
    EvidencePack,
    ProducerRef,
    ProposalItem,
    ProposalPurpose,
    ProposalRequest,
    ProposalResponse,
    RecordRef,
    Sensitivity,
    canonical_json_bytes,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.base import ProducerKind
from raytsystem.control import ControlDB, LeaseBusy
from raytsystem.corpus import ActiveCorpus, CorpusIntegrityError
from raytsystem.derived import assert_safe_replace_target
from raytsystem.io import UnsafeWritePath, ensure_safe_directory, write_bytes_atomic
from raytsystem.rendering import escape_untrusted_markdown
from raytsystem.security.paths import PathPolicyError, read_regular_file
from raytsystem.security.sensitivity import SecretScanner, SensitivityDecision
from raytsystem.storage import IntegrityError, read_current_generation


class SaveRejected(RuntimeError):
    """SAVE could not produce a safe, generation-bound draft bundle."""


@dataclass(frozen=True)
class SaveResult:
    status: str
    noop: bool
    run_id: str
    operation_key: str
    artifact_id: str
    generation_id: str
    preview_path: str
    staging_path: str

    @classmethod
    def from_dict(cls, value: dict[str, Any], *, noop: bool) -> SaveResult:
        return cls(
            status=str(value["status"]),
            noop=noop,
            run_id=str(value["run_id"]),
            operation_key=str(value["operation_key"]),
            artifact_id=str(value["artifact_id"]),
            generation_id=str(value["generation_id"]),
            preview_path=str(value["preview_path"]),
            staging_path=str(value["staging_path"]),
        )


class SaveService:
    version = "1.0.0"

    def __init__(self, root: Path, *, scanner: SecretScanner | None = None) -> None:
        self.root = root.resolve()
        self.scanner = scanner or SecretScanner()

    def stage(
        self,
        text: str,
        *,
        evidence_ids: tuple[str, ...],
        title: str = "Saved synthesis",
    ) -> SaveResult:
        title = self._validate_inputs(text, evidence_ids, title)
        try:
            corpus = ActiveCorpus.load(self.root)
        except CorpusIntegrityError as error:
            raise SaveRejected(f"SAVE evidence integrity failed with code {error.code}") from None
        ordered_evidence = tuple(sorted(set(evidence_ids)))
        if len(ordered_evidence) != len(evidence_ids):
            raise SaveRejected("SAVE evidence IDs must be unique")
        resolved = [corpus.resolve_evidence(evidence_id) for evidence_id in ordered_evidence]
        active_claim_ids = tuple(
            sorted(
                claim.claim_id
                for claim in corpus.claims.values()
                if set(claim.evidence_ids).intersection(ordered_evidence)
                and claim.status.value in {"supported", "confirmed"}
            )
        )
        if not active_claim_ids:
            raise SaveRejected("SAVE evidence is not attached to an active factual claim")
        component = self._component()
        operation_key = derive_id(
            "op",
            {
                "operation_type": "save_synthesis",
                "generation_id": corpus.generation.generation_id,
                "generation_sha256": corpus.generation_sha256,
                "text_sha256": sha256_hex(text.encode("utf-8")),
                "title_sha256": sha256_hex(title.encode("utf-8")),
                "evidence_ids": ordered_evidence,
                "schema_registry_sha256": corpus.generation.schema_registry_sha256,
                "component": component,
                "config_sha256": self._config_sha256(),
            },
        )
        run_id = derive_id("run", {"operation_key": operation_key})
        try:
            control = ControlDB(self._control_path())
        except (OSError, sqlite3.Error, UnsafeWritePath) as error:
            raise SaveRejected("SAVE coordination database failed closed") from error
        worker_id = f"worker_{uuid.uuid4().hex}"
        try:
            claim = control.claim_operation(
                operation_key=operation_key,
                run_id=run_id,
                stage="save_synthesis",
                partition_key=f"draft:save:{operation_key}",
                now_ms=time.time_ns() // 1_000_000,
            )
            if claim.state == "succeeded" and claim.result is not None:
                result = SaveResult.from_dict(claim.result, noop=True)
                self._verify_result(result)
                return result
            lease = self._acquire_or_join(control, operation_key, worker_id)
            if lease is None:
                row = control.connection.execute(
                    "SELECT result_json FROM operations WHERE operation_key = ?",
                    (operation_key,),
                ).fetchone()
                if row is None or row["result_json"] is None:
                    raise SaveRejected("Concurrent SAVE did not produce a durable result")
                result = SaveResult.from_dict(json.loads(str(row["result_json"])), noop=True)
                self._verify_result(result)
                return result
            try:
                row = control.connection.execute(
                    "SELECT state, result_json FROM operations WHERE operation_key = ?",
                    (operation_key,),
                ).fetchone()
                if row is not None and row["state"] == "succeeded" and row["result_json"]:
                    result = SaveResult.from_dict(json.loads(str(row["result_json"])), noop=True)
                    self._verify_result(result)
                    return result
                if read_current_generation(self.root) != corpus.generation.generation_id:
                    raise SaveRejected("SAVE generation changed before staging")
                result = self._write_bundle(
                    corpus=corpus,
                    run_id=run_id,
                    operation_key=operation_key,
                    text=text,
                    title=title,
                    evidence_ids=ordered_evidence,
                    active_claim_ids=active_claim_ids,
                    resolved=resolved,
                    component=component,
                )
                manifest_path = self.root / "ops" / "runs" / run_id / "manifest.json"
                manifest = {
                    "schema_version": "1.0.0",
                    "run_id": run_id,
                    "operation_type": "save_synthesis",
                    "operation_key": operation_key,
                    "generation_id": corpus.generation.generation_id,
                    "artifact_id": result.artifact_id,
                    "state": "succeeded",
                    "staging_path": result.staging_path,
                    "preview_path": result.preview_path,
                    "created_at": corpus.generation.created_at,
                    "updated_at": corpus.generation.created_at,
                }
                self._write_safe(manifest_path, canonical_json_bytes(manifest))
                control.update_operation(
                    operation_key,
                    state="succeeded",
                    result=asdict(result),
                    now_ms=time.time_ns() // 1_000_000,
                )
                return result
            finally:
                control.release_lease(lease)
        except (IntegrityError, OSError, sqlite3.Error, UnsafeWritePath, ValueError) as error:
            raise SaveRejected("SAVE staging failed closed") from error
        finally:
            control.close()

    def _write_bundle(
        self,
        *,
        corpus: ActiveCorpus,
        run_id: str,
        operation_key: str,
        text: str,
        title: str,
        evidence_ids: tuple[str, ...],
        active_claim_ids: tuple[str, ...],
        resolved: list[Any],
        component: ComponentRef,
    ) -> SaveResult:
        evidence_items = tuple(
            EvidenceItem(
                source_revision_id=item.revision.source_revision_id,
                normalization_id=item.normalization.normalization_id,
                segment_id=item.segment.segment_id,
                locator=item.segment.locator,
                excerpt=item.excerpt,
                excerpt_sha256=item.segment.excerpt_sha256,
                trust_class=item.source.trust_class,
                captured_at=item.revision.captured_at,
            )
            for item in resolved
        )
        pack_material = {
            "run_id": run_id,
            "purpose": "save_synthesis",
            "items": evidence_items,
        }
        pack = EvidencePack(
            evidence_pack_id=derive_id("pack", pack_material),
            run_id=run_id,
            purpose="save_synthesis",
            items=evidence_items,
            classification=Sensitivity.INTERNAL,
            pack_sha256=sha256_hex(canonical_json_bytes(pack_material)),
            created_at=corpus.generation.created_at,
        )
        pack_sha256 = sha256_hex(canonical_json_bytes(pack))
        request_material = {
            "run_id": run_id,
            "operation_key": operation_key,
            "pack_sha256": pack_sha256,
        }
        request = ProposalRequest(
            proposal_request_id=derive_id("preq", request_material),
            run_id=run_id,
            operation_key=operation_key,
            purpose=ProposalPurpose.SAVE_SYNTHESIS,
            evidence_pack_ref=RecordRef(
                kind="evidence_pack",
                id=pack.evidence_pack_id,
                object_sha256=pack_sha256,
            ),
            allowed_evidence_ids=evidence_ids,
            target_schema_refs=(),
            prompt_or_skill_ref=component,
            policy_constraints=("draft_only", "no_canonical_write", "no_external_side_effect"),
            created_at=corpus.generation.created_at,
        )
        item = ProposalItem(
            proposal_item_id=derive_id(
                "pitem",
                {
                    "request_id": request.proposal_request_id,
                    "title_sha256": sha256_hex(title.encode("utf-8")),
                    "text_sha256": sha256_hex(text.encode("utf-8")),
                },
            ),
            kind="saved_synthesis",
            payload={"title": title, "text": text},
            evidence_ids=evidence_ids,
        )
        request_sha256 = sha256_hex(canonical_json_bytes(request))
        producer = ProducerRef(kind=ProducerKind.KERNEL, component=component)
        response = ProposalResponse(
            proposal_response_id=derive_id(
                "pres",
                {"request_sha256": request_sha256, "items": [item]},
            ),
            request_ref=RecordRef(
                kind="proposal_request",
                id=request.proposal_request_id,
                object_sha256=request_sha256,
            ),
            producer=producer,
            allowed_evidence_ids=evidence_ids,
            proposed_items=(item,),
            created_at=corpus.generation.created_at,
        )
        artifact_id = derive_id(
            "art",
            {
                "operation_key": operation_key,
                "generation_id": corpus.generation.generation_id,
                "proposal_response_id": response.proposal_response_id,
            },
        )
        preview_relative = f"artifacts/drafts/saves/{artifact_id}/preview.md"
        preview = (
            "---\n"
            "draft: true\n"
            "external_side_effects: forbidden\n"
            f"generation_id: {corpus.generation.generation_id}\n"
            f"artifact_id: {artifact_id}\n"
            "---\n\n"
            f"# {escape_untrusted_markdown(title)}\n\n"
            f"{escape_untrusted_markdown(text)}\n\n"
            f"Evidence: `{', '.join(evidence_ids)}`\n"
        ).encode()
        self._scan_generated(preview, preview_relative)
        input_refs = tuple(
            RecordRef(
                kind="claim",
                id=claim_id,
                object_sha256=corpus.records[f"claim:{claim_id}"].object_sha256,
            )
            for claim_id in active_claim_ids
        )
        artifact = Artifact(
            artifact_id=artifact_id,
            kind="saved_synthesis",
            project_id="project_raytsystem",
            stage_id="save_draft",
            run_id=run_id,
            state=ArtifactState.DRAFT,
            input_refs=input_refs,
            claim_ids=active_claim_ids,
            skill_ref=component,
            output_sha256=sha256_hex(preview),
            path=preview_relative,
            created_at=corpus.generation.created_at,
            extensions={
                "raytsystem.generation_id": corpus.generation.generation_id,
                "raytsystem.generation_sha256": corpus.generation_sha256,
                "raytsystem.evidence_ids": list(evidence_ids),
            },
        )
        staging_relative = f"ops/staging/{run_id}"
        staging = self.root / staging_relative
        ensure_safe_directory(staging)
        payloads = {
            "artifact.json": canonical_json_bytes(artifact),
            "evidence_pack.json": canonical_json_bytes(pack),
            "proposal_request.json": canonical_json_bytes(request),
            "proposal_response.json": canonical_json_bytes(response),
        }
        for filename, data in sorted(payloads.items()):
            self._scan_generated(data, f"{staging_relative}/{filename}")
            self._write_safe(staging / filename, data)
        marker_material = {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "operation_key": operation_key,
            "generation_id": corpus.generation.generation_id,
            "artifact_id": artifact_id,
            "files": {filename: sha256_hex(data) for filename, data in sorted(payloads.items())},
        }
        marker = {
            **marker_material,
            "bundle_sha256": sha256_hex(canonical_json_bytes(marker_material)),
        }
        self._write_safe(staging / "bundle.json", canonical_json_bytes(marker))
        preview_path = self.root / preview_relative
        ensure_safe_directory(preview_path.parent)
        self._write_safe(preview_path, preview)
        self._write_safe(preview_path.parent / "artifact.json", canonical_json_bytes(artifact))
        if read_current_generation(self.root) != corpus.generation.generation_id:
            raise SaveRejected("SAVE generation changed before draft completion")
        return SaveResult(
            status="succeeded",
            noop=False,
            run_id=run_id,
            operation_key=operation_key,
            artifact_id=artifact_id,
            generation_id=corpus.generation.generation_id,
            preview_path=preview_relative,
            staging_path=staging_relative,
        )

    def _validate_inputs(
        self,
        text: str,
        evidence_ids: tuple[str, ...],
        title: str,
    ) -> str:
        if not text.strip() or len(text.encode("utf-8")) > 64 * 1024 or "\x00" in text:
            raise SaveRejected("SAVE text exceeds limits")
        if (
            not title.strip()
            or len(title.encode("utf-8")) > 256
            or any(character in title for character in ("/", "\\", "\x00", "\r", "\n"))
            or ".." in title
        ):
            raise SaveRejected("SAVE title is unsafe")
        if not 1 <= len(evidence_ids) <= 50:
            raise SaveRejected("SAVE requires 1..50 evidence IDs")
        for value, label in ((text, "text"), (title, "title")):
            decision = self.scanner.scan(value.encode("utf-8"), path=None)
            if not isinstance(decision, SensitivityDecision) or decision.disposition != "allow":
                raise SaveRejected(f"SAVE {label} failed the sensitivity gate")
        return title.strip()

    def _acquire_or_join(
        self,
        control: ControlDB,
        operation_key: str,
        worker_id: str,
    ) -> Any:
        deadline = time.monotonic() + 10
        while True:
            try:
                return control.acquire_lease(
                    f"draft:save:{operation_key}",
                    worker_id,
                    ttl_ms=30_000,
                    now_ms=time.time_ns() // 1_000_000,
                )
            except LeaseBusy:
                row = control.connection.execute(
                    "SELECT state, result_json FROM operations WHERE operation_key = ?",
                    (operation_key,),
                ).fetchone()
                if row is not None and row["state"] == "succeeded" and row["result_json"]:
                    return None
                if time.monotonic() >= deadline:
                    raise SaveRejected("Timed out waiting for identical SAVE") from None
                time.sleep(0.01)

    def _verify_result(self, result: SaveResult) -> None:
        if read_current_generation(self.root) != result.generation_id:
            raise SaveRejected("Saved draft belongs to a different active generation")
        expected_preview = f"artifacts/drafts/saves/{result.artifact_id}/preview.md"
        expected_staging = f"ops/staging/{result.run_id}"
        if result.preview_path != expected_preview or result.staging_path != expected_staging:
            raise SaveRejected("Saved draft result paths are invalid")
        preview_bytes = read_regular_file(
            self.root,
            result.preview_path,
            max_bytes=128 * 1024,
        ).data
        artifact_relative = (PurePosixPath(result.preview_path).parent / "artifact.json").as_posix()
        artifact_bytes = read_regular_file(
            self.root,
            artifact_relative,
            max_bytes=1024 * 1024,
        ).data
        artifact = Artifact.model_validate(json.loads(artifact_bytes))
        if (
            artifact.artifact_id != result.artifact_id
            or artifact.run_id != result.run_id
            or artifact.path != result.preview_path
            or artifact.output_sha256 != sha256_hex(preview_bytes)
            or canonical_json_bytes(artifact) != artifact_bytes
        ):
            raise SaveRejected("Saved draft result hash changed")
        marker_relative = f"{result.staging_path}/bundle.json"
        marker_bytes = read_regular_file(
            self.root,
            marker_relative,
            max_bytes=1024 * 1024,
        ).data
        marker = json.loads(marker_bytes)
        if not isinstance(marker, dict) or canonical_json_bytes(marker) != marker_bytes:
            raise SaveRejected("Saved draft bundle marker is invalid")
        material = dict(marker)
        bundle_sha256 = material.pop("bundle_sha256", None)
        files = material.get("files")
        if (
            bundle_sha256 != sha256_hex(canonical_json_bytes(material))
            or material.get("run_id") != result.run_id
            or material.get("operation_key") != result.operation_key
            or material.get("generation_id") != result.generation_id
            or material.get("artifact_id") != result.artifact_id
            or not isinstance(files, dict)
        ):
            raise SaveRejected("Saved draft bundle identity changed")
        for filename, expected_sha256 in sorted(files.items()):
            if (
                not isinstance(filename, str)
                or filename != PurePosixPath(filename).name
                or not isinstance(expected_sha256, str)
            ):
                raise SaveRejected("Saved draft bundle file list is invalid")
            payload = read_regular_file(
                self.root,
                f"{result.staging_path}/{filename}",
                max_bytes=4 * 1024 * 1024,
            ).data
            if sha256_hex(payload) != expected_sha256:
                raise SaveRejected("Saved draft bundle payload changed")

    def _scan_generated(self, data: bytes, relative: str) -> None:
        decision = self.scanner.scan(data, path=relative)
        if not isinstance(decision, SensitivityDecision) or decision.disposition != "allow":
            raise SaveRejected("SAVE generated output failed the sensitivity gate")

    @staticmethod
    def _write_safe(path: Path, data: bytes) -> None:
        assert_safe_replace_target(path)
        write_bytes_atomic(path, data)

    @classmethod
    def _component(cls) -> ComponentRef:
        return ComponentRef(
            name="raytsystem_save",
            version=cls.version,
            config_sha256=sha256_hex(
                canonical_json_bytes(
                    {
                        "mode": "typed_draft_only",
                        "renderer": "escaped_markdown_v1",
                        "side_effects": "forbidden",
                    }
                )
            ),
        )

    def _control_path(self) -> Path:
        try:
            data = read_regular_file(
                self.root,
                "config/raytsystem.toml",
                max_bytes=1024 * 1024,
            ).data
            config = tomllib.loads(data.decode("utf-8"))
        except (OSError, PathPolicyError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
            raise SaveRejected("raytsystem config is invalid") from error
        relative = Path(str(config.get("control_db", "ops/control.sqlite")))
        if relative.is_absolute() or ".." in relative.parts:
            raise SaveRejected("Control database path escapes the workspace")
        return self.root / relative

    def _config_sha256(self) -> str:
        data = read_regular_file(
            self.root,
            "config/raytsystem.toml",
            max_bytes=1024 * 1024,
        ).data
        return sha256_hex(data)
