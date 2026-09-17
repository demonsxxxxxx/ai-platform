import json
from pathlib import Path

from jsonschema import Draft202012Validator

from tools import generate_sse_v4_contracts


ROOT = Path(__file__).resolve().parents[1]
V4_SCHEMA_PATH = ROOT / "schemas/public_run_stream.v4.schema.json"


def _schema(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(definition: str) -> Draft202012Validator:
    schema = _schema(V4_SCHEMA_PATH)
    schema["$ref"] = f"#/$defs/{definition}"
    return Draft202012Validator(schema)


def _v4_event(
    event_type: str,
    payload: dict[str, object],
    *,
    message_id: str | None = "msg-1",
) -> dict[str, object]:
    return {
        "schema": "ai-platform.public-run-stream-event.v4",
        "event_id": "event-1",
        "run_id": "run-1",
        "message_id": message_id,
        "seq": 1,
        "event_type": event_type,
        "stream_incarnation": 1,
        "replayable": True,
        "trace_ref": None,
        "causation_event_id": None,
        "emitted_at": "2026-08-17T00:00:00Z",
        "payload": payload,
    }


def _v4_internal_event(
    event_type: str,
    payload: dict[str, object],
    *,
    message_id: str | None = "msg-1",
    seq: int | None = 1,
    replayable: bool = True,
) -> dict[str, object]:
    return {
        "schema": "ai-platform.stream-event.v4",
        "event_id": "event-1",
        "tenant_scope": "tenant-1",
        "run_id": "run-1",
        "attempt_id": "attempt-1",
        "message_id": message_id,
        "seq": seq,
        "event_type": event_type,
        "stream_incarnation": 1,
        "replayable": replayable,
        "trace_ref": None,
        "causation_event_id": None,
        "emitted_at": "2026-08-17T00:00:00Z",
        "projection_version": "public-stream-v4",
        "payload": payload,
        "source": {
            "kind": "run_event",
            "run_event_id": "event-1",
            "sequence": 1,
        },
    }


def test_generated_sse_v4_contracts_are_current():
    assert generate_sse_v4_contracts.generate(check=True) == []


def test_generated_frontend_contract_exposes_only_public_v4_fields():
    source = (ROOT / "frontend/web/src/generated/publicRunStreamV4.ts").read_text(
        encoding="utf-8"
    )

    assert "PublicApplicationEventV4" in source
    assert "PublicTransportControlEventV4" in source
    assert '"tenant_scope"' not in source
    assert '"attempt_id"' not in source
    assert '"projection_version": "public-stream-v4"' not in source
    assert '"kind"' not in source
    assert "reasoning.delta" not in source
    assert "approval.required" not in source


def test_v4_schema_keeps_internal_and_public_contracts_separate():
    source = V4_SCHEMA_PATH.read_text(encoding="utf-8")

    assert '"InternalStreamEnvelopeV4"' in source
    assert '"PublicApplicationEventV4"' in source
    assert '"PublicTransportControlEventV4"' in source
    assert '"projection_version": { "const": "public-stream-v4" }' in source
    assert "semantic_stage" not in source
    assert "semantic_progress" not in source
    assert "assistant_text_delta" not in source
    assert '"owner_epoch"' not in source
    assert '"generation_id"' not in source


def test_v4_rejects_unknown_events_and_payload_fields():
    validator = _validator("PublicRunStreamEventV4")
    valid = _v4_event("message.delta", {"delta": "hello"})
    assert list(validator.iter_errors(valid)) == []

    unknown_field = {**valid, "unexpected": True}
    assert list(validator.iter_errors(unknown_field))
    unknown_payload = {**valid, "payload": {"delta": "hello", "raw_sdk": "nope"}}
    assert list(validator.iter_errors(unknown_payload))
    unknown_event = {**valid, "event_type": "assistant_text_delta"}
    assert list(validator.iter_errors(unknown_event))


def test_v4_enforces_message_identity_and_transport_controls():
    validator = _validator("PublicRunStreamEventV4")
    missing_message = _v4_event("message.started", {}, message_id=None)
    assert list(validator.iter_errors(missing_message))

    control = {
        "schema": "ai-platform.public-run-stream-control.v4",
        "event_id": "control-1",
        "run_id": "run-1",
        "message_id": None,
        "seq": None,
        "event_type": "stream.gap",
        "stream_incarnation": 1,
        "replayable": False,
        "trace_ref": None,
        "causation_event_id": None,
        "emitted_at": "2026-08-17T00:00:00Z",
        "payload": {
            "reason": "stream_missing",
            "recovery": "reload_durable_state",
            "requested_event_id": None,
            "requested_stream_incarnation": None,
            "current_stream_incarnation": 1,
            "earliest_available_event_id": None,
            "latest_available_event_id": None,
        },
    }
    assert list(validator.iter_errors(control)) == []


def test_v4_internal_envelope_matches_event_variants_strictly():
    validator = _validator("InternalStreamEnvelopeV4")
    valid = _v4_internal_event("message.delta", {"delta": "hello"})
    assert list(validator.iter_errors(valid)) == []

    for invalid in (
        {**valid, "message_id": None},
        {**valid, "seq": None},
        {**valid, "replayable": False},
        {**valid, "payload": {"delta": "hello", "raw_sdk": "nope"}},
    ):
        assert list(validator.iter_errors(invalid))

    control = _v4_internal_event(
        "stream.heartbeat",
        {"status": "running"},
        message_id=None,
        seq=None,
        replayable=False,
    )
    control["trace_ref"] = None
    assert list(validator.iter_errors(control)) == []
    assert list(validator.iter_errors({**control, "seq": 1}))
    assert list(validator.iter_errors({**control, "replayable": True}))


def test_schema_backend_and_sdk_share_valid_invalid_payload_matrix():
    import pytest
    from app.execution.application.claude_agent_events import ClaudeAgentEventCandidate
    from app.platform.public_payload import sanitize_public_payload
    from app.streaming.domain.public_events_v4 import (
        V4ProjectionError,
        validate_public_application_payload_v4,
    )

    valid_payloads = {
        "message.delta": {"delta": "hello"},
        "model.completed": {
            "duration_ms": 0,
            "turn_count": 0,
            "stop_category": "completed",
        },
        "tool.started": {
            "operation_id": "operation-1",
            "category": "read",
            "display_name": "Read file",
        },
        "artifact.created": {
            "artifact_id": "artifact-1",
            "filename": "report.txt",
            "media_type": "text/plain",
            "size_bytes": 1,
            "status": "created",
        },
        "run.failed": {
            "terminal_event_id": "terminal-1",
            "hydrate_required": True,
            "projection_version": "ai-platform.chat-public-projection.v1",
            "code": "run_failed",
            "default_message": "Run failed",
            "detail": None,
        },
    }
    invalid_payloads = {
        "message.delta": {"delta": "", "unexpected": True},
        "model.completed": {
            "duration_ms": 86400001,
            "turn_count": 0,
            "stop_category": "completed",
        },
        "tool.started": {
            "operation_id": "private/path",
            "category": "read",
            "display_name": "Read file",
        },
        "artifact.created": {
            "artifact_id": "artifact-1",
            "filename": "report.txt",
            "media_type": "text/plain",
            "size_bytes": -1,
            "status": "created",
        },
        "run.failed": {
            "terminal_event_id": "terminal-1",
            "hydrate_required": True,
            "projection_version": "ai-platform.chat-public-projection.v1",
            "code": "run_failed",
            "default_message": "Run failed",
            "detail": None,
            "raw_sdk": "forbidden",
        },
    }

    for index, (event_type, payload) in enumerate(valid_payloads.items()):
        document = _v4_event(event_type, payload)
        assert list(_validator("PublicRunStreamEventV4").iter_errors(document)) == []
        assert validate_public_application_payload_v4(event_type, payload) == payload
        candidate = ClaudeAgentEventCandidate(
            run_id="run-1",
            event_id=f"event-valid-{index}",
            event_type=event_type,
            message_id="msg-1",
            causation_event_id=None,
            payload=payload,
            payload_sanitizer=sanitize_public_payload,
        )
        assert candidate.payload == payload

    for index, (event_type, payload) in enumerate(invalid_payloads.items()):
        document = _v4_event(event_type, payload)
        assert list(_validator("PublicRunStreamEventV4").iter_errors(document))
        with pytest.raises(V4ProjectionError):
            validate_public_application_payload_v4(event_type, payload)
        with pytest.raises(ValueError):
            ClaudeAgentEventCandidate(
                run_id="run-1",
                event_id=f"event-invalid-{index}",
                event_type=event_type,
                message_id="msg-1",
                causation_event_id=None,
                payload=payload,
                payload_sanitizer=sanitize_public_payload,
            )


def test_v4_rejects_retired_v3_frames():
    validator = _validator("PublicRunStreamEventV4")
    legacy = {
        "schema": "ai-platform.public-run-stream-event.v3",
        "event_id": "event-1", "run_id": "run-1", "stream_incarnation": 1,
        "emitted_at": "2026-08-17T00:00:00Z", "event_type": "assistant_text_delta",
        "payload": {"delta": "legacy"},
    }
    assert list(validator.iter_errors(legacy))
    assert list(validator.iter_errors(_v4_event("message.delta", {"delta": "current"}))) == []


def test_generated_public_boundary_exposes_v4_without_legacy_types():
    from app.streaming.domain import protocol_v4

    assert not hasattr(protocol_v4, "PublicRunStreamEventV3")
    assert protocol_v4.PUBLIC_RUN_STREAM_SCHEMA == "ai-platform.public-run-stream-event.v4"
    assert protocol_v4.INTERNAL_STREAM_EVENT_SCHEMA == "ai-platform.stream-event.v4"
    assert protocol_v4.STREAM_PROJECTION_VERSION == "public-stream-v4"
    assert protocol_v4.STREAM_DESIGN_ID == "ai-platform.redis-streams-sse-event-channel.v4"
    assert protocol_v4.PUBLIC_APPLICATION_EVENT_TYPES == frozenset(
        value
        for value in protocol_v4.PUBLIC_STREAM_EVENT_TYPES
        if not value.startswith("stream.")
    )


def test_stream_end_uses_the_run_fact_source_and_rejects_terminal_intents():
    import pytest
    from app.streaming.domain.public_events_v4 import V4ProjectionError, validate_internal_envelope_v4

    end = _v4_internal_event("stream.end", {"terminal_event_id": "terminal-1"}, message_id=None, seq=None)
    validator = _validator("InternalStreamEnvelopeV4")
    assert list(validator.iter_errors(end)) == []
    assert validate_internal_envelope_v4(end) == end
    end["source"]["callback_sequence"] = 0
    assert list(validator.iter_errors(end)) == []
    assert validate_internal_envelope_v4(end) == end
    for invalid in (True, -1, "1", 2**63):
        end["source"]["callback_sequence"] = invalid
        assert list(validator.iter_errors(end))
        with pytest.raises(V4ProjectionError):
            validate_internal_envelope_v4(end)
    end["source"] = {"kind": "terminal_intent", "terminal_event_id": "terminal-1"}
    assert list(validator.iter_errors(end))
    with pytest.raises(V4ProjectionError, match="v4_source_invalid"):
        validate_internal_envelope_v4(end)
