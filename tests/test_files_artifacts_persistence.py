import pytest
import app.files.infrastructure.run_bindings_postgres as _repo_owner_app_files_infrastructure_run_bindings_postgres
import app.persistence.artifacts as _repo_owner_app_persistence_artifacts
import app.platform.postgres.errors as _repo_owner_app_platform_postgres_errors
from app.persistence import artifacts as artifact_persistence
from app.artifacts.infrastructure.records_postgres import create_artifact, list_run_artifacts
from tests.support.repository_fixtures import RecordingConnection, SingleRowConnection


class _RowsCursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows


class _RevealedArtifactTableConnection:
    def __init__(self, *, artifact, run, session):
        self.artifact = artifact
        self.run = run
        self.session = session
        self.calls = []

    async def execute(self, sql, params):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, params))
        tenant_id, user_id = params[:2]
        artifact = self.artifact
        run = self.run
        visible = (
            artifact["tenant_id"] == tenant_id
            and run["id"] == artifact["run_id"]
            and run["tenant_id"] == artifact["tenant_id"]
            and run["user_id"] == user_id
        )
        session = self.session
        session_matches = session["id"] == run["session_id"]
        if "sessions.tenant_id = runs.tenant_id" in normalized:
            session_matches = session_matches and session["tenant_id"] == run["tenant_id"]
        if "sessions.workspace_id = runs.workspace_id" in normalized:
            session_matches = session_matches and session["workspace_id"] == run["workspace_id"]
        if "sessions.user_id = runs.user_id" in normalized:
            session_matches = session_matches and session["user_id"] == run["user_id"]
        if "sessions.agent_id = runs.agent_id" in normalized:
            session_matches = session_matches and session["agent_id"] == run["agent_id"]
        if "sessions.status = 'active'" in normalized:
            session_matches = session_matches and session["status"] == "active"
        if "left join sessions" not in normalized:
            visible = visible and session_matches
        elif "sessions.status = 'active'" in normalized:
            visible = visible and session_matches
        if not visible:
            return _RowsCursor([])
        if "group by runs.session_id" in normalized:
            return _RowsCursor(
                [
                    {
                        "session_id": run["session_id"],
                        "session_name": session["title"],
                        "file_count": 1,
                        "updated_at": artifact["created_at"],
                    }
                ]
            )
        return _RowsCursor(
            [
                {
                    **artifact,
                    "run_id": run["id"],
                    "session_id": run["session_id"],
                    "workspace_id": run["workspace_id"],
                    "user_id": run["user_id"],
                    "session_name": session["title"],
                }
            ]
        )


@pytest.mark.asyncio
async def test_authorized_artifact_requires_an_active_exact_scope_owning_session():
    artifact = {
        "id": "artifact-a",
        "run_id": "run-a",
        "storage_key": "tenants/tenant-a/runs/run-a/artifact-a.txt",
    }
    conn = SingleRowConnection(artifact)

    row = await _repo_owner_app_persistence_artifacts.get_authorized_artifact(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        artifact_id="artifact-a",
    )

    assert row == artifact
    assert "join sessions on sessions.id = runs.session_id" in conn.sql
    assert "sessions.tenant_id = runs.tenant_id" in conn.sql
    assert "sessions.workspace_id = runs.workspace_id" in conn.sql
    assert "sessions.user_id = runs.user_id" in conn.sql
    assert "sessions.agent_id = runs.agent_id" in conn.sql
    assert "runs.user_id = %s" in conn.sql
    assert "sessions.status = 'active'" in conn.sql
    assert conn.params == ("tenant-a", "artifact-a", "user-a")


