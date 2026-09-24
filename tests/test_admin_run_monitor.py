from app.runs.application.admin_run_monitor import build_admin_worker_execution


def test_worker_execution_projects_only_safe_effective_information():
    events = [
        {
            "event_id": "old-delta",
            "sequence": 1,
            "type": "message.delta",
            "visible_to_user": True,
            "payload": {"delta": "旧返回", "__stream_v4": {"message_id": "msg-old"}},
        },
        {
            "event_id": "new-delta",
            "sequence": 10,
            "type": "message.delta",
            "visible_to_user": True,
            "payload": {
                "delta": "有效返回 /secret",
                "__stream_v4": {"message_id": "msg-new"},
            },
        },
        {
            "event_id": "tool-start",
            "sequence": 20,
            "type": "tool.started",
            "visible_to_user": True,
            "created_at": "2026-09-18T08:00:00Z",
            "payload": {
                "operation_id": "private-operation-id",
                "display_name": "Read",
                "category": "read",
                "input_summary": "读取 /secret",
            },
        },
        {
            "event_id": "tool-complete",
            "sequence": 21,
            "type": "tool.completed",
            "visible_to_user": True,
            "created_at": "2026-09-18T08:00:01Z",
            "payload": {
                "operation_id": "private-operation-id",
                "display_name": "Read",
                "category": "read",
                "duration_ms": 1000,
                "result_summary": "读取 3 个文件",
            },
        },
        {
            "event_id": "tool-failed",
            "sequence": 30,
            "type": "tool.failed",
            "visible_to_user": True,
            "payload": {
                "operation_id": "failed-operation-id",
                "display_name": "Bash",
                "failure_category": "command_failed",
            },
        },
        {
            "event_id": "private-tool",
            "sequence": 31,
            "type": "tool.completed",
            "visible_to_user": False,
            "payload": {
                "operation_id": "hidden-operation-id",
                "display_name": "PrivateTool",
                "result_summary": "private result",
            },
        },
        {
            "event_id": "model-complete",
            "sequence": 40,
            "type": "model.completed",
            "visible_to_user": True,
            "payload": {
                "turn_count": 5,
                "duration_ms": 190737,
                "stop_category": "completed",
            },
        },
    ]

    projected = build_admin_worker_execution(
        events,
        sanitize_text=lambda value: value.replace("/secret", "[redacted]"),
    )

    assert projected == {
        "response": "有效返回 [redacted]",
        "messages": [
            {
                "ordinal": 1,
                "kind": "answer",
                "text": "旧返回",
                "sequence": 1,
                "created_at": None,
            },
            {
                "ordinal": 2,
                "kind": "answer",
                "text": "有效返回 [redacted]",
                "sequence": 10,
                "created_at": None,
            },
        ],
        "actions": [
            {
                "ordinal": 1,
                "sequence": 20,
                "invocation_id": "private-operation-id",
                "label": "Read",
                "category": "read",
                "status": "succeeded",
                "input_summary": "读取 [redacted]",
                "result_summary": "读取 3 个文件",
                "duration_ms": 1000,
                "started_at": "2026-09-18T08:00:00Z",
                "finished_at": "2026-09-18T08:00:01Z",
            },
            {
                "ordinal": 2,
                "sequence": 30,
                "invocation_id": "failed-operation-id",
                "label": "Bash",
                "category": "",
                "status": "failed",
                "input_summary": "",
                "result_summary": "command_failed",
                "duration_ms": None,
                "started_at": None,
                "finished_at": None,
            },
        ],
        "model": {
            "turn_count": 5,
            "duration_ms": 190737,
            "stop_category": "completed",
        },
    }
    assert "hidden-operation-id" not in repr(projected)
    assert "PrivateTool" not in repr(projected)


def test_worker_execution_redacts_complete_public_messages_and_omits_private_output():
    events = [
        {
            "sequence": 1,
            "type": "commentary.delta",
            "visible_to_user": True,
            "payload": {
                "summary_id": "summary-a",
                "delta": "正在检查 tok",
                "__stream_v4": {"message_id": "comment-a"},
            },
        },
        {
            "sequence": 2,
            "type": "commentary.delta",
            "visible_to_user": True,
            "payload": {
                "summary_id": "summary-a",
                "delta": "en",
                "__stream_v4": {"message_id": "comment-a"},
            },
        },
        {
            "sequence": 3,
            "type": "commentary.delta",
            "visible_to_user": True,
            "payload": {
                "summary_id": "summary-b",
                "delta": "下一步检查工具回执",
                "__stream_v4": {"message_id": "comment-a"},
            },
        },
        {
            "sequence": 4,
            "type": "message.delta",
            "visible_to_user": False,
            "payload": {"delta": "PRIVATE_OUTPUT", "__stream_v4": {"message_id": "hidden"}},
        },
        {
            "sequence": 5,
            "type": "message.delta",
            "visible_to_user": True,
            "payload": {"delta": "结果 tok", "__stream_v4": {"message_id": "answer-a"}},
        },
        {
            "sequence": 6,
            "type": "message.delta",
            "visible_to_user": True,
            "payload": {"delta": "en", "__stream_v4": {"message_id": "answer-a"}},
        },
    ]
    projected = build_admin_worker_execution(
        events,
        sanitize_text=lambda value: value.replace("token", "[redacted]"),
    )

    assert projected["messages"] == [
        {
            "ordinal": 1,
            "kind": "commentary",
            "text": "正在检查 [redacted]",
            "sequence": 1,
            "created_at": None,
        },
        {
            "ordinal": 2,
            "kind": "commentary",
            "text": "下一步检查工具回执",
            "sequence": 3,
            "created_at": None,
        },
        {
            "ordinal": 3,
            "kind": "answer",
            "text": "结果 [redacted]",
            "sequence": 6,
            "created_at": None,
        },
    ]
    assert "token" not in repr(projected)
    assert "PRIVATE_OUTPUT" not in repr(projected)
    assert "comment-a" not in repr(projected)


def test_hidden_v4_message_does_not_suppress_visible_legacy_answer():
    projected = build_admin_worker_execution(
        [
            {
                "event_id": "hidden-v4",
                "sequence": 1,
                "type": "message.delta",
                "visible_to_user": False,
                "payload": {"delta": "private", "__stream_v4": {"message_id": "hidden"}},
            },
            {
                "event_id": "legacy-answer",
                "sequence": 2,
                "type": "assistant_delta",
                "visible_to_user": True,
                "payload": {"delta": "公开答复"},
            },
        ],
        sanitize_text=lambda value: value,
    )

    assert projected["response"] == "公开答复"
    assert projected["messages"][0]["text"] == "公开答复"
    assert "private" not in repr(projected)
