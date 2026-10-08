import inspect
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, ClassVar

from app import control_plane_contracts as run_controls
from app.context_builder import executor_context_pack_from_snapshot
from app.context.api import (
    ContextFileContentError,
    context_file_executor_failure,
    manifest_with_worker_attachment_metadata,
)
from app.bootstrap.context import (
    ContextRetrievalAuthority,
    ContextRetrievalIdentity,
    materialize_worker_context_files,
    worker_context_retrieval_authority,
)
from app.context_manifest import CONTEXT_MANIFEST_SCHEMA_VERSION
from app.control_plane_contracts import (
    LEGACY_SYNTHETIC_CHAT_SKILL_ID,
    RUN_EXECUTION_KIND_HARNESS_CHAT,
    RUN_EXECUTION_KIND_SKILL,
    standard_trace_id,
)
from app.execution_boundary import (
    CLAUDE_WORKER_EXECUTOR,
    ExecutionBoundaryDecision,
    decide_execution_boundary,
)
from app.executors.claude.capability_policy import (
    CapabilityExecutionPlan,
    _canonical_tool_policy_subjects,
    sandbox_runtime_tool_policy_subjects,
)
from app.executors.base import (
    ArtifactManifest,
    ExecutorDispatchAccepted,
    ExecutorEventSink,
    ExecutorResult,
    RunExecutionOwner,
    RunPayload,
)
from app.executors.claude_agent_sdk_runner import project_sdk_turn_diagnostics
from app.executors.claude.prompts import (
    CurrentRequestTooLargeError,
    build_skill_prompt,
    build_harness_chat_prompt, compose_system_prompt,
)
from app.execution import api as execution_api
from app.execution.api import (
    collect_workspace_artifacts,
    runtime_terminal_payload,
)
from app.path_safety import ensure_creatable_inside, ensure_path_inside
from app.required_tool_contract import (
    capability_invocation_completion_decision,
    validate_runtime_tool_evidence,
)
from app.runtime.event_bridge import agent_event_to_executor_event
from app.runtime.sandbox.callback_tokens import (
    CallbackTokenBinding,
    callback_token_id_for_binding,
    executor_callback_url as _sandbox_callback_url,
)
from app.runtime.sandbox.container_provider import (
    DockerContainerProvider,
    FakeContainerProvider,
    OpenSandboxContainerProvider,
)
from app.runtime.sandbox.contracts import (
    ContextRetrievalScope,
    ModelTokenLimits,
    SandboxRuntimeRequest,
)
from app.runtime.sandbox.runtime import SandboxRuntime
from app.settings import get_settings
from app.skills.api import (
    AuthorizedSkillCatalogError,
    BuiltinSkill,
    SkillStager,
    materialize_worker_pinned_skill,
    merged_worker_pinned_manifests,
    resolve_worker_runtime_catalog,
    worker_catalog_public_metadata,
    worker_pinned_manifests,
    pin_manifests_for_result,
    select_pinned_skill_snapshots,
    skill_manifests_from_catalog,
    staged_skill_manifests,
)
from app.storage import ObjectStorage

_SANDBOX_SUCCESS_TERMINAL_STATUSES = {"completed", "succeeded"}
_TOOL_PERMISSION_POLL_INTERVAL_SECONDS = 0.25


async def _emit_public_progress_event(
    event_sink: ExecutorEventSink | None,
    *,
    event_type: str,
    stage: str,
    message: str,
) -> None:
    """Emit one fixed public-safe progress fact without executor-owned detail."""

    if event_sink is None:
        return
    await event_sink(
        event_type=event_type,
        stage=stage,
        message=message,
        payload={"visible_to_user": True, "severity": "info"},
    )


def _capability_execution_error(
    payload: RunPayload,
    evidence: object,
    *,
    available_skill_identities: object = (),
) -> str | None:
    """Validate only the authorized capability invocations that actually occurred."""

    plan = CapabilityExecutionPlan.from_tool_policy_subjects(
        payload.input.get("_runtime_tool_policy_subjects"),
        available_skill_identities=available_skill_identities,
    )
    authorized_subjects = _canonical_tool_policy_subjects(
        payload.input.get("_runtime_tool_policy_subjects")
    )
    allowed_terminal_failures = {
        ("skill", identity)
        for kind, identity in plan.available
        if kind == "skill"
    }
    allowed_terminal_failures.update(
        ("mcp", identity)
        for identity, subject in authorized_subjects.items()
        if ("mcp", identity) in plan.available
        and subject.get("write_capable") is False
    )
    decision = capability_invocation_completion_decision(
        plan.available,
        binding={
            "tenant_id": payload.tenant_id,
            "workspace_id": payload.workspace_id,
            "user_id": payload.user_id,
            "session_id": payload.session_id,
            "run_id": payload.run_id,
            "attempt_id": payload.attempt_id,
        },
        evidence=evidence,
        allow_terminal_failure_capabilities=allowed_terminal_failures,
    )
    return None if decision.allowed else decision.reason


@dataclass(frozen=True)
class _AuthorizedAttachmentMetadata:
    """Authorized attachment metadata that never requires reading object bytes."""

    file_id: str
    file_name: str
    content_type: str
    size_bytes: int


@dataclass(frozen=True)
class PreparedSandboxFinalization:
    """Non-secret context required to normalize an asynchronous terminal receipt."""

    workspace: Path
    allowed_skill_names: list[str]
    staged_skill_names: list[str]
    skill_manifests: list[dict[str, Any]]
    public_skill_metadata: dict[str, dict[str, str]]


