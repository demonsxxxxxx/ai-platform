import asyncio
import io
import json
import logging
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
    capture_sdk_runtime_diagnostics,
    normalize_sdk_runtime_diagnostics,
)
from app.sandbox.domain import runtime_diagnostics as sandbox_diagnostics_contract


NOW = datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc)


def _log_payload(record):
    metadata = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
    return {
        "message": record.getMessage(),
        "extra": {key: value for key, value in record.__dict__.items() if key not in metadata},
    }


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


def test_capture_identity_is_stable_through_validation_and_legacy_reads_do_not_mint():
    legacy = normalize_sdk_runtime_diagnostics(runtime_diagnostics())
    assert "observation_id" not in legacy["failure_observations"][0]
    assert normalize_sdk_runtime_diagnostics(legacy) == legacy
    captured = capture_sdk_runtime_diagnostics(legacy)
    assert capture_sdk_runtime_diagnostics(captured) == captured
    assert normalize_sdk_runtime_diagnostics(captured) == captured
    assert sanitize_runtime_diagnostics(captured)["failure_observations"] == captured["failure_observations"]
    assert "observation_id" not in legacy["failure_observations"][0]


def test_invalid_or_ambiguous_observation_ids_are_rejected_without_echo():
    first = {"error_code": "provider_timeout", "observation_id": "obs_" + "1" * 32}
    raw = runtime_diagnostics(failure_observations=[
        {**first, "failure_source": "sdk_exception", "failure_stage": "model_wait",
         "parent_observation_id": "obs_" + "2" * 32},
        {**first, "error_code": "conflicting_failure"},
        {"error_code": "wrapper_failed", "observation_id": "obs_" + "2" * 32,
         "parent_observation_id": "obs_" + "1" * 32},
        {"error_code": "invalid_identity", "observation_id": "PRIVATE_SECRET",
         "parent_observation_id": "PRIVATE_PARENT"},
    ])
    normalized = normalize_sdk_runtime_diagnostics(raw)
    assert "PRIVATE" not in str(normalized)
    assert "conflicting_failure" not in str(normalized)
    assert "parent_observation_id" not in normalized["failure_observations"][0]
    assert {loss["reason"] for loss in normalized["normalization_losses"]} == {"invalid_field", "cycle"}
    assert normalize_sdk_runtime_diagnostics(normalized) == normalized


@pytest.mark.parametrize("receipt_source", ["executor_callback", "executor_probe"])
@pytest.mark.parametrize("same_attempt,same_lease,ambiguous", [
    (True, True, False), (False, True, False), (True, False, False), (True, True, True),
])
def test_reconciliation_parent_is_bound_to_the_recorded_attempt_and_lease(same_attempt, same_lease, ambiguous, receipt_source):
    receipt = build_failure_observation(
        attempt_id="attempt-a", lease_id="lease-a", source=receipt_source, stage="terminal_receipt",
        error_code="executor_failed", runtime_diagnostics=runtime_diagnostics(), received_at=NOW,
    )
    payload, _ = merge_run_diagnostics(None, receipt)
    if ambiguous:
        payload, _ = merge_run_diagnostics(payload, {**receipt, "observation_id": "obs_" + "2" * 32})
    handling = build_failure_observation(
        attempt_id="attempt-a" if same_attempt else "attempt-b",
        lease_id="lease-a" if same_lease else "lease-b",
        source="executor_reconciler", stage="terminalization", error_code="terminal_reconciliation_failed",
        runtime_diagnostics=runtime_diagnostics(), received_at=NOW,
    )
    payload, changed = merge_run_diagnostics(payload, handling)
    assert changed
    if same_attempt and same_lease and not ambiguous:
        assert payload["observations"][-1]["parent_observation_id"] == receipt["observation_id"]
    else:
        assert "parent_observation_id" not in payload["observations"][-1]
        assert payload["losses"][0]["reason"] == "parent_observation_not_collected"
    repeated, changed = merge_run_diagnostics(payload, handling)
    assert not changed and repeated == payload
    if payload["losses"]:
        payload, _ = merge_run_diagnostics(payload, {**handling, "observation_id": "obs_" + "3" * 32})
        assert payload["losses"][0]["count"] == 2


