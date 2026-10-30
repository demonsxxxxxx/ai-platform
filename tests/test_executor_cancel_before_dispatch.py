import time

import pytest
from fastapi.testclient import TestClient

from app.runtime.sandbox import executor_app
from app.runtime.sandbox.executor_app import create_executor_app
from tests.test_sandbox_executor_app import (
    EXECUTOR_AUTH_TOKEN,
    TRUSTED_CALLBACK_BASE_URL,
    auth_headers,
    callback_ack,
    task_payload,
)


@pytest.mark.parametrize(
    ("dispatch_in_background", "expected_dispatch_status"),
    [(True, 202), (False, 200)],
    ids=["background", "inline"],
)
def test_authenticated_pre_dispatch_cancel_is_latched_until_dispatch(
    tmp_path,
    monkeypatch,
    dispatch_in_background,
    expected_dispatch_status,
):
    callbacks = []
    default_runner_entries = []

    async def default_runner_spy(*_args, **_kwargs):
        default_runner_entries.append(True)
        return {"status": "completed", "message": "unexpected"}

    async def callback_sender(_url, payload, _token):
        callbacks.append(payload)
        return callback_ack(payload)

    monkeypatch.setattr(
        executor_app,
        "_default_executor_runner",
        default_runner_spy,
    )
    monkeypatch.setattr(
        executor_app,
        "_EXECUTOR_SHUTDOWN_TIMEOUT_SECONDS",
        0.05,
    )
    app = create_executor_app(
        workspace_root=tmp_path,
        callback_sender=callback_sender,
        executor_auth_token=EXECUTOR_AUTH_TOKEN,
        expected_session_id="session-a",
        expected_run_id="run-a",
        expected_attempt_id="qat-attempt-a",
        trusted_callback_base_url=TRUSTED_CALLBACK_BASE_URL,
        dispatch_in_background=dispatch_in_background,
        terminal_callback_retry_seconds=1.0,
    )

    with TestClient(app) as client:
        unauthorized = client.post(
            "/v2/tasks/run-a/qat-attempt-a/cancel",
            headers=auth_headers("wrong-token"),
        )
        assert unauthorized.status_code == 401

        invalid_scope = client.post(
            "/v2/tasks/other-run/qat-attempt-a/cancel",
            headers=auth_headers(),
        )
        assert invalid_scope.status_code == 409

        first_cancel = client.post(
            "/v2/tasks/run-a/qat-attempt-a/cancel",
            headers=auth_headers(),
        )
        repeated_cancel = client.post(
            "/v2/tasks/run-a/qat-attempt-a/cancel",
            headers=auth_headers(),
        )
        assert first_cancel.status_code == 202
        assert repeated_cancel.status_code == 202

        # A pre-dispatch cancel must not spend the callback/shutdown deadline.
        time.sleep(0.07)
        dispatched = client.post(
            "/v2/tasks",
            json=task_payload(),
            headers=auth_headers(),
        )
        assert dispatched.status_code == expected_dispatch_status
        assert dispatched.json()["status"] == (
            "accepted" if dispatch_in_background else "cancelled"
        )

        duplicate_dispatch = client.post(
            "/v2/tasks",
            json=task_payload(),
            headers=auth_headers(),
        )
        assert duplicate_dispatch.status_code == 202

        repeated_cancel = client.post(
            "/v2/tasks/run-a/qat-attempt-a/cancel",
            headers=auth_headers(),
        )
        assert repeated_cancel.status_code == 202

        deadline = time.monotonic() + 2.0
        status_response = None
        while time.monotonic() < deadline:
            status_response = client.get(
                "/v2/tasks/run-a/qat-attempt-a",
                headers=auth_headers(),
            )
            if (
                status_response.status_code == 200
                and status_response.json().get("status") == "cancelled"
                and "terminal_result" in status_response.json()
            ):
                break
            time.sleep(0.01)

        assert status_response is not None
        assert status_response.status_code == 200
        assert status_response.json()["status"] == "cancelled"
        terminal_result = status_response.json()["terminal_result"]
        assert terminal_result["error_code"] == "executor_cancelled"
        assert terminal_result["sdk_settlement"] == {
            "schema_version": "ai-platform.sdk-settlement.v1",
            "received_result": False,
            "input_accepted": False,
            "ack_unknown": False,
            "mirror_complete": False,
            "external_effect_unknown": False,
        }

        if dispatch_in_background:
            terminal_deadline = time.monotonic() + 2.0
            while time.monotonic() < terminal_deadline and not any(
                item.get("status") == "cancelled" for item in callbacks
            ):
                time.sleep(0.01)
            terminals = [
                item for item in callbacks if item.get("status") == "cancelled"
            ]
            assert len(terminals) == 1
        else:
            # Inline dispatch keeps its direct terminal response contract.
            assert callbacks == []

    assert default_runner_entries == []
