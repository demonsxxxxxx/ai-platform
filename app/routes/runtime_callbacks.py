import asyncio
import hashlib
import json
import logging
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from app.bootstrap.context_artifact_transfer import build_artifact_transfer_response
from app.context.api import ArtifactTransferError
from app.bootstrap.run_inputs import RunInputsCallbackRequest
from app.bootstrap.sandbox_callbacks import build_sandbox_callback_authority
from app.bootstrap.execution import (
    ProviderSessionCallbackWireResponse,
    build_provider_session_callback_response,
)
from app.context import api as context_api
from app.context.infrastructure import snapshot_postgres as context_snapshot
from app.context.retrieval import (
    ContextRetrievalAuthority,
    ContextRetrievalDenied,
    ContextRetrievalInputError,
)
from app.context_manifest import available_context_retrieval_tools
from app.db import transaction
from app.bootstrap.provider_epoch_verification import provider_callback_transaction
from app.mcp.api import (
    MCP_CREDENTIAL_SUBJECTS_LEASE_KEY,
    McpRuntimeContextError,
    read_mcp_execution_credential,
)
from app.sandbox.api import (
    SandboxExecutorCallbackLeasePort,
    SandboxExecutorTerminalConflictError,
    SandboxLeaseReleaseScopeMismatchError,
)
from app.platform.tracing import TerminalStageTiming
from app.platform.public_payload import sanitize_public_reasoning_text
from app.streaming.api import PUBLIC_AGENT_PROGRESS_EVENT_TYPE
from app.bootstrap.sandbox_cleanup import build_sandbox_runtime_cleanup
from app.runs.api import (
    RunInputClosed,
    RunInputConflict,
    RunInputError,
    RunInputsService,
    RunDiagnosticsService,
)
from app.runtime.event_bridge import agent_event_to_executor_event
from app.runtime.kernel_contracts import (
    CLAUDE_SDK_TEXT_CHECKPOINT_EVENT_TYPE,
    CLAUDE_SDK_THINKING_SUMMARY_EVENT_TYPE,
)
from app.runtime.sandbox.callback_tokens import (
    callback_token_id_matches_attempt,
    callback_token_matches,
)
from app.runtime.sandbox.container_provider import create_container_provider
from app.runs.api import ModelTokenUsageService, normalize_model_usage_snapshot
from app.runtime.sandbox.contracts import (
    ExecutorCallbackEvent,
    ExecutorContextRetrievalRequest,
    McpCredentialCallbackRequest,
    ProviderSessionCallbackRequest,
    RecoveryOperationCallbackRequest,
    ProviderSessionCallbackResponse,
    PROVIDER_SESSION_READ_VIEW_HEADER,
    PROVIDER_SESSION_CHILD_READ_VIEW,
    executor_callback_receipt_event_count,
    executor_terminal_receipt_payload,
)
from app.runtime.sandbox.event_normalizer import callback_event_to_run_events
from app.runtime.sandbox.executor_signals import (
    ExecutorSignalUnavailable,
    publish_executor_terminal_signal,
)
from app.runtime.sandbox.providers.opensandbox.startup import renew_opensandbox_lifetime
from app.sandbox.api import (
    ExecutorTerminalReceiptIdentity,
    ProviderSessionAppendIdentity,
    SandboxCallbackAuthorityError,
    SandboxCallbackIdentity,
)
from app.settings import get_settings
from app.storage import ObjectStorage, run_storage_io
from app.streaming.api import (
    V4ProjectionError,
    V4PublicationTransportUnavailable,
    WorkerV4Capabilities,
    append_callback_v4_rows,
    callback_item_to_v4,
    callback_thinking_summary_to_v4,
    publish_callback_rows,
)
from app.streaming.infrastructure import run_events_postgres as streaming_run_events
from app.streaming.redis import get_stream_authority

_sandbox_cleanup = build_sandbox_runtime_cleanup()
_sandbox_callback_authority = build_sandbox_callback_authority()

router = APIRouter()
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _RecoveryOperationCompletionIdentity(ProviderSessionAppendIdentity):
    """Type gate for the original writer's terminal operation receipt during cancel drain."""


_TERMINAL_EXECUTOR_CALLBACK_STATUSES = {"completed", "failed", "cancelled"}
MAX_PROVIDER_SESSION_CALLBACK_BODY_BYTES = context_api.MAX_PROVIDER_SESSION_BATCH_BYTES + 64 * 1024


class _CallbackIdentityEnvelope(Protocol):
    run_id: str
    attempt_id: str
    callback_token_id: str


def _callback_identity(callback: _CallbackIdentityEnvelope) -> SandboxCallbackIdentity:
    values = dict(
        run_id=callback.run_id,
        attempt_id=callback.attempt_id,
        callback_token_id=callback.callback_token_id,
        session_id=getattr(callback, "session_id", None),
    )
    if isinstance(callback, ProviderSessionCallbackRequest) and callback.action == "append":
        return ProviderSessionAppendIdentity(**values)
    if isinstance(callback, ExecutorCallbackEvent) and callback.status in _TERMINAL_EXECUTOR_CALLBACK_STATUSES:
        return ExecutorTerminalReceiptIdentity(**values)
    if isinstance(callback, RecoveryOperationCallbackRequest) and callback.operation in {
        "completed",
        "failed",
    }:
        return _RecoveryOperationCompletionIdentity(**values)
    return SandboxCallbackIdentity(**values)


def _executor_terminal_callback_is_receipt_only(
    callback: ExecutorCallbackEvent,
) -> bool:
    """Exclude ordinary events, messages and heartbeats from cancel-tail receipt writes."""
    return (
        callback.status in _TERMINAL_EXECUTOR_CALLBACK_STATUSES
        and callback.terminal_result is not None
        and callback.new_message is None
        and not callback.state_patch
        and not callback.events
    )