@pytest.mark.parametrize("producer_ids", [False, True])
def test_distinct_receipt_identity_survives_matching_source_stage_and_code(producer_ids):
    from app.runs.application.diagnostics import _project_observations

    capture = capture_sdk_runtime_diagnostics if producer_ids else normalize_sdk_runtime_diagnostics
    receipt = build_failure_observation(
        attempt_id="attempt-a", lease_id="lease-a", source="executor_callback", stage="terminal_receipt",
        error_code="provider_timeout", received_at=NOW,
        runtime_diagnostics=capture(runtime_diagnostics(failure_source="executor_callback", failure_stage="terminal_receipt")),
    )
    handling = build_failure_observation(
        attempt_id="attempt-a", lease_id="lease-a", source="executor_reconciler", stage="terminalization",
        error_code="terminal_reconciliation_failed", received_at=NOW, runtime_diagnostics=runtime_diagnostics(),
    )
    payload, _ = merge_run_diagnostics(None, receipt)
    payload, _ = merge_run_diagnostics(payload, handling)
    root, processing, _details, _losses = _project_observations(payload["observations"])
    identities = {item["observation_id"] for item in [root, *processing]}
    assert receipt["observation_id"] in identities
    assert payload["observations"][-1]["parent_observation_id"] in identities
    assert len(processing) == (2 if producer_ids else 1)


