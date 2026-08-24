import inspect
import posixpath
import shutil
import zipfile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, ClassVar
from xml.etree import ElementTree

from app import repositories
from app.capabilities import required_artifact_types_for_skill
from app.context_builder import executor_context_pack_from_snapshot
from app.context.api import (
    ContextFileContentError,
    context_file_executor_failure,
)
from app.context.file_continuity import materialize_run_context_files
from app.context_manifest import CONTEXT_MANIFEST_SCHEMA_VERSION
from app.context.retrieval import ContextRetrievalAuthority
from app.control_plane_contracts import (
    LEGACY_SYNTHETIC_CHAT_SKILL_ID,
    RUN_EXECUTION_KIND_HARNESS_CHAT,
    RUN_EXECUTION_KIND_SKILL,
    artifact_lineage_contract,
    standard_trace_id,
)
from app.db import transaction
from app.execution_boundary import (
    CLAUDE_WORKER_EXECUTOR,
    ExecutionBoundaryDecision,
    decide_execution_boundary,
)
from app.executors.base import (
    ArtifactManifest,
    ExecutorDispatchAccepted,
    ExecutorEventSink,
    ExecutorResult,
    RunExecutionOwner,
    RunPayload,
)
from app.executors.claude_agent_sdk_runner import (
    CapabilityExecutionPlan,
    ClaudeAgentSdkRunResult,
    ClaudeAgentSdkNotAvailable,
    ScopedContextRetrievalIdentity,
    build_skill_prompt,
    project_sdk_turn_diagnostics,
    runtime_tool_policy_subjects as _runtime_tool_policy_subjects,
    run_claude_agent_sdk,
    sandbox_runtime_tool_policy_subjects as _sandbox_runtime_tool_policy_subjects,
)
from app.executors.claude.prompts import build_harness_chat_prompt
from app.execution.api import (
    SkillInvocationEvidenceBinder,
    sandbox_reconciliation_payload,
)
from app.path_safety import ensure_creatable_inside, ensure_path_inside
from app.required_tool_contract import (
    RequiredCapabilityDecision,
    RequiredCapabilityDeclaration,
    RequiredCapabilityEvidence,
    RequiredToolContractError,
    selected_capability_completion_decision,
    validate_runtime_tool_evidence,
)
from app.runtime.event_bridge import agent_event_to_executor_event
from app.runtime.sandbox.callback_tokens import (
    CallbackTokenBinding,
    callback_token_id_for_binding,
)
from app.runtime.sandbox.container_provider import (
    DockerContainerProvider,
    FakeContainerProvider,
    OpenSandboxContainerProvider,
)
from app.runtime.sandbox.contracts import ContextRetrievalScope, SandboxRuntimeRequest
from app.runtime.sandbox.runtime import SandboxRuntime
from app.session_continuity import sdk_session_id_for_run
from app.settings import get_settings
from app.skills.catalog import (
    AuthorizedSkillCatalogBinding,
    AuthorizedSkillCatalogError,
    AuthorizedSkillCatalogResolution,
    load_runtime_authorized_skill_catalog,
)
from app.skills.dependencies import skill_dependency_ids, with_skill_dependencies
from app.skills.pinning import (
    pin_manifests_for_result as _pin_manifests_for_result,
    pinned_skill_manifests as _pinned_skill_manifests,
    select_pinned_skills as _select_pinned_skills,
)
from app.skills.registry import BuiltinSkill, BuiltinSkillRegistry
from app.skills.stager import SkillStager
from app.storage import ObjectStorage

_MAX_WORKSPACE_ARTIFACT_FILES = 128
_MAX_WORKSPACE_ARTIFACT_FILE_BYTES = 64 * 1024 * 1024
_MAX_WORKSPACE_ARTIFACT_TOTAL_BYTES = 256 * 1024 * 1024

_SANDBOX_SUCCESS_TERMINAL_STATUSES = {"completed", "succeeded"}
_SELECTED_SKILL_INVOCATION_ERRORS = {
    "claude_agent_sdk_selected_skill_not_invoked",
    "claude_agent_sdk_selected_skill_hook_failed",
    "claude_agent_sdk_selected_skill_not_authorized",
}
_SDK_ACTIONABLE_FAILURE_CODES = {
    *_SELECTED_SKILL_INVOCATION_ERRORS,
    "claude_agent_sdk_cancelled",
    "claude_agent_sdk_missing_structured_terminal",
    "claude_agent_sdk_turn_limit_exceeded",
    "claude_agent_sdk_timeout",
    "claude_agent_sdk_tool_admission_failed",
    "claude_agent_sdk_upstream_error",
}
_TOOL_PERMISSION_POLL_INTERVAL_SECONDS = 0.25
_REQUIRED_DOCX_MAX_ENTRY_COUNT = 128
_REQUIRED_DOCX_MAX_COMPRESSED_BYTES = 32 * 1024 * 1024
_REQUIRED_DOCX_MAX_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
_REQUIRED_DOCX_MAX_COMPRESSION_RATIO = 100
_OPC_CONTENT_TYPES_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/content-types"
_OPC_RELATIONSHIPS_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"
_OPC_OFFICE_DOCUMENT_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
_WORDPROCESSINGML_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_WORD_MAIN_DOCUMENT_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)


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


def _capability_completion_decision(
    plan: CapabilityExecutionPlan, *, binding: dict[str, object], evidence: object
) -> RequiredCapabilityDecision:
    """Validate every observed invocation and each explicit requirement."""

    mismatch = RequiredCapabilityDecision(False, "required_tool_completion_evidence_mismatch", "", "")
    if not isinstance(evidence, list):
        return mismatch
    try:
        records = [RequiredCapabilityEvidence.from_payload(item) for item in evidence]
    except RequiredToolContractError:
        return mismatch
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    call_owners: dict[str, tuple[str, str]] = {}
    for record in records:
        key = (record.capability_kind, record.canonical_identity)
        call_id = record.tool_call_id
        if key not in plan.available or not isinstance(call_id, str) or not call_id:
            return mismatch
        if call_owners.setdefault(call_id, key) != key:
            return mismatch
        groups.setdefault((*key, call_id), []).append(asdict(record))
    for (capability_kind, canonical_identity, _call_id), invocation in groups.items():
        declaration = RequiredCapabilityDeclaration.from_authorized_subject(
            capability_kind=capability_kind,
            canonical_identity=canonical_identity,
        )
        decision = selected_capability_completion_decision(
            declarations=[declaration],
            binding=binding,
            evidence=invocation,
        )
        if not decision.allowed:
            return decision
    for declaration in plan.required:
        match_count = sum(
            key[:2] == (declaration.capability_kind, declaration.canonical_identity)
            for key in groups
        )
        if not match_count:
            return selected_capability_completion_decision(
                declarations=[declaration], binding=binding, evidence=[]
            )
        if match_count != 1:
            return mismatch
    reason = "required_tool_completion_evidence_valid" if plan.required or groups else "required_capability_not_selected"
    return RequiredCapabilityDecision(True, reason, "", "")


