from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from raytsystem.authority import AuthorityError, AuthorityResolver
from raytsystem.contracts import TraceRecord, TraceSpan, canonical_json_bytes, derive_id, sha256_hex
from raytsystem.contracts.observability import RedactionStatus, SpanKind, SpanStatus
from raytsystem.features import FeatureConfig, load_feature_config
from raytsystem.platform_store import (
    PlatformStore,
    StoredRecord,
    initialize_platform_store,
    open_platform_store_read_only,
)
from raytsystem.security.sensitivity import SecretScanner

_SENSITIVE_ATTRIBUTE_KEYS = frozenset(
    {
        "prompt",
        "system_prompt",
        "raw_prompt",
        "input",
        "output",
        "tool_arguments",
        "arguments",
        "environment",
        "terminal_output",
        "secret",
        "authorization",
        "credential",
        "cookie",
    }
)
_SENSITIVE_ATTRIBUTE_MARKERS = (
    "prompt",
    "input",
    "output",
    "argument",
    "environment",
    "secret",
    "authorization",
    "credential",
    "cookie",
    "token",
)
_ROOT_SPAN_KINDS = frozenset({SpanKind.TASK, SpanKind.RUN})
_OTLP_STATUS_CODES: dict[SpanStatus, int] = {
    SpanStatus.UNSET: 0,
    SpanStatus.OK: 1,
    SpanStatus.ERROR: 2,
    SpanStatus.CANCELLED: 2,
    SpanStatus.BLOCKED: 2,
}


class TelemetryError(RuntimeError):
    """Trace data violates integrity, redaction, or feature policy."""


