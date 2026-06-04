import asyncio
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from app.executors.claude_agent_sdk_runner import run_claude_agent_sdk
from app.runtime.kernel_contracts import AgentEvent, RunContext
from app.settings import get_settings


KernelEventSink = Callable[[AgentEvent], Awaitable[None] | None]
RoleDeltaSink = Callable[[str], Awaitable[None] | None]


@dataclass(frozen=True)
class RoleResult:
    output: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentStepPlan:
    step_key: str
    role: str
    depends_on: list[str] = field(default_factory=list)
    skill_ids: list[str] | None = None
    mcp_tool_ids: list[str] | None = None
    resource_limits: dict[str, object] | None = None
    sandbox_mode: str | None = None
    browser_enabled: bool | None = None


@dataclass(frozen=True)
class AgentStepExecutionContext:
    step_key: str
    role: str
    step_index: int
    depends_on: list[str]
    skill_ids: list[str]
    mcp_tool_ids: list[str]
    resource_limits: dict[str, object]
    sandbox_mode: str
    browser_enabled: bool


class RoleRunner(Protocol):
    async def run_role(
        self,
        *,
        role: str,
        context: RunContext,
        previous_outputs: list[str],
        step: AgentStepExecutionContext,
        emit_delta: RoleDeltaSink | None = None,
    ) -> RoleResult | str:
        """Run one logical agent role and return its user-visible contribution."""


class DeterministicRoleRunner:
    async def run_role(
        self,
        *,
        role: str,
        context: RunContext,
        previous_outputs: list[str],
        step: AgentStepExecutionContext,
        emit_delta: RoleDeltaSink | None = None,
    ) -> RoleResult:
        return RoleResult(output=_multi_agent_delta(role, context.input_message))


class ClaudeAgentRoleRunner:
    def __init__(self, *, workspace_root: str | Path | None = None, sdk_runner=None) -> None:
        self._workspace_root = Path(workspace_root) if workspace_root is not None else None
        self._sdk_runner = sdk_runner or run_claude_agent_sdk

    async def run_role(
        self,
        *,
        role: str,
        context: RunContext,
        previous_outputs: list[str],
        step: AgentStepExecutionContext,
        emit_delta: RoleDeltaSink | None = None,
    ) -> RoleResult:
        workspace_root = self._workspace_root or Path(get_settings().claude_agent_workspace_root)
        role_workspace = workspace_root / context.tenant_id / context.run_id / "roles" / _safe_role_path(step.step_key)
        role_workspace.mkdir(parents=True, exist_ok=True)
        async def on_text(delta: str) -> None:
            if emit_delta and delta:
                result = emit_delta(delta)
                if inspect.isawaitable(result):
                    await result

        sdk_result = await self._sdk_runner(
            prompt=_role_prompt(role=role, context=context, previous_outputs=previous_outputs, step=step),
            cwd=role_workspace,
            skill_id=str(step.skill_ids[0] if step.skill_ids else context.metadata.get("skill_id") or "general-chat"),
            on_text=on_text,
        )
        if getattr(sdk_result, "error", None):
            raise RuntimeError(str(sdk_result.error))
        return RoleResult(
            output=str(getattr(sdk_result, "message", "") or ""),
            metadata={
                "runner": "claude_agent_sdk",
                "sdk_used": bool(getattr(sdk_result, "used_sdk", False)),
                "sdk_session_id": getattr(sdk_result, "session_id", None),
                "sdk_usage": getattr(sdk_result, "usage", {}) or {},
                "step_key": step.step_key,
                "step_index": step.step_index,
            },
        )


