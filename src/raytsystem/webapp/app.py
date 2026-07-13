from __future__ import annotations

import asyncio
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from raytsystem.bootstrap.service import BootstrapError, BootstrapService
from raytsystem.catalog import CatalogError, CatalogService, CatalogSnapshot
from raytsystem.codegraph.contracts import CodeNodeKind
from raytsystem.codegraph.projection import CodeGraphProjection, CodeGraphUnavailable
from raytsystem.codegraph.querying import CodeGraphQueryError, CodeGraphQueryService
from raytsystem.contracts import (
    Sensitivity,
    SkillDefinition,
    TaskStatus,
    derive_id,
    sha256_hex,
)
from raytsystem.contracts.execution import (
    CommentKind,
    ExecutionComment,
    ExecutionRun,
    ExecutionRunStatus,
    ExecutionSessionStatus,
    TaskAssignment,
    TaskWorkspace,
    WorkspaceStatus,
)
from raytsystem.corpus import CorpusIntegrityError
from raytsystem.execution.config import ExecutionConfigError
from raytsystem.execution.service import (
    ExecutionAssignmentError,
    ExecutionConcurrencyError,
    ExecutionService,
    ExecutionServiceError,
)
from raytsystem.execution.store import ExecutionStoreConflict, ExecutionStoreError
from raytsystem.platform_store import (
    PlatformStoreError,
    open_platform_store_read_only,
)
from raytsystem.readmodel import ReadModelError
from raytsystem.skill_authoring import (
    PINNED_SKILL_POLICY_UNKNOWN,
    SkillAuthoringError,
    SkillAuthoringService,
    active_pinned_skill_ids,
)
from raytsystem.storage import IntegrityError
from raytsystem.tasking import (
    TaskConflict,
    TaskLedgerError,
    TaskService,
    TaskTransitionRejected,
)
from raytsystem.universe import UniverseError, graph_logical_sha256
from raytsystem.webapp.dto import (
    CodeGraphImpactRequest,
    CodeGraphMutationRequest,
    CodeGraphNodeRequest,
    CodeGraphPathRequest,
    CodeGraphQueryRequest,
    ExecutionAssignmentRequest,
    ExecutionCommentRequest,
    ExecutionHeartbeatRequest,
    ExecutionResumeRequest,
    ExecutionRunControlRequest,
    ExecutionWorkspaceRequest,
    OnboardingApplyRequest,
    OnboardingUninstallRequest,
    SkillForkPreviewRequest,
    SkillForkRequest,
    SkillSaveRequest,
    TaskCreateRequest,
    TaskTransitionRequest,
)
from raytsystem.webapp.execution_views import ExecutionViewProvider
from raytsystem.webapp.handbook import HandbookError, HandbookService
from raytsystem.webapp.security import (
    SESSION_COOKIE,
    SecurityMiddleware,
    SessionRecord,
    SessionStore,
)
from raytsystem.webapp.snapshot import ReadSnapshot, SnapshotError, SnapshotProvider

_PUBLIC_ID = re.compile(r"^[a-z][a-z0-9_:.@-]{1,255}$")
_SNAPSHOT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_:.@-]{0,255}$")
_KNOWLEDGE_KINDS = frozenset({"generation", "claim", "entity", "source", "evidence"})
_TASK_KINDS = frozenset({"task", "task_generation"})
_CATALOG_KINDS = frozenset({"pack", "agent", "skill", "instruction", "adapter"})
_CODE_KINDS = frozenset(kind.value for kind in CodeNodeKind)
_PUBLIC_CODE_METADATA = frozenset(
    {
        "candidate_count",
        "configuration",
        "external",
        "handler",
        "language",
        "method",
        "module",
        "reference_kind",
        "resolution_key",
        "route",
        "symbol",
    }
)
_SPA_ROUTES = frozenset(
    {
        "command-center",
        "handbook",
        "documents",
        "onboarding",
        "tasks",
        "universe",
        "runs",
        "agents",
        "skills",
        "context",
        "safety",
        "systems",
    }
)


def _api_error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
    )


def _safe_public_id(value: str) -> str:
    if _PUBLIC_ID.fullmatch(value) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Object was not found."},
        )
    return value


def _truncate(value: str, limit: int = 320) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _require_snapshot(expected: str, actual: str | None) -> None:
    if _SNAPSHOT_ID.fullmatch(expected) is None or actual is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "snapshot_stale", "message": "The selected snapshot changed."},
        )
    if not secrets.compare_digest(expected, actual):
        raise HTTPException(
            status_code=409,
            detail={"code": "snapshot_stale", "message": "The selected snapshot changed."},
        )


def _node_snapshot_id(kind: str, snapshot: ReadSnapshot) -> str:
    if kind in _KNOWLEDGE_KINDS:
        return snapshot.corpus.generation.generation_id
    if kind in _TASK_KINDS and snapshot.tasks.generation_id is not None:
        return snapshot.tasks.generation_id
    if kind in _CATALOG_KINDS:
        return snapshot.catalog.catalog_sha256
    if kind in _CODE_KINDS and snapshot.code is not None:
        return snapshot.code.snapshot_id
    return snapshot.graph.graph_snapshot_id


def _require_code_snapshot(snapshot: ReadSnapshot, expected: str) -> None:
    _require_snapshot(
        expected,
        None if snapshot.code is None else snapshot.code.snapshot_id,
    )


def _skill_source_path(skill_id: str) -> str:
    """Build the only skill source path exposed or accepted by the web surface."""

    return f"skills/{skill_id}/SKILL.md"


def _active_pinned_skill_ids(root: Path) -> frozenset[str]:
    """Resolve active package manifests to their exact pinned skill identities."""

    store = open_platform_store_read_only(root)
    if store is None:
        return frozenset()
    try:
        with store:
            return active_pinned_skill_ids(store)
    except (OSError, PlatformStoreError, ValueError):
        return frozenset({PINNED_SKILL_POLICY_UNKNOWN})


def _skill_policy(
    skill: SkillDefinition,
    *,
    pinned_skill_ids: frozenset[str],
) -> dict[str, Any]:
    """Apply the authoring service policy to one body-free catalog definition."""

    return SkillAuthoringService.policy_for_definition(
        skill,
        pinned_skill_ids=pinned_skill_ids,
    )


def _related_agents_by_skill(snapshot: CatalogSnapshot) -> dict[str, list[dict[str, str]]]:
    related: dict[str, list[dict[str, str]]] = {skill.skill_id: [] for skill in snapshot.skills}
    for agent in snapshot.agents:
        public_agent = {
            "agent_id": agent.agent_id,
            "name": agent.name,
            # The UI localizes this stable role source; canonical agent names stay untouched.
            "role": agent.role,
        }
        for skill_id in agent.skill_ids:
            if skill_id in related:
                related[skill_id].append(public_agent)
    return related


def _empty_skill_history(availability: str) -> dict[str, Any]:
    return {
        "availability": availability,
        "revisions": [],
        "audit_events": [],
        "current_revision_only": True,
        "truncated": False,
    }


def _public_history_id(value: object, *, fallback: str | None = None) -> str | None:
    if isinstance(value, str) and _PUBLIC_ID.fullmatch(value) is not None:
        return value
    return fallback


def _public_history_actor(value: object) -> str:
    """Map stored actor tokens to bounded provenance classes, never raw user input."""

    if isinstance(value, str) and value.startswith("user_"):
        return "local_user"
    if isinstance(value, str) and value.startswith("raytsystem_"):
        return "raytsystem_system"
    return "redacted"