class TraceService:
    def __init__(
        self,
        root: Path,
        *,
        scanner: SecretScanner | None = None,
        features: FeatureConfig | None = None,
    ) -> None:
        self.root = root.resolve()
        self.scanner = scanner or SecretScanner()
        self.features = features or load_feature_config(self.root)

    def create_trace(
        self,
        trace: TraceRecord,
        *,
        actor_id: str = "raytsystem_kernel",
        repository_sha256: str | None = None,
    ) -> TraceRecord:
        self._require_enabled()
        if repository_sha256 is not None and trace.trace_id != deterministic_trace_id(
            trace.task_id, trace.root_run_id, repository_sha256
        ):
            raise TelemetryError("Trace ID does not match its deterministic derivation")
        if self.scanner.scan(trace.model_dump_json().encode("utf-8")).blocks_processing:
            raise TelemetryError("Trace identifiers contain restricted data")
        with initialize_platform_store(self.root) as store:
            if store.head("trace", trace.trace_id) is not None:
                raise TelemetryError("Trace already exists")
            store.append_record(
                kind="trace",
                record_id=trace.trace_id,
                payload=trace.model_dump(mode="json"),
                state=trace.status.value,
                expected_revision=None,
            )
            store.append_event(
                stream_id=trace.trace_id,
                aggregate_id=trace.trace_id,
                event_type="trace_created",
                actor_id=actor_id,
                payload_schema="trace_record_v1",
                payload={"trace_id": trace.trace_id, "root_run_id": trace.root_run_id},
            )
        return trace

    def record_span(
        self,
        span: TraceSpan,
        *,
        actor_id: str = "raytsystem_kernel",
        sequence: int | None = None,
    ) -> TraceSpan:
        self._require_enabled()
        if sequence is not None and span.span_id != deterministic_span_id(
            span.trace_id, span.operation_name, sequence, span.parent_span_id
        ):
            raise TelemetryError("Span ID does not match its deterministic derivation")
        sanitized = self._sanitize_span(span)
        with initialize_platform_store(self.root) as store:
            trace = store.head("trace", sanitized.trace_id)
            if trace is None:
                raise TelemetryError("Span references an unknown trace")
            trace_payload = trace.payload
            if (
                trace_payload.get("root_run_id") != sanitized.run_id
                and sanitized.parent_span_id is None
            ):
                raise TelemetryError("A root span must belong to the trace root run")
            if (
                sanitized.parent_span_id is None
                and trace_payload.get("root_span_id") != sanitized.span_id
            ):
                raise TelemetryError("Root span does not match the trace root span")
            if sanitized.parent_span_id is None and sanitized.span_kind not in _ROOT_SPAN_KINDS:
                raise TelemetryError("Child span kinds require a parent span in the same trace")
            if sanitized.parent_span_id is not None:
                parent = store.head("span", sanitized.parent_span_id)
                if parent is None or parent.payload.get("trace_id") != sanitized.trace_id:
                    raise TelemetryError("Span parent is missing or belongs to another trace")
            prior = store.head("span", sanitized.span_id)
            expected_revision = None if prior is None else prior.revision
            if prior is not None:
                immutable = ("trace_id", "span_id", "parent_span_id", "started_at", "run_id")
                if any(
                    prior.payload.get(key) != sanitized.model_dump(mode="json").get(key)
                    for key in immutable
                ):
                    raise TelemetryError("Span identity fields cannot change")
                if prior.payload.get("status") in {"ok", "error", "cancelled", "blocked"}:
                    raise TelemetryError("Terminal spans cannot be reopened or rewritten")
            store.append_record(
                kind="span",
                record_id=sanitized.span_id,
                payload=sanitized.model_dump(mode="json"),
                state=sanitized.status.value,
                expected_revision=expected_revision,
            )
            store.append_event(
                stream_id=sanitized.trace_id,
                aggregate_id=sanitized.span_id,
                event_type="span_recorded" if prior is None else "span_updated",
                actor_id=actor_id,
                payload_schema="trace_span_v1",
                payload={
                    "span_id": sanitized.span_id,
                    "status": sanitized.status.value,
                    "redaction_status": sanitized.redaction_status.value,
                },
            )
        return sanitized

    def close_trace(
        self,
        trace_id: str,
        *,
        status: str,
        completed_at: datetime | None = None,
        actor_id: str = "raytsystem_kernel",
    ) -> TraceRecord:
        self._require_enabled()
        with initialize_platform_store(self.root) as store:
            prior = store.head("trace", trace_id)
            if prior is None:
                raise TelemetryError("Trace does not exist")
            spans = _trace_span_records(store, trace_id)
            payload = dict(prior.payload)
            payload.update(
                {
                    "completed_at": (completed_at or datetime.now(UTC)).isoformat(),
                    "status": status,
                    "span_count": len(spans),
                    "input_tokens": sum(int(item.payload.get("input_tokens", 0)) for item in spans),
                    "output_tokens": sum(
                        int(item.payload.get("output_tokens", 0)) for item in spans
                    ),
                    "cached_tokens": sum(
                        int(item.payload.get("cached_tokens", 0)) for item in spans
                    ),
                    "estimated_cost": str(
                        sum(
                            (Decimal(str(item.payload.get("estimated_cost", 0))) for item in spans),
                            Decimal("0"),
                        )
                    ),
                }
            )
            try:
                closed = TraceRecord.model_validate(payload)
            except ValidationError as error:
                raise TelemetryError("Trace closure is invalid") from error
            store.append_record(
                kind="trace",
                record_id=trace_id,
                payload=closed.model_dump(mode="json"),
                state=closed.status.value,
                expected_revision=prior.revision,
            )
            store.append_event(
                stream_id=trace_id,
                aggregate_id=trace_id,
                event_type="trace_closed",
                actor_id=actor_id,
                payload_schema="trace_record_v1",
                payload={"trace_id": trace_id, "status": closed.status.value},
            )
        return closed

    def list_traces(self, *, limit: int = 100) -> dict[str, Any]:
        store = open_platform_store_read_only(self.root)
        if store is None:
            return {"snapshot_id": "pview_unavailable", "state": "unavailable", "traces": []}
        with store:
            records = store.list_heads("trace", limit=limit)
            return {
                "snapshot_id": store.snapshot_id(),
                "state": "ready" if self.features.enabled("telemetry_enabled") else "disabled",
                "traces": [_public_trace(item) for item in records],
            }

    def trace_detail(self, trace_id: str, *, limit: int = 500) -> dict[str, Any] | None:
        store = open_platform_store_read_only(self.root)
        if store is None:
            return None
        with store:
            trace = store.head("trace", trace_id)
            if trace is None:
                return None
            spans = [_public_span(item) for item in _trace_span_records(store, trace_id)[:limit]]
            spans.sort(key=lambda item: (str(item["started_at"]), str(item["span_id"])))
            return {
                "snapshot_id": store.snapshot_id(),
                "trace": _public_trace(trace),
                "spans": spans,
            }

    def export_fingerprint(self) -> dict[str, str]:
        self._require_enabled()
        store = open_platform_store_read_only(self.root)
        if store is None:
            raise TelemetryError("The platform store is not initialized")
        with store:
            traces = _all_head_records(store, "trace")
            spans = _all_head_records(store, "span")
        target_id, artifact_sha256 = _export_identity(traces, spans)
        return {
            "action": "export_traces",
            "target_id": target_id,
            "artifact_sha256": artifact_sha256,
            "required_scope": "otel_export",
        }

    def export_otlp(
        self,
        destination_path: Path | str,
        *,
        approval_id: str,
        actor_id: str = "raytsystem_kernel",
    ) -> dict[str, Any]:
        self._require_enabled()
        if not self.features.enabled("otel_export_enabled"):
            raise TelemetryError("OTLP export is disabled")
        candidate = Path(destination_path).expanduser()
        if candidate.is_symlink():
            raise TelemetryError("OTLP export destination cannot be a symlink")
        destination = candidate.resolve()
        if destination.exists():
            raise TelemetryError("OTLP export destination already exists")
        with initialize_platform_store(self.root) as store:
            traces = _all_head_records(store, "trace")
            spans = _all_head_records(store, "span")
            target_id, artifact_sha256 = _export_identity(traces, spans)
            try:
                AuthorityResolver(self.root).require_approval(
                    approval_id,
                    action="export_traces",
                    target_id=target_id,
                    artifact_sha256=artifact_sha256,
                    destination=str(destination),
                    required_scope=frozenset({"otel_export"}),
                )
            except AuthorityError as error:
                raise TelemetryError("OTLP export authority is invalid") from error
            document = self._otlp_document(traces, spans)
            rendered = canonical_json_bytes(document)
            if self.scanner.scan(rendered).blocks_processing:
                raise TelemetryError("OTLP export contains restricted data")
            document_sha256 = sha256_hex(rendered)
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination.write_bytes(rendered)
            store.append_event(
                stream_id=target_id,
                aggregate_id=target_id,
                event_type="otlp_exported",
                actor_id=actor_id,
                payload_schema="otlp_export_v1",
                payload={
                    "export_id": target_id,
                    "destination": str(destination),
                    "artifact_sha256": artifact_sha256,
                    "document_sha256": document_sha256,
                    "trace_count": len(traces),
                    "span_count": len(spans),
                    "approval_id": approval_id,
                },
            )
        return {
            "export_id": target_id,
            "destination": str(destination),
            "artifact_sha256": artifact_sha256,
            "document_sha256": document_sha256,
            "trace_count": len(traces),
            "span_count": len(spans),
        }

    def _otlp_document(
        self,
        traces: tuple[StoredRecord, ...],
        spans: tuple[StoredRecord, ...],
    ) -> dict[str, Any]:
        sanitized: list[TraceSpan] = []
        for record in spans:
            try:
                span = TraceSpan.model_validate(record.payload)
            except ValidationError as error:
                raise TelemetryError("Stored span is invalid and cannot be exported") from error
            sanitized.append(self._sanitize_span(span))
        sanitized.sort(key=lambda item: (item.started_at, item.span_id))
        return {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "raytsystem"}},
                            {
                                "key": "raytsystem.trace_count",
                                "value": {"stringValue": str(len(traces))},
                            },
                        ]
                    },
                    "scopeSpans": [
                        {
                            "scope": {"name": "raytsystem.telemetry", "version": "1.0.0"},
                            "spans": [_otlp_span(item) for item in sanitized],
                        }
                    ],
                }
            ]
        }

    def _sanitize_span(self, span: TraceSpan) -> TraceSpan:
        max_attributes = self._policy_int("max_span_attributes", 32)
        max_attribute_bytes = self._policy_int("max_span_attribute_bytes", 16_384)
        if len(span.attributes) > max_attributes:
            raise TelemetryError(
                f"Span attributes exceed the max_span_attributes policy bound of {max_attributes}"
            )
        for key, value in span.attributes.items():
            if len(key.encode("utf-8")) + len(value.encode("utf-8")) > max_attribute_bytes:
                raise TelemetryError(
                    "A span attribute exceeds the max_span_attribute_bytes policy bound of "
                    f"{max_attribute_bytes}"
                )
        if len(canonical_json_bytes(span.attributes)) > max_attribute_bytes:
            raise TelemetryError(
                "Span attributes exceed the max_span_attribute_bytes policy bound of "
                f"{max_attribute_bytes}"
            )
        attributes: dict[str, str] = {}
        redacted = span.redaction_status
        for index, (key, value) in enumerate(span.attributes.items()):
            lowered = key.casefold()
            raw = value.encode("utf-8")
            decision = self.scanner.scan(raw)
            sensitive_key = lowered in _SENSITIVE_ATTRIBUTE_KEYS or any(
                marker in lowered for marker in _SENSITIVE_ATTRIBUTE_MARKERS
            )
            unsafe_key = self.scanner.scan(key.encode("utf-8")).blocks_processing
            if sensitive_key or unsafe_key or decision.blocks_processing:
                digest_key = f"attribute_{index}_sha256" if unsafe_key else f"{key[:110]}_sha256"
                attributes[digest_key] = sha256_hex(raw)
                redacted = RedactionStatus.REDACTED
            else:
                attributes[key] = value
        payload = span.model_dump(mode="python")
        for field in ("provider", "model", "tool_name", "error_code"):
            field_value = payload.get(field)
            if (
                isinstance(field_value, str)
                and self.scanner.scan(field_value.encode("utf-8")).blocks_processing
            ):
                payload[field] = "redacted" if field != "model" else "[REDACTED]"
                redacted = RedactionStatus.REDACTED
        payload["attributes"] = attributes
        payload["redaction_status"] = redacted
        try:
            sanitized = TraceSpan.model_validate(payload)
        except ValidationError as error:
            raise TelemetryError("Span is invalid after redaction") from error
        if self.scanner.scan(sanitized.model_dump_json().encode("utf-8")).blocks_processing:
            raise TelemetryError("Span contains restricted data outside redactable fields")
        return sanitized

    def _require_enabled(self) -> None:
        if not self.features.enabled("telemetry_enabled"):
            raise TelemetryError("Local telemetry is disabled")

    def _policy_int(self, key: str, default: int) -> int:
        value = self.features.policy.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise TelemetryError(f"Platform policy {key} must be a positive integer")
        return value


