import asyncio
import io
import json
import zipfile
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.platform.tracing import safe_exception_log_fields

from app.platform.postgres.limits import json_size_bytes
from app.runs.application.diagnostic_export import build_admin_diagnostic_export
from app.runs.application.diagnostics import RunDiagnosticsService
from app.runs.domain import diagnostics as runs_diagnostics_contract
from app.runs.domain.diagnostics import (
    RUN_DIAGNOSTICS_MAX_BYTES,
    RUN_DIAGNOSTICS_SCHEMA_VERSION,
    build_executor_protocol_diagnostics,
    build_failure_observation,
    merge_run_diagnostics,
    sanitize_runtime_diagnostics,
)
from app.runs.infrastructure.diagnostics_postgres import PostgresRunDiagnosticsRepository
from app.sandbox.api import (
    SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    normalize_sdk_runtime_diagnostics,
)
from app.sandbox.domain import runtime_diagnostics as sandbox_diagnostics_contract


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
async def test_admin_monitor_metadata_read_is_deduplicated_and_bounded():
    class MetadataPersistence(InMemoryDiagnostics):
        async def get_admin_monitor_metadata(self, conn, **kwargs):
            self.calls.append((conn, kwargs))
            return {"run-0": {"session_title": "Readable task"}}

    persistence = MetadataPersistence()
    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=persistence,
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )
    conn = object()

    result = await service.read_admin_monitor_metadata(
        conn,
        tenant_id="tenant-a",
        run_ids=["", " run-0 ", "run-0", *[f"run-{index}" for index in range(1, 105)]],
    )

    assert result == {"run-0": {"session_title": "Readable task"}}
    assert persistence.calls[0][0] is conn
    assert persistence.calls[0][1]["tenant_id"] == "tenant-a"
    assert persistence.calls[0][1]["run_ids"][:2] == ("run-0", "run-1")
    assert len(persistence.calls[0][1]["run_ids"]) == 100


@pytest.mark.asyncio
async def test_postgres_admin_monitor_metadata_is_tenant_scoped_and_batched():
    class MetadataCursor:
        async def fetchall(self):
            return [
                {
                    "run_id": "run-a",
                    "session_title": "审核采购合同",
                    "task_summary": "检查付款条款",
                    "user_display_name": "王敏",
                    "workspace_name": "法务工作区",
                    "agent_name": "合同审阅助手",
                    "skill_name": "文档审阅",
                    "latency_ms": 320,
                    "input_token_count": 10,
                    "output_token_count": 20,
                    "total_token_count": 30,
                    "estimated_cost_minor": 4,
                    "model_value": "model-a",
                    "copied_from_run_id": None,
                    "trace_id_recorded": True,
                }
            ]

    class MetadataConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return MetadataCursor()

    conn = MetadataConnection()
    repository = PostgresRunDiagnosticsRepository()

    result = await repository.get_admin_monitor_metadata(
        conn,
        tenant_id="tenant-a",
        run_ids=("run-a", "run-b"),
    )

    sql, params = conn.calls[0]
    assert "left join sessions" in sql
    assert "left join agent_profile_revisions admitted_profile" in sql
    assert "where runs.tenant_id = %s and runs.id = any(%s::text[])" in sql
    assert params == ("tenant-a", ["run-a", "run-b"])
    assert result["run-a"]["session_title"] == "审核采购合同"
    assert result["run-a"]["trace_id_recorded"] is True
    assert "run_id" not in result["run-a"]


