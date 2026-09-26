import json
import pytest
import app.context.file_continuity as _repo_owner_app_context_file_continuity
import app.context.infrastructure.postgres as _repo_owner_app_context_infrastructure_postgres
import app.context.infrastructure.snapshot_postgres as _repo_owner_app_context_infrastructure_snapshot_postgres
import app.context.infrastructure.sources_postgres as _repo_owner_app_context_infrastructure_sources_postgres
from app.files.api import get_owned_session_file, get_owned_unbound_file, list_owned_session_files
from app.context.infrastructure.postgres import list_scoped_context_memory_records
from app.context.infrastructure.snapshot_postgres import create_context_snapshot, get_context_snapshot_for_worker, get_latest_authorized_executor_context_snapshot, list_context_share_snapshots_for_target_session
from app.context.infrastructure.sources_postgres import get_scoped_context_artifact, get_scoped_context_file, list_scoped_context_messages
from app.conversations.infrastructure.session_queries_postgres import get_authorized_context_target_session
from app.platform.postgres.errors import RepositoryConflictError
from tests.support.repository_fixtures import RecordingConnection, SingleRowConnection, SingleRowCursor


class TwoSnapshotFileMembershipConnection:
    """Model S1 as persisted authority even though S2 is created later."""

    def __init__(self, *, selected_member: bool, later_member: bool):
        self.selected_snapshot_id = "ctx-s1"
        self.snapshots = [
            {"id": "ctx-s1", "included_file_ids": ["file-a"] if selected_member else []},
            {"id": "ctx-s2", "included_file_ids": ["file-a"] if later_member else []},
        ]
        self.calls: list[tuple[str, tuple]] = []

    async def execute(self, sql, params):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, params))
        is_projection = len(params) == 4
        exact_snapshot_join = (
            "authorized_snapshot.id = runs.context_snapshot_id"
            if is_projection
            else "context_snapshot.id = current_run.context_snapshot_id"
        )
        uses_selected_snapshot = exact_snapshot_join in normalized
        snapshot = self.snapshots[0] if uses_selected_snapshot else self.snapshots[-1]
        authorized = "file-a" in snapshot["included_file_ids"]
        row = (
            {
                "id": "file-a",
                "run_id": "run-a",
                "original_name": "source.txt",
                "content_type": "text/plain",
                "size_bytes": 10,
                "storage_key": "tenants/private/source.txt",
            }
            if authorized
            else None
        )
        return SingleRowCursor(row)


@pytest.mark.asyncio
async def test_create_context_snapshot_preserves_private_context_manifest_refs_without_storage_keys():
    conn = SingleRowConnection({"id": "ctx-row"})

    await create_context_snapshot(
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
        included_artifact_ids=[],
        included_memory_record_ids=[],
        redaction_summary_json={},
        payload_json={
            "context_manifest": {
                "schema_version": "ai-platform.context-manifest.v1",
                "recent_messages": [{"message_id": "msg-a", "requires_retrieval": True}],
                "files": [
                    {
                        "file_id": "file-a",
                        "storage_key": "tenants/tenant-a/private/source.docx",
                        "requires_retrieval": True,
                    }
                ],
                "raw_storage_key": "tenants/tenant-a/private/raw.docx",
            }
        },
    )

    persisted_payload = json.loads(conn.params[-2])
    assert persisted_payload["context_manifest"]["recent_messages"] == [
        {"message_id": "msg-a", "requires_retrieval": True}
    ]
    assert persisted_payload["context_manifest"]["files"] == [
        {"file_id": "file-a", "requires_retrieval": True}
    ]
    serialized = json.dumps(persisted_payload)
    assert "storage_key" not in serialized
    assert "tenants/tenant-a/private" not in serialized


@pytest.mark.asyncio
async def test_list_scoped_context_messages_filters_full_scope_and_limits_rows():
    conn = SingleRowConnection(
        {
            "id": "msg-a",
            "session_id": "session-a",
            "run_id": "run-a",
            "role": "user",
            "content": "hello",
            "metadata_json": {},
            "created_at": "now",
        }
    )

    rows = await list_scoped_context_messages(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        limit=25,
        offset=5,
    )

    assert rows[0]["id"] == "msg-a"
    assert "join sessions" in conn.sql
    assert "join runs source_runs" in conn.sql
    assert "join runs current_run" in conn.sql
    assert "context_snapshot.id = current_run.context_snapshot_id" in conn.sql
    assert "context_snapshot.included_message_ids ? messages.id" in conn.sql
    assert "source_runs.session_id = current_run.session_id" in conn.sql
    assert conn.params == ("run-a", "tenant-a", "workspace-a", "user-a", "session-a", "run-a", 25, 5)


