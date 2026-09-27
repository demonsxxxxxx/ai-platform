"""Execution-owned terminal answer materialization."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Callable

from app.streaming.api import (
    AssistantAnswerReceiptError,
    WorkerV4Capabilities,
    canonical_answer_body_digest,
)

_ANSWER_BODY_REFERENCE = "The complete assistant response is available in the run history."
_ANSWER_MATERIALIZATION_SCHEMA = "ai-platform.answer-materialization-proof.v1"
_ANSWER_BODY_PROJECTION_VERSION = "ai-platform.assistant-message-body.v1"
_ANSWER_BODY_PROJECTION_POLICY = "sanitize-assistant-message.v1"
_STREAM_PROJECTION_VERSION = "public-stream-v4"
_ARTIFACT_PROJECTION = "assistant_message_parts_v1"
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,255}$")


@dataclass(frozen=True, slots=True)
class VerifiedAnswerMaterialization:
    content: str
    event_id: str
    sequence: int
    created_at: str | None


_ANSWER_MATERIALIZATION_FIELDS = frozenset(
    {
        "schema_version",
        "proof_version",
        "stream_projection_version",
        "body_projection_version",
        "body_projection_policy",
        "tenant_id",
        "run_id",
        "attempt_id",
        "stream_incarnation",
        "authorization_epoch",
        "message_id",
        "last_delta_event_id",
        "delta_count",
        "text_length",
        "last_delta_sequence",
        "last_delta_created_at",
        "last_delta_row_id",
        "stream_answer_digest",
        "persisted_body_digest",
        "persisted_body_codepoints",
        "body_complete",
        "body_projection_changed",
        "answer_source_count",
        "answer_interleaved",
        "artifact_projection",
        "artifact_count",
    }
)


def build_answer_materialization_proof(
    reconstructed: Any,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    persisted_body: str,
    answer_source_count: object,
    artifact_count: int,
) -> dict[str, Any]:
    source_count = (
        answer_source_count
        if isinstance(answer_source_count, int)
        and not isinstance(answer_source_count, bool)
        and 1 <= answer_source_count <= 64
        else None
    )
    return {
        "schema_version": _ANSWER_MATERIALIZATION_SCHEMA,
        "proof_version": 1,
        "stream_projection_version": _STREAM_PROJECTION_VERSION,
        "body_projection_version": _ANSWER_BODY_PROJECTION_VERSION,
        "body_projection_policy": _ANSWER_BODY_PROJECTION_POLICY,
        "tenant_id": tenant_id,
        "run_id": run_id,
        "attempt_id": attempt_id,
        "stream_incarnation": reconstructed.stream_incarnation,
        "authorization_epoch": getattr(reconstructed, "authorization_epoch", 1),
        "message_id": reconstructed.message_id,
        "last_delta_event_id": reconstructed.last_delta_event_id,
        "delta_count": reconstructed.delta_count,
        "text_length": reconstructed.text_length,
        "last_delta_sequence": reconstructed.last_delta_sequence,
        "last_delta_created_at": reconstructed.last_delta_created_at,
        "last_delta_row_id": reconstructed.last_delta_row_id,
        "stream_answer_digest": reconstructed.stream_answer_digest,
        "persisted_body_digest": canonical_answer_body_digest((persisted_body,)),
        "persisted_body_codepoints": len(persisted_body),
        "body_complete": persisted_body != _ANSWER_BODY_REFERENCE,
        "body_projection_changed": persisted_body != reconstructed.text,
        "answer_source_count": source_count,
        "answer_interleaved": reconstructed.interleaved,
        "artifact_projection": _ARTIFACT_PROJECTION,
        "artifact_count": artifact_count,
    }


def verify_answer_materialization(
    metadata: object,
    *,
    content: object,
    tenant_id: str,
    run_id: str,
    status: str,
    authority: object,
    terminal_proof: object = None,
) -> VerifiedAnswerMaterialization | None:
    if not isinstance(metadata, Mapping) or not isinstance(content, str):
        return None
    proof = metadata.get("answer_materialization_proof")
    receipt = metadata.get("answer_receipt")
    if (
        status != "succeeded"
        or not isinstance(proof, Mapping)
        or set(proof) != _ANSWER_MATERIALIZATION_FIELDS
        or not isinstance(receipt, Mapping)
        or not isinstance(terminal_proof, Mapping)
        or dict(terminal_proof) != dict(proof)
        or proof.get("schema_version") != _ANSWER_MATERIALIZATION_SCHEMA
        or isinstance(proof.get("proof_version"), bool)
        or not isinstance(proof.get("proof_version"), int)
        or proof.get("proof_version") != 1
        or proof.get("stream_projection_version") != _STREAM_PROJECTION_VERSION
        or proof.get("body_projection_version") != _ANSWER_BODY_PROJECTION_VERSION
        or proof.get("body_projection_policy") != _ANSWER_BODY_PROJECTION_POLICY
        or proof.get("tenant_id") != tenant_id
        or proof.get("run_id") != run_id
        or getattr(authority, "tenant_id", None) != tenant_id
        or getattr(authority, "run_id", None) != run_id
        or getattr(authority, "attempt_id", None) != proof.get("attempt_id")
        or getattr(authority, "stream_incarnation", None)
        != proof.get("stream_incarnation")
        or getattr(authority, "authorization_epoch", None)
        != proof.get("authorization_epoch")
        or getattr(authority, "state", None) != "terminal"
        or proof.get("body_complete") is not True
        or proof.get("body_projection_changed") is not False
        or proof.get("answer_source_count") != 1
        or proof.get("answer_interleaved") is not False
        or proof.get("artifact_projection") != _ARTIFACT_PROJECTION
        or content == _ANSWER_BODY_REFERENCE
        or proof.get("persisted_body_digest")
        != canonical_answer_body_digest((content,))
        or proof.get("persisted_body_codepoints") != len(content)
    ):
        return None
    positive_integers = (
        proof.get("stream_incarnation"),
        proof.get("authorization_epoch"),
        proof.get("delta_count"),
        proof.get("text_length"),
        proof.get("last_delta_sequence"),
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 1
        for value in positive_integers
    ):
        return None
    nonnegative_integers = (
        proof.get("artifact_count"),
        proof.get("persisted_body_codepoints"),
    )
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in nonnegative_integers
    ):
        return None
    answer_source_count = proof.get("answer_source_count")
    if (
        isinstance(answer_source_count, bool)
        or not isinstance(answer_source_count, int)
        or answer_source_count != 1
    ):
        return None
    if (
        not isinstance(proof.get("message_id"), str)
        or not _SAFE_REF_RE.fullmatch(proof["message_id"])
        or not isinstance(proof.get("last_delta_event_id"), str)
        or not _SAFE_REF_RE.fullmatch(proof["last_delta_event_id"])
        or not isinstance(proof.get("last_delta_row_id"), str)
        or not proof["last_delta_row_id"].startswith("evt4_")
        or not _SAFE_REF_RE.fullmatch(proof["last_delta_row_id"])
    ):
        return None
    if any(
        not isinstance(proof.get(field), str)
        or len(str(proof[field])) != 64
        or any(character not in "0123456789abcdef" for character in str(proof[field]))
        for field in ("stream_answer_digest", "persisted_body_digest")
    ):
        return None
    receipt_fields = (
        "message_id",
        "last_delta_event_id",
        "delta_count",
        "text_length",
    )
    if receipt.get("schema_version") != "ai-platform.assistant-answer-receipt.v1" or any(
        receipt.get(field) != proof.get(field) for field in receipt_fields
    ):
        return None
    event_id = str(proof["last_delta_row_id"])
    created_at = proof.get("last_delta_created_at")
    if created_at is not None:
        if not isinstance(created_at, str) or len(created_at) > 64:
            return None
        try:
            parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed_created_at.tzinfo is None or parsed_created_at.utcoffset() is None:
            return None
    return VerifiedAnswerMaterialization(
        content=content,
        event_id=event_id,
        sequence=int(proof["last_delta_sequence"]),
        created_at=created_at,
    )


@dataclass(frozen=True, slots=True)
class AnswerPersistenceLimits:
    message_content_max_bytes: int
    run_result_max_bytes: int
    json_size_bytes: Callable[[Any], int]


@dataclass(frozen=True, slots=True)
class WorkerAnswerMaterialization:
    result: Any
    result_payload: dict[str, Any]
    artifact_records: list[dict[str, Any]]
    assistant_message_for_persistence: str | None
    assistant_message_metadata: dict[str, Any]


def assistant_artifact_metadata(
    artifact_records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Describe ordered message attachments without embedding links in text."""

    return {
        "artifact_count": len(artifact_records),
        "artifact_ids": [artifact["id"] for artifact in artifact_records],
        "artifact_delivery": "assistant_message_parts_v1",
    }


