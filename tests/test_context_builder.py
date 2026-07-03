import pytest
from datetime import datetime

from app.context_builder import (
    executor_context_pack_from_snapshot,
    ensure_public_context_provenance,
    initial_context_summary,
    public_context_payload,
    public_context_provenance,
    record_initial_context_snapshot,
)


@pytest.mark.asyncio
async def test_record_initial_context_snapshot_persists_context_manifest_for_executor_pack(monkeypatch):
    calls = []

    async def fake_get_effective_memory_policy(conn, **kwargs):
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 90,
            "source": "default",
        }

    async def fake_create_context_snapshot(conn, **kwargs):
        calls.append(("snapshot", kwargs))
        return {"id": "ctx-manifest"}

    async def fake_update_run_context_snapshot_ref(conn, **kwargs):
        calls.append(("run_ref", kwargs))

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-manifest"

    monkeypatch.setattr("app.context_builder.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.context_builder.repositories.create_context_snapshot", fake_create_context_snapshot)
    monkeypatch.setattr("app.context_builder.repositories.update_run_context_snapshot_ref", fake_update_run_context_snapshot_ref)
    monkeypatch.setattr("app.context_builder.repositories.append_event", fake_append_event)

    context_ref = await record_initial_context_snapshot(
        object(),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        trace_id="trace-a",
        agent_id="general-agent",
        skill_id="general-chat",
        input_payload={
            "message": "continue from prior context",
            "raw_storage_key": "tenants/tenant-a/private/source.docx",
            "history": [{"role": "user", "content": "history must not become prompt stuffing"}],
        },
        message_ids=["msg-a"],
        file_ids=["file-a"],
        source="chat_stream",
    )

    snapshot_call = next(item[1] for item in calls if item[0] == "snapshot")
    manifest = snapshot_call["payload_json"]["context_manifest"]
    assert manifest["schema_version"] == "ai-platform.context-manifest.v1"
    assert manifest["scope"] == {
        "tenant_id": "tenant-a",
        "workspace_id": "workspace-a",
        "user_id": "user-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "agent_id": "general-agent",
        "skill_id": "general-chat",
    }
    assert manifest["current_message"] == "continue from prior context"
    assert manifest["recent_messages"] == [{"message_id": "msg-a", "requires_retrieval": True}]
    assert manifest["files"] == [{"file_id": "file-a", "requires_retrieval": True}]
    assert manifest["budget"]["max_prompt_tokens"] > 0
    assert context_ref["context_manifest"]["schema_version"] == "ai-platform.context-manifest.v1"
    assert context_ref["context_manifest"]["redaction"]["object_locator_refs_removed"] is True
    assert "file-a" not in str(context_ref)
    assert "msg-a" not in str(context_ref)

    context_pack = executor_context_pack_from_snapshot(snapshot_call["payload_json"])
    assert context_pack["source"] == "context_manifest"
    assert context_pack["context_manifest"]["files"][0]["file_id"] == "file-a"
    assert "Use context retrieval tools" in context_pack["prompt_summary"]
    serialized = str(context_pack).lower()
    assert "raw_storage_key" not in serialized
    assert "tenants/tenant-a/private" not in serialized
    assert "history must not become prompt stuffing" not in serialized


def test_initial_context_summary_strips_context_private_aliases_from_input_keys():
    summary = initial_context_summary(
        source="runs_api",
        agent_id="general-agent",
        skill_id="general-chat",
        input_payload={
            "message": "hello",
            "copied_from_run_id": "run-source",
            "source_run_id": "run-source",
            "parent_run_id": "run-parent",
            "raw_storage_key": "storage-key-value",
            "sandbox_workdir": "relative-workdir",
            "executor_private_payload": {"token": "hidden"},
            "nested": {
                "storage_key": "nested-storage-key",
                "safe": "kept",
            },
        },
        message_ids=[],
        file_ids=[],
    )

    assert summary["input_keys"] == ["message", "nested"]
    assert summary["used_context_summary"]["input_keys"] == ["message", "nested"]
    serialized = str(summary).lower()
    assert "raw_storage_key" not in serialized
    assert "copied_from_run_id" not in serialized
    assert "source_run_id" not in serialized
    assert "parent_run_id" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "executor_private_payload" not in serialized
    assert "storage-key-value" not in serialized
    assert "relative-workdir" not in serialized
    assert "nested-storage-key" not in serialized