@pytest.mark.asyncio
async def test_scoped_context_file_and_artifact_queries_bind_full_scope():
    file_conn = SingleRowConnection(
        {
            "id": "file-a",
            "original_name": "source.txt",
            "content_type": "text/plain",
            "size_bytes": 10,
            "storage_key": "tenants/private/source.txt",
            "sha256": "hash",
        }
    )
    artifact_conn = SingleRowConnection(
        {
            "id": "artifact-a",
            "artifact_type": "report_txt",
            "label": "report.txt",
            "content_type": "text/plain",
            "size_bytes": 10,
            "storage_key": "tenants/private/report.txt",
        }
    )

    file_row = await get_scoped_context_file(
        file_conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        file_id="file-a",
    )
    artifact_row = await get_scoped_context_artifact(
        artifact_conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        artifact_id="artifact-a",
    )

    assert file_row["id"] == "file-a"
    assert "join runs source_run" in file_conn.sql
    assert "join runs current_run" in file_conn.sql
    assert "context_snapshot.id = current_run.context_snapshot_id" in file_conn.sql
    assert "join lateral" not in file_conn.sql
    assert "sessions.status = 'active'" in file_conn.sql
    assert "context_snapshot.included_file_ids ? files.id" in file_conn.sql
    assert "source_run.session_id = current_run.session_id" in file_conn.sql
    assert file_conn.params == ("run-a", "tenant-a", "workspace-a", "user-a", "session-a", "run-a", "file-a")
    assert artifact_row["id"] == "artifact-a"
    assert "join runs source_run" in artifact_conn.sql
    assert "join runs current_run" in artifact_conn.sql
    assert "context_snapshot.included_artifact_ids ? artifacts.id" in artifact_conn.sql
    assert "source_run.session_id = current_run.session_id" in artifact_conn.sql
    assert "artifacts.expires_at is null or artifacts.expires_at > now()" in artifact_conn.sql
    assert artifact_conn.params == ("run-a", "tenant-a", "workspace-a", "user-a", "session-a", "run-a", "artifact-a")


@pytest.mark.parametrize(
    ("selected_member", "later_member", "expected_authorized"),
    [
        (True, False, True),
        (False, True, False),
    ],
)
@pytest.mark.asyncio
async def test_input_file_list_and_read_use_persisted_s1_after_later_s2(
    selected_member,
    later_member,
    expected_authorized,
):
    conn = TwoSnapshotFileMembershipConnection(
        selected_member=selected_member,
        later_member=later_member,
    )

    file_row = await get_scoped_context_file(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        file_id="file-a",
    )
    projected_rows = await _repo_owner_app_context_file_continuity.list_authorized_session_input_files(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
    )

    assert conn.selected_snapshot_id == "ctx-s1"
    assert conn.snapshots[-1]["id"] == "ctx-s2"
    assert (file_row is not None) is expected_authorized
    assert bool(projected_rows) is expected_authorized
    read_sql, projection_sql = (call[0] for call in conn.calls)
    assert "context_snapshot.id = current_run.context_snapshot_id" in read_sql
    assert "authorized_snapshot.id = runs.context_snapshot_id" in projection_sql
    assert "join lateral" not in read_sql
    assert "join lateral" not in projection_sql