def sanitize_assistant_message(message: str) -> str:
    """Remove legacy local-path hints without mixing artifacts into answer text."""

    lines = []
    for line in message.splitlines():
        stripped = line.strip()
        if stripped.startswith(("详细报告:", "批注文档:")) and "/tmp/" in stripped:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _bounded_answer_persistence(
    result_payload: Mapping[str, Any],
    *,
    message: str,
    answer_receipt: Mapping[str, Any],
    limits: AnswerPersistenceLimits,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    receipt_json = dict(answer_receipt)
    metadata = {
        "answer_receipt": receipt_json,
        "answer_body_source": "run_events_v4",
    }
    full_result_payload = {
        **result_payload,
        "message": message,
        "answer_receipt": receipt_json,
    }
    if len(message.encode("utf-8")) <= limits.message_content_max_bytes and limits.json_size_bytes(
        full_result_payload
    ) <= limits.run_result_max_bytes:
        return full_result_payload, message, metadata
    return (
        {
            **result_payload,
            "message": _ANSWER_BODY_REFERENCE,
            "answer_receipt": receipt_json,
            "answer_body_source": "run_events_v4",
        },
        message if len(message.encode("utf-8")) <= limits.message_content_max_bytes else _ANSWER_BODY_REFERENCE,
        metadata,
    )


async def materialize_worker_answer(
    capabilities: WorkerV4Capabilities,
    conn: Any,
    *,
    result: Any,
    result_payload: dict[str, Any],
    artifact_records: list[dict[str, Any]],
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    answer_receipt: Mapping[str, Any],
    limits: AnswerPersistenceLimits,
    answer_source_count: object = None,
) -> WorkerAnswerMaterialization:
    try:
        reconstructed = await capabilities.event_persistence.load_answer_by_receipt(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            receipt=answer_receipt,
        )
    except AssistantAnswerReceiptError as exc:
        if exc.retryable:
            raise
        error_code = AssistantAnswerReceiptError.code
        error_message = "The assistant response could not be verified."
        return WorkerAnswerMaterialization(
            result=replace(
                result,
                status="failed",
                artifacts=[],
                result={
                    **result.result,
                    "message": error_message,
                    "error_code": error_code,
                },
            ),
            result_payload={
                **result_payload,
                "message": error_message,
                "error_code": error_code,
                "artifacts": [],
            },
            artifact_records=[],
            assistant_message_for_persistence=None,
            assistant_message_metadata={},
        )

    reconstructed_message = sanitize_assistant_message(reconstructed.text)
    (
        materialized_payload,
        assistant_message_for_persistence,
        assistant_message_metadata,
    ) = _bounded_answer_persistence(
        result_payload,
        message=reconstructed_message,
        answer_receipt=answer_receipt,
        limits=limits,
    )
    proof = build_answer_materialization_proof(
        reconstructed,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
        persisted_body=assistant_message_for_persistence,
        answer_source_count=answer_source_count,
        artifact_count=len(artifact_records),
    )
    assistant_message_metadata = {
        **assistant_message_metadata,
        "answer_materialization_proof": proof,
    }
    terminal_proof_payload = {
        **materialized_payload,
        "answer_materialization_proof": proof,
    }
    if limits.json_size_bytes(terminal_proof_payload) <= limits.run_result_max_bytes:
        materialized_payload = terminal_proof_payload
    return WorkerAnswerMaterialization(
        result=result,
        result_payload=materialized_payload,
        artifact_records=artifact_records,
        assistant_message_for_persistence=assistant_message_for_persistence,
        assistant_message_metadata=assistant_message_metadata,
    )


__all__ = [
    "AnswerPersistenceLimits",
    "VerifiedAnswerMaterialization",
    "WorkerAnswerMaterialization",
    "assistant_artifact_metadata",
    "build_answer_materialization_proof",
    "materialize_worker_answer",
    "sanitize_assistant_message",
    "verify_answer_materialization",
]
