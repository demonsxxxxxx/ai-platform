"""Bind Execution's durable queue-delivery recovery to PostgreSQL and Redis."""

from pydantic import ValidationError

from app.execution.application.run_delivery_intents import RunDeliveryIntents
from app.execution.infrastructure import run_delivery_intents_postgres
from app.execution.infrastructure.run_delivery_queue import RedisRunDeliveryQueue
from app.models import QueueRunPayload
from app.queue import (
    QueueAdmissionRejected,
    enqueue_run_with_metadata,
    read_queue_admission,
    validate_skill_manifest_refs,
)
from app.skills.pinning import SkillVersionMaterializationError


def build_run_delivery_intents(settings: object, transaction) -> RunDeliveryIntents:
    enabled = getattr(settings, "run_delivery_intents_enabled", False)
    scan_batch_size = int(getattr(settings, "run_delivery_intent_scan_batch_size", 25))
    claim_ttl_seconds = int(
        getattr(settings, "run_delivery_intent_claim_ttl_seconds", 120)
    )
    retry_base_seconds = int(
        getattr(settings, "run_delivery_intent_retry_base_seconds", 5)
    )
    retry_cap_seconds = int(
        getattr(settings, "run_delivery_intent_retry_cap_seconds", 600)
    )
    if retry_cap_seconds < retry_base_seconds:
        raise ValueError("run_delivery_retry_cap_below_base")
    transport = RedisRunDeliveryQueue(
        payload_model=QueueRunPayload,
        payload_errors=(ValidationError, ValueError, SkillVersionMaterializationError),
        validate_skill_manifest_refs=validate_skill_manifest_refs,
        enqueue_run_with_metadata=enqueue_run_with_metadata,
        read_queue_admission=read_queue_admission,
        queue_admission_rejected=QueueAdmissionRejected,
    )
    return RunDeliveryIntents(
        enabled=bool(enabled),
        store=run_delivery_intents_postgres,
        codec=transport,
        queue=transport,
        transaction=transaction,
        scan_batch_size=scan_batch_size,
        claim_ttl_seconds=claim_ttl_seconds,
        retry_base_seconds=retry_base_seconds,
        retry_cap_seconds=retry_cap_seconds,
    )
