from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.auth import AuthPrincipal
from app.context.api import CONTEXT_FILE_ERROR_CODES
from app.run_projection import (
    PublicChatAnswerStreamProjector,
    artifact_card,
    progress_for_status,
    public_chat_answer_text,
    public_chat_terminal_projection,
    public_terminal_projection,
    run_event_response,
    run_step_response,
)
from app.runtime.event_bridge import agent_event_to_executor_event
from app.runtime.kernel_contracts import AgentEvent


def principal(**overrides):
    values = {
        "user_id": "user-a",
        "display_name": "User A",
        "tenant_id": "tenant-a",
    }
    values.update(overrides)
    return AuthPrincipal(**values)


def event_row(event_type: str, *, payload_json=None, sequence: int = 1):
    return {
        "id": f"evt-{sequence}",
        "trace_id": "trace_run_a",
        "schema_version": "ai-platform.event-envelope.v1",
        "sequence": sequence,
        "event_type": event_type,
        "stage": "executor-private-stage",
        "message": "executor-private-message",
        "severity": "info",
        "visible_to_user": True,
        "error_code": None,
        "latency_ms": None,
        "input_token_count": 0,
        "output_token_count": 0,
        "total_token_count": 0,
        "estimated_cost_minor": 0,
        "payload_json": payload_json or {},
        "created_at": None,
    }


def test_projection_module_owns_run_progress_event_step_and_artifact_cards():
    assert progress_for_status("canceled") == 100

    event = run_event_response(
        "run-a",
        {
            "id": "evt-a",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.event-envelope.v1",
            "sequence": 3,
            "event_type": "run_multi_agent_child_created",
            "stage": "control",
            "message": "child created",
            "severity": "info",
            "visible_to_user": True,
            "error_code": None,
            "latency_ms": 7,
            "input_token_count": 1,
            "output_token_count": 2,
            "total_token_count": 3,
            "estimated_cost_minor": 4,
            "payload_json": {
                "visible_to_user": True,
                "step_key": "review",
                "dispatch_id": "dispatch-private",
                "parent_run_id": "run-parent",
            },
            "created_at": None,
        },
        principal=principal(),
    )
    assert event["event_type"] == "run_child_created"
    assert event["stage"] == "status"
    assert event["message"] == "已安排协同任务。"
    assert event["payload"] == {"activity": {"category": "status", "status": "running"}}
    assert "review" not in str(event)
    assert "dispatch-private" not in str(event)
    assert "run-parent" not in str(event)

    step = run_step_response(
        {
            "id": "step-a",
            "run_id": "run-a",
            "step_key": "review",
            "step_kind": "agent",
            "status": "canceled",
            "title": "Review",
            "role": "reviewer",
            "sequence": 1,
            "payload_json": {
                "public_note": "ok",
                "dispatch_id": "dispatch-private",
                "resource_limits": {"max_seconds": 30},
            },
            "started_at": None,
            "finished_at": None,
            "created_at": None,
            "updated_at": None,
        },
        principal=principal(),
    )
    assert step["status"] == "cancelled"
    assert step["id"] == "step-a"
    assert step["step_key"] == "step-a"
    assert step["title"] == "步骤已取消"
    assert step["role"] is None
    assert step["payload"] == {}
    assert "public_note" not in str(step)
    assert "review" not in str(step)
    assert "resource_limits" not in step

    card = artifact_card(
        {
            "id": "artifact-a",
            "artifact_type": "reviewed_docx",
            "label": "reviewed.docx",
            "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "storage_key": "tenants/private/reviewed.docx",
            "size_bytes": 12,
            "manifest_version": "ai-platform.artifact-manifest.v1",
            "manifest_json": {
                "source_run_id": "run-private",
                "source_file_id": "file-a",
                "storage_key": "tenants/private/manifest.json",
            },
            "created_at": None,
        },
        principal=principal(),
    )
    assert card["preview_url"] == "/api/ai/artifacts/artifact-a/preview"
    assert card["label"] == "reviewed_docx"
    assert card["lineage"] == {}
    assert card["manifest"] == {}
    assert "source_run_id" not in str(card)
    assert "source_file_id" not in str(card)
    assert "storage_key" not in str(card)

    admin_step = run_step_response(
        {
            "id": "step-a",
            "run_id": "run-a",
            "step_key": "review",
            "step_kind": "agent",
            "status": "canceled",
            "title": "Review",
            "role": "reviewer",
            "sequence": 1,
            "payload_json": {"public_note": "ok", "dispatch_id": "dispatch-private"},
            "started_at": None,
            "finished_at": None,
            "created_at": None,
            "updated_at": None,
        },
        principal=principal(roles=["admin"]),
    )
    assert admin_step["step_key"] == "review"
    assert admin_step["role"] == "reviewer"
    assert admin_step["payload"] == {"public_note": "ok", "dispatch_id": "dispatch-private"}


