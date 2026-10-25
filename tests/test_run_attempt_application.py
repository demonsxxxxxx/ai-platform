from datetime import datetime, timedelta, timezone

import pytest

from app.runs.application.attempt_lifecycle import RunAttemptLifecycleService


class AttemptPersistenceStub:
    def __init__(self, attempt=None):
        self.attempt = attempt
        self.calls = []

    async def get_latest_run_attempt(self, conn, **kwargs):
        self.calls.append(("get_latest", conn, kwargs))
        return self.attempt

    async def request_run_attempt_cancel(self, conn, **kwargs):
        self.calls.append(("request_cancel", conn, kwargs))
        return {**self.attempt, "status": "cancel_requested"}

    async def terminalize_run_attempt(self, conn, **kwargs):
        self.calls.append(("terminalize", conn, kwargs))
        return {"id": kwargs["attempt_id"], "status": kwargs["status"]}

    async def assert_worker_run_attempt_current(self, conn, **kwargs):
        self.calls.append(("assert_worker_current", conn, kwargs))
        return self.attempt

    async def heartbeat_worker_run_attempt(self, conn, **kwargs):
        self.calls.append(("heartbeat_worker", conn, kwargs))
        return {**self.attempt, **kwargs}


@pytest.mark.asyncio
async def test_two_lifecycle_instances_keep_their_persistence_dependencies_isolated():
    first_persistence = AttemptPersistenceStub({"id": "rat-a", "status": "running"})
    second_persistence = AttemptPersistenceStub({"id": "rat-b", "status": "running"})
    first_service = RunAttemptLifecycleService(persistence=first_persistence)
    second_service = RunAttemptLifecycleService(persistence=second_persistence)
    first_connection = object()
    second_connection = object()

    first = await first_service.get_latest(
        first_connection,
        tenant_id="tenant-a",
        run_id="run-a",
    )
    second = await second_service.get_latest(
        second_connection,
        tenant_id="tenant-b",
        run_id="run-b",
    )
    await first_service.request_cancel(
        first_connection,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="rat-a",
    )
    await second_service.terminalize(
        second_connection,
        tenant_id="tenant-b",
        run_id="run-b",
        attempt_id="rat-b",
        status="succeeded",
        terminal_reason="completed",
    )

    assert first == {"id": "rat-a", "status": "running"}
    assert second == {"id": "rat-b", "status": "running"}
    assert first_persistence.calls == [
        (
            "get_latest",
            first_connection,
            {"tenant_id": "tenant-a", "run_id": "run-a"},
        ),
        (
            "request_cancel",
            first_connection,
            {
                "tenant_id": "tenant-a",
                "run_id": "run-a",
                "attempt_id": "rat-a",
            },
        ),
    ]
    assert second_persistence.calls == [
        (
            "get_latest",
            second_connection,
            {"tenant_id": "tenant-b", "run_id": "run-b"},
        ),
        (
            "terminalize",
            second_connection,
            {
                "tenant_id": "tenant-b",
                "run_id": "run-b",
                "attempt_id": "rat-b",
                "status": "succeeded",
                "terminal_reason": "completed",
            },
        ),
    ]


@pytest.mark.asyncio
async def test_terminalize_latest_run_attempt_projects_failed_run_authority():
    persistence = AttemptPersistenceStub(
        {"id": "rat-a", "status": "running"}
    )
    service = RunAttemptLifecycleService(persistence=persistence)

    result = await service.terminalize_latest(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        status="failed",
        terminal_reason="multi_agent_parent_failed",
        error_code="multi_agent_child_failed",
    )

    assert result == {"id": "rat-a", "status": "failed"}
    assert [call[0] for call in persistence.calls] == ["get_latest", "terminalize"]
    assert persistence.calls[-1][2] == {
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "attempt_id": "rat-a",
        "status": "failed",
        "terminal_reason": "multi_agent_parent_failed",
        "error_code": "multi_agent_child_failed",
    }


@pytest.mark.asyncio
async def test_terminalize_latest_run_attempt_prepares_cancel_before_terminal():
    persistence = AttemptPersistenceStub(
        {"id": "rat-a", "status": "running"}
    )
    service = RunAttemptLifecycleService(persistence=persistence)

    result = await service.terminalize_latest(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        status="cancelled",
        terminal_reason="multi_agent_parent_cancelled",
    )

    assert result == {"id": "rat-a", "status": "cancelled"}
    assert [call[0] for call in persistence.calls] == [
        "get_latest",
        "request_cancel",
        "terminalize",
    ]


@pytest.mark.asyncio
async def test_terminalize_latest_run_attempt_is_legacy_compatible_without_attempt():
    persistence = AttemptPersistenceStub()
    service = RunAttemptLifecycleService(persistence=persistence)

    result = await service.terminalize_latest(
        object(),
        tenant_id="tenant-a",
        run_id="legacy-run",
        status="succeeded",
        terminal_reason="multi_agent_parent_succeeded",
    )

    assert result is None
    assert [call[0] for call in persistence.calls] == ["get_latest"]


