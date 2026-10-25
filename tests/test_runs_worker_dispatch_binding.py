import asyncio
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "private_reason", "public_code"),
    [
        ("model", "execution_spec_model_snapshot_mismatch", "execution_spec_invalid"),
        ("credential", "execution_spec_credential_material_forbidden", "execution_spec_invalid"),
        ("unknown", None, "execution_spec_invalid"),
        ("mcp", None, "mcp_runtime_configuration_unavailable"),
        ("cancelled", None, None),
    ],
)
async def test_dispatch_keeps_typed_codec_reason_only_in_private_diagnostics(
    failure, private_reason, public_code,
):
    from app.execution.api import predispatch_failure_result
    from app.runs.api import WorkerEarlyFailureService, compile_execution_spec_for_dispatch

    failures, events = [], []
    marker = "SYNTHETIC_PRIVATE_VALUE_NOT_FOR_DIAGNOSTICS"

    @asynccontextmanager
    async def transaction():
        yield object()

    async def fence(*args, **kwargs):
        return "ready"

    async def materialize(*args, **kwargs):
        return ({
            "file_ids": [], "context_snapshot_id": "snapshot",
            "context_snapshot": {"context_snapshot_id": "snapshot"},
            "conversation_context": {},
        }, None)

    async def append_event(conn, **kwargs):
        events.append(kwargs)

    async def forbidden(*args, **kwargs):
        raise AssertionError("Rejected binding cannot dispatch, audit or start an Attempt")

    class McpError(ValueError):
        code = "mcp_runtime_configuration_unavailable"

    async def attach_mcp(*args, **kwargs):
        if failure == "mcp":
            raise McpError(marker)
        raise AssertionError("Invalid specification cannot attach MCP configuration")

    def compile_spec(**kwargs):
        if failure == "cancelled":
            raise asyncio.CancelledError(marker)
        if failure == "unknown":
            raise ValueError(marker)
        return compile_execution_spec_for_dispatch(**kwargs)

    class Payload(SimpleNamespace):
        def model_copy(self, *, update):
            return Payload(**{**vars(self), **update})

    payload = Payload(
        schema_version="ai-platform.run-payload.v2", skill_id=None,
        file_ids=[], input={"password": marker} if failure == "credential" else {},
        executor_type="synthetic", context_snapshot_id="snapshot",
        skill_version="", release_decision={}, skill_manifests=[],
        model_id="model-a", model_value="model-a", agent_profile={},
    )
    model = {
        "model_id": "model-a", "model_value": "other" if failure == "model" else "model-a",
        "model_gateway_revision": 1, "auto_compact_window": 200_000, "max_output_tokens": 2048,
    }

    class Attempt:
        async def fail(self, conn, **kwargs):
            failures.append(kwargs)
            return True

        bind_execution_spec = forbidden

    early = WorkerEarlyFailureService(
        fail_result=predispatch_failure_result, append_event=append_event,
        record_denial_audit=forbidden, locked_principal=lambda *args: None,
        denied_capability=lambda *args: None,
    )
    service = WorkerDispatchBindingService(
        transaction_factory=transaction, dispatch_fence=fence, compile_spec=compile_spec,
        context=WorkerDispatchContextPorts(materialize, lambda row: row, lambda snapshot: {}),
        execution=WorkerDispatchExecutionPorts(
            project_spec=lambda *args, **kwargs: object(), attach_mcp=attach_mcp,
            mcp_runtime_error=McpError, append_user_event=forbidden,
            runtime_evidence=lambda **kwargs: {}, uses_runtime_sandbox=lambda *args, **kwargs: True,
            create_runtime_lease=forbidden, missing_attempt_error=RuntimeError,
        ),
        fail_pre_dispatch=early.pre_dispatch_error,
    )
    binding = service.bind(
        payload=payload,
        run_identity={
            "tenant_id": "tenant", "workspace_id": "workspace", "user_id": "user",
            "session_id": "session", "run_id": "run", "agent_id": "agent",
            "execution_kind": "harness_chat", "skill_id": "",
        },
        locked_run=model, trace_id="trace", attempt_id="attempt",
        attempt_authority=Attempt(), capabilities=object(), principal=object(),
        worker_id="worker", reconciliation=False,
    )

    if failure == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await binding
        assert failures == events == []
        return
    outcome = await binding
    assert outcome.outcome.status == "failed"
    assert outcome.outcome.error_code == public_code
    assert outcome.publish_after_commit is True
    assert len(failures) == len(events) == 1
    assert failures[0]["error_code"] == public_code
    diagnostic = failures[0]["result_json"]["runtime_diagnostics"]
    assert diagnostic["sdk"]["errors"] == [private_reason or public_code]
    assert diagnostic["failure_source"] == "worker_pre_dispatch"
    assert diagnostic["failure_stage"] == "worker"
    assert events[0]["payload"]["error_code"] == public_code
    if private_reason:
        assert private_reason not in repr(events)
        assert private_reason not in repr(outcome.outcome)
    assert marker not in repr((failures, events, outcome.outcome))
