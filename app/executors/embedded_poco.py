import hashlib
import inspect
from typing import Any

from app.executors.base import ExecutorEventSink, ExecutorResult, RunPayload
from app.runtime.embedded_poco_kernel import (
    AgentStepExecutionContext,
    ClaudeAgentRoleRunner,
    DeterministicRoleRunner,
    InProcessEmbeddedPocoKernel,
    RoleResult,
)
from app.runtime.event_bridge import agent_event_to_executor_event
from app.runtime.kernel_contracts import RunContext
from app.runtime.sandbox.contracts import SandboxRuntimeRequest


class EmbeddedPocoAdapter:
    def __init__(
        self,
        kernel: InProcessEmbeddedPocoKernel | None = None,
        sandbox_runtime=None,
    ) -> None:
        self._kernel = kernel
        self._sandbox_runtime = sandbox_runtime

    async def submit_run(self, payload: RunPayload, event_sink: ExecutorEventSink | None = None) -> ExecutorResult:
        try:
            context = build_run_context(payload)
        except ValueError as exc:
            return _failed_result("missing_user_id", str(exc))

        async def bridge_event(agent_event):
            if event_sink is None:
                return
            converted = agent_event_to_executor_event(agent_event)
            result = event_sink(**converted)
            if inspect.isawaitable(result):
                await result

        if context.sandbox_mode in {"ephemeral", "persistent"} and context.metadata.get("execution_mode") != "multi_agent":
            runtime = self._sandbox_runtime
            if runtime is None:
                from app.runtime.sandbox.runtime import SandboxRuntime

                runtime = SandboxRuntime()

            await runtime.submit(
                build_sandbox_request(context, attempt_id=payload.attempt_id),
                bridge_event,
            )
            return ExecutorResult(
                status="succeeded",
                adapter_version="embedded-poco-adapter/0.2.0",
                executor_type="embedded-poco-kernel",
                executor_version="sandbox-runtime/0.1.0",
                capabilities={
                    "streaming": True,
                    "tools": False,
                    "artifacts": False,
                    "sandbox": True,
                    "multi_agent": context.metadata.get("execution_mode") == "multi_agent",
                },
                result={"message": "Sandbox run accepted", "sandbox_mode": context.sandbox_mode},
            )

        kernel = self._kernel or _kernel_for_context(context, sandbox_runtime=self._sandbox_runtime)
        events = await kernel.submit_run(context, bridge_event)
        capabilities = _capabilities_for_context(context)
        failed = next((event for event in reversed(events) if event.type == "run_failed"), None)
        if failed is not None:
            return _failed_result(
                str(failed.payload.get("error_code") or "embedded_kernel_failed"),
                failed.message or "Embedded kernel failed",
                capabilities=capabilities,
            )
        message = _message_from_events(events)
        return ExecutorResult(
            status="succeeded",
            adapter_version="embedded-poco-adapter/0.1.0",
            executor_type="embedded-poco-kernel",
            executor_version="in-process/0.1.0",
            capabilities=capabilities,
            result={"message": message},
        )


