import subprocess

import pytest

from app.context_retrieval import (
    ContextRetrieval,
    ContextRetrievalDenied,
    InMemoryContextRetrievalRepository,
    RepositoryContextRetrievalRepository,
)


def _symlink_or_skip(target, link):
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as exc:
        if not target.is_dir():
            pytest.skip(f"symlink creation not available: {exc}")
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip(f"symlink/junction creation not available: {exc}; {result.stderr.strip()}")


def _retrieval() -> ContextRetrieval:
    repo = InMemoryContextRetrievalRepository(
        messages=[
            {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "message_id": "msg-1",
                "role": "user",
                "content": "alpha beta gamma delta epsilon",
            },
            {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "message_id": "msg-2",
                "role": "assistant",
                "content": "zeta eta theta",
            },
            {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-b",
                "session_id": "session-b",
                "run_id": "run-b",
                "message_id": "msg-cross",
                "role": "user",
                "content": "cross user",
            },
        ],
        files=[
            {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "file_id": "file-a",
                "original_name": "source.txt",
                "content_type": "text/plain",
                "content": "file content is bounded by bytes",
                "storage_key": "tenants/tenant-a/private/source.txt",
            }
        ],
        artifacts=[
            {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "artifact_id": "artifact-a",
                "artifact_type": "report_txt",
                "label": "report.txt",
                "content": "artifact content",
                "storage_key": "tenants/tenant-a/private/report.txt",
            },
            {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-b",
                "session_id": "session-b",
                "run_id": "run-b",
                "artifact_id": "artifact-cross",
                "artifact_type": "report_txt",
                "label": "cross.txt",
                "content": "cross artifact",
                "storage_key": "tenants/tenant-a/private/cross.txt",
            },
        ],
        memory_records=[
            {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "session_id": "session-a",
                "memory_record_id": "mem-a",
                "record_type": "preference",
                "content": "prefer precise citations",
                "status": "active",
                "deleted_at": None,
            },
            {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "session_id": "session-a",
                "memory_record_id": "mem-deleted",
                "record_type": "preference",
                "content": "deleted sensitive content",
                "status": "deleted",
                "deleted_at": "2026-07-02T00:00:00Z",
            },
        ],
    )
    return ContextRetrieval(repo)


@pytest.mark.asyncio
async def test_read_session_messages_scopes_and_paginates_with_token_limit():
    retrieval = _retrieval()

    result = await retrieval.read_session_messages(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        limit=2,
        max_tokens=5,
    )

    assert result["items"] == [
        {
            "message_id": "msg-1",
            "run_id": "run-a",
            "role": "user",
            "content": "alpha beta gamma delta epsilon",
            "truncated": False,
        }
    ]
    assert result["next_offset"] == 1
    assert result["audit"]["action"] == "context_retrieval.read_session_messages"
    assert result["redaction"]["object_locator_refs_removed"] is True


@pytest.mark.asyncio
async def test_read_session_messages_denies_cross_user_scope():
    retrieval = _retrieval()

    with pytest.raises(ContextRetrievalDenied):
        await retrieval.read_session_messages(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-b",
            run_id="run-b",
            limit=5,
            max_tokens=20,
        )


@pytest.mark.asyncio
async def test_file_and_artifact_reads_are_scoped_limited_and_redacted():
    retrieval = _retrieval()

    file_result = await retrieval.read_context_file(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        file_id="file-a",
        max_bytes=9,
    )
    artifact_result = await retrieval.read_run_artifact(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        artifact_id="artifact-a",
        max_bytes=20,
    )

    assert file_result["content"] == "file cont"
    assert file_result["truncated"] is True
    assert artifact_result["content"] == "artifact content"
    assert "storage_key" not in file_result
    assert "storage_key" not in artifact_result

    with pytest.raises(ContextRetrievalDenied):
        await retrieval.read_run_artifact(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            artifact_id="artifact-cross",
            max_bytes=20,
        )


@pytest.mark.asyncio
async def test_search_memory_excludes_deleted_records_and_audits_redaction():
    retrieval = _retrieval()

    result = await retrieval.search_memory(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        session_id="session-a",
        query="prefer deleted",
        limit=10,
        max_tokens=20,
    )

    assert result["items"] == [
        {
            "memory_record_id": "mem-a",
            "record_type": "preference",
            "content": "prefer precise citations",
            "truncated": False,
        }
    ]
    assert result["audit"]["action"] == "context_retrieval.search_memory"
    serialized = str(result)
    assert "deleted sensitive content" not in serialized
    assert "storage_key" not in serialized


