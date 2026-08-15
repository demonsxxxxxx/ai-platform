from app.auth import AuthPrincipal
from app.routes.lambchat_compat import _compatibility_events_for_run


def test_public_lifecycle_singletons_match_exact_history_projection():
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
        event("evt-skill-executor", 6, "skill_selected", {"visible_to_user": True}),
        event("evt-heartbeat", 8, "run_started", {"heartbeat": True, "visible_to_user": True}),
        event("evt-intent-executor", 5, "intent_detected", {"visible_to_user": True}),
        event(
            "evt-hidden",
            10,
            "agent_step_started",
            {"private_payload": "must-not-leak-private-marker", "visible_to_user": False},
            message="must-not-leak-private-marker",
            visible_to_user=False,
        ),
        event("evt-worker-started", 4, "worker_started", {"visible_to_user": True}),
        event("evt-control", 9, "cancel_requested", {"visible_to_user": True}),
        event("evt-intent-route", 1, "intent_detected", {"visible_to_user": True}),
        event("evt-executor-started", 7, "run_started", {"visible_to_user": True}),
        event("evt-queued", 2, "queued", {"visible_to_user": True}),
        event("evt-skill-route", 3, "skill_selected", {"visible_to_user": True}),
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
