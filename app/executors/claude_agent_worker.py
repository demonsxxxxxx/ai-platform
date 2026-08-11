import base64
import binascii
import inspect
import logging
import posixpath
import shutil
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar
from xml.etree import ElementTree

from app import repositories
from app.context_builder import executor_context_pack_from_snapshot
from app.context.file_content import ContextFileContentError
from app.context.file_continuity import materialize_run_context_files
from app.context_manifest import (
    CONTEXT_MANIFEST_SCHEMA_VERSION,
    available_context_retrieval_tools,
)
from app.control_plane_contracts import standard_trace_id
from app.db import transaction
from app.execution_boundary import (
    CLAUDE_WORKER_EXECUTOR,
    ExecutionBoundaryDecision,
    decide_execution_boundary,
)
from app.executors.base import (
    ArtifactManifest,
    ExecutorEventSink,
    ExecutorResult,
    RunExecutionOwner,
    RunPayload,
)
from app.executors.claude_agent_sdk_runner import (
    CapabilityExecutionPlan,
    build_skill_prompt,
    internal_context_tool_policy_subjects,
    project_sdk_turn_diagnostics,
)
from app.file_parser_contracts import (
    AttachmentPreprocessingError,
    MaterializedAttachmentFact,
    attachment_requirements_from_contract,
    build_attachment_preprocessing_contract,
    dispatched_context_file_ids,
    validate_required_parser_evidence,
)
from app.path_safety import ensure_creatable_inside, ensure_path_inside
from app.required_tool_contract import (
    RequiredCapabilityDecision,
    RequiredCapabilityDeclaration,
    RequiredCapabilityEvidence,
    RequiredToolContractError,
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
    MAX_SKILL_SNAPSHOT_FILE_BYTES,
    MAX_SKILL_SNAPSHOT_TOTAL_BYTES,
)
from app.skills.registry import BuiltinSkill, BuiltinSkillRegistry, skill_content_hash
from app.skills.stager import SkillStager
from app.storage import ObjectStorage

logger = logging.getLogger(__name__)

_MAX_WORKSPACE_ARTIFACT_FILES = 128
_MAX_WORKSPACE_ARTIFACT_FILE_BYTES = 64 * 1024 * 1024
_MAX_WORKSPACE_ARTIFACT_TOTAL_BYTES = 256 * 1024 * 1024

