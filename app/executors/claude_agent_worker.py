import asyncio
import base64
import binascii
import hashlib
from pathlib import Path
import shutil
from typing import Any

from app import repositories
from app.control_plane_contracts import artifact_lineage_contract, standard_trace_id
from app.db import transaction
from app.executors.base import ArtifactManifest, ExecutorEventSink, ExecutorResult, RunPayload
from app.executors.claude_agent_sdk_runner import (
    ClaudeAgentSdkNotAvailable,
    build_skill_prompt,
    run_claude_agent_sdk,
)
from app.path_safety import ensure_creatable_inside, ensure_path_inside
from app.settings import get_settings
from app.skills.pinning import MAX_SKILL_SNAPSHOT_FILE_BYTES, MAX_SKILL_SNAPSHOT_TOTAL_BYTES
from app.skills.registry import BuiltinSkill, BuiltinSkillRegistry, skill_content_hash
from app.skills.dependencies import skill_dependency_ids, with_skill_dependencies
from app.skills.stager import SkillStager
from app.storage import ObjectStorage
from app.tool_policy import evaluate_tool_policy

NATIVE_USED_SKILL_SOURCES = {"executor_hook", "executor_native"}


def _claude_sdk_tool_id(tool_name: str) -> str:
    safe_name = "".join(char if char.isalnum() or char in "_.:-" else "_" for char in str(tool_name or "unknown"))
    safe_name = safe_name or "unknown"
    return f"claude-sdk:{safe_name[:96]}"


def _claude_sdk_tool_request_payload(request: dict[str, Any]) -> dict[str, Any]:
    tool_input = request.get("tool_input")
    command = ""
    if isinstance(tool_input, dict):
        command = str(tool_input.get("command") or "")
        input_keys = sorted(str(key) for key in tool_input)
    else:
        input_keys = []
    return {
        "source": "claude_agent_sdk_hook",
        "tool_name": str(request.get("tool_name") or ""),
        "tool_input_keys": input_keys,
        "command_length": len(command),
        "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest() if command else "",
    }


class PinnedSkillMismatch(ValueError):
    def __init__(self, message: str, *, actual_content_hash: str = "") -> None:
        super().__init__(message)
        self.actual_content_hash = actual_content_hash


