import pytest
from datetime import datetime

from app.context_builder import (
    ensure_public_context_provenance,
    initial_context_summary,
    public_context_payload,
    record_initial_context_snapshot,
)


def test_initial_context_summary_strips_context_private_aliases_from_input_keys():
    summary = initial_context_summary(
        source="runs_api",
        agent_id="general-agent",
        skill_id="general-chat",
        input_payload={
            "message": "hello",
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
            "long_term_memory_read": False,
        },
        "execution_tier": "document_worker",
        "latest_artifact_version": "artifact-v7",
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
        "input_keys": ["message"],
        "memory_policy_source": "stored",
        "long_term_memory_read": False,
    }
    assert projected["execution_tier"] == "document_worker"
    assert projected["latest_artifact_version"] == "artifact-v7"
    assert projected["context_pack_generated_at"] == "2026-06-12T01:23:45Z"
    serialized = str(projected).lower()
    assert "raw_storage_key" not in serialized


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
        "latest_artifact_version": "raw_storage_key=s3://private/key",
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
        "input_keys": ["window"],
        "memory_policy_source": "not_recorded",
        "long_term_memory_read": False,
    }
    assert projected["execution_tier"] == "sdk_only_writing"
    assert projected["latest_artifact_version"] is None
    assert datetime.fromisoformat(projected["context_pack_generated_at"].replace("Z", "+00:00"))
    assert projected["context_pack_generated_at"] != "not-a-date"
    serialized = str(projected).lower()
    assert "raw_storage_key" not in serialized
    assert "s3://private/key" not in serialized


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
        "input_keys": ["message"],
        "memory_policy_source": "stored",
        "long_term_memory_read": False,
    }
    assert summary["latest_artifact_version"] is None
    assert summary["execution_tier"] == "sdk_only_writing"
    assert datetime.fromisoformat(summary["context_pack_generated_at"].replace("Z", "+00:00"))
    serialized = str(summary).lower()
    assert "msg-a" not in serialized
    assert "file-a" not in serialized
    assert "mem-a" not in serialized
    assert "private_payload" not in serialized


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