@pytest.mark.asyncio
async def test_origin_chain_survives_receipt_reconciliation_admin_and_export():
    from app.runtime.sandbox.executor_app import _merge_runtime_diagnostics
    from app.runs.application.diagnostics import _project_observations

    origin = capture_sdk_runtime_diagnostics(runtime_diagnostics())
    origin_id = origin["failure_observations"][0]["observation_id"]
    wrapped = _merge_runtime_diagnostics(
        origin, error_code="executor_failed", failure_source="executor_wrapper", failure_stage="sandbox_submission",
    )
    wrapper_id = wrapped["failure_observations"][-1]["observation_id"]
    persistence = InMemoryDiagnostics()
    service = RunDiagnosticsService(
        persistence=persistence, normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        exception_log_fields=safe_exception_log_fields, clock=lambda: NOW,
    )
    context = dict(tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a", lease_id="lease-a")
    public = await service.capture_failure_result(
        object(), **context, source="executor_callback", stage="terminal_receipt", error_code="executor_failed",
        result_json={"status": "failed", "runtime_diagnostics": wrapped},
    )
    await service.capture_reconciliation_failure(
        object(), **context, terminal_result=public, reconciliation_error_code="artifact_manifest_invalid",
    )
    receipt, handling = persistence.payload["observations"]
    assert receipt["parent_observation_id"] == wrapper_id
    assert handling["parent_observation_id"] == receipt["observation_id"]
    root, processing, details, losses = _project_observations(persistence.payload["observations"])
    assert root["observation_id"] == origin_id
    by_id = {item["observation_id"]: item for item in processing}
    assert by_id[wrapper_id]["parent_observation_id"] == origin_id
    assert by_id[receipt["observation_id"]]["parent_observation_id"] == wrapper_id
    assert by_id[handling["observation_id"]]["parent_observation_id"] == receipt["observation_id"]
    diagnostics = {"root": root, "handling": processing, "details": details, "losses": losses}
    package = build_admin_diagnostic_export(diagnostics=diagnostics, export_id="export-a", generated_at=NOW)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert json.loads(archive.read("diagnostics.json")) == diagnostics


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


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_mode", ["none", "factory", "persist"])
async def test_reconciliation_cause_is_private_and_diagnostic_failure_is_nonfatal(failure_mode):
    from app.executor_reconciler import _exception_diagnostic_evidence

    class Persistence(InMemoryDiagnostics):
        async def append_observation(self, conn, **kwargs):
            if failure_mode == "persist" and kwargs["observation"]["stage"] == "terminalization":
                raise OSError("synthetic diagnostic persistence failure")
            return await super().append_observation(conn, **kwargs)

    persistence = Persistence()
    service = RunDiagnosticsService(
        persistence=persistence,
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        exception_log_fields=safe_exception_log_fields,
        clock=lambda: NOW,
    )
    try:
        raise OSError("/private/workspace token=synthetic-token Bearer synthetic-bearer " + "x" * 30_000)
    except OSError as cause:
        try:
            raise ValueError("synthetic reconciliation failure") from cause
        except ValueError as error:
            failure = error

    def evidence_factory():
        if failure_mode == "factory":
            raise ValueError("synthetic extraction failure")
        return _exception_diagnostic_evidence(failure)

    terminal = {"status": "failed", "error_code": "provider_timeout", "runtime_diagnostics": runtime_diagnostics()}
    original = deepcopy(terminal)
    await service.capture_reconciliation_failure(
        object(), tenant_id="tenant-a", run_id="run-a", attempt_id="attempt-a",
        lease_id="lease-a", terminal_result=terminal,
        reconciliation_error_code="artifact_manifest_invalid",
        reconciliation_evidence_factory=evidence_factory,
    )
    assert terminal == original
    observations = persistence.payload["observations"]
    assert observations[0]["runtime_diagnostics"]["error_code"] == "provider_timeout"
    assert len(observations) == (2 if failure_mode == "none" else 1)
    if failure_mode == "none":
        handling = observations[1]
        assert handling["source"] == "executor_reconciler"
        assert handling["stage"] == "terminalization"
        assert handling["attempt_id"] == "attempt-a"
        assert handling["lease_id"] == "lease-a"
        sdk = handling["runtime_diagnostics"]["sdk"]
        assert sdk["errors"] == ["artifact_manifest_invalid"]
        assert [node["type"] for node in sdk["exception_chain"]] == ["ValueError", "OSError"]
        assert sdk["exception_chain"][0]["relation"] == "cause"
        assert "test_reconciliation_cause" in sdk["exception_chain"][1]["traceback"]
        assert json_size_bytes(persistence.payload) <= RUN_DIAGNOSTICS_MAX_BYTES
        assert handling["runtime_diagnostics"]["normalization_losses"]
        from app.run_projection import public_chat_terminal_projection
        public = public_chat_terminal_projection({
            "id": "run-a", "status": "failed", "error_code": "terminal_reconciliation_failed",
            "result_json": {"runtime_diagnostics": {"sdk": _exception_diagnostic_evidence(failure)["sdk"]}},
        })
        assert "exception_chain" not in json.dumps(public)
        for secret in ("synthetic-token", "synthetic-bearer"):
            assert secret not in json.dumps(persistence.payload)
            assert secret not in json.dumps(public)
        assert "/private/workspace" in json.dumps(persistence.payload)
        assert "/private/workspace" not in json.dumps(public)


def test_run_budget_keeps_the_first_and_latest_observations():
    payload = None
    observation_ids = []
    root_ids = []
    for index in range(70):
        observation = build_failure_observation(
            attempt_id=f"attempt-{index}",
            source="worker_executor",
            stage="terminalization",
            error_code="executor_failure",
            runtime_diagnostics=capture_sdk_runtime_diagnostics(
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
        root_ids.append(observation["parent_observation_id"])
        payload, _ = merge_run_diagnostics(payload, observation)

    retained = [item["observation_id"] for item in payload["observations"]]
    assert retained[0] == observation_ids[0]
    assert retained[-1] == observation_ids[-1]
    assert payload["observations"][0]["runtime_diagnostics"]["failure_observations"][0]["observation_id"] == root_ids[0]
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
async def test_capture_masks_credentials_but_keeps_tool_context_for_admin_diagnostics():
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
    evidence = persistence.payload["observations"][0]["runtime_diagnostics"]
    assert evidence["sdk"]["exception_traceback"] == f"{private_path}:42 token=[redacted-secret]"
    assert evidence["sdk"]["errors"] == {"prompt": "private prompt", "message": "[redacted-secret]"}
    assert evidence["tool_calls"][0]["tool_input"] == {
        "command": f"cat {private_path}",
        "token": "[redacted-secret]",
    }
    assert evidence["tool_calls"][0]["failure"] == {"message": "[redacted-secret]"}
    assert not any(loss.get("reason") == "redacted" for loss in evidence["normalization_losses"])

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
    assert "private-run-token" not in str(response)
    assert "private-attempt-token" not in str(response)
    assert response["details"]["tool_calls"][0]["tool_input"]["command"] == f"cat {private_path}"
    assert response["attempts"][0]["terminal_reason"] == "failed at /home/operator/private/run.py"


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
                "exception_traceback": (
                    f"https://operator:STORED_SECRET_{index}@private-{index}.example.invalid/trace"
                    f"?attempt=2&token=STORED_SECRET_{index}"
                ),
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
    # Hosts stay visible for debugging; embedded credentials do not.
    expected_stacks = {
        f"https://[redacted-secret]@private-{stored}.example.invalid/trace"
        "?attempt=2&token=[redacted-secret]"
        for stored in (0, 1)
    }
    for failure in [response["root"], *response["handling"]]:
        assert failure["exception_type"] == "RuntimeError"
        assert failure["message"] == "retained failure token=[redacted-secret]"
        assert failure["stack"] in expected_stacks
    for evidence in response["details"]["observations"]:
        assert evidence["sdk"]["exception_message"] == response["root"]["message"]
        assert evidence["sdk"]["exception_traceback"] in expected_stacks
    assert "STORED_SECRET_" not in str(response)

    package = build_admin_diagnostic_export(
        diagnostics=response, export_id="export-a", generated_at=NOW,
    )
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        exported = json.loads(archive.read("diagnostics.json"))
    assert exported["root"] == response["root"]
    assert exported["handling"] == response["handling"]
    assert "STORED_SECRET_" not in str(exported)
    assert ".example.invalid" in str(exported)


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
    assert item["tool_input"] == {"prompt": "PRIVATE_MARKER"}
    assert "PRIVATE_MARKER" not in str(candidate.payload)
    assert "PRIVATE_MARKER" not in str(public)
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
    captured = capture_sdk_runtime_diagnostics(runtime_diagnostics(sdk={"api_error_status": 500}))
    observation = {"runtime_diagnostics": sanitize_runtime_diagnostics(captured)}
    runs_diagnostics_contract._minimize_observation(observation, severe=severe)
    assert observation["runtime_diagnostics"]["sdk"] == {"api_error_status": 500}
    assert observation["runtime_diagnostics"]["failure_observations"] == captured["failure_observations"]


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
        assert secret not in repr(_log_payload(record))


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
    logged = repr(_log_payload(record))
    for secret in ("private-prompt", "private-token", "private-source", "/private/path"):
        assert secret not in logged
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


@pytest.mark.asyncio
async def test_postgres_admin_snapshot_reads_the_run_window_column():
    """The admin snapshot selects the renamed window column, not the retired one."""

    class SnapshotCursor:
        def __init__(self, row):
            self._row = row

        async def fetchone(self):
            return self._row

        async def fetchall(self):
            return []

    class SnapshotConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((" ".join(sql.split()), params))
            return SnapshotCursor({"run_id": "run-a", "auto_compact_window": 200_000, "max_output_tokens": 2048})

    conn = SnapshotConnection()
    snapshot = await PostgresRunDiagnosticsRepository().get_admin_snapshot(
        conn, tenant_id="tenant-a", run_id="run-a",
    )

    sql, params = conn.calls[0]
    assert "r.auto_compact_window" in sql
    assert "r.max_input_tokens" not in sql
    assert params == ("tenant-a", "run-a")
    assert snapshot["run"]["auto_compact_window"] == 200_000
    assert "max_input_tokens" not in snapshot["run"]


@pytest.mark.parametrize("key", ["password", "API_KEY", "access-token", "Authorization", "bearer", "credential", "private_key", "refresh_token", "secret", "token"])
@pytest.mark.parametrize("key_quote", ["", "'", '"'])
@pytest.mark.parametrize("value_quote", ["", "'", '"'])
def test_diagnostic_assignment_redacts_quoted_keys_and_values(key, key_quote, value_quote):
    secret = "syntheticsecret" + (" tail" if value_quote else "")
    value = f'{key_quote}{key}{key_quote}: {value_quote}{secret}{value_quote}, status=failed'
    expected = f"{key}=[redacted-secret], status=failed"
    assert runs_diagnostics_contract.sanitize_run_diagnostic_text(value) == expected
    for _ in range(4):
        assert runs_diagnostics_contract.sanitize_run_diagnostic_text(expected) == expected


@pytest.mark.parametrize("value, expected", [
    ('{"password": "syntheticsecret", "api_key": "syntheticsecond", "status": "failed"}',
     '{password=[redacted-secret], api_key=[redacted-secret], "status": "failed"}'),
    (r'''password="syntheticsecret\" tail\\secret"; retry=2''', 'password=[redacted-secret]; retry=2'),
    (r"password='syntheticsecret\' tail\\secret'; retry=2", 'password=[redacted-secret]; retry=2'),
    ('password="syntheticsecret tail', 'password=[redacted-secret]'),
    ('password="syntheticsecret\\', 'password=[redacted-secret]'),
    ("password='syntheticsecret\nnext diagnostic line", 'password=[redacted-secret]\nnext diagnostic line'),
    ('password="syntheticsecret\r\nnext diagnostic line', 'password=[redacted-secret]\r\nnext diagnostic line'),
    ('password="syntheticsecret\rnext diagnostic line', 'password=[redacted-secret]\rnext diagnostic line'),
    ("password='syntheticsecret\rnext diagnostic line", 'password=[redacted-secret]\rnext diagnostic line'),
    ('password="syntheticsecret\\\rnext diagnostic line', 'password=[redacted-secret]\rnext diagnostic line'),
    ("password='syntheticsecret\\\rnext diagnostic line", 'password=[redacted-secret]\rnext diagnostic line'),
    ('password=""; status=failed', 'password=[redacted-secret]; status=failed'),
    ('password=[redacted-secret]}syntheticsecret', 'password=[redacted-secret]'),
    ('password=[redacted-secret]]syntheticsecret', 'password=[redacted-secret]'),
    ('password=[redacted-secret]}]', 'password=[redacted-secret]}]'),
    ('password=prefix]syntheticsecret', 'password=[redacted-secret]'),
    ('password=prefix}syntheticsecret', 'password=[redacted-secret]'),
    ('password=abc\"syntheticsecret', 'password=[redacted-secret]'),
    ("password=abc'syntheticsecret", 'password=[redacted-secret]'),
    ('password=syntheticsecret}', 'password=[redacted-secret]'),
    ('[token=syntheticsecret]', '[token=[redacted-secret]'),
    ('status=failed, retry=2; notpassword=normal', 'status=failed, retry=2; notpassword=normal'),
    ('Bearer syntheticsecret', 'Bearer [redacted-secret]'),
    ('https://syntheticuser:syntheticsecret@example.invalid/path?token=syntheticsecond&page=2',
     'https://[redacted-secret]@example.invalid/path?token=[redacted-secret]&page=2'),
    ('postgres://syntheticuser:syntheticsecret@db.example.invalid:5432/app',
     'postgres://[redacted-secret]@db.example.invalid:5432/app'),
    ('https://bucket.example.invalid/a.xlsx?X-Amz-Credential=c&X-Amz-Signature=s&part=1',
     'https://bucket.example.invalid/a.xlsx?X-Amz-Credential=[redacted-secret]&X-Amz-Signature=[redacted-secret]&part=1'),
    ('max_tokens=4096 input_tokens=5 token_count=3', 'max_tokens=4096 input_tokens=5 token_count=3'),
    ('sk-abcdefghijklmnopqrst', '[redacted-secret]'),
    ('eyJabcdefghijk.abcdefghijkl.abcdefghijkl', '[redacted-secret]'),
    ('/home/synthetic/private/tool.py', '/home/synthetic/private/tool.py'),
    (r'C:\synthetic\private\tool.py', r'C:\synthetic\private\tool.py'),
])
def test_diagnostic_assignment_escapes_boundaries_and_existing_rules(value, expected):
    for _ in range(4):
        value = runs_diagnostics_contract.sanitize_run_diagnostic_text(value)
        assert value == expected


@pytest.mark.parametrize("value", [None, 12, {}, [], ""])
def test_diagnostic_text_rejects_non_text_and_empty_values(value):
    assert runs_diagnostics_contract.sanitize_run_diagnostic_text(value) is None


def test_diagnostic_assignment_redacts_before_utf8_bounding_and_handles_long_values():
    text = 'normal ' + '"password": "' + 'syntheticsecret ' * 10000 + '"; status=failed'
    assert runs_diagnostics_contract.sanitize_run_diagnostic_text(text) == 'normal password=[redacted-secret]; status=failed'
    for limit in (0, 1, 16, 32, 4096):
        result = runs_diagnostics_contract.sanitize_run_diagnostic_text(text, max_bytes=limit)
        assert 'synthetic' not in result
        assert len(result.encode()) <= limit
    # Unterminated, escaped tail and repeated key-like text must finish linearly.
    text = 'password="' + ('syntheticsecret\\" password=' * 10000) + '\\'
    assert runs_diagnostics_contract.sanitize_run_diagnostic_text(text) == 'password=[redacted-secret]'


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    '{"password": "syntheticsecret tail", "api_key": "syntheticsecond"}',
    "{'password': 'syntheticsecret tail', 'api_key': 'syntheticsecond'}",
    'password="syntheticsecret tail"; api_key=syntheticsecond',
    'password="syntheticsecret ' + 'tail ' * 2000,
])
async def test_worker_diagnostic_assignment_write_and_admin_read_redaction(message):
    from app.execution.application.worker_failure_diagnostics import executor_exception_failure

    code, public_message, carrier = executor_exception_failure(RuntimeError(message))
    assert public_message == 'Executor failed'
    assert 'syntheticsecret' not in str(carrier)
    assert 'syntheticsecond' not in str(carrier)
    persistence = InMemoryDiagnostics()
    service = RunDiagnosticsService(
        exception_log_fields=safe_exception_log_fields,
        persistence=persistence,
        normalize_runtime_diagnostics=normalize_sdk_runtime_diagnostics,
        runtime_diagnostics_schema_version=SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        clock=lambda: NOW,
    )
    await service.capture_failure_result(
        object(), tenant_id='tenant-a', run_id='run-a', attempt_id='attempt-a',
        source='worker_executor', stage='terminalization', error_code=code,
        result_json=carrier,
    )
    encoded = runs_diagnostics_contract.serialize_run_diagnostics_payload(persistence.payload)
    assert 'syntheticsecret' not in encoded
    assert 'syntheticsecond' not in encoded
    stored = json.loads(encoded)
    persistence.snapshot = {
        'run': {'run_id': 'run-a', 'status': 'failed'}, 'result_json': {},
        'diagnostic': {'diagnostic_id': 'rdiag-a', 'schema_version': RUN_DIAGNOSTICS_SCHEMA_VERSION,
                       'revision': 1, 'payload_json': stored}, 'attempts': [],
    }
    response = await service.read_admin(object(), tenant_id='tenant-a', run_id='run-a')
    assert '[redacted-secret]' in response['root']['message']
    assert 'syntheticsecret' not in str(response)
    assert 'syntheticsecond' not in str(response)

    # Independently exercise read-time protection for old, unsanitized rows.
    evidence = stored['observations'][0]['runtime_diagnostics']
    evidence['sdk']['exception_message'] = message
    evidence['sdk']['exception_traceback'] = message
    evidence['sdk']['errors'] = {'message': message}
    evidence['failure_observations'] = [{
        'error_code': code, 'failure_source': 'worker_executor', 'failure_stage': 'executor',
        'exception': {'type': 'RuntimeError', 'message': message, 'traceback': message},
    }]
    snapshot_before = deepcopy(persistence.snapshot)
    response = await service.read_admin(object(), tenant_id='tenant-a', run_id='run-a')
    assert persistence.snapshot == snapshot_before
    assert '[redacted-secret]' in response['root']['message']
    assert '[redacted-secret]' in response['root']['stack']
    assert 'syntheticsecret' not in str(response)
    assert 'syntheticsecond' not in str(response)


def test_diagnostic_fields_mask_only_credential_values():
    projected = runs_diagnostics_contract._sanitize_diagnostic_value({
        "api_key": "syntheticsecret", "Authorization": "Bearer syntheticsecret",
        "client_secret": "syntheticsecret", "x-amz-security-token": "syntheticsecret",
        "refresh_token": "syntheticsecret", "cookie": "syntheticsecret", "password": "",
        "input_tokens": 1200, "output_tokens": 300, "max_tokens": 4096, "token_count": 3,
        "request_id": "req-1", "response_status": 502, "content_type": "application/json",
        "workspace_path": "/srv/workspaces/run-a/inputs/a.xlsx", "command": "python run.py",
        "prompt": "summarize the sheet", "tool_input": {"file": "a.xlsx", "token": "syntheticsecret"},
    })
    assert "syntheticsecret" not in str(projected)
    for key in ("api_key", "Authorization", "client_secret", "x-amz-security-token",
                "refresh_token", "cookie"):
        assert projected[key] == "[redacted-secret]"
    assert projected["password"] == ""
    assert projected["tool_input"] == {"file": "a.xlsx", "token": "[redacted-secret]"}
    for key in ("input_tokens", "output_tokens", "max_tokens", "token_count", "request_id",
                "response_status", "content_type", "workspace_path", "command", "prompt"):
        assert key in projected and projected[key] != "[redacted-secret]"