@pytest.mark.asyncio
async def test_capture_moves_private_diagnostics_out_of_terminal_result_and_is_idempotent():
    persistence = InMemoryDiagnostics()
    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=persistence,
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        clock=lambda: NOW,
    )
    conn = object()
    result = {
        "message": "safe public result",
        "runtime_diagnostics": runtime_diagnostics(sdk={"exception_message": "model wait expired", "api_error_status": 429}),
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

    assert persistence.payload["observations"][0]["runtime_diagnostics"]["sdk"]["api_error_status"] == 429


@pytest.mark.asyncio
@pytest.mark.parametrize("private_diagnostics", [None, {}])
async def test_successful_terminal_without_diagnostics_does_not_create_failure(
    private_diagnostics,
):
    persistence = InMemoryDiagnostics()
    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=persistence,
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        clock=lambda: NOW,
    )

    public_result = await service.capture_failure_result(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        source="executor_callback",
        stage="terminal_receipt",
        error_code="completed",
        result_json={"status": "completed", "runtime_diagnostics": private_diagnostics},
    )

    assert public_result == {"status": "completed"}
    assert persistence.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("private_diagnostics", "expected_code"),
    [
        (runtime_diagnostics(), "provider_timeout"),
        *[(value, "runtime_diagnostics_rejected")
          for value in ({"sdk": {}}, {"schema_version": "future"}, [], "", False)],
    ],
)
async def test_successful_terminal_retains_diagnostic_evidence(
    private_diagnostics, expected_code,
):
    persistence = InMemoryDiagnostics()
    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=persistence,
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        clock=lambda: NOW,
    )

    public_result = await service.capture_failure_result(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        source="executor_callback",
        stage="terminal_receipt",
        error_code="completed",
        result_json={"status": "completed", "runtime_diagnostics": private_diagnostics},
    )

    assert public_result == {"status": "completed"}
    assert len(persistence.calls) == 1
    assert persistence.payload["observations"][0]["runtime_diagnostics"]["error_code"] == expected_code


def test_run_sanitizer_preserves_bounded_source_and_stage_labels():
    projected = sanitize_runtime_diagnostics(
        runtime_diagnostics(
            failure_source="sdk exception",
            failure_stage="model wait",
        )
    )
    observation = build_failure_observation(
        attempt_id=None,
        source="queue admission",
        stage="enqueue rejection",
        error_code="queue_enqueue_failed",
        runtime_diagnostics=projected,
        received_at=NOW,
    )

    assert projected["failure_source"] == "sdk exception"
    assert projected["failure_stage"] == "model wait"
    assert observation["source"] == "queue admission"
    assert observation["stage"] == "enqueue rejection"


def test_projection_failure_survives_sandbox_and_runs_diagnostic_boundaries():
    expected = {
        "reason": "raw_delta_conflict",
        "stage": "message",
        "location": "answer_delta",
    }
    sandbox_projection = normalize_sdk_runtime_diagnostics(
        runtime_diagnostics(projection_failure=expected)
    )
    runs_projection = sanitize_runtime_diagnostics(sandbox_projection)
    observation = build_failure_observation(
        attempt_id="attempt-a",
        source="worker_executor",
        stage="terminalization",
        error_code="claude_agent_sdk_output_validation_failed",
        runtime_diagnostics=runs_projection,
        received_at=NOW,
    )

    repeated_sandbox_projection = normalize_sdk_runtime_diagnostics(
        observation["runtime_diagnostics"]
    )

    assert sandbox_projection["projection_failure"] == expected
    assert observation["runtime_diagnostics"]["projection_failure"] == expected
    assert repeated_sandbox_projection["projection_failure"] == expected


def test_raw_frame_shape_label_contracts_match_across_boundaries():
    assert runs_diagnostics_contract._RUN_RAW_FRAME_LABELS == (
        sandbox_diagnostics_contract._RAW_FRAME_SHAPE_LABELS
    )
    assert runs_diagnostics_contract._RUN_RAW_FRAME_OBSERVATION_LABELS == (
        sandbox_diagnostics_contract._RAW_FRAME_OBSERVATION_LABELS
    )


def test_raw_frame_shape_survives_sandbox_runs_and_rejects_untrusted_values():
    shape = {
        "event_type": "content_block_delta", "block_type": "other",
        "delta_type": "text_delta", "message_state": "open",
        "open_block_type": "tool_use", "index_state": "ignored",
        "guard": "block_delta_type",
    }
    preceding = {
        "event_type": "content_block_start", "block_type": "tool_use",
        "delta_type": "other", "message_state": "open",
        "open_block_type": "none", "index_state": "other",
    }
    failure = {
        "reason": "raw_frame_invalid", "stage": "message",
        "location": "raw_stream_frame", "frame_shape": shape,
        "preceding_frames": [preceding],
    }
    projected = normalize_sdk_runtime_diagnostics(
        runtime_diagnostics(projection_failure=failure)
    )
    observation = build_failure_observation(
        attempt_id="attempt-a", source="worker_executor", stage="terminalization",
        error_code="claude_agent_sdk_output_validation_failed",
        runtime_diagnostics=sanitize_runtime_diagnostics(projected), received_at=NOW,
    )
    assert observation["runtime_diagnostics"]["projection_failure"] == failure
    assert normalize_sdk_runtime_diagnostics(
        observation["runtime_diagnostics"]
    )["projection_failure"] == failure
    assert "frame_shape" not in sanitize_runtime_diagnostics({
        "projection_failure": {**failure, "frame_shape": {**shape, "guard": "private_token"}},
    })["projection_failure"]
    for invalid in ([preceding] * 5, [{**preceding, "index": "private_token"}]):
        assert "preceding_frames" not in sanitize_runtime_diagnostics({
            "projection_failure": {**failure, "preceding_frames": invalid},
        })["projection_failure"]


