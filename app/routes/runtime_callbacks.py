from fastapi import APIRouter, Header, HTTPException, status

from app import repositories
from app.db import transaction
from app.executors.claude_agent_worker import resolve_claude_sdk_tool_permission
from app.runtime.sandbox.callback_tokens import callback_token_id_belongs_to_run, callback_token_matches
from app.runtime.sandbox.contracts import ExecutorCallbackEvent, ExecutorToolPermissionRequest
from app.runtime.sandbox.event_normalizer import callback_event_to_run_events
from app.runtime.event_bridge import agent_event_to_executor_event
from app.settings import get_settings
from app.tool_permission_lifecycle import tool_permission_budget

router = APIRouter()


TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled", "canceled"}
_TERMINAL_EXECUTOR_CALLBACK_STATUSES = {"completed", "failed", "cancelled"}


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
        await repositories.append_event(
            conn,
            tenant_id=tenant_id,
            run_id=callback.run_id,
            event_type="executor_callback",
            stage="executor",
            message=f"Executor callback: {callback.status}",
            payload={
                "callback_status": callback.status,
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


async def record_executor_tool_permission(callback: ExecutorToolPermissionRequest) -> dict[str, object]:
    """Validate a sandbox executor tool-permission callback against the owning run."""

    async with transaction() as conn:
        run_identity = await repositories.get_run_identity(conn, run_id=callback.run_id, for_update=True)
        if run_identity is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        if str(run_identity.get("session_id") or "") != callback.session_id:
            raise HTTPException(status_code=409, detail="callback_session_mismatch")
        if str(run_identity.get("status") or "").lower() in TERMINAL_RUN_STATUSES:
            raise HTTPException(status_code=409, detail="run_already_terminal")
        tenant_id = str(run_identity["tenant_id"])
        run = await repositories.get_run(conn, tenant_id=tenant_id, run_id=callback.run_id, for_update=True)
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        scope = {
            "tenant_id": tenant_id,
            "workspace_id": str(run.get("workspace_id") or ""),
            "user_id": str(run.get("user_id") or ""),
            "session_id": str(run.get("session_id") or ""),
            "run_id": callback.run_id,
            "agent_id": str(run.get("agent_id") or ""),
            "skill_id": str(run.get("skill_id") or ""),
            "trace_id": str(run.get("trace_id") or ""),
        }
    return await resolve_claude_sdk_tool_permission(
        **scope,
        request=callback.model_dump(),
        wait_timeout_seconds=(
            callback.permission_wait_seconds
            if callback.permission_wait_seconds is not None
            else tool_permission_budget().aggregate_permission_wait_seconds
        ),
    )


def _require_valid_callback_token(provided_token: str | None, callback_token_id: str, run_id: str) -> None:
    expected_token = get_settings().sandbox_callback_token
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="callback_token_not_configured",
        )
    if not callback_token_id_belongs_to_run(callback_token_id, run_id):
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
    _require_valid_callback_token(callback_token, callback.callback_token_id, callback.run_id)
    return await record_executor_callback(callback)


@router.post("/runtime/callbacks/tool-permission")
async def executor_tool_permission_callback(
    callback: ExecutorToolPermissionRequest,
    callback_token: str | None = Header(default=None, alias="X-AI-Platform-Callback-Token"),
) -> dict[str, object]:
    _require_valid_callback_token(callback_token, callback.callback_token_id, callback.run_id)
    return await record_executor_tool_permission(callback)
