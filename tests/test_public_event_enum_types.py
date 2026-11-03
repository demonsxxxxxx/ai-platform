"""JSON enum identity at independently validated public-event boundaries."""

import pytest
from types import MappingProxyType

from app.execution.application.claude_agent_events import (
    ClaudeAgentEventCandidate,
    _without_none_public_values,
)
from app.streaming.domain.public_events_v4 import (
    V4ProjectionError,
    _validate_control_payload,
    validate_public_application_payload_v4,
)


def _candidate(event_type, payload):
    return ClaudeAgentEventCandidate(
        run_id="run1", event_id="event1", event_type=event_type,
        message_id="message1", causation_event_id=None, payload=payload,
        payload_sanitizer=_without_none_public_values,
    )


def _terminal_payload(event_type):
    payload = {"terminal_event_id": "terminal1", "hydrate_required": True}
    if event_type == "run.cancelled":
        payload["reason_code"] = "user_cancelled"
    if event_type == "run.failed":
        payload.update({"projection_version": "ai-platform.chat-public-projection.v1", "code": "run_failed", "default_message": "Run failed", "detail": None})
    return payload


@pytest.mark.parametrize("event_type", ["run.succeeded", "run.cancelled", "run.failed"])
@pytest.mark.parametrize("value", [1, 0, False, 1.0, None, "true", [], {}])
def test_boolean_const_rejects_numeric_equality_at_both_boundaries(event_type, value):
    payload = {**_terminal_payload(event_type), "hydrate_required": value}
    with pytest.raises(ValueError, match="^invalid hydrate_required$"):
        _candidate(event_type, payload)
    with pytest.raises(V4ProjectionError, match="^v4_hydrate_required_invalid$"):
        validate_public_application_payload_v4(event_type, payload)


@pytest.mark.parametrize("event_type", ["run.succeeded", "run.cancelled", "run.failed"])
def test_true_is_accepted_but_mutation_is_revalidated(event_type):
    payload = _terminal_payload(event_type)
    candidate = _candidate(event_type, payload)
    assert validate_public_application_payload_v4(candidate.event_type, payload) == payload
    payload["hydrate_required"] = 1
    with pytest.raises(V4ProjectionError, match="v4_hydrate_required_invalid"):
        validate_public_application_payload_v4(candidate.event_type, candidate.payload)


@pytest.mark.parametrize("value", [True, False, 1, None, [], {"private": "PRIVATE_SENTINEL"}, ["read"], "unknown"])
def test_string_enum_types_and_nested_values_fail_without_input(value):
    payload = {"operation_id": "operation1", "category": value, "display_name": "Read"}
    for validate, expected in ((_candidate, "invalid category"), (validate_public_application_payload_v4, "v4_category_invalid")):
        with pytest.raises(ValueError) as failure:
            validate("tool.started", payload)
        assert str(failure.value) == expected
        assert failure.value.__cause__ is None


@pytest.mark.parametrize("value", [True, 1, None, [], {"private": "PRIVATE_SENTINEL"}, "unknown"])
def test_control_enum_types_preserve_fixed_error(value):
    with pytest.raises(V4ProjectionError, match="^v4_status_invalid$"):
        _validate_control_payload("stream.heartbeat", {"status": value})
    assert _validate_control_payload("stream.heartbeat", {"status": "queued"}) == {"status": "queued"}


def test_shape_correction_preserves_streaming_specific_disclosure_policy():
    payload = {"operation_id": "operation1", "category": "read", "display_name": "Read", "input_summary": "arbitrary summary"}
    _candidate("tool.started", payload)
    with pytest.raises(V4ProjectionError, match="v4_input_summary_invalid"):
        validate_public_application_payload_v4("tool.started", payload)
    payload["input_summary"] = "Starting Read"
    assert validate_public_application_payload_v4("tool.started", payload) == payload
    with pytest.raises(ValueError, match="private text"):
        ClaudeAgentEventCandidate(
            run_id="run1", event_id="event1", event_type="tool.started",
            message_id="message1", causation_event_id=None, payload=payload,
            payload_sanitizer=lambda _value: {},
        )


