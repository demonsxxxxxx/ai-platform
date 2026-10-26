"""Durable reconciliation for asynchronously dispatched sandbox executor tasks."""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.runs.infrastructure import postgres as runs_postgres

from app.bootstrap.worker_maintenance import maintenance_phase_until_done
from app.context.api import ProviderSessionConflictError, ProviderSessionContinuityError
from app.db import transaction
from app.settings import get_settings
from app.execution.api import ContinuationSettlementPorts, restored_sandbox_run_payload
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
    finalize_stopped_executor_checkpoint,
)
from app.runs.api import cancel_run_with_v4, fail_run_with_v4, finish_run_terminalization
from app.storage import run_storage_io
from app.bootstrap.executor_terminal_reconciliation import reconcile_executor_terminal_result
from app.streaming.api import WorkerV4Capabilities, publish_run_event

_sandbox_cleanup = build_sandbox_runtime_cleanup()

_RECONCILIATION_BATCH_SIZE = 1
_RECONCILIATION_CLAIM_STALE_SECONDS = 300
_RECONCILIATION_WORK_TIMEOUT_SECONDS = 240.0
_RECONCILIATION_IDLE_SECONDS = 30.0
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
    lease_payload = lease_row.get("lease_payload_json") or {}
    lease_generation = lease_payload.get("owner_generation")
    if "owner_generation" in lease_payload and (
        type(lease_generation) is not int or lease_generation < 1
        or lease_payload.get("attempt_id") != lease_row.get("attempt_id")
    ):
        raise ReconciliationAuthorityError("executor_reconciliation_owner_generation_mismatch")
    serialized_payload = run_payload_data.get("execution_payload", run_payload_data)
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
    if lease_generation is not None:
        # Older snapshots omitted generation; recover it only from the exact persisted lease.
        if isinstance(serialized_payload, dict) and "owner_generation" not in serialized_payload:
            run_payload = replace(run_payload, owner_generation=lease_generation)
        if run_payload.owner_generation != lease_generation:
            raise ReconciliationAuthorityError("executor_reconciliation_owner_generation_mismatch")
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
    continuation_ports: ContinuationSettlementPorts | None = None,
    continuation_settlement: dict[str, Any] | None = None,
    reconciliation_error: BaseException | None = None,
) -> None:
    tenant_id = str(lease_row["tenant_id"])
    run_id = str(lease_row["run_id"])
    progress = None
    run_was_terminal = False
    attempt_id = str(lease_row.get("attempt_id") or "") or None
    from app.context.api import provider_session_verification_scope
    from contextlib import nullcontext
    verification = (provider_session_verification_scope(transaction, tenant_id=tenant_id, run_id=run_id)
                    if continuation_settlement is not None else nullcontext())
    async with verification:
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
                if continuation_settlement is not None:
                    if continuation_ports is None or attempt_id is None:
                        raise RuntimeError("continuation_settlement_ports_unconfigured")
                    await continuation_ports.commit(
                        conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id,
                        settlement={**continuation_settlement, "sdk_settlement": {
                            **continuation_settlement["sdk_settlement"],
                            "status": "cancelled" if cancel_requested else "failed",
                        }},
                    )
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
    continuation_ports: ContinuationSettlementPorts | None = None,
    reconciliation_error: BaseException | None = None,
) -> None:
    stage = (lease_row.get("lease_payload_json") or {}).get("terminal_reconciliation")
    if isinstance(stage, dict) and stage.get("continuation") is not None:
        # A completed stop/save checkpoint can only settle atomically or stay quarantined.
        await _quarantine_failed_reconciliation(lease_row, claim_token=claim_token, error_code=error_code)
        return
    lease = _sandbox_cleanup.container_lease_from_persisted_row(lease_row)
    if lease is None:
        await _quarantine_failed_reconciliation(lease_row, claim_token=claim_token, error_code=error_code)
        return
    if continuation_ports is None:
        await _terminalize_reconciliation_failure(
            lease_row, claim_token=claim_token, logger=logger,
            v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
            lifecycle=lifecycle, run_diagnostics=run_diagnostics,
            reconciliation_error_code=error_code, reconciliation_error=reconciliation_error,
        )
        await _mark_reconciliation_for_cleanup(lease_row, claim_token=claim_token, error_code=error_code)
        return
    lease = _sandbox_cleanup.container_lease_from_persisted_row(lease_row)
    if lease is None:
        await _quarantine_failed_reconciliation(
            lease_row,
            claim_token=claim_token,
            error_code=error_code,
        )
        return
    provider = _container_provider_for_lease(lease)
    async with transaction() as conn:
        claimed = await sandbox_lease_repository.has_sandbox_executor_reconciliation_claim(
            conn, lease_id=str(lease_row["id"]), claim_token=claim_token,
        )
    if not claimed:
        raise RuntimeError("executor_reconciliation_claim_lost")
    stopped = await _stop_reconciled_provider(provider, lease)
    if stopped.status not in {"stopped", "not_found"} or stopped.container_id != lease.container_id:
        raise SandboxReconciliationStopError("executor_reconciliation_sandbox_stop_failed")
    continuation = None
    if continuation_ports is not None:
        # Invalid frozen input cannot safely identify original input copies for a save.
        continuation = {
            "tenant_id": str(lease_row["tenant_id"]), "run_id": str(lease_row["run_id"]),
            "attempt_id": str(lease_row.get("attempt_id") or ""), "final_sequence": None,
            "workspace_receipt": {"state": "unsaved", "reason_code": "workspace_unsaved"},
            "stop_evidence": "provider_stopped" if stopped.status == "stopped" else "provider_not_found",
            "sdk_settlement": {"status": "failed", "mirror_complete": False, "received_result": False,
                               "error_code": error_code}, "external_effect_unknown": True,
        }
        try:
            _context, terminal_result, run_payload = _context_and_payload(lease_row)
            request = _reconciliation_request(lease_row, run_payload)
        except (ValueError, TypeError, KeyError):
            pass
        else:
            workspace = SandboxWorkspaceManager().prepare(request)
            continuation = await continuation_ports.prepare(
                request=request, workspace_lease=workspace, stop_result=stopped,
                terminal_result=terminal_result,
            )
    await _terminalize_reconciliation_failure(
        lease_row, claim_token=claim_token, logger=logger,
        v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
        lifecycle=lifecycle, run_diagnostics=run_diagnostics,
        reconciliation_error_code=error_code, reconciliation_error=reconciliation_error,
        **({"continuation_ports": continuation_ports, "continuation_settlement": continuation}
           if continuation is not None else {}),
    )
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