def test_public_context_payload_strips_legacy_context_summary_fields():
    payload = {
        "schema_version": "ai-platform.context-snapshot.v1",
        "source": "runs_api",
        "agent_id": "general-agent",
        "skill_id": "general-chat",
        "capability_id": "general_chat",
        "input_keys": ["message", "raw_storage_key"],
        "message_count": 99,
        "file_count": 99,
        "artifact_count": 99,
        "memory_record_count": 99,
        "included_message_ids": ["msg-secret"],
        "included_file_ids": ["file-secret"],
        "included_artifact_ids": ["artifact-secret"],
        "included_memory_record_ids": ["memory-secret"],
        "context_snapshot_id": "ctx-secret",
        "schemaVersion": "forged",
        "agentId": "forged-agent",
        "messageCount": 123,
        "contextSnapshotId": "ctx-forged",
        "usedContextSummary": {"source": "forged-camel"},
        "referencedMaterials": {"messageCount": 123},
        "provenance": {"source": "forged"},
        "Provenance": {"source": "forged-title"},
        "provenance%5Fsummary": {"source": "forged-encoded"},
        "summary": "legacy summary",
        "Summary": "legacy title summary",
        "summary%5Fpayload": {"source": "forged-encoded-summary"},
        "raw%5Fstorage%5Fkey": "s3://encoded/private",
        "sandbox%5Fworkdir": "/tmp/encoded-private",
        "executor%5Fprivate%5Fpayload": {"token": "encoded-private"},
        "used%5Fcontext%5Fsummary": {"source": "forged-encoded"},
        "memory_policy": {"source": "stored"},
        "memoryPolicy": {"source": "forged-camel"},
        "window": "current",
        "nested": {"safe": "kept"},
    }

    assert public_context_payload(payload) == {
        "window": "current",
        "nested": {"safe": "kept"},
    }


def test_public_context_payload_strips_raw_material_id_aliases_and_overencoded_private_keys():
    overencoded_raw_storage_key = "raw_storage_key"
    for _ in range(9):
        overencoded_raw_storage_key = overencoded_raw_storage_key.replace("_", "%5F").replace("%", "%25")

    payload = {
        "messageids": ["msg-lower-secret"],
        "message_ids": ["msg-secret"],
        "fileids": ["file-lower-secret"],
        "fileIds": ["file-secret"],
        "artifactids": ["artifact-lower-secret"],
        "artifact_ids": ["artifact-secret"],
        "memoryrecordids": ["memory-lower-secret"],
        "memoryRecordIds": ["memory-secret"],
        "materialids": ["material-lower-secret"],
        "raw_material_ids": ["raw-material-secret"],
        "sourcefileid": "source-file-lower-secret",
        "sourceFileId": "source-file-secret",
        overencoded_raw_storage_key: "object-locator-123",
        "profile_id": "public-profile-id",
        "safe_context_label": "kept",
    }

    assert public_context_payload(payload) == {
        "profile_id": "public-profile-id",
        "safe_context_label": "kept",
    }


def test_ensure_public_context_provenance_preserves_stored_safe_explainability_fields():
    payload = {
        "window": "current",
        "used_context_summary": {
            "source": "chat_stream",
            "input_keys": ["message", "raw_storage_key"],
            "memory_policy_source": "stored",
            "long_term_memory_read": True,
        },
        "execution_tier": "document_worker",
        "latest_artifact_version": "v7",
        "context_pack_generated_at": "2026-06-12T01:23:45Z",
    }

    projected = ensure_public_context_provenance(
        payload,
        source="stored_context_snapshot",
        message_count=1,
        file_count=1,
        artifact_count=1,
        memory_record_count=0,
        preserve_stored_input_keys=True,
    )

    assert projected["used_context_summary"] == {
        "source": "chat_stream",
        "input_keys": ["attachments", "message"],
        "memory_policy_source": "stored",
        "long_term_memory_read": True,
    }
    assert projected["execution_tier"] == "document_worker"
    assert projected["latest_artifact_version"] == "v7"
    assert projected["context_pack_generated_at"] == "2026-06-12T01:23:45Z"
    serialized = str(projected).lower()
    assert "raw_storage_key" not in serialized


