import json

import pytest

from app import repositories
from app.repositories import (
    RepositoryConflictError,
    RepositoryNotFoundError,
    append_audit_log,
    append_event,
    cancel_run,
    complete_run,
    count_active_runs_for_user,
    create_artifact,
    create_context_snapshot,
    create_sandbox_lease,
    create_tool_permission_request,
    create_run,
    admin_delete_memory_record,
    delete_memory_record,
    fail_run,
    get_admin_run_detail,
    get_context_snapshot_for_worker,
    get_latest_tool_permission_decision,
    get_run_identity,
    list_run_events,
    list_run_artifacts,
    renew_sandbox_lease,
    upsert_run_step,
)


class FakeCursor:
    async def fetchone(self):
        return {"count": 2, "id": "step-a"}

    async def fetchall(self):
        return []


class FakeConnection:
    def __init__(self):
        self.sql = ""
        self.params = None

    async def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return FakeCursor()


class RecordingConnection:
    def __init__(self):
        self.calls = []

    async def execute(self, sql, params):
        self.calls.append((" ".join(sql.split()), params))
        return FakeCursor()


@pytest.mark.asyncio
async def test_count_active_runs_for_user_counts_queued_and_running_only():
    conn = FakeConnection()

    count = await count_active_runs_for_user(conn, tenant_id="tenant-a", user_id="user-a")

    assert count == 2
    assert "status in ('queued', 'running')" in conn.sql
    assert conn.params == ("tenant-a", "user-a")


@pytest.mark.asyncio
async def test_cancel_run_closes_non_terminal_run_steps():
    class RecordingConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return FakeCursor()

    conn = RecordingConnection()

    await cancel_run(conn, tenant_id="tenant-a", run_id="run-a", result_json={"message": "cancelled"})

    assert len(conn.calls) == 2
    assert conn.calls[0][0].startswith("update runs")
    assert conn.calls[1][0].startswith("update run_steps")
    assert "status = 'cancelled'" in conn.calls[1][0]
    assert "status in ('pending', 'running')" in conn.calls[1][0]
    assert conn.calls[1][1] == ("tenant-a", "run-a")


@pytest.mark.asyncio
async def test_fail_run_closes_non_terminal_run_steps_without_leaving_stale_progress():
    class RecordingConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return FakeCursor()

    conn = RecordingConnection()

    await fail_run(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        error_code="executor_failure",
        error_message="boom",
        result_json={"message": "failed"},
    )

    assert len(conn.calls) == 2
    assert conn.calls[0][0].startswith("update runs")
    assert conn.calls[1][0].startswith("update run_steps")
    assert "case when status = 'running' then 'failed' else 'cancelled' end" in conn.calls[1][0]
    assert "status in ('pending', 'running')" in conn.calls[1][0]
    assert conn.calls[1][1] == ("tenant-a", "run-a")


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

    sql, params = conn.calls[0]
    assert run_id.startswith("run_")
    assert "trace_id" in sql
    assert "schema_version" in sql
    assert "executor_schema_version" in sql
    assert "principal_roles" in sql
    assert any(str(item).startswith("trace_") for item in params)
    assert "ai-platform.run.v1" in params
    assert "ai-platform.executor-result.v1" in params


@pytest.mark.asyncio
async def test_create_run_rejects_session_scope_mismatch_before_insert_returns():
    class EmptyCursor:
        async def fetchone(self):
            return None

    class RunConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
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

    sql, params = conn.calls[0]
    assert "insert into runs" in sql
    assert "from sessions" in sql
    assert "sessions.tenant_id = %s" in sql
    assert "sessions.workspace_id = %s" in sql
    assert "sessions.user_id = %s" in sql
    assert "sessions.id = %s" in sql
    assert "sessions.agent_id = %s" in sql
    assert "returning id" in sql
    assert "session-cross-scope" in params


@pytest.mark.asyncio
async def test_mark_run_running_requires_run_session_scope_to_match():
    conn = RecordingConnection()

    await repositories.mark_run_running(conn, tenant_id="tenant-a", run_id="run-a")

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
async def test_append_event_persists_standard_envelope_columns():
    conn = RecordingConnection()

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

    sql, params = conn.calls[0]
    assert "trace_id" in sql
    assert "schema_version" in sql
    assert "sequence" in sql
    assert "coalesce(max(sequence), 0) + 1" in sql
    assert "severity" in sql
    assert "visible_to_user" in sql
    assert "error_code" in sql
    assert "latency_ms" in sql
    assert "trace_a" in params
    assert "ai-platform.event-envelope.v1" in params
    assert "error" in params
    assert False in params
    assert "executor_failure" in params
    assert 12 in params


@pytest.mark.asyncio
async def test_list_run_events_supports_sequence_cursor_and_limit():
    conn = RecordingConnection()

    await list_run_events(conn, tenant_id="tenant-a", run_id="run-a", after_sequence=7, limit=20)

    sql, params = conn.calls[0]
    assert "sequence > %s" in sql
    assert "order by sequence asc, created_at asc" in sql
    assert "limit %s" in sql
    assert params == ("tenant-a", "run-a", 7, 20)


@pytest.mark.asyncio
async def test_create_artifact_persists_manifest_version_and_trace_id():
    conn = RecordingConnection()

    await create_artifact(
        conn,
        artifact_id="art-a",
        tenant_id="tenant-a",
        run_id="run-a",
        trace_id="trace_a",
        artifact_type="reviewed_docx",
        label="批注 Word",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        storage_key="tenants/tenant-a/runs/run-a/artifacts/1/reviewed.docx",
        size_bytes=10,
        manifest_json={},
    )

    sql, params = conn.calls[0]
    assert "manifest_version" in sql
    assert "trace_id" in sql
    assert "ai-platform.artifact-manifest.v1" in params
    assert "trace_a" in params


@pytest.mark.asyncio
async def test_create_context_snapshot_persists_scope_and_context_contract():
    conn = RecordingConnection()

    snapshot = await create_context_snapshot(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        trace_id="trace-a",
        context_kind="executor",
        included_message_ids=["msg-a"],
        included_file_ids=["file-a"],
        included_artifact_ids=["art-a"],
        included_memory_record_ids=["mem-a"],
        redaction_summary_json={"secrets": 0},
        payload_json={"window": "current"},
    )

    sql, params = conn.calls[0]
    assert snapshot["id"].startswith("ctx_")
    assert "run_context_snapshots" in sql
    assert "ai-platform.context-snapshot.v1" in params
    assert any("\"msg-a\"" in str(item) for item in params)
    assert any("\"mem-a\"" in str(item) for item in params)


@pytest.mark.asyncio
async def test_create_context_snapshot_sanitizes_payload_and_summary_before_insert():
    conn = RecordingConnection()

    snapshot = await create_context_snapshot(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        trace_id="trace-a",
        context_kind="executor",
        included_message_ids=[],
        included_file_ids=[],
        included_artifact_ids=[],
        included_memory_record_ids=[],
        redaction_summary_json={
            "source": "internal",
            "client_secret": "client-secret-context",
            "note": "authorization: Bearer context-bearer",
        },
        payload_json={
            "window": "current",
            "api_key": "sk-context-secret",
            "runtime_path": "/var/lib/ai-platform/run-a",
            "nested": {"email": "alice@example.com", "safe": "kept"},
        },
    )

    _sql, params = conn.calls[0]
    inserted_summary = json.loads(params[-2])
    inserted_payload = json.loads(params[-1])
    assert inserted_summary == {"source": "internal", "note": "authorization=[redacted-secret]"}
    assert inserted_payload == {
        "window": "current",
        "nested": {"email": "[redacted-email]", "safe": "kept"},
    }
    assert snapshot["redaction_summary_json"] == inserted_summary
    assert snapshot["payload_json"] == inserted_payload
    serialized = json.dumps(snapshot, ensure_ascii=False) + str(params)
    assert "client-secret-context" not in serialized
    assert "context-bearer" not in serialized
    assert "sk-context-secret" not in serialized
    assert "/var/lib/ai-platform" not in serialized
    assert "alice@example.com" not in serialized


@pytest.mark.asyncio
async def test_create_context_snapshot_rejects_run_scope_mismatch_before_insert_returns():
    class EmptyCursor:
        async def fetchone(self):
            return None

    class SnapshotConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return EmptyCursor()

    conn = SnapshotConnection()

    with pytest.raises(RepositoryNotFoundError, match="run_not_found"):
        await create_context_snapshot(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-cross-scope",
            trace_id="trace-a",
            context_kind="executor",
            included_message_ids=[],
            included_file_ids=[],
            included_artifact_ids=[],
            included_memory_record_ids=[],
            redaction_summary_json={},
            payload_json={},
        )

    sql, params = conn.calls[0]
    assert "insert into run_context_snapshots" in sql
    assert "from runs" in sql
    assert "join sessions" in sql
    assert "runs.tenant_id = %s" in sql
    assert "runs.workspace_id = %s" in sql
    assert "runs.user_id = %s" in sql
    assert "runs.session_id = %s" in sql
    assert "runs.id = %s" in sql
    assert "sessions.id = runs.session_id" in sql
    assert "returning id" in sql
    assert "run-cross-scope" in params


@pytest.mark.asyncio
async def test_get_context_snapshot_for_worker_scopes_by_full_run_identity():
    class SnapshotCursor:
        async def fetchone(self):
            return {"id": "ctx-a", "payload_json": {"source": "db"}}

    class SnapshotConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return SnapshotCursor()

    conn = SnapshotConnection()

    row = await get_context_snapshot_for_worker(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        context_snapshot_id="ctx-a",
    )

    assert row == {"id": "ctx-a", "payload_json": {"source": "db"}}
    assert "from run_context_snapshots" in conn.sql
    assert "tenant_id = %s" in conn.sql
    assert "workspace_id = %s" in conn.sql
    assert "user_id = %s" in conn.sql
    assert "session_id = %s" in conn.sql
    assert "run_id = %s" in conn.sql
    assert "id = %s" in conn.sql
    assert conn.params == ("tenant-a", "workspace-a", "user-a", "session-a", "run-a", "ctx-a")


@pytest.mark.asyncio
async def test_get_effective_memory_policy_defaults_to_session_only_memory_when_no_policy():
    class EmptyPolicyCursor:
        async def fetchone(self):
            return None

    class PolicyConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return EmptyPolicyCursor()

    conn = PolicyConnection()

    policy = await repositories.get_effective_memory_policy(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
    )

    sql, params = conn.calls[0]
    assert "from memory_policies" in sql
    assert "agent_id = %s or agent_id is null" in sql
    assert params == ("tenant-a", "workspace-a", "user-a", "general-agent", "general-agent")
    assert policy == {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "agent_id": "general-agent",
        "memory_enabled": True,
        "long_term_memory_enabled": False,
        "retention_days": 90,
        "source": "default",
        "reason": "",
        "updated_by": "",
        "updated_at": None,
    }


@pytest.mark.asyncio
async def test_get_effective_memory_policy_clamps_legacy_long_term_memory_enabled_rows():
    class LegacyPolicyCursor:
        async def fetchone(self):
            return {
                "id": "mempol-legacy",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "memory_enabled": True,
                "long_term_memory_enabled": True,
                "retention_days": 90,
                "reason": "legacy dirty row",
                "updated_by": "legacy-admin",
                "updated_at": "2026-06-02T12:00:00Z",
            }

    class PolicyConnection:
        async def execute(self, sql, params):
            return LegacyPolicyCursor()

    policy = await repositories.get_effective_memory_policy(
        PolicyConnection(),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
    )

    assert policy["source"] == "stored"
    assert policy["memory_enabled"] is True
    assert policy["long_term_memory_enabled"] is False


