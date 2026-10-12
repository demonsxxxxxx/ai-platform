import asyncio
import threading
import time

import httpx
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from app.runtime.kernel_contracts import AgentEvent
from app.runtime.sandbox import executor_app
from app.runtime.sandbox.contracts import ExecutorCallbackEvent, ExecutorTerminalResult
from app.runtime.sandbox.executor_app import (
    _CallbackRetryPolicy,
    create_executor_app as _create_executor_app,
)
from tests.test_sandbox_executor_app import (
    EXECUTOR_AUTH_TOKEN,
    TRUSTED_CALLBACK_BASE_URL,
    auth_headers,
    callback_ack,
    message_delta_callback,
    sdk_result,
    sdk_mcp_evidence,
    selected_mcp_task_payload,
    task_payload,
)


def create_executor_app(*args, **kwargs):
    kwargs.setdefault("terminal_callback_retry_seconds", 0.1)
    return _create_executor_app(*args, **kwargs)


def _wait_for_status(client: TestClient, expected: str) -> dict[str, object]:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        response = client.get(
            "/v2/tasks/run-a/qat-attempt-a",
            headers=auth_headers(),
        )
        if (
            response.status_code == 200
            and response.json().get("status") == expected
            and "terminal_result" in response.json()
        ):
            return response.json()
        time.sleep(0.01)
    raise AssertionError(f"executor task did not reach {expected}: {response.json()}")


def _wait_for_terminal_callback(
    callbacks: list[dict[str, object]], expected: str
) -> list[dict[str, object]]:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        terminal = [item for item in callbacks if item.get("status") == expected]
        if terminal:
            return terminal
        time.sleep(0.01)
    raise AssertionError(
        f"terminal callback did not reach {expected}: "
        f"{[(item.get('status'), item.get('error_message')) for item in callbacks]}"
    )


def test_v2_dispatch_returns_accepted_and_delivers_terminal_callback(tmp_path):
    callbacks: list[dict[str, object]] = []

    async def executor_runner(_request, _workspace_root, _emit_event):
        await asyncio.sleep(0.05)
        return {"status": "completed", "message": "done"}

    async def callback_sender(_url, payload, _token):
        callbacks.append(payload)
        return callback_ack(payload)

    app = create_executor_app(
        workspace_root=tmp_path,
        executor_runner=executor_runner,
        callback_sender=callback_sender,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a",
        expected_run_id="run-a",
        expected_attempt_id="qat-attempt-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
    )
    with TestClient(app) as client:
        started = time.monotonic()
        response = client.post(
            "/v2/tasks",
            json=task_payload(),
            headers=auth_headers(),
        )
        assert response.status_code == 202
        assert time.monotonic() - started < 0.5
        assert response.json() == {
            "status": "accepted",
            "run_id": "run-a",
            "attempt_id": "qat-attempt-a",
        }
        status_payload = _wait_for_status(client, "completed")
        assert status_payload["terminal_result"]["status"] == "completed"
        assert status_payload.get("error_message") is None

        terminal = _wait_for_terminal_callback(callbacks, "completed")
        assert len(terminal) == 1
        assert terminal[0]["terminal_result"]["message"] == "done"

        assert (
            client.post(
                "/v1/tasks/execute",
                json=task_payload(),
                headers=auth_headers(),
            ).status_code
            == 404
        )


def test_v2_status_retains_completed_result_when_terminal_callback_is_unavailable(tmp_path):
    async def executor_runner(_request, _workspace_root, _emit_event):
        return {"status": "completed", "message": "done"}

    async def callback_sender(url, payload, _token):
        if payload.get("status") == "running":
            return callback_ack(payload)
        raise httpx.ConnectError(
            "callback unavailable",
            request=httpx.Request("POST", url),
        )

    app = create_executor_app(
        workspace_root=tmp_path,
        executor_runner=executor_runner,
        callback_sender=callback_sender,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a",
        expected_run_id="run-a",
        expected_attempt_id="qat-attempt-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v2/tasks",
            json=task_payload(),
            headers=auth_headers(),
        )
        assert response.status_code == 202
        status_payload = _wait_for_status(client, "callback_failed")

    terminal_result = status_payload["terminal_result"]
    assert terminal_result["run_id"] == "run-a"
    assert terminal_result["status"] == "completed"
    assert terminal_result["message"] == "done"


