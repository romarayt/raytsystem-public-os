import secrets
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from raytsystem.documents import load_document_config
from raytsystem.documents.contracts import (
    DocumentConfigError,
    DocumentConflict,
    DocumentError,
    DocumentIndexError,
    DocumentNotFound,
    DocumentPolicyError,
    DocumentRestricted,
)
from raytsystem.documents.history import DocumentHistory
from raytsystem.documents.index import DocumentIndex
from raytsystem.documents.service import DocumentService
from raytsystem.platform_store import PlatformStoreError, initialize_platform_store
from raytsystem.webapp.document_dto import (
    DocumentCreateRequest,
    DocumentFolderCreateRequest,
    DocumentIndexRefreshRequest,
    DocumentMoveRequest,
    DocumentRenameRequest,
    DocumentRestorePreviewRequest,
    DocumentRestoreRequest,
    DocumentUpdateRequest,
)


def initialize_document_module(root: Path) -> dict[str, Any]:
    """Explicit startup hook; reads never rebuild the disposable projection."""

    index = DocumentIndex(root, config=load_document_config(root))
    status = index.status()
    return index.rebuild() if status["state"] != "current" else status


def create_document_router(
    root: Path,
    *,
    require_session: Callable[..., Any],
) -> APIRouter:
    resolved_root = root.resolve()
    router = APIRouter(prefix="/api/v1")
    try:
        config = load_document_config(resolved_root)
    except DocumentConfigError:
        return _degraded_router(router, require_session=require_session)
    index = DocumentIndex(resolved_root, config=config)
    service = DocumentService(resolved_root, index=index)
    history = DocumentHistory(resolved_root, index=index)
    projection_lock = threading.RLock()

    def idempotent_projection(
        *,
        operation: str,
        idempotency_key: str,
        request: dict[str, Any],
        callback: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        command = {"operation": operation, **request}
        with projection_lock, initialize_platform_store(resolved_root) as store:
            prior = store.idempotent_receipt(
                scope="document_projection",
                idempotency_key=idempotency_key,
                request=command,
            )
            if prior is not None:
                return prior
            result = callback()
            store.idempotent_receipt(
                scope="document_projection",
                idempotency_key=idempotency_key,
                request=command,
                receipt=result,
            )
            return result

    @router.get("/documents")
    def documents(
        _session: Annotated[Any, Depends(require_session)],
        limit: Annotated[int | None, Query(ge=1, le=200)] = None,
        cursor: str | None = None,
        root_id: str | None = None,
        mode: str | None = None,
        folder: str | None = None,
        kind: str | None = None,
        tag: str | None = None,
        document_ids: Annotated[list[str] | None, Query()] = None,
        sort: str = "modified_desc",
    ) -> Any:
        return _read(
            lambda: index.list_documents(
                limit=limit,
                cursor=cursor,
                root_id=root_id,
                mode=mode,
                folder=folder,
                kind=kind,
                tag=tag,
                document_ids=tuple(document_ids or ()),
                sort=sort,
            )
        )

    @router.get("/documents/search")
    def search_documents(
        _session: Annotated[Any, Depends(require_session)],
        q: Annotated[str, Query(min_length=1, max_length=2048)],
        limit: Annotated[int | None, Query(ge=1, le=200)] = None,
        cursor: str | None = None,
        root_id: str | None = None,
        folder: str | None = None,
        kind: str | None = None,
        mode: str | None = None,
        tag: str | None = None,
        sort: str = "modified_desc",
    ) -> Any:
        return _read(
            lambda: index.search(
                q,
                limit=limit,
                cursor=cursor,
                root_id=root_id,
                folder=folder,
                kind=kind,
                mode=mode,
                tag=tag,
                sort=sort,
            )
        )

    @router.get("/documents/recent")
    def recent_documents(
        _session: Annotated[Any, Depends(require_session)],
        kind: Literal["recent", "modified", "added"] = "recent",
        limit: Annotated[int | None, Query(ge=1, le=200)] = None,
        cursor: str | None = None,
    ) -> Any:
        return _read(lambda: index.recent(kind=kind, limit=limit, cursor=cursor))

    @router.get("/documents/index")
    def document_index(
        _session: Annotated[Any, Depends(require_session)],
    ) -> dict[str, Any]:
        return index.status()

    @router.post("/documents/index/refresh")
    def refresh_document_index(
        payload: DocumentIndexRefreshRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[Any, Depends(require_session)],
    ) -> Any:
        def refresh() -> dict[str, Any]:
            _require_index_snapshot(index, payload.expected_snapshot_id)
            document_ids = tuple(
                dict.fromkeys(
                    (
                        *((payload.document_id,) if payload.document_id is not None else ()),
                        *payload.document_ids,
                    )
                )
            )
            paths = tuple(
                str(index.row_for_id(document_id)["relative_path"]) for document_id in document_ids
            )
            return index.refresh(paths)

        document_ids = tuple(
            dict.fromkeys(
                (
                    *((payload.document_id,) if payload.document_id is not None else ()),
                    *payload.document_ids,
                )
            )
        )
        return _write(
            lambda: idempotent_projection(
                operation="refresh",
                idempotency_key=idempotency_key,
                request={
                    "expected_snapshot_id": payload.expected_snapshot_id,
                    "document_ids": document_ids,
                },
                callback=refresh,
            )
        )

    @router.post("/documents/index/rebuild")
    def rebuild_document_index(
        payload: DocumentIndexRefreshRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[Any, Depends(require_session)],
    ) -> Any:
        def rebuild() -> dict[str, Any]:
            _require_index_snapshot(index, payload.expected_snapshot_id)
            return index.rebuild()

        return _write(
            lambda: idempotent_projection(
                operation="rebuild",
                idempotency_key=idempotency_key,
                request={"expected_snapshot_id": payload.expected_snapshot_id},
                callback=rebuild,
            )
        )

    @router.get("/documents/assets/{asset_id}")
    def document_asset(
        asset_id: str,
        _session: Annotated[Any, Depends(require_session)],
    ) -> Response:
        try:
            data, media_type = index.asset_bytes(asset_id)
        except DocumentNotFound as error:
            raise _http(404, "document_asset_not_found", "Document asset was not found.") from error
        except DocumentError as error:
            raise _http(
                409, "document_asset_stale", "Document asset changed. Refresh Documents."
            ) from error
        return Response(
            content=data,
            media_type=media_type,
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post("/documents")
    def create_document(
        payload: DocumentCreateRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[Any, Depends(require_session)],
    ) -> Any:
        return _write(
            lambda: service.create(
                root_id=payload.root_id,
                name=payload.name,
                folder=payload.folder,
                content=payload.content,
                template=payload.template,
                properties=payload.properties,
                tags=payload.tags,
                expected_snapshot_id=payload.expected_snapshot_id,
                idempotency_key=idempotency_key,
            )
        )

    @router.post("/documents/folders")
    def create_document_folder(
        payload: DocumentFolderCreateRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[Any, Depends(require_session)],
    ) -> Any:
        return _write(
            lambda: service.create_folder(
                root_id=payload.root_id,
                folder=payload.folder,
                expected_snapshot_id=payload.expected_snapshot_id,
                idempotency_key=idempotency_key,
            )
        )

    @router.get("/documents/folders")
    def document_folders(
        _session: Annotated[Any, Depends(require_session)],
        root_id: str | None = None,
        parent_path: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
        cursor: str | None = None,
    ) -> Any:
        return _read(
            lambda: index.folders(
                root_id=root_id,
                parent_path=parent_path,
                limit=limit,
                cursor=cursor,
            )
        )

    @router.get("/documents/{document_id}")
    def document_detail(
        document_id: str,
        _session: Annotated[Any, Depends(require_session)],
        expected_snapshot_id: str | None = None,
    ) -> Any:
        return _read(lambda: index.detail(document_id, expected_snapshot_id=expected_snapshot_id))

    @router.get("/documents/{document_id}/links")
    def document_links(
        document_id: str,
        _session: Annotated[Any, Depends(require_session)],
        expected_snapshot_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=2000)] = 500,
        cursor: str | None = None,
    ) -> Any:
        return _read(
            lambda: index.links(
                document_id,
                expected_snapshot_id=expected_snapshot_id,
                limit=limit,
                cursor=cursor,
            )
        )

    @router.get("/documents/{document_id}/backlinks")
    def document_backlinks(
        document_id: str,
        _session: Annotated[Any, Depends(require_session)],
        expected_snapshot_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=2000)] = 500,
        cursor: str | None = None,
    ) -> Any:
        return _read(
            lambda: index.links(
                document_id,
                backlinks=True,
                expected_snapshot_id=expected_snapshot_id,
                limit=limit,
                cursor=cursor,
            )
        )

    @router.get("/documents/{document_id}/history")
    def document_history(
        document_id: str,
        _session: Annotated[Any, Depends(require_session)],
        expected_snapshot_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        cursor: str | None = None,
    ) -> Any:
        return _read(
            lambda: history.list(
                document_id,
                expected_snapshot_id=expected_snapshot_id,
                limit=limit,
                cursor=cursor,
            )
        )

    @router.get("/documents/{document_id}/history/{history_id}")
    def document_history_detail(
        document_id: str,
        history_id: str,
        _session: Annotated[Any, Depends(require_session)],
        expected_snapshot_id: str | None = None,
    ) -> Any:
        return _read(
            lambda: history.detail(
                document_id,
                history_id,
                expected_snapshot_id=expected_snapshot_id,
            )
        )

    @router.get("/documents/{document_id}/graph")
    def document_graph(
        document_id: str,
        _session: Annotated[Any, Depends(require_session)],
        max_nodes: Annotated[int, Query(ge=1, le=500)] = 250,
        max_edges: Annotated[int, Query(ge=1, le=2000)] = 500,
    ) -> Any:
        return _read(
            lambda: index.focused_graph(document_id, max_nodes=max_nodes, max_edges=max_edges)
        )

    @router.post("/documents/{document_id}/update")
    def update_document(
        document_id: str,
        payload: DocumentUpdateRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[Any, Depends(require_session)],
    ) -> Any:
        return _write(
            lambda: service.update(
                document_id,
                content=payload.content,
                expected_sha256=payload.expected_sha256,
                expected_snapshot_id=payload.expected_snapshot_id,
                idempotency_key=idempotency_key,
            )
        )

    @router.post("/documents/{document_id}/rename")
    def rename_document(
        document_id: str,
        payload: DocumentRenameRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[Any, Depends(require_session)],
    ) -> Any:
        return _write(
            lambda: service.rename(
                document_id,
                new_name=payload.name,
                expected_sha256=payload.expected_sha256,
                expected_snapshot_id=payload.expected_snapshot_id,
                idempotency_key=idempotency_key,
            )
        )

    @router.post("/documents/{document_id}/move")
    def move_document(
        document_id: str,
        payload: DocumentMoveRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[Any, Depends(require_session)],
    ) -> Any:
        return _write(
            lambda: service.move(
                document_id,
                destination_root_id=payload.destination_root_id,
                destination_folder=payload.destination_folder,
                expected_sha256=payload.expected_sha256,
                expected_snapshot_id=payload.expected_snapshot_id,
                idempotency_key=idempotency_key,
            )
        )

    @router.post("/documents/{document_id}/restore-preview")
    def preview_document_restore(
        document_id: str,
        payload: DocumentRestorePreviewRequest,
        _session: Annotated[Any, Depends(require_session)],
    ) -> Any:
        return _write(
            lambda: service.restore_preview(
                document_id,
                history_id=payload.history_id,
                expected_sha256=payload.expected_sha256,
                expected_snapshot_id=payload.expected_snapshot_id,
            )
        )

    @router.post("/documents/{document_id}/restore")
    def restore_document(
        document_id: str,
        payload: DocumentRestoreRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[Any, Depends(require_session)],
    ) -> Any:
        return _write(
            lambda: service.restore(
                document_id,
                history_id=payload.history_id,
                expected_sha256=payload.expected_sha256,
                expected_snapshot_id=payload.expected_snapshot_id,
                preview_token=payload.preview_token,
                confirmed=payload.confirmed,
                idempotency_key=idempotency_key,
            )
        )

    return router