@pytest.mark.asyncio
async def test_revealed_session_artifacts_are_acl_scoped_and_not_globally_capped():
    expected_rows = [{"id": f"artifact-{index}"} for index in range(501)]

    class Cursor:
        async def fetchall(self):
            return expected_rows

    class Connection:
        def __init__(self):
            self.sql = ""
            self.params = None

        async def execute(self, sql, params):
            self.sql = " ".join(sql.split()).lower()
            self.params = params
            return Cursor()

    conn = Connection()

    rows = await artifact_persistence.list_revealed_session_artifacts(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
    )

    assert rows == expected_rows
    assert len(rows) == 501
    assert "count(*) over()" not in conn.sql
    assert "join sessions on sessions.id = runs.session_id" in conn.sql
    assert "sessions.tenant_id = runs.tenant_id" in conn.sql
    assert "sessions.workspace_id = runs.workspace_id" in conn.sql
    assert "sessions.user_id = runs.user_id" in conn.sql
    assert "sessions.agent_id = runs.agent_id" in conn.sql
    assert "sessions.status = 'active'" in conn.sql
    assert "artifacts.lifecycle_state = 'active'" in conn.sql
    assert "artifacts.expires_at is null or artifacts.expires_at > now()" in conn.sql
    assert "order by artifacts.created_at desc, artifacts.id desc" in conn.sql
    assert " limit " not in f" {conn.sql} "
    assert conn.params == ("tenant-a", "user-a", "session-a")


@pytest.mark.asyncio
async def test_revealed_artifact_rows_disappear_after_exact_owning_session_is_deleted():
    artifact = {
        "id": "artifact-a",
        "tenant_id": "tenant-a",
        "run_id": "run-a",
        "storage_key": "tenants/tenant-a/runs/run-a/artifact-a.txt",
        "label": "Artifact A",
        "content_type": "text/plain",
        "size_bytes": 10,
        "artifact_type": "document",
        "created_at": "2026-07-18T00:00:00Z",
        "trace_id": "trace-a",
    }
    run = {
        "id": "run-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "agent_id": "general-agent",
    }
    session = {
        "id": "session-a",
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "agent_id": "general-agent",
        "title": "Session A",
        "status": "active",
    }
    conn = _RevealedArtifactTableConnection(artifact=artifact, run=run, session=session)

    assert [row["id"] for row in await _repo_owner_app_persistence_artifacts.list_revealed_artifacts(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
    )] == ["artifact-a"]
    assert [row["session_id"] for row in await _repo_owner_app_persistence_artifacts.list_revealed_artifact_sessions(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
    )] == ["session-a"]
    for sql, params in conn.calls:
        assert "join sessions on sessions.id = runs.session_id" in sql
        assert "left join sessions" not in sql
        assert "sessions.tenant_id = runs.tenant_id" in sql
        assert "sessions.workspace_id = runs.workspace_id" in sql
        assert "sessions.user_id = runs.user_id" in sql
        assert "sessions.agent_id = runs.agent_id" in sql
        assert "sessions.status = 'active'" in sql
        assert params == ("tenant-a", "user-a")

    session["status"] = "deleted"
    assert await _repo_owner_app_persistence_artifacts.list_revealed_artifacts(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
    ) == []
    assert await _repo_owner_app_persistence_artifacts.list_revealed_artifact_sessions(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
    ) == []

    session["status"] = "active"
    session["workspace_id"] = "other-workspace"
    assert await _repo_owner_app_persistence_artifacts.list_revealed_artifacts(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
    ) == []


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
async def test_authorize_files_for_run_locks_and_validates_without_writing():
    class FileCursor:
        async def fetchone(self):
            return {
                "id": "file-a",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": None,
                "run_id": None,
                "original_name": "source.docx",
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "size_bytes": 1024,
                "sha256": "a" * 64,
            }

    class FileConnection(RecordingConnection):
        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return FileCursor()

    conn = FileConnection()
    await _repo_owner_app_files_infrastructure_run_bindings_postgres.authorize_files_for_run(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        file_ids=["file-a"],
    )

    assert len(conn.calls) == 1
    sql, params = conn.calls[0]
    assert "select id, tenant_id, workspace_id, user_id, session_id, run_id," in sql
    assert "lifecycle_state = 'active'" in sql
    assert "for update" in sql
    assert "update files" not in sql
    assert params == ("file-a",)


