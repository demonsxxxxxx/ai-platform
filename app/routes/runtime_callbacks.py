from typing import Any

from fastapi import APIRouter, Header, HTTPException, status

from app import repositories
from app.context_manifest import available_context_retrieval_tools
from app.context.retrieval import (
    ContextRetrievalAuthority,
    ContextRetrievalDenied,
    ContextRetrievalInputError,
)
from app.db import transaction
from app.public_execution import (
    PUBLIC_AGENT_PROGRESS_EVENT_TYPE,
    PUBLIC_EXECUTION_EVENT_TYPES,
)
from app.runtime.event_bridge import agent_event_to_executor_event
from app.runtime.sandbox.callback_tokens import (
    CallbackTokenBinding,
    callback_token_id_matches_binding,
    callback_token_matches,
)
from app.runtime.sandbox.contracts import (
    ExecutorCallbackEvent,
    ExecutorContextRetrievalRequest,
)
from app.runtime.sandbox.event_normalizer import callback_event_to_run_events
from app.settings import get_settings
from app.storage import ObjectStorage
from app.worker import _canonical_assistant_delta_event as canonical_assistant_delta_event

router = APIRouter()


TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled", "canceled"}
_TERMINAL_EXECUTOR_CALLBACK_STATUSES = {"completed", "failed", "cancelled"}
async def record_executor_callback(callback: ExecutorCallbackEvent) -> dict[str, object]:
    """Persist only non-terminal sandbox observations; worker owns run terminal facts."""

    if callback.status in _TERMINAL_EXECUTOR_CALLBACK_STATUSES:
        raise HTTPException(status_code=409, detail="executor_terminal_callback_not_allowed")
    callback_for_events = callback
    if callback.status == "running" and callback.new_message is not None:
        raw_delta = (
            callback.new_message["delta"]
            if "delta" in callback.new_message
            else callback.new_message.get("text")
        )
        if canonical_assistant_delta_event(stage="message", payload={"delta": raw_delta}) is None:
            callback_for_events = callback.model_copy(update={"new_message": None})
    events = callback_event_to_run_events(callback_for_events)
    async with transaction() as conn:
        run_identity = await repositories.get_run_identity(conn, run_id=callback.run_id, for_update=True)
        if run_identity is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        if str(run_identity.get("session_id") or "") != callback.session_id:
            raise HTTPException(status_code=409, detail="callback_session_mismatch")
        if str(run_identity.get("status") or "").lower() in TERMINAL_RUN_STATUSES:
            raise HTTPException(status_code=409, detail="run_already_terminal")
        tenant_id = str(run_identity["tenant_id"])
        await _require_current_runtime_attempt(
            conn,
            tenant_id=tenant_id,
            run_id=callback.run_id,
            attempt_id=callback.attempt_id,
        )
        event_batch: list[dict[str, Any]] = [
            {
                "event_type": "executor_callback",
                "stage": "executor",
                "message": f"Executor callback: {callback.status}",
                "payload": {
                    "callback_status": callback.status,
                    "attempt_id": callback.attempt_id,
                    "batch_id": callback.batch_id,
                    "callback_token_id": callback.callback_token_id,
                    "progress": callback.progress,
                    "sdk_session_id": callback.sdk_session_id,
                    "visible_to_user": False,
                },
            }
        ]
        for event in events:
            executor_event = agent_event_to_executor_event(event)
            executor_event_type = str(executor_event["event_type"])
            executor_payload = dict(executor_event["payload"])
            if executor_event_type == "assistant_delta":
                canonical_delta = canonical_assistant_delta_event(
                    stage=str(executor_event["stage"]),
                    payload=executor_payload,
                )
                if canonical_delta is None:
                    continue
                event_stage, event_message, event_payload = canonical_delta
            else:
                event_stage = str(executor_event["stage"])
                event_message = str(executor_event["message"])
                event_payload = executor_payload
            if (
                executor_event_type not in PUBLIC_EXECUTION_EVENT_TYPES
                and executor_event_type
                not in {"assistant_delta", PUBLIC_AGENT_PROGRESS_EVENT_TYPE}
            ):
                event_payload["source"] = "executor_callback"
            event_batch.append(
                {
                    "event_type": executor_event_type,
                    "stage": event_stage,
                    "message": event_message,
                    "payload": event_payload,
                }
            )
        if callback.batch_id:
            await repositories.append_event_batch(
                conn,
                tenant_id=tenant_id,
                run_id=callback.run_id,
                attempt_id=callback.attempt_id,
                batch_id=callback.batch_id,
                events=event_batch,
            )
        else:
            for event in event_batch:
                await repositories.append_event(
                    conn,
                    tenant_id=tenant_id,
                    run_id=callback.run_id,
                    **event,
                )
        await _require_current_runtime_attempt(
            conn,
            tenant_id=tenant_id,
            run_id=callback.run_id,
            attempt_id=callback.attempt_id,
        )
    return {"accepted": True, "event_count": len(event_batch)}


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
    callback: ExecutorCallbackEvent,
    callback_token: str | None = Header(default=None, alias="X-AI-Platform-Callback-Token"),
) -> dict[str, object]:
    _require_valid_callback_token(
        callback_token,
        callback.callback_token_id,
        run_id=callback.run_id,
        attempt_id=callback.attempt_id,
    )
    return await record_executor_callback(callback)


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
        run_identity = await repositories.get_run_identity(conn, run_id=request.run_id, for_update=True)
        if run_identity is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        if str(run_identity.get("session_id") or "") != request.session_id:
            raise HTTPException(status_code=409, detail="callback_session_mismatch")
        if str(run_identity.get("status") or "").lower() in TERMINAL_RUN_STATUSES:
            raise HTTPException(status_code=409, detail="run_already_terminal")
        tenant_id = str(run_identity.get("tenant_id") or "")
        workspace_id = str(run_identity.get("workspace_id") or "")
        user_id = str(run_identity.get("user_id") or "")
        agent_id = str(run_identity.get("agent_id") or "")
        await _require_current_runtime_attempt(
            conn,
            tenant_id=tenant_id,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
        )
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
