import json
from datetime import datetime

from app.context_manifest import ContextPlanner
from app.context_manifest import public_context_manifest_projection


def test_context_planner_builds_bounded_manifest_without_large_file_or_private_payload():
    planner = ContextPlanner(max_inline_message_chars=80, recent_message_limit=4, token_budget=320)

    manifest = planner.plan(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        agent_id="general-agent",
        skill_id="general-chat",
        current_message="continue the document review",
        recent_messages=[
            {"id": "msg-1", "role": "user", "content": "short context"},
            {"id": "msg-2", "role": "assistant", "content": "a" * 200},
            {"id": "msg-3", "role": "user", "content": "raw_storage_key=s3://private/object"},
            {"id": "msg-4", "role": "assistant", "content": "latest public answer"},
            {"id": "msg-5", "role": "user", "content": "final short note"},
        ],
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
                "content": "prefer concise answers",
                "status": "active",
                "deleted_at": None,
            },
            {
                "id": "mem-deleted",
                "record_type": "preference",
                "content": "deleted secret",
                "status": "deleted",
                "deleted_at": "2026-07-02T00:00:00Z",
            },
        ],
        source_run_ids=["run-source"],
    )

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
    assert manifest["current_message"] == "continue the document review"
    assert len(manifest["recent_messages"]) == 4
    assert manifest["recent_messages"][-1]["message_id"] == "msg-5"
    assert manifest["recent_messages"][1]["inline_content"] is None
    assert manifest["recent_messages"][1]["summary"]
    assert manifest["files"][0]["inline_preview"] == "tiny note"
    assert manifest["files"][1]["inline_preview"] is None
    assert manifest["artifacts"][0]["artifact_id"] == "artifact-a"
    assert manifest["memory_records"] == [
        {"memory_record_id": "mem-a", "record_type": "preference", "status": "active"}
    ]
    assert manifest["source_runs"] == [{"run_id": "run-source"}]
    assert set(manifest["available_retrieval_tools"]) == {
        "read_session_messages",
        "read_context_file",
        "read_run_artifact",
        "stage_context_file_to_workspace",
        "stage_run_artifact_to_workspace",
        "search_memory",
    }
    assert manifest["budget"]["max_prompt_tokens"] == 320
    assert manifest["redaction"]["private_payloads_removed"] is True
    assert manifest["audit"]["retrieval_required_for_full_content"] is True

    serialized = json.dumps(manifest, ensure_ascii=False)
    assert "storage_key" not in serialized
    assert "s3://private" not in serialized
    assert "tenants/tenant-a/private" not in serialized
    assert "large body must not be in prompt" not in serialized
    assert "deleted secret" not in serialized


def test_executor_context_pack_from_manifest_contains_only_index_and_retrieval_rules():
    planner = ContextPlanner(max_inline_message_chars=40, recent_message_limit=2, token_budget=128)
    manifest = planner.plan(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        agent_id="general-agent",
        skill_id="general-chat",
        current_message="please use the prior file",
        recent_messages=[{"id": "msg-a", "role": "user", "content": "hello"}],
        files=[
            {
                "id": "file-a",
                "original_name": "source.docx",
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "size_bytes": 2048,
                "text_preview": "docx body must be retrieved",
                "storage_key": "tenants/tenant-a/private/source.docx",
            }
        ],
        artifacts=[],
        memory_records=[],
        source_run_ids=[],
    )

    context_pack = planner.executor_context_pack(manifest)

    assert context_pack["schema_version"] == "ai-platform.executor-context-pack.v1"
    assert context_pack["context_manifest"]["schema_version"] == "ai-platform.context-manifest.v1"
    assert context_pack["context_manifest"]["current_message"] == "please use the prior file"
    assert "Use context retrieval tools for full message, file, artifact, or memory content" in context_pack["prompt_summary"]
    serialized = json.dumps(context_pack, ensure_ascii=False)
    assert "storage_key" not in serialized
    assert "source.docx" in serialized
    assert "docx body must be retrieved" not in serialized