def test_ensure_public_context_provenance_preserves_safe_top_level_legacy_source():
    payload = {
        "source": "chat_stream",
        "message": "hello",
        "context_pack_generated_at": "2026-06-12T01:23:45Z",
    }

    projected = ensure_public_context_provenance(
        payload,
        source="stored_context_snapshot",
        message_count=1,
        file_count=0,
        artifact_count=0,
        memory_record_count=0,
        preserve_stored_input_keys=True,
    )

    assert projected["used_context_summary"] == {
        "source": "chat_stream",
        "input_keys": ["message"],
        "memory_policy_source": "not_recorded",
        "long_term_memory_read": False,
    }
    assert projected["context_pack_generated_at"] == "2026-06-12T01:23:45Z"
    serialized = str(projected).lower()
    assert "stored_context_snapshot" not in serialized


def test_public_context_provenance_preserves_stored_source_and_falls_back_from_unknown_source():
    stored = public_context_provenance(
        source="stored_context_snapshot",
        input_payload={"message": "hello"},
        message_count=1,
    )
    unknown = public_context_provenance(
        source="private_runtime_source",
        input_payload={"message": "hello"},
        message_count=1,
    )

    assert stored["used_context_summary"]["source"] == "stored_context_snapshot"
    assert unknown["used_context_summary"]["source"] == "stored_context_snapshot"
    assert "private_runtime_source" not in str(unknown)


def test_public_context_provenance_rejects_unsafe_direct_explainability_values():
    projected = public_context_provenance(
        source="runs_api",
        input_payload={"message": "hello"},
        memory_policy_source="private_policy",
        latest_artifact_version="tenants/default/runs/run-a/artifacts/private.docx",
        execution_tier="root_shell",
        context_pack_version="sha256:" + "a" * 64,
        generated_at="/workspace/private/context.json",
    )

    assert projected["used_context_summary"]["memory_policy_source"] == "not_recorded"
    assert projected["latest_artifact_version"] is None
    assert projected["execution_tier"] == "sdk_only_writing"
    assert projected["context_pack_version"] == "v1"
    assert projected["context_pack_generated_at"] != "/workspace/private/context.json"
    assert datetime.fromisoformat(projected["context_pack_generated_at"].replace("Z", "+00:00"))
    serialized = str(projected).lower()
    assert "private_policy" not in serialized
    assert "private.docx" not in serialized
    assert "root_shell" not in serialized
    assert "/workspace/private" not in serialized


def test_ensure_public_context_provenance_rejects_unsafe_stored_explainability_fields():
    payload = {
        "window": "current",
        "used_context_summary": {
            "source": "forged_source",
            "input_keys": ["raw_storage_key"],
            "memory_policy_source": "forged_policy",
            "long_term_memory_read": True,
        },
        "execution_tier": "private_root_shell",
        "latest_artifact_version": "artifact-a",
        "context_pack_generated_at": "not-a-date",
    }

    projected = ensure_public_context_provenance(
        payload,
        source="stored_context_snapshot",
        message_count=1,
        file_count=1,
        artifact_count=0,
        memory_record_count=0,
        preserve_stored_input_keys=True,
    )

    assert projected["used_context_summary"] == {
        "source": "stored_context_snapshot",
        "input_keys": ["attachments", "window"],
        "memory_policy_source": "not_recorded",
        "long_term_memory_read": False,
    }
    assert projected["execution_tier"] == "sdk_only_writing"
    assert projected["latest_artifact_version"] is None
    assert datetime.fromisoformat(projected["context_pack_generated_at"].replace("Z", "+00:00"))
    assert projected["context_pack_generated_at"] != "not-a-date"
    serialized = str(projected).lower()
    assert "raw_storage_key" not in serialized
    assert "artifact-a" not in serialized