def deterministic_trace_id(task_id: str | None, run_id: str, repository_sha256: str) -> str:
    return derive_id(
        "trace", {"task_id": task_id, "run_id": run_id, "repository": repository_sha256}
    )


def deterministic_span_id(
    trace_id: str,
    operation_name: str,
    sequence: int,
    parent_span_id: str | None,
) -> str:
    if sequence < 1:
        raise ValueError("Span sequence must be positive")
    return derive_id(
        "span",
        {
            "trace_id": trace_id,
            "operation_name": operation_name,
            "sequence": sequence,
            "parent_span_id": parent_span_id,
        },
    )


def _public_trace(record: StoredRecord) -> dict[str, Any]:
    payload = record.payload
    return {
        key: payload.get(key)
        for key in (
            "trace_id",
            "task_id",
            "root_run_id",
            "root_span_id",
            "created_at",
            "completed_at",
            "status",
            "span_count",
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "estimated_cost",
            "actual_cost",
        )
    } | {"revision": record.revision, "record_sha256": record.payload_sha256}


def _public_span(record: StoredRecord) -> dict[str, Any]:
    payload = record.payload
    allowed = (
        "trace_id",
        "span_id",
        "parent_span_id",
        "span_kind",
        "task_id",
        "run_id",
        "employee_id",
        "agent_id",
        "session_id",
        "workspace_id",
        "graph_snapshot_id",
        "knowledge_generation_id",
        "operation_name",
        "started_at",
        "ended_at",
        "duration_ms",
        "status",
        "provider",
        "model",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "estimated_cost",
        "actual_cost",
        "retry_count",
        "tool_name",
        "policy_decision_id",
        "approval_id",
        "error_code",
        "redaction_status",
        "attributes",
    )
    return {key: payload.get(key) for key in allowed} | {
        "revision": record.revision,
        "record_sha256": record.payload_sha256,
    }


