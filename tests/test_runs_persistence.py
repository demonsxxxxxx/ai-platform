import json
import pytest
import app.conversations.infrastructure.postgres as _repo_owner_app_conversations_infrastructure_postgres
import app.identity.infrastructure.audit_postgres as _repo_owner_app_identity_infrastructure_audit_postgres
import app.identity.infrastructure.postgres as _repo_owner_app_identity_infrastructure_postgres
import app.platform.postgres.errors as _repo_owner_app_platform_postgres_errors
import app.runs.infrastructure.creation_postgres as _repo_owner_app_runs_infrastructure_creation_postgres
import app.sandbox.infrastructure.leases_postgres as _repo_owner_app_sandbox_infrastructure_leases_postgres
import app.streaming.infrastructure.run_events_postgres as _repo_owner_app_streaming_infrastructure_run_events_postgres
import app.runs.infrastructure.creation_postgres as run_creation_persistence
import app.sandbox.infrastructure.leases_postgres as sandbox_leases_persistence
from app.streaming import postgres as event_ledger
from app.conversations.infrastructure import postgres as conversation_persistence
from app.platform.public_payload import sanitize_public_payload, sanitize_public_text
from app.platform.tracing import standard_trace_id
from app.runs.application.lifecycle import RunLifecycleService
from app.runs.infrastructure.lifecycle_postgres import PostgresRunLifecyclePersistence, require_run_result_size
from app.platform.postgres.errors import RepositoryConflictError, RepositoryNotFoundError
from app.runs.infrastructure.creation_postgres import create_run
from app.runs.infrastructure.postgres import count_active_runs_for_user
from app.runs.infrastructure.steps_postgres import upsert_run_step
from app.streaming.infrastructure.run_events_postgres import append_event, list_run_events
from tests.support.repository_fixtures import FakeConnection, RecordingConnection, SingleRowConnection, SingleRowCursor


@pytest.mark.asyncio
async def test_tenant_exists_checks_tenant_identity():
    class ExistingTenantCursor:
        async def fetchone(self):
            return {"exists": 1}

    class MissingTenantCursor:
        async def fetchone(self):
            return None

    class TenantConnection:
        def __init__(self, cursor):
            self.cursor = cursor
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return self.cursor

    existing_conn = TenantConnection(ExistingTenantCursor())
    missing_conn = TenantConnection(MissingTenantCursor())

    assert await _repo_owner_app_identity_infrastructure_postgres.tenant_exists(existing_conn, tenant_id="tenant-a") is True
    assert "from tenants where id = %s" in existing_conn.sql
    assert existing_conn.params == ("tenant-a",)
    assert await _repo_owner_app_identity_infrastructure_postgres.tenant_exists(missing_conn, tenant_id="tenant-b") is False
    assert missing_conn.params == ("tenant-b",)


@pytest.mark.asyncio
async def test_count_active_runs_for_user_counts_queued_and_running_only():
    conn = FakeConnection()

    count = await count_active_runs_for_user(conn, tenant_id="tenant-a", user_id="user-a")

    assert count == 2
    assert "status in ('queued', 'running')" in conn.sql
    assert conn.params == ("tenant-a", "user-a")


@pytest.mark.asyncio


@pytest.mark.asyncio


@pytest.mark.asyncio


@pytest.mark.asyncio


@pytest.mark.asyncio
async def test_record_sandbox_runtime_cleanup_outcome_writes_event_and_audit(monkeypatch):
    calls = []

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-cleanup"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-cleanup"

    monkeypatch.setattr(sandbox_leases_persistence, "append_event", fake_append_event)
    monkeypatch.setattr(sandbox_leases_persistence, "append_audit_log", fake_append_audit_log)

    await _repo_owner_app_sandbox_infrastructure_leases_postgres.record_sandbox_runtime_cleanup_outcome(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        trace_id="trace-a",
        user_id="user-a",
        requested_by_role="owner",
        reason="cancel_requested",
        status="failed",
        lease_ids=["lease-a"],
        failures=[{"container_id": "lease-a", "message": "runtime handle missing"}],
    )

    assert calls == [
        (
            "event",
            {
                "tenant_id": "tenant-a",
                "run_id": "run-a",
                "trace_id": "trace-a",
                "event_type": "sandbox_runtime_cleanup_failed",
                "stage": "sandbox",
                "message": "Sandbox runtime cleanup failed",
                "payload": {
                    "visible_to_user": False,
                    "reason": "cancel_requested",
                    "status": "failed",
                    "lease_ids": ["lease-a"],
                    "failure_count": 1,
                    "requested_by_role": "owner",
                    "failures": [{"container_id": "lease-a", "message": "runtime handle missing"}],
                },
            },
        ),
        (
            "audit",
            {
                "tenant_id": "tenant-a",
                "user_id": "user-a",
                "action": "sandbox.runtime.cleanup.failed",
                "target_type": "run",
                "target_id": "run-a",
                "trace_id": "trace-a",
                "payload_json": {
                    "run_id": "run-a",
                    "reason": "cancel_requested",
                    "status": "failed",
                    "lease_ids": ["lease-a"],
                    "failures": [{"container_id": "lease-a", "message": "runtime handle missing"}],
                    "requested_by_role": "owner",
                },
            },
        ),
    ]


