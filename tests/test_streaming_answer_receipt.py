from __future__ import annotations

import pytest

from app.persistence_limits import MESSAGE_CONTENT_MAX_BYTES, RUN_RESULT_MAX_BYTES, json_size_bytes
from app.streaming.api import opaque_message_id
from app.streaming.application.worker_publication_v4 import AssistantAnswerReceiptError
from app.streaming.infrastructure.v4 import load_answer_by_receipt
from app.worker import _ANSWER_BODY_REFERENCE, _bounded_answer_persistence
from tests.test_streaming_v4_durable import _authority, _row


class _Cursor:
    def __init__(self, *, row: object = None, rows: list[object] | None = None) -> None:
        self._row = row
        self._rows = list(rows or [])

    async def fetchone(self) -> object:
        return self._row

    async def fetchall(self) -> list[object]:
        return self._rows


class _Connection:
    def __init__(self, *, authority: dict[str, object] | None, rows: list[object]) -> None:
        self.authority = authority
        self.rows = rows
        self.statements: list[tuple[str, object]] = []

    async def execute(self, statement: str, params: object = ()) -> _Cursor:
        normalized = " ".join(statement.split()).lower()
        self.statements.append((normalized, params))
        if "from sse_stream_authorities" in normalized:
            return _Cursor(row=self.authority)
        if "from run_events" in normalized:
            return _Cursor(rows=self.rows)
        raise AssertionError(f"unexpected SQL: {statement}")


def _authority_row(*, attempt_id: str = "attempt-a") -> dict[str, object]:
    authority = _authority()
    return {
        "tenant_id": authority.tenant_id,
        "run_id": authority.run_id,
        "attempt_id": attempt_id,
        "tenant_scope": authority.tenant_scope,
        "stream_incarnation": authority.stream_incarnation,
        "state": authority.state,
        "open_event_id": authority.open_event_id,
        "open_payload_bytes": authority.open_payload_bytes,
        "open_payload_digest": authority.open_payload_digest,
        "authorization_epoch": authority.authorization_epoch,
        "revocation_state": authority.revocation_state,
    }


def _answer_fixture() -> tuple[list[dict[str, object]], dict[str, object]]:
    message_id = opaque_message_id("tenant-a", "run-a")
    deltas = ("hello ", "world")
    source_ids = ("source-delta-a", "source-delta-b")
    rows: list[dict[str, object]] = []
    event_specs = (
        ("evt4_answer_started", "message.started", {}),
        ("evt4_answer_delta_a", "message.delta", {"delta": deltas[0]}),
        ("evt4_answer_delta_b", "message.delta", {"delta": deltas[1]}),
        (
            "evt4_answer_completed",
            "message.completed",
            {"delta_count": len(deltas), "text_length": len("".join(deltas))},
        ),
    )
    for sequence, (event_id, event_type, payload) in enumerate(event_specs, start=1):
        row = _row(
            payload,
            id=event_id,
            sequence=sequence,
            event_type=event_type,
            stream_publication_state="published",
        )
        metadata = row["payload_json"]["__stream_v4"]
        assert isinstance(metadata, dict)
        metadata["publication_state"] = "published"
        metadata["message_id"] = message_id
        metadata["source_event_id"] = (
            source_ids[sequence - 2] if event_type == "message.delta" else event_id
        )
        if event_type == "message.completed":
            metadata["causation_event_id"] = source_ids[-1]
        rows.append(row)

    return rows, {
        "schema_version": "ai-platform.assistant-answer-receipt.v1",
        "message_id": message_id,
        "delta_count": len(deltas),
        "text_length": len("".join(deltas)),
        "last_delta_event_id": source_ids[-1],
    }


def _connection(rows: list[dict[str, object]], *, attempt_id: str = "attempt-a") -> _Connection:
    return _Connection(authority=_authority_row(attempt_id=attempt_id), rows=rows)


@pytest.mark.asyncio
async def test_load_answer_by_receipt_reconstructs_complete_current_attempt_answer():
    rows, receipt = _answer_fixture()
    connection = _connection(rows)

    reconstructed = await load_answer_by_receipt(
        connection,
        tenant_id="tenant-a",
        run_id="run-a",
        attempt_id="attempt-a",
        receipt=receipt,
    )

    assert reconstructed.text == "hello world"
    assert len(connection.statements) == 2
    assert connection.statements[1][1] == ("tenant-a", "run-a", "attempt-a")