def test_artifact_card_uses_stored_filename_for_xlsx_preview_eligibility():
    valid = artifact_card(
        {
            "id": "artifact-valid",
            "artifact_type": "spreadsheet",
            "label": "misleading.xlsm",
            "storage_key": "private/export.xlsx",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "manifest_json": {},
            "created_at": None,
        }
    )
    legacy = artifact_card(
        {
            "id": "artifact-legacy",
            "artifact_type": "spreadsheet",
            "label": "misleading.xlsx",
            "storage_key": "private/export.xlsm",
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "manifest_json": {},
            "created_at": None,
        }
    )

    assert valid["preview_url"] == "/api/ai/artifacts/artifact-valid/preview"
    assert legacy["preview_url"] is None


def test_projection_keeps_terminal_tool_permission_events_as_fixed_activity():
    event = run_event_response(
        "run-a",
        {
            "id": "evt-terminal-permission",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.event-envelope.v1",
            "sequence": 9,
            "event_type": "tool_permission_terminalized",
            "stage": "tool_policy",
            "message": "工具权限请求已终结",
            "severity": "info",
            "visible_to_user": True,
            "error_code": None,
            "latency_ms": None,
            "input_token_count": 0,
            "output_token_count": 0,
            "total_token_count": 0,
            "estimated_cost_minor": 0,
            "payload_json": {
                "visible_to_user": True,
                "permission_request_id": "tpr-terminal",
                "tool_id": "Bash",
                "tool_call_id": "call-terminal",
                "action": "execute",
                "risk_level": "high",
                "write_capable": True,
                "status": "cancelled",
                "reason": "run_cancel_requested",
                "decision_endpoint": "/api/ai/runs/run-a/tool-permissions/tpr-terminal/decision",
                "decision_options": ["allow_once", "allow_for_run", "deny"],
            },
            "created_at": None,
        },
        principal=principal(),
    )

    assert event["event_type"] == "tool_permission_card"
    assert event["stage"] == "policy"
    assert event["message"] == "权限请求已结束。"
    assert event["payload"] == {"activity": {"category": "policy", "status": "completed"}}
    assert "tool_permission_card" not in str(event["payload"])
    assert "tpr-terminal" not in str(event)
    assert "decision_endpoint" not in str(event)


def test_required_capability_terminal_projection_is_stable_for_users_and_admins():
    ordinary = public_terminal_projection("failed", "required_tool_unavailable")
    admin = public_terminal_projection("failed", "required_tool_completion_evidence_missing")

    assert ordinary["detail_code"] == "required_capability_unavailable"
    assert admin["detail_code"] == "required_capability_unavailable"
    assert ordinary["message"] == admin["message"]
    assert "Bash" not in str(ordinary)


def test_tool_evidence_terminal_projection_preserves_safe_code_without_private_detail():
    projection = public_terminal_projection("failed", "tool_invocation_evidence_mismatch")

    assert projection["detail_code"] == "tool_invocation_evidence_mismatch"
    assert projection["error_code"] == "tool_invocation_evidence_mismatch"
    assert "tool_invocation_evidence_mismatch" in projection["message"]
    assert "/workspace/" not in str(projection)
    assert "token" not in str(projection)