def test_v2_delivery_exhaustion_preserves_failure_without_uncertain_terminal(tmp_path):
    callbacks: list[dict[str, object]] = []
    assistant_attempts: list[dict[str, object]] = []

    async def executor_runner(_request, _workspace_root, emit_event):
        await emit_event(AgentEvent(type="assistant_delta", message="partial", payload={"delta": "partial"}))
        return {"status": "completed", "message": "done"}

    async def callback_sender(url, payload, _token):
        callbacks.append(payload)
        if any(event.get("type") == "assistant_delta" for event in payload.get("events", [])):
            assistant_attempts.append(payload)
            raise httpx.ConnectError(
                "callback unavailable",
                request=httpx.Request("POST", url),
            )
        return callback_ack(payload)

    app = create_executor_app(
        workspace_root=tmp_path,
        executor_runner=executor_runner,
        callback_sender=callback_sender,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a",
        expected_run_id="run-a",
        expected_attempt_id="qat-attempt-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
        nonterminal_callback_retry_policy=_CallbackRetryPolicy(
            max_attempts=2,
            attempt_timeout_seconds=0.05,
            initial_backoff_seconds=0,
            max_backoff_seconds=0,
            jitter_ratio=0,
        ),
    )
    with TestClient(app) as client:
        response = client.post("/v2/tasks", json=task_payload(), headers=auth_headers())
        assert response.status_code == 202
        status_payload = _wait_for_status(client, "callback_failed")
        assert status_payload["error_message"] == "executor_callback_delivery_uncertain"
        assert status_payload["terminal_result"]["error_code"] == "stream_delivery_exhausted"
        assert not [item for item in callbacks if item.get("status") == "failed"]

    assert len(assistant_attempts) == 2
    assert assistant_attempts[0] == assistant_attempts[1]


def test_v2_cancel_during_callback_retry_cancels_batch_and_blocks_next(tmp_path, caplog, monkeypatch):
    callbacks: list[dict[str, object]] = []
    assistant_attempts: list[dict[str, object]] = []
    post_cancel_results: list[bool] = []
    retry_started = threading.Event()
    caplog.set_level("INFO", logger=executor_app.__name__)
    monkeypatch.setattr(executor_app, "_EXECUTOR_SHUTDOWN_TIMEOUT_SECONDS", 0.05)

    async def executor_runner(_request, _workspace_root, emit_event):
        try:
            await emit_event(AgentEvent(type="assistant_delta", message="first", payload={"delta": "first"}))
        except asyncio.CancelledError:
            post_cancel_results.append(
                await emit_event(
                    AgentEvent(type="assistant_delta", message="second", payload={"delta": "second"})
                )
            )
            raise
        await emit_event(AgentEvent(type="assistant_delta", message="second", payload={"delta": "second"}))
        return {"status": "completed", "message": "done"}

    async def callback_sender(url, payload, _token):
        callbacks.append(payload)
        if any(event.get("type") == "assistant_delta" for event in payload.get("events", [])):
            assistant_attempts.append(payload)
            raise httpx.ConnectError(
                "callback unavailable",
                request=httpx.Request("POST", url),
            )
        return callback_ack(payload)

    async def wait_for_cancellation(_delay):
        retry_started.set()
        await asyncio.Event().wait()

    app = create_executor_app(
        workspace_root=tmp_path,
        executor_runner=executor_runner,
        callback_sender=callback_sender,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a",
        expected_run_id="run-a",
        expected_attempt_id="qat-attempt-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
        nonterminal_callback_retry_policy=_CallbackRetryPolicy(
            max_attempts=3,
            attempt_timeout_seconds=0.05,
            initial_backoff_seconds=0,
            max_backoff_seconds=0,
            jitter_ratio=0,
        ),
        callback_retry_sleep=wait_for_cancellation,
    )
    with TestClient(app) as client:
        response = client.post("/v2/tasks", json=task_payload(), headers=auth_headers())
        assert response.status_code == 202
        assert retry_started.wait(timeout=2)

        cancelled = client.post(
            "/v2/tasks/run-a/qat-attempt-a/cancel",
            headers=auth_headers(),
        )
        assert cancelled.status_code == 202
        status_payload = _wait_for_status(client, "callback_failed")
        assert status_payload["error_message"] == "executor_shutdown_delivery_uncertain"
        assert status_payload["terminal_result"]["status"] == "cancelled"

    cancelled_terminal_callbacks = [
        payload for payload in callbacks if payload.get("status") == "cancelled"
    ]
    assert cancelled_terminal_callbacks == []
    assert len(assistant_attempts) == 1
    assert assistant_attempts[0]["events"][0]["payload"]["delta"] == "first"
    assert post_cancel_results == [False]
    cancelled_records = [
        record for record in caplog.records if record.message == "sandbox_callback_batch_cancelled"
    ]
    assert len(cancelled_records) == 1
    assert cancelled_records[0].callback_batch_id == assistant_attempts[0]["batch_id"]
    assert cancelled_records[0].callback_batch_digest
    assert cancelled_records[0].callback_attempt == 1
    assert cancelled_records[0].callback_batch_state == "cancelled"
    assert "first" not in caplog.text
    assert "second" not in caplog.text
    assert "secret" not in caplog.text