@pytest.mark.asyncio
async def test_capture_diagnostic_failure_does_not_veto_public_terminal_result():
    class FailingPersistence:
        async def append_observation(self, _conn, **_kwargs):
            raise RuntimeError("diagnostic database is unavailable")

    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=FailingPersistence(),
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        clock=lambda: NOW,
    )

    public_result = await service.capture_failure_result(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        source="worker_executor",
        stage="terminalization",
        error_code="executor_failure",
        result_json={
            "message": "safe public result",
            "runtime_diagnostics": runtime_diagnostics(),
        },
    )

    await service.capture_executor_protocol_failure(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        lease_id="lease-a",
        task_status="failed",
        terminal_result={"status": "invalid"},
        validation_errors=[],
    )

    assert public_result == {"message": "safe public result"}


@pytest.mark.asyncio
async def test_capture_diagnostic_normalizer_failure_still_strips_private_carrier():
    def fail_normalization(_value):
        raise ValueError("normalization failed")

    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=InMemoryDiagnostics(),
        normalize_runtime_diagnostics=fail_normalization,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )

    public_result = await service.capture_failure_result(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id=None,
        source="queue admission",
        stage="enqueue rejection",
        error_code="queue_enqueue_failed",
        result_json={
            "message": "safe",
            "nested": {"runtime_diagnostics": {"token": "private"}},
            "runtime_diagnostics": {"token": "private"},
        },
    )

    assert public_result == {"message": "safe", "nested": {}}


@pytest.mark.asyncio
async def test_capture_treats_callable_private_carrier_as_data():
    called = False

    def private_carrier():
        nonlocal called
        called = True
        return runtime_diagnostics()

    persistence = InMemoryDiagnostics()
    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=persistence,
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )

    public_result = await service.capture_failure_result(
        object(),
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id=None,
        source="run_admission",
        stage="queue_enqueue",
        error_code="queue_enqueue_failed",
        result_json={
            "message": "safe",
            "runtime_diagnostics": private_carrier,
        },
    )

    assert public_result == {"message": "safe"}
    assert called is False
    assert persistence.payload["observations"][0]["error_code"] == (
        "queue_enqueue_failed"
    )


@pytest.mark.asyncio
async def test_capture_does_not_swallow_cancellation():
    class CancelledPersistence:
        async def append_observation(self, _conn, **_kwargs):
            raise asyncio.CancelledError

    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=CancelledPersistence(),
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )

    with pytest.raises(asyncio.CancelledError):
        await service.capture_failure_result(
            object(),
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            source="worker_executor",
            stage="terminalization",
            error_code="executor_failure",
            result_json={"runtime_diagnostics": runtime_diagnostics()},
        )


