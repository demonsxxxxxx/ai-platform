import base64
from typing import Any

from fastapi import APIRouter, Header, HTTPException, status

from app import repositories
from app.context_manifest import available_context_retrieval_tools
from app.context_retrieval import (
    ContextRetrieval,
    ContextRetrievalDenied,
    RepositoryContextRetrievalRepository,
)
from app.db import transaction
from app.runtime.sandbox.callback_tokens import (
    CallbackTokenBinding,
    callback_token_id_matches_binding,
    callback_token_matches,
)
from app.runtime.sandbox.contracts import ExecutorCallbackEvent, ExecutorContextRetrievalRequest
from app.runtime.sandbox.event_normalizer import callback_event_to_run_events
from app.runtime.event_bridge import agent_event_to_executor_event
from app.settings import get_settings
from app.storage import ObjectStorage

router = APIRouter()


TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled", "canceled"}
_TERMINAL_EXECUTOR_CALLBACK_STATUSES = {"completed", "failed", "cancelled"}
_CONTEXT_ACTION_ARGUMENTS = {
    "read_session_messages": {"limit", "offset", "max_tokens"},
    "read_context_file": {"file_id", "max_bytes"},
    "read_run_artifact": {"artifact_id", "max_bytes"},
    "stage_context_file_to_workspace": {"file_id", "max_bytes"},
    "stage_run_artifact_to_workspace": {"artifact_id", "max_bytes"},
    "search_memory": {"query", "limit", "max_tokens"},
}


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = default
    return max(minimum, min(maximum, normalized))


def _context_arguments(request: ExecutorContextRetrievalRequest) -> dict[str, Any]:
    arguments = dict(request.arguments)
    allowed = _CONTEXT_ACTION_ARGUMENTS[request.action]
    if set(arguments) - allowed:
        raise HTTPException(status_code=422, detail="context_retrieval_parameters_invalid")
    required_key = {
        "read_context_file": "file_id",
        "read_run_artifact": "artifact_id",
        "stage_context_file_to_workspace": "file_id",
        "stage_run_artifact_to_workspace": "artifact_id",
    }.get(request.action)
    if required_key and not str(arguments.get(required_key) or "").strip():
        raise HTTPException(status_code=422, detail=f"{required_key}_required")
    return arguments


async def _run_context_retrieval_action(
    retrieval: ContextRetrieval,
    *,
    action: str,
    arguments: dict[str, Any],
    identity: dict[str, str],
) -> dict[str, Any]:
    scoped_identity = {
        key: identity[key]
        for key in ("tenant_id", "workspace_id", "user_id", "session_id", "run_id")
    }
    if action == "read_session_messages":
        return await retrieval.read_session_messages(
            **scoped_identity,
            limit=_bounded_int(arguments.get("limit"), default=20, minimum=1, maximum=100),
            offset=_bounded_int(arguments.get("offset"), default=0, minimum=0, maximum=10000),
            max_tokens=_bounded_int(arguments.get("max_tokens"), default=1200, minimum=1, maximum=8000),
        )
    if action == "read_context_file":
        return await retrieval.read_context_file(
            **scoped_identity,
            file_id=str(arguments["file_id"]),
            max_bytes=_bounded_int(arguments.get("max_bytes"), default=65536, minimum=1, maximum=262144),
        )
    if action == "read_run_artifact":
        return await retrieval.read_run_artifact(
            **scoped_identity,
            artifact_id=str(arguments["artifact_id"]),
            max_bytes=_bounded_int(arguments.get("max_bytes"), default=65536, minimum=1, maximum=262144),
        )
    if action == "stage_context_file_to_workspace":
        exported = await retrieval.export_context_file_for_broker(
            **scoped_identity,
            file_id=str(arguments["file_id"]),
            max_bytes=_bounded_int(arguments.get("max_bytes"), default=1048576, minimum=1, maximum=1048576),
        )
    elif action == "stage_run_artifact_to_workspace":
        exported = await retrieval.export_run_artifact_for_broker(
            **scoped_identity,
            artifact_id=str(arguments["artifact_id"]),
            max_bytes=_bounded_int(arguments.get("max_bytes"), default=16777216, minimum=1, maximum=16777216),
        )
    else:
        return await retrieval.search_memory(
            tenant_id=identity["tenant_id"],
            workspace_id=identity["workspace_id"],
            user_id=identity["user_id"],
            agent_id=identity["agent_id"],
            session_id=identity["session_id"],
            query=str(arguments.get("query") or ""),
            limit=_bounded_int(arguments.get("limit"), default=10, minimum=1, maximum=50),
            max_tokens=_bounded_int(arguments.get("max_tokens"), default=1200, minimum=1, maximum=8000),
        )
    raw_bytes = bytes(exported.pop("content_bytes"))
    return {
        **exported,
        "content_base64": base64.b64encode(raw_bytes).decode("ascii"),
        "redaction": {"object_locator_refs_removed": True},
    }


async def record_executor_callback(callback: ExecutorCallbackEvent) -> dict[str, object]:
    """Persist only non-terminal sandbox observations; worker owns run terminal facts."""

    if callback.status in _TERMINAL_EXECUTOR_CALLBACK_STATUSES:
        raise HTTPException(status_code=409, detail="executor_terminal_callback_not_allowed")
    events = callback_event_to_run_events(callback)
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
        await repositories.append_event(
            conn,
            tenant_id=tenant_id,
            run_id=callback.run_id,
            event_type="executor_callback",
            stage="executor",
            message=f"Executor callback: {callback.status}",
            payload={
                "callback_status": callback.status,
                "attempt_id": callback.attempt_id,
                "callback_token_id": callback.callback_token_id,
                "progress": callback.progress,
                "sdk_session_id": callback.sdk_session_id,
                "visible_to_user": False,
            },
        )
        for event in events:
            executor_event = agent_event_to_executor_event(event)
            executor_payload = dict(executor_event["payload"])
            executor_payload["source"] = "executor_callback"
            await repositories.append_event(
                conn,
                tenant_id=tenant_id,
                run_id=callback.run_id,
                event_type=str(executor_event["event_type"]),
                stage=str(executor_event["stage"]),
                message=str(executor_event["message"]),
                payload=executor_payload,
            )
    return {"accepted": True, "event_count": 1 + len(events)}


async def _require_current_runtime_attempt(
    conn,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
) -> dict[str, Any]:
    leases = await repositories.list_current_sandbox_runtime_leases_for_run(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    if len(leases) != 1:
        raise HTTPException(status_code=409, detail="sandbox_runtime_attempt_inactive")
    lease = leases[0]
    payload = lease.get("lease_payload_json") if isinstance(lease, dict) else None
    if not isinstance(payload, dict) or str(payload.get("attempt_id") or "") != attempt_id:
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
    arguments = _context_arguments(request)
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
        retrieval = ContextRetrieval(
            RepositoryContextRetrievalRepository(conn, storage=ObjectStorage())
        )
        try:
            result = await _run_context_retrieval_action(
                retrieval,
                action=request.action,
                arguments=arguments,
                identity=identity,
            )
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
    return {"result": result}


@router.post("/runtime/callbacks/tool-permission")
async def executor_tool_permission_callback(
) -> dict[str, object]:
    """Retired callback endpoint: never deserialize or resolve a permission request."""

    raise HTTPException(status_code=status.HTTP_410_GONE, detail="tool_permission_runtime_approval_removed")