def build_run_context(payload: RunPayload) -> RunContext:
    if not payload.user_id:
        raise ValueError("user_id is required for embedded Poco runtime")
    input_payload: dict[str, Any] = payload.input or {}
    resource_limits = {
        "max_seconds": int(input_payload.get("max_seconds") or 120),
        "max_files": int(input_payload.get("max_files") or 20),
        "max_artifact_bytes": int(input_payload.get("max_artifact_bytes") or 100_000_000),
        "max_tool_calls": int(input_payload.get("max_tool_calls") or 20),
    }
    if input_payload.get("max_parallel_steps") is not None:
        resource_limits["max_parallel_steps"] = int(input_payload["max_parallel_steps"])
    metadata = {
        "executor_type": "embedded-poco-kernel",
        "authoritative_attempt_id": payload.attempt_id,
        "agent_id": payload.agent_id,
        "skill_id": payload.skill_id,
        "file_count": len(payload.file_ids),
        "execution_mode": str(input_payload.get("execution_mode") or "single_agent"),
        "multi_agent_role_runner": str(input_payload.get("multi_agent_role_runner") or "deterministic"),
        "multi_agent_roles": list(input_payload.get("multi_agent_roles") or []),
        "multi_agent_steps": list(input_payload.get("multi_agent_steps") or []),
    }
    if isinstance(input_payload.get("resume"), dict):
        metadata["resume"] = dict(input_payload["resume"])
    return RunContext.model_validate(
        {
            "tenant_id": payload.tenant_id,
            "workspace_id": payload.workspace_id,
            "user_id": payload.user_id,
            "session_id": payload.session_id,
            "run_id": payload.run_id,
            "agent_id": payload.agent_id,
            "skill_ids": _run_skill_ids(payload.skill_id, input_payload.get("skill_ids")),
            "mcp_tool_ids": list(input_payload.get("mcp_tool_ids") or []),
            "model": str(input_payload.get("model") or "deepseek-v4-flash"),
            "model_gateway": "new-api",
            "input_message": str(input_payload.get("message") or input_payload.get("prompt") or ""),
            "file_ids": payload.file_ids,
            "sandbox_mode": str(input_payload.get("sandbox_mode") or "none"),
            "browser_enabled": bool(input_payload.get("browser_enabled") or False),
            "permissions": ["chat.respond"],
            "resource_limits": resource_limits,
            "metadata": metadata,
        }
    )


def _run_skill_ids(primary_skill_id: str, raw_skill_ids: object) -> list[str]:
    skill_ids = [primary_skill_id]
    if isinstance(raw_skill_ids, list):
        skill_ids.extend(str(item) for item in raw_skill_ids)
    return list(dict.fromkeys(skill_ids))


class SandboxAwareRoleRunner:
    def __init__(self, *, base_runner, sandbox_runtime=None) -> None:
        self._base_runner = base_runner
        self._sandbox_runtime = sandbox_runtime

    async def run_role(
        self,
        *,
        role: str,
        context: RunContext,
        previous_outputs: list[str],
        step: AgentStepExecutionContext,
        emit_delta=None,
    ) -> RoleResult:
        if step.sandbox_mode not in {"ephemeral", "persistent"}:
            result = await self._base_runner.run_role(
                role=role,
                context=context,
                previous_outputs=previous_outputs,
                step=step,
                emit_delta=emit_delta,
            )
            return result if isinstance(result, RoleResult) else RoleResult(output=str(result))

        runtime = self._sandbox_runtime
        if runtime is None:
            from app.runtime.sandbox.runtime import SandboxRuntime

            runtime = SandboxRuntime()

        async def bridge_sandbox_event(agent_event):
            if emit_delta is None or agent_event.type != "assistant_delta":
                return
            delta = str(agent_event.payload.get("delta") or agent_event.message or "")
            if not delta:
                return
            result = emit_delta(delta)
            if inspect.isawaitable(result):
                await result

        sandbox_result = await runtime.submit(
            build_step_sandbox_request(context=context, step=step),
            bridge_sandbox_event,
        )
        return RoleResult(
            output="Sandbox step accepted",
            metadata={
                "runner": "sandbox_runtime",
                "sandbox_status": sandbox_result.status,
                "executor_response": sandbox_result.executor_response,
                "step_key": step.step_key,
                "sandbox_mode": step.sandbox_mode,
            },
        )


def _kernel_for_context(context: RunContext, sandbox_runtime=None) -> InProcessEmbeddedPocoKernel:
    if context.metadata.get("execution_mode") != "multi_agent":
        return InProcessEmbeddedPocoKernel()

    if (
        context.metadata.get("multi_agent_role_runner") == "claude_agent_sdk"
    ):
        base_runner = ClaudeAgentRoleRunner()
    else:
        base_runner = DeterministicRoleRunner()
    if not _uses_sandbox(context):
        return InProcessEmbeddedPocoKernel(role_runner=base_runner)
    return InProcessEmbeddedPocoKernel(
        role_runner=SandboxAwareRoleRunner(base_runner=base_runner, sandbox_runtime=sandbox_runtime)
    )