def test_context_file_size_terminal_projection_is_specific_and_safe():
    projection = public_terminal_projection("failed", "context_file_too_large")

    assert projection["detail_code"] == "context_file_too_large"
    assert projection["error_code"] == "context_file_too_large"
    assert projection["message"] == "文件超过 32 MB 处理上限。请选择更小的文件后重试。"
    assert projection["event_payload"] == {}


@pytest.mark.parametrize(
    ("error_code", "detail_code", "message_fragment"),
    [
        (
            "context_file_pdf_password_required",
            "context_file_pdf_password_required",
            "需要密码",
        ),
        (
            "context_file_pdf_active_content_unsupported",
            "context_file_unsafe_content",
            "活动内容",
        ),
        (
            "context_file_pdf_parse_failed",
            "context_file_invalid",
            "无法解析",
        ),
        (
            "context_file_identity_mismatch",
            "context_file_identity_mismatch",
            "完整性校验失败",
        ),
    ],
)
def test_context_file_terminal_projection_is_actionable_and_private_safe(
    error_code,
    detail_code,
    message_fragment,
):
    projection = public_terminal_projection("failed", error_code)

    assert projection["detail_code"] == detail_code
    assert message_fragment in projection["message"]
    assert projection["event_payload"] == {}
    assert "attachment_index" not in str(projection)
    assert "exception_chain" not in str(projection)


@pytest.mark.parametrize("error_code", sorted(CONTEXT_FILE_ERROR_CODES))
def test_all_context_file_failure_codes_have_actionable_public_projection(error_code):
    projection = public_terminal_projection("failed", error_code)

    assert projection["detail_code"] != "run_failed"
    assert projection["event_payload"] == {}


def test_public_chat_terminal_projection_owns_versioned_terminal_payloads():
    succeeded = public_chat_terminal_projection(
        {
            "id": "run-a",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "status": "succeeded",
            "result_json": {"message": "当前（general-chat），没有 Bash 工具，无法执行。"},
        }
    )
    failed = public_chat_terminal_projection(
        {
            "id": "run-b",
            "status": "failed",
            "error_code": "required_tool_unavailable",
        }
    )

    assert succeeded == {
        "event_type": "message:chunk",
        "payload": {
            "projection_version": "ai-platform.chat-public-projection.v1",
            "projection_kind": "assistant_final",
            "content": "当前（general-agent），没有 Bash 工具，无法执行。",
        },
        "message": "当前（general-agent），没有 Bash 工具，无法执行。",
        "event_payload": {},
        "severity": "info",
    }
    assert failed is not None
    assert failed["event_type"] == "final_detail"
    assert failed["payload"] == {
        "projection_version": "ai-platform.chat-public-projection.v1",
        "detail_kind": "failed",
        "detail_code": "required_capability_unavailable",
        "message": public_terminal_projection("failed", "required_tool_unavailable")["message"],
    }
    assert failed["event_payload"] == {"detail_code": "required_capability_unavailable"}
    assert failed["severity"] == "error"


@pytest.mark.parametrize(
    ("event_type", "projected_type", "stage", "message", "status"),
    [
        (
            "intent_detected",
            "intent_detected",
            "preparation",
            "正在准备受控运行请求。",
            "running",
        ),
        (
            "skill_selected",
            "capability_selected",
            "capability",
            "已加载授权处理能力。",
            "completed",
        ),
    ],
)
def test_public_progress_projection_uses_fixed_safe_details(
    event_type,
    projected_type,
    stage,
    message,
    status,
):
    forbidden = (
        "raw prompt amber-lantern",
        "powershell -Command private",
        "C:\\private\\runtime",
        "secret-token-value",
    )
    event = run_event_response(
        "run-a",
        event_row(
            event_type,
            payload_json={
                "prompt": forbidden[0],
                "command": forbidden[1],
                "runtime_path": forbidden[2],
                "private_payload": {"token": forbidden[3]},
                "visible_to_user": True,
            },
        ),
        principal=principal(),
    )

    assert event["event_type"] == projected_type
    assert event["stage"] == stage
    assert event["message"] == message
    assert event["payload"] == {"activity": {"category": stage, "status": status}}
    assert all(term not in str(event) for term in forbidden)


