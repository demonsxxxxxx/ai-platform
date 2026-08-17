import asyncio
import inspect
import logging
import time
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.db import transaction
from app.execution_boundary import (
    REAL_SANDBOX_PROVIDERS,
    governed_egress_authorized_native_tool_scope,
    governed_egress_authorized_skill_scope,
    governed_egress_proof_from_labels,
)
from app.executors.base import RunExecutionOwner
from app.runtime.kernel_contracts import AgentEvent
from app.runtime.sandbox.container_provider import (
    ContainerStartFailedError,
    ContainerCleanupFailedError,
    ContainerProvider,
    ExecutorHealthTimeoutError,
    OpenSandboxStartupFailedError,
    SandboxRuntimeError,
    create_container_provider,
    executor_callback_target,
)
from app.runtime.sandbox.creation_claim import (
    SandboxCreationClaim,
    SandboxCreationClaimError,
    SandboxCreationScope,
    acquire_sandbox_creation_claim,
)
from app.platform.postgres import sandbox_leases as sandbox_lease_repository
from app.runtime.sandbox.contracts import (
    ContainerLease,
    ExecutorTaskRequest,
    SandboxRuntimeRequest,
    StopResult,
    WorkspaceLease,
)
from app.runtime.sandbox.callback_tokens import (
    CallbackTokenBinding,
    callback_token_id_for_binding,
    derive_callback_token,
)
from app.runtime.sandbox.event_normalizer import container_started_event
from app.runtime.sandbox.executor_client import (
    SandboxExecutorClient,
    normalize_executor_reported_failure,
)
from app.runtime.sandbox.readiness_evidence import (
    ExecutorReadinessEvidence,
    safe_readiness_evidence_payload,
)
from app.runtime.sandbox.opensandbox_policy import (
    SANDBOX_SECURITY_PROFILE_GOVERNED,
    SANDBOX_SECURITY_PROFILE_INTERNAL_TEST,
    SANDBOX_SECURITY_PROFILE_LABEL,
    OpenSandboxProfileConfigurationError,
    internal_test_orphan_cleanup_expected_labels,
)
from app.skills.execution_profiles import OPEN_SANDBOX_GOVERNED_SDK_EXECUTION_PROFILE
from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager
from app.settings import get_settings


EventSink = Callable[[AgentEvent], Awaitable[None] | None]
ExecuteTask = Callable[..., Awaitable[dict[str, Any]]]
TokenResolver = Callable[[str], str]
LeaseRecorder = Callable[[ContainerLease, SandboxRuntimeRequest, WorkspaceLease], Awaitable[Any] | Any]
LeaseReleaser = Callable[..., Awaitable[Any] | Any]
CreationClaimFactory = Callable[..., AbstractAsyncContextManager[SandboxCreationClaim]]

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SandboxRuntimeResult:
    status: str
    session_id: str
    run_id: str
    attempt_id: str
    lease_id: str
    provider: str
    executor_response: dict[str, Any]
    timings: dict[str, Any]


class SandboxRuntimeCleanupError(RuntimeError):
    """Raised when an ephemeral sandbox container cannot be stopped safely."""

    def __init__(self, *, reason: str, stop_result: StopResult) -> None:
        super().__init__("sandbox_runtime_cleanup_failed")
        self.reason = reason
        self.stop_result = stop_result


