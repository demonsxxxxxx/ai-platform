import hashlib
import json
import logging
from types import SimpleNamespace
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.context import api as context_api
from app.context.infrastructure import snapshot_postgres as context_snapshot
from app.context.retrieval import (
    ContextRetrievalAuthority,
    ContextRetrievalDenied,
    ContextRetrievalInputError,
)
from app.context_manifest import available_context_retrieval_tools
from app.db import transaction
from app.files.api import (
    ProfileDriveTransferError,
    open_profile_drive_file,
    profile_drive_streaming_response,
)
from app.mcp.api import McpRuntimeContextError, get_mcp_principal_jwt_store
from app.platform.postgres import sandbox_leases as sandbox_lease_repository
from app.platform.public_payload import sanitize_public_reasoning_text
from app.public_execution import PUBLIC_AGENT_PROGRESS_EVENT_TYPE
from app.routes.sandbox_runtime_cleanup import container_lease_from_persisted_row
from app.runs.api import RunDiagnosticsService
from app.runs.infrastructure import postgres as runs_postgres
from app.runtime.event_bridge import agent_event_to_executor_event
from app.runtime.kernel_contracts import CLAUDE_SDK_THINKING_SUMMARY_EVENT_TYPE
from app.runtime.sandbox.callback_tokens import (
    callback_token_id_matches_attempt,
    callback_token_matches,
)
from app.runtime.sandbox.container_provider import create_container_provider
from app.runtime.sandbox.contracts import (
    PROFILE_DRIVE_STAGE_LEASE_FLAG,
    PROFILE_DRIVE_STAGE_MAX_BYTES,
    PROFILE_DRIVE_STAGE_TOOL,
    ExecutorCallbackEvent,
    ExecutorContextRetrievalRequest,
    ProviderSessionCallbackRequest,
    ProviderSessionCallbackResponse,
    executor_callback_receipt_event_count,
    executor_terminal_receipt_payload,
)
from app.runtime.sandbox.event_normalizer import callback_event_to_run_events
from app.runtime.sandbox.executor_signals import (
    ExecutorSignalUnavailable,
    publish_executor_terminal_signal,
)
from app.runtime.sandbox.providers.opensandbox.startup import renew_opensandbox_lifetime
from app.sandbox.infrastructure import leases_postgres as sandbox_leases
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

router = APIRouter()
logger = logging.getLogger(__name__)


TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled", "canceled"}
_TERMINAL_EXECUTOR_CALLBACK_STATUSES = {"completed", "failed", "cancelled"}
MAX_PROVIDER_SESSION_CALLBACK_BODY_BYTES = context_api.MAX_PROVIDER_SESSION_BATCH_BYTES + 64 * 1024


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