async def _record_reconciliation_stage(lease_row: dict[str, Any], *, claim_token: str,
                                       stage: dict[str, Any]) -> None:
    async with transaction() as conn:
        recorded = await sandbox_lease_repository.record_sandbox_reconciliation_stage(
            conn, lease_id=str(lease_row["id"]), claim_token=claim_token, stage=stage,
        )
    if not recorded:
        raise RuntimeError("executor_reconciliation_claim_lost")
    lease_row.setdefault("lease_payload_json", {})["terminal_reconciliation"] = stage


def _reconciliation_runtime_lease(lease_row: dict[str, Any], request: Any, *, adapter_name: str) -> Any:
    payload = lease_row.get("lease_payload_json") or {}
    stage = payload.get("terminal_reconciliation") if adapter_name == "claude-agent-worker" else None
    historical_checkpoint = False
    if stage is not None:
        scope = {key: getattr(request, key) for key in (
            "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id",
        )}
        if (not isinstance(stage, dict)
            or stage.get("schema_version") != "ai-platform.terminal-reconciliation.v1"
            or stage.get("scope") != scope
            or type(stage.get("owner_generation")) is not int
            or stage["owner_generation"] != request.owner_generation
            or stage["owner_generation"] != payload.get("owner_generation")
            or stage.get("container_id") != lease_row.get("runtime_container_id")):
            raise RuntimeError("executor_reconciliation_stage_scope_invalid")
        continuation = stage.get("continuation")
        if isinstance(continuation, dict):
            receipt = continuation.get("workspace_receipt")
            historical_checkpoint = (
                all(continuation.get(key) == scope[key] for key in ("tenant_id", "run_id", "attempt_id"))
                and continuation.get("stop_evidence") in {"provider_stopped", "provider_not_found"}
                and isinstance(receipt, dict)
                and receipt.get("schema_version") == "ai-platform.workspace-version.v1"
                and receipt.get("state") == "saved" and receipt.get("scope") == scope
                and isinstance(receipt.get("version_id"), str) and bool(receipt["version_id"])
                and receipt.get("source_stop_evidence") == continuation.get("stop_evidence")
                and type(receipt.get("file_count")) is int and receipt["file_count"] >= 0
                and type(receipt.get("total_bytes")) is int and receipt["total_bytes"] >= 0
            )
    # This historical handle is a read-only checkpoint identity, never spawn/probe authority.
    if historical_checkpoint and lease_row.get("provider") == "opensandbox":
        return _sandbox_cleanup.container_lease_from_persisted_row(
            lease_row, allow_historical_internal_test_cleanup=True,
        )
    return _sandbox_cleanup.container_lease_from_persisted_row(lease_row)


