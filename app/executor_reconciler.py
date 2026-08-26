"""Durable reconciliation for asynchronously dispatched sandbox executor tasks."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import replace
from typing import Any

from app import repositories
from app.db import transaction
from app.execution.api import restored_sandbox_run_payload
from app.executors.base import ExecutorResult, RunPayload
from app.executors.registry import AdapterRegistry
from app.platform.postgres import sandbox_leases as sandbox_lease_repository
from app.routes.sandbox_runtime_cleanup import (
    cleanup_failed_sandbox_executor_reconciliation_leases,
    container_lease_from_persisted_row,
)
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
from app.settings import get_settings
from app.tool_permission_lifecycle import (
    drain_run_tool_permission_terminalization,
    fail_run_with_v4,
    reconcile_terminalized_permission_run,
)
from app.worker import (
    WorkerV4Capabilities,
    publish_pending_run_terminal,
    reconcile_executor_terminal_result,
)

_RECONCILIATION_BATCH_SIZE = 1
_RECONCILIATION_CLAIM_STALE_SECONDS = 300
_RECONCILIATION_IDLE_SECONDS = 30.0
_EXECUTOR_HEARTBEAT_STALE_SECONDS = 45
_EXECUTOR_PROBE_FAILURE_LIMIT = 3
_RECONCILIATION_FAILURE_LIMIT = 5
_TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled"}
_logger = logging.getLogger(__name__)


class PermanentExecutorReconciliationError(ValueError):
    """A persisted terminal receipt cannot become valid through retry."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SandboxReconciliationStopError(RuntimeError):
    """A verified runtime remains eligible for claim-fenced cleanup after stop failure."""


def _sandbox_cleanup_timeout_seconds() -> float:
    return max(
        float(getattr(get_settings(), "sandbox_cleanup_timeout_seconds", 30) or 30),
        0.001,
    )


def _consume_stopped_task(task: asyncio.Task[Any]) -> None:
    try:
        task.exception()
    except BaseException:
        pass