def test_initial_context_summary_includes_public_context_provenance_contract():
    summary = initial_context_summary(
        source="runs_api",
        agent_id="general-agent",
        skill_id="general-chat",
        input_payload={"message": "hello", "private_payload": "not stored"},
        message_ids=["msg-a", "msg-b"],
        file_ids=["file-a"],
        memory_record_ids=["mem-a"],
        memory_policy={
            "source": "stored",
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 30,
        },
    )

    assert summary["referenced_materials"] == {
        "message_count": 2,
        "file_count": 1,
        "artifact_count": 0,
        "memory_record_count": 1,
    }
    assert summary["used_context_summary"] == {
        "source": "runs_api",
        "input_keys": ["attachments", "message"],
        "memory_policy_source": "stored",
        "long_term_memory_read": False,
    }
    assert summary["latest_artifact_version"] is None
    assert summary["execution_tier"] == "sdk_only_writing"
    assert summary["context_pack_version"] == "v1"
    assert datetime.fromisoformat(summary["context_pack_generated_at"].replace("Z", "+00:00"))
    serialized = str(summary).lower()
    assert "msg-a" not in serialized
    assert "file-a" not in serialized
    assert "mem-a" not in serialized
    assert "private_payload" not in serialized


def test_initial_context_summary_rejects_unsafe_context_pack_version():
    summary = public_context_provenance(
        source="runs_api",
        input_payload={"message": "hello"},
        context_pack_version="0123456789abcdef0123456789abcdef",
    )

    assert summary["context_pack_version"] == "v1"
    serialized = str(summary).lower()
    assert "0123456789abcdef0123456789abcdef" not in serialized


def test_initial_context_summary_adds_attachment_signal_for_file_context():
    summary = initial_context_summary(
        source="chat_stream",
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
        input_payload={"message": "review this document"},
        message_ids=["msg-a"],
        file_ids=["file-a"],
    )

    assert summary["referenced_materials"]["file_count"] == 1
    assert summary["used_context_summary"]["input_keys"] == ["attachments", "message"]
    serialized = str(summary).lower()
    assert "file-a" not in serialized
    assert "raw_storage_key" not in serialized


def test_initial_context_summary_routes_document_skill_to_document_worker():
    summary = initial_context_summary(
        source="chat_stream",
        agent_id="baoyu-translate",
        skill_id="baoyu-translate",
        input_payload={"message": "Translate this DOCX and return a Word document."},
        message_ids=["msg-a"],
        file_ids=["file-a"],
    )

    assert summary["execution_tier"] == "document_worker"
    assert summary["used_context_summary"]["input_keys"] == ["attachments", "message"]
    assert summary["referenced_materials"]["file_count"] == 1


def test_initial_context_summary_routes_explicit_sandbox_request_to_heavy_sandbox():
    summary = initial_context_summary(
        source="runs_api",
        agent_id="general-agent",
        skill_id="general-chat",
        input_payload={
            "message": "Run this script in a browser automation task.",
            "sandbox_mode": "ephemeral",
        },
        message_ids=["msg-a"],
        file_ids=[],
    )

    assert summary["execution_tier"] == "heavy_sandbox"


def test_executor_context_pack_from_snapshot_returns_bounded_safe_prompt_contract():
    context_pack = executor_context_pack_from_snapshot(
        {
            "context_snapshot_id": "ctx-a",
            "source": "chat_stream",
            "referenced_materials": {
                "message_count": 3,
                "file_count": 1,
                "artifact_count": 2,
                "memory_record_count": 4,
            },
            "used_context_summary": {
                "source": "chat_stream",
                "input_keys": ["message", "attachments", "raw_storage_key"],
                "memory_policy_source": "stored",
                "long_term_memory_read": True,
            },
            "latest_artifact_version": "v2",
            "execution_tier": "document_worker",
            "context_pack_version": "v8",
            "context_pack_generated_at": "2026-06-12T01:23:45Z",
            "raw_storage_key": "s3://private/object",
            "sandbox_workdir": "/tmp/private",
            "executor_private_payload": {"token": "secret"},
        }
    )

    assert context_pack == {
        "schema_version": "ai-platform.executor-context-pack.v1",
        "source": "chat_stream",
        "referenced_materials": {
            "message_count": 3,
            "file_count": 1,
            "artifact_count": 2,
            "memory_record_count": 4,
        },
        "used_context_summary": {
            "source": "chat_stream",
            "input_keys": ["attachments", "message"],
            "memory_policy_source": "stored",
            "long_term_memory_read": False,
        },
        "latest_artifact_version": "v2",
        "execution_tier": "document_worker",
        "context_pack_version": "v8",
        "context_pack_generated_at": "2026-06-12T01:23:45Z",
        "prompt_summary": (
            "Context pack: 3 message(s), 1 file(s), 2 artifact(s), "
            "0 long-term memory record(s). Inputs: attachments, message. "
            "Execution tier: document_worker. Context pack version: v8. Latest artifact version: v2."
        ),
    }
    serialized = str(context_pack).lower()
    assert "raw_storage_key" not in serialized
    assert "s3://private" not in serialized
    assert "sandbox_workdir" not in serialized
    assert "executor_private_payload" not in serialized
    assert "secret" not in serialized