async def _collect_workspace_and_convert_result(
    lease_row: dict[str, Any],
    *,
    registry: AdapterRegistry,
    claim_token: str,
    run_diagnostics: RunDiagnosticsService | None = None,
    continuation_ports: ContinuationSettlementPorts | None = None,
    transaction_factory: Any | None = None,
) -> tuple[ExecutorResult, Any, Any]:
    transaction_factory = transaction_factory or transaction
    context, terminal_result, run_payload = _context_and_payload(lease_row)
    diagnostics = terminal_result.get("diagnostics")
    if isinstance(diagnostics, list) and diagnostics:
        async with transaction_factory() as conn:
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
    lease = _reconciliation_runtime_lease(lease_row, request, adapter_name=adapter_name)
    if lease is None:
        raise _permanent_reconciliation_error(
            "executor_reconciliation_runtime_handle_invalid"
        )
    lease = lease.model_copy(update={"workspace_host_path": workspace.workspace_host_path})
    provider = _container_provider_for_lease(lease)
    stage_binding = {
        "scope": {key: getattr(request, key) for key in (
            "tenant_id", "workspace_id", "user_id", "session_id", "run_id", "attempt_id",
        )}, "owner_generation": request.owner_generation, "container_id": lease.container_id,
    } if adapter_name == "claude-agent-worker" else {}
    saved_stage = ((lease_row.get("lease_payload_json") or {}).get("terminal_reconciliation")
                   if adapter_name == "claude-agent-worker" else None)
    if saved_stage is not None and (
        not isinstance(saved_stage, dict)
        or any(saved_stage.get(key) != value for key, value in stage_binding.items())
        or saved_stage.get("schema_version") != "ai-platform.terminal-reconciliation.v1"
    ):
        raise RuntimeError("executor_reconciliation_stage_scope_invalid")
    collection_error: Exception | None = None
    artifact_workspace: Path | None = None
    try:
        raw_response_files = terminal_result.get("response_files", [])
        if not isinstance(raw_response_files, list) or not all(
            isinstance(path, str) for path in raw_response_files
        ):
            raise ValueError("executor response file selection is invalid")
        if saved_stage is not None:
            stored_path = saved_stage.get("artifact_workspace")
            if stored_path is not None:
                expected = Path(workspace.workspace_host_path).parent / ".ai-platform-artifact-snapshot-v1"
                if Path(stored_path) != expected or expected.is_symlink() or not expected.is_dir():
                    raise RuntimeError("executor_reconciliation_snapshot_unavailable")
                artifact_workspace = expected
            terminal_result = saved_stage["terminal_result"]
        else:
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
        if saved_stage is not None:
            raise
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
                    async with transaction_factory() as conn:
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
    continuation = saved_stage.get("continuation") if saved_stage else None
    stage = saved_stage or {
        **stage_binding, "schema_version": "ai-platform.terminal-reconciliation.v1",
        "artifact_workspace": str(artifact_workspace) if artifact_workspace is not None else None,
        "terminal_result": terminal_result,
    }
    if adapter_name == "claude-agent-worker" and saved_stage is None:
        # Persist the bounded trusted snapshot before destroying its live producer.
        await _record_reconciliation_stage(lease_row, claim_token=claim_token, stage=stage)
    if adapter_name == "claude-agent-worker" and continuation is None:
        if continuation_ports is None:
            raise RuntimeError("continuation_settlement_ports_unconfigured")
        async with transaction_factory() as conn:
            current = await sandbox_lease_repository.has_sandbox_executor_reconciliation_claim(
                conn, lease_id=str(lease_row["id"]), claim_token=claim_token,
            )
        if not current:
            raise RuntimeError("executor_reconciliation_claim_lost")
        stopped = await _stop_reconciled_provider(provider, lease)
        if stopped.status not in {"stopped", "not_found"} or stopped.container_id != lease.container_id:
            raise SandboxReconciliationStopError("executor_reconciliation_sandbox_stop_failed")
        continuation = await continuation_ports.prepare(
            request=request, workspace_lease=workspace, stop_result=stopped,
            terminal_result=terminal_result,
        )
        await _record_reconciliation_stage(
            lease_row, claim_token=claim_token, stage={**stage, "continuation": continuation},
        )
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
    if continuation is not None:
        result = replace(result, executor_payload={
            **result.executor_payload, "continuation_settlement": continuation,
        })
    return result, provider, lease