@pytest.mark.asyncio
async def test_reconciliation_keeps_original_failure_and_appends_classified_handling():
    persistence = InMemoryDiagnostics()
    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
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
            "error_code": "terminal_reconciliation_failed",
        },
        "result_json": {},
        "diagnostic": {
            "diagnostic_id": "rdiag-a",
            "schema_version": RUN_DIAGNOSTICS_SCHEMA_VERSION,
            "revision": 2,
            "payload_json": persistence.payload,
        },
        "attempts": [],
    }
    projected = await service.read_admin(
        object(), tenant_id="tenant-a", run_id="run-a"
    )

    assert projected["details"]["observations"][1]["sdk"]["errors"] == [
        "artifact_manifest_invalid"
    ]
    assert projected["details"]["observations"][1]["attempt_id"] == "attempt-a"
    assert [item["error_code"] for item in projected["handling"]].count(
        "terminal_reconciliation_failed"
    ) == 1


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
                        "api_error_status": 529,
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

    assert all(item["runtime_diagnostics"]["sdk"]["api_error_status"] == 529 for item in payload["observations"])


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
    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
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
@pytest.mark.parametrize("has_failure_observations", [False, True])
async def test_admin_read_redacts_stored_sdk_fallbacks_in_root_handling_and_export(
    has_failure_observations,
):
    observations = []
    for index, (source, stage, code) in enumerate(
        (
            ("executor_callback", "terminal_receipt", "provider_timeout"),
            ("executor_reconciler", "terminalization", "terminal_reconciliation_failed"),
        )
    ):
        evidence = runtime_diagnostics(
            error_code=code,
            failure_source=source,
            failure_stage=stage,
            sdk={
                "exception_type": "RuntimeError",
                "exception_message": f"retained failure token=STORED_SECRET_{index}",
                "exception_traceback": f"https://private-{index}.example.invalid/trace",
            },
            failure_observations=(
                [{"error_code": code, "failure_source": source, "failure_stage": stage}]
                if has_failure_observations else []
            ),
        )
        observations.append({
            "observation_id": f"obs-{index}",
            "attempt_id": "attempt-a",
            "source": source,
            "stage": stage,
            "error_code": code,
            "received_at": NOW.isoformat(),
            "runtime_diagnostics": evidence,
        })
    # Model retained data from an earlier policy, not the already-redacted writer.
    payload = {
        **runs_diagnostics_contract.empty_run_diagnostics_payload(),
        "observations": observations,
        "counts": {"retained_observations": 2, "omitted_observations": 0},
    }
    snapshot = {
        "run": {"run_id": "run-a", "status": "failed"},
        "result_json": {},
        "diagnostic": {
            "diagnostic_id": "rdiag-a",
            "schema_version": RUN_DIAGNOSTICS_SCHEMA_VERSION,
            "revision": 2,
            "payload_json": payload,
        },
        "attempts": [],
    }
    stored_snapshot = deepcopy(snapshot)
    service = RunDiagnosticsService(
        exception_log_fields=safe_exception_log_fields,
        persistence=InMemoryDiagnostics(snapshot),
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )

    response = await service.read_admin(object(), tenant_id="tenant-a", run_id="run-a")

    assert snapshot == stored_snapshot
    assert response["root"]["error_code"] == "provider_timeout"
    assert len(response["handling"]) == 1
    assert response["handling"][0]["error_code"] == "terminal_reconciliation_failed"
    for failure in [response["root"], *response["handling"]]:
        assert failure["exception_type"] == "RuntimeError"
        assert failure["message"] == "retained failure token=[redacted-secret]"
        assert failure["stack"] == "https://[redacted-host]"
    for evidence in response["details"]["observations"]:
        assert evidence["sdk"]["exception_message"] == response["root"]["message"]
        assert evidence["sdk"]["exception_traceback"] == response["root"]["stack"]
    assert "STORED_SECRET_" not in str(response)
    assert ".example.invalid" not in str(response)

    package = build_admin_diagnostic_export(
        diagnostics=response, export_id="export-a", generated_at=NOW,
    )
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        exported = json.loads(archive.read("diagnostics.json"))
    assert exported["root"] == response["root"]
    assert exported["handling"] == response["handling"]
    assert "STORED_SECRET_" not in str(exported)
    assert ".example.invalid" not in str(exported)