def test_producer_and_projection_keep_their_closed_context_shape_contracts():
    payload = {"delta": "safe"}
    candidate = _candidate("message.delta", payload)
    assert candidate.payload is payload
    projected = validate_public_application_payload_v4(
        "message.delta", MappingProxyType(payload)
    )
    assert projected == payload
    assert projected is not payload
    with pytest.raises(ValueError, match="event payload must be an object"):
        ClaudeAgentEventCandidate(
            run_id="run1", event_id="event1", event_type="message.delta",
            message_id="message1", causation_event_id=None,
            payload=MappingProxyType(payload),
            payload_sanitizer=_without_none_public_values,
        )


def test_payload_context_differences_remain_explicit_at_semantic_edges():
    progress = {
        "schema_version": "ai-platform.public-agent-progress.v1",
        "step_id": "phase_model_wait",
        "phase": "model_wait",
        "lifecycle": "started",
        "message": "Loading authorized Skills",
    }
    _candidate("agent.progress", progress)
    with pytest.raises(V4ProjectionError, match="v4_agent_progress_payload_invalid"):
        validate_public_application_payload_v4("agent.progress", progress)

    malformed_refs = {
        "operation_id": "operation1",
        "category": "read",
        "display_name": "Read",
        "evidence_refs": [{}],
    }
    with pytest.raises(TypeError):
        _candidate("tool.started", malformed_refs)
    with pytest.raises(V4ProjectionError, match="v4_evidence_refs_invalid"):
        validate_public_application_payload_v4("tool.started", malformed_refs)

    duplicate_refs = {
        "operation_id": "operation1",
        "category": "read",
        "display_name": "Read",
        "evidence_refs": ["ref1", "ref1"],
    }
    with pytest.raises(ValueError, match="invalid evidence_refs"):
        _candidate("tool.started", duplicate_refs)
    with pytest.raises(V4ProjectionError, match="v4_evidence_refs_invalid"):
        validate_public_application_payload_v4("tool.started", duplicate_refs)

    private_extra = {"delta": "safe", "__private": "SENTINEL"}
    with pytest.raises(ValueError, match="event payload fields do not match schema"):
        _candidate("message.delta", private_extra)
    with pytest.raises(V4ProjectionError, match="v4_payload_keys_invalid"):
        validate_public_application_payload_v4("message.delta", private_extra)


def test_shared_field_predicates_preserve_delta_and_filename_bounds():
    maximum_delta = {"delta": "x" * 8_192}
    assert _candidate("message.delta", maximum_delta).payload is maximum_delta
    assert validate_public_application_payload_v4("message.delta", maximum_delta) == maximum_delta
    too_long = {"delta": "x" * 8_193}
    with pytest.raises(ValueError, match="invalid delta bound"):
        _candidate("message.delta", too_long)
    with pytest.raises(V4ProjectionError, match="v4_delta_invalid"):
        validate_public_application_payload_v4("message.delta", too_long)

    filename_payload = {
        "artifact_id": "artifact1", "filename": "bad\x00name",
        "media_type": "text/plain", "size_bytes": 1, "status": "ready",
    }
    with pytest.raises(ValueError, match="invalid filename"):
        _candidate("artifact.ready", filename_payload)
    with pytest.raises(V4ProjectionError, match="v4_filename_invalid"):
        validate_public_application_payload_v4("artifact.ready", filename_payload)


def test_candidate_completion_counter_resource_limit_is_unchanged():
    payload = {"delta_count": 2**63 - 1, "text_length": 1}
    _candidate("message.completed", payload)
    payload["delta_count"] += 1
    with pytest.raises(ValueError, match="invalid delta_count bound"):
        _candidate("message.completed", payload)
    assert validate_public_application_payload_v4("message.completed", payload) == payload


@pytest.mark.parametrize("field", ["requested_stream_incarnation", "current_stream_incarnation"])
def test_control_incarnation_resource_limit_is_unchanged(field):
    payload = {"reason": "stream_missing", "recovery": "reload_durable_state", "requested_event_id": None, "requested_stream_incarnation": None, "current_stream_incarnation": 1, "earliest_available_event_id": None, "latest_available_event_id": None}
    payload[field] = 2**63 - 1
    assert _validate_control_payload("stream.gap", payload) == payload
    payload[field] += 1
    with pytest.raises(V4ProjectionError):
        _validate_control_payload("stream.gap", payload)