@pytest.mark.asyncio
async def test_create_run_persists_g2_contract_fields():
    conn = RecordingConnection()

    run_id = await create_run(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        session_id="session-a",
        user_id="user-a",
        agent_id="general-agent",
        skill_id="general-chat",
        input_json={},
    )

    sql, params = conn.calls[-1]
    assert run_id.startswith("run_")
    assert "trace_id" in sql
    assert "schema_version" in sql
    assert "executor_schema_version" in sql
    assert "principal_roles" in sql
    assert any(str(item).startswith("trace_") for item in params)
    assert "ai-platform.run.v1" in params
    assert "ai-platform.executor-result.v1" in params


@pytest.mark.asyncio
async def test_create_run_persists_skillless_harness_identity():
    conn = RecordingConnection()

    await create_run(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        session_id="session-a",
        user_id="user-a",
        agent_id="general-agent",
        execution_kind="harness_chat",
        skill_id=None,
        input_json={"execution_kind": "harness_chat"},
    )

    sql, params = conn.calls[-1]
    assert "execution_kind, skill_id" in sql
    assert "harness_chat" in params
    harness_index = params.index("harness_chat")
    assert params[harness_index + 1] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("execution_kind", "skill_id"),
    [
        ("harness_chat", "general-chat"),
        ("skill", None),
        ("unknown", None),
    ],
)
async def test_create_run_rejects_execution_skill_identity_mismatch(
    execution_kind,
    skill_id,
):
    with pytest.raises(
        _repo_owner_app_platform_postgres_errors.RepositoryConflictError,
        match="run_execution_skill_identity_mismatch",
    ):
        await create_run(
            RecordingConnection(),
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            session_id="session-a",
            user_id="user-a",
            agent_id="general-agent",
            execution_kind=execution_kind,
            skill_id=skill_id,
            input_json={},
        )


@pytest.mark.asyncio
async def test_create_run_binds_normalized_auth_snapshot():
    conn = RecordingConnection()

    await create_run(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        session_id="session-a",
        user_id="user-a",
        agent_id="general-agent",
        skill_id="general-chat",
        input_json={},
        principal_roles=[" QA-Operator ", "qa operator", "User"],
        principal_department_id="qa",
        auth_source="trusted-header",
    )

    sql, params = conn.calls[-1]
    assert "principal_roles, principal_department_id, auth_source" in sql
    assert json.dumps(["qa-operator", "qa operator", "user"], ensure_ascii=False) in params
    assert "qa" in params
    assert "trusted-header" in params


@pytest.mark.asyncio
async def test_session_generation_allocator_serializes_allocation_at_the_session_row():
    class GenerationConnection:
        def __init__(self):
            self.calls = []
            self.next_generation = 0

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            self.next_generation += 1
            return SingleRowCursor({"next_run_generation": self.next_generation})

    conn = GenerationConnection()
    first = await _repo_owner_app_runs_infrastructure_creation_postgres.allocate_session_run_generation(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="general-agent",
    )
    second = await _repo_owner_app_runs_infrastructure_creation_postgres.allocate_session_run_generation(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        agent_id="general-agent",
    )

    assert (first, second) == (1, 2)
    sql, params = conn.calls[0]
    assert sql.startswith("update sessions set next_run_generation = next_run_generation + 1")
    assert "user_id is not distinct from %s" in sql
    assert "returning next_run_generation" in sql
    assert params == ("tenant-a", "workspace-a", "user-a", "session-a", "general-agent")