def _degraded_router(
    router: APIRouter,
    *,
    require_session: Callable[..., Any],
) -> APIRouter:
    @router.get("/documents/index")
    def degraded_index(
        _session: Annotated[Any, Depends(require_session)],
    ) -> dict[str, Any]:
        return {
            "state": "error",
            "snapshot_id": None,
            "file_count": 0,
            "last_refresh_at": None,
            "error_count": 1,
            "roots": [],
            "message": "Documents configuration is invalid.",
        }

    @router.api_route(
        "/documents",
        methods=["GET", "POST"],
        response_model=None,
    )
    def degraded_documents(
        _session: Annotated[Any, Depends(require_session)],
    ) -> JSONResponse:
        return _error(
            503,
            "document_configuration_invalid",
            "Documents configuration is invalid.",
        )

    @router.api_route(
        "/documents/{subpath:path}",
        methods=["GET", "POST"],
        response_model=None,
    )
    def degraded_document_path(
        subpath: str,
        _session: Annotated[Any, Depends(require_session)],
    ) -> JSONResponse:
        _ = subpath
        return _error(
            503,
            "document_configuration_invalid",
            "Documents configuration is invalid.",
        )

    return router


def _read(callback: Callable[[], dict[str, Any]]) -> Any:
    try:
        return callback()
    except DocumentNotFound:
        return _error(404, "document_not_found", "Document was not found.")
    except DocumentRestricted:
        return _error(403, "document_restricted", "Document content is restricted.")
    except DocumentPolicyError as error:
        return _error(403, "document_policy_denied", str(error))
    except DocumentConfigError:
        return _error(503, "document_configuration_invalid", "Documents configuration is invalid.")
    except DocumentIndexError as error:
        if str(error) == "Document index is unavailable":
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "document_index_initializing",
                        "message": "Documents index is being prepared.",
                    }
                },
                headers={"Cache-Control": "no-store", "Retry-After": "1"},
            )
        return _error(409, "document_index_stale", str(error))