def _skill_history(root: Path, skill_id: str) -> dict[str, Any]:
    """Return allowlisted authoring metadata only; never revision bodies or arbitrary paths."""

    store = open_platform_store_read_only(root)
    if store is None:
        database = root / "ops" / "platform.sqlite"
        return _empty_skill_history(
            "unavailable" if database.exists() or database.is_symlink() else "not_initialized"
        )
    revision_keys = (
        "skill_revision_id",
        "operation",
        "source_skill_id",
        "skill_id",
        "source_sha256",
        "previous_source_sha256",
        "catalog_sha256",
        "previous_catalog_sha256",
        "previous_revision_sha256",
        "test_status",
        "trust_class",
        "pack_id",
        "changed_at",
        "validation_sha256",
        "diff_sha256",
    )
    audit_payload_keys = (
        "skill_revision_id",
        "record_revision",
        "source_sha256",
        "previous_source_sha256",
        "catalog_sha256",
        "operation",
        "idempotency_key_sha256",
    )
    try:
        with store:
            head = store.head("skill_authoring_revision", skill_id)
            events = store.list_events(f"skill_authoring_{skill_id}", limit=200)
    except (OSError, PlatformStoreError, ValueError):
        return _empty_skill_history("unavailable")
    revisions: list[dict[str, Any]] = []
    if head is not None:
        revision_payload = {key: head.payload.get(key) for key in revision_keys}
        revision_payload["skill_id"] = skill_id
        revision_payload["source_skill_id"] = _public_history_id(
            revision_payload.get("source_skill_id"),
            fallback=skill_id,
        )
        revisions.append(
            {
                "record_revision": head.revision,
                "record_state": head.state if head.state == "pending" else "unknown",
                "recorded_at": head.created_at,
                "record_sha256": head.payload_sha256,
                **revision_payload,
                "source_path": _skill_source_path(skill_id),
            }
        )
    public_events = [
        {
            "event_id": event["event_id"],
            "sequence": event["sequence"],
            "event_type": (
                event["event_type"]
                if event["event_type"] in {"skill_saved", "skill_forked"}
                else "unknown"
            ),
            "actor_id": _public_history_actor(event["actor_id"]),
            "recorded_at": event["recorded_at"],
            "payload_sha256": event["payload_sha256"],
            "previous_event_sha256": event["previous_event_sha256"],
            "payload": {
                key: event["payload"].get(key)
                for key in audit_payload_keys
                if key in event["payload"]
            },
        }
        for event in events
    ]
    return {
        "availability": "available",
        "revisions": revisions,
        "audit_events": public_events,
        "current_revision_only": True,
        "truncated": len(events) >= 200,
    }


def _skill_authoring_service(root: Path) -> SkillAuthoringService:
    return SkillAuthoringService(root, pinned_skill_ids=_active_pinned_skill_ids(root))


def require_session(request: Request) -> SessionRecord:
    sessions = cast(SessionStore, request.app.state.sessions)
    record = sessions.get(request.cookies.get(SESSION_COOKIE))
    if record is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "session_required", "message": "Reopen the local interface."},
        )
    return record


def read_snapshot(
    request: Request,
    _session: Annotated[SessionRecord, Depends(require_session)],
) -> ReadSnapshot:
    provider = cast(SnapshotProvider, request.app.state.snapshots)
    return provider.get()


