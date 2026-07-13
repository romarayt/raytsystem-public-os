import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from raytsystem.emergency import EmergencyError, EmergencyService
from raytsystem.notifications import NotificationError, NotificationService
from raytsystem.policy_simulator import PolicySimulator, PolicySimulatorError
from raytsystem.replay import ReplayError, ReplayService
from raytsystem.webapp.feature_dto import (
    EmergencyCommandRequest,
    NotificationTransitionRequest,
    PolicySimulationRequest,
    ReplayPlanRequest,
)
from raytsystem.webapp.feature_readmodel import SYSTEM_SECTIONS, FeatureReadModel


def create_feature_router(
    root: Path,
    *,
    require_session: Callable[..., Any],
) -> APIRouter:
    resolved_root = root.resolve()
    router = APIRouter(prefix="/api/v1")
    reads = FeatureReadModel(resolved_root)

    @router.get("/features")
    def features(_session: Annotated[Any, Depends(require_session)]) -> dict[str, Any]:
        return reads.features()

    @router.get("/systems/{section}")
    def section(
        section: str,
        _session: Annotated[Any, Depends(require_session)],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
    ) -> dict[str, Any]:
        if section not in SYSTEM_SECTIONS:
            raise HTTPException(
                404,
                detail={"code": "section_not_found", "message": "System section was not found."},
            )
        try:
            return reads.section(section, limit=limit)
        except Exception as error:
            raise HTTPException(
                503,
                detail={
                    "code": "section_unavailable",
                    "message": "The local system section is unavailable.",
                },
            ) from error

    @router.get("/traces/{trace_id}")
    def trace_detail(
        trace_id: str,
        _session: Annotated[Any, Depends(require_session)],
    ) -> dict[str, Any]:
        detail = reads.trace_detail(_identifier(trace_id))
        if detail is None:
            raise HTTPException(
                404, detail={"code": "trace_not_found", "message": "Trace was not found."}
            )
        return detail

    @router.post("/policy-simulations")
    def simulate_policy(
        payload: PolicySimulationRequest,
        _idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[Any, Depends(require_session)],
    ) -> dict[str, Any]:
        try:
            return (
                PolicySimulator(resolved_root)
                .simulate(
                    payload.plan,
                    granted_approval_kinds=frozenset(payload.granted_approval_kinds),
                )
                .model_dump(mode="json")
            )
        except PolicySimulatorError as error:
            raise HTTPException(
                422,
                detail={"code": "policy_simulation_rejected", "message": str(error)},
            ) from error

    @router.post("/emergency-commands")
    def emergency_command(
        payload: EmergencyCommandRequest,
        idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[Any, Depends(require_session)],
    ) -> dict[str, Any]:
        service = EmergencyService(resolved_root)
        current = service.snapshot()
        if not secrets.compare_digest(
            str(payload.expected_snapshot_id), str(current["snapshot_id"])
        ):
            raise HTTPException(
                409,
                detail={"code": "snapshot_stale", "message": "Emergency state changed."},
            )
        try:
            return service.activate(
                payload.actions,
                reason=payload.reason,
                actor_id="user_local_web",
                idempotency_key=idempotency_key,
                approval_id=payload.approval_id,
            )
        except EmergencyError as error:
            raise HTTPException(
                422, detail={"code": "emergency_rejected", "message": str(error)}
            ) from error

    @router.post("/replay-plans")
    def replay_plan(
        payload: ReplayPlanRequest,
        _idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[Any, Depends(require_session)],
    ) -> dict[str, Any]:
        try:
            plan = ReplayService(resolved_root).plan_replay(
                payload.original_run_id,
                new_run_id=payload.new_run_id,
                recorded_side_effect_ids=payload.recorded_side_effect_ids,
            )
            return plan.model_dump(mode="json")
        except ReplayError as error:
            raise HTTPException(
                422, detail={"code": "replay_rejected", "message": str(error)}
            ) from error

    @router.post("/notifications/{notification_id}/transitions")
    def notification_transition(
        notification_id: str,
        payload: NotificationTransitionRequest,
        _idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
        _session: Annotated[Any, Depends(require_session)],
    ) -> dict[str, Any]:
        service = NotificationService(resolved_root)
        current = service.snapshot()
        if not secrets.compare_digest(
            str(payload.expected_snapshot_id), str(current["snapshot_id"])
        ):
            raise HTTPException(
                409,
                detail={"code": "snapshot_stale", "message": "Notification state changed."},
            )
        try:
            return service.transition(
                _identifier(notification_id),
                cast(Literal["read", "acknowledged", "resolved"], payload.state),
                actor_id="user_local_web",
            ).model_dump(mode="json")
        except NotificationError as error:
            raise HTTPException(
                422, detail={"code": "notification_rejected", "message": str(error)}
            ) from error

    return router


def _identifier(value: str) -> str:
    if (
        len(value) < 2
        or len(value) > 256
        or not value[0].isalpha()
        or any(not (character.isalnum() or character in "_:.@-") for character in value)
    ):
        raise HTTPException(404, detail={"code": "not_found", "message": "Object not found."})
    return value