def _receive_callback_identity(
    provided_token: str | None,
    callback: _CallbackIdentityEnvelope,
) -> SandboxCallbackIdentity:
    """Authenticate one callback envelope before its operation-specific work."""
    identity = _callback_identity(callback)
    _require_valid_callback_token(provided_token, identity)
    return identity


async def _enforce_provider_session_callback_body_limit(request: Request) -> bytes:
    """Bound raw callback bytes before FastAPI materializes the request model."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_PROVIDER_SESSION_CALLBACK_BODY_BYTES:
            raise HTTPException(status_code=413, detail="provider_session_request_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


async def _provider_session_callback_from_request(
    request: Request,
) -> ProviderSessionCallbackRequest:
    body = await _enforce_provider_session_callback_body_limit(request)
    try:
        return ProviderSessionCallbackRequest.model_validate_json(body)
    except Exception as exc:  # noqa: BLE001 - expose only a stable validation detail.
        raise HTTPException(status_code=422, detail="provider_session_request_invalid") from exc


def _executor_callback_receipt(
    callback: ExecutorCallbackEvent,
    *,
    deduplicated: bool,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "accepted": True,
        "event_count": executor_callback_receipt_event_count(
            input_event_count=len(callback.events)
        ),
    }
    if callback.batch_id is not None:
        receipt["batch_id"] = callback.batch_id
    if deduplicated:
        receipt["deduplicated"] = True
    return receipt


def _runtime_lease_identity_fence(lease: dict[str, Any]) -> tuple[str, ...]:
    """Capture the immutable lease and provider identity across remote renewal."""

    payload = lease.get("lease_payload_json")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=409, detail="sandbox_runtime_attempt_inactive")
    try:
        payload_digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409, detail="sandbox_runtime_attempt_inactive"
        ) from exc
    return (
        str(lease.get("id") or ""),
        str(lease.get("attempt_id") or ""),
        str(lease.get("provider") or "").strip().lower(),
        str(lease.get("runtime_container_id") or ""),
        str(lease.get("runtime_container_name") or ""),
        str(lease.get("runtime_executor_url") or ""),
        str(lease.get("runtime_workspace_container_path") or ""),
        payload_digest,
    )


async def record_executor_callback(
    callback: ExecutorCallbackEvent,
    *,
    capabilities: WorkerV4Capabilities,
    callback_identity: SandboxCallbackIdentity | None = None,
    lease_port: SandboxExecutorCallbackLeasePort,
    run_diagnostics: RunDiagnosticsService | None = None,
    model_token_usage: ModelTokenUsageService | None = None,
) -> dict[str, object]:
    """Persist one fenced sandbox observation or terminal result."""
    expected_identity = _callback_identity(callback)
    if callback_identity is None:
        callback_identity = expected_identity
    elif callback_identity != expected_identity:
        raise HTTPException(status_code=409, detail="callback_identity_mismatch")

    # Compatibility fields are retained in the private callback receipt only;
    # v4 public rows come from the typed post-bridge event subset.
    if callback.batch_id is None and any(
        event.type
        in {
            PUBLIC_AGENT_PROGRESS_EVENT_TYPE,
            CLAUDE_SDK_THINKING_SUMMARY_EVENT_TYPE,
            CLAUDE_SDK_TEXT_CHECKPOINT_EVENT_TYPE,
        }
        for event in callback.events
    ):
        raise HTTPException(status_code=409, detail="callback_batch_id_required")
    if callback.model_usage_snapshot is not None and not callback.batch_id:
        raise HTTPException(status_code=409, detail="callback_batch_id_required")
    callback_for_events = callback.model_copy(update={"new_message": None})
    events = callback_event_to_run_events(callback_for_events)
    v4_items = []
    committed_rows = ()
    authority = None
    callback_deduplicated = False
    source_digest = hashlib.sha256(
        json.dumps(
            callback_for_events.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    event_batch: list[dict[str, Any]] = [
        {
            "event_type": "executor_callback",
            "stage": "executor",
            "message": f"Executor callback: {callback.status}",
            "payload": {
                "callback_status": callback.status,
                "attempt_id": callback.attempt_id,
                "batch_id": callback.batch_id,
                "progress": callback.progress,
                "source_digest": source_digest,
                "visible_to_user": False,
            },
        }
    ]
    for item_index, event in enumerate(events):
        if event.type == CLAUDE_SDK_TEXT_CHECKPOINT_EVENT_TYPE:
            event_batch.append(
                {
                    "event_type": "executor_sdk_text_checkpoint",
                    "stage": "executor",
                    "message": "",
                    "visible_to_user": False,
                    "payload": {**event.payload, "visible_to_user": False},
                }
            )
            continue
        thinking_items = callback_thinking_summary_to_v4(
            event.model_dump(mode="python"),
            callback_index=item_index,
            first_batch_index=len(v4_items),
            callback_batch_id=str(callback.batch_id or ""),
            expected_event_type=CLAUDE_SDK_THINKING_SUMMARY_EVENT_TYPE,
            sanitizer=sanitize_public_reasoning_text,
        )
        if thinking_items:
            v4_items.extend(thinking_items)
            event_batch.append(
                {
                    "event_type": "executor_private_event",
                    "stage": "executor",
                    "message": "Executor event projected to v4",
                    "payload": {
                        "source": "executor_callback",
                        "source_event_type": event.type,
                        "source_class": "public_v4",
                        "visible_to_user": False,
                    },
                }
            )
            continue
        executor_event = agent_event_to_executor_event(event)
        item = callback_item_to_v4(
            executor_event,
            callback_index=item_index,
            batch_index=len(v4_items),
            message_id=executor_event.get("message_id"),
        )
        if item is not None and item.source_run_id == callback.run_id:
            v4_items.append(item)
            event_batch.append(
                {
                    "event_type": "executor_private_event",
                    "stage": "executor",
                    "message": "Executor event projected to v4",
                    "payload": {
                        "source": "executor_callback",
                        "source_event_type": item.event_type,
                        "source_class": "public_v4",
                        "visible_to_user": False,
                    },
                }
            )
            continue
        event_batch.append(
            {
                "event_type": "executor_private_event",
                "stage": "executor",
                "message": "Executor event withheld from public projection",
                "payload": {
                    "source": "executor_callback",
                    "source_event_type": event.type,
                    "source_class": "rejected",
                    "visible_to_user": False,
                },
            }
        )

    async def persist_callback(
        conn,
        run_identity: dict[str, Any],
        lease: dict[str, Any],
        *,
        provider_expires_at=None,
        cancellation_tail: bool = False,
    ) -> tuple[tuple[Any, ...], Any, bool]:
        tenant_id = str(run_identity["tenant_id"])
        lease_id = str(lease.get("id") or "")
        committed_rows: tuple[Any, ...] = ()
        authority = None
        callback_deduplicated = False
        if cancellation_tail and (
            not isinstance(callback_identity, ExecutorTerminalReceiptIdentity)
            or not _executor_terminal_callback_is_receipt_only(callback)
        ):
            raise HTTPException(
                status_code=409,
                detail="executor_terminal_receipt_only_required",
            )
        if v4_items and not cancellation_tail:
            if not callback.batch_id:
                raise HTTPException(
                    status_code=409, detail="callback_batch_id_required"
                )
            authority = await get_stream_authority(
                conn, tenant_id=tenant_id, run_id=callback.run_id
            )
            if (
                authority is None
                or authority.attempt_id != callback.attempt_id
                or authority.state != "confirmed"
            ):
                raise HTTPException(
                    status_code=409, detail="sse_stream_attempt_inactive"
                )
        if callback.batch_id:
            receipt = await streaming_run_events.append_event_batch(
                conn,
                tenant_id=tenant_id,
                run_id=callback.run_id,
                attempt_id=callback.attempt_id,
                batch_id=callback.batch_id,
                events=event_batch[:1] if cancellation_tail else event_batch,
            )
            callback_deduplicated = bool(
                receipt.get("duplicate") if isinstance(receipt, dict) else False
            )
            if callback.model_usage_snapshot is not None and not callback_deduplicated:
                if model_token_usage is None:
                    raise RuntimeError("model_token_usage_service_unavailable")
                await model_token_usage.record(
                    conn,
                    tenant_id=tenant_id,
                    run_id=callback.run_id,
                    attempt_id=callback.attempt_id,
                    snapshot=normalize_model_usage_snapshot(callback.model_usage_snapshot.model_dump()),
                    receipt_id=str(receipt["id"]),
                    recorded_at=receipt["callback_received_at"],
                )
            if v4_items and not cancellation_tail:
                try:
                    committed_rows = await append_callback_v4_rows(
                        capabilities,
                        conn,
                        tenant_id=tenant_id,
                        run_id=callback.run_id,
                        attempt_id=callback.attempt_id,
                        batch_id=callback.batch_id,
                        items=v4_items,
                        authority=authority,
                        execution_lease_id=lease_id,
                    )
                except V4ProjectionError as exc:
                    raise HTTPException(
                        status_code=409, detail="callback_v4_projection_invalid"
                    ) from exc
        elif not cancellation_tail:
            for event in event_batch:
                await streaming_run_events.append_event(
                    conn,
                    tenant_id=tenant_id,
                    run_id=callback.run_id,
                    **event,
                )
        if callback.status in _TERMINAL_EXECUTOR_CALLBACK_STATUSES:
            if callback.terminal_result is None:
                raise HTTPException(
                    status_code=422, detail="executor_terminal_result_required"
                )
            if not lease_id:
                raise HTTPException(
                    status_code=503,
                    detail="sandbox_executor_lease_receipt_unavailable",
                )
            terminal_result = callback.terminal_result.model_dump(
                mode="json", exclude_none=True
            )
            try:
                terminal_receipt = executor_terminal_receipt_payload(
                    callback.terminal_result
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail="executor_terminal_result_invalid",
                ) from exc
            terminal_was_new = lease.get("executor_terminal_json") is None
            try:
                terminal_write_scope = (
                    {
                        "cancelling_terminal_identity": callback_identity,
                        "workspace_id": str(run_identity.get("workspace_id") or ""),
                        "user_id": str(run_identity.get("user_id") or ""),
                        "session_id": str(run_identity.get("session_id") or ""),
                    }
                    if cancellation_tail
                    else {}
                )
                await lease_port.record_sandbox_executor_terminal(
                    conn,
                    tenant_id=tenant_id,
                    run_id=callback.run_id,
                    attempt_id=callback.attempt_id,
                    lease_id=lease_id,
                    executor_status=callback.status,
                    terminal_result=terminal_receipt,
                    **terminal_write_scope,
                )
            except (
                SandboxExecutorTerminalConflictError,
                SandboxLeaseReleaseScopeMismatchError,
                ValueError,
            ) as exc:
                raise HTTPException(
                    status_code=409,
                    detail="sandbox_executor_terminal_conflict",
                ) from exc
            if terminal_was_new and run_diagnostics is not None:
                await run_diagnostics.capture_failure_result(
                    conn,
                    tenant_id=tenant_id,
                    run_id=callback.run_id,
                    attempt_id=callback.attempt_id,
                    source="executor_callback",
                    stage="terminal_receipt",
                    error_code=str(
                        terminal_result.get("error_code") or callback.status
                    ),
                    result_json=terminal_result,
                    lease_id=lease_id,
                    callback_id=callback.batch_id,
                )
        elif lease_id:
            settings = get_settings()
            heartbeat = await lease_port.record_sandbox_executor_heartbeat(
                conn,
                tenant_id=tenant_id,
                run_id=callback.run_id,
                attempt_id=callback.attempt_id,
                lease_id=lease_id,
                executor_status="running",
                ttl_seconds=settings.sandbox_lease_ttl_seconds,
            )
            if heartbeat is None:
                raise HTTPException(
                    status_code=409,
                    detail="sandbox_runtime_attempt_inactive",
                )
            if (
                callback.state_patch.get("executor_heartbeat") is True
                and isinstance(heartbeat, dict)
                and str(heartbeat.get("provider") or "").strip().lower()
                == "opensandbox"
            ):
                if provider_expires_at is None:
                    raise HTTPException(
                        status_code=503,
                        detail="sandbox_runtime_renewal_failed",
                    )
                receipt = await lease_port.record_opensandbox_renewal_receipt(
                    conn,
                    tenant_id=tenant_id,
                    run_id=callback.run_id,
                    attempt_id=callback.attempt_id,
                    lease_id=lease_id,
                    provider_expires_at=provider_expires_at,
                )
                if receipt is None:
                    raise HTTPException(
                        status_code=409,
                        detail="sandbox_runtime_attempt_inactive",
                    )
        await _recheck_callback_authority(
            conn,
            tenant_id=tenant_id,
            identity=callback_identity,
        )
        return committed_rows, authority, callback_deduplicated

    heartbeat_requested = (
        callback.status not in _TERMINAL_EXECUTOR_CALLBACK_STATUSES
        and callback.state_patch.get("executor_heartbeat") is True
    )
    renewal_fence: tuple[str, ...] | None = None
    renewal_tenant_id = ""
    renewal_row: dict[str, Any] | None = None
    renewal_settings = None
    if heartbeat_requested:
        async with transaction() as conn:
            run_identity, lease = await _lock_callback_authority(
                conn,
                identity=callback_identity,
            )
            if str(lease.get("provider") or "").strip().lower() == "opensandbox":
                if lease.get("executor_terminal_json") is not None:
                    raise HTTPException(
                        status_code=409,
                        detail="sandbox_runtime_attempt_inactive",
                    )
                renewal_fence = _runtime_lease_identity_fence(lease)
                renewal_tenant_id = str(run_identity["tenant_id"])
                renewal_row = dict(lease)
                renewal_settings = get_settings()
            else:
                committed_rows, authority, callback_deduplicated = await persist_callback(
                    conn, run_identity, lease
                )

    if renewal_fence is not None:
        try:
            persisted_lease = _sandbox_cleanup.container_lease_from_persisted_row(renewal_row or {})
            if persisted_lease is None or persisted_lease.provider != "opensandbox":
                raise ValueError("sandbox_runtime_renewal_lease_unavailable")
            provider = create_container_provider(persisted_lease.provider)
            provider_expires_at = await renew_opensandbox_lifetime(
                provider,
                persisted_lease,
                renewal_settings,
                ttl_seconds=renewal_settings.sandbox_lease_ttl_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep renewal failures disclosure-safe.
            raise HTTPException(
                status_code=503,
                detail="sandbox_runtime_renewal_failed",
            ) from exc
        async with transaction() as conn:
            run_identity, lease = await _lock_callback_authority(
                conn,
                identity=callback_identity,
            )
            if lease.get("executor_terminal_json") is not None:
                raise HTTPException(
                    status_code=409,
                    detail="sandbox_runtime_attempt_inactive",
                )
            if (
                str(run_identity.get("tenant_id") or "") != renewal_tenant_id
                or _runtime_lease_identity_fence(lease) != renewal_fence
            ):
                raise HTTPException(
                    status_code=409,
                    detail="sandbox_runtime_owner_generation_stale",
                )
            committed_rows, authority, callback_deduplicated = await persist_callback(
                conn,
                run_identity,
                lease,
                provider_expires_at=provider_expires_at,
            )
    elif not heartbeat_requested:
        with (
            TerminalStageTiming(
                "terminal_receipt_commit", run_id=callback.run_id,
                attempt_id=callback.attempt_id, terminal_status=callback.status,
            )
            if callback.status in _TERMINAL_EXECUTOR_CALLBACK_STATUSES
            else nullcontext()
        ):
            async with transaction() as conn:
                cancellation_tail = False
                if isinstance(callback_identity, ExecutorTerminalReceiptIdentity):
                    run_identity, lease, cancellation_tail = (
                        await _lock_executor_terminal_callback_authority(
                            conn,
                            identity=callback_identity,
                        )
                    )
                else:
                    run_identity, lease = await _lock_callback_authority(
                        conn,
                        identity=callback_identity,
                    )
                committed_rows, authority, callback_deduplicated = await persist_callback(
                    conn,
                    run_identity,
                    lease,
                    cancellation_tail=cancellation_tail,
                )
    if callback.status in _TERMINAL_EXECUTOR_CALLBACK_STATUSES:
        try:
            with TerminalStageTiming(
                "terminal_receipt_signal", run_id=callback.run_id,
                attempt_id=callback.attempt_id, terminal_status=callback.status,
            ):
                await publish_executor_terminal_signal()
        except ExecutorSignalUnavailable:
            logger.warning(
                "executor_terminal_reconciliation_signal_unavailable",
                extra={
                    "run_id": callback.run_id,
                    "attempt_id": callback.attempt_id,
                },
            )
    try:
        with (
            TerminalStageTiming(
                "terminal_receipt_publication", run_id=callback.run_id,
                attempt_id=callback.attempt_id, terminal_status=callback.status,
            )
            if callback.status in _TERMINAL_EXECUTOR_CALLBACK_STATUSES
            else nullcontext()
        ):
            await publish_callback_rows(capabilities, committed_rows, authority=authority)
    except V4PublicationTransportUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="callback_stream_unavailable"
        ) from exc
    return _executor_callback_receipt(
        callback,
        deduplicated=callback_deduplicated,
    )


_PROVIDER_SESSION_LIMIT_ERRORS = frozenset(
    {
        "provider_session_entry_too_large",
        "provider_session_entry_batch_too_large",
        "provider_session_transcript_too_large",
        "provider_session_image_too_large",
        "provider_session_image_count_exceeded",
    }
)
_PROVIDER_SESSION_CONFLICT_ERRORS = frozenset(
    {
        "provider_session_identity_mismatch",
        "provider_session_append_conflict",
        "provider_session_append_sequence_invalid",
        "provider_session_epoch_unavailable",
        "provider_session_spec_mismatch",
        "provider_session_lineage_busy",
        "provider_session_owner_invalid",
        "provider_session_writer_conflict",
        "provider_session_entry_conflict",
        "provider_session_image_snapshot_conflict",
        "provider_session_image_reservation_expired",
        "provider_session_image_upload_pending",
    }
)


def _provider_session_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, context_api.ProviderSessionNotFoundError):
        return HTTPException(status_code=404, detail="provider_session_not_found")
    code = str(exc)
    if code in _PROVIDER_SESSION_LIMIT_ERRORS:
        return HTTPException(status_code=413, detail=code)
    if code in _PROVIDER_SESSION_CONFLICT_ERRORS:
        return HTTPException(status_code=409, detail=code)
    return HTTPException(status_code=503, detail="provider_session_callback_failed")


@router.post("/runtime/callbacks/inputs")
async def run_inputs_callback(
    request: Request,
    callback: RunInputsCallbackRequest,
    callback_token: str | None = Header(
        default=None, alias="X-AI-Platform-Callback-Token"
    ),
) -> dict[str, Any]:
    """Apply one Run input operation through the existing callback authority."""

    identity = _receive_callback_identity(callback_token, callback)
    service = getattr(request.app.state, "run_inputs_service", None)
    if not isinstance(service, RunInputsService):
        raise HTTPException(status_code=503, detail="run_inputs_unavailable")
    from app.execution.api import RecoveryOperationError
    try:
        async with provider_callback_transaction(
            transaction, authorize=_lock_callback_authority, run_id=callback.run_id,
            attempt_id=callback.attempt_id, callback_token_id=callback.callback_token_id,
            allow_provider_tail=False,
        ) as conn:
            run_identity, _lease = await _lock_callback_authority(
                conn,
                identity=identity,
            )
            result = await service.callback(
                conn,
                tenant_id=str(run_identity.get("tenant_id") or ""),
                run=run_identity,
                run_id=callback.run_id,
                attempt_id=callback.attempt_id,
                operation=callback.operation,
                question_id=callback.question_id,
                questions=(
                    [item.model_dump(exclude_none=True) for item in callback.questions]
                    if callback.questions is not None
                    else None
                ),
                input_ids=[str(item) for item in callback.input_ids]
                if callback.input_ids is not None
                else None,
            )
            if callback.operation in {"open", "poll"}:
                from app.bootstrap.recovery_operations import publish_recovery_source_questions
                result.update(await publish_recovery_source_questions(
                    conn, run=run_identity, attempt_id=callback.attempt_id,
                ))
            return result
    except RecoveryOperationError as exc:
        raise HTTPException(status_code=409, detail=exc.code) from exc
    except RunInputError as exc:
        status_code = (
            409
            if isinstance(exc, (RunInputClosed, RunInputConflict))
            else 422
        )
        raise HTTPException(status_code=status_code, detail=exc.code) from exc


@router.post("/runtime/callbacks/recovery-operation")
async def recovery_operation_callback(
    callback: RecoveryOperationCallbackRequest,
    callback_token: str | None = Header(default=None, alias="X-AI-Platform-Callback-Token"),
) -> dict[str, Any]:
    from app.bootstrap.recovery_operations import build_recovery_operations
    from app.execution.api import RecoveryInvocation, RecoveryOperationScope, RecoveryOperationError

    identity = _receive_callback_identity(callback_token, callback)
    allow_provider_tail = isinstance(identity, ProviderSessionAppendIdentity)

    async def authorize_callback(conn, **_binding):
        return await _lock_callback_authority(conn, identity=identity)

    try:
        async with provider_callback_transaction(
            transaction, authorize=authorize_callback, run_id=callback.run_id,
            attempt_id=callback.attempt_id, callback_token_id=callback.callback_token_id,
            allow_provider_tail=allow_provider_tail,
        ) as conn:
            run, lease = await _lock_callback_authority(conn, identity=identity)
            lease_payload = lease.get("lease_payload_json")
            generation = lease_payload.get("owner_generation") if isinstance(lease_payload, dict) else None
            if type(generation) is not int or generation < 1:
                raise RecoveryOperationError("recovery_operation_authority_stale")
            invocation = RecoveryInvocation(
                scope=RecoveryOperationScope(
                    **{
                        key: str(run.get(key) or "")
                        for key in ("tenant_id", "workspace_id", "user_id", "session_id")
                    }
                ),
                run_id=callback.run_id,
                attempt_id=callback.attempt_id,
                owner_generation=generation,
                invocation_id=callback.invocation_id,
                tool_identity=callback.tool_identity,
            )
            service, _inputs = build_recovery_operations()
            if callback.operation in {"completed", "failed"}:
                cursor = await conn.execute(
                    """select operation_key from execution_recovery_operations
                       where tenant_id=%s and workspace_id=%s and user_id=%s and session_id=%s
                         and source_run_id=%s and source_attempt_id=%s and native_tool_use_id=%s
                         and tool_identity=%s""",
                    (
                        *invocation.scope.mapping().values(),
                        callback.run_id,
                        callback.attempt_id,
                        callback.invocation_id,
                        callback.tool_identity,
                    ),
                )
                operation = await cursor.fetchone()
                if operation is None:
                    raise RecoveryOperationError("recovery_operation_source_invalid")
                await service.record_reported(conn, invocation=invocation,
                    operation_key=operation["operation_key"], status=callback.operation)
                return {"accepted": True}
            authority = await service._authority(conn, invocation)
            subject_cursor = await conn.execute(
                "select execution_spec_json from run_attempts where tenant_id=%s and id=%s and run_id=%s",
                (run["tenant_id"], callback.attempt_id, callback.run_id),
            )
            subject_row = await subject_cursor.fetchone()
            spec = subject_row.get("execution_spec_json") if isinstance(subject_row, dict) else None
            inputs = spec.get("input") if isinstance(spec, dict) else None
            subjects = inputs.get("_runtime_tool_policy_subjects") if isinstance(inputs, dict) else None
            if not isinstance(subjects, list):
                raise RecoveryOperationError("recovery_operation_tool_unauthorized")
            if not any(item.get("identity") == invocation.tool_identity
                       and all(item.get(key) is True for key in ("registered", "declared", "active", "distributed"))
                       for item in subjects if isinstance(item, dict)):
                raise RecoveryOperationError("recovery_operation_tool_unauthorized")
            if callback.operation == "query":
                if not authority.requires_recovery_gate:
                    raise RecoveryOperationError("recovery_operation_binding_invalid")
                if not authority.manual_confirmation_available and invocation.tool_identity not in service.authoritative_queries:
                    return await service.query(conn, invocation=invocation)
                binding = await service.persistence.get_binding(conn, invocation=invocation)
                if binding is None:
                    cursor = await conn.execute(
                        """select attempt.execution_spec_json->'context_pack'->'conversation_context'->'recovery' as recovery
                           from run_attempts attempt where tenant_id=%s and run_id=%s and id=%s""",
                        (run["tenant_id"], callback.run_id, callback.attempt_id),
                    )
                    recovery_row = await cursor.fetchone()
                    recovery = recovery_row.get("recovery") if isinstance(recovery_row, dict) else None
                    recovery = recovery if isinstance(recovery, dict) else {}
                    frozen_epoch_id = recovery.get("provider_epoch_id")
                    if not isinstance(frozen_epoch_id, str) or not frozen_epoch_id:
                        raise RecoveryOperationError("recovery_operation_binding_invalid")
                    cursor = await conn.execute(
                        """select operation_key from execution_recovery_operations
                           where tenant_id=%s and workspace_id=%s and user_id=%s and session_id=%s
                             and source_run_id=%s and source_attempt_id=%s and native_tool_use_id=%s
                             and tool_identity=%s""",
                        (*invocation.scope.mapping().values(), recovery.get("source_run_id"),
                         recovery.get("source_attempt_id"), invocation.invocation_id, invocation.tool_identity),
                    )
                    operation = await cursor.fetchone()
                    if operation is None:
                        return {"decision": "deny", "reason_code": "recovery_operation_binding_missing"}
                    await service.bind_invocation(
                        conn, invocation=invocation, operation_key=operation["operation_key"],
                        frozen_binding_ref=frozen_epoch_id,
                        public_summary=authority.public_summary or "继续原生记录中尚待核实的外部操作",
                    )
                return await service.query(conn, invocation=invocation)
            if callback.operation == "started":
                return await service.record_started(
                    conn, invocation=invocation, tool_facts={"write_capable": True},
                    public_summary="本轮已授权外部工具操作",
                )
            if callback.operation == "close":
                await service.close_invocation(conn, invocation=invocation, reason=callback.reason or "denied")
            else:
                cursor = await conn.execute(
                    """select operation_key from execution_recovery_operations
                       where tenant_id=%s and workspace_id=%s and user_id=%s and session_id=%s
                         and source_run_id=%s and source_attempt_id=%s and native_tool_use_id=%s
                         and tool_identity=%s""",
                    (
                        *invocation.scope.mapping().values(),
                        callback.run_id,
                        callback.attempt_id,
                        callback.invocation_id,
                        callback.tool_identity,
                    ),
                )
                operation = await cursor.fetchone()
                if operation is None:
                    raise RecoveryOperationError("recovery_operation_source_invalid")
                await service.record_reported(conn, invocation=invocation,
                                              operation_key=operation["operation_key"], status=callback.operation)
            return {"accepted": True}
    except RecoveryOperationError as exc:
        raise HTTPException(status_code=409, detail=exc.code) from exc


@router.post("/runtime/callbacks/mcp-credential")
async def mcp_credential_callback(
    callback: McpCredentialCallbackRequest,
    response: Response,
    callback_token: str | None = Header(default=None, alias="X-AI-Platform-Callback-Token"),
) -> dict[str, str]:
    identity = _receive_callback_identity(callback_token, callback)
    async with transaction() as conn:
        run_identity, lease = await _lock_callback_authority(conn, identity=identity)
        payload = lease.get("lease_payload_json") or {}
        bindings = payload.get(MCP_CREDENTIAL_SUBJECTS_LEASE_KEY) or {}
        subject = bindings.get(callback.server_id)
        if not isinstance(subject, dict):
            raise HTTPException(status_code=403, detail="mcp_credential_not_authorized")
        subject = dict(subject)
        scope = {key: str(run_identity.get(key) or "") for key in ("tenant_id", "user_id")}
        lease_id = lease.get("id")
    try:
        jwt = await read_mcp_execution_credential(**scope, credential_subject=subject)
    except McpRuntimeContextError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None
    async with transaction() as conn:
        final_run_identity, final_lease = await _lock_callback_authority(conn, identity=identity)
        final_bindings = (final_lease.get("lease_payload_json") or {}).get(MCP_CREDENTIAL_SUBJECTS_LEASE_KEY) or {}
        if (any(str(final_run_identity.get(key) or "") != value for key, value in scope.items())
            or final_lease.get("id") != lease_id
            or final_bindings.get(callback.server_id) != subject):
            raise HTTPException(status_code=409, detail="mcp_credential_scope_changed")
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"
    return {"jwt": jwt}


@router.post(
    "/runtime/callbacks/provider-session",
    response_model=ProviderSessionCallbackResponse,
)
async def provider_session_callback(
    callback: ProviderSessionCallbackRequest = Depends(_provider_session_callback_from_request),
    callback_token: str | None = Header(default=None, alias="X-AI-Platform-Callback-Token"),
    provider_read_view: str | None = Header(default=None, alias=PROVIDER_SESSION_READ_VIEW_HEADER),
) -> ProviderSessionCallbackWireResponse:
    """Broker one exact-run Claude SessionStore operation."""

    identity = _receive_callback_identity(callback_token, callback)
    include_children = (
        callback.action == "list_subkeys"
        and provider_read_view == PROVIDER_SESSION_CHILD_READ_VIEW
    )
    try:
        async with transaction() as conn:
            run_identity, _lease = await _lock_callback_authority(
                conn,
                identity=identity,
            )
            binding = {
                "tenant_id": str(run_identity.get("tenant_id") or ""),
                "workspace_id": str(run_identity.get("workspace_id") or ""),
                "user_id": str(run_identity.get("user_id") or ""),
                "session_id": str(run_identity.get("session_id") or ""),
                "agent_id": str(run_identity.get("agent_id") or ""),
            }
            operation = dict(
                **binding, run_id=callback.run_id, attempt_id=callback.attempt_id,
                provider_session_id=callback.provider_session_id, action=callback.action,
                **({"provider_epoch_id": callback.provider_epoch_id} if callback.provider_epoch_id is not None else {}),
                entries=callback.entries, subpath=callback.subpath,
                expected_sequence=callback.expected_sequence,
                **({"include_child_entries": True} if include_children else {}),
            )
            media = await context_api.prepare_provider_session_media(conn, **operation)
        await context_api.transfer_provider_session_media(media)
        async with transaction() as conn:
            final_identity, _lease = await _lock_callback_authority(
                conn, identity=identity,
            )
            if any(str(final_identity.get(key) or "") != value for key, value in binding.items()):
                raise context_api.ProviderSessionConflictError("provider_session_scope_invalid")
            result = await context_api.execute_provider_session_callback(
                conn, **operation, **({"media": media} if media is not None else {}),
            )
            return build_provider_session_callback_response(
                result, include_child_entries=include_children,
            )
    except HTTPException:
        raise
    except (context_api.ProviderSessionContinuityError, ValueError) as exc:
        if getattr(exc, "debug", None):
            from app.bootstrap.run_diagnostics import record_execution_debug
            await record_execution_debug(run_id=callback.run_id, attempt_id=callback.attempt_id,
                                         error_code=str(exc), debug=exc.debug)
        raise _provider_session_http_error(exc) from exc
    except Exception as exc:  # noqa: BLE001 - callback details stay private.
        raise HTTPException(status_code=503, detail="provider_session_callback_failed") from exc


async def _recheck_callback_authority(
    conn,
    *,
    tenant_id: str,
    identity: SandboxCallbackIdentity | None = None,
    **binding,
) -> dict[str, Any]:
    if identity is None:
        identity = SandboxCallbackIdentity(**binding)
    elif binding:
        raise TypeError("callback identity cannot be combined with legacy binding fields")
    try:
        if isinstance(identity, ExecutorTerminalReceiptIdentity):
            return await _sandbox_callback_authority.recheck_executor_terminal_receipt(
                conn, tenant_id=tenant_id, identity=identity,
            )
        if isinstance(identity, ProviderSessionAppendIdentity):
            return await _sandbox_callback_authority.recheck_provider_session_append(
                conn, tenant_id=tenant_id, identity=identity,
            )
        return await _sandbox_callback_authority.recheck_attempt(
            conn, tenant_id=tenant_id, identity=identity,
        )
    except SandboxCallbackAuthorityError as exc:
        raise _callback_authority_http_error(exc) from exc


async def _lock_executor_terminal_callback_authority(
    conn,
    *,
    identity: ExecutorTerminalReceiptIdentity,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    if not isinstance(identity, ExecutorTerminalReceiptIdentity):
        raise HTTPException(
            status_code=409,
            detail="executor_terminal_receipt_identity_required",
        )
    try:
        return await _sandbox_callback_authority.lock_executor_terminal_receipt(
            conn, identity,
        )
    except SandboxCallbackAuthorityError as exc:
        raise _callback_authority_http_error(exc) from exc


async def _lock_callback_authority(
    conn,
    identity: SandboxCallbackIdentity | None = None,
    *,
    allow_provider_tail: bool = False,
    **binding,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if identity is None:
        identity = SandboxCallbackIdentity(**binding)
    elif binding:
        raise TypeError("callback identity cannot be combined with legacy binding fields")
    if isinstance(identity, ExecutorTerminalReceiptIdentity):
        raise HTTPException(
            status_code=409,
            detail="executor_terminal_receipt_authority_required",
        )
    if allow_provider_tail and not isinstance(identity, ProviderSessionAppendIdentity):
        raise HTTPException(status_code=409, detail="provider_tail_identity_required")
    try:
        if isinstance(identity, ProviderSessionAppendIdentity):
            return await _sandbox_callback_authority.lock_provider_session_append(conn, identity)
        return await _sandbox_callback_authority.lock_run_then_attempt(conn, identity)
    except SandboxCallbackAuthorityError as exc:
        raise _callback_authority_http_error(exc) from exc


def _callback_authority_http_error(exc: SandboxCallbackAuthorityError) -> HTTPException:
    detail = str(exc)
    return HTTPException(status_code=404 if detail == "run_not_found" else 409, detail=detail)


def _require_valid_callback_token(
    provided_token: str | None,
    identity: SandboxCallbackIdentity,
) -> None:
    expected_token = get_settings().sandbox_callback_token
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="callback_token_not_configured",
        )
    if not callback_token_id_matches_attempt(
        identity.callback_token_id, run_id=identity.run_id, attempt_id=identity.attempt_id
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_callback_token",
        )
    if not callback_token_matches(
        secret=expected_token,
        token_id=identity.callback_token_id,
        provided_token=provided_token,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_callback_token",
        )


@router.post("/runtime/callbacks/executor")
async def executor_callback(
    request: Request,
    callback: ExecutorCallbackEvent,
    callback_token: str | None = Header(
        default=None, alias="X-AI-Platform-Callback-Token"
    ),
) -> dict[str, object]:
    callback_identity = _receive_callback_identity(callback_token, callback)
    runtime = request.app.state.run_stream_runtime
    return await record_executor_callback(
        callback,
        capabilities=runtime.worker_capabilities,
        callback_identity=callback_identity,
        lease_port=request.app.state.sandbox_executor_callback_leases,
        run_diagnostics=request.app.state.run_diagnostics_service,
        model_token_usage=request.app.state.model_token_usage_service,
    )


@router.post("/runtime/callbacks/context-retrieval")
async def executor_context_retrieval_callback(
    request: ExecutorContextRetrievalRequest,
    callback_token: str | None = Header(default=None, alias="X-AI-Platform-Callback-Token"),
    accept: str | None = Header(default=None),
) -> Any:
    """Broker one exact snapshot-authorized retrieval without exposing backend credentials."""

    callback_identity = _receive_callback_identity(callback_token, request)
    if request.action == "stage_run_artifact_to_workspace" and accept == "application/octet-stream":
        try:
            return await build_artifact_transfer_response(
                request, transaction=transaction, lock_authority=_lock_callback_authority,
                load_snapshot=context_snapshot.get_bound_executor_context_snapshot,
                storage_factory=ObjectStorage, storage_io=run_storage_io,
                tools=available_context_retrieval_tools, record_event=streaming_run_events.append_event,
            )
        except ContextRetrievalInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except (ArtifactTransferError, ContextRetrievalDenied) as exc:
            reason = str(exc)
            code = 413 if reason in {"context_artifact_too_large", "context_artifact_size_required"} else 403
            raise HTTPException(status_code=code, detail=reason) from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="context_retrieval_failed") from exc
    async with transaction() as conn:
        run_identity, _lease = await _lock_callback_authority(
            conn,
            identity=callback_identity,
        )
        tenant_id = str(run_identity.get("tenant_id") or "")
        workspace_id = str(run_identity.get("workspace_id") or "")
        user_id = str(run_identity.get("user_id") or "")
        agent_id = str(run_identity.get("agent_id") or "")
        snapshot = await context_snapshot.get_bound_executor_context_snapshot(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=request.session_id,
            run_id=request.run_id,
        )
        if snapshot is None:
            raise HTTPException(status_code=409, detail="context_snapshot_unavailable")
        payload = snapshot.get("payload_json") if isinstance(snapshot, dict) else None
        manifest = payload.get("context_manifest") if isinstance(payload, dict) else None
        if request.action not in available_context_retrieval_tools(manifest):
            raise HTTPException(status_code=403, detail="context_retrieval_not_authorized")
        context_identity = {
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "user_id": user_id,
            "session_id": request.session_id,
            "run_id": request.run_id,
            "agent_id": agent_id,
        }
        retrieval = ContextRetrievalAuthority.for_broker_connection(
            conn,
            ObjectStorage(),
            storage_io=run_storage_io,
        )
        try:
            result = await retrieval.execute(request.action, context_identity, request.arguments)
        except ContextRetrievalInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ContextRetrievalDenied as exc:
            reason = str(exc)
            if reason in {
                "context_file_too_large",
                "context_artifact_too_large",
                "context_file_size_required",
                "context_artifact_size_required",
            }:
                raise HTTPException(status_code=413, detail=reason) from exc
            raise HTTPException(status_code=403, detail="context_scope_denied") from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=503, detail="context_retrieval_failed") from exc
        await streaming_run_events.append_event(
            conn,
            tenant_id=tenant_id,
            run_id=request.run_id,
            event_type="context_retrieved",
            stage="context",
            message="Sandbox context retrieval completed",
            payload={
                "action": request.action,
                "result": "allowed",
                "visible_to_user": False,
            },
        )
        await _recheck_callback_authority(
            conn,
            tenant_id=tenant_id,
            identity=callback_identity,
        )
    return {"result": result}
