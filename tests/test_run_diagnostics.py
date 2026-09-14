from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.persistence_limits import json_size_bytes
from app.runs.application.diagnostics import RunDiagnosticsService
from app.runs.domain.diagnostics import (
    RUN_DIAGNOSTICS_MAX_BYTES,
    RUN_DIAGNOSTICS_SCHEMA_VERSION,
    build_executor_protocol_diagnostics,
    build_failure_observation,
    merge_run_diagnostics,
    sanitize_runtime_diagnostics,
)
from app.sandbox.api import (
    SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    normalize_sdk_runtime_diagnostics,
)


NOW = datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc)


def runtime_diagnostics(**overrides):
    value = {
        "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        "error_code": "provider_timeout",
        "failure_source": "sdk_exception",
        "failure_stage": "model_wait",
        "sdk": {
            "exception_type": "TimeoutError",
            "exception_message": "model wait expired",
            "exception_traceback": "model.py:42\nTimeoutError: model wait expired",
        },
    }
    value.update(overrides)
    return value


class InMemoryDiagnostics:
    def __init__(self, snapshot=None):
        self.payload = None
        self.calls = []
        self.snapshot = snapshot

    async def append_observation(self, conn, **kwargs):
        self.calls.append((conn, kwargs))
        self.payload, changed = merge_run_diagnostics(
            self.payload,
            kwargs["observation"],
        )
        return {"payload_json": self.payload, "changed": changed}

    async def get_admin_snapshot(self, _conn, **_kwargs):
        return self.snapshot


@pytest.mark.asyncio
async def test_capture_moves_private_diagnostics_out_of_terminal_result_and_is_idempotent():
    persistence = InMemoryDiagnostics()
    service = RunDiagnosticsService(
        persistence=persistence,
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        clock=lambda: NOW,
    )
    conn = object()
    result = {
        "message": "safe public result",
        "runtime_diagnostics": runtime_diagnostics(),
        "nested": {"runtime_diagnostics": {"token": "must-not-persist"}},
    }

    public_result = await service.capture_failure_result(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        source="worker_executor",
        stage="terminalization",
        error_code="executor_http_failure",
        result_json=result,
    )
    await service.capture_failure_result(
        conn,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        source="worker_executor",
        stage="terminalization",
        error_code="executor_http_failure",
        result_json=result,
    )

    assert public_result == {"message": "safe public result", "nested": {}}
    assert result["runtime_diagnostics"]["sdk"]["exception_message"] == (
        "model wait expired"
    )
    assert persistence.calls[0][0] is conn
    assert persistence.payload["schema_version"] == RUN_DIAGNOSTICS_SCHEMA_VERSION
    assert len(persistence.payload["observations"]) == 1
    assert persistence.payload["observations"][0]["attempt_id"] == "attempt-a"


@pytest.mark.asyncio
async def test_reconciliation_keeps_original_failure_and_appends_classified_handling():
    persistence = InMemoryDiagnostics()
    service = RunDiagnosticsService(
        persistence=persistence,
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        clock=lambda: NOW,
    )
    terminal_result = {
        "status": "failed",
        "run_id": "run-a",
        "error_code": "executor_failed",
        "runtime_diagnostics": runtime_diagnostics(),
    }

    await service.capture_reconciliation_failure(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        terminal_result=terminal_result,
        reconciliation_error_code="artifact_manifest_invalid",
    )

    observations = persistence.payload["observations"]
    assert [item["runtime_diagnostics"]["error_code"] for item in observations] == [
        "provider_timeout",
        "terminal_reconciliation_failed",
    ]
    assert observations[1]["runtime_diagnostics"]["sdk"]["errors"] == [
        "artifact_manifest_invalid"
    ]


def test_run_budget_keeps_the_first_and_latest_observations():
    payload = None
    observation_ids = []
    for index in range(70):
        observation = build_failure_observation(
            attempt_id=f"attempt-{index}",
            source="worker_executor",
            stage="terminalization",
            error_code="executor_failure",
            runtime_diagnostics=normalize_sdk_runtime_diagnostics(
                runtime_diagnostics(
                    sdk={
                        "exception_type": "RuntimeError",
                        "exception_message": f"failure-{index}-" + "x" * 7_000,
                    }
                )
            ),
            received_at=NOW,
        )
        observation_ids.append(observation["observation_id"])
        payload, _ = merge_run_diagnostics(payload, observation)

    retained = [item["observation_id"] for item in payload["observations"]]
    assert retained[0] == observation_ids[0]
    assert retained[-1] == observation_ids[-1]
    assert payload["counts"]["omitted_observations"] > 0
    assert payload["coverage"] == "partial"
    assert json_size_bytes(payload) <= RUN_DIAGNOSTICS_MAX_BYTES