def test_executor_context_pack_from_snapshot_defaults_for_missing_snapshot():
    context_pack = executor_context_pack_from_snapshot(None)

    assert context_pack["schema_version"] == "ai-platform.executor-context-pack.v1"
    assert context_pack["source"] == "stored_context_snapshot"
    assert context_pack["referenced_materials"] == {
        "message_count": 0,
        "file_count": 0,
        "artifact_count": 0,
        "memory_record_count": 0,
    }
    assert context_pack["used_context_summary"] == {
        "source": "stored_context_snapshot",
        "input_keys": [],
        "memory_policy_source": "not_recorded",
        "long_term_memory_read": False,
    }
    assert context_pack["execution_tier"] == "sdk_only_writing"
    assert context_pack["context_pack_version"] == "v1"
    assert "0 long-term memory record(s)" in context_pack["prompt_summary"]


@pytest.mark.asyncio
async def test_record_initial_context_snapshot_records_effective_memory_policy_without_reading_long_term_memory(monkeypatch):
    calls = []

    async def fake_get_effective_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "source": "stored",
            "reason": "user opt-out",
            "updated_by": "admin-a",
            "updated_at": "2026-06-02T12:00:00Z",
        }

    async def fake_create_context_snapshot(conn, **kwargs):
        calls.append(("snapshot", kwargs))
        return {"id": "ctx-policy"}

    async def fake_update_run_context_snapshot_ref(conn, **kwargs):
        calls.append(("run_ref", kwargs))

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-policy"

    monkeypatch.setattr("app.context_builder.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.context_builder.repositories.create_context_snapshot", fake_create_context_snapshot)
    monkeypatch.setattr("app.context_builder.repositories.update_run_context_snapshot_ref", fake_update_run_context_snapshot_ref)
    monkeypatch.setattr("app.context_builder.repositories.append_event", fake_append_event)

    context_ref = await record_initial_context_snapshot(
        object(),
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        trace_id="trace-a",
        agent_id="general-agent",
        skill_id="general-chat",
        input_payload={"message": "hello"},
        message_ids=["msg-a"],
        file_ids=[],
        source="runs_api",
    )

    assert calls[0] == (
        "policy",
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
            "agent_id": "general-agent",
        },
    )
    snapshot = calls[1][1]
    assert snapshot["included_memory_record_ids"] == []
    assert snapshot["redaction_summary_json"] == {
        "input_payload_stored": False,
        "raw_skill_selector_stored": False,
        "long_term_memory_read": False,
        "memory_policy_source": "stored",
        "memory_enabled": False,
        "long_term_memory_enabled": False,
        "retention_days": 30,
    }
    assert snapshot["payload_json"]["memory_policy"] == {
        "source": "stored",
        "memory_enabled": False,
        "long_term_memory_enabled": False,
        "retention_days": 30,
    }
    assert context_ref["memory_policy"]["memory_enabled"] is False
    assert context_ref["memory_record_count"] == 0
    assert context_ref["referenced_materials"] == {
        "message_count": 1,
        "file_count": 0,
        "artifact_count": 0,
        "memory_record_count": 0,
    }
    assert context_ref["used_context_summary"]["source"] == "runs_api"
    assert context_ref["execution_tier"] == "sdk_only_writing"
    assert context_ref["context_pack_version"] == "v1"


def test_initial_context_summary_clamps_long_term_memory_policy_projection():
    summary = initial_context_summary(
        source="chat",
        agent_id="general-agent",
        skill_id="general-chat",
        input_payload={"message": "hello"},
        message_ids=[],
        file_ids=[],
        memory_policy={
            "source": "stored",
            "memory_enabled": True,
            "long_term_memory_enabled": True,
            "retention_days": 90,
        },
    )

    assert summary["memory_policy"]["memory_enabled"] is True
    assert summary["memory_policy"]["long_term_memory_enabled"] is False