async def _stop_reconciled_provider(provider: Any, lease: Any) -> Any:
    stop_task = asyncio.create_task(provider.stop(lease, reason="executor_reconciled"))
    try:
        return await asyncio.wait_for(
            asyncio.shield(stop_task),
            timeout=_sandbox_cleanup_timeout_seconds(),
        )
    except asyncio.CancelledError:
        stop_task.cancel()
        stop_task.add_done_callback(_consume_stopped_task)
        raise
    except TimeoutError as exc:
        stop_task.cancel()
        stop_task.add_done_callback(_consume_stopped_task)
        raise SandboxReconciliationStopError(
            "executor_reconciliation_sandbox_stop_failed"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - provider errors become durable cleanup retry.
        raise SandboxReconciliationStopError(
            "executor_reconciliation_sandbox_stop_failed"
        ) from exc


async def _release_reconciled_lease(
    lease_row: dict[str, Any],
    *,
    provider: Any,
    lease: Any,
    claim_token: str,
    transaction_factory: Any,
) -> None:
    async with transaction_factory() as conn:
        claimed = await sandbox_lease_repository.has_sandbox_executor_reconciliation_claim(
            conn,
            lease_id=str(lease_row["id"]),
            claim_token=claim_token,
        )
    if not claimed:
        raise RuntimeError("executor_reconciliation_claim_lost")
    stop_result = await _stop_reconciled_provider(provider, lease)
    if getattr(stop_result, "status", "failed") not in {"stopped", "not_found"}:
        raise SandboxReconciliationStopError(
            "executor_reconciliation_sandbox_stop_failed"
        )
    async with transaction_factory() as conn:
        finalized = await sandbox_lease_repository.release_and_finalize_sandbox_executor_reconciliation(
            conn,
            tenant_id=str(lease_row["tenant_id"]),
            user_id=str(lease_row["user_id"]),
            run_id=str(lease_row["run_id"]),
            lease_id=str(lease_row["id"]),
            claim_token=claim_token,
            reason="executor_reconciled",
        )
    if not finalized:
        raise RuntimeError("executor_reconciliation_claim_lost")


def _permanent_reconciliation_error(
    code: str,
) -> PermanentExecutorReconciliationError:
    return PermanentExecutorReconciliationError(code)


def _reconciliation_error_code(exc: Exception) -> str:
    if isinstance(exc, PermanentExecutorReconciliationError):
        return exc.code
    return type(exc).__name__


def _reconciliation_failure_is_terminal(
    lease_row: dict[str, Any],
    exc: Exception,
) -> bool:
    attempts = int(lease_row.get("executor_terminal_reconciliation_attempt_count") or 0)
    return (
        isinstance(exc, PermanentExecutorReconciliationError)
        or attempts >= _RECONCILIATION_FAILURE_LIMIT
    )


def _context_payload(
    lease_row: dict[str, Any],
    terminal_result: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], RunPayload]:
    context = lease_row.get("executor_reconciliation_context_json")
    if not isinstance(context, dict):
        raise _permanent_reconciliation_error("executor_reconciliation_context_missing")
    adapter_context = context.get("adapter_context")
    if not isinstance(adapter_context, dict):
        raise _permanent_reconciliation_error(
            "executor_reconciliation_adapter_context_missing"
        )
    run_payload_data = context.get("run_payload")
    if not isinstance(run_payload_data, dict):
        raise _permanent_reconciliation_error(
            "executor_reconciliation_run_payload_missing"
        )
    try:
        run_payload = restored_sandbox_run_payload(
            run_payload_data,
            RunPayload,
            terminal_result if terminal_result is not None else {},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _permanent_reconciliation_error(
            "executor_reconciliation_run_payload_invalid"
        ) from exc
    for field in (
        "tenant_id",
        "workspace_id",
        "user_id",
        "session_id",
        "run_id",
        "attempt_id",
    ):
        if getattr(run_payload, field) != lease_row.get(field):
            raise _permanent_reconciliation_error(
                "executor_reconciliation_identity_mismatch"
            )
    return context, run_payload


def _context_and_payload(
    lease_row: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], RunPayload]:
    terminal_result = lease_row.get("executor_terminal_json")
    if not isinstance(terminal_result, dict):
        raise _permanent_reconciliation_error("executor_terminal_result_missing")
    context, run_payload = _context_payload(lease_row, terminal_result)
    return context, terminal_result, run_payload


def _reconciliation_request(
    lease_row: dict[str, Any],
    run_payload: RunPayload,
) -> SandboxRuntimeRequest:
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


def _container_provider_for_lease(lease: Any) -> Any:
    """Select the provider that owns the persisted runtime handle."""
    return create_container_provider(lease.provider)


async def _release_claimed_probe_batch(
    claimed: list[dict[str, Any]],
    claim_token: str,
) -> None:
    """Best-effort release every receipt-less probe claim owned by this batch."""
    try:
        async with transaction() as conn:
            for lease_row in claimed:
                await sandbox_lease_repository.release_sandbox_executor_probe_claim(
                    conn,
                    lease_id=str(lease_row["id"]),
                    claim_token=claim_token,
                    error="probe_cancelled",
                )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - the original cancellation must propagate.
        _logger.exception("Sandbox executor probe cancellation claim release failed")


async def _release_claimed_terminal_batch(
    claimed: list[dict[str, Any]],
    claim_token: str,
) -> None:
    """Best-effort release every terminal claim still owned by this batch."""
    try:
        async with transaction() as conn:
            for lease_row in claimed:
                await sandbox_lease_repository.retry_sandbox_executor_reconciliation(
                    conn,
                    lease_id=str(lease_row["id"]),
                    claim_token=claim_token,
                    error="reconciler_cancelled",
                )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - the original cancellation must propagate.
        _logger.exception("Sandbox executor terminal cancellation claim release failed")