def test_context_planner_token_budget_limits_inline_context_material():
    planner = ContextPlanner(max_inline_message_chars=200, recent_message_limit=8, token_budget=18)

    manifest = planner.plan(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        agent_id="general-agent",
        skill_id="general-chat",
        current_message="current turn",
        recent_messages=[
            {"id": "msg-1", "role": "user", "content": "one two three four"},
            {"id": "msg-2", "role": "assistant", "content": "five six seven"},
        ],
        files=[
            {
                "id": "file-a",
                "original_name": "note.txt",
                "content_type": "text/plain",
                "size_bytes": 32,
                "text_preview": "eight nine",
            }
        ],
        artifacts=[],
        memory_records=[],
        source_run_ids=[],
    )

    assert manifest["recent_messages"][0]["inline_content"] is None
    assert manifest["recent_messages"][0]["approx_tokens"] == 18
    assert manifest["recent_messages"][1]["inline_content"] == "five six seven"
    assert manifest["recent_messages"][1]["approx_tokens"] == 14
    assert manifest["files"][0]["inline_preview"] is None
    assert manifest["files"][0]["requires_retrieval"] is True
    assert manifest["budget"]["inline_tokens_used"] == 14
    assert manifest["budget"]["inline_budget_exhausted"] is False
    assert manifest["selection"]["history_trimmed_count"] == 1
    assert manifest["selection"]["selection_order"] == "newest_first"
    assert manifest["selection"]["render_order"] == "chronological"


def test_context_planner_uses_one_conservative_utf8_budget_for_cjk_and_emoji():
    planner = ContextPlanner(
        max_inline_message_chars=20,
        max_inline_message_bytes=12,
        max_inline_history_bytes=12,
        recent_message_limit=8,
        token_budget=12,
    )

    manifest = planner.plan(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-a",
        agent_id="general-agent",
        skill_id="general-chat",
        current_message="继续",
        recent_messages=[
            {"id": "msg-cjk", "role": "user", "content": "你好世界"},
            {"id": "msg-emoji", "role": "assistant", "content": "🧪"},
        ],
    )

    assert manifest["recent_messages"][0]["inline_content"] is None
    assert manifest["recent_messages"][0]["approx_tokens"] == 12
    assert manifest["recent_messages"][1]["inline_content"] == "🧪"
    assert manifest["budget"]["inline_tokens_used"] == 4
    assert manifest["budget"]["inline_budget_exhausted"] is False
    assert manifest["selection"]["history_trimmed_count"] == 1


def test_public_context_manifest_projection_exposes_only_counts_flags_and_valid_timestamp():
    projection = public_context_manifest_projection(
        {
            "context_manifest_version": "v1",
            "generated_at": "not-a-timestamp read_context_file secret",
            "recent_messages": [{"message_id": "msg-secret", "content": "secret body"}],
            "files": [{"file_id": "file-secret", "original_name": "source.txt"}],
            "artifacts": [{"artifact_id": "artifact-secret"}],
            "memory_records": [{"memory_record_id": "mem-secret"}],
            "source_runs": [{"run_id": "run-secret"}],
            "available_retrieval_tools": ["read_context_file", "stage_context_file_to_workspace"],
        }
    )

    assert projection["referenced_materials"] == {
        "message_count": 1,
        "file_count": 1,
        "artifact_count": 1,
        "memory_record_count": 1,
        "source_run_count": 1,
    }
    assert projection["retrieval"] == {
        "available": True,
        "tool_count": 2,
        "workspace_staging_available": True,
    }
    datetime.fromisoformat(projection["generated_at"].replace("Z", "+00:00"))
    assert projection["generated_at"] != "not-a-timestamp read_context_file secret"

    serialized = json.dumps(projection, ensure_ascii=False)
    assert "available_retrieval_tools" not in serialized
    assert "read_context_file" not in serialized
    assert "stage_context_file_to_workspace" not in serialized
    assert "msg-secret" not in serialized
    assert "file-secret" not in serialized
    assert "artifact-secret" not in serialized


def test_context_planner_marks_legacy_history_degraded_without_exposing_ids_or_prompt_text():
    planner = ContextPlanner(max_inline_message_chars=80, token_budget=80)
    manifest = planner.plan(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        session_id="session-a",
        run_id="run-current",
        agent_id="general-agent",
        skill_id="general-chat",
        current_message="current input appears only here",
        recent_messages=[
            {
                "id": "msg-prior",
                "session_generation": 3,
                "created_at": "2026-07-19T00:00:01Z",
                "role": "user",
                "content": "prior context",
            }
        ],
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

    assert manifest["current_message"] == "current input appears only here"
    assert manifest["selection"]["status"] == "degraded"
    assert projection["context_window"] == {
        "status": "degraded",
        "selection_version": "session-context-v1",
        "history_candidate_count": 1,
        "history_inline_count": 1,
        "history_trimmed_count": 0,
        "legacy_history_excluded": True,
        "selected_file_names": ["accepted-report.txt"],
    }
    serialized = json.dumps(projection, ensure_ascii=False)
    assert "file-private" not in serialized
    assert "current input appears only here" not in serialized
