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


def test_v4_and_v3_public_contracts_reject_each_other():
    v3_schema = {
        "type": "object",
        "properties": {
            "schema": {"const": "ai-platform.public-run-stream-event.v3"},
            "event_id": {"type": "string", "minLength": 1, "maxLength": 256},
            "run_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"},
            "stream_incarnation": {"type": "integer", "minimum": 1},
            "emitted_at": {"type": "string", "format": "date-time", "maxLength": 64},
            "event_type": {"const": "assistant_text_delta"},
            "payload": {
                "type": "object",
                "properties": {"delta": {"type": "string", "minLength": 1, "maxLength": 8192}},
                "required": ["delta"],
                "additionalProperties": False,
            },
        },
        "required": ["schema", "event_id", "run_id", "stream_incarnation", "emitted_at", "event_type", "payload"],
        "additionalProperties": False,
    }
    v3_validator = Draft202012Validator(v3_schema)
    v4_validator = _validator("PublicRunStreamEventV4")
    v3_event = {
        "schema": "ai-platform.public-run-stream-event.v3",
        "event_id": "event-1",
        "run_id": "run-1",
        "stream_incarnation": 1,
        "emitted_at": "2026-08-17T00:00:00Z",
        "event_type": "assistant_text_delta",
        "payload": {"delta": "legacy"},
    }
    v4_event = _v4_event("message.delta", {"delta": "current"})

    assert list(v3_validator.iter_errors(v3_event)) == []
    assert list(v4_validator.iter_errors(v3_event))
    assert list(v4_validator.iter_errors(v4_event)) == []
    assert list(v3_validator.iter_errors(v4_event))