_SANDBOX_SUCCESS_TERMINAL_STATUSES = {"completed", "succeeded"}
_SDK_ACTIONABLE_FAILURE_CODES = {
    "claude_agent_sdk_selected_skill_not_authorized",
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
    """Validate the scope and lifecycle of every observed optional invocation."""

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
        if any(getattr(record, field) != value for field, value in binding.items()):
            return mismatch
        if call_owners.setdefault(call_id, key) != key:
            return mismatch
        groups.setdefault((*key, call_id), []).append(asdict(record))
    for (capability_kind, canonical_identity, _call_id), invocation in groups.items():
        declaration = RequiredCapabilityDeclaration.from_authorized_subject(
            capability_kind=capability_kind,
            canonical_identity=canonical_identity,
        )
        phases = [item.get("lifecycle_phase") for item in invocation]
        statuses = [item.get("lifecycle_status") for item in invocation]
        if (
            len(invocation) != 2
            or phases[0] != "invocation_requested"
            or phases[1] not in {"completed", "failed"}
            or statuses[0] != "invoking"
            or statuses[1] != ("succeeded" if phases[1] == "completed" else "failed")
            or any(item.get("declaration_sha256") != declaration.declaration_sha256 for item in invocation)
        ):
            return mismatch
    reason = "capability_invocation_evidence_valid" if groups else "capability_not_invoked"
    return RequiredCapabilityDecision(True, reason, "", "")


def _capability_execution_error(
    payload: RunPayload,
    evidence: object,
    *,
    available_skill_ids: object = (),
    claimed_used_skill_ids: object | None = None,
) -> str | None:
    """Validate only the Skill and MCP invocations that actually occurred."""

    plan = CapabilityExecutionPlan.from_tool_policy_subjects(
        payload.input.get("_runtime_tool_policy_subjects"),
        available_skill_identities=available_skill_ids,
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
    if not decision.allowed:
        return decision.reason
    if claimed_used_skill_ids is None:
        return None
    if not isinstance(claimed_used_skill_ids, list) or any(
        not isinstance(item, str) or not item for item in claimed_used_skill_ids
    ):
        return "required_tool_completion_evidence_mismatch"
    try:
        records = [RequiredCapabilityEvidence.from_payload(item) for item in evidence]
    except (RequiredToolContractError, TypeError):
        return "required_tool_completion_evidence_mismatch"
    completed_skill_ids = {
        record.canonical_identity
        for record in records
        if record.capability_kind == "skill" and record.lifecycle_phase == "completed"
    }
    if set(claimed_used_skill_ids) != completed_skill_ids or len(
        claimed_used_skill_ids
    ) != len(set(claimed_used_skill_ids)):
        return "required_tool_completion_evidence_mismatch"
    return None


@dataclass(frozen=True)
class _AuthorizedAttachmentMetadata:
    """Authorized attachment metadata that never requires reading object bytes."""

    file_id: str
    file_name: str
    content_type: str
    size_bytes: int


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
    attachment_facts: list[MaterializedAttachmentFact] = field(default_factory=list)
    attachment_metadata: list[_AuthorizedAttachmentMetadata] = field(default_factory=list)
    materialized_file_names: list[str] | None = None


class _MaterializedFileNames(list[str]):
    def __init__(
        self,
        values: list[str],
        *,
        attachment_facts: list[MaterializedAttachmentFact],
        attachment_metadata: list[_AuthorizedAttachmentMetadata] | None = None,
        materialized_file_names: list[str] | None = None,
    ) -> None:
        super().__init__(values)
        self.attachment_facts = list(attachment_facts)
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


def _runtime_request_skill_ids(payload: RunPayload, prepared: PreparedSdkRun) -> list[str]:
    return list(dict.fromkeys([payload.skill_id, *prepared.staged_skill_names]))


def _authorized_skill_catalog_binding(payload: RunPayload) -> AuthorizedSkillCatalogBinding:
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
        selected_skill_id=(payload.skill_id if payload.skill_id != "general-chat" else ""),
        used_skill_ids=used_skill_ids,
        public_skill_metadata=public_skill_metadata,
    )


def _merged_pinned_skill_manifests(
    payload: RunPayload,
    catalog: AuthorizedSkillCatalogResolution | None,
) -> dict[str, dict[str, Any]]:
    pinned = _pinned_skill_manifests(payload)
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


def _attachment_preprocessing_contract(
    payload: RunPayload,
    prepared: PreparedSdkRun,
) -> dict[str, Any]:
    if not _requires_typed_attachment_preprocessing(payload):
        return build_attachment_preprocessing_contract()
    if prepared.attachment_facts:
        return build_attachment_preprocessing_contract(
            attachment_facts=list(prepared.attachment_facts)
        )
    return build_attachment_preprocessing_contract(
        file_ids=list(payload.file_ids[: len(prepared.file_names)]),
        file_names=list(prepared.file_names),
    )


def _requires_typed_attachment_preprocessing(payload: RunPayload) -> bool:
    """Stage authorized attachments independently of Expert or Skill identity."""

    return bool(payload.file_ids)


def _context_manifest_with_attachment_metadata(
    manifest: dict[str, Any] | None,
    metadata: list[_AuthorizedAttachmentMetadata],
    *,
    allow_file_content_tools: bool,
) -> dict[str, Any]:
    """Enrich authorized refs and remove file-content tools for metadata-only runs."""

    result = dict(manifest or {})
    available_tools = result.get("available_retrieval_tools")
    if not allow_file_content_tools and isinstance(available_tools, list):
        file_content_tools = (
            "read_context_file",
            "stage_context_file_to_workspace",
        )
        result["available_retrieval_tools"] = [
            tool_name
            for tool_name in available_tools
            if tool_name not in file_content_tools
        ]
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


class PinnedSkillMismatch(ValueError):
    def __init__(self, message: str, *, actual_content_hash: str = "") -> None:
        super().__init__(message)
        self.actual_content_hash = actual_content_hash


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

    async def submit_run(
        self,
        payload: RunPayload,
        event_sink: ExecutorEventSink | None = None,
        execution_owner: RunExecutionOwner | None = None,
    ) -> ExecutorResult:
        decision = _execution_boundary_decision(payload)
        if decision.fail_closed:
            return ExecutorResult(
                status="failed",
                adapter_version=self.adapter_version,
                executor_type=self.executor_type,
                executor_version=self.executor_version,
                capabilities=self.capabilities,
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
                sandbox_provider=configured_provider,
                runtime_started=False,
            )
        if not bool(getattr(settings, "claude_agent_sdk_enabled", False)):
            return self._sdk_required_result(payload, sdk_result=None)
        sandbox_runtime = SandboxRuntime(workspace_root=settings.sandbox_workspace_root)
        actual_provider = _sandbox_runtime_provider(sandbox_runtime)
        if actual_provider not in decision.accepted_providers:
            return self._sandbox_provider_required_result(
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
            return self._context_file_failure_result(error_code=exc.code)
        if sdk_result is not None:
            return sdk_result

        return self._sdk_required_result(payload, sdk_result=None)

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
            "claude_agent_sdk_disabled": "Claude Agent SDK is required for general chat runs.",
            "claude_agent_sdk_required": "Claude Agent SDK is required for general chat runs.",
            "claude_agent_sdk_unavailable": "Claude Agent SDK is required for general chat runs.",
        }
        return messages.get(error_code, "Claude Agent SDK execution failed")

    def _sdk_required_result(self, payload: RunPayload, sdk_result) -> ExecutorResult:
        error_code = self._sdk_failure_code(sdk_result)
        sdk_used = bool(sdk_result and sdk_result.used_sdk)
        sdk_error = sdk_result.error if sdk_result else "claude_agent_sdk_disabled"
        diagnostic_id = str(getattr(sdk_result, "diagnostic_id", "") or "") or None
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
            capabilities=self.capabilities,
            result={
                "message": "Claude Agent SDK with platform-managed Skills is required for ai-platform runs.",
                "error_code": error_code,
                "skill_id": payload.skill_id,
                "sdk_used": sdk_used,
                "sdk_error": sdk_error,
                "delegate_used": False,
                "worker_boundary": self.executor_type,
                "sdk_turn_diagnostics": turn_diagnostics,
                "diagnostic_id": diagnostic_id,
            },
            executor_payload={
                "sdk_used": sdk_used,
                "sdk_error": sdk_error,
                "delegate_used": False,
                "worker_boundary": self.executor_type,
                "sdk_turn_diagnostics": turn_diagnostics,
                "diagnostic_id": diagnostic_id,
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
        raw_attachment_facts = getattr(materialized_file_names, "attachment_facts", [])
        attachment_facts = (
            list(raw_attachment_facts)
            if isinstance(raw_attachment_facts, list)
            and len(raw_attachment_facts) == len(file_names)
            and all(isinstance(fact, MaterializedAttachmentFact) for fact in raw_attachment_facts)
            else []
        )
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

        try:
            authorized_catalog = _runtime_authorized_skill_catalog(payload)
            pinned_manifests = _merged_pinned_skill_manifests(payload, authorized_catalog)
        except AuthorizedSkillCatalogError:
            return None, self._authorized_skill_catalog_failure_result(
                "authorized_skill_catalog_invalid"
            )
        if (
            authorized_catalog is not None
            and payload.skill_id != "general-chat"
            and payload.skill_id not in authorized_catalog.materialized_skill_ids
        ):
            return None, self._authorized_skill_catalog_failure_result(
                "authorized_skill_selected_unavailable"
            )
        skills = (
            []
            if authorized_catalog is not None
            else BuiltinSkillRegistry(settings.platform_skills_root).list_builtin_skills()
        )
        available_names = list(dict.fromkeys([skill.name for skill in skills] + list(pinned_manifests)))
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
        staged_skill_names = SkillStager(settings.skill_staging_subdir).stage_skills(
            workspace=resolved_workspace,
            skills=selected_skills,
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
                    allow_file_content_tools=_requires_typed_attachment_preprocessing(payload),
                )
            )
        prompt = build_skill_prompt(
            skill_id=payload.skill_id,
            user_message=str(payload.input.get("message") or payload.input.get("prompt") or ""),
            file_names=file_names,
            context_pack=prompt_context_pack,
            authorized_skill_catalog=(
                authorized_catalog.snapshot if authorized_catalog is not None else None
            ),
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
                attachment_facts=attachment_facts,
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
    ) -> ExecutorResult:
        settings = get_settings()
        context_pack = self._executor_context_pack(payload)
        context_manifest = _context_manifest_from_pack(context_pack)
        try:
            attachment_contract = _attachment_preprocessing_contract(payload, prepared)
            attachment_requirements = attachment_requirements_from_contract(attachment_contract)
        except AttachmentPreprocessingError as exc:
            return self._attachment_parser_failure_result(error_code=exc.code)
        runtime_context_manifest = _context_manifest_with_attachment_metadata(
            context_manifest,
            prepared.attachment_metadata,
            allow_file_content_tools=_requires_typed_attachment_preprocessing(payload),
        )
        runtime_context_manifest = dict(runtime_context_manifest or {})
        runtime_context_manifest["queue_attempt_id"] = payload.attempt_id
        if attachment_requirements:
            if context_manifest is None:
                return self._attachment_parser_failure_result(
                    error_code="attachment_parser_context_manifest_required"
                )
            manifest_file_ids = dispatched_context_file_ids(runtime_context_manifest)
            if any(
                requirement.file_id not in manifest_file_ids
                for requirement in attachment_requirements
            ):
                return self._attachment_parser_failure_result(
                    error_code="attachment_parser_manifest_file_mismatch"
                )
            if "stage_context_file_to_workspace" not in available_context_retrieval_tools(
                runtime_context_manifest
            ):
                return self._attachment_parser_failure_result(
                    error_code="attachment_parser_staging_not_authorized"
                )
            runtime_context_manifest["attachment_preprocessing"] = attachment_contract
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
            tool_policy_subjects=_runtime_tool_policy_subjects(payload, runtime_context_manifest),
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
            governed_permission_wait=False,
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
        return self._executor_result_from_sandbox_runtime(payload, prepared, runtime_result)

    def _sandbox_provider_required_result(
        self,
        *,
        sandbox_provider: str,
        runtime_started: bool,
        runtime_terminal_status: str = "",
    ) -> ExecutorResult:
        return ExecutorResult(
            status="failed",
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities={**self.capabilities, "platform_skills": True},
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

    def _attachment_parser_failure_result(
        self,
        *,
        error_code: str,
        sandbox_provider: str = "",
        runtime_started: bool = False,
        runtime_terminal_status: str = "",
        evidence: object = None,
    ) -> ExecutorResult:
        return ExecutorResult(
            status="failed",
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities={**self.capabilities, "platform_skills": True},
            result={
                "message": "Required attachment parser evidence is unavailable.",
                "error_code": error_code,
                "sdk_used": False,
                "delegate_used": False,
                "worker_boundary": self.executor_type,
            },
            artifacts=[],
            executor_payload={
                "sdk_used": False,
                "delegate_used": False,
                "worker_boundary": self.executor_type,
                "sandbox_provider": sandbox_provider,
                "sandbox_runtime_used": runtime_started,
                "runtime_terminal_status": runtime_terminal_status,
                "attachment_parser_evidence": evidence if isinstance(evidence, list) else [],
            },
        )

    def _context_file_failure_result(self, *, error_code: str) -> ExecutorResult:
        safe_error_code = (
            "context_file_too_large"
            if error_code == "context_file_too_large"
            else "context_file_preprocessing_failed"
        )
        message = (
            "The input file exceeds the 32 MiB processing limit."
            if safe_error_code == "context_file_too_large"
            else "The input file could not be prepared for execution."
        )
        return ExecutorResult(
            status="failed",
            adapter_version=self.adapter_version,
            executor_type=self.executor_type,
            executor_version=self.executor_version,
            capabilities={**self.capabilities, "platform_skills": True},
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
            },
        )

    def _executor_result_from_sandbox_runtime(
        self,
        payload: RunPayload,
        prepared: PreparedSdkRun,
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
                sandbox_provider=sandbox_provider,
                runtime_started=True,
                runtime_terminal_status=runtime_status,
            )
        parser_evidence = executor_response.get("attachment_parser_evidence")
        try:
            attachment_requirements = attachment_requirements_from_contract(
                _attachment_preprocessing_contract(payload, prepared)
            )
        except AttachmentPreprocessingError as exc:
            return self._attachment_parser_failure_result(
                error_code=exc.code,
                sandbox_provider=sandbox_provider,
                runtime_started=True,
                runtime_terminal_status=runtime_status,
                evidence=parser_evidence,
            )
        evidence_valid, evidence_error = validate_required_parser_evidence(
            requirements=attachment_requirements,
            evidence=parser_evidence,
        )
        if runtime_status in _SANDBOX_SUCCESS_TERMINAL_STATUSES and not evidence_valid:
            return self._attachment_parser_failure_result(
                error_code=evidence_error,
                sandbox_provider=sandbox_provider,
                runtime_started=True,
                runtime_terminal_status=runtime_status,
                evidence=parser_evidence,
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
        used_skill_names = _sdk_used_skill_names(
            runtime_sdk_result,
            prepared.staged_skill_names,
        )
        used_skills_source = _sdk_used_skills_source(runtime_sdk_result, used_skill_names)
        capability_evidence_error = _capability_execution_error(
            payload,
            capability_evidence,
            available_skill_ids=prepared.staged_skill_names,
            claimed_used_skill_ids=used_skill_names,
        )
        skill_manifests = _skill_manifests(
            prepared.selected_skills,
            used_skill_names=used_skill_names,
            pins=prepared.pinned_manifests,
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
            "skill_manifests": skill_manifests,
            "sandbox_provider": sandbox_provider,
            "sandbox_runtime_used": True,
            "sandbox_timings": sandbox_timings,
            "diagnostic_id": str(executor_response.get("diagnostic_id") or "") or None,
            "attachment_parser_evidence": parser_evidence if isinstance(parser_evidence, list) else [],
            "capability_evidence": capability_evidence,
            "capability_evidence_validated": capability_evidence_error is None,
        }
        raw_used_skills_source = str(
            executor_response.get("used_skills_source") or ""
        ).strip()
        sdk_boundary_error = (
            runtime_status in _SANDBOX_SUCCESS_TERMINAL_STATUSES
            and (
                executor_response.get("sdk_used") is not True
                or raw_used_skills_source not in {"", "executor_hook"}
            )
        )
        if sdk_boundary_error:
            error_code = "claude_agent_sdk_required"
            turn_diagnostics = _public_sdk_turn_diagnostics(
                payload,
                executor_response.get("sdk_turn_diagnostics"),
                error_code=error_code,
                used_skill_ids=[],
                public_skill_metadata=prepared.public_skill_metadata,
            )
            return ExecutorResult(
                status="failed",
                adapter_version=self.adapter_version,
                executor_type=self.executor_type,
                executor_version=self.executor_version,
                capabilities={**self.capabilities, "platform_skills": True},
                result={
                    "message": "Claude Agent SDK execution evidence was unavailable. Please retry.",
                    "error_code": error_code,
                    "sdk_used": False,
                    "sdk_error": error_code,
                    "delegate_used": False,
                    "worker_boundary": self.executor_type,
                    "allowed_skills": prepared.allowed_skill_names,
                    "staged_skills": prepared.staged_skill_names,
                    "used_skills": [],
                    "sdk_turn_diagnostics": turn_diagnostics,
                },
                artifacts=[],
                executor_payload={
                    **common_payload,
                    "sdk_used": False,
                    "sdk_error": error_code,
                    "used_skills": [],
                    "used_skills_source": "none",
                    "sdk_turn_diagnostics": turn_diagnostics,
                },
            )
        if runtime_status in _SANDBOX_SUCCESS_TERMINAL_STATUSES and capability_evidence_error is not None:
            turn_diagnostics = _public_sdk_turn_diagnostics(
                payload,
                executor_response.get("sdk_turn_diagnostics"),
                error_code=capability_evidence_error,
                used_skill_ids=used_skill_names,
                public_skill_metadata=prepared.public_skill_metadata,
            )
            return ExecutorResult(
                status="failed",
                adapter_version=self.adapter_version,
                executor_type=self.executor_type,
                executor_version=self.executor_version,
                capabilities={**self.capabilities, "platform_skills": True},
                result={
                    "message": "Capability execution evidence was incomplete. Please retry.",
                    "error_code": capability_evidence_error,
                    "sdk_used": bool(executor_response.get("sdk_used")),
                    "sdk_session_id": executor_response.get("sdk_session_id"),
                    "sdk_error": capability_evidence_error,
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
                    "sdk_error": capability_evidence_error,
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
                capabilities={**self.capabilities, "platform_skills": True},
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
                capabilities={**self.capabilities, "platform_skills": True},
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
            capabilities={**self.capabilities, "platform_skills": True},
            result={
                "message": str(executor_response.get("message") or "任务完成"),
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
    ) -> ExecutorResult | None:
        settings = get_settings()
        if not settings.claude_agent_sdk_enabled:
            return None
        sandbox_required = _ordinary_run_requires_sandbox(payload)
        if not sandbox_required:
            return self._sandbox_provider_required_result(
                sandbox_provider="",
                runtime_started=False,
            )
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
        try:
            attachment_requirements_from_contract(
                _attachment_preprocessing_contract(payload, prepared)
            )
        except AttachmentPreprocessingError as exc:
            return self._attachment_parser_failure_result(error_code=exc.code)
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
        if workspace.exists() and workspace.is_symlink():
            raise ValueError("run workspace must not be a symlink")
        typed_preprocessing = _requires_typed_attachment_preprocessing(payload)
        result = await materialize_run_context_files(
            transaction_factory=transaction,
            repository=repositories,
            storage=ObjectStorage() if typed_preprocessing else None,
            workspace=workspace,
            tenant_id=payload.tenant_id,
            workspace_id=payload.workspace_id,
            user_id=payload.user_id,
            session_id=payload.session_id,
            run_id=payload.run_id,
            file_ids=payload.file_ids,
            typed_preprocessing=typed_preprocessing,
        )
        return _MaterializedFileNames(
            list(result.file_names),
            attachment_facts=list(result.attachment_facts),
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
            artifact_type = _artifact_type(path.name)
            if path.suffix.lower() == ".docx" and not _is_usable_docx(path):
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


def _context_retrieval_tool_names(context_manifest: dict[str, Any] | None) -> list[str]:
    return available_context_retrieval_tools(context_manifest)


def _runtime_tool_policy_subjects(
    payload: RunPayload,
    context_manifest: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    value = payload.input.get("_runtime_tool_policy_subjects")
    subjects = (
        [
            dict(item)
            for item in value
            if isinstance(item, dict)
            and not str(item.get("identity") or "").startswith("mcp__ai-platform-context__")
        ]
        if isinstance(value, list)
        else []
    )
    subjects.extend(
        internal_context_tool_policy_subjects(
            _context_retrieval_tool_names(context_manifest)
        )
    )
    return subjects


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
    requested: list[str] = []
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
            # The pinned-skill payload contract reports malformed entries as value errors.
            raise ValueError(f"invalid pinned skill file entry: {skill_name}")  # noqa: TRY004
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


def _artifact_type(filename: str) -> str:
    lower = filename.lower()
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
    if artifact_type == "result_docx":
        return "Word 文件"
    if artifact_type == "result_json":
        return "结果 JSON"
    if artifact_type == "report_txt":
        return "详细报告"
    return filename
