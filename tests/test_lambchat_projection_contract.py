from fastapi.testclient import TestClient

from app.auth import AuthPrincipal
from app.main import create_app
from app.routes.lambchat_compat import _compatibility_events_for_run


def auth_settings():
    return type("S", (), {"trusted_principal_secret": "test-secret", "frontend_poc_auth_enabled": False})()


def test_retired_agent_apps_compatibility_response_contains_no_executor_secrets(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/agent-apps",
        headers={
            "x-ai-user-id": "user-a",
            "x-ai-user-name": "User A",
            "x-ai-tenant-id": "default",
            "x-ai-roles": "developer",
            "x-ai-gateway-secret": "test-secret",
        },
    )

    assert response.status_code == 410
    payload_text = response.text.lower()
    assert "api_key" not in payload_text
    assert "token" not in payload_text
    assert "password" not in payload_text
    assert "runtime_211_base_url" not in payload_text
    assert "claude" not in payload_text


def test_lambchat_live_and_history_use_public_execution_event_names():
    principal = AuthPrincipal(user_id="user-a", display_name="User A", tenant_id="default")
    public_records = _compatibility_events_for_run(
        {"id": "run-a", "status": "running", "trace_id": "trace-run-a"},
        [
            {
                "id": "evt-1",
                "trace_id": "trace-run-a",
                "schema_version": "ai-platform.public-execution-event.v1",
                "sequence": 1,
                "event_type": "execution_step",
                "stage": "execution",
                "message": "Document review",
                "severity": "info",
                "visible_to_user": True,
                "payload_json": {
                    "step_id": "step-opaque-a",
                    "kind": "processing",
                    "stage": "execution",
                    "status": "running",
                    "title": "Document review",
                    "summary": "Processing",
                    "progress": {"current": 0, "total": 4},
                },
                "created_at": None,
            }
        ],
        [],
        principal,
    )

    assert [(record.stream_event_type, record.history_event["event_type"]) for record in public_records] == [
        ("execution_step", "execution_step")
    ]

    raw_records = _compatibility_events_for_run(
        {"id": "run-a", "status": "running", "trace_id": "trace-run-a"},
        [
            {
                "id": "evt-raw",
                "trace_id": "trace-run-a",
                "schema_version": "ai-platform.event-envelope.v1",
                "sequence": 2,
                "event_type": "tool_call_started",
                "stage": "tool",
                "message": "private command",
                "severity": "info",
                "visible_to_user": True,
                "payload_json": {"command": "powershell -Command private-token"},
                "created_at": None,
            }
        ],
        [],
        principal,
    )
    assert raw_records == []


def test_lambchat_omits_legacy_capability_rows_when_strict_timeline_exists_for_every_principal():
    legacy_event = {
        "id": "evt-legacy",
        "trace_id": "trace-run-a",
        "schema_version": "ai-platform.event-envelope.v1",
        "sequence": 1,
        "event_type": "capability_invoking",
        "stage": "execution",
        "message": "Capability lifecycle update",
        "severity": "info",
        "visible_to_user": True,
        "payload_json": {"capability": {"kind": "mcp", "name": "Tenant Search", "status": "invoking"}},
        "created_at": None,
    }
    public_event = {
        "id": "evt-execution",
        "trace_id": "trace-run-a",
        "schema_version": "ai-platform.public-execution-event.v1",
        "sequence": 2,
        "event_type": "execution_step",
        "stage": "execution",
        "message": "",
        "severity": "info",
        "visible_to_user": True,
        "payload_json": {
            "step_id": "step-opaque-a",
            "kind": "capability",
            "stage": "execution",
            "status": "running",
            "title": "Tenant Search",
            "summary": "Started",
            "progress": {"current": 0, "total": 1},
        },
        "created_at": None,
    }
    for principal in (
        AuthPrincipal(user_id="user-a", display_name="User A", tenant_id="default"),
        AuthPrincipal(user_id="admin-a", display_name="Admin A", tenant_id="default", roles=["admin"]),
    ):
        records = _compatibility_events_for_run(
            {"id": "run-a", "status": "running", "trace_id": "trace-run-a"},
            [legacy_event, public_event],
            [],
            principal,
        )

        assert [(record.stream_event_type, record.id) for record in records] == [
            ("execution_step", "evt-execution")
        ]
