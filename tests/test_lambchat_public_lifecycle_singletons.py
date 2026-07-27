import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.auth import AuthPrincipal
from app.main import create_app
from app.routes.lambchat_compat import _compatibility_events_for_run
from tests.test_lambchat_frontend_compat import action_headers, auth_settings, fake_transaction


def test_public_lifecycle_singletons_match_builder_sse_reconnect_and_exact_history(monkeypatch):
    run = {
        "id": "run_a",
        "session_id": "ses_a",
        "trace_id": "trace_run_a",
        "agent_id": "general-agent",
        "skill_id": "general-chat",
        "status": "running",
        "result_json": {},
        "error_code": None,
        "error_message": None,
    }
    base_event = {
        "trace_id": "trace_run_a",
        "schema_version": "ai-platform.event-envelope.v1",
        "stage": "internal",
        "message": "private lifecycle detail",
        "severity": "info",
        "visible_to_user": True,
        "error_code": None,
        "created_at": "2026-07-27T00:00:00Z",
    }

    def event(event_id, sequence, event_type, payload, **overrides):
        return {
            **base_event,
            "id": event_id,
            "sequence": sequence,
            "event_type": event_type,
            "payload_json": payload,
            **overrides,
        }

    run_events = [
        event("evt-intent-route", 1, "intent_detected", {"visible_to_user": True}),
        event("evt-queued", 2, "queued", {"visible_to_user": True}),
        event("evt-skill-route", 3, "skill_selected", {"visible_to_user": True}),
        event("evt-worker-started", 4, "worker_started", {"visible_to_user": True}),
        event("evt-intent-executor", 5, "intent_detected", {"visible_to_user": True}),
        event("evt-skill-executor", 6, "skill_selected", {"visible_to_user": True}),
        event("evt-executor-started", 7, "run_started", {"visible_to_user": True}),
        event("evt-heartbeat", 8, "run_started", {"heartbeat": True, "visible_to_user": True}),
        event("evt-control", 9, "cancel_requested", {"visible_to_user": True}),
        event(
            "evt-hidden",
            10,
            "agent_step_started",
            {"private_payload": "must-not-leak-private-marker", "visible_to_user": False},
            message="must-not-leak-private-marker",
            visible_to_user=False,
        ),
    ]
    expected_lifecycle = [
        ("intent_detected", "evt-intent-route", 1),
        ("capability_selected", "evt-skill-route", 3),
        ("run_started", "evt-worker-started", 4),
    ]
    principals = [
        AuthPrincipal("user-a", "User A", "default", roles=["user"]),
        AuthPrincipal("admin-a", "Admin A", "default", roles=["admin"]),
    ]

    for principal in principals:
        history = [
            record.history_event
            for record in _compatibility_events_for_run(run, run_events, [], principal)
        ]
        lifecycle = [
            (item["event_type"], item["id"], item["sequence"])
            for item in history
            if item["event_type"] in {event_type for event_type, _, _ in expected_lifecycle}
        ]
        assert lifecycle == expected_lifecycle
        assert ("heartbeat", "evt-heartbeat", 8) in [
            (item["event_type"], item["id"], item["sequence"]) for item in history
        ]
        assert ("cancel_requested", "evt-control", 9) in [
            (item["event_type"], item["id"], item["sequence"]) for item in history
        ]
        assert "must-not-leak-private-marker" not in str(history)

    strict_event = event(
        "evt-strict-step",
        11,
        "execution_step",
        {
            "step_id": "step-1",
            "kind": "processing",
            "stage": "execution",
            "status": "running",
            "title": "Controlled processing",
            "summary": "Processing authorized input",
            "progress": {"current": 0, "total": 1},
        },
    )
    assert [
        (record.id, record.stream_event_type)
        for record in _compatibility_events_for_run(run, [strict_event], [], principals[0])
    ] == [("evt-strict-step", "execution_step")]

    async def session(_conn, *, tenant_id, user_id, session_id):
        return {"id": session_id}

    async def authorized_run(_conn, *, tenant_id, user_id, run_id):
        return run

    async def events(_conn, *, tenant_id, run_id):
        return run_events

    async def artifacts(_conn, *, tenant_id, run_id):
        return []

    async def user_messages(_conn, *, tenant_id, user_id, session_id, run_ids):
        return []

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.lambchat_compat.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.get_settings",
        lambda: SimpleNamespace(run_event_stream_max_heartbeats=1),
    )
    monkeypatch.setattr("app.routes.lambchat_compat.asyncio.sleep", no_sleep)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.get_authorized_lambchat_session", session
    )
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.get_authorized_run", authorized_run)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.list_run_events", events)
    monkeypatch.setattr("app.routes.lambchat_compat.repositories.list_run_artifacts", artifacts)
    monkeypatch.setattr(
        "app.routes.lambchat_compat.repositories.list_authorized_user_messages_for_runs",
        user_messages,
    )
    client = TestClient(create_app())

    for roles in ("user", "admin"):
        headers = action_headers(roles=roles)
        streams = [
            client.get("/api/chat/sessions/ses_a/stream?run_id=run_a", headers=headers)
            for _ in range(2)
        ]
        history_response = client.get("/api/sessions/ses_a/events?run_id=run_a", headers=headers)
        assert all(response.status_code == 200 for response in streams)
        assert history_response.status_code == 200
        stream_lifecycles = []
        for response in streams:
            payloads = [
                json.loads(line.removeprefix("data: "))
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            stream_lifecycles.append(
                [
                    (item["event_type"], item["event_id"], item["sequence"])
                    for item in payloads
                    if item.get("event_type") in {event_type for event_type, _, _ in expected_lifecycle}
                ]
            )
            assert "must-not-leak-private-marker" not in response.text
        history_lifecycle = [
            (item["event_type"], item["id"], item["sequence"])
            for item in history_response.json()["events"]
            if item["event_type"] in {event_type for event_type, _, _ in expected_lifecycle}
        ]
        assert stream_lifecycles == [expected_lifecycle, expected_lifecycle]
        assert history_lifecycle == expected_lifecycle
        assert "must-not-leak-private-marker" not in history_response.text
