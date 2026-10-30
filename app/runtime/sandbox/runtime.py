import asyncio
import inspect
import logging
import time
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.execution.api import ContinuationSettlementPorts
from app.db import transaction
from app.mcp.api import MCP_CREDENTIAL_SUBJECTS_LEASE_KEY, mcp_credential_subjects_for_lease
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
from app.runtime.sandbox.contracts import (
    ContainerLease,
    ExecutorTaskRequest,
    ExecutorTerminalResult,
    executor_terminal_receipt_payload,
    normalize_executor_terminal_status,
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
from app.runtime.sandbox.executor_client import SandboxExecutorClient
from app.runtime.sandbox.executor_signals import (
    ExecutorSignalUnavailable,
    publish_executor_terminal_signal,
)
from app.sandbox.api import (
    SandboxExecutorCallbackLeasePort,
    SandboxRuntimeLeasePort,
    normalize_executor_reported_failure,
)
from app.sandbox.api import (
    SandboxDispatchIdentity,
    SandboxDispatchIntentService,
    SandboxDispatchOutcomeUnknown,
    SandboxExecutorHttpError,
)
from app.runtime.sandbox.readiness_evidence import (
    ExecutorReadinessEvidence,
    safe_readiness_evidence_payload,
)
from app.runtime.sandbox.opensandbox_policy import (
    SANDBOX_SECURITY_PROFILE_GOVERNED,
    SANDBOX_SECURITY_PROFILE_INTERNAL_TEST,
    SANDBOX_SECURITY_PROFILE_LABEL,
    active_internal_test_lease_is_authorized,
)
from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager
from app.settings import DIRECT_OPENSANDBOX_NETWORK_NAME, get_settings


EventSink = Callable[[AgentEvent], Awaitable[None] | None]
ExecuteTask = Callable[..., Awaitable[dict[str, Any]]]
ExecutorStatusReader = Callable[..., Awaitable[dict[str, Any]]]
TokenResolver = Callable[[str], str]
LeaseRecorder = Callable[[ContainerLease, SandboxRuntimeRequest, WorkspaceLease], Awaitable[Any] | Any]
LeaseReleaser = Callable[..., Awaitable[Any] | Any]
CreationClaimFactory = Callable[..., AbstractAsyncContextManager[SandboxCreationClaim]]

_logger = logging.getLogger(__name__)
_EXECUTOR_TERMINAL_SIGNAL_TIMEOUT_SECONDS = 1.0


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
    artifact_workspace_path: str | None = None


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
        get_executor_status: ExecutorStatusReader | None = None,
        callback_token_resolver: TokenResolver | None = None,
        lease_port: SandboxRuntimeLeasePort,
        callback_lease_port: SandboxExecutorCallbackLeasePort | None = None,
        dispatch_intents: SandboxDispatchIntentService,
        record_lease: LeaseRecorder | None = None,
        release_lease: LeaseReleaser | None = None,
        creation_claim_factory: CreationClaimFactory = acquire_sandbox_creation_claim,
        continuation_ports: ContinuationSettlementPorts | None = None,
    ) -> None:
        self.settings = get_settings()
        self.workspace_manager = SandboxWorkspaceManager(root=workspace_root)
        self.provider = provider or create_container_provider()
        client = SandboxExecutorClient()
        self.executor_client = client
        self.execute_task = execute_task or client.execute
        self.get_executor_status = get_executor_status or SandboxExecutorClient(
            timeout_seconds=10.0,
        ).get_status
        self.dispatch_intents = dispatch_intents
        self.callback_token_resolver = callback_token_resolver or (
            lambda token_id: derive_callback_token(self.settings.sandbox_callback_token, token_id)
        )
        self.lease_port = lease_port
        self.callback_lease_port = callback_lease_port
        self.record_lease = record_lease or self._record_runtime_lease
        self._uses_default_lease_recorder = record_lease is None
        self.release_lease = release_lease or self._release_runtime_lease
        self.creation_claim_factory = creation_claim_factory
        self.continuation_ports = continuation_ports
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

    async def _probe_dispatch_status(
        self, lease: ContainerLease, request: SandboxRuntimeRequest,
    ) -> dict[str, Any]:
        resolver = getattr(self.provider, "executor_control_endpoint", None)
        if callable(resolver):
            executor_url, executor_headers = await resolver(lease, request)
        else:
            executor_url, executor_headers = lease.executor_url, dict(lease.executor_headers)
        return await self.get_executor_status(
            executor_url,
            run_id=request.run_id,
            attempt_id=request.attempt_id,
            executor_headers=executor_headers,
        )

    async def _record_dispatch_unknown(self, identity: SandboxDispatchIdentity) -> None:
        async with transaction() as conn:
            await self.dispatch_intents.record_outcome(
                conn, identity=identity, outcome="unknown",
            )

    async def _record_dispatch_unknown_best_effort(
        self, identity: SandboxDispatchIdentity,
    ) -> None:
        try:
            await self._record_dispatch_unknown(identity)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.warning(
                "sandbox_dispatch_unknown_observation_unavailable",
                extra={"run_id": identity.run_id, "attempt_id": identity.attempt_id},
            )

    async def _record_dispatch_unknown_after_cancellation(
        self, identity: SandboxDispatchIdentity | None,
    ) -> None:
        if identity is None:
            return
        try:
            await asyncio.shield(self._record_dispatch_unknown(identity))
        except BaseException:
            # Keep the original cancellation as the caller-visible outcome.
            pass

    @staticmethod
    def _status_proves_dispatch(status: dict[str, Any]) -> bool:
        return str(status.get("status") or "").lower() in {
            "accepted", "running", "completed", "succeeded", "failed", "cancelled", "canceled",
        }

    @staticmethod
    def _dispatch_refused(error: BaseException) -> bool:
        status_code = getattr(error, "status_code", None)
        return isinstance(error, SandboxExecutorHttpError) and type(status_code) is int and (
            400 <= status_code < 500 and status_code not in {408, 409, 429}
        )

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
        if lease_security_profile == SANDBOX_SECURITY_PROFILE_INTERNAL_TEST:
            if (
                lease.provider != "opensandbox"
                or lease.labels.get("ai-platform.attempt_id") != request.attempt_id
                or not active_internal_test_lease_is_authorized(lease, settings)
            ):
                raise ValueError("sandbox_security_profile_invalid")
        elif lease_security_profile != SANDBOX_SECURITY_PROFILE_GOVERNED:
            raise ValueError("sandbox_security_profile_invalid")
        authorized_skill_scope = governed_egress_authorized_skill_scope(
            skill_ids=request.skill_ids,
            mcp_tool_ids=request.mcp_tool_ids,
        )
        authorized_native_tool_scope = governed_egress_authorized_native_tool_scope(request.tool_policy_subjects)
        expected_binding: dict[str, object] = {
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
        }
        if lease.provider == "opensandbox":
            expected_binding["network_name"] = getattr(
                settings,
                "opensandbox_expected_network_mode",
                DIRECT_OPENSANDBOX_NETWORK_NAME,
            )
        governed_egress_proof = governed_egress_proof_from_labels(
            lease.provider,
            lease.labels,
            signing_key=getattr(settings, "sandbox_egress_proof_signing_key", ""),
            signing_key_id=getattr(settings, "sandbox_egress_proof_key_id", "current"),
            expected_binding=expected_binding,
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
            persisted_labels.update({
                key: lease.labels[key]
                for key in (
                    "ai-platform.executor.requested_image",
                    "ai-platform.executor.requested_image_digest",
                    "ai-platform.executor.user",
                    "ai-platform.executor.uid",
                    "ai-platform.executor.gid",
                    "ai-platform.executor.identity_evidence",
                )
            })
        lease_payload = {
            "source": "sandbox_runtime",
            "evidence_class": "runtime_lease_projection",
            "security_profile": lease_security_profile,
            "attempt_id": request.attempt_id,
            "owner_generation": request.owner_generation,
            "callback_token_id": self._lease_callback_token_id(
                lease,
                attempt_id=request.attempt_id,
                owner_generation=request.owner_generation,
            ),
            "container_id": runtime_container_id,
            "container_name": runtime_container_name,
            "executor_url": runtime_executor_url,
            "workspace_host_path": lease.workspace_host_path,
            "workspace_container_path": runtime_workspace_container_path,
            "labels": persisted_labels,
        }
        credential_subjects = mcp_credential_subjects_for_lease(
            request.tool_policy_subjects, tenant_id=lease.tenant_id, user_id=lease.user_id,
        )
        if credential_subjects:
            lease_payload[MCP_CREDENTIAL_SUBJECTS_LEASE_KEY] = credential_subjects
        if lease_security_profile == SANDBOX_SECURITY_PROFILE_INTERNAL_TEST:
            lease_payload["internal_test_lease_version"] = "active-v1"
            lease_payload["requested_image"] = image_subject
            lease_payload["requested_image_digest"] = image_digest
        if governed_egress_proof is not None:
            lease_payload["governed_egress_proof"] = governed_egress_proof
            if lease.provider == "opensandbox":
                lease_payload["governed_egress_network_name"] = str(
                    expected_binding["network_name"]
                )
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
                row = await self.lease_port.create_sandbox_lease(
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
                    ttl_seconds=self.settings.sandbox_lease_ttl_seconds,
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
            await self.lease_port.fence_sandbox_lease_release(
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

    def _lease_callback_token_id(
        self, lease: ContainerLease, *, attempt_id: str, owner_generation: int
    ) -> str:
        return callback_token_id_for_binding(
            CallbackTokenBinding(
                run_id=lease.run_id,
                attempt_id=attempt_id,
                owner_generation=owner_generation,
            )
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

    @staticmethod
    def _predispatch_settlement(request: SandboxRuntimeRequest, stop_evidence: str,
                               lease_id: str | None = None) -> dict[str, Any]:
        settlement = {
            "tenant_id": request.tenant_id, "run_id": request.run_id,
            "attempt_id": request.attempt_id, "final_sequence": None,
            "workspace_receipt": None, "stop_evidence": stop_evidence,
            "sdk_settlement": {
                "owner_generation": request.owner_generation, "input_accepted": False,
                "ack_unknown": False, "mirror_complete": False, "received_result": False,
                "terminal_reason": "predispatch_failure",
            },
            "external_effect_unknown": False,
        }
        if lease_id is not None:
            settlement["runtime_lease"] = {
                "lease_id": lease_id, "tenant_id": request.tenant_id, "user_id": request.user_id,
                "run_id": request.run_id, "attempt_id": request.attempt_id,
            }
        return settlement

    async def submit(
        self,
        request: SandboxRuntimeRequest,
        event_sink: EventSink | None = None,
        execution_owner: RunExecutionOwner | None = None,
    ) -> SandboxRuntimeResult:
        total_started_at = time.monotonic()
        configured_provider = str(getattr(self.settings, "sandbox_container_provider", "fake") or "fake")
        try:
            trusted_callback_target = self._trusted_callback_target(configured_provider)
            workspace = self.workspace_manager.prepare(request)
        except Exception as exc:
            if request.provider_epoch_id is not None:
                # This boundary precedes every provider call, including creation.
                exc.predispatch_continuation_settlement = self._predispatch_settlement(
                    request, "provider_not_created",
                )
            raise
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
                lease_record_id = self.lease_port.new_lease_id()
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
                if (stop_result.status not in {"stopped", "not_found"}
                    or stop_result.container_id != lease.container_id):
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
                if request.provider_epoch_id is not None and isinstance(exc, Exception):
                    exc.predispatch_continuation_settlement = self._predispatch_settlement(
                        request, "provider_stopped" if stop_result.status == "stopped" else "provider_not_found",
                    )
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
        artifact_workspace_path: str | None = None
        response: dict[str, Any] = {}
        predispatch_settlement: dict[str, Any] | None = None
        dispatch_sent = False
        observation_handed_off = False
        sandbox_executor_dispatch_latency_ms = 0

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
                artifact_workspace_path=artifact_workspace_path,
            )

        accepted_dispatch = False
        dispatch_identity: SandboxDispatchIdentity | None = None

        async def stop_owned_runtime(reason: str) -> bool:
            """Elect exactly one stop/release owner across runtime cancellation paths."""

            nonlocal externally_stopped, terminal_stop_result, observation_handed_off, predispatch_settlement
            async with terminal_stop_lock:
                if accepted_dispatch or observation_handed_off:
                    # A durable receipt owns all later settlement and cleanup.
                    return True
                if externally_stopped:
                    return True
                if self.continuation_ports is not None and dispatch_sent and (
                    not response or response.get("status") == "accepted"
                ):
                    # A missing dispatch response is not proof that execution never started.
                    try:
                        endpoint, headers = await self.provider.executor_control_endpoint(lease, request)
                        await self.executor_client.cancel(
                            endpoint, run_id=request.run_id, attempt_id=request.attempt_id,
                            executor_headers=headers,
                        )
                        observed = await self.executor_client.get_status(
                            endpoint, run_id=request.run_id, attempt_id=request.attempt_id,
                            executor_headers=headers,
                        )
                        terminal = ExecutorTerminalResult.model_validate(observed.get("terminal_result"))
                        executor_status = normalize_executor_terminal_status(observed.get("status"), terminal.status)
                        if (executor_status is None or observed.get("run_id") != request.run_id
                            or observed.get("attempt_id") != request.attempt_id
                            or terminal.run_id != request.run_id):
                            return False
                        if self.callback_lease_port is None:
                            raise RuntimeError("sandbox_executor_callback_lease_port_required")
                        async with transaction() as conn:
                            await self.callback_lease_port.record_sandbox_executor_terminal(
                                conn, tenant_id=request.tenant_id, run_id=request.run_id,
                                attempt_id=request.attempt_id, lease_id=lease_record_id,
                                executor_status=executor_status,
                                terminal_result=executor_terminal_receipt_payload(
                                    terminal.model_dump(mode="json", exclude_none=True),
                                ),
                            )
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        # The callback/reconciler may already have stopped and released this lease.
                        try:
                            async with transaction() as conn:
                                persisted = await self.lease_port.get_sandbox_executor_terminal_handoff(
                                    conn, tenant_id=request.tenant_id, workspace_id=request.workspace_id,
                                    user_id=request.user_id, session_id=request.session_id,
                                    run_id=request.run_id, attempt_id=request.attempt_id,
                                    lease_id=lease_record_id, owner_generation=request.owner_generation,
                                )
                            if persisted is None:
                                return False
                            terminal = ExecutorTerminalResult.model_validate(persisted["executor_terminal_json"])
                            if (terminal.run_id != request.run_id or normalize_executor_terminal_status(
                                persisted["executor_status"], terminal.status,
                            ) is None):
                                return False
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            return False
                    observation_handed_off = True
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
                if (
                    terminal_stop_result.status not in {"stopped", "not_found"}
                    or terminal_stop_result.container_id != lease.container_id
                ):
                    return False
                if not dispatch_sent and request.provider_epoch_id is not None:
                    predispatch_settlement = self._predispatch_settlement(
                        request,
                        "provider_stopped" if terminal_stop_result.status == "stopped" else "provider_not_found",
                        lease_record_id,
                    )
                    if self.continuation_ports is None:
                        await self._call_release_lease(lease, reason, lease_record_id)
                elif self.continuation_ports is not None and response:
                    response["continuation_settlement"] = await self.continuation_ports.prepare(
                        request=request, workspace_lease=workspace,
                        stop_result=terminal_stop_result, terminal_result=response,
                    )
                    if lease_record_id is not None:
                        response["continuation_settlement"]["runtime_lease"] = {
                            "lease_id": lease_record_id, "tenant_id": request.tenant_id,
                            "user_id": request.user_id, "run_id": request.run_id,
                            "attempt_id": request.attempt_id,
                        }
                else:
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

        async def persist_dispatch_accepted(accepted_result: SandboxRuntimeResult) -> dict[str, Any] | None:
            nonlocal accepted_dispatch
            # The provider has accepted this exact task. Never stop it because a
            # later local transaction or notification fails.
            accepted_dispatch = True
            if not self._uses_default_lease_recorder:
                return None
            if dispatch_identity is None or lease_record_id is None:
                raise RuntimeError("sandbox_dispatch_identity_required")
            async with transaction() as conn:
                dispatch_row = await self.dispatch_intents.record_outcome(
                    conn, identity=dispatch_identity, outcome="accepted",
                )
                if dispatch_row is None or dispatch_row.get("status") not in (None, "accepted"):
                    raise RuntimeError("sandbox_dispatch_intent_missing")
                return await self.lease_port.record_sandbox_executor_accepted(
                    conn,
                    tenant_id=request.tenant_id,
                    run_id=request.run_id,
                    attempt_id=request.attempt_id,
                    lease_id=lease_record_id,
                    reconciliation_context={
                        **request.reconciliation_context,
                        "dispatch_timings": accepted_result.timings,
                    },
                    ttl_seconds=self.settings.sandbox_lease_ttl_seconds,
                )

        try:
            if lease.provider != configured_provider:
                trusted_callback_target = self._trusted_callback_target(lease.provider)
            staging_started = True
            await self.provider.stage_workspace(lease, request, workspace)
            staging_succeeded = True
            await self._emit(event_sink, container_started_event(lease))

            task_config = {
                "model": request.model,
                "thinking_effort": request.thinking_effort,
                "browser_enabled": request.browser_enabled,
                "resource_limits": request.resource_limits,
                "skill_ids": request.skill_ids,
                "public_skill_metadata": request.public_skill_metadata,
                "mcp_tool_ids": request.mcp_tool_ids,
                "tool_policy_subjects": request.tool_policy_subjects,
                "input_files": request.file_ids,
                "materialized_file_names": request.materialized_file_names,
                "provider_session_resume_required": request.provider_session_resume_required,
                "provider_epoch_id": request.provider_epoch_id,
                "operation_receipts_enabled": request.provider_epoch_id is not None,
            }
            recovery_tool_gate = getattr(request, "recovery_tool_gate", None)
            if recovery_tool_gate is not None:
                task_config["recovery_tool_gate"] = recovery_tool_gate
            if request.model_token_limits is not None:
                task_config["model_token_limits"] = request.model_token_limits.model_dump()
            if request.context_manifest:
                task_config["context_manifest"] = dict(request.context_manifest)
            if request.context_retrieval_scope is not None:
                task_config["context_retrieval_scope"] = request.context_retrieval_scope.model_dump()
            if request.system_prompt:
                # The executor treats this as server-owned configuration, never as user input.
                task_config["system_prompt"] = request.system_prompt

            callback_token_id = self._lease_callback_token_id(
                lease,
                attempt_id=request.attempt_id,
                owner_generation=request.owner_generation,
            )
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
            intent_created = True
            if self._uses_default_lease_recorder:
                if lease_record_id is None:
                    raise RuntimeError("sandbox_executor_lease_receipt_required")
                if not request.reconciliation_context:
                    raise RuntimeError("sandbox_executor_reconciliation_context_required")
                if not str(request.reconciliation_context.get("adapter_name") or "").strip():
                    raise RuntimeError("sandbox_executor_reconciliation_adapter_required")
                dispatch_identity = SandboxDispatchIdentity(
                    tenant_id=request.tenant_id,
                    run_id=request.run_id,
                    attempt_id=request.attempt_id,
                    owner_generation=request.owner_generation,
                    lease_id=lease_record_id,
                    provider=lease.provider,
                    provider_task_id=request.attempt_id,
                )
                async with transaction() as conn:
                    dispatch_record, intent_created = await self.dispatch_intents.persist_intent(
                        conn,
                        identity=dispatch_identity,
                        reconciliation_context=request.reconciliation_context,
                    )
            if not intent_created:
                # A persisted intent may have crossed the provider boundary in
                # a prior process. Read the exact original task; never POST it again.
                if str(dispatch_record.get("status") or "") == "accepted":
                    dispatch_sent = True
                    response = {"status": "accepted", "run_id": request.run_id, "attempt_id": request.attempt_id}
                else:
                    if str(dispatch_record.get("status") or "") == "rejected":
                        raise RuntimeError("sandbox_dispatch_previously_rejected")
                    try:
                        observed_status = await self._probe_dispatch_status(lease, request)
                    except asyncio.CancelledError:
                        accepted_dispatch = True
                        await self._record_dispatch_unknown_after_cancellation(dispatch_identity)
                        raise
                    except Exception:
                        observed_status = {}
                    if self._status_proves_dispatch(observed_status):
                        dispatch_sent = True
                        response = {"status": "accepted", "run_id": request.run_id, "attempt_id": request.attempt_id}
                    else:
                        accepted_dispatch = True
                        if self._uses_default_lease_recorder and dispatch_identity is not None:
                            await self._record_dispatch_unknown_best_effort(dispatch_identity)
                        raise SandboxDispatchOutcomeUnknown()
            else:
                dispatch_started_at = time.monotonic()
                # From this point a provider task may exist. Only a typed HTTP
                # refusal below is enough evidence to treat it as undispatched.
                dispatch_sent = True
                try:
                    response = await self._call_execute_task(
                        lease.executor_url,
                        task_request,
                        lease.executor_headers,
                    )
                except BaseException as dispatch_error:
                    if isinstance(dispatch_error, asyncio.CancelledError) and observation_handed_off:
                        # The owner's stop callback committed this exact terminal
                        # receipt before cancelling the outstanding dispatch await.
                        # Keep reconciliation as the settlement owner.
                        sandbox_executor_dispatch_latency_ms = self._elapsed_ms(dispatch_started_at)
                        return build_runtime_result({
                            "status": "accepted", "run_id": request.run_id,
                            "attempt_id": request.attempt_id,
                        })
                    if not self._uses_default_lease_recorder:
                        raise
                    if isinstance(dispatch_error, asyncio.CancelledError):
                        accepted_dispatch = True
                        await self._record_dispatch_unknown_after_cancellation(dispatch_identity)
                        raise
                    try:
                        observed_status = await self._probe_dispatch_status(lease, request)
                    except asyncio.CancelledError:
                        accepted_dispatch = True
                        await self._record_dispatch_unknown_after_cancellation(dispatch_identity)
                        raise
                    except Exception:
                        observed_status = {}
                    if self._status_proves_dispatch(observed_status):
                        response = {"status": "accepted", "run_id": request.run_id, "attempt_id": request.attempt_id}
                    else:
                        refused = self._dispatch_refused(dispatch_error)
                        if refused:
                            # Only a definitive refusal proves the provider task was
                            # not dispatched; all other failures stay unknown.
                            dispatch_sent = False
                        else:
                            accepted_dispatch = True
                        if self._uses_default_lease_recorder and dispatch_identity is not None:
                            if refused:
                                async with transaction() as conn:
                                    await self.dispatch_intents.record_outcome(
                                        conn, identity=dispatch_identity, outcome="rejected",
                                    )
                            else:
                                await self._record_dispatch_unknown_best_effort(dispatch_identity)
                        if not refused:
                            raise SandboxDispatchOutcomeUnknown() from dispatch_error
                        raise
                sandbox_executor_dispatch_latency_ms = self._elapsed_ms(dispatch_started_at)
                if observation_handed_off:
                    return build_runtime_result({"status": "accepted", "run_id": request.run_id,
                                                 "attempt_id": request.attempt_id})
            if str(response.get("status") or "").lower() == "accepted":
                accepted_result = build_runtime_result(response)
                if self._uses_default_lease_recorder:
                    try:
                        accepted_lease = await persist_dispatch_accepted(accepted_result)
                    except asyncio.CancelledError:
                        await self._record_dispatch_unknown_after_cancellation(dispatch_identity)
                        raise
                    except BaseException as accepted_commit_error:
                        # Even when the POST ACK arrived, query the exact task
                        # after a failed local commit before attempting recovery.
                        try:
                            observed_status = await self._probe_dispatch_status(lease, request)
                        except asyncio.CancelledError:
                            await self._record_dispatch_unknown_after_cancellation(dispatch_identity)
                            raise
                        except Exception:
                            observed_status = {}
                        if self._status_proves_dispatch(observed_status):
                            try:
                                accepted_lease = await persist_dispatch_accepted(accepted_result)
                            except asyncio.CancelledError:
                                await self._record_dispatch_unknown_after_cancellation(dispatch_identity)
                                raise
                            except BaseException as recovery_error:
                                if dispatch_identity is not None:
                                    await self._record_dispatch_unknown_best_effort(dispatch_identity)
                                raise SandboxDispatchOutcomeUnknown() from recovery_error
                        else:
                            if dispatch_identity is not None:
                                await self._record_dispatch_unknown_best_effort(dispatch_identity)
                            raise SandboxDispatchOutcomeUnknown() from accepted_commit_error
                    if (
                        accepted_lease
                        and accepted_lease.get("executor_terminal_json") is not None
                        and accepted_lease.get("executor_reconciliation_status") == "pending"
                    ):
                        try:
                            async with asyncio.timeout(_EXECUTOR_TERMINAL_SIGNAL_TIMEOUT_SECONDS):
                                await publish_executor_terminal_signal()
                        except (ExecutorSignalUnavailable, TimeoutError):
                            _logger.warning(
                                "executor_terminal_reconciliation_signal_unavailable",
                                extra={"run_id": request.run_id, "attempt_id": request.attempt_id},
                            )
                return accepted_result
            if self._uses_default_lease_recorder and dispatch_identity is not None and str(
                response.get("status") or ""
            ).lower() in {"running", "completed", "succeeded", "failed", "cancelled", "canceled"}:
                async with transaction() as conn:
                    await self.dispatch_intents.record_outcome(
                        conn, identity=dispatch_identity, outcome="accepted",
                    )
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
                raw_response_files = response.get("response_files", [])
                if not isinstance(raw_response_files, list) or not all(
                    isinstance(path, str) for path in raw_response_files
                ):
                    raise ContainerStartFailedError(
                        "Sandbox response file selection is invalid"
                    )
                collection_started = True
                artifact_workspace = await self.provider.collect_workspace(
                    lease,
                    request,
                    workspace,
                    raw_response_files,
                )
                artifact_workspace_path = (
                    str(artifact_workspace) if artifact_workspace is not None else None
                )
                collection_succeeded = True
        except BaseException as exc:
            if accepted_dispatch:
                # The committed terminal receipt owns settlement even if its wake is cancelled.
                raise
            validation_rejected = validation_started and not validation_succeeded
            staging_rejected = staging_started and not staging_succeeded
            collection_rejected = collection_started and not collection_succeeded
            if not externally_stopped and (
                request.sandbox_mode == "ephemeral"
                or validation_rejected
                or staging_rejected
                or collection_rejected
                or (self.continuation_ports is not None and not dispatch_sent)
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
                if observation_handed_off:
                    return build_runtime_result({"status": "accepted", "run_id": request.run_id,
                                                 "attempt_id": request.attempt_id})
            if predispatch_settlement is not None and isinstance(exc, Exception):
                exc.predispatch_continuation_settlement = predispatch_settlement
            raise
        sandbox_cleanup_latency_ms = 0
        if (request.sandbox_mode == "ephemeral" or self.continuation_ports is not None) and not externally_stopped:
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