def _all_head_records(store: PlatformStore, kind: str) -> tuple[StoredRecord, ...]:
    records: list[StoredRecord] = []
    offset = 0
    while offset <= 10_000:
        page = store.list_heads(kind, limit=500, offset=offset)
        records.extend(page)
        if len(page) < 500:
            break
        offset += len(page)
    return tuple(records)


def _export_identity(
    traces: tuple[StoredRecord, ...],
    spans: tuple[StoredRecord, ...],
) -> tuple[str, str]:
    material = {
        "traces": {record.record_id: record.payload_sha256 for record in traces},
        "spans": {record.record_id: record.payload_sha256 for record in spans},
    }
    artifact_sha256 = sha256_hex(canonical_json_bytes(material))
    return derive_id("otlpexp", {"artifact_sha256": artifact_sha256}), artifact_sha256


def _otlp_span(span: TraceSpan) -> dict[str, Any]:
    started = _unix_nano(span.started_at)
    ended = started if span.ended_at is None else _unix_nano(span.ended_at)
    attributes = [
        {"key": "raytsystem.trace_id", "value": {"stringValue": span.trace_id}},
        {"key": "raytsystem.span_id", "value": {"stringValue": span.span_id}},
        {"key": "raytsystem.span_kind", "value": {"stringValue": span.span_kind.value}},
        {
            "key": "raytsystem.redaction_status",
            "value": {"stringValue": span.redaction_status.value},
        },
    ] + [
        {"key": key, "value": {"stringValue": value}}
        for key, value in sorted(span.attributes.items())
    ]
    return {
        "traceId": _otlp_hex(span.trace_id, 32),
        "spanId": _otlp_hex(span.span_id, 16),
        "parentSpanId": "" if span.parent_span_id is None else _otlp_hex(span.parent_span_id, 16),
        "name": span.operation_name,
        "kind": 1,
        "startTimeUnixNano": str(started),
        "endTimeUnixNano": str(ended),
        "attributes": attributes,
        "status": {"code": _OTLP_STATUS_CODES[span.status]},
    }


def _otlp_hex(identifier: str, length: int) -> str:
    return sha256_hex(identifier.encode("utf-8"))[:length]


def _unix_nano(value: datetime) -> int:
    moment = value.astimezone(UTC)
    return int(moment.timestamp()) * 1_000_000_000 + moment.microsecond * 1_000


def _trace_span_records(store: Any, trace_id: str) -> list[StoredRecord]:
    matches: list[StoredRecord] = []
    offset = 0
    while offset <= 10_000:
        page = store.list_heads("span", limit=500, offset=offset)
        matches.extend(item for item in page if item.payload.get("trace_id") == trace_id)
        if len(page) < 500:
            break
        offset += len(page)
    return matches