@pytest.mark.asyncio
async def test_stage_context_file_to_workspace_returns_safe_workspace_ref_without_storage_key(tmp_path):
    retrieval = _retrieval()

    result = await retrieval.stage_context_file_to_workspace(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        file_id="file-a",
        workspace_root=str(tmp_path),
    )

    assert result == {
        "file_id": "file-a",
        "workspace_path": "context/file-a/source.txt",
        "bytes_staged": len("file content is bounded by bytes".encode("utf-8")),
        "max_bytes": 1048576,
        "audit": {
            "action": "context_retrieval.stage_context_file_to_workspace",
            "bytes_read": len("file content is bounded by bytes".encode("utf-8")),
            "max_bytes": 1048576,
            "result": "staged",
        },
        "redaction": {"object_locator_refs_removed": True},
    }
    assert (tmp_path / "context" / "file-a" / "source.txt").read_text(encoding="utf-8") == "file content is bounded by bytes"


@pytest.mark.asyncio
async def test_stage_context_file_to_workspace_accepts_snapshot_authorized_prior_run_file(tmp_path):
    class SnapshotAuthorizedRepository:
        async def get_file(self, **kwargs):
            assert kwargs["run_id"] == "run-current"
            assert kwargs["file_id"] == "file-prior"
            return {
                "file_id": "file-prior",
                "run_id": "run-prior",
                "original_name": "source.docx",
                "size_bytes": 5,
                "content": b"docx!",
            }

        def read_storage_bytes(self, row):
            return row["content"]

    retrieval = ContextRetrieval(SnapshotAuthorizedRepository())

    result = await retrieval.stage_context_file_to_workspace(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-current",
        file_id="file-prior",
        workspace_root=str(tmp_path),
    )

    assert result["workspace_path"] == "context/file-prior/source.docx"
    assert (tmp_path / "context" / "file-prior" / "source.docx").read_bytes() == b"docx!"


@pytest.mark.asyncio
async def test_stage_run_artifact_to_workspace_uses_snapshot_authorized_repository_scope(tmp_path):
    class SnapshotAuthorizedRepository:
        async def get_artifact(self, **kwargs):
            assert kwargs == {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-current",
                "artifact_id": "artifact-prior",
            }
            return {
                "artifact_id": "artifact-prior",
                "run_id": "run-prior",
                "label": "translated.docx",
                "artifact_type": "translated_docx",
                "size_bytes": 5,
                "content": b"docx!",
            }

        def read_storage_bytes(self, row):
            return row["content"]

    retrieval = ContextRetrieval(SnapshotAuthorizedRepository())

    result = await retrieval.stage_run_artifact_to_workspace(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-current",
        artifact_id="artifact-prior",
        workspace_root=str(tmp_path),
    )

    assert result["workspace_path"] == "context/artifact-prior/translated.docx"
    assert result["artifact_id"] == "artifact-prior"
    assert "storage_key" not in str(result)
    assert (tmp_path / "context" / "artifact-prior" / "translated.docx").read_bytes() == b"docx!"


@pytest.mark.asyncio
async def test_stage_run_artifact_to_workspace_rejects_cross_scope_and_oversize_without_writing(tmp_path):
    retrieval = _retrieval()

    with pytest.raises(ContextRetrievalDenied, match="context_scope_denied"):
        await retrieval.stage_run_artifact_to_workspace(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            artifact_id="artifact-cross",
            workspace_root=str(tmp_path),
        )

    with pytest.raises(ContextRetrievalDenied, match="context_artifact_too_large"):
        await retrieval.stage_run_artifact_to_workspace(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            artifact_id="artifact-a",
            workspace_root=str(tmp_path),
            max_bytes=4,
        )

    assert not (tmp_path / "context").exists()


@pytest.mark.asyncio
async def test_stage_context_file_to_workspace_rejects_file_over_byte_cap_without_writing(tmp_path):
    retrieval = _retrieval()

    with pytest.raises(ContextRetrievalDenied, match="context_file_too_large"):
        await retrieval.stage_context_file_to_workspace(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            file_id="file-a",
            workspace_root=str(tmp_path),
            max_bytes=8,
        )

    assert not (tmp_path / "context").exists()


