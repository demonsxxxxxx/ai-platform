import json
import pytest
import app.identity.infrastructure.audit_postgres as _repo_owner_app_identity_infrastructure_audit_postgres
import app.mcp.infrastructure.tool_policies_postgres as _repo_owner_app_mcp_infrastructure_tool_policies_postgres
import app.runs.infrastructure.admin_queries_postgres as _repo_owner_app_runs_infrastructure_admin_queries_postgres
import app.runs.infrastructure.admin_queries_postgres as run_queries_persistence
from app.identity.infrastructure.audit_postgres import append_audit_log
from app.platform.postgres.errors import RepositoryConflictError
from app.runs.infrastructure.admin_queries_postgres import get_admin_run_detail
from tests.support.repository_fixtures import FakeConnection, RecordingConnection


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

    monkeypatch.setattr(run_queries_persistence, "get_run", fake_get_run)

    with pytest.raises(RepositoryConflictError, match="invalid_run_contract"):
        await _repo_owner_app_runs_infrastructure_admin_queries_postgres.get_admin_run_detail(FakeConnection(), tenant_id="tenant-a", run_id="run-a")


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

    monkeypatch.setattr(run_queries_persistence, "get_run", fake_get_run)
    monkeypatch.setattr(run_queries_persistence, "list_run_events", fake_empty_list)
    monkeypatch.setattr(run_queries_persistence, "list_run_steps", fake_empty_list)
    monkeypatch.setattr(run_queries_persistence, "list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr(run_queries_persistence, "list_run_skill_snapshots", fake_empty_list)

    with pytest.raises(RepositoryConflictError, match="invalid_artifact_manifest_schema_version"):
        await _repo_owner_app_runs_infrastructure_admin_queries_postgres.get_admin_run_detail(FakeConnection(), tenant_id="tenant-a", run_id="run-a")


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

    class EmptyListCursor:
        async def fetchall(self):
            return []

    class AuditConnection:
        async def execute(self, sql, params):
            if "from sandbox_leases" in " ".join(sql.split()):
                return EmptyListCursor()
            return AuditCursor()

    monkeypatch.setattr(run_queries_persistence, "get_run", fake_get_run)
    monkeypatch.setattr(run_queries_persistence, "list_run_events", fake_empty_list)
    monkeypatch.setattr(run_queries_persistence, "list_run_steps", fake_empty_list)
    monkeypatch.setattr(run_queries_persistence, "list_run_artifacts", fake_empty_list)
    monkeypatch.setattr(run_queries_persistence, "list_run_skill_snapshots", fake_empty_list)

    with pytest.raises(RepositoryConflictError, match="invalid_audit_event_schema_version"):
        await _repo_owner_app_runs_infrastructure_admin_queries_postgres.get_admin_run_detail(AuditConnection(), tenant_id="tenant-a", run_id="run-a")


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
async def test_list_admin_tool_policy_history_uses_bounded_tenant_scoped_audit_query():
    class HistoryCursor:
        async def fetchall(self):
            return [{"id": "aud-policy", "target_id": "ragflow-knowledge-search"}]

    class HistoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return HistoryCursor()

    conn = HistoryConnection()

    rows = await _repo_owner_app_mcp_infrastructure_tool_policies_postgres.list_admin_tool_policy_history(
        conn,
        tenant_id="tenant-a",
        tool_id="ragflow-knowledge-search",
        limit=25,
    )

    assert rows == [{"id": "aud-policy", "target_id": "ragflow-knowledge-search"}]
    sql, params = conn.calls[0]
    assert "from audit_logs" in sql
    assert "tenant_id = %s" in sql
    assert "target_type = %s" in sql
    assert "action = %s" in sql
    assert "target_id = %s" in sql
    assert "limit %s" in sql.lower()
    assert params == (
        "tenant-a",
        "tool_policy",
        "admin.tool_policy.updated",
        "ragflow-knowledge-search",
        25,
    )


@pytest.mark.asyncio
async def test_list_admin_tool_policy_history_clamps_limit_for_direct_callers():
    class HistoryCursor:
        async def fetchall(self):
            return []

    class HistoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return HistoryCursor()

    conn = HistoryConnection()

    await _repo_owner_app_mcp_infrastructure_tool_policies_postgres.list_admin_tool_policy_history(
        conn,
        tenant_id="tenant-a",
        tool_id=None,
        limit=9999,
    )
    await _repo_owner_app_mcp_infrastructure_tool_policies_postgres.list_admin_tool_policy_history(
        conn,
        tenant_id="tenant-a",
        tool_id=None,
        limit=-5,
    )
    await _repo_owner_app_mcp_infrastructure_tool_policies_postgres.list_admin_tool_policy_history(
        conn,
        tenant_id="tenant-a",
        tool_id=None,
        limit=0,
    )

    assert conn.calls[0][1] == ("tenant-a", "tool_policy", "admin.tool_policy.updated", 500)
    assert conn.calls[1][1] == ("tenant-a", "tool_policy", "admin.tool_policy.updated", 1)
    assert conn.calls[2][1] == ("tenant-a", "tool_policy", "admin.tool_policy.updated", 1)


@pytest.mark.asyncio
async def test_list_role_governance_audit_history_uses_bounded_tenant_scoped_query():
    class RoleGovernanceHistoryCursor:
        async def fetchall(self):
            return [{"id": "aud-role", "target_id": "skill_developer"}]

    class RoleGovernanceHistoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return RoleGovernanceHistoryCursor()

    conn = RoleGovernanceHistoryConnection()

    rows = await _repo_owner_app_identity_infrastructure_audit_postgres.list_role_governance_audit_history(
        conn,
        tenant_id="tenant-a",
        user_id="ordinary",
        limit=10,
    )

    assert rows == [{"id": "aud-role", "target_id": "skill_developer"}]
    sql, params = conn.calls[0]
    assert "from audit_logs" in sql
    assert "tenant_id = %s" in sql
    assert "action = any(%s)" in sql
    assert "user_id = %s or payload_json->>'requester_id' = %s" in sql
    assert "order by created_at desc, id desc" in sql
    assert "limit %s" in sql.lower()
    assert params == (
        "tenant-a",
        [
            "role_governance.request.created",
            "role_governance.approval.approve_requested",
            "role_governance.approval.reject_requested",
            "role_governance.rollback.requested",
        ],
        "ordinary",
        "ordinary",
        10,
    )


@pytest.mark.asyncio
async def test_list_role_governance_audit_history_clamps_limit_for_direct_callers():
    class RoleGovernanceHistoryCursor:
        async def fetchall(self):
            return []

    class RoleGovernanceHistoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return RoleGovernanceHistoryCursor()

    conn = RoleGovernanceHistoryConnection()

    await _repo_owner_app_identity_infrastructure_audit_postgres.list_role_governance_audit_history(conn, tenant_id="tenant-a", limit=9999)
    await _repo_owner_app_identity_infrastructure_audit_postgres.list_role_governance_audit_history(conn, tenant_id="tenant-a", limit=-5)
    await _repo_owner_app_identity_infrastructure_audit_postgres.list_role_governance_audit_history(conn, tenant_id="tenant-a", limit=0)

    assert conn.calls[0][1][-1] == 100
    assert conn.calls[1][1][-1] == 1
    assert conn.calls[2][1][-1] == 1


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
            if "from runs" in compact and "where runs.tenant_id" in compact:
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
            if "from sandbox_leases" in compact:
                return DetailCursor(
                    many=[
                        {
                            "id": "lease-a",
                            "tenant_id": "tenant-a",
                            "workspace_id": "default",
                            "user_id": "user-a",
                            "session_id": "ses-a",
                            "run_id": "run-a",
                            "trace_id": "trace_lease_a",
                            "sandbox_mode": "ephemeral",
                            "provider": "fake",
                            "status": "released",
                            "browser_enabled": False,
                            "resource_limits_json": {"cpu": 1, "token": "secret-limit"},
                            "user_visible_payload_json": {
                                "workspace_fingerprint": "tenant-a:default:ses-a:run-a",
                                "runtime_private_payload": {"cwd": "/var/lib/ai-platform/run-a"},
                            },
                            "lease_payload_json": {
                                "source": "foundation_runtime_lifecycle_probe",
                                "container_id": "exec-run-a",
                                "container_name": "executor-exec-run-a",
                                "executor_url": "http://executor.internal",
                                "workspace_host_path": "/var/lib/ai-platform/run-a",
                                "workspace_container_path": "/workspace",
                                "labels": {"runtime": "private"},
                                "client_secret": "lease-secret",
                            },
                            "runtime_container_id": "exec-run-a",
                            "runtime_container_name": "executor-exec-run-a",
                            "runtime_executor_url": "http://executor.internal",
                            "runtime_workspace_container_path": "/workspace",
                            "runtime_handle_verified_at": "2026-07-11T00:00:00Z",
                            "heartbeat_at": None,
                            "expires_at": None,
                            "released_at": None,
                            "release_reason": "completed",
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
    assert detail["sandbox_leases"][0]["lease_id"] == "lease-a"
    assert detail["sandbox_leases"][0]["lease_payload"] == {"source": "foundation_runtime_lifecycle_probe"}
    assert "resource_limits" in detail["sandbox_leases"][0]
    serialized_leases = json.dumps(detail["sandbox_leases"], ensure_ascii=False, default=str)
    assert "lease-secret" not in serialized_leases
    assert "secret-limit" not in serialized_leases
    assert "/var/lib/ai-platform" not in serialized_leases
    assert "exec-run-a" not in serialized_leases
    assert "executor.internal" not in serialized_leases
    assert "runtime_handle_verified_at" not in serialized_leases
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
    serialized_skill_snapshots = json.dumps(detail["skill_snapshots"], ensure_ascii=False, default=str)
    assert '"skill_version": "hash-a"' in serialized_skill_snapshots
    assert '"content_hash": "hash-a"' in serialized_skill_snapshots
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

    class EmptyListCursor:
        async def fetchall(self):
            return []

    class AuditConnection:
        async def execute(self, sql, params):
            if "from sandbox_leases" in " ".join(sql.split()):
                return EmptyListCursor()
            return AuditCursor()

    monkeypatch.setattr(run_queries_persistence, "get_run", fake_get_run)
    monkeypatch.setattr(run_queries_persistence, "list_run_events", fake_list_run_events)
    monkeypatch.setattr(run_queries_persistence, "list_run_steps", fake_list_run_steps)
    monkeypatch.setattr(run_queries_persistence, "list_run_artifacts", fake_empty_list)
    monkeypatch.setattr(run_queries_persistence, "list_run_skill_snapshots", fake_empty_list)

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

    monkeypatch.setattr(run_queries_persistence, "get_run", fake_get_run)
    monkeypatch.setattr(run_queries_persistence, "list_run_events", fake_list_run_events)
    monkeypatch.setattr(run_queries_persistence, "list_run_steps", fake_empty_list)
    monkeypatch.setattr(run_queries_persistence, "list_run_artifacts", fake_empty_list)
    monkeypatch.setattr(run_queries_persistence, "list_run_skill_snapshots", fake_list_run_skill_snapshots)

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


@pytest.mark.asyncio


@pytest.mark.asyncio


@pytest.mark.asyncio


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

    summary = await _repo_owner_app_runs_infrastructure_admin_queries_postgres.get_admin_runtime_run_summary(
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
async def test_get_admin_runtime_admission_summary_counts_same_tenant_active_users():
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
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            assert "where tenant_id = %s" in normalized
            assert "status in ('queued', 'running')" in normalized
            assert "input_json" not in normalized
            assert "skill_id" not in normalized
            if "sum(active)" in normalized:
                return SummaryCursor(row={"active_runs": 9, "active_users": 3, "saturated_users": 2})
            return SummaryCursor(
                rows=[
                    {"user_id": "user-a", "active": 3},
                    {"user_id": "user-b", "active": 2},
                ]
            )

    conn = SummaryConnection()

    summary = await _repo_owner_app_runs_infrastructure_admin_queries_postgres.get_admin_runtime_admission_summary(
        conn,
        tenant_id="tenant-a",
        limit=3,
        top_user_limit=2,
    )

    assert summary == {
        "policy_active": True,
        "max_active_runs_per_user": 3,
        "active_runs": 9,
        "active_users": 3,
        "saturated_users": 2,
        "top_users": [
            {"user_id": "user-a", "active": 3, "saturated": True},
            {"user_id": "user-b", "active": 2, "saturated": False},
        ],
    }
    assert conn.calls[0][1] == ("tenant-a", 3, 3)
    assert conn.calls[1][1] == ("tenant-a", 2)


@pytest.mark.asyncio
async def test_get_admin_runtime_admission_summary_disables_saturation_when_limit_off():
    class SummaryCursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class SummaryConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if "sum(active)" in normalized:
                return SummaryCursor(row={"active_runs": 7, "active_users": 1, "saturated_users": 0})
            return SummaryCursor(rows=[{"user_id": "user-a", "active": 7}])

    summary = await _repo_owner_app_runs_infrastructure_admin_queries_postgres.get_admin_runtime_admission_summary(
        SummaryConnection(),
        tenant_id="tenant-a",
        limit=0,
        top_user_limit=10,
    )

    assert summary["policy_active"] is False
    assert summary["max_active_runs_per_user"] == 0
    assert summary["active_runs"] == 7
    assert summary["active_users"] == 1
    assert summary["saturated_users"] == 0
    assert summary["top_users"] == [{"user_id": "user-a", "active": 7, "saturated": False}]


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
                    "p50_latency_ms": None,
                    "p95_latency_ms": None,
                    "p99_latency_ms": None,
                    "input_token_count": None,
                    "output_token_count": None,
                    "total_token_count": None,
                    "estimated_cost_minor": None,
                }
            )

    summary = await _repo_owner_app_runs_infrastructure_admin_queries_postgres.get_admin_runtime_observability_summary(
        SummaryConnection(),
        tenant_id="tenant-a",
    )

    assert summary == {
        "event_count": 0,
        "artifact_count": 0,
        "error_count": 0,
        "error_types": {},
        "error_categories": {},
        "latency_ms": {"avg": None, "max": None, "p50": None, "p95": None, "p99": None},
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
            assert len(params) == 5
            assert compact.count("tenant_id = %s") == 5
            assert "+ event_summary.event_input_token_count" not in compact
            assert "+ event_summary.event_output_token_count" not in compact
            assert "+ event_summary.event_total_token_count" not in compact
            assert "+ event_summary.event_estimated_cost_minor" not in compact
            assert "percentile_cont(0.5)" in compact
            assert "percentile_cont(0.95)" in compact
            assert "percentile_cont(0.99)" in compact
            return SummaryCursor(
                {
                    "event_count": 2,
                    "artifact_count": 1,
                    "error_count": 2,
                    "error_types": {"executor_failure": 2},
                    "avg_latency_ms": 250,
                    "max_latency_ms": 300,
                    "p50_latency_ms": 240,
                    "p95_latency_ms": 295,
                    "p99_latency_ms": 299,
                    "input_token_count": 10,
                    "output_token_count": 20,
                    "total_token_count": 30,
                    "estimated_cost_minor": 7,
                }
            )

    summary = await _repo_owner_app_runs_infrastructure_admin_queries_postgres.get_admin_runtime_observability_summary(
        SummaryConnection(),
        tenant_id="tenant-a",
    )

    assert summary["event_count"] == 2
    assert summary["error_count"] == 2
    assert summary["error_types"] == {"executor_failure": 2}
    assert summary["error_categories"] == {"executor": 2}
    assert summary["latency_ms"] == {"avg": 250, "max": 300, "p50": 240, "p95": 295, "p99": 299}
    assert summary["token_counts"] == {"input": 10, "output": 20, "total": 30}
    assert summary["estimated_cost_minor"] == 7