@pytest.mark.asyncio
async def test_owned_session_file_queries_include_unbound_imports_and_bind_full_scope():
    row = {
        "id": "file-profile",
        "run_id": None,
        "original_name": "report.pdf",
        "content_type": "application/pdf",
        "size_bytes": 42,
        "created_at": "now",
    }
    list_conn = SingleRowConnection(row)
    get_conn = SingleRowConnection(row)

    rows = await list_owned_session_files(
        list_conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
    )
    selected = await get_owned_session_file(
        get_conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        file_id="file-profile",
    )

    assert rows == [row]
    assert selected == row
    for statement in (list_conn.sql, get_conn.sql):
        assert "join sessions" in statement
        assert "sessions.status = 'active'" in statement
        assert "files.lifecycle_state = 'active'" in statement
        assert "sessions.workspace_id = files.workspace_id" in statement
        assert "sessions.user_id = files.user_id" in statement
        assert "join runs" not in statement
    assert list_conn.params == (
        "tenant-a",
        "workspace-a",
        "user-a",
        "session-a",
    )
    assert get_conn.params == (
        "tenant-a",
        "workspace-a",
        "user-a",
        "session-a",
        "file-profile",
    )


@pytest.mark.asyncio
async def test_owned_unbound_file_query_binds_full_owner_scope():
    row = {
        "id": "file-profile",
        "session_id": None,
        "run_id": None,
        "lifecycle_state": "active",
    }
    conn = SingleRowConnection(row)

    selected = await get_owned_unbound_file(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        file_id="file-profile",
    )

    assert selected == row
    assert "tenant_id = %s" in conn.sql
    assert "workspace_id = %s" in conn.sql
    assert "user_id = %s" in conn.sql
    assert "session_id is null" in conn.sql
    assert "run_id is null" in conn.sql
    assert "lifecycle_state = 'active'" in conn.sql
    assert conn.params == (
        "tenant-a",
        "workspace-a",
        "user-a",
        "file-profile",
    )


@pytest.mark.asyncio
async def test_session_context_candidates_bind_owner_scope_and_latest_successful_artifact_run():
    conn = RecordingConnection()

    await _repo_owner_app_context_infrastructure_sources_postgres.list_session_context_messages(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-current",
        limit=8,
    )
    messages_sql, messages_params = conn.calls[-1]
    assert "sessions.status = 'active'" in messages_sql
    assert "runs.workspace_id = sessions.workspace_id" in messages_sql
    assert "runs.user_id = sessions.user_id" in messages_sql
    assert "runs.session_generation <" in messages_sql
    assert "order by runs.session_generation desc" in messages_sql
    assert "order by session_generation asc" in messages_sql
    assert messages_params == (
        "tenant-a", "workspace-a", "user-a", "session-a", "run-current",
        "tenant-a", "session-a", "workspace-a", "user-a", 8,
    )

    await _repo_owner_app_context_infrastructure_sources_postgres.count_session_context_messages(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-current",
    )
    count_sql, count_params = conn.calls[-1]
    assert "count(*) as context_message_count" in count_sql
    assert "messages.content" not in count_sql
    assert "order by" not in count_sql
    assert "limit" not in count_sql
    assert count_params == (
        "tenant-a", "workspace-a", "user-a", "session-a", "run-current",
        "tenant-a", "session-a", "workspace-a", "user-a",
    )

    await _repo_owner_app_context_infrastructure_sources_postgres.list_session_context_files(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-current",
        limit=8,
    )
    files_sql, files_params = conn.calls[-1]
    assert "sessions.status = 'active'" in files_sql
    assert "runs.session_id = files.session_id" in files_sql
    assert "authorized_snapshot.included_file_ids ? files.id" in files_sql
    assert "runs.input_json->>'context_snapshot_id' = runs.context_snapshot_id" in files_sql
    assert "runs.input_json->'context_snapshot'->>'context_snapshot_id' = runs.context_snapshot_id" in files_sql
    assert "runs.session_generation <" in files_sql
    assert "order by runs.session_generation desc" in files_sql
    assert "order by session_generation asc" in files_sql
    assert files_params == (
        "tenant-a", "workspace-a", "user-a", "session-a", "run-current",
        "tenant-a", "workspace-a", "user-a", "session-a", 8,
    )

    await _repo_owner_app_context_file_continuity.list_authorized_session_input_files(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
    )
    projection_sql, projection_params = conn.calls[-1]
    assert "join sessions" in projection_sql
    assert "join runs" in projection_sql
    assert "join lateral" not in projection_sql
    assert "authorized_snapshot.id = runs.context_snapshot_id" in projection_sql
    assert "authorized_snapshot.included_file_ids ? files.id" in projection_sql
    assert "sessions.status = 'active'" in projection_sql
    assert "sessions.workspace_id = files.workspace_id" in projection_sql
    assert "sessions.user_id = files.user_id" in projection_sql
    assert "runs.session_id = files.session_id" in projection_sql
    assert projection_params == ("tenant-a", "workspace-a", "user-a", "session-a")

    await _repo_owner_app_context_infrastructure_sources_postgres.list_session_context_artifacts(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        exclude_run_id="run-current",
        limit=8,
    )
    artifacts_sql, artifacts_params = conn.calls[-1]
    assert "with latest_source_run" in artifacts_sql
    assert "runs.status = 'succeeded'" in artifacts_sql
    assert "runs.id <> %s" in artifacts_sql
    assert "artifacts.expires_at is null or artifacts.expires_at > now()" in artifacts_sql
    assert artifacts_params == (
        "tenant-a",
        "workspace-a",
        "user-a",
        "session-a",
        "run-current",
        "tenant-a",
        "workspace-a",
        "user-a",
        "session-a",
        "run-current",
        "tenant-a",
        8,
    )


