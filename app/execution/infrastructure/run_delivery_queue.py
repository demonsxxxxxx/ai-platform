"""Adapter from frozen Run delivery intents to the existing Redis queue owner."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.execution.application.run_delivery_intents import (
    FrozenQueueMessage,
    QueueDeliveryError,
    QueueDeliveryReceipt,
)


class RedisRunDeliveryQueue:
    """Translate frozen values through legacy queue callables supplied by bootstrap."""

    def __init__(
        self,
        *,
        payload_model: Any,
        payload_errors: tuple[type[BaseException], ...],
        validate_skill_manifest_refs: Callable[[Any], None],
        enqueue_run_with_metadata: Callable[[dict[str, Any]], Awaitable[Any]],
        read_queue_admission: Callable[[dict[str, Any]], Awaitable[Any]],
        queue_admission_rejected: type[BaseException],
    ) -> None:
        self._payload_model = payload_model
        self._payload_errors = payload_errors
        self._validate_skill_manifest_refs = validate_skill_manifest_refs
        self._enqueue_run_with_metadata = enqueue_run_with_metadata
        self._read_queue_admission = read_queue_admission
        self._queue_admission_rejected = queue_admission_rejected

    def freeze(self, payload: Mapping[str, Any]) -> FrozenQueueMessage:
        try:
            validated = self._payload_model.model_validate(dict(payload))
            self._validate_skill_manifest_refs(validated.skill_manifests)
        except self._payload_errors as exc:
            raise QueueDeliveryError(
                "queue_payload_invalid", definitive_rejection=True
            ) from exc
        payload_json = validated.model_dump_json()
        return FrozenQueueMessage(
            payload_json=payload_json,
            message_id=hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
        )

    def _decode_frozen(self, payload_json: str, *, message_id: str) -> dict[str, Any]:
        try:
            validated = self._payload_model.model_validate_json(payload_json)
            self._validate_skill_manifest_refs(validated.skill_manifests)
        except self._payload_errors as exc:
            raise QueueDeliveryError(
                "queue_payload_invalid", definitive_rejection=True
            ) from exc
        canonical = validated.model_dump_json()
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if canonical != payload_json or digest != message_id:
            raise QueueDeliveryError(
                "queue_payload_identity_mismatch", definitive_rejection=True
            )
        return validated.model_dump(mode="json")

    async def readback(
        self, payload_json: str, *, message_id: str
    ) -> QueueDeliveryReceipt | None:
        payload = self._decode_frozen(payload_json, message_id=message_id)
        try:
            metadata = await self._read_queue_admission(payload)
        except self._queue_admission_rejected as exc:
            raise QueueDeliveryError(
                str(exc),
                definitive_rejection=str(exc) != "run_reconciliation_in_progress",
            ) from exc
        if metadata is None:
            return None
        return _receipt(metadata)

    async def enqueue(
        self, payload_json: str, *, message_id: str
    ) -> QueueDeliveryReceipt:
        payload = self._decode_frozen(payload_json, message_id=message_id)
        try:
            metadata = await self._enqueue_run_with_metadata(payload)
        except self._queue_admission_rejected as exc:
            raise QueueDeliveryError(
                str(exc),
                definitive_rejection=str(exc) == "queue_payload_invalid",
            ) from exc
        return _receipt(metadata)


def _receipt(metadata: Any) -> QueueDeliveryReceipt:
    return QueueDeliveryReceipt(
        queue_position=int(metadata.queue_position),
        queue_admission_ordinal=int(metadata.queue_admission_ordinal),
        message_id=str(metadata.message_id),
        source=str(metadata.source),
    )