def _write(callback: Callable[[], dict[str, Any]]) -> dict[str, Any] | JSONResponse:
    try:
        return callback()
    except DocumentConflict as error:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "document_conflict",
                    "message": str(error),
                    "details": error.details,
                }
            },
            headers={"Cache-Control": "no-store"},
        )
    except DocumentNotFound:
        return _error(404, "document_not_found", "Document was not found.")
    except DocumentRestricted:
        return _error(403, "document_restricted", "Restricted content is not available.")
    except DocumentPolicyError as error:
        return _error(403, "document_policy_denied", str(error))
    except PlatformStoreError:
        return _error(409, "document_idempotency_conflict", "Document command binding changed.")
    except DocumentIndexError as error:
        return _error(409, "document_index_stale", str(error))
    except DocumentConfigError:
        return _error(503, "document_configuration_invalid", "Documents configuration is invalid.")


def _require_index_snapshot(index: DocumentIndex, expected: str | None) -> None:
    status = index.status()
    current = status.get("snapshot_id")
    unavailable_recovery = (
        expected is None and current is None and status.get("state") in {"missing", "error"}
    )
    if unavailable_recovery:
        return
    if (
        not isinstance(expected, str)
        or not isinstance(current, str)
        or not secrets.compare_digest(expected, current)
    ):
        raise DocumentConflict(
            "Document index changed after the command was prepared",
            details={
                "expected_snapshot_id": expected,
                "current_snapshot_id": current,
            },
        )


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
        headers={"Cache-Control": "no-store"},
    )


def _http(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})
