import asyncio
import threading
import time

from fastapi.testclient import TestClient

from app.runtime.sandbox.executor_app import create_executor_app as _create_executor_app
from tests.test_sandbox_executor_app import (
    EXECUTOR_AUTH_TOKEN,
    TRUSTED_CALLBACK_BASE_URL,
    auth_headers,
    callback_ack,
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
        if response.status_code == 200 and response.json().get("status") == expected:
            return response.json()
        time.sleep(0.01)
    raise AssertionError(f"executor task did not reach {expected}")


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
        _wait_for_status(client, "cancelled")

        terminal = _wait_for_terminal_callback(callbacks, "cancelled")
        assert len(terminal) == 1
        assert terminal[0]["terminal_result"]["error_code"] == "executor_cancelled"


def test_v2_supervisor_sends_heartbeat_while_runner_is_silent(tmp_path):
    callbacks: list[dict[str, object]] = []

    async def executor_runner(_request, _workspace_root, _emit_event):
        await asyncio.sleep(0.06)
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