@pytest.mark.asyncio
async def test_protocol_failure_capture_keeps_structure_and_drops_reported_values():
    persistence = InMemoryDiagnostics()
    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
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
    projection_failure = {
        "reason": "raw_frame_invalid", "stage": "message", "location": "raw_stream_frame",
        "frame_shape": {
            "event_type": "content_block_delta", "block_type": "other",
            "delta_type": "text_delta", "message_state": "open",
            "open_block_type": "tool_use", "index_state": "ignored",
            "guard": "block_delta_type",
        },
        "preceding_frames": [{
            "event_type": "content_block_start", "block_type": "tool_use",
            "delta_type": "other", "message_state": "open",
            "open_block_type": "none", "index_state": "other",
        }],
    }
    normalized = normalize_sdk_runtime_diagnostics(runtime_diagnostics(
        projection_failure={**projection_failure, "body": "PRIVATE_PAYLOAD_MARKER"},
    ))
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
    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=InMemoryDiagnostics(snapshot),
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )

    response = await service.read_admin(object(), tenant_id="tenant-a", run_id="run-a")

    assert response["coverage"] == "partial"
    assert {"field": "projection_failure", "reason": "unknown_fields_dropped", "count": 1} in response["losses"]
    assert response["root"]["stage"] == "model_wait"
    assert response["root"]["exception_type"] == "TimeoutError"
    assert response["root"]["stack"].endswith("TimeoutError: model wait expired")
    assert response["attempts"][0]["attempt_id"] == "attempt-a"
    assert response["details"]["sdk"]["exception_message"] == "model wait expired"
    assert response["details"]["projection_failure"] == projection_failure
    assert response["details"]["observations"][0]["projection_failure"] == projection_failure
    assert "PRIVATE_PAYLOAD_MARKER" not in str(response)

    package = build_admin_diagnostic_export(
        diagnostics=response, export_id="export-a", generated_at=NOW,
    )
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        exported = json.loads(archive.read("diagnostics.json"))
    assert exported["details"]["projection_failure"] == projection_failure
    assert exported["details"]["observations"][0]["projection_failure"] == projection_failure
    assert "PRIVATE_PAYLOAD_MARKER" not in str(exported)


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
    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=InMemoryDiagnostics(
            {**base, "result_json": {"runtime_diagnostics": runtime_diagnostics()}},
        ),
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )
    legacy = await service.read_admin(object(), tenant_id="tenant-a", run_id="run-a")

    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
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
        "projection_failure": None,
        "observations": [],
    }


@pytest.mark.asyncio
async def test_admin_projection_marks_empty_legacy_carrier_as_not_collected():
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
    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=InMemoryDiagnostics(snapshot),
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )

    response = await service.read_admin(object(), tenant_id="tenant-a", run_id="run-a")

    assert response["coverage"] == "not_collected"
    assert response["root"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("run_id", ["run-correlation-fixture", "run-correlation-other"])
async def test_admin_tool_correlation_matches_native_public_projection_without_storage_changes(run_id):
    from app.execution.api import ClaudeSdkAgentEventAdapter, public_tool_operation_id
    from app.platform.public_payload import sanitize_public_answer_text, sanitize_public_event_candidate
    from app.runs.application.admin_run_monitor import build_admin_worker_execution

    native_id = "sdk-native-call-1"
    adapter = ClaudeSdkAgentEventAdapter(
        run_id=run_id, attempt_id="attempt-1",
        sanitizer=sanitize_public_answer_text, payload_sanitizer=sanitize_public_event_candidate,
        authorized_capabilities={"Read": ("read", "Read file")},
    )
    hook = {"tool_name": "Read", "tool_use_id": native_id, "tool_input": {"file_path": "/private/fixture"}}
    candidate, = adapter.accept_hook("PreToolUse", hook, tool_use_id=native_id)
    public = build_admin_worker_execution([
        {"type": candidate.event_type, "payload": dict(candidate.payload), "sequence": 1, "visible_to_user": True},
    ], sanitize_text=lambda value: value)
    raw = runtime_diagnostics(tool_calls=[{
        "tool_name": "Read", "invocation_id": native_id, "last_stage": "failed",
        "tool_input": {"prompt": "PRIVATE_MARKER"}, "failure": {"error": "PRIVATE_MARKER"},
    }])
    snapshot = {"run": {"run_id": run_id}, "result_json": {"runtime_diagnostics": raw}, "attempts": []}
    before = deepcopy(snapshot)
    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=InMemoryDiagnostics(snapshot), normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        tool_operation_id=public_tool_operation_id,
    )
    response = await service.read_admin(object(), tenant_id="tenant-a", run_id=run_id)
    item = response["details"]["tool_calls"][0]
    assert item["operation_id"] == public["actions"][0]["invocation_id"]
    assert item["invocation_id"] == native_id
    assert native_id not in str(candidate.payload)
    assert "PRIVATE_MARKER" not in str(response)
    assert snapshot == before
    assert "operation_id" not in normalize_sdk_runtime_diagnostics(raw)["tool_calls"][0]
    assert item["operation_id"] != public_tool_operation_id(run_id + "-other", native_id)
    assert response["details"]["observations"][0]["tool_calls"][0]["operation_id"] == item["operation_id"]