@pytest.mark.asyncio
async def test_heartbeat_worker_run_attempt_uses_the_locked_owner_generation():
    persistence = AttemptPersistenceStub(
        {
            "id": "rat-a",
            "status": "running",
            "owner_generation": 4,
        }
    )
    service = RunAttemptLifecycleService(persistence=persistence)
    last_heartbeat_at = datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc)
    lease_expires_at = last_heartbeat_at + timedelta(minutes=15)

    result = await service.heartbeat_worker(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        queue_attempt_id="qat-a",
        queue_message_id="b" * 64,
        worker_id="worker-a",
        last_heartbeat_at=last_heartbeat_at,
        lease_expires_at=lease_expires_at,
    )

    assert result["expected_owner_generation"] == 4
    assert [call[0] for call in persistence.calls] == [
        "assert_worker_current",
        "heartbeat_worker",
    ]
    assert persistence.calls[-1][2] == {
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "attempt_id": "rat-a",
        "queue_attempt_id": "qat-a",
        "queue_message_id": "b" * 64,
        "worker_id": "worker-a",
        "expected_owner_generation": 4,
        "last_heartbeat_at": last_heartbeat_at,
        "lease_expires_at": lease_expires_at,
    }


@pytest.mark.asyncio
async def test_heartbeat_worker_run_attempt_waits_for_attempt_creation():
    persistence = AttemptPersistenceStub()
    service = RunAttemptLifecycleService(persistence=persistence)
    last_heartbeat_at = datetime(2026, 8, 30, 8, 0, tzinfo=timezone.utc)

    result = await service.heartbeat_worker(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        queue_attempt_id="qat-a",
        queue_message_id="b" * 64,
        worker_id="worker-a",
        last_heartbeat_at=last_heartbeat_at,
        lease_expires_at=last_heartbeat_at + timedelta(minutes=15),
    )

    assert result is None
    assert [call[0] for call in persistence.calls] == ["assert_worker_current"]


@pytest.mark.asyncio
async def test_settlement_writers_fence_source_locks_and_marker_before_projecting():
    calls = []
    conn = object()

    class Projection:
        async def lock_writer_fence(self, _conn):
            calls.append("writer_fence")

        async def lock_sources_in_transaction(self, _conn, **_kwargs):
            calls.append("source_locks")

        async def writes_enabled(self, _conn, *, shadow_enabled):
            calls.append(("marker", shadow_enabled))
            return True

        async def refresh_in_transaction(self, _conn, **_kwargs):
            calls.append("refresh")

    class Persistence(AttemptPersistenceStub):
        async def get_run(self, _conn, **kwargs):
            calls.append(("run_lock", kwargs["for_update"]))
            return {"id": kwargs["run_id"]}

        async def start_worker_run_attempt(self, _conn, **_kwargs):
            calls.append("start_write")
            return {"id": "rat-a", "tenant_id": "tenant-a", "run_id": "run-a"}

        async def prepare_stale_run_attempt_reconciliation(self, _conn, **_kwargs):
            calls.append("stale_write")
            return {"id": "rat-a", "status": "expired"}

        async def get_latest_run_attempt(self, _conn, **kwargs):
            calls.append(("get_latest", kwargs.get("for_update", False)))
            return {"id": "rat-a", "status": "running"}

        async def request_run_attempt_cancel(self, _conn, **_kwargs):
            calls.append("cancel_write")
            return {"id": "rat-a", "status": "cancel_requested"}

        async def terminalize_run_attempt(self, _conn, **kwargs):
            calls.append("terminal_write")
            return {"id": "rat-a", "status": kwargs["status"]}

    service = RunAttemptLifecycleService(
        persistence=Persistence({"id": "rat-a", "status": "running"}),
        settlement_projection=Projection(),
        settlement_shadow_enabled=lambda: False,
    )
    common = {"tenant_id": "tenant-a", "run_id": "run-a", "attempt_id": "rat-a"}

    await service.start_worker(conn, **common)
    assert calls == [
        "writer_fence", "start_write", "writer_fence", "source_locks",
        ("marker", False), "refresh",
    ]

    calls.clear()
    await service.request_cancel(conn, **common)
    assert calls == [
        "writer_fence", "source_locks", ("marker", False), "cancel_write", "refresh",
    ]

    calls.clear()
    await service.terminalize(
        conn, **common, status="failed", terminal_reason="run_failed"
    )
    assert calls == [
        "writer_fence", "source_locks", ("marker", False), "terminal_write", "refresh",
    ]

    calls.clear()
    await service.prepare_stale_reconciliation(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        terminal_status="failed",
        reconciler_id="stale-maintenance",
    )
    assert calls == [
        "writer_fence", ("run_lock", True), ("get_latest", False),
        "writer_fence", "source_locks", ("marker", False), "stale_write", "refresh",
    ]

    calls.clear()
    await service.terminalize_latest(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        status="failed",
        terminal_reason="run_failed",
    )
    assert calls == [
        "writer_fence", ("run_lock", True), ("get_latest", False),
        "writer_fence", "source_locks", ("marker", False), ("get_latest", True),
        "writer_fence", "source_locks", ("marker", False), "terminal_write", "refresh",
    ]