class InProcessEmbeddedPocoKernel:
    def __init__(self, role_runner: RoleRunner | None = None) -> None:
        self._role_runner = role_runner or DeterministicRoleRunner()

    async def submit_run(self, context: RunContext, event_sink: KernelEventSink | None = None) -> list[AgentEvent]:
        events: list[AgentEvent] = []

        async def emit(event: AgentEvent) -> None:
            events.append(event)
            if event_sink is None:
                return
            result = event_sink(event)
            if inspect.isawaitable(result):
                await result

        await emit(AgentEvent(type="run_started", message="Run started"))
        if "chat.respond" not in context.permissions:
            await emit(
                AgentEvent(
                    type="run_failed",
                    message="Permission denied: chat.respond is required",
                    payload={
                        "error_code": "permission_denied",
                        "required_permission": "chat.respond",
                    },
                )
            )
            return events

        if context.metadata.get("execution_mode") == "multi_agent":
            steps = _multi_agent_steps_from_metadata(context.metadata)
            capability_violation = _first_step_capability_violation(context, steps)
            if capability_violation is not None:
                await emit(
                    AgentEvent(
                        type="run_failed",
                        message=str(capability_violation["message"]),
                        payload=capability_violation,
                    )
                )
                return events
            max_parallel_steps = _max_parallel_steps(context)
            completed_outputs: dict[str, str] = _resume_completed_step_outputs(context.metadata)
            pending_steps = []
            copied_from_run_id = _resume_copied_from_run_id(context.metadata)
            for index, step in enumerate(steps, start=1):
                if step.step_key in completed_outputs:
                    await emit(
                        AgentEvent(
                            type="agent_step_reused",
                            message=f"{step.role} agent reused checkpoint",
                            payload={
                                "role": step.role,
                                "step_key": step.step_key,
                                "step_index": index,
                                "depends_on": step.depends_on,
                                "output": completed_outputs[step.step_key],
                                "copied_from_run_id": copied_from_run_id,
                                "checkpoint_reused": True,
                            },
                        )
                    )
                    continue
                pending_steps.append((index, step))

            async def run_step(index: int, step: AgentStepPlan) -> tuple[AgentStepPlan, str | None, str | None]:
                previous_outputs = [completed_outputs[key] for key in step.depends_on]
                step_context = AgentStepExecutionContext(
                    step_key=step.step_key,
                    role=step.role,
                    step_index=index,
                    depends_on=list(step.depends_on),
                    skill_ids=list(step.skill_ids if step.skill_ids is not None else context.skill_ids),
                    mcp_tool_ids=list(step.mcp_tool_ids if step.mcp_tool_ids is not None else context.mcp_tool_ids),
                    resource_limits=_step_resource_limits(context.resource_limits, step.resource_limits),
                    sandbox_mode=step.sandbox_mode or context.sandbox_mode,
                    browser_enabled=step.browser_enabled if step.browser_enabled is not None else context.browser_enabled,
                )
                await emit(
                    AgentEvent(
                        type="agent_step_started",
                        message=f"{step.role} agent started",
                        payload={
                            "role": step.role,
                            "step_key": step.step_key,
                            "step_index": index,
                            "depends_on": step.depends_on,
                            "skill_ids": step_context.skill_ids,
                            "mcp_tool_ids": step_context.mcp_tool_ids,
                            "resource_limits": step_context.resource_limits,
                            "sandbox_mode": step_context.sandbox_mode,
                            "browser_enabled": step_context.browser_enabled,
                        },
                    )
                )
                async def emit_step_delta(delta: str) -> None:
                    await emit(
                        AgentEvent(
                            type="assistant_delta",
                            message=delta,
                            payload={
                                "delta": delta,
                                "role": step.role,
                                "step_key": step.step_key,
                                "step_index": index,
                                "depends_on": step.depends_on,
                                "streaming": True,
                            },
                        )
                    )

                try:
                    role_result = await self._role_runner.run_role(
                        role=step.role,
                        context=context,
                        previous_outputs=previous_outputs,
                        step=step_context,
                        emit_delta=emit_step_delta,
                    )
                except Exception as exc:
                    await emit(
                        AgentEvent(
                            type="agent_step_failed",
                            message=f"{step.role} agent failed: {exc}",
                            payload={
                                "role": step.role,
                                "step_key": step.step_key,
                                "step_index": index,
                                "depends_on": step.depends_on,
                                "error_code": "multi_agent_step_failed",
                                "error": str(exc),
                            },
                        )
                    )
                    return step, str(exc), None
                if isinstance(role_result, RoleResult):
                    delta = role_result.output
                    result_metadata = role_result.metadata
                else:
                    delta = str(role_result)
                    result_metadata = {}
                await emit(
                    AgentEvent(
                        type="assistant_delta",
                        message=delta,
                        payload={
                            "delta": delta,
                            "role": step.role,
                            "step_key": step.step_key,
                            "step_index": index,
                            "depends_on": step.depends_on,
                        },
                    )
                )
                await emit(
                    AgentEvent(
                        type="agent_step_completed",
                        message=f"{step.role} agent completed",
                        payload={
                            "role": step.role,
                            "step_key": step.step_key,
                            "step_index": index,
                            "depends_on": step.depends_on,
                            "output": delta,
                            "metadata": result_metadata,
                            "skill_ids": step_context.skill_ids,
                            "mcp_tool_ids": step_context.mcp_tool_ids,
                            "resource_limits": step_context.resource_limits,
                            "sandbox_mode": step_context.sandbox_mode,
                            "browser_enabled": step_context.browser_enabled,
                        },
                    )
                )
                return step, None, delta

            while pending_steps:
                ready = [
                    item
                    for item in pending_steps
                    if all(dependency in completed_outputs for dependency in item[1].depends_on)
                ]
                if not ready:
                    blocked_keys = [step.step_key for _, step in pending_steps]
                    for index, step in pending_steps:
                        missing_dependencies = [
                            dependency for dependency in step.depends_on if dependency not in completed_outputs
                        ]
                        await emit(
                            AgentEvent(
                                type="agent_step_blocked",
                                message=f"{step.role} agent blocked by unresolved dependencies",
                                payload={
                                    "role": step.role,
                                    "step_key": step.step_key,
                                    "step_index": index,
                                    "depends_on": step.depends_on,
                                    "missing_dependencies": missing_dependencies,
                                    "error_code": "multi_agent_dependency_blocked",
                                },
                            )
                        )
                    await emit(
                        AgentEvent(
                            type="run_failed",
                            message="Multi-agent execution plan has unresolved dependencies",
                            payload={
                                "status": "failed",
                                "execution_mode": "multi_agent",
                                "error_code": "multi_agent_dependency_blocked",
                                "blocked_step_keys": blocked_keys,
                            },
                        )
                    )
                    return events
                ready_batch = ready if max_parallel_steps is None else ready[:max_parallel_steps]
                pending_steps = [item for item in pending_steps if item not in ready_batch]
                results = await asyncio.gather(*(run_step(index, step) for index, step in ready_batch))
                failed = next(((step, error) for step, error, _output in results if error is not None), None)
                if failed is not None:
                    step, error = failed
                    await emit(
                        AgentEvent(
                            type="run_failed",
                            message=f"Multi-agent execution failed at {step.role}",
                            payload={
                                "status": "failed",
                                "execution_mode": "multi_agent",
                                "failed_role": step.role,
                                "failed_step_key": step.step_key,
                                "error_code": "multi_agent_step_failed",
                                "error": error,
                            },
                        )
                    )
                    return events
                for step, _error, output in results:
                    completed_outputs[step.step_key] = str(output or "")
            await emit(
                AgentEvent(
                    type="run_completed",
                    message="Run completed",
                    payload={"status": "succeeded", "execution_mode": "multi_agent"},
                )
            )
            return events

        response_delta = context.input_message.strip()
        await emit(
            AgentEvent(
                type="assistant_delta",
                message=response_delta,
                payload={"delta": response_delta},
            )
        )
        await emit(
            AgentEvent(
                type="run_completed",
                message="Run completed",
                payload={"status": "succeeded"},
            )
        )
        return events


