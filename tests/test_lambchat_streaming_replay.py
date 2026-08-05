import json
from contextlib import asynccontextmanager

import pytest
from fastapi import HTTPException

from app.auth import AuthPrincipal
from app.routes import lambchat_compat as lambchat


@asynccontextmanager
async def _transaction():
    yield object()


def _principal() -> AuthPrincipal:
    return AuthPrincipal(user_id="user-a", display_name="User A", tenant_id="tenant-a")


def _run(run_id: str, *, status: str) -> dict[str, object]:
    return {
        "id": run_id,
        "session_id": "session-a",
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


def _run_started(sequence: int) -> dict[str, object]:
    return {
        "id": f"evt-{sequence}",
        "trace_id": "trace-a",
        "schema_version": "ai-platform.event-envelope.v1",
        "sequence": sequence,
        "event_type": "worker_started",
        "stage": "worker",
        "message": "Run started",
        "severity": "info",
        "visible_to_user": True,
        "payload_json": {"visible_to_user": True},
        "created_at": None,
    }


def _strict_execution(sequence: int) -> dict[str, object]:
    return {
        "id": f"evt-{sequence}",
        "trace_id": "trace-a",
        "schema_version": "ai-platform.public-execution-event.v1",
        "sequence": sequence,
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
            "title": "Authorized capability",
            "summary": "Started",
            "progress": {"current": 0, "total": 1},
        },
        "created_at": None,
    }


def _legacy_capability(sequence: int) -> dict[str, object]:
    return {
        "id": f"evt-{sequence}",
        "trace_id": "trace-a",
        "schema_version": "ai-platform.event-envelope.v1",
        "sequence": sequence,
        "event_type": "capability_invoking",
        "stage": "execution",
        "message": "Capability lifecycle update",
        "severity": "info",
        "visible_to_user": True,
        "payload_json": {"capability": {"kind": "mcp", "name": "Tenant Search", "status": "invoking"}},
        "created_at": None,
    }


def _durable_sse_records(body: str) -> list[tuple[str, str, dict[str, object]]]:
    records = []
    for frame in body.split("\n\n"):
        lines = frame.splitlines()
        event_id = next((line.removeprefix("id: ") for line in lines if line.startswith("id: ")), None)
        event_type = next((line.removeprefix("event: ") for line in lines if line.startswith("event: ")), None)
        payload = next((line.removeprefix("data: ") for line in lines if line.startswith("data: ")), None)
        if event_id and event_type and payload:
            records.append((event_id, event_type, json.loads(payload)))
    return records


def _expected_durable_records(rows: list[dict[str, object]]) -> list[tuple[str, str, dict[str, object]]]:
    records = lambchat._compatibility_events_for_run(
        _run("run-a", status="succeeded"), rows, [], _principal(), include_terminal=False
    )
    return [
        (cursor_id, record.stream_event_type, record.stream_data)
        for record in records
        if (cursor_id := lambchat._durable_cursor_id("run-a", record.history_event)) is not None
    ]


@pytest.mark.asyncio
async def test_lambchat_replay_uses_durable_ids_and_shared_success_fold_before_final(monkeypatch):
    event_calls = []
    run_calls = 0
    forged = _delta(3, "forged")
    forged["payload_json"]["private_payload"] = {"token": "secret"}
    legacy = _delta(4, "legacy")
    legacy["payload_json"]["source"] = "legacy_callback"
    malformed = _delta(5, "malformed")
    malformed["payload_json"]["delta"] = 7
    persisted = [_delta(2, "accepted"), forged, legacy, malformed]

    async def get_run(_conn, *, tenant_id, user_id, run_id):
        nonlocal run_calls
        assert (tenant_id, user_id) == ("tenant-a", "user-a")
        run_calls += 1
        return _run(run_id, status="running" if run_calls == 1 else "succeeded")

    async def list_events(_conn, *, tenant_id, run_id, after_sequence=None, limit=None):
        event_calls.append((tenant_id, run_id, after_sequence, limit))
        return persisted if after_sequence in (None, 0) else []

    async def artifacts(*_args, **_kwargs):
        return []

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(lambchat, "transaction", _transaction)
    monkeypatch.setattr(lambchat.repositories, "get_authorized_run", get_run)
    monkeypatch.setattr(lambchat.repositories, "list_run_events", list_events)
    monkeypatch.setattr(lambchat.repositories, "list_run_artifacts", artifacts)
    monkeypatch.setattr(lambchat, "get_settings", lambda: type("S", (), {"run_event_stream_max_heartbeats": 3})())
    monkeypatch.setattr(lambchat.asyncio, "sleep", no_sleep)

    history = lambchat._compatibility_events_for_run(_run("run-a", status="succeeded"), persisted, [], _principal())
    response = await lambchat.chat_session_stream(
        "session-a",
        "run-a",
        last_event_id="run-a:0",
        principal=_principal(),
    )
    body = "".join([chunk async for chunk in response.body_iterator])

    assert history[-1].stream_event_type == "done"
    assert any(
        record.stream_data.get("projection_kind") == "assistant_final"
        for record in history
    )
    assert any(record.stream_event_type == "message:chunk" for record in history)
    assert event_calls == [("tenant-a", "run-a", 0, None), ("tenant-a", "run-a", 5, None)]
    assert "id: run-a:2" in body
    assert body.count("id: run-a:2") == 1
    assert body.index('"content": "accepted"') < body.index('"projection_kind": "assistant_final"')
    assert all(value not in body for value in ("forged", "legacy", "malformed", "secret"))
    assert "id: run-a:final" not in body
    assert "id: run-a:terminal:succeeded" not in body