@pytest.mark.asyncio
async def test_set_memory_policy_upserts_deterministic_scope_without_secret_reason_leak():
    class UpsertCursor:
        async def fetchone(self):
            return {
                "id": "mempol_test",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "memory_enabled": False,
                "long_term_memory_enabled": False,
                "retention_days": 30,
                "reason": "user opt-out [redacted-secret]",
                "updated_by": "admin-a",
                "updated_at": "2026-06-02T12:00:00Z",
            }

    class PolicyConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return UpsertCursor()

    conn = PolicyConnection()

    policy = await repositories.set_memory_policy(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        memory_enabled=False,
        long_term_memory_enabled=False,
        retention_days=30,
        reason="user opt-out [redacted-secret]",
        updated_by="admin-a",
    )

    sql, params = conn.calls[0]
    assert "insert into memory_policies" in sql
    assert "on conflict (id) do update" in sql
    assert "token=hidden" not in str(params)
    assert params[1:10] == (
        "tenant-a",
        "workspace-a",
        "user-a",
        "general-agent",
        False,
        False,
        30,
        "user opt-out [redacted-secret]",
        "admin-a",
    )
    assert policy["memory_enabled"] is False
    assert policy["long_term_memory_enabled"] is False
    assert policy["source"] == "stored"


@pytest.mark.asyncio
async def test_set_memory_policy_rejects_long_term_enable_at_repository_boundary():
    class FailConnection:
        async def execute(self, sql, params):
            raise AssertionError("repository must reject long-term memory before SQL")

    with pytest.raises(RepositoryConflictError, match="long_term_memory_not_available"):
        await repositories.set_memory_policy(
            FailConnection(),
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id="general-agent",
            memory_enabled=True,
            long_term_memory_enabled=True,
            retention_days=90,
            reason="enable",
            updated_by="admin-a",
        )


@pytest.mark.asyncio
async def test_list_admin_memory_policies_scopes_filters_clamps_and_closes_long_term():
    class PolicyCursor:
        async def fetchall(self):
            return [
                {
                    "id": "mempol-user-a",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-a",
                    "agent_id": "general-agent",
                    "memory_enabled": False,
                    "long_term_memory_enabled": True,
                    "retention_days": 30,
                    "reason": "user opt-out",
                    "updated_by": "user-a",
                    "updated_at": "2026-06-05T00:00:00Z",
                }
            ]

    class PolicyConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return PolicyCursor()

    conn = PolicyConnection()

    rows = await repositories.list_admin_memory_policies(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        limit=999,
    )

    sql, params = conn.calls[0]
    assert "from memory_policies" in sql
    assert "tenant_id = %s" in sql
    assert "workspace_id = %s" in sql
    assert "(%s::text is null or user_id = %s)" in sql
    assert "(%s::text is null or agent_id = %s)" in sql
    assert params == ("tenant-a", "workspace-a", "user-a", "user-a", "general-agent", "general-agent", 500)
    assert rows == [
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "agent_id": "general-agent",
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "source": "stored",
            "reason": "user opt-out",
            "updated_by": "user-a",
            "updated_at": "2026-06-05T00:00:00Z",
        }
    ]


@pytest.mark.asyncio
async def test_ensure_mcp_tool_active_applies_tenant_tool_policy_fail_closed():
    class ToolCursor:
        async def fetchone(self):
            return {
                "id": "ragflow-knowledge-search",
                "server_id": "ragflow",
                "registry_status": "active",
                "policy_status": "disabled",
                "registry_write_capable": False,
                "policy_write_capable": False,
                "registry_risk_level": "low",
                "policy_risk_level": "low",
                "registry_visible_to_user": True,
                "policy_visible_to_user": True,
            }

    class ToolConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ToolCursor()

    conn = ToolConnection()

    with pytest.raises(RepositoryConflictError, match="mcp_tool_disabled"):
        await repositories.ensure_mcp_tool_active(
            conn,
            tenant_id="tenant-a",
            tool_id="ragflow-knowledge-search",
        )

    sql, params = conn.calls[0]
    assert "left join tool_policies" in sql
    assert "tool_policies.tenant_id = %s" in sql
    assert params == ("tenant-a", "ragflow-knowledge-search")


@pytest.mark.asyncio
async def test_ensure_mcp_tool_active_requires_tenant_tool_policy_row():
    class ToolCursor:
        async def fetchone(self):
            return {
                "id": "ragflow-knowledge-search",
                "server_id": "ragflow",
                "registry_status": "active",
                "policy_status": None,
                "registry_write_capable": False,
                "policy_write_capable": None,
                "registry_risk_level": "low",
                "policy_risk_level": None,
                "registry_visible_to_user": True,
                "policy_visible_to_user": None,
            }

    class ToolConnection:
        async def execute(self, sql, params):
            return ToolCursor()

    with pytest.raises(RepositoryConflictError, match="mcp_tool_disabled"):
        await repositories.ensure_mcp_tool_active(
            ToolConnection(),
            tenant_id="tenant-a",
            tool_id="ragflow-knowledge-search",
        )


@pytest.mark.asyncio
async def test_ensure_mcp_tool_active_cannot_lower_registry_write_or_risk():
    class ToolCursor:
        async def fetchone(self):
            return {
                "id": "dangerous-writer",
                "server_id": "business",
                "registry_status": "active",
                "policy_status": "active",
                "registry_write_capable": True,
                "policy_write_capable": False,
                "registry_risk_level": "high",
                "policy_risk_level": "low",
                "registry_visible_to_user": True,
                "policy_visible_to_user": True,
            }

    class ToolConnection:
        async def execute(self, sql, params):
            return ToolCursor()

    row = await repositories.ensure_mcp_tool_active(
        ToolConnection(),
        tenant_id="tenant-a",
        tool_id="dangerous-writer",
    )

    assert row["id"] == "dangerous-writer"
    assert row["status"] == "active"
    assert row["write_capable"] is True
    assert row["risk_level"] == "high"
    assert row["visible_to_user"] is True


@pytest.mark.asyncio
async def test_list_admin_tool_policies_returns_missing_tenant_policy_as_disabled_inventory():
    class ToolPolicyCursor:
        async def fetchall(self):
            return [
                {
                    "tenant_id": "tenant-a",
                    "tool_id": "ragflow-knowledge-search",
                    "server_id": "ragflow",
                    "name": "RAGFlow",
                    "description": "Read-only search",
                    "registry_status": "active",
                    "policy_status": None,
                    "registry_write_capable": False,
                    "policy_write_capable": None,
                    "registry_risk_level": "low",
                    "policy_risk_level": None,
                    "registry_visible_to_user": True,
                    "policy_visible_to_user": None,
                    "reason": None,
                    "updated_by": None,
                    "updated_at": None,
                    "endpoint": "https://internal.example",
                    "auth_mode": "api-key",
                }
            ]

    class ToolPolicyConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ToolPolicyCursor()

    conn = ToolPolicyConnection()

    rows = await repositories.list_admin_tool_policies(
        conn,
        tenant_id="tenant-a",
        include_disabled=True,
        limit=999,
    )

    sql, params = conn.calls[0]
    assert "from mcp_tools" in sql
    assert "left join tool_policies" in sql
    assert "tool_policies.tenant_id = %s" in sql
    assert params == ("tenant-a", True, 500)
    assert "endpoint" not in rows[0]
    assert "auth_mode" not in rows[0]
    assert rows == [
        {
            "tenant_id": "tenant-a",
            "tool_id": "ragflow-knowledge-search",
            "id": "ragflow-knowledge-search",
            "server_id": "ragflow",
            "name": "RAGFlow",
            "description": "Read-only search",
            "registry_status": "active",
            "policy_status": "disabled",
            "effective_status": "disabled",
            "status": "disabled",
            "registry_write_capable": False,
            "policy_write_capable": False,
            "write_capable": False,
            "registry_risk_level": "low",
            "policy_risk_level": "low",
            "risk_level": "low",
            "registry_visible_to_user": True,
            "policy_visible_to_user": False,
            "visible_to_user": False,
            "source": "registry",
            "reason": "",
            "updated_by": None,
            "updated_at": None,
        }
    ]


@pytest.mark.asyncio
async def test_list_admin_tool_policies_filters_hidden_when_disabled_excluded():
    class ToolPolicyCursor:
        async def fetchall(self):
            return []

    class ToolPolicyConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ToolPolicyCursor()

    conn = ToolPolicyConnection()

    rows = await repositories.list_admin_tool_policies(
        conn,
        tenant_id="tenant-a",
        include_disabled=False,
        limit=50,
    )

    assert rows == []
    sql, params = conn.calls[0]
    assert "coalesce(mcp_tools.visible_to_user, false) = true" in sql
    assert "tool_policies.status = 'active'" in sql
    assert "tool_policies.visible_to_user = true" in sql
    assert params == ("tenant-a", False, 50)


@pytest.mark.asyncio
async def test_upsert_admin_tool_policy_writes_tenant_policy_and_returns_effective_row():
    class ToolPolicyCursor:
        async def fetchone(self):
            return {
                "tenant_id": "tenant-a",
                "tool_id": "ragflow-knowledge-search",
                "server_id": "ragflow",
                "name": "RAGFlow",
                "description": "Read-only search",
                "registry_status": "active",
                "policy_status": "active",
                "registry_write_capable": False,
                "policy_write_capable": True,
                "registry_risk_level": "low",
                "policy_risk_level": "high",
                "registry_visible_to_user": True,
                "policy_visible_to_user": True,
                "reason": "controlled write",
                "updated_by": "tool-admin",
                "updated_at": "2026-06-05T00:00:00Z",
            }

    class ToolPolicyConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return ToolPolicyCursor()

    conn = ToolPolicyConnection()

    row = await repositories.upsert_admin_tool_policy(
        conn,
        tenant_id="tenant-a",
        tool_id="ragflow-knowledge-search",
        status="active",
        risk_level="high",
        write_capable=True,
        visible_to_user=True,
        reason="controlled write",
        updated_by="tool-admin",
    )

    sql, params = conn.calls[0]
    assert "insert into tool_policies" in sql
    assert "on conflict (tenant_id, tool_id) do update" in sql
    assert params == (
        "tenant-a",
        "active",
        True,
        "high",
        True,
        "controlled write",
        "tool-admin",
        "ragflow-knowledge-search",
    )
    assert row["source"] == "tenant"
    assert row["effective_status"] == "active"
    assert row["write_capable"] is True
    assert row["risk_level"] == "high"


@pytest.mark.asyncio
async def test_upsert_admin_tool_policy_raises_for_missing_tool():
    class MissingCursor:
        async def fetchone(self):
            return None

    class MissingConnection:
        async def execute(self, sql, params):
            return MissingCursor()

    with pytest.raises(RepositoryNotFoundError, match="mcp_tool_not_found"):
        await repositories.upsert_admin_tool_policy(
            MissingConnection(),
            tenant_id="tenant-a",
            tool_id="missing-tool",
            status="disabled",
            risk_level="low",
            write_capable=False,
            visible_to_user=False,
            reason="missing",
            updated_by="tool-admin",
        )


@pytest.mark.asyncio
async def test_create_memory_record_sets_expires_at_from_retention_days():
    class MemoryCursor:
        async def fetchone(self):
            return {
                "id": "mem-retention",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "session_id": "session-a",
                "record_type": "session_summary",
                "content": "Retain for policy duration.",
                "metadata_json": {"source": "test"},
                "status": "active",
                "expires_at": "2026-07-03T12:00:00Z",
                "deleted_at": None,
                "created_at": "2026-06-03T12:00:00Z",
                "updated_at": "2026-06-03T12:00:00Z",
            }

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return MemoryCursor()

    conn = MemoryConnection()

    row = await repositories.create_memory_record(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        session_id="session-a",
        record_type="session_summary",
        content="Retain for policy duration.",
        metadata_json={"source": "test"},
        retention_days=30,
    )

    sql, params = conn.calls[0]
    assert "insert into memory_records" in sql
    assert "expires_at" in sql
    assert "now() + (%s * interval '1 day')" in sql
    assert params[9] == 30
    assert row["expires_at"] == "2026-07-03T12:00:00Z"
    assert row["status"] == "active"


