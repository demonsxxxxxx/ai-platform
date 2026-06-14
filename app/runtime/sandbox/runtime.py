import asyncio
import inspect
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from app import repositories
from app.db import transaction
from app.runtime.kernel_contracts import AgentEvent
from app.runtime.sandbox.container_provider import ContainerProvider, create_container_provider
from app.runtime.sandbox.contracts import ContainerLease, ExecutorTaskRequest, SandboxRuntimeRequest, StopResult, WorkspaceLease
from app.runtime.sandbox.callback_tokens import derive_callback_token
from app.runtime.sandbox.event_normalizer import container_started_event
from app.runtime.sandbox.executor_client import SandboxExecutorClient
from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager
from app.settings import get_settings


EventSink = Callable[[AgentEvent], Awaitable[None] | None]
ExecuteTask = Callable[[str, ExecutorTaskRequest], Awaitable[dict[str, Any]]]
TokenResolver = Callable[[str], str]
LeaseRecorder = Callable[[ContainerLease, SandboxRuntimeRequest, WorkspaceLease], Awaitable[Any] | Any]
LeaseReleaser = Callable[..., Awaitable[Any] | Any]


@dataclass(frozen=True)
class SandboxRuntimeResult:
    status: str
    session_id: str
    run_id: str
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

    async def _record_runtime_lease(
        self,
        lease: ContainerLease,
        request: SandboxRuntimeRequest,
        workspace: WorkspaceLease,
    ) -> str | None:
        async with transaction() as conn:
            row = await repositories.create_sandbox_lease(
                conn,
                tenant_id=lease.tenant_id,
                workspace_id=lease.workspace_id,
                user_id=lease.user_id,
                session_id=lease.session_id,
                run_id=lease.run_id,
                trace_id="",
                sandbox_mode=lease.sandbox_mode,
                provider=lease.provider,
                browser_enabled=lease.browser_enabled,
                ttl_seconds=1800,
                resource_limits_json=request.resource_limits,
                user_visible_payload_json=workspace.user_visible_payload(),
                lease_payload_json={},
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

    async def _stop_and_release_ephemeral_lease(
        self,
        lease: ContainerLease,
        *,
        reason: str,
        lease_record_id: str | None,
    ) -> None:
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

    async def submit(
        self,
        request: SandboxRuntimeRequest,
        event_sink: EventSink | None = None,
    ) -> SandboxRuntimeResult:
        total_started_at = time.monotonic()
        workspace = self.workspace_manager.prepare(request)
        lease_started_at = time.monotonic()
        lease = await self.provider.create_or_reuse(request, workspace)
        lease_acquire_latency_ms = self._elapsed_ms(lease_started_at)
        lease_record_id: str | None = None
        try:
            lease_record_id = await self._call_record_lease(lease, request, workspace)
        except BaseException as exc:
            stop_result = await self.provider.stop(lease, reason="lease_record_failed")
            if stop_result.status == "failed":
                raise SandboxRuntimeCleanupError(reason="lease_record_failed", stop_result=stop_result) from exc
            raise
        try:
            await self._emit(event_sink, container_started_event(lease))

            task_request = ExecutorTaskRequest(
                session_id=request.session_id,
                run_id=request.run_id,
                prompt=request.input_message,
                callback_url=request.callback_url,
                callback_token_id=request.callback_token_id,
                callback_token=self.callback_token_resolver(request.callback_token_id),
                callback_base_url=self.settings.sandbox_callback_base_url,
                permission_mode="default",
                config={
                    "model": request.model,
                    "browser_enabled": request.browser_enabled,
                    "resource_limits": request.resource_limits,
                    "skill_ids": request.skill_ids,
                    "mcp_tool_ids": request.mcp_tool_ids,
                    "input_files": request.file_ids,
                },
            )
            dispatch_started_at = time.monotonic()
            response = await self.execute_task(lease.executor_url, task_request)
            sandbox_executor_dispatch_latency_ms = self._elapsed_ms(dispatch_started_at)
        except BaseException as exc:
            if request.sandbox_mode == "ephemeral":
                reason = "dispatch_cancelled" if isinstance(exc, asyncio.CancelledError) else "dispatch_failed"
                try:
                    await self._stop_and_release_ephemeral_lease(lease, reason=reason, lease_record_id=lease_record_id)
                except SandboxRuntimeCleanupError as cleanup_exc:
                    raise cleanup_exc from exc
            raise
        sandbox_cleanup_latency_ms = 0
        if request.sandbox_mode == "ephemeral":
            cleanup_started_at = time.monotonic()
            await self._stop_and_release_ephemeral_lease(
                lease,
                reason="dispatch_completed",
                lease_record_id=lease_record_id,
            )
            sandbox_cleanup_latency_ms = self._elapsed_ms(cleanup_started_at)

        return SandboxRuntimeResult(
            status=str(response.get("status") or "accepted"),
            session_id=request.session_id,
            run_id=request.run_id,
            executor_response=response,
            timings={
                "schema_version": "ai-platform.sandbox-latency-split.v1",
                "sandbox_lease_acquire_latency_ms": lease_acquire_latency_ms,
                "sandbox_container_cold_start_latency_ms": self._timing_value(
                    lease.timings.get("sandbox_container_cold_start_latency_ms")
                ),
                "sandbox_healthcheck_latency_ms": self._timing_value(
                    lease.timings.get("sandbox_healthcheck_latency_ms")
                ),
                "sandbox_executor_dispatch_latency_ms": sandbox_executor_dispatch_latency_ms,
                "executor_model_latency_ms": self._timing_value(response.get("executor_model_latency_ms")),
                "document_processing_latency_ms": self._timing_value(
                    response.get("document_processing_latency_ms")
                ),
                "sandbox_cleanup_latency_ms": sandbox_cleanup_latency_ms,
                "sandbox_total_latency_ms": self._elapsed_ms(total_started_at),
            },
        )