def test_observation_limit_reports_loss_even_when_byte_budget_is_not_exhausted():
    payload = None
    for index in range(65):
        observation = build_failure_observation(
            attempt_id=f"attempt-{index}",
            source="worker_executor",
            stage="terminalization",
            error_code="executor_failure",
            runtime_diagnostics=normalize_sdk_runtime_diagnostics(
                runtime_diagnostics()
            ),
            received_at=NOW,
        )
        payload, _ = merge_run_diagnostics(payload, observation)

    assert payload["coverage"] == "partial"
    assert payload["counts"] == {
        "retained_observations": 64,
        "omitted_observations": 1,
    }
    assert {
        "field": "observations",
        "reason": "observation_limit_truncated",
        "count": 1,
    } in payload["losses"]
    assert json_size_bytes(payload) <= RUN_DIAGNOSTICS_MAX_BYTES


def test_observation_identity_keeps_distinct_capture_boundaries():
    normalized = normalize_sdk_runtime_diagnostics(runtime_diagnostics())
    worker = build_failure_observation(
        attempt_id="attempt-a",
        source="worker_executor",
        stage="terminalization",
        error_code="executor_failure",
        runtime_diagnostics=normalized,
        received_at=NOW,
    )
    callback = build_failure_observation(
        attempt_id="attempt-a",
        source="executor_callback",
        stage="terminal_receipt",
        error_code="executor_failure",
        runtime_diagnostics=normalized,
        received_at=NOW,
    )

    assert worker["observation_id"] != callback["observation_id"]


def test_non_finite_diagnostic_values_degrade_before_run_observation_hashing():
    normalized = normalize_sdk_runtime_diagnostics(
        runtime_diagnostics(sdk={"errors": {"latency": float("nan")}})
    )

    observation = build_failure_observation(
        attempt_id="attempt-a",
        source="worker_executor",
        stage="terminalization",
        error_code="executor_failure",
        runtime_diagnostics=normalized,
        received_at=NOW,
    )

    assert observation["runtime_diagnostics"]["sdk"]["errors"] is None
    assert {
        "field": "sdk.errors",
        "reason": "invalid_field",
    } in observation["runtime_diagnostics"]["normalization_losses"]


@pytest.mark.asyncio
async def test_capture_redacts_private_tool_values_before_storage_and_admin_projection():
    persistence = InMemoryDiagnostics()
    service = RunDiagnosticsService(
        persistence=persistence,
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        clock=lambda: NOW,
    )
    private_token = "sk-private-secret-value"
    private_path = "/home/operator/private/project/tool.py"
    await service.capture_failure_result(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        source="worker_executor",
        stage="terminalization",
        error_code="executor_failure",
        result_json={
            "runtime_diagnostics": runtime_diagnostics(
                sdk={
                    "exception_type": "RuntimeError",
                    "exception_message": f"api_key={private_token}",
                    "exception_traceback": f'{private_path}:42 token="{private_token}"',
                    "errors": {"prompt": "private prompt", "message": private_token},
                },
                tool_calls=[
                    {
                        "tool_name": "Bash",
                        "invocation_id": "tool-a",
                        "state": "failed",
                        "tool_input": {
                            "command": f"cat {private_path}",
                            "token": private_token,
                        },
                        "failure": {"message": private_token},
                    }
                ],
            )
        },
    )

    serialized = str(persistence.payload)
    assert private_token not in serialized
    assert private_path not in serialized
    assert "private prompt" not in serialized
    assert "tool_input" not in serialized
    assert {
        "field": "tool_calls",
        "reason": "redacted",
        "count": 1,
    } in persistence.payload["observations"][0]["runtime_diagnostics"][
        "normalization_losses"
    ]

    persistence.snapshot = {
        "run": {
            "run_id": "run-a",
            "session_id": "session-a",
            "user_id": "user-a",
            "workspace_id": "workspace-a",
            "status": "failed",
            "trace_id": None,
            "created_at": NOW,
            "queued_at": NOW,
            "started_at": NOW,
            "finished_at": NOW,
            "error_code": "executor_failure token=private-run-token",
        },
        "result_json": {},
        "diagnostic": {
            "diagnostic_id": "rdiag-a",
            "schema_version": RUN_DIAGNOSTICS_SCHEMA_VERSION,
            "revision": 1,
            "payload_json": persistence.payload,
        },
        "attempts": [
            {
                "attempt_id": "attempt-a",
                "ordinal": 1,
                "status": "failed",
                "owner_kind": "queue_worker",
                "started_at": NOW,
                "finished_at": NOW,
                "terminal_reason": "failed at /home/operator/private/run.py",
                "error_code": "provider token=private-attempt-token",
            }
        ],
    }
    response = await service.read_admin(object(), tenant_id="tenant-a", run_id="run-a")

    assert private_token not in str(response)
    assert private_path not in str(response)
    assert "tool_input" not in str(response)
    assert "private-run-token" not in str(response)
    assert "private-attempt-token" not in str(response)
    assert "/home/operator/private/run.py" not in str(response)


