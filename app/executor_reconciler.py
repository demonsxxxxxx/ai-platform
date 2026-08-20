"""Durable reconciliation for asynchronously dispatched sandbox executor tasks."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from dataclasses import replace

from app import repositories
from app.db import transaction
from app.execution.api import restored_sandbox_run_payload
from app.executors.base import ExecutorResult, RunPayload
from app.executors.registry import AdapterRegistry
from app.platform.postgres import sandbox_leases as sandbox_lease_repository
from app.routes.sandbox_runtime_cleanup import container_lease_from_persisted_row
from app.runtime.sandbox.container_provider import create_container_provider
from app.runtime.sandbox.contracts import SandboxRuntimeRequest
from app.runtime.sandbox.executor_client import SandboxExecutorClient
from app.runtime.sandbox.executor_signals import (
    ExecutorSignalUnavailable,
    publish_executor_terminal_signal,
    wait_for_executor_reconciliation_signal,
)
from app.runtime.sandbox.providers.opensandbox.startup import (
    is_authoritative_not_found_error,
)
from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager
from app.worker import reconcile_executor_terminal_result

_RECONCILIATION_BATCH_SIZE = 8
_RECONCILIATION_CLAIM_STALE_SECONDS = 300
_RECONCILIATION_IDLE_SECONDS = 30.0
_EXECUTOR_HEARTBEAT_STALE_SECONDS = 45
_EXECUTOR_PROBE_FAILURE_LIMIT = 3
_TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled"}
_logger = logging.getLogger(__name__)


def _reconciliation_request(lease_row: dict[str, Any], run_payload: RunPayload) -> SandboxRuntimeRequest:
    return SandboxRuntimeRequest(
        tenant_id=run_payload.tenant_id,
        workspace_id=run_payload.workspace_id,
        user_id=run_payload.user_id,
        session_id=run_payload.session_id,
        run_id=run_payload.run_id,
        attempt_id=run_payload.attempt_id,
        agent_id=run_payload.agent_id,
        skill_ids=[run_payload.skill_id] if run_payload.skill_id else [],
        input_message="",
        system_prompt="",
        file_ids=[],
        sandbox_mode=str(lease_row.get("sandbox_mode") or "ephemeral"),
        browser_enabled=bool(lease_row.get("browser_enabled")),
        model=run_payload.model_value or run_payload.model_id or "reconciliation",
        trace_id=run_payload.trace_id,
        callback_url="http://127.0.0.1/internal/runtime/callback",
        callback_token_id="reconciler",
        require_selected_skill_invocation=False,
    )


def _context_payload(lease_row: dict[str, Any]) -> tuple[dict[str, Any], RunPayload]:
    context = lease_row.get("executor_reconciliation_context_json")
    if not isinstance(context, dict):
        raise ValueError("executor_reconciliation_context_missing")
    adapter_context = context.get("adapter_context")
    if not isinstance(adapter_context, dict):
        raise ValueError("executor_reconciliation_adapter_context_missing")
    run_payload_data = context.get("run_payload")
    if not isinstance(run_payload_data, dict):
        raise ValueError("executor_reconciliation_run_payload_missing")
    terminal_result = lease_row.get("executor_terminal_json")
    result = terminal_result if isinstance(terminal_result, dict) else {}
    return context, restored_sandbox_run_payload(run_payload_data, RunPayload, result)


def _context_and_payload(lease_row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], RunPayload]:
    context, run_payload = _context_payload(lease_row)
    terminal_result = lease_row.get("executor_terminal_json")
    if not isinstance(terminal_result, dict):
        raise ValueError("executor_terminal_result_missing")
    return context, terminal_result, run_payload


async def _collect_workspace_and_convert_result(
    lease_row: dict[str, Any],
    *,
    registry: AdapterRegistry,
) -> tuple[ExecutorResult, Any, Any]:
    context, terminal_result, run_payload = _context_and_payload(lease_row)
    adapter_name = str(context.get("adapter_name") or "").strip()
    adapter = registry.get(adapter_name)
    request = _reconciliation_request(lease_row, run_payload)
    workspace = SandboxWorkspaceManager().prepare(request)
    lease = container_lease_from_persisted_row(lease_row)
    if lease is None:
        raise ValueError("executor_reconciliation_runtime_handle_invalid")
    lease = lease.model_copy(update={"workspace_host_path": workspace.workspace_host_path})
    provider = create_container_provider()
    collection_error: Exception | None = None
    try:
        await provider.collect_workspace(lease, request, workspace)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - converted into a controlled terminal result.
        collection_error = exc
    result = adapter.reconcile_sandbox_terminal(
        payload=run_payload,
        terminal_result=terminal_result,
        adapter_context=context.get("adapter_context") or {},
        provider=str(lease_row.get("provider") or ""),
        timings=context.get("dispatch_timings") or {},
    )
    if collection_error is not None:
        result = replace(
            result,
            status="failed",
            artifacts=[],
            result={
                **result.result,
                "message": "Sandbox workspace collection failed.",
                "error_code": "sandbox_workspace_collection_failed",
            },
        )
    return result, provider, lease


async def _release_reconciled_lease(
    lease_row: dict[str, Any],
    *,
    provider: Any,
    lease: Any,
    claim_token: str,
) -> None:
    stop_result = await provider.stop(lease, reason="executor_reconciled")
    if stop_result.status == "failed":
        raise RuntimeError("executor_reconciliation_sandbox_stop_failed")
    async with transaction() as conn:
        released = await repositories.release_sandbox_lease(
            conn,
            tenant_id=str(lease_row["tenant_id"]),
            user_id=str(lease_row["user_id"]),
            run_id=str(lease_row["run_id"]),
            lease_id=str(lease_row["id"]),
            reason="executor_reconciled",
        )
        if not released and str(lease_row.get("status") or "") == "active":
            raise RuntimeError("executor_reconciliation_lease_release_lost")
        finalized = await sandbox_lease_repository.finalize_sandbox_executor_reconciliation(
            conn,
            lease_id=str(lease_row["id"]),
            claim_token=claim_token,
        )
        if not finalized:
            raise RuntimeError("executor_reconciliation_claim_lost")


async def _persist_probe_terminal(
    lease_row: dict[str, Any],
    *,
    executor_status: str,
    terminal_result: dict[str, Any],
) -> None:
    async with transaction() as conn:
        await sandbox_lease_repository.record_sandbox_executor_terminal(
            conn,
            tenant_id=str(lease_row["tenant_id"]),
            run_id=str(lease_row["run_id"]),
            attempt_id=str(lease_row["attempt_id"]),
            lease_id=str(lease_row["id"]),
            executor_status=executor_status,
            terminal_result=terminal_result,
        )
    try:
        await publish_executor_terminal_signal()
    except ExecutorSignalUnavailable:
        pass


async def probe_suspect_executor_tasks_once(
    *,
    limit: int = _RECONCILIATION_BATCH_SIZE,
) -> int:
    claim_token = uuid.uuid4().hex
    async with transaction() as conn:
        claimed = await sandbox_lease_repository.claim_sandbox_executor_suspects(
            conn,
            claim_token=claim_token,
            stale_after_seconds=_EXECUTOR_HEARTBEAT_STALE_SECONDS,
            limit=limit,
        )
    for lease_row in claimed:
        lease_id = str(lease_row["id"])
        try:
            _context, run_payload = _context_payload(lease_row)
            request = _reconciliation_request(lease_row, run_payload)
            lease = container_lease_from_persisted_row(lease_row)
            if lease is None:
                raise ValueError("executor_reconciliation_runtime_handle_invalid")
            provider = create_container_provider()
            executor_url, executor_headers = await provider.executor_control_endpoint(lease, request)
            client = SandboxExecutorClient(timeout_seconds=10.0)
            async with transaction() as conn:
                run = await repositories.get_run(
                    conn,
                    tenant_id=str(lease_row["tenant_id"]),
                    run_id=str(lease_row["run_id"]),
                )
            if run is not None and str(run.get("status") or "") == "cancelled":
                await client.cancel(
                    executor_url,
                    run_id=str(lease_row["run_id"]),
                    attempt_id=str(lease_row["attempt_id"]),
                    executor_headers=executor_headers,
                )
            status = await client.get_status(
                executor_url,
                run_id=str(lease_row["run_id"]),
                attempt_id=str(lease_row["attempt_id"]),
                executor_headers=executor_headers,
            )
            terminal_result = status.get("terminal_result")
            if isinstance(terminal_result, dict):
                result_status = str(terminal_result.get("status") or "").strip().lower()
                executor_status = "completed" if result_status == "succeeded" else "failed"
                if str(status.get("status") or "").strip().lower() == "cancelled":
                    executor_status = "cancelled"
                await _persist_probe_terminal(
                    lease_row,
                    executor_status=executor_status,
                    terminal_result=terminal_result,
                )
                continue
            async with transaction() as conn:
                await sandbox_lease_repository.release_sandbox_executor_probe_claim(
                    conn,
                    lease_id=lease_id,
                    claim_token=claim_token,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - persisted retry before eventual terminalization.
            attempts = int(lease_row.get("executor_reconciliation_attempt_count") or 0)
            if is_authoritative_not_found_error(exc) or attempts >= _EXECUTOR_PROBE_FAILURE_LIMIT:
                await _persist_probe_terminal(
                    lease_row,
                    executor_status="failed",
                    terminal_result={
                        "run_id": str(lease_row["run_id"]),
                        "status": "failed",
                        "error_code": "sandbox_executor_lost",
                        "error_message": "Sandbox executor stopped responding",
                    },
                )
            else:
                async with transaction() as conn:
                    await sandbox_lease_repository.release_sandbox_executor_probe_claim(
                        conn,
                        lease_id=lease_id,
                        claim_token=claim_token,
                        error=str(exc),
                    )
    return len(claimed)


async def reconcile_pending_executor_terminals_once(
    *,
    registry: AdapterRegistry | None = None,
    worker_id: str | None = None,
    limit: int = _RECONCILIATION_BATCH_SIZE,
) -> int:
    claim_token = uuid.uuid4().hex
    async with transaction() as conn:
        claimed = await sandbox_lease_repository.claim_sandbox_executor_reconciliations(
            conn,
            claim_token=claim_token,
            stale_after_seconds=_RECONCILIATION_CLAIM_STALE_SECONDS,
            limit=limit,
        )
    if not claimed:
        return 0
    adapter_registry = registry or AdapterRegistry()
    for lease_row in claimed:
        try:
            async with transaction() as conn:
                run = await repositories.get_run(
                    conn,
                    tenant_id=str(lease_row["tenant_id"]),
                    run_id=str(lease_row["run_id"]),
                    for_update=True,
                )
            if run is None:
                raise ValueError("executor_reconciliation_run_missing")
            if str(run.get("status") or "") in _TERMINAL_RUN_STATUSES:
                context, _terminal_result, run_payload = _context_and_payload(lease_row)
                request = _reconciliation_request(lease_row, run_payload)
                workspace = SandboxWorkspaceManager().prepare(request)
                lease = container_lease_from_persisted_row(lease_row)
                if lease is None:
                    raise ValueError("executor_reconciliation_runtime_handle_invalid")
                lease = lease.model_copy(update={"workspace_host_path": workspace.workspace_host_path})
                provider = create_container_provider()
            else:
                result, provider, lease = await _collect_workspace_and_convert_result(
                    lease_row,
                    registry=adapter_registry,
                )
                await reconcile_executor_terminal_result(
                    lease_row=lease_row,
                    result=result,
                    registry=adapter_registry,
                    worker_id=worker_id,
                    claim_token=claim_token,
                )
            await _release_reconciled_lease(
                lease_row,
                provider=provider,
                lease=lease,
                claim_token=claim_token,
            )
        except asyncio.CancelledError:
            async with transaction() as conn:
                await sandbox_lease_repository.retry_sandbox_executor_reconciliation(
                    conn,
                    lease_id=str(lease_row["id"]),
                    claim_token=claim_token,
                    error="reconciler_cancelled",
                )
            raise
        except Exception as exc:  # noqa: BLE001 - durable retry boundary.
            _logger.exception(
                "executor_terminal_reconciliation_failed",
                extra={
                    "lease_id": str(lease_row["id"]),
                    "run_id": str(lease_row["run_id"]),
                    "error_type": type(exc).__name__,
                },
            )
            async with transaction() as conn:
                await sandbox_lease_repository.retry_sandbox_executor_reconciliation(
                    conn,
                    lease_id=str(lease_row["id"]),
                    claim_token=claim_token,
                    error=type(exc).__name__,
                )
    return len(claimed)


async def run_executor_terminal_reconciler(
    stop_event: asyncio.Event,
    *,
    registry: AdapterRegistry | None = None,
    worker_id: str | None = None,
) -> None:
    while not stop_event.is_set():
        try:
            await reconcile_pending_executor_terminals_once(
                registry=registry,
                worker_id=worker_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one failed scan must not stop recovery.
            _logger.exception("executor_terminal_reconciliation_scan_failed")
        if stop_event.is_set():
            break
        try:
            await probe_suspect_executor_tasks_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - polling failures must not stop terminal recovery.
            _logger.exception("executor_suspect_probe_failed")
        try:
            await asyncio.wait_for(
                wait_for_executor_reconciliation_signal(
                    block_ms=int(_RECONCILIATION_IDLE_SECONDS * 1000),
                ),
                timeout=_RECONCILIATION_IDLE_SECONDS + 5.0,
            )
        except ExecutorSignalUnavailable:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_RECONCILIATION_IDLE_SECONDS)
            except TimeoutError:
                pass
        except TimeoutError:
            pass
