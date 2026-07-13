"""OTel-compatible local traces: redaction, bounds, hierarchy, and gated OTLP export."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from platform_helpers import make_platform_workspace, store_approval
from raytsystem.contracts import TraceRecord, TraceSpan, canonical_json_bytes
from raytsystem.contracts.observability import RedactionStatus, SpanKind, SpanStatus
from raytsystem.platform_store import PlatformStoreError, open_platform_store_read_only
from raytsystem.telemetry import TelemetryError, TraceService
from raytsystem.telemetry.service import deterministic_span_id, deterministic_trace_id

pytestmark = pytest.mark.filterwarnings("error")

_SECRET = "AKIA" + "A" * 16
_REPOSITORY_SHA256 = "0" * 64


def _new_trace(run_id: str = "run_root") -> TraceRecord:
    trace_id = deterministic_trace_id(None, run_id, _REPOSITORY_SHA256)
    return TraceRecord(
        trace_id=trace_id,
        root_run_id=run_id,
        root_span_id=deterministic_span_id(trace_id, "operation_root", 1, None),
        created_at=datetime.now(UTC),
    )


def _span(
    trace: TraceRecord,
    *,
    operation: str = "operation_root",
    sequence: int = 1,
    parent_span_id: str | None = None,
    span_kind: SpanKind = SpanKind.RUN,
    attributes: dict[str, str] | None = None,
    span_id: str | None = None,
    status: SpanStatus = SpanStatus.UNSET,
    ended: bool = False,
) -> TraceSpan:
    started = datetime.now(UTC)
    return TraceSpan(
        trace_id=trace.trace_id,
        span_id=span_id
        or deterministic_span_id(trace.trace_id, operation, sequence, parent_span_id),
        parent_span_id=parent_span_id,
        span_kind=span_kind,
        run_id=trace.root_run_id,
        workspace_id="workspace_test",
        operation_name=operation,
        started_at=started,
        ended_at=started + timedelta(seconds=1) if ended else None,
        duration_ms=1_000 if ended else None,
        status=status,
        attributes=attributes or {},
    )


def _raw_payload_rows(root: Path) -> list[str]:
    connection = sqlite3.connect(root / "ops" / "platform.sqlite")
    try:
        rows = connection.execute("SELECT payload_json FROM records").fetchall()
        rows += connection.execute("SELECT payload_json FROM audit_events").fetchall()
    finally:
        connection.close()
    return [str(row[0]) for row in rows]


def test_secret_attribute_is_redacted_before_persistence(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = TraceService(root)
    trace = service.create_trace(_new_trace(), repository_sha256=_REPOSITORY_SHA256)
    recorded = service.record_span(
        _span(trace, attributes={"note": f"deploy key {_SECRET}"}), sequence=1
    )
    assert recorded.redaction_status is RedactionStatus.REDACTED
    assert _SECRET not in recorded.model_dump_json()
    rows = _raw_payload_rows(root)
    assert rows and all(_SECRET not in row for row in rows)


def test_oversized_span_attributes_are_rejected(tmp_path: Path) -> None:
    root = make_platform_workspace(
        tmp_path,
        policy_overrides={"max_span_attributes": 2, "max_span_attribute_bytes": 64},
    )
    service = TraceService(root)
    trace = service.create_trace(_new_trace())
    too_many = _span(trace, attributes={"a_one": "x", "a_two": "x", "a_three": "x"})
    with pytest.raises(TelemetryError, match="max_span_attributes"):
        service.record_span(too_many)
    too_large = _span(trace, attributes={"note": "v" * 100})
    with pytest.raises(TelemetryError, match="max_span_attribute_bytes"):
        service.record_span(too_large)
    assert service.trace_detail(trace.trace_id) is not None


def test_forged_span_and_event_payloads_are_detected(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = TraceService(root)
    trace = service.create_trace(_new_trace())
    service.record_span(_span(trace))
    connection = sqlite3.connect(root / "ops" / "platform.sqlite")
    try:
        connection.execute(
            "UPDATE records SET payload_json = "
            "replace(payload_json, 'workspace_test', 'workspace_forged') WHERE kind='span'"
        )
        connection.execute(
            "UPDATE audit_events SET payload_json='{\"forged\":true}' WHERE stream_id=?",
            (trace.trace_id,),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(PlatformStoreError, match="hash"):
        service.trace_detail(trace.trace_id)
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        assert store.verify_event_stream(trace.trace_id) is False


def test_child_span_kind_without_parent_is_rejected(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = TraceService(root)
    trace = service.create_trace(_new_trace())
    orphan = _span(trace, span_kind=SpanKind.MODEL)
    with pytest.raises(TelemetryError, match="parent"):
        service.record_span(orphan)


def test_missing_and_cross_trace_parents_are_rejected(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = TraceService(root)
    trace_alpha = service.create_trace(_new_trace("run_alpha"))
    root_alpha = service.record_span(_span(trace_alpha))
    trace_beta = service.create_trace(_new_trace("run_beta"))
    service.record_span(_span(trace_beta))
    missing = _span(
        trace_beta,
        operation="operation_model",
        sequence=2,
        parent_span_id="span_missing",
        span_kind=SpanKind.MODEL,
    )
    with pytest.raises(TelemetryError, match="missing or belongs"):
        service.record_span(missing)
    cross = _span(
        trace_beta,
        operation="operation_model",
        sequence=2,
        parent_span_id=root_alpha.span_id,
        span_kind=SpanKind.MODEL,
    )
    with pytest.raises(TelemetryError, match="another trace"):
        service.record_span(cross)


def test_terminal_spans_are_immutable(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = TraceService(root)
    trace = service.create_trace(_new_trace())
    closed = service.record_span(_span(trace, status=SpanStatus.OK, ended=True))
    reopened = closed.model_copy(update={"attributes": {"note": "rewritten"}})
    with pytest.raises(TelemetryError, match="Terminal"):
        service.record_span(reopened)


def test_deterministic_identity_material_is_validated(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = TraceService(root)
    trace = _new_trace()
    with pytest.raises(TelemetryError, match="deterministic"):
        service.create_trace(trace, repository_sha256="1" * 64)
    service.create_trace(trace, repository_sha256=_REPOSITORY_SHA256)
    span = _span(trace)
    with pytest.raises(TelemetryError, match="deterministic"):
        service.record_span(span, sequence=2)
    service.record_span(span, sequence=1)


def test_telemetry_disabled_fails_closed(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path, flag_overrides={"telemetry_enabled": False})
    service = TraceService(root)
    with pytest.raises(TelemetryError, match="disabled"):
        service.create_trace(_new_trace())
    with pytest.raises(TelemetryError, match="disabled"):
        service.record_span(_span(_new_trace()))
    with pytest.raises(TelemetryError, match="disabled"):
        service.export_fingerprint()
    with pytest.raises(TelemetryError, match="disabled"):
        service.export_otlp(tmp_path / "otlp.json", approval_id="apr_missing")
    assert service.list_traces()["state"] in {"disabled", "unavailable"}


def test_list_and_detail_omit_sensitive_payloads(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = TraceService(root)
    trace = service.create_trace(_new_trace())
    service.record_span(_span(trace, attributes={"prompt": "the hidden prompt body"}))
    listing = service.list_traces()
    assert listing["state"] == "ready"
    assert "hidden prompt body" not in canonical_json_bytes(listing).decode("utf-8")
    detail = service.trace_detail(trace.trace_id)
    assert detail is not None
    assert "hidden prompt body" not in canonical_json_bytes(detail).decode("utf-8")
    span = detail["spans"][0]
    assert span["redaction_status"] == "redacted"
    assert "prompt" not in span["attributes"]
    assert "prompt_sha256" in span["attributes"]


def test_otlp_export_disabled_fails_closed(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path)
    service = TraceService(root)
    trace = service.create_trace(_new_trace())
    service.record_span(_span(trace))
    with pytest.raises(TelemetryError, match="disabled"):
        service.export_otlp(tmp_path / "exports" / "otlp.json", approval_id="apr_missing")
    assert not (tmp_path / "exports" / "otlp.json").exists()


def test_otlp_export_requires_exact_fresh_approval(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path, flag_overrides={"otel_export_enabled": True})
    service = TraceService(root)
    trace = service.create_trace(_new_trace())
    service.record_span(_span(trace))
    destination = tmp_path.resolve() / "exports" / "otlp.json"
    with pytest.raises(TelemetryError, match="authority"):
        service.export_otlp(destination, approval_id="apr_missing")
    fingerprint = service.export_fingerprint()
    wrong_destination = store_approval(
        root,
        action="export_traces",
        target_id=fingerprint["target_id"],
        artifact_sha256=fingerprint["artifact_sha256"],
        scope=("otel_export",),
        destination=str(tmp_path.resolve() / "elsewhere.json"),
    )
    with pytest.raises(TelemetryError, match="authority"):
        service.export_otlp(destination, approval_id=wrong_destination.approval_id)
    wrong_scope = store_approval(
        root,
        action="export_traces",
        target_id=fingerprint["target_id"],
        artifact_sha256=fingerprint["artifact_sha256"],
        scope=("other_scope",),
        destination=str(destination),
    )
    with pytest.raises(TelemetryError, match="authority"):
        service.export_otlp(destination, approval_id=wrong_scope.approval_id)
    stale = store_approval(
        root,
        action="export_traces",
        target_id=fingerprint["target_id"],
        artifact_sha256=fingerprint["artifact_sha256"],
        scope=("otel_export",),
        destination=str(destination),
    )
    service.record_span(
        _span(
            trace,
            operation="operation_late",
            sequence=2,
            parent_span_id=trace.root_span_id,
            span_kind=SpanKind.MODEL,
        )
    )
    with pytest.raises(TelemetryError, match="authority"):
        service.export_otlp(destination, approval_id=stale.approval_id)
    assert not destination.exists()


def test_otlp_export_writes_redacted_local_file_with_audit(tmp_path: Path) -> None:
    root = make_platform_workspace(tmp_path, flag_overrides={"otel_export_enabled": True})
    service = TraceService(root)
    trace = service.create_trace(_new_trace(), repository_sha256=_REPOSITORY_SHA256)
    root_span = service.record_span(_span(trace, attributes={"note": f"deploy key {_SECRET}"}))
    child = _span(
        trace,
        operation="operation_model",
        sequence=2,
        parent_span_id=root_span.span_id,
        span_kind=SpanKind.MODEL,
    )
    service.record_span(child, sequence=2)
    destination = tmp_path.resolve() / "exports" / "otlp.json"
    fingerprint = service.export_fingerprint()
    approval = store_approval(
        root,
        action="export_traces",
        target_id=fingerprint["target_id"],
        artifact_sha256=fingerprint["artifact_sha256"],
        scope=("otel_export",),
        destination=str(destination),
    )
    result = service.export_otlp(
        destination, approval_id=approval.approval_id, actor_id="user_local_test"
    )
    assert destination.is_file()
    text = destination.read_text("utf-8")
    assert _SECRET not in text
    document = json.loads(text)
    spans = document["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(spans) == 2 == result["span_count"]
    assert all(len(item["traceId"]) == 32 and len(item["spanId"]) == 16 for item in spans)
    assert all(
        set(item) >= {"name", "startTimeUnixNano", "endTimeUnixNano", "attributes", "status"}
        for item in spans
    )
    parent_ids = {item["parentSpanId"] for item in spans}
    assert "" in parent_ids and len(parent_ids) == 2
    store = open_platform_store_read_only(root)
    assert store is not None
    with store:
        events = store.list_events(result["export_id"])
        assert [event["event_type"] for event in events] == ["otlp_exported"]
        assert events[0]["actor_id"] == "user_local_test"
        assert events[0]["payload"]["destination"] == str(destination)
        assert store.verify_event_stream(result["export_id"])
    with pytest.raises(TelemetryError, match="already exists"):
        service.export_otlp(destination, approval_id=approval.approval_id)
