from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.runs.api import (
    WorkerDispatchBindingService,
    WorkerDispatchContextPorts,
    WorkerDispatchExecutionPorts,
)


@pytest.mark.asyncio
async def test_dispatch_binding_starts_attempt_and_lease_on_one_connection():
    events = []

    @asynccontextmanager
    async def transaction():
        conn = object()
        events.append(("transaction_enter", conn))
        yield conn
        events.append(("transaction_commit", conn))

    async def fence(conn, **kwargs):
        events.append(("fence", conn))
        return "ready"

    async def materialize(conn, **kwargs):
        events.append(("context", conn))
        return ({
            "file_ids": ["file"], "context_snapshot_id": "snapshot",
            "context_snapshot": {}, "conversation_context": {},
        }, None)

    def compile_spec(**kwargs):
        events.append(("compile", None))
        assert kwargs["queue_payload"].file_ids == ["file"]
        return "spec"

    @dataclass(frozen=True)
    class ProjectedPayload:
        owner_generation: int = 0

    async def attach_mcp(conn, **kwargs):
        events.append(("attach_mcp", conn))
        return kwargs["run_payload"]

    async def append_event(conn, **kwargs):
        events.append((kwargs["event_type"], conn))

    async def create_lease(conn, **kwargs):
        events.append(("create_lease", conn))
        assert kwargs["owner_generation"] == 7
        return {"id": "lease"}

    async def should_not_fail(*args, **kwargs):
        raise AssertionError("Successful binding cannot fail before dispatch")

    class Attempt:
        async def bind_execution_spec(self, conn, spec):
            assert spec == "spec"
            events.append(("bind_attempt", conn))
            return {"owner_generation": 7}

    class Payload:
        tenant_id = "tenant"
        run_id = "run"
        context_snapshot_id = "snapshot"
        executor_type = "fake"
        file_ids = []

        def model_copy(self, *, update):
            self.file_ids = update["file_ids"]
            return self

    service = WorkerDispatchBindingService(
        transaction_factory=transaction, dispatch_fence=fence, compile_spec=compile_spec,
        context=WorkerDispatchContextPorts(
            materialize=materialize, project=lambda row: row,
            execution_pack=lambda snapshot: {},
        ),
        execution=WorkerDispatchExecutionPorts(
            project_spec=lambda *args, **kwargs: ProjectedPayload(),
            attach_mcp=attach_mcp, mcp_runtime_error=ValueError,
            append_user_event=append_event,
            runtime_evidence=lambda **kwargs: {},
            uses_runtime_sandbox=lambda *args, **kwargs: False,
            create_runtime_lease=create_lease, missing_attempt_error=RuntimeError,
        ),
        fail_pre_dispatch=should_not_fail,
    )
    result = await service.bind(
        payload=Payload(), run_identity={"tenant_id": "tenant", "run_id": "run"},
        locked_run={}, trace_id="trace", attempt_id="attempt",
        attempt_authority=Attempt(), capabilities=object(), principal=SimpleNamespace(),
        worker_id="worker", reconciliation=False,
    )

    assert result.outcome is None
    assert result.run_payload.owner_generation == 7
    assert result.runtime_sandbox_lease == {"id": "lease"}
    assert [event for event, _ in events] == [
        "transaction_enter", "fence", "context", "compile", "attach_mcp",
        "bind_attempt", "worker_started", "create_lease", "transaction_commit",
    ]
    assert len({conn for event, conn in events if conn is not None}) == 1