@pytest.mark.asyncio
async def test_protocol_failure_capture_keeps_structure_and_drops_reported_values():
    persistence = InMemoryDiagnostics()
    service = RunDiagnosticsService(
        persistence=persistence,
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        clock=lambda: NOW,
    )
    private_marker = "sk-private-terminal-marker"
    private_path = "/home/operator/private/result.json"

    await service.capture_failure_result(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        source="worker_executor",
        stage="terminalization",
        error_code="provider_timeout",
        result_json={"runtime_diagnostics": runtime_diagnostics()},
    )

    await service.capture_executor_protocol_failure(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        lease_id="lease-a",
        task_status="callback_failed",
        terminal_result={
            "status": "completed",
            "run_id": "run-other",
            "message": private_marker,
            "answer_receipt": {
                "message_id": private_marker,
                "path": private_path,
            },
            "error_message": private_path,
            "secret_extra_name": private_marker,
        },
        validation_errors=[
            {
                "loc": ("answer_receipt", "message_id"),
                "type": "value_error",
                "msg": f"invalid receipt token={private_marker} at {private_path}",
                "input": {"secret": private_marker},
            }
        ],
    )

    serialized = str(persistence.payload)
    assert private_marker not in serialized
    assert private_path not in serialized
    assert "secret_extra_name" not in serialized
    protocol = persistence.payload["observations"][1]["runtime_diagnostics"][
        "executor_protocol"
    ]
    assert protocol["reported"]["task_status"] == "callback_failed"
    assert protocol["reported"]["terminal_status"] == "completed"
    assert protocol["reported"]["run_id_matches"] is False
    assert protocol["reported"]["fields"]["message"] == {
        "present": True,
        "type": "string",
        "bytes": len(private_marker),
        "non_empty": True,
    }
    assert protocol["reported"]["fields"]["answer_receipt"] == {
        "present": True,
        "type": "object",
        "items": 2,
    }
    assert protocol["reported"]["additional_field_count"] == 1
    assert protocol["validation"] == [
        {
            "location": "answer_receipt.message_id",
            "type": "value_error",
            "message": "Terminal result violates a protocol rule",
        }
    ]
    assert protocol["canonical"]["error_code"] == "executor_protocol_invalid"

    malformed_protocol = deepcopy(protocol)
    malformed_protocol["canonical"]["message_non_empty"] = 0
    persistence.payload["observations"][0]["runtime_diagnostics"][
        "executor_protocol"
    ] = malformed_protocol

    persistence.snapshot = {
        "run": {
            "run_id": "run-a",
            "session_id": "session-a",
            "user_id": "user-a",
            "workspace_id": "workspace-a",
            "status": "failed",
            "trace_id": None,
            "created_at": NOW,
            "queued_at": NOW,
            "started_at": NOW,
            "finished_at": NOW,
            "error_code": "executor_protocol_invalid",
        },
        "result_json": {},
        "diagnostic": {
            "diagnostic_id": "rdiag-a",
            "schema_version": RUN_DIAGNOSTICS_SCHEMA_VERSION,
            "revision": 1,
            "payload_json": persistence.payload,
        },
        "attempts": [],
    }
    response = await service.read_admin(object(), tenant_id="tenant-a", run_id="run-a")

    assert response["root"]["error_code"] == "provider_timeout"
    assert response["details"]["executor_protocol"] == protocol
    assert [item["error_code"] for item in response["handling"]].count(
        "executor_protocol_invalid"
    ) == 1
    assert private_marker not in str(response)
    assert private_path not in str(response)


def test_protocol_diagnostics_drops_unrecognized_status_values():
    private_marker = "untrusted-status-value"

    evidence = build_executor_protocol_diagnostics(
        runtime_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        task_status=private_marker,
        terminal_result={"status": private_marker, "run_id": "run-a"},
        expected_run_id="run-a",
        validation_errors=[{"loc": [], "type": "value_error", "msg": private_marker}],
    )

    reported = evidence["executor_protocol"]["reported"]
    assert reported["task_status"] is None
    assert reported["terminal_status"] is None
    assert private_marker not in str(evidence)

    evidence["executor_protocol"]["validation"][0]["type"] = []
    evidence["executor_protocol"]["validation"][0]["location"] = "9" * 5_000
    assert "executor_protocol" not in sanitize_runtime_diagnostics(evidence)