@pytest.mark.asyncio
async def test_create_memory_record_redacts_secret_like_content_and_metadata_before_insert():
    class MemoryCursor:
        async def fetchone(self):
            return {
                "id": "mem-redacted",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "session_id": "session-a",
                "record_type": "session_summary",
                "content": "safe",
                "metadata_json": {"source": "test"},
                "status": "active",
                "expires_at": "2026-07-03T12:00:00Z",
                "deleted_at": None,
                "created_at": "2026-06-03T12:00:00Z",
                "updated_at": "2026-06-03T12:00:00Z",
            }

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return MemoryCursor()

    conn = MemoryConnection()

    await repositories.create_memory_record(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        session_id="session-a",
        record_type="session_summary",
        content=(
            "User api_key=sk-live-123 password: hidden-password email alice@example.com "
            "authorization: Bearer sk-live-bearer {\"api_key\":\"sk-live-json\"} "
            "client_secret=client-secret-text openai_api_key=sk-openai-text id_token=id-token-text "
            "{\"client_secret\":\"client-secret-json\",\"openai_api_key\":\"sk-openai-json\",\"id_token\":\"id-token-json\"}"
        ),
        metadata_json={
            "source": "test",
            "api_key": "sk-live-456",
            "client_secret": "client-secret-value",
            "openai_api_key": "sk-openai-value",
            "id_token": "id-token-value",
            "nested": {
                "token": "hidden-token",
                "note": "password=hidden-password-2 authorization: Bearer nested-bearer-token",
                "json": (
                    "{\"api_key\":\"sk-live-json-meta\",\"client_secret\":\"client-secret-json-meta\","
                    "\"openai_api_key\":\"sk-openai-json-meta\",\"id_token\":\"id-token-json-meta\"}"
                ),
            },
        },
        retention_days=30,
    )

    _, params = conn.calls[0]
    inserted_content = params[7]
    inserted_metadata = json.loads(params[8])
    serialized = f"{inserted_content} {inserted_metadata}"
    assert "sk-live-123" not in serialized
    assert "sk-live-456" not in serialized
    assert "sk-live-bearer" not in serialized
    assert "sk-live-json" not in serialized
    assert "client-secret-value" not in serialized
    assert "sk-openai-value" not in serialized
    assert "id-token-value" not in serialized
    assert "client-secret-text" not in serialized
    assert "sk-openai-text" not in serialized
    assert "id-token-text" not in serialized
    assert "client-secret-json" not in serialized
    assert "sk-openai-json" not in serialized
    assert "id-token-json" not in serialized
    assert "hidden-password" not in serialized
    assert "hidden-token" not in serialized
    assert "nested-bearer-token" not in serialized
    assert "sk-live-json-meta" not in serialized
    assert "client-secret-json-meta" not in serialized
    assert "sk-openai-json-meta" not in serialized
    assert "id-token-json-meta" not in serialized
    assert "alice@example.com" not in serialized
    assert "authorization=[redacted-secret] [redacted-secret]" not in serialized
    assert "[redacted-secret]" in serialized
    assert "[redacted-email]" in serialized
    assert inserted_metadata["source"] == "test"
    assert inserted_metadata["client_secret"] == "[redacted-secret]"
    assert inserted_metadata["openai_api_key"] == "[redacted-secret]"
    assert inserted_metadata["id_token"] == "[redacted-secret]"


@pytest.mark.asyncio
async def test_create_memory_record_rejects_missing_session_id_before_insert():
    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            raise AssertionError("memory write must fail before SQL when session_id is missing")

    conn = MemoryConnection()

    with pytest.raises(RepositoryConflictError, match="memory_session_id_required"):
        await repositories.create_memory_record(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id="general-agent",
            session_id=None,
            record_type="session_summary",
            content="unsafe cross-session memory",
            metadata_json={},
        )

    assert conn.calls == []


@pytest.mark.asyncio
async def test_list_memory_records_rejects_missing_session_id_before_query():
    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            raise AssertionError("memory list must fail before SQL when session_id is missing")

    conn = MemoryConnection()

    with pytest.raises(RepositoryConflictError, match="memory_session_id_required"):
        await repositories.list_memory_records(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id="general-agent",
            session_id=None,
        )

    assert conn.calls == []


@pytest.mark.asyncio
async def test_create_memory_record_rejects_missing_agent_id_before_insert():
    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            raise AssertionError("memory write must fail before SQL when agent_id is missing")

    conn = MemoryConnection()

    with pytest.raises(RepositoryConflictError, match="memory_agent_id_required"):
        await repositories.create_memory_record(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id=None,
            session_id="session-a",
            record_type="session_summary",
            content="unsafe cross-agent memory",
            metadata_json={},
        )

    assert conn.calls == []


@pytest.mark.asyncio
async def test_create_memory_record_rejects_session_scope_mismatch_before_insert_returns():
    class EmptyCursor:
        async def fetchone(self):
            return None

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return EmptyCursor()

    conn = MemoryConnection()

    with pytest.raises(RepositoryNotFoundError, match="session_not_found"):
        await repositories.create_memory_record(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id="general-agent",
            session_id="session-cross-scope",
            record_type="session_summary",
            content="unsafe cross-session memory",
            metadata_json={},
        )

    sql, params = conn.calls[0]
    assert "from sessions" in sql
    assert "sessions.tenant_id = %s" in sql
    assert "sessions.workspace_id = %s" in sql
    assert "sessions.user_id = %s" in sql
    assert "sessions.id = %s" in sql
    assert "sessions.agent_id = %s" in sql
    assert "session-cross-scope" in params


@pytest.mark.asyncio
async def test_get_user_scopes_by_tenant_and_user_id():
    class UserCursor:
        async def fetchone(self):
            return {"id": "user-a", "tenant_id": "tenant-a", "display_name": "User A", "status": "active"}

    class UserConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return UserCursor()

    conn = UserConnection()

    user = await repositories.get_user(conn, tenant_id="tenant-a", user_id="user-a")

    sql, params = conn.calls[0]
    assert "from users" in sql
    assert "tenant_id = %s" in sql
    assert "id = %s" in sql
    assert params == ("tenant-a", "user-a")
    assert user["id"] == "user-a"


@pytest.mark.asyncio
async def test_get_agent_scopes_by_tenant_and_agent_id():
    class AgentCursor:
        async def fetchone(self):
            return {"id": "general-agent", "tenant_id": "tenant-a", "status": "active"}

    class AgentConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return AgentCursor()

    conn = AgentConnection()

    agent = await repositories.get_agent(conn, tenant_id="tenant-a", agent_id="general-agent")

    sql, params = conn.calls[0]
    assert "from agents" in sql
    assert "tenant_id = %s" in sql
    assert "id = %s" in sql
    assert params == ("tenant-a", "general-agent")
    assert agent["id"] == "general-agent"


@pytest.mark.asyncio
async def test_delete_memory_record_soft_deletes_with_user_workspace_session_scope():
    class MemoryCursor:
        async def fetchone(self):
            return {
                "id": "mem-a",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "session_id": "session-a",
                "record_type": "session_summary",
                "content": "User prefers concise answers.",
                "metadata_json": {},
                "status": "deleted",
                "expires_at": None,
                "deleted_at": "2026-06-02T12:00:00Z",
                "created_at": "2026-06-02T11:00:00Z",
                "updated_at": "2026-06-02T12:00:00Z",
            }

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return MemoryCursor()

    conn = MemoryConnection()

    row = await delete_memory_record(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        session_id="session-a",
        record_id="mem-a",
    )

    sql, params = conn.calls[0]
    assert row["status"] == "deleted"
    assert "update memory_records" in sql
    assert "status = 'deleted'" in sql
    assert "deleted_at = now()" in sql
    assert "tenant_id = %s" in sql
    assert "workspace_id = %s" in sql
    assert "user_id = %s" in sql
    assert "agent_id = %s" in sql
    assert "session_id = %s" in sql
    assert "status = 'active'" in sql
    assert "deleted_at is null" in sql
    assert "content" not in sql
    assert "metadata_json" not in sql
    assert params == ("tenant-a", "workspace-a", "user-a", "general-agent", "session-a", "mem-a")


@pytest.mark.asyncio
async def test_admin_delete_memory_record_soft_deletes_with_tenant_workspace_scope():
    class MemoryCursor:
        async def fetchone(self):
            return {
                "id": "mem-b",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-b",
                "agent_id": "general-agent",
                "session_id": "session-b",
                "record_type": "user_preference",
                "content": "Use short answers.",
                "metadata_json": {},
                "status": "deleted",
                "expires_at": None,
                "deleted_at": "2026-06-02T12:00:00Z",
                "created_at": "2026-06-02T11:00:00Z",
                "updated_at": "2026-06-02T12:00:00Z",
            }

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return MemoryCursor()

    conn = MemoryConnection()

    row = await admin_delete_memory_record(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        record_id="mem-b",
    )

    sql, params = conn.calls[0]
    assert row["user_id"] == "user-b"
    assert row["status"] == "deleted"
    assert "update memory_records" in sql
    assert "tenant_id = %s" in sql
    assert "workspace_id = %s" in sql
    assert "user_id = %s" not in sql
    assert "status = 'active'" in sql
    assert "content" not in sql
    assert "metadata_json" not in sql
    assert params == ("tenant-a", "workspace-a", "mem-b")


@pytest.mark.asyncio
async def test_cleanup_expired_memory_records_soft_deletes_only_expired_active_rows():
    class CleanupCursor:
        async def fetchall(self):
            return [
                {
                    "id": "mem-expired",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-a",
                    "agent_id": "general-agent",
                    "session_id": "session-a",
                    "record_type": "session_summary",
                    "status": "deleted",
                    "expires_at": "2026-06-01T12:00:00Z",
                    "deleted_at": "2026-06-03T12:00:00Z",
                    "created_at": "2026-05-31T12:00:00Z",
                    "updated_at": "2026-06-03T12:00:00Z",
                }
            ]

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return CleanupCursor()

    conn = MemoryConnection()

    rows = await repositories.cleanup_expired_memory_records(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        limit=25,
    )

    sql, params = conn.calls[0]
    assert rows[0]["id"] == "mem-expired"
    assert rows[0]["status"] == "deleted"
    assert "update memory_records" in sql
    assert "status = 'deleted'" in sql
    assert "expires_at is not null" in sql
    assert "expires_at <= now()" in sql
    assert "deleted_at is null" in sql
    assert "workspace_id = %s" in sql
    assert "%s::text is null" not in sql
    assert "for update skip locked" in sql
    assert "content" not in sql
    assert "metadata_json" not in sql
    assert params == ("tenant-a", "workspace-a", 25)


@pytest.mark.asyncio
async def test_list_admin_memory_records_projects_operator_fields_without_content_or_metadata():
    class MemoryCursor:
        async def fetchall(self):
            return [
                {
                    "id": "mem-ops",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-b",
                    "agent_id": "general-agent",
                    "session_id": "session-b",
                    "record_type": "session_summary",
                    "status": "active",
                    "expires_at": "2026-07-03T12:00:00Z",
                    "deleted_at": None,
                    "created_at": "2026-06-03T12:00:00Z",
                    "updated_at": "2026-06-03T12:30:00Z",
                }
            ]

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return MemoryCursor()

    conn = MemoryConnection()

    rows = await repositories.list_admin_memory_records(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-b",
        status="active",
        limit=25,
    )

    sql, params = conn.calls[0]
    selected_columns = sql.lower().split("from memory_records", 1)[0]
    assert "content" not in selected_columns
    assert "metadata" not in selected_columns
    assert "where tenant_id = %s" in sql
    assert "and workspace_id = %s" in sql
    assert "(%s::text is null or user_id = %s)" in sql
    assert "(%s = 'all' or status = %s)" in sql
    assert params == ("tenant-a", "workspace-a", "user-b", "user-b", "active", "active", 25)
    assert rows[0]["id"] == "mem-ops"