def test_v2_dispatch_is_idempotent_while_original_task_is_running(tmp_path):
    release_runner = threading.Event()
    callbacks: list[dict[str, object]] = []

    async def executor_runner(_request, _workspace_root, _emit_event):
        await asyncio.to_thread(release_runner.wait)
        return {"status": "completed", "message": "done"}

    async def callback_sender(_url, payload, _token):
        callbacks.append(payload)
        return callback_ack(payload)

    app = create_executor_app(
        workspace_root=tmp_path,
        executor_runner=executor_runner,
        callback_sender=callback_sender,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a",
        expected_run_id="run-a",
        expected_attempt_id="qat-attempt-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
    )
    with TestClient(app) as client:
        first = client.post("/v2/tasks", json=task_payload(), headers=auth_headers())
        duplicate = client.post("/v2/tasks", json=task_payload(), headers=auth_headers())

        assert first.status_code == 202
        assert duplicate.status_code == 202
        assert duplicate.json()["run_id"] == "run-a"
        assert duplicate.json()["attempt_id"] == "qat-attempt-a"
        assert duplicate.json()["status"] in {"accepted", "running"}

        release_runner.set()
        assert _wait_for_status(client, "completed")["terminal_result"]["status"] == "completed"
        assert len(_wait_for_terminal_callback(callbacks, "completed")) == 1


def test_v2_cancel_stops_background_task_and_delivers_cancelled_terminal(tmp_path):
    callbacks: list[dict[str, object]] = []

    async def executor_runner(_request, _workspace_root, _emit_event):
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def callback_sender(_url, payload, _token):
        callbacks.append(payload)
        return callback_ack(payload)

    app = create_executor_app(
        workspace_root=tmp_path,
        executor_runner=executor_runner,
        callback_sender=callback_sender,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a",
        expected_run_id="run-a",
        expected_attempt_id="qat-attempt-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
    )
    with TestClient(app) as client:
        assert (
            client.post(
                "/v2/tasks",
                json=task_payload(),
                headers=auth_headers(),
            ).status_code
            == 202
        )
        response = client.post(
            "/v2/tasks/run-a/qat-attempt-a/cancel",
            headers=auth_headers(),
        )
        assert response.status_code == 202
        observed = _wait_for_status(client, "cancelled")
        ExecutorTerminalResult.model_validate(observed["terminal_result"])

        terminal = _wait_for_terminal_callback(callbacks, "cancelled")
        assert len(terminal) == 1
        assert terminal[0]["terminal_result"]["error_code"] == "executor_cancelled"
        ExecutorCallbackEvent.model_validate(terminal[0])


def test_v2_supervisor_sends_heartbeat_while_runner_is_silent(tmp_path):
    callbacks: list[dict[str, object]] = []
    active_senders = 0
    max_active_senders = 0

    async def executor_runner(_request, _workspace_root, _emit_event):
        await asyncio.sleep(0.06)
        return {"status": "completed", "message": "done"}

    async def callback_sender(_url, payload, _token):
        nonlocal active_senders, max_active_senders
        active_senders += 1
        max_active_senders = max(max_active_senders, active_senders)
        try:
            await asyncio.sleep(0.02)
            callbacks.append(payload)
            return callback_ack(payload)
        finally:
            active_senders -= 1

    app = create_executor_app(
        workspace_root=tmp_path,
        executor_runner=executor_runner,
        callback_sender=callback_sender,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a",
        expected_run_id="run-a",
        expected_attempt_id="qat-attempt-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
        heartbeat_interval_seconds=0.01,
    )
    with TestClient(app) as client:
        response = client.post("/v2/tasks", json=task_payload(), headers=auth_headers())
        assert response.status_code == 202
        _wait_for_status(client, "completed")

    heartbeats = [
        item
        for item in callbacks
        if item.get("state_patch") == {"executor_heartbeat": True}
    ]
    assert heartbeats
    assert all(item["status"] == "running" for item in heartbeats)
    assert max_active_senders == 1