@pytest.mark.asyncio
async def test_record_initial_context_snapshot_adds_source_run_artifact_followup_state(monkeypatch):
    calls = []

    async def fake_get_effective_memory_policy(conn, **kwargs):
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 90,
            "source": "default",
        }

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id, for_update=False):
        calls.append(("authorize_source", tenant_id, user_id, run_id, for_update))
        return {
            "id": run_id,
            "tenant_id": tenant_id,
            "workspace_id": "default",
            "user_id": user_id,
            "session_id": "session-a",
        }

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        calls.append(("source_artifacts", tenant_id, run_id))
        return [
            {
                "id": "art-v1",
                "artifact_type": "reviewed_docx",
                "storage_key": "tenants/tenant-a/workspaces/default/runs/run-source/artifacts/v1.docx",
                "manifest_json": {"artifact_version": "v1"},
            },
            {
                "id": "art-v2",
                "artifact_type": "reviewed_docx",
                "storage_key": "tenants/tenant-a/workspaces/default/runs/run-source/artifacts/v2.docx",
                "manifest_json": {"document_version": "v2"},
            },
        ]

    async def fake_create_context_snapshot(conn, **kwargs):
        calls.append(("snapshot", kwargs))
        return {"id": "ctx-followup"}

    async def fake_update_run_context_snapshot_ref(conn, **kwargs):
        calls.append(("run_ref", kwargs))

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-followup"

    monkeypatch.setattr("app.context_builder.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.context_builder.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.context_builder.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.context_builder.repositories.create_context_snapshot", fake_create_context_snapshot)
    monkeypatch.setattr("app.context_builder.repositories.update_run_context_snapshot_ref", fake_update_run_context_snapshot_ref)
    monkeypatch.setattr("app.context_builder.repositories.append_event", fake_append_event)

    context_ref = await record_initial_context_snapshot(
        object(),
        tenant_id="tenant-a",
        workspace_id="default",
        user_id="user-a",
        session_id="session-a",
        run_id="run-followup",
        trace_id="trace-followup",
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
        input_payload={"message": "continue previous document"},
        message_ids=["msg-followup"],
        file_ids=["file-a"],
        source="copy_run",
        source_run_id="run-source",
    )

    assert ("authorize_source", "tenant-a", "user-a", "run-source", False) in calls
    assert ("source_artifacts", "tenant-a", "run-source") in calls
    snapshot_call = next(item[1] for item in calls if item[0] == "snapshot")
    assert snapshot_call["included_artifact_ids"] == ["art-v1", "art-v2"]
    assert snapshot_call["payload_json"]["referenced_materials"]["artifact_count"] == 2
    assert snapshot_call["payload_json"]["latest_artifact_version"] == "v2"

    assert context_ref["referenced_materials"]["artifact_count"] == 2
    assert context_ref["latest_artifact_version"] == "v2"
    serialized_ref = str(context_ref).lower()
    assert "art-v1" not in serialized_ref
    assert "art-v2" not in serialized_ref
    assert "storage_key" not in serialized_ref
    assert "v1.docx" not in serialized_ref