@pytest.mark.asyncio
async def test_create_session_validates_workspace_tenant_before_insert(monkeypatch):
    calls = []

    async def ensure_workspace_belongs_to_tenant(conn, *, tenant_id, workspace_id):
        calls.append(("ensure_workspace", tenant_id, workspace_id, len(conn.calls)))

    monkeypatch.setattr(
        conversation_persistence,
        "ensure_workspace_belongs_to_tenant",
        ensure_workspace_belongs_to_tenant,
    )
    conn = RecordingConnection()

    await _repo_owner_app_conversations_infrastructure_postgres.create_session(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        title="General",
    )

    assert calls == [("ensure_workspace", "tenant-a", "workspace-a", 0)]
    assert conn.calls[-1][0].startswith("insert into sessions")


@pytest.mark.asyncio
async def test_create_session_conflict_is_atomic_and_requires_exact_binding(monkeypatch):
    async def ensure_workspace_belongs_to_tenant(_conn, *, tenant_id, workspace_id):
        assert (tenant_id, workspace_id) == ("tenant-a", "workspace-a")

    monkeypatch.setattr(
        conversation_persistence,
        "ensure_workspace_belongs_to_tenant",
        ensure_workspace_belongs_to_tenant,
    )
    conn = SingleRowConnection(None)

    with pytest.raises(RepositoryConflictError, match="session_scope_mismatch"):
        await _repo_owner_app_conversations_infrastructure_postgres.create_session(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id="agent-a",
            title="Agent A",
            session_id="session-shared",
            admitted_agent_profile_revision=3,
            admitted_agent_profile_hash="profile-hash",
        )

    assert conn.sql.startswith("insert into sessions")
    assert "on conflict (id) do update" in conn.sql
    assert "sessions.tenant_id = excluded.tenant_id" in conn.sql
    assert "sessions.workspace_id = excluded.workspace_id" in conn.sql
    assert "sessions.user_id is not distinct from excluded.user_id" in conn.sql
    assert "sessions.agent_id = excluded.agent_id" in conn.sql
    assert "sessions.admitted_agent_profile_revision is not distinct from excluded.admitted_agent_profile_revision" in conn.sql
    assert "sessions.admitted_agent_profile_hash is not distinct from excluded.admitted_agent_profile_hash" in conn.sql
    assert "returning sessions.id" in conn.sql


@pytest.mark.asyncio
async def test_create_session_allows_exact_idempotent_binding(monkeypatch):
    async def ensure_workspace_belongs_to_tenant(_conn, *, tenant_id, workspace_id):
        assert (tenant_id, workspace_id) == ("tenant-a", "workspace-a")

    monkeypatch.setattr(
        conversation_persistence,
        "ensure_workspace_belongs_to_tenant",
        ensure_workspace_belongs_to_tenant,
    )
    conn = SingleRowConnection({"id": "session-shared"})

    session_id = await _repo_owner_app_conversations_infrastructure_postgres.create_session(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id=None,
        agent_id="agent-a",
        title="Agent A",
        session_id="session-shared",
        admitted_agent_profile_revision=None,
        admitted_agent_profile_hash=None,
    )

    assert session_id == "session-shared"
    assert conn.params == (
        "session-shared",
        "tenant-a",
        "workspace-a",
        None,
        "agent-a",
        "Agent A",
        "initial",
        None,
        None,
        "conversation",
    )
    assert "sessions.purpose = excluded.purpose" in conn.sql


@pytest.mark.asyncio
async def test_create_session_reports_whether_an_exact_operation_created_the_row(monkeypatch):
    async def ensure_workspace_belongs_to_tenant(_conn, *, tenant_id, workspace_id):
        assert (tenant_id, workspace_id) == ("tenant-a", "workspace-a")

    monkeypatch.setattr(
        conversation_persistence,
        "ensure_workspace_belongs_to_tenant",
        ensure_workspace_belongs_to_tenant,
    )
    conn = SingleRowConnection({"id": "ses_agent_operation", "created": False})

    result = await _repo_owner_app_conversations_infrastructure_postgres.create_session(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="agent-a",
        title="Agent A",
        session_id="ses_agent_operation",
        admitted_agent_profile_revision=3,
        admitted_agent_profile_hash="profile-hash",
        return_created=True,
    )

    assert result == ("ses_agent_operation", False)
    assert "on conflict (id) do update" in conn.sql
    assert "sessions.title = excluded.title" in conn.sql
    assert "(xmax = 0) as created" in conn.sql


