import json
from types import SimpleNamespace

import pytest

from app.execution.domain.public_projection import (
    claude_sdk_failure_code,
    claude_sdk_failure_message,
)
from app.runs.domain.public_terminal import public_terminal_projection
from app.sandbox import api as sandbox_api
from app.sandbox.domain import runtime_diagnostics as runtime_diagnostics_contract


_EXPECTED = {
    "reason": "raw_delta_conflict",
    "stage": "message",
    "location": "answer_delta",
}


_FRAME_SHAPE = {
    "event_type": "content_block_delta",
    "block_type": "other",
    "delta_type": "text_delta",
    "message_state": "open",
    "open_block_type": "tool_use",
    "index_state": "ignored",
    "guard": "block_delta_type",
}


def _runtime_diagnostics(**overrides):
    value = {
        "schema_version": sandbox_api.SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
        "error_code": "claude_agent_sdk_output_validation_failed",
        "failure_source": "sdk_result_error",
        "failure_stage": "message",
        "sdk": {},
    }
    value.update(overrides)
    return value


@pytest.mark.parametrize(
    "projection_failure",
    [
        {
            **_EXPECTED,
            "reason": "raw_delta_conflict run_id=private-run-id",
        },
        {**_EXPECTED, "stage": "provider_callback"},
        {**_EXPECTED, "location": "answer_delta private-run-id"},
        {**_EXPECTED, "location": "private-run-id"},
        {"reason": "raw_delta_conflict", "stage": "message"},
        "raw_delta_conflict",
    ],
)
def test_projection_failure_rejects_unrecognized_taxonomy_values(projection_failure):
    normalized = sandbox_api.normalize_sdk_runtime_diagnostics(
        _runtime_diagnostics(projection_failure=projection_failure)
    )

    assert "projection_failure" not in normalized
    assert "private-run-id" not in str(normalized)


def test_projection_failure_drops_payload_and_identifier_extras():
    private_payload = "assistant body with a private marker"
    raw = {
        **_EXPECTED,
        "body": private_payload,
        "run_id": "run-private-42",
        "assistant_message_id": "message-private-42",
        "event_id": "event-private-42",
    }

    normalized = sandbox_api.normalize_sdk_runtime_diagnostics(
        _runtime_diagnostics(projection_failure=raw)
    )

    assert normalized["projection_failure"] == _EXPECTED
    assert private_payload not in str(normalized)
    assert "run-private-42" not in str(normalized)
    assert "message-private-42" not in str(normalized)
    assert "event-private-42" not in str(normalized)
    assert {
        "field": "projection_failure",
        "reason": "unknown_fields_dropped",
        "count": 4,
    } in normalized["normalization_losses"]


def test_raw_frame_shape_rejects_private_values_at_sandbox_boundary():
    expected = {
        "reason": "raw_frame_invalid", "stage": "message", "location": "raw_stream_frame",
    }
    for shape in (
        {**_FRAME_SHAPE, "event_type": "private-token"},
        {**_FRAME_SHAPE, "payload": "private-token"},
        {**_FRAME_SHAPE, "guard": ["private-token"]},
    ):
        normalized = sandbox_api.normalize_sdk_runtime_diagnostics(
            _runtime_diagnostics(projection_failure={**expected, "frame_shape": shape})
        )
        assert normalized["projection_failure"] == expected
        assert "private-token" not in str(normalized)
    normalized = sandbox_api.normalize_sdk_runtime_diagnostics(
        _runtime_diagnostics(projection_failure={**expected, "frame_shape": _FRAME_SHAPE})
    )
    assert normalized["projection_failure"] == {**expected, "frame_shape": _FRAME_SHAPE}


def test_projection_failure_survives_diagnostic_byte_budget(monkeypatch):
    max_bytes = 4_096
    monkeypatch.setattr(
        runtime_diagnostics_contract,
        "SDK_RUNTIME_DIAGNOSTICS_MAX_BYTES",
        max_bytes,
    )

    normalized = sandbox_api.normalize_sdk_runtime_diagnostics(
        _runtime_diagnostics(
            projection_failure={
                "reason": "raw_frame_invalid", "stage": "message",
                "location": "raw_stream_frame", "frame_shape": _FRAME_SHAPE,
            },
            sdk={
                "exception_message": "x" * 8_192,
                "exception_traceback": "y" * 8_192,
            },
        )
    )
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    assert normalized["projection_failure"]["frame_shape"] == _FRAME_SHAPE
    assert len(encoded) <= max_bytes


def test_sdk_output_validation_failure_keeps_fixed_code_and_public_taxonomy():
    upstream = SimpleNamespace(
        error="claude_agent_sdk_upstream_error",
        used_sdk=True,
    )
    output_validation = SimpleNamespace(
        error="claude_agent_sdk_output_validation_failed",
        used_sdk=True,
    )

    assert claude_sdk_failure_code(upstream) == "claude_agent_sdk_upstream_error"
    assert public_terminal_projection(
        "failed", "claude_agent_sdk_upstream_error"
    )["detail_code"] == "model_service_unavailable"
    assert claude_sdk_failure_code(output_validation) == (
        "claude_agent_sdk_output_validation_failed"
    )
    assert claude_sdk_failure_message(output_validation) == (
        "This run's output could not be validated. "
        "Please contact an administrator and provide the run ID."
    )
    assert public_terminal_projection(
        "failed", "claude_agent_sdk_output_validation_failed"
    )["detail_code"] == "run_failed"
    assert public_terminal_projection(
        "failed", "terminal_reconciliation_failed"
    )["detail_code"] == "terminal_reconciliation_failed"