def _capability_execution_error(
    payload: RunPayload,
    evidence: object,
    *,
    required_skill_identity: object = None,
    available_skill_identities: object = (),
) -> str | None:
    """Validate only the authorized capability invocations that actually occurred."""

    plan = CapabilityExecutionPlan.from_tool_policy_subjects(
        payload.input.get("_runtime_tool_policy_subjects"),
        required_skill_identity=required_skill_identity,
        available_skill_identities=available_skill_identities,
    )
    decision = _capability_completion_decision(
        plan,
        binding={
            "tenant_id": payload.tenant_id,
            "workspace_id": payload.workspace_id,
            "user_id": payload.user_id,
            "session_id": payload.session_id,
            "run_id": payload.run_id,
            "attempt_id": payload.attempt_id,
        },
        evidence=evidence,
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
        mcp_requires_sandbox=CapabilityExecutionPlan.mcp_requires_sandbox(
            payload.input.get("_runtime_tool_policy_subjects"),
            broker_capability=payload.mcp_broker_capability,
        ),
    )


def _ordinary_run_requires_sandbox(payload: RunPayload) -> bool:
    return _execution_boundary_decision(payload).requires_real_sandbox


def _required_artifact_types(payload: RunPayload) -> tuple[str, ...]:
    """Resolve the capability-owned artifact contract for this selected Skill."""
    return required_artifact_types_for_skill(payload.skill_id)


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


def _sandbox_callback_url(settings: object) -> str:
    return f"{str(settings.sandbox_callback_base_url).rstrip('/')}/api/ai/runtime/callbacks/executor"


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


def _authorized_skill_catalog_binding(
    payload: RunPayload,
) -> AuthorizedSkillCatalogBinding:
    if payload.skill_id is None:
        raise AuthorizedSkillCatalogError("authorized_skill_catalog_binding_invalid")
    return AuthorizedSkillCatalogBinding(
        tenant_id=payload.tenant_id,
        workspace_id=payload.workspace_id,
        user_id=payload.user_id,
        session_id=payload.session_id,
        run_id=payload.run_id,
        agent_id=payload.agent_id,
        selected_skill_id=payload.skill_id,
    )


def _runtime_authorized_skill_catalog(
    payload: RunPayload,
) -> AuthorizedSkillCatalogResolution | None:
    if payload.execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT:
        return None
    return load_runtime_authorized_skill_catalog(
        payload.input,
        expected_binding=_authorized_skill_catalog_binding(payload),
    )


def _authorized_catalog_public_skill_metadata(
    catalog: AuthorizedSkillCatalogResolution | None,
) -> dict[str, dict[str, str]]:
    if catalog is None:
        return {}
    return {
        entry.skill_id: {
            "name": entry.name,
            "version": entry.version,
            "availability": entry.availability,
        }
        for entry in catalog.snapshot.entries
    }


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


def _merged_pinned_skill_manifests(
    payload: RunPayload,
    catalog: AuthorizedSkillCatalogResolution | None,
) -> dict[str, dict[str, Any]]:
    pinned = _pinned_skill_manifests(payload.skill_manifests)
    if catalog is None:
        return pinned
    for manifest in catalog.manifests:
        skill_id = str(manifest.get("skill_id") or "")
        existing = pinned.get(skill_id)
        if existing is not None:
            existing_version = str(existing.get("content_hash") or existing.get("version") or "")
            runtime_version = str(manifest.get("content_hash") or manifest.get("version") or "")
            if existing_version != runtime_version:
                raise AuthorizedSkillCatalogError("authorized_skill_catalog_pin_mismatch")
            continue
        pinned[skill_id] = manifest
    return pinned


