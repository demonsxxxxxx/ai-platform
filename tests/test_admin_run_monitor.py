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
        "actions": [
            {
                "ordinal": 1,
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
    assert "private-operation-id" not in repr(projected)
    assert "PrivateTool" not in repr(projected)
