"""Compose durable immediate Sandbox executor cancellation delivery."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, Callable

from app.db import transaction
from app.execution.api import restored_sandbox_run_payload
from app.executors.base import RunPayload
from app.bootstrap.sandbox_cleanup import build_sandbox_runtime_cleanup
from app.runtime.sandbox.container_provider import create_container_provider
from app.runtime.sandbox.contracts import SandboxRuntimeRequest
from app.runtime.sandbox.executor_client import SandboxExecutorClient

if TYPE_CHECKING:
    from app.sandbox.api import SandboxCancelDeliveryService


def _persisted_cancel_request(lease_row: dict[str, Any]) -> SandboxRuntimeRequest:
    context = lease_row.get("executor_reconciliation_context_json")
    if not isinstance(context, dict):
        raise ValueError("sandbox_cancel_context_missing")
    payload_value = context.get("run_payload")
    if not isinstance(payload_value, dict):
        raise ValueError("sandbox_cancel_run_payload_missing")

    execution_payload = payload_value.get("execution_payload", payload_value)
    lease_payload = lease_row.get("lease_payload_json")
    lease_payload = lease_payload if isinstance(lease_payload, dict) else {}
    lease_generation = lease_payload.get("owner_generation")
    if "owner_generation" in lease_payload and (
        type(lease_generation) is not int
        or lease_generation < 1
        or lease_payload.get("attempt_id") != lease_row.get("attempt_id")
    ):
        raise ValueError("sandbox_cancel_owner_generation_mismatch")

    try:
        run_payload = restored_sandbox_run_payload(payload_value, RunPayload, {})
    except (KeyError, TypeError, ValueError):
        raise ValueError("sandbox_cancel_run_payload_invalid") from None
    if lease_generation is not None:
        if isinstance(execution_payload, dict) and "owner_generation" not in execution_payload:
            run_payload = replace(run_payload, owner_generation=lease_generation)
        if run_payload.owner_generation != lease_generation:
            raise ValueError("sandbox_cancel_owner_generation_mismatch")

    for field_name in (
        "tenant_id",
        "workspace_id",
        "user_id",
        "session_id",
        "run_id",
        "attempt_id",
    ):
        if getattr(run_payload, field_name) != lease_row.get(field_name):
            raise ValueError("sandbox_cancel_identity_mismatch")

    raw_subjects = run_payload.input.get("_runtime_tool_policy_subjects")
    tool_policy_subjects = [
        {key: value for key, value in subject.items() if key != "mcp_server_config"}
        for subject in raw_subjects
        if isinstance(subject, dict)
    ] if isinstance(raw_subjects, list) else []
    return SandboxRuntimeRequest(
        tenant_id=run_payload.tenant_id,
        workspace_id=run_payload.workspace_id,
        user_id=run_payload.user_id,
        session_id=run_payload.session_id,
        run_id=run_payload.run_id,
        attempt_id=run_payload.attempt_id,
        owner_generation=run_payload.owner_generation,
        agent_id=run_payload.agent_id,
        skill_ids=[run_payload.skill_id] if run_payload.skill_id else [],
        tool_policy_subjects=tool_policy_subjects,
        input_message="",
        system_prompt="",
        file_ids=[],
        sandbox_mode=str(lease_row.get("sandbox_mode") or "ephemeral"),
        browser_enabled=bool(lease_row.get("browser_enabled")),
        model=run_payload.model_value or run_payload.model_id or "reconciliation",
        trace_id=run_payload.trace_id,
        callback_url="http://127.0.0.1/internal/runtime/callback",
        callback_token_id="reconciler",
    )


async def _send_cancel(
    claimed_row: dict[str, Any],
    *,
    sandbox_cleanup: Any,
    provider_factory: Callable[[str | None], Any],
    client_factory: Callable[..., SandboxExecutorClient] | None = None,
) -> dict[str, Any]:
    request = _persisted_cancel_request(claimed_row)
    lease = sandbox_cleanup.container_lease_from_persisted_row(claimed_row)
    if lease is None:
        raise ValueError("sandbox_cancel_runtime_handle_invalid")

    provider = provider_factory(lease.provider)
    executor_url, executor_headers = await provider.executor_control_endpoint(lease, request)
    client = (client_factory or SandboxExecutorClient)(timeout_seconds=5.0)
    acknowledgement = await client.cancel(
        executor_url,
        run_id=request.run_id,
        attempt_id=request.attempt_id,
        executor_headers=executor_headers,
    )
    if acknowledgement != {
        "status": "cancel_requested",
        "run_id": request.run_id,
        "attempt_id": request.attempt_id,
    }:
        raise ValueError("sandbox_cancel_ack_invalid")
    return acknowledgement


def build_sandbox_cancel_delivery_service() -> "SandboxCancelDeliveryService":
    """Build durable staging and authenticated, claim-fenced executor delivery."""

    from app.sandbox.api import SandboxCancelDeliveryService
    from app.sandbox.infrastructure import cancel_delivery_postgres

    sandbox_cleanup = build_sandbox_runtime_cleanup()

    async def send_cancel(claimed_row: dict[str, Any]) -> dict[str, Any]:
        return await _send_cancel(
            claimed_row,
            sandbox_cleanup=sandbox_cleanup,
            provider_factory=create_container_provider,
        )

    return SandboxCancelDeliveryService(
        repository=cancel_delivery_postgres,
        transaction_factory=transaction,
        send_cancel=send_cancel,
    )
