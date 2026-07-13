from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from raytsystem.contracts.execution import TaskLeaseStatus
from raytsystem.control import ControlDB, LeaseBusy
from raytsystem.execution.leases import TaskLeaseManager

NOW = datetime(2026, 7, 12, tzinfo=UTC)


def _manager(tmp_path: Path) -> tuple[ControlDB, TaskLeaseManager]:
    control = ControlDB(tmp_path / "control.sqlite")
    return control, TaskLeaseManager(control, ttl_seconds=10)


def test_task_lease_is_idempotent_for_same_run_and_fenced_for_others(tmp_path: Path) -> None:
    control, manager = _manager(tmp_path)
    try:
        first = manager.acquire(
            task_id="task_example",
            task_generation_id="tgen_a",
            task_revision=2,
            employee_id="employee_a",
            run_id="xrun_a",
            now=NOW,
        )
        repeated = manager.acquire(
            task_id="task_example",
            task_generation_id="tgen_a",
            task_revision=2,
            employee_id="employee_a",
            run_id="xrun_a",
            now=NOW + timedelta(seconds=1),
        )

        assert repeated.fencing_token == first.fencing_token
        with pytest.raises(LeaseBusy):
            manager.acquire(
                task_id="task_example",
                task_generation_id="tgen_a",
                task_revision=2,
                employee_id="employee_b",
                run_id="xrun_b",
                now=NOW + timedelta(seconds=2),
            )
    finally:
        control.close()


def test_lease_verification_binds_task_generation_and_revision(tmp_path: Path) -> None:
    control, manager = _manager(tmp_path)
    try:
        lease = manager.acquire(
            task_id="task_example",
            task_generation_id="tgen_a",
            task_revision=2,
            employee_id="employee_a",
            run_id="xrun_a",
            now=NOW,
        )

        assert manager.verify(
            lease,
            task_generation_id="tgen_a",
            task_revision=2,
            now=NOW + timedelta(seconds=1),
        )
        assert not manager.verify(
            lease,
            task_generation_id="tgen_b",
            task_revision=2,
            now=NOW + timedelta(seconds=1),
        )
        assert not manager.verify(
            lease,
            task_generation_id="tgen_a",
            task_revision=3,
            now=NOW + timedelta(seconds=1),
        )
    finally:
        control.close()


def test_release_allows_new_owner_with_higher_fence(tmp_path: Path) -> None:
    control, manager = _manager(tmp_path)
    try:
        first = manager.acquire(
            task_id="task_example",
            task_generation_id="tgen_a",
            task_revision=1,
            employee_id="employee_a",
            run_id="xrun_a",
            now=NOW,
        )
        released = manager.release(first)
        second = manager.acquire(
            task_id="task_example",
            task_generation_id="tgen_a",
            task_revision=1,
            employee_id="employee_b",
            run_id="xrun_b",
            now=NOW + timedelta(seconds=1),
        )

        assert released.status is TaskLeaseStatus.RELEASED
        assert second.fencing_token > first.fencing_token
    finally:
        control.close()