def _max_parallel_steps(context: RunContext) -> int | None:
    for source in (context.resource_limits, context.metadata):
        raw_value = source.get("max_parallel_steps")
        if raw_value is None or isinstance(raw_value, bool):
            continue
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            continue
        return max(1, value)
    return None


def _multi_agent_steps_from_metadata(metadata: dict[str, object]) -> list[AgentStepPlan]:
    raw_steps = metadata.get("multi_agent_steps")
    if isinstance(raw_steps, list) and raw_steps:
        steps = []
        for index, item in enumerate(raw_steps, start=1):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or item.get("agent_role") or f"agent-{index}")
            step_key = str(item.get("step_key") or item.get("id") or f"{role}-{index}")
            depends_on_raw = item.get("depends_on") or []
            depends_on = [str(value) for value in depends_on_raw] if isinstance(depends_on_raw, list) else []
            steps.append(
                AgentStepPlan(
                    step_key=step_key,
                    role=role,
                    depends_on=depends_on,
                    skill_ids=_optional_string_list(item.get("skill_ids")),
                    mcp_tool_ids=_optional_string_list(item.get("mcp_tool_ids")),
                    resource_limits=dict(item["resource_limits"]) if isinstance(item.get("resource_limits"), dict) else None,
                    sandbox_mode=str(item["sandbox_mode"]) if item.get("sandbox_mode") is not None else None,
                    browser_enabled=item["browser_enabled"] if isinstance(item.get("browser_enabled"), bool) else None,
                )
            )
        if steps:
            return steps

    roles = list(metadata.get("multi_agent_roles") or ["coding", "test"])
    steps = []
    previous_step_keys: list[str] = []
    for index, role in enumerate(roles, start=1):
        step_key = f"{role}-{index}"
        steps.append(AgentStepPlan(step_key=step_key, role=str(role), depends_on=list(previous_step_keys)))
        previous_step_keys.append(step_key)
    return steps