@pytest.mark.asyncio
async def test_list_scoped_context_memory_records_excludes_deleted_and_binds_session_scope():
    conn = SingleRowConnection(
        {
            "id": "mem-a",
            "record_type": "preference",
            "content": "prefer concise answers",
            "metadata_json": {},
            "status": "active",
            "deleted_at": None,
            "created_at": "now",
        }
    )

    rows = await list_scoped_context_memory_records(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        session_id="session-a",
        query="prefer",
        limit=10,
    )

    assert rows[0]["id"] == "mem-a"
    assert "status = 'active'" in conn.sql
    assert "deleted_at is null" in conn.sql
    assert "session_id = %s" in conn.sql
    assert conn.params == (
        "tenant-a",
        "workspace-a",
        "user-a",
        "general-agent",
        "session-a",
        "%prefer%",
        "%prefer%",
        10,
    )


@pytest.mark.asyncio
async def test_context_snapshot_binding_rejects_a_mismatched_public_reference_before_sql():
    class NoQueryConnection:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("mismatched snapshot reference must not execute SQL")

    with pytest.raises(RepositoryConflictError, match="context_snapshot_binding_invalid"):
        await _repo_owner_app_context_infrastructure_snapshot_postgres.update_run_context_snapshot_ref(
            NoQueryConnection(),
            tenant_id="tenant-a",
            run_id="run-a",
            context_snapshot_id="ctx-a",
            context_snapshot={"context_snapshot_id": "ctx-other"},
        )


@pytest.mark.asyncio
async def test_context_snapshot_binding_requires_same_scope_executor_and_allows_only_exact_repeat():
    conn = SingleRowConnection({"context_snapshot_id": "ctx-a"})

    await _repo_owner_app_context_infrastructure_snapshot_postgres.update_run_context_snapshot_ref(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        context_snapshot_id="ctx-a",
        context_snapshot={"context_snapshot_id": "ctx-a", "source": "chat_stream"},
    )

    assert "context_kind = 'executor'" in conn.sql
    assert "context_snapshot_id is null" in conn.sql
    assert "context_snapshot_id = %s" in conn.sql
    assert "input_json->>'context_snapshot_id' = context_snapshot_id" in conn.sql
    assert conn.params[-2:] == ("ctx-a", "ctx-a")