def create_app(
    root: Path,
    *,
    allowed_hosts: frozenset[str] | None = None,
    allowed_origins: frozenset[str] | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    resolved_root = root.resolve()
    sessions = SessionStore()
    skill_authoring = _skill_authoring_service(resolved_root)
    skill_authoring.recover_pending()
    provider = SnapshotProvider(
        resolved_root,
        catalog_read_guard=skill_authoring.catalog_read_guard,
    )
    execution_service: ExecutionService | None = None
    execution_service_lock = asyncio.Lock()
    app = FastAPI(
        title="raytsystem local control plane",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    execution_views = ExecutionViewProvider(
        resolved_root,
        store_provider=lambda: None if execution_service is None else execution_service.store,
        catalog_read_guard=skill_authoring.catalog_read_guard,
    )
    app.state.root = resolved_root
    app.state.sessions = sessions
    app.state.snapshots = provider
    app.state.skill_authoring = skill_authoring
    app.state.execution_views = execution_views
    app.state.execution_service = None

    async def mutable_execution_service() -> ExecutionService:
        nonlocal execution_service
        async with execution_service_lock:
            if execution_service is None:
                execution_service = ExecutionService(
                    resolved_root,
                    catalog_read_guard=skill_authoring.catalog_read_guard,
                )
                app.state.execution_service = execution_service
            return execution_service

    async def close_execution_service() -> None:
        nonlocal execution_service
        async with execution_service_lock:
            if execution_service is not None:
                execution_service.close()
                execution_service = None
                app.state.execution_service = None

    app.router.add_event_handler("shutdown", close_execution_service)
    host_allowlist = allowed_hosts or frozenset(
        {"127.0.0.1", "127.0.0.1:8765", "localhost", "localhost:8765"}
    )
    origin_allowlist = allowed_origins or frozenset(
        {"http://127.0.0.1:8765", "http://localhost:8765"}
    )
    app.add_middleware(
        SecurityMiddleware,
        sessions=sessions,
        allowed_hosts=host_allowlist,
        allowed_origins=origin_allowlist,
    )

    selected_static = static_dir or Path(__file__).parent / "static"
    assets = selected_static / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return _api_error(422, "request_invalid", "Request fields are invalid.")

    @app.exception_handler(SkillAuthoringError)
    async def skill_authoring_error_handler(
        _request: Request,
        error: SkillAuthoringError,
    ) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content=error.to_dict())

    @app.exception_handler(TaskConflict)
    async def task_conflict_handler(_request: Request, _error: TaskConflict) -> JSONResponse:
        return _api_error(
            409,
            "task_conflict",
            "The task board changed. Refresh it before retrying the command.",
        )

    @app.exception_handler(TaskTransitionRejected)
    async def task_rejected_handler(
        _request: Request,
        _error: TaskTransitionRejected,
    ) -> JSONResponse:
        return _api_error(
            422,
            "task_rejected",
            "The task command violates the current state or safety policy.",
        )

    @app.exception_handler(CodeGraphQueryError)
    async def code_graph_query_error_handler(
        _request: Request,
        _error: CodeGraphQueryError,
    ) -> JSONResponse:
        return _api_error(
            422,
            "code_graph_query_rejected",
            "The graph request is invalid, ambiguous or outside its response budget.",
        )

    @app.exception_handler(CodeGraphUnavailable)
    async def code_graph_unavailable_handler(
        _request: Request,
        _error: CodeGraphUnavailable,
    ) -> JSONResponse:
        return _api_error(
            409,
            "code_graph_unavailable",
            "The code graph is missing, stale or being refreshed.",
        )

    @app.exception_handler(ExecutionConfigError)
    async def execution_config_error_handler(
        _request: Request,
        _error: ExecutionConfigError,
    ) -> JSONResponse:
        return _api_error(
            503,
            "execution_configuration_invalid",
            "The execution plane configuration is unavailable or invalid.",
        )

    @app.exception_handler(ExecutionStoreError)
    async def execution_store_error_handler(
        _request: Request,
        _error: ExecutionStoreError,
    ) -> JSONResponse:
        return _api_error(
            503,
            "execution_state_unavailable",
            "Verified execution state is unavailable. Run the documented recovery checks.",
        )

    @app.exception_handler(ExecutionStoreConflict)
    async def execution_store_conflict_handler(
        _request: Request,
        _error: ExecutionStoreConflict,
    ) -> JSONResponse:
        return _api_error(
            409,
            "execution_conflict",
            "Execution state changed or the idempotency binding does not match.",
        )

    @app.exception_handler(ExecutionServiceError)
    async def execution_service_error_handler(
        _request: Request,
        error: ExecutionServiceError,
    ) -> JSONResponse:
        if isinstance(error, ExecutionConcurrencyError):
            code = "execution_busy"
        elif isinstance(error, ExecutionAssignmentError):
            code = "assignment_rejected"
        else:
            code = "execution_rejected"
        return _api_error(
            409,
            code,
            "The execution command is stale, unavailable, or denied by policy.",
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(_request: Request, error: HTTPException) -> JSONResponse:
        if isinstance(error.detail, dict):
            code = str(error.detail.get("code", "request_failed"))
            message = str(error.detail.get("message", "Request failed."))
        else:
            code = "request_failed"
            message = "Request failed."
        return _api_error(error.status_code, code, message)

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(_request: Request, error: IntegrityError) -> JSONResponse:
        if isinstance(error, CatalogError):
            code = "catalog_integrity_failed"
        elif isinstance(error, (ReadModelError, UniverseError, SnapshotError)):
            code = "snapshot_integrity_failed"
        elif isinstance(error, (CorpusIntegrityError, TaskLedgerError)):
            code = "integrity_failed"
        else:
            code = "integrity_failed"
        return _api_error(
            503,
            code,
            "Verified local state is unavailable. Run the documented integrity checks.",
        )

    @app.exception_handler(Exception)
    async def internal_error_handler(_request: Request, _error: Exception) -> JSONResponse:
        return _api_error(500, "internal_error", "The local control plane could not respond.")

    @app.get("/api/v1/session")
    def session_info(
        session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        return {
            "csrf_token": session.csrf_token,
            "expires_at_epoch": round(session.expires_at),
            "local_only": True,
        }

    @app.get("/api/v1/system")
    async def system_snapshot(
        snapshot: Annotated[ReadSnapshot, Depends(read_snapshot)],
    ) -> dict[str, Any]:
        execution_features = execution_views.features()
        task_counts = {status.value: 0 for status in TaskStatus}
        for task in snapshot.tasks.tasks:
            task_counts[task.status.value] += 1
        failed_states = {"retryable_failed", "terminal_failed", "quarantined"}
        attention = {
            "blocked_tasks": sum(
                1 for task in snapshot.tasks.tasks if task.status is TaskStatus.BLOCKED
            ),
            "failed_runs": sum(1 for run in snapshot.runs if run.state in failed_states),
            "restricted_skills": sum(1 for skill in snapshot.catalog.skills if not skill.enabled),
        }
        fingerprint = {
            "knowledge_generation_id": snapshot.corpus.generation.generation_id,
            "knowledge_generation_sha256": snapshot.corpus.generation_sha256,
            "task_generation_id": snapshot.tasks.generation_id,
            "task_generation_sha256": snapshot.tasks.generation_sha256,
            "catalog_sha256": snapshot.catalog.catalog_sha256,
            "graph_snapshot_id": snapshot.graph.graph_snapshot_id,
            "graph_sha256": graph_logical_sha256(snapshot.graph),
            "code_snapshot_id": snapshot.graph.code_snapshot_id,
            "code_snapshot_sha256": snapshot.graph.code_snapshot_sha256,
            "code_snapshot_fingerprint": snapshot.graph.code_snapshot_fingerprint,
            "code_graph_state": snapshot.graph.code_graph_state,
            "execution_feature_snapshot_id": execution_features["snapshot_id"],
        }
        return {
            "snapshot_id": derive_id("view", fingerprint),
            "loaded_at": snapshot.loaded_at.isoformat(),
            "fingerprint": fingerprint,
            "counts": {
                "claims": len(snapshot.corpus.claims),
                "entities": len(snapshot.corpus.entities),
                "sources": len(snapshot.corpus.sources),
                "evidence": len(snapshot.corpus.evidence),
                "runs": len(snapshot.runs),
                "tasks": task_counts,
                "agents": len(snapshot.catalog.agents),
                "skills": len(snapshot.catalog.skills),
                "adapters": len(snapshot.catalog.adapters),
                "code_files": snapshot.graph.code_file_count,
                "code_nodes": snapshot.graph.code_node_count,
                "code_edges": snapshot.graph.code_edge_count,
                "code_ambiguous_edges": snapshot.graph.code_ambiguous_edges,
            },
            "attention": attention,
            "safety": {
                "binding": "loopback_only",
                "workspace": "pinned_at_start",
                "session": "http_only_same_site_strict",
                "csrf": "required_for_writes",
                "runtime_execution": (
                    "enabled"
                    if execution_features["features"]["runtime_execution_enabled"]
                    else "disabled"
                ),
                "scheduled_heartbeats": (
                    "enabled"
                    if execution_features["features"]["scheduled_heartbeats_enabled"]
                    else "disabled"
                ),
                "external_mutations": "not_available",
                "canonical_knowledge_writes": "not_available_over_http",
            },
        }

    @app.get("/api/v1/handbook")
    def handbook_tree(
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        return HandbookService().tree()

    @app.get("/api/v1/handbook/article")
    def handbook_article(
        _session: Annotated[SessionRecord, Depends(require_session)],
        slug: Annotated[str, Query(max_length=256)] = "/",
    ) -> dict[str, Any]:
        try:
            return HandbookService().article(slug)
        except HandbookError as error:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "handbook_article_not_found",
                    "message": "Статья базы знаний не найдена.",
                },
            ) from error

    @app.get("/api/v1/execution/features")
    async def execution_feature_snapshot(
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        return execution_views.features()

    @app.get("/api/v1/agents")
    async def unified_agents(
        _session: Annotated[SessionRecord, Depends(require_session)],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> dict[str, Any]:
        return execution_views.agents(limit=limit, offset=offset)

    @app.get("/api/v1/agents/{agent_id}")
    async def unified_agent_detail(
        agent_id: str,
        _session: Annotated[SessionRecord, Depends(require_session)],
        expected: Annotated[str, Query(min_length=1, max_length=256)],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        safe_id = _safe_public_id(agent_id)
        try:
            detail = execution_views.agent_detail(safe_id, limit=limit)
        except ValueError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "agent_not_found", "message": "Agent was not found."},
            ) from error
        _require_snapshot(expected, str(detail["catalog_sha256"]))
        return detail

    @app.get("/api/v1/employees")
    @app.get("/api/v1/execution/employees")
    async def employee_snapshot(
        _session: Annotated[SessionRecord, Depends(require_session)],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> dict[str, Any]:
        return execution_views.employees(limit=limit, offset=offset)

    @app.get("/api/v1/execution/assignments")
    async def execution_assignments(
        _session: Annotated[SessionRecord, Depends(require_session)],
        task_id: Annotated[str | None, Query(max_length=256)] = None,
        employee_id: Annotated[str | None, Query(max_length=256)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> dict[str, Any]:
        return execution_views.assignments(
            task_id=None if task_id is None else _safe_public_id(task_id),
            employee_id=None if employee_id is None else _safe_public_id(employee_id),
            limit=limit,
            offset=offset,
        )

    @app.get("/api/v1/execution/workspaces")
    async def execution_workspaces(
        _session: Annotated[SessionRecord, Depends(require_session)],
        task_id: Annotated[str | None, Query(max_length=256)] = None,
        status: WorkspaceStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> dict[str, Any]:
        return execution_views.workspaces(
            task_id=None if task_id is None else _safe_public_id(task_id),
            status=status,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/v1/execution/graph-scopes")
    async def execution_graph_scopes(
        _session: Annotated[SessionRecord, Depends(require_session)],
        task_id: Annotated[str | None, Query(max_length=256)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> dict[str, Any]:
        return execution_views.graph_scopes(
            task_id=None if task_id is None else _safe_public_id(task_id),
            limit=limit,
            offset=offset,
        )

    @app.get("/api/v1/execution/runs")
    async def execution_runs(
        _session: Annotated[SessionRecord, Depends(require_session)],
        task_id: Annotated[str | None, Query(max_length=256)] = None,
        employee_id: Annotated[str | None, Query(max_length=256)] = None,
        status: ExecutionRunStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> dict[str, Any]:
        return execution_views.runs(
            task_id=None if task_id is None else _safe_public_id(task_id),
            employee_id=None if employee_id is None else _safe_public_id(employee_id),
            status=status,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/v1/execution/sessions")
    async def execution_sessions(
        _session: Annotated[SessionRecord, Depends(require_session)],
        task_id: Annotated[str | None, Query(max_length=256)] = None,
        employee_id: Annotated[str | None, Query(max_length=256)] = None,
        status: ExecutionSessionStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> dict[str, Any]:
        return execution_views.sessions(
            task_id=None if task_id is None else _safe_public_id(task_id),
            employee_id=None if employee_id is None else _safe_public_id(employee_id),
            status=status,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/v1/execution/budgets")
    async def execution_budgets(
        _session: Annotated[SessionRecord, Depends(require_session)],
        scope_id: Annotated[str | None, Query(max_length=256)] = None,
        scope_kind: Annotated[str | None, Query(max_length=256)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> dict[str, Any]:
        return execution_views.budgets(
            scope_id=None if scope_id is None else _safe_public_id(scope_id),
            scope_kind=None if scope_kind is None else _safe_public_id(scope_kind),
            limit=limit,
            offset=offset,
        )

    @app.get("/api/v1/execution/approvals")
    async def execution_approvals(
        _session: Annotated[SessionRecord, Depends(require_session)],
        task_id: Annotated[str | None, Query(max_length=256)] = None,
        employee_id: Annotated[str | None, Query(max_length=256)] = None,
        run_id: Annotated[str | None, Query(max_length=256)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> dict[str, Any]:
        return execution_views.approvals(
            task_id=None if task_id is None else _safe_public_id(task_id),
            employee_id=None if employee_id is None else _safe_public_id(employee_id),
            run_id=None if run_id is None else _safe_public_id(run_id),
            limit=limit,
            offset=offset,
        )

    @app.get("/api/v1/execution/comments")
    async def execution_comments(
        _session: Annotated[SessionRecord, Depends(require_session)],
        task_id: Annotated[str | None, Query(max_length=256)] = None,
        run_id: Annotated[str | None, Query(max_length=256)] = None,
        kind: CommentKind | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    ) -> dict[str, Any]:
        return execution_views.comments(
            task_id=None if task_id is None else _safe_public_id(task_id),
            run_id=None if run_id is None else _safe_public_id(run_id),
            kind=kind,
            limit=limit,
            offset=offset,
        )

    @app.get("/api/v1/execution/tasks/{task_id}")
    async def execution_task_detail(
        task_id: str,
        _session: Annotated[SessionRecord, Depends(require_session)],
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return execution_views.task_detail(_safe_public_id(task_id), limit=limit)

    @app.get("/api/v1/execution/runs/{run_id}/transcript")
    async def execution_transcript(
        run_id: str,
        _session: Annotated[SessionRecord, Depends(require_session)],
        after_sequence: Annotated[int, Query(ge=-1, le=10_000_000)] = -1,
        limit: Annotated[int, Query(ge=1, le=1_000)] = 250,
    ) -> dict[str, Any]:
        return execution_views.transcript(
            _safe_public_id(run_id),
            after_sequence=after_sequence,
            limit=limit,
        )

    @app.get("/api/v1/execution/runtime-health")
    async def execution_runtime_health(
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        return execution_views.runtime_health()

    def require_task_generation(expected: str) -> None:
        _require_snapshot(expected, TaskService(resolved_root).snapshot().generation_id)

    def public_run_action(run: ExecutionRun, *, no_op: bool = False) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "task_id": run.task_id,
            "employee_id": run.employee_id,
            "workspace_id": run.workspace_id,
            "status": run.status.value,
            "error_code": run.error_code,
            "no_op": no_op,
        }

    @app.post("/api/v1/tasks/{task_id}/execution/assignments")
    async def assign_execution_employee(
        task_id: str,
        payload: ExecutionAssignmentRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        safe_task_id = _safe_public_id(task_id)
        require_task_generation(payload.expected_task_generation_id)
        service = await mutable_execution_service()
        result = service.assign_task(
            task_id=safe_task_id,
            employee_id=payload.employee_id,
            expected_generation_id=payload.expected_task_generation_id,
            idempotency_key=idempotency_key,
            budget_policy_id=payload.budget_policy_id,
            approval_policy_id=payload.approval_policy_id,
        )
        provider.invalidate()
        return {
            "assignment_id": result.assignment.assignment_id,
            "task_id": result.assignment.task_id,
            "employee_id": result.assignment.employee_id,
            "runtime_adapter_id": result.assignment.runtime_adapter_id,
            "task_generation_id": result.assignment.task_generation_id,
            "task_revision": result.assignment.task_revision,
            "revision": result.assignment.revision,
            "no_op": result.no_op,
        }

    @app.post("/api/v1/execution/assignments/{assignment_id}/workspace")
    async def prepare_execution_workspace(
        assignment_id: str,
        payload: ExecutionWorkspaceRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        safe_assignment_id = _safe_public_id(assignment_id)
        require_task_generation(payload.expected_task_generation_id)
        service = await mutable_execution_service()
        assignment = service.store.get(TaskAssignment, safe_assignment_id)
        if assignment is None:
            raise HTTPException(
                404,
                detail={"code": "assignment_not_found", "message": "Assignment was not found."},
            )
        if assignment.task_generation_id != payload.expected_task_generation_id:
            raise HTTPException(
                409,
                detail={"code": "assignment_stale", "message": "Assignment is stale."},
            )
        request = payload.model_dump(mode="json") | {"assignment_id": safe_assignment_id}
        key = derive_id("idem", {"scope": "workspace_prepare", "value": idempotency_key})
        receipt = service.store.receipt(
            scope="workspace_prepare",
            idempotency_key=key,
            request=request,
        )
        if receipt is not None:
            workspace_id = receipt.get("workspace_id")
            graph_scope_id = receipt.get("graph_scope_id")
            if not isinstance(workspace_id, str) or not isinstance(graph_scope_id, str):
                raise ExecutionStoreError("Workspace receipt is malformed")
            workspace = service.store.get(TaskWorkspace, workspace_id)
            if workspace is None:
                raise ExecutionStoreError("Workspace receipt points to missing state")
            return {
                "assignment_id": safe_assignment_id,
                "workspace_id": workspace.workspace_id,
                "graph_scope_id": graph_scope_id,
                "graph_snapshot_id": workspace.graph_snapshot_id,
                "manifest_sha256": workspace.manifest_sha256,
                "status": workspace.status.value,
                "no_op": True,
            }
        run_id = derive_id(
            "xrun",
            {
                "scope": "workspace_prepare",
                "assignment_id": safe_assignment_id,
                "idempotency_key": key,
            },
        )
        prepared = service.prepare_workspace(safe_assignment_id, run_id=run_id)
        workspace = prepared.preparation.workspace
        graph_scope = prepared.preparation.graph_scope
        service.store.store_receipt(
            scope="workspace_prepare",
            idempotency_key=key,
            request=request,
            receipt={
                "workspace_id": workspace.workspace_id,
                "graph_scope_id": graph_scope.graph_scope_id,
            },
        )
        return {
            "assignment_id": safe_assignment_id,
            "workspace_id": workspace.workspace_id,
            "graph_scope_id": graph_scope.graph_scope_id,
            "graph_snapshot_id": workspace.graph_snapshot_id,
            "manifest_sha256": workspace.manifest_sha256,
            "status": workspace.status.value,
            "no_op": prepared.preparation.no_op,
        }

    @app.post("/api/v1/execution/assignments/{assignment_id}/heartbeat")
    async def run_execution_heartbeat(
        assignment_id: str,
        payload: ExecutionHeartbeatRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        safe_assignment_id = _safe_public_id(assignment_id)
        require_task_generation(payload.expected_task_generation_id)
        service = await mutable_execution_service()
        result = await service.manual_heartbeat(
            assignment_id=safe_assignment_id,
            idempotency_key=idempotency_key,
            approval_id=payload.approval_id,
        )
        provider.invalidate()
        return result.receipt() | {
            "approval_required": result.approval_required,
            "no_op": result.no_op,
        }

    @app.post("/api/v1/execution/runs/{run_id}/pause")
    async def pause_execution_run(
        run_id: str,
        payload: ExecutionRunControlRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        return await control_execution_run(
            _safe_public_id(run_id),
            payload,
            idempotency_key=idempotency_key,
            action="pause",
        )

    @app.post("/api/v1/execution/runs/{run_id}/cancel")
    async def cancel_execution_run(
        run_id: str,
        payload: ExecutionRunControlRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        return await control_execution_run(
            _safe_public_id(run_id),
            payload,
            idempotency_key=idempotency_key,
            action="cancel",
        )

    async def control_execution_run(
        run_id: str,
        payload: ExecutionRunControlRequest,
        *,
        idempotency_key: str,
        action: str,
    ) -> dict[str, Any]:
        service = await mutable_execution_service()
        request = payload.model_dump(mode="json") | {"run_id": run_id, "action": action}
        key = derive_id("idem", {"scope": f"run_{action}", "value": idempotency_key})
        receipt = service.store.receipt(
            scope=f"run_{action}",
            idempotency_key=key,
            request=request,
        )
        if receipt is not None:
            current = service.store.get(ExecutionRun, run_id)
            if current is None:
                raise ExecutionStoreError("Run control receipt points to missing state")
            return public_run_action(current, no_op=True)
        current = service.store.get(ExecutionRun, run_id)
        if current is None:
            raise HTTPException(
                404,
                detail={"code": "execution_run_not_found", "message": "Run was not found."},
            )
        if current.status is not payload.expected_status:
            raise HTTPException(
                409,
                detail={"code": "run_stale", "message": "Run status changed."},
            )
        changed = (
            await service.pause_run(run_id)
            if action == "pause"
            else await service.cancel_run(run_id)
        )
        service.store.store_receipt(
            scope=f"run_{action}",
            idempotency_key=key,
            request=request,
            receipt={"run_id": run_id},
        )
        provider.invalidate()
        return public_run_action(changed)

    @app.post("/api/v1/execution/runs/{run_id}/resume")
    async def resume_execution_run(
        run_id: str,
        payload: ExecutionResumeRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        safe_run_id = _safe_public_id(run_id)
        require_task_generation(payload.expected_task_generation_id)
        service = await mutable_execution_service()
        previous = service.store.get(ExecutionRun, safe_run_id)
        if previous is None:
            raise HTTPException(
                404,
                detail={"code": "execution_run_not_found", "message": "Run was not found."},
            )
        if previous.status.value != payload.expected_status:
            raise HTTPException(
                409,
                detail={"code": "run_stale", "message": "Run status changed."},
            )
        result = await service.resume_run(
            safe_run_id,
            idempotency_key=idempotency_key,
            approval_id=payload.approval_id,
        )
        provider.invalidate()
        return result.receipt() | {
            "approval_required": result.approval_required,
            "no_op": result.no_op,
        }

    @app.post("/api/v1/tasks/{task_id}/execution/comments")
    async def create_execution_comment(
        task_id: str,
        payload: ExecutionCommentRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        safe_task_id = _safe_public_id(task_id)
        task_snapshot = TaskService(resolved_root).snapshot()
        if not any(task.task_id == safe_task_id for task in task_snapshot.tasks):
            raise HTTPException(
                404,
                detail={"code": "task_not_found", "message": "Task was not found."},
            )
        service = await mutable_execution_service()
        request = payload.model_dump(mode="json") | {"task_id": safe_task_id}
        key = derive_id("idem", {"scope": "execution_comment", "value": idempotency_key})
        receipt = service.store.receipt(
            scope="execution_comment",
            idempotency_key=key,
            request=request,
        )
        if receipt is not None:
            comment_id = receipt.get("comment_id")
            if not isinstance(comment_id, str):
                raise ExecutionStoreError("Comment receipt is malformed")
            return {"comment_id": comment_id, "task_id": safe_task_id, "no_op": True}
        created_at = datetime.now(UTC)
        seed = ExecutionComment(
            comment_id="comment_pending",
            task_id=safe_task_id,
            kind=payload.kind,
            actor="user:local-web",
            run_id=payload.run_id,
            body=payload.body,
            created_at=created_at,
        )
        comment = seed.model_copy(
            update={"comment_id": derive_id("comment", seed.identity_payload())}
        )
        service.store.put(comment, expected_revision=None)
        service.store.store_receipt(
            scope="execution_comment",
            idempotency_key=key,
            request=request,
            receipt={"comment_id": comment.comment_id},
        )
        return {"comment_id": comment.comment_id, "task_id": safe_task_id, "no_op": False}

    @app.get("/api/v1/tasks")
    def list_tasks(
        snapshot: Annotated[ReadSnapshot, Depends(read_snapshot)],
    ) -> dict[str, Any]:
        return snapshot.tasks.to_dict()

    @app.get("/api/v1/tasks/{task_id}")
    def task_detail(
        task_id: str,
        snapshot: Annotated[ReadSnapshot, Depends(read_snapshot)],
        expected: Annotated[str, Query(min_length=1, max_length=256)],
    ) -> dict[str, Any]:
        safe_id = _safe_public_id(task_id)
        _require_snapshot(expected, snapshot.tasks.generation_id)
        service = TaskService(resolved_root)
        task = next((item for item in snapshot.tasks.tasks if item.task_id == safe_id), None)
        if task is None:
            raise HTTPException(
                404,
                detail={"code": "task_not_found", "message": "Task was not found."},
            )
        history = service.history(
            safe_id,
            expected_generation_id=snapshot.tasks.generation_id,
        )
        return {
            "generation_id": snapshot.tasks.generation_id,
            "generation_sha256": snapshot.tasks.generation_sha256,
            "task": task.model_dump(mode="json"),
            "history": [
                {
                    "event_id": event.event_id,
                    "event_kind": event.event_kind.value,
                    "from_status": (None if event.from_status is None else event.from_status.value),
                    "to_status": event.to_status.value,
                    "actor": event.actor,
                    "created_at": event.created_at.isoformat(),
                    "task_sha256": event.task_sha256,
                }
                for event in history
            ],
        }

    @app.post("/api/v1/tasks")
    def create_task(
        payload: TaskCreateRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> JSONResponse:
        result = TaskService(resolved_root).create_task(
            title=payload.title,
            description=payload.description,
            priority=payload.priority,
            project_id=payload.project_id,
            mission_id=payload.mission_id,
            assignee_ids=payload.assignee_ids,
            skill_ids=payload.skill_ids,
            dependency_ids=payload.dependency_ids,
            tags=payload.tags,
            actor="user:local-web",
            idempotency_key=idempotency_key,
            expected_generation_id=payload.expected_generation_id,
        )
        provider.invalidate()
        return JSONResponse(
            status_code=200 if result.no_op else 201,
            content=result.to_dict(),
        )

    @app.post("/api/v1/tasks/{task_id}/transitions")
    def transition_task(
        task_id: str,
        payload: TaskTransitionRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        result = TaskService(resolved_root).transition_task(
            _safe_public_id(task_id),
            payload.target,
            actor="user:local-web",
            idempotency_key=idempotency_key,
            expected_generation_id=payload.expected_generation_id,
            blocked_reason=payload.blocked_reason,
        )
        provider.invalidate()
        return result.to_dict()

    @app.get("/api/v1/catalog")
    def catalog_snapshot(
        snapshot: Annotated[ReadSnapshot, Depends(read_snapshot)],
    ) -> dict[str, Any]:
        return snapshot.catalog.to_dict()

    @app.get("/api/v1/skills")
    def skills_snapshot(
        snapshot: Annotated[ReadSnapshot, Depends(read_snapshot)],
    ) -> dict[str, Any]:
        pinned_skill_ids = _active_pinned_skill_ids(resolved_root)
        related_agents = _related_agents_by_skill(snapshot.catalog)
        return {
            "catalog_sha256": snapshot.catalog.catalog_sha256,
            "skills": [
                {
                    **skill.model_dump(mode="json"),
                    "source_path": _skill_source_path(skill.skill_id),
                    "policy": _skill_policy(skill, pinned_skill_ids=pinned_skill_ids),
                    "related_agents": related_agents[skill.skill_id],
                }
                for skill in snapshot.catalog.skills
            ],
        }

    @app.get("/api/v1/skills/{skill_id}")
    def skill_detail(
        skill_id: str,
        snapshot: Annotated[ReadSnapshot, Depends(read_snapshot)],
        expected: Annotated[str, Query(min_length=1, max_length=256)],
    ) -> dict[str, Any]:
        safe_id = _safe_public_id(skill_id)
        _require_snapshot(expected, snapshot.catalog.catalog_sha256)
        skill = snapshot.catalog.skill(safe_id)
        if skill is None:
            raise HTTPException(
                404,
                detail={"code": "skill_not_found", "message": "Skill was not found."},
            )
        source_path = _skill_source_path(safe_id)
        pinned_skill_ids = _active_pinned_skill_ids(resolved_root)
        policy = _skill_policy(skill, pinned_skill_ids=pinned_skill_ids)
        body = snapshot.catalog.skill_bodies.get(safe_id)
        related_agents = _related_agents_by_skill(snapshot.catalog)[safe_id]
        return {
            "catalog_sha256": snapshot.catalog.catalog_sha256,
            "skill": skill.model_dump(mode="json") | {"source_path": source_path},
            "policy": policy,
            "source": {
                "path": source_path,
                "sha256": skill.source_sha256,
                "content_available": body is not None,
                "content_restricted": body is None,
            },
            "content": body,
            # Keep the original raw-text contract while declaring Markdown semantics explicitly.
            "format": "text",
            "content_format": "markdown",
            "related_agents": related_agents,
            "permission_boundary": {
                "availability": "catalog_metadata",
                "declared_permission_ids": list(skill.permissions),
                "filesystem": {"availability": "not_modeled", "items": []},
                "network": {"availability": "not_modeled", "items": []},
                "tools": {"availability": "not_modeled", "items": []},
                "secrets": {"availability": "not_modeled", "items": []},
                "approvals": {"availability": "not_modeled", "items": []},
                "side_effects": {"availability": "not_modeled", "items": []},
                "sensitivity": skill.sensitivity.value,
            },
            "workflows": {"availability": "not_modeled", "items": []},
            "tools": {"availability": "not_modeled", "items": []},
            "tests": {
                "availability": "catalog_metadata",
                "test_status": skill.test_status,
                "evals": [],
                "last_checked_at": None,
                "commands": [],
                "known_limitations": ["verified_test_registry_unavailable"],
            },
            "history": _skill_history(resolved_root, safe_id),
        }

    @app.post("/api/v1/skills/{skill_id}/save/preview")
    def preview_skill_save(
        skill_id: str,
        payload: SkillSaveRequest,
        _idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        return skill_authoring.preview_save(
            _safe_public_id(skill_id),
            content=payload.content,
            expected_catalog_sha256=payload.expected_catalog_sha256,
            expected_source_sha256=payload.expected_source_sha256,
        )

    @app.post("/api/v1/skills/{skill_id}/save")
    def save_skill(
        skill_id: str,
        payload: SkillSaveRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        safe_id = _safe_public_id(skill_id)
        with skill_authoring.catalog_read_guard():
            skill_before = CatalogService(resolved_root).load().skill(safe_id)
            source_before_sha256 = (
                None
                if skill_before is None
                else sha256_hex((resolved_root / skill_before.source_path).read_bytes())
            )
        result = skill_authoring.save(
            safe_id,
            content=payload.content,
            expected_catalog_sha256=payload.expected_catalog_sha256,
            expected_source_sha256=payload.expected_source_sha256,
            idempotency_key=idempotency_key,
            actor_id="user_local_web",
        )
        if source_before_sha256 != result["source_sha256"]:
            provider.invalidate()
        return result

    @app.post("/api/v1/skills/{skill_id}/fork/preview")
    def preview_skill_fork(
        skill_id: str,
        payload: SkillForkPreviewRequest,
        _idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        return skill_authoring.preview_fork(
            _safe_public_id(skill_id),
            new_skill_id=payload.new_skill_id,
            expected_catalog_sha256=payload.expected_catalog_sha256,
            expected_source_sha256=payload.expected_source_sha256,
        )

    @app.post("/api/v1/skills/{skill_id}/fork")
    def fork_skill(
        skill_id: str,
        payload: SkillForkRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        safe_id = _safe_public_id(skill_id)
        with skill_authoring.catalog_read_guard():
            destination_existed = (
                CatalogService(resolved_root).load().skill(payload.new_skill_id) is not None
            )
        result = skill_authoring.create_fork(
            safe_id,
            new_skill_id=payload.new_skill_id,
            expected_catalog_sha256=payload.expected_catalog_sha256,
            expected_source_sha256=payload.expected_source_sha256,
            idempotency_key=idempotency_key,
            actor_id="user_local_web",
        )
        if not destination_existed:
            provider.invalidate()
        return result

    @app.get("/api/v1/instructions/{document_id}")
    def instruction_detail(
        document_id: str,
        snapshot: Annotated[ReadSnapshot, Depends(read_snapshot)],
        expected: Annotated[str, Query(min_length=1, max_length=256)],
    ) -> dict[str, Any]:
        safe_id = _safe_public_id(document_id)
        _require_snapshot(expected, snapshot.catalog.catalog_sha256)
        document = snapshot.catalog.instruction(safe_id)
        if document is None:
            raise HTTPException(
                404,
                detail={"code": "context_not_found", "message": "Context was not found."},
            )
        body = snapshot.catalog.instruction_bodies.get(safe_id)
        if body is None:
            raise HTTPException(
                403,
                detail={
                    "code": "content_restricted",
                    "message": "Context is restricted by the sensitivity gate.",
                },
            )
        return {
            "catalog_sha256": snapshot.catalog.catalog_sha256,
            "instruction": document.model_dump(mode="json"),
            "content": body,
            "format": "text",
        }

    @app.get("/api/v1/runs")
    def list_runs(
        snapshot: Annotated[ReadSnapshot, Depends(read_snapshot)],
    ) -> dict[str, Any]:
        return {
            "knowledge_generation_id": snapshot.corpus.generation.generation_id,
            "runs": [run.model_dump(mode="json") for run in snapshot.runs],
        }

    @app.get("/api/v1/universe")
    def universe_snapshot(
        snapshot: Annotated[ReadSnapshot, Depends(read_snapshot)],
    ) -> dict[str, Any]:
        return snapshot.graph.model_dump(mode="json")

    @app.get("/api/v1/code-graph/status")
    def code_graph_status(
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        return CodeGraphProjection(resolved_root).status(verify_hashes=True).model_dump(mode="json")

    @app.get("/api/v1/code-graph/nodes/{node_id}")
    def code_graph_node_detail(
        node_id: str,
        snapshot: Annotated[ReadSnapshot, Depends(read_snapshot)],
        expected: Annotated[str, Query(min_length=1, max_length=256)],
    ) -> dict[str, Any]:
        safe_id = _safe_public_id(node_id)
        _require_code_snapshot(snapshot, expected)
        assert snapshot.code is not None
        node = next((item for item in snapshot.code.nodes if item.node_id == safe_id), None)
        if node is None:
            raise HTTPException(
                404,
                detail={"code": "code_node_not_found", "message": "Code node was not found."},
            )
        incoming = sum(1 for edge in snapshot.code.edges if edge.target == safe_id)
        outgoing = sum(1 for edge in snapshot.code.edges if edge.source == safe_id)
        return {
            "snapshot_id": snapshot.code.snapshot_id,
            "snapshot_fingerprint": snapshot.code.logical_fingerprint,
            "node": {
                "node_id": node.node_id,
                "kind": node.kind.value,
                "label": node.label,
                "qualified_name": node.qualified_name,
                "path": node.path,
                "location": (
                    None if node.location is None else node.location.model_dump(mode="json")
                ),
                "community_id": node.community_id,
                "is_god": node.is_god,
                "is_bridge": node.is_bridge,
                "content_fingerprint": node.content_fingerprint,
                "extractor": node.extractor,
                "extractor_version": node.extractor_version,
                "metadata": {
                    key: value
                    for key, value in node.metadata.items()
                    if key in _PUBLIC_CODE_METADATA
                },
                "incoming_edges": incoming,
                "outgoing_edges": outgoing,
            },
        }

    @app.post("/api/v1/code-graph/query")
    def code_graph_query(
        payload: CodeGraphQueryRequest,
        _idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        result = CodeGraphQueryService(resolved_root).query(payload.query, depth=payload.depth)
        _require_snapshot(payload.expected_snapshot_id, result.snapshot_id)
        return result.model_dump(mode="json")

    @app.post("/api/v1/code-graph/neighbors")
    def code_graph_neighbors(
        payload: CodeGraphNodeRequest,
        _idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        result = CodeGraphQueryService(resolved_root).neighbors(
            payload.node_id,
            depth=payload.depth,
            direction=payload.direction,
        )
        _require_snapshot(payload.expected_snapshot_id, result.snapshot_id)
        return result.model_dump(mode="json")

    @app.post("/api/v1/code-graph/path")
    def code_graph_path(
        payload: CodeGraphPathRequest,
        _idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        result = CodeGraphQueryService(resolved_root).path(
            payload.source_node_id,
            payload.target_node_id,
        )
        _require_snapshot(payload.expected_snapshot_id, result.snapshot_id)
        return result.model_dump(mode="json")

    @app.post("/api/v1/code-graph/impact")
    def code_graph_impact(
        payload: CodeGraphImpactRequest,
        _idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        result = CodeGraphQueryService(resolved_root).impact(
            payload.node_id,
            depth=payload.depth,
        )
        _require_snapshot(payload.expected_snapshot_id, result.snapshot_id)
        return result.model_dump(mode="json")

    def mutate_code_graph(
        payload: CodeGraphMutationRequest,
        *,
        operation: str,
    ) -> dict[str, Any]:
        projection = CodeGraphProjection(resolved_root)
        try:
            current = projection.current_snapshot()
        except CodeGraphUnavailable:
            current = None
        if current is not None:
            if payload.expected_snapshot_id is None:
                raise HTTPException(
                    409,
                    detail={
                        "code": "snapshot_required",
                        "message": "Bind the graph command to the current snapshot.",
                    },
                )
            _require_snapshot(payload.expected_snapshot_id, current.snapshot_id)
        elif payload.expected_snapshot_id is not None:
            _require_snapshot(payload.expected_snapshot_id, None)
        ledger_pointer = resolved_root / "ledger" / "CURRENT"
        canonical_before = ledger_pointer.read_bytes() if ledger_pointer.is_file() else None
        result = projection.rebuild() if operation == "rebuild" else projection.update()
        canonical_after = ledger_pointer.read_bytes() if ledger_pointer.is_file() else None
        if canonical_after != canonical_before:
            raise IntegrityError("Code graph operation crossed the canonical write boundary")
        provider.invalidate()
        return result.to_dict()

    @app.post("/api/v1/code-graph/update")
    def update_code_graph(
        payload: CodeGraphMutationRequest,
        _idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        return mutate_code_graph(payload, operation="update")

    @app.post("/api/v1/code-graph/rebuild")
    def rebuild_code_graph(
        payload: CodeGraphMutationRequest,
        _idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        return mutate_code_graph(payload, operation="rebuild")

    @app.get("/api/v1/knowledge")
    def knowledge_snapshot(
        snapshot: Annotated[ReadSnapshot, Depends(read_snapshot)],
    ) -> dict[str, Any]:
        corpus = snapshot.corpus
        return {
            "generation_id": corpus.generation.generation_id,
            "generation_sha256": corpus.generation_sha256,
            "claims": [
                {
                    "claim_id": claim.claim_id,
                    "statement": _truncate(claim.statement),
                    "status": claim.status.value,
                    "recorded_at": claim.recorded_at.isoformat(),
                    "evidence_count": len(claim.evidence_ids),
                }
                for claim in sorted(corpus.claims.values(), key=lambda item: item.claim_id)
            ],
            "entities": [
                {
                    "entity_id": entity.entity_id,
                    "label": entity.canonical_label,
                    "entity_type": entity.entity_type,
                    "status": entity.lifecycle_status.value,
                }
                for entity in sorted(corpus.entities.values(), key=lambda item: item.entity_id)
            ],
            "sources": [
                {
                    "source_id": source.source_id,
                    "label": source.display_name or source.source_type,
                    "source_type": source.source_type,
                    "trust": source.trust_class.value,
                    "sensitivity": source.sensitivity.value,
                }
                for source in sorted(corpus.sources.values(), key=lambda item: item.source_id)
            ],
            "evidence": [
                {
                    "evidence_id": evidence_id,
                    "source_id": resolved.source.source_id,
                    "locator_kind": resolved.segment.locator.kind,
                    "excerpt_sha256": resolved.segment.excerpt_sha256,
                }
                for evidence_id, resolved in sorted(corpus.evidence.items())
            ],
        }

    @app.get("/api/v1/knowledge/{kind}/{object_id}")
    def knowledge_detail(
        kind: str,
        object_id: str,
        snapshot: Annotated[ReadSnapshot, Depends(read_snapshot)],
        expected: Annotated[str, Query(min_length=1, max_length=256)],
    ) -> dict[str, Any]:
        safe_id = _safe_public_id(object_id)
        corpus = snapshot.corpus
        _require_snapshot(expected, corpus.generation.generation_id)
        base = {
            "generation_id": corpus.generation.generation_id,
            "generation_sha256": corpus.generation_sha256,
        }
        if kind == "claim" and safe_id in corpus.claims:
            claim = corpus.claims[safe_id]
            return base | {
                "kind": "claim",
                "claim": {
                    "claim_id": claim.claim_id,
                    "statement": claim.statement,
                    "status": claim.status.value,
                    "language": claim.language,
                    "evidence_ids": claim.evidence_ids,
                    "relation_ids": claim.relation_ids,
                    "supersedes": claim.supersedes,
                    "contradicts": claim.contradicts,
                    "temporal": claim.temporal.model_dump(mode="json"),
                    "recorded_at": claim.recorded_at.isoformat(),
                },
            }
        if kind == "entity" and safe_id in corpus.entities:
            entity = corpus.entities[safe_id]
            return base | {
                "kind": "entity",
                "entity": {
                    "entity_id": entity.entity_id,
                    "label": entity.canonical_label,
                    "entity_type": entity.entity_type,
                    "aliases": [alias.value for alias in entity.aliases],
                    "status": entity.lifecycle_status.value,
                    "superseded_by": entity.superseded_by,
                },
            }
        if kind == "source" and safe_id in corpus.sources:
            source = corpus.sources[safe_id]
            return base | {
                "kind": "source",
                "source": {
                    "source_id": source.source_id,
                    "label": source.display_name or source.source_type,
                    "source_type": source.source_type,
                    "trust": source.trust_class.value,
                    "rights": source.rights,
                    "sensitivity": source.sensitivity.value,
                    "created_at": source.created_at.isoformat(),
                },
            }
        if kind == "evidence" and safe_id in corpus.evidence:
            resolved = corpus.evidence[safe_id]
            if resolved.source.sensitivity in {
                Sensitivity.CONFIDENTIAL,
                Sensitivity.RESTRICTED,
                Sensitivity.SECRET,
            }:
                raise HTTPException(
                    403,
                    detail={
                        "code": "evidence_restricted",
                        "message": "Evidence is restricted by the disclosure policy.",
                    },
                )
            return base | {
                "kind": "evidence",
                "evidence": {
                    "evidence_id": safe_id,
                    "source_id": resolved.source.source_id,
                    "source_label": resolved.source.display_name or resolved.source.source_type,
                    "source_revision_id": resolved.revision.source_revision_id,
                    "normalization_id": resolved.normalization.normalization_id,
                    "locator": resolved.segment.locator.model_dump(mode="json"),
                    "excerpt": resolved.excerpt,
                    "excerpt_sha256": resolved.segment.excerpt_sha256,
                    "content_sha256": resolved.revision.content_sha256,
                },
            }
        raise HTTPException(
            404,
            detail={"code": "knowledge_not_found", "message": "Object was not found."},
        )

    @app.get("/api/v1/search")
    def local_search(
        snapshot: Annotated[ReadSnapshot, Depends(read_snapshot)],
        query: Annotated[str, Query(alias="q", min_length=1, max_length=128)],
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> dict[str, Any]:
        needle = query.casefold().strip()
        matches = [
            node
            for node in snapshot.graph.nodes
            if needle in node.label.casefold()
            or needle in node.subtitle.casefold()
            or needle in node.node_id.casefold()
        ]
        matches.sort(
            key=lambda node: (
                not node.label.casefold().startswith(needle),
                -node.importance,
                node.label.casefold(),
            )
        )
        return {
            "graph_snapshot_id": snapshot.graph.graph_snapshot_id,
            "knowledge_generation_id": snapshot.corpus.generation.generation_id,
            "task_generation_id": snapshot.tasks.generation_id,
            "catalog_sha256": snapshot.catalog.catalog_sha256,
            "results": [
                {
                    "id": node.node_id,
                    "kind": node.kind,
                    "label": node.label,
                    "subtitle": node.subtitle,
                    "status": node.status,
                    "snapshot_id": _node_snapshot_id(node.kind, snapshot),
                }
                for node in matches[:limit]
            ],
        }

    from raytsystem.webapp.document_routes import (
        create_document_router,
        initialize_document_module,
    )
    from raytsystem.webapp.feature_routes import create_feature_router

    async def initialize_documents() -> None:
        # The disposable projection is built explicitly, but never blocks unrelated
        # control-plane routes. Reads do not trigger scans; a malformed Documents
        # configuration therefore degrades this surface without taking down Safety,
        # Handbook, or the rest of the local UI.
        app.state.document_initialization = {"state": "checking"}
        try:
            status = await asyncio.to_thread(initialize_document_module, resolved_root)
        except Exception:  # the detached surface must fail isolated
            app.state.document_initialization = {"state": "error"}
        else:
            app.state.document_initialization = status

    async def start_document_initialization() -> None:
        app.state.document_initialization_task = asyncio.create_task(
            initialize_documents(),
            name="raytsystem-documents-initial-scan",
        )

    app.state.document_initialization = {"state": "pending"}
    app.state.document_initialization_task = None
    app.router.add_event_handler("startup", start_document_initialization)
    app.include_router(create_document_router(resolved_root, require_session=require_session))

    app.include_router(create_feature_router(resolved_root, require_session=require_session))

    def serve_index(request: Request) -> HTMLResponse:
        index_path = selected_static / "index.html"
        if not index_path.is_file():
            raise HTTPException(
                503,
                detail={
                    "code": "ui_bundle_missing",
                    "message": "Build the local web bundle before starting the interface.",
                },
            )
        try:
            source = index_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise HTTPException(
                503,
                detail={"code": "ui_bundle_invalid", "message": "The local UI bundle is invalid."},
            ) from error
        nonce = str(request.scope.get("state", {}).get("csp_nonce", ""))
        if not nonce:
            raise HTTPException(
                503,
                detail={"code": "ui_nonce_missing", "message": "The local UI session is invalid."},
            )
        marker = "__RAYTSYSTEM_CSP_NONCE__"
        if marker not in source:
            source = source.replace(
                '<meta name="color-scheme" content="dark light" />',
                f'<meta name="color-scheme" content="dark light" />\n    '
                f'<meta name="raytsystem-csp-nonce" content="{nonce}" />',
                1,
            )
        else:
            source = source.replace(marker, nonce, 1)
        if marker in source or f'name="raytsystem-csp-nonce" content="{nonce}"' not in source:
            raise HTTPException(
                503,
                detail={
                    "code": "ui_nonce_marker_missing",
                    "message": "The local UI bundle is incompatible with the CSP policy.",
                },
            )
        response = HTMLResponse(source)
        response.headers["Cache-Control"] = "no-store"
        existing = sessions.get(request.cookies.get(SESSION_COOKIE))
        if existing is None:
            issued = sessions.issue()
            response.set_cookie(
                SESSION_COOKIE,
                issued.session_id,
                max_age=sessions.ttl_seconds,
                httponly=True,
                secure=False,
                samesite="strict",
                path="/",
            )
        return response

    register_onboarding_routes(app)

    @app.get("/", include_in_schema=False)
    def index(request: Request) -> HTMLResponse:
        return serve_index(request)

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon() -> FileResponse:
        path = selected_static / "favicon.svg"
        if not path.is_file():
            raise HTTPException(404, detail={"code": "not_found", "message": "Not found."})
        return FileResponse(path, media_type="image/svg+xml")

    @app.get("/{route:path}", include_in_schema=False)
    def spa_route(route: str, request: Request) -> HTMLResponse:
        if route not in _SPA_ROUTES:
            raise HTTPException(404, detail={"code": "not_found", "message": "Not found."})
        return serve_index(request)

    return app


_OnboardingChoice = Literal["auto", "software", "content", "research"]


def register_onboarding_routes(app: FastAPI) -> None:
    """Local, loopback-only HTTP surface for the ``bootstrap`` installer.

    Reads (`plan`, `prompt`) are safe previews; writes (`apply`, `uninstall`) are
    CSRF- and idempotency-gated like every other mutation. The target path is
    client-supplied input; responses carry only a basename, never an absolute path.
    """

    @app.exception_handler(BootstrapError)
    async def _bootstrap_error(_request: Request, error: BootstrapError) -> JSONResponse:
        return _api_error(400, "bootstrap_failed", str(error))

    @app.get("/api/v1/onboarding/plan")
    def onboarding_plan(
        _session: Annotated[SessionRecord, Depends(require_session)],
        target: Annotated[str, Query(min_length=1, max_length=4096)],
        source_type: Annotated[_OnboardingChoice, Query()] = "auto",
        template: Annotated[_OnboardingChoice, Query()] = "auto",
        mode: Annotated[Literal["managed", "vendored"], Query()] = "managed",
        context_language: Annotated[Literal["en", "ru"], Query()] = "en",
    ) -> dict[str, Any]:
        plan = BootstrapService(Path(target)).plan(
            template=template,
            source_type=source_type,
            mode=mode,
            context_language=context_language,
        )
        return plan.model_dump(mode="json")

    @app.get("/api/v1/onboarding/prompt")
    def onboarding_prompt(
        _session: Annotated[SessionRecord, Depends(require_session)],
        target: Annotated[str, Query(min_length=1, max_length=4096)],
        agent: Annotated[Literal["codex", "claude"], Query()] = "claude",
        mode: Annotated[Literal["managed", "vendored"], Query()] = "managed",
        context_language: Annotated[Literal["en", "ru"], Query()] = "en",
    ) -> dict[str, Any]:
        return dict(
            BootstrapService(Path(target)).onboarding_prompt(
                agent=agent, mode=mode, context_language=context_language
            )
        )

    @app.post("/api/v1/onboarding/apply")
    def onboarding_apply(
        payload: OnboardingApplyRequest,
        _idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        return BootstrapService(Path(payload.target)).apply(
            confirm=payload.confirm,
            template=payload.template,
            source_type=payload.source_type,
            mode=payload.mode,
            context_language=payload.context_language,
        )

    @app.post("/api/v1/onboarding/uninstall")
    def onboarding_uninstall(
        payload: OnboardingUninstallRequest,
        _idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[SessionRecord, Depends(require_session)],
    ) -> dict[str, Any]:
        return BootstrapService(Path(payload.target)).uninstall()