@pytest.mark.asyncio
async def test_stage_context_file_to_workspace_uses_stable_file_prefix_to_avoid_name_collisions(tmp_path):
    retrieval = ContextRetrieval(
        InMemoryContextRetrievalRepository(
            files=[
                {
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-a",
                    "session_id": "session-a",
                    "run_id": "run-a",
                    "file_id": "file-a",
                    "original_name": "source.txt",
                    "content": "alpha",
                },
                {
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-a",
                    "session_id": "session-a",
                    "run_id": "run-a",
                    "file_id": "file-b",
                    "original_name": "source.txt",
                    "content": "bravo",
                },
            ]
        )
    )

    first = await retrieval.stage_context_file_to_workspace(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        file_id="file-a",
        workspace_root=str(tmp_path),
    )
    second = await retrieval.stage_context_file_to_workspace(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        file_id="file-b",
        workspace_root=str(tmp_path),
    )

    assert first["workspace_path"] == "context/file-a/source.txt"
    assert second["workspace_path"] == "context/file-b/source.txt"
    assert (tmp_path / "context" / "file-a" / "source.txt").read_text(encoding="utf-8") == "alpha"
    assert (tmp_path / "context" / "file-b" / "source.txt").read_text(encoding="utf-8") == "bravo"


@pytest.mark.asyncio
async def test_stage_context_file_to_workspace_normalizes_windows_path_separators(tmp_path):
    retrieval = ContextRetrieval(
        InMemoryContextRetrievalRepository(
            files=[
                {
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-a",
                    "session_id": "session-a",
                    "run_id": "run-a",
                    "file_id": "file-a",
                    "original_name": "..\\..\\.claude\\settings.json",
                    "content": "safe staged content",
                }
            ]
        )
    )

    result = await retrieval.stage_context_file_to_workspace(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        file_id="file-a",
        workspace_root=str(tmp_path),
    )

    assert result["workspace_path"] == "context/file-a/settings.json"
    assert (tmp_path / "context" / "file-a" / "settings.json").read_text(encoding="utf-8") == "safe staged content"
    assert not (tmp_path / ".claude" / "settings.json").exists()