@pytest.mark.asyncio
async def test_context_snapshot_binding_fails_closed_when_the_database_rejects_scope_or_rebinding():
    conn = SingleRowConnection(None)

    with pytest.raises(RepositoryConflictError, match="context_snapshot_binding_invalid"):
        await _repo_owner_app_context_infrastructure_snapshot_postgres.update_run_context_snapshot_ref(
            conn,
            tenant_id="tenant-a",
            run_id="run-a",
            context_snapshot_id="ctx-other",
            context_snapshot={"context_snapshot_id": "ctx-other", "source": "chat_stream"},
        )


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
    assert "eligible_members" in sql
    assert "jsonb_array_elements_text" in sql
    assert "eligible_message_count = jsonb_array_length(message_ids)" in sql
    assert "eligible_file_count = jsonb_array_length(file_ids)" in sql
    assert "eligible_artifact_count = jsonb_array_length(artifact_ids)" in sql
    assert "locked_artifacts as materialized" in sql
    assert "for update of artifacts" in sql
    assert "eligible_memory_record_count = jsonb_array_length(memory_record_ids)" in sql
    assert "runs.workspace_id = %s" not in sql
    assert "runs.session_id = %s" not in sql
    assert "workspace-a" not in params
    assert "session-a" not in params
    assert "message_run.id = scoped_run.run_id" not in sql
    assert "file_run.id = scoped_run.run_id" not in sql
    assert "artifact_run.id = scoped_run.run_id" not in sql
    assert "join runs message_run" in sql
    assert "join runs file_run" in sql
    assert "messages.run_id is null" not in sql
    assert "files.run_id is null" not in sql
    assert "artifacts.expires_at > statement_timestamp()" in sql
    assert "memory_records.expires_at > statement_timestamp()" in sql


@pytest.mark.asyncio
async def test_create_context_snapshot_returns_scope_derived_by_the_atomic_statement():
    conn = SingleRowConnection(
        {
            "id": "ctx-from-statement",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-canonical",
            "user_id": "user-a",
            "session_id": "session-canonical",
            "run_id": "run-a",
            "trace_id": "trace-canonical",
        }
    )

    snapshot = await create_context_snapshot(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-untrusted",
        user_id="user-a",
        session_id="session-untrusted",
        run_id="run-a",
        trace_id="trace-untrusted",
        context_kind="executor",
        included_message_ids=[],
        included_file_ids=[],
        included_artifact_ids=[],
        included_memory_record_ids=[],
        redaction_summary_json={},
        payload_json={},
    )

    assert snapshot["id"].startswith("ctx_")
    assert snapshot["workspace_id"] == "workspace-canonical"
    assert snapshot["session_id"] == "session-canonical"
    assert snapshot["trace_id"] == "trace-canonical"
    assert "workspace-untrusted" not in conn.params
    assert "session-untrusted" not in conn.params
    assert "trace-untrusted" not in conn.params


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
    inserted_summary = json.loads(params[-3])
    inserted_payload = json.loads(params[-2])
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
async def test_create_context_snapshot_rejects_unverified_members_without_insert_returning():
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

    with pytest.raises(RepositoryConflictError, match="context_snapshot_material_invalid"):
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
    assert "runs.user_id = %s" in sql
    assert "runs.id = %s" in sql
    assert "sessions.id = runs.session_id" in sql
    assert "messages.session_id = scoped_run.session_id" in sql
    assert "file_run.session_id = scoped_run.session_id" in sql
    assert "artifact_run.session_id = scoped_run.session_id" in sql
    assert "memory_records.session_id = scoped_run.session_id" in sql
    assert "memory_records.status = 'active'" in sql
    assert "memory_records.deleted_at is null" in sql
    assert "artifacts.expires_at is null or artifacts.expires_at > statement_timestamp()" in sql
    assert "returning id" in sql
    assert "run-cross-scope" in params


@pytest.mark.asyncio
async def test_create_context_snapshot_rejects_duplicate_or_oversized_members_before_sql():
    class NoQueryConnection:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("invalid context snapshot members must not execute SQL")

    common = {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "trace_id": "trace-a",
        "context_kind": "executor",
        "included_file_ids": [],
        "included_artifact_ids": [],
        "included_memory_record_ids": [],
        "redaction_summary_json": {},
        "payload_json": {},
    }

    with pytest.raises(RepositoryConflictError, match="context_snapshot_material_invalid"):
        await create_context_snapshot(
            NoQueryConnection(),
            included_message_ids=["msg-a", "msg-a"],
            **common,
        )
    with pytest.raises(RepositoryConflictError, match="context_snapshot_material_invalid"):
        await create_context_snapshot(
            NoQueryConnection(),
            included_message_ids=[f"msg-{index}" for index in range(129)],
            **common,
        )


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
    assert "join runs on runs.context_snapshot_id = run_context_snapshots.id" in conn.sql
    assert "runs.input_json->>'context_snapshot_id' = runs.context_snapshot_id" in conn.sql
    assert "runs.input_json->'context_snapshot'->>'context_snapshot_id' = runs.context_snapshot_id" in conn.sql
    assert conn.params == ("tenant-a", "workspace-a", "user-a", "session-a", "run-a", "ctx-a")