class ClaudeAgentWorkerAdapter:
    adapter_version = "claude-agent-worker-adapter/1"
    executor_type = "claude-agent-worker"
    executor_version = "claude-agent-sdk-poc"
    capabilities = {
        "artifacts": True,
        "streaming": True,
        "tools": True,
        "skills": True,
    }

    def __init__(self, delegate: Any | None = None) -> None:
        # Primary execution is Claude Agent SDK with platform-owned staged Skills.
        # runtime211 is not a dependency of ai-platform execution.
        self._delegate = delegate

    async def submit_run(self, payload: RunPayload, event_sink: ExecutorEventSink | None = None) -> ExecutorResult:
        if payload.input.get("execution_mode") == "multi_agent":
            return await self._run_multi_agent_file_skill(payload, event_sink=event_sink)

        sdk_result = await self._run_with_staged_skills(payload, event_sink=event_sink)
        if sdk_result is not None:
            return sdk_result

        return self._sdk_required_result(payload, sdk_result=None)

    async def _run_general_chat(self, payload: RunPayload, event_sink: ExecutorEventSink | None = None) -> ExecutorResult:
        sdk_result = await self._try_run_sdk(payload, event_sink=event_sink)
        if sdk_result and sdk_result.used_sdk and not sdk_result.error:
            return ExecutorResult(
                status="succeeded",
                adapter_version=self.adapter_version,
                executor_type=self.executor_type,
                executor_version=self.executor_version,
                capabilities=self.capabilities,
                result={
                    "message": sdk_result.message or "任务完成",
                    "sdk_used": True,
                    "sdk_session_id": sdk_result.session_id,
                    "sdk_error": None,
                    "delegate_used": False,
                    "worker_boundary": self.executor_type,
                },
                artifacts=[],
                executor_payload={
                    "sdk_used": True,
                    "sdk_session_id": sdk_result.session_id,
                    "sdk_usage": sdk_result.usage,
                    "delegate_used": False,
                    "worker_boundary": self.executor_type,
                },
            )
        error_code = self._sdk_failure_code(sdk_result)
        sdk_used = bool(sdk_result and sdk_result.used_sdk)
        sdk_error = sdk_result.error if sdk_result else "claude_agent_sdk_disabled"
        return ExecutorResult(
            status="failed",
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities=self.capabilities,
            result={
                "message": "Claude Agent SDK is required for general chat runs.",
                "error_code": error_code,
                "sdk_used": sdk_used,
                "sdk_error": sdk_error,
                "delegate_used": False,
                "worker_boundary": self.executor_type,
            },
            executor_payload={
                "sdk_used": sdk_used,
                "sdk_error": sdk_error,
                "delegate_used": False,
                "worker_boundary": self.executor_type,
            },
        )

    def _sdk_failure_code(self, sdk_result) -> str:
        if sdk_result is None:
            return "claude_agent_sdk_disabled"
        error_text = str(getattr(sdk_result, "error", "") or "")
        if error_text.startswith("claude_agent_sdk_unavailable"):
            return "claude_agent_sdk_unavailable"
        if getattr(sdk_result, "used_sdk", False):
            return "claude_agent_sdk_runtime_error"
        if error_text == "claude_agent_sdk_disabled":
            return "claude_agent_sdk_disabled"
        return "claude_agent_sdk_required"

    def _sdk_required_result(self, payload: RunPayload, sdk_result) -> ExecutorResult:
        error_code = self._sdk_failure_code(sdk_result)
        sdk_used = bool(sdk_result and sdk_result.used_sdk)
        sdk_error = sdk_result.error if sdk_result else "claude_agent_sdk_disabled"
        return ExecutorResult(
            status="failed",
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities=self.capabilities,
            result={
                "message": "Claude Agent SDK with platform-managed Skills is required for ai-platform runs.",
                "error_code": error_code,
                "skill_id": payload.skill_id,
                "sdk_used": sdk_used,
                "sdk_error": sdk_error,
                "delegate_used": False,
                "worker_boundary": self.executor_type,
            },
            executor_payload={
                "sdk_used": sdk_used,
                "sdk_error": sdk_error,
                "delegate_used": False,
                "worker_boundary": self.executor_type,
            },
        )

    def _wrap_file_skill_result(
        self,
        result: ExecutorResult,
        *,
        multi_agent: bool = False,
        legacy_runtime_fallback_used: bool = False,
    ) -> ExecutorResult:
        legacy_runtime_fallback_used = (
            legacy_runtime_fallback_used
            or bool(result.result.get("legacy_runtime_fallback_used"))
            or bool(result.executor_payload.get("legacy_runtime_fallback_used"))
        )
        return ExecutorResult(
            status=result.status,
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities={**self.capabilities, **result.capabilities, "multi_agent": multi_agent},
            result={
                **result.result,
                "sdk_used": bool(result.result.get("sdk_used", result.executor_payload.get("sdk_used", False))),
                "sdk_session_id": result.result.get("sdk_session_id", result.executor_payload.get("sdk_session_id")),
                "sdk_message": result.result.get("sdk_message", result.result.get("message", "")),
                "sdk_error": result.result.get("sdk_error", result.executor_payload.get("sdk_error")),
                "delegate_used": bool(result.result.get("delegate_used", result.executor_payload.get("delegate_used", True))),
                "worker_boundary": self.executor_type,
                "delegate_executor_type": result.executor_type,
                "legacy_runtime_fallback_used": legacy_runtime_fallback_used,
            },
            artifacts=result.artifacts,
            executor_payload={
                **result.executor_payload,
                "sdk_used": bool(result.executor_payload.get("sdk_used", result.result.get("sdk_used", False))),
                "sdk_session_id": result.executor_payload.get("sdk_session_id", result.result.get("sdk_session_id")),
                "sdk_usage": result.executor_payload.get("sdk_usage", {}),
                "sdk_error": result.executor_payload.get("sdk_error", result.result.get("sdk_error")),
                "delegate_used": bool(result.executor_payload.get("delegate_used", result.result.get("delegate_used", True))),
                "worker_boundary": self.executor_type,
                "delegate_executor_type": result.executor_type,
                "legacy_runtime_fallback_used": legacy_runtime_fallback_used,
            },
        )

    async def _run_multi_agent_file_skill(
        self,
        payload: RunPayload,
        event_sink: ExecutorEventSink | None = None,
    ) -> ExecutorResult:
        steps = _file_skill_steps(payload.input)
        completed_outputs = _resume_completed_step_outputs(payload.input)
        completed_checkpoints = _resume_completed_step_checkpoints(payload.input)
        copied_from_run_id = _resume_copied_from_run_id(payload.input)
        completed_step_keys: set[str] = set(completed_outputs)
        skill_result: ExecutorResult | None = None
        skill_executed = False
        if completed_outputs:
            resume_preflight_failure = await self._preflight_resume_pinned_skills(payload, event_sink=event_sink)
            if resume_preflight_failure is not None:
                return self._wrap_file_skill_result(resume_preflight_failure, multi_agent=True)

        for index, step in enumerate(steps, start=1):
            step_key = str(step["step_key"])
            role = str(step["role"])
            if step_key in completed_outputs:
                checkpoint_lineage = _resume_checkpoint_lineage(
                    completed_checkpoints,
                    step_key=step_key,
                    copied_from_run_id=copied_from_run_id,
                )
                await self._emit_agent_step(
                    event_sink,
                    event_type="agent_step_reused",
                    step=step,
                    step_index=index,
                    message=f"{role} agent reused checkpoint",
                    payload={
                        "output": completed_outputs[step_key],
                        "copied_from_run_id": copied_from_run_id,
                        "checkpoint_reused": True,
                        **checkpoint_lineage,
                    },
                )
                continue
            depends_on = list(step.get("depends_on") or [])
            missing_dependencies = [dependency for dependency in depends_on if dependency not in completed_step_keys]
            if missing_dependencies:
                await self._emit_agent_step(
                    event_sink,
                    event_type="agent_step_blocked",
                    step=step,
                    step_index=index,
                    message=f"{role} agent blocked by unresolved dependencies",
                    payload={
                        "missing_dependencies": missing_dependencies,
                        "error_code": "multi_agent_dependency_blocked",
                    },
                )
                return self._multi_agent_file_skill_failed(
                    "multi_agent_dependency_blocked",
                    f"Multi-agent file workflow blocked at {step_key}",
                )

            await self._emit_agent_step(
                event_sink,
                event_type="agent_step_started",
                step=step,
                step_index=index,
                message=f"{role} agent started",
            )

            if not skill_executed and _is_file_skill_execution_step(step):
                skill_result = await self._run_file_skill_once(payload, event_sink=event_sink)
                skill_executed = True
                if skill_result.status != "succeeded":
                    await self._emit_agent_step(
                        event_sink,
                        event_type="agent_step_failed",
                        step=step,
                        step_index=index,
                        message=f"{role} agent failed",
                        payload={
                            "error_code": str(skill_result.result.get("error_code") or "file_skill_failed"),
                            "error": str(skill_result.result.get("message") or "File skill failed"),
                        },
                    )
                    return self._wrap_file_skill_result(skill_result, multi_agent=True)
                output = str(skill_result.result.get("message") or "File skill completed")
                extra_payload = {
                    "output": output,
                    "artifact_count": len(skill_result.artifacts),
                    "delegate_executor_type": skill_result.executor_type,
                }
            else:
                output = _non_execution_step_output(step=step, payload=payload, skill_result=skill_result)
                extra_payload = {"output": output}

            checkpoint_payload = _completed_step_checkpoint_payload(payload, step_index=index)
            extra_payload.update({key: value for key, value in checkpoint_payload.items() if key not in extra_payload})
            await self._emit_agent_step(
                event_sink,
                event_type="agent_step_completed",
                step=step,
                step_index=index,
                message=f"{role} agent completed",
                payload=extra_payload,
            )
            completed_step_keys.add(step_key)
            completed_outputs[step_key] = output

        if skill_result is None:
            if completed_outputs:
                skill_result = self._multi_agent_resume_result(completed_outputs)
            else:
                skill_result = await self._run_file_skill_once(payload, event_sink=event_sink)
        return self._wrap_file_skill_result(skill_result, multi_agent=True)

    async def _preflight_resume_pinned_skills(
        self,
        payload: RunPayload,
        event_sink: ExecutorEventSink | None = None,
    ) -> ExecutorResult | None:
        settings = get_settings()
        workspace = _run_workspace(settings, payload)
        _prepare_run_workspace(settings.claude_agent_workspace_root, workspace)

        skills = BuiltinSkillRegistry(settings.platform_skills_root).list_builtin_skills()
        pinned_manifests = _pinned_skill_manifests(payload)
        available_names = list(dict.fromkeys([skill.name for skill in skills] + list(pinned_manifests)))
        allowed_skill_names = _allowed_skill_names(payload, available_names)
        _selected_skills, pin_mismatches = _select_pinned_skills(
            skills,
            allowed_skill_names,
            pinned_manifests,
            workspace / ".ai-platform" / "pinned-skills",
        )
        if not pin_mismatches:
            return None

        if event_sink is not None:
            await event_sink(
                event_type="error",
                stage="skills",
                message="Pinned Skill version does not match available source",
                payload={
                    "error_code": "skill_version_pin_mismatch",
                    "mismatches": pin_mismatches,
                    "visible_to_user": False,
                    "severity": "error",
                },
            )
        return ExecutorResult(
            status="failed",
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities={**self.capabilities, "platform_skills": True},
            result={
                "message": "Pinned Skill version mismatch",
                "error_code": "skill_version_pin_mismatch",
                "sdk_used": False,
                "sdk_error": "skill_version_pin_mismatch",
                "delegate_used": False,
                "worker_boundary": self.executor_type,
                "allowed_skills": allowed_skill_names,
                "staged_skills": [],
                "used_skills": [],
            },
            artifacts=[],
            executor_payload={
                "sdk_used": False,
                "sdk_error": "skill_version_pin_mismatch",
                "delegate_used": False,
                "worker_boundary": self.executor_type,
                "allowed_skills": allowed_skill_names,
                "staged_skills": [],
                "used_skills": [],
                "skill_manifests": _pin_manifests_for_result(pinned_manifests, allowed_skill_names),
                "pin_mismatches": pin_mismatches,
            },
        )

    def _multi_agent_resume_result(self, completed_outputs: dict[str, str]) -> ExecutorResult:
        message = "\n".join(completed_outputs.values()).strip() or "Checkpointed multi-agent steps reused."
        return ExecutorResult(
            status="succeeded",
            adapter_version=self.adapter_version,
            executor_type="multi-agent-resume",
            executor_version="checkpoint-reuse/1",
            capabilities={**self.capabilities, "multi_agent": True},
            result={
                "message": message,
                "artifact_count": 0,
                "checkpoint_reused": True,
                "reused_step_keys": list(completed_outputs.keys()),
            },
            artifacts=[],
            executor_payload={
                "checkpoint_reused": True,
                "reused_step_keys": list(completed_outputs.keys()),
            },
        )

    async def _run_file_skill_once(
        self,
        payload: RunPayload,
        event_sink: ExecutorEventSink | None = None,
    ) -> ExecutorResult:
        sdk_result = await self._run_with_staged_skills(payload, event_sink=event_sink)
        if sdk_result is not None:
            return sdk_result
        return self._sdk_required_result(payload, sdk_result=None)

    async def _emit_legacy_runtime_fallback_marker(self, event_sink: ExecutorEventSink | None) -> None:
        if event_sink is None:
            return
        await event_sink(
            event_type="legacy_runtime_fallback_used",
            stage="executor",
            message="runtime211 legacy fallback used",
            payload={
                "delegate_executor_type": "runtime211",
                "visible_to_user": False,
                "severity": "warning",
            },
        )

    async def _emit_agent_step(
        self,
        event_sink: ExecutorEventSink | None,
        *,
        event_type: str,
        step: dict[str, object],
        step_index: int,
        message: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        if event_sink is None:
            return
        merged_payload = {
            "role": str(step["role"]),
            "step_key": str(step["step_key"]),
            "step_index": step_index,
            "depends_on": list(step.get("depends_on") or []),
            "skill_ids": list(step.get("skill_ids") or []),
            "mcp_tool_ids": list(step.get("mcp_tool_ids") or []),
        }
        if payload:
            merged_payload.update(payload)
        await event_sink(
            event_type=event_type,
            stage="agent",
            message=message,
            payload=merged_payload,
        )

    def _multi_agent_file_skill_failed(self, error_code: str, message: str) -> ExecutorResult:
        return ExecutorResult(
            status="failed",
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities={**self.capabilities, "multi_agent": True},
            result={
                "message": message,
                "error_code": error_code,
                "sdk_used": False,
                "sdk_session_id": None,
                "sdk_message": "",
                "sdk_error": None,
                "delegate_used": False,
                "worker_boundary": self.executor_type,
            },
            executor_payload={
                "sdk_used": False,
                "sdk_session_id": None,
                "sdk_usage": {},
                "delegate_used": False,
                "worker_boundary": self.executor_type,
            },
        )

    async def _run_with_staged_skills(
        self,
        payload: RunPayload,
        event_sink: ExecutorEventSink | None = None,
    ) -> ExecutorResult | None:
        settings = get_settings()
        if not settings.claude_agent_sdk_enabled:
            return None
        workspace = _run_workspace(settings, payload)
        _prepare_run_workspace(settings.claude_agent_workspace_root, workspace)
        file_names = await self._materialize_files(payload, workspace)

        skills = BuiltinSkillRegistry(settings.platform_skills_root).list_builtin_skills()
        pinned_manifests = _pinned_skill_manifests(payload)
        available_names = list(dict.fromkeys([skill.name for skill in skills] + list(pinned_manifests)))
        allowed_skill_names = _allowed_skill_names(payload, available_names)
        selected_skills, pin_mismatches = _select_pinned_skills(
            skills,
            allowed_skill_names,
            pinned_manifests,
            workspace / ".ai-platform" / "pinned-skills",
        )
        if pin_mismatches:
            if event_sink is not None:
                await event_sink(
                    event_type="error",
                    stage="skills",
                    message="Pinned Skill version does not match available source",
                    payload={
                        "error_code": "skill_version_pin_mismatch",
                        "mismatches": pin_mismatches,
                        "visible_to_user": False,
                        "severity": "error",
                    },
                )
            return ExecutorResult(
                status="failed",
                adapter_version=self.adapter_version,
                executor_type=self.executor_type,
                executor_version=self.executor_version,
                capabilities={**self.capabilities, "platform_skills": True},
                result={
                    "message": "Pinned Skill version mismatch",
                    "error_code": "skill_version_pin_mismatch",
                    "sdk_used": False,
                    "sdk_error": "skill_version_pin_mismatch",
                    "delegate_used": False,
                    "worker_boundary": self.executor_type,
                    "allowed_skills": allowed_skill_names,
                    "staged_skills": [],
                    "used_skills": [],
                },
                artifacts=[],
                executor_payload={
                    "sdk_used": False,
                    "sdk_error": "skill_version_pin_mismatch",
                    "delegate_used": False,
                    "worker_boundary": self.executor_type,
                    "allowed_skills": allowed_skill_names,
                    "staged_skills": [],
                    "used_skills": [],
                    "skill_manifests": _pin_manifests_for_result(pinned_manifests, allowed_skill_names),
                    "pin_mismatches": pin_mismatches,
                },
            )
        staged_skill_names = SkillStager(settings.skill_staging_subdir).stage_skills(
            workspace=workspace,
            skills=selected_skills,
        )
        if event_sink is not None:
            await event_sink(
                event_type="skills_staged",
                stage="skills",
                message="Platform Skills staged for Claude Agent SDK",
                payload={
                    "allowed_skills": allowed_skill_names,
                    "staged_skills": staged_skill_names,
                    "visible_to_user": False,
                    "severity": "info",
                },
            )

        prompt = build_skill_prompt(
            skill_id=payload.skill_id,
            user_message=str(payload.input.get("message") or payload.input.get("prompt") or ""),
            file_names=file_names,
        )
        sdk_result = await self._try_run_sdk(
            payload,
            event_sink=event_sink,
            workspace=workspace,
            file_names=file_names,
            prompt=prompt,
            staged_skill_names=staged_skill_names,
        )
        if sdk_result and sdk_result.used_sdk and not sdk_result.error:
            artifacts = self._collect_workspace_artifacts(payload, workspace)
            used_skill_names = _sdk_used_skill_names(sdk_result, staged_skill_names)
            used_skills_source = _sdk_used_skills_source(sdk_result, used_skill_names)
            inferred_used_skill_names = _inferred_used_skill_names(payload, staged_skill_names)
            skill_manifests = _skill_manifests(
                selected_skills,
                used_skill_names=used_skill_names,
                pins=pinned_manifests,
            )
            return ExecutorResult(
                status="succeeded",
                adapter_version=self.adapter_version,
                executor_type=self.executor_type,
                executor_version=self.executor_version,
                capabilities={**self.capabilities, "platform_skills": True},
                result={
                    "message": sdk_result.message or "任务完成",
                    "artifact_count": len(artifacts),
                    "sdk_used": True,
                    "sdk_session_id": sdk_result.session_id,
                    "sdk_error": None,
                    "delegate_used": False,
                    "worker_boundary": self.executor_type,
                    "allowed_skills": allowed_skill_names,
                    "staged_skills": staged_skill_names,
                    "used_skills": used_skill_names,
                },
                artifacts=artifacts,
                executor_payload={
                    "sdk_used": True,
                    "sdk_session_id": sdk_result.session_id,
                    "sdk_usage": sdk_result.usage,
                    "delegate_used": False,
                    "worker_boundary": self.executor_type,
                    "allowed_skills": allowed_skill_names,
                    "staged_skills": staged_skill_names,
                    "used_skills": used_skill_names,
                    "used_skills_source": used_skills_source,
                    "inferred_used_skills": inferred_used_skill_names,
                    "skill_manifests": skill_manifests,
                },
            )
        used_skill_names = _sdk_used_skill_names(sdk_result, staged_skill_names) if sdk_result else []
        used_skills_source = _sdk_used_skills_source(sdk_result, used_skill_names)
        inferred_used_skill_names = _inferred_used_skill_names(payload, staged_skill_names)
        skill_manifests = _skill_manifests(
            selected_skills,
            used_skill_names=used_skill_names,
            pins=pinned_manifests,
        )
        return ExecutorResult(
            status="failed",
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities={**self.capabilities, "platform_skills": True},
            result={
                "message": sdk_result.message if sdk_result else "Claude Agent SDK execution failed",
                "error_code": self._sdk_failure_code(sdk_result),
                "sdk_used": bool(sdk_result and sdk_result.used_sdk),
                "sdk_error": sdk_result.error if sdk_result else "claude_agent_sdk_required",
                "delegate_used": False,
                "worker_boundary": self.executor_type,
                "allowed_skills": allowed_skill_names,
                "staged_skills": staged_skill_names,
                "used_skills": used_skill_names,
            },
            artifacts=[],
            executor_payload={
                "sdk_used": bool(sdk_result and sdk_result.used_sdk),
                "sdk_error": sdk_result.error if sdk_result else "claude_agent_sdk_required",
                "delegate_used": False,
                "worker_boundary": self.executor_type,
                "allowed_skills": allowed_skill_names,
                "staged_skills": staged_skill_names,
                "used_skills": used_skill_names,
                "used_skills_source": used_skills_source,
                "inferred_used_skills": inferred_used_skill_names,
                "skill_manifests": skill_manifests,
            },
        )

    async def _try_run_sdk(
        self,
        payload: RunPayload,
        event_sink: ExecutorEventSink | None = None,
        *,
        workspace: Path | None = None,
        file_names: list[str] | None = None,
        prompt: str | None = None,
        staged_skill_names: list[str] | None = None,
    ):
        settings = get_settings()
        if not settings.claude_agent_sdk_enabled:
            return None
        if workspace is None:
            workspace = _run_workspace(settings, payload)
            _prepare_run_workspace(settings.claude_agent_workspace_root, workspace)
        else:
            ensure_creatable_inside(
                settings.claude_agent_workspace_root,
                workspace,
                "run workspace must stay inside the configured workspace root",
            )
            workspace.mkdir(parents=True, exist_ok=True)
        file_names = file_names if file_names is not None else await self._materialize_files(payload, workspace)
        prompt = prompt or build_skill_prompt(
            skill_id=payload.skill_id,
            user_message=str(payload.input.get("message") or payload.input.get("prompt") or ""),
            file_names=file_names,
        )
        async def on_text(delta: str) -> None:
            if event_sink:
                await event_sink(
                    event_type="assistant_delta",
                    stage="message",
                    message=delta,
                    payload={"delta": delta, "visible_to_user": True, "severity": "info"},
                )

        async def on_skill_use(skill_name: str, metadata: dict[str, Any]) -> None:
            if event_sink:
                await event_sink(
                    event_type="skill_used",
                    stage="skills",
                    message=f"Platform Skill used: {skill_name}",
                    payload={
                        "skill_id": skill_name,
                        "tool_use_id": str(metadata.get("tool_use_id") or ""),
                        "source": str(metadata.get("source") or "claude_agent_sdk_hook"),
                        "used_skills_source": "executor_hook",
                        "visible_to_user": False,
                        "severity": "info",
                    },
                )

        async def on_tool_permission(request: dict[str, Any]) -> dict[str, Any]:
            tool_name = str(request.get("tool_name") or "")
            tool_id = _claude_sdk_tool_id(tool_name)
            tool_call_id = str(request.get("tool_call_id") or "")
            action = str(request.get("action") or "execute")
            requested_risk_level = str(request.get("risk_level") or "high")
            requested_write_capable = bool(request.get("write_capable", True))
            reason = str(request.get("reason") or "Claude SDK tool permission required")
            trace_id = payload.trace_id or standard_trace_id(payload.run_id)
            request_payload = _claude_sdk_tool_request_payload(request)
            tool = {
                "id": tool_id,
                "risk_level": requested_risk_level,
                "write_capable": requested_write_capable,
            }

            async with transaction() as conn:
                permission_decision = await repositories.get_latest_tool_permission_decision(
                    conn,
                    tenant_id=payload.tenant_id,
                    user_id=payload.user_id,
                    run_id=payload.run_id,
                    tool_id=tool_id,
                    action=action,
                    tool_call_id=tool_call_id,
                    request_payload_json=request_payload,
                )
                if tool_id == "claude-sdk:Bash" and permission_decision is not None:
                    decision = str(permission_decision.get("decision") or "")
                    decision_tool_call_id = str(permission_decision.get("tool_call_id") or "")
                    decision_payload = permission_decision.get("request_payload_json")
                    if not isinstance(decision_payload, dict):
                        decision_payload = {}
                    decision_command_hash = str(decision_payload.get("command_sha256") or "")
                    request_command_hash = str(request_payload.get("command_sha256") or "")
                    if decision == "allow_once" and (not tool_call_id or decision_tool_call_id != tool_call_id):
                        permission_decision = None
                    elif decision == "allow_for_run" and (
                        not request_command_hash or decision_command_hash != request_command_hash
                    ):
                        permission_decision = None
                    elif decision == "deny" and (not tool_call_id or decision_tool_call_id != tool_call_id):
                        permission_decision = None
                tool_gate = evaluate_tool_policy(
                    tool=tool,
                    permission_decision=permission_decision,
                    requested_risk_level=requested_risk_level,
                    requested_write_capable=requested_write_capable,
                )
                if tool_gate.allowed:
                    if tool_gate.decision == "allow_once":
                        consumed_decision = await repositories.consume_tool_permission_decision(
                            conn,
                            tenant_id=payload.tenant_id,
                            user_id=payload.user_id,
                            run_id=payload.run_id,
                            request_id=tool_gate.permission_request_id,
                        )
                        if consumed_decision is None:
                            await repositories.append_audit_log(
                                conn,
                                tenant_id=payload.tenant_id,
                                user_id=payload.user_id,
                                action="claude_sdk_tool_policy_denied",
                                target_type="tool",
                                target_id=tool_id,
                                trace_id=trace_id,
                                payload_json={
                                    "run_id": payload.run_id,
                                    "session_id": payload.session_id,
                                    "agent_id": payload.agent_id,
                                    "skill_id": payload.skill_id,
                                    "tool_call_id": tool_call_id,
                                    "reason": "tool_permission_consumed_or_expired",
                                    "risk_level": tool_gate.risk_level,
                                    "write_capable": tool_gate.write_capable,
                                    "decision": tool_gate.decision,
                                    "permission_request_id": tool_gate.permission_request_id,
                                },
                            )
                            return {
                                "allowed": False,
                                "reason": "tool_permission_consumed_or_expired",
                                "risk_level": tool_gate.risk_level,
                                "write_capable": tool_gate.write_capable,
                                "decision": tool_gate.decision,
                                "permission_request_id": tool_gate.permission_request_id,
                            }
                    await repositories.append_audit_log(
                        conn,
                        tenant_id=payload.tenant_id,
                        user_id=payload.user_id,
                        action="claude_sdk_tool_policy_allowed",
                        target_type="tool",
                        target_id=tool_id,
                        trace_id=trace_id,
                        payload_json={
                            "run_id": payload.run_id,
                            "session_id": payload.session_id,
                            "agent_id": payload.agent_id,
                            "skill_id": payload.skill_id,
                            "tool_call_id": tool_call_id,
                            "reason": tool_gate.reason,
                            "risk_level": tool_gate.risk_level,
                            "write_capable": tool_gate.write_capable,
                            "decision": tool_gate.decision,
                            "permission_request_id": tool_gate.permission_request_id,
                        },
                    )
                    return {
                        "allowed": True,
                        "reason": tool_gate.reason,
                        "risk_level": tool_gate.risk_level,
                        "write_capable": tool_gate.write_capable,
                        "decision": tool_gate.decision,
                        "permission_request_id": tool_gate.permission_request_id,
                    }

                permission_request_id = tool_gate.permission_request_id
                if tool_gate.reason == "tool_permission_required":
                    row = await repositories.create_tool_permission_request(
                        conn,
                        tenant_id=payload.tenant_id,
                        workspace_id=payload.workspace_id,
                        user_id=payload.user_id,
                        session_id=payload.session_id,
                        run_id=payload.run_id,
                        trace_id=trace_id,
                        tool_id=tool_id,
                        tool_call_id=tool_call_id,
                        action=action,
                        risk_level=tool_gate.risk_level,
                        write_capable=tool_gate.write_capable,
                        reason=reason,
                        request_payload_json=request_payload,
                    )
                    permission_request_id = str(row["id"])
                    await repositories.append_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        trace_id=trace_id,
                        event_type="tool_permission_requested",
                        stage="tool_policy",
                        message="工具调用需要权限决策",
                        payload={
                            "visible_to_user": True,
                            "permission_request_id": permission_request_id,
                            "tool_id": tool_id,
                            "tool_call_id": tool_call_id,
                            "action": action,
                            "risk_level": tool_gate.risk_level,
                            "write_capable": tool_gate.write_capable,
                            "reason": reason,
                            "status": "pending",
                        },
                    )
                await repositories.append_audit_log(
                    conn,
                    tenant_id=payload.tenant_id,
                    user_id=payload.user_id,
                    action="claude_sdk_tool_policy_denied",
                    target_type="tool",
                    target_id=tool_id,
                    trace_id=trace_id,
                    payload_json={
                        "run_id": payload.run_id,
                        "session_id": payload.session_id,
                        "agent_id": payload.agent_id,
                        "skill_id": payload.skill_id,
                        "tool_call_id": tool_call_id,
                        "reason": tool_gate.reason,
                        "risk_level": tool_gate.risk_level,
                        "write_capable": tool_gate.write_capable,
                        "decision": tool_gate.decision,
                        "permission_request_id": permission_request_id,
                    },
                )
                return {
                    "allowed": False,
                    "reason": tool_gate.reason,
                    "risk_level": tool_gate.risk_level,
                    "write_capable": tool_gate.write_capable,
                    "decision": tool_gate.decision,
                    "permission_request_id": permission_request_id,
                }

        try:
            return await run_claude_agent_sdk(
                prompt=prompt,
                cwd=workspace,
                skill_id=payload.skill_id,
                skills=staged_skill_names,
                on_text=on_text,
                on_skill_use=on_skill_use,
                on_tool_permission=on_tool_permission,
            )
        except ClaudeAgentSdkNotAvailable as exc:
            return type("SdkUnavailable", (), {
                "used_sdk": False,
                "message": "",
                "session_id": None,
                "usage": {},
                "error": f"claude_agent_sdk_unavailable: {exc}",
            })()
        except Exception as exc:
            return type("SdkFailed", (), {
                "used_sdk": True,
                "message": "",
                "session_id": None,
                "usage": {},
                "error": str(exc),
            })()

    async def _materialize_files(self, payload: RunPayload, workspace: Path) -> list[str]:
        if not payload.file_ids:
            return []
        if workspace.exists() and workspace.is_symlink():
            raise ValueError("run workspace must not be a symlink")
        storage = ObjectStorage()
        file_names: list[str] = []
        async with transaction() as conn:
            for file_id in payload.file_ids:
                row = await repositories.get_run_file(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    file_id=file_id,
                )
                if row is None:
                    continue
                filename = Path(str(row["original_name"] or file_id)).name
                target = workspace / filename
                ensure_creatable_inside(workspace, target, "uploaded file target must stay inside the run workspace")
                target.write_bytes(storage.get_bytes(storage_key=row["storage_key"]))
                file_names.append(filename)
        return file_names

    def _collect_workspace_artifacts(self, payload: RunPayload, workspace: Path) -> list[ArtifactManifest]:
        output_dir = workspace / "output"
        if not output_dir.is_dir():
            return []
        ensure_path_inside(workspace, output_dir, "workspace output must stay inside the run workspace")
        artifacts: list[ArtifactManifest] = []
        storage = ObjectStorage()
        candidates: list[Path] = []
        for item in sorted(output_dir.rglob("*")):
            if item.is_symlink():
                raise ValueError("workspace output must not contain symlinks")
            if not item.is_file():
                continue
            ensure_path_inside(output_dir, item, "workspace artifact must stay inside output directory")
            candidates.append(item)
        for index, path in enumerate(candidates, start=1):
            content_type = _artifact_content_type(path.name)
            artifact_type = _artifact_type(path.name, payload.skill_id)
            storage_key = (
                f"tenants/{payload.tenant_id}/workspaces/{payload.workspace_id}/"
                f"sessions/{payload.session_id}/runs/{payload.run_id}/artifacts/{index}/{path.name}"
            )
            stored = storage.put_bytes(
                storage_key=storage_key,
                content=path.read_bytes(),
                content_type=content_type,
            )
            artifacts.append(
                ArtifactManifest(
                    artifact_type=artifact_type,
                    label=_artifact_label(path.name, artifact_type),
                    content_type=content_type,
                    storage_key=stored.storage_key,
                    size_bytes=stored.size_bytes,
                    manifest={
                        "source_executor": self.executor_type,
                        "workspace_output": str(path.relative_to(workspace)),
                    },
                )
            )
        return artifacts


