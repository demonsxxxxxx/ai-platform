from contextlib import asynccontextmanager

import pytest

from app.sandbox.application.cancel_delivery import SandboxCancelDeliveryService


class FakeRepository:
    def __init__(self, rows=(), *, cursors=()):
        self.rows = list(rows)
        self.cursors = list(cursors)
        self.claim_calls = []
        self.staged = []
        self.results = []

    async def stage_in_transaction(self, conn, **scope):
        self.staged.append((conn, scope))
        return 1

    async def claim_due(self, conn, **kwargs):
        self.claim_calls.append(kwargs)
        next_cursor = self.cursors.pop(0) if self.cursors else None
        return list(self.rows), next_cursor

    async def record_delivery_result(self, conn, **kwargs):
        self.results.append((conn, kwargs))
        return "accepted" if kwargs["accepted"] else "retry"


def _service(repository, send_cancel):
    @asynccontextmanager
    async def transaction():
        yield object()

    return SandboxCancelDeliveryService(repository, transaction, send_cancel)


@pytest.mark.asyncio
async def test_cancel_delivery_stages_in_caller_transaction_and_accepts_exact_response():
    row = {
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "cancel_delivery_claim_token": "claim-a",
    }
    repository = FakeRepository([row])
    service = _service(
        repository,
        lambda _row: _exact_response("run-a", "attempt-a"),
    )
    conn = object()

    assert await service.stage_in_transaction(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
    ) == 1
    assert repository.staged == [
        (conn, {"tenant_id": "tenant-a", "run_id": "run-a", "attempt_id": "attempt-a"})
    ]
    assert await service.deliver_once(tenant_id="tenant-a", run_id="run-a") == 1
    result = repository.results[0][1]
    assert result["accepted"] is True
    assert result["error_code"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"status": "cancelled", "run_id": "run-a", "attempt_id": "attempt-a"},
        {"status": "cancel_requested", "run_id": "run-other", "attempt_id": "attempt-a"},
        {"status": "cancel_requested", "run_id": "run-a", "attempt_id": "attempt-a", "extra": True},
    ],
)
async def test_cancel_delivery_requires_exact_ack_identity_and_shape(response):
    repository = FakeRepository([{
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "cancel_delivery_claim_token": "claim-a",
    }])
    service = _service(repository, lambda _row: _return(response))

    assert await service.deliver_once() == 1
    result = repository.results[0][1]
    assert result["accepted"] is False
    assert result["error_code"] == "invalid_response"


@pytest.mark.asyncio
async def test_cancel_delivery_records_only_a_bounded_error_code_after_provider_failure():
    repository = FakeRepository([{
        "run_id": "run-a",
        "attempt_id": "attempt-a",
        "cancel_delivery_claim_token": "claim-a",
    }])

    async def fail(_row):
        raise RuntimeError("contains secret response body")

    service = _service(repository, fail)
    assert await service.deliver_once() == 1
    result = repository.results[0][1]
    assert result["accepted"] is False
    assert result["error_code"] == "provider_error"
    assert "secret response body" not in repr(result)


@pytest.mark.asyncio
async def test_global_scan_cursor_is_independent_of_scoped_delivery():
    cursor = ("tenant-a", "run-a", "lease-a")
    repository = FakeRepository(cursors=[cursor, None, None])
    service = _service(repository, lambda _row: _return({}))

    assert await service.deliver_once() == 0
    assert await service.deliver_once(tenant_id="tenant-b") == 0
    assert await service.deliver_once() == 0

    assert repository.claim_calls[0]["after_lease_key"] is None
    assert repository.claim_calls[1]["after_lease_key"] is None
    assert repository.claim_calls[2]["after_lease_key"] == cursor
    assert repository.claim_calls[0]["scan_budget"] == repository.claim_calls[1]["scan_budget"]


async def _return(value):
    return value


async def _exact_response(run_id, attempt_id):
    return {"status": "cancel_requested", "run_id": run_id, "attempt_id": attempt_id}