class SandboxRuntime:
    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        provider: ContainerProvider | None = None,
        execute_task: ExecuteTask | None = None,
        callback_token_resolver: TokenResolver | None = None,
        record_lease: LeaseRecorder | None = None,
        release_lease: LeaseReleaser | None = None,
        creation_claim_factory: CreationClaimFactory = acquire_sandbox_creation_claim,
    ) -> None:
        self.settings = get_settings()
        self.workspace_manager = SandboxWorkspaceManager(root=workspace_root)
        self.provider = provider or create_container_provider()
        client = SandboxExecutorClient()
        self.execute_task = execute_task or client.execute
        self.callback_token_resolver = callback_token_resolver or (
            lambda token_id: derive_callback_token(self.settings.sandbox_callback_token, token_id)
        )
        self.record_lease = record_lease or self._record_runtime_lease
        self._uses_default_lease_recorder = record_lease is None
        self.release_lease = release_lease or self._release_runtime_lease
        self.creation_claim_factory = creation_claim_factory
        self._lease_record_connections: dict[str, Any] = {}

    async def _emit(self, sink: EventSink | None, event: AgentEvent) -> None:
        if sink is None:
            return
        result = sink(event)
        if inspect.isawaitable(result):
            await result

    async def _call_record_lease(
        self,
        lease: ContainerLease,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
        lease_record_id: str | None = None,
    ) -> str | None:
        if self._uses_default_lease_recorder:
            result = self._record_runtime_lease(
                lease,
                request,
                workspace,
                lease_record_id=lease_record_id,
            )
        else:
            result = self.record_lease(lease, request, workspace)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict) and result.get("id"):
            return str(result["id"])
        if isinstance(result, str) and result:
            return result
        return lease_record_id

    async def _call_release_lease(self, lease: ContainerLease, reason: str, lease_record_id: str | None = None) -> None:
        if len(inspect.signature(self.release_lease).parameters) >= 3:
            result = self.release_lease(lease, reason, lease_record_id)
        else:
            result = self.release_lease(lease, reason)
        if inspect.isawaitable(result):
            await result

    async def _call_execute_task(
        self,
        executor_url: str,
        task_request: ExecutorTaskRequest,
        executor_headers: dict[str, str],
    ) -> dict[str, Any]:
        try:
            parameters = inspect.signature(self.execute_task).parameters.values()
        except (TypeError, ValueError):
            return await self.execute_task(executor_url, task_request)
        accepts_headers = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD or parameter.name == "executor_headers"
            for parameter in parameters
        )
        if accepts_headers:
            return await self.execute_task(
                executor_url,
                task_request,
                executor_headers=dict(executor_headers),
            )
        return await self.execute_task(executor_url, task_request)

    async def _record_runtime_lease(
        self,
        lease: ContainerLease,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
        *,
        lease_record_id: str | None = None,
    ) -> str | None:
        runtime_container_id = str(lease.container_id or "").strip()
        runtime_container_name = str(lease.container_name or "").strip()
        runtime_executor_url = str(lease.executor_url or "").strip()
        runtime_workspace_container_path = str(lease.workspace_container_path or "").strip()
        if not (
            runtime_container_id
            and runtime_container_name
            and runtime_executor_url
            and runtime_workspace_container_path
        ):
            raise ValueError("incomplete_runtime_handle")
        image_subject = str(lease.labels.get("ai-platform.executor.requested_image") or "").strip()
        image_digest = str(lease.labels.get("ai-platform.executor.requested_image_digest") or "").strip()
        settings = get_settings()
        lease_security_profile = str(
            lease.labels.get(SANDBOX_SECURITY_PROFILE_LABEL) or SANDBOX_SECURITY_PROFILE_GOVERNED
        )
        if lease_security_profile not in {
            SANDBOX_SECURITY_PROFILE_GOVERNED,
            SANDBOX_SECURITY_PROFILE_INTERNAL_TEST,
        }:
            raise ValueError("sandbox_security_profile_invalid")
        if lease_security_profile == SANDBOX_SECURITY_PROFILE_INTERNAL_TEST and not (
            lease.provider == "opensandbox"
            and getattr(settings, "sandbox_security_profile", "") == SANDBOX_SECURITY_PROFILE_INTERNAL_TEST
            and getattr(settings, "deployment_environment", "") == "test"
            and getattr(settings, "opensandbox_expected_network_mode", "") == "bridge"
        ):
            raise ValueError("sandbox_security_profile_invalid")
        direct_requested_image = ""
        direct_requested_image_digest = ""
        if lease_security_profile == SANDBOX_SECURITY_PROFILE_INTERNAL_TEST:
            cleanup_scope = {
                "tenant_id": lease.tenant_id,
                "workspace_id": lease.workspace_id,
                "user_id": lease.user_id,
                "session_id": lease.session_id,
                "run_id": lease.run_id,
                "attempt_id": request.attempt_id,
                "sandbox_mode": lease.sandbox_mode,
                "security_profile": SANDBOX_SECURITY_PROFILE_INTERNAL_TEST,
            }
            try:
                raw_expected_labels = internal_test_orphan_cleanup_expected_labels(cleanup_scope, settings) or {}
            except OpenSandboxProfileConfigurationError:
                raise ValueError("sandbox_security_profile_invalid") from None
            if any(str(lease.labels.get(key) or "") != value for key, value in raw_expected_labels.items()):
                raise ValueError("sandbox_security_profile_invalid")
            direct_requested_image = raw_expected_labels["ai-platform.executor.requested_image"]
            direct_requested_image_digest = raw_expected_labels["ai-platform.executor.requested_image_digest"]
        authorized_skill_scope = governed_egress_authorized_skill_scope(
            skill_ids=request.skill_ids,
            mcp_tool_ids=request.mcp_tool_ids,
        )
        authorized_native_tool_scope = governed_egress_authorized_native_tool_scope(request.tool_policy_subjects)
        governed_egress_proof = governed_egress_proof_from_labels(
            lease.provider,
            lease.labels,
            signing_key=getattr(settings, "sandbox_egress_proof_signing_key", ""),
            signing_key_id=getattr(settings, "sandbox_egress_proof_key_id", "current"),
            expected_binding={
                "tenant_id": lease.tenant_id,
                "workspace_id": lease.workspace_id,
                "user_id": lease.user_id,
                "session_id": lease.session_id,
                "run_id": lease.run_id,
                "attempt_id": request.attempt_id,
                "image_subject": image_subject,
                "image_digest": image_digest,
                "authorized_skill_scope": authorized_skill_scope,
                "authorized_native_tool_scope": authorized_native_tool_scope,
                "lease_identity": f"{lease.provider}:{lease.container_name}:{lease.container_id}",
            },
        )
        if (
            lease.provider in REAL_SANDBOX_PROVIDERS
            and lease_security_profile == SANDBOX_SECURITY_PROFILE_GOVERNED
            and governed_egress_proof is None
        ):
            raise ValueError("governed_egress_proof_invalid")
        persisted_labels = {
            str(key): str(value)
            for key, value in lease.labels.items()
            if not str(key).startswith(
                (
                    "ai-platform.executor.",
                    "ai-platform.external_egress.",
                    "ai-platform.governed_egress.",
                )
            )
        }
        if lease_security_profile == SANDBOX_SECURITY_PROFILE_INTERNAL_TEST:
            persisted_labels.update(
                {
                    key: str(lease.labels[key])
                    for key in (
                        "ai-platform.executor.requested_image",
                        "ai-platform.executor.requested_image_digest",
                    )
                    if key in lease.labels
                }
            )
        lease_payload = {
            "source": "sandbox_runtime",
            "evidence_class": "runtime_lease_projection",
            "security_profile": lease_security_profile,
            "attempt_id": request.attempt_id,
            "container_id": runtime_container_id,
            "container_name": runtime_container_name,
            "executor_url": runtime_executor_url,
            "workspace_host_path": lease.workspace_host_path,
            "workspace_container_path": runtime_workspace_container_path,
            "labels": persisted_labels,
        }
        if lease_security_profile == SANDBOX_SECURITY_PROFILE_INTERNAL_TEST:
            lease_payload["requested_image"] = direct_requested_image
            lease_payload["requested_image_digest"] = direct_requested_image_digest
        if governed_egress_proof is not None:
            lease_payload["governed_egress_proof"] = governed_egress_proof
            for proof_field in (
                "image_subject_sha256",
                "image_digest_sha256",
                "authorized_skill_scope_sha256",
                "authorized_native_tool_scope_sha256",
            ):
                lease_payload[f"governed_egress_{proof_field}"] = governed_egress_proof[proof_field]
        try:
            async with transaction() as conn:
                if lease_record_id is not None:
                    self._lease_record_connections[lease_record_id] = conn
                row = await sandbox_lease_repository.create_sandbox_lease(
                    conn,
                    tenant_id=lease.tenant_id,
                    workspace_id=lease.workspace_id,
                    user_id=lease.user_id,
                    session_id=lease.session_id,
                    run_id=lease.run_id,
                    attempt_id=request.attempt_id,
                    trace_id=request.trace_id,
                    sandbox_mode=lease.sandbox_mode,
                    provider=lease.provider,
                    browser_enabled=lease.browser_enabled,
                    ttl_seconds=1800,
                    resource_limits_json=request.resource_limits,
                    user_visible_payload_json=workspace.user_visible_payload(),
                    lease_payload_json=lease_payload,
                    runtime_container_id=runtime_container_id,
                    runtime_container_name=runtime_container_name,
                    runtime_executor_url=runtime_executor_url,
                    runtime_workspace_container_path=runtime_workspace_container_path,
                    lease_id=lease_record_id,
                )
        finally:
            if lease_record_id is not None:
                self._lease_record_connections.pop(lease_record_id, None)
        return str(row.get("id")) if isinstance(row, dict) and row.get("id") else None

    def _force_finish_lease_record(self, lease_record_id: str | None) -> bool:
        if lease_record_id is None:
            return False
        connection = self._lease_record_connections.get(lease_record_id)
        pgconn = getattr(connection, "pgconn", None)
        finish = getattr(pgconn, "finish", None)
        if not callable(finish):
            return False
        finish()
        return True

    async def _release_runtime_lease(self, lease: ContainerLease, reason: str, lease_record_id: str | None = None) -> None:
        if not lease_record_id:
            return
        attempt_id = str(lease.labels.get("ai-platform.attempt_id") or "").strip() or None
        async with transaction() as conn:
            await sandbox_lease_repository.fence_sandbox_lease_release(
                conn,
                tenant_id=lease.tenant_id,
                workspace_id=lease.workspace_id,
                user_id=lease.user_id,
                session_id=lease.session_id,
                run_id=lease.run_id,
                attempt_id=attempt_id,
                lease_id=lease_record_id,
                sandbox_mode=lease.sandbox_mode,
                provider=lease.provider,
                browser_enabled=lease.browser_enabled,
                reason=reason,
            )

    def _elapsed_ms(self, started_at: float) -> int:
        return max(int(round((time.monotonic() - started_at) * 1000)), 0)

    def _timing_value(self, value: object) -> int:
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return max(parsed, 0)

    def _trusted_callback_target(self, provider_name: str):
        return executor_callback_target(self.settings, provider_name)

    def _lease_callback_token_id(self, lease: ContainerLease, *, attempt_id: str) -> str:
        return callback_token_id_for_binding(
            CallbackTokenBinding(run_id=lease.run_id, attempt_id=attempt_id)
        )

    @staticmethod
    def _readiness_failure_event(
        request: SandboxRuntimeRequest,
        evidence: ExecutorReadinessEvidence,
    ) -> AgentEvent:
        return AgentEvent(
            type="sandbox_executor_readiness_failed",
            message="Sandbox executor readiness failed",
            admin_only=True,
            payload={
                "schema_version": "ai-platform.executor-readiness-evidence.v1",
                "run_id": request.run_id,
                "attempt_id": request.attempt_id,
                **safe_readiness_evidence_payload(evidence),
            },
        )

    @staticmethod
    def _log_opensandbox_startup_evidence(
        request: SandboxRuntimeRequest,
        evidence: dict[str, str | None],
    ) -> None:
        _logger.error(
            "OpenSandbox startup failed",
            extra={
                "run_id": request.run_id,
                "attempt_id": request.attempt_id,
                "provider": evidence["provider"],
                "startup_stage": evidence["startup_stage"],
                "sdk_error_code": evidence["sdk_error_code"],
                "request_id": evidence["request_id"],
            },
        )

    async def submit(
        self,
        request: SandboxRuntimeRequest,
        event_sink: EventSink | None = None,
        execution_owner: RunExecutionOwner | None = None,
    ) -> SandboxRuntimeResult:
        total_started_at = time.monotonic()
        configured_provider = str(getattr(self.settings, "sandbox_container_provider", "fake") or "fake")
        trusted_callback_target = self._trusted_callback_target(configured_provider)
        workspace = self.workspace_manager.prepare(request)
        lease_started_at = time.monotonic()
        lease_record_id: str | None = None
        lease: ContainerLease | None = None
        lease_record_uncertain = False
        cleanup_timeout_seconds = max(
            float(
                getattr(
                    self.settings,
                    "sandbox_cleanup_timeout_seconds",
                    30,
                )
                or 30
            ),
            0.001,
        )

        def consume_background_task(task: asyncio.Task[Any]) -> None:
            try:
                task.exception()
            except BaseException:
                pass

        async def await_bounded_task(task: asyncio.Task[Any]) -> Any:
            cancellation: asyncio.CancelledError | None = None
            loop = asyncio.get_running_loop()
            deadline = loop.time() + cleanup_timeout_seconds
            while not task.done():
                remaining = deadline - loop.time()
                if remaining <= 0:
                    task.cancel()
                    task.add_done_callback(consume_background_task)
                    raise TimeoutError("sandbox cleanup timed out")
                try:
                    await asyncio.wait_for(
                        asyncio.shield(task),
                        timeout=remaining,
                    )
                except asyncio.CancelledError as exc:
                    if cancellation is None:
                        cancellation = exc
                except TimeoutError:
                    task.cancel()
                    task.add_done_callback(consume_background_task)
                    raise TimeoutError("sandbox cleanup timed out") from None
            result = task.result()
            if cancellation is not None:
                raise cancellation
            return result

        async def create_and_record_lease() -> ContainerLease:
            nonlocal lease, lease_record_id, lease_record_uncertain
            lease = await self.provider.create_or_reuse(request, workspace)
            if self._uses_default_lease_recorder:
                lease_record_id = sandbox_lease_repository.new_lease_id()
            record_task = asyncio.create_task(
                self._call_record_lease(
                    lease,
                    request,
                    workspace,
                    lease_record_id,
                )
            )
            try:
                lease_record_id = await asyncio.shield(record_task)
            except asyncio.CancelledError:
                try:
                    lease_record_id = await await_bounded_task(record_task)
                except TimeoutError:
                    lease_record_uncertain = True
                    record_task.add_done_callback(consume_background_task)
                    force_finished = (
                        self._uses_default_lease_recorder
                        and self._force_finish_lease_record(lease_record_id)
                    )
                    if force_finished and not record_task.done():
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(record_task),
                                timeout=cleanup_timeout_seconds,
                            )
                        except BaseException:
                            pass
                except Exception:
                    pass
                raise
            except BaseException as exc:
                try:
                    stop_result = await self.provider.stop(
                        lease,
                        reason="lease_record_failed",
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    stop_result = StopResult(
                        container_id=lease.container_id,
                        status="failed",
                        message="sandbox provider stop raised",
                    )
                if stop_result.status == "failed":
                    raise SandboxRuntimeCleanupError(reason="lease_record_failed", stop_result=stop_result) from exc
                if lease_record_id is not None:
                    try:
                        await self._call_release_lease(
                            lease,
                            "lease_record_failed",
                            lease_record_id,
                        )
                    except Exception as release_exc:
                        raise SandboxRuntimeCleanupError(
                            reason="lease_record_failed",
                            stop_result=StopResult(
                                container_id=lease.container_id,
                                status="failed",
                                message="sandbox lease release fence failed",
                            ),
                        ) from release_exc
                raise
            return lease

        async def compensate_created_lease(reason: str) -> None:
            if lease is None:
                return
            try:
                stop_result = await self.provider.stop(lease, reason=reason)
            except Exception:
                stop_result = StopResult(
                    container_id=lease.container_id,
                    status="failed",
                    message="sandbox provider stop raised",
                )
            if stop_result.status == "failed":
                raise SandboxRuntimeCleanupError(
                    reason=reason,
                    stop_result=stop_result,
                )
            try:
                await self._call_release_lease(
                    lease,
                    reason,
                    lease_record_id,
                )
            except Exception as release_exc:
                raise SandboxRuntimeCleanupError(
                    reason=reason,
                    stop_result=StopResult(
                        container_id=lease.container_id,
                        status="failed",
                        message="sandbox lease release failed",
                    ),
                ) from release_exc
            if lease_record_uncertain and lease_record_id is None:
                raise SandboxRuntimeCleanupError(
                    reason=reason,
                    stop_result=StopResult(
                        container_id=lease.container_id,
                        status="failed",
                        message="sandbox lease persistence outcome is uncertain",
                    ),
                )

        async def run_compensation(reason: str) -> None:
            cleanup_task = asyncio.create_task(compensate_created_lease(reason))
            try:
                await await_bounded_task(cleanup_task)
            except TimeoutError as exc:
                raise SandboxRuntimeCleanupError(
                    reason=reason,
                    stop_result=StopResult(
                        container_id=lease.container_id if lease is not None else "unknown",
                        status="failed",
                        message="sandbox cleanup timed out",
                    ),
                ) from exc

        try:
            claim_provider = str(
                getattr(self.provider, "provider_name", "") or ""
            ).strip().lower()
            if claim_provider in REAL_SANDBOX_PROVIDERS:
                claim_scope = SandboxCreationScope(
                    provider=claim_provider,
                    tenant_id=request.tenant_id,
                    workspace_id=request.workspace_id,
                    user_id=request.user_id,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    attempt_id=request.attempt_id,
                )
                timeout_seconds = max(
                    float(getattr(self.settings, "sandbox_container_start_timeout_seconds", 30) or 30),
                    0.001,
                )
                async with self.creation_claim_factory(claim_scope, timeout_seconds=timeout_seconds) as claim:
                    if claim.active_lease_exists:
                        raise ContainerStartFailedError(
                            "Sandbox exact-attempt lease is already active"
                        )
                    lease = await create_and_record_lease()
            else:
                lease = await create_and_record_lease()
        except asyncio.CancelledError as exc:
            if lease is not None:
                try:
                    await run_compensation("creation_claim_cancelled")
                except SandboxRuntimeCleanupError as cleanup_exc:
                    raise cleanup_exc from exc
            raise
        except SandboxCreationClaimError as exc:
            await run_compensation("creation_claim_release_failed")
            raise ContainerStartFailedError("Sandbox creation claim is unavailable") from exc
        except OpenSandboxStartupFailedError as exc:
            self._log_opensandbox_startup_evidence(request, exc.private_evidence)
            raise
        except (ContainerCleanupFailedError, ExecutorHealthTimeoutError) as exc:
            startup_evidence = exc.opensandbox_startup_evidence
            if startup_evidence is not None:
                self._log_opensandbox_startup_evidence(request, startup_evidence.private_payload())
            evidence = exc.readiness_evidence
            if isinstance(evidence, ExecutorReadinessEvidence):
                try:
                    await self._emit(event_sink, self._readiness_failure_event(request, evidence))
                except Exception:
                    pass
            raise
        except SandboxRuntimeError as exc:
            startup_evidence = exc.opensandbox_startup_evidence
            if startup_evidence is not None:
                self._log_opensandbox_startup_evidence(request, startup_evidence.private_payload())
            raise
        if lease.provider != configured_provider:
            trusted_callback_target = self._trusted_callback_target(lease.provider)
        lease_acquire_latency_ms = self._elapsed_ms(lease_started_at)
        externally_stopped = False
        terminal_stop_result: StopResult | None = None
        terminal_stop_lock = asyncio.Lock()
        validation_started = False
        validation_succeeded = False
        staging_started = False
        staging_succeeded = False
        collection_started = False
        collection_succeeded = False

        def build_runtime_result(
            response_payload: dict[str, Any],
            *,
            cleanup_latency_ms: int = 0,
        ) -> SandboxRuntimeResult:
            return SandboxRuntimeResult(
                status=str(response_payload.get("status") or "accepted"),
                session_id=request.session_id,
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                lease_id=str(lease_record_id or ""),
                provider=lease.provider,
                executor_response=response_payload,
                timings={
                    "schema_version": "ai-platform.sandbox-latency-split.v1",
                    "sandbox_queue_wait_latency_ms": self._timing_value(request.queue_wait_ms),
                    "sandbox_lease_acquire_latency_ms": lease_acquire_latency_ms,
                    "sandbox_container_start_latency_ms": self._timing_value(
                        lease.timings.get("sandbox_container_start_latency_ms")
                        or lease.timings.get("sandbox_container_cold_start_latency_ms")
                    ),
                    "sandbox_container_cold_start_latency_ms": self._timing_value(
                        lease.timings.get("sandbox_container_cold_start_latency_ms")
                    ),
                    "sandbox_healthcheck_latency_ms": self._timing_value(
                        lease.timings.get("sandbox_healthcheck_latency_ms")
                    ),
                    "sandbox_executor_dispatch_latency_ms": sandbox_executor_dispatch_latency_ms,
                    "executor_first_token_latency_ms": self._timing_value(
                        response_payload.get("executor_first_token_latency_ms")
                    ),
                    "executor_tool_call_latency_ms": self._timing_value(
                        response_payload.get("executor_tool_call_latency_ms")
                    ),
                    "executor_model_latency_ms": self._timing_value(
                        response_payload.get("executor_model_latency_ms")
                    ),
                    "document_processing_latency_ms": self._timing_value(
                        response_payload.get("document_processing_latency_ms")
                    ),
                    "artifact_upload_latency_ms": self._timing_value(
                        response_payload.get("artifact_upload_latency_ms")
                    ),
                    "sandbox_cleanup_latency_ms": cleanup_latency_ms,
                    "sandbox_total_latency_ms": self._elapsed_ms(total_started_at),
                },
            )

        async def stop_owned_runtime(reason: str) -> bool:
            """Elect exactly one stop/release owner across runtime cancellation paths."""

            nonlocal externally_stopped, terminal_stop_result
            async with terminal_stop_lock:
                if externally_stopped:
                    return True
                try:
                    terminal_stop_result = await self.provider.stop(lease, reason=reason)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    terminal_stop_result = StopResult(
                        container_id=lease.container_id,
                        status="failed",
                        message="sandbox provider stop raised",
                    )
                if terminal_stop_result.status == "failed":
                    return False
                await self._call_release_lease(lease, reason, lease_record_id)
                externally_stopped = True
                return True

        async def stop_and_release_owned(reason: str) -> None:
            if await stop_owned_runtime(reason):
                return
            raise SandboxRuntimeCleanupError(
                reason=reason,
                stop_result=terminal_stop_result
                or StopResult(container_id=lease.container_id, status="failed", message="sandbox stop failed"),
            )

        if execution_owner is not None:
            execution_owner.register_stop(stop_owned_runtime)
        try:
            staging_started = True
            await self.provider.stage_workspace(lease, request, workspace)
            staging_succeeded = True
            await self._emit(event_sink, container_started_event(lease))

            task_config = {
                "model": request.model,
                "browser_enabled": request.browser_enabled,
                "resource_limits": request.resource_limits,
                "skill_ids": request.skill_ids,
                "mcp_tool_ids": request.mcp_tool_ids,
                "tool_policy_subjects": request.tool_policy_subjects,
                "input_files": request.file_ids,
                "materialized_file_names": request.materialized_file_names,
                "require_selected_skill_invocation": request.require_selected_skill_invocation,
            }
            if lease.provider == "opensandbox":
                task_config["sdk_execution_profile"] = OPEN_SANDBOX_GOVERNED_SDK_EXECUTION_PROFILE
            if request.context_manifest:
                task_config["context_manifest"] = dict(request.context_manifest)
            if request.context_retrieval_scope is not None:
                task_config["context_retrieval_scope"] = request.context_retrieval_scope.model_dump()
            if request.system_prompt:
                # The executor treats this as server-owned configuration, never as user input.
                task_config["system_prompt"] = request.system_prompt

            callback_token_id = self._lease_callback_token_id(lease, attempt_id=request.attempt_id)
            task_request = ExecutorTaskRequest(
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=request.run_id,
                attempt_id=request.attempt_id,
                prompt=request.input_message,
                callback_url=trusted_callback_target.callback_url,
                callback_token_id=callback_token_id,
                callback_token=self.callback_token_resolver(callback_token_id),
                callback_base_url=trusted_callback_target.base_url,
                sdk_session_id=request.sdk_session_id,
                permission_mode="default",
                governed_permission_wait=request.governed_permission_wait,
                config=task_config,
            )
            validation_started = True
            await self.provider.validate_for_dispatch(lease, request, workspace)
            validation_succeeded = True
            dispatch_started_at = time.monotonic()
            response = await self._call_execute_task(
                lease.executor_url,
                task_request,
                lease.executor_headers,
            )
            sandbox_executor_dispatch_latency_ms = self._elapsed_ms(dispatch_started_at)
            if str(response.get("status") or "").lower() == "accepted":
                if lease_record_id is None and self._uses_default_lease_recorder:
                    raise RuntimeError("sandbox_executor_lease_receipt_required")
                if self._uses_default_lease_recorder:
                    async with transaction() as conn:
                        accepted = await sandbox_lease_repository.record_sandbox_executor_heartbeat(
                            conn,
                            tenant_id=request.tenant_id,
                            run_id=request.run_id,
                            attempt_id=request.attempt_id,
                            lease_id=lease_record_id,
                            executor_status="accepted",
                        )
                    if accepted is None:
                        raise RuntimeError("sandbox_executor_attempt_inactive")
                    if not request.reconciliation_context:
                        raise RuntimeError("sandbox_executor_reconciliation_context_required")
                    await sandbox_lease_repository.record_sandbox_executor_reconciliation_context(
                        conn,
                        tenant_id=request.tenant_id,
                        run_id=request.run_id,
                        attempt_id=request.attempt_id,
                        lease_id=lease_record_id,
                        context=request.reconciliation_context,
                    )
                return build_runtime_result(response)
            response = normalize_executor_reported_failure(
                response,
                expected_run_id=request.run_id,
            )
            cleanup_timed_out = (
                str(response.get("status") or "") == "failed"
                and str(response.get("error_code") or "") == "executor_cleanup_timeout"
            )
            if cleanup_timed_out:
                await stop_and_release_owned("executor_cleanup_timeout")
            else:
                collection_started = True
                await self.provider.collect_workspace(lease, request, workspace)
                collection_succeeded = True
        except BaseException as exc:
            validation_rejected = validation_started and not validation_succeeded
            staging_rejected = staging_started and not staging_succeeded
            collection_rejected = collection_started and not collection_succeeded
            if not externally_stopped and (
                request.sandbox_mode == "ephemeral"
                or validation_rejected
                or staging_rejected
                or collection_rejected
            ):
                reason = (
                    "workspace_stage_cancelled"
                    if staging_rejected and isinstance(exc, asyncio.CancelledError)
                    else "workspace_stage_failed"
                    if staging_rejected
                    else "workspace_collect_cancelled"
                    if collection_rejected and isinstance(exc, asyncio.CancelledError)
                    else "workspace_collect_failed"
                    if collection_rejected
                    else
                    "dispatch_validation_cancelled"
                    if validation_rejected and isinstance(exc, asyncio.CancelledError)
                    else "dispatch_validation_failed"
                    if validation_rejected
                    else "dispatch_cancelled"
                    if isinstance(exc, asyncio.CancelledError)
                    else "dispatch_failed"
                )
                try:
                    await stop_and_release_owned(reason)
                except SandboxRuntimeCleanupError as cleanup_exc:
                    raise cleanup_exc from exc
            raise
        sandbox_cleanup_latency_ms = 0
        if request.sandbox_mode == "ephemeral" and not externally_stopped:
            cleanup_started_at = time.monotonic()
            terminal_status = str(response.get("status") or "")
            release_reason = (
                "run_failed"
                if terminal_status == "failed"
                else "run_cancelled"
                if terminal_status in {"cancelled", "canceled"}
                else "dispatch_completed"
            )
            await stop_and_release_owned(release_reason)
            sandbox_cleanup_latency_ms = self._elapsed_ms(cleanup_started_at)

        return build_runtime_result(
            response,
            cleanup_latency_ms=sandbox_cleanup_latency_ms,
        )
