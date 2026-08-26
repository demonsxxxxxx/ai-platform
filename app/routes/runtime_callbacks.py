import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status

from app import repositories
from app.context_manifest import available_context_retrieval_tools
from app.context.retrieval import (
    ContextRetrievalAuthority,
    ContextRetrievalDenied,
    ContextRetrievalInputError,
)
from app.db import transaction
from app.platform.postgres import sandbox_leases as sandbox_lease_repository
from app.runtime.event_bridge import agent_event_to_executor_event
from app.runtime.sandbox.callback_tokens import (
    CallbackTokenBinding,
    callback_token_id_matches_binding,
    callback_token_matches,
)
from app.runtime.sandbox.contracts import (
    ExecutorCallbackEvent,
    ExecutorContextRetrievalRequest,
    executor_callback_receipt_event_count,
)
from app.runtime.sandbox.event_normalizer import callback_event_to_run_events
from app.runtime.sandbox.executor_signals import (
    ExecutorSignalUnavailable,
    publish_executor_terminal_signal,
)
from app.settings import get_settings
from app.streaming.api import (
    V4ProjectionError,
    WorkerV4Capabilities,
    admit_v4_stream,
    append_callback_v4_rows,
    callback_item_to_v4,
    publish_pending_v4_events,
)
from app.streaming.redis import get_stream_authority
from app.storage import ObjectStorage

router = APIRouter()
logger = logging.getLogger(__name__)


TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled", "canceled"}
_TERMINAL_EXECUTOR_CALLBACK_STATUSES = {"completed", "failed", "cancelled"}


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
) -> dict[str, object]:
    """Persist one fenced sandbox observation or terminal result."""

    # Compatibility fields are retained in the private callback receipt only;
    # v4 public rows come from the typed post-bridge event subset.
    callback_for_events = callback.model_copy(update={"new_message": None})
    events = callback_event_to_run_events(callback_for_events)
    v4_items = []
    authority = None
    callback_deduplicated = False
    tenant_id = ""
    lease_id = ""
    async with transaction() as conn:
        run_identity, lease = await _lock_current_runtime_attempt_then_run(
            conn,
            run_id=callback.run_id,
            attempt_id=callback.attempt_id,
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
            executor_event = agent_event_to_executor_event(event)
            item = callback_item_to_v4(
                executor_event,
                callback_index=item_index,
                batch_index=item_index,
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
            receipt = await repositories.append_event_batch(
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
                    await append_callback_v4_rows(
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
                await repositories.append_event(
                    conn,
                    tenant_id=tenant_id,
                    run_id=callback.run_id,
                    **event,
                )
        lease_id = str(lease.get("id") or "") if isinstance(lease, dict) else ""
        if callback.status in _TERMINAL_EXECUTOR_CALLBACK_STATUSES:
            if callback.terminal_result is None:
                raise HTTPException(status_code=422, detail="executor_terminal_result_required")
            if not lease_id:
                raise HTTPException(
                    status_code=503,
                    detail="sandbox_executor_lease_receipt_unavailable",
                )
            try:
                await sandbox_lease_repository.record_sandbox_executor_terminal(
                    conn,
                    tenant_id=tenant_id,
                    run_id=callback.run_id,
                    attempt_id=callback.attempt_id,
                    lease_id=lease_id,
                    executor_status=callback.status,
                    terminal_result=callback.terminal_result.model_dump(
                        mode="json", exclude_none=True
                    ),
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
        elif lease_id:
            heartbeat = await sandbox_lease_repository.record_sandbox_executor_heartbeat(
                conn,
                tenant_id=tenant_id,
                run_id=callback.run_id,
                attempt_id=callback.attempt_id,
                lease_id=lease_id,
                executor_status="running",
                ttl_seconds=get_settings().sandbox_lease_ttl_seconds,
            )
            if heartbeat is None:
                raise HTTPException(
                    status_code=409,
                    detail="sandbox_runtime_attempt_inactive",
                )
        await _require_current_runtime_attempt(
            conn,
            tenant_id=tenant_id,
            run_id=callback.run_id,
            attempt_id=callback.attempt_id,
        )
    if v4_items:
        try:
            if v4_items:
                await admit_v4_stream(
                    capabilities,
                    tenant_id=tenant_id,
                    run_id=callback.run_id,
                    attempt_id=callback.attempt_id,
                )
            await publish_pending_v4_events(
                capabilities,
                tenant_id=tenant_id,
                run_id=callback.run_id,
                attempt_id=callback.attempt_id,
            )
        except Exception:  # noqa: BLE001 - PostgreSQL remains the callback authority.
            logger.warning("callback_v4_publication_deferred", exc_info=True)
    if callback.status in _TERMINAL_EXECUTOR_CALLBACK_STATUSES and lease_id:
        try:
            await publish_executor_terminal_signal()
        except ExecutorSignalUnavailable:
            # PostgreSQL is authoritative; the worker falls back to bounded polling.
            logger.warning("executor_terminal_signal_unavailable")
    return _executor_callback_receipt(
        callback,
        deduplicated=callback_deduplicated,
    )


async def _require_current_runtime_attempt(
    conn,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
) -> dict[str, Any]:
    leases = await repositories.list_current_sandbox_runtime_leases_for_attempt(
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
    return lease


async def _lock_current_runtime_attempt_then_run(
    conn,
    *,
    run_id: str,
    attempt_id: str,
    session_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    run_hint = await repositories.get_run_identity(conn, run_id=run_id, for_update=False)
    if run_hint is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    if str(run_hint.get("session_id") or "") != session_id:
        raise HTTPException(status_code=409, detail="callback_session_mismatch")
    if str(run_hint.get("status") or "").lower() in TERMINAL_RUN_STATUSES:
        raise HTTPException(status_code=409, detail="run_already_terminal")
    tenant_id = str(run_hint.get("tenant_id") or "")
    lease = await _require_current_runtime_attempt(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
    )
    locked_run = await repositories.get_run_identity(conn, run_id=run_id, for_update=True)
    if locked_run is None or str(locked_run.get("tenant_id") or "") != tenant_id:
        raise HTTPException(status_code=409, detail="sandbox_runtime_attempt_inactive")
    if str(locked_run.get("session_id") or "") != session_id:
        raise HTTPException(status_code=409, detail="callback_session_mismatch")
    if str(locked_run.get("status") or "").lower() in TERMINAL_RUN_STATUSES:
        raise HTTPException(status_code=409, detail="run_already_terminal")
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
    binding = CallbackTokenBinding(run_id=run_id, attempt_id=attempt_id)
    if not callback_token_id_matches_binding(callback_token_id, binding):
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
    callback_token: str | None = Header(default=None, alias="X-AI-Platform-Callback-Token"),
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
    )


@router.post("/runtime/callbacks/context-retrieval")
async def executor_context_retrieval_callback(
    request: ExecutorContextRetrievalRequest,
    callback_token: str | None = Header(default=None, alias="X-AI-Platform-Callback-Token"),
) -> dict[str, object]:
    """Broker one exact snapshot-authorized retrieval without exposing backend credentials."""

    _require_valid_callback_token(
        callback_token,
        request.callback_token_id,
        run_id=request.run_id,
        attempt_id=request.attempt_id,
    )
    async with transaction() as conn:
        run_identity, _lease = await _lock_current_runtime_attempt_then_run(
            conn,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            session_id=request.session_id,
        )
        tenant_id = str(run_identity.get("tenant_id") or "")
        workspace_id = str(run_identity.get("workspace_id") or "")
        user_id = str(run_identity.get("user_id") or "")
        agent_id = str(run_identity.get("agent_id") or "")
        snapshot = await repositories.get_bound_executor_context_snapshot(
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
        retrieval = ContextRetrievalAuthority.for_broker_connection(conn, ObjectStorage())
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
        await repositories.append_event(
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
        )
    return {"result": result}


@router.post("/runtime/callbacks/tool-permission")
async def executor_tool_permission_callback(
) -> dict[str, object]:
    """Retired callback endpoint: never deserialize or resolve a permission request."""

    raise HTTPException(status_code=status.HTTP_410_GONE, detail="tool_permission_runtime_approval_removed")