def _context_manifest_with_attachment_metadata(
    manifest: dict[str, Any] | None,
    metadata: list[_AuthorizedAttachmentMetadata],
) -> dict[str, Any]:
    """Enrich authorized attachment refs with sandbox materialization metadata."""

    result = dict(manifest or {})
    raw_files = result.get("files")
    if not metadata or not isinstance(raw_files, list):
        return result
    metadata_by_file_id = {item.file_id: item for item in metadata}
    enriched_files: list[Any] = []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            enriched_files.append(raw_file)
            continue
        file_ref = dict(raw_file)
        item = metadata_by_file_id.get(str(file_ref.get("file_id") or ""))
        if item is not None:
            file_ref.update(
                {
                    "name": item.file_name,
                    "content_type": item.content_type,
                    "size_bytes": item.size_bytes,
                    "requires_retrieval": True,
                }
            )
        enriched_files.append(file_ref)
    result["files"] = enriched_files
    return result


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

    async def _run_general_chat(self, payload: RunPayload, event_sink: ExecutorEventSink | None = None) -> ExecutorResult:
        sdk_result = await self._try_run_sdk(payload, event_sink=event_sink)
        if self._sdk_completed_normally(sdk_result):
            turn_diagnostics = _public_sdk_turn_diagnostics(
                payload,
                getattr(sdk_result, "turn_diagnostics", {}),
                error_code=None,
                used_skill_ids=list(getattr(sdk_result, "used_skills", []) or []),
                public_skill_metadata={},
            )
            return ExecutorResult(
                status="succeeded",
                adapter_version=self.adapter_version,
                executor_type=self.executor_type,
                executor_version=self.executor_version,
                capabilities=self._run_capabilities(payload),
                result={
                    "message": sdk_result.message or "",
                    "sdk_used": True,
                    "sdk_session_id": sdk_result.session_id,
                    "sdk_error": None,
                    "delegate_used": False,
                    "worker_boundary": self.executor_type,
                    "sdk_turn_diagnostics": turn_diagnostics,
                },
                artifacts=[],
                executor_payload={
                    "sdk_used": True,
                    "sdk_session_id": sdk_result.session_id,
                    "sdk_usage": sdk_result.usage,
                    "sdk_terminal_reason": self._sdk_terminal_reason(sdk_result),
                    "delegate_used": False,
                    "worker_boundary": self.executor_type,
                    "sdk_turn_diagnostics": turn_diagnostics,
                },
            )
        error_code = self._sdk_failure_code(sdk_result)
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
                "message": self._sdk_failure_message(sdk_result),
                "error_code": error_code,
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

    def _sdk_failure_code(self, sdk_result) -> str:
        if sdk_result is None:
            return "claude_agent_sdk_disabled"
        error_text = str(getattr(sdk_result, "error", "") or "")
        if error_text in _SDK_ACTIONABLE_FAILURE_CODES:
            return error_text
        if error_text.startswith("claude_agent_sdk_unavailable"):
            return "claude_agent_sdk_unavailable"
        if getattr(sdk_result, "used_sdk", False):
            return "claude_agent_sdk_runtime_error"
        if error_text == "claude_agent_sdk_disabled":
            return "claude_agent_sdk_disabled"
        return "claude_agent_sdk_required"

    def _sdk_failure_message(self, sdk_result) -> str:
        error_code = self._sdk_failure_code(sdk_result)
        if error_code in _SELECTED_SKILL_INVOCATION_ERRORS:
            return "The selected capability did not complete its required Skill execution. Please retry."
        messages = {
            "claude_agent_sdk_cancelled": "This run was cancelled before completion.",
            "claude_agent_sdk_turn_limit_exceeded": (
                "This run reached its turn limit. Continue in the same session or narrow the request."
            ),
            "claude_agent_sdk_timeout": "This run timed out. Retry or split the request.",
            "claude_agent_sdk_missing_structured_terminal": (
                "The executor ended without an authoritative terminal result. Please retry."
            ),
            "claude_agent_sdk_tool_admission_failed": (
                "The selected capability or tool was not admitted by platform policy."
            ),
            "claude_agent_sdk_upstream_error": (
                "The execution service failed. Please retry later."
            ),
            "claude_agent_sdk_disabled": "Claude Agent SDK is required for this run.",
            "claude_agent_sdk_required": "Claude Agent SDK is required for this run.",
            "claude_agent_sdk_unavailable": "Claude Agent SDK is required for this run.",
        }
        return messages.get(error_code, "Claude Agent SDK execution failed")

    def _sdk_completed_normally(self, sdk_result) -> bool:
        return bool(
            sdk_result
            and getattr(sdk_result, "used_sdk", False)
            and not getattr(sdk_result, "error", None)
            and getattr(sdk_result, "received_structured_terminal", False)
        )

    def _sdk_terminal_reason(self, sdk_result) -> str | None:
        terminal_reason = getattr(sdk_result, "terminal_reason", None)
        return terminal_reason if isinstance(terminal_reason, str) and terminal_reason else None

    def _sdk_required_result(self, payload: RunPayload, sdk_result) -> ExecutorResult:
        error_code = self._sdk_failure_code(sdk_result)
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

    def _wrap_file_skill_result(
        self,
        result: ExecutorResult,
        *,
        multi_agent: bool = False,
    ) -> ExecutorResult:
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
            },
        )

    async def _run_multi_agent_file_skill(
        self,
        payload: RunPayload,
        event_sink: ExecutorEventSink | None = None,
    ) -> ExecutorResult:
        if (
            payload.execution_kind != RUN_EXECUTION_KIND_SKILL
            or payload.skill_id is None
        ):
            return self._multi_agent_file_skill_failed(
                "skill_execution_identity_invalid",
                "Harness chat cannot enter a Skill execution path",
            )
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
        pinned_manifests = _pinned_skill_manifests(payload.skill_manifests)
        available_names = list(dict.fromkeys([skill.name for skill in skills] + list(pinned_manifests)))
        allowed_skill_names = _allowed_skill_names(payload, available_names)
        _selected_skills, pin_mismatches = _select_pinned_skills(
            skills,
            allowed_skill_names,
            pinned_manifests,
            _pinned_snapshot_root(workspace),
            path_guard=ensure_creatable_inside,
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

    def _agent_profile_system_prompt(self, payload: RunPayload) -> str:
        """Return profile instructions only for the SDK/runtime system channel."""

        value = payload.agent_profile.get("instructions") if isinstance(payload.agent_profile, dict) else None
        return value if isinstance(value, str) and value else ""

    def _executor_context_pack(self, payload: RunPayload) -> dict[str, Any]:
        if payload.context_pack.get("schema_version") == "ai-platform.executor-context-pack.v1":
            return payload.context_pack
        return executor_context_pack_from_snapshot(payload.context_snapshot)

    def _context_retrieval_for_payload(
        self,
        payload: RunPayload,
        context_pack: dict[str, Any],
        workspace: Path,
    ) -> tuple[ContextRetrievalAuthority | None, ScopedContextRetrievalIdentity | None]:
        scope = self._context_retrieval_scope_for_payload(payload, context_pack)
        if scope is None:
            return None, None
        return (
            ContextRetrievalAuthority.for_workspace_transaction(
                transaction, ObjectStorage(), workspace
            ),
            ScopedContextRetrievalIdentity(**scope.model_dump()),
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
                authorized_catalog = _runtime_authorized_skill_catalog(payload)
                pinned_manifests = _merged_pinned_skill_manifests(
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
            skills = (
                []
                if authorized_catalog is not None
                else BuiltinSkillRegistry(
                    settings.platform_skills_root
                ).list_builtin_skills()
            )
            available_names = list(
                dict.fromkeys([skill.name for skill in skills] + list(pinned_manifests))
            )
            allowed_skill_names = _allowed_skill_names(
                payload,
                available_names,
                authorized_catalog=authorized_catalog,
            )
            selected_skills, pin_mismatches = _select_pinned_skills(
                skills,
                allowed_skill_names,
                pinned_manifests,
                _pinned_snapshot_root(resolved_workspace),
                path_guard=ensure_creatable_inside,
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
                    "skill_manifests": _pin_manifests_for_result(pinned_manifests, allowed_skill_names),
                    "pin_mismatches": pin_mismatches,
                },
            )
        staged_skill_names = (
            []
            if payload.execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT
            else SkillStager(settings.skill_staging_subdir).stage_skills(
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
                _context_manifest_with_attachment_metadata(
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
        prompt = (
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
        return (
            PreparedSdkRun(
                workspace=resolved_workspace,
                file_names=file_names,
                selected_skills=selected_skills,
                pinned_manifests=pinned_manifests,
                allowed_skill_names=allowed_skill_names,
                staged_skill_names=staged_skill_names,
                public_skill_metadata=_authorized_catalog_public_skill_metadata(
                    authorized_catalog
                ),
                prompt=prompt,
                system_prompt=self._agent_profile_system_prompt(payload),
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
        runtime_context_manifest = _context_manifest_with_attachment_metadata(
            context_manifest,
            prepared.attachment_metadata,
        )
        runtime_context_manifest = dict(runtime_context_manifest or {})
        runtime_context_manifest["queue_attempt_id"] = payload.attempt_id
        adapter_reconciliation_context = {
            "schema_version": "ai-platform.claude-agent-reconciliation-context.v1",
            "run_payload": sandbox_reconciliation_payload(payload),
            "workspace": str(prepared.workspace),
            "allowed_skill_names": list(prepared.allowed_skill_names),
            "staged_skill_names": list(prepared.staged_skill_names),
            "skill_manifests": _skill_manifests(
                prepared.selected_skills,
                used_skill_names=[],
                pins=prepared.pinned_manifests,
            ),
            "public_skill_metadata": dict(prepared.public_skill_metadata),
        }
        reconciliation_context = {
            "schema_version": "ai-platform.executor-reconciliation.v1",
            "adapter_name": "claude-agent-worker",
            "run_payload": sandbox_reconciliation_payload(payload),
            "adapter_context": adapter_reconciliation_context,
        }
        request = SandboxRuntimeRequest(
            tenant_id=payload.tenant_id,
            workspace_id=payload.workspace_id,
            user_id=payload.user_id,
            session_id=payload.session_id,
            run_id=payload.run_id,
            attempt_id=payload.attempt_id,
            agent_id=payload.agent_id,
            skill_ids=_runtime_request_skill_ids(payload, prepared),
            mcp_tool_ids=_string_list(payload.input.get("mcp_tool_ids")),
            tool_policy_subjects=_sandbox_runtime_tool_policy_subjects(
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
            model=payload.model_value or payload.model_id or getattr(settings, "claude_agent_model", ""),
            resource_limits=_payload_resource_limits(payload),
            queue_wait_ms=_payload_queue_wait_ms(payload),
            trace_id=payload.trace_id or standard_trace_id(payload.run_id),
            callback_url=_sandbox_callback_url(settings),
            callback_token_id=callback_token_id_for_binding(
                CallbackTokenBinding(run_id=payload.run_id, attempt_id=payload.attempt_id)
            ),
            context_manifest=runtime_context_manifest,
            context_retrieval_scope=self._context_retrieval_scope_for_payload(payload, context_pack),
            sdk_session_id=sdk_session_id_for_run(payload.run_id),
            mcp_broker_capability=payload.mcp_broker_capability,
            governed_permission_wait=False,
            require_selected_skill_invocation=False,
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
            workspace=Path(workspace_value),
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
        runtime_result = _PersistedSandboxRuntimeResult(
            status=str(terminal_result.get("status") or ""),
            provider=provider,
            executor_response=dict(terminal_result),
            timings=dict(timings or {}),
        )
        return self._executor_result_from_sandbox_runtime(payload, prepared, runtime_result)

    def _executor_result_from_sandbox_runtime(
        self,
        payload: RunPayload,
        prepared: PreparedSdkRun | PreparedSandboxFinalization,
        runtime_result: object,
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
        reported_used_skill_names = _sdk_used_skill_names(
            runtime_sdk_result,
            prepared.staged_skill_names,
            allow_platform_controlled_runner=True,
        )
        selected_capability_error = _capability_execution_error(
            payload,
            capability_evidence,
            required_skill_identity=(
                payload.skill_id if payload.skill_id in reported_used_skill_names else None
            ),
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
            allow_platform_controlled_runner=selected_capability_error is None,
        )
        used_skills_source = _sdk_used_skills_source(runtime_sdk_result, used_skill_names)
        inferred_used_skill_names = _inferred_used_skill_names(payload, prepared.staged_skill_names)
        skill_manifests = (
            _skill_manifests_from_catalog(
                prepared.skill_manifests,
                used_skill_names=used_skill_names,
            )
            if isinstance(prepared, PreparedSandboxFinalization)
            else _skill_manifests(
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
            "sdk_session_id": executor_response.get("sdk_session_id"),
            "sdk_usage": executor_response.get("sdk_usage", {}) or {},
            "runtime_terminal_status": runtime_status,
            "delegate_used": False,
            "worker_boundary": self.executor_type,
            "allowed_skills": prepared.allowed_skill_names,
            "staged_skills": prepared.staged_skill_names,
            "used_skills": used_skill_names,
            "used_skills_source": used_skills_source,
            "inferred_used_skills": inferred_used_skill_names,
            "skill_manifests": skill_manifests,
            "sandbox_provider": sandbox_provider,
            "sandbox_runtime_used": True,
            "required_artifact_types": list(_required_artifact_types(payload)),
            "sandbox_timings": sandbox_timings,
            "capability_evidence": capability_evidence,
            **runtime_tool_evidence.private_payload(),
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
                    "sdk_used": bool(executor_response.get("sdk_used")),
                    "sdk_session_id": executor_response.get("sdk_session_id"),
                    "sdk_error": selected_capability_error,
                    "delegate_used": False,
                    "worker_boundary": self.executor_type,
                    "allowed_skills": prepared.allowed_skill_names,
                    "staged_skills": prepared.staged_skill_names,
                    "used_skills": used_skill_names,
                    "sdk_turn_diagnostics": turn_diagnostics,
                },
                artifacts=[],
                executor_payload={
                    **common_payload,
                    "sdk_error": selected_capability_error,
                    "sdk_turn_diagnostics": turn_diagnostics,
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
                    "sdk_used": bool(executor_response.get("sdk_used")),
                    "sdk_session_id": executor_response.get("sdk_session_id"),
                    "sdk_error": error_code,
                    "delegate_used": False,
                    "worker_boundary": self.executor_type,
                    "allowed_skills": prepared.allowed_skill_names,
                    "staged_skills": prepared.staged_skill_names,
                    "used_skills": used_skill_names,
                    "sdk_turn_diagnostics": turn_diagnostics,
                },
                artifacts=[],
                executor_payload={
                    **common_payload,
                    "sdk_error": error_code,
                    "sdk_turn_diagnostics": turn_diagnostics,
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
                else self._sdk_failure_message(
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
                    "sdk_used": bool(executor_response.get("sdk_used")),
                    "sdk_session_id": executor_response.get("sdk_session_id"),
                    "sdk_error": sdk_error,
                    "delegate_used": False,
                    "worker_boundary": self.executor_type,
                    "allowed_skills": prepared.allowed_skill_names,
                    "staged_skills": prepared.staged_skill_names,
                    "used_skills": used_skill_names,
                    "sdk_turn_diagnostics": turn_diagnostics,
                },
                artifacts=[],
                executor_payload={
                    **common_payload,
                    "sdk_error": sdk_error,
                    "sdk_turn_diagnostics": turn_diagnostics,
                },
            )

        artifacts = self._collect_workspace_artifacts(payload, prepared.workspace)
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
                "sdk_session_id": executor_response.get("sdk_session_id"),
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
        sandbox_required = _ordinary_run_requires_sandbox(payload)
        await _emit_public_progress_event(
            event_sink,
            event_type="intent_detected",
            stage="planning",
            message="Run preparation started",
        )
        prepared, preflight_failure = await self._prepare_sdk_run(
            payload,
            event_sink=event_sink,
            workspace=_sandbox_workspace(settings, payload) if sandbox_required else None,
            workspace_root=settings.sandbox_workspace_root if sandbox_required else None,
        )
        if preflight_failure is not None:
            return preflight_failure
        if prepared is None:
            return None
        if sandbox_required:
            return await self._submit_prepared_run_to_sandbox_runtime(
                payload,
                prepared,
                event_sink=event_sink,
                sandbox_runtime=sandbox_runtime,
                execution_owner=execution_owner,
            )

        sdk_result = await self._try_run_sdk(
            payload,
            event_sink=event_sink,
            workspace=prepared.workspace,
            file_names=prepared.file_names,
            prompt=prepared.prompt,
            system_prompt=prepared.system_prompt,
            staged_skill_names=prepared.staged_skill_names,
            public_skill_metadata=prepared.public_skill_metadata,
        )
        if self._sdk_completed_normally(sdk_result):
            artifacts = self._collect_workspace_artifacts(payload, prepared.workspace)
            used_skill_names = _sdk_used_skill_names(sdk_result, prepared.staged_skill_names)
            used_skills_source = _sdk_used_skills_source(sdk_result, used_skill_names)
            inferred_used_skill_names = _inferred_used_skill_names(payload, prepared.staged_skill_names)
            skill_manifests = _skill_manifests(
                prepared.selected_skills,
                used_skill_names=used_skill_names,
                pins=prepared.pinned_manifests,
            )
            selected_skill_error = _capability_execution_error(
                payload,
                getattr(sdk_result, "capability_evidence", None),
                required_skill_identity=(
                    payload.skill_id if payload.skill_id in used_skill_names else None
                ),
                available_skill_identities=prepared.allowed_skill_names,
            )
            if selected_skill_error is not None:
                turn_diagnostics = _public_sdk_turn_diagnostics(
                    payload,
                    getattr(sdk_result, "turn_diagnostics", {}),
                    error_code=selected_skill_error,
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
                        "error_code": selected_skill_error,
                        "sdk_used": True,
                        "sdk_session_id": sdk_result.session_id,
                        "sdk_error": selected_skill_error,
                        "delegate_used": False,
                        "worker_boundary": self.executor_type,
                        "allowed_skills": prepared.allowed_skill_names,
                        "staged_skills": prepared.staged_skill_names,
                        "used_skills": used_skill_names,
                        "sdk_turn_diagnostics": turn_diagnostics,
                    },
                    artifacts=[],
                    executor_payload={
                        "sdk_used": True,
                        "sdk_session_id": sdk_result.session_id,
                        "sdk_usage": sdk_result.usage,
                        "sdk_terminal_reason": self._sdk_terminal_reason(sdk_result),
                        "sdk_error": selected_skill_error,
                        "delegate_used": False,
                        "worker_boundary": self.executor_type,
                        "allowed_skills": prepared.allowed_skill_names,
                        "staged_skills": prepared.staged_skill_names,
                        "used_skills": used_skill_names,
                        "used_skills_source": used_skills_source,
                        "inferred_used_skills": inferred_used_skill_names,
                        "skill_manifests": skill_manifests,
                        "required_artifact_types": list(_required_artifact_types(payload)),
                        "capability_evidence": list(
                            getattr(sdk_result, "capability_evidence", []) or []
                        ),
                        "sdk_turn_diagnostics": turn_diagnostics,
                    },
                )
            turn_diagnostics = _public_sdk_turn_diagnostics(
                payload,
                getattr(sdk_result, "turn_diagnostics", {}),
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
                    "message": sdk_result.message or "",
                    "artifact_count": len(artifacts),
                    "sdk_used": True,
                    "sdk_session_id": sdk_result.session_id,
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
                    "sdk_used": True,
                    "sdk_session_id": sdk_result.session_id,
                    "sdk_usage": sdk_result.usage,
                    "sdk_terminal_reason": self._sdk_terminal_reason(sdk_result),
                    "delegate_used": False,
                    "worker_boundary": self.executor_type,
                    "allowed_skills": prepared.allowed_skill_names,
                    "staged_skills": prepared.staged_skill_names,
                    "used_skills": used_skill_names,
                    "used_skills_source": used_skills_source,
                    "inferred_used_skills": inferred_used_skill_names,
                    "skill_manifests": skill_manifests,
                    "required_artifact_types": list(_required_artifact_types(payload)),
                    "capability_evidence": list(
                        getattr(sdk_result, "capability_evidence", []) or []
                    ),
                    "sdk_turn_diagnostics": turn_diagnostics,
                },
            )
        used_skill_names = _sdk_used_skill_names(sdk_result, prepared.staged_skill_names) if sdk_result else []
        used_skills_source = _sdk_used_skills_source(sdk_result, used_skill_names)
        inferred_used_skill_names = _inferred_used_skill_names(payload, prepared.staged_skill_names)
        skill_manifests = _skill_manifests(
            prepared.selected_skills,
            used_skill_names=used_skill_names,
            pins=prepared.pinned_manifests,
        )
        failure_code = self._sdk_failure_code(sdk_result)
        turn_diagnostics = _public_sdk_turn_diagnostics(
            payload,
            getattr(sdk_result, "turn_diagnostics", {}) if sdk_result else {},
            error_code=failure_code,
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
                "message": self._sdk_failure_message(sdk_result) if sdk_result else "Claude Agent SDK execution failed",
                "error_code": failure_code,
                "sdk_used": bool(sdk_result and sdk_result.used_sdk),
                "sdk_error": sdk_result.error if sdk_result else "claude_agent_sdk_required",
                "delegate_used": False,
                "worker_boundary": self.executor_type,
                "allowed_skills": prepared.allowed_skill_names,
                "staged_skills": prepared.staged_skill_names,
                "used_skills": used_skill_names,
                "sdk_turn_diagnostics": turn_diagnostics,
            },
            artifacts=[],
            executor_payload={
                "sdk_used": bool(sdk_result and sdk_result.used_sdk),
                "sdk_error": sdk_result.error if sdk_result else "claude_agent_sdk_required",
                "delegate_used": False,
                "worker_boundary": self.executor_type,
                "allowed_skills": prepared.allowed_skill_names,
                "staged_skills": prepared.staged_skill_names,
                "used_skills": used_skill_names,
                "used_skills_source": used_skills_source,
                "inferred_used_skills": inferred_used_skill_names,
                "skill_manifests": skill_manifests,
                "sdk_turn_diagnostics": turn_diagnostics,
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
        system_prompt: str | None = None,
        staged_skill_names: list[str] | None = None,
        public_skill_metadata: dict[str, dict[str, str]] | None = None,
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
        prepared_file_names = (
            file_names
            if file_names is not None
            else await self._materialize_files(payload, workspace)
        )
        raw_attachment_metadata = getattr(prepared_file_names, "attachment_metadata", [])
        attachment_metadata = (
            [
                item
                for item in raw_attachment_metadata
                if isinstance(item, _AuthorizedAttachmentMetadata)
            ]
            if isinstance(raw_attachment_metadata, list)
            else []
        )
        file_names = list(prepared_file_names)
        context_pack = self._executor_context_pack(payload)
        context_manifest = _context_manifest_from_pack(context_pack)
        if context_manifest is not None:
            context_pack = dict(context_pack)
            context_pack["context_manifest"] = (
                _context_manifest_with_attachment_metadata(
                    context_manifest,
                    attachment_metadata,
                )
            )
        if not prompt:
            if payload.execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT:
                prompt = build_harness_chat_prompt(
                    user_message=str(
                        payload.input.get("message")
                        or payload.input.get("prompt")
                        or ""
                    ),
                    file_names=file_names,
                    context_pack=context_pack,
                )
            else:
                try:
                    authorized_catalog = _runtime_authorized_skill_catalog(payload)
                except AuthorizedSkillCatalogError:
                    return type(
                        "SdkFailed",
                        (),
                        {
                            "used_sdk": False,
                            "message": "",
                            "session_id": None,
                            "usage": {},
                            "error": "authorized_skill_catalog_invalid",
                            "turn_diagnostics": {},
                        },
                    )()
                prompt = build_skill_prompt(
                    skill_id=str(payload.skill_id),
                    user_message=str(
                        payload.input.get("message")
                        or payload.input.get("prompt")
                        or ""
                    ),
                    file_names=file_names,
                    context_pack=context_pack,
                    authorized_skill_catalog=(
                        authorized_catalog.snapshot
                        if authorized_catalog is not None
                        else None
                    ),
                )
        if system_prompt is None:
            system_prompt = self._agent_profile_system_prompt(payload)
        context_retrieval, context_retrieval_identity = self._context_retrieval_for_payload(payload, context_pack, workspace)

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

        autonomous_agent_profile = bool(payload.agent_profile)
        evidence_skill_names = (
            set(staged_skill_names or [])
            if autonomous_agent_profile
            else {
                payload.skill_id
            }
            if payload.execution_kind == RUN_EXECUTION_KIND_SKILL
            and payload.skill_id is not None
            and payload.skill_id != LEGACY_SYNTHETIC_CHAT_SKILL_ID
            and (staged_skill_names is None or payload.skill_id in staged_skill_names)
            else set()
        )

        def project_skill_evidence(raw: dict[str, str]) -> dict[str, object]:
            declaration = RequiredCapabilityDeclaration.from_authorized_subject(
                capability_kind="skill",
                canonical_identity=str(raw.get("canonical_identity") or ""),
            )
            tool_call_id = str(raw.get("tool_call_id") or "")
            lifecycle_phase = str(raw.get("lifecycle_phase") or "")
            if raw != RequiredCapabilityEvidence.sdk_hook_payload(
                declaration=declaration,
                tool_call_id=tool_call_id,
                lifecycle_phase=lifecycle_phase,
            ):
                raise RequiredToolContractError(
                    "required_tool_completion_evidence_mismatch"
                )
            return asdict(
                RequiredCapabilityEvidence.from_sdk_hook(
                    declaration=declaration,
                    binding={
                        "tenant_id": payload.tenant_id,
                        "workspace_id": payload.workspace_id,
                        "user_id": payload.user_id,
                        "session_id": payload.session_id,
                        "run_id": payload.run_id,
                        "attempt_id": payload.attempt_id,
                    },
                    tool_call_id=tool_call_id,
                    lifecycle_phase=lifecycle_phase,
                )
            )

        skill_evidence_binder = (
            SkillInvocationEvidenceBinder(
                allowed_skill_names=evidence_skill_names,
                project_record=project_skill_evidence,
            )
            if evidence_skill_names
            else None
        )

        try:
            sdk_kwargs = {
                "prompt": prompt,
                "cwd": workspace,
                "skill_id": payload.skill_id,
                "session_id": sdk_session_id_for_run(payload.run_id),
                "model_id": payload.model_value or payload.model_id or None,
                "skills": (
                    []
                    if payload.execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT
                    else staged_skill_names
                ),
                "on_text": on_text,
                "on_skill_use": (
                    on_skill_use
                    if payload.execution_kind == RUN_EXECUTION_KIND_SKILL
                    else None
                ),
                "public_skill_metadata": public_skill_metadata,
                "tool_policy_subjects": _runtime_tool_policy_subjects(
                    payload,
                    _context_manifest_from_pack(context_pack),
                ),
                "require_selected_skill_invocation": False,
            }
            if system_prompt:
                sdk_kwargs["system_prompt"] = system_prompt
            if context_retrieval is not None and context_retrieval_identity is not None:
                sdk_kwargs["context_retrieval"] = context_retrieval
                sdk_kwargs["context_retrieval_identity"] = context_retrieval_identity
            if skill_evidence_binder is not None:
                sdk_kwargs["on_capability_evidence"] = skill_evidence_binder.bind
            await _emit_public_progress_event(
                event_sink,
                event_type="run_started",
                stage="runtime",
                message="SDK runtime dispatch is active",
            )
            sdk_result = await run_claude_agent_sdk(**sdk_kwargs)
            if skill_evidence_binder is not None and isinstance(
                sdk_result,
                ClaudeAgentSdkRunResult,
            ):
                return replace(
                    sdk_result,
                    capability_evidence=skill_evidence_binder.records,
                )
            return sdk_result
        except ClaudeAgentSdkNotAvailable:
            return type("SdkUnavailable", (), {
                "used_sdk": False,
                "message": "",
                "session_id": None,
                "usage": {},
                "error": "claude_agent_sdk_unavailable",
                "turn_diagnostics": {},
            })()
        # The worker boundary maps all SDK dependency failures to one stable public error.
        except Exception:  # noqa: BLE001
            return type("SdkFailed", (), {
                "used_sdk": True,
                "message": "",
                "session_id": None,
                "usage": {},
                "error": "claude_agent_sdk_upstream_error",
                "turn_diagnostics": {},
            })()

    async def _materialize_files(self, payload: RunPayload, workspace: Path) -> list[str]:
        if not payload.file_ids:
            return []
        if workspace.exists() and workspace.is_symlink():
            raise ValueError("run workspace must not be a symlink")
        result = await materialize_run_context_files(
            transaction_factory=transaction,
            repository=repositories,
            storage=ObjectStorage(),
            workspace=workspace,
            tenant_id=payload.tenant_id,
            workspace_id=payload.workspace_id,
            user_id=payload.user_id,
            session_id=payload.session_id,
            run_id=payload.run_id,
            file_ids=payload.file_ids,
        )
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

    def _collect_workspace_artifacts(self, payload: RunPayload, workspace: Path) -> list[ArtifactManifest]:
        artifacts: list[ArtifactManifest] = []
        storage = ObjectStorage()
        candidates: list[Path] = []
        seen_candidates: set[Path] = set()
        total_bytes = 0
        for output_dir in self._workspace_artifact_dirs(workspace):
            for item in sorted(output_dir.rglob("*")):
                if item.is_symlink():
                    raise ValueError("workspace output must not contain symlinks")
                if not item.is_file():
                    continue
                ensure_path_inside(output_dir, item, "workspace artifact must stay inside output directory")
                resolved = item.resolve(strict=False)
                if resolved in seen_candidates:
                    continue
                size_bytes = item.stat().st_size
                if size_bytes > _MAX_WORKSPACE_ARTIFACT_FILE_BYTES:
                    raise ValueError("workspace artifact exceeds the per-file byte limit")
                total_bytes += size_bytes
                if total_bytes > _MAX_WORKSPACE_ARTIFACT_TOTAL_BYTES:
                    raise ValueError("workspace artifacts exceed the total byte limit")
                if len(candidates) >= _MAX_WORKSPACE_ARTIFACT_FILES:
                    raise ValueError("workspace artifacts exceed the file count limit")
                seen_candidates.add(resolved)
                candidates.append(item)
        for index, path in enumerate(candidates, start=1):
            content_type = _artifact_content_type(path.name)
            artifact_type = _artifact_type(path.name, payload.skill_id)
            if artifact_type in {"reviewed_docx", "translated_docx"} and not _is_usable_docx(path):
                continue
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
                        "workspace_output": path.relative_to(workspace).as_posix(),
                    },
                )
            )
        return artifacts

    def _workspace_artifact_dirs(self, workspace: Path) -> list[Path]:
        roots: list[Path] = []
        legacy_output = workspace / "output"
        if legacy_output.is_dir():
            ensure_path_inside(workspace, legacy_output, "workspace output must stay inside the run workspace")
            roots.append(legacy_output)

        outputs_root = workspace / "outputs"
        if not outputs_root.is_dir():
            return roots
        ensure_path_inside(workspace, outputs_root, "workspace output must stay inside the run workspace")
        for delivery_dir in sorted(outputs_root.rglob("delivery")):
            if delivery_dir.is_symlink():
                raise ValueError("workspace output must not contain symlinks")
            if not delivery_dir.is_dir():
                continue
            ensure_path_inside(outputs_root, delivery_dir, "workspace artifact must stay inside output directory")
            roots.append(delivery_dir)
        return roots


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


def _allowed_skill_names(
    payload: RunPayload,
    available_names: list[str],
    *,
    authorized_catalog: AuthorizedSkillCatalogResolution | None = None,
) -> list[str]:
    available = set(available_names)
    if authorized_catalog is not None:
        return [
            skill_id
            for skill_id in authorized_catalog.materialized_skill_ids
            if skill_id in available
        ]
    requested = _string_list(payload.input.get("skill_ids"))
    if payload.skill_id and payload.skill_id in available:
        requested.insert(0, payload.skill_id)
    if not requested:
        return []
    selected = list(dict.fromkeys(name for name in requested if name in available))
    pinned_manifests = _pinned_skill_manifests(payload.skill_manifests)
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
        pinned_manifests = _pinned_skill_manifests(payload.skill_manifests)
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


def _sdk_used_skill_names(
    sdk_result: object,
    staged_skill_names: list[str],
    *,
    allow_platform_controlled_runner: bool = False,
) -> list[str]:
    source = str(getattr(sdk_result, "used_skills_source", "") or "").strip()
    trusted_sources = {"executor_hook"}
    if allow_platform_controlled_runner:
        trusted_sources.add("platform_controlled_runner")
    if source not in trusted_sources:
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


def _skill_manifests_from_catalog(
    manifests: list[dict[str, Any]],
    *,
    used_skill_names: list[str],
) -> list[dict[str, Any]]:
    used = set(used_skill_names)
    return [
        {
            **dict(manifest),
            "used": str(manifest.get("skill_id") or "") in used,
        }
        for manifest in manifests
    ]


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
    explicit = {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pdf": "application/pdf",
        ".csv": "text/csv; charset=utf-8",
        ".json": "application/json",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",
        ".zip": "application/zip",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    for suffix, content_type in explicit.items():
        if lower.endswith(suffix):
            return content_type
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
    if lower.endswith((".txt", ".md")):
        return "report_txt"
    return "runtime_file"


def _is_usable_docx(path: Path) -> bool:
    """Accept a required DOCX only when its bounded OPC package is usable."""

    try:
        if not 0 < path.stat().st_size <= _REQUIRED_DOCX_MAX_COMPRESSED_BYTES:
            return False
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if not _docx_archive_entries_are_bounded(entries):
                return False
            content_types = archive.read("[Content_Types].xml")
            relationships = archive.read("_rels/.rels")
            document = archive.read("word/document.xml")
    except (KeyError, OSError, ValueError, zipfile.BadZipFile):
        return False
    try:
        content_types_root = ElementTree.fromstring(content_types)
        relationships_root = ElementTree.fromstring(relationships)
        document_root = ElementTree.fromstring(document)
    except ElementTree.ParseError:
        return False
    if (
        content_types_root.tag != f"{{{_OPC_CONTENT_TYPES_NAMESPACE}}}Types"
        or relationships_root.tag != f"{{{_OPC_RELATIONSHIPS_NAMESPACE}}}Relationships"
        or document_root.tag != f"{{{_WORDPROCESSINGML_NAMESPACE}}}document"
    ):
        return False
    has_document_override = any(
        item.tag == f"{{{_OPC_CONTENT_TYPES_NAMESPACE}}}Override"
        and item.attrib.get("PartName") == "/word/document.xml"
        and item.attrib.get("ContentType") == _WORD_MAIN_DOCUMENT_CONTENT_TYPE
        for item in content_types_root
    )
    relationship_ids: set[str] = set()
    root_office_document_relationships = []
    for item in relationships_root:
        if item.tag != f"{{{_OPC_RELATIONSHIPS_NAMESPACE}}}Relationship":
            return False
        relationship_id = str(item.attrib.get("Id") or "")
        if not _is_valid_opc_relationship_id(relationship_id) or relationship_id in relationship_ids:
            return False
        relationship_ids.add(relationship_id)
        if str(item.attrib.get("Type") or "") == _OPC_OFFICE_DOCUMENT_RELATIONSHIP:
            root_office_document_relationships.append(item)
    has_main_document_relationship = (
        len(root_office_document_relationships) == 1
        and str(root_office_document_relationships[0].attrib.get("TargetMode") or "").lower() != "external"
        and _resolve_root_relationship_target(str(root_office_document_relationships[0].attrib.get("Target") or ""))
        == "word/document.xml"
    )
    body = next((item for item in document_root if item.tag == f"{{{_WORDPROCESSINGML_NAMESPACE}}}body"), None)
    return has_document_override and has_main_document_relationship and body is not None and any(True for _ in body)


def _is_valid_opc_relationship_id(value: str) -> bool:
    """Return whether an OPC relationship Id is a non-colon XML NCName.

    OPC relationship identifiers are XML ``xsd:ID`` values.  XML allows
    Unicode letters and combining marks, but a colon would make the value a
    QName rather than the required NCName.  This small predicate keeps the
    package parser dependency-free while accepting the XML name classes that
    legitimate non-ASCII producers use.
    """

    if not value or ":" in value or not _is_xml_ncname_start(value[0]):
        return False
    return all(_is_xml_ncname_char(character) for character in value[1:])


def _is_xml_ncname_start(character: str) -> bool:
    """Implement XML 1.0 ``NameStartChar`` ranges excluding the QName colon."""

    codepoint = ord(character)
    return (
        character == "_"
        or "A" <= character <= "Z"
        or "a" <= character <= "z"
        or 0xC0 <= codepoint <= 0xD6
        or 0xD8 <= codepoint <= 0xF6
        or 0xF8 <= codepoint <= 0x2FF
        or 0x370 <= codepoint <= 0x37D
        or 0x37F <= codepoint <= 0x1FFF
        or 0x200C <= codepoint <= 0x200D
        or 0x2070 <= codepoint <= 0x218F
        or 0x2C00 <= codepoint <= 0x2FEF
        or 0x3001 <= codepoint <= 0xD7FF
        or 0xF900 <= codepoint <= 0xFDCF
        or 0xFDF0 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0xEFFFF
    )


def _is_xml_ncname_char(character: str) -> bool:
    """Implement XML 1.0 ``NameChar`` ranges for a non-colon NCName."""

    codepoint = ord(character)
    return (
        _is_xml_ncname_start(character)
        or character in {"-", "."}
        or "0" <= character <= "9"
        or codepoint == 0xB7
        or 0x300 <= codepoint <= 0x36F
        or 0x203F <= codepoint <= 0x2040
    )


def _docx_archive_entries_are_bounded(entries: list[zipfile.ZipInfo]) -> bool:
    """Reject malformed, path-traversing, or expansion-prone OPC archive metadata before reads."""

    if not entries or len(entries) > _REQUIRED_DOCX_MAX_ENTRY_COUNT:
        return False
    compressed_total = 0
    uncompressed_total = 0
    seen_package_parts: set[str] = set()
    for entry in entries:
        filename = str(entry.filename or "")
        package_path = filename[:-1] if entry.is_dir() and filename.endswith("/") else filename
        if (
            not package_path
            or "\x00" in filename
            or "\\" in filename
            or filename.startswith("/")
            or any(part in {"", ".", ".."} for part in package_path.split("/"))
            or bool(entry.flag_bits & 0x1)
        ):
            return False
        normalized_part = package_path.casefold()
        if normalized_part in seen_package_parts:
            return False
        seen_package_parts.add(normalized_part)
        compressed_size = int(entry.compress_size)
        uncompressed_size = int(entry.file_size)
        if compressed_size < 0 or uncompressed_size < 0:
            return False
        compressed_total += compressed_size
        uncompressed_total += uncompressed_size
        if (
            compressed_total > _REQUIRED_DOCX_MAX_COMPRESSED_BYTES
            or uncompressed_total > _REQUIRED_DOCX_MAX_UNCOMPRESSED_BYTES
            or (
                compressed_size > 0
                and uncompressed_size > compressed_size * _REQUIRED_DOCX_MAX_COMPRESSION_RATIO
            )
        ):
            return False
    return True


def _resolve_root_relationship_target(target: str) -> str | None:
    """Resolve a root OPC relationship only when it stays within the package root."""

    if not target or "\\" in target or target.startswith("/"):
        return None
    normalized = posixpath.normpath(target)
    if normalized.startswith("../") or normalized in {".", ".."}:
        return None
    return normalized


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