def build_sandbox_request(context: RunContext, *, attempt_id: str):
    from app.runtime.sandbox.callback_tokens import CallbackTokenBinding, callback_token_id_for_binding
    from app.runtime.sandbox.contracts import SandboxRuntimeRequest
    from app.settings import get_settings

    settings = get_settings()
    permissions = list(dict.fromkeys([*context.permissions, "sandbox.execute"]))
    return SandboxRuntimeRequest(
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        session_id=context.session_id,
        run_id=context.run_id,
        attempt_id=attempt_id,
        agent_id=context.agent_id,
        skill_ids=context.skill_ids,
        mcp_tool_ids=context.mcp_tool_ids,
        input_message=context.input_message,
        file_ids=context.file_ids,
        sandbox_mode=context.sandbox_mode,
        browser_enabled=context.browser_enabled,
        model=context.model,
        model_gateway="new-api",
        permissions=permissions,
        resource_limits=context.resource_limits,
        callback_url=f"{settings.sandbox_callback_base_url.rstrip('/')}/api/ai/runtime/callbacks/executor",
        callback_token_id=callback_token_id_for_binding(
            CallbackTokenBinding(run_id=context.run_id, attempt_id=attempt_id)
        ),
    )


def build_step_sandbox_request(*, context: RunContext, step: AgentStepExecutionContext) -> SandboxRuntimeRequest:
    from app.runtime.sandbox.callback_tokens import CallbackTokenBinding, callback_token_id_for_binding
    from app.settings import get_settings

    settings = get_settings()
    permissions = list(dict.fromkeys([*context.permissions, "sandbox.execute"]))
    attempt_id = _step_attempt_id(context, step)
    return SandboxRuntimeRequest(
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        session_id=context.session_id,
        run_id=context.run_id,
        attempt_id=attempt_id,
        agent_id=context.agent_id,
        skill_ids=step.skill_ids,
        mcp_tool_ids=step.mcp_tool_ids,
        input_message=context.input_message,
        file_ids=context.file_ids,
        sandbox_mode=step.sandbox_mode,
        browser_enabled=step.browser_enabled,
        model=context.model,
        model_gateway="new-api",
        permissions=permissions,
        resource_limits=step.resource_limits,
        callback_url=f"{settings.sandbox_callback_base_url.rstrip('/')}/api/ai/runtime/callbacks/executor",
        callback_token_id=callback_token_id_for_binding(
            CallbackTokenBinding(run_id=context.run_id, attempt_id=attempt_id)
        ),
    )


def _step_attempt_id(context: RunContext, step: AgentStepExecutionContext) -> str:
    parent_attempt = context.metadata.get("authoritative_attempt_id")
    if not isinstance(parent_attempt, str) or not parent_attempt:
        raise ValueError("authoritative parent attempt_id is required for sandbox step")
    material = b"embedded-poco-step-attempt-v1\0" + parent_attempt.encode("utf-8") + b"\0" + step.step_key.encode("utf-8")
    return "step-" + hashlib.sha256(material).hexdigest()


def _uses_sandbox(context: RunContext) -> bool:
    if context.sandbox_mode in {"ephemeral", "persistent"}:
        return True
    raw_steps = context.metadata.get("multi_agent_steps")
    if not isinstance(raw_steps, list):
        return False
    return any(isinstance(step, dict) and step.get("sandbox_mode") in {"ephemeral", "persistent"} for step in raw_steps)


def _message_from_events(events) -> str:
    chunks = [
        str(event.payload.get("delta") or event.message or "")
        for event in events
        if event.type == "assistant_delta"
    ]
    return "".join(chunks)


def _capabilities_for_context(context: RunContext) -> dict[str, bool]:
    return {
        "streaming": True,
        "tools": False,
        "artifacts": False,
        "sandbox": _uses_sandbox(context),
        "multi_agent": context.metadata.get("execution_mode") == "multi_agent",
    }


def _failed_result(
    error_code: str,
    message: str,
    *,
    capabilities: dict[str, bool] | None = None,
) -> ExecutorResult:
    return ExecutorResult(
        status="failed",
        adapter_version="embedded-poco-adapter/0.1.0",
        executor_type="embedded-poco-kernel",
        executor_version="in-process/0.1.0",
        capabilities=capabilities or {"streaming": True, "tools": False, "artifacts": False, "sandbox": False},
        result={"error_code": error_code, "message": message},
    )
