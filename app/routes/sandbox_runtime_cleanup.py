import asyncio
import uuid
from collections.abc import Callable
from typing import Any

from app.db import transaction
from app.execution_boundary import (
    GOVERNED_EGRESS_PROOF_DEFAULT_KEY_ID,
    GOVERNED_EGRESS_PROOF_LABEL,
    governed_egress_previous_signing_keys,
    governed_egress_proof_label,
    is_governed_egress_proof,
)
from app.platform.postgres import sandbox_leases as sandbox_lease_repository
from app.runtime.sandbox.container_provider import ContainerProvider
from app.runtime.sandbox.contracts import ContainerLease
from app.runtime.sandbox.opensandbox_policy import (
    SANDBOX_SECURITY_PROFILE_GOVERNED,
    SANDBOX_SECURITY_PROFILE_INTERNAL_TEST,
    SANDBOX_SECURITY_PROFILE_LABEL,
    internal_test_orphan_cleanup_expected_labels,
    requested_opensandbox_image,
)
from app.sandbox.infrastructure import leases_postgres as sandbox_leases_postgres
from app.settings import get_settings
from app.streaming.infrastructure import run_events_postgres as streaming_run_events
from app.validation import assert_safe_id

ProviderFactory = Callable[[str | None], ContainerProvider]