@pytest.mark.asyncio
async def test_create_tool_permission_request_persists_pending_snapshot():
    conn = RecordingConnection()

    row = await create_tool_permission_request(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        trace_id="trace-a",
        tool_id="ragflow-knowledge-search",
        tool_call_id="call-a",
        action="execute",
        risk_level="low",
        write_capable=False,
        reason="read-only search",
        request_payload_json={"query": "sop"},
    )

    sql, params = conn.calls[0]
    assert row["id"].startswith("tpr_")
    assert "run_tool_permission_requests" in sql
    assert "ragflow-knowledge-search" in params
    assert "call-a" in params
    assert False in params


@pytest.mark.asyncio
async def test_decide_tool_permission_request_sets_decision_expiry():
    conn = RecordingConnection()

    row = await repositories.decide_tool_permission_request(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        request_id="tpr-a",
        decision="allow_once",
        reason="approved once",
        decision_payload_json={"source": "card"},
        expires_in_seconds=900,
    )

    sql, params = conn.calls[0]
    assert row["id"] == "step-a"
    assert "update run_tool_permission_requests" in sql
    assert "expires_at = now() + (%s * interval '1 second')" in sql
    assert "decision_payload_json = %s::jsonb" in sql
    assert params == (
        "allow_once",
        "approved once",
        '{"source": "card"}',
        900,
        "tenant-a",
        "user-a",
        "run-a",
        "tpr-a",
    )


@pytest.mark.asyncio
async def test_get_latest_tool_permission_decision_scopes_by_run_user_and_tool():
    conn = RecordingConnection()

    row = await get_latest_tool_permission_decision(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        tool_id="ragflow-knowledge-search",
        action="execute",
    )

    assert row is None
    assert conn.calls == []


@pytest.mark.asyncio
async def test_get_latest_tool_permission_decision_can_filter_exact_tool_call_or_fingerprint():
    conn = RecordingConnection()

    await get_latest_tool_permission_decision(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        tool_id="claude-sdk:Bash",
        action="execute",
        tool_call_id="tool-current",
        request_payload_json={"command_sha256": "a" * 64},
    )

    sql, params = conn.calls[0]
    assert "decision in ('allow_once', 'deny')" in sql
    assert "tool_call_id = %s" in sql
    assert "decision = 'allow_for_run'" in sql
    assert "request_payload_json ->> %s = %s" in sql
    assert params == (
        "tenant-a",
        "user-a",
        "run-a",
        "claude-sdk:Bash",
        "execute",
        "tool-current",
        "command_sha256",
        "a" * 64,
    )


@pytest.mark.asyncio
async def test_consume_tool_permission_decision_marks_only_decided_allow_once_consumed():
    conn = RecordingConnection()
    consume = getattr(repositories, "consume_tool_permission_decision", None)
    assert consume is not None, "repository must expose consume_tool_permission_decision"

    row = await consume(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        request_id="tpr-a",
    )

    sql, params = conn.calls[0]
    assert row["id"] == "step-a"
    assert "update run_tool_permission_requests" in sql
    assert "set status = 'consumed'" in sql
    assert "where tenant_id = %s" in sql
    assert "and user_id = %s" in sql
    assert "and run_id = %s" in sql
    assert "and id = %s" in sql
    assert "and decision = 'allow_once'" in sql
    assert "and status = 'decided'" in sql
    assert "(expires_at is null or expires_at > now())" in sql
    assert "returning *" in sql
    assert params == ("tenant-a", "user-a", "run-a", "tpr-a")


@pytest.mark.asyncio
async def test_create_and_renew_sandbox_lease_persists_ttl_contract():
    conn = RecordingConnection()

    await create_sandbox_lease(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        trace_id="trace-a",
        sandbox_mode="ephemeral",
        provider="fake",
        browser_enabled=False,
        ttl_seconds=600,
        resource_limits_json={"max_seconds": 60},
        user_visible_payload_json={"workspace": "/workspace"},
        lease_payload_json={"purpose": "test"},
    )
    await renew_sandbox_lease(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        lease_id="lease-a",
        ttl_seconds=900,
    )

    create_sql, create_params = conn.calls[0]
    renew_sql, renew_params = conn.calls[1]
    assert "sandbox_leases" in create_sql
    assert "now() + (%s * interval '1 second')" in create_sql
    assert 600 in create_params
    assert "status = 'active'" in renew_sql
    assert "(expires_at is null or expires_at > now())" in renew_sql
    assert renew_params == (900, "tenant-a", "user-a", "run-a", "lease-a")


@pytest.mark.asyncio
async def test_cleanup_expired_sandbox_leases_releases_expired_non_runtime_leases_and_emits_events(monkeypatch):
    from app.repositories import cleanup_expired_sandbox_leases

    calls = []

    class ExpiredLeaseCursor:
        async def fetchall(self):
            return [
                {
                    "id": "lease-expired",
                    "tenant_id": "tenant-a",
                    "run_id": "run-a",
                    "trace_id": "trace-lease",
                    "release_reason": "expired",
                }
            ]

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update sandbox_leases"):
                return ExpiredLeaseCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)

    cleaned = await cleanup_expired_sandbox_leases(
        FakeConnection(),
        tenant_id="tenant-a",
        reason="expired",
    )

    assert [item["id"] for item in cleaned] == ["lease-expired"]
    update_sql, update_params = calls[0]
    assert "status = 'released'" in update_sql
    assert "status = 'active'" in update_sql
    assert "expires_at <= now()" in update_sql
    assert "provider not in ('fake', 'docker')" in update_sql
    assert update_params == ("expired", "tenant-a", "tenant-a")
    assert calls[1] == (
        "event",
        {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "trace_id": "trace-lease",
            "event_type": "sandbox_lease_released",
            "stage": "sandbox",
            "message": "已释放过期 Sandbox 租约",
            "payload": {
                "visible_to_user": True,
                "lease_id": "lease-expired",
                "reason": "expired",
            },
        },
    )


@pytest.mark.asyncio
async def test_cleanup_expired_sandbox_leases_global_scope_emits_events_for_each_tenant(monkeypatch):
    from app.repositories import cleanup_expired_sandbox_leases

    calls = []

    class ExpiredLeaseCursor:
        async def fetchall(self):
            return [
                {
                    "id": "lease-a",
                    "tenant_id": "tenant-a",
                    "run_id": "run-a",
                    "trace_id": "trace-a",
                    "release_reason": "expired",
                },
                {
                    "id": "lease-b",
                    "tenant_id": "tenant-b",
                    "run_id": "run-b",
                    "trace_id": "trace-b",
                    "release_reason": "expired",
                },
            ]

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update sandbox_leases"):
                return ExpiredLeaseCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return f"evt-{kwargs['tenant_id']}"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)

    cleaned = await cleanup_expired_sandbox_leases(FakeConnection())

    assert [item["id"] for item in cleaned] == ["lease-a", "lease-b"]
    update_sql, update_params = calls[0]
    assert "where (%s::text is null or tenant_id = %s)" in update_sql
    assert update_params == ("expired", None, None)
    assert [call[1]["tenant_id"] for call in calls[1:]] == ["tenant-a", "tenant-b"]
    assert [call[1]["payload"]["lease_id"] for call in calls[1:]] == ["lease-a", "lease-b"]


@pytest.mark.asyncio
async def test_cleanup_expired_multi_agent_dispatch_claims_reclaims_steps_and_writes_audit(monkeypatch):
    calls = []

    class ExpiredClaimCursor:
        async def fetchall(self):
            return [
                {
                    "id": "step-code",
                    "tenant_id": "default",
                    "run_id": "run-ready",
                    "trace_id": "trace-ready",
                    "step_key": "code",
                    "payload_json": {
                        "dispatch_id": "dispatch-code",
                        "dispatch_state": "claimed",
                        "dispatch_lease_expires_at": "2000-01-01T00:00:00+00:00",
                    },
                }
            ]

    class UpdateCursor:
        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("select rs.id"):
                return ExpiredClaimCursor()
            if normalized.startswith("update run_steps"):
                return UpdateCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-expired"

    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    cleaned = await repositories.cleanup_expired_multi_agent_dispatch_claims(
        FakeConnection(),
        tenant_id="default",
        cleaned_by="admin-a",
        limit=25,
    )

    assert cleaned == [
        {
            "step_id": "step-code",
            "run_id": "run-ready",
            "step_key": "code",
            "dispatch_id": "dispatch-code",
            "status": "pending",
        }
    ]
    select_sql, select_params = calls[0]
    assert "payload_json->>'dispatch_state' = 'claimed'" in select_sql
    assert "payload_json->>'dispatch_lease_expires_at'" in select_sql
    assert "::timestamptz" not in select_sql
    assert "r.status in" not in select_sql
    assert "skip locked" in select_sql
    assert select_params[0] == "default"
    assert select_params[-1] == 25
    update_sql, update_params = calls[1]
    assert "status = 'pending'" in update_sql
    assert "started_at = null" in update_sql
    assert "finished_at = null" in update_sql
    assert "dispatch_state" in update_sql
    assert update_params[1] == "default"
    assert update_params[2] == "step-code"
    assert calls[2] == (
        "audit",
        {
            "tenant_id": "default",
            "user_id": "admin-a",
            "action": "run.multi_agent.dispatch.expire",
            "target_type": "run_step",
            "target_id": "step-code",
            "trace_id": "trace-ready",
            "payload_json": {
                "run_id": "run-ready",
                "step_key": "code",
                "dispatch_id": "dispatch-code",
                "result_status": "expired",
            },
        },
    )


@pytest.mark.asyncio
async def test_cleanup_expired_multi_agent_dispatch_claims_skips_malformed_timestamp_without_audit(monkeypatch):
    calls = []

    class ExpiredClaimCursor:
        async def fetchall(self):
            return [
                {
                    "id": "step-code",
                    "tenant_id": "default",
                    "run_id": "run-ready",
                    "trace_id": "trace-ready",
                    "step_key": "code",
                    "payload_json": {
                        "dispatch_id": "dispatch-code",
                        "dispatch_state": "claimed",
                        "dispatch_lease_expires_at": "2026-99-99Tbad",
                    },
                }
            ]

    class UpdateCursor:
        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("select rs.id"):
                return ExpiredClaimCursor()
            if normalized.startswith("update run_steps"):
                return UpdateCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fail_append_audit_log(*args, **kwargs):
        raise AssertionError("malformed lease timestamps must not write audit rows")

    monkeypatch.setattr("app.repositories.append_audit_log", fail_append_audit_log)

    cleaned = await repositories.cleanup_expired_multi_agent_dispatch_claims(
        FakeConnection(),
        tenant_id="default",
        cleaned_by="admin-a",
        limit=25,
    )

    assert cleaned == []
    assert len(calls) == 1
    select_sql, select_params = calls[0]
    assert "::timestamptz" not in select_sql
    assert "skip locked" in select_sql
    assert select_params[0] == "default"
    assert select_params[-1] == 25