@dataclass(frozen=True)
class _PersistedSandboxRuntimeResult:
    status: str
    provider: str
    executor_response: dict[str, Any]
    timings: dict[str, Any]


@dataclass(frozen=True)
class PreparedSdkRun:
    """Resolved SDK staging inputs that can run locally or via SandboxRuntime."""

    workspace: Path
    file_names: list[str]
    selected_skills: list[BuiltinSkill]
    pinned_manifests: dict[str, dict[str, Any]]
    allowed_skill_names: list[str]
    staged_skill_names: list[str]
    prompt: str
    system_prompt: str = ""
    public_skill_metadata: dict[str, dict[str, str]] = field(default_factory=dict)
    attachment_metadata: list[_AuthorizedAttachmentMetadata] = field(default_factory=list)
    materialized_file_names: list[str] | None = None


class _MaterializedFileNames(list[str]):
    def __init__(
        self,
        values: list[str],
        *,
        attachment_metadata: list[_AuthorizedAttachmentMetadata] | None = None,
        materialized_file_names: list[str] | None = None,
    ) -> None:
        super().__init__(values)
        self.attachment_metadata = list(attachment_metadata or [])
        self.materialized_file_names = list(
            values if materialized_file_names is None else materialized_file_names
        )


def _execution_tier(payload: RunPayload) -> str:
    for source in (payload.context_pack, payload.context_snapshot, payload.input):
        if not isinstance(source, dict):
            continue
        value = source.get("execution_tier")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _execution_boundary_decision(payload: RunPayload) -> ExecutionBoundaryDecision:
    return decide_execution_boundary(
        executor_type=CLAUDE_WORKER_EXECUTOR,
        execution_mode=str(payload.input.get("execution_mode") or ""),
        execution_tier=_execution_tier(payload),
        mcp_requires_sandbox=any(
            kind == "mcp"
            for kind, _identity in CapabilityExecutionPlan.from_tool_policy_subjects(
                payload.input.get("_runtime_tool_policy_subjects")
            ).available
        ),
    )


def _ordinary_run_requires_sandbox(payload: RunPayload) -> bool:
    return _execution_boundary_decision(payload).requires_real_sandbox


def _sandbox_workspace(settings: object, payload: RunPayload) -> Path:
    return (
        Path(settings.sandbox_workspace_root)
        / "tenants"
        / payload.tenant_id
        / "workspaces"
        / payload.workspace_id
        / "users"
        / payload.user_id
        / "sessions"
        / payload.session_id
        / "runs"
        / payload.run_id
        / "attempts"
        / payload.attempt_id
        / "workspace"
    )


def _pinned_snapshot_root(workspace: Path) -> Path:
    return workspace / ".pins"


def _runtime_provider(result: object) -> str:
    return str(getattr(result, "provider", "") or "").strip()


def _sandbox_runtime_provider(runtime: object) -> str:
    provider = getattr(runtime, "provider", None)
    if isinstance(provider, DockerContainerProvider):
        return "docker"
    if isinstance(provider, OpenSandboxContainerProvider):
        return "opensandbox"
    if isinstance(provider, FakeContainerProvider):
        return "fake"
    return ""


def _context_manifest_from_pack(context_pack: dict[str, Any]) -> dict[str, Any] | None:
    manifest = context_pack.get("context_manifest")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != CONTEXT_MANIFEST_SCHEMA_VERSION:
        return None
    return manifest


def _runtime_request_skill_ids(
    payload: RunPayload, prepared: PreparedSdkRun
) -> list[str]:
    return list(
        dict.fromkeys(
            skill_id
            for skill_id in [payload.skill_id, *prepared.staged_skill_names]
            if skill_id
        )
    )


def _public_sdk_turn_diagnostics(
    payload: RunPayload,
    value: object,
    *,
    error_code: str | None,
    used_skill_ids: list[str],
    public_skill_metadata: dict[str, dict[str, str]],
) -> dict[str, Any]:
    return project_sdk_turn_diagnostics(
        value,
        error_code=error_code,
        selected_skill_id=(
            payload.skill_id
            if payload.skill_id != LEGACY_SYNTHETIC_CHAT_SKILL_ID
            else ""
        )
        or "",
        used_skill_ids=used_skill_ids,
        public_skill_metadata=public_skill_metadata,
    )


def _payload_sandbox_mode(payload: RunPayload) -> str:
    return "persistent" if payload.input.get("sandbox_mode") == "persistent" else "ephemeral"


def _payload_resource_limits(payload: RunPayload) -> dict[str, Any]:
    resource_limits = payload.input.get("resource_limits")
    return dict(resource_limits) if isinstance(resource_limits, dict) else {}


def _payload_queue_wait_ms(payload: RunPayload) -> int:
    value = payload.input.get("queue_wait_ms")
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


async def _submit_sandbox_runtime(
    runtime: SandboxRuntime,
    request: SandboxRuntimeRequest,
    *,
    event_sink: Any,
    execution_owner: RunExecutionOwner | None,
):
    """Call the runtime seam compatibly while threading ownership when supported."""

    try:
        parameters = inspect.signature(runtime.submit).parameters.values()
    except (TypeError, ValueError):
        parameters = ()
    accepts_owner = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        or parameter.name == "execution_owner"
        for parameter in parameters
    )
    kwargs = {"event_sink": event_sink}
    if accepts_owner:
        kwargs["execution_owner"] = execution_owner
    return await runtime.submit(request, **kwargs)


