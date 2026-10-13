"""Durable reconciliation for asynchronously dispatched sandbox executor tasks."""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from pathlib import Path
from typing import Any

from app.runs.infrastructure import postgres as runs_postgres

from app.bootstrap.worker_maintenance import maintenance_phase_until_done
from app.context.api import ProviderSessionConflictError, ProviderSessionContinuityError
from app.db import transaction
from app.execution.api import restored_sandbox_run_payload
from app.executors.base import ExecutorResult, RunPayload
from app.executors.registry import AdapterRegistry
from app.platform.postgres import sandbox_leases as sandbox_lease_repository
from app.platform.tracing import TerminalStageTiming
from app.persistence.artifacts import reserve_provisional_artifact_cleanup
from app.bootstrap.sandbox_cleanup import build_sandbox_runtime_cleanup
from app.runtime.sandbox.container_provider import create_container_provider
from app.runtime.sandbox.contracts import (
    ExecutorTerminalResult,
    SandboxRuntimeRequest,
    executor_terminal_receipt_payload,
    normalize_executor_terminal_status,
)
from app.runtime.sandbox.executor_client import SandboxExecutorClient
from app.runtime.sandbox.executor_signals import (
    ExecutorReconciliationSignalCursor,
    ExecutorSignalUnavailable,
    initialize_executor_reconciliation_signal_cursor,
    wait_for_executor_reconciliation_signal,
)
from app.runtime.sandbox.providers.opensandbox.startup import (
    is_authoritative_not_found_error,
    opensandbox_probe_error_facts,
)
from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager
from app.runs.api import (
    ReconciliationAuthorityError, RunAttemptLifecycleService, RunDiagnosticsService, RunLifecycleService,
)
from app.sandbox.api import (
    SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
    SandboxExecutorHttpError,
    exception_chain_from_error,
)
from app.runs.api import cancel_run_with_v4, fail_run_with_v4, finish_run_terminalization
from app.storage import run_storage_io
from app.worker import (
    WorkerV4Capabilities,
    publish_run_event,
    reconcile_executor_terminal_result,
)

_sandbox_cleanup = build_sandbox_runtime_cleanup()

_RECONCILIATION_BATCH_SIZE = 1
_RECONCILIATION_CLAIM_STALE_SECONDS = 300
_RECONCILIATION_WORK_TIMEOUT_SECONDS = 240.0
_RECONCILIATION_IDLE_SECONDS = 30.0
_RECONCILIATION_CONCURRENCY = 2
_RECONCILIATION_FALLBACK_SECONDS = 2.0
_RECONCILIATION_SIGNAL_INIT_TIMEOUT_SECONDS = 5.0
_EXECUTOR_HEARTBEAT_STALE_SECONDS = 45
_EXECUTOR_PROBE_FAILURE_LIMIT = 3
_TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled"}
_logger = logging.getLogger(__name__)