@pytest.mark.asyncio
async def test_cleanup_expired_multi_agent_dispatch_claims_scans_past_unreclaimable_candidates(monkeypatch):
    calls = []

    class CandidateCursor:
        def __init__(self, rows):
            self.rows = rows

        async def fetchall(self):
            return self.rows

    class UpdateCursor:
        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("select rs.id"):
                seen_ids = set(params[1])
                first_batch = [
                    {
                        "id": "step-malformed",
                        "tenant_id": "default",
                        "run_id": "run-ready",
                        "trace_id": "trace-ready",
                        "step_key": "malformed",
                        "payload_json": {
                            "dispatch_id": "dispatch-malformed",
                            "dispatch_state": "claimed",
                            "dispatch_lease_expires_at": "2026-99-99Tbad",
                        },
                    },
                    {
                        "id": "step-future",
                        "tenant_id": "default",
                        "run_id": "run-ready",
                        "trace_id": "trace-ready",
                        "step_key": "future",
                        "payload_json": {
                            "dispatch_id": "dispatch-future",
                            "dispatch_state": "claimed",
                            "dispatch_lease_expires_at": "2999-01-01T00:00:00+00:00",
                        },
                    },
                ]
                second_batch = [
                    {
                        "id": "step-expired",
                        "tenant_id": "default",
                        "run_id": "run-ready",
                        "trace_id": "trace-ready",
                        "step_key": "expired",
                        "payload_json": {
                            "dispatch_id": "dispatch-expired",
                            "dispatch_state": "claimed",
                            "dispatch_lease_expires_at": "2000-01-01T00:00:00+00:00",
                        },
                    }
                ]
                rows = [row for row in first_batch + second_batch if row["id"] not in seen_ids]
                return CandidateCursor(rows[: params[-1]])
            if normalized.startswith("update run_steps"):
                return UpdateCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-expired"

    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    cleaned = await repositories.cleanup_expired_multi_agent_dispatch_claims(
        FakeConnection(),
        tenant_id="default",
        cleaned_by="admin-a",
        limit=2,
    )

    assert cleaned == [
        {
            "step_id": "step-expired",
            "run_id": "run-ready",
            "step_key": "expired",
            "dispatch_id": "dispatch-expired",
            "status": "pending",
        }
    ]
    select_calls = [call for call in calls if call[0].startswith("select rs.id")]
    assert len(select_calls) == 2
    assert select_calls[0][1][1] == []
    assert select_calls[1][1][1] == ["step-malformed", "step-future"]


@pytest.mark.asyncio
async def test_list_expired_active_sandbox_leases_preserves_runtime_stop_targets():
    from app.repositories import list_expired_active_sandbox_leases

    class ExpiredLeaseCursor:
        async def fetchall(self):
            return [
                {
                    "id": "lease-docker",
                    "tenant_id": "tenant-a",
                    "run_id": "run-a",
                    "provider": "docker",
                    "status": "active",
                }
            ]

    class FakeConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            return ExpiredLeaseCursor()

    conn = FakeConnection()

    rows = await list_expired_active_sandbox_leases(conn, tenant_id="tenant-a", limit=25)

    assert [row["id"] for row in rows] == ["lease-docker"]
    select_sql, select_params = conn.calls[0]
    assert select_sql.startswith("select * from sandbox_leases")
    assert "status = 'active'" in select_sql
    assert "expires_at <= now()" in select_sql
    assert "provider not in" not in select_sql
    assert select_params == ("tenant-a", "tenant-a", 25)


@pytest.mark.asyncio
async def test_release_stopped_sandbox_leases_releases_by_stopped_ids_and_emits_expired_events(monkeypatch):
    from app import repositories

    calls = []

    class LeaseCursor:
        async def fetchall(self):
            return [
                {
                    "id": "lease-a",
                    "tenant_id": "tenant-a",
                    "run_id": "run-a",
                    "trace_id": "trace-a",
                }
            ]

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("update sandbox_leases"):
                return LeaseCursor()
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-a"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)

    released = await repositories.release_stopped_sandbox_leases(
        FakeConnection(),
        tenant_id="tenant-a",
        reason="expired",
        lease_ids=["lease-a"],
    )

    assert [lease["id"] for lease in released] == ["lease-a"]
    update_sql, update_params = calls[0]
    assert "id = any(%s)" in update_sql
    assert "run_id = %s" not in update_sql
    assert update_params == ("expired", "tenant-a", ["lease-a"])
    assert calls[1] == (
        "event",
        {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "trace_id": "trace-a",
            "event_type": "sandbox_lease_released",
            "stage": "sandbox",
            "message": "已释放过期 Sandbox 租约",
            "payload": {
                "visible_to_user": True,
                "lease_id": "lease-a",
                "reason": "expired",
            },
        },
    )


@pytest.mark.asyncio
async def test_get_run_identity_can_lock_row_for_callback_race_window():
    conn = RecordingConnection()

    await get_run_identity(conn, run_id="run-a", for_update=True)

    sql, params = conn.calls[0]
    assert sql.endswith("for update")
    assert params == ("run-a",)


@pytest.mark.asyncio
async def test_get_authorized_run_can_lock_row_for_retry_race_window():
    conn = RecordingConnection()

    await repositories.get_authorized_run(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-a",
        for_update=True,
    )

    sql, params = conn.calls[0]
    assert sql.endswith("for update")
    assert params == ("tenant-a", "run-a", "user-a")


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

    sql, _params = conn.calls[0]
    assert "payload_json = run_steps.payload_json || excluded.payload_json" in sql


@pytest.mark.asyncio
async def test_admin_run_detail_rejects_missing_run_contract(monkeypatch):
    async def fake_get_run(conn, *, tenant_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            "user_id": "user-a",
            "workspace_id": "default",
            "status": "succeeded",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "created_at": None,
            "input_json": {},
            "result_json": {},
        }

    monkeypatch.setattr(repositories, "get_run", fake_get_run)

    with pytest.raises(RepositoryConflictError, match="invalid_run_contract"):
        await repositories.get_admin_run_detail(FakeConnection(), tenant_id="tenant-a", run_id="run-a")


@pytest.mark.asyncio
async def test_admin_run_detail_rejects_missing_artifact_manifest_schema(monkeypatch):
    async def fake_get_run(conn, *, tenant_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            "user_id": "user-a",
            "workspace_id": "default",
            "status": "succeeded",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "created_at": None,
            "input_json": {},
            "result_json": {},
            "schema_version": "ai-platform.run.v1",
            "executor_schema_version": "ai-platform.executor-result.v1",
        }

    async def fake_empty_list(*args, **kwargs):
        return []

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [
            {
                "id": "art-a",
                "trace_id": "trace-a",
                "artifact_type": "reviewed_docx",
                "label": "Reviewed",
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "size_bytes": 10,
                "manifest_version": None,
                "manifest_json": {},
                "created_at": None,
            }
        ]

    monkeypatch.setattr(repositories, "get_run", fake_get_run)
    monkeypatch.setattr(repositories, "list_run_events", fake_empty_list)
    monkeypatch.setattr(repositories, "list_run_steps", fake_empty_list)
    monkeypatch.setattr(repositories, "list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr(repositories, "list_run_skill_snapshots", fake_empty_list)

    with pytest.raises(RepositoryConflictError, match="invalid_artifact_manifest_schema_version"):
        await repositories.get_admin_run_detail(FakeConnection(), tenant_id="tenant-a", run_id="run-a")


@pytest.mark.asyncio
async def test_admin_run_detail_rejects_missing_audit_schema(monkeypatch):
    async def fake_get_run(conn, *, tenant_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            "user_id": "user-a",
            "workspace_id": "default",
            "status": "succeeded",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "created_at": None,
            "input_json": {},
            "result_json": {},
            "schema_version": "ai-platform.run.v1",
            "executor_schema_version": "ai-platform.executor-result.v1",
        }

    async def fake_empty_list(*args, **kwargs):
        return []

    class AuditCursor:
        async def fetchall(self):
            return [
                {
                    "id": "aud-a",
                    "user_id": "admin-a",
                    "action": "admin_run_viewed",
                    "target_type": "run",
                    "target_id": "run-a",
                    "trace_id": "trace-a",
                    "schema_version": None,
                    "payload_json": {"run_id": "run-a"},
                    "created_at": None,
                }
            ]

    class AuditConnection:
        async def execute(self, sql, params):
            return AuditCursor()

    monkeypatch.setattr(repositories, "get_run", fake_get_run)
    monkeypatch.setattr(repositories, "list_run_events", fake_empty_list)
    monkeypatch.setattr(repositories, "list_run_steps", fake_empty_list)
    monkeypatch.setattr(repositories, "list_run_artifacts", fake_empty_list)
    monkeypatch.setattr(repositories, "list_run_skill_snapshots", fake_empty_list)

    with pytest.raises(RepositoryConflictError, match="invalid_audit_event_schema_version"):
        await repositories.get_admin_run_detail(AuditConnection(), tenant_id="tenant-a", run_id="run-a")


@pytest.mark.asyncio
async def test_append_audit_log_accepts_trace_context():
    conn = RecordingConnection()

    await append_audit_log(
        conn,
        tenant_id="tenant-a",
        user_id="admin-a",
        action="admin_artifact_downloaded",
        target_type="artifact",
        target_id="art-a",
        trace_id="trace_a",
        payload_json={"run_id": "run-a"},
    )

    sql, params = conn.calls[0]
    assert "trace_id" in sql
    assert "schema_version" in sql
    assert "trace_a" in params
    assert "ai-platform.audit-event.v1" in params


@pytest.mark.asyncio
async def test_resolve_agent_skill_uses_tenant_stable_release_policy():
    class ResolveCursor:
        async def fetchone(self):
            return {
                "agent_id": "qa-word-review",
                "agent_status": "active",
                "default_skill_id": "qa-file-reviewer",
                "skill_id": "qa-file-reviewer",
                "skill_status": "active",
                "skill_version": "hash-release",
                "release_policy_version": "hash-release",
                "release_policy_previous_version": "hash-previous",
                "release_policy_rollout_percent": 50,
                "executor_type": "claude-agent-worker",
                "mcp_tool_status": None,
                "input_modes": ["docx"],
            }

    class ResolveConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return ResolveCursor()

    conn = ResolveConnection()

    row = await repositories.resolve_agent_skill(
        conn,
        tenant_id="default",
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
    )

    assert "left join skill_release_policies" in conn.sql
    assert "coalesce(skill_release_policies.current_version, skills.version) as skill_version" in conn.sql
    assert "skill_release_policies.current_version as release_policy_version" in conn.sql
    assert "skill_release_policies.previous_version as release_policy_previous_version" in conn.sql
    assert "skill_release_policies.rollout_percent as release_policy_rollout_percent" in conn.sql
    assert "skill_release_policies.channel = 'stable'" in conn.sql
    assert conn.params == ("qa-file-reviewer", "default", "qa-word-review")
    assert row["skill_version"] == "hash-release"
    assert row["release_policy_version"] == "hash-release"
    assert row["release_policy_previous_version"] == "hash-previous"
    assert row["release_policy_rollout_percent"] == 50


@pytest.mark.asyncio
async def test_resolve_agent_skill_rejects_embedded_poco_executor_fact_source():
    class ResolveCursor:
        async def fetchone(self):
            return {
                "agent_id": "general-agent",
                "agent_status": "active",
                "default_skill_id": "general-chat",
                "skill_id": "general-chat",
                "skill_status": "active",
                "skill_version": "0.1.0",
                "release_policy_version": None,
                "executor_type": "embedded-poco-kernel",
                "mcp_tool_status": None,
                "input_modes": ["chat"],
            }

    class ResolveConnection:
        async def execute(self, sql, params):
            return ResolveCursor()

    with pytest.raises(repositories.RepositoryConflictError, match="executor_type_not_allowed"):
        await repositories.resolve_agent_skill(
            ResolveConnection(),
            tenant_id="default",
            agent_id="general-agent",
            skill_id="general-chat",
        )


@pytest.mark.asyncio
async def test_upsert_run_skill_snapshot_is_tenant_and_run_scoped():
    conn = RecordingConnection()

    await repositories.upsert_run_skill_snapshot(
        conn,
        tenant_id="default",
        run_id="run-a",
        skill_id="qa-file-reviewer",
        skill_version="hash-a",
        content_hash="hash-a",
        source_json={"kind": "builtin"},
        dependency_ids=["minimax-docx"],
        allowed=True,
        staged=True,
        used=True,
        used_skills_source="executor_hook",
        inferred_used=False,
    )

    sql, params = conn.calls[0]
    assert "insert into run_skill_snapshots" in sql
    assert "on conflict (tenant_id, run_id, skill_id)" in sql
    assert params[0].startswith("rss_")
    assert params[1:5] == ("default", "run-a", "qa-file-reviewer", "hash-a")
    assert any('"kind": "builtin"' in str(item) for item in params)
    assert any("minimax-docx" in str(item) for item in params)
    assert "used_skills_source" in sql
    assert "inferred_used" in sql
    assert "executor_hook" in params
    assert False in params


@pytest.mark.asyncio
async def test_list_run_skill_snapshots_projects_persisted_telemetry():
    class SnapshotCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "qa-file-reviewer",
                    "skill_version": "hash-a",
                    "content_hash": "hash-a",
                    "source_json": {"kind": "builtin"},
                    "dependency_ids": ["minimax-docx"],
                    "allowed": True,
                    "staged": True,
                    "used": False,
                    "used_skills_source": "inferred",
                    "inferred_used": True,
                    "created_at": None,
                }
            ]

    class SnapshotConnection:
        async def execute(self, sql, params):
            assert "used_skills_source" in sql
            assert "inferred_used" in sql
            assert params == ("default", "run-a")
            return SnapshotCursor()

    snapshots = await repositories.list_run_skill_snapshots(
        SnapshotConnection(),
        tenant_id="default",
        run_id="run-a",
    )

    assert snapshots == [
        {
            "skill_id": "qa-file-reviewer",
            "skill_version": "hash-a",
            "content_hash": "hash-a",
            "source": {"kind": "builtin"},
            "dependency_ids": ["minimax-docx"],
            "allowed": True,
            "staged": True,
            "used": False,
            "created_at": None,
            "usage": {
                "used_skills_source": "inferred",
                "inferred_used": True,
                "inferred_used_skills": ["qa-file-reviewer"],
            },
        }
    ]