@pytest.mark.asyncio
async def test_record_initial_context_snapshot_does_not_invent_artifact_version_from_count(monkeypatch):
    calls = []

    async def fake_get_effective_memory_policy(conn, **kwargs):
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 90,
            "source": "default",
        }

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id, for_update=False):
        return {
            "id": run_id,
            "tenant_id": tenant_id,
            "workspace_id": "default",
            "user_id": user_id,
            "session_id": "session-a",
        }

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        return [
            {
                "id": "art-a",
                "artifact_type": "reviewed_docx",
                "manifest_json": {"schema_version": "ai-platform.artifact-manifest.v1"},
            },
            {
                "id": "art-b",
                "artifact_type": "reviewed_docx",
                "manifest_json": {"artifact_version": "artifact-secret-id"},
            },
        ]

    async def fake_create_context_snapshot(conn, **kwargs):
        calls.append(("snapshot", kwargs))
        return {"id": "ctx-followup"}

    async def fake_update_run_context_snapshot_ref(conn, **kwargs):
        calls.append(("run_ref", kwargs))

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-followup"

    monkeypatch.setattr("app.context_builder.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.context_builder.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.context_builder.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.context_builder.repositories.create_context_snapshot", fake_create_context_snapshot)
    monkeypatch.setattr("app.context_builder.repositories.update_run_context_snapshot_ref", fake_update_run_context_snapshot_ref)
    monkeypatch.setattr("app.context_builder.repositories.append_event", fake_append_event)

    context_ref = await record_initial_context_snapshot(
        object(),
        tenant_id="tenant-a",
        workspace_id="default",
        user_id="user-a",
        session_id="session-a",
        run_id="run-followup",
        trace_id="trace-followup",
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
        input_payload={"message": "continue previous document"},
        message_ids=["msg-followup"],
        file_ids=["file-a"],
        source="copy_run",
        source_run_id="run-source",
    )

    snapshot_call = next(item[1] for item in calls if item[0] == "snapshot")
    assert snapshot_call["included_artifact_ids"] == ["art-a", "art-b"]
    assert snapshot_call["payload_json"]["referenced_materials"]["artifact_count"] == 2
    assert snapshot_call["payload_json"]["latest_artifact_version"] is None
    assert context_ref["referenced_materials"]["artifact_count"] == 2
    assert context_ref["latest_artifact_version"] is None


@pytest.mark.asyncio
async def test_record_initial_context_snapshot_skips_source_artifacts_without_same_scope_authorization(monkeypatch):
    calls = []

    async def fake_get_effective_memory_policy(conn, **kwargs):
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 90,
            "source": "default",
        }

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id, for_update=False):
        calls.append(("authorize_source", tenant_id, user_id, run_id, for_update))
        return None

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        calls.append(("source_artifacts", tenant_id, run_id))
        raise AssertionError("unauthorized follow-up source run artifacts must not be read")

    async def fake_create_context_snapshot(conn, **kwargs):
        calls.append(("snapshot", kwargs))
        return {"id": "ctx-followup"}

    async def fake_update_run_context_snapshot_ref(conn, **kwargs):
        calls.append(("run_ref", kwargs))

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-followup"

    monkeypatch.setattr("app.context_builder.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.context_builder.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.context_builder.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.context_builder.repositories.create_context_snapshot", fake_create_context_snapshot)
    monkeypatch.setattr("app.context_builder.repositories.update_run_context_snapshot_ref", fake_update_run_context_snapshot_ref)
    monkeypatch.setattr("app.context_builder.repositories.append_event", fake_append_event)

    context_ref = await record_initial_context_snapshot(
        object(),
        tenant_id="tenant-a",
        workspace_id="default",
        user_id="user-a",
        session_id="session-a",
        run_id="run-followup",
        trace_id="trace-followup",
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
        input_payload={"message": "continue previous document"},
        message_ids=["msg-followup"],
        file_ids=["file-a"],
        source="copy_run",
        source_run_id="run-cross-user",
    )

    assert ("authorize_source", "tenant-a", "user-a", "run-cross-user", False) in calls
    assert not any(item[0] == "source_artifacts" for item in calls)
    snapshot_call = next(item[1] for item in calls if item[0] == "snapshot")
    assert snapshot_call["included_artifact_ids"] == []
    assert snapshot_call["payload_json"]["referenced_materials"]["artifact_count"] == 0
    assert snapshot_call["payload_json"]["latest_artifact_version"] is None
    assert context_ref["referenced_materials"]["artifact_count"] == 0
    assert context_ref["latest_artifact_version"] is None