def _file_skill_steps(input_payload: dict[str, object]) -> list[dict[str, object]]:
    raw_steps = input_payload.get("multi_agent_steps")
    if isinstance(raw_steps, list) and raw_steps:
        steps = []
        for index, raw_step in enumerate(raw_steps, start=1):
            if not isinstance(raw_step, dict):
                continue
            role = str(raw_step.get("role") or raw_step.get("agent_role") or f"step-{index}")
            step_key = str(raw_step.get("step_key") or raw_step.get("id") or role or f"step-{index}")
            depends_on = raw_step.get("depends_on") if isinstance(raw_step.get("depends_on"), list) else []
            steps.append(
                {
                    "step_key": step_key,
                    "role": role,
                    "depends_on": [str(item) for item in depends_on],
                    "skill_ids": _string_list(raw_step.get("skill_ids")),
                    "mcp_tool_ids": _string_list(raw_step.get("mcp_tool_ids")),
                }
            )
        if steps:
            return steps
    return [
        {"step_key": "inspect", "role": "inspect", "depends_on": [], "skill_ids": [], "mcp_tool_ids": []},
        {
            "step_key": "execute",
            "role": "execute",
            "depends_on": ["inspect"],
            "skill_ids": _string_list(input_payload.get("skill_ids")),
            "mcp_tool_ids": _string_list(input_payload.get("mcp_tool_ids")),
        },
        {"step_key": "verify", "role": "verify", "depends_on": ["execute"], "skill_ids": [], "mcp_tool_ids": []},
    ]


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _allowed_skill_names(payload: RunPayload, available_names: list[str]) -> list[str]:
    available = set(available_names)
    requested = _string_list(payload.input.get("skill_ids"))
    if payload.skill_id and payload.skill_id in available:
        requested.insert(0, payload.skill_id)
    if not requested:
        return []
    selected = list(dict.fromkeys(name for name in requested if name in available))
    pinned_manifests = _pinned_skill_manifests(payload)
    if pinned_manifests:
        return _with_pinned_manifest_dependencies(selected, pinned_manifests)
    return _with_skill_dependencies(selected, available)