@pytest.mark.asyncio
async def test_update_run_input_skill_version_is_tenant_and_run_scoped():
    conn = RecordingConnection()

    await repositories.update_run_input_skill_version(
        conn,
        tenant_id="default",
        run_id="run-a",
        skill_version="hash-a",
    )

    sql, params = conn.calls[0]
    assert "update runs" in sql
    assert "jsonb_set" in sql
    assert "release_decision,selected_version" in sql
    assert "tenant_id = %s and id = %s" in sql
    assert params == ('"hash-a"', '"manifest_pin"', '"hash-a"', "default", "run-a")


@pytest.mark.asyncio
async def test_upsert_skill_version_records_immutable_catalog_version():
    conn = RecordingConnection()

    await repositories.upsert_skill_version(
        conn,
        skill_id="qa-file-reviewer",
        version="hash-a",
        content_hash="hash-a",
        description="QA review",
        source_json={"kind": "builtin"},
        dependency_ids=["minimax-docx"],
        status="active",
        created_by="admin-a",
    )

    sql, params = conn.calls[0]
    assert "insert into skill_versions" in sql
    assert "on conflict (skill_id, version)" in sql
    assert "do nothing" in sql
    assert "do update set" not in sql
    assert "content_hash = excluded.content_hash" not in sql
    assert params[0].startswith("skv_")
    assert params[1:4] == ("qa-file-reviewer", "hash-a", "hash-a")
    assert '"kind": "builtin"' in str(params)
    assert "minimax-docx" in str(params)


@pytest.mark.asyncio
async def test_update_skill_catalog_version_updates_current_skill_pointer():
    conn = RecordingConnection()

    await repositories.update_skill_catalog_version(
        conn,
        skill_id="qa-file-reviewer",
        version="hash-current",
        description="Current QA review",
    )

    sql, params = conn.calls[0]
    assert "update skills" in sql
    assert "set version = %s" in sql
    assert "where id = %s" in sql
    assert params == ("hash-current", "Current QA review", "qa-file-reviewer")


@pytest.mark.asyncio
async def test_backfill_builtin_skill_version_snapshot_only_updates_incomplete_builtin_rows():
    conn = RecordingConnection()

    await repositories.backfill_builtin_skill_version_snapshot(
        conn,
        skill_id="qa-file-reviewer",
        version="hash-current",
        source_json={
            "kind": "builtin",
            "asset_dir": "qa-file-reviewer",
            "version": "hash-current",
            "files": [{"relative_path": "SKILL.md", "content_base64": "c2tpbGw=", "size_bytes": 5}],
            "dependency_manifests": [
                {
                    "skill_id": "minimax-docx",
                    "version": "hash-dep",
                    "content_hash": "hash-dep",
                    "source": {"kind": "builtin", "asset_dir": "minimax-docx"},
                    "files": [{"relative_path": "SKILL.md", "content_base64": "ZGVw", "size_bytes": 3}],
                }
            ],
        },
        dependency_ids=["minimax-docx"],
        description="Current QA review",
    )

    sql, params = conn.calls[0]
    assert "update skill_versions" in sql
    assert "source_json->>'kind' = 'builtin'" in sql
    assert "not (source_json ? 'files')" in sql
    assert "source_json->'files' is distinct from (%s::jsonb->'files')" in sql
    assert "dependency_ids <> %s::jsonb" in sql
    assert "source_json->'dependency_manifests' is distinct from (%s::jsonb->'dependency_manifests')" in sql
    assert params[3:] == (
        "qa-file-reviewer",
        "hash-current",
        params[0],
        params[1],
        params[0],
    )
    assert '"files"' in params[0]
    assert '"dependency_manifests"' in params[0]
    assert "minimax-docx" in params[1]


@pytest.mark.asyncio
async def test_list_skill_versions_projects_source_and_dependencies():
    class VersionCursor:
        async def fetchall(self):
            return [
                {
                    "skill_id": "qa-file-reviewer",
                    "version": "hash-a",
                    "content_hash": "hash-a",
                    "description": "QA review",
                    "source_json": {"kind": "builtin"},
                    "dependency_ids": ["minimax-docx"],
                    "status": "active",
                    "created_by": "admin-a",
                    "created_at": None,
                }
            ]

    class VersionConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return VersionCursor()

    conn = VersionConnection()

    versions = await repositories.list_skill_versions(conn, skill_id="qa-file-reviewer")

    assert "from skill_versions" in conn.sql
    assert conn.params == ("qa-file-reviewer",)
    assert versions == [
        {
            "skill_id": "qa-file-reviewer",
            "version": "hash-a",
            "content_hash": "hash-a",
            "description": "QA review",
            "source": {"kind": "builtin"},
            "dependency_ids": ["minimax-docx"],
            "status": "active",
            "created_by": "admin-a",
            "created_at": None,
        }
    ]


@pytest.mark.asyncio
async def test_get_effective_skill_version_for_policy_returns_uploaded_source():
    class VersionCursor:
        async def fetchone(self):
            return {
                "skill_id": "qa-file-reviewer",
                "version": "hash-uploaded",
                "content_hash": "hash-uploaded",
                "description": "QA review",
                "source_json": {"kind": "uploaded", "files": [{"relative_path": "SKILL.md"}]},
                "dependency_ids": [],
                "status": "active",
                "created_by": "admin-a",
                "created_at": None,
            }

    class VersionConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return VersionCursor()

    conn = VersionConnection()

    version = await repositories.get_effective_skill_version_for_policy(
        conn,
        skill_id="qa-file-reviewer",
        version="hash-uploaded",
    )

    assert "from skill_versions" in conn.sql
    assert conn.params == ("qa-file-reviewer", "hash-uploaded")
    assert version["source"]["kind"] == "uploaded"
    assert version["version"] == "hash-uploaded"


@pytest.mark.asyncio
async def test_get_skill_projects_status_for_upload_preflight():
    class SkillCursor:
        async def fetchone(self):
            return {"skill_id": "qa-file-reviewer", "version": "0.1.0", "status": "active"}

    class SkillConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return SkillCursor()

    conn = SkillConnection()

    row = await repositories.get_skill(conn, skill_id="qa-file-reviewer")

    assert "from skills" in conn.sql
    assert "version" in conn.sql
    assert conn.params == ("qa-file-reviewer",)
    assert row == {"skill_id": "qa-file-reviewer", "version": "0.1.0", "status": "active"}


@pytest.mark.asyncio
async def test_list_skill_ids_returns_all_catalog_ids_for_dependency_policy():
    class SkillCursor:
        async def fetchall(self):
            return [{"id": "qa-file-reviewer"}, {"id": "minimax-docx"}]

    class SkillConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params=()):
            self.sql = " ".join(sql.split())
            self.params = params
            return SkillCursor()

    conn = SkillConnection()

    skill_ids = await repositories.list_skill_ids(conn)

    assert "from skills" in conn.sql
    assert conn.params == ()
    assert skill_ids == ["qa-file-reviewer", "minimax-docx"]


@pytest.mark.asyncio
async def test_get_skill_release_policy_projects_current_version():
    class ReleaseCursor:
        async def fetchone(self):
            return {
                "skill_id": "qa-file-reviewer",
                "channel": "stable",
                "current_version": "hash-b",
                "previous_version": "hash-a",
                "rollout_percent": 100,
                "status": "active",
                "promoted_by": "dev-admin",
                "promoted_at": None,
            }

    class ReleaseConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return ReleaseCursor()

    conn = ReleaseConnection()

    policy = await repositories.get_skill_release_policy(
        conn,
        tenant_id="default",
        skill_id="qa-file-reviewer",
    )

    assert "status = 'active'" in conn.sql
    assert policy == {
        "skill_id": "qa-file-reviewer",
        "channel": "stable",
        "current_version": "hash-b",
        "previous_version": "hash-a",
        "rollout_percent": 100,
        "status": "active",
        "promoted_by": "dev-admin",
        "promoted_at": None,
    }


@pytest.mark.asyncio
async def test_set_skill_release_policy_is_tenant_scoped_and_preserves_previous_version():
    conn = RecordingConnection()

    await repositories.set_skill_release_policy(
        conn,
        tenant_id="default",
        skill_id="qa-file-reviewer",
        version="hash-b",
        previous_version="hash-a",
        promoted_by="dev-admin",
        channel="stable",
        rollout_percent=100,
    )

    sql, params = conn.calls[0]
    assert "insert into skill_release_policies" in sql
    assert "on conflict (tenant_id, skill_id, channel)" in sql
    assert "current_version = excluded.current_version" in sql
    assert params[0].startswith("skr_")
    assert params[1:6] == ("default", "qa-file-reviewer", "stable", "hash-b", "hash-a")
    assert params[6:9] == (100, "active", "dev-admin")


@pytest.mark.asyncio
async def test_diff_skill_versions_reports_manifest_and_dependency_changes():
    class DiffCursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class DiffConnection:
        async def execute(self, sql, params):
            assert "from skill_versions" in " ".join(sql.split())
            version = params[1]
            rows = {
                "hash-a": {
                    "skill_id": "qa-file-reviewer",
                    "version": "hash-a",
                    "content_hash": "hash-a",
                    "description": "old QA",
                    "source_json": {"kind": "builtin", "asset_dir": "qa-file-reviewer"},
                    "dependency_ids": ["minimax-docx"],
                    "status": "active",
                    "created_by": "admin-a",
                    "created_at": None,
                },
                "hash-b": {
                    "skill_id": "qa-file-reviewer",
                    "version": "hash-b",
                    "content_hash": "hash-b",
                    "description": "new QA",
                    "source_json": {"kind": "builtin", "asset_dir": "qa-file-reviewer-v2"},
                    "dependency_ids": ["minimax-docx", "term-checker"],
                    "status": "active",
                    "created_by": "admin-a",
                    "created_at": None,
                },
            }
            return DiffCursor(rows.get(version))

    diff = await repositories.diff_skill_versions(
        DiffConnection(),
        skill_id="qa-file-reviewer",
        from_version="hash-a",
        to_version="hash-b",
    )

    assert diff == {
        "skill_id": "qa-file-reviewer",
        "from_version": "hash-a",
        "to_version": "hash-b",
        "content_hash_changed": True,
        "description_changed": True,
        "source_changed": True,
        "dependency_added": ["term-checker"],
        "dependency_removed": [],
    }


@pytest.mark.asyncio
async def test_diff_skill_versions_raises_when_version_missing():
    class MissingCursor:
        async def fetchone(self):
            return None

    class MissingConnection:
        async def execute(self, sql, params):
            return MissingCursor()

    with pytest.raises(repositories.RepositoryNotFoundError, match="skill_version_not_found"):
        await repositories.diff_skill_versions(
            MissingConnection(),
            skill_id="qa-file-reviewer",
            from_version="hash-a",
            to_version="hash-b",
        )