def test_run_started_heartbeat_is_liveness_not_meaningful_progress():
    heartbeat = run_event_response(
        "run-a",
        event_row("run_started", payload_json={"heartbeat": True, "visible_to_user": True}),
        principal=principal(),
    )
    meaningful_start = run_event_response(
        "run-a",
        event_row("run_started", payload_json={"heartbeat": False, "visible_to_user": True}, sequence=2),
        principal=principal(),
    )

    assert heartbeat["event_type"] == "heartbeat"
    assert heartbeat["stage"] == "liveness"
    assert heartbeat["message"] == "任务仍在运行。"
    assert heartbeat["payload"] == {
        "activity": {"category": "liveness", "status": "running", "meaningful": False}
    }
    assert meaningful_start["event_type"] == "run_started"
    assert meaningful_start["stage"] == "execution"
    assert meaningful_start["payload"] == {
        "activity": {"category": "execution", "status": "running"}
    }


def test_event_bridge_maps_valid_events_and_fails_closed_for_admin_and_unknown_events():
    progress = AgentEvent(
        type="run_started",
        message="Runtime started",
        payload={"visible_to_user": True},
        admin_only=False,
    )
    private = AgentEvent(
        type="runtime_container_started",
        message="Sandbox executor container started",
        payload={"container_id": "private-container"},
        admin_only=True,
    )
    unknown = SimpleNamespace(
        type="future_executor_private_event",
        message="secret executor message",
        payload={"runtime_path": "C:\\private\\runtime", "token": "secret-token"},
        admin_only=False,
    )

    assert agent_event_to_executor_event(progress) == {
        "event_type": "run_started",
        "stage": "runtime",
        "message": "Runtime started",
        "payload": {"visible_to_user": True},
    }
    assert agent_event_to_executor_event(private)["payload"] == {
        "container_id": "private-container",
        "visible_to_user": False,
        "admin_only": True,
    }
    assert agent_event_to_executor_event(unknown) == {  # type: ignore[arg-type]
        "event_type": "executor_private_event",
        "stage": "runtime",
        "message": "",
        "payload": {"visible_to_user": False, "admin_only": True},
    }


@pytest.mark.parametrize("event_type", ["run_failed", "run_cancelled"])
def test_projection_preserves_durable_terminalization_observability(event_type):
    event = run_event_response(
        "run-a",
        {
            "id": f"evt-{event_type}",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.event-envelope.v1",
            "sequence": 51,
            "event_type": event_type,
            "stage": "worker" if event_type == "run_failed" else "control",
            "message": "Run failed" if event_type == "run_failed" else "任务已取消",
            "severity": "error" if event_type == "run_failed" else "warning",
            "visible_to_user": True,
            "error_code": "executor_failure" if event_type == "run_failed" else None,
            "latency_ms": 17,
            "input_token_count": 3,
            "output_token_count": 5,
            "total_token_count": 8,
            "estimated_cost_minor": 11,
            "payload_json": {
                "visible_to_user": True,
                "artifact_count": 2,
                "result_status": "failed" if event_type == "run_failed" else "cancelled",
                "result": {"message": "safe durable result"},
                "error_message": "safe terminal error" if event_type == "run_failed" else None,
            },
            "created_at": None,
        },
        principal=principal(),
    )

    assert event["latency_ms"] == 17
    assert event["token_counts"] == {"input": 3, "output": 5, "total": 8}
    assert event["cost"] == {"estimated_cost_minor": 11}
    assert event["payload"] == {}
    if event_type == "run_failed":
        assert event["event_type"] == "error"
        assert event["error_code"] == "run_failed"
        assert event["message"] == "任务未能完成。请稍后重试；如问题持续，请联系管理员。"
    else:
        assert event["event_type"] == "run_cancelled"
        assert event["error_code"] is None
        assert event["message"] == "任务已取消。取消前已产生的公开内容仍会保留。"
    assert "safe durable result" not in str(event)
    assert "safe terminal error" not in str(event)

    admin_event = run_event_response(
        "run-a",
        {
            "id": f"admin-{event_type}",
            "trace_id": "trace_run_a",
            "schema_version": "ai-platform.event-envelope.v1",
            "sequence": 51,
            "event_type": event_type,
            "stage": "worker",
            "message": "Run failed" if event_type == "run_failed" else "任务已取消",
            "severity": "error",
            "visible_to_user": True,
            "error_code": "executor_failure" if event_type == "run_failed" else None,
            "latency_ms": 17,
            "input_token_count": 3,
            "output_token_count": 5,
            "total_token_count": 8,
            "estimated_cost_minor": 11,
            "payload_json": {
                "visible_to_user": True,
                "artifact_count": 2,
                "result": {"message": "safe durable result"},
                "error_message": "safe terminal error" if event_type == "run_failed" else None,
            },
            "created_at": None,
        },
        principal=principal(roles=["admin"]),
    )
    assert admin_event["payload"]["result"] == {"message": "safe durable result"}
    if event_type == "run_failed":
        assert admin_event["payload"]["error_message"] == "safe terminal error"