def _with_pinned_manifest_dependencies(selected: list[str], pins: dict[str, dict[str, Any]]) -> list[str]:
    expanded: list[str] = []

    def add_skill(skill_name: str) -> None:
        if skill_name in expanded:
            return
        expanded.append(skill_name)
        manifest = pins.get(skill_name)
        if not manifest:
            return
        for dependency_id in _string_list(manifest.get("dependency_ids")):
            add_skill(dependency_id)

    for skill_name in selected:
        add_skill(skill_name)
    return expanded


def _inferred_used_skill_names(payload: RunPayload, staged_skill_names: list[str]) -> list[str]:
    staged = set(staged_skill_names)
    used: list[str] = []
    if payload.skill_id and payload.skill_id in staged:
        used.append(payload.skill_id)
        pinned_manifests = _pinned_skill_manifests(payload)
        if pinned_manifests and payload.skill_id in pinned_manifests:
            dependency_ids = _string_list(pinned_manifests[payload.skill_id].get("dependency_ids"))
        else:
            dependency_ids = skill_dependency_ids(payload.skill_id, staged)
        for dependency_id in dependency_ids:
            if dependency_id in staged and dependency_id not in used:
                used.append(dependency_id)
    return used


def _run_workspace(settings: object, payload: RunPayload) -> Path:
    return Path(settings.claude_agent_workspace_root) / payload.tenant_id / payload.run_id


