import asyncio
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.runtime.kernel_contracts import AgentEvent
from app.runtime.sandbox.container_provider import ContainerProvider, create_container_provider
from app.runtime.sandbox.contracts import ExecutorTaskRequest, SandboxRuntimeRequest
from app.runtime.sandbox.callback_tokens import derive_callback_token
from app.runtime.sandbox.event_normalizer import container_started_event
from app.runtime.sandbox.executor_client import SandboxExecutorClient
from app.runtime.sandbox.workspace_manager import SandboxWorkspaceManager
from app.settings import get_settings


EventSink = Callable[[AgentEvent], Awaitable[None] | None]
ExecuteTask = Callable[[str, ExecutorTaskRequest], Awaitable[dict[str, Any]]]
TokenResolver = Callable[[str], str]


@dataclass(frozen=True)
class SandboxRuntimeResult:
    status: str
    session_id: str
    run_id: str
    executor_response: dict[str, Any]


class SandboxRuntime:
    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        provider: ContainerProvider | None = None,
        execute_task: ExecuteTask | None = None,
        callback_token_resolver: TokenResolver | None = None,
    ) -> None:
        self.settings = get_settings()
        self.workspace_manager = SandboxWorkspaceManager(root=workspace_root)
        self.provider = provider or create_container_provider()
        client = SandboxExecutorClient()
        self.execute_task = execute_task or client.execute
        self.callback_token_resolver = callback_token_resolver or (
            lambda token_id: derive_callback_token(self.settings.sandbox_callback_token, token_id)
        )

    async def _emit(self, sink: EventSink | None, event: AgentEvent) -> None:
        if sink is None:
            return
        result = sink(event)
        if inspect.isawaitable(result):
            await result

    async def submit(
        self,
        request: SandboxRuntimeRequest,
        event_sink: EventSink | None = None,
    ) -> SandboxRuntimeResult:
        workspace = self.workspace_manager.prepare(request)
        lease = await self.provider.create_or_reuse(request, workspace)
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
            response = await self.execute_task(lease.executor_url, task_request)
        except BaseException as exc:
            if request.sandbox_mode == "ephemeral":
                reason = "dispatch_cancelled" if isinstance(exc, asyncio.CancelledError) else "dispatch_failed"
                await self.provider.stop(lease, reason=reason)
            raise
        if request.sandbox_mode == "ephemeral":
            await self.provider.stop(lease, reason="dispatch_completed")

        return SandboxRuntimeResult(
            status=str(response.get("status") or "accepted"),
            session_id=request.session_id,
            run_id=request.run_id,
            executor_response=response,
        )
