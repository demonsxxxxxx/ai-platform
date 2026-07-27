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
            "id": "evt-capability-invoking",
            "sequence": 12,
            "event_type": "capability_invoking",
            "message": "private invocation request message",
            "payload_json": {
                "capability": {
                    "kind": "mcp",
                    "name": "Knowledge search",
                    "status": "invoking",
                },
                "canonical_identity": "mcp__private-server__search",
                "tool_call_id": "private-tool-call-id",
                "arguments": {"query": "private"},
            },
        },
        {
            **base_event,
            "id": "evt-capability-completed",
            "sequence": 13,
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
            "sequence": 14,
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
            "evt-capability-invoking",
            12,
            {"kind": "mcp", "name": "Knowledge search", "status": "invoking"},
        ),
        (
            "evt-capability-completed",
            13,
            {"kind": "mcp", "name": "Knowledge search", "status": "completed"},
        ),
        (
            "evt-capability-failed",
            14,
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
        "private invocation request message",
        "private completed message",
        "private failed message",
    ):
        assert private_term not in rendered


def test_strict_execution_timeline_replaces_legacy_capability_rows():
    """One strict execution row is authoritative for both stream and history."""

    principal = AuthPrincipal(
        user_id="user-a",
        display_name="User",
        tenant_id="default",
        roles=["user"],
    )
    run = {
        "id": "run-timeline",
        "trace_id": "trace-timeline",
        "agent_id": "general-agent",
        "skill_id": "general-chat",
        "status": "running",
        "result_json": {},
        "error_code": None,
        "error_message": None,
    }
    base_event = {
        "trace_id": "trace-timeline",
        "schema_version": "ai-platform.event-envelope.v1",
        "stage": "execution",
        "severity": "info",
        "visible_to_user": True,
        "error_code": None,
        "created_at": "2026-07-27T00:00:00Z",
    }
    events = [
        {
            **base_event,
            "id": "evt-legacy",
            "sequence": 1,
            "event_type": "capability_invoking",
            "message": "private legacy message",
            "payload_json": {
                "capability": {"kind": "mcp", "name": "Knowledge search", "status": "invoking"},
                "tool_call_id": "private-call-id",
            },
        },
        {
            **base_event,
            "id": "evt-execution",
            "sequence": 2,
            "event_type": "execution_step",
            "message": "private projector message",
            "payload_json": {
                "step_id": "pex_public_1",
                "kind": "capability",
                "stage": "execution",
                "status": "running",
                "title": "Query knowledge",
                "summary": "Querying authorized knowledge",
                "progress": {"current": 0, "total": 1},
            },
        },
    ]

    records = lambchat_compat._compatibility_events_for_run(run, events, [], principal)

    assert len(records) == 1
    record = records[0]
    assert record.id == "evt-execution"
    assert record.stream_event_type == "execution_step"
    assert record.stream_data == record.history_event["data"]
    assert record.history_event["payload"] == record.stream_data
    assert set(record.stream_data) == {
        "schema_version", "event_id", "sequence", "run_id", "step_id", "kind", "stage",
        "status", "title", "summary", "progress", "safe_file_name", "artifact_public_id", "created_at",
    }
    rendered = json.dumps(record.stream_data)
    assert "private-call-id" not in rendered
    assert "private legacy message" not in rendered
    assert "private projector message" not in rendered


def test_terminal_final_payload_only_adapts_projection_authority(monkeypatch):
    projection = {
        "event_type": "final_detail",
        "payload": {
            "projection_version": "authority-version",
            "detail_kind": "failed",
            "detail_code": "authority-code",
            "message": "authority message",
        },
        "message": "authority message",
        "event_payload": {"detail_code": "authority-code"},
        "severity": "error",
    }
    monkeypatch.setattr(
        lambchat_compat,
        "public_chat_terminal_projection",
        lambda _run: projection,
    )

    assert lambchat_compat._terminal_final_payload(
        {"id": "run-a", "status": "failed", "error_code": "ignored"}
    ) == ("final_detail", projection["payload"], "error")


def test_lambchat_compat_keeps_lowest_public_lifecycle_projection_per_run():
    principal = AuthPrincipal(
        user_id="user-a",
        display_name="User A",
        tenant_id="default",
        roles=["user"],
    )
    base = {
        "schema_version": "ai-platform.event-envelope.v1",
        "stage": "internal",
        "message": "private lifecycle detail",
        "severity": "info",
        "visible_to_user": True,
        "error_code": None,
        "created_at": None,
        "payload_json": {"visible_to_user": True},
    }

    for run_id in ("run-singleton-a", "run-singleton-b"):
        run = {
            "id": run_id,
            "trace_id": f"trace-{run_id}",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "status": "running",
        }
        rows = [
            {**base, "id": f"{run_id}-skill-executor", "sequence": 5, "event_type": "skill_selected"},
            {**base, "id": f"{run_id}-run-executor", "sequence": 6, "event_type": "run_started"},
            {**base, "id": f"{run_id}-intent-route", "sequence": 1, "event_type": "intent_detected"},
            {**base, "id": f"{run_id}-intent-executor", "sequence": 4, "event_type": "intent_detected"},
            {**base, "id": f"{run_id}-worker", "sequence": 3, "event_type": "worker_started"},
            {**base, "id": f"{run_id}-skill-route", "sequence": 2, "event_type": "skill_selected"},
        ]
        expected = [
            ("intent_detected", f"{run_id}-intent-route", 1),
            ("capability_selected", f"{run_id}-skill-route", 2),
            ("run_started", f"{run_id}-worker", 3),
        ]

        records = lambchat_compat._compatibility_events_for_run(run, rows, [], principal)
        assert [
            (record.stream_data["event_type"], record.id, record.stream_data["sequence"])
            for record in records
        ] == expected
        assert [
            (record.history_event["event_type"], record.id, record.history_event["sequence"])
            for record in records
        ] == expected