@pytest.mark.asyncio
async def test_lambchat_invalid_or_cross_run_last_event_id_fails_before_event_read(monkeypatch):
    async def forbidden_events(*_args, **_kwargs):
        raise AssertionError("run-event read must not occur")

    monkeypatch.setattr(lambchat.repositories, "list_run_events", forbidden_events)

    for value in ("bad", "other-run:1"):
        with pytest.raises(HTTPException, match="invalid_last_event_id") as exc_info:
            await lambchat.chat_session_stream("session-a", "run-a", last_event_id=value, principal=_principal())
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_lambchat_live_pages_match_full_history_singleton_and_strict_fold(monkeypatch):
    first_page = [_run_started(1), _strict_execution(2)]
    second_page = [_run_started(3), _legacy_capability(4)]
    event_calls = []

    async def get_run(_conn, *, tenant_id, user_id, run_id):
        return _run(run_id, status="succeeded")

    async def list_events(_conn, *, tenant_id, run_id, after_sequence=None, limit=None):
        event_calls.append(after_sequence)
        if after_sequence is None:
            return first_page
        if after_sequence == 2:
            return second_page
        return []

    async def artifacts(*_args, **_kwargs):
        return []

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(lambchat, "transaction", _transaction)
    monkeypatch.setattr(lambchat.repositories, "get_authorized_run", get_run)
    monkeypatch.setattr(lambchat.repositories, "list_run_events", list_events)
    monkeypatch.setattr(lambchat.repositories, "list_run_artifacts", artifacts)
    monkeypatch.setattr(lambchat, "get_settings", lambda: type("S", (), {"run_event_stream_max_heartbeats": 3})())
    monkeypatch.setattr(lambchat.asyncio, "sleep", no_sleep)

    response = await lambchat.chat_session_stream("session-a", "run-a", principal=_principal())
    body = "".join([chunk async for chunk in response.body_iterator])

    assert event_calls == [None, 2, 4]
    assert _durable_sse_records(body) == _expected_durable_records(first_page + second_page)


@pytest.mark.asyncio
async def test_lambchat_reconnect_seeds_fold_before_projecting_later_page(monkeypatch):
    first_page = [_run_started(1), _strict_execution(2)]
    second_page = [_run_started(3), _legacy_capability(4)]
    event_calls = []

    async def get_run(_conn, *, tenant_id, user_id, run_id):
        return _run(run_id, status="succeeded")

    async def list_events(_conn, *, tenant_id, run_id, after_sequence=None, limit=None):
        event_calls.append(after_sequence)
        if after_sequence is None:
            return first_page + second_page
        if after_sequence == 2:
            return second_page
        return []

    async def artifacts(*_args, **_kwargs):
        return []

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(lambchat, "transaction", _transaction)
    monkeypatch.setattr(lambchat.repositories, "get_authorized_run", get_run)
    monkeypatch.setattr(lambchat.repositories, "list_run_events", list_events)
    monkeypatch.setattr(lambchat.repositories, "list_run_artifacts", artifacts)
    monkeypatch.setattr(lambchat, "get_settings", lambda: type("S", (), {"run_event_stream_max_heartbeats": 2})())
    monkeypatch.setattr(lambchat.asyncio, "sleep", no_sleep)

    response = await lambchat.chat_session_stream(
        "session-a", "run-a", last_event_id="run-a:2", principal=_principal()
    )
    body = "".join([chunk async for chunk in response.body_iterator])

    assert event_calls == [None, 2, 4]
    assert _durable_sse_records(body) == []