def _successful_output(kind="all"):
    output = {}
    if kind in {"receipt", "all"}:
        output["answer_receipt"] = {
            "schema_version": "ai-platform.assistant-answer-receipt.v2",
            "message_id": "msg-a",
            "delta_count": 1,
            "text_length": 4,
            "last_delta_event_id": "evt_answer",
        }
    if kind in {"files", "described_files", "all"}:
        output["response_files"] = ["outputs/delivery/report.txt"]
    if kind in {"described_files", "all"}:
        output["response_file_descriptors"] = [{
            "source_path": "outputs/delivery/report.txt",
            "display_name": "Report.txt",
            "role": "primary",
        }]
    return output


def _terminal_test_app(tmp_path, callback_sender, **kwargs):
    return create_executor_app(
        workspace_root=tmp_path, callback_sender=callback_sender,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a", expected_run_id="run-a",
        expected_attempt_id="qat-attempt-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
        heartbeat_interval_seconds=60, **kwargs,
    )


@pytest.mark.parametrize("prior_diagnostics", [False, True])
@pytest.mark.parametrize("output_kind", ["receipt", "files", "described_files", "all"])
@pytest.mark.parametrize("outcome", [
    "accepted", "phase_rejected", "drain_rejected", "capability_rejected", "exhausted",
])
def test_v2_terminal_output_follows_executor_owned_outcome(
    tmp_path, monkeypatch, output_kind, outcome, prior_diagnostics,
):
    callbacks, rejected = [], []
    outputs = _successful_output(output_kind)
    output_file = tmp_path / "outputs/delivery/report.txt"
    output_file.parent.mkdir(parents=True)
    output_file.write_text("body")
    continuity = {"sdk_usage": {"input_tokens": 3}, "provider_session_final_sequence": 7, "sdk_used": True}
    diagnostics = executor_app._merge_runtime_diagnostics(
        None, error_code="tool_permission_denied", failure_source="sdk_tool_admission",
        failure_stage="model_wait", exception=RuntimeError("synthetic prior detail"),
    ) if prior_diagnostics else {}
    # Keep the last delta queued until final close; the duplicate phase is suppressed.
    monkeypatch.setattr(executor_app, "_MESSAGE_DELTA_FLUSH_SECONDS", 60)

    async def runner(_request, _workspace_root, emit):
        if outcome == "drain_rejected":
            await emit(executor_app._PlatformExecutionPhaseFact("sandbox_submission", "completed"))
            assert await emit(message_delta_callback("answer", "body").events[0])
        elif outcome == "capability_rejected":
            assert not await emit(AgentEvent(type="capability_completed"))
        return {"status": "completed", "message": "", **outputs, **continuity, "runtime_diagnostics": diagnostics}

    async def sender(url, payload, _token):
        callbacks.append(payload)
        terminal_phase = any(
            event.get("payload", {}).get("phase") == "sandbox_submission"
            and event["payload"].get("lifecycle") == "completed"
            for event in payload.get("events", [])
        )
        reject = (
            (outcome in {"phase_rejected", "exhausted"} and terminal_phase)
            or (outcome == "drain_rejected" and any(e["type"] == "message.delta" for e in payload["events"]))
            or (outcome == "capability_rejected" and any(e["type"].startswith("capability_") for e in payload["events"]))
        )
        if reject:
            rejected.append(payload)
            if outcome == "exhausted":
                raise httpx.ReadTimeout("synthetic response loss", request=httpx.Request("POST", url))
            return {"accepted": False}
        return callback_ack(payload)

    app = _terminal_test_app(
        tmp_path, sender, executor_runner=runner,
        nonterminal_callback_retry_policy=_CallbackRetryPolicy(
            max_attempts=2, initial_backoff_seconds=0, jitter_ratio=0,
        ),
    )
    status = "completed" if outcome == "accepted" else "callback_failed" if outcome == "exhausted" else "failed"
    with TestClient(app) as client:
        assert client.post("/v2/tasks", json=task_payload(), headers=auth_headers()).status_code == 202
        observed = _wait_for_status(client, status)
        if outcome != "exhausted":
            terminal = _wait_for_terminal_callback(callbacks, status)
            assert len(terminal) == 1
            parsed_callback = ExecutorCallbackEvent.model_validate(terminal[0])
            assert parsed_callback.terminal_result == ExecutorTerminalResult.model_validate(observed["terminal_result"])
    result = observed["terminal_result"]
    parsed = ExecutorTerminalResult.model_validate(result)
    assert all(result[key] == value for key, value in continuity.items())
    assert output_file.read_text() == "body"
    if outcome == "accepted":
        assert all(result[key] == value for key, value in outputs.items())
        assert rejected == []
    else:
        expected_code = (
            "capability_callback_not_acknowledged" if outcome == "capability_rejected"
            else "stream_delivery_exhausted" if outcome == "exhausted" else "stream_delivery_rejected"
        )
        assert parsed.status == "failed" and parsed.error_code == expected_code
        assert not set(outputs) & result.keys()
        assert parsed.message == ""
        final_diagnostics = result["runtime_diagnostics"]
        assert final_diagnostics["error_code"] == ("tool_permission_denied" if prior_diagnostics else expected_code)
        assert final_diagnostics["failure_observations"][-1]["error_code"] == expected_code
        if prior_diagnostics:
            assert final_diagnostics["sdk"] == diagnostics["sdk"]
            assert final_diagnostics["failure_observations"][0] == diagnostics["failure_observations"][0]
        assert len(rejected) == (2 if outcome == "exhausted" else 1)
        if outcome == "exhausted":
            assert observed["error_message"] == "executor_callback_delivery_uncertain"
            assert not [item for item in callbacks if item.get("terminal_result")]
            assert rejected[0] == rejected[1]
        else:
            assert "error_message" not in observed


