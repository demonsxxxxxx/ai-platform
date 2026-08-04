from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

from app.auth import AuthPrincipal
from app.routes import runs


@asynccontextmanager
async def _transaction():
    yield object()


def _principal() -> AuthPrincipal:
    return AuthPrincipal(user_id="user-a", display_name="User A", tenant_id="tenant-a")


def _run(run_id: str, *, status: str) -> dict[str, object]:
    return {
        "id": run_id,
        "session_id": "session-a",
        "schema_version": "ai-platform.run.v1",
        "executor_schema_version": "ai-platform.executor-result.v1",
        "status": status,
        "result_json": {"message": "final"},
        "error_code": None,
        "error_message": None,
    }


def _delta(sequence: int, value: str) -> dict[str, object]:
    return {
        "id": f"evt-{sequence}",
        "trace_id": "trace-a",
        "schema_version": "ai-platform.event-envelope.v1",
        "sequence": sequence,
        "event_type": "assistant_delta",
        "stage": "answer",
        "message": "",
        "severity": "info",
        "visible_to_user": True,
        "error_code": None,
        "latency_ms": None,
        "input_token_count": 0,
        "output_token_count": 0,
        "total_token_count": 0,
        "estimated_cost_minor": 0,
        "payload_json": {
            "delta": value,
            "source": "worker_answer_delta_v1",
            "visible_to_user": True,
            "severity": "info",
        },
        "created_at": None,
    }


@pytest.mark.asyncio
async def test_native_last_event_id_replay_is_cursor_bound_and_drains_before_done(monkeypatch):
    event_calls = []
    run_calls = 0

    async def get_run(_conn, *, tenant_id, user_id, run_id):
        nonlocal run_calls
        assert (tenant_id, user_id) == ("tenant-a", "user-a")
        run_calls += 1
        return _run(run_id, status="running" if run_calls == 1 else "succeeded")

    async def list_events(_conn, *, tenant_id, run_id, after_sequence=None, limit=None):
        event_calls.append((tenant_id, run_id, after_sequence, limit))
        if after_sequence == 0:
            hidden = _delta(1, "hidden")
            hidden["visible_to_user"] = False
            legacy = _delta(3, "legacy")
            legacy["payload_json"]["source"] = "legacy_callback"
            private = _delta(4, "private")
            private["payload_json"]["private_payload"] = {"token": "secret"}
            malformed = _delta(5, "malformed")
            malformed["payload_json"]["delta"] = 7
            return [hidden, _delta(2, "accepted"), legacy, private, malformed]
        return []

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(runs, "transaction", _transaction)
    monkeypatch.setattr(runs.repositories, "get_authorized_run", get_run)
    monkeypatch.setattr(runs.repositories, "list_run_events", list_events)
    monkeypatch.setattr(runs, "get_settings", lambda: type("S", (), {"run_event_stream_max_heartbeats": 3})())
    monkeypatch.setattr(runs.asyncio, "sleep", no_sleep)

    response = await runs.stream_run_events(
        "run-a",
        after_sequence=99,
        last_event_id="run-a:0",
        principal=_principal(),
    )
    body = "".join([chunk async for chunk in response.body_iterator])

    assert event_calls == [("tenant-a", "run-a", 0, None), ("tenant-a", "run-a", 5, None)]
    assert "id: run-a:2" in body
    assert body.count("id: run-a:2") == 1
    assert all(value not in body for value in ("hidden", "legacy", "private", "malformed", "secret"))
    assert body.index('"delta": "accepted"') < body.index("event: done")
    assert "id: run-a:done" not in body
    assert "id: run-a:heartbeat" not in body


@pytest.mark.asyncio
async def test_native_invalid_or_cross_run_last_event_id_fails_before_event_read(monkeypatch):
    async def forbidden_events(*_args, **_kwargs):
        raise AssertionError("run-event read must not occur")

    monkeypatch.setattr(runs.repositories, "list_run_events", forbidden_events)

    for value in ("bad", "other-run:1"):
        with pytest.raises(HTTPException, match="invalid_last_event_id") as exc_info:
            await runs.stream_run_events("run-a", last_event_id=value, principal=_principal())
        assert exc_info.value.status_code == 400