class PermanentExecutorReconciliationError(ValueError):
    """A persisted terminal receipt cannot become valid through retry."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _permanent_reconciliation_error(
    code: str,
) -> PermanentExecutorReconciliationError:
    return PermanentExecutorReconciliationError(code)


def _reconciliation_error_code(exc: Exception) -> str:
    if isinstance(
        exc,
        (PermanentExecutorReconciliationError, ProviderSessionContinuityError, ReconciliationAuthorityError),
    ):
        return exc.code
    return type(exc).__name__


def _reconciliation_failure_is_terminal(exc: Exception) -> bool:
    return isinstance(exc, PermanentExecutorReconciliationError) or (
        isinstance(exc, ProviderSessionConflictError)
        and exc.code.startswith("provider_session_terminal_")
    )


def _context_payload(
    lease_row: dict[str, Any],
    terminal_result: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], RunPayload]:
    context = lease_row.get("executor_reconciliation_context_json")
    if not isinstance(context, dict):
        raise ReconciliationAuthorityError("executor_reconciliation_context_missing")
    adapter_context = context.get("adapter_context")
    if not isinstance(adapter_context, dict):
        raise ReconciliationAuthorityError(
            "executor_reconciliation_adapter_context_missing"
        )
    run_payload_data = context.get("run_payload")
    if not isinstance(run_payload_data, dict):
        raise ReconciliationAuthorityError(
            "executor_reconciliation_run_payload_missing"
        )
    try:
        run_payload = restored_sandbox_run_payload(
            run_payload_data,
            RunPayload,
            terminal_result if terminal_result is not None else {},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ReconciliationAuthorityError(
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
            raise ReconciliationAuthorityError(
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
    raw_subjects = run_payload.input.get("_runtime_tool_policy_subjects") if isinstance(run_payload.input, dict) else None
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


def _exception_diagnostic_evidence(error: BaseException) -> dict[str, Any]:
    losses: list[dict[str, object]] = []
    return {
        "sdk": {
            "exception_type": type(error).__name__,
            "exception_message": str(error),
            "exception_chain": exception_chain_from_error(error, losses=losses),
        },
        "normalization_losses": losses,
    }


async def _terminalize_reconciliation_failure(
    lease_row: dict[str, Any],
    *,
    claim_token: str,
    logger: logging.Logger,
    v4_capabilities: WorkerV4Capabilities,
    attempt_lifecycle: RunAttemptLifecycleService,
    lifecycle: RunLifecycleService,
    run_diagnostics: RunDiagnosticsService | None,
    reconciliation_error_code: str,
    reconciliation_error: BaseException | None = None,
) -> None:
    tenant_id = str(lease_row["tenant_id"])
    run_id = str(lease_row["run_id"])
    progress = None
    run_was_terminal = False
    attempt_id = str(lease_row.get("attempt_id") or "") or None
    async with transaction() as conn:
        # All receipt settlement locks Run before Sandbox claim, then Attempt.
        run = await runs_postgres.get_run(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            for_update=True,
        )
        claimed = (
            await sandbox_lease_repository.has_sandbox_executor_reconciliation_claim(
                conn,
                lease_id=str(lease_row["id"]),
                claim_token=claim_token,
            )
        )
        if not claimed:
            raise RuntimeError("executor_reconciliation_claim_lost")
        if run is None:
            return
        if run_diagnostics is not None:
            await run_diagnostics.capture_reconciliation_failure(
                conn,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                terminal_result=lease_row.get("executor_terminal_json"),
                reconciliation_error_code=reconciliation_error_code,
                reconciliation_evidence_factory=(
                    lambda: _exception_diagnostic_evidence(reconciliation_error)
                ) if reconciliation_error is not None else None,
                lease_id=str(lease_row["id"]),
            )
        if str(run.get("status") or "") in _TERMINAL_RUN_STATUSES:
            run_was_terminal = True
        else:
            cancel_requested = bool(run.get("cancel_requested_at")) or str(
                run.get("terminalization_target") or ""
            ) in {"cancel_requested", "cancelled"}
            if attempt_id is not None and cancel_requested:
                await attempt_lifecycle.request_cancel(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                )
            if cancel_requested:
                progress = await cancel_run_with_v4(
                    conn,
                    lifecycle=lifecycle,
                    capabilities=v4_capabilities,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    result_json={"message": "任务已取消"},
                )
            else:
                progress = await fail_run_with_v4(
                    conn,
                    lifecycle=lifecycle,
                    capabilities=v4_capabilities,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    error_code="terminal_reconciliation_failed",
                    error_message="Executor terminal reconciliation could not be completed.",
                    result_json={
                        "message": "Executor terminal reconciliation could not be completed.",
                    },
                )
            if progress.is_terminal() and attempt_id is not None:
                await attempt_lifecycle.terminalize(
                    conn,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    status=str(progress.status),
                    terminal_reason=(
                        "run_cancelled"
                        if progress.status == "cancelled"
                        else "executor_terminal_reconciliation_failed"
                    ),
                    error_code=(
                        "terminal_reconciliation_failed"
                        if progress.status == "failed"
                        else None
                    ),
                )
    if progress is not None and not progress.is_terminal():
        progress = await finish_run_terminalization(
            lifecycle=lifecycle,
            tenant_id=tenant_id,
            run_id=run_id,
            transaction_factory=transaction,
            capabilities=v4_capabilities,
            attempt_lifecycle=attempt_lifecycle,
            attempt_id=attempt_id,
            attempt_error_code=(
                None if cancel_requested else "terminal_reconciliation_failed"
            ),
        )
    if run_was_terminal:
        return
    await publish_run_event(
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


async def _mark_reconciliation_for_cleanup(
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
    attempt_lifecycle: RunAttemptLifecycleService,
    lifecycle: RunLifecycleService,
    run_diagnostics: RunDiagnosticsService | None,
    reconciliation_error: BaseException | None = None,
) -> None:
    await _terminalize_reconciliation_failure(
        lease_row,
        claim_token=claim_token,
        logger=logger,
        v4_capabilities=v4_capabilities,
        attempt_lifecycle=attempt_lifecycle,
        lifecycle=lifecycle,
        run_diagnostics=run_diagnostics,
        reconciliation_error_code=error_code,
        reconciliation_error=reconciliation_error,
    )
    lease = _sandbox_cleanup.container_lease_from_persisted_row(lease_row)
    if lease is None:
        await _quarantine_failed_reconciliation(
            lease_row,
            claim_token=claim_token,
            error_code=error_code,
        )
        return
    await _mark_reconciliation_for_cleanup(
        lease_row, claim_token=claim_token, error_code=error_code,
    )

async def _reserve_reconciliation_artifact_cleanup(
    *,
    tenant_id: str,
    run_id: str,
    storage_key: str,
) -> str:
    async with transaction() as conn:
        return await reserve_provisional_artifact_cleanup(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            storage_key=storage_key,
        )


async def _collect_workspace_and_convert_result(
    lease_row: dict[str, Any],
    *,
    registry: AdapterRegistry,
    claim_token: str,
    run_diagnostics: RunDiagnosticsService | None = None,
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
    lease = _sandbox_cleanup.container_lease_from_persisted_row(lease_row)
    if lease is None:
        raise _permanent_reconciliation_error(
            "executor_reconciliation_runtime_handle_invalid"
        )
    lease = lease.model_copy(update={"workspace_host_path": workspace.workspace_host_path})
    provider = _container_provider_for_lease(lease)
    collection_error: Exception | None = None
    artifact_workspace: Path | None = None
    try:
        raw_response_files = terminal_result.get("response_files", [])
        if not isinstance(raw_response_files, list) or not all(
            isinstance(path, str) for path in raw_response_files
        ):
            raise ValueError("executor response file selection is invalid")
        with TerminalStageTiming(
            "reconciler_workspace_snapshot", run_id=str(lease_row["run_id"]),
            attempt_id=run_payload.attempt_id, terminal_status=terminal_result.get("status"),
        ):
            artifact_workspace = await provider.collect_workspace(
                lease,
                request,
                workspace,
                raw_response_files,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - converted into a controlled terminal result.
        collection_error = exc
    adapter_context = dict(context.get("adapter_context") or {})
    if artifact_workspace is not None:
        adapter_context["_artifact_workspace"] = str(artifact_workspace)
    else:
        adapter_context.pop("_artifact_workspace", None)
    if collection_error is not None:
        collection_code = "sandbox_workspace_collection_failed"
        # A late artifact failure cannot replace execution/cancellation authority,
        # especially the MCP outcome codes that prohibit unsafe retries.
        primary_failure = str(terminal_result.get("status") or "").strip().lower() in {
            "failed", "cancelled", "canceled",
        }
        _logger.warning(
            "sandbox_workspace_collection_failed",
            extra={
                "lease_id": str(lease_row["id"]),
                "run_id": str(lease_row["run_id"]),
                "attempt_id": str(run_payload.attempt_id),
                "error_code": collection_code,
                "primary_failure_preserved": primary_failure,
            },
        )
        if primary_failure:
            if run_diagnostics is not None:
                try:
                    async with transaction() as conn:
                        # The persisted receipt normally has its private carrier
                        # stripped. Keep the authoritative cause ahead of handling
                        # evidence even when no earlier diagnostic was captured.
                        primary_code = str(
                            terminal_result.get("error_code") or terminal_result["status"]
                        )
                        primary_diagnostics = terminal_result.get("runtime_diagnostics") or {
                            "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
                            "error_code": primary_code,
                            "failure_source": "sandbox_runtime",
                            "failure_stage": "terminal_receipt",
                        }
                        collection_diagnostics = {
                            "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
                            "error_code": collection_code,
                            "failure_source": "sandbox_runtime",
                            "failure_stage": "workspace_collection",
                            **_exception_diagnostic_evidence(collection_error),
                        }
                        for stage, code, evidence in (
                            ("terminal_receipt", primary_code, primary_diagnostics),
                            ("workspace_collection", collection_code, collection_diagnostics),
                        ):
                            await run_diagnostics.capture_failure_result(
                                conn,
                                tenant_id=str(lease_row["tenant_id"]),
                                run_id=str(lease_row["run_id"]),
                                attempt_id=str(run_payload.attempt_id),
                                lease_id=str(lease_row["id"]),
                                source="executor_reconciliation",
                                stage=stage,
                                error_code=code,
                                result_json={"runtime_diagnostics": evidence},
                            )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - diagnostic-only transaction cannot veto the receipt.
                    _logger.warning(
                        "executor_reconciliation_diagnostics_unavailable",
                        extra={
                            "lease_id": str(lease_row["id"]),
                            "run_id": str(lease_row["run_id"]),
                            "attempt_id": str(run_payload.attempt_id),
                            "error_type": type(exc).__name__,
                        },
                    )
        else:
            terminal_result = {
                **terminal_result,
                "status": "failed",
                "error_code": "sandbox_workspace_collection_failed",
                "message": "Sandbox workspace collection failed.",
                "runtime_diagnostics": {
                    "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
                    "error_code": "sandbox_workspace_collection_failed",
                    "failure_source": "sandbox_runtime",
                    "failure_stage": "workspace_collection",
                    **_exception_diagnostic_evidence(collection_error),
                },
            }
    abandoned = threading.Event()
    loop = asyncio.get_running_loop()

    def reserve_artifact_storage(storage_key: str) -> str:
        receipt = asyncio.run_coroutine_threadsafe(
            _reserve_reconciliation_artifact_cleanup(
                tenant_id=str(lease_row["tenant_id"]),
                run_id=str(lease_row["run_id"]),
                storage_key=storage_key,
            ),
            loop,
        )
        return receipt.result()

    adapter_context["_artifact_storage_scope"] = run_payload.attempt_id
    adapter_context["_artifact_collection_abandoned"] = abandoned
    adapter_context["_reserve_artifact_storage"] = reserve_artifact_storage
    try:
        with TerminalStageTiming(
            "reconciler_result_conversion", run_id=str(lease_row["run_id"]),
            attempt_id=run_payload.attempt_id, terminal_status=terminal_result.get("status"),
        ):
            result = await run_storage_io(
                adapter.reconcile_sandbox_terminal,
                payload=run_payload,
                terminal_result=terminal_result,
                adapter_context=adapter_context,
                provider=str(lease_row.get("provider") or ""),
                timings=context.get("dispatch_timings") or {},
                on_abandoned=abandoned.set,
            )
    except (KeyError, TypeError, ValueError) as exc:
        abandoned.set()
        raise _permanent_reconciliation_error(
            "executor_reconciliation_terminal_result_invalid"
        ) from exc
    except BaseException:
        abandoned.set()
        raise
    return result, provider, lease


async def _persist_probe_terminal(
    lease_row: dict[str, Any],
    *,
    executor_status: str,
    terminal_result: dict[str, Any],
    claim_token: str,
    run_diagnostics: RunDiagnosticsService | None = None,
    protocol_failure: dict[str, Any] | None = None,
) -> None:
    terminal_receipt = executor_terminal_receipt_payload(terminal_result)
    async with transaction() as conn:
        await sandbox_lease_repository.record_sandbox_executor_terminal(
            conn,
            tenant_id=str(lease_row["tenant_id"]),
            run_id=str(lease_row["run_id"]),
            attempt_id=str(lease_row["attempt_id"]),
            lease_id=str(lease_row["id"]),
            executor_status=executor_status,
            terminal_result=terminal_receipt,
            claim_token=claim_token,
        )
        if run_diagnostics is not None:
            if protocol_failure is not None:
                await run_diagnostics.capture_executor_protocol_failure(
                    conn,
                    tenant_id=str(lease_row["tenant_id"]),
                    run_id=str(lease_row["run_id"]),
                    attempt_id=str(lease_row["attempt_id"]),
                    lease_id=str(lease_row["id"]),
                    task_status=protocol_failure.get("task_status"),
                    terminal_result=protocol_failure.get("terminal_result"),
                    validation_errors=protocol_failure.get("validation_errors"),
                )
            else:
                await run_diagnostics.capture_failure_result(
                    conn,
                    tenant_id=str(lease_row["tenant_id"]),
                    run_id=str(lease_row["run_id"]),
                    attempt_id=str(lease_row["attempt_id"]),
                    source="executor_probe",
                    stage="terminal_receipt",
                    error_code=str(terminal_result.get("error_code") or executor_status),
                    result_json=terminal_result,
                    lease_id=str(lease_row["id"]),
                )


async def probe_suspect_executor_tasks_once(
    *,
    run_diagnostics: RunDiagnosticsService | None = None,
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
    persisted_count = 0
    for lease_row in claimed:
        lease_id = str(lease_row["id"])
        failure_stage = "attempt_reconstruction"
        try:
            _context, run_payload = _context_payload(lease_row)
            request = _reconciliation_request(lease_row, run_payload)
            lease = _sandbox_cleanup.container_lease_from_persisted_row(lease_row)
            if lease is None:
                raise ValueError("executor_reconciliation_runtime_handle_invalid")
            provider = _container_provider_for_lease(lease)
            failure_stage = "provider_endpoint"
            executor_url, executor_headers = await provider.executor_control_endpoint(lease, request)
            client = SandboxExecutorClient(timeout_seconds=10.0)
            failure_stage = "run_state_check"
            async with transaction() as conn:
                run = await runs_postgres.get_run(
                    conn,
                    tenant_id=str(lease_row["tenant_id"]),
                    run_id=str(lease_row["run_id"]),
                )
            if run is not None and str(run.get("status") or "") == "cancelled":
                failure_stage = "executor_cancel"
                await client.cancel(
                    executor_url,
                    run_id=str(lease_row["run_id"]),
                    attempt_id=str(lease_row["attempt_id"]),
                    executor_headers=executor_headers,
                )
            failure_stage = "executor_status"
            status = await client.get_status(
                executor_url,
                run_id=str(lease_row["run_id"]),
                attempt_id=str(lease_row["attempt_id"]),
                executor_headers=executor_headers,
            )
            terminal_result = status.get("terminal_result")
            if terminal_result is not None:
                validation_errors: list[dict[str, Any]] = []
                try:
                    canonical_result = ExecutorTerminalResult.model_validate(
                        terminal_result
                    ).model_dump(mode="json", exclude_none=True)
                except ValueError as exc:
                    canonical_result = None
                    error_details = getattr(exc, "errors", None)
                    validation_errors = (
                        error_details(
                            include_url=False,
                            include_context=False,
                            include_input=False,
                        )
                        if callable(error_details)
                        else [
                            {
                                "loc": [],
                                "type": "value_error",
                                "msg": "Terminal result validation failed",
                            }
                        ]
                    )
                if canonical_result is None:
                    executor_status = None
                elif canonical_result.get("run_id") != str(lease_row["run_id"]):
                    executor_status = None
                    validation_errors = [
                        {
                            "loc": ["run_id"],
                            "type": "run_id_mismatch",
                            "msg": "Terminal result Run does not match the claimed Run",
                        }
                    ]
                else:
                    executor_status = normalize_executor_terminal_status(
                        status.get("status"),
                        canonical_result.get("status"),
                    )
                    if executor_status is None:
                        validation_errors = [
                            {
                                "loc": ["status"],
                                "type": "status_mismatch",
                                "msg": "Executor task status does not match terminal result status",
                            }
                        ]
                if executor_status is None:
                    await _persist_probe_terminal(
                        lease_row,
                        executor_status="failed",
                        terminal_result={
                            "run_id": str(lease_row["run_id"]),
                            "status": "failed",
                            "message": "",
                            "error_code": "executor_protocol_invalid",
                            "error_message": "Sandbox executor returned an invalid terminal result",
                        },
                        claim_token=claim_token,
                        run_diagnostics=run_diagnostics,
                        protocol_failure={
                            "task_status": status.get("status"),
                            "terminal_result": terminal_result,
                            "validation_errors": validation_errors,
                        },
                    )
                    persisted_count += 1
                    continue
                await _persist_probe_terminal(
                    lease_row,
                    executor_status=executor_status,
                    terminal_result=canonical_result,
                    claim_token=claim_token,
                    run_diagnostics=run_diagnostics,
                )
                persisted_count += 1
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
            authoritative_absence = is_authoritative_not_found_error(exc)
            if authoritative_absence or attempts >= _EXECUTOR_PROBE_FAILURE_LIMIT:
                facts = opensandbox_probe_error_facts(exc)
                if isinstance(exc, SandboxExecutorHttpError):
                    facts = {"http_status": exc.status_code, "executor_error_code": exc.error_code}
                await _persist_probe_terminal(
                    lease_row,
                    executor_status="failed",
                    terminal_result={
                        "run_id": str(lease_row["run_id"]),
                        "status": "failed",
                        "error_code": "sandbox_executor_lost",
                        "error_message": "Sandbox executor stopped responding",
                        "runtime_diagnostics": {
                            "schema_version": SDK_RUNTIME_DIAGNOSTICS_SCHEMA_VERSION,
                            "error_code": "sandbox_executor_lost",
                            "failure_source": "executor_probe",
                            "failure_stage": failure_stage,
                            "sdk": {
                                "exception_type": type(exc).__name__,
                                "errors": [{
                                    "reason": "provider_absent" if authoritative_absence else "probe_retry_exhausted",
                                    **facts,
                                }],
                            },
                        },
                    },
                    claim_token=claim_token,
                    run_diagnostics=run_diagnostics,
                )
                persisted_count += 1
            else:
                async with transaction() as conn:
                    await sandbox_lease_repository.release_sandbox_executor_probe_claim(
                        conn,
                        lease_id=lease_id,
                        claim_token=claim_token,
                        error="sandbox_executor_probe_failed",
                    )
    return persisted_count


async def reconcile_pending_executor_terminals_once(
    *,
    registry: AdapterRegistry | None = None,
    worker_id: str | None = None,
    v4_capabilities: WorkerV4Capabilities | None = None,
    attempt_lifecycle: RunAttemptLifecycleService,
    lifecycle: RunLifecycleService,
    run_diagnostics: RunDiagnosticsService | None = None,
    limit: int = _RECONCILIATION_BATCH_SIZE,
) -> int:
    claim_token = uuid.uuid4().hex
    reconciliation_deadline = (
        asyncio.get_running_loop().time() + _RECONCILIATION_WORK_TIMEOUT_SECONDS
    )
    async with transaction() as conn:
        claimed = await sandbox_lease_repository.claim_sandbox_executor_reconciliations(
            conn,
            claim_token=claim_token,
            stale_after_seconds=_RECONCILIATION_CLAIM_STALE_SECONDS,
            limit=min(max(limit, 0), _RECONCILIATION_BATCH_SIZE),
        )
    if not claimed:
        return 0
    reconciled_count = 0
    adapter_registry = registry or AdapterRegistry()
    for lease_row in claimed:
        reconciled_count += 1
        try:
            async with asyncio.timeout_at(reconciliation_deadline):
                async with transaction() as conn:
                    claimed_current = await sandbox_lease_repository.has_sandbox_executor_reconciliation_claim(
                        conn,
                        lease_id=str(lease_row["id"]),
                        claim_token=claim_token,
                    )
                    if not claimed_current:
                        raise RuntimeError("executor_reconciliation_claim_lost")
                    run = await runs_postgres.get_run(
                        conn,
                        tenant_id=str(lease_row["tenant_id"]),
                        run_id=str(lease_row["run_id"]),
                        for_update=False,
                    )
                if run is None:
                    raise ValueError("executor_reconciliation_run_missing")
                if str(run.get("status") or "") in _TERMINAL_RUN_STATUSES:
                    _context_and_payload(lease_row)
                    if _sandbox_cleanup.container_lease_from_persisted_row(lease_row) is None:
                        raise _permanent_reconciliation_error(
                            "executor_reconciliation_runtime_handle_invalid"
                        )
                else:
                    (
                        result,
                        _provider,
                        _lease,
                    ) = await _collect_workspace_and_convert_result(
                        lease_row,
                        registry=adapter_registry,
                        claim_token=claim_token,
                        run_diagnostics=run_diagnostics,
                    )
                    outcome = await reconcile_executor_terminal_result(
                        lease_row=lease_row,
                        result=result,
                        registry=adapter_registry,
                        worker_id=worker_id,
                        claim_token=claim_token,
                        transaction_factory=transaction,
                        v4_capabilities=v4_capabilities,
                        run_attempt_lifecycle=attempt_lifecycle,
                        run_lifecycle=lifecycle,
                    )
                    if (
                        outcome.run_id != str(lease_row["run_id"])
                        or outcome.status not in _TERMINAL_RUN_STATUSES
                    ):
                        # A stale dispatch fence can mean cancellation intent,
                        # not a committed terminal Run. Keep the receipt as the
                        # settlement owner until a later claim can finish it.
                        async with transaction() as conn:
                            await sandbox_lease_repository.retry_sandbox_executor_reconciliation(
                                conn,
                                lease_id=str(lease_row["id"]),
                                claim_token=claim_token,
                                error=(
                                    "executor_reconciliation_outcome_run_mismatch"
                                    if outcome.run_id != str(lease_row["run_id"])
                                    else "executor_reconciliation_run_not_terminal"
                                ),
                            )
                        continue
                await _mark_reconciliation_for_cleanup(
                    lease_row, claim_token=claim_token, error_code="",
                )
        except asyncio.CancelledError:
            await _release_claimed_terminal_batch(claimed, claim_token)
            raise
        except Exception as exc:  # noqa: BLE001 - durable retry or terminal failure boundary.
            error_code = _reconciliation_error_code(exc)
            attempt_count = int(
                lease_row.get("executor_terminal_reconciliation_attempt_count") or 0
            )
            terminal_failure = _reconciliation_failure_is_terminal(exc)
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
            if isinstance(exc, ReconciliationAuthorityError):
                await _quarantine_failed_reconciliation(
                    lease_row, claim_token=claim_token, error_code=exc.code,
                )
                continue
            if not terminal_failure:
                async with transaction() as conn:
                    await (
                        sandbox_lease_repository.retry_sandbox_executor_reconciliation(
                            conn,
                            lease_id=str(lease_row["id"]),
                            claim_token=claim_token,
                            error=error_code,
                        )
                    )
                continue
            try:
                async with asyncio.timeout_at(reconciliation_deadline):
                    await _finish_terminal_reconciliation_failure(
                        lease_row,
                        claim_token=claim_token,
                        error_code=error_code,
                        reconciliation_error=exc,
                        logger=_logger,
                        v4_capabilities=v4_capabilities,
                        attempt_lifecycle=attempt_lifecycle,
                        lifecycle=lifecycle,
                        run_diagnostics=run_diagnostics,
                    )
            except asyncio.CancelledError:
                await _release_claimed_terminal_batch(claimed, claim_token)
                raise
            except TimeoutError:
                async with transaction() as conn:
                    await (
                        sandbox_lease_repository.retry_sandbox_executor_reconciliation(
                            conn,
                            lease_id=str(lease_row["id"]),
                            claim_token=claim_token,
                            error="TimeoutError",
                        )
                    )
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
                    await (
                        sandbox_lease_repository.retry_sandbox_executor_reconciliation(
                            conn,
                            lease_id=str(lease_row["id"]),
                            claim_token=claim_token,
                            error="terminal_reconciliation_failure_handler_failed",
                        )
                    )
    return reconciled_count


async def _next_terminal_retry_delay() -> float | None:
    async with transaction() as conn:
        return await sandbox_lease_repository.next_sandbox_executor_reconciliation_retry_delay(conn)


async def run_executor_terminal_reconciler(
    stop_event: asyncio.Event,
    *,
    registry: AdapterRegistry | None = None,
    worker_id: str | None = None,
    v4_capabilities: WorkerV4Capabilities | None = None,
    attempt_lifecycle: RunAttemptLifecycleService,
    lifecycle: RunLifecycleService,
    run_diagnostics: RunDiagnosticsService | None = None,
) -> None:
    wake = asyncio.Event()
    cleanup_wake = asyncio.Event()
    active: set[asyncio.Task[int]] = set()

    async def read_signals() -> None:
        cursor: ExecutorReconciliationSignalCursor | None = None
        while not stop_event.is_set():
            try:
                if cursor is None:
                    cursor = await asyncio.wait_for(
                        initialize_executor_reconciliation_signal_cursor(),
                        timeout=_RECONCILIATION_SIGNAL_INIT_TIMEOUT_SECONDS,
                    )
                    # A callback may commit while the initial tail is being read.
                    wake.set()
                signalled = await wait_for_executor_reconciliation_signal(
                    block_ms=int(_RECONCILIATION_IDLE_SECONDS * 1000), cursor=cursor,
                )
                if signalled:
                    wake.set()
                    cleanup_wake.set()
                await asyncio.sleep(0)
            except (ExecutorSignalUnavailable, TimeoutError):
                cursor = None
                await asyncio.sleep(_RECONCILIATION_FALLBACK_SECONDS)

    async def reconcile() -> int:
        try:
            return await reconcile_pending_executor_terminals_once(
                registry=registry, worker_id=worker_id,
                v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
                lifecycle=lifecycle, run_diagnostics=run_diagnostics,
            )
        except Exception:  # noqa: BLE001 - one failed scan must not stop recovery.
            _logger.exception("executor_terminal_reconciliation_scan_failed")
            return 0

    async def cleanup() -> None:
        while not stop_event.is_set():
            cleanup_wake.clear()
            try:
                async with asyncio.timeout(_RECONCILIATION_WORK_TIMEOUT_SECONDS):
                    released = await _sandbox_cleanup.cleanup_failed_sandbox_executor_reconciliation_leases(
                        provider_factory=create_container_provider,
                    )
                if released:
                    await asyncio.sleep(0)
                    continue
            except Exception:  # noqa: BLE001 - cleanup has its own durable retry owner.
                _logger.exception("executor_terminal_cleanup_scan_failed")
            try:
                await asyncio.wait_for(cleanup_wake.wait(), _RECONCILIATION_FALLBACK_SECONDS)
            except TimeoutError:
                pass

    async def probe() -> int:
        probe_kwargs = ({"run_diagnostics": run_diagnostics} if run_diagnostics is not None else {})
        probed = await probe_suspect_executor_tasks_once(**probe_kwargs)
        if probed:
            wake.set()
        return probed

    owned = [
        asyncio.create_task(read_signals(), name="executor-terminal-signals"),
        asyncio.create_task(cleanup(), name="executor-reconciliation-cleanup"),
        asyncio.create_task(maintenance_phase_until_done(
            "executor-suspect-probe", probe, _RECONCILIATION_IDLE_SECONDS,
            _RECONCILIATION_WORK_TIMEOUT_SECONDS, logger=_logger,
            repeat_on_progress=True,
        ), name="executor-suspect-probe"),
        asyncio.create_task(stop_event.wait(), name="executor-terminal-stop"),
        asyncio.create_task(wake.wait(), name="executor-terminal-wake"),
    ]
    stop_waiter, wake_waiter = owned[-2:]
    loop = asyncio.get_running_loop()
    next_scan = loop.time()
    scan_needed = True
    try:
        while not stop_event.is_set():
            empty_scan = False
            for task in tuple(active):
                if task.done():
                    active.remove(task)
                    if task.result():
                        scan_needed = True
                        cleanup_wake.set()
                    else:
                        empty_scan = True
            if wake_waiter.done():
                wake.clear()
                scan_needed = True
                owned.remove(wake_waiter)
                wake_waiter = asyncio.create_task(wake.wait(), name="executor-terminal-wake")
                owned.append(wake_waiter)
            if empty_scan:
                try:
                    async with asyncio.timeout(_RECONCILIATION_FALLBACK_SECONDS):
                        retry_delay = await _next_terminal_retry_delay()
                    if retry_delay is not None:
                        next_scan = min(next_scan, loop.time() + max(retry_delay, 0.01))
                except Exception:  # noqa: BLE001 - keep the independent fallback clock.
                    _logger.exception("executor_terminal_retry_clock_read_failed")
            if loop.time() >= next_scan:
                scan_needed = True
            if scan_needed and len(active) < _RECONCILIATION_CONCURRENCY:
                for _ in range(_RECONCILIATION_CONCURRENCY - len(active)):
                    active.add(asyncio.create_task(reconcile(), name="executor-terminal-completion"))
                scan_needed = False
                next_scan = loop.time() + _RECONCILIATION_FALLBACK_SECONDS
            timeout = max(0.0, next_scan - loop.time()) if len(active) < _RECONCILIATION_CONCURRENCY else None
            await asyncio.wait(
                active | {stop_waiter, wake_waiter}, timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
    finally:
        for task in [*active, *owned]:
            task.cancel()
        await asyncio.gather(*active, *owned, return_exceptions=True)