def _prepare_run_workspace(workspace_root: str | Path, workspace: Path) -> None:
    ensure_creatable_inside(
        workspace_root,
        workspace,
        "run workspace must stay inside the configured workspace root",
    )
    if workspace.exists():
        if workspace.is_symlink() or not workspace.is_dir():
            raise ValueError("run workspace must stay inside the configured workspace root")
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=False)
    ensure_creatable_inside(
        workspace_root,
        workspace,
        "run workspace must stay inside the configured workspace root",
    )


def _sdk_used_skill_names(sdk_result: object, staged_skill_names: list[str]) -> list[str]:
    raw = getattr(sdk_result, "used_skills", None)
    if not isinstance(raw, list):
        return []
    staged = set(staged_skill_names)
    used: list[str] = []
    for item in raw:
        skill_name = str(item).strip()
        if not skill_name or skill_name not in staged or skill_name in used:
            continue
        used.append(skill_name)
    return used


def _sdk_used_skills_source(sdk_result: object | None, used_skill_names: list[str]) -> str:
    if not used_skill_names:
        return "none"
    source = str(getattr(sdk_result, "used_skills_source", "") or "").strip()
    return source or "executor_hook"


def _pinned_skill_manifests(payload: RunPayload) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("skill_id")).strip(): item
        for item in payload.skill_manifests
        if isinstance(item, dict) and str(item.get("skill_id") or "").strip()
    }