def _optional_string_list(value: object) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _resume_completed_step_outputs(metadata: dict[str, object]) -> dict[str, str]:
    resume = metadata.get("resume")
    if not isinstance(resume, dict):
        return {}
    raw_outputs = resume.get("completed_step_outputs")
    if not isinstance(raw_outputs, dict):
        return {}
    return {str(key): str(value) for key, value in raw_outputs.items() if value is not None}


def _resume_copied_from_run_id(metadata: dict[str, object]) -> str | None:
    resume = metadata.get("resume")
    if not isinstance(resume, dict):
        return None
    copied_from_run_id = resume.get("copied_from_run_id")
    return str(copied_from_run_id) if copied_from_run_id else None


def _step_resource_limits(
    run_limits: dict[str, object],
    step_limits: dict[str, object] | None,
) -> dict[str, object]:
    merged = dict(run_limits)
    if step_limits:
        merged.update(step_limits)
    return merged


def _first_step_capability_violation(context: RunContext, steps: list[AgentStepPlan]) -> dict[str, object] | None:
    run_skill_ids = set(context.skill_ids)
    run_mcp_tool_ids = set(context.mcp_tool_ids)
    for step in steps:
        denied_skill_ids = [skill_id for skill_id in step.skill_ids or [] if skill_id not in run_skill_ids]
        denied_mcp_tool_ids = [tool_id for tool_id in step.mcp_tool_ids or [] if tool_id not in run_mcp_tool_ids]
        if denied_skill_ids or denied_mcp_tool_ids:
            return {
                "status": "failed",
                "execution_mode": "multi_agent",
                "error_code": "multi_agent_step_capability_denied",
                "message": f"Multi-agent step {step.step_key} requested capabilities outside the run allowlist",
                "role": step.role,
                "step_key": step.step_key,
                "denied_skill_ids": denied_skill_ids,
                "denied_mcp_tool_ids": denied_mcp_tool_ids,
            }
    return None


def _multi_agent_delta(role: str, prompt: str) -> str:
    normalized_prompt = prompt.strip() or "the requested task"
    if role == "coding":
        return f"Coding agent draft for: {normalized_prompt}"
    if role == "test":
        return f"Test agent verification plan for: {normalized_prompt}"
    return f"{role.title()} agent contribution for: {normalized_prompt}"


def _safe_role_path(role: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in role.strip().lower())
    return safe or "agent"


def _role_prompt(
    *,
    role: str,
    context: RunContext,
    previous_outputs: list[str],
    step: AgentStepExecutionContext,
) -> str:
    prior = "\n\n".join(previous_outputs).strip() or "None"
    return (
        "You are one role in an ai-platform multi-agent run. "
        "Use only backend-managed skills and MCP tools made available by the platform.\n\n"
        f"Role: {role}\n"
        f"Step key: {step.step_key}\n"
        f"Step index: {step.step_index}\n"
        f"Depends on: {', '.join(step.depends_on) or 'none'}\n"
        f"Skill IDs: {', '.join(step.skill_ids) or 'none'}\n"
        f"MCP Tool IDs: {', '.join(step.mcp_tool_ids) or 'none'}\n"
        f"Sandbox mode: {step.sandbox_mode}\n"
        f"Browser enabled: {step.browser_enabled}\n"
        f"Resource limits: {step.resource_limits}\n"
        f"User request: {context.input_message}\n\n"
        f"Previous step outputs:\n{prior}\n\n"
        "Return this role's concise contribution for the next step."
    )