async def repair_quarantined_executor_checkpoint(
    *, scope: dict[str, str], lease_id: str, invocation_id: str, owner_generation: int,
    v4_capabilities: WorkerV4Capabilities, attempt_lifecycle: RunAttemptLifecycleService,
    lifecycle: RunLifecycleService, registry: AdapterRegistry | None = None,
    continuation_ports: ContinuationSettlementPorts | None = None,
    transaction_factory: Any | None = None,
) -> Any:
    """Explicit operator invocation; settle one verified receipt, never re-execute it."""
    from app.bootstrap.conversation_continuation import (
        build_continuation_settlement_ports, verify_checkpoint_workspace,
    )
    from app.runs.api import ExecutionSpec
    from app.kernel.identifiers import assert_safe_id
    from app.kernel.identifiers import assert_safe_principal_user_id

    fields = {"tenant_id", "workspace_id", "user_id", "session_id", "agent_id", "run_id", "attempt_id"}
    if set(scope) != fields or type(owner_generation) is not int or owner_generation < 1:
        raise ReconciliationAuthorityError("checkpoint_repair_scope_invalid")
    for key, value in scope.items():
        (assert_safe_principal_user_id if key == "user_id" else assert_safe_id)(value, key)
    assert_safe_id(lease_id, "lease_id")
    assert_safe_id(invocation_id, "invocation_id")
    factory = transaction_factory or transaction
    ports = continuation_ports or build_continuation_settlement_ports()
    adapters = registry or AdapterRegistry()
    claim_token = uuid.uuid4().hex
    from app.context.api import provider_session_verification_scope
    async with provider_session_verification_scope(factory, tenant_id=scope["tenant_id"], run_id=scope["run_id"]):
        async with factory() as conn:
            @asynccontextmanager
            async def bound_transaction():
                async with conn.transaction():
                    yield conn

            run = await runs_postgres.get_run(conn, tenant_id=scope["tenant_id"], run_id=scope["run_id"], for_update=True)
            if (run is None or run.get("status") != "running"
                or run.get("terminalization_target") != "cancel_requested" or run.get("cancel_requested_at") is None
                or any(run.get(key) != scope[key] for key in ("tenant_id", "workspace_id", "user_id", "session_id", "agent_id"))):
                raise ReconciliationAuthorityError("checkpoint_repair_run_invalid")
            row = await sandbox_lease_repository.get_quarantined_checkpoint_for_repair(conn, scope=scope, lease_id=lease_id)
            if row is None:
                raise ReconciliationAuthorityError("checkpoint_repair_not_eligible")
            context, terminal, payload = _context_and_payload(row)
            if (context.get("adapter_name") != "claude-agent-worker" or payload.owner_generation != owner_generation
                or any(getattr(payload, key) != value for key, value in scope.items())):
                raise ReconciliationAuthorityError("checkpoint_repair_scope_invalid")
            attempt = await attempt_lifecycle.get(conn, tenant_id=scope["tenant_id"], run_id=scope["run_id"], attempt_id=scope["attempt_id"], for_update=True)
            latest = await attempt_lifecycle.get_latest(conn, tenant_id=scope["tenant_id"], run_id=scope["run_id"], for_update=True)
            if (attempt is None or latest is None or latest["id"] != scope["attempt_id"]
                or attempt.get("status") != "cancel_requested" or attempt.get("owner_kind") != "queue_worker"
                or attempt.get("owner_generation") != owner_generation + 1):
                raise ReconciliationAuthorityError("checkpoint_repair_attempt_invalid")
            spec = ExecutionSpec.from_canonical_json(
                attempt["execution_spec_canonical_json"].encode("utf-8"), expected_sha256=attempt["execution_spec_sha256"],
            )
            if spec.to_mapping() != attempt["execution_spec_json"]:
                raise ReconciliationAuthorityError("checkpoint_repair_spec_invalid")
            request = _reconciliation_request(row, payload)
            handle = _reconciliation_runtime_lease(row, request, adapter_name=context["adapter_name"])
            stage = (row.get("lease_payload_json") or {}).get("terminal_reconciliation")
            if handle is None or not isinstance(stage, dict) or stage.get("terminal_result") != terminal:
                raise ReconciliationAuthorityError("checkpoint_repair_receipt_invalid")
            continuation = stage.get("continuation")
            sdk = continuation.get("sdk_settlement") if isinstance(continuation, dict) else None
            original_sdk = terminal.get("sdk_settlement")
            if (terminal.get("status") != "failed" or terminal.get("error_code") != "stream_delivery_rejected"
                or not isinstance(original_sdk, dict)
                or not isinstance(sdk, dict) or sdk.get("error_code") != "mcp_execution_outcome_unknown"
                or any(sdk.get(key) != original_sdk.get(key) for key in (
                    "error_code", "terminal_reason", "input_accepted", "mirror_complete",
                    "received_result", "ack_unknown", "external_effect_unknown",
                ))
                or not isinstance(continuation.get("workspace_receipt"), dict)
                or sdk.get("input_accepted") is not True or sdk.get("mirror_complete") is not True
                or sdk.get("received_result") is not True or sdk.get("ack_unknown") is not False
                or sdk.get("external_effect_unknown") is not True
                or continuation.get("external_effect_unknown") is not True):
                raise ReconciliationAuthorityError("checkpoint_repair_receipt_invalid")
            await verify_checkpoint_workspace(request=request, receipt=continuation["workspace_receipt"])
            proof = await ports.commit(conn, tenant_id=scope["tenant_id"], run_id=scope["run_id"], attempt_id=scope["attempt_id"],
                                       settlement={**continuation, "sdk_settlement": {**sdk, "status": "cancelled"}, "validate_only": True})
            if proof.get("state") != "recovery_candidate" or proof.get("reason_code") != "external_effect_unknown":
                raise ReconciliationAuthorityError("checkpoint_repair_native_proof_invalid")
            claimed = await sandbox_lease_repository.claim_quarantined_checkpoint_for_repair(
                conn, scope=scope, lease_id=lease_id, invocation_id=invocation_id, claim_token=claim_token,
                expected_updated_at=row["updated_at"], expected_stage=stage, writer_generation=owner_generation,
                cancellation_generation=attempt["owner_generation"],
            )
            if claimed is None:
                raise ReconciliationAuthorityError("checkpoint_repair_claim_lost")
            result, provider, lease = await _collect_workspace_and_convert_result(
                claimed, registry=adapters, claim_token=claim_token, continuation_ports=ports,
                transaction_factory=bound_transaction,
            )
            outcome = await reconcile_executor_terminal_result(
                lease_row=claimed, result=result, registry=adapters, worker_id="operator-checkpoint-repair",
                claim_token=claim_token, transaction_factory=bound_transaction, outer_terminal_transaction=True,
                v4_capabilities=v4_capabilities, run_attempt_lifecycle=attempt_lifecycle, run_lifecycle=lifecycle,
            )
            if outcome.status != "cancelled" or outcome.run_id != scope["run_id"]:
                raise ReconciliationAuthorityError("checkpoint_repair_terminal_cas_lost")
    await publish_run_event(v4_capabilities, tenant_id=scope["tenant_id"], run_id=scope["run_id"])
    return outcome


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
            if run is not None and (
                run.get("cancel_requested_at") is not None
                or str(run.get("status") or "") in {"cancel_requested", "cancelled"}
            ):
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
    continuation_ports: ContinuationSettlementPorts | None = None,
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
                stage = (lease_row.get("lease_payload_json") or {}).get("terminal_reconciliation")
                stopped_checkpoint = isinstance(stage, dict) and stage.get("continuation") is not None
                if str(run.get("status") or "") in _TERMINAL_RUN_STATUSES:
                    if stopped_checkpoint:
                        async with transaction() as conn:
                            await finalize_stopped_executor_checkpoint(
                                conn, lease_row=lease_row, claim_token=claim_token,
                                finalize=sandbox_lease_repository.finalize_stopped_executor_checkpoint,
                            )
                        continue
                    context, _terminal_result, run_payload = _context_and_payload(
                        lease_row
                    )
                    request = _reconciliation_request(lease_row, run_payload)
                    lease = _reconciliation_runtime_lease(
                        lease_row, request, adapter_name=str(context.get("adapter_name") or ""),
                    )
                    if lease is None:
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
                        **({"continuation_ports": continuation_ports} if continuation_ports is not None else {}),
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
                stage = (lease_row.get("lease_payload_json") or {}).get("terminal_reconciliation")
                if not (isinstance(stage, dict) and stage.get("continuation") is not None):
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
                        **({"continuation_ports": continuation_ports} if continuation_ports is not None else {}),
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
    continuation_ports: ContinuationSettlementPorts | None = None,
    concurrency: int = 2,
) -> None:
    concurrency = max(int(concurrency), 1)
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
                **({"continuation_ports": continuation_ports} if continuation_ports is not None else {}),
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
            if scan_needed and len(active) < concurrency:
                for _ in range(concurrency - len(active)):
                    active.add(asyncio.create_task(reconcile(), name="executor-terminal-completion"))
                scan_needed = False
                next_scan = loop.time() + _RECONCILIATION_FALLBACK_SECONDS
            timeout = max(0.0, next_scan - loop.time()) if len(active) < concurrency else None
            await asyncio.wait(
                active | {stop_waiter, wake_waiter}, timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
    finally:
        for task in [*active, *owned]:
            task.cancel()
        await asyncio.gather(*active, *owned, return_exceptions=True)