@pytest.mark.parametrize("raw_status", ["failed", "cancelled", "completed"])
def test_v2_does_not_repair_unrelated_invalid_runner_terminal(tmp_path, caplog, raw_status):
    callbacks = []
    invalid_output = _successful_output()
    if raw_status == "completed":
        invalid_output["answer_receipt"]["text_length"] = "synthetic-private-invalid-input"

    async def runner(_request, _workspace_root, _emit):
        return {
            "status": raw_status, "message": "", **invalid_output,
            "error_code": "controlled_failure", "error_message": "Controlled failure",
        }

    async def sender(_url, payload, _token):
        callbacks.append(payload)
        return callback_ack(payload)

    with TestClient(_terminal_test_app(tmp_path, sender, executor_runner=runner)) as client:
        assert client.post("/v2/tasks", json=task_payload(), headers=auth_headers()).status_code == 202
        observed = _wait_for_status(client, "callback_failed")
    with pytest.raises(ValidationError):
        ExecutorTerminalResult.model_validate(observed["terminal_result"])
    assert not [item for item in callbacks if item.get("terminal_result")]
    assert "synthetic-private-invalid-input" not in caplog.text


@pytest.mark.parametrize("outcome", ["accepted", "capability_callback", "capability_evidence"])
def test_v2_sdk_capability_outcome_precedes_success_output_admission(tmp_path, monkeypatch, outcome):
    callbacks = []
    outputs = _successful_output()

    async def fake_sdk(**kwargs):
        if outcome == "capability_callback":
            assert not await kwargs["on_capability_evidence"](
                sdk_mcp_evidence("mcp__tenant-server__search", "call-a", "invocation_requested")
            )
        elif outcome == "capability_evidence":
            assert not await kwargs["on_tool_lifecycle"]({
                "tool_name": "Write", "invocation_id": "call-a", "lifecycle": "completed",
            })
        return sdk_result("", **outputs, usage={"output_tokens": 2}, provider_final_sequence=9)

    async def sender(_url, payload, _token):
        callbacks.append(payload)
        if outcome == "capability_callback" and any(
            item["type"].startswith("capability_") for item in payload.get("events", [])
        ):
            return {"accepted": False}
        return callback_ack(payload)

    monkeypatch.setattr(executor_app, "get_settings", lambda: type("Settings", (), {"claude_agent_sdk_enabled": True})())
    monkeypatch.setattr(executor_app, "run_claude_agent_sdk", fake_sdk)
    expected_status = "completed" if outcome == "accepted" else "failed"
    with TestClient(_terminal_test_app(tmp_path, sender)) as client:
        assert client.post("/v2/tasks", json=selected_mcp_task_payload(), headers=auth_headers()).status_code == 202
        observed = _wait_for_status(client, expected_status)
        terminal = _wait_for_terminal_callback(callbacks, expected_status)
    result = observed["terminal_result"]
    ExecutorTerminalResult.model_validate(result)
    assert len(terminal) == 1
    ExecutorCallbackEvent.model_validate(terminal[0])
    assert result["sdk_usage"] == {"output_tokens": 2}
    assert result["provider_session_final_sequence"] == 9
    if outcome == "accepted":
        assert all(result[key] == value for key, value in outputs.items())
    else:
        assert not set(outputs) & result.keys()
        assert result["error_code"] == (
            "capability_callback_not_acknowledged" if outcome == "capability_callback"
            else "tool_invocation_evidence_mismatch"
        )
