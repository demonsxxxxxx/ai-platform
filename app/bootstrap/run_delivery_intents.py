"""Bind Execution's durable queue-delivery recovery to PostgreSQL and Redis."""

from pydantic import ValidationError

from app.conversations.api import (
    record_accepted_chat_delivery_receipt,
    record_accepted_chat_delivery_rejection,
)
from app.persistence import chat_submissions
from app.streaming.infrastructure import run_events_postgres
from app.execution.application.run_delivery_intents import RunDeliveryIntents
from app.execution.infrastructure import run_delivery_intents_postgres
from app.execution.infrastructure.run_delivery_queue import RedisRunDeliveryQueue
from app.models import QueueRunPayload
from app.runs.application.queue_delivery_failure import QueueDeliveryFailureOwner
from app.runs.infrastructure import queue_delivery_failure_postgres
from app.runs.application.lifecycle import RunLifecycleService
from app.streaming.api import WorkerV4Capabilities
from app.queue import (
    QueueAdmissionRejected,
    enqueue_run_with_metadata,
    read_queue_admission,
    validate_skill_manifest_refs,
)
from app.skills.pinning import SkillVersionMaterializationError


def build_run_delivery_intents(
    settings: object,
    transaction,
    *,
    lifecycle: RunLifecycleService | None = None,
    capabilities: WorkerV4Capabilities | None = None,
) -> RunDeliveryIntents:
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
    failure_owner = None
    if lifecycle is not None and capabilities is not None:
        failure_owner = QueueDeliveryFailureOwner(
            persistence=queue_delivery_failure_postgres,
            lifecycle=lifecycle,
            capabilities=capabilities,
        )

    async def persist_chat_receipt(connection, *, intent, receipt) -> None:
        await record_accepted_chat_delivery_receipt(
            connection,
            intent=intent,
            receipt=receipt,
            get_submission=chat_submissions.get_chat_submission,
            append_event=run_events_postgres.append_event,
            finalize_submission=chat_submissions.finalize_chat_submission,
        )

    async def persist_chat_rejection(connection, *, intent, reason) -> None:
        await record_accepted_chat_delivery_rejection(
            connection,
            intent=intent,
            reason=reason,
            get_submission=chat_submissions.get_chat_submission,
            finalize_submission=chat_submissions.finalize_chat_submission,
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
        delivered_retry_seconds=int(
            getattr(settings, "stale_run_reconciliation_seconds", 900)
        ),
        permanent_rejection_owner=failure_owner,
        chat_receipt_owner=persist_chat_receipt,
        chat_rejection_owner=persist_chat_rejection,
    )