class ClaudeAgentWorkerAdapter:
    adapter_version = "claude-agent-worker-adapter/1"
    executor_type = CLAUDE_WORKER_EXECUTOR
    executor_version = "claude-agent-sdk-poc"
    capabilities: ClassVar[dict[str, bool]] = {
        "artifacts": True,
        "streaming": True,
        "tools": True,
        "skills": True,
    }

    def _run_capabilities(self, payload: RunPayload) -> dict[str, bool]:
        return {
            **self.capabilities,
            "platform_skills": payload.execution_kind == RUN_EXECUTION_KIND_SKILL,
        }

    async def submit_run(
        self,
        payload: RunPayload,
        event_sink: ExecutorEventSink | None = None,
        execution_owner: RunExecutionOwner | None = None,
    ) -> ExecutorResult | ExecutorDispatchAccepted:
        decision = _execution_boundary_decision(payload)
        if decision.fail_closed:
            return ExecutorResult(
                status="failed",
                adapter_version=self.adapter_version,
                executor_type=self.executor_type,
                executor_version=self.executor_version,
                capabilities=self._run_capabilities(payload),
                result={
                    "message": "Claude worker execution boundary rejected the run.",
                    "error_code": decision.reason,
                    "sdk_used": False,
                    "delegate_used": False,
                    "worker_boundary": self.executor_type,
                },
                executor_payload={
                    "sdk_used": False,
                    "delegate_used": False,
                    "worker_boundary": self.executor_type,
                    "execution_boundary": decision.reason,
                },
            )
        settings = get_settings()
        configured_provider = str(getattr(settings, "sandbox_container_provider", "") or "").strip()
        if decision.requires_real_sandbox and configured_provider not in decision.accepted_providers:
            return self._sandbox_provider_required_result(
                payload=payload,
                sandbox_provider=configured_provider,
                runtime_started=False,
            )
        if not bool(getattr(settings, "claude_agent_sdk_enabled", False)):
            return self._sdk_required_result(payload, sdk_result=None)
        sandbox_runtime = SandboxRuntime(workspace_root=settings.sandbox_workspace_root)
        actual_provider = _sandbox_runtime_provider(sandbox_runtime)
        if actual_provider not in decision.accepted_providers:
            return self._sandbox_provider_required_result(
                payload=payload,
                sandbox_provider=actual_provider,
                runtime_started=False,
            )

        try:
            sdk_result = await self._run_with_staged_skills(
                payload,
                event_sink=event_sink,
                sandbox_runtime=sandbox_runtime,
                execution_owner=execution_owner,
            )
        except ContextFileContentError as exc:
            return self._context_file_failure_result(payload=payload, error=exc)
        if sdk_result is not None:
            return sdk_result

        return self._sdk_required_result(payload, sdk_result=None)

    def _sdk_required_result(self, payload: RunPayload, sdk_result) -> ExecutorResult:
        error_code = execution_api.claude_sdk_failure_code(sdk_result)
        sdk_used = bool(sdk_result and sdk_result.used_sdk)
        sdk_error = sdk_result.error if sdk_result else "claude_agent_sdk_disabled"
        turn_diagnostics = _public_sdk_turn_diagnostics(
            payload,
            getattr(sdk_result, "turn_diagnostics", {}) if sdk_result else {},
            error_code=error_code,
            used_skill_ids=list(getattr(sdk_result, "used_skills", []) or []) if sdk_result else [],
            public_skill_metadata={},
        )
        return ExecutorResult(
            status="failed",
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities=self._run_capabilities(payload),
            result={
                "message": (
                    "Claude Agent SDK is required for Harness chat."
                    if payload.execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT
                    else "Claude Agent SDK with the authorized Skill is required for this run."
                ),
                "error_code": error_code,
                "skill_id": payload.skill_id,
                "sdk_used": sdk_used,
                "sdk_error": sdk_error,
                "delegate_used": False,
                "worker_boundary": self.executor_type,
                "sdk_turn_diagnostics": turn_diagnostics,
            },
            executor_payload={
                "sdk_used": sdk_used,
                "sdk_error": sdk_error,
                "delegate_used": False,
                "worker_boundary": self.executor_type,
                "sdk_turn_diagnostics": turn_diagnostics,
            },
        )

    def _agent_profile_system_prompt(self, payload: RunPayload) -> str:
        """Return profile instructions only for the SDK/runtime system channel."""

        value = payload.agent_profile.get("instructions") if isinstance(payload.agent_profile, dict) else None
        return value if isinstance(value, str) and value else ""

    def _executor_context_pack(self, payload: RunPayload) -> dict[str, Any]:
        pack = payload.context_pack
        if pack.get("schema_version") != "ai-platform.executor-context-pack.v1":
            return executor_context_pack_from_snapshot(payload.context_snapshot)
        if "prompt_summary" in pack or "context_manifest" in pack:
            return pack
        provider_context = pack.get("conversation_context")
        snapshot_pack = executor_context_pack_from_snapshot(payload.context_snapshot)
        return ({**snapshot_pack, "conversation_context": provider_context}
                if isinstance(provider_context, dict) else pack)

    def _context_retrieval_for_payload(
        self,
        payload: RunPayload,
        context_pack: dict[str, Any],
        workspace: Path,
    ) -> tuple[ContextRetrievalAuthority | None, ContextRetrievalIdentity | None]:
        scope = self._context_retrieval_scope_for_payload(payload, context_pack)
        if scope is None:
            return None, None
        return (
            worker_context_retrieval_authority(workspace),
            ContextRetrievalIdentity(**scope.model_dump()),
        )

    def _context_retrieval_scope_for_payload(
        self,
        payload: RunPayload,
        context_pack: dict[str, Any],
    ) -> ContextRetrievalScope | None:
        if _context_manifest_from_pack(context_pack) is None:
            return None
        return ContextRetrievalScope(
            tenant_id=payload.tenant_id,
            workspace_id=payload.workspace_id,
            user_id=payload.user_id,
            session_id=payload.session_id,
            run_id=payload.run_id,
            agent_id=payload.agent_id,
        )

    def _authorized_skill_catalog_failure_result(self, error_code: str) -> ExecutorResult:
        return ExecutorResult(
            status="failed",
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities={**self.capabilities, "platform_skills": True},
            result={
                "message": "Authorized Skill catalog validation failed. Please retry.",
                "error_code": error_code,
                "sdk_used": False,
                "sdk_error": error_code,
                "delegate_used": False,
                "worker_boundary": self.executor_type,
                "allowed_skills": [],
                "staged_skills": [],
                "used_skills": [],
            },
            artifacts=[],
            executor_payload={
                "sdk_used": False,
                "sdk_error": error_code,
                "delegate_used": False,
                "worker_boundary": self.executor_type,
                "allowed_skills": [],
                "staged_skills": [],
                "used_skills": [],
            },
        )

    async def _prepare_sdk_run(
        self,
        payload: RunPayload,
        event_sink: ExecutorEventSink | None = None,
        *,
        workspace: Path | None = None,
        workspace_root: str | Path | None = None,
    ) -> tuple[PreparedSdkRun | None, ExecutorResult | None]:
        settings = get_settings()
        resolved_workspace = workspace or _run_workspace(settings, payload)
        resolved_workspace_root = workspace_root or settings.claude_agent_workspace_root
        _prepare_run_workspace(resolved_workspace_root, resolved_workspace)
        materialized_file_names = await self._materialize_files(payload, resolved_workspace)
        file_names = list(materialized_file_names)
        raw_attachment_metadata = getattr(materialized_file_names, "attachment_metadata", [])
        attachment_metadata = (
            list(raw_attachment_metadata)
            if isinstance(raw_attachment_metadata, list)
            and len(raw_attachment_metadata) == len(file_names)
            and all(
                isinstance(item, _AuthorizedAttachmentMetadata)
                for item in raw_attachment_metadata
            )
            else []
        )
        raw_staged_file_names = getattr(
            materialized_file_names,
            "materialized_file_names",
            None,
        )
        staged_file_names = (
            list(raw_staged_file_names)
            if isinstance(raw_staged_file_names, list)
            and all(isinstance(item, str) for item in raw_staged_file_names)
            else list(file_names)
        )

        if payload.execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT:
            authorized_catalog = None
            pinned_manifests: dict[str, dict[str, Any]] = {}
            skills: list[BuiltinSkill] = []
            allowed_skill_names: list[str] = []
            selected_skills: list[BuiltinSkill] = []
            pin_mismatches: list[dict[str, str]] = []
        else:
            try:
                authorized_catalog = resolve_worker_runtime_catalog(payload)
                pinned_manifests = merged_worker_pinned_manifests(
                    payload,
                    authorized_catalog,
                )
            except AuthorizedSkillCatalogError:
                return None, self._authorized_skill_catalog_failure_result(
                    "authorized_skill_catalog_invalid"
                )
            if (
                authorized_catalog is not None
                and payload.skill_id != LEGACY_SYNTHETIC_CHAT_SKILL_ID
                and payload.skill_id not in authorized_catalog.materialized_skill_ids
            ):
                return None, self._authorized_skill_catalog_failure_result(
                    "authorized_skill_selected_unavailable"
                )
            skills = []
            available_names = list(pinned_manifests)
            allowed_skill_names = execution_api.select_execution_skill_names(
                selected_skill_id=payload.skill_id,
                requested_skill_ids=(
                    _string_list(payload.input.get("skill_ids")) if authorized_catalog is None else []
                ),
                available_skill_ids=available_names,
                pinned_manifests=worker_pinned_manifests(payload) if authorized_catalog is None else {},
                authorized_skill_ids=(
                    authorized_catalog.materialized_skill_ids if authorized_catalog is not None else None
                ),
            )
            selected_skills, pin_mismatches = select_pinned_skill_snapshots(
                skills,
                allowed_skill_names,
                pinned_manifests,
                _pinned_snapshot_root(resolved_workspace),
                materialize=materialize_worker_pinned_skill,
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
            return None, ExecutorResult(
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
                    "skill_manifests": pin_manifests_for_result(pinned_manifests, allowed_skill_names),
                    "pin_mismatches": pin_mismatches,
                },
            )
        staged_skill_names = (
            []
            if payload.execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT
            else SkillStager().stage_skills(
                workspace=resolved_workspace,
                skills=selected_skills,
            )
        )
        if selected_skills:
            await _emit_public_progress_event(
                event_sink,
                event_type="skill_selected",
                stage="skills",
                message="Selected authorized Skill is ready",
            )

        prompt_context_pack = self._executor_context_pack(payload)
        prompt_context_manifest = _context_manifest_from_pack(prompt_context_pack)
        if prompt_context_manifest is not None:
            prompt_context_pack = dict(prompt_context_pack)
            prompt_context_pack["context_manifest"] = (
                manifest_with_worker_attachment_metadata(
                    prompt_context_manifest,
                    attachment_metadata,
                )
            )
        prompt_builder_kwargs = {
            "user_message": str(
                payload.input.get("message") or payload.input.get("prompt") or ""
            ),
            "file_names": file_names,
            "context_pack": prompt_context_pack,
        }
        try:
            control_prompt = (
                build_harness_chat_prompt(**prompt_builder_kwargs)
                if payload.execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT
                else build_skill_prompt(
                    skill_id=str(payload.skill_id),
                    authorized_skill_catalog=(
                        authorized_catalog.snapshot
                        if authorized_catalog is not None
                        else None
                    ),
                    **prompt_builder_kwargs,
                )
            )
        except CurrentRequestTooLargeError:
            return None, ExecutorResult(
                status="failed",
                adapter_version=self.adapter_version,
                executor_type=self.executor_type,
                executor_version=self.executor_version,
                capabilities=self.capabilities,
                result={
                    "message": "Current request exceeds the execution limit",
                    "error_code": "current_request_too_large",
                    "sdk_used": False,
                    "sdk_error": "current_request_too_large",
                    "delegate_used": False,
                    "worker_boundary": self.executor_type,
                },
                artifacts=[],
                executor_payload={
                    "sdk_used": False,
                    "sdk_error": "current_request_too_large",
                    "delegate_used": False,
                    "worker_boundary": self.executor_type,
                },
            )
        return (
            PreparedSdkRun(
                workspace=resolved_workspace,
                file_names=file_names,
                selected_skills=selected_skills,
                pinned_manifests=pinned_manifests,
                allowed_skill_names=allowed_skill_names,
                staged_skill_names=staged_skill_names,
                public_skill_metadata=worker_catalog_public_metadata(
                    authorized_catalog
                ),
                prompt=prompt_builder_kwargs["user_message"],
                system_prompt=compose_system_prompt(self._agent_profile_system_prompt(payload), control_prompt),
                attachment_metadata=attachment_metadata,
                materialized_file_names=staged_file_names,
            ),
            None,
        )

    async def _submit_prepared_run_to_sandbox_runtime(
        self,
        payload: RunPayload,
        prepared: PreparedSdkRun,
        *,
        event_sink: ExecutorEventSink | None = None,
        sandbox_runtime: SandboxRuntime | None = None,
        execution_owner: RunExecutionOwner | None = None,
    ) -> ExecutorResult | ExecutorDispatchAccepted:
        settings = get_settings()
        context_pack = self._executor_context_pack(payload)
        context_manifest = _context_manifest_from_pack(context_pack)
        runtime_context_manifest = manifest_with_worker_attachment_metadata(
            context_manifest,
            prepared.attachment_metadata,
        )
        runtime_context_manifest = dict(runtime_context_manifest or {})
        runtime_context_manifest["queue_attempt_id"] = payload.attempt_id
        adapter_reconciliation_context = {
            "schema_version": "ai-platform.claude-agent-reconciliation-context.v1",
            "run_payload": execution_api.sandbox_reconciliation_payload(payload),
            "workspace": str(prepared.workspace),
            "allowed_skill_names": list(prepared.allowed_skill_names),
            "staged_skill_names": list(prepared.staged_skill_names),
            "skill_manifests": staged_skill_manifests(
                prepared.selected_skills,
                used_skill_names=[],
                pins=prepared.pinned_manifests,
            ),
            "public_skill_metadata": dict(prepared.public_skill_metadata),
        }
        reconciliation_context = {
            "schema_version": "ai-platform.executor-reconciliation.v1",
            "adapter_name": "claude-agent-worker",
            "run_payload": execution_api.sandbox_reconciliation_payload(payload),
            "adapter_context": adapter_reconciliation_context,
        }
        runtime_skill_ids = _runtime_request_skill_ids(payload, prepared)
        request = SandboxRuntimeRequest(
            tenant_id=payload.tenant_id,
            workspace_id=payload.workspace_id,
            user_id=payload.user_id,
            session_id=payload.session_id,
            run_id=payload.run_id,
            attempt_id=payload.attempt_id,
            agent_id=payload.agent_id,
            skill_ids=runtime_skill_ids,
            public_skill_metadata={
                skill_id: dict(prepared.public_skill_metadata[skill_id])
                for skill_id in runtime_skill_ids
                if skill_id in prepared.public_skill_metadata
            },
            mcp_tool_ids=_string_list(payload.input.get("mcp_tool_ids")),
            tool_policy_subjects=sandbox_runtime_tool_policy_subjects(
                payload,
                runtime_context_manifest,
                sandbox_provider=str(settings.sandbox_container_provider),
            ),
            input_message=prepared.prompt,
            system_prompt=prepared.system_prompt,
            file_ids=payload.file_ids,
            materialized_file_names=(
                prepared.file_names
                if prepared.materialized_file_names is None
                else prepared.materialized_file_names
            ),
            sandbox_mode=_payload_sandbox_mode(payload),
            browser_enabled=bool(payload.input.get("browser_enabled")),
            model_token_limits=(
                ModelTokenLimits(
                    max_input_tokens=payload.model_max_input_tokens,
                    max_output_tokens=payload.model_max_output_tokens,
                )
                if payload.model_max_input_tokens is not None
                and payload.model_max_output_tokens is not None
                else None
            ),
            **run_controls.executor_model_controls(payload, getattr(settings, "claude_agent_model", ""), "model"),
            resource_limits=_payload_resource_limits(payload),
            queue_wait_ms=_payload_queue_wait_ms(payload),
            trace_id=payload.trace_id or standard_trace_id(payload.run_id),
            callback_url=_sandbox_callback_url(settings),
            owner_generation=payload.owner_generation,
            callback_token_id=callback_token_id_for_binding(
                CallbackTokenBinding(run_id=payload.run_id, attempt_id=payload.attempt_id)
            ),
            context_manifest=runtime_context_manifest,
            context_retrieval_scope=self._context_retrieval_scope_for_payload(payload, context_pack),
            **execution_api.claude_provider_session_dispatch(payload, context_pack),
            governed_permission_wait=False,
            reconciliation_context=reconciliation_context,
        )
        runtime = sandbox_runtime or SandboxRuntime(workspace_root=settings.sandbox_workspace_root)
        runtime_event_sink = None
        if event_sink is not None:

            async def runtime_event_sink(agent_event):
                await event_sink(**agent_event_to_executor_event(agent_event))

        await _emit_public_progress_event(
            event_sink,
            event_type="run_started",
            stage="runtime",
            message="Sandbox runtime dispatch is active",
        )
        runtime_result = await _submit_sandbox_runtime(
            runtime,
            request,
            event_sink=runtime_event_sink,
            execution_owner=execution_owner,
        )
        if str(getattr(runtime_result, "status", "") or "").lower() == "accepted":
            return ExecutorDispatchAccepted(
                run_id=payload.run_id,
                attempt_id=payload.attempt_id,
                lease_id=str(getattr(runtime_result, "lease_id", "") or ""),
                provider=_runtime_provider(runtime_result),
                adapter_context=adapter_reconciliation_context,
                timings=dict(getattr(runtime_result, "timings", {}) or {}),
            )
        return self._executor_result_from_sandbox_runtime(payload, prepared, runtime_result)

    def _sandbox_provider_required_result(
        self,
        *,
        payload: RunPayload,
        sandbox_provider: str,
        runtime_started: bool,
        runtime_terminal_status: str = "",
    ) -> ExecutorResult:
        return ExecutorResult(
            status="failed",
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities=self._run_capabilities(payload),
            result={
                "message": "A real sandbox provider is required for Claude worker execution.",
                "error_code": "sandbox_real_provider_required",
                "sdk_used": False,
                "delegate_used": False,
                "worker_boundary": self.executor_type,
            },
            artifacts=[],
            executor_payload={
                "sandbox_provider": sandbox_provider,
                "sandbox_runtime_used": runtime_started,
                "runtime_terminal_status": runtime_terminal_status,
            },
        )

    def _context_file_failure_result(
        self,
        *,
        payload: RunPayload,
        error: ContextFileContentError,
    ) -> ExecutorResult:
        safe_error_code, message, diagnostic = context_file_executor_failure(error)
        return ExecutorResult(
            status="failed",
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities=self._run_capabilities(payload),
            result={
                "message": message,
                "error_code": safe_error_code,
                "sdk_used": False,
                "delegate_used": False,
                "worker_boundary": self.executor_type,
            },
            artifacts=[],
            executor_payload={
                "sdk_used": False,
                "delegate_used": False,
                "worker_boundary": self.executor_type,
                "context_file_failure": diagnostic,
            },
        )

    def reconcile_sandbox_terminal(
        self,
        payload: RunPayload,
        *,
        adapter_context: dict[str, Any],
        terminal_result: dict[str, Any],
        provider: str,
        timings: dict[str, Any] | None = None,
    ) -> ExecutorResult:
        if adapter_context.get("schema_version") != "ai-platform.claude-agent-reconciliation-context.v1":
            raise ValueError("sandbox_reconciliation_context_schema_invalid")
        workspace_value = str(adapter_context.get("workspace") or "").strip()
        if not workspace_value:
            raise ValueError("sandbox_reconciliation_workspace_missing")
        prepared = PreparedSandboxFinalization(
            workspace=Path(str(adapter_context.get("_artifact_workspace") or workspace_value)),
            allowed_skill_names=_string_list(adapter_context.get("allowed_skill_names")),
            staged_skill_names=_string_list(adapter_context.get("staged_skill_names")),
            skill_manifests=[
                dict(item)
                for item in adapter_context.get("skill_manifests", [])
                if isinstance(item, dict)
            ],
            public_skill_metadata={
                str(key): dict(value)
                for key, value in dict(adapter_context.get("public_skill_metadata") or {}).items()
                if isinstance(value, dict)
            },
        )
        storage_scope = str(adapter_context.get("_artifact_storage_scope") or "")
        abandoned = adapter_context.get("_artifact_collection_abandoned")
        if abandoned is not None and not isinstance(abandoned, threading.Event):
            raise ValueError("artifact collection abandonment signal is invalid")
        reserve_artifact_storage = adapter_context.get("_reserve_artifact_storage")
        if reserve_artifact_storage is not None and not callable(reserve_artifact_storage):
            raise ValueError("artifact storage reservation callback is invalid")
        runtime_result = _PersistedSandboxRuntimeResult(
            status=str(terminal_result.get("status") or ""),
            provider=provider,
            executor_response=dict(terminal_result),
            timings=dict(timings or {}),
        )
        return self._executor_result_from_sandbox_runtime(
            payload,
            prepared,
            runtime_result,
            storage_scope=storage_scope,
            abandoned=abandoned,
            reserve_storage=reserve_artifact_storage,
        )

    def _executor_result_from_sandbox_runtime(
        self,
        payload: RunPayload,
        prepared: PreparedSdkRun | PreparedSandboxFinalization,
        runtime_result: object,
        *,
        storage_scope: str = "",
        abandoned: threading.Event | None = None,
        reserve_storage: Callable[[str], str] | None = None,
    ) -> ExecutorResult:
        executor_response = (
            dict(getattr(runtime_result, "executor_response", {}))
            if isinstance(getattr(runtime_result, "executor_response", {}), dict)
            else {}
        )
        runtime_status = str(
            executor_response.get("status") or getattr(runtime_result, "status", "") or ""
        ).strip().lower()
        sandbox_provider = _runtime_provider(runtime_result)
        decision = _execution_boundary_decision(payload)
        if sandbox_provider not in decision.accepted_providers:
            return self._sandbox_provider_required_result(
                payload=payload,
                sandbox_provider=sandbox_provider,
                runtime_started=True,
                runtime_terminal_status=runtime_status,
            )
        capability_evidence = (
            executor_response.get("capability_evidence")
            if isinstance(executor_response.get("capability_evidence"), list)
            else []
        )
        runtime_sdk_result = type(
            "RuntimeSdkResult",
            (),
            {
                "used_skills": executor_response.get("used_skills"),
                "used_skills_source": executor_response.get("used_skills_source", ""),
            },
        )()
        selected_capability_error = _capability_execution_error(
            payload,
            capability_evidence,
            available_skill_identities=prepared.allowed_skill_names,
        )
        runtime_tool_evidence = validate_runtime_tool_evidence(
            executor_response,
            binding={
                "tenant_id": payload.tenant_id,
                "workspace_id": payload.workspace_id,
                "user_id": payload.user_id,
                "session_id": payload.session_id,
                "run_id": payload.run_id,
                "attempt_id": payload.attempt_id,
            },
            capability_evidence=capability_evidence,
            capability_error=selected_capability_error,
        )
        selected_capability_error = runtime_tool_evidence.error_code
        used_skill_names = _sdk_used_skill_names(
            runtime_sdk_result,
            prepared.staged_skill_names,
        )
        used_skills_source = _sdk_used_skills_source(runtime_sdk_result, used_skill_names)
        skill_manifests = (
            skill_manifests_from_catalog(
                prepared.skill_manifests,
                used_skill_names=used_skill_names,
            )
            if isinstance(prepared, PreparedSandboxFinalization)
            else staged_skill_manifests(
                prepared.selected_skills,
                used_skill_names=used_skill_names,
                pins=prepared.pinned_manifests,
            )
        )
        sandbox_timings = getattr(runtime_result, "timings", {})
        if not isinstance(sandbox_timings, dict):
            sandbox_timings = {}
        common_payload = {
            "sdk_used": bool(executor_response.get("sdk_used")),
            "sdk_usage": executor_response.get("sdk_usage", {}) or {},
            **runtime_terminal_payload(executor_response, runtime_status=runtime_status),
            "delegate_used": False,
            "worker_boundary": self.executor_type,
            "allowed_skills": prepared.allowed_skill_names,
            "staged_skills": prepared.staged_skill_names,
            "used_skills": used_skill_names,
            "used_skills_source": used_skills_source,
            "skill_manifests": skill_manifests,
            "sandbox_provider": sandbox_provider,
            "sandbox_runtime_used": True,
            "sandbox_timings": sandbox_timings,
            "capability_evidence": capability_evidence,
            **runtime_tool_evidence.private_payload(),
        }
        diagnostic_payload = execution_api.normalized_runtime_diagnostics_payload(
            executor_response.get("runtime_diagnostics")
        )
        failure_result_context = {
            "sdk_used": bool(executor_response.get("sdk_used")),
            "delegate_used": False,
            "worker_boundary": self.executor_type,
            "allowed_skills": prepared.allowed_skill_names,
            "staged_skills": prepared.staged_skill_names,
            "used_skills": used_skill_names,
            **diagnostic_payload,
        }
        if runtime_status in _SANDBOX_SUCCESS_TERMINAL_STATUSES and selected_capability_error is not None:
            turn_diagnostics = _public_sdk_turn_diagnostics(
                payload,
                executor_response.get("sdk_turn_diagnostics"),
                error_code=selected_capability_error,
                used_skill_ids=used_skill_names,
                public_skill_metadata=prepared.public_skill_metadata,
            )
            return ExecutorResult(
                status="failed",
                adapter_version=self.adapter_version,
                executor_type=self.executor_type,
                executor_version=self.executor_version,
                capabilities=self._run_capabilities(payload),
                result={
                    "message": "Capability execution evidence was incomplete. Please retry.",
                    "error_code": selected_capability_error,
                    "sdk_error": selected_capability_error,
                    **execution_api.sdk_failure_result_fields(turn_diagnostics),
                    **failure_result_context,
                },
                artifacts=[],
                executor_payload={
                    **common_payload,
                    "sdk_error": selected_capability_error,
                    "sdk_turn_diagnostics": turn_diagnostics,
                    **diagnostic_payload,
                },
            )
        if runtime_status == "accepted":
            error_code = "executor_missing_structured_terminal"
            message = "Sandbox executor returned without an authoritative terminal result"
            turn_diagnostics = _public_sdk_turn_diagnostics(
                payload,
                executor_response.get("sdk_turn_diagnostics"),
                error_code=error_code,
                used_skill_ids=used_skill_names,
                public_skill_metadata=prepared.public_skill_metadata,
            )
            return ExecutorResult(
                status="failed",
                adapter_version=self.adapter_version,
                executor_type=self.executor_type,
                executor_version=self.executor_version,
                capabilities=self._run_capabilities(payload),
                result={
                    "message": message,
                    "error_code": error_code,
                    "sdk_error": error_code,
                    **execution_api.sdk_failure_result_fields(turn_diagnostics),
                    **failure_result_context,
                },
                artifacts=[],
                executor_payload={
                    **common_payload,
                    "sdk_error": error_code,
                    "sdk_turn_diagnostics": turn_diagnostics,
                    **diagnostic_payload,
                },
            )
        if runtime_status not in _SANDBOX_SUCCESS_TERMINAL_STATUSES:
            error_code = str(executor_response.get("error_code") or "")
            if not error_code and runtime_status in {"cancelled", "canceled"}:
                error_code = "executor_cancelled"
            if not error_code:
                error_code = "executor_reported_failure"
            message = (
                "任务已取消"
                if runtime_status in {"cancelled", "canceled"}
                else execution_api.claude_sdk_failure_message(
                    type("SdkFailure", (), {"error": error_code})()
                )
            )
            sdk_error = error_code
            turn_diagnostics = _public_sdk_turn_diagnostics(
                payload,
                executor_response.get("sdk_turn_diagnostics"),
                error_code=error_code,
                used_skill_ids=used_skill_names,
                public_skill_metadata=prepared.public_skill_metadata,
            )
            return ExecutorResult(
                status="failed",
                adapter_version=self.adapter_version,
                executor_type=self.executor_type,
                executor_version=self.executor_version,
                capabilities=self._run_capabilities(payload),
                result={
                    "message": message,
                    "error_code": error_code,
                    "sdk_error": sdk_error,
                    **execution_api.sdk_failure_result_fields(turn_diagnostics),
                    **failure_result_context,
                },
                artifacts=[],
                executor_payload={
                    **common_payload,
                    "sdk_error": sdk_error,
                    "sdk_turn_diagnostics": turn_diagnostics,
                    **diagnostic_payload,
                },
            )

        artifacts = self._collect_workspace_artifacts(
            payload,
            Path(str(getattr(runtime_result, "artifact_workspace_path", "") or prepared.workspace)),
            response_files=executor_response.get("response_files", []),
            response_file_descriptors=executor_response.get("response_file_descriptors"),
            allowed_skill_names=prepared.staged_skill_names,
            storage_scope=storage_scope,
            abandoned=abandoned,
            reserve_storage=reserve_storage,
        )
        turn_diagnostics = _public_sdk_turn_diagnostics(
            payload,
            executor_response.get("sdk_turn_diagnostics"),
            error_code=None,
            used_skill_ids=used_skill_names,
            public_skill_metadata=prepared.public_skill_metadata,
        )
        return ExecutorResult(
            status="succeeded",
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities=self._run_capabilities(payload),
            result={
                "message": str(executor_response.get("message") or ""),
                "artifact_count": len(artifacts),
                "sdk_used": bool(executor_response.get("sdk_used")),
                "sdk_error": None,
                "delegate_used": False,
                "worker_boundary": self.executor_type,
                "allowed_skills": prepared.allowed_skill_names,
                "staged_skills": prepared.staged_skill_names,
                "used_skills": used_skill_names,
                "sdk_turn_diagnostics": turn_diagnostics,
            },
            artifacts=artifacts,
            executor_payload={
                **common_payload,
                "sdk_turn_diagnostics": turn_diagnostics,
            },
        )

    async def _run_with_staged_skills(
        self,
        payload: RunPayload,
        event_sink: ExecutorEventSink | None = None,
        *,
        sandbox_runtime: SandboxRuntime | None = None,
        execution_owner: RunExecutionOwner | None = None,
    ) -> ExecutorResult | ExecutorDispatchAccepted | None:
        settings = get_settings()
        if not settings.claude_agent_sdk_enabled:
            return None
        await _emit_public_progress_event(
            event_sink,
            event_type="intent_detected",
            stage="planning",
            message="Run preparation started",
        )
        prepared, preflight_failure = await self._prepare_sdk_run(
            payload,
            event_sink=event_sink,
            workspace=_sandbox_workspace(settings, payload),
            workspace_root=settings.sandbox_workspace_root,
        )
        if preflight_failure is not None:
            return preflight_failure
        if prepared is None:
            return None
        return await self._submit_prepared_run_to_sandbox_runtime(
            payload,
            prepared,
            event_sink=event_sink,
            sandbox_runtime=sandbox_runtime,
            execution_owner=execution_owner,
        )

    async def _materialize_files(self, payload: RunPayload, workspace: Path) -> list[str]:
        if not payload.file_ids:
            return []
        result = await materialize_worker_context_files(payload=payload, workspace=workspace)
        return _MaterializedFileNames(
            list(result.file_names),
            attachment_metadata=[
                _AuthorizedAttachmentMetadata(
                    item.file_id,
                    item.file_name,
                    item.content_type,
                    item.size_bytes,
                )
                for item in result.attachment_metadata
            ],
            materialized_file_names=list(result.materialized_file_names),
        )

    def _collect_workspace_artifacts(
        self,
        payload: RunPayload,
        workspace: Path,
        *,
        response_files: Any,
        response_file_descriptors: Any = None, allowed_skill_names: Any = None,
        storage_scope: str = "",
        abandoned: threading.Event | None = None,
        reserve_storage: Callable[[str], str] | None = None,
    ) -> list[ArtifactManifest]:
        return collect_workspace_artifacts(
            tenant_id=payload.tenant_id,
            workspace_id=payload.workspace_id,
            session_id=payload.session_id,
            run_id=payload.run_id,
            source_executor=self.executor_type,
            workspace=workspace,
            response_files=response_files,
            response_file_descriptors=response_file_descriptors,
            allowed_skill_names=allowed_skill_names,
            required_artifact_types=(),
            artifact_factory=ArtifactManifest,
            storage_factory=ObjectStorage,
            ensure_inside=ensure_path_inside,
            storage_scope=storage_scope,
            abandoned=abandoned,
            reserve_storage=reserve_storage,
        )


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


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


def _sdk_used_skill_names(
    sdk_result: object,
    staged_skill_names: list[str],
) -> list[str]:
    source = str(getattr(sdk_result, "used_skills_source", "") or "").strip()
    if source != "executor_hook":
        return []
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
