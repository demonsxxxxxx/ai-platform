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