@pytest.mark.asyncio
async def test_record_initial_context_snapshot_requires_source_artifacts_same_workspace_and_session(monkeypatch):
    calls = []

    async def fake_get_effective_memory_policy(conn, **kwargs):
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 90,
            "source": "default",
        }

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id, for_update=False):
        calls.append(("authorize_source", tenant_id, user_id, run_id, for_update))
        return {
            "id": run_id,
            "tenant_id": tenant_id,
            "workspace_id": "other-workspace",
            "user_id": user_id,
            "session_id": "other-session",
        }

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        calls.append(("source_artifacts", tenant_id, run_id))
        raise AssertionError("cross-workspace or cross-session source artifacts must not be read")

    async def fake_create_context_snapshot(conn, **kwargs):
        calls.append(("snapshot", kwargs))
        return {"id": "ctx-followup"}

    async def fake_update_run_context_snapshot_ref(conn, **kwargs):
        calls.append(("run_ref", kwargs))

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-followup"

    monkeypatch.setattr("app.context_builder.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.context_builder.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.context_builder.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.context_builder.repositories.create_context_snapshot", fake_create_context_snapshot)
    monkeypatch.setattr("app.context_builder.repositories.update_run_context_snapshot_ref", fake_update_run_context_snapshot_ref)
    monkeypatch.setattr("app.context_builder.repositories.append_event", fake_append_event)

    context_ref = await record_initial_context_snapshot(
        object(),
        tenant_id="tenant-a",
        workspace_id="default",
        user_id="user-a",
        session_id="session-a",
        run_id="run-followup",
        trace_id="trace-followup",
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
        input_payload={"message": "continue previous document"},
        message_ids=["msg-followup"],
        file_ids=["file-a"],
        source="copy_run",
        source_run_id="run-other-scope",
    )

    assert ("authorize_source", "tenant-a", "user-a", "run-other-scope", False) in calls
    assert not any(item[0] == "source_artifacts" for item in calls)
    snapshot_call = next(item[1] for item in calls if item[0] == "snapshot")
    assert snapshot_call["included_artifact_ids"] == []
    assert snapshot_call["payload_json"]["referenced_materials"]["artifact_count"] == 0
    assert context_ref["referenced_materials"]["artifact_count"] == 0


@pytest.mark.asyncio
async def test_record_initial_context_snapshot_requires_source_artifacts_same_tenant_and_user(monkeypatch):
    calls = []

    async def fake_get_effective_memory_policy(conn, **kwargs):
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": True,
            "long_term_memory_enabled": False,
            "retention_days": 90,
            "source": "default",
        }

    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id, for_update=False):
        calls.append(("authorize_source", tenant_id, user_id, run_id, for_update))
        return {
            "id": run_id,
            "tenant_id": "tenant-b",
            "workspace_id": "default",
            "user_id": "user-b",
            "session_id": "session-a",
        }

    async def fake_list_run_artifacts(conn, *, tenant_id, run_id):
        calls.append(("source_artifacts", tenant_id, run_id))
        raise AssertionError("cross-tenant or cross-user source artifacts must not be read")

    async def fake_create_context_snapshot(conn, **kwargs):
        calls.append(("snapshot", kwargs))
        return {"id": "ctx-followup"}

    async def fake_update_run_context_snapshot_ref(conn, **kwargs):
        calls.append(("run_ref", kwargs))

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-followup"

    monkeypatch.setattr("app.context_builder.repositories.get_effective_memory_policy", fake_get_effective_memory_policy)
    monkeypatch.setattr("app.context_builder.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.context_builder.repositories.list_run_artifacts", fake_list_run_artifacts)
    monkeypatch.setattr("app.context_builder.repositories.create_context_snapshot", fake_create_context_snapshot)
    monkeypatch.setattr("app.context_builder.repositories.update_run_context_snapshot_ref", fake_update_run_context_snapshot_ref)
    monkeypatch.setattr("app.context_builder.repositories.append_event", fake_append_event)

    context_ref = await record_initial_context_snapshot(
        object(),
        tenant_id="tenant-a",
        workspace_id="default",
        user_id="user-a",
        session_id="session-a",
        run_id="run-followup",
        trace_id="trace-followup",
        agent_id="qa-word-review",
        skill_id="qa-file-reviewer",
        input_payload={"message": "continue previous document"},
        message_ids=["msg-followup"],
        file_ids=["file-a"],
        source="copy_run",
        source_run_id="run-other-owner",
    )

    assert ("authorize_source", "tenant-a", "user-a", "run-other-owner", False) in calls
    assert not any(item[0] == "source_artifacts" for item in calls)
    snapshot_call = next(item[1] for item in calls if item[0] == "snapshot")
    assert snapshot_call["included_artifact_ids"] == []
    assert snapshot_call["payload_json"]["referenced_materials"]["artifact_count"] == 0
    assert context_ref["referenced_materials"]["artifact_count"] == 0
