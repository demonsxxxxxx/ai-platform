import json
from datetime import datetime

from app.context_manifest import (
    ContextPlanner,
    available_context_retrieval_tools,
    public_context_manifest_projection,
)


def test_context_planner_builds_non_conversation_manifest_without_private_payload():
    planner = ContextPlanner(max_inline_file_preview_chars=80, token_budget=320)

    manifest = planner.plan(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        agent_id="general-agent",
        skill_id="general-chat",
        history_candidate_count=12,
        history_authorized_count=8,
        context_chips=["needs citations", "storage_key=tenants/private/file.docx"],
        files=[
            {
                "id": "file-small",
                "original_name": "brief.txt",
                "content_type": "text/plain",
                "size_bytes": 12,
                "text_preview": "tiny note",
                "storage_key": "tenants/tenant-a/private/brief.txt",
            },
            {
                "id": "file-large",
                "original_name": "large.pdf",
                "content_type": "application/pdf",
                "size_bytes": 10_000_000,
                "text_preview": "large body must not be in prompt",
                "storage_key": "tenants/tenant-a/private/large.pdf",
            },
        ],
        artifacts=[
            {
                "id": "artifact-a",
                "run_id": "run-source",
                "artifact_type": "reviewed_docx",
                "label": "reviewed.docx",
                "size_bytes": 4096,
                "storage_key": "tenants/tenant-a/private/reviewed.docx",
            }
        ],
        memory_records=[
            {
                "id": "mem-a",
                "record_type": "preference",
                "status": "active",
                "deleted_at": None,
            },
            {
                "id": "mem-deleted",
                "record_type": "preference",
                "status": "deleted",
                "deleted_at": "2026-07-02T00:00:00Z",
            },
        ],
        source_run_ids=["run-source"],
    )

    assert manifest["schema_version"] == "ai-platform.context-manifest.v1"
    assert "current_message" not in manifest
    assert "recent_messages" not in manifest
    assert manifest["selection"] == {
        "selection_version": "conversation-turns-v1",
        "status": "trimmed",
        "history_candidate_count": 12,
        "history_authorized_count": 8,
        "history_omitted_count": 4,
        "legacy_history_excluded": False,
    }
    assert manifest["files"][0]["inline_preview"] == "tiny note"
    assert manifest["files"][1]["inline_preview"] is None
    assert manifest["artifacts"][0]["artifact_id"] == "artifact-a"
    assert manifest["memory_records"] == [
        {"memory_record_id": "mem-a", "record_type": "preference", "status": "active"}
    ]
    assert set(available_context_retrieval_tools(manifest)) == {
        "read_session_messages",
        "read_run_artifact",
        "stage_context_file_to_workspace",
        "stage_run_artifact_to_workspace",
        "search_memory",
    }

    serialized = json.dumps(manifest, ensure_ascii=False)
    assert "storage_key" not in serialized
    assert "tenants/tenant-a/private" not in serialized
    assert "large body must not be in prompt" not in serialized


def test_executor_context_pack_counts_authorized_history_without_message_refs():
    planner = ContextPlanner(token_budget=128)
    manifest = planner.plan(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        agent_id="general-agent",
        skill_id="general-chat",
        history_candidate_count=3,
        history_authorized_count=3,
        files=[
            {
                "id": "file-a",
                "original_name": "source.docx",
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "size_bytes": 2048,
                "text_preview": "docx body must be retrieved",
            }
        ],
    )

    context_pack = planner.executor_context_pack(manifest)

    assert context_pack["referenced_materials"]["message_count"] == 3
    assert "Recent conversation text is supplied separately" in context_pack["prompt_summary"]
    serialized = json.dumps(context_pack, ensure_ascii=False)
    assert "message_id" not in serialized
    assert "docx body must be retrieved" not in serialized


def test_context_planner_budget_applies_only_to_inline_non_conversation_material():
    planner = ContextPlanner(max_inline_file_preview_chars=200, token_budget=4)

    manifest = planner.plan(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        agent_id="general-agent",
        skill_id="general-chat",
        files=[
            {
                "id": "file-a",
                "original_name": "note.txt",
                "content_type": "text/plain",
                "size_bytes": 32,
                "text_preview": "eight nine",
            }
        ],
    )

    assert manifest["files"][0]["inline_preview"] is None
    assert manifest["files"][0]["requires_retrieval"] is True
    assert manifest["budget"] == {
        "max_inline_material_tokens": 4,
        "max_inline_file_bytes": 8192,
        "inline_tokens_used": 0,
        "inline_budget_exhausted": True,
    }


def test_public_context_manifest_projection_exposes_only_counts_and_flags():
    projection = public_context_manifest_projection(
        {
            "context_manifest_version": "v1",
            "generated_at": "not-a-timestamp opaque secret",
            "selection": {
                "selection_version": "conversation-turns-v1",
                "status": "trimmed",
                "history_candidate_count": 9,
                "history_authorized_count": 8,
                "history_omitted_count": 1,
            },
            "files": [{"file_id": "file-secret", "original_name": "source.txt"}],
            "artifacts": [{"artifact_id": "artifact-secret"}],
            "memory_records": [{"memory_record_id": "mem-secret"}],
            "source_runs": [{"run_id": "run-secret"}],
            "available_retrieval_tools": ["read_session_messages", "stage_context_file_to_workspace"],
        }
    )

    assert projection["referenced_materials"] == {
        "message_count": 8,
        "file_count": 1,
        "artifact_count": 1,
        "memory_record_count": 1,
        "source_run_count": 1,
    }
    assert projection["context_window"]["history_omitted_count"] == 1
    datetime.fromisoformat(projection["generated_at"].replace("Z", "+00:00"))
    serialized = json.dumps(projection, ensure_ascii=False)
    assert "available_retrieval_tools" not in serialized
    assert "file-secret" not in serialized
    assert "artifact-secret" not in serialized


def test_context_planner_marks_legacy_history_degraded_without_message_data():
    manifest = ContextPlanner().plan(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-current",
        agent_id="general-agent",
        skill_id="general-chat",
        history_candidate_count=1,
        history_authorized_count=1,
        files=[
            {
                "id": "file-private",
                "original_name": "accepted-report.txt",
                "content_type": "text/plain",
                "size_bytes": 4,
            }
        ],
        legacy_history_excluded=True,
    )

    projection = public_context_manifest_projection(manifest)

    assert manifest["selection"]["status"] == "degraded"
    assert projection["context_window"] == {
        "status": "degraded",
        "selection_version": "conversation-turns-v1",
        "history_candidate_count": 1,
        "history_authorized_count": 1,
        "history_omitted_count": 0,
        "legacy_history_excluded": True,
        "selected_file_names": ["accepted-report.txt"],
    }
    assert "file-private" not in json.dumps(projection, ensure_ascii=False)
