"""Bind the Runs Worker dispatch transaction to Context, MCP and Execution."""

from typing import Any, Callable

from app.context_builder import executor_context_pack_from_snapshot
from app.executors.base import project_execution_spec_to_run_payload
from app.execution.api import (
    create_worker_runtime_sandbox_lease,
    release_worker_runtime_sandbox_lease,
)
from app.platform.postgres import sandbox_leases as sandbox_lease_repository
from app.mcp import api as mcp_api
from app.platform.postgres import errors as platform_errors
from app.runs import api as runs_api
from app.settings import get_settings
from app.streaming.api import append_worker_user_event
from app.streaming.infrastructure import run_events_postgres


async def _append_user_event(conn: Any, **kwargs: Any) -> None:
    await append_worker_user_event(
        conn, append_event=run_events_postgres.append_event, **kwargs
    )


def _sdk_import_status() -> str:
    try:
        import claude_agent_sdk  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - optional SDK imports may fail arbitrarily.
        return f"unavailable:{exc.__class__.__name__}"
    return "ok"


def _worker_runtime_evidence(*, worker_id: str | None, executor_type: str) -> dict[str, Any]:
    settings = get_settings()
    return {
        "worker_id": worker_id,
        "executor_type": executor_type,
        "claude_agent_sdk_enabled": bool(settings.claude_agent_sdk_enabled),
        "claude_agent_model": settings.claude_agent_model,
        "claude_agent_sdk_import": _sdk_import_status(),
    }


async def create_worker_runtime_lease(conn: Any, **kwargs: Any) -> Any:
    return await create_worker_runtime_sandbox_lease(
        conn, **kwargs, ttl_seconds=get_settings().sandbox_lease_ttl_seconds,
        create_lease=sandbox_lease_repository.create_sandbox_lease,
    )


async def release_worker_runtime_lease(conn: Any, lease: Any, *, reason: str) -> None:
    await release_worker_runtime_sandbox_lease(
        conn, lease, reason=reason,
        release_lease=sandbox_lease_repository.release_sandbox_lease,
    )


def build_worker_dispatch_binding_service(
    transaction_factory: Any,
    *,
    context_projector: Callable[..., dict[str, Any]],
    materialize_context: Callable[..., Any],
    compile_spec: Callable[..., Any],
    uses_runtime_sandbox: Callable[..., bool],
    create_runtime_lease: Callable[..., Any],
    fail_pre_dispatch: Callable[..., Any],
) -> runs_api.WorkerDispatchBindingService:
    return runs_api.WorkerDispatchBindingService(
        transaction_factory=transaction_factory,
        dispatch_fence=runs_api.worker_dispatch_fence,
        compile_spec=compile_spec,
        context=runs_api.WorkerDispatchContextPorts(
            materialize=materialize_context,
            project=context_projector,
            execution_pack=executor_context_pack_from_snapshot,
        ),
        execution=runs_api.WorkerDispatchExecutionPorts(
            project_spec=project_execution_spec_to_run_payload,
            attach_mcp=mcp_api.attach_mcp_server_configs,
            mcp_runtime_error=mcp_api.McpRuntimeContextError,
            append_user_event=_append_user_event,
            runtime_evidence=_worker_runtime_evidence,
            uses_runtime_sandbox=uses_runtime_sandbox,
            create_runtime_lease=create_runtime_lease,
            missing_attempt_error=platform_errors.RepositoryConflictError,
        ),
        fail_pre_dispatch=fail_pre_dispatch,
    )