@pytest.mark.asyncio
async def test_admin_skill_detail_projects_versions_and_recent_snapshots():
    class DetailCursor:
        def __init__(self, *, one=None, many=None):
            self.one = one
            self.many = many or []

        async def fetchone(self):
            return self.one

        async def fetchall(self):
            return self.many

    class SkillDetailConnection:
        async def execute(self, sql, params):
            compact = " ".join(sql.split())
            if "from skills" in compact:
                assert params == ("tenant-a", "qa-file-reviewer")
                return DetailCursor(
                    one={
                        "skill_id": "qa-file-reviewer",
                        "name": "qa-file-reviewer",
                        "version": "0.1.0",
                        "description": "QA review",
                        "input_modes": ["docx"],
                        "output_modes": ["reviewed_docx"],
                        "executor_type": "claude_agent",
                        "status": "active",
                        "visible_to_user": True,
                    }
                )
            if "from skill_versions" in compact:
                assert params == ("qa-file-reviewer",)
                return DetailCursor(
                    many=[
                        {
                            "skill_id": "qa-file-reviewer",
                            "version": "hash-a",
                            "content_hash": "hash-a",
                            "description": "QA review",
                            "source_json": {"kind": "builtin"},
                            "dependency_ids": ["minimax-docx"],
                            "status": "active",
                            "created_by": "admin-a",
                            "created_at": None,
                        }
                    ]
                )
            if "from skill_release_policies" in compact:
                assert params == ("tenant-a", "qa-file-reviewer", "stable")
                return DetailCursor(
                    one={
                        "skill_id": "qa-file-reviewer",
                        "channel": "stable",
                        "current_version": "hash-a",
                        "previous_version": "0.1.0",
                        "rollout_percent": 100,
                        "status": "active",
                        "promoted_by": "admin-a",
                        "promoted_at": None,
                    }
                )
            if "from run_skill_snapshots" in compact:
                assert params == ("tenant-a", "qa-file-reviewer")
                return DetailCursor(
                    many=[
                        {
                            "run_id": "run-a",
                            "skill_id": "qa-file-reviewer",
                            "skill_version": "hash-a",
                            "content_hash": "hash-a",
                            "source_json": {"kind": "builtin"},
                            "dependency_ids": ["minimax-docx"],
                            "allowed": True,
                            "staged": True,
                            "used": True,
                            "created_at": None,
                        }
                    ]
                )
            raise AssertionError(compact)

    detail = await repositories.get_admin_skill_detail(
        SkillDetailConnection(),
        tenant_id="tenant-a",
        skill_id="qa-file-reviewer",
    )

    assert detail["skill"]["skill_id"] == "qa-file-reviewer"
    assert detail["versions"][0]["content_hash"] == "hash-a"
    assert detail["versions"][0]["source"] == {"kind": "builtin"}
    assert detail["versions"][0]["dependency_ids"] == ["minimax-docx"]
    assert detail["release_policy"]["current_version"] == "hash-a"
    assert detail["release_policy"]["previous_version"] == "0.1.0"
    assert detail["recent_snapshots"][0]["run_id"] == "run-a"
    assert detail["recent_snapshots"][0]["dependency_ids"] == ["minimax-docx"]


@pytest.mark.asyncio
async def test_set_workbench_skill_status_rejects_internal_dependency_skill():
    conn = RecordingConnection()

    with pytest.raises(repositories.RepositoryNotFoundError, match="workbench_skill_not_found"):
        await repositories.set_workbench_skill_status(
            conn,
            tenant_id="default",
            skill_id="minimax-docx",
            status="active",
        )

    assert conn.calls == []


@pytest.mark.asyncio
async def test_list_run_artifacts_returns_manifest_contract_columns():
    conn = RecordingConnection()

    await list_run_artifacts(conn, tenant_id="tenant-a", run_id="run-a")

    sql, _ = conn.calls[0]
    assert "trace_id" in sql
    assert "manifest_version" in sql


@pytest.mark.asyncio
async def test_admin_run_detail_projects_g2_trace_event_artifact_and_audit_contracts():
    class DetailCursor:
        def __init__(self, *, one=None, many=None):
            self.one = one
            self.many = many or []

        async def fetchone(self):
            return self.one

        async def fetchall(self):
            return self.many

    class DetailConnection:
        async def execute(self, sql, params):
            compact = " ".join(sql.split())
            if "from runs where tenant_id" in compact:
                return DetailCursor(
                    one={
                        "id": "run-a",
                        "trace_id": "trace_run_a",
                        "schema_version": "ai-platform.run.v1",
                        "executor_schema_version": "ai-platform.executor-result.v1",
                        "session_id": "ses-a",
                        "user_id": "user-a",
                        "workspace_id": "default",
                        "status": "succeeded",
                        "agent_id": "qa-word-review",
                        "skill_id": "qa-file-reviewer",
                        "created_at": None,
                        "queued_at": None,
                        "started_at": None,
                        "finished_at": None,
                        "cancel_requested_at": None,
                        "cancel_requested_by": None,
                        "input_json": {},
                        "result_json": {},
                        "error_code": None,
                        "error_message": None,
                    }
                )
            if "from run_events" in compact:
                return DetailCursor(
                    many=[
                        {
                            "id": "evt-a",
                            "trace_id": "trace_run_a",
                            "schema_version": "ai-platform.event-envelope.v1",
                            "event_type": "run_succeeded",
                            "stage": "worker",
                            "message": "Run succeeded",
                            "severity": "info",
                            "visible_to_user": True,
                            "error_code": None,
                            "latency_ms": 12,
                            "input_token_count": 1,
                            "output_token_count": 2,
                            "total_token_count": 3,
                            "estimated_cost_minor": 4,
                            "payload_json": {"message": "done"},
                            "created_at": None,
                        },
                        {
                            "id": "evt-skill-b",
                            "trace_id": "trace_run_a",
                            "schema_version": "ai-platform.event-envelope.v1",
                            "event_type": "skill_used",
                            "stage": "skills",
                            "message": "Platform Skill used: qa-file-reviewer",
                            "severity": "info",
                            "visible_to_user": False,
                            "error_code": None,
                            "latency_ms": None,
                            "input_token_count": 0,
                            "output_token_count": 0,
                            "total_token_count": 0,
                            "estimated_cost_minor": 0,
                            "payload_json": {
                                "skill_id": "qa-file-reviewer",
                                "used_skills_source": "executor_hook",
                                "source": "claude_agent_sdk_hook",
                                "tool_use_id": "tool-use-b",
                            },
                            "created_at": None,
                        },
                        {
                            "id": "evt-skill-visible",
                            "trace_id": "trace_run_a",
                            "schema_version": "ai-platform.event-envelope.v1",
                            "event_type": "skill_used",
                            "stage": "skills",
                            "message": "Visible skill label",
                            "severity": "info",
                            "visible_to_user": True,
                            "error_code": None,
                            "latency_ms": None,
                            "input_token_count": 0,
                            "output_token_count": 0,
                            "total_token_count": 0,
                            "estimated_cost_minor": 0,
                            "payload_json": {
                                "skill_id": "qa-file-reviewer",
                                "used_skills_source": "visible_projection",
                                "source": "visible_event",
                                "tool_use_id": "visible-tool",
                            },
                            "created_at": None,
                        },
                        {
                            "id": "evt-skill-a",
                            "trace_id": "trace_run_a",
                            "schema_version": "ai-platform.event-envelope.v1",
                            "event_type": "skill_used",
                            "stage": "skills",
                            "message": "Platform Skill used: qa-file-reviewer",
                            "severity": "info",
                            "visible_to_user": False,
                            "error_code": None,
                            "latency_ms": None,
                            "input_token_count": 0,
                            "output_token_count": 0,
                            "total_token_count": 0,
                            "estimated_cost_minor": 0,
                            "payload_json": {
                                "skill_id": "qa-file-reviewer",
                                "used_skills_source": "executor_hook",
                                "source": "claude_agent_sdk_hook",
                                "tool_use_id": "tool-use-a",
                            },
                            "created_at": None,
                        }
                    ]
                )
            if "from run_steps" in compact:
                return DetailCursor(many=[])
            if "from artifacts" in compact:
                return DetailCursor(
                    many=[
                        {
                            "id": "art-a",
                            "trace_id": "trace_run_a",
                            "manifest_version": "ai-platform.artifact-manifest.v1",
                            "artifact_type": "reviewed_docx",
                            "label": "审核 Word",
                            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            "storage_key": "tenants/default/private.docx",
                            "size_bytes": 10,
                            "manifest_json": {"storage_key": "tenants/default/private.docx", "source_file_id": "file-a"},
                            "created_at": None,
                        }
                    ]
                )
            if "from run_skill_snapshots" in compact:
                return DetailCursor(
                    many=[
                        {
                            "skill_id": "qa-file-reviewer",
                            "skill_version": "hash-a",
                            "content_hash": "hash-a",
                            "source_json": {"kind": "builtin"},
                            "dependency_ids": ["minimax-docx"],
                            "allowed": True,
                            "staged": True,
                            "used": True,
                            "created_at": None,
                        }
                    ]
                )
            if "from audit_logs" in compact:
                return DetailCursor(
                    many=[
                        {
                            "id": "aud-a",
                            "trace_id": "trace_run_a",
                            "schema_version": "ai-platform.audit-event.v1",
                            "user_id": "admin-a",
                            "action": "admin_artifact_downloaded",
                            "target_type": "artifact",
                            "target_id": "art-a",
                            "payload_json": {"run_id": "run-a"},
                            "created_at": None,
                        }
                    ]
                )
            raise AssertionError(compact)

    detail = await get_admin_run_detail(DetailConnection(), tenant_id="tenant-a", run_id="run-a")

    assert detail["run"]["trace_id"] == "trace_run_a"
    assert detail["run"]["contract_version"] == "ai-platform.run.v1"
    assert detail["run"]["executor_schema_version"] == "ai-platform.executor-result.v1"
    assert detail["events"][0]["schema_version"] == "ai-platform.event-envelope.v1"
    assert detail["events"][0]["trace_id"] == "trace_run_a"
    assert detail["events"][0]["token_counts"] == {"input": 1, "output": 2, "total": 3}
    assert detail["artifacts"][0]["trace_id"] == "trace_run_a"
    assert detail["artifacts"][0]["manifest"]["schema_version"] == "ai-platform.artifact-manifest.v1"
    assert "storage_key" not in str(detail["artifacts"][0]["manifest"])
    assert detail["skill_snapshots"] == [
        {
            "skill_id": "qa-file-reviewer",
            "skill_version": "hash-a",
            "content_hash": "hash-a",
            "source": {"kind": "builtin"},
            "dependency_ids": ["minimax-docx"],
            "allowed": True,
            "staged": True,
            "used": True,
            "usage": {
                "event_source": "claude_agent_sdk_hook",
                "event_count": 2,
                "tool_use_ids": ["tool-use-a", "tool-use-b"],
            },
            "created_at": None,
        }
    ]
    assert detail["audit"][0]["schema_version"] == "ai-platform.audit-event.v1"
    assert detail["audit"][0]["trace_id"] == "trace_run_a"


