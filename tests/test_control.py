from __future__ import annotations

from pathlib import Path

import pytest

from raytsystem.control import ControlDB, LeaseBusy


def test_control_database_retries_only_a_bounded_transient_open_failure(
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from raytsystem import control as control_module

    original = control_module.sqlite3.connect
    calls = 0

    def transient(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise control_module.sqlite3.OperationalError("disk I/O error")
        return original(*args, **kwargs)

    monkeypatch.setattr(control_module.sqlite3, "connect", transient)
    database = ControlDB(project_root / "ops" / "control.sqlite")

    assert calls == 2
    assert database.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_operation_key_is_unique_and_joins_existing_run(project_root: Path) -> None:
    db = ControlDB(project_root / "ops" / "control.sqlite")

    first = db.claim_operation(
        operation_key="op_example",
        run_id="run_first",
        stage="ingest",
        partition_key="source:example",
        now_ms=1000,
    )
    second = db.claim_operation(
        operation_key="op_example",
        run_id="run_second",
        stage="ingest",
        partition_key="source:example",
        now_ms=2000,
    )

    assert first.created is True
    assert second.created is False
    assert second.run_id == "run_first"
    run_count = db.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert run_count == 1


def test_fencing_token_increases_and_stale_owner_is_rejected(project_root: Path) -> None:
    db = ControlDB(project_root / "ops" / "control.sqlite")

    lease_a = db.acquire_lease(
        partition_key="ledger:current",
        owner_run_id="run_a",
        ttl_ms=100,
        now_ms=1000,
    )
    lease_b = db.acquire_lease(
        partition_key="ledger:current",
        owner_run_id="run_b",
        ttl_ms=100,
        now_ms=1101,
    )

    assert lease_b.fencing_token > lease_a.fencing_token
    assert not db.verify_lease(lease_a, now_ms=1101)
    assert db.verify_lease(lease_b, now_ms=1101)


def test_lease_row_survives_release_and_next_acquire_increments_fence(project_root: Path) -> None:
    db = ControlDB(project_root / "ops" / "control.sqlite")
    first = db.acquire_lease("source:one", "run_a", ttl_ms=100, now_ms=1000)
    assert db.release_lease(first)

    second = db.acquire_lease("source:one", "run_b", ttl_ms=100, now_ms=1001)

    assert second.fencing_token == first.fencing_token + 1


def test_live_lease_cannot_be_stolen(project_root: Path) -> None:
    db = ControlDB(project_root / "ops" / "control.sqlite")
    db.acquire_lease("ledger:current", "run_a", ttl_ms=100, now_ms=1000)

    with pytest.raises(LeaseBusy):
        db.acquire_lease("ledger:current", "run_b", ttl_ms=100, now_ms=1050)


def test_stale_lease_cannot_enter_promotion_guard(project_root: Path) -> None:
    db = ControlDB(project_root / "ops" / "control.sqlite")
    lease_a = db.acquire_lease("ledger:current", "run_a", ttl_ms=100, now_ms=1000)
    db.acquire_lease("ledger:current", "run_b", ttl_ms=100, now_ms=1101)

    with (
        pytest.raises(LeaseBusy, match="stale or expired"),
        db.hold_valid_leases((lease_a,), now_ms=1101),
    ):
        raise AssertionError("guard must not yield")


def test_lease_renewal_preserves_fence_and_extends_expiry(project_root: Path) -> None:
    db = ControlDB(project_root / "ops" / "control.sqlite")
    lease = db.acquire_lease("ledger:current", "run_a", ttl_ms=100, now_ms=1000)

    renewed = db.renew_lease(lease, ttl_ms=200, now_ms=1050)

    assert renewed.fencing_token == lease.fencing_token
    assert renewed.expires_at_ms == 1250
    assert db.verify_lease(renewed, now_ms=1200)


def test_promotion_guard_renews_lease_before_releasing_writer_lock(
    project_root: Path,
) -> None:
    db = ControlDB(project_root / "ops" / "control.sqlite")
    lease = db.acquire_lease("ledger:current", "run_a", ttl_ms=100, now_ms=1000)

    with db.hold_valid_leases(
        (lease,),
        now_ms=1050,
        renew_ttl_ms=200,
    ) as (renewed,):
        assert renewed.expires_at_ms == 1250

    assert db.verify_lease(renewed, now_ms=1200)


def test_continuous_writer_guard_can_heartbeat_after_long_validation(
    project_root: Path,
) -> None:
    db = ControlDB(project_root / "ops" / "control.sqlite")
    lease = db.acquire_lease("ledger:current", "run_a", ttl_ms=100, now_ms=1000)

    with db.hold_valid_leases(
        (lease,),
        now_ms=1050,
        renew_ttl_ms=20,
    ) as guarded:
        (renewed,) = db.renew_held_leases(guarded, ttl_ms=200, now_ms=1100)

    assert renewed.expires_at_ms == 1300
    assert db.verify_lease(renewed, now_ms=1200)