def test_projection_module_rejects_invalid_event_schema_version():
    with pytest.raises(HTTPException) as exc_info:
        run_event_response(
            "run-a",
            {
                "id": "evt-a",
                "trace_id": "trace_run_a",
                "event_type": "queued",
                "stage": "queue",
                "message": "queued",
                "payload_json": {},
                "created_at": None,
            },
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "invalid_event_schema_version"


def test_run_projection_returns_only_the_public_execution_event_v1_shape():
    event = run_event_response(
        "run-a",
        event_row(
            "execution_step",
            payload_json={
                "step_id": "step-opaque-a",
                "kind": "processing",
                "stage": "execution",
                "status": "running",
                "title": "Document review",
                "summary": "Processing",
                "progress": {"current": 1, "total": 4},
            },
        ),
        principal=principal(),
    )

    assert event["schema_version"] == "ai-platform.public-execution-event.v1"
    assert set(event) <= {
        "schema_version",
        "event_id",
        "sequence",
        "run_id",
        "step_id",
        "kind",
        "stage",
        "status",
        "title",
        "summary",
        "progress",
        "safe_file_name",
        "artifact_public_id",
        "created_at",
    }

    raw = run_event_response(
        "run-a",
        event_row(
            "tool_call_started",
            payload_json={
                "command": "powershell -Command private-token",
                "private_payload": {"path": "C:\\private\\workspace"},
            },
            sequence=2,
        ),
        principal=principal(),
    )
    assert raw["schema_version"] != "ai-platform.public-execution-event.v1"
    assert raw.get("kind") not in {"execution_step", "execution_progress", "execution_step_completed"}


def test_public_chat_answer_text_retains_answer_when_capabilities_differ():
    run = {
        "id": "run-a",
        "skill_id": "general-chat",
        "agent_id": "qa-word-review",
        "status": "succeeded",
    }
    content = public_chat_answer_text(
        run,
        "使用了 general-chat 完成审阅，样本 q 数据已归档。",
    )

    assert content == "使用了 general-agent 完成审阅，样本 q 数据已归档。"
    assert "general-chat" not in content
    assert "qa-word-review" not in content


def test_successful_terminal_projection_without_answer_is_result_unavailable():
    projection = public_chat_terminal_projection(
        {
            "id": "run-a",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "status": "succeeded",
            "result_json": {"message": ""},
        }
    )

    assert projection is not None
    assert projection["event_type"] == "final_detail"
    assert projection["payload"]["detail_kind"] == "result_unavailable"
    assert projection["payload"]["detail_code"] == "result_unavailable"
    assert projection["payload"]["message"] == "本次执行未能生成可展示的回复内容。"
    assert projection["severity"] == "info"
    assert "任务完成" not in str(projection)


def test_live_delta_and_terminal_final_converge_to_same_public_text():
    run = {
        "id": "run-a",
        "agent_id": "qa-word-review",
        "skill_id": "general-chat",
        "status": "succeeded",
    }
    full_answer = "general-chat 已处理该文档，qa-word-review 审核通过。"
    delta_fragment = "general-chat 已处理"
    delta_tail = "该文档，qa-word-review 审核通过。"

    final = public_chat_terminal_projection(
        {**run, "result_json": {"message": full_answer}}
    )
    assert final is not None
    assert final["event_type"] == "message:chunk"
    assert final["payload"]["projection_kind"] == "assistant_final"

    assembled = (
        public_chat_answer_text(run, delta_fragment)
        + public_chat_answer_text(run, delta_tail)
    )
    assert assembled == final["payload"]["content"]
    assert "general-chat" not in final["payload"]["content"]
    assert "qa-word-review" not in final["payload"]["content"]
    assert "general-agent" in final["payload"]["content"]
    assert "document-review" in final["payload"]["content"]


def test_stream_projector_withholds_identifier_until_split_token_is_complete():
    run = {
        "id": "run-a",
        "agent_id": "qa-word-review",
        "skill_id": "general-chat",
        "status": "running",
    }
    projector = PublicChatAnswerStreamProjector(run)

    chunks = [
        projector.push("已开始处理，general-"),
        projector.push("chat 已完成，qa-word-"),
        projector.push("review 审核通过。"),
    ]
    streamed = "".join(chunks)

    assert streamed == "已开始处理，general-agent 已完成，document-review 审核通过。"
    assert "general-chat" not in streamed
    assert "qa-word-review" not in streamed
    assert projector.flush() == ""


@pytest.mark.parametrize(
    ("secret_text", "split"),
    [
        ("api_key=sk-abcdefghi12", 9),
        ("Bearer abcdefgh1", 7),
        ("abcdefghij.klmnopqrst.uvwxyzabcd", 21),
    ],
)
def test_stream_projector_matches_terminal_projection_at_secret_split_boundaries(
    secret_text, split
):
    run = {
        "id": "run-secret-parity",
        "agent_id": "general-agent",
        "skill_id": "general-chat",
        "status": "running",
    }
    projector = PublicChatAnswerStreamProjector(run)
    full_answer = f"ordinary {secret_text} after"

    streamed = "".join(
        (
            projector.push(full_answer[: len("ordinary ") + split]),
            projector.push(full_answer[len("ordinary ") + split :]),
            projector.flush(),
        )
    )
    terminal = public_chat_terminal_projection(
        {**run, "status": "succeeded", "result_json": {"message": full_answer}}
    )

    assert terminal is not None
    assert streamed == terminal["payload"]["content"]
    assert secret_text not in streamed
    assert "ordinary" in streamed




@pytest.mark.parametrize(
    "secret_text",
    [
        'client_secret="opaque12345"',
        "api-key='opaque12345'",
        "access_token=opaque12345",
        'authorization: "opaque12345"',
        "Bearer abcdefgh1",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature12345",
    ],
)
def test_stream_projector_matches_terminal_for_every_secret_split(secret_text):
    run = {
        "id": "run-secret-every-split",
        "agent_id": "general-agent",
        "skill_id": "general-chat",
        "status": "running",
    }
    for split in range(1, len(secret_text)):
        projector = PublicChatAnswerStreamProjector(run)
        full_answer = f"ordinary {secret_text} after"
        prefix_length = len("ordinary ") + split
        streamed = "".join(
            (
                projector.push(full_answer[:prefix_length]),
                projector.push(full_answer[prefix_length:]),
                projector.flush(),
            )
        )
        terminal = public_chat_terminal_projection(
            {**run, "status": "succeeded", "result_json": {"message": full_answer}}
        )
        assert terminal is not None
        assert streamed == terminal["payload"]["content"]
        assert secret_text not in streamed


def test_stream_projector_blocks_a_forbidden_marker_split_across_chunks():
    run = {
        "id": "run-a",
        "agent_id": "general-agent",
        "skill_id": "general-chat",
        "status": "running",
    }
    projector = PublicChatAnswerStreamProjector(run)

    safe_prefix = projector.push("已生成安全摘要。 /va")
    blocked_suffix = projector.push("r/private/result.txt")

    assert safe_prefix == ""
    assert blocked_suffix == ""
    assert projector.flush() == ""
