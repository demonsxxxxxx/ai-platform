import json

import pytest

from app.auth import AuthPrincipal
from app.routes import lambchat_compat


@pytest.mark.parametrize("role", ["user", "admin"])
def test_capability_projection_is_safe_and_stable_for_live_reconnect_and_history(role):
    """All Chat delivery modes share one identity-safe capability projection."""

    principal = AuthPrincipal(
        user_id=f"{role}-a",
        display_name=role.title(),
        tenant_id="default",
        roles=[role],
    )
    run = {
        "id": "run-capabilities",
        "trace_id": "trace-capabilities",
        "agent_id": "general-agent",
        "skill_id": "general-chat",
        "status": "running",
        "result_json": {},
        "error_code": None,
        "error_message": None,
    }
    base_event = {
        "trace_id": "trace-capabilities",
        "schema_version": "ai-platform.event-envelope.v1",
        "stage": "capability",
        "severity": "info",
        "visible_to_user": True,
        "error_code": None,
        "created_at": "2026-07-26T00:00:00Z",
    }
    events = [
        {
            **base_event,
            "id": "evt-capability-selected",
            "sequence": 11,
            "event_type": "capability_selected",
            "message": "private selected message",
            "payload_json": {
                "capability": {
                    "kind": "skill",
                    "name": "Document review",
                    "status": "selected",
                },
                "skill_id": "private-skill-id",
                "arguments": {"path": "C:/private/input.docx"},
            },
        },
        {
            **base_event,
            "id": "evt-capability-completed",
            "sequence": 12,
            "event_type": "capability_completed",
            "message": "private completed message",
            "payload_json": {
                "capability": {
                    "kind": "mcp",
                    "name": "Knowledge search",
                    "status": "completed",
                },
                "canonical_identity": "mcp__private-server__search",
                "tool_call_id": "private-tool-call-id",
                "endpoint": "https://private.example.test/mcp",
            },
        },
        {
            **base_event,
            "id": "evt-capability-failed",
            "sequence": 13,
            "event_type": "capability_failed",
            "message": "private failed message",
            "payload_json": {
                "capability": {
                    "kind": "mcp",
                    "name": "Knowledge search",
                    "status": "failed",
                },
                "canonical_identity": "mcp__private-server__search",
                "raw_error": "private token and stack trace",
            },
        },
    ]

    live = lambchat_compat._compatibility_events_for_run(run, events, [], principal)
    reconnect = lambchat_compat._compatibility_events_for_run(run, events, [], principal)

    expected = [
        ("evt-capability-selected", 11, {"kind": "skill", "name": "Document review", "status": "selected"}),
        (
            "evt-capability-completed",
            12,
            {"kind": "mcp", "name": "Knowledge search", "status": "completed"},
        ),
        (
            "evt-capability-failed",
            13,
            {"kind": "mcp", "name": "Knowledge search", "status": "failed"},
        ),
    ]
    for delivery in (live, reconnect):
        assert [
            (
                record.id,
                record.stream_data["sequence"],
                record.stream_data["payload"]["capability"],
            )
            for record in delivery
        ] == expected
        assert len({record.id for record in delivery}) == len(delivery)
        for record in delivery:
            assert set(record.stream_data["payload"]) == {"capability"}
            assert set(record.stream_data["payload"]["capability"]) == {"kind", "name", "status"}
            assert record.history_event["sequence"] == record.stream_data["sequence"]
            assert record.history_event["payload"] == record.stream_data["payload"]
            assert record.history_event["data"]["payload"] == record.stream_data["payload"]

    rendered = json.dumps(
        [record.stream_data for record in live] + [record.history_event for record in live]
    )
    for private_term in (
        "private-skill-id",
        "C:/private/input.docx",
        "mcp__private-server__search",
        "private-tool-call-id",
        "https://private.example.test/mcp",
        "private token and stack trace",
        "private selected message",
        "private completed message",
        "private failed message",
    ):
        assert private_term not in rendered