async def _terminalize_reconciliation_failure(
    lease_row: dict[str, Any],
    *,
    claim_token: str,
    logger: logging.Logger,
    v4_capabilities: WorkerV4Capabilities,
) -> None:
    tenant_id = str(lease_row["tenant_id"])
    run_id = str(lease_row["run_id"])
    progress = None
    run_was_terminal = False
    async with transaction() as conn:
        claimed = await sandbox_lease_repository.has_sandbox_executor_reconciliation_claim(
            conn,
            lease_id=str(lease_row["id"]),
            claim_token=claim_token,
        )
        if not claimed:
            raise RuntimeError("executor_reconciliation_claim_lost")
        run = await repositories.get_run(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            for_update=True,
        )
        if run is None:
            return
        if str(run.get("status") or "") in _TERMINAL_RUN_STATUSES:
            run_was_terminal = True
        else:
            progress = await fail_run_with_v4(
                conn,
                capabilities=v4_capabilities,
                tenant_id=tenant_id,
                run_id=run_id,
                error_code="terminal_reconciliation_failed",
                error_message="Executor terminal reconciliation could not be completed.",
                result_json={
                    "message": "Executor terminal reconciliation could not be completed.",
                },
            )
    if progress is not None and not progress.is_terminal():
        progress = await drain_run_tool_permission_terminalization(
            tenant_id=tenant_id,
            run_id=run_id,
            capabilities=v4_capabilities,
            transaction_factory=transaction,
        )
    if progress is not None and progress.did_transition and progress.needs_reconcile:
        try:
            await reconcile_terminalized_permission_run(
                tenant_id=tenant_id,
                run_id=run_id,
                progress=progress,
                transaction_factory=transaction,
            )
        except Exception:  # noqa: BLE001 - durable child reconciliation remains retryable.
            logger.exception(
                "executor_terminal_reconciliation_child_reconcile_failed",
                extra={"run_id": run_id},
            )
    if run_was_terminal:
        return
    await publish_pending_run_terminal(
        v4_capabilities,
        tenant_id=tenant_id,
        run_id=run_id,
    )


async def _quarantine_failed_reconciliation(
    lease_row: dict[str, Any],
    *,
    claim_token: str,
    error_code: str,
) -> None:
    async with transaction() as conn:
        quarantined = (
            await sandbox_lease_repository.quarantine_sandbox_executor_reconciliation(
                conn,
                lease_id=str(lease_row["id"]),
                claim_token=claim_token,
                error=error_code,
            )
        )
    if not quarantined:
        raise RuntimeError("executor_reconciliation_claim_lost")


async def _mark_failed_reconciliation_for_cleanup(
    lease_row: dict[str, Any],
    *,
    claim_token: str,
    error_code: str,
) -> None:
    async with transaction() as conn:
        marked = await sandbox_lease_repository.mark_sandbox_executor_reconciliation_cleanup_pending(
            conn,
            lease_id=str(lease_row["id"]),
            claim_token=claim_token,
            error=error_code,
        )
    if not marked:
        raise RuntimeError("executor_reconciliation_claim_lost")


async def _finish_terminal_reconciliation_failure(
    lease_row: dict[str, Any],
    *,
    claim_token: str,
    error_code: str,
    logger: logging.Logger,
    v4_capabilities: WorkerV4Capabilities,
) -> None:
    await _terminalize_reconciliation_failure(
        lease_row,
        claim_token=claim_token,
        logger=logger,
        v4_capabilities=v4_capabilities,
    )
    lease = container_lease_from_persisted_row(lease_row)
    if lease is None:
        await _quarantine_failed_reconciliation(
            lease_row,
            claim_token=claim_token,
            error_code=error_code,
        )
        return
    try:
        await _release_reconciled_lease(
            lease_row,
            provider=_container_provider_for_lease(lease),
            lease=lease,
            claim_token=claim_token,
            transaction_factory=transaction,
        )
    except asyncio.CancelledError:
        raise
    except SandboxReconciliationStopError as exc:
        logger.exception(
            "executor_terminal_reconciliation_cleanup_pending",
            extra={
                "lease_id": str(lease_row["id"]),
                "run_id": str(lease_row["run_id"]),
                "error_type": type(exc).__name__,
            },
        )
        await _mark_failed_reconciliation_for_cleanup(
            lease_row,
            claim_token=claim_token,
            error_code=error_code,
        )


