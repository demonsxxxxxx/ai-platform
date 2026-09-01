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
