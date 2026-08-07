import hashlib
from contextlib import asynccontextmanager

import pytest

from app.executors.base import RunPayload
from app.executors.claude_agent_worker import ClaudeAgentWorkerAdapter
from app.file_parser_contracts import XLSX_CONTENT_TYPE


def payload(*, file_ids: list[str]) -> RunPayload:
    skill_version = "test-skill-version"
    return RunPayload(
        tenant_id="default",
        workspace_id="default",
        user_id="user-a",
        session_id="ses_1",
        run_id="run_1",
        attempt_id="qat-test-attempt",
        agent_id="translate",
        skill_id="baoyu-translate",
        file_ids=file_ids,
        input={},
        skill_version=skill_version,
        release_decision={
            "schema_version": "ai-platform.skill-release-decision.v1",
            "selected_version": skill_version,
        },
        skill_manifests=[
            {
                "skill_id": "baoyu-translate",
                "content_hash": skill_version,
            }
        ],
    )


@pytest.mark.asyncio
async def test_materialize_files_accepts_prior_run_file_authorized_by_current_snapshot(
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    raw = b"snapshot-authorized-content"

    class FakeStorage:
        def get_bytes(self, *, storage_key):
            assert storage_key == "files/prior.docx"
            return raw

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def fake_get_scoped_context_file(_conn, **kwargs):
        assert kwargs == {
            "tenant_id": "default",
            "workspace_id": "default",
            "user_id": "user-a",
            "session_id": "ses_1",
            "run_id": "run_1",
            "file_id": "file-prior",
        }
        return {
            "run_id": "run-prior",
            "original_name": "prior.docx",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "size_bytes": len(raw),
            "storage_key": "files/prior.docx",
        }

    adapter = ClaudeAgentWorkerAdapter()
    monkeypatch.setattr("app.executors.claude_agent_worker.ObjectStorage", FakeStorage)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_scoped_context_file",
        fake_get_scoped_context_file,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)

    materialized = await adapter._materialize_files(
        payload(file_ids=["file-prior"]),
        workspace,
    )

    assert list(materialized) == ["prior.docx"]
    assert materialized.materialized_file_names == ["prior.docx"]
    assert (workspace / "inputs" / "prior.docx").read_bytes() == raw


@pytest.mark.asyncio
async def test_materialize_files_fails_when_primary_file_is_not_snapshot_authorized(
    monkeypatch,
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def missing_file(*args, **kwargs):
        return None

    adapter = ClaudeAgentWorkerAdapter()
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_scoped_context_file",
        missing_file,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)

    with pytest.raises(ValueError, match="context_file_unavailable"):
        await adapter._materialize_files(payload(file_ids=["file-missing"]), workspace)


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["sha256", "size_bytes"])
async def test_materialize_files_fails_when_snapshot_file_identity_mismatches(
    monkeypatch,
    tmp_path,
    mismatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    raw = b"stored-content"

    class FakeStorage:
        def get_bytes(self, *, storage_key):
            return raw

    @asynccontextmanager
    async def fake_transaction():
        yield object()

    async def fake_get_scoped_context_file(_conn, **kwargs):
        row = {
            "original_name": "source.xlsx",
            "content_type": XLSX_CONTENT_TYPE,
            "size_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "storage_key": "files/source.xlsx",
        }
        row[mismatch] = "0" * 64 if mismatch == "sha256" else len(raw) + 1
        return row

    adapter = ClaudeAgentWorkerAdapter()
    monkeypatch.setattr("app.executors.claude_agent_worker.ObjectStorage", FakeStorage)
    monkeypatch.setattr(
        "app.executors.claude_agent_worker.repositories.get_scoped_context_file",
        fake_get_scoped_context_file,
    )
    monkeypatch.setattr("app.executors.claude_agent_worker.transaction", fake_transaction)

    with pytest.raises(ValueError, match="context_file_identity_mismatch"):
        await adapter._materialize_files(payload(file_ids=["file-prior"]), workspace)