@pytest.mark.asyncio
async def test_authorize_files_for_run_rejects_skill_file_with_mismatched_mime():
    class FileCursor:
        async def fetchone(self):
            return {
                "id": "file-a",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": None,
                "run_id": None,
                "original_name": "source.docx",
                "content_type": "application/pdf",
                "size_bytes": 1024,
                "sha256": "a" * 64,
            }

    class FileConnection(RecordingConnection):
        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return FileCursor()

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryConflictError, match="file_required_for_skill"):
        await _repo_owner_app_files_infrastructure_run_bindings_postgres.authorize_files_for_run(
            FileConnection(),
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            file_ids=["file-a"],
            input_modes=["docx"],
        )


@pytest.mark.asyncio
async def test_authorize_files_for_run_does_not_apply_profile_format_whitelists():
    class FileCursor:
        async def fetchone(self):
            return {
                "id": "file-a",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": None,
                "run_id": None,
                "original_name": "report.pdf",
                "content_type": "application/pdf",
                "size_bytes": 1024,
                "sha256": "a" * 64,
            }

    class FileConnection(RecordingConnection):
        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return FileCursor()

    rows = await _repo_owner_app_files_infrastructure_run_bindings_postgres.authorize_files_for_run(
        FileConnection(),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        file_ids=["file-a"],
    )
    assert [row["id"] for row in rows] == ["file-a"]


@pytest.mark.asyncio
async def test_authorize_files_for_run_accepts_mixed_authorized_file_formats():
    rows = {
        "file-requested": {
            "id": "file-requested",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": None,
            "run_id": None,
            "original_name": "report.pdf",
            "content_type": "application/pdf",
            "size_bytes": 1024,
            "sha256": "a" * 64,
        },
        "file-reusable": {
            "id": "file-reusable",
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "session_id": "session-a",
            "run_id": "run-prior",
            "original_name": "diagram.png",
            "content_type": "image/png",
            "size_bytes": 2048,
            "sha256": "b" * 64,
        },
    }

    class FileCursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class FileConnection(RecordingConnection):
        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return FileCursor(rows.get(params[0]))

    conn = FileConnection()
    authorized = await _repo_owner_app_files_infrastructure_run_bindings_postgres.authorize_files_for_run(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-current",
        file_ids=["file-requested", "file-reusable"],
        reusable_file_ids=["file-reusable"],
    )
    assert [row["id"] for row in authorized] == ["file-requested", "file-reusable"]
    reusable_sql = conn.calls[1][0]
    assert "join run_context_snapshots authorized_snapshot" in reusable_sql
    assert "authorized_snapshot.included_file_ids ? files.id" in reusable_sql
    assert "for update of files" in reusable_sql


@pytest.mark.asyncio
async def test_authorize_files_for_run_rejects_reusable_id_outside_requested_set():
    class ForbiddenConnection:
        async def execute(self, *_args, **_kwargs):
            raise AssertionError("invalid reusable file scope must fail before SQL")

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryConflictError, match="file_scope_mismatch"):
        await _repo_owner_app_files_infrastructure_run_bindings_postgres.authorize_files_for_run(
            ForbiddenConnection(),
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-current",
            file_ids=["file-requested"],
            reusable_file_ids=["file-prior"],
        )


@pytest.mark.asyncio
async def test_authorize_files_for_run_rejects_reusable_file_from_other_session():
    class FileCursor:
        async def fetchone(self):
            return {
                "id": "file-prior",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-other",
                "run_id": "run-prior",
                "original_name": "prior.pdf",
                "content_type": "application/pdf",
                "size_bytes": 1024,
                "sha256": "a" * 64,
            }

    class FileConnection:
        async def execute(self, *_args, **_kwargs):
            return FileCursor()

    with pytest.raises(_repo_owner_app_platform_postgres_errors.RepositoryConflictError, match="file_session_mismatch"):
        await _repo_owner_app_files_infrastructure_run_bindings_postgres.authorize_files_for_run(
            FileConnection(),
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-current",
            file_ids=["file-prior"],
            reusable_file_ids=["file-prior"],
        )


@pytest.mark.asyncio
async def test_list_run_artifacts_returns_manifest_contract_columns():
    conn = RecordingConnection()

    await list_run_artifacts(conn, tenant_id="tenant-a", run_id="run-a")

    sql, _ = conn.calls[0]
    assert "trace_id" in sql
    assert "manifest_version" in sql
