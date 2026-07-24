import asyncio
import inspect
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from app import repositories
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
    ContainerProvider,
    create_container_provider,
    executor_callback_target,
)
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
from app.runtime.sandbox.executor_client import SandboxExecutorClient
from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager
from app.settings import get_settings


EventSink = Callable[[AgentEvent], Awaitable[None] | None]
ExecuteTask = Callable[..., Awaitable[dict[str, Any]]]
TokenResolver = Callable[[str], str]
LeaseRecorder = Callable[[ContainerLease, SandboxRuntimeRequest, WorkspaceLease], Awaitable[Any] | Any]
LeaseReleaser = Callable[..., Awaitable[Any] | Any]


@dataclass(frozen=True)
class SandboxRuntimeResult:
    status: str
    session_id: str
    run_id: str
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
        self.release_lease = release_lease or self._release_runtime_lease

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
    ) -> str | None:
        result = self.record_lease(lease, request, workspace)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict) and result.get("id"):
            return str(result["id"])
        if isinstance(result, str) and result:
            return result
        return None

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
        authorized_skill_scope = governed_egress_authorized_skill_scope(
            skill_ids=request.skill_ids,
            mcp_tool_ids=request.mcp_tool_ids,
        )
        authorized_native_tool_scope = governed_egress_authorized_native_tool_scope(request.tool_policy_subjects)
        governed_egress_proof = governed_egress_proof_from_labels(
            lease.provider,
            lease.labels,
            signing_key=getattr(get_settings(), "sandbox_egress_proof_signing_key", ""),
            signing_key_id=getattr(get_settings(), "sandbox_egress_proof_key_id", "current"),
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
        if lease.provider in REAL_SANDBOX_PROVIDERS and governed_egress_proof is None:
            raise ValueError("governed_egress_proof_invalid")
        lease_payload = {
            "source": "sandbox_runtime",
            "evidence_class": "runtime_lease_projection",
            "attempt_id": request.attempt_id,
            "container_id": runtime_container_id,
            "container_name": runtime_container_name,
            "executor_url": runtime_executor_url,
            "workspace_host_path": lease.workspace_host_path,
            "workspace_container_path": runtime_workspace_container_path,
            "labels": {
                str(key): str(value)
                for key, value in lease.labels.items()
                if not str(key).startswith(
                    (
                        "ai-platform.executor.",
                        "ai-platform.external_egress.",
                        "ai-platform.governed_egress.",
                    )
                )
            },
        }
        if governed_egress_proof is not None:
            lease_payload["governed_egress_proof"] = governed_egress_proof
            for proof_field in (
                "image_subject_sha256",
                "image_digest_sha256",
                "authorized_skill_scope_sha256",
                "authorized_native_tool_scope_sha256",
            ):
                lease_payload[f"governed_egress_{proof_field}"] = governed_egress_proof[proof_field]
        async with transaction() as conn:
            row = await repositories.create_sandbox_lease(
                conn,
                tenant_id=lease.tenant_id,
                workspace_id=lease.workspace_id,
                user_id=lease.user_id,
                session_id=lease.session_id,
                run_id=lease.run_id,
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
            )
        return str(row.get("id")) if isinstance(row, dict) and row.get("id") else None

    async def _release_runtime_lease(self, lease: ContainerLease, reason: str, lease_record_id: str | None = None) -> None:
        if not lease_record_id:
            return
        async with transaction() as conn:
            await repositories.release_sandbox_lease(
                conn,
                tenant_id=lease.tenant_id,
                user_id=lease.user_id,
                run_id=lease.run_id,
                lease_id=lease_record_id,
                reason=reason,
            )

    async def _stop_and_release_recorded_lease(
        self,
        lease: ContainerLease,
        *,
        reason: str,
        lease_record_id: str | None,
    ) -> None:
        """Confirm provider stop before releasing one recorded runtime lease."""
        stop_result = await self.provider.stop(lease, reason=reason)
        if stop_result.status == "failed":
            raise SandboxRuntimeCleanupError(reason=reason, stop_result=stop_result)
        await self._call_release_lease(lease, reason, lease_record_id)

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
        lease = await self.provider.create_or_reuse(request, workspace)
        if lease.provider != configured_provider:
            trusted_callback_target = self._trusted_callback_target(lease.provider)
        lease_acquire_latency_ms = self._elapsed_ms(lease_started_at)
        lease_record_id: str | None = None
        try:
            lease_record_id = await self._call_record_lease(lease, request, workspace)
        except BaseException as exc:
            stop_result = await self.provider.stop(lease, reason="lease_record_failed")
            if stop_result.status == "failed":
                raise SandboxRuntimeCleanupError(reason="lease_record_failed", stop_result=stop_result) from exc
            raise
        externally_stopped = False
        validation_started = False
        validation_succeeded = False

        async def stop_owned_runtime(reason: str) -> bool:
            nonlocal externally_stopped
            if externally_stopped:
                return True
            stop_result = await self.provider.stop(lease, reason=reason)
            if stop_result.status == "failed":
                return False
            await self._call_release_lease(lease, reason, lease_record_id)
            externally_stopped = True
            return True

        if execution_owner is not None:
            execution_owner.register_stop(stop_owned_runtime)
        try:
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
            }
            if request.context_manifest:
                task_config["context_manifest"] = dict(request.context_manifest)
            if request.context_retrieval_scope is not None:
                task_config["context_retrieval_scope"] = request.context_retrieval_scope.model_dump()

            callback_token_id = self._lease_callback_token_id(lease, attempt_id=request.attempt_id)
            task_request = ExecutorTaskRequest(
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
            response = await self._call_execute_task(lease.executor_url, task_request, lease.executor_headers)
            sandbox_executor_dispatch_latency_ms = self._elapsed_ms(dispatch_started_at)
        except BaseException as exc:
            validation_rejected = validation_started and not validation_succeeded
            if not externally_stopped and (request.sandbox_mode == "ephemeral" or validation_rejected):
                reason = (
                    "dispatch_validation_cancelled"
                    if validation_rejected and isinstance(exc, asyncio.CancelledError)
                    else "dispatch_validation_failed"
                    if validation_rejected
                    else "dispatch_cancelled"
                    if isinstance(exc, asyncio.CancelledError)
                    else "dispatch_failed"
                )
                try:
                    await self._stop_and_release_recorded_lease(lease, reason=reason, lease_record_id=lease_record_id)
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
            await self._stop_and_release_recorded_lease(
                lease,
                reason=release_reason,
                lease_record_id=lease_record_id,
            )
            sandbox_cleanup_latency_ms = self._elapsed_ms(cleanup_started_at)

        return SandboxRuntimeResult(
            status=str(response.get("status") or "accepted"),
            session_id=request.session_id,
            run_id=request.run_id,
            provider=lease.provider,
            executor_response=response,
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
                    response.get("executor_first_token_latency_ms")
                ),
                "executor_tool_call_latency_ms": self._timing_value(
                    response.get("executor_tool_call_latency_ms")
                ),
                "executor_model_latency_ms": self._timing_value(response.get("executor_model_latency_ms")),
                "document_processing_latency_ms": self._timing_value(
                    response.get("document_processing_latency_ms")
                ),
                "artifact_upload_latency_ms": self._timing_value(response.get("artifact_upload_latency_ms")),
                "sandbox_cleanup_latency_ms": sandbox_cleanup_latency_ms,
                "sandbox_total_latency_ms": self._elapsed_ms(total_started_at),
            },
        )