@pytest.mark.asyncio
async def test_get_authorized_context_target_session_scopes_by_tenant_workspace_user_and_session():
    class SessionCursor:
        async def fetchone(self):
            return {
                "id": "session-target",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "status": "active",
            }

    class SessionConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return SessionCursor()

    conn = SessionConnection()

    row = await get_authorized_context_target_session(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-target",
    )

    assert row["id"] == "session-target"
    assert "from sessions" in conn.sql
    assert "tenant_id = %s" in conn.sql
    assert "workspace_id = %s" in conn.sql
    assert "user_id = %s" in conn.sql
    assert "id = %s" in conn.sql
    assert "status = 'active'" in conn.sql
    assert conn.params == ("tenant-a", "workspace-a", "user-a", "session-target")


@pytest.mark.asyncio
async def test_list_context_share_snapshots_for_target_session_filters_public_payload_target_binding():
    class ShareCursor:
        async def fetchall(self):
            return [
                {
                    "id": "ctx-share",
                    "payload_json": {
                        "share_fork_context": {
                            "target_session_id": "session-target",
                            "redaction_state": "public_redacted",
                        }
                    },
                }
            ]

    class ShareConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return ShareCursor()

    conn = ShareConnection()

    rows = await list_context_share_snapshots_for_target_session(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        target_session_id="session-target",
    )

    assert rows[0]["id"] == "ctx-share"
    assert "from run_context_snapshots" in conn.sql
    assert "context_kind = 'share_fork'" in conn.sql
    assert "payload_json->'share_fork_context'->>'target_session_id' = %s" in conn.sql
    assert "tenant_id = %s" in conn.sql
    assert "workspace_id = %s" in conn.sql
    assert "user_id = %s" in conn.sql
    assert conn.params == ("tenant-a", "workspace-a", "user-a", "session-target")


@pytest.mark.asyncio
async def test_executor_context_compatibility_lookup_uses_physical_run_binding():
    class ExecutorSnapshotCursor:
        async def fetchone(self):
            return {"id": "ctx-executor", "context_kind": "executor"}

    class ExecutorSnapshotConnection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split())
            self.params = params
            return ExecutorSnapshotCursor()

    conn = ExecutorSnapshotConnection()

    row = await get_latest_authorized_executor_context_snapshot(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        run_id="run-source",
    )

    assert row["id"] == "ctx-executor"
    assert "from runs" in conn.sql
    assert "context_snapshot.id = runs.context_snapshot_id" in conn.sql
    assert "runs.tenant_id = %s" in conn.sql
    assert "runs.user_id = %s" in conn.sql
    assert "runs.id = %s" in conn.sql
    assert "context_snapshot.context_kind = 'executor'" in conn.sql
    assert "order by" not in conn.sql
    assert conn.params == ("tenant-a", "user-a", "run-source")


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

    policy = await _repo_owner_app_context_infrastructure_postgres.get_effective_memory_policy(
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
        "redaction_mode": "standard",
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
                "redaction_mode": "standard",
                "reason": "legacy dirty row",
                "updated_by": "legacy-admin",
                "updated_at": "2026-06-02T12:00:00Z",
            }

    class PolicyConnection:
        async def execute(self, sql, params):
            return LegacyPolicyCursor()

    policy = await _repo_owner_app_context_infrastructure_postgres.get_effective_memory_policy(
        PolicyConnection(),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
    )

    assert policy["source"] == "stored"
    assert policy["memory_enabled"] is True
    assert policy["long_term_memory_enabled"] is False
    assert policy["redaction_mode"] == "standard"


@pytest.mark.asyncio
async def test_get_effective_memory_policy_treats_invalid_stored_redaction_mode_as_strict():
    class DirtyPolicyCursor:
        async def fetchone(self):
            return {
                "id": "mempol-dirty",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "memory_enabled": True,
                "long_term_memory_enabled": False,
                "retention_days": 90,
                "redaction_mode": "off",
                "reason": "manual dirty row",
                "updated_by": "legacy-admin",
                "updated_at": "2026-06-02T12:00:00Z",
            }

    class PolicyConnection:
        async def execute(self, sql, params):
            return DirtyPolicyCursor()

    policy = await _repo_owner_app_context_infrastructure_postgres.get_effective_memory_policy(
        PolicyConnection(),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
    )

    assert policy["redaction_mode"] == "strict"


