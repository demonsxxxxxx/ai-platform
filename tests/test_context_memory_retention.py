import json
import pytest
import app.agent_apps.infrastructure.catalog_postgres as _repo_owner_app_agent_apps_infrastructure_catalog_postgres
import app.context.infrastructure.postgres as _repo_owner_app_context_infrastructure_postgres
import app.identity.infrastructure.postgres as _repo_owner_app_identity_infrastructure_postgres
import app.persistence.artifacts as _repo_owner_app_persistence_artifacts
import app.persistence.retention as _repo_owner_app_persistence_retention
import app.platform.postgres.errors as _repo_owner_app_platform_postgres_errors
from app.context.infrastructure.postgres import admin_delete_memory_record, delete_memory_record
from app.platform.postgres.errors import RepositoryConflictError, RepositoryNotFoundError
from tests.support.repository_fixtures import FakeCursor, RecordingConnection, SingleRowCursor


@pytest.mark.asyncio
async def test_retention_queries_are_bounded_reference_safe_and_skip_locked():
    class RetentionConnection(RecordingConnection):
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if len(self.calls) == 1:
                return SingleRowCursor({"id": "artifact-a", "tenant_id": "tenant-a", "storage_key": "a"})
            return FakeCursor()

    conn = RetentionConnection()
    await _repo_owner_app_persistence_artifacts.queue_expired_artifacts_for_deletion(conn, limit=20)
    lock_sql, lock_params = conn.calls[0]
    write_sql, write_params = conn.calls[1]
    assert "for update of artifacts skip locked" in lock_sql
    assert "sessions.status <> 'active'" in lock_sql
    assert "snapshots.included_artifact_ids ? artifacts.id" in lock_sql
    assert lock_params == (20,)
    assert "insert into object_deletion_outbox" in write_sql
    assert "snapshots.included_artifact_ids ? artifacts.id" in write_sql
    assert "'retention_artifact_cleanup', true" in write_sql
    assert "'deletion_owner_run_id', artifacts.run_id" in write_sql
    assert "run_id = null" in write_sql
    assert json.loads(write_params[0]) == ["artifact-a"]

    await _repo_owner_app_persistence_retention.purge_deleted_memory_records(conn, grace_days=7, limit=25)
    sql, params = conn.calls[-1]
    assert "for update of memory_records skip locked" in sql
    assert "snapshots.included_memory_record_ids ? memory_records.id" in sql
    assert "sessions.status = 'active'" in sql
    assert "delete from memory_records" in sql
    assert params == (7, 25)


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

    row = await _repo_owner_app_context_infrastructure_postgres.create_memory_record(
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

    await _repo_owner_app_context_infrastructure_postgres.create_memory_record(
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
async def test_create_memory_record_strict_mode_redacts_raw_provider_and_jwt_tokens_before_insert():
    raw_openai = "sk-strict1234567890abcdef"
    raw_github = "ghp_strict1234567890abcdef"
    raw_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzdHJpY3QifQ.signature1234567890"

    class MemoryCursor:
        async def fetchone(self):
            return {
                "id": "mem-strict-redacted",
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

    await _repo_owner_app_context_infrastructure_postgres.create_memory_record(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        session_id="session-a",
        record_type="session_summary",
        content=f"raw provider markers {raw_openai} {raw_github} {raw_jwt}",
        metadata_json={
            "source": "test",
            "note": f"raw provider markers {raw_openai} {raw_github}",
            "nested": {"jwt": raw_jwt},
        },
        retention_days=30,
        redaction_mode="strict",
    )

    _, params = conn.calls[0]
    inserted_content = params[7]
    inserted_metadata = json.loads(params[8])
    serialized = f"{inserted_content} {inserted_metadata}"
    assert raw_openai not in serialized
    assert raw_github not in serialized
    assert raw_jwt not in serialized
    assert serialized.count("[redacted-secret]") >= 3


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
        await _repo_owner_app_context_infrastructure_postgres.create_memory_record(
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
        await _repo_owner_app_context_infrastructure_postgres.list_memory_records(
            conn,
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id="general-agent",
            session_id=None,
        )

    assert conn.calls == []


@pytest.mark.asyncio
async def test_list_memory_records_exports_only_active_unexpired_session_memory():
    class MemoryCursor:
        async def fetchall(self):
            return [
                {
                    "id": "mem-active",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-a",
                    "agent_id": "general-agent",
                    "session_id": "session-a",
                    "record_type": "session_summary",
                    "content": "safe summary",
                    "metadata_json": {"source": "test"},
                    "status": "active",
                    "expires_at": "2026-07-03T12:00:00Z",
                    "deleted_at": None,
                    "created_at": "2026-06-03T12:00:00Z",
                    "updated_at": "2026-06-03T12:00:00Z",
                }
            ]

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split()).lower()
            self.calls.append((normalized, params))
            return MemoryCursor()

    conn = MemoryConnection()

    rows = await _repo_owner_app_context_infrastructure_postgres.list_memory_records(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        session_id="session-a",
        limit=50,
    )

    sql, params = conn.calls[0]
    assert "from memory_records" in sql
    assert "tenant_id = %s" in sql
    assert "workspace_id = %s" in sql
    assert "user_id = %s" in sql
    assert "status = 'active'" in sql
    assert "deleted_at is null" in sql
    assert "expires_at is null or expires_at > now()" in sql
    assert "session_id = %s" in sql
    assert params == (
        "tenant-a",
        "workspace-a",
        "user-a",
        "general-agent",
        "general-agent",
        "session-a",
        "session-a",
        50,
    )
    assert rows[0]["id"] == "mem-active"


@pytest.mark.asyncio
async def test_list_admin_memory_records_operator_export_does_not_select_content_or_metadata():
    class MemoryCursor:
        async def fetchall(self):
            return [
                {
                    "id": "mem-active",
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-a",
                    "agent_id": "general-agent",
                    "session_id": "session-a",
                    "record_type": "session_summary",
                    "status": "active",
                    "expires_at": "2026-07-03T12:00:00Z",
                    "deleted_at": None,
                    "created_at": "2026-06-03T12:00:00Z",
                    "updated_at": "2026-06-03T12:00:00Z",
                }
            ]

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split()).lower()
            self.calls.append((normalized, params))
            return MemoryCursor()

    conn = MemoryConnection()

    rows = await _repo_owner_app_context_infrastructure_postgres.list_admin_memory_records(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        status="active",
        limit=25,
    )

    sql, params = conn.calls[0]
    selected = sql.split(" from memory_records", 1)[0]
    assert "content" not in selected
    assert "metadata_json" not in selected
    assert "tenant_id = %s" in sql
    assert "workspace_id = %s" in sql
    assert "user_id = %s" in sql
    assert "%s = 'all' or status = %s" in sql
    assert params == ("tenant-a", "workspace-a", "user-a", "user-a", "active", "active", 25)
    assert "content" not in rows[0]
    assert "metadata_json" not in rows[0]


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
        await _repo_owner_app_context_infrastructure_postgres.create_memory_record(
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
        await _repo_owner_app_context_infrastructure_postgres.create_memory_record(
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

    user = await _repo_owner_app_identity_infrastructure_postgres.get_user(conn, tenant_id="tenant-a", user_id="user-a")

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

    agent = await _repo_owner_app_agent_apps_infrastructure_catalog_postgres.get_agent(conn, tenant_id="tenant-a", agent_id="general-agent")

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

    rows = await _repo_owner_app_context_infrastructure_postgres.cleanup_expired_memory_records(
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
async def test_cleanup_expired_memory_records_across_scopes_rejects_non_positive_limit():
    class MemoryConnection:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("invalid limit must fail before SQL execution")

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryConflictError, match="memory_cleanup_limit_invalid"):
        await _repo_owner_app_context_infrastructure_postgres.cleanup_expired_memory_records_across_scopes(MemoryConnection(), limit=0)


@pytest.mark.asyncio
async def test_cleanup_expired_memory_records_across_scopes_prioritizes_one_row_per_scope():
    class EmptyCursor:
        async def fetchone(self):
            return None

    class ScopeCursor:
        async def fetchall(self):
            return [
                {"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
                {"tenant_id": "tenant-b", "workspace_id": "workspace-b"},
                {"tenant_id": "tenant-c", "workspace_id": "workspace-c"},
                {"tenant_id": "tenant-d", "workspace_id": "workspace-d"},
            ]

    class CleanupCursor:
        async def fetchall(self):
            return []

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if "from worker_maintenance_cursors" in normalized:
                return EmptyCursor()
            if "group by tenant_id, workspace_id" in normalized:
                return ScopeCursor()
            return CleanupCursor()

    conn = MemoryConnection()

    await _repo_owner_app_context_infrastructure_postgres.cleanup_expired_memory_records_across_scopes(conn, limit=5)

    cleanup_sql, cleanup_params = conn.calls[3]
    assert "row_number() over" in cleanup_sql
    assert "order by locked_rows.expires_at asc, locked_rows.created_at asc, locked_rows.id asc" in cleanup_sql
    assert "case when selected.scope_rank = 1 then 0 else 1 end" in cleanup_sql
    assert cleanup_params == (
        ["tenant-a", "tenant-b", "tenant-c", "tenant-d"],
        ["workspace-a", "workspace-b", "workspace-c", "workspace-d"],
        2,
        5,
    )


@pytest.mark.asyncio
async def test_cleanup_expired_memory_records_across_scopes_uses_bounded_scope_cursor():
    class CursorCursor:
        async def fetchone(self):
            return {"tenant_id": "tenant-a", "workspace_id": "workspace-a"}

    class ScopeCursor:
        async def fetchall(self):
            return [
                {"tenant_id": "tenant-b", "workspace_id": "workspace-b"},
                {"tenant_id": "tenant-c", "workspace_id": "workspace-c"},
            ]

    class UpdateCursor:
        async def fetchall(self):
            return [
                {
                    "id": "mem-b",
                    "tenant_id": "tenant-b",
                    "workspace_id": "workspace-b",
                    "user_id": "user-b",
                    "agent_id": "general-agent",
                    "session_id": "session-b",
                    "record_type": "session_summary",
                    "status": "deleted",
                    "expires_at": "2026-06-01T12:00:00Z",
                    "deleted_at": "2026-06-03T12:00:00Z",
                    "created_at": "2026-05-31T12:00:00Z",
                    "updated_at": "2026-06-03T12:00:00Z",
                },
                {
                    "id": "mem-c",
                    "tenant_id": "tenant-c",
                    "workspace_id": "workspace-c",
                    "user_id": "user-c",
                    "agent_id": "general-agent",
                    "session_id": "session-c",
                    "record_type": "session_summary",
                    "status": "deleted",
                    "expires_at": "2026-06-01T12:05:00Z",
                    "deleted_at": "2026-06-03T12:00:00Z",
                    "created_at": "2026-05-31T12:05:00Z",
                    "updated_at": "2026-06-03T12:00:00Z",
                },
            ]

    class MemoryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if "from worker_maintenance_cursors" in normalized:
                return CursorCursor()
            if "group by tenant_id, workspace_id" in normalized:
                return ScopeCursor()
            return UpdateCursor()

    conn = MemoryConnection()

    rows = await _repo_owner_app_context_infrastructure_postgres.cleanup_expired_memory_records_across_scopes(conn, limit=2)

    assert [row["tenant_id"] for row in rows] == ["tenant-b", "tenant-c"]
    cursor_sql, cursor_params = conn.calls[0]
    scope_sql, scope_params = conn.calls[1]
    cursor_update_sql, cursor_update_params = conn.calls[2]
    cleanup_sql, cleanup_params = conn.calls[3]
    assert "from worker_maintenance_cursors" in cursor_sql
    assert "for update" in cursor_sql
    assert cursor_params == ("memory_retention_cleanup",)
    assert "group by tenant_id, workspace_id" in scope_sql
    assert "(tenant_id, workspace_id) > (%s, %s)" in scope_sql
    assert scope_params == ("tenant-a", "tenant-a", "workspace-a", None, None, None, 2)
    assert "insert into worker_maintenance_cursors" in cursor_update_sql
    assert "on conflict (cursor_key) do update" in cursor_update_sql
    assert cursor_update_params == ("memory_retention_cleanup", "tenant-c", "workspace-c")
    assert "unnest(%s::text[], %s::text[])" in cleanup_sql
    assert "cross join lateral" in cleanup_sql
    assert "memory_records.tenant_id = scope.tenant_id" in cleanup_sql
    assert "memory_records.workspace_id = scope.workspace_id" in cleanup_sql
    assert "for update skip locked" in cleanup_sql
    assert "content" not in cleanup_sql
    assert "metadata_json" not in cleanup_sql
    assert cleanup_params == (["tenant-b", "tenant-c"], ["workspace-b", "workspace-c"], 1, 2)


@pytest.mark.asyncio
async def test_cleanup_expired_memory_records_across_scopes_soft_deletes_bounded_rows():
    class EmptyCursor:
        async def fetchone(self):
            return None

    class ScopeCursor:
        async def fetchall(self):
            return [{"tenant_id": "tenant-a", "workspace_id": "workspace-a"}]

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
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if "from worker_maintenance_cursors" in normalized:
                return EmptyCursor()
            if "group by tenant_id, workspace_id" in normalized:
                return ScopeCursor()
            return CleanupCursor()

    conn = MemoryConnection()

    rows = await _repo_owner_app_context_infrastructure_postgres.cleanup_expired_memory_records_across_scopes(conn, limit=25)

    sql, params = conn.calls[3]
    assert rows[0]["tenant_id"] == "tenant-a"
    assert rows[0]["workspace_id"] == "workspace-a"
    assert "update memory_records" in sql
    assert "status = 'deleted'" in sql
    assert "expires_at is not null" in sql
    assert "expires_at <= now()" in sql
    assert "deleted_at is null" in sql
    assert "tenant_id = %s" not in sql
    assert "workspace_id = %s" not in sql
    assert "cross join lateral" in sql
    assert "case when selected.scope_rank = 1 then 0 else 1 end" in sql
    assert "selected.expires_at asc, selected.created_at asc" in sql
    assert "for update skip locked" in sql
    assert "content" not in sql
    assert "metadata_json" not in sql
    assert params == (["tenant-a"], ["workspace-a"], 25, 25)


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

    rows = await _repo_owner_app_context_infrastructure_postgres.list_admin_memory_records(
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