@pytest.mark.asyncio
async def test_load_answer_by_receipt_requires_current_authority_attempt():
    rows, receipt = _answer_fixture()

    with pytest.raises(AssistantAnswerReceiptError):
        await load_answer_by_receipt(
            _connection(rows, attempt_id="attempt-other"),
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            receipt=receipt,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "missing_started",
        "missing_delta",
        "missing_completed",
        "malformed_row",
        "mixed_message_id",
        "duplicate_source_id",
        "duplicate_sequence",
        "out_of_order_sequence",
        "wrong_last_delta_source",
        "wrong_completion_causation",
        "wrong_receipt_count",
        "wrong_receipt_length",
        "wrong_completed_count",
        "wrong_completed_length",
        "publication_pending",
        "publication_state_mismatch",
        "publication_state_invalid",
    ],
)
async def test_load_answer_by_receipt_rejects_invalid_current_attempt_rows(mutation: str):
    rows, receipt = _answer_fixture()
    if mutation == "missing_started":
        rows.pop(0)
    elif mutation == "missing_delta":
        rows.pop(2)
    elif mutation == "missing_completed":
        rows.pop()
    elif mutation == "malformed_row":
        rows[1]["payload_json"] = None
    elif mutation == "mixed_message_id":
        rows[2]["payload_json"]["__stream_v4"]["message_id"] = "msg_other"
    elif mutation == "duplicate_source_id":
        rows[2]["payload_json"]["__stream_v4"]["source_event_id"] = "source-delta-a"
    elif mutation == "duplicate_sequence":
        rows[2]["sequence"] = rows[1]["sequence"]
    elif mutation == "out_of_order_sequence":
        rows[2]["sequence"] = 1
        rows[1]["sequence"] = 3
    elif mutation == "wrong_last_delta_source":
        receipt["last_delta_event_id"] = "source-delta-wrong"
    elif mutation == "wrong_completion_causation":
        rows[-1]["payload_json"]["__stream_v4"]["causation_event_id"] = "source-delta-wrong"
    elif mutation == "wrong_receipt_count":
        receipt["delta_count"] = 3
    elif mutation == "wrong_receipt_length":
        receipt["text_length"] = 99
    elif mutation == "wrong_completed_count":
        rows[-1]["payload_json"]["delta_count"] = 3
    elif mutation == "wrong_completed_length":
        rows[-1]["payload_json"]["text_length"] = 99
    elif mutation == "publication_pending":
        rows[1]["stream_publication_state"] = "pending"
        rows[1]["payload_json"]["__stream_v4"]["publication_state"] = "pending"
    elif mutation == "publication_state_mismatch":
        rows[1]["stream_publication_state"] = "pending"
    elif mutation == "publication_state_invalid":
        rows[1]["stream_publication_state"] = "failed"
        rows[1]["payload_json"]["__stream_v4"]["publication_state"] = "failed"

    with pytest.raises(AssistantAnswerReceiptError):
        await load_answer_by_receipt(
            _connection(rows),
            tenant_id="tenant-a",
            run_id="run-a",
            attempt_id="attempt-a",
            receipt=receipt,
        )


def _receipt() -> dict[str, object]:
    return {
        "schema_version": "ai-platform.assistant-answer-receipt.v1",
        "message_id": "msg_answer_a",
        "delta_count": 1,
        "text_length": 5,
        "last_delta_event_id": "evt4_delta_a",
    }


def test_bounded_answer_persistence_keeps_short_compatibility_message():
    payload, persisted_message, metadata = _bounded_answer_persistence(
        {"status": "completed"},
        message="short answer",
        answer_receipt=_receipt(),
    )

    assert payload["message"] == "short answer"
    assert persisted_message == "short answer"
    assert "answer_body_source" not in payload
    assert metadata["answer_body_source"] == "run_events_v4"
    assert json_size_bytes(payload) <= RUN_RESULT_MAX_BYTES


def test_bounded_answer_persistence_references_body_when_message_is_over_limit():
    answer = "x" * (MESSAGE_CONTENT_MAX_BYTES + 1)

    payload, persisted_message, metadata = _bounded_answer_persistence(
        {"status": "completed"},
        message=answer,
        answer_receipt=_receipt(),
    )

    assert payload["message"] == _ANSWER_BODY_REFERENCE
    assert persisted_message == _ANSWER_BODY_REFERENCE
    assert payload["answer_body_source"] == "run_events_v4"
    assert metadata["answer_receipt"] == _receipt()
    assert answer not in repr(payload)


def test_bounded_answer_persistence_references_body_when_result_json_overflows():
    answer = "y" * 256
    receipt = _receipt()
    base_result = {
        "status": "completed",
        "answer_receipt": receipt,
        "diagnostics": "",
    }
    reference_payload = {
        **base_result,
        "message": _ANSWER_BODY_REFERENCE,
        "answer_body_source": "run_events_v4",
    }
    base_result["diagnostics"] = "x" * (
        RUN_RESULT_MAX_BYTES - json_size_bytes(reference_payload)
    )
    full_inline_payload = {**base_result, "message": answer}

    assert json_size_bytes(base_result) <= RUN_RESULT_MAX_BYTES
    assert len(answer.encode("utf-8")) <= MESSAGE_CONTENT_MAX_BYTES
    assert json_size_bytes(full_inline_payload) > RUN_RESULT_MAX_BYTES

    payload, persisted_message, metadata = _bounded_answer_persistence(
        base_result,
        message=answer,
        answer_receipt=receipt,
    )

    assert json_size_bytes(payload) <= RUN_RESULT_MAX_BYTES
    assert payload["message"] == _ANSWER_BODY_REFERENCE
    assert persisted_message == answer
    assert payload["answer_body_source"] == "run_events_v4"
    assert metadata["answer_receipt"] == receipt
    assert answer not in repr(payload)