@pytest.mark.asyncio
async def test_create_run_validates_workspace_tenant_before_insert(monkeypatch):
    calls = []

    async def ensure_workspace_belongs_to_tenant(conn, *, tenant_id, workspace_id):
        calls.append(("ensure_workspace", tenant_id, workspace_id, len(conn.calls)))

    monkeypatch.setattr(run_creation_persistence, "ensure_workspace_belongs_to_tenant", ensure_workspace_belongs_to_tenant, raising=False)
    conn = RecordingConnection()

    await _repo_owner_app_runs_infrastructure_creation_postgres.create_run(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        session_id="session-a",
        user_id="user-a",
        agent_id="general-agent",
        skill_id="general-chat",
        input_json={},
    )

    assert calls == [("ensure_workspace", "tenant-a", "workspace-a", 0)]
    assert conn.calls[-1][0].startswith("insert into runs")


@pytest.mark.asyncio
async def test_ensure_workspace_belongs_to_tenant_raises_for_missing_workspace():
    ensure_workspace = _repo_owner_app_conversations_infrastructure_postgres.ensure_workspace_belongs_to_tenant
    assert callable(ensure_workspace), "ensure_workspace_belongs_to_tenant missing"

    class EmptyCursor:
        async def fetchone(self):
            return None

    class WorkspaceConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return EmptyCursor()

    conn = WorkspaceConnection()

    with pytest.raises(RepositoryNotFoundError, match="workspace_not_found"):
        await ensure_workspace(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-b",
        )

    assert conn.calls[0][1] == ("tenant-a", "workspace-b")


@pytest.mark.asyncio
async def test_update_run_auth_snapshot_normalizes_roles_and_scopes_update():
    conn = RecordingConnection()

    await _repo_owner_app_runs_infrastructure_creation_postgres.update_run_auth_snapshot(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        principal_roles=[" QA-Operator ", "qa operator", "User"],
        principal_department_id="qa",
        auth_source="trusted-header",
        authz_policy_version=7,
        authority_source="identity-gateway",
        authority_checked_at="2026-08-12T01:02:03Z",
    )

    sql, params = conn.calls[-1]
    assert "update runs" in sql
    assert "principal_roles = %s::jsonb" in sql
    assert "principal_department_id = %s" in sql
    assert "auth_source = %s" in sql
    assert "authz_policy_version = %s" in sql
    assert "authority_source = %s" in sql
    assert "authority_checked_at = %s" in sql
    assert params == (
        json.dumps(["qa-operator", "qa operator", "user"], ensure_ascii=False),
        "qa",
        "trusted-header",
        7,
        "identity-gateway",
        "2026-08-12T01:02:03Z",
        "tenant-a",
        "run-a",
    )


@pytest.mark.asyncio
async def test_locked_run_query_projects_complete_auth_snapshot():
    conn = RecordingConnection()

    await PostgresRunLifecyclePersistence().mark_run_running(conn, tenant_id="tenant-a", run_id="run-a")

    sql, _params = conn.calls[0]
    assert "runs.execution_kind" in sql
    assert "runs.principal_roles" in sql
    assert "runs.principal_department_id" in sql
    assert "runs.auth_source" in sql


@pytest.mark.asyncio
async def test_create_run_rejects_session_scope_mismatch_before_insert_returns():
    class EmptyCursor:
        async def fetchone(self):
            return None

    class RunConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if normalized.startswith("select id, tenant_id, status from workspaces"):
                return SingleRowCursor({"id": "workspace-a", "tenant_id": "tenant-a", "status": "active"})
            return EmptyCursor()

    conn = RunConnection()

    with pytest.raises(RepositoryNotFoundError, match="session_not_found"):
        await create_run(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            session_id="session-cross-scope",
            user_id="user-a",
            agent_id="general-agent",
            skill_id="general-chat",
            input_json={},
        )

    sql, params = conn.calls[-1]
    assert sql.startswith("update sessions set next_run_generation")
    assert "tenant_id = %s" in sql
    assert "workspace_id = %s" in sql
    assert "user_id is not distinct from %s" in sql
    assert "id = %s" in sql
    assert "agent_id = %s" in sql
    assert "returning next_run_generation" in sql
    assert "session-cross-scope" in params