def _materialize_pinned_skill(skill_name: str, pin: dict[str, Any], snapshot_root: Path) -> BuiltinSkill:
    if Path(skill_name).name != skill_name:
        raise ValueError(f"invalid pinned skill name: {skill_name}")
    expected_hash = str(pin.get("content_hash") or pin.get("version") or "")
    if not expected_hash:
        raise ValueError(f"pinned skill missing content hash: {skill_name}")
    target = snapshot_root / skill_name
    workspace_root = snapshot_root.parents[1]
    ensure_creatable_inside(workspace_root, target, "pinned skill path must stay inside the run workspace")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    ensure_creatable_inside(workspace_root, target, "pinned skill path must stay inside the run workspace")
    total_bytes = 0
    for item in pin.get("files") or []:
        if not isinstance(item, dict):
            raise ValueError(f"invalid pinned skill file entry: {skill_name}")
        relative_path = str(item.get("relative_path") or "")
        if not relative_path or Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
            raise ValueError(f"invalid pinned skill file path: {skill_name}")
        content = base64.b64decode(str(item.get("content_base64") or ""), validate=True)
        if "size_bytes" not in item:
            raise ValueError(f"pinned skill file missing size_bytes: {skill_name}")
        if int(item["size_bytes"]) != len(content):
            raise ValueError(f"pinned skill file size mismatch: {skill_name}")
        if len(content) > MAX_SKILL_SNAPSHOT_FILE_BYTES:
            raise ValueError(f"pinned skill file too large: {skill_name}")
        total_bytes += len(content)
        if total_bytes > MAX_SKILL_SNAPSHOT_TOTAL_BYTES:
            raise ValueError(f"pinned skill snapshot too large: {skill_name}")
        output = target / relative_path
        ensure_creatable_inside(target, output, f"invalid pinned skill file path: {skill_name}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
    if not (target / "SKILL.md").is_file():
        raise ValueError(f"pinned skill missing SKILL.md: {skill_name}")
    actual_hash = skill_content_hash(target)
    if actual_hash != expected_hash:
        shutil.rmtree(target, ignore_errors=True)
        raise PinnedSkillMismatch(
            f"pinned skill content hash mismatch: {skill_name}",
            actual_content_hash=actual_hash,
        )
    return BuiltinSkill(
        name=skill_name,
        description=str(pin.get("description") or ""),
        path=target,
        version=expected_hash,
        source=pin.get("source") if isinstance(pin.get("source"), dict) else {},
        entry={"kind": "run-snapshot", "path": str(target)},
    )


def _select_pinned_skills(
    skills,
    allowed_skill_names: list[str],
    pins: dict[str, dict[str, Any]],
    snapshot_root: Path,
):
    selected = []
    mismatches = []
    by_name = {skill.name: skill for skill in skills}
    for skill_name in allowed_skill_names:
        skill = by_name.get(skill_name)
        pin = pins.get(skill_name)
        if not pin:
            mismatches.append(
                {
                    "skill_id": skill_name,
                    "expected_content_hash": "",
                    "actual_content_hash": skill.version if skill else "",
                    "reason": "missing_pinned_manifest",
                }
            )
            continue
        expected = str((pin or {}).get("content_hash") or (pin or {}).get("version") or "")
        if pin.get("files"):
            try:
                selected.append(_materialize_pinned_skill(skill_name, pin, snapshot_root))
            except PinnedSkillMismatch as exc:
                mismatches.append(
                    {
                        "skill_id": skill_name,
                        "expected_content_hash": expected,
                        "actual_content_hash": exc.actual_content_hash,
                        "reason": str(exc),
                    }
                )
            except (binascii.Error, ValueError) as exc:
                mismatches.append(
                    {
                        "skill_id": skill_name,
                        "expected_content_hash": expected,
                        "actual_content_hash": "",
                        "reason": str(exc),
                    }
            )
            continue
        if not expected:
            mismatches.append(
                {
                    "skill_id": skill_name,
                    "expected_content_hash": "",
                    "actual_content_hash": skill.version if skill else "",
                    "reason": "missing_pinned_content_hash",
                }
            )
            continue
        if not pin.get("files"):
            mismatches.append(
                {
                    "skill_id": skill_name,
                    "expected_content_hash": expected,
                    "actual_content_hash": skill.version if skill else "",
                    "reason": "missing_pinned_snapshot",
                }
            )
            continue
        if expected and (skill is None or skill.version != expected):
            mismatches.append(
                {
                    "skill_id": skill_name,
                    "expected_content_hash": expected,
                    "actual_content_hash": skill.version if skill else "",
                }
            )
            continue
    return selected, mismatches


def _pin_manifests_for_result(pins: dict[str, dict[str, Any]], allowed_skill_names: list[str]) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for skill_name in allowed_skill_names:
        pin = pins.get(skill_name)
        if not pin:
            continue
        manifest = {key: value for key, value in pin.items() if key != "files"}
        version = str(manifest.get("version") or pin.get("content_hash") or "")
        content_hash = str(manifest.get("content_hash") or pin.get("version") or version)
        manifest["version"] = version
        manifest["content_hash"] = content_hash
        manifest.setdefault("dependency_ids", [])
        manifest["allowed"] = bool(manifest.get("allowed", True))
        manifest["staged"] = False
        manifest["used"] = False
        manifests.append(manifest)
    return manifests


def _skill_manifests(selected_skills, *, used_skill_names: list[str], pins: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    used = set(used_skill_names)
    staged = {skill.name for skill in selected_skills}
    pinned_manifests = dict(pins or {})
    manifests = []
    for skill in selected_skills:
        pin = pinned_manifests.get(skill.name)
        if pin is not None:
            dependency_ids = [
                dependency_id
                for dependency_id in _string_list(pin.get("dependency_ids"))
                if dependency_id in staged
            ]
        else:
            dependency_ids = _skill_dependency_ids(skill.name, staged)
        manifests.append(
            {
                "skill_id": skill.name,
                "description": skill.description,
                "version": skill.version,
                "content_hash": skill.version,
                "source": skill.source,
                "dependency_ids": dependency_ids,
                "allowed": True,
                "staged": True,
                "used": skill.name in used,
            }
        )
    return manifests


def _skill_dependency_ids(skill_name: str, available: set[str]) -> list[str]:
    return skill_dependency_ids(skill_name, available)


def _with_skill_dependencies(selected: list[str], available: set[str]) -> list[str]:
    return with_skill_dependencies(selected, available)


def _artifact_content_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if lower.endswith(".json"):
        return "application/json"
    if lower.endswith(".txt"):
        return "text/plain; charset=utf-8"
    if lower.endswith(".md"):
        return "text/markdown; charset=utf-8"
    return "application/octet-stream"


def _artifact_type(filename: str, skill_id: str | None = None) -> str:
    lower = filename.lower()
    if skill_id == "qa-file-reviewer" and lower.endswith(".docx"):
        return "reviewed_docx"
    if skill_id == "baoyu-translate" and lower.endswith(".docx"):
        return "translated_docx"
    if lower.endswith(".docx"):
        return "result_docx"
    if lower.endswith(".json"):
        return "result_json"
    if lower.endswith(".txt") or lower.endswith(".md"):
        return "report_txt"
    return "runtime_file"


def _artifact_label(filename: str, artifact_type: str) -> str:
    if artifact_type == "reviewed_docx":
        return "审核 Word"
    if artifact_type == "translated_docx":
        return "翻译 Word"
    if artifact_type == "result_docx":
        return "Word 文件"
    if artifact_type == "result_json":
        return "结果 JSON"
    if artifact_type == "report_txt":
        return "详细报告"
    return filename


def _resume_completed_step_outputs(input_payload: dict[str, object]) -> dict[str, str]:
    resume = input_payload.get("resume")
    if not isinstance(resume, dict):
        return {}
    outputs = resume.get("completed_step_outputs")
    if not isinstance(outputs, dict):
        return {}
    return {str(key): str(value) for key, value in outputs.items() if value is not None}


def _resume_completed_step_checkpoints(input_payload: dict[str, object]) -> dict[str, dict[str, object]]:
    resume = input_payload.get("resume")
    if not isinstance(resume, dict):
        return {}
    checkpoints = resume.get("completed_step_checkpoints")
    if not isinstance(checkpoints, dict):
        return {}
    result: dict[str, dict[str, object]] = {}
    for key, value in checkpoints.items():
        if isinstance(value, dict):
            result[str(key)] = dict(value)
    return result


def _resume_checkpoint_lineage(
    completed_checkpoints: dict[str, dict[str, object]],
    *,
    step_key: str,
    copied_from_run_id: object,
) -> dict[str, str]:
    checkpoint = completed_checkpoints.get(step_key)
    if not isinstance(checkpoint, dict):
        return {}
    lineage = artifact_lineage_contract(
        {
            "checkpoint_id": checkpoint.get("checkpoint_id"),
            "source_step_id": checkpoint.get("source_step_id"),
        },
        source_run_id=checkpoint.get("copied_from_run_id") or copied_from_run_id,
    )
    checkpoint_id = lineage.get("checkpoint_id")
    source_step_id = lineage.get("source_step_id")
    source_run_id = lineage.get("source_run_id")
    if not checkpoint_id or not source_step_id:
        return {}
    result = {
        "checkpoint_id": str(checkpoint_id),
        "source_step_id": str(source_step_id),
    }
    if source_run_id:
        result["copied_from_run_id"] = str(source_run_id)
    return result


def _completed_step_checkpoint_payload(payload: RunPayload, *, step_index: int) -> dict[str, str]:
    checkpoint_id = artifact_lineage_contract(
        {"checkpoint_id": f"checkpoint-{payload.run_id}-step-{step_index}"}
    ).get("checkpoint_id")
    if not checkpoint_id:
        return {}
    return {"checkpoint_id": str(checkpoint_id)}


def _resume_copied_from_run_id(input_payload: dict[str, object]) -> str | None:
    resume = input_payload.get("resume")
    if not isinstance(resume, dict):
        return None
    copied_from_run_id = resume.get("copied_from_run_id")
    return str(copied_from_run_id) if copied_from_run_id else None


def _is_file_skill_execution_step(step: dict[str, object]) -> bool:
    value = f"{step.get('step_key', '')} {step.get('role', '')}".lower()
    return any(token in value for token in ("review", "translate", "answer", "execute"))


def _non_execution_step_output(
    *,
    step: dict[str, object],
    payload: RunPayload,
    skill_result: ExecutorResult | None,
) -> str:
    step_key = str(step.get("step_key") or "")
    role = str(step.get("role") or step_key or "agent")
    if step_key == "inspect" or role == "inspect":
        return f"Input inspected: {len(payload.file_ids)} file(s)."
    if step_key == "verify" or role == "verify":
        artifact_count = len(skill_result.artifacts) if skill_result else 0
        return f"Verification completed: {artifact_count} artifact(s) prepared."
    return f"{role} step completed."