async def record_executor_callback(
    callback: ExecutorCallbackEvent,
    *,
    capabilities: WorkerV4Capabilities,
    run_diagnostics: RunDiagnosticsService | None = None,
) -> dict[str, object]:
    """Persist one fenced sandbox observation or terminal result."""

    # Compatibility fields are retained in the private callback receipt only;
    # v4 public rows come from the typed post-bridge event subset.
    if callback.batch_id is None and any(
        event.type
        in {
            PUBLIC_AGENT_PROGRESS_EVENT_TYPE,
            CLAUDE_SDK_THINKING_SUMMARY_EVENT_TYPE,
        }
        for event in callback.events
    ):
        raise HTTPException(status_code=409, detail="callback_batch_id_required")
    callback_for_events = callback.model_copy(update={"new_message": None})
    events = callback_event_to_run_events(callback_for_events)
    v4_items = []
    committed_rows = ()
    authority = None
    callback_deduplicated = False
    tenant_id = ""
    lease_id = ""
    async with transaction() as conn:
        run_identity, lease = await _lock_current_runtime_attempt_then_run(
            conn,
            run_id=callback.run_id,
            attempt_id=callback.attempt_id,
            callback_token_id=callback.callback_token_id,
            session_id=callback.session_id,
        )
        tenant_id = str(run_identity["tenant_id"])
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
        lease_id = str(lease.get("id") or "") if isinstance(lease, dict) else ""
        for item_index, event in enumerate(events):
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
        if v4_items:
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
                events=event_batch,
            )
            callback_deduplicated = bool(
                receipt.get("duplicate") if isinstance(receipt, dict) else False
            )
            if v4_items:
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
        else:
            for event in event_batch:
                await streaming_run_events.append_event(
                    conn,
                    tenant_id=tenant_id,
                    run_id=callback.run_id,
                    **event,
                )
        lease_id = str(lease.get("id") or "") if isinstance(lease, dict) else ""
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
                await sandbox_lease_repository.record_sandbox_executor_terminal(
                    conn,
                    tenant_id=tenant_id,
                    run_id=callback.run_id,
                    attempt_id=callback.attempt_id,
                    lease_id=lease_id,
                    executor_status=callback.status,
                    terminal_result=terminal_receipt,
                )
            except (
                sandbox_lease_repository.SandboxExecutorTerminalConflictError,
                sandbox_lease_repository.SandboxLeaseReleaseScopeMismatchError,
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
            heartbeat = (
                await sandbox_lease_repository.record_sandbox_executor_heartbeat(
                    conn,
                    tenant_id=tenant_id,
                    run_id=callback.run_id,
                    attempt_id=callback.attempt_id,
                    lease_id=lease_id,
                    executor_status="running",
                    ttl_seconds=settings.sandbox_lease_ttl_seconds,
                )
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
                try:
                    persisted_lease = container_lease_from_persisted_row(heartbeat)
                    if (
                        persisted_lease is None
                        or persisted_lease.provider != "opensandbox"
                    ):
                        raise ValueError("sandbox_runtime_renewal_lease_unavailable")
                    provider = create_container_provider(persisted_lease.provider)
                    provider_expires_at = await renew_opensandbox_lifetime(
                        provider,
                        persisted_lease,
                        settings,
                        ttl_seconds=settings.sandbox_lease_ttl_seconds,
                    )
                except Exception as exc:  # noqa: BLE001 - renewal is one disclosure-safe failure boundary.
                    raise HTTPException(
                        status_code=503,
                        detail="sandbox_runtime_renewal_failed",
                    ) from exc
                receipt = await sandbox_lease_repository.record_opensandbox_renewal_receipt(
                    conn,
                    tenant_id=tenant_id,
                    run_id=callback.run_id,
                    attempt_id=callback.attempt_id,
                    lease_id=lease_id,
                    provider_expires_at=provider_expires_at,
                )
                if receipt is None:
                    raise HTTPException(status_code=409, detail="sandbox_runtime_attempt_inactive")
        await _require_current_runtime_attempt(
            conn,
            tenant_id=tenant_id,
            run_id=callback.run_id,
            attempt_id=callback.attempt_id,
            callback_token_id=callback.callback_token_id,
        )
    if callback.status in _TERMINAL_EXECUTOR_CALLBACK_STATUSES:
        try:
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


@router.post(
    "/runtime/callbacks/provider-session",
    response_model=ProviderSessionCallbackResponse,
)
async def provider_session_callback(
    callback: ProviderSessionCallbackRequest = Depends(_provider_session_callback_from_request),
    callback_token: str | None = Header(default=None, alias="X-AI-Platform-Callback-Token"),
) -> ProviderSessionCallbackResponse:
    """Broker one exact-run Claude SessionStore operation."""

    _require_valid_callback_token(
        callback_token,
        callback.callback_token_id,
        run_id=callback.run_id,
        attempt_id=callback.attempt_id,
    )
    try:
        async with transaction() as conn:
            run_identity, _lease = await _lock_current_runtime_attempt_then_run(
                conn,
                run_id=callback.run_id,
                attempt_id=callback.attempt_id,
                callback_token_id=callback.callback_token_id,
            )
            result = await context_api.execute_provider_session_callback(
                conn,
                tenant_id=str(run_identity.get("tenant_id") or ""),
                workspace_id=str(run_identity.get("workspace_id") or ""),
                user_id=str(run_identity.get("user_id") or ""),
                session_id=str(run_identity.get("session_id") or ""),
                agent_id=str(run_identity.get("agent_id") or ""),
                run_id=callback.run_id,
                attempt_id=callback.attempt_id,
                provider_session_id=callback.provider_session_id,
                action=callback.action,
                entries=callback.entries,
                subpath=callback.subpath,
                expected_sequence=callback.expected_sequence,
            )
            return ProviderSessionCallbackResponse(
                action=result.action,
                entries=list(result.entries),
                subpaths=list(result.subpaths),
                accepted=result.accepted,
                entry_count=result.entry_count,
                next_sequence=result.next_sequence,
                last_sequence=result.last_sequence,
            )
    except HTTPException:
        raise
    except (context_api.ProviderSessionContinuityError, ValueError) as exc:
        raise _provider_session_http_error(exc) from exc
    except Exception as exc:  # noqa: BLE001 - callback details stay private.
        raise HTTPException(status_code=503, detail="provider_session_callback_failed") from exc


async def _require_current_runtime_attempt(
    conn,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    callback_token_id: str,
) -> dict[str, Any]:
    leases = await sandbox_leases.list_current_sandbox_runtime_leases_for_attempt(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
    )
    if len(leases) != 1:
        raise HTTPException(status_code=409, detail="sandbox_runtime_attempt_inactive")
    lease = leases[0]
    payload = lease.get("lease_payload_json") if isinstance(lease, dict) else None
    persisted_attempt_id = str(lease.get("attempt_id") or "") if isinstance(lease, dict) else ""
    payload_attempt_id = str(payload.get("attempt_id") or "") if isinstance(payload, dict) else ""
    if (persisted_attempt_id or payload_attempt_id) != attempt_id or (
        persisted_attempt_id and payload_attempt_id and persisted_attempt_id != payload_attempt_id
    ):
        raise HTTPException(status_code=409, detail="sandbox_runtime_attempt_mismatch")
    persisted_token_id = str(payload.get("callback_token_id") or "") if isinstance(payload, dict) else ""
    if persisted_token_id and persisted_token_id != callback_token_id:
        raise HTTPException(status_code=409, detail="sandbox_runtime_owner_generation_stale")
    return lease


async def _lock_current_runtime_attempt_then_run(
    conn,
    *,
    run_id: str,
    attempt_id: str,
    callback_token_id: str,
    session_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    run_hint = await runs_postgres.get_run_identity(conn, run_id=run_id, for_update=False)
    if run_hint is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    if session_id is not None and str(run_hint.get("session_id") or "") != session_id:
        raise HTTPException(status_code=409, detail="callback_session_mismatch")
    if str(run_hint.get("status") or "").lower() in TERMINAL_RUN_STATUSES:
        raise HTTPException(status_code=409, detail="run_already_terminal")
    tenant_id = str(run_hint.get("tenant_id") or "")
    locked_run = await runs_postgres.get_run_identity(conn, run_id=run_id, for_update=True)
    if locked_run is None or str(locked_run.get("tenant_id") or "") != tenant_id:
        raise HTTPException(status_code=409, detail="sandbox_runtime_attempt_inactive")
    if session_id is not None and str(locked_run.get("session_id") or "") != session_id:
        raise HTTPException(status_code=409, detail="callback_session_mismatch")
    if str(locked_run.get("status") or "").lower() in TERMINAL_RUN_STATUSES:
        raise HTTPException(status_code=409, detail="run_already_terminal")
    lease = await _require_current_runtime_attempt(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
        callback_token_id=callback_token_id,
    )
    return locked_run, lease


def _require_valid_callback_token(
    provided_token: str | None,
    callback_token_id: str,
    *,
    run_id: str,
    attempt_id: str,
) -> None:
    expected_token = get_settings().sandbox_callback_token
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="callback_token_not_configured",
        )
    if not callback_token_id_matches_attempt(
        callback_token_id, run_id=run_id, attempt_id=attempt_id
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_callback_token",
        )
    if not callback_token_matches(
        secret=expected_token,
        token_id=callback_token_id,
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
    _require_valid_callback_token(
        callback_token,
        callback.callback_token_id,
        run_id=callback.run_id,
        attempt_id=callback.attempt_id,
    )
    runtime = request.app.state.run_stream_runtime
    return await record_executor_callback(
        callback,
        capabilities=runtime.worker_capabilities,
        run_diagnostics=request.app.state.run_diagnostics_service,
    )


async def _profile_drive_file_response(
    request: ExecutorContextRetrievalRequest,
) -> Any:
    arguments = request.arguments
    path = arguments.get("path") if set(arguments) == {"path"} else None
    if (
        not isinstance(path, str)
        or not path
        or len(path) > 1024
        or "\x00" in path
    ):
        raise HTTPException(status_code=422, detail="context_retrieval_parameters_invalid")

    async with transaction() as conn:
        run_identity, lease = await _lock_current_runtime_attempt_then_run(
            conn,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            callback_token_id=request.callback_token_id,
            session_id=request.session_id,
        )
        lease_payload = lease.get("lease_payload_json") if isinstance(lease, dict) else None
        if not isinstance(lease_payload, dict) or lease_payload.get(PROFILE_DRIVE_STAGE_LEASE_FLAG) is not True:
            raise HTTPException(status_code=403, detail="context_retrieval_not_authorized")
        tenant_id = str(run_identity.get("tenant_id") or "")
        user_id = str(run_identity.get("user_id") or "")

    settings = get_settings()
    try:
        jwt = await get_mcp_principal_jwt_store().get(
            SimpleNamespace(tenant_id=tenant_id, user_id=user_id)
        )
    except McpRuntimeContextError as exc:
        raise HTTPException(status_code=409, detail="profile_drive_reauth_required") from exc

    try:
        client, upstream_response, content_length, _content_type = await open_profile_drive_file(
            upstream=str(getattr(settings, "profile_drive_transfer_upstream", "") or ""),
            ca_cert_file=str(
                getattr(settings, "profile_drive_transfer_ca_cert_file", "") or ""
            ),
            jwt=jwt,
            path=path,
            max_bytes=PROFILE_DRIVE_STAGE_MAX_BYTES,
            require_nonempty=False,
            not_found_status=403,
            too_large_detail="profile_drive_file_too_large",
        )
    except ProfileDriveTransferError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    try:
        async with transaction() as conn:
            await _require_current_runtime_attempt(
                conn,
                tenant_id=tenant_id,
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                callback_token_id=request.callback_token_id,
            )
            await streaming_run_events.append_event(
                conn,
                tenant_id=tenant_id,
                run_id=request.run_id,
                event_type="context_retrieved",
                stage="context",
                message="Sandbox context retrieval admitted",
                payload={
                    "action": PROFILE_DRIVE_STAGE_TOOL,
                    "result": "allowed",
                    "visible_to_user": False,
                },
            )
    except Exception:
        await upstream_response.aclose()
        await client.aclose()
        raise

    async def stream_file():
        transferred = 0
        try:
            async for chunk in upstream_response.aiter_raw():
                transferred += len(chunk)
                if transferred > content_length or transferred > PROFILE_DRIVE_STAGE_MAX_BYTES:
                    raise RuntimeError("profile_drive_transfer_size_mismatch")
                yield chunk
            if transferred != content_length:
                raise RuntimeError("profile_drive_transfer_size_mismatch")
        finally:
            await upstream_response.aclose()
            await client.aclose()

    return profile_drive_streaming_response(
        stream_file(),
        content_length=content_length,
    )


@router.post("/runtime/callbacks/context-retrieval")
async def executor_context_retrieval_callback(
    request: ExecutorContextRetrievalRequest,
    callback_token: str | None = Header(default=None, alias="X-AI-Platform-Callback-Token"),
) -> Any:
    """Broker one exact snapshot-authorized retrieval without exposing backend credentials."""

    _require_valid_callback_token(
        callback_token,
        request.callback_token_id,
        run_id=request.run_id,
        attempt_id=request.attempt_id,
    )
    if request.action == PROFILE_DRIVE_STAGE_TOOL:
        return await _profile_drive_file_response(request)
    async with transaction() as conn:
        run_identity, _lease = await _lock_current_runtime_attempt_then_run(
            conn,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            callback_token_id=request.callback_token_id,
            session_id=request.session_id,
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
        identity = {
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
            result = await retrieval.execute(request.action, identity, request.arguments)
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
        await _require_current_runtime_attempt(
            conn,
            tenant_id=tenant_id,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            callback_token_id=request.callback_token_id,
        )
    return {"result": result}
