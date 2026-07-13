from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from raytsystem.contracts import ExecutionPlan
from raytsystem.contracts.governance import EmergencyAction

PublicIdentifier = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_:.@-]{1,255}$"),
]


class FeatureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PolicySimulationRequest(FeatureRequest):
    plan: ExecutionPlan
    granted_approval_kinds: tuple[PublicIdentifier, ...] = Field(default=(), max_length=32)


class EmergencyCommandRequest(FeatureRequest):
    actions: tuple[EmergencyAction, ...] = Field(min_length=1, max_length=16)
    reason: str = Field(min_length=3, max_length=4096)
    approval_id: PublicIdentifier | None = None
    expected_snapshot_id: PublicIdentifier


class ReplayPlanRequest(FeatureRequest):
    original_run_id: PublicIdentifier
    new_run_id: PublicIdentifier
    recorded_side_effect_ids: tuple[PublicIdentifier, ...] = Field(default=(), max_length=64)


class NotificationTransitionRequest(FeatureRequest):
    state: str = Field(pattern=r"^(read|acknowledged|resolved)$")
    expected_snapshot_id: PublicIdentifier