@pytest.mark.asyncio
async def test_admin_run_detail_sanitizes_secret_and_runtime_payloads(monkeypatch):
    async def fake_get_run(conn, *, tenant_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            "user_id": "user-a",
            "workspace_id": "default",
            "status": "failed",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "created_at": None,
            "queued_at": None,
            "started_at": None,
            "finished_at": None,
            "cancel_requested_at": None,
            "cancel_requested_by": None,
            "input_json": {
                "input": {"message": "审核", "api_key": "sk-admin-input"},
                "workerPath": "/var/lib/ai-platform/run-a/worker.py",
                "skill_ids": ["qa-file-reviewer"],
            },
            "result_json": {
                "message": "failed client_secret=admin-result-secret",
                "runtime_private_payload": {"cwd": "/var/lib/ai-platform/run-a"},
                "used_skills": ["qa-file-reviewer"],
            },
            "error_code": "executor_failure token=admin-detail-code-token",
            "error_message": "failed token=admin-error-token /var/lib/ai-platform/run-a/out.log",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.run.v1",
            "executor_schema_version": "ai-platform.executor-result.v1",
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id, **kwargs):
        return [
            {
                "id": "evt-a",
                "trace_id": "trace_run_a",
                "schema_version": "ai-platform.event-envelope.v1",
                "sequence": 1,
                "event_type": "error",
                "stage": "worker",
                "message": "failed token=admin-event-token",
                "severity": "error",
                "visible_to_user": True,
                "error_code": "executor_failure",
                "latency_ms": None,
                "input_token_count": 0,
                "output_token_count": 0,
                "total_token_count": 0,
                "estimated_cost_minor": 0,
                "payload_json": {
                    "runtime_private_payload": {"cwd": "/var/lib/ai-platform/run-a"},
                    "summary": "client_secret=admin-event-secret",
                },
                "created_at": None,
            }
        ]

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-a",
                "run_id": run_id,
                "step_key": "review",
                "step_kind": "agent",
                "status": "failed",
                "title": "Review token=admin-title-token",
                "role": "reviewer client_secret=admin-role-secret",
                "sequence": 1,
                "payload_json": {
                    "skill_ids": ["qa-file-reviewer"],
                    "runtime_private_payload": {"cwd": "/var/lib/ai-platform/run-a"},
                    "note": "client_secret=admin-step-secret",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fake_empty_list(*args, **kwargs):
        return []

    class AuditCursor:
        async def fetchall(self):
            return [
                {
                    "id": "aud-a",
                    "trace_id": "trace_run_a",
                    "schema_version": "ai-platform.audit-event.v1",
                    "user_id": "admin-a",
                    "action": "admin_run_viewed",
                    "target_type": "run",
                    "target_id": "run-a",
                    "payload_json": {
                        "run_id": "run-a",
                        "client_secret": "admin-audit-secret",
                        "runtime_private_payload": {"cwd": "/var/lib/ai-platform/run-a"},
                    },
                    "created_at": None,
                }
            ]

    class AuditConnection:
        async def execute(self, sql, params):
            return AuditCursor()

    monkeypatch.setattr(repositories, "get_run", fake_get_run)
    monkeypatch.setattr(repositories, "list_run_events", fake_list_run_events)
    monkeypatch.setattr(repositories, "list_run_steps", fake_list_run_steps)
    monkeypatch.setattr(repositories, "list_run_artifacts", fake_empty_list)
    monkeypatch.setattr(repositories, "list_run_skill_snapshots", fake_empty_list)

    detail = await get_admin_run_detail(AuditConnection(), tenant_id="tenant-a", run_id="run-a")

    assert detail["run"]["input"]["skill_ids"] == ["qa-file-reviewer"]
    assert detail["run"]["result"]["used_skills"] == ["qa-file-reviewer"]
    assert detail["steps"][0]["payload"]["skill_ids"] == ["qa-file-reviewer"]
    serialized = json.dumps(detail, ensure_ascii=False, default=str)
    assert "sk-admin-input" not in serialized
    assert "admin-result-secret" not in serialized
    assert "admin-detail-code-token" not in serialized
    assert "admin-error-token" not in serialized
    assert "admin-event-token" not in serialized
    assert "admin-event-secret" not in serialized
    assert "admin-title-token" not in serialized
    assert "admin-role-secret" not in serialized
    assert "admin-step-secret" not in serialized
    assert "admin-audit-secret" not in serialized
    assert "/var/lib/ai-platform" not in serialized
    assert "runtime_private_payload" not in serialized


@pytest.mark.asyncio
async def test_admin_run_detail_sanitizes_dirty_skill_snapshot_source_and_usage(monkeypatch):
    async def fake_get_run(conn, *, tenant_id, run_id):
        return {
            "id": run_id,
            "session_id": "ses-a",
            "user_id": "user-a",
            "workspace_id": "default",
            "status": "succeeded",
            "agent_id": "qa-word-review",
            "skill_id": "qa-file-reviewer",
            "created_at": None,
            "input_json": {},
            "result_json": {},
            "error_code": None,
            "error_message": None,
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.run.v1",
            "executor_schema_version": "ai-platform.executor-result.v1",
        }

    async def fake_list_run_events(conn, *, tenant_id, run_id, **kwargs):
        return [
            {
                "id": "evt-skill-a",
                "trace_id": "trace_run_a",
                "schema_version": "ai-platform.event-envelope.v1",
                "sequence": 1,
                "event_type": "skill_used",
                "stage": "skills",
                "message": "hidden",
                "severity": "info",
                "visible_to_user": False,
                "error_code": None,
                "latency_ms": None,
                "input_token_count": 0,
                "output_token_count": 0,
                "total_token_count": 0,
                "estimated_cost_minor": 0,
                "payload_json": {
                    "skill_id": "qa-file-reviewer",
                    "source": "claude_agent_sdk_hook token=skill-event-token",
                    "tool_use_id": "tool-use-a token=skill-tool-token",
                },
                "created_at": None,
            }
        ]

    async def fake_list_run_skill_snapshots(conn, *, tenant_id, run_id):
        return [
            {
                "skill_id": "qa-file-reviewer",
                "skill_version": "hash-a",
                "content_hash": "hash-a",
                "source": {
                    "kind": "builtin",
                    "client_secret": "skill-source-secret",
                    "local_path": "/var/lib/ai-platform/skills/qa-file-reviewer",
                    "nested": {"safe": "kept", "token": "nested-token"},
                },
                "dependency_ids": ["minimax-docx"],
                "allowed": True,
                "staged": True,
                "used": True,
                "usage": {"used_skills_source": "claude_agent_sdk_hook token=skill-persisted-token"},
                "created_at": None,
            }
        ]

    async def fake_empty_list(*args, **kwargs):
        return []

    class EmptyAuditConnection:
        async def execute(self, sql, params):
            class Cursor:
                async def fetchall(self):
                    return []

            return Cursor()

    monkeypatch.setattr(repositories, "get_run", fake_get_run)
    monkeypatch.setattr(repositories, "list_run_events", fake_list_run_events)
    monkeypatch.setattr(repositories, "list_run_steps", fake_empty_list)
    monkeypatch.setattr(repositories, "list_run_artifacts", fake_empty_list)
    monkeypatch.setattr(repositories, "list_run_skill_snapshots", fake_list_run_skill_snapshots)

    detail = await get_admin_run_detail(EmptyAuditConnection(), tenant_id="tenant-a", run_id="run-a")

    snapshot = detail["skill_snapshots"][0]
    assert snapshot["skill_id"] == "qa-file-reviewer"
    assert snapshot["source"] == {"kind": "builtin", "nested": {"safe": "kept"}}
    assert snapshot["usage"]["used_skills_source"] == "claude_agent_sdk_hook token=[redacted-secret]"
    assert snapshot["usage"]["event_source"] == "claude_agent_sdk_hook token=[redacted-secret]"
    assert snapshot["usage"]["tool_use_ids"] == ["tool-use-a token=[redacted-secret]"]
    serialized = json.dumps(detail, ensure_ascii=False, default=str)
    assert "skill-source-secret" not in serialized
    assert "nested-token" not in serialized
    assert "skill-persisted-token" not in serialized
    assert "skill-event-token" not in serialized
    assert "skill-tool-token" not in serialized
    assert "/var/lib/ai-platform" not in serialized


@pytest.mark.asyncio
async def test_complete_run_persists_g2_observability_columns_from_result_json():
    conn = RecordingConnection()

    await complete_run(
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

    sql, params = conn.calls[0]
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


@pytest.mark.asyncio
async def test_get_admin_runtime_run_summary_counts_statuses_and_redacts_failures():
    class SummaryCursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class SummaryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            compact = " ".join(sql.lower().split())
            self.calls.append((compact, params))
            if "group by status" in compact:
                return SummaryCursor(
                    rows=[
                        {"status": "queued", "count": 2},
                        {"status": "running", "count": 1},
                        {"status": "failed", "count": 1},
                    ]
                )
            if "from runs" in compact and "error_code" in compact:
                return SummaryCursor(
                    rows=[
                        {
                            "id": "run-failed",
                            "user_id": "user-a",
                            "agent_id": "qa-word-review",
                            "skill_id": "qa-file-reviewer",
                            "error_code": "executor_failure token=run-code-token",
                            "error_message": "failed token=run-message-token /var/lib/ai-platform/x",
                            "created_at": None,
                        }
                    ]
                )
            raise AssertionError(compact)

    summary = await repositories.get_admin_runtime_run_summary(
        SummaryConnection(),
        tenant_id="tenant-a",
        limit=5,
    )

    assert summary["total"] == 4
    assert summary["active"] == 3
    assert summary["terminal"] == 1
    assert summary["by_status"] == {"queued": 2, "running": 1, "failed": 1}
    assert "skill_id" not in summary["recent_failures"][0]
    assert summary["recent_failures"][0]["error_code"] == "executor_failure token=[redacted-secret]"
    assert summary["recent_failures"][0]["error_message"] == ""
    serialized = json.dumps(summary, ensure_ascii=False, default=str)
    assert "qa-file-reviewer" not in serialized
    assert "run-code-token" not in serialized
    assert "run-message-token" not in serialized
    assert "/var/lib/ai-platform" not in serialized


@pytest.mark.asyncio
async def test_get_admin_runtime_observability_summary_coerces_nulls_to_defaults():
    class SummaryCursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class SummaryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            assert "tenant_id = %s" in sql
            assert all(param == "tenant-a" for param in params)
            return SummaryCursor(
                {
                    "event_count": None,
                    "artifact_count": None,
                    "error_count": None,
                    "error_types": None,
                    "avg_latency_ms": None,
                    "max_latency_ms": None,
                    "input_token_count": None,
                    "output_token_count": None,
                    "total_token_count": None,
                    "estimated_cost_minor": None,
                }
            )

    summary = await repositories.get_admin_runtime_observability_summary(
        SummaryConnection(),
        tenant_id="tenant-a",
    )

    assert summary == {
        "event_count": 0,
        "artifact_count": 0,
        "error_count": 0,
        "error_types": {},
        "latency_ms": {"avg": None, "max": None},
        "token_counts": {"input": 0, "output": 0, "total": 0},
        "estimated_cost_minor": 0,
    }


@pytest.mark.asyncio
async def test_get_admin_runtime_observability_summary_uses_run_totals_for_terminal_token_cost():
    class SummaryCursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class SummaryConnection:
        async def execute(self, sql, params):
            compact = " ".join(sql.lower().split())
            assert all(param == "tenant-a" for param in params)
            assert "+ event_summary.event_input_token_count" not in compact
            assert "+ event_summary.event_output_token_count" not in compact
            assert "+ event_summary.event_total_token_count" not in compact
            assert "+ event_summary.event_estimated_cost_minor" not in compact
            return SummaryCursor(
                {
                    "event_count": 2,
                    "artifact_count": 1,
                    "error_count": 2,
                    "error_types": {"executor_failure": 2},
                    "avg_latency_ms": 250,
                    "max_latency_ms": 300,
                    "input_token_count": 10,
                    "output_token_count": 20,
                    "total_token_count": 30,
                    "estimated_cost_minor": 7,
                }
            )

    summary = await repositories.get_admin_runtime_observability_summary(
        SummaryConnection(),
        tenant_id="tenant-a",
    )

    assert summary["event_count"] == 2
    assert summary["error_count"] == 2
    assert summary["latency_ms"] == {"avg": 250, "max": 300}
    assert summary["token_counts"] == {"input": 10, "output": 20, "total": 30}
    assert summary["estimated_cost_minor"] == 7