@pytest.mark.asyncio
async def test_get_effective_memory_policy_treats_blank_stored_redaction_mode_as_strict():
    class DirtyPolicyCursor:
        async def fetchone(self):
            return {
                "id": "mempol-dirty",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "memory_enabled": True,
                "long_term_memory_enabled": False,
                "retention_days": 90,
                "redaction_mode": "",
                "reason": "manual dirty row",
                "updated_by": "legacy-admin",
                "updated_at": "2026-06-02T12:00:00Z",
            }

    class PolicyConnection:
        async def execute(self, sql, params):
            return DirtyPolicyCursor()

    policy = await _repo_owner_app_context_infrastructure_postgres.get_effective_memory_policy(
        PolicyConnection(),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
    )

    assert policy["redaction_mode"] == "strict"


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
                "redaction_mode": "strict",
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

    policy = await _repo_owner_app_context_infrastructure_postgres.set_memory_policy(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        memory_enabled=False,
        long_term_memory_enabled=False,
        retention_days=30,
        redaction_mode="strict",
        reason="user opt-out [redacted-secret]",
        updated_by="admin-a",
    )

    sql, params = conn.calls[0]
    assert "insert into memory_policies" in sql
    assert "on conflict (id) do update" in sql
    assert "token=hidden" not in str(params)
    assert params[1:11] == (
        "tenant-a",
        "workspace-a",
        "user-a",
        "general-agent",
        False,
        False,
        30,
        "strict",
        "user opt-out [redacted-secret]",
        "admin-a",
    )
    assert policy["memory_enabled"] is False
    assert policy["long_term_memory_enabled"] is False
    assert policy["redaction_mode"] == "strict"
    assert policy["source"] == "stored"


@pytest.mark.asyncio
async def test_set_memory_policy_rejects_long_term_enable_at_repository_boundary():
    class FailConnection:
        async def execute(self, sql, params):
            raise AssertionError("repository must reject long-term memory before SQL")

    with pytest.raises(RepositoryConflictError, match="long_term_memory_not_available"):
        await _repo_owner_app_context_infrastructure_postgres.set_memory_policy(
            FailConnection(),
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id="general-agent",
            memory_enabled=True,
            long_term_memory_enabled=True,
            retention_days=90,
            redaction_mode="standard",
            reason="enable",
            updated_by="admin-a",
        )


@pytest.mark.asyncio
async def test_set_memory_policy_rejects_invalid_redaction_mode_before_sql():
    class FailConnection:
        async def execute(self, sql, params):
            raise AssertionError("repository must reject invalid redaction mode before SQL")

    with pytest.raises(RepositoryConflictError, match="memory_redaction_mode_invalid"):
        await _repo_owner_app_context_infrastructure_postgres.set_memory_policy(
            FailConnection(),
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id="general-agent",
            memory_enabled=True,
            long_term_memory_enabled=False,
            retention_days=90,
            redaction_mode="off",
            reason="invalid",
            updated_by="admin-a",
        )


@pytest.mark.asyncio
async def test_set_memory_policy_rejects_blank_redaction_mode_before_sql():
    class FailConnection:
        async def execute(self, sql, params):
            raise AssertionError("repository must reject blank redaction mode before SQL")

    with pytest.raises(RepositoryConflictError, match="memory_redaction_mode_invalid"):
        await _repo_owner_app_context_infrastructure_postgres.set_memory_policy(
            FailConnection(),
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            agent_id="general-agent",
            memory_enabled=True,
            long_term_memory_enabled=False,
            retention_days=90,
            redaction_mode="",
            reason="invalid",
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
                    "redaction_mode": "strict",
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

    rows = await _repo_owner_app_context_infrastructure_postgres.list_admin_memory_policies(
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
            "redaction_mode": "strict",
            "source": "stored",
            "reason": "user opt-out",
            "updated_by": "user-a",
            "updated_at": "2026-06-05T00:00:00Z",
        }
    ]