class SandboxRuntimeCleanupError(RuntimeError):
    """Raised when one or more active sandbox leases cannot be stopped."""

    def __init__(
        self,
        failures: list[dict[str, str]],
        *,
        stopped_leases: list[dict[str, Any]] | None = None,
        failed_leases: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__("sandbox_runtime_cleanup_failed")
        self.failures = failures
        self.stopped_leases = stopped_leases or []
        self.failed_leases = failed_leases or []


def _sandbox_cleanup_timeout_seconds() -> float:
    return max(
        float(getattr(get_settings(), "sandbox_cleanup_timeout_seconds", 30) or 30),
        0.001,
    )


def _consume_cleanup_stop_task(task: asyncio.Task[Any]) -> None:
    try:
        task.exception()
    except BaseException:
        pass


async def _stop_failed_reconciliation_lease(
    provider: ContainerProvider,
    lease: ContainerLease,
) -> Any:
    stop_task = asyncio.create_task(
        provider.stop(lease, reason="executor_reconciliation_cleanup")
    )
    try:
        return await asyncio.wait_for(
            asyncio.shield(stop_task),
            timeout=_sandbox_cleanup_timeout_seconds(),
        )
    except asyncio.CancelledError:
        stop_task.cancel()
        stop_task.add_done_callback(_consume_cleanup_stop_task)
        raise
    except TimeoutError:
        stop_task.cancel()
        stop_task.add_done_callback(_consume_cleanup_stop_task)
        return None
    except Exception:  # noqa: BLE001 - the claim is released for bounded cleanup retry.
        return None


def container_lease_from_persisted_row(row: dict[str, Any]) -> ContainerLease | None:
    provider = str(row.get("provider") or "fake")
    if provider not in {"fake", "docker", "opensandbox"}:
        return None
    run_id = str(row["run_id"])
    container_id = str(row.get("runtime_container_id") or "").strip()
    container_name = str(row.get("runtime_container_name") or "").strip()
    executor_url = str(row.get("runtime_executor_url") or "").strip()
    workspace_container_path = str(row.get("runtime_workspace_container_path") or "").strip()
    if not (
        container_id
        and container_name
        and executor_url
        and workspace_container_path
        and row.get("runtime_handle_verified_at")
    ):
        return None
    labels: dict[str, str] = {}
    if provider == "docker":
        lease_payload = row.get("lease_payload_json")
        if not isinstance(lease_payload, dict):
            lease_payload = row.get("lease_payload")
        lease_payload = lease_payload if isinstance(lease_payload, dict) else {}
        attempt_id = row.get("attempt_id")
        if attempt_id is None:
            attempt_id = lease_payload.get("attempt_id")
        persisted_labels = lease_payload.get("labels")
        persisted_labels = persisted_labels if isinstance(persisted_labels, dict) else {}
        if attempt_id is None and persisted_labels.get("ai-platform.attempt_id") is not None:
            return None
        if attempt_id is not None:
            try:
                labels["ai-platform.attempt_id"] = assert_safe_id(attempt_id, "attempt_id")
            except (TypeError, ValueError):
                return None
            if any(
                value is not None and value != attempt_id
                for value in (
                    lease_payload.get("attempt_id"),
                    persisted_labels.get("ai-platform.attempt_id"),
                )
            ):
                return None
        native_required = persisted_labels.get("ai-platform.native_tool_required")
        if native_required is not None:
            if native_required not in ("true", "false") or (native_required == "true" and not attempt_id):
                return None
            labels["ai-platform.native_tool_required"] = native_required
    if provider == "opensandbox":
        lease_payload = row.get("lease_payload_json")
        if not isinstance(lease_payload, dict):
            lease_payload = row.get("lease_payload")
        if not isinstance(lease_payload, dict):
            return None
        security_profile = lease_payload.get("security_profile")
        if not isinstance(security_profile, str) or not security_profile:
            persisted_labels = lease_payload.get("labels")
            security_profile = (
                str(persisted_labels.get(SANDBOX_SECURITY_PROFILE_LABEL) or "")
                if isinstance(persisted_labels, dict)
                else ""
            ) or SANDBOX_SECURITY_PROFILE_GOVERNED
        if security_profile == SANDBOX_SECURITY_PROFILE_INTERNAL_TEST:
            persisted = lease_payload.get("labels")
            settings = get_settings()
            if not (
                isinstance(persisted, dict)
                and getattr(settings, "sandbox_security_profile", "") == SANDBOX_SECURITY_PROFILE_INTERNAL_TEST
                and getattr(settings, "deployment_environment", "") == "test"
                and getattr(settings, "sandbox_container_provider", "") == "opensandbox"
                and getattr(settings, "opensandbox_expected_network_mode", "") == "bridge"
            ):
                return None
            try:
                expected_image, expected_digest = requested_opensandbox_image(settings)
                attempt_id = assert_safe_id(str(lease_payload.get("attempt_id") or ""), "attempt_id")
            except ValueError:
                return None
            expected = internal_test_orphan_cleanup_expected_labels(
                {
                    "tenant_id": str(row["tenant_id"]),
                    "workspace_id": str(row["workspace_id"]),
                    "user_id": str(row["user_id"]),
                    "session_id": str(row["session_id"]),
                    "run_id": run_id,
                    "attempt_id": attempt_id,
                    "sandbox_mode": str(row["sandbox_mode"]),
                    "security_profile": SANDBOX_SECURITY_PROFILE_INTERNAL_TEST,
                },
                settings,
            )
            if expected is None:
                return None
            if (
                lease_payload.get("requested_image") != expected_image
                or lease_payload.get("requested_image_digest") != expected_digest
            ):
                return None
            if any(str(persisted.get(key) or "") != value for key, value in expected.items()):
                return None
            labels.update({str(key): str(value) for key, value in persisted.items()})
        elif security_profile != SANDBOX_SECURITY_PROFILE_GOVERNED:
            return None
        else:
            attempt_id = lease_payload.get("attempt_id")
            if not isinstance(attempt_id, str):
                return None
            proof = lease_payload.get("governed_egress_proof")
            try:
                labels["ai-platform.attempt_id"] = assert_safe_id(attempt_id, "attempt_id")
                labels[GOVERNED_EGRESS_PROOF_LABEL] = governed_egress_proof_label(proof)
            except ValueError:
                return None
            settings = get_settings()
            if not is_governed_egress_proof(
                proof,
                provider="opensandbox",
                signing_key=getattr(settings, "sandbox_egress_proof_signing_key", ""),
                signing_key_id=getattr(
                    settings,
                    "sandbox_egress_proof_key_id",
                    GOVERNED_EGRESS_PROOF_DEFAULT_KEY_ID,
                ),
                previous_signing_keys=governed_egress_previous_signing_keys(
                    getattr(settings, "sandbox_egress_proof_previous_keys_json", "")
                ),
                allow_previous_keys=True,
                expected_binding={"attempt_id": attempt_id},
                require_fresh=False,
            ):
                return None
    return ContainerLease(
        container_id=container_id,
        container_name=container_name,
        provider=provider,
        executor_url=executor_url,
        tenant_id=str(row["tenant_id"]),
        workspace_id=str(row["workspace_id"]),
        user_id=str(row["user_id"]),
        session_id=str(row["session_id"]),
        run_id=run_id,
        sandbox_mode=str(row["sandbox_mode"]),
        browser_enabled=bool(row.get("browser_enabled")),
        workspace_host_path="",
        workspace_container_path=workspace_container_path,
        labels=labels,
    )


async def stop_sandbox_leases(
    sandbox_leases: list[dict[str, Any]] | None,
    *,
    reason: str,
    provider_factory: ProviderFactory,
) -> list[dict[str, Any]]:
    """Stop runtime containers for active sandbox lease rows."""
    failures: list[dict[str, str]] = []
    stopped_leases: list[dict[str, Any]] = []
    failed_leases: list[dict[str, Any]] = []
    for row in sandbox_leases or []:
        if (
            row.get("executor_terminal_json") is not None
            and row.get("executor_reconciliation_status") != "finalized"
        ):
            continue
        lease = container_lease_from_persisted_row(row)
        if lease is None:
            failures.append(
                {
                    "container_id": str(row.get("id") or row.get("run_id") or "unknown"),
                    "message": f"Unsupported sandbox provider: {row.get('provider')}",
                }
            )
            failed_leases.append(row)
            continue
        try:
            provider = provider_factory(lease.provider)
            result = await provider.stop(lease, reason=reason)
        except asyncio.CancelledError:
            raise
        except Exception:
            failures.append(
                {
                    "container_id": lease.container_id,
                    "message": "Sandbox provider stop raised an exception",
                }
            )
            failed_leases.append(row)
            continue
        if result.status == "failed":
            failures.append({"container_id": result.container_id, "message": "Sandbox provider stop failed"})
            failed_leases.append(row)
            continue
        stopped_leases.append(row)
    if failures:
        raise SandboxRuntimeCleanupError(
            failures,
            stopped_leases=stopped_leases,
            failed_leases=failed_leases,
        )
    return stopped_leases


async def release_stopped_sandbox_leases_for_cancel(
    conn: Any,
    *,
    tenant_id: str,
    run_id: str,
    reason: str,
    lease_ids: list[str],
    trace_id: str | None = None,
    requested_by_role: str | None = None,
) -> list[dict[str, Any]]:
    """Release leases after their runtime containers have been stopped."""
    if not lease_ids:
        return []
    released_leases = await sandbox_lease_repository.release_stopped_sandbox_leases(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        reason=reason,
        lease_ids=lease_ids,
    )
    for lease in released_leases:
        payload: dict[str, Any] = {
            "visible_to_user": True,
            "lease_id": lease.get("id"),
            "reason": reason,
        }
        if requested_by_role:
            payload["requested_by_role"] = requested_by_role
        await streaming_run_events.append_event(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=lease.get("trace_id") or trace_id,
            event_type="sandbox_lease_released",
            stage="sandbox",
            message="已因取消释放 Sandbox 租约",
            payload=payload,
        )
    return released_leases


def _sandbox_lease_release_message(reason: str) -> str:
    if reason == "expired":
        return "已释放过期 Sandbox 租约"
    if reason in {"cancel_requested", "admin_cancel_requested"}:
        return "已因取消释放 Sandbox 租约"
    return "已释放 Sandbox 租约"


async def release_stopped_sandbox_leases(
    conn: Any,
    *,
    tenant_id: str,
    reason: str,
    lease_ids: list[str],
    trace_id: str | None = None,
) -> list[dict[str, Any]]:
    """Release DB leases only after their runtime stop operation has succeeded."""
    if not lease_ids:
        return []
    released_leases = await sandbox_lease_repository.release_stopped_sandbox_leases(
        conn,
        tenant_id=tenant_id,
        reason=reason,
        lease_ids=lease_ids,
    )
    for lease in released_leases:
        await streaming_run_events.append_event(
            conn,
            tenant_id=tenant_id,
            run_id=str(lease["run_id"]),
            trace_id=lease.get("trace_id") or trace_id,
            event_type="sandbox_lease_released",
            stage="sandbox",
            message=_sandbox_lease_release_message(reason),
            payload={
                "visible_to_user": True,
                "lease_id": lease.get("id"),
                "reason": reason,
            },
        )
    return released_leases


async def cleanup_expired_sandbox_leases(
    conn: Any,
    *,
    tenant_id: str | None = None,
    reason: str = "expired",
) -> list[dict[str, Any]]:
    """Release expired DB-only leases; runtime providers must be stopped first."""
    rows = await sandbox_lease_repository.cleanup_expired_sandbox_leases(
        conn,
        tenant_id=tenant_id,
        reason=reason,
    )
    for lease in rows:
        await streaming_run_events.append_event(
            conn,
            tenant_id=str(lease["tenant_id"]),
            run_id=str(lease["run_id"]),
            trace_id=lease.get("trace_id"),
            event_type="sandbox_lease_released",
            stage="sandbox",
            message="已释放过期 Sandbox 租约",
            payload={
                "visible_to_user": True,
                "lease_id": lease.get("id"),
                "reason": reason,
            },
        )
    return rows


async def cleanup_failed_sandbox_executor_reconciliation_leases(
    *,
    tenant_id: str | None = None,
    provider_factory: ProviderFactory,
    stale_after_seconds: int = 45,
    transaction_factory: Any | None = None,
) -> list[dict[str, Any]]:
    """Retry one verified runtime cleanup without holding DB locks across provider I/O."""

    transaction_factory = transaction_factory or transaction
    claim_token = str(uuid.uuid4())
    async with transaction_factory() as conn:
        claimed = await sandbox_lease_repository.claim_failed_sandbox_executor_reconciliation_cleanups(
            conn,
            claim_token=claim_token,
            tenant_id=tenant_id,
            limit=1,
            stale_after_seconds=stale_after_seconds,
        )
    released: list[dict[str, Any]] = []
    for row in claimed:
        async with transaction_factory() as conn:
            owns_claim = await sandbox_lease_repository.has_failed_sandbox_executor_reconciliation_cleanup_claim(
                conn,
                lease_id=str(row["id"]),
                claim_token=claim_token,
            )
            if not owns_claim:
                continue
            lease = container_lease_from_persisted_row(row)
            if lease is None:
                await sandbox_lease_repository.quarantine_failed_sandbox_executor_reconciliation_cleanup(
                    conn,
                    lease_id=str(row["id"]),
                    claim_token=claim_token,
                    error="executor_reconciliation_runtime_handle_invalid",
                )
                continue
        stop_result = await _stop_failed_reconciliation_lease(
            provider_factory(lease.provider),
            lease,
        )
        if getattr(stop_result, "status", "failed") not in {"stopped", "not_found"}:
            async with transaction_factory() as conn:
                await sandbox_lease_repository.release_failed_sandbox_executor_reconciliation_cleanup_claim(
                    conn,
                    lease_id=str(row["id"]),
                    claim_token=claim_token,
                    error="executor_reconciliation_sandbox_stop_failed",
                )
            continue
        async with transaction_factory() as conn:
            finalized = await sandbox_lease_repository.finalize_failed_sandbox_executor_reconciliation_cleanup(
                conn,
                tenant_id=str(row["tenant_id"]),
                user_id=str(row["user_id"]),
                run_id=str(row["run_id"]),
                lease_id=str(row["id"]),
                claim_token=claim_token,
                reason="executor_reconciliation_cleanup",
            )
            if finalized is None:
                continue
            await streaming_run_events.append_event(
                conn,
                tenant_id=str(finalized["tenant_id"]),
                run_id=str(finalized["run_id"]),
                trace_id=finalized.get("trace_id"),
                event_type="sandbox_lease_released",
                stage="sandbox",
                message="已释放 Sandbox 租约",
                payload={
                    "visible_to_user": True,
                    "lease_id": finalized.get("id"),
                    "reason": "executor_reconciliation_cleanup",
                },
            )
            released.append(finalized)
    return released


async def cleanup_expired_sandbox_runtime_leases(
    *,
    tenant_id: str | None = None,
    reason: str = "expired",
    provider_factory: ProviderFactory,
    limit: int = 100,
    transaction_factory: Any | None = None,
) -> list[dict[str, Any]]:
    """Own the locked cleanup transaction and commit outcomes before reporting failure."""
    transaction_factory = transaction_factory or transaction

    async def release_stopped_with_conn(release_conn: Any, stopped_leases: list[dict[str, Any]]) -> list[dict[str, Any]]:
        released: list[dict[str, Any]] = []
        grouped: dict[str, list[dict[str, Any]]] = {}
        for lease in stopped_leases:
            grouped.setdefault(str(lease["tenant_id"]), []).append(lease)
        for release_tenant_id, tenant_leases in grouped.items():
            released.extend(
                await release_stopped_sandbox_leases(
                    release_conn,
                    tenant_id=release_tenant_id,
                    reason=reason,
                    lease_ids=[str(lease["id"]) for lease in tenant_leases],
                )
            )
        return released

    async def record_partial_failure(conn: Any, exc: SandboxRuntimeCleanupError) -> None:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for lease, failure in zip(exc.failed_leases, exc.failures, strict=True):
            key = (str(lease["tenant_id"]), str(lease["run_id"]))
            subject = grouped.setdefault(
                key,
                {"lease_ids": [], "failures": [], "trace_id": lease.get("trace_id")},
            )
            subject["lease_ids"].append(str(lease["id"]))
            subject["failures"].append(failure)
        if exc.stopped_leases:
            await release_stopped_with_conn(conn, exc.stopped_leases)
        for (failure_tenant_id, failure_run_id), subject in grouped.items():
            await sandbox_leases_postgres.record_sandbox_runtime_cleanup_outcome(
                conn,
                tenant_id=failure_tenant_id,
                run_id=failure_run_id,
                trace_id=subject["trace_id"],
                requested_by_role="maintenance",
                reason=reason,
                status="failed",
                lease_ids=subject["lease_ids"],
                failures=subject["failures"],
            )

    failure: SandboxRuntimeCleanupError | None = None
    async with transaction_factory() as conn:
        expired_leases = await sandbox_lease_repository.list_expired_active_sandbox_leases(
            conn, tenant_id=tenant_id, limit=limit,
        )
        if not expired_leases:
            return []
        try:
            stopped_leases = await stop_sandbox_leases(
                expired_leases, reason=reason, provider_factory=provider_factory,
            )
        except SandboxRuntimeCleanupError as exc:
            await record_partial_failure(conn, exc)
            failure = exc
        else:
            released = await release_stopped_with_conn(conn, stopped_leases)
    if failure is not None:
        raise failure
    return released