@pytest.mark.asyncio
async def test_stage_context_file_to_workspace_rejects_symlinked_context_parent(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _symlink_or_skip(outside, workspace / "context")
    retrieval = ContextRetrieval(
        InMemoryContextRetrievalRepository(
            files=[
                {
                    "tenant_id": "tenant-a",
                    "workspace_id": "workspace-a",
                    "user_id": "user-a",
                    "session_id": "session-a",
                    "run_id": "run-a",
                    "file_id": "file-a",
                    "original_name": "source.txt",
                    "content": "must not escape workspace",
                }
            ]
        )
    )

    with pytest.raises(ValueError, match="context_file_workspace_escape"):
        await retrieval.stage_context_file_to_workspace(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            file_id="file-a",
            workspace_root=str(workspace),
        )

    assert not (outside / "file-a" / "source.txt").exists()


@pytest.mark.asyncio
async def test_repository_context_retrieval_reads_file_through_scoped_repository_and_storage(monkeypatch):
    calls = []

    async def fake_get_scoped_context_file(conn, **kwargs):
        calls.append(("file_scope", kwargs))
        return {
            "id": kwargs["file_id"],
            "original_name": "source.txt",
            "content_type": "text/plain",
            "size_bytes": 64,
            "storage_key": "tenants/tenant-a/private/source.txt",
        }

    class FakeStorage:
        def get_bytes(self, *, storage_key):
            calls.append(("storage", storage_key))
            return b"repository backed file content"

    monkeypatch.setattr("app.context_retrieval.repositories.get_scoped_context_file", fake_get_scoped_context_file)
    retrieval = ContextRetrieval(RepositoryContextRetrievalRepository(object(), storage=FakeStorage()))

    result = await retrieval.read_context_file(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        file_id="file-a",
        max_bytes=10,
    )

    assert calls == [
        (
            "file_scope",
            {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "file_id": "file-a",
            },
        ),
        ("storage", "tenants/tenant-a/private/source.txt"),
    ]
    assert result["content"] == "repository"
    assert result["truncated"] is True
    serialized = str(result)
    assert "storage_key" not in serialized
    assert "tenants/tenant-a/private" not in serialized


@pytest.mark.asyncio
async def test_repository_stage_context_file_rejects_oversize_metadata_before_storage_read(monkeypatch, tmp_path):
    calls = []

    async def fake_get_scoped_context_file(conn, **kwargs):
        calls.append(("file_scope", kwargs))
        return {
            "id": kwargs["file_id"],
            "original_name": "huge-source.txt",
            "content_type": "text/plain",
            "size_bytes": 4096,
            "storage_key": "tenants/tenant-a/private/huge-source.txt",
        }

    class FakeStorage:
        def get_bytes(self, *, storage_key):
            calls.append(("storage", storage_key))
            return b"x" * 4096

    monkeypatch.setattr("app.context_retrieval.repositories.get_scoped_context_file", fake_get_scoped_context_file)
    retrieval = ContextRetrieval(RepositoryContextRetrievalRepository(object(), storage=FakeStorage()))

    with pytest.raises(ContextRetrievalDenied, match="context_file_too_large"):
        await retrieval.stage_context_file_to_workspace(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            file_id="file-large",
            workspace_root=str(tmp_path),
            max_bytes=1024,
        )

    assert calls == [
        (
            "file_scope",
            {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "file_id": "file-large",
            },
        )
    ]
    assert not (tmp_path / "context").exists()


@pytest.mark.asyncio
async def test_repository_stage_context_file_requires_declared_size_before_storage_read(monkeypatch, tmp_path):
    calls = []

    async def fake_get_scoped_context_file(conn, **kwargs):
        calls.append(("file_scope", kwargs))
        return {
            "id": kwargs["file_id"],
            "original_name": "unknown-size.txt",
            "content_type": "text/plain",
            "storage_key": "tenants/tenant-a/private/unknown-size.txt",
        }

    class FakeStorage:
        def get_bytes(self, *, storage_key):
            calls.append(("storage", storage_key))
            return b"x" * 4096

    monkeypatch.setattr("app.context_retrieval.repositories.get_scoped_context_file", fake_get_scoped_context_file)
    retrieval = ContextRetrieval(RepositoryContextRetrievalRepository(object(), storage=FakeStorage()))

    with pytest.raises(ContextRetrievalDenied, match="context_file_size_required"):
        await retrieval.stage_context_file_to_workspace(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            file_id="file-unknown-size",
            workspace_root=str(tmp_path),
            max_bytes=1024,
        )

    assert calls == [
        (
            "file_scope",
            {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "session_id": "session-a",
                "run_id": "run-a",
                "file_id": "file-unknown-size",
            },
        )
    ]
    assert not (tmp_path / "context").exists()


@pytest.mark.asyncio
async def test_repository_context_retrieval_message_pagination_uses_limit_plus_one(monkeypatch):
    calls = []

    async def fake_list_scoped_context_messages(conn, **kwargs):
        calls.append(kwargs)
        return [
            {
                "id": "msg-1",
                "run_id": kwargs["run_id"],
                "role": "user",
                "content": "first message",
            },
            {
                "id": "msg-2",
                "run_id": kwargs["run_id"],
                "role": "assistant",
                "content": "second message",
            },
            {
                "id": "msg-3",
                "run_id": kwargs["run_id"],
                "role": "user",
                "content": "third message",
            },
        ]

    monkeypatch.setattr(
        "app.context_retrieval.repositories.list_scoped_context_messages",
        fake_list_scoped_context_messages,
    )
    retrieval = ContextRetrieval(RepositoryContextRetrievalRepository(object(), storage=object()))

    result = await retrieval.read_session_messages(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        limit=2,
        offset=4,
        max_tokens=100,
    )

    assert calls[0]["limit"] == 3
    assert result["items"] == [
        {
            "message_id": "msg-1",
            "run_id": "run-a",
            "role": "user",
            "content": "first message",
            "truncated": False,
        },
        {
            "message_id": "msg-2",
            "run_id": "run-a",
            "role": "assistant",
            "content": "second message",
            "truncated": False,
        },
    ]
    assert result["next_offset"] == 6
