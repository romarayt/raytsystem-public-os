from __future__ import annotations

from datetime import UTC, datetime

from raytsystem.contracts.base import derive_id
from raytsystem.contracts.execution import TaskLease, TaskLeaseStatus
from raytsystem.control import ControlDB, LeaseToken


def _milliseconds(value: datetime) -> int:
    return int(value.astimezone(UTC).timestamp() * 1_000)


def _datetime(milliseconds: int) -> datetime:
    return datetime.fromtimestamp(milliseconds / 1_000, tz=UTC)


class TaskLeaseManager:
    """Binds task checkout to raytsystem TTL leases and monotonic fencing tokens."""

    def __init__(self, control: ControlDB, *, ttl_seconds: int = 60) -> None:
        if not 10 <= ttl_seconds <= 3_600:
            raise ValueError("Task lease TTL must be inside 10..3600 seconds")
        self.control = control
        self.ttl_ms = ttl_seconds * 1_000

    @staticmethod
    def partition_key(task_id: str) -> str:
        return f"task:{task_id}"

    @classmethod
    def _token(cls, lease: TaskLease) -> LeaseToken:
        return LeaseToken(
            partition_key=cls.partition_key(lease.task_id),
            control_epoch=lease.control_epoch,
            owner_run_id=lease.run_id,
            fencing_token=lease.fencing_token,
            expires_at_ms=_milliseconds(lease.expires_at),
        )

    def acquire(
        self,
        *,
        task_id: str,
        task_generation_id: str,
        task_revision: int,
        employee_id: str,
        run_id: str,
        now: datetime | None = None,
    ) -> TaskLease:
        acquired_at = (now or datetime.now(UTC)).astimezone(UTC)
        token = self.control.acquire_lease(
            self.partition_key(task_id),
            run_id,
            ttl_ms=self.ttl_ms,
            now_ms=_milliseconds(acquired_at),
        )
        identity = {
            "task_id": task_id,
            "employee_id": employee_id,
            "run_id": run_id,
            "task_generation_id": task_generation_id,
            "task_revision": task_revision,
            "control_epoch": token.control_epoch,
            "fencing_token": token.fencing_token,
        }
        return TaskLease(
            lease_id=derive_id("tlease", identity),
            task_id=task_id,
            employee_id=employee_id,
            run_id=run_id,
            task_generation_id=task_generation_id,
            task_revision=task_revision,
            control_epoch=token.control_epoch,
            fencing_token=token.fencing_token,
            acquired_at=acquired_at,
            expires_at=_datetime(token.expires_at_ms),
        )

    def verify(
        self,
        lease: TaskLease,
        *,
        task_generation_id: str,
        task_revision: int,
        now: datetime | None = None,
    ) -> bool:
        observed_at = (now or datetime.now(UTC)).astimezone(UTC)
        return bool(
            lease.status is TaskLeaseStatus.ACTIVE
            and lease.task_generation_id == task_generation_id
            and lease.task_revision == task_revision
            and self.control.verify_lease(
                self._token(lease),
                now_ms=_milliseconds(observed_at),
            )
        )

    def verify_task_revision(
        self,
        lease: TaskLease,
        *,
        task_revision: int,
        now: datetime | None = None,
    ) -> bool:
        """Verify the live fence after unrelated task-board generations may advance."""

        observed_at = (now or datetime.now(UTC)).astimezone(UTC)
        return bool(
            lease.status is TaskLeaseStatus.ACTIVE
            and lease.task_revision == task_revision
            and self.control.verify_lease(
                self._token(lease),
                now_ms=_milliseconds(observed_at),
            )
        )

    def renew(self, lease: TaskLease, *, now: datetime | None = None) -> TaskLease:
        renewed_at = (now or datetime.now(UTC)).astimezone(UTC)
        token = self.control.renew_lease(
            self._token(lease),
            ttl_ms=self.ttl_ms,
            now_ms=_milliseconds(renewed_at),
        )
        return lease.model_copy(update={"expires_at": _datetime(token.expires_at_ms)})

    def rebind(
        self,
        lease: TaskLease,
        *,
        task_generation_id: str,
        task_revision: int,
        now: datetime | None = None,
    ) -> TaskLease:
        """Bind an acquired fence to the post-checkout immutable task revision."""

        observed_at = (now or datetime.now(UTC)).astimezone(UTC)
        if not self.control.verify_lease(
            self._token(lease),
            now_ms=_milliseconds(observed_at),
        ):
            raise RuntimeError("Task lease changed before task-version binding")
        identity = {
            "task_id": lease.task_id,
            "employee_id": lease.employee_id,
            "run_id": lease.run_id,
            "task_generation_id": task_generation_id,
            "task_revision": task_revision,
            "control_epoch": lease.control_epoch,
            "fencing_token": lease.fencing_token,
        }
        return lease.model_copy(
            update={
                "lease_id": derive_id("tlease", identity),
                "task_generation_id": task_generation_id,
                "task_revision": task_revision,
            }
        )

    def release(self, lease: TaskLease) -> TaskLease:
        released = self.control.release_lease(self._token(lease))
        status = TaskLeaseStatus.RELEASED if released else TaskLeaseStatus.REVOKED
        return lease.model_copy(update={"status": status})
