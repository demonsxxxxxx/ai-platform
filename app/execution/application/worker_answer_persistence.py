"""Execution-owned terminal answer materialization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Callable

from app.streaming.api import AssistantAnswerReceiptError, WorkerV4Capabilities

_ANSWER_BODY_REFERENCE = "The complete assistant response is available in the run history."


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


def _strip_local_output_paths(message: str) -> str:
    lines = []
    for line in message.splitlines():
        stripped = line.strip()
        if stripped.startswith(("详细报告:", "批注文档:")) and "/tmp/" in stripped:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def append_artifact_links(message: str, artifact_records: list[dict[str, Any]]) -> str:
    base = _strip_local_output_paths(message)
    if not artifact_records:
        return base
    links = [f"- {item['label']}: {item['download_url']}" for item in artifact_records]
    suffix = "输出文件:\n" + "\n".join(links)
    return f"{base}\n\n{suffix}" if base else suffix


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

    reconstructed_message = append_artifact_links(
        reconstructed.text,
        artifact_records,
    )
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
    return WorkerAnswerMaterialization(
        result=result,
        result_payload=materialized_payload,
        artifact_records=artifact_records,
        assistant_message_for_persistence=assistant_message_for_persistence,
        assistant_message_metadata=assistant_message_metadata,
    )


__all__ = [
    "AnswerPersistenceLimits",
    "WorkerAnswerMaterialization",
    "append_artifact_links",
    "materialize_worker_answer",
]