@pytest.mark.asyncio
async def test_admin_tool_correlation_omits_ambiguous_or_missing_native_identity():
    from app.execution.api import public_tool_operation_id

    snapshot = {"run": {"run_id": "run-a"}, "result_json": {"runtime_diagnostics": runtime_diagnostics(
        tool_calls=[{"tool_name": name, "invocation_id": "duplicate"} for name in ("Read", "Write")],
        tool_policy_denials=[{"tool_name": "Read", "reason": "denied"}],
    )}, "attempts": []}
    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=InMemoryDiagnostics(snapshot), normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        tool_operation_id=public_tool_operation_id,
    )
    response = await service.read_admin(object(), tenant_id="tenant-a", run_id="run-a")
    for item in response["details"]["tool_calls"] + response["details"]["tool_policy_denials"]:
        assert "operation_id" not in item
    assert public_tool_operation_id("run-a", "") is None
    assert public_tool_operation_id(None, "native") is None


def test_bootstrap_uses_execution_tool_operation_identity_owner():
    from app.bootstrap.run_diagnostics import build_run_diagnostics_service
    from app.execution.api import public_tool_operation_id

    assert build_run_diagnostics_service().tool_operation_id is public_tool_operation_id
    # Paired with the mounted Run Monitor fixture; no frontend hash implementation.
    assert public_tool_operation_id("run_running", "tool-call-7") == "op_6aaadf71488c9cf9ca0e4928bbf70f1e"


@pytest.mark.parametrize("severe", [False, True])
def test_run_diagnostic_minimization_preserves_api_status(severe):
    observation = {"runtime_diagnostics": sanitize_runtime_diagnostics(runtime_diagnostics(sdk={"api_error_status": 500}))}
    runs_diagnostics_contract._minimize_observation(observation, severe=severe)
    assert observation["runtime_diagnostics"]["sdk"] == {"api_error_status": 500}


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["normalize", "sanitize", "build", "persist"])
async def test_capture_failure_logs_phase_correlations_and_safe_cause(phase, monkeypatch, caplog):
    from psycopg.errors import SerializationFailure
    from app.runs.application import diagnostics as application

    original = SerializationFailure("SQL SELECT private-prompt password=private-token")
    wrapper = RuntimeError("postgresql://private-user:private-password@private-host/db")
    wrapper.__cause__ = original
    def fail(*_args, **_kwargs):
        raise wrapper

    class Persistence(InMemoryDiagnostics):
        async def append_observation(self, conn, **kwargs):
            self.calls.append((conn, kwargs))
            fail()

    persistence = Persistence()
    normalizer = fail if phase == "normalize" else normalize_sdk_runtime_diagnostics
    if phase == "sanitize":
        monkeypatch.setattr(application, "sanitize_runtime_diagnostics", fail)
    if phase == "build":
        monkeypatch.setattr(application, "build_failure_observation", fail)
    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=persistence, normalize_runtime_diagnostics=normalizer,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION, clock=lambda: NOW,
    )
    public = {"status": "failed", "error_code": "mcp_execution_outcome_unknown", "message": "safe"}
    result = await service.capture_failure_result(
        object(), tenant_id="tenant-private", run_id="run_" + "a" * 32,
        attempt_id="rat_" + "b" * 64, source="worker_executor", stage="terminalization",
        error_code=public["error_code"], result_json={**public, "runtime_diagnostics": runtime_diagnostics()},
    )
    assert result == public
    assert wrapper.__cause__ is original
    record = caplog.records[-1]
    assert record.capture_phase == phase
    assert record.run_id == "run_" + "a" * 32
    assert record.attempt_id == "rat_" + "b" * 64
    assert record.exception_chain[1]["sqlstate"] == "40001"
    assert record.exception_chain[1]["relation"] == "cause"
    assert record.exc_info is None
    assert len(persistence.calls) == (1 if phase == "persist" else 0)
    if phase == "persist":
        assert record.observation_id == persistence.calls[0][1]["observation"]["observation_id"]
    else:
        assert not hasattr(record, "observation_id")
    for secret in ("SELECT", "private-prompt", "private-token", "private-password", "tenant-private", "postgresql://"):
        assert secret not in caplog.text
        assert secret not in repr(record.__dict__)