async def _collect_workspace_and_convert_result(
    lease_row: dict[str, Any],
    *,
    registry: AdapterRegistry,
    claim_token: str,
) -> tuple[ExecutorResult, Any, Any]:
    context, terminal_result, run_payload = _context_and_payload(lease_row)
    diagnostics = terminal_result.get("diagnostics")
    if isinstance(diagnostics, list) and diagnostics:
        async with transaction() as conn:
            persisted = await sandbox_lease_repository.record_sandbox_executor_terminal_diagnostics(
                conn,
                lease_id=str(lease_row["id"]),
                claim_token=claim_token,
                diagnostics=[str(item) for item in diagnostics],
            )
        if not persisted:
            raise RuntimeError("executor_reconciliation_claim_lost")
    adapter_name = str(context.get("adapter_name") or "").strip()
    if not adapter_name:
        raise _permanent_reconciliation_error(
            "executor_reconciliation_adapter_name_missing"
        )
    try:
        adapter = registry.get(adapter_name)
    except KeyError as exc:
        raise _permanent_reconciliation_error(
            "executor_reconciliation_adapter_unknown"
        ) from exc
    request = _reconciliation_request(lease_row, run_payload)
    workspace = SandboxWorkspaceManager().prepare(request)
    lease = container_lease_from_persisted_row(lease_row)
    if lease is None:
        raise _permanent_reconciliation_error(
            "executor_reconciliation_runtime_handle_invalid"
        )
    lease = lease.model_copy(update={"workspace_host_path": workspace.workspace_host_path})
    provider = _container_provider_for_lease(lease)
    collection_error: Exception | None = None
    try:
        await provider.collect_workspace(lease, request, workspace)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - converted into a controlled terminal result.
        collection_error = exc
    try:
        result = adapter.reconcile_sandbox_terminal(
            payload=run_payload,
            terminal_result=terminal_result,
            adapter_context=context.get("adapter_context") or {},
            provider=str(lease_row.get("provider") or ""),
            timings=context.get("dispatch_timings") or {},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _permanent_reconciliation_error(
            "executor_reconciliation_terminal_result_invalid"
        ) from exc
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


async def _persist_probe_terminal(
    lease_row: dict[str, Any],
    *,
    executor_status: str,
    terminal_result: dict[str, Any],
    claim_token: str,
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
            claim_token=claim_token,
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
            provider = _container_provider_for_lease(lease)
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
                    claim_token=claim_token,
                )
                continue
            async with transaction() as conn:
                await sandbox_lease_repository.release_sandbox_executor_probe_claim(
                    conn,
                    lease_id=lease_id,
                    claim_token=claim_token,
                )
        except asyncio.CancelledError:
            await _release_claimed_probe_batch(claimed, claim_token)
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
                    claim_token=claim_token,
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
    v4_capabilities: WorkerV4Capabilities | None = None,
    limit: int = _RECONCILIATION_BATCH_SIZE,
) -> int:
    claim_token = uuid.uuid4().hex
    async with transaction() as conn:
        claimed = await sandbox_lease_repository.claim_sandbox_executor_reconciliations(
            conn,
            claim_token=claim_token,
            stale_after_seconds=_RECONCILIATION_CLAIM_STALE_SECONDS,
            limit=min(max(limit, 0), _RECONCILIATION_BATCH_SIZE),
        )
    if not claimed:
        return 0
    adapter_registry = registry or AdapterRegistry()
    for lease_row in claimed:
        try:
            async with transaction() as conn:
                claimed_current = await sandbox_lease_repository.has_sandbox_executor_reconciliation_claim(
                    conn,
                    lease_id=str(lease_row["id"]),
                    claim_token=claim_token,
                )
                if not claimed_current:
                    raise RuntimeError("executor_reconciliation_claim_lost")
                run = await repositories.get_run(
                    conn,
                    tenant_id=str(lease_row["tenant_id"]),
                    run_id=str(lease_row["run_id"]),
                    for_update=False,
                )
            if run is None:
                raise ValueError("executor_reconciliation_run_missing")
            if str(run.get("status") or "") in _TERMINAL_RUN_STATUSES:
                context, _terminal_result, run_payload = _context_and_payload(lease_row)
                request = _reconciliation_request(lease_row, run_payload)
                workspace = SandboxWorkspaceManager().prepare(request)
                lease = container_lease_from_persisted_row(lease_row)
                if lease is None:
                    raise _permanent_reconciliation_error(
                        "executor_reconciliation_runtime_handle_invalid"
                    )
                lease = lease.model_copy(update={"workspace_host_path": workspace.workspace_host_path})
                provider = _container_provider_for_lease(lease)
            else:
                result, provider, lease = await _collect_workspace_and_convert_result(
                    lease_row,
                    registry=adapter_registry,
                    claim_token=claim_token,
                )
                await reconcile_executor_terminal_result(
                    lease_row=lease_row,
                    result=result,
                    registry=adapter_registry,
                    worker_id=worker_id,
                    claim_token=claim_token,
                    transaction_factory=transaction,
                    v4_capabilities=v4_capabilities,
                )
            await _release_reconciled_lease(
                lease_row,
                provider=provider,
                lease=lease,
                claim_token=claim_token,
                transaction_factory=transaction,
            )
        except asyncio.CancelledError:
            await _release_claimed_terminal_batch(claimed, claim_token)
            raise
        except Exception as exc:  # noqa: BLE001 - durable retry or terminal failure boundary.
            error_code = _reconciliation_error_code(exc)
            attempt_count = int(
                lease_row.get("executor_terminal_reconciliation_attempt_count") or 0
            )
            terminal_failure = _reconciliation_failure_is_terminal(lease_row, exc)
            _logger.exception(
                "executor_terminal_reconciliation_attempt_failed",
                extra={
                    "lease_id": str(lease_row["id"]),
                    "run_id": str(lease_row["run_id"]),
                    "attempt_id": str(lease_row.get("attempt_id") or ""),
                    "attempt_count": attempt_count,
                    "error_type": type(exc).__name__,
                    "error_code": error_code,
                    "terminal_failure": terminal_failure,
                },
            )
            if not terminal_failure:
                async with transaction() as conn:
                    await sandbox_lease_repository.retry_sandbox_executor_reconciliation(
                        conn,
                        lease_id=str(lease_row["id"]),
                        claim_token=claim_token,
                        error=error_code,
                    )
                continue
            try:
                await _finish_terminal_reconciliation_failure(
                    lease_row,
                    claim_token=claim_token,
                    error_code=error_code,
                    logger=_logger,
                    v4_capabilities=v4_capabilities,
                )
            except asyncio.CancelledError:
                await _release_claimed_terminal_batch(claimed, claim_token)
                raise
            except Exception as terminal_exc:  # noqa: BLE001 - retry the durable failure handler.
                _logger.exception(
                    "executor_terminal_reconciliation_failure_handler_failed",
                    extra={
                        "lease_id": str(lease_row["id"]),
                        "run_id": str(lease_row["run_id"]),
                        "error_type": type(terminal_exc).__name__,
                    },
                )
                async with transaction() as conn:
                    await sandbox_lease_repository.retry_sandbox_executor_reconciliation(
                        conn,
                        lease_id=str(lease_row["id"]),
                        claim_token=claim_token,
                        error="terminal_reconciliation_failure_handler_failed",
                    )
    return len(claimed)


async def run_executor_terminal_reconciler(
    stop_event: asyncio.Event,
    *,
    registry: AdapterRegistry | None = None,
    worker_id: str | None = None,
    v4_capabilities: WorkerV4Capabilities | None = None,
) -> None:
    while not stop_event.is_set():
        try:
            await reconcile_pending_executor_terminals_once(
                registry=registry,
                worker_id=worker_id,
                v4_capabilities=v4_capabilities,
            )
            await cleanup_failed_sandbox_executor_reconciliation_leases(
                provider_factory=create_container_provider,
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