@pytest.mark.asyncio
async def test_admin_projection_returns_structured_root_attempts_and_losses():
    normalized = normalize_sdk_runtime_diagnostics(runtime_diagnostics())
    observation = build_failure_observation(
        attempt_id="attempt-a",
        source="worker_executor",
        stage="terminalization",
        error_code="executor_http_failure",
        runtime_diagnostics=normalized,
        received_at=NOW,
    )
    payload, _ = merge_run_diagnostics(None, observation)
    snapshot = {
        "run": {
            "run_id": "run-a",
            "session_id": "session-a",
            "user_id": "user-a",
            "workspace_id": "workspace-a",
            "status": "failed",
            "trace_id": "trace-a",
            "created_at": NOW,
            "queued_at": NOW,
            "started_at": NOW,
            "finished_at": NOW,
            "error_code": "executor_http_failure",
        },
        "result_json": {"message": "safe"},
        "diagnostic": {
            "diagnostic_id": "rdiag-a",
            "schema_version": RUN_DIAGNOSTICS_SCHEMA_VERSION,
            "revision": 1,
            "payload_json": payload,
        },
        "attempts": [
            {
                "attempt_id": "attempt-a",
                "ordinal": 1,
                "status": "failed",
                "owner_kind": "queue_worker",
                "started_at": NOW,
                "finished_at": NOW,
                "terminal_reason": "run_failed",
                "error_code": "executor_http_failure",
            }
        ],
    }
    service = RunDiagnosticsService(
        persistence=InMemoryDiagnostics(snapshot),
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )

    response = await service.read_admin(object(), tenant_id="tenant-a", run_id="run-a")

    assert response["coverage"] == "full"
    assert response["root"]["stage"] == "model_wait"
    assert response["root"]["exception_type"] == "TimeoutError"
    assert response["root"]["stack"].endswith("TimeoutError: model wait expired")
    assert response["attempts"][0]["attempt_id"] == "attempt-a"
    assert response["details"]["sdk"]["exception_message"] == "model wait expired"


@pytest.mark.asyncio
async def test_admin_projection_marks_legacy_and_absent_records_explicitly():
    base = {
        "run": {
            "run_id": "run-a",
            "session_id": "session-a",
            "user_id": "user-a",
            "workspace_id": "workspace-a",
            "status": "cancelled",
            "trace_id": "trace-a",
            "created_at": NOW,
            "queued_at": NOW,
            "started_at": NOW,
            "finished_at": NOW,
            "error_code": None,
        },
        "diagnostic": None,
        "attempts": [],
    }
    service = RunDiagnosticsService(
        persistence=InMemoryDiagnostics(
            {**base, "result_json": {"runtime_diagnostics": runtime_diagnostics()}},
        ),
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )
    legacy = await service.read_admin(object(), tenant_id="tenant-a", run_id="run-a")

    service = RunDiagnosticsService(
        persistence=InMemoryDiagnostics({**base, "result_json": {}}),
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )
    absent = await service.read_admin(object(), tenant_id="tenant-a", run_id="run-a")

    assert legacy["coverage"] == "legacy_record"
    assert legacy["root"]["error_code"] == "provider_timeout"
    assert absent["coverage"] == "not_collected"
    assert absent["root"] is None
    assert absent["details"] == {
        "schema_version": None,
        "sdk": {},
        "tool_lifecycles": [],
        "tool_calls": [],
        "tool_policy_denials": [],
        "executor_protocol": None,
    }


@pytest.mark.asyncio
async def test_admin_projection_marks_empty_legacy_carrier_as_legacy_record():
    snapshot = {
        "run": {
            "run_id": "run-a",
            "session_id": "session-a",
            "user_id": "user-a",
            "workspace_id": "workspace-a",
            "status": "failed",
            "trace_id": None,
            "created_at": NOW,
            "queued_at": NOW,
            "started_at": NOW,
            "finished_at": NOW,
            "error_code": "executor_failure",
        },
        "result_json": {"runtime_diagnostics": {}},
        "diagnostic": None,
        "attempts": [],
    }
    service = RunDiagnosticsService(
        persistence=InMemoryDiagnostics(snapshot),
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )

    response = await service.read_admin(object(), tenant_id="tenant-a", run_id="run-a")

    assert response["coverage"] == "legacy_record"
    assert response["root"]["error_code"] == "runtime_diagnostics_rejected"