@pytest.mark.asyncio
async def test_protocol_factory_failure_logs_exact_phase_without_database(monkeypatch, caplog):
    from app.runs.application import diagnostics as application

    def fail(**_kwargs):
        raise ValueError("private-prompt")
    monkeypatch.setattr(application, "build_executor_protocol_diagnostics", fail)
    persistence = InMemoryDiagnostics()
    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=persistence, normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )
    await service.capture_executor_protocol_failure(
        object(), tenant_id="tenant", run_id="run_" + "a" * 32, attempt_id=None,
        lease_id=None, task_status="failed", terminal_result={}, validation_errors=[],
    )
    assert caplog.records[-1].capture_phase == "factory"
    assert not hasattr(caplog.records[-1], "attempt_id")
    assert not persistence.calls
    assert "private-prompt" not in caplog.text


@pytest.mark.asyncio
async def test_capture_rejects_private_metadata_and_logger_failure_is_nonfatal(monkeypatch, caplog):
    from app.runs.application import diagnostics as application

    class Hostile(str):
        def __str__(self):
            raise AssertionError("metadata must not render")
    def fail(*_args, **_kwargs):
        raise RuntimeError("private-token")
    service = RunDiagnosticsService(exception_log_fields=safe_exception_log_fields,
        persistence=InMemoryDiagnostics(), normalize_runtime_diagnostics=fail,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )
    kwargs = dict(
        tenant_id="tenant", run_id="private-prompt", attempt_id=Hostile("private-token"),
        source=Hostile("private-source"), stage="/private/path", error_code="executor_failure",
        result_json={"status": "failed", "runtime_diagnostics": runtime_diagnostics()},
    )
    assert await service.capture_failure_result(object(), **kwargs) == {"status": "failed"}
    record = caplog.records[-1]
    assert not hasattr(record, "run_id") and not hasattr(record, "attempt_id")
    assert record.diagnostic_source == record.diagnostic_stage == "unknown"
    assert "private" not in repr(record.__dict__)
    monkeypatch.setattr(application.logger, "warning", fail)
    assert await service.capture_failure_result(object(), **kwargs) == {"status": "failed"}


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["write", "format"])
async def test_capture_sink_failure_stderr_cannot_render_private_chain(failure, monkeypatch, capsys):
    import logging
    from app.runs.application import diagnostics as application

    class BrokenStream:
        def write(self, _value):
            raise OSError("synthetic sink unavailable")
        def flush(self):
            pass

    class BrokenFormatter(logging.Formatter):
        def format(self, _record):
            raise ValueError("synthetic formatter unavailable")

    handler = logging.StreamHandler(BrokenStream())
    if failure == "format":
        handler.setFormatter(BrokenFormatter())
    monkeypatch.setattr(application.logger, "handlers", [handler])
    monkeypatch.setattr(application.logger, "propagate", False)
    monkeypatch.setattr(logging, "raiseExceptions", True)
    cause = ValueError("SYNTHETIC_SECRET_CAUSE")
    original = RuntimeError("SYNTHETIC_SECRET_MARKER")
    original.__cause__ = cause
    def fail(_value):
        raise original
    service = RunDiagnosticsService(
        exception_log_fields=safe_exception_log_fields,
        persistence=InMemoryDiagnostics(), normalize_runtime_diagnostics=fail,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    )
    result = await service.capture_failure_result(
        object(), tenant_id="tenant", run_id="run_" + "a" * 32, attempt_id=None,
        source="worker_executor", stage="terminalization", error_code="executor_failure",
        result_json={"status": "failed", "runtime_diagnostics": runtime_diagnostics()},
    )
    stderr = capsys.readouterr().err
    assert "--- Logging error ---" in stderr
    assert "SYNTHETIC_SECRET_MARKER" not in stderr
    assert "SYNTHETIC_SECRET_CAUSE" not in stderr
    assert original.__cause__ is cause
    assert result == {"status": "failed"}