@pytest.mark.asyncio
async def test_mark_run_running_requires_run_session_scope_to_match():
    conn = RecordingConnection()

    await PostgresRunLifecyclePersistence().mark_run_running(conn, tenant_id="tenant-a", run_id="run-a")

    sql, params = conn.calls[0]
    assert "update runs" in sql
    assert "from sessions" in sql
    assert "sessions.id = runs.session_id" in sql
    assert "sessions.tenant_id = runs.tenant_id" in sql
    assert "sessions.workspace_id = runs.workspace_id" in sql
    assert "sessions.user_id = runs.user_id" in sql
    assert "sessions.agent_id = runs.agent_id" in sql
    assert params == ("tenant-a", "run-a")


@pytest.mark.asyncio
async def test_append_event_persists_standard_envelope_columns(monkeypatch):
    conn = RecordingConnection()
    captured = []

    async def append_one(_conn, *, tenant_id, run_id, event):
        captured.append((tenant_id, run_id, event))
        return event_ledger.EventReceipt(
            "evt-a",
            event_ledger.RunCursor(run_id, 1),
        )

    monkeypatch.setattr(event_ledger, "append_event", append_one)

    await append_event(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        trace_id="trace_a",
        event_type="run_failed",
        stage="worker",
        message="Run failed",
        payload={"severity": "error", "visible_to_user": False, "error_code": "executor_failure"},
        latency_ms=12,
    )

    assert captured == [
        (
            "tenant-a",
            "run-a",
            event_ledger.LedgerEvent(
                event_type="run_failed",
                stage="worker",
                message="Run failed",
                payload={"severity": "error", "visible_to_user": False, "error_code": "executor_failure"},
                trace_id="trace_a",
                latency_ms=12,
            ),
        )
    ]


@pytest.mark.asyncio
async def test_list_run_events_supports_sequence_cursor_and_limit():
    conn = RecordingConnection()

    await list_run_events(conn, tenant_id="tenant-a", run_id="run-a", after_sequence=7, limit=20)

    sql, params = conn.calls[0]
    assert "sequence > %s" in sql
    assert "order by event.sequence asc, event.created_at asc" in sql
    assert "limit %s" in sql
    assert params == ("tenant-a", "run-a", 7, 20)


@pytest.mark.asyncio
async def test_upsert_run_step_merges_existing_payload_on_conflict():
    conn = RecordingConnection()

    await upsert_run_step(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        step_key="code",
        step_kind="agent",
        status="succeeded",
        title="coding agent reused checkpoint",
        role="coding",
        sequence=1,
        payload_json={"checkpoint_reused": True, "output": "code output"},
    )

    assert conn.calls[0][0].startswith("select pg_advisory_xact_lock")
    assert conn.calls[1][0].endswith("for update")
    update_sql, update_params = conn.calls[2]
    assert update_sql.startswith("update run_steps")
    assert json.loads(update_params[5]) == {
        "checkpoint_reused": True,
        "output": "code output",
    }


@pytest.mark.asyncio
async def test_complete_run_persists_g2_observability_columns_from_result_json():
    conn = RecordingConnection()

    await RunLifecycleService(
        persistence=PostgresRunLifecyclePersistence(),
        append_event=_repo_owner_app_streaming_infrastructure_run_events_postgres.append_event,
        append_audit_log=_repo_owner_app_identity_infrastructure_audit_postgres.append_audit_log,
        validate_result_size=require_run_result_size,
        sanitize_payload=sanitize_public_payload,
        sanitize_text=sanitize_public_text,
        make_trace_id=standard_trace_id,
    ).complete_run(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        result_json={
            "message": "done",
            "latency_ms": 250,
            "token_counts": {"input": 11, "output": 13, "total": 24},
            "cost": {"estimated_cost_minor": 17},
        },
    )

    sql, params = next(
        (sql, params)
        for sql, params in conn.calls
        if sql.startswith("update runs") and "set status = 'succeeded'" in sql
    )
    assert "latency_ms" in sql
    assert "input_token_count" in sql
    assert "output_token_count" in sql
    assert "total_token_count" in sql
    assert "estimated_cost_minor" in sql
    assert 250 in params
    assert 11 in params
    assert 13 in params
    assert 24 in params
    assert 17 in params
