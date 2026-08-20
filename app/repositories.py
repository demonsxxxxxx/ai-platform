from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from psycopg import AsyncConnection

from app.auth import ADMIN_ROLE_ALIASES, normalize_roles
from app.capability_distribution import (
    CapabilityAccessDecision,
    CapabilityAccessContext,
    CapabilityAuthorizationDenial,
    CapabilityDistributionSubject,
    capability_distribution_audit_payload,
    has_valid_capability_distribution_archive_evidence,
    is_capability_distribution_archived as shared_capability_distribution_archived,
    is_valid_archive_actor,
    resolve_capability_access,
)
from app.context.file_continuity import (
    compatible_reusable_file_ids,
    has_file_input_mode,
    list_authorized_session_input_files as list_authorized_session_input_files,
)
from app.control_plane_contracts import (
    ARTIFACT_MANIFEST_SCHEMA_VERSION,
    AUDIT_EVENT_SCHEMA_VERSION,
    EVENT_ENVELOPE_SCHEMA_VERSION,
    EXECUTOR_RESULT_SCHEMA_VERSION,
    HARNESS_CHAT_EXECUTOR_TYPE,
    HASH_LIKE_VALUE_PATTERN,
    LEGACY_SYNTHETIC_CHAT_SKILL_ID,
    RUN_CONTRACT_VERSION,
    RUN_EXECUTION_KIND_HARNESS_CHAT,
    RUN_EXECUTION_KIND_SKILL,
    RUN_PAYLOAD_SCHEMA_VERSION,
    RUN_PAYLOAD_SCHEMA_VERSION_V2,
    artifact_lineage_contract,
    artifact_manifest_contract,
    is_legacy_synthetic_chat_identity,
    sanitize_public_payload,
    sanitize_public_text,
    standard_error_code,
    standard_trace_id,
)
from app.error_taxonomy import summarize_error_categories
from app.file_type_validation import profile_file_type_allowed
from app.memory_redaction import normalize_memory_redaction_mode, redact_memory_metadata, redact_memory_text
from app.persistence import (
    RepositoryNotFoundError,
    artifacts,
    chat_submissions,
    file_deletions,
    object_deletions,
    retention,
)
import app.agent_apps.infrastructure.postgres as agent_profile_persistence
import app.conversations.infrastructure.postgres as conversation_persistence
import app.platform.postgres.errors as postgres_errors
import app.runs.api as runs_api
import app.runs.infrastructure.postgres as run_persistence
import app.skills.infrastructure.postgres as skill_persistence
from app.platform.postgres.errors import RepositoryConflictError
from app.persistence_limits import (
    ARTIFACT_MANIFEST_MAX_BYTES,
    AUDIT_PAYLOAD_MAX_BYTES,
    CONTEXT_SNAPSHOT_PAYLOAD_MAX_BYTES,
    MESSAGE_CONTENT_MAX_BYTES,  # noqa: F401 - legacy compatibility export
    MESSAGE_METADATA_MAX_BYTES,  # noqa: F401 - legacy compatibility export
    RUN_INPUT_MAX_BYTES,
    RUN_RESULT_MAX_BYTES,
    RUN_STEP_PAYLOAD_MAX_BYTES,
    PersistenceSizeLimitError,
    compact_json_dumps,
    ensure_json_size,
    ensure_text_size,
)
from app.projection_redaction import sanitize_user_control_input, strip_server_owned_control_metadata
from app import run_event_repository as _run_event_repository
from app.run_admission_policy import RETIRED_PLATFORM_MULTI_AGENT_TERMINAL_REASON
from app.skills.dependencies import PUBLIC_WORKBENCH_SKILL_IDS, is_workbench_skill_public
from app.skills.lifecycle import is_user_runnable_status
from app.skills.pinning import (
    SKILL_PINNED_SNAPSHOT_GOVERNANCE_SCHEMA_VERSION_V1,
    SKILL_PINNED_SNAPSHOT_GOVERNANCE_SCHEMA_VERSION_V2,
    SkillVersionMaterializationError,
    build_skill_manifest_refs,
    build_skill_snapshot_governance,  # noqa: F401 - migration bridge AST compatibility
    skill_manifest_materialization_sha256,
    validate_skill_manifest_refs,
)
from app.skills.release_policy import resolve_rollout_skill_decision
from app.tool_policy import evaluate_tool_policy, max_risk
from app.validation import SAFE_ID_PATTERN
from app.tool_permission_lifecycle import (
    TOOL_PERMISSION_EXPIRY_BATCH_LIMIT,
    TOOL_PERMISSION_REQUEST_TTL_SECONDS,
)

claim_object_deletions = object_deletions.claim_object_deletions
complete_object_deletion = object_deletions.complete_object_deletion
fail_object_deletion = object_deletions.fail_object_deletion
requeue_dead_letter_object_deletion = object_deletions.requeue_dead_letter_object_deletion
ObjectDeletionStateError = object_deletions.ObjectDeletionStateError
FileDeletionBlockedError = file_deletions.FileDeletionBlockedError
queue_unbound_file_for_deletion = file_deletions.queue_unbound_file_for_deletion
get_admin_artifact = artifacts.get_admin_artifact
get_artifact = artifacts.get_artifact
get_authorized_artifact = artifacts.get_authorized_artifact
list_revealed_artifact_sessions = artifacts.list_revealed_artifact_sessions
list_revealed_artifacts = artifacts.list_revealed_artifacts
queue_expired_artifacts_for_deletion = artifacts.queue_expired_artifacts_for_deletion
get_data_retention_backlog = retention.get_data_retention_backlog
purge_deleted_memory_records = retention.purge_deleted_memory_records
acquire_agent_profile_lifecycle_lock = (
    agent_profile_persistence.acquire_agent_profile_lifecycle_lock
)
create_agent_profile_revision = agent_profile_persistence.create_agent_profile_revision
ensure_agent_profile_identity = agent_profile_persistence.ensure_agent_profile_identity
get_agent_profile_aggregate = agent_profile_persistence.get_agent_profile_aggregate
get_agent_profile_revision = agent_profile_persistence.get_agent_profile_revision
get_bound_published_agent_profile = (
    agent_profile_persistence.get_bound_published_agent_profile
)
get_current_published_agent_profile = (
    agent_profile_persistence.get_current_published_agent_profile
)
list_agent_profile_revision_history = (
    agent_profile_persistence.list_agent_profile_revision_history
)
list_current_published_agent_profiles = (
    agent_profile_persistence.list_current_published_agent_profiles
)
list_latest_agent_profile_revisions = (
    agent_profile_persistence.list_latest_agent_profile_revisions
)
record_agent_profile_draft = agent_profile_persistence.record_agent_profile_draft
record_agent_profile_publication = agent_profile_persistence.record_agent_profile_publication
record_agent_profile_withdrawal = agent_profile_persistence.record_agent_profile_withdrawal
append_message = conversation_persistence.append_message
create_session = conversation_persistence.create_session
ensure_workspace_belongs_to_tenant = (
    conversation_persistence.ensure_workspace_belongs_to_tenant
)
get_authorized_lambchat_session = conversation_persistence.get_authorized_lambchat_session
get_authorized_session_projection = conversation_persistence.get_authorized_session_projection
get_session_for_action = conversation_persistence.get_session_for_action
list_authorized_messages = conversation_persistence.list_authorized_messages
list_authorized_sessions = conversation_persistence.list_authorized_sessions
list_authorized_user_messages_for_runs = (
    conversation_persistence.list_authorized_user_messages_for_runs
)
list_session_messages_for_fork = conversation_persistence.list_session_messages_for_fork
mark_session_deleted = conversation_persistence.mark_session_deleted
update_session_title = conversation_persistence.update_session_title
_stage_run_tool_permission_terminalization = (
    run_persistence._stage_run_tool_permission_terminalization
)
acquire_user_active_run_admission_lock = (
    run_persistence.acquire_user_active_run_admission_lock
)
count_active_runs_for_user = run_persistence.count_active_runs_for_user
enforce_user_active_run_admission = run_persistence.enforce_user_active_run_admission
enforce_user_active_run_admission_under_lock = (
    run_persistence.enforce_user_active_run_admission_under_lock
)
get_active_resume_for_source_run = run_persistence.get_active_resume_for_source_run
get_active_retry_for_source_run = run_persistence.get_active_retry_for_source_run
get_run = run_persistence.get_run
get_run_identity = run_persistence.get_run_identity
RepositoryAuthorizationError = postgres_errors.RepositoryAuthorizationError
canonical_builtin_tool_identities = skill_persistence.canonical_builtin_tool_identities
get_skill_version = skill_persistence.get_skill_version
run_skill_snapshot_source_json = skill_persistence.run_skill_snapshot_source_json
validate_replay_skill_manifests = skill_persistence.validate_replay_skill_manifests
# Preserve the established repository facade used by Chat callers while making
# the cross-module ownership explicit to Ruff.
chat_submission_fingerprint = chat_submissions.chat_submission_fingerprint
claim_chat_submission = chat_submissions.claim_chat_submission
finalize_chat_submission = chat_submissions.finalize_chat_submission
get_chat_submission = chat_submissions.get_chat_submission
DEFAULT_RUN_EXECUTOR_TYPES = {"claude-agent-worker"}
ACTIVE_RUN_STATUSES = {"queued", "running"}
RETRYABLE_RUN_STATUSES = {"failed", "dead-letter", "dead_letter", "dead-lettered"}
RUN_CONTROL_OPERATION_ACTIONS = {"retry", "resume"}
MEMORY_RETENTION_CLEANUP_CURSOR_KEY = "memory_retention_cleanup"
TOOL_PERMISSION_TERMINALIZATION_BATCH_LIMIT = TOOL_PERMISSION_EXPIRY_BATCH_LIMIT
TOOL_PERMISSION_TERMINALIZATION_MAINTENANCE_LIMIT = TOOL_PERMISSION_EXPIRY_BATCH_LIMIT
CONTEXT_SNAPSHOT_MEMBER_BATCH_LIMIT = 128


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def memory_policy_id(*, tenant_id: str, workspace_id: str, user_id: str, agent_id: str | None) -> str:
    raw = "\x1f".join([tenant_id, workspace_id, user_id, agent_id or ""])
    return f"mempol_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def _require_json_size(value: Any, *, max_bytes: int, code: str) -> None:
    try:
        ensure_json_size(value, max_bytes=max_bytes, code=code)
    except PersistenceSizeLimitError as exc:
        raise RepositoryConflictError(exc.code) from exc


def _require_text_size(value: str, *, max_bytes: int, code: str) -> None:
    try:
        ensure_text_size(value, max_bytes=max_bytes, code=code)
    except PersistenceSizeLimitError as exc:
        raise RepositoryConflictError(exc.code) from exc


async def tenant_exists(conn: AsyncConnection, *, tenant_id: str) -> bool:
    """Return whether the tenant identity is already provisioned."""

    cursor = await conn.execute(
        """
        select 1
        from tenants
        where id = %s
        """,
        (tenant_id,),
    )
    return await cursor.fetchone() is not None


def dumps_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)


def _effective_status(registry_status: str, policy_status: str) -> str:
    return "active" if registry_status == "active" and policy_status == "active" else "disabled"


def _tool_policy_projection(row: dict[str, Any], *, tenant_id: str) -> dict[str, Any]:
    has_tenant_policy = row.get("policy_status") is not None
    policy_source = "tenant" if has_tenant_policy else "registry"
    registry_status = str(row.get("registry_status") or row.get("status") or "disabled")
    policy_status = str(row.get("policy_status") or "disabled")
    registry_write_capable = _coerce_bool(row.get("registry_write_capable"), _coerce_bool(row.get("write_capable")))
    policy_write_capable = _coerce_bool(row.get("policy_write_capable"), False)
    registry_risk_level = str(row.get("registry_risk_level") or row.get("risk_level") or "low")
    policy_risk_level = str(row.get("policy_risk_level") or "low")
    registry_visible_to_user = _coerce_bool(row.get("registry_visible_to_user"), _coerce_bool(row.get("visible_to_user"), True))
    policy_visible_to_user = _coerce_bool(row.get("policy_visible_to_user"), False)
    effective_visible_to_user = registry_visible_to_user and policy_visible_to_user
    effective_policy_status = _effective_status(registry_status, policy_status)
    if not effective_visible_to_user:
        effective_policy_status = "disabled"
    return {
        "tenant_id": tenant_id,
        "tool_id": str(row.get("tool_id") or row.get("id") or ""),
        "id": str(row.get("tool_id") or row.get("id") or ""),
        "server_id": str(row.get("server_id") or ""),
        "name": str(row.get("name") or ""),
        "description": str(row.get("description") or ""),
        "registry_status": registry_status,
        "policy_status": policy_status,
        "effective_status": effective_policy_status,
        "status": effective_policy_status,
        "registry_write_capable": registry_write_capable,
        "policy_write_capable": policy_write_capable,
        "write_capable": registry_write_capable or policy_write_capable,
        "registry_risk_level": registry_risk_level,
        "policy_risk_level": policy_risk_level,
        "risk_level": max_risk(registry_risk_level, policy_risk_level),
        "registry_visible_to_user": registry_visible_to_user,
        "policy_visible_to_user": policy_visible_to_user,
        "visible_to_user": effective_visible_to_user,
        "source": policy_source,
        "reason": str(row.get("reason") or ""),
        "updated_by": row.get("updated_by"),
        "updated_at": row.get("updated_at"),
    }


def _repository_ledger_conflict(error: _run_event_repository.RunEventLedgerConflictError) -> RepositoryConflictError:
    return RepositoryConflictError(str(error))


def _required_schema_version(row: dict[str, Any], field: str, expected: str, error_code: str) -> str:
    value = row.get(field)
    if value != expected:
        raise RepositoryConflictError(error_code)
    return str(value)


def _result_observability_values(result_json: dict[str, Any] | None) -> tuple[int | None, int, int, int, int]:
    result = result_json or {}
    token_counts = result.get("token_counts") if isinstance(result.get("token_counts"), dict) else {}
    cost = result.get("cost") if isinstance(result.get("cost"), dict) else {}
    latency = result.get("latency_ms")
    try:
        latency_ms = int(latency) if latency is not None else None
    except (TypeError, ValueError):
        latency_ms = None
    try:
        input_tokens = int(token_counts.get("input") or 0)
    except (TypeError, ValueError):
        input_tokens = 0
    try:
        output_tokens = int(token_counts.get("output") or 0)
    except (TypeError, ValueError):
        output_tokens = 0
    try:
        total_tokens = int(token_counts.get("total") or (input_tokens + output_tokens))
    except (TypeError, ValueError):
        total_tokens = input_tokens + output_tokens
    try:
        estimated_cost_minor = int(cost.get("estimated_cost_minor") or 0)
    except (TypeError, ValueError):
        estimated_cost_minor = 0
    return latency_ms, input_tokens, output_tokens, total_tokens, estimated_cost_minor


async def _resolve_executable_skill(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    skill_id: str,
    require_default_skill: bool,
) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        select
          agents.id as agent_id,
          agents.status as agent_status,
          agents.default_skill_id,
          skills.id as skill_id,
          skills.name as skill_display_label,
          skills.status as skill_status,
          coalesce(skill_release_policies.current_version, skills.version) as skill_version,
          coalesce(skill_versions.content_hash, coalesce(skill_release_policies.current_version, skills.version)) as skill_content_hash,
          coalesce(skill_versions.status, 'active') as skill_version_status,
          skill_release_policies.current_version as release_policy_version,
          skill_release_policies.previous_version as release_policy_previous_version,
          skill_release_policies.rollout_percent as release_policy_rollout_percent,
          skills.executor_type,
          mcp_tools.id as backing_mcp_tool_id,
          mcp_tools.status as mcp_tool_status,
          mcp_tools.status as registry_status,
          tool_policies.status as policy_status,
          mcp_tools.write_capable as registry_write_capable,
          tool_policies.write_capable as policy_write_capable,
          mcp_tools.risk_level as registry_risk_level,
          tool_policies.risk_level as policy_risk_level,
          mcp_tools.visible_to_user as registry_visible_to_user,
          tool_policies.visible_to_user as policy_visible_to_user,
          mcp_tools.server_id as server_id,
          skills.input_modes
        from agents
        join skills on skills.id = %s
        left join skill_release_policies
          on skill_release_policies.tenant_id = agents.tenant_id
         and skill_release_policies.skill_id = skills.id
         and skill_release_policies.channel = 'stable'
         and skill_release_policies.status = 'active'
        left join skill_versions
          on skill_versions.skill_id = skills.id
         and skill_versions.version = coalesce(skill_release_policies.current_version, skills.version)
        left join mcp_tools on mcp_tools.id = skills.id
        left join tool_policies
          on tool_policies.tenant_id = agents.tenant_id
         and tool_policies.tool_id = mcp_tools.id
        where agents.tenant_id = %s and agents.id = %s
        """,
        (skill_id, tenant_id, agent_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("agent_or_skill_not_found")
    row = dict(row)
    if row["agent_status"] != "active":
        raise RepositoryConflictError("agent_inactive")
    if row["skill_status"] != "active":
        raise RepositoryConflictError("skill_inactive")
    if not is_user_runnable_status(row.get("skill_version_status", "active")):
        raise RepositoryConflictError("skill_version_not_released")
    skill_version = str(row.get("skill_version") or "")
    skill_content_hash = str(row.get("skill_content_hash") or skill_version)
    if not skill_version or skill_content_hash != skill_version:
        raise RepositoryConflictError("skill_version_not_materializable")
    if row["executor_type"] not in DEFAULT_RUN_EXECUTOR_TYPES:
        raise RepositoryConflictError("executor_type_not_allowed")
    if require_default_skill and row["default_skill_id"] != skill_id:
        raise RepositoryConflictError("agent_skill_mismatch")
    return row


async def resolve_agent_skill(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    skill_id: str,
) -> dict[str, Any]:
    """Resolve an active fixed capability Skill bound as the Agent default."""

    return await _resolve_executable_skill(
        conn,
        tenant_id=tenant_id,
        agent_id=agent_id,
        skill_id=skill_id,
        require_default_skill=True,
    )


async def resolve_selected_skill(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    skill_id: str,
) -> dict[str, Any]:
    """Resolve an active ordinary-user selected Skill without default binding."""

    return await _resolve_executable_skill(
        conn,
        tenant_id=tenant_id,
        agent_id=agent_id,
        skill_id=skill_id,
        require_default_skill=False,
    )


async def ensure_mcp_tool_active(conn: AsyncConnection, *, tenant_id: str, tool_id: str) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        select
          mcp_tools.id,
          mcp_tools.server_id,
          mcp_tools.name,
          mcp_tools.description,
          mcp_tools.transport_type,
          mcp_tools.endpoint,
          mcp_tools.auth_mode,
          mcp_tools.allowed_tools,
          mcp_tools.status as registry_status,
          tool_policies.status as policy_status,
          mcp_tools.write_capable as registry_write_capable,
          tool_policies.write_capable as policy_write_capable,
          mcp_tools.risk_level as registry_risk_level,
          tool_policies.risk_level as policy_risk_level,
          mcp_tools.visible_to_user as registry_visible_to_user,
          tool_policies.visible_to_user as policy_visible_to_user
        from mcp_tools
        left join tool_policies
          on tool_policies.tenant_id = %s
         and tool_policies.tool_id = mcp_tools.id
        where mcp_tools.id = %s
          and """ + _mcp_repository.mcp_tool_tenant_authority_sql() + """
        """,
        (tenant_id, tool_id, tenant_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("mcp_tool_not_found")
    policy = _tool_policy_projection(dict(row), tenant_id=tenant_id)
    if policy["effective_status"] != "active" or not policy["visible_to_user"]:
        raise RepositoryConflictError("mcp_tool_disabled")
    return policy




async def get_tenant_profile_validation_agent(
    conn: AsyncConnection,
    *,
    tenant_id: str,
) -> str | None:
    """Find a same-tenant active agent for capability-only unsaved draft validation."""

    cursor = await conn.execute(
        """
        select id
        from agents
        where tenant_id = %s and status = 'active'
        order by case when id = 'general-agent' then 0 else 1 end, id asc
        limit 1
        """,
        (tenant_id,),
    )
    row = await cursor.fetchone()
    return str(row["id"]) if row is not None else None


async def list_scoped_context_messages(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select messages.id, messages.session_id, messages.run_id, messages.role, messages.content,
               messages.metadata_json, messages.created_at
        from messages
        join runs source_runs on source_runs.id = messages.run_id and source_runs.tenant_id = messages.tenant_id
        join runs current_run on current_run.id = %s and current_run.tenant_id = messages.tenant_id
        join sessions on sessions.id = current_run.session_id and sessions.tenant_id = current_run.tenant_id
        join run_context_snapshots context_snapshot
          on context_snapshot.id = current_run.context_snapshot_id
          and context_snapshot.tenant_id = current_run.tenant_id
          and context_snapshot.workspace_id = current_run.workspace_id
          and context_snapshot.user_id = current_run.user_id
          and context_snapshot.session_id = current_run.session_id
          and context_snapshot.run_id = current_run.id
          and context_snapshot.context_kind = 'executor'
        where messages.tenant_id = %s
          and current_run.workspace_id = %s
          and current_run.user_id = %s
          and messages.session_id = %s
          and current_run.id = %s
          and current_run.input_json->>'context_snapshot_id' = current_run.context_snapshot_id
          and current_run.input_json->'context_snapshot'->>'context_snapshot_id' = current_run.context_snapshot_id
          and source_runs.workspace_id = current_run.workspace_id
          and source_runs.user_id = current_run.user_id
          and source_runs.session_id = current_run.session_id
          and sessions.user_id = current_run.user_id
          and sessions.workspace_id = current_run.workspace_id
          and context_snapshot.included_message_ids ? messages.id
        order by messages.created_at asc
        limit %s offset %s
        """,
        (run_id, tenant_id, workspace_id, user_id, session_id, run_id, max(1, int(limit)), max(0, int(offset))),
    )
    return list(await cursor.fetchall())


async def get_scoped_context_file(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    file_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select files.*
        from files
        join runs source_run on source_run.id = files.run_id and source_run.tenant_id = files.tenant_id
        join runs current_run on current_run.id = %s and current_run.tenant_id = files.tenant_id
        join sessions on sessions.id = current_run.session_id
          and sessions.tenant_id = current_run.tenant_id
          and sessions.status = 'active'
        join run_context_snapshots context_snapshot
          on context_snapshot.id = current_run.context_snapshot_id
          and context_snapshot.tenant_id = current_run.tenant_id
          and context_snapshot.workspace_id = current_run.workspace_id
          and context_snapshot.user_id = current_run.user_id
          and context_snapshot.session_id = current_run.session_id
          and context_snapshot.run_id = current_run.id
          and context_snapshot.context_kind = 'executor'
        where files.tenant_id = %s
          and current_run.workspace_id = %s
          and current_run.user_id = %s
          and current_run.session_id = %s
          and current_run.id = %s
          and current_run.input_json->>'context_snapshot_id' = current_run.context_snapshot_id
          and current_run.input_json->'context_snapshot'->>'context_snapshot_id' = current_run.context_snapshot_id
          and files.workspace_id = current_run.workspace_id
          and files.user_id = current_run.user_id
          and files.session_id = current_run.session_id
          and source_run.workspace_id = current_run.workspace_id
          and source_run.user_id = current_run.user_id
          and source_run.session_id = current_run.session_id
          and sessions.user_id = current_run.user_id
          and sessions.workspace_id = current_run.workspace_id
          and context_snapshot.included_file_ids ? files.id
          and files.id = %s
        """,
        (run_id, tenant_id, workspace_id, user_id, session_id, run_id, file_id),
    )
    return await cursor.fetchone()


async def get_scoped_context_artifact(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    artifact_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select artifacts.*
        from artifacts
        join runs source_run on source_run.id = artifacts.run_id and source_run.tenant_id = artifacts.tenant_id
        join runs current_run on current_run.id = %s and current_run.tenant_id = artifacts.tenant_id
        join sessions on sessions.id = current_run.session_id and sessions.tenant_id = current_run.tenant_id
        join run_context_snapshots context_snapshot
          on context_snapshot.id = current_run.context_snapshot_id
          and context_snapshot.tenant_id = current_run.tenant_id
          and context_snapshot.workspace_id = current_run.workspace_id
          and context_snapshot.user_id = current_run.user_id
          and context_snapshot.session_id = current_run.session_id
          and context_snapshot.run_id = current_run.id
          and context_snapshot.context_kind = 'executor'
        where artifacts.tenant_id = %s
          and current_run.workspace_id = %s
          and current_run.user_id = %s
          and current_run.session_id = %s
          and current_run.id = %s
          and current_run.input_json->>'context_snapshot_id' = current_run.context_snapshot_id
          and current_run.input_json->'context_snapshot'->>'context_snapshot_id' = current_run.context_snapshot_id
          and source_run.workspace_id = current_run.workspace_id
          and source_run.user_id = current_run.user_id
          and source_run.session_id = current_run.session_id
          and sessions.user_id = current_run.user_id
          and sessions.workspace_id = current_run.workspace_id
          and context_snapshot.included_artifact_ids ? artifacts.id
          and artifacts.id = %s
          and artifacts.lifecycle_state = 'active'
          and (artifacts.expires_at is null or artifacts.expires_at > now())
        """,
        (run_id, tenant_id, workspace_id, user_id, session_id, run_id, artifact_id),
    )
    return await cursor.fetchone()


async def list_session_context_messages(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return a bounded ordered message tail for one exact owned session."""

    cursor = await conn.execute(
        """
        with current_run as (
          select runs.session_generation
          from runs
          join sessions on sessions.id = runs.session_id
            and sessions.tenant_id = runs.tenant_id
          where runs.tenant_id = %s
            and runs.workspace_id = %s
            and runs.user_id = %s
            and runs.session_id = %s
            and runs.id = %s
            and sessions.status = 'active'
            and runs.session_generation is not null
        )
        select *
        from (
          select messages.id, messages.run_id, messages.role, messages.content,
                 messages.metadata_json, messages.created_at, runs.session_generation
          from messages
          join sessions on sessions.id = messages.session_id and sessions.tenant_id = messages.tenant_id
          join runs on runs.id = messages.run_id and runs.tenant_id = messages.tenant_id
          where messages.tenant_id = %s
            and messages.session_id = %s
            and sessions.workspace_id = %s
            and sessions.user_id = %s
            and sessions.status = 'active'
            and runs.workspace_id = sessions.workspace_id
            and runs.user_id = sessions.user_id
            and runs.session_id = sessions.id
            and runs.session_generation is not null
            and runs.session_generation < (select session_generation from current_run)
          order by runs.session_generation desc, messages.created_at desc, messages.id desc
          limit %s
        ) recent_messages
        order by session_generation asc, created_at asc, id asc
        """,
        (
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            run_id,
            tenant_id,
            session_id,
            workspace_id,
            user_id,
            max(1, int(limit)),
        ),
    )
    return list(await cursor.fetchall())


async def count_session_context_messages(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
) -> int:
    """Count eligible ordered session history without loading its content."""

    cursor = await conn.execute(
        """
        with current_run as (
          select runs.session_generation
          from runs
          join sessions on sessions.id = runs.session_id
            and sessions.tenant_id = runs.tenant_id
          where runs.tenant_id = %s
            and runs.workspace_id = %s
            and runs.user_id = %s
            and runs.session_id = %s
            and runs.id = %s
            and sessions.status = 'active'
            and runs.session_generation is not null
        )
        select count(*) as context_message_count
        from messages
        join sessions on sessions.id = messages.session_id and sessions.tenant_id = messages.tenant_id
        join runs on runs.id = messages.run_id and runs.tenant_id = messages.tenant_id
        where messages.tenant_id = %s
          and messages.session_id = %s
          and sessions.workspace_id = %s
          and sessions.user_id = %s
          and sessions.status = 'active'
          and runs.workspace_id = sessions.workspace_id
          and runs.user_id = sessions.user_id
          and runs.session_id = sessions.id
          and runs.session_generation is not null
          and runs.session_generation < (select session_generation from current_run)
        """,
        (
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            run_id,
            tenant_id,
            session_id,
            workspace_id,
            user_id,
        ),
    )
    row = await cursor.fetchone()
    try:
        return max(0, int((row or {}).get("context_message_count") or 0))
    except (AttributeError, TypeError, ValueError):
        return 0


async def list_session_context_files(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return a bounded recent file tail for one exact owned session."""

    cursor = await conn.execute(
        """
        with current_run as (
          select runs.session_generation
          from runs
          join sessions on sessions.id = runs.session_id
            and sessions.tenant_id = runs.tenant_id
          where runs.tenant_id = %s
            and runs.workspace_id = %s
            and runs.user_id = %s
            and runs.session_id = %s
            and runs.id = %s
            and sessions.status = 'active'
            and runs.session_generation is not null
        )
        select *
        from (
          select files.id, files.run_id, files.original_name, files.content_type,
                 files.size_bytes, files.sha256, files.created_at, runs.session_generation
          from files
          join sessions on sessions.id = files.session_id and sessions.tenant_id = files.tenant_id
          join runs on runs.id = files.run_id and runs.tenant_id = files.tenant_id
          join run_context_snapshots authorized_snapshot
            on authorized_snapshot.id = runs.context_snapshot_id
            and authorized_snapshot.tenant_id = runs.tenant_id
            and authorized_snapshot.workspace_id = runs.workspace_id
            and authorized_snapshot.user_id = runs.user_id
            and authorized_snapshot.session_id = runs.session_id
            and authorized_snapshot.run_id = runs.id
            and authorized_snapshot.context_kind = 'executor'
            and authorized_snapshot.included_file_ids ? files.id
          where files.tenant_id = %s
            and files.workspace_id = %s
            and files.user_id = %s
            and files.session_id = %s
            and sessions.workspace_id = files.workspace_id
            and sessions.user_id = files.user_id
            and sessions.status = 'active'
            and runs.workspace_id = files.workspace_id
            and runs.user_id = files.user_id
            and runs.session_id = files.session_id
            and runs.input_json->>'context_snapshot_id' = runs.context_snapshot_id
            and runs.input_json->'context_snapshot'->>'context_snapshot_id' = runs.context_snapshot_id
            and runs.session_generation is not null
            and runs.session_generation < (select session_generation from current_run)
          order by runs.session_generation desc, files.created_at desc, files.id desc
          limit %s
        ) recent_files
        order by session_generation asc, created_at asc, id asc
        """,
        (
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            run_id,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            max(1, int(limit)),
        ),
    )
    return list(await cursor.fetchall())


async def list_authorized_context_file_rows(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    file_ids: list[str],
) -> list[dict[str, Any]]:
    """Return only scoped file display metadata for an already-authorized context set."""

    normalized_ids = [str(file_id) for file_id in file_ids if isinstance(file_id, str) and file_id]
    if not normalized_ids:
        return []
    cursor = await conn.execute(
        """
        select id, original_name
        from files
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and session_id = %s
          and id = any(%s::text[])
        order by id asc
        """,
        (tenant_id, workspace_id, user_id, session_id, normalized_ids),
    )
    return list(await cursor.fetchall())


async def list_session_context_artifacts(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    exclude_run_id: str,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return artifacts from the latest successful prior run in one owned session."""

    cursor = await conn.execute(
        """
        with latest_source_run as (
          select runs.id
          from runs
          join sessions on sessions.id = runs.session_id and sessions.tenant_id = runs.tenant_id
          where runs.tenant_id = %s
            and runs.workspace_id = %s
            and runs.user_id = %s
            and runs.session_id = %s
            and runs.id <> %s
            and runs.session_generation is not null
            and runs.session_generation < (
              select session_generation
              from runs
              where tenant_id = %s
                and workspace_id = %s
                and user_id = %s
                and session_id = %s
                and id = %s
                and session_generation is not null
            )
            and runs.status = 'succeeded'
            and sessions.workspace_id = runs.workspace_id
            and sessions.user_id = runs.user_id
            and sessions.status = 'active'
            and exists (
              select 1 from artifacts
              where artifacts.tenant_id = runs.tenant_id
                and artifacts.run_id = runs.id
                and artifacts.lifecycle_state = 'active'
                and (artifacts.expires_at is null or artifacts.expires_at > now())
            )
          order by runs.session_generation desc
          limit 1
        )
        select artifacts.id, artifacts.run_id, artifacts.trace_id, artifacts.artifact_type,
               artifacts.label, artifacts.content_type, artifacts.size_bytes,
               artifacts.manifest_version, artifacts.manifest_json, artifacts.created_at
        from artifacts
        join latest_source_run on latest_source_run.id = artifacts.run_id
        where artifacts.tenant_id = %s
          and artifacts.lifecycle_state = 'active'
          and (artifacts.expires_at is null or artifacts.expires_at > now())
        order by artifacts.created_at asc, artifacts.id asc
        limit %s
        """,
        (
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            exclude_run_id,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            exclude_run_id,
            tenant_id,
            max(1, int(limit)),
        ),
    )
    return list(await cursor.fetchall())


async def session_has_legacy_run_history(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
) -> bool:
    """Report only whether pre-generation same-session history was excluded."""

    cursor = await conn.execute(
        """
        select exists (
          select 1
          from runs legacy_run
          join sessions on sessions.id = legacy_run.session_id
            and sessions.tenant_id = legacy_run.tenant_id
          where legacy_run.tenant_id = %s
            and legacy_run.workspace_id = %s
            and legacy_run.user_id = %s
            and legacy_run.session_id = %s
            and legacy_run.id <> %s
            and legacy_run.session_generation is null
            and sessions.status = 'active'
        ) as legacy_history_excluded
        """,
        (tenant_id, workspace_id, user_id, session_id, run_id),
    )
    row = await cursor.fetchone()
    return bool(row and row.get("legacy_history_excluded"))


async def list_scoped_context_memory_records(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str,
    session_id: str,
    query: str = "",
    limit: int = 20,
) -> list[dict[str, Any]]:
    query_pattern = f"%{query}%" if query else ""
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id, session_id,
               record_type, content, metadata_json, status, deleted_at, created_at
        from memory_records
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and agent_id = %s
          and session_id = %s
          and status = 'active'
          and deleted_at is null
          and (%s = '' or content ilike %s)
        order by created_at desc
        limit %s
        """,
        (tenant_id, workspace_id, user_id, agent_id, session_id, query_pattern, query_pattern, max(1, int(limit))),
    )
    return list(await cursor.fetchall())


def _principal_skill_release_decision(
    row: dict[str, Any],
    *,
    tenant_id: str,
    skill_id: str,
    rollout_key: str,
    fallback_version_field: str,
):
    return resolve_rollout_skill_decision(
        {
            "skill_version": row.get(fallback_version_field),
            "release_policy_version": row.get("release_policy_version"),
            "release_policy_previous_version": row.get("release_policy_previous_version"),
            "release_policy_rollout_percent": row.get("release_policy_rollout_percent"),
        },
        tenant_id=tenant_id,
        skill_id=skill_id,
        rollout_key=rollout_key,
    )


async def list_lambchat_agents(conn: AsyncConnection, *, tenant_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          agents.id,
          agents.name,
          agents.description,
          agents.agent_type,
          agents.default_skill_id,
          agents.status,
          coalesce(skill_release_policies.current_version, skills.version) as skill_version,
          coalesce(skill_versions.status, 'active') as skill_version_status,
          skill_release_policies.current_version as release_policy_version,
          skill_release_policies.previous_version as release_policy_previous_version,
          skill_release_policies.rollout_percent as release_policy_rollout_percent,
          previous_skill_versions.status as release_policy_previous_version_status,
          skills.input_modes,
          skills.output_modes
        from agents
        left join skills on skills.id = agents.default_skill_id
        left join skill_release_policies
          on skill_release_policies.tenant_id = agents.tenant_id
         and skill_release_policies.skill_id = skills.id
         and skill_release_policies.channel = 'stable'
         and skill_release_policies.status = 'active'
        left join skill_versions
          on skill_versions.skill_id = skills.id
         and skill_versions.version = coalesce(skill_release_policies.current_version, skills.version)
        left join skill_versions as previous_skill_versions
          on previous_skill_versions.skill_id = skills.id
         and previous_skill_versions.version = skill_release_policies.previous_version
        where agents.tenant_id = %s
          and agents.id in ('general-agent', 'baoyu-translate', 'qa-word-review')
          and agents.status = 'active'
          and (agents.default_skill_id is null or skills.status = 'active')
        order by case agents.id
          when 'general-agent' then 1
          when 'baoyu-translate' then 2
          when 'qa-word-review' then 3
          else 99
        end, agents.id asc
        """,
        (tenant_id,),
    )
    return list(await cursor.fetchall())


async def list_principal_lambchat_agents(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    actor_user_id: str,
    department_id: str,
    roles: list[str] | None,
    is_admin: bool,
    permissions: list[str] | None,
) -> list[dict[str, Any]]:
    """Return canonical Agent rows discoverable by the principal."""

    rows = await list_lambchat_agents(conn, tenant_id=tenant_id)
    distributions = await list_capability_distribution_rows(
        conn,
        tenant_id=tenant_id,
        capability_kind="skill",
        include_disabled=True,
    )
    distribution_by_skill = {
        str(distribution.get("capability_id") or ""): distribution
        for distribution in distributions
    }
    context = CapabilityAccessContext(
        tenant_id=tenant_id,
        department_id=str(department_id or ""),
        roles=normalize_roles(roles or []),
        is_admin=bool(is_admin),
        permissions=[str(item) for item in permissions or [] if str(item)],
    )
    authorized_rows: list[dict[str, Any]] = []
    for row in rows:
        projected = dict(row)
        skill_id = str(projected.get("default_skill_id") or "")
        if not skill_id:
            if str(projected.get("agent_type") or "") != "chat":
                continue
            projected["skill_version"] = None
            projected["skill_version_status"] = None
            projected["input_modes"] = ["chat"]
            projected["output_modes"] = ["answer"]
            for field in (
                "release_policy_version",
                "release_policy_previous_version",
                "release_policy_rollout_percent",
                "release_policy_previous_version_status",
            ):
                projected.pop(field, None)
            authorized_rows.append(projected)
            continue
        release_decision = _principal_skill_release_decision(
            projected,
            tenant_id=tenant_id,
            skill_id=skill_id,
            rollout_key=actor_user_id,
            fallback_version_field="skill_version",
        )
        selected_version_status = (
            projected.get("release_policy_previous_version_status")
            if release_decision.selected_track == "previous"
            else projected.get("skill_version_status", "active")
        )
        projected["skill_version"] = release_decision.selected_version
        projected["skill_version_status"] = selected_version_status
        for field in (
            "release_policy_version",
            "release_policy_previous_version",
            "release_policy_rollout_percent",
            "release_policy_previous_version_status",
        ):
            projected.pop(field, None)
        lifecycle_status = str(projected.get("status") or "disabled")
        if not is_user_runnable_status(selected_version_status):
            lifecycle_status = "disabled"
        decision = resolve_capability_access(
            context,
            CapabilityDistributionSubject(
                capability_kind="skill",
                capability_id=skill_id,
                lifecycle_status=lifecycle_status,
                distribution=distribution_by_skill.get(skill_id),
            ),
            intent="discover",
        )
        if not decision.visible:
            continue
        if decision.admin_bypass:
            await append_audit_log(
                conn,
                tenant_id=tenant_id,
                user_id=actor_user_id,
                action="capability_distribution.admin_bypass",
                target_type="skill",
                target_id=skill_id,
                trace_id=standard_trace_id(skill_id),
                payload_json=capability_distribution_audit_payload(
                    decision=decision,
                    actor_department_id=context.department_id,
                    actor_roles=context.roles,
                    capability_kind="skill",
                    capability_id=skill_id,
                ),
            )
        authorized_rows.append(projected)
    return authorized_rows


async def list_workbench_skills(conn: AsyncConnection, *, tenant_id: str, include_disabled: bool = False) -> list[dict[str, Any]]:
    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    cursor = await conn.execute(
        """
        select
          skills.id as skill_id,
          skills.name,
          skills.version,
          skills.description,
          skills.input_modes,
          skills.output_modes,
          skills.executor_type,
          skills.status as lifecycle_status,
          coalesce(tenant_capability_distributions.status, 'disabled') as status,
          coalesce(tenant_capability_distributions.visible_to_user, false) as visible_to_user
        from skills
        left join tenant_capability_distributions
          on tenant_capability_distributions.tenant_id = %s
         and tenant_capability_distributions.capability_kind = 'skill'
         and tenant_capability_distributions.capability_id = skills.id
        where skills.id in ('qa-file-reviewer', 'baoyu-translate', 'ragflow-knowledge-search')
          and (%s or (
            skills.status = 'active'
            and tenant_capability_distributions.status = 'active'
          ))
        order by case skills.id
          when 'qa-file-reviewer' then 1
          when 'baoyu-translate' then 2
          when 'ragflow-knowledge-search' then 3
          else 99
        end
        """,
        (tenant_id, include_disabled),
    )
    return list(await cursor.fetchall())


async def get_skill(conn: AsyncConnection, *, skill_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select id as skill_id, version, status
        from skills
        where id = %s
        """,
        (skill_id,),
    )
    return await cursor.fetchone()


async def list_skill_ids(conn: AsyncConnection) -> list[str]:
    cursor = await conn.execute(
        """
        select id
        from skills
        order by id asc
        """,
        (),
    )
    return [str(row["id"]) for row in list(await cursor.fetchall())]


async def _upsert_workbench_skill_status(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
    status: str,
) -> dict[str, Any]:
    await set_capability_distribution_status(
        conn,
        tenant_id=tenant_id,
        capability_kind="skill",
        capability_id=skill_id,
        status=status,
        updated_by=None,
    )
    cursor = await conn.execute(
        """
        select
          skills.id as skill_id,
          skills.name,
          skills.version,
          skills.description,
          skills.input_modes,
          skills.output_modes,
          skills.executor_type,
          skills.status as lifecycle_status,
          tenant_capability_distributions.status,
          tenant_capability_distributions.visible_to_user
        from skills
        join tenant_capability_distributions
          on tenant_capability_distributions.tenant_id = %s
         and tenant_capability_distributions.capability_kind = 'skill'
         and tenant_capability_distributions.capability_id = skills.id
        where skills.id = %s
        """,
        (tenant_id, skill_id),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("skill_not_found")
    return row


async def set_workbench_skill_status(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
    status: str,
) -> dict[str, Any]:
    if not is_workbench_skill_public(skill_id):
        raise RepositoryNotFoundError("workbench_skill_not_found")
    return await _upsert_workbench_skill_status(
        conn,
        tenant_id=tenant_id,
        skill_id=skill_id,
        status=status,
    )


async def set_uploaded_workbench_skill_status(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
    status: str,
) -> dict[str, Any]:
    """Enable a governed uploaded Skill for one tenant after catalog creation."""
    return await _upsert_workbench_skill_status(
        conn,
        tenant_id=tenant_id,
        skill_id=skill_id,
        status=status,
    )


async def list_public_skill_catalog(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    include_disabled: bool = False,
    rollout_key: str | None = None,
) -> list[dict[str, Any]]:
    """Return tenant-visible public Skills/Marketplace catalog rows."""

    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    cursor = await conn.execute(
        """
        select
          skills.id as skill_id,
          skills.name,
          coalesce(skill_release_policies.current_version, skills.version) as version,
          skill_versions.content_hash as expected_version,
          coalesce(skill_versions.description, skills.description) as description,
          skills.input_modes,
          skills.status as lifecycle_status,
          coalesce(tenant_capability_distributions.status, 'disabled') as status,
          coalesce(tenant_capability_distributions.visible_to_user, false) as visible_to_user,
          coalesce(tenant_capability_distributions.department_ids, array[]::text[]) as department_ids,
          coalesce(tenant_capability_distributions.allowed_roles, '[]'::jsonb) as allowed_roles,
          coalesce(tenant_capability_distributions.metadata_json, '{}'::jsonb) as distribution_metadata_json,
          coalesce(skill_versions.status, 'active') as version_status,
          skill_release_policies.current_version as release_policy_version,
          skill_release_policies.previous_version as release_policy_previous_version,
          skill_release_policies.rollout_percent as release_policy_rollout_percent,
          previous_skill_versions.status as release_policy_previous_version_status,
          previous_skill_versions.content_hash as release_policy_previous_content_hash,
          previous_skill_versions.description as release_policy_previous_description,
          previous_skill_versions.source_json as release_policy_previous_source_json,
          previous_skill_versions.dependency_ids as release_policy_previous_dependency_ids,
          previous_skill_versions.created_by as release_policy_previous_created_by,
          previous_skill_versions.created_at as release_policy_previous_created_at,
          coalesce(skill_versions.source_json, '{}'::jsonb) as source_json,
          coalesce(skill_versions.dependency_ids, '[]'::jsonb) as dependency_ids,
          skill_versions.created_by,
          skill_versions.created_at,
          skills.created_at as updated_at
        from skills
        left join tenant_capability_distributions
          on tenant_capability_distributions.tenant_id = %s
         and tenant_capability_distributions.capability_kind = 'skill'
         and tenant_capability_distributions.capability_id = skills.id
        left join skill_release_policies
          on skill_release_policies.tenant_id = %s
         and skill_release_policies.skill_id = skills.id
         and skill_release_policies.channel = 'stable'
         and skill_release_policies.status = 'active'
        left join skill_versions
          on skill_versions.skill_id = skills.id
         and skill_versions.version = coalesce(skill_release_policies.current_version, skills.version)
        left join skill_versions as previous_skill_versions
          on previous_skill_versions.skill_id = skills.id
         and previous_skill_versions.version = skill_release_policies.previous_version
        where (skills.id = any(%s) or tenant_capability_distributions.capability_id is not null)
          and skills.id <> %s
          and skills.status = 'active'
        order by skills.name asc, skills.id asc
        """,
        (
            tenant_id,
            tenant_id,
            sorted(PUBLIC_WORKBENCH_SKILL_IDS),
            LEGACY_SYNTHETIC_CHAT_SKILL_ID,
        ),
    )
    rows = []
    for row in list(await cursor.fetchall()):
        projected = dict(row)
        if is_capability_distribution_archived({"metadata_json": projected.get("distribution_metadata_json")}):
            continue
        projected.pop("distribution_metadata_json", None)
        if rollout_key is not None:
            release_decision = _principal_skill_release_decision(
                projected,
                tenant_id=tenant_id,
                skill_id=str(projected.get("skill_id") or ""),
                rollout_key=rollout_key,
                fallback_version_field="version",
            )
            if release_decision.selected_track == "previous":
                projected["version"] = release_decision.selected_version
                projected["expected_version"] = projected.get("release_policy_previous_content_hash")
                projected["version_status"] = projected.get("release_policy_previous_version_status")
                projected["description"] = projected.get("release_policy_previous_description") or ""
                projected["source_json"] = projected.get("release_policy_previous_source_json") or {}
                projected["dependency_ids"] = projected.get("release_policy_previous_dependency_ids") or []
                projected["created_by"] = projected.get("release_policy_previous_created_by")
                projected["created_at"] = projected.get("release_policy_previous_created_at")
        for field in (
            "release_policy_version",
            "release_policy_previous_version",
            "release_policy_rollout_percent",
            "release_policy_previous_version_status",
            "release_policy_previous_content_hash",
            "release_policy_previous_description",
            "release_policy_previous_source_json",
            "release_policy_previous_dependency_ids",
            "release_policy_previous_created_by",
            "release_policy_previous_created_at",
        ):
            projected.pop(field, None)
        selected_version = str(projected.get("version") or "")
        expected_version = str(projected.get("expected_version") or "")
        if not selected_version or expected_version != selected_version:
            continue
        projected["expected_version"] = expected_version
        if not include_disabled and not is_user_runnable_status(projected.get("version_status")):
            continue
        projected["source"] = _json_dict(projected.pop("source_json", {}))
        projected["dependency_ids"] = _json_list(projected.get("dependency_ids"))
        projected["input_modes"] = _json_list(projected.get("input_modes"))
        rows.append(projected)
    return rows


async def set_public_skill_enabled(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
    status: str,
) -> dict[str, Any]:
    """Set tenant availability for a user-facing public skill."""

    if status not in {"active", "disabled"}:
        raise RepositoryConflictError("invalid_skill_status")
    if not is_workbench_skill_public(skill_id):
        distribution = await get_capability_distribution_row(
            conn,
            tenant_id=tenant_id,
            capability_kind="skill",
            capability_id=skill_id,
        )
        if distribution is None:
            raise RepositoryNotFoundError("workbench_skill_not_found")
    return await _upsert_workbench_skill_status(
        conn,
        tenant_id=tenant_id,
        skill_id=skill_id,
        status=status,
    )


async def list_user_skill_file_overlays(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    skill_ids: list[str],
    include_content: bool = False,
) -> list[dict[str, Any]]:
    """Return tenant/user file overlays for public Skill file projections."""

    if not skill_ids:
        return []
    content_projection = "content_base64" if include_content else "'' as content_base64"
    cursor = await conn.execute(
        f"""
        select skill_id, file_path, {content_projection}
          , size_bytes, status, updated_at
        from user_skill_files
        where tenant_id = %s
          and user_id = %s
          and skill_id = any(%s)
          and status in ('active', 'deleted')
        order by skill_id asc, file_path asc
        """,
        (tenant_id, user_id, skill_ids),
    )
    return [dict(row) for row in list(await cursor.fetchall())]


async def upsert_user_skill_file(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    skill_id: str,
    file_path: str,
    content_base64: str,
    size_bytes: int,
) -> dict[str, Any]:
    """Persist a tenant/user Skill file overlay and mark it active."""

    cursor = await conn.execute(
        """
        insert into user_skill_files(
          id, tenant_id, user_id, skill_id, file_path, content_base64, size_bytes, status
        )
        values (%s, %s, %s, %s, %s, %s, %s, 'active')
        on conflict (tenant_id, user_id, skill_id, file_path)
        do update set
          content_base64 = excluded.content_base64,
          size_bytes = excluded.size_bytes,
          status = 'active',
          updated_at = now()
        returning skill_id, file_path, content_base64, size_bytes, status, updated_at
        """,
        (
            new_id("usf"),
            tenant_id,
            user_id,
            skill_id,
            file_path,
            content_base64,
            size_bytes,
        ),
    )
    row = await cursor.fetchone()
    return dict(row)


async def delete_user_skill_file(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    skill_id: str,
    file_path: str,
) -> dict[str, Any]:
    """Persist a tenant/user Skill file tombstone for public projections."""

    cursor = await conn.execute(
        """
        insert into user_skill_files(
          id, tenant_id, user_id, skill_id, file_path, content_base64, size_bytes, status
        )
        values (%s, %s, %s, %s, %s, '', 0, 'deleted')
        on conflict (tenant_id, user_id, skill_id, file_path)
        do update set
          content_base64 = '',
          size_bytes = 0,
          status = 'deleted',
          updated_at = now()
        returning skill_id, file_path, content_base64, size_bytes, status, updated_at
        """,
        (new_id("usf"), tenant_id, user_id, skill_id, file_path),
    )
    row = await cursor.fetchone()
    return dict(row)


async def list_chat_mcp_tool_catalog_entries(
    conn: AsyncConnection,
    *,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Load canonical generic MCP candidates for principal-scoped Chat projection."""

    cursor = await conn.execute(
        """
        select
          mcp_tools.id as tool_id,
          mcp_tools.server_id,
          mcp_tools.name,
          mcp_tools.description,
          mcp_tools.transport_type,
          mcp_tools.endpoint,
          mcp_tools.auth_mode,
          mcp_tools.allowed_tools,
          mcp_tools.status as registry_status,
          mcp_servers.status as server_status,
          mcp_servers.catalog_status as server_catalog_status,
          mcp_tool_catalog_entries.status as catalog_status,
          mcp_tools.write_capable as registry_write_capable,
          mcp_tools.risk_level as registry_risk_level,
          mcp_tools.visible_to_user as registry_visible_to_user,
          tool_policies.status as policy_status,
          tool_policies.write_capable as policy_write_capable,
          tool_policies.risk_level as policy_risk_level,
          tool_policies.visible_to_user as policy_visible_to_user
        from mcp_tools
        join mcp_servers
          on mcp_servers.tenant_id = %s
         and mcp_servers.name = mcp_tools.server_id
         and mcp_servers.status = 'active'
        left join mcp_tool_catalog_entries
          on mcp_tool_catalog_entries.tool_id = mcp_tools.id
        join tool_policies
          on tool_policies.tenant_id = mcp_servers.tenant_id
         and tool_policies.tool_id = mcp_tools.id
         and tool_policies.status = 'active'
         and tool_policies.visible_to_user = true
        where mcp_tools.status = 'active'
          and mcp_tools.visible_to_user = true
          and """ + _mcp_repository.mcp_tool_tenant_authority_sql() + """
        order by mcp_tools.id asc
        """,
        (tenant_id, tenant_id),
    )
    entries: list[dict[str, Any]] = []
    for row in await cursor.fetchall():
        record = dict(row)
        entry = _tool_policy_projection(record, tenant_id=tenant_id)
        entry.update(
            server_status=str(record.get("server_status") or "disabled"),
            server_catalog_status=str(record.get("server_catalog_status") or "legacy"),
            catalog_status=str(record.get("catalog_status") or "legacy"),
            transport_type=str(record.get("transport_type") or ""),
            endpoint=str(record.get("endpoint") or ""),
            auth_mode=str(record.get("auth_mode") or ""),
            allowed_tools=(
                list(record.get("allowed_tools"))
                if isinstance(record.get("allowed_tools"), list)
                else []
            ),
        )
        entries.append(entry)
    return entries


def _chat_mcp_access_context(
    *,
    tenant_id: str,
    principal_department_id: str,
    principal_roles: list[str] | None,
    is_admin: bool,
    permissions: list[str] | None,
) -> CapabilityAccessContext:
    return CapabilityAccessContext(
        tenant_id=tenant_id,
        department_id=str(principal_department_id or ""),
        roles=normalize_roles(principal_roles or []),
        is_admin=bool(is_admin),
        permissions=[str(item) for item in permissions or [] if str(item)],
    )


async def _authorize_chat_mcp_tool_entry(
    conn: AsyncConnection,
    *,
    context: CapabilityAccessContext,
    tenant_id: str,
    tool: dict[str, Any],
) -> dict[str, Any]:
    tool_id = str(tool.get("tool_id") or "").strip()
    server_id = str(tool.get("server_id") or "").strip()
    if not tool_id or not server_id or not mcp_runtime_metadata_usable(tool):
        raise _capability_not_authorized(
            context=context,
            capability_kind="mcp_tool",
            capability_id=tool_id or "mcp_tool",
        )
    try:
        distribution = await get_capability_distribution_row(
            conn,
            tenant_id=tenant_id,
            capability_kind="mcp_server",
            capability_id=server_id,
        )
    except RepositoryConflictError as exc:
        raise _capability_not_authorized(
            context=context,
            capability_kind="mcp_tool",
            capability_id=tool_id,
        ) from exc
    lifecycle_status = (
        "active"
        if str(tool.get("effective_status") or "disabled") == "active"
        and str(tool.get("server_status") or "disabled") == "active"
        and bool(tool.get("visible_to_user"))
        else "disabled"
    )
    decision = resolve_capability_access(
        context,
        CapabilityDistributionSubject(
            capability_kind="mcp_tool",
            capability_id=tool_id,
            lifecycle_status=lifecycle_status,
            distribution=distribution,
            inherited_distribution_source=f"mcp_server:{server_id}",
        ),
        intent="use",
    )
    allowed_tool_name = str((tool.get("allowed_tools") or [""])[0])
    policy = evaluate_tool_policy(
        tool={
            "mcp_server": server_id,
            "mcp_tool": allowed_tool_name,
            "registered": True,
            "declared": True,
            "active": lifecycle_status == "active",
            "distributed": decision.usable,
            "identity_authorized": True,
            "object_authorized": True,
            "parameters_authorized": True,
            "risk_level": str(tool.get("risk_level") or "low"),
            "write_capable": bool(tool.get("write_capable")),
        }
    )
    if not decision.usable or not policy.allowed:
        raise _capability_not_authorized(
            context=context,
            capability_kind="mcp_tool",
            capability_id=tool_id,
            decision=decision,
        )
    return tool


def _json_dict_projection(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_string_list_projection(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    return []


def _mcp_server_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "tenant_id": str(row.get("tenant_id") or ""),
        "name": str(row.get("name") or ""),
        "transport": str(row.get("transport") or "streamable_http"),
        "endpoint_redacted": str(row.get("endpoint_redacted") or ""),
        "status": str(row.get("status") or "disabled"),
        "is_system": bool(row.get("is_system")),
        "allowed_roles": _json_string_list_projection(row.get("allowed_roles")),
        "role_quotas": _json_dict_projection(row.get("role_quotas_json") or row.get("role_quotas")),
        "department_ids": _json_string_list_projection(row.get("department_ids")),
        "credential_state": str(row.get("credential_state") or "not_configured"),
        "credential_metadata": _json_dict_projection(row.get("credential_metadata_json") or row.get("credential_metadata")),
        "catalog_generation": int(row.get("catalog_generation") or 0),
        "catalog_revision": int(row.get("catalog_revision") or 0),
        "catalog_status": str(row.get("catalog_status") or "legacy"),
        "catalog_unavailable_reason": str(row.get("catalog_unavailable_reason") or ""),
        "catalog_discovered_count": int(row.get("catalog_discovered_count") or 0),
        "catalog_selectable_count": int(row.get("catalog_selectable_count") or 0),
        "catalog_last_synced_at": row.get("catalog_last_synced_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _capability_distribution_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            raise RepositoryConflictError("capability_distribution_scope_invalid")
    if isinstance(value, (list, tuple)):
        if any(not isinstance(item, str) or not item.strip() for item in value):
            raise RepositoryConflictError("capability_distribution_scope_invalid")
        return list(value)
    raise RepositoryConflictError("capability_distribution_scope_invalid")


def _capability_distribution_json(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _capability_distribution_projection(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "tenant_id": str(row.get("tenant_id") or ""),
        "capability_kind": str(row.get("capability_kind") or ""),
        "capability_id": str(row.get("capability_id") or ""),
        "status": str(row.get("status") or "disabled"),
        "visible_to_user": bool(row.get("visible_to_user")),
        "scope_mode": str(row.get("scope_mode") or "allowlist"),
        "department_ids": _capability_distribution_string_list(row.get("department_ids")),
        "allowed_roles": _capability_distribution_string_list(row.get("allowed_roles")),
        "metadata_json": _capability_distribution_json(row.get("metadata_json")),
        "updated_by": row.get("updated_by"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def is_capability_distribution_archived(row: dict[str, Any] | None) -> bool:
    """Return whether a tenant capability binding has been archived."""

    return shared_capability_distribution_archived(row)


async def _acquire_capability_distribution_lifecycle_lock(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
) -> None:
    """Serialize one distribution lifecycle key, including writes for currently missing rows."""

    lock_scope = json.dumps(
        {
            "capability_id": capability_id,
            "capability_kind": capability_kind,
            "tenant_id": tenant_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s::text, 0::bigint))",
        (lock_scope,),
    )


async def acquire_capability_distribution_lifecycle_locks(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_ids: list[str],
) -> None:
    """Finish tenant backfill before pre-acquiring ordered lifecycle keys for one batch."""

    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    for capability_id in sorted(set(capability_ids)):
        await _acquire_capability_distribution_lifecycle_lock(
            conn,
            tenant_id=tenant_id,
            capability_kind=capability_kind,
            capability_id=capability_id,
        )


async def _lock_capability_distribution_metadata(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
) -> dict[str, Any] | None:
    """Lock one binding and return its parsed metadata for archive/write lifecycle decisions."""

    cursor = await conn.execute(
        """
        select metadata_json
        from tenant_capability_distributions
        where tenant_id = %s and capability_kind = %s and capability_id = %s
        for update
        """,
        (tenant_id, capability_kind, capability_id),
    )
    row = await cursor.fetchone()
    return _capability_distribution_json(row.get("metadata_json")) if row is not None else None


async def _require_unarchived_capability_distribution(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
    allow_missing: bool,
) -> None:
    """Reject valid archive markers before a distribution mutation while holding the row lock."""

    metadata_json = await _lock_capability_distribution_metadata(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
    )
    if metadata_json is None:
        if not allow_missing:
            raise RepositoryNotFoundError("capability_distribution_not_found")
        return
    if shared_capability_distribution_archived({"metadata_json": metadata_json}):
        raise RepositoryConflictError("capability_distribution_archived")


async def _raise_distribution_update_failure(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
) -> None:
    """Distinguish an archived binding from a missing row after a guarded write."""

    cursor = await conn.execute(
        """
        select metadata_json
        from tenant_capability_distributions
        where tenant_id = %s and capability_kind = %s and capability_id = %s
        """,
        (tenant_id, capability_kind, capability_id),
    )
    row = await cursor.fetchone()
    if row is not None and is_capability_distribution_archived(dict(row)):
        raise RepositoryConflictError("capability_distribution_archived")
    raise RepositoryNotFoundError("capability_distribution_not_found")


async def ensure_tenant_capability_distribution_backfill(
    conn: AsyncConnection,
    *,
    tenant_id: str,
) -> None:
    """Backfill one tenant exactly once; insert-only conflicts cannot overwrite lifecycle state."""

    completion_cursor = await conn.execute(
        """
        select completed_at
        from tenant_capability_distribution_backfills
        where tenant_id = %s
        """,
        (tenant_id,),
    )
    completion = await completion_cursor.fetchone()
    if completion is not None and completion.get("completed_at") is not None:
        return

    await conn.execute(
        """
        insert into tenant_capability_distribution_backfills(tenant_id)
        values (%s)
        on conflict (tenant_id) do nothing
        """,
        (tenant_id,),
    )
    completion_cursor = await conn.execute(
        """
        select completed_at
        from tenant_capability_distribution_backfills
        where tenant_id = %s
        for update
        """,
        (tenant_id,),
    )
    completion = await completion_cursor.fetchone()
    if completion is not None and completion.get("completed_at") is not None:
        return

    await conn.execute(
        """
        insert into tenant_capability_distributions(
          id, tenant_id, capability_kind, capability_id, status, visible_to_user,
          scope_mode, department_ids, allowed_roles, metadata_json
        )
        select
          source_rows.id, source_rows.tenant_id, source_rows.capability_kind,
          source_rows.capability_id, source_rows.status, source_rows.visible_to_user,
          source_rows.scope_mode, source_rows.department_ids, source_rows.allowed_roles,
          source_rows.metadata_json
        from (
          select
            'capdist_' || substr(md5(tenant_workbench_skills.tenant_id || ':skill:' || tenant_workbench_skills.skill_id), 1, 24),
            tenant_workbench_skills.tenant_id,
            'skill',
            tenant_workbench_skills.skill_id,
            tenant_workbench_skills.status,
            tenant_workbench_skills.visible_to_user,
            'allowlist',
            array[]::text[],
            '[]'::jsonb,
            '{"legacy_source":"tenant_workbench_skills"}'::jsonb
          from tenant_workbench_skills
          join skills on skills.id = tenant_workbench_skills.skill_id
          where tenant_workbench_skills.tenant_id = %s
            and skills.status = 'active'
          union all
          select
            'capdist_' || substr(md5(%s || ':skill:' || skills.id), 1, 24),
            %s,
            'skill',
            skills.id,
            'active',
            true,
            'allowlist',
            array[]::text[],
            '[]'::jsonb,
            '{"legacy_source":"builtin_public_skill"}'::jsonb
          from skills
          left join tenant_workbench_skills
            on tenant_workbench_skills.tenant_id = %s
           and tenant_workbench_skills.skill_id = skills.id
          where skills.id = any(%s)
            and skills.status = 'active'
            and tenant_workbench_skills.skill_id is null
        ) as source_rows(
          id, tenant_id, capability_kind, capability_id, status, visible_to_user,
          scope_mode, department_ids, allowed_roles, metadata_json
        )
        on conflict (tenant_id, capability_kind, capability_id) do nothing
        """,
        (tenant_id, tenant_id, tenant_id, tenant_id, sorted(PUBLIC_WORKBENCH_SKILL_IDS)),
    )
    await conn.execute(
        """
        insert into tenant_capability_distributions(
          id, tenant_id, capability_kind, capability_id, status, visible_to_user,
          scope_mode, department_ids, allowed_roles, metadata_json, updated_by
        )
        select
          'capdist_' || substr(md5(mcp_servers.tenant_id || ':mcp_server:' || mcp_servers.name), 1, 24),
          mcp_servers.tenant_id,
          'mcp_server',
          mcp_servers.name,
          case
            when not role_validation.scope_valid or not department_validation.scope_valid then 'disabled'
            when mcp_servers.status = 'active' then 'active'
            else 'disabled'
          end,
          true,
          'allowlist',
          case
            when department_validation.scope_valid then mcp_servers.department_ids
            else array[]::text[]
          end,
          role_scope.normalized_allowed_roles,
          jsonb_build_object(
            'legacy_source', 'mcp_servers',
            'legacy_scope_invalid',
            not role_validation.scope_valid or not department_validation.scope_valid
          ),
          mcp_servers.updated_by
        from mcp_servers
        cross join lateral (
          select case
            when jsonb_typeof(mcp_servers.allowed_roles) is distinct from 'array' then false
            when exists (
              select 1
              from jsonb_array_elements(mcp_servers.allowed_roles) as role_items(role_value)
              where jsonb_typeof(role_value) is distinct from 'string'
                 or btrim(role_value #>> '{}') = ''
            ) then false
            else true
          end as scope_valid
        ) as role_validation
        cross join lateral (
          select coalesce(
            bool_and(department_id is not null and btrim(department_id) <> ''),
            true
          ) as scope_valid
          from unnest(mcp_servers.department_ids) as department_items(department_id)
        ) as department_validation
        cross join lateral (
          select case
            when role_validation.scope_valid then coalesce(
              (
                select jsonb_agg(normalized_role order by normalized_role)
                from (
                  select distinct lower(btrim(role_value #>> '{}')) as normalized_role
                  from jsonb_array_elements(mcp_servers.allowed_roles) as role_items(role_value)
                ) as normalized_roles
              ),
              '[]'::jsonb
            )
            else '[]'::jsonb
          end as normalized_allowed_roles
        ) as role_scope
        where mcp_servers.tenant_id = %s
          and mcp_servers.status <> 'deleted'
        on conflict (tenant_id, capability_kind, capability_id) do nothing
        """,
        (tenant_id,),
    )
    await conn.execute(
        """
        update tenant_capability_distribution_backfills
        set completed_at = now()
        where tenant_id = %s
        """,
        (tenant_id,),
    )


async def list_capability_distribution_rows(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str | None = None,
    include_disabled: bool = True,
) -> list[dict[str, Any]]:
    """List the authoritative distribution rows for one tenant."""

    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    filters = ["tenant_id = %s", "(%s or status = 'active')"]
    params: list[Any] = [tenant_id, include_disabled]
    if capability_kind is not None:
        filters.insert(1, "capability_kind = %s")
        params.insert(1, capability_kind)
    cursor = await conn.execute(
        f"""
        select id, tenant_id, capability_kind, capability_id, status, visible_to_user,
               scope_mode, department_ids, allowed_roles, metadata_json, updated_by,
               created_at, updated_at
        from tenant_capability_distributions
        where {' and '.join(filters)}
        order by capability_kind asc, capability_id asc
        """,
        tuple(params),
    )
    return [_capability_distribution_projection(dict(row)) for row in await cursor.fetchall()]


async def get_capability_distribution_row(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
) -> dict[str, Any] | None:
    """Fetch one authoritative distribution row after insert-only backfill."""

    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    cursor = await conn.execute(
        """
        select id, tenant_id, capability_kind, capability_id, status, visible_to_user,
               scope_mode, department_ids, allowed_roles, metadata_json, updated_by,
               created_at, updated_at
        from tenant_capability_distributions
        where tenant_id = %s and capability_kind = %s and capability_id = %s
        """,
        (tenant_id, capability_kind, capability_id),
    )
    row = await cursor.fetchone()
    return _capability_distribution_projection(dict(row)) if row is not None else None


async def upsert_capability_distribution_row(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
    status: str,
    visible_to_user: bool,
    scope_mode: str,
    department_ids: list[str],
    allowed_roles: list[str],
    metadata_json: dict[str, Any],
    updated_by: str | None,
) -> dict[str, Any]:
    """Create or update one authoritative capability distribution row."""

    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    await _acquire_capability_distribution_lifecycle_lock(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
    )
    await _require_unarchived_capability_distribution(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
        allow_missing=True,
    )
    cursor = await conn.execute(
        """
        insert into tenant_capability_distributions(
          id, tenant_id, capability_kind, capability_id, status, visible_to_user,
          scope_mode, department_ids, allowed_roles, metadata_json, updated_by, updated_at
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, now())
        on conflict (tenant_id, capability_kind, capability_id) do update
        set status = excluded.status,
            visible_to_user = excluded.visible_to_user,
            scope_mode = excluded.scope_mode,
            department_ids = excluded.department_ids,
            allowed_roles = excluded.allowed_roles,
            metadata_json = excluded.metadata_json,
            updated_by = excluded.updated_by,
            updated_at = now()
        returning id, tenant_id, capability_kind, capability_id, status, visible_to_user,
                  scope_mode, department_ids, allowed_roles, metadata_json, updated_by,
                  created_at, updated_at
        """,
        (
            new_id("capdist"),
            tenant_id,
            capability_kind,
            capability_id,
            status,
            visible_to_user,
            scope_mode,
            department_ids,
            json.dumps(allowed_roles, ensure_ascii=False),
            dumps_json(metadata_json),
            updated_by,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        await _raise_distribution_update_failure(
            conn,
            tenant_id=tenant_id,
            capability_kind=capability_kind,
            capability_id=capability_id,
        )
    projected = _capability_distribution_projection(dict(row))
    if capability_kind == "mcp_server":
        distribution_enabled = projected["status"] == "active"
        catalog_cursor = await conn.execute(
            """
            update mcp_servers
            set catalog_generation = catalog_generation + 1,
                catalog_status = case
                  when %s::boolean and status = 'active' then 'refresh_required'
                  else 'disabled'
                end,
                catalog_unavailable_reason = case
                  when %s::boolean and status = 'active' then 'refresh_required'
                  else 'disabled'
                end,
                catalog_discovered_count = 0,
                catalog_selectable_count = 0,
                catalog_sync_lease_expires_at = null,
                updated_at = now()
            where tenant_id = %s
              and name = %s
              and status <> 'deleted'
            returning name
            """,
            (distribution_enabled, distribution_enabled, tenant_id, capability_id),
        )
        if await catalog_cursor.fetchone() is None:
            raise RepositoryNotFoundError("mcp_server_not_found")
    return projected


async def archive_capability_distribution_row(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
    archived_by: str | None,
) -> dict[str, Any]:
    """Archive one tenant capability binding without mutating global Skill evidence."""

    if not is_valid_archive_actor(archived_by):
        raise RepositoryConflictError("capability_distribution_archive_actor_invalid")
    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    await _acquire_capability_distribution_lifecycle_lock(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
    )
    metadata_json = await _lock_capability_distribution_metadata(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
    )
    if metadata_json is None:
        raise RepositoryNotFoundError("capability_distribution_not_found")
    preserve_existing_evidence = has_valid_capability_distribution_archive_evidence(
        {"metadata_json": metadata_json}
    )
    cursor = await conn.execute(
        """
        update tenant_capability_distributions
        set status = 'disabled',
            visible_to_user = false,
            metadata_json = case
              when jsonb_typeof(metadata_json) = 'object' then metadata_json
              else '{}'::jsonb
            end || jsonb_build_object(
              'archived_at', case
                when %s::boolean then metadata_json -> 'archived_at'
                else to_jsonb(to_char(now() at time zone 'utc', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'))
              end,
              'archived_by', case
                when %s::boolean then metadata_json -> 'archived_by'
                else to_jsonb(left(coalesce(%s, ''), 255))
              end
            ),
            updated_by = %s,
            updated_at = now()
        where tenant_id = %s and capability_kind = %s and capability_id = %s
        returning id, tenant_id, capability_kind, capability_id, status, visible_to_user,
                  scope_mode, department_ids, allowed_roles, metadata_json, updated_by,
                  created_at, updated_at
        """,
        (
            preserve_existing_evidence,
            preserve_existing_evidence,
            archived_by,
            archived_by,
            tenant_id,
            capability_kind,
            capability_id,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("capability_distribution_not_found")
    return _capability_distribution_projection(dict(row))


async def toggle_capability_distribution_row(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
    enabled: bool | None,
    updated_by: str | None,
) -> dict[str, Any]:
    """Toggle or set the status of one authoritative distribution row."""

    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    await _acquire_capability_distribution_lifecycle_lock(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
    )
    await _require_unarchived_capability_distribution(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
        allow_missing=False,
    )
    cursor = await conn.execute(
        """
        update tenant_capability_distributions
        set status = case
              when %s::boolean is null then case when status = 'active' then 'disabled' else 'active' end
              when %s::boolean then 'active'
              else 'disabled'
            end,
            updated_by = %s,
            updated_at = now()
        where tenant_id = %s and capability_kind = %s and capability_id = %s
        returning id, tenant_id, capability_kind, capability_id, status, visible_to_user,
                  scope_mode, department_ids, allowed_roles, metadata_json, updated_by,
                  created_at, updated_at
        """,
        (enabled, enabled, updated_by, tenant_id, capability_kind, capability_id),
    )
    row = await cursor.fetchone()
    if row is None:
        await _raise_distribution_update_failure(
            conn,
            tenant_id=tenant_id,
            capability_kind=capability_kind,
            capability_id=capability_id,
        )
    projected = _capability_distribution_projection(dict(row))
    if capability_kind == "mcp_server":
        distribution_enabled = projected["status"] == "active"
        catalog_cursor = await conn.execute(
            """
            update mcp_servers
            set catalog_generation = catalog_generation + 1,
                catalog_status = case
                  when %s::boolean and status = 'active' then 'refresh_required'
                  else 'disabled'
                end,
                catalog_unavailable_reason = case
                  when %s::boolean and status = 'active' then 'refresh_required'
                  else 'disabled'
                end,
                catalog_discovered_count = 0,
                catalog_selectable_count = 0,
                catalog_sync_lease_expires_at = null,
                updated_at = now()
            where tenant_id = %s
              and name = %s
              and status <> 'deleted'
            returning name
            """,
            (distribution_enabled, distribution_enabled, tenant_id, capability_id),
        )
        if await catalog_cursor.fetchone() is None:
            raise RepositoryNotFoundError("mcp_server_not_found")
    return projected


async def set_capability_distribution_status(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    capability_kind: str,
    capability_id: str,
    status: str,
    updated_by: str | None,
) -> dict[str, Any]:
    """Set authoritative status while preserving existing distribution scope."""

    if status not in {"active", "disabled"}:
        raise RepositoryConflictError("invalid_capability_distribution_status")
    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    await _acquire_capability_distribution_lifecycle_lock(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
    )
    await _require_unarchived_capability_distribution(
        conn,
        tenant_id=tenant_id,
        capability_kind=capability_kind,
        capability_id=capability_id,
        allow_missing=True,
    )
    cursor = await conn.execute(
        """
        insert into tenant_capability_distributions(
          id, tenant_id, capability_kind, capability_id, status, visible_to_user,
          scope_mode, department_ids, allowed_roles, metadata_json, updated_by, updated_at
        )
        values (%s, %s, %s, %s, %s, true, 'allowlist', array[]::text[], '[]'::jsonb, '{}'::jsonb, %s, now())
        on conflict (tenant_id, capability_kind, capability_id) do update
        set status = excluded.status,
            updated_by = excluded.updated_by,
            updated_at = now()
        returning id, tenant_id, capability_kind, capability_id, status, visible_to_user,
                  scope_mode, department_ids, allowed_roles, metadata_json, updated_by,
                  created_at, updated_at
        """,
        (new_id("capdist"), tenant_id, capability_kind, capability_id, status, updated_by),
    )
    row = await cursor.fetchone()
    if row is None:
        await _raise_distribution_update_failure(
            conn,
            tenant_id=tenant_id,
            capability_kind=capability_kind,
            capability_id=capability_id,
        )
    return _capability_distribution_projection(dict(row))


def _capability_not_authorized(
    *,
    context: CapabilityAccessContext | None = None,
    capability_kind: str = "",
    capability_id: str = "",
    decision: CapabilityAccessDecision | None = None,
) -> RepositoryAuthorizationError:
    denial = None
    if context is not None and capability_kind and capability_id:
        denial_decision = decision or CapabilityAccessDecision(
            visible=False,
            usable=False,
            manageable=False,
            admin_bypass=False,
            decision_reason="capability_not_authorized",
        )
        denial = CapabilityAuthorizationDenial.from_decision(
            decision=denial_decision,
            actor_department_id=context.department_id,
            actor_roles=context.roles,
            capability_kind=capability_kind,
            capability_id=capability_id,
        )
    return RepositoryAuthorizationError("capability_not_authorized", denial=denial)


_MCP_TOOL_ID_KEYS = ("mcp_tool_ids", "mcpToolIds")
_CALLER_AUTH_SNAPSHOT_KEY_ALIASES = {
    "principalroles",
    "principaldepartmentid",
    "authsource",
}


def _append_explicit_mcp_tool_ids(target: list[str], container: dict[str, Any]) -> None:
    for key in _MCP_TOOL_ID_KEYS:
        if key not in container:
            continue
        raw_tool_ids = container[key]
        if not isinstance(raw_tool_ids, list):
            raise _capability_not_authorized()
        for raw_tool_id in raw_tool_ids:
            if not isinstance(raw_tool_id, str) or not raw_tool_id.strip():
                raise _capability_not_authorized()
            tool_id = raw_tool_id.strip()
            if tool_id not in target:
                target.append(tool_id)


def _explicit_mcp_tool_scope(container: dict[str, Any]) -> tuple[bool, list[str]]:
    tool_ids: list[str] = []
    _append_explicit_mcp_tool_ids(tool_ids, container)
    return any(key in container for key in _MCP_TOOL_ID_KEYS), tool_ids


def extract_run_mcp_tool_ids(normalized_input: dict[str, Any]) -> list[str]:
    """Extract explicit MCP IDs from accepted run and multi-agent step fields."""

    requested_tool_ids: list[str] = []
    _append_explicit_mcp_tool_ids(requested_tool_ids, normalized_input)
    raw_steps = normalized_input.get("multi_agent_steps")
    if isinstance(raw_steps, list):
        for raw_step in raw_steps:
            if isinstance(raw_step, dict):
                _append_explicit_mcp_tool_ids(requested_tool_ids, raw_step)
    return requested_tool_ids


def run_mcp_tool_ids_for_skill(skill: dict[str, Any], normalized_input: dict[str, Any]) -> list[str]:
    """Return one canonical MCP authorization set for a Harness-backed Skill."""

    requested_tool_ids: list[str] = []
    skill_id = str(skill.get("skill_id") or "").strip()
    backing_tool_id = str(skill.get("backing_mcp_tool_id") or "").strip()
    if skill_id == _mcp_repository.TRUSTED_BUILTIN_MCP_TOOL_ID and not backing_tool_id:
        raise _capability_not_authorized()
    if backing_tool_id:
        requested_tool_ids.append(backing_tool_id)
    for tool_id in extract_run_mcp_tool_ids(normalized_input):
        if tool_id not in requested_tool_ids:
            requested_tool_ids.append(tool_id)
    return requested_tool_ids


def strip_caller_run_auth_snapshot_fields(value: Any) -> Any:
    """Remove caller-controlled run authorization snapshot fields recursively."""

    if isinstance(value, list):
        return [strip_caller_run_auth_snapshot_fields(item) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned: dict[str, Any] = {}
    for key, item in value.items():
        normalized_key = "".join(character for character in str(key) if character.isalnum()).lower()
        if normalized_key in _CALLER_AUTH_SNAPSHOT_KEY_ALIASES:
            continue
        cleaned[key] = strip_caller_run_auth_snapshot_fields(item)
    return cleaned


def normalize_run_input_for_enqueue(input_payload: object, *, redact_public: bool) -> dict[str, Any]:
    """Sanitize run input while preserving validated explicit MCP selectors."""

    if not isinstance(input_payload, dict):
        return {}
    top_level_tools_present, top_level_tool_ids = _explicit_mcp_tool_scope(input_payload)
    if redact_public:
        cleaned = sanitize_user_control_input(input_payload)
    else:
        stripped = strip_server_owned_control_metadata(input_payload)
        cleaned = stripped if isinstance(stripped, dict) else {}
    normalized = strip_caller_run_auth_snapshot_fields(cleaned)
    if not isinstance(normalized, dict):
        normalized = {}
    if redact_public:
        for key in _MCP_TOOL_ID_KEYS:
            normalized.pop(key, None)
    if top_level_tools_present:
        normalized["mcp_tool_ids"] = top_level_tool_ids

    original_steps = input_payload.get("multi_agent_steps")
    normalized_steps = normalized.get("multi_agent_steps")
    if isinstance(original_steps, list) and isinstance(normalized_steps, list):
        for original_step, normalized_step in zip(original_steps, normalized_steps):
            if not isinstance(original_step, dict) or not isinstance(normalized_step, dict):
                continue
            step_tools_present, step_tool_ids = _explicit_mcp_tool_scope(original_step)
            if redact_public:
                for key in _MCP_TOOL_ID_KEYS:
                    normalized_step.pop(key, None)
            if step_tools_present:
                normalized_step["mcp_tool_ids"] = step_tool_ids
    return normalized


async def _authorize_run_capabilities(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    skill_id: str,
    normalized_input: dict[str, Any],
    principal_department_id: str,
    principal_roles: list[str] | None,
    is_admin: bool,
    permissions: list[str] | None,
    skill_resolver,
) -> dict[str, Any]:
    """Apply shared distribution and MCP admission to one Skill resolver."""

    context = CapabilityAccessContext(
        tenant_id=tenant_id,
        department_id=str(principal_department_id or ""),
        roles=normalize_roles(principal_roles or []),
        is_admin=bool(is_admin),
        permissions=[str(item) for item in permissions or [] if str(item)],
    )
    try:
        skill = await skill_resolver(
            conn,
            tenant_id=tenant_id,
            agent_id=agent_id,
            skill_id=skill_id,
        )
    except RepositoryNotFoundError as exc:
        raise _capability_not_authorized(
            context=context,
            capability_kind="skill",
            capability_id=skill_id,
        ) from exc
    except RepositoryConflictError as exc:
        raise _capability_not_authorized(
            context=context,
            capability_kind="skill",
            capability_id=skill_id,
        ) from exc

    try:
        skill_distribution = await get_capability_distribution_row(
            conn,
            tenant_id=tenant_id,
            capability_kind="skill",
            capability_id=skill_id,
        )
    except RepositoryConflictError as exc:
        raise _capability_not_authorized(
            context=context,
            capability_kind="skill",
            capability_id=skill_id,
        ) from exc
    if is_capability_distribution_archived(skill_distribution):
        raise _capability_not_authorized(
            context=context,
            capability_kind="skill",
            capability_id=skill_id,
        )
    skill_decision = resolve_capability_access(
        context,
        CapabilityDistributionSubject(
            capability_kind="skill",
            capability_id=skill_id,
            lifecycle_status=str(skill.get("skill_status") or "disabled"),
            distribution=skill_distribution,
        ),
        intent="use",
    )
    if not skill_decision.usable:
        raise _capability_not_authorized(
            context=context,
            capability_kind="skill",
            capability_id=skill_id,
            decision=skill_decision,
        )

    try:
        tool_ids = run_mcp_tool_ids_for_skill(skill, normalized_input)
    except RepositoryAuthorizationError as exc:
        raise _capability_not_authorized(
            context=context,
            capability_kind="skill",
            capability_id=skill_id,
        ) from exc
    for tool_id in tool_ids:
        tool = await get_mcp_tool_registry_entry(
            conn,
            tenant_id=tenant_id,
            tool_id=tool_id,
        )
        if tool is None:
            raise _capability_not_authorized(
                context=context,
                capability_kind="mcp_tool",
                capability_id=tool_id,
            )
        server_id = str(tool.get("server_id") or "").strip()
        if not server_id:
            raise _capability_not_authorized(
                context=context,
                capability_kind="mcp_tool",
                capability_id=tool_id,
            )
        try:
            server_distribution = await get_capability_distribution_row(
                conn,
                tenant_id=tenant_id,
                capability_kind="mcp_server",
                capability_id=server_id,
            )
        except RepositoryConflictError as exc:
            raise _capability_not_authorized(
                context=context,
                capability_kind="mcp_tool",
                capability_id=tool_id,
            ) from exc
        tool_lifecycle_status = (
            "active"
            if str(tool.get("effective_status") or "disabled") == "active"
            and str(tool.get("server_status") or "disabled") == "active"
            and bool(tool.get("visible_to_user", True))
            else "disabled"
        )
        tool_decision = resolve_capability_access(
            context,
            CapabilityDistributionSubject(
                capability_kind="mcp_tool",
                capability_id=tool_id,
                lifecycle_status=tool_lifecycle_status,
                distribution=server_distribution,
                inherited_distribution_source=f"mcp_server:{server_id}",
            ),
            intent="use",
        )
        if not tool_decision.usable:
            raise _capability_not_authorized(
                context=context,
                capability_kind="mcp_tool",
                capability_id=tool_id,
                decision=tool_decision,
            )
    return skill


async def authorize_run_capabilities(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    skill_id: str,
    normalized_input: dict[str, Any],
    principal_department_id: str,
    principal_roles: list[str] | None,
    is_admin: bool,
    permissions: list[str] | None,
) -> dict[str, Any]:
    """Authorize a fixed Agent/default Skill and its explicit MCP tools."""

    return await _authorize_run_capabilities(
        conn,
        tenant_id=tenant_id,
        agent_id=agent_id,
        skill_id=skill_id,
        normalized_input=normalized_input,
        principal_department_id=principal_department_id,
        principal_roles=principal_roles,
        is_admin=is_admin,
        permissions=permissions,
        skill_resolver=resolve_agent_skill,
    )


async def authorize_selected_run_capabilities(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    skill_id: str,
    expected_version: str,
    rollout_key: str,
    normalized_input: dict[str, Any],
    principal_department_id: str,
    principal_roles: list[str] | None,
    is_admin: bool,
    permissions: list[str] | None,
) -> dict[str, Any]:
    """Authorize an ordinary selected Skill and validate its optimistic hash lock."""

    skill = await _authorize_run_capabilities(
        conn,
        tenant_id=tenant_id,
        agent_id=agent_id,
        skill_id=skill_id,
        normalized_input=normalized_input,
        principal_department_id=principal_department_id,
        principal_roles=principal_roles,
        is_admin=is_admin,
        permissions=permissions,
        skill_resolver=resolve_selected_skill,
    )
    release_decision = resolve_rollout_skill_decision(
        skill,
        tenant_id=tenant_id,
        skill_id=skill_id,
        rollout_key=rollout_key,
    )
    selected_version = str(release_decision.selected_version or "")
    if release_decision.policy_active:
        exact_version = await get_effective_skill_version_for_policy(
            conn,
            skill_id=skill_id,
            version=selected_version,
        )
        if exact_version is None or not is_user_runnable_status(exact_version.get("status")):
            raise _capability_not_authorized()
        content_hash = str(exact_version.get("content_hash") or "")
        materialized_version = str(exact_version.get("version") or "")
    else:
        materialized_version = str(skill.get("skill_version") or "")
        content_hash = str(skill.get("skill_content_hash") or materialized_version)
    if not materialized_version or materialized_version != selected_version or content_hash != materialized_version:
        raise _capability_not_authorized()
    if expected_version != selected_version:
        raise RepositoryConflictError("skill_selection_stale")
    return {**skill, "skill_version": selected_version, "skill_content_hash": content_hash}


async def authorize_replay_run_capabilities(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    agent_id: str,
    skill_id: str,
    pinned_version: str,
    pinned_executor_type: str,
    skill_manifests: list[dict[str, Any]],
    normalized_input: dict[str, Any],
    principal_department_id: str,
    principal_roles: list[str] | None,
    is_admin: bool,
    permissions: list[str] | None,
) -> dict[str, Any]:
    """Reauthorize current access while preserving an exact historical Skill pin."""

    pinned_mcp_tool_ids = pinned_replay_mcp_tool_ids(
        skill_id=skill_id,
        pinned_version=pinned_version,
        pinned_executor_type=pinned_executor_type,
        skill_manifests=skill_manifests,
    )
    replay_input = dict(normalized_input)
    if pinned_mcp_tool_ids:
        replay_input["mcp_tool_ids"] = pinned_mcp_tool_ids
    skill = await _authorize_run_capabilities(
        conn,
        tenant_id=tenant_id,
        agent_id=agent_id,
        skill_id=skill_id,
        normalized_input=replay_input,
        principal_department_id=principal_department_id,
        principal_roles=principal_roles,
        is_admin=is_admin,
        permissions=permissions,
        skill_resolver=resolve_selected_skill,
    )
    await validate_replay_skill_manifests(
        conn,
        skill_id=skill_id,
        pinned_version=pinned_version,
        pinned_executor_type=pinned_executor_type,
        skill_manifests=skill_manifests,
    )
    return skill


def pinned_replay_mcp_tool_ids(
    *,
    skill_id: str,
    pinned_version: str,
    pinned_executor_type: str,
    skill_manifests: list[dict[str, Any]],
) -> list[str]:
    """Extract the historical MCP authorization set without consulting mutable release state."""

    if pinned_executor_type not in DEFAULT_RUN_EXECUTOR_TYPES or not pinned_version:
        raise _capability_not_authorized()
    primary = next(
        (
            manifest
            for manifest in skill_manifests
            if str(manifest.get("skill_id") or "") == skill_id
            and str(manifest.get("version") or manifest.get("skill_version") or "") == pinned_version
        ),
        None,
    )
    if primary is None:
        raise _capability_not_authorized()
    raw_mcp_tool_ids = primary.get("mcp_tool_ids")
    if not isinstance(raw_mcp_tool_ids, list) or any(
        not isinstance(item, str) or not item for item in raw_mcp_tool_ids
    ):
        raise _capability_not_authorized()
    pinned_mcp_tool_ids = list(dict.fromkeys(raw_mcp_tool_ids))
    if (
        skill_id == _mcp_repository.TRUSTED_BUILTIN_MCP_TOOL_ID
        and _mcp_repository.TRUSTED_BUILTIN_MCP_TOOL_ID not in pinned_mcp_tool_ids
    ):
        raise _capability_not_authorized()
    return pinned_mcp_tool_ids


def require_replay_source_identity(
    *,
    pinned_version: str,
    pinned_executor_type: str,
    release_decision: dict[str, Any],
    skill_manifests: list[dict[str, Any]],
) -> None:
    """Fail closed when a source run lacks the immutable replay contract."""

    if (
        not pinned_version
        or pinned_executor_type not in DEFAULT_RUN_EXECUTOR_TYPES
        or not release_decision
        or not skill_manifests
    ):
        raise _capability_not_authorized()


async def list_mcp_server_registry(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    department_id: str,
    include_disabled: bool = True,
) -> list[dict[str, Any]]:
    """Return tenant-scoped MCP server lifecycle registry without secret material."""

    cursor = await conn.execute(
        """
        select
          tenant_id,
          name,
          transport,
          endpoint_redacted,
          status,
          is_system,
          allowed_roles,
          role_quotas_json,
          department_ids,
          credential_state,
          credential_metadata_json,
          catalog_generation,
          catalog_revision,
          catalog_status,
          catalog_unavailable_reason,
          catalog_discovered_count,
          catalog_selectable_count,
          catalog_last_synced_at,
          created_at,
          updated_at
        from mcp_servers
        where tenant_id = %s
          and (cardinality(department_ids) = 0 or %s = any(department_ids))
          and status <> 'deleted'
          and (%s or status = 'active')
        order by is_system desc, name asc
        """,
        (tenant_id, department_id, include_disabled),
    )
    return [_mcp_server_projection(dict(row)) for row in await cursor.fetchall()]


async def list_tenant_mcp_server_registry(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    include_disabled: bool = True,
) -> list[dict[str, Any]]:
    """Return the unfiltered tenant MCP registry for distribution resolution."""

    cursor = await conn.execute(
        """
        select
          tenant_id,
          name,
          transport,
          endpoint_redacted,
          status,
          is_system,
          allowed_roles,
          role_quotas_json,
          department_ids,
          credential_state,
          credential_metadata_json,
          catalog_generation,
          catalog_revision,
          catalog_status,
          catalog_unavailable_reason,
          catalog_discovered_count,
          catalog_selectable_count,
          catalog_last_synced_at,
          created_at,
          updated_at
        from mcp_servers
        where tenant_id = %s
          and status <> 'deleted'
          and (%s or status = 'active')
        order by is_system desc, name asc
        """,
        (tenant_id, include_disabled),
    )
    return [_mcp_server_projection(dict(row)) for row in await cursor.fetchall()]


async def list_mcp_server_registry_names(
    conn: AsyncConnection,
    *,
    tenant_id: str,
) -> list[str]:
    """Return non-deleted tenant MCP server names for legacy fallback suppression."""

    cursor = await conn.execute(
        """
        select name
        from mcp_servers
        where tenant_id = %s
          and status <> 'deleted'
        order by name asc
        """,
        (tenant_id,),
    )
    return [str(row.get("name") or "") for row in await cursor.fetchall() if row.get("name")]


async def upsert_mcp_server_registry(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    name: str,
    transport: str,
    enabled: bool,
    is_system: bool,
    endpoint_redacted: str,
    allowed_roles: list[str],
    role_quotas: dict[str, Any],
    department_ids: list[str],
    credential_state: str,
    credential_metadata: dict[str, Any],
    credential_fingerprint: str,
    updated_by: str,
) -> dict[str, Any]:
    """Upsert a tenant-scoped MCP server registry row with redacted connection metadata."""

    cursor = await conn.execute(
        """
        with scope_guard as (
          select not exists (
            select 1
            from mcp_servers existing
            where existing.tenant_id = %s
              and existing.name = %s
              and existing.is_system <> %s
          ) as allowed
        ),
        upserted as (
          insert into mcp_servers(
            id, tenant_id, name, transport, endpoint_redacted, status, is_system,
            allowed_roles, role_quotas_json, department_ids, credential_state,
            credential_metadata_json, credential_fingerprint, catalog_generation,
            catalog_status, catalog_unavailable_reason, updated_by, updated_at
          )
          select %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s::jsonb, %s, 1, %s, %s, %s, now()
          from scope_guard
          where allowed
          on conflict (tenant_id, name) do update
          set transport = excluded.transport,
              endpoint_redacted = excluded.endpoint_redacted,
              status = excluded.status,
              allowed_roles = excluded.allowed_roles,
              role_quotas_json = excluded.role_quotas_json,
              department_ids = excluded.department_ids,
              credential_state = excluded.credential_state,
              credential_metadata_json = excluded.credential_metadata_json,
              credential_fingerprint = excluded.credential_fingerprint,
              catalog_generation = mcp_servers.catalog_generation + 1,
              catalog_status = case when excluded.status = 'active' then 'refresh_required' else 'disabled' end,
              catalog_unavailable_reason = case when excluded.status = 'active' then 'refresh_required' else 'disabled' end,
              catalog_discovered_count = 0,
              catalog_selectable_count = 0,
              catalog_sync_lease_expires_at = null,
              updated_by = excluded.updated_by,
              updated_at = now()
          where mcp_servers.is_system = excluded.is_system
          returning *
        )
        select
          tenant_id,
          name,
          transport,
          endpoint_redacted,
          status,
          is_system,
          allowed_roles,
          role_quotas_json,
          department_ids,
          credential_state,
          credential_metadata_json,
          catalog_generation,
          catalog_revision,
          catalog_status,
          catalog_unavailable_reason,
          catalog_discovered_count,
          catalog_selectable_count,
          catalog_last_synced_at,
          created_at,
          updated_at
        from upserted
        """,
        (
            tenant_id,
            name,
            is_system,
            new_id("mcpsrv"),
            tenant_id,
            name,
            transport,
            endpoint_redacted,
            "active" if enabled else "disabled",
            is_system,
            json.dumps(allowed_roles, ensure_ascii=False),
            dumps_json(role_quotas),
            department_ids,
            credential_state,
            dumps_json(credential_metadata),
            credential_fingerprint,
            "refresh_required" if enabled else "disabled",
            "refresh_required" if enabled else "disabled",
            updated_by,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryConflictError("mcp_server_scope_conflict")
    return _mcp_server_projection(dict(row))


async def toggle_mcp_server_registry(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    name: str,
    enabled: bool | None,
    updated_by: str,
) -> dict[str, Any]:
    """Toggle or set a tenant-scoped MCP server status."""

    cursor = await conn.execute(
        """
        update mcp_servers
        set status = case
              when %s::boolean is null then case when status = 'active' then 'disabled' else 'active' end
              when %s::boolean then 'active'
              else 'disabled'
            end,
            updated_by = %s,
            catalog_generation = catalog_generation + 1,
            catalog_status = case
              when %s::boolean is null then case when status = 'active' then 'disabled' else 'refresh_required' end
              when %s::boolean then 'refresh_required'
              else 'disabled'
            end,
            catalog_unavailable_reason = case
              when %s::boolean is null then case when status = 'active' then 'disabled' else 'refresh_required' end
              when %s::boolean then 'refresh_required'
              else 'disabled'
            end,
            catalog_discovered_count = 0,
            catalog_selectable_count = 0,
            catalog_sync_lease_expires_at = null,
            updated_at = now()
        where tenant_id = %s
          and name = %s
          and status <> 'deleted'
        returning
          tenant_id,
          name,
          transport,
          endpoint_redacted,
          status,
          is_system,
          allowed_roles,
          role_quotas_json,
          department_ids,
          credential_state,
          credential_metadata_json,
          catalog_generation,
          catalog_revision,
          catalog_status,
          catalog_unavailable_reason,
          catalog_discovered_count,
          catalog_selectable_count,
          catalog_last_synced_at,
          created_at,
          updated_at
        """,
        (enabled, enabled, updated_by, enabled, enabled, enabled, enabled, tenant_id, name),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("mcp_server_not_found")
    return _mcp_server_projection(dict(row))


async def delete_mcp_server_registry(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    name: str,
    updated_by: str,
) -> dict[str, Any]:
    """Soft-delete a tenant-scoped MCP server registry row."""

    cursor = await conn.execute(
        """
        update mcp_servers
        set status = 'deleted',
            updated_by = %s,
            catalog_generation = catalog_generation + 1,
            catalog_status = 'deleted',
            catalog_unavailable_reason = 'deleted',
            catalog_discovered_count = 0,
            catalog_selectable_count = 0,
            catalog_sync_lease_expires_at = null,
            updated_at = now()
        where tenant_id = %s
          and name = %s
        returning
          tenant_id,
          name,
          transport,
          endpoint_redacted,
          status,
          is_system,
          allowed_roles,
          role_quotas_json,
          department_ids,
          credential_state,
          credential_metadata_json,
          catalog_generation,
          catalog_revision,
          catalog_status,
          catalog_unavailable_reason,
          catalog_discovered_count,
          catalog_selectable_count,
          catalog_last_synced_at,
          created_at,
          updated_at
        """,
        (updated_by, tenant_id, name),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("mcp_server_not_found")
    return _mcp_server_projection(dict(row))


async def record_mcp_server_credential(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    server_name: str,
    credential_fingerprint: str,
    metadata: dict[str, Any],
    updated_by: str,
) -> None:
    """Record credential fingerprint metadata without storing raw credential values."""

    await conn.execute(
        """
        insert into mcp_server_credentials(
          tenant_id, server_name, credential_fingerprint, metadata_json, updated_by, updated_at
        )
        values (%s, %s, %s, %s::jsonb, %s, now())
        on conflict (tenant_id, server_name) do update
        set credential_fingerprint = excluded.credential_fingerprint,
            metadata_json = excluded.metadata_json,
            updated_by = excluded.updated_by,
            updated_at = now()
        """,
        (
            tenant_id,
            server_name,
            credential_fingerprint,
            dumps_json(metadata),
            updated_by,
        ),
    )


async def list_admin_tool_policies(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    include_disabled: bool = True,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return tenant-scoped admin tool policy projections without private connection fields."""
    limit = max(min(int(limit), 500), 1)
    cursor = await conn.execute(
        """
        select
          mcp_tools.id as tool_id,
          mcp_tools.server_id,
          mcp_tools.name,
          mcp_tools.description,
          mcp_tools.status as registry_status,
          tool_policies.status as policy_status,
          mcp_tools.write_capable as registry_write_capable,
          tool_policies.write_capable as policy_write_capable,
          mcp_tools.risk_level as registry_risk_level,
          tool_policies.risk_level as policy_risk_level,
          mcp_tools.visible_to_user as registry_visible_to_user,
          tool_policies.visible_to_user as policy_visible_to_user,
          tool_policies.reason,
          tool_policies.updated_by,
          tool_policies.updated_at
        from mcp_tools
        left join tool_policies
          on tool_policies.tenant_id = %s
         and tool_policies.tool_id = mcp_tools.id
        where """ + _mcp_repository.mcp_tool_tenant_authority_sql() + """
          and (
            %s
            or (
              mcp_tools.status = 'active'
              and tool_policies.status = 'active'
              and coalesce(mcp_tools.visible_to_user, false) = true
              and tool_policies.visible_to_user = true
            )
          )
        order by case mcp_tools.id
          when 'ragflow-knowledge-search' then 1
          else 99
        end, mcp_tools.id asc
        limit %s
        """,
        (tenant_id, tenant_id, include_disabled, limit),
    )
    return [_tool_policy_projection(dict(row), tenant_id=tenant_id) for row in await cursor.fetchall()]


async def list_admin_tool_policy_history(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    tool_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return bounded tenant-scoped audit history for admin tool policy updates."""
    bounded_limit = max(min(int(100 if limit is None else limit), 500), 1)
    clauses = [
        "tenant_id = %s",
        "target_type = %s",
        "action = %s",
    ]
    params: list[Any] = [tenant_id, "tool_policy", "admin.tool_policy.updated"]
    if tool_id:
        clauses.append("target_id = %s")
        params.append(tool_id)
    params.append(bounded_limit)
    cursor = await conn.execute(
        f"""
        select id, user_id, action, target_type, target_id, trace_id, schema_version, payload_json, created_at
        from audit_logs
        where {" and ".join(clauses)}
        order by created_at desc, id desc
        limit %s
        """,
        tuple(params),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def list_role_governance_audit_history(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str | None = None,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Return bounded tenant-scoped role governance audit history."""
    bounded_limit = max(min(int(25 if limit is None else limit), 100), 1)
    role_actions = (
        "role_governance.request.created",
        "role_governance.approval.approve_requested",
        "role_governance.approval.reject_requested",
        "role_governance.rollback.requested",
    )
    clauses = ["tenant_id = %s", "action = any(%s)"]
    params: list[Any] = [tenant_id, list(role_actions)]
    if user_id:
        clauses.append("(user_id = %s or payload_json->>'requester_id' = %s)")
        params.extend([user_id, user_id])
    params.append(bounded_limit)
    cursor = await conn.execute(
        f"""
        select id, user_id, action, target_type, target_id, trace_id, schema_version, payload_json, created_at
        from audit_logs
        where {" and ".join(clauses)}
        order by created_at desc, id desc
        limit %s
        """,
        tuple(params),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def upsert_admin_tool_policy(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    tool_id: str,
    status: str,
    risk_level: str,
    write_capable: bool,
    visible_to_user: bool,
    reason: str,
    updated_by: str,
) -> dict[str, Any]:
    """Upsert a tenant-scoped tool policy and return its effective runtime projection."""
    cursor = await conn.execute(
        """
        with upserted as (
          insert into tool_policies(
            tenant_id, tool_id, status, write_capable, risk_level,
            visible_to_user, reason, updated_by, updated_at
          )
          select %s, mcp_tools.id, %s, %s, %s, %s, %s, %s, now()
          from mcp_tools
          where mcp_tools.id = %s
            and """ + _mcp_repository.mcp_tool_tenant_authority_sql() + """
          on conflict (tenant_id, tool_id) do update
          set status = excluded.status,
              write_capable = excluded.write_capable,
              risk_level = excluded.risk_level,
              visible_to_user = excluded.visible_to_user,
              reason = excluded.reason,
              updated_by = excluded.updated_by,
              updated_at = now()
          returning *
        )
        select
          mcp_tools.id as tool_id,
          mcp_tools.server_id,
          mcp_tools.name,
          mcp_tools.description,
          mcp_tools.status as registry_status,
          upserted.status as policy_status,
          mcp_tools.write_capable as registry_write_capable,
          upserted.write_capable as policy_write_capable,
          mcp_tools.risk_level as registry_risk_level,
          upserted.risk_level as policy_risk_level,
          mcp_tools.visible_to_user as registry_visible_to_user,
          upserted.visible_to_user as policy_visible_to_user,
          upserted.reason,
          upserted.updated_by,
          upserted.updated_at
        from upserted
        join mcp_tools on mcp_tools.id = upserted.tool_id
        """,
        (
            tenant_id,
            status,
            write_capable,
            risk_level,
            visible_to_user,
            reason,
            updated_by,
            tool_id,
            tenant_id,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("mcp_tool_not_found")
    return _tool_policy_projection(dict(row), tenant_id=tenant_id)


async def list_workbench_capabilities(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    include_admin_fields: bool = False,
) -> list[dict[str, Any]]:
    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    cursor = await conn.execute(
        """
        select
          case agents.id
            when 'general-agent' then 'general_chat'
            when 'qa-word-review' then 'document_review'
            when 'baoyu-translate' then 'document_translation'
            when 'sop-assistant' then 'knowledge_answer'
            else agents.id
          end as capability_id,
          agents.name as label,
          agents.description,
          case
            when agents.agent_type = 'chat' and agents.default_skill_id is null then 'active'
            when skills.status <> 'active'
              or coalesce(tenant_capability_distributions.status, 'disabled') <> 'active'
              or coalesce(tenant_capability_distributions.visible_to_user, false) = false
            then 'disabled'
            when skills.id = 'ragflow-knowledge-search'
             and (
               coalesce(mcp_tools.status, 'disabled') <> 'active'
               or coalesce(tool_policies.status, 'disabled') <> 'active'
               or coalesce(mcp_tools.visible_to_user, false) = false
               or coalesce(tool_policies.visible_to_user, false) = false
             )
            then 'disabled'
            else 'active'
          end as status,
          case when agents.agent_type = 'chat' and agents.default_skill_id is null then '["chat"]'::jsonb else skills.input_modes end as input_modes,
          case when agents.agent_type = 'chat' and agents.default_skill_id is null then '["answer"]'::jsonb else skills.output_modes end as output_modes,
          agents.id as agent_id,
          skills.id as skill_id,
          skills.version as skill_version,
          case when agents.agent_type = 'chat' and agents.default_skill_id is null then 'claude-agent-worker' else skills.executor_type end as executor_type,
          case when skills.id = 'ragflow-knowledge-search' then mcp_tools.server_id else null end as mcp_server_id,
          case when skills.id = 'ragflow-knowledge-search' then mcp_tools.id else null end as mcp_tool_id,
          case
            when skills.id <> 'ragflow-knowledge-search' then null
            when mcp_tools.risk_level = 'high' or tool_policies.risk_level = 'high' then 'high'
            when mcp_tools.risk_level = 'medium' or tool_policies.risk_level = 'medium' then 'medium'
            else coalesce(mcp_tools.risk_level, 'low')
          end as risk_level,
          0 as recent_failures
        from agents
        left join skills on skills.id = agents.default_skill_id
        left join tenant_capability_distributions
          on tenant_capability_distributions.tenant_id = %s
         and tenant_capability_distributions.capability_kind = 'skill'
         and tenant_capability_distributions.capability_id = skills.id
        left join mcp_tools
          on mcp_tools.id = skills.id
        left join tool_policies
          on tool_policies.tenant_id = agents.tenant_id
         and tool_policies.tool_id = mcp_tools.id
        where agents.tenant_id = %s
          and agents.id in ('general-agent', 'qa-word-review', 'baoyu-translate', 'sop-assistant')
          and agents.status = 'active'
        order by case agents.id
          when 'general-agent' then 1
          when 'qa-word-review' then 2
          when 'baoyu-translate' then 3
          when 'sop-assistant' then 4
          else 99
        end
        """,
        (tenant_id, tenant_id),
    )
    rows = list(await cursor.fetchall())
    if include_admin_fields:
        return rows
    redacted = []
    for row in rows:
        item = dict(row)
        item["agent_id"] = None
        item["skill_id"] = None
        item["skill_version"] = None
        item["executor_type"] = None
        item["mcp_server_id"] = None
        item["mcp_tool_id"] = None
        item["risk_level"] = None
        item["recent_failures"] = None
        redacted.append(item)
    return redacted


async def ensure_workspace(conn: AsyncConnection, *, tenant_id: str, workspace_id: str) -> None:
    cursor = await conn.execute(
        """
        select 1
        from workspaces
        where tenant_id = %s and id = %s and status = 'active'
        """,
        (tenant_id, workspace_id),
    )
    if await cursor.fetchone() is None:
        raise RepositoryNotFoundError("workspace_not_found")


async def ensure_user(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str | None,
    display_name: str | None = None,
) -> None:
    if not user_id:
        return
    await conn.execute(
        """
        insert into users(id, tenant_id, display_name)
        values (%s, %s, %s)
        on conflict (id) do nothing
        """,
        (user_id, tenant_id, display_name or user_id),
    )


async def ensure_submission_principal(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    display_name: str | None = None,
) -> dict[str, Any]:
    """Provision and tenant-validate a principal before a submission ledger write."""

    await ensure_user(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        display_name=display_name,
    )
    principal_user = await get_user(conn, tenant_id=tenant_id, user_id=user_id)
    if principal_user is None:
        raise RepositoryAuthorizationError("principal_user_scope_mismatch")
    return principal_user


async def get_user(conn: AsyncConnection, *, tenant_id: str, user_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select id, tenant_id, display_name, email, external_id, status, created_at
        from users
        where tenant_id = %s
          and id = %s
          and status = 'active'
        """,
        (tenant_id, user_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def get_agent(conn: AsyncConnection, *, tenant_id: str, agent_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select id, tenant_id, name, agent_type, default_skill_id, status, created_at
        from agents
        where tenant_id = %s
          and id = %s
          and status = 'active'
        """,
        (tenant_id, agent_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def allocate_session_run_generation(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str | None,
    session_id: str,
    agent_id: str,
) -> int:
    """Atomically allocate the sole durable creation generation for one session run."""

    cursor = await conn.execute(
        """
        update sessions
        set next_run_generation = next_run_generation + 1,
            updated_at = now()
        where tenant_id = %s
          and workspace_id = %s
          and user_id is not distinct from %s
          and id = %s
          and agent_id = %s
          and status = 'active'
        returning next_run_generation
        """,
        (tenant_id, workspace_id, user_id, session_id, agent_id),
    )
    row = await cursor.fetchone()
    generation = row.get("next_run_generation") if row else None
    if not isinstance(generation, int) or isinstance(generation, bool) or generation <= 0:
        raise RepositoryNotFoundError("session_not_found")
    return generation


async def create_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    session_id: str,
    user_id: str | None,
    agent_id: str,
    skill_id: str | None,
    execution_kind: str = RUN_EXECUTION_KIND_SKILL,
    input_json: dict[str, Any],
    principal_roles: list[str] | None = None,
    principal_department_id: str = "",
    auth_source: str | None = None,
    authz_policy_version: int = 1,
    authority_source: str = "",
    authority_checked_at: str | None = None,
    run_id: str | None = None,
    admitted_agent_profile_revision: int | None = None,
    admitted_agent_profile_hash: str | None = None,
) -> str:
    _require_json_size(
        input_json, max_bytes=RUN_INPUT_MAX_BYTES, code="run_input_too_large"
    )
    if (
        (execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT and skill_id is not None)
        or (execution_kind == RUN_EXECUTION_KIND_SKILL and not skill_id)
        or execution_kind
        not in {
            RUN_EXECUTION_KIND_HARNESS_CHAT,
            RUN_EXECUTION_KIND_SKILL,
        }
    ):
        raise RepositoryConflictError("run_execution_skill_identity_mismatch")
    resolved_run_id = run_id or new_id("run")
    trace_id = standard_trace_id(resolved_run_id)
    await ensure_workspace_belongs_to_tenant(conn, tenant_id=tenant_id, workspace_id=workspace_id)
    session_generation = await allocate_session_run_generation(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        agent_id=agent_id,
    )
    cursor = await conn.execute(
        """
        insert into runs(
          id, tenant_id, workspace_id, session_id, user_id, agent_id, execution_kind, skill_id,
          trace_id, schema_version, executor_schema_version,
          principal_roles, principal_department_id, auth_source,
          authz_policy_version, authority_source, authority_checked_at,
          admitted_agent_profile_revision, admitted_agent_profile_hash,
          status, input_json, queued_at,
          session_generation,
          input_token_count, output_token_count, total_token_count, estimated_cost_minor
        )
        select %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, 'queued', %s::jsonb, now(), %s, 0, 0, 0, 0
        from sessions
        where sessions.tenant_id = %s
          and sessions.workspace_id = %s
          and sessions.user_id = %s
          and sessions.id = %s
          and sessions.agent_id = %s
        returning id
        """,
        (
            resolved_run_id,
            tenant_id,
            workspace_id,
            session_id,
            user_id,
            agent_id,
            execution_kind,
            skill_id,
            trace_id,
            RUN_CONTRACT_VERSION,
            EXECUTOR_RESULT_SCHEMA_VERSION,
            dumps_json(normalize_roles(principal_roles or [])),
            str(principal_department_id or ""),
            auth_source,
            int(authz_policy_version),
            str(authority_source or auth_source or ""),
            authority_checked_at or None,
            admitted_agent_profile_revision,
            admitted_agent_profile_hash,
            dumps_json(input_json),
            session_generation,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            agent_id,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("session_not_found")
    return resolved_run_id


async def update_run_auth_snapshot(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    principal_roles: list[str] | None,
    principal_department_id: str,
    auth_source: str | None,
    authz_policy_version: int,
    authority_source: str,
    authority_checked_at: str | datetime | None,
) -> None:
    """Refresh the server-owned authorization snapshot for one tenant run."""

    await conn.execute(
        """
        update runs
        set principal_roles = %s::jsonb,
            principal_department_id = %s,
            auth_source = %s,
            authz_policy_version = %s,
            authority_source = %s,
            authority_checked_at = %s
        where tenant_id = %s
          and id = %s
        """,
        (
            dumps_json(normalize_roles(principal_roles or [])),
            str(principal_department_id or ""),
            auth_source,
            int(authz_policy_version),
            str(authority_source or auth_source or ""),
            authority_checked_at,
            tenant_id,
            run_id,
        ),
    )


def _validated_run_control_operation_identity(*, action: str, operation_id: str) -> tuple[str, str]:
    normalized_action = str(action or "").strip()
    if normalized_action not in RUN_CONTROL_OPERATION_ACTIONS:
        raise RepositoryConflictError("invalid_run_control_action")
    normalized_operation_id = str(operation_id or "").strip()
    try:
        parsed_operation_id = uuid.UUID(normalized_operation_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise RepositoryConflictError("invalid_run_control_operation_id") from exc
    if parsed_operation_id.version != 4 or str(parsed_operation_id) != normalized_operation_id:
        raise RepositoryConflictError("invalid_run_control_operation_id")
    return normalized_action, normalized_operation_id


async def acquire_run_control_operation_lock(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    source_run_id: str,
    action: str,
    operation_id: str,
) -> None:
    """Serialize one exact principal-scoped retry/resume identity for this transaction."""

    normalized_action, normalized_operation_id = _validated_run_control_operation_identity(
        action=action,
        operation_id=operation_id,
    )
    lock_scope = dumps_json(
        {
            "scope": "run_control_operation",
            "tenant_id": tenant_id,
            "user_id": user_id,
            "source_run_id": source_run_id,
            "action": normalized_action,
            "operation_id": normalized_operation_id,
        }
    )
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s::text, 0::bigint))",
        (lock_scope,),
    )


async def get_run_control_operation(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    source_run_id: str,
    action: str,
    operation_id: str,
) -> dict[str, Any] | None:
    """Resolve an exact durable child mapping without crossing its principal or action scope."""

    normalized_action, normalized_operation_id = _validated_run_control_operation_identity(
        action=action,
        operation_id=operation_id,
    )
    cursor = await conn.execute(
        """
        select source.id as source_run_id,
               operation.payload_json->>'action' as action,
               operation.payload_json->>'operation_id' as operation_id,
               child.id as run_id,
               child.session_id,
               child.status,
               child.workspace_id,
               child.user_id,
               child.agent_id,
               child.skill_id,
               child.input_json
        from run_events operation
        join runs source
          on source.tenant_id = operation.tenant_id
         and source.id = operation.run_id
         and source.user_id = %s
        join runs child
          on child.tenant_id = operation.tenant_id
         and child.id = operation.payload_json->>'child_run_id'
         and child.user_id = %s
         and child.copied_from_run_id = source.id
        where operation.tenant_id = %s
          and operation.run_id = %s
          and operation.event_type = 'run_control_operation_committed'
          and operation.payload_json->>'source_run_id' = %s
          and operation.payload_json->>'action' = %s
          and operation.payload_json->>'operation_id' = %s
        order by operation.sequence desc, operation.created_at desc
        limit 1
        """,
        (
            user_id,
            user_id,
            tenant_id,
            source_run_id,
            source_run_id,
            normalized_action,
            normalized_operation_id,
        ),
    )
    return await cursor.fetchone()


async def record_run_control_operation(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    source_run_id: str,
    child_run_id: str,
    action: str,
    operation_id: str,
    trace_id: str | None = None,
) -> str:
    """Persist an immutable exact operation-to-child link in the authoritative event store."""

    normalized_action, normalized_operation_id = _validated_run_control_operation_identity(
        action=action,
        operation_id=operation_id,
    )
    return await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=source_run_id,
        trace_id=trace_id,
        event_type="run_control_operation_committed",
        stage="control",
        message="Run control operation committed",
        visible_to_user=False,
        payload={
            "visible_to_user": False,
            "source_run_id": source_run_id,
            "child_run_id": child_run_id,
            "action": normalized_action,
            "operation_id": normalized_operation_id,
        },
    )


async def list_stale_run_reconciliation_candidates(
    conn: AsyncConnection,
    *,
    stale_after_seconds: int,
    cancel_requested_after_seconds: int | None = None,
    limit: int,
) -> list[dict[str, Any]]:
    """List bounded active runs whose durable progress is stale and lease-free."""

    bounded_staleness = max(int(stale_after_seconds), 1)
    bounded_cancel_staleness = max(
        int(
            bounded_staleness
            if cancel_requested_after_seconds is None
            else cancel_requested_after_seconds
        ),
        1,
    )
    bounded_limit = max(1, min(int(limit), 50))
    cursor = await conn.execute(
        """
        select runs.tenant_id, runs.workspace_id, runs.user_id, runs.id as run_id,
               runs.status, runs.cancel_requested_at,
               runs.cancel_requested_at as cancel_requested_before,
               greatest(
                 coalesce(latest_event.created_at, '-infinity'::timestamptz),
                 coalesce(runs.started_at, '-infinity'::timestamptz),
                 coalesce(runs.queued_at, '-infinity'::timestamptz),
                 runs.created_at
               ) as stale_before
        from runs
        left join lateral (
          select run_events.created_at
          from run_events
          where run_events.tenant_id = runs.tenant_id
            and run_events.run_id = runs.id
          order by run_events.created_at desc, run_events.sequence desc
          limit 1
        ) as latest_event on true
        where runs.status in ('queued', 'running')
          and (
            (runs.cancel_requested_at is not null
             and runs.cancel_requested_at <= clock_timestamp() - (%s * interval '1 second'))
            or
            (runs.cancel_requested_at is null
             and greatest(
                   coalesce(latest_event.created_at, '-infinity'::timestamptz),
                   coalesce(runs.started_at, '-infinity'::timestamptz),
                   coalesce(runs.queued_at, '-infinity'::timestamptz),
                   runs.created_at
                 ) <= clock_timestamp() - (%s * interval '1 second'))
          )
          and not exists (
            select 1 from sandbox_leases
            where sandbox_leases.tenant_id = runs.tenant_id
              and sandbox_leases.run_id = runs.id
              and sandbox_leases.status = 'active'
          )
          and not exists (
            select 1 from sandbox_leases
            where sandbox_leases.tenant_id = runs.tenant_id
              and sandbox_leases.run_id = runs.id
              and sandbox_leases.executor_terminal_json is not null
              and sandbox_leases.executor_reconciliation_status <> 'finalized'
          )
        order by stale_before asc, runs.tenant_id asc, runs.id asc
        limit %s
        """,
        (bounded_cancel_staleness, bounded_staleness, bounded_limit),
    )
    return list(await cursor.fetchall())


async def stage_stale_run_reconciliation(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str | None,
    run_id: str,
    expected_status: str,
    stale_before: Any,
    cancel_requested_before: Any | None = None,
    terminal_status: str,
    error_code: str | None,
    error_message: str | None,
) -> dict[str, Any] | None:
    """CAS one ownerless stale run into the existing terminalization path."""

    if expected_status not in {"queued", "running"}:
        raise ValueError("invalid_stale_run_expected_status")
    if terminal_status not in {"failed", "cancelled"}:
        raise ValueError("invalid_stale_run_terminal_status")
    target_result = (
        {"message": "任务已取消", "reconciliation_reason": "stale_run_no_owner"}
        if terminal_status == "cancelled"
        else {
            "message": "Run interrupted because no live execution owner remains.",
            "retryable": True,
            "reconciliation_reason": "stale_run_no_owner",
        }
    )
    cursor = await conn.execute(
        """
        update runs
        set permission_terminalization_target = %s,
            permission_terminalization_reason = 'stale_run_no_owner',
            permission_terminalization_result_json = %s::jsonb,
            permission_terminalization_error_code = %s,
            permission_terminalization_error_message = %s
        where tenant_id = %s
          and workspace_id = %s
          and user_id is not distinct from %s
          and id = %s
          and status = %s
          and permission_terminalization_target is null
          and (%s <> 'cancelled' or cancel_requested_at is not null)
          and (%s <> 'failed' or cancel_requested_at is null)
          and not exists (
            select 1 from sandbox_leases
            where sandbox_leases.tenant_id = runs.tenant_id
              and sandbox_leases.run_id = runs.id
              and sandbox_leases.status = 'active'
          )
          and (
            (%s = 'cancelled' and cancel_requested_at <= %s::timestamptz)
            or
            (%s = 'failed' and greatest(
                  coalesce((select max(created_at) from run_events
                            where run_events.tenant_id = runs.tenant_id
                              and run_events.run_id = runs.id), '-infinity'::timestamptz),
                  coalesce(started_at, '-infinity'::timestamptz),
                  coalesce(queued_at, '-infinity'::timestamptz),
                  created_at
                ) <= %s::timestamptz)
          )
        returning id, trace_id, permission_terminalization_target
        """,
        (
            terminal_status,
            dumps_json(target_result),
            error_code,
            error_message,
            tenant_id,
            workspace_id,
            user_id,
            run_id,
            expected_status,
            terminal_status,
            terminal_status,
            terminal_status,
            cancel_requested_before,
            terminal_status,
            stale_before,
        ),
    )
    staged = await cursor.fetchone()
    if staged is None:
        return None
    event_payload: dict[str, Any] = {
        "visible_to_user": True,
        "severity": "warning" if terminal_status == "cancelled" else "error",
        "result_status": terminal_status,
        "reason": "stale_run_no_owner",
    }
    if error_code:
        event_payload["error_code"] = error_code
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=staged.get("trace_id"),
        event_type="stale_run_reconciled",
        stage="worker_maintenance",
        message=(
            "任务取消请求已在执行器丢失后收口"
            if terminal_status == "cancelled"
            else "任务因执行器丢失而中断"
        ),
        payload=event_payload,
        error_code=error_code,
    )
    await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=None,
        action="run.stale.reconcile",
        target_type="run",
        target_id=run_id,
        trace_id=staged.get("trace_id"),
        payload_json={
            "workspace_id": workspace_id,
            "target_user_id": user_id,
            "expected_status": expected_status,
            "result_status": terminal_status,
            "reason": "stale_run_no_owner",
            "error_code": error_code,
        },
    )
    return dict(staged)


async def append_event(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    event_type: str,
    stage: str,
    message: str,
    payload: dict[str, Any] | None = None,
    trace_id: str | None = None,
    severity: str | None = None,
    visible_to_user: bool | None = None,
    error_code: str | None = None,
    latency_ms: int | None = None,
    input_token_count: int = 0,
    output_token_count: int = 0,
    total_token_count: int = 0,
    estimated_cost_minor: int = 0, return_record: bool = False,
) -> str | dict[str, Any]:
    try:
        return await _run_event_repository.append_event(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            event_type=event_type,
            stage=stage,
            message=message,
            payload=payload,
            trace_id=trace_id,
            severity=severity,
            visible_to_user=visible_to_user,
            error_code=error_code,
            latency_ms=latency_ms,
            input_token_count=input_token_count,
            output_token_count=output_token_count,
            total_token_count=total_token_count,
            estimated_cost_minor=estimated_cost_minor, return_record=return_record,
        )
    except _run_event_repository.RunEventLedgerConflictError as exc:
        raise _repository_ledger_conflict(exc) from exc


async def append_event_batch(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    batch_id: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        return await _run_event_repository.append_event_batch(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            batch_id=batch_id,
            events=events,
        )
    except _run_event_repository.RunEventLedgerConflictError as exc:
        raise _repository_ledger_conflict(exc) from exc


async def acquire_run_event_terminal_drain_fence(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
    batch_id: str,
) -> dict[str, bool]:
    try:
        return await _run_event_repository.acquire_terminal_drain_fence(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_id=attempt_id,
            batch_id=batch_id,
        )
    except _run_event_repository.RunEventLedgerConflictError as exc:
        raise _repository_ledger_conflict(exc) from exc


async def get_authorized_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    for_update: bool = False,
) -> dict[str, Any] | None:
    lock_clause = "for update of runs" if for_update else ""
    cursor = await conn.execute(
        f"""
        select runs.*
        from runs
        join sessions on sessions.id = runs.session_id
          and sessions.tenant_id = runs.tenant_id
          and sessions.workspace_id = runs.workspace_id
          and sessions.user_id = runs.user_id
          and sessions.agent_id = runs.agent_id
        where runs.tenant_id = %s
          and runs.id = %s
          and runs.user_id = %s
          and sessions.status = 'active'
        {lock_clause}
        """,
        (tenant_id, run_id, user_id),
    )
    return await cursor.fetchone()


async def get_authorized_session(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    workspace_id: str | None = None,
    for_update: bool = False,
) -> dict[str, Any] | None:
    lock_clause = " for update" if for_update else ""
    workspace_filter = "and workspace_id = %s" if workspace_id else ""
    params: list[Any] = [tenant_id, session_id, user_id]
    if workspace_id:
        params.append(workspace_id)
    if for_update:
        cursor = await conn.execute(
            f"""
            select *
            from sessions
            where tenant_id = %s
              and id = %s
              and user_id = %s
              and status = 'active'
              {workspace_filter}
            {lock_clause}
            """,
            tuple(params),
        )
        return await cursor.fetchone()
    cursor = await conn.execute(
        f"""
        select sessions.*, latest_run.input_json as latest_run_input_json
        from sessions
        left join lateral (
          select runs.input_json
          from runs
          where runs.tenant_id = sessions.tenant_id
            and runs.workspace_id = sessions.workspace_id
            and runs.user_id = sessions.user_id
            and runs.session_id = sessions.id
          order by runs.session_generation desc nulls last,
                   runs.created_at desc,
                   runs.id desc
          limit 1
        ) latest_run on true
        where sessions.tenant_id = %s
          and sessions.id = %s
          and sessions.user_id = %s
          and sessions.status = 'active'
          {"and sessions.workspace_id = %s" if workspace_id else ""}
        """,
        tuple(params),
    )
    return await cursor.fetchone()


async def get_authorized_context_target_session(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
) -> dict[str, Any] | None:
    """Load a target session only when it matches the source run owner scope."""
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id, status
        from sessions
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and id = %s
          and status = 'active'
        """,
        (tenant_id, workspace_id, user_id, session_id),
    )
    return await cursor.fetchone()


async def list_run_events(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    after_sequence: int | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    return await _run_event_repository.list_run_events(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        after_sequence=after_sequence,
        limit=limit,
    )


async def create_context_snapshot(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    trace_id: str,
    context_kind: str,
    included_message_ids: list[str],
    included_file_ids: list[str],
    included_artifact_ids: list[str],
    included_memory_record_ids: list[str],
    redaction_summary_json: dict[str, Any],
    payload_json: dict[str, Any],
) -> dict[str, Any]:
    """Atomically authorize and persist one run-scoped context snapshot."""
    snapshot_id = new_id("ctx")
    included_message_ids = _normalize_context_snapshot_member_ids(included_message_ids)
    included_file_ids = _normalize_context_snapshot_member_ids(included_file_ids)
    included_artifact_ids = _normalize_context_snapshot_member_ids(included_artifact_ids)
    included_memory_record_ids = _normalize_context_snapshot_member_ids(included_memory_record_ids)
    if (
        len(included_message_ids)
        + len(included_file_ids)
        + len(included_artifact_ids)
        + len(included_memory_record_ids)
        > CONTEXT_SNAPSHOT_MEMBER_BATCH_LIMIT
    ):
        raise RepositoryConflictError("context_snapshot_material_invalid")
    redaction_summary_json = sanitize_public_payload(redaction_summary_json)
    if not isinstance(redaction_summary_json, dict):
        redaction_summary_json = {}
    payload_json = sanitize_public_payload(payload_json)
    if not isinstance(payload_json, dict):
        payload_json = {}
    _require_json_size(
        payload_json,
        max_bytes=CONTEXT_SNAPSHOT_PAYLOAD_MAX_BYTES,
        code="context_snapshot_payload_too_large",
    )
    cursor = await conn.execute(
        """
        with scoped_run as (
          select runs.tenant_id, runs.workspace_id, runs.user_id, runs.session_id,
                 runs.id as run_id, runs.agent_id, runs.trace_id
          from runs
          join sessions on sessions.id = runs.session_id
            and sessions.tenant_id = runs.tenant_id
            and sessions.workspace_id = runs.workspace_id
            and sessions.user_id = runs.user_id
            and sessions.agent_id = runs.agent_id
          where runs.tenant_id = %s
            and runs.user_id = %s
            and runs.id = %s
        ), requested_members as (
          select %s::jsonb as message_ids,
                 %s::jsonb as file_ids,
                 %s::jsonb as artifact_ids,
                 %s::jsonb as memory_record_ids
        ), locked_artifacts as materialized (
          select artifacts.id
          from scoped_run
          cross join requested_members
          cross join lateral jsonb_array_elements_text(requested_members.artifact_ids) requested(id)
          join artifacts on artifacts.id = requested.id
            and artifacts.tenant_id = scoped_run.tenant_id
          join runs artifact_run on artifact_run.id = artifacts.run_id
            and artifact_run.tenant_id = artifacts.tenant_id
          where artifact_run.workspace_id = scoped_run.workspace_id
            and artifact_run.user_id = scoped_run.user_id
            and artifact_run.session_id = scoped_run.session_id
            and artifact_run.agent_id = scoped_run.agent_id
            and artifacts.lifecycle_state = 'active'
            and (artifacts.expires_at is null or artifacts.expires_at > statement_timestamp())
          for update of artifacts
        ), locked_memory_records as materialized (
          select memory_records.id
          from scoped_run
          cross join requested_members
          cross join lateral jsonb_array_elements_text(requested_members.memory_record_ids) requested(id)
          join memory_records on memory_records.id = requested.id
            and memory_records.tenant_id = scoped_run.tenant_id
          where memory_records.workspace_id = scoped_run.workspace_id
            and memory_records.user_id = scoped_run.user_id
            and memory_records.session_id = scoped_run.session_id
            and memory_records.agent_id = scoped_run.agent_id
            and memory_records.status = 'active'
            and memory_records.deleted_at is null
            and (memory_records.expires_at is null or memory_records.expires_at > statement_timestamp())
          for update of memory_records
        ), eligible_members as (
          select scoped_run.*, requested_members.*,
            (
              select count(*)
              from jsonb_array_elements_text(requested_members.message_ids) requested(id)
              join messages on messages.id = requested.id
              join sessions message_session on message_session.id = messages.session_id
                and message_session.tenant_id = messages.tenant_id
              join runs message_run on message_run.id = messages.run_id
                and message_run.tenant_id = messages.tenant_id
              where messages.tenant_id = scoped_run.tenant_id
                and messages.session_id = scoped_run.session_id
                and message_session.workspace_id = scoped_run.workspace_id
                and message_session.user_id = scoped_run.user_id
                and message_session.agent_id = scoped_run.agent_id
                and message_run.workspace_id = scoped_run.workspace_id
                and message_run.user_id = scoped_run.user_id
                and message_run.session_id = scoped_run.session_id
                and message_run.agent_id = scoped_run.agent_id
            ) as eligible_message_count,
            (
              select count(*)
              from jsonb_array_elements_text(requested_members.file_ids) requested(id)
              join files on files.id = requested.id
              join sessions file_session on file_session.id = files.session_id
                and file_session.tenant_id = files.tenant_id
              join runs file_run on file_run.id = files.run_id
                and file_run.tenant_id = files.tenant_id
              where files.tenant_id = scoped_run.tenant_id
                and files.workspace_id = scoped_run.workspace_id
                and files.user_id = scoped_run.user_id
                and files.lifecycle_state = 'active'
                and files.session_id = scoped_run.session_id
                and file_session.user_id = scoped_run.user_id
                and file_session.workspace_id = scoped_run.workspace_id
                and file_session.agent_id = scoped_run.agent_id
                and file_run.workspace_id = scoped_run.workspace_id
                and file_run.user_id = scoped_run.user_id
                and file_run.session_id = scoped_run.session_id
                and file_run.agent_id = scoped_run.agent_id
            ) as eligible_file_count,
            (
              select count(*)
              from locked_artifacts
            ) as eligible_artifact_count,
            (
              select count(*)
              from locked_memory_records
            ) as eligible_memory_record_count
          from scoped_run
          cross join requested_members
        )
        insert into run_context_snapshots(
          id, tenant_id, workspace_id, user_id, session_id, run_id, trace_id,
          schema_version, context_kind, included_message_ids, included_file_ids,
          included_artifact_ids, included_memory_record_ids, redaction_summary_json, payload_json
        )
        select %s, tenant_id, workspace_id, user_id, session_id, run_id, coalesce(trace_id, ''),
               %s, %s, message_ids, file_ids, artifact_ids, memory_record_ids, %s::jsonb, %s::jsonb
        from eligible_members
        where eligible_message_count = jsonb_array_length(message_ids)
          and eligible_file_count = jsonb_array_length(file_ids)
          and eligible_artifact_count = jsonb_array_length(artifact_ids)
          and eligible_memory_record_count = jsonb_array_length(memory_record_ids)
        returning id, tenant_id, workspace_id, user_id, session_id, run_id, trace_id,
                  schema_version, context_kind, included_message_ids, included_file_ids,
                  included_artifact_ids, included_memory_record_ids, redaction_summary_json,
                  payload_json, created_at
        """,
        (
            tenant_id,
            user_id,
            run_id,
            json.dumps(included_message_ids, ensure_ascii=False),
            json.dumps(included_file_ids, ensure_ascii=False),
            json.dumps(included_artifact_ids, ensure_ascii=False),
            json.dumps(included_memory_record_ids, ensure_ascii=False),
            snapshot_id,
            "ai-platform.context-snapshot.v1",
            context_kind,
            dumps_json(redaction_summary_json),
            dumps_json(payload_json),
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryConflictError("context_snapshot_material_invalid")
    return {
        "id": snapshot_id,
        "tenant_id": str(row.get("tenant_id") or tenant_id),
        "workspace_id": str(row.get("workspace_id") or workspace_id),
        "user_id": str(row.get("user_id") or user_id),
        "session_id": str(row.get("session_id") or session_id),
        "run_id": str(row.get("run_id") or run_id),
        "trace_id": str(row.get("trace_id") or trace_id),
        "schema_version": "ai-platform.context-snapshot.v1",
        "context_kind": context_kind,
        "included_message_ids": included_message_ids,
        "included_file_ids": included_file_ids,
        "included_artifact_ids": included_artifact_ids,
        "included_memory_record_ids": included_memory_record_ids,
        "redaction_summary_json": redaction_summary_json,
        "payload_json": payload_json,
    }


def _normalize_context_snapshot_member_ids(member_ids: list[str]) -> list[str]:
    """Reject malformed or duplicate snapshot members before the atomic SQL seam."""
    if not isinstance(member_ids, list) or len(member_ids) > CONTEXT_SNAPSHOT_MEMBER_BATCH_LIMIT:
        raise RepositoryConflictError("context_snapshot_material_invalid")
    normalized: list[str] = []
    seen: set[str] = set()
    for member_id in member_ids:
        if not isinstance(member_id, str):
            raise RepositoryConflictError("context_snapshot_material_invalid")
        normalized_id = member_id.strip()
        if not SAFE_ID_PATTERN.fullmatch(normalized_id) or normalized_id in seen:
            raise RepositoryConflictError("context_snapshot_material_invalid")
        seen.add(normalized_id)
        normalized.append(normalized_id)
    return normalized


async def update_run_context_snapshot_ref(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    context_snapshot_id: str,
    context_snapshot: dict[str, Any],
) -> None:
    if str(context_snapshot.get("context_snapshot_id") or "") != context_snapshot_id:
        raise RepositoryConflictError("context_snapshot_binding_invalid")
    cursor = await conn.execute(
        """
        update runs
        set context_snapshot_id = %s,
            input_json = case
              when runs.context_snapshot_id is null then jsonb_set(
                jsonb_set(coalesce(input_json, '{}'::jsonb), '{context_snapshot_id}', %s::jsonb, true),
                '{context_snapshot}',
                %s::jsonb,
                true
              )
              else input_json
            end
        where tenant_id = %s
          and id = %s
          and exists (
            select 1
            from run_context_snapshots
            where id = %s
              and tenant_id = runs.tenant_id
              and workspace_id = runs.workspace_id
              and user_id = runs.user_id
              and session_id = runs.session_id
              and run_id = runs.id
              and context_kind = 'executor'
          )
          and (
            context_snapshot_id is null
            and coalesce(input_json->>'context_snapshot_id', '') = ''
            or (
              context_snapshot_id = %s
              and input_json->>'context_snapshot_id' = context_snapshot_id
            )
          )
        returning context_snapshot_id
        """,
        (
            context_snapshot_id,
            json.dumps(context_snapshot_id, ensure_ascii=False),
            dumps_json(context_snapshot),
            tenant_id,
            run_id,
            context_snapshot_id,
            context_snapshot_id,
        ),
    )
    row = await cursor.fetchone()
    if row is None or str(row.get("context_snapshot_id") or "") != context_snapshot_id:
        raise RepositoryConflictError("context_snapshot_binding_invalid")


async def list_context_snapshots(conn: AsyncConnection, *, tenant_id: str, user_id: str, run_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select run_context_snapshots.id, run_context_snapshots.tenant_id,
               run_context_snapshots.workspace_id, run_context_snapshots.user_id,
               run_context_snapshots.session_id, run_context_snapshots.run_id,
               run_context_snapshots.trace_id, run_context_snapshots.schema_version,
               run_context_snapshots.context_kind, run_context_snapshots.included_message_ids,
               run_context_snapshots.included_file_ids, run_context_snapshots.included_artifact_ids,
               run_context_snapshots.included_memory_record_ids,
               run_context_snapshots.redaction_summary_json, run_context_snapshots.payload_json,
               run_context_snapshots.created_at
        from run_context_snapshots
        where tenant_id = %s and user_id = %s and run_id = %s
        order by created_at desc
        """,
        (tenant_id, user_id, run_id),
    )
    return list(await cursor.fetchall())


async def get_latest_authorized_executor_context_snapshot(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Compatibility lookup that still returns only the physical run binding."""
    cursor = await conn.execute(
        """
        select context_snapshot.id, context_snapshot.tenant_id, context_snapshot.workspace_id,
               context_snapshot.user_id, context_snapshot.session_id, context_snapshot.run_id,
               context_snapshot.trace_id, context_snapshot.schema_version, context_snapshot.context_kind,
               context_snapshot.included_message_ids, context_snapshot.included_file_ids,
               context_snapshot.included_artifact_ids, context_snapshot.included_memory_record_ids,
               context_snapshot.redaction_summary_json, context_snapshot.payload_json,
               context_snapshot.created_at
        from runs
        join run_context_snapshots context_snapshot
          on context_snapshot.id = runs.context_snapshot_id
          and context_snapshot.tenant_id = runs.tenant_id
          and context_snapshot.workspace_id = runs.workspace_id
          and context_snapshot.user_id = runs.user_id
          and context_snapshot.session_id = runs.session_id
          and context_snapshot.run_id = runs.id
          and context_snapshot.context_kind = 'executor'
        where runs.tenant_id = %s
          and runs.user_id = %s
          and runs.id = %s
          and runs.input_json->>'context_snapshot_id' = runs.context_snapshot_id
          and runs.input_json->'context_snapshot'->>'context_snapshot_id' = runs.context_snapshot_id
        """,
        (tenant_id, user_id, run_id),
    )
    return await cursor.fetchone()


async def get_bound_executor_context_snapshot(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Load exactly the run's immutable physical snapshot binding, never the latest row."""

    cursor = await conn.execute(
        """
        select context_snapshot.id, context_snapshot.tenant_id, context_snapshot.workspace_id,
               context_snapshot.user_id, context_snapshot.session_id, context_snapshot.run_id,
               context_snapshot.trace_id, context_snapshot.schema_version, context_snapshot.context_kind,
               context_snapshot.included_message_ids, context_snapshot.included_file_ids,
               context_snapshot.included_artifact_ids, context_snapshot.included_memory_record_ids,
               context_snapshot.redaction_summary_json, context_snapshot.payload_json,
               context_snapshot.created_at
        from runs
        join run_context_snapshots context_snapshot
          on context_snapshot.id = runs.context_snapshot_id
          and context_snapshot.tenant_id = runs.tenant_id
          and context_snapshot.workspace_id = runs.workspace_id
          and context_snapshot.user_id = runs.user_id
          and context_snapshot.session_id = runs.session_id
          and context_snapshot.run_id = runs.id
          and context_snapshot.context_kind = 'executor'
        where runs.tenant_id = %s
          and runs.workspace_id = %s
          and runs.user_id = %s
          and runs.session_id = %s
          and runs.id = %s
          and runs.input_json->>'context_snapshot_id' = runs.context_snapshot_id
          and runs.input_json->'context_snapshot'->>'context_snapshot_id' = runs.context_snapshot_id
        """,
        (tenant_id, workspace_id, user_id, session_id, run_id),
    )
    return await cursor.fetchone()


async def list_context_share_snapshots_for_target_session(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    target_session_id: str,
) -> list[dict[str, Any]]:
    """List share/fork snapshots whose public binding names an authorized target session."""
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, session_id, run_id, trace_id,
               schema_version, context_kind, included_message_ids, included_file_ids,
               included_artifact_ids, included_memory_record_ids, redaction_summary_json,
               payload_json, created_at
        from run_context_snapshots
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and context_kind = 'share_fork'
          and payload_json->'share_fork_context'->>'target_session_id' = %s
        order by created_at desc
        """,
        (tenant_id, workspace_id, user_id, target_session_id),
    )
    return list(await cursor.fetchall())


async def get_context_snapshot_for_worker(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    context_snapshot_id: str,
) -> dict[str, Any] | None:
    """Load a context snapshot only when it matches the full worker run identity."""
    cursor = await conn.execute(
        """
        select run_context_snapshots.id, run_context_snapshots.tenant_id,
               run_context_snapshots.workspace_id, run_context_snapshots.user_id,
               run_context_snapshots.session_id, run_context_snapshots.run_id,
               run_context_snapshots.trace_id, run_context_snapshots.schema_version,
               run_context_snapshots.context_kind, run_context_snapshots.included_message_ids,
               run_context_snapshots.included_file_ids, run_context_snapshots.included_artifact_ids,
               run_context_snapshots.included_memory_record_ids,
               run_context_snapshots.redaction_summary_json, run_context_snapshots.payload_json,
               run_context_snapshots.created_at
        from run_context_snapshots
        join runs on runs.context_snapshot_id = run_context_snapshots.id
          and runs.tenant_id = run_context_snapshots.tenant_id
          and runs.workspace_id = run_context_snapshots.workspace_id
          and runs.user_id = run_context_snapshots.user_id
          and runs.session_id = run_context_snapshots.session_id
          and runs.id = run_context_snapshots.run_id
        where run_context_snapshots.tenant_id = %s
          and run_context_snapshots.workspace_id = %s
          and run_context_snapshots.user_id = %s
          and run_context_snapshots.session_id = %s
          and run_context_snapshots.run_id = %s
          and run_context_snapshots.id = %s
          and run_context_snapshots.context_kind = 'executor'
          and runs.input_json->>'context_snapshot_id' = runs.context_snapshot_id
          and runs.input_json->'context_snapshot'->>'context_snapshot_id' = runs.context_snapshot_id
        """,
        (tenant_id, workspace_id, user_id, session_id, run_id, context_snapshot_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


def _default_memory_policy(*, tenant_id: str, workspace_id: str, user_id: str, agent_id: str | None) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "agent_id": agent_id,
        "memory_enabled": True,
        "long_term_memory_enabled": False,
        "retention_days": 90,
        "redaction_mode": "standard",
        "source": "default",
        "reason": "",
        "updated_by": "",
        "updated_at": None,
    }


def _stored_memory_redaction_mode(value: object) -> str:
    if value is None or str(value).strip() == "":
        return "strict"
    try:
        return normalize_memory_redaction_mode(value)
    except ValueError:
        return "strict"


def _validated_memory_redaction_mode(value: object) -> str:
    try:
        return normalize_memory_redaction_mode(value)
    except ValueError as exc:
        raise RepositoryConflictError(str(exc)) from exc


def _memory_policy_from_row(row: dict[str, Any], *, source: str = "stored") -> dict[str, Any]:
    return {
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "agent_id": row.get("agent_id"),
        "memory_enabled": bool(row.get("memory_enabled", True)),
        "long_term_memory_enabled": False,
        "retention_days": int(row.get("retention_days") or 90),
        "redaction_mode": _stored_memory_redaction_mode(row.get("redaction_mode")),
        "source": source,
        "reason": str(row.get("reason") or ""),
        "updated_by": str(row.get("updated_by") or ""),
        "updated_at": row.get("updated_at"),
    }


async def get_effective_memory_policy(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str | None,
) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id,
               memory_enabled, long_term_memory_enabled, retention_days,
               redaction_mode, reason, updated_by, updated_at
        from memory_policies
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and (agent_id = %s or agent_id is null)
        order by case when agent_id = %s then 0 else 1 end, updated_at desc
        limit 1
        """,
        (tenant_id, workspace_id, user_id, agent_id, agent_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return _default_memory_policy(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id, agent_id=agent_id)
    return _memory_policy_from_row(dict(row))


async def set_memory_policy(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str | None,
    memory_enabled: bool,
    long_term_memory_enabled: bool,
    retention_days: int,
    redaction_mode: str,
    reason: str,
    updated_by: str,
) -> dict[str, Any]:
    if long_term_memory_enabled:
        raise RepositoryConflictError("long_term_memory_not_available")
    redaction_mode = _validated_memory_redaction_mode(redaction_mode)
    policy_id = memory_policy_id(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id, agent_id=agent_id)
    cursor = await conn.execute(
        """
        insert into memory_policies(
          id, tenant_id, workspace_id, user_id, agent_id,
          memory_enabled, long_term_memory_enabled, retention_days, redaction_mode,
          reason, updated_by
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (id) do update
        set memory_enabled = excluded.memory_enabled,
            long_term_memory_enabled = excluded.long_term_memory_enabled,
            retention_days = excluded.retention_days,
            redaction_mode = excluded.redaction_mode,
            reason = excluded.reason,
            updated_by = excluded.updated_by,
            updated_at = now()
        returning id, tenant_id, workspace_id, user_id, agent_id,
                  memory_enabled, long_term_memory_enabled, retention_days, redaction_mode,
                  reason, updated_by, updated_at
        """,
        (
            policy_id,
            tenant_id,
            workspace_id,
            user_id,
            agent_id,
            bool(memory_enabled),
            bool(long_term_memory_enabled),
            int(retention_days),
            redaction_mode,
            reason,
            updated_by,
        ),
    )
    row = await cursor.fetchone()
    return _memory_policy_from_row(dict(row))


async def list_admin_memory_policies(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return stored memory policies for an admin same-tenant operational view."""
    limit = max(min(int(limit), 500), 1)
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id,
               memory_enabled, long_term_memory_enabled, retention_days,
               redaction_mode, reason, updated_by, updated_at
        from memory_policies
        where tenant_id = %s
          and workspace_id = %s
          and (%s::text is null or user_id = %s)
          and (%s::text is null or agent_id = %s)
        order by updated_at desc, created_at desc
        limit %s
        """,
        (tenant_id, workspace_id, user_id, user_id, agent_id, agent_id, limit),
    )
    return [_memory_policy_from_row(dict(row)) for row in await cursor.fetchall()]


async def create_memory_record(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str | None,
    session_id: str | None,
    record_type: str,
    content: str,
    metadata_json: dict[str, Any],
    retention_days: int = 90,
    redaction_mode: str = "standard",
) -> dict[str, Any]:
    if not session_id:
        raise RepositoryConflictError("memory_session_id_required")
    if not agent_id:
        raise RepositoryConflictError("memory_agent_id_required")
    retention_days = int(retention_days)
    if retention_days <= 0:
        raise RepositoryConflictError("memory_retention_days_invalid")
    redaction_mode = _validated_memory_redaction_mode(redaction_mode)
    record_id = new_id("mem")
    redacted_content = redact_memory_text(content, mode=redaction_mode)
    redacted_metadata = redact_memory_metadata(metadata_json, mode=redaction_mode)
    cursor = await conn.execute(
        """
        insert into memory_records(
          id, tenant_id, workspace_id, user_id, agent_id, session_id,
          record_type, content, metadata_json, expires_at
        )
        select %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, now() + (%s * interval '1 day')
        from sessions
        where sessions.tenant_id = %s
          and sessions.workspace_id = %s
          and sessions.user_id = %s
          and sessions.id = %s
          and sessions.agent_id = %s
        returning id, tenant_id, workspace_id, user_id, agent_id, session_id,
                  record_type, content, metadata_json, status, expires_at,
                  deleted_at, created_at, updated_at
        """,
        (
            record_id,
            tenant_id,
            workspace_id,
            user_id,
            agent_id,
            session_id,
            record_type,
            redacted_content,
            dumps_json(redacted_metadata),
            retention_days,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            agent_id,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("session_not_found")
    return dict(row)


async def list_memory_records(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if not session_id:
        raise RepositoryConflictError("memory_session_id_required")
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id, session_id,
               record_type, content, metadata_json, status, expires_at,
               deleted_at, created_at, updated_at
        from memory_records
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and status = 'active'
          and deleted_at is null
          and (%s::text is null or agent_id = %s)
          and (%s::text is null or session_id = %s)
          and (expires_at is null or expires_at > now())
        order by created_at desc
        limit %s
        """,
        (tenant_id, workspace_id, user_id, agent_id, agent_id, session_id, session_id, limit),
    )
    return list(await cursor.fetchall())


async def list_admin_memory_records(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str | None = None,
    status: str = "active",
    limit: int = 50,
) -> list[dict[str, Any]]:
    if status not in {"active", "deleted", "all"}:
        raise RepositoryConflictError("memory_status_invalid")
    limit = max(min(int(limit), 500), 1)
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id, session_id,
               record_type, status, expires_at, deleted_at, created_at, updated_at
        from memory_records
        where tenant_id = %s
          and workspace_id = %s
          and (%s::text is null or user_id = %s)
          and (%s = 'all' or status = %s)
        order by coalesce(deleted_at, expires_at, created_at) desc, created_at desc
        limit %s
        """,
        (tenant_id, workspace_id, user_id, user_id, status, status, limit),
    )
    return list(await cursor.fetchall())


async def delete_memory_record(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    agent_id: str,
    session_id: str,
    record_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update memory_records
        set status = 'deleted',
            deleted_at = now(),
            updated_at = now()
        where tenant_id = %s
          and workspace_id = %s
          and user_id = %s
          and agent_id = %s
          and session_id = %s
          and id = %s
          and status = 'active'
          and deleted_at is null
        returning id, tenant_id, workspace_id, user_id, agent_id, session_id,
                  record_type, status, expires_at, deleted_at, created_at, updated_at
        """,
        (tenant_id, workspace_id, user_id, agent_id, session_id, record_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def admin_delete_memory_record(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    record_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update memory_records
        set status = 'deleted',
            deleted_at = now(),
            updated_at = now()
        where tenant_id = %s
          and workspace_id = %s
          and id = %s
          and status = 'active'
          and deleted_at is null
        returning id, tenant_id, workspace_id, user_id, agent_id, session_id,
                  record_type, status, expires_at, deleted_at, created_at, updated_at
        """,
        (tenant_id, workspace_id, record_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def cleanup_expired_memory_records(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    if not workspace_id:
        raise RepositoryConflictError("memory_workspace_id_required")
    limit = int(limit)
    if limit <= 0:
        raise RepositoryConflictError("memory_cleanup_limit_invalid")
    cursor = await conn.execute(
        """
        update memory_records
        set status = 'deleted',
            deleted_at = now(),
            updated_at = now()
        where id in (
          select id
          from memory_records
          where tenant_id = %s
            and workspace_id = %s
            and status = 'active'
            and deleted_at is null
            and expires_at is not null
            and expires_at <= now()
          order by expires_at asc, created_at asc
          limit %s
          for update skip locked
        )
        returning id, tenant_id, workspace_id, user_id, agent_id, session_id,
                  record_type, status, expires_at, deleted_at, created_at, updated_at
        """,
        (tenant_id, workspace_id, limit),
    )
    return list(await cursor.fetchall())


async def cleanup_expired_memory_records_across_scopes(
    conn: AsyncConnection,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Soft-delete expired memory records using a bounded rotating scope cursor."""
    limit = int(limit)
    if limit <= 0:
        raise RepositoryConflictError("memory_cleanup_limit_invalid")
    cursor = await conn.execute(
        """
        select tenant_id, workspace_id
        from worker_maintenance_cursors
        where cursor_key = %s
        for update
        """,
        (MEMORY_RETENTION_CLEANUP_CURSOR_KEY,),
    )
    cursor_row = await cursor.fetchone()
    last_tenant_id = str(cursor_row["tenant_id"]) if cursor_row and cursor_row.get("tenant_id") else None
    last_workspace_id = str(cursor_row["workspace_id"]) if cursor_row and cursor_row.get("workspace_id") else None

    scope_rows: list[dict[str, Any]] = []
    if last_tenant_id is not None and last_workspace_id is not None:
        scope_rows.extend(
            await _list_expired_memory_cleanup_scopes(
                conn,
                after_tenant_id=last_tenant_id,
                after_workspace_id=last_workspace_id,
                limit=limit,
            )
        )
    else:
        scope_rows.extend(
            await _list_expired_memory_cleanup_scopes(
                conn,
                after_tenant_id=None,
                after_workspace_id=None,
                limit=limit,
            )
        )
    if last_tenant_id is not None and last_workspace_id is not None and len(scope_rows) < limit:
        scope_rows.extend(
            await _list_expired_memory_cleanup_scopes(
                conn,
                before_or_at_tenant_id=last_tenant_id,
                before_or_at_workspace_id=last_workspace_id,
                limit=limit - len(scope_rows),
            )
        )
    if not scope_rows:
        return []

    last_scope = scope_rows[-1]
    await conn.execute(
        """
        insert into worker_maintenance_cursors(cursor_key, tenant_id, workspace_id)
        values (%s, %s, %s)
        on conflict (cursor_key) do update
        set tenant_id = excluded.tenant_id,
            workspace_id = excluded.workspace_id,
            updated_at = now()
        """,
        (MEMORY_RETENTION_CLEANUP_CURSOR_KEY, str(last_scope["tenant_id"]), str(last_scope["workspace_id"])),
    )
    per_scope_limit = max(1, (limit + len(scope_rows) - 1) // len(scope_rows))
    tenant_ids = [str(row["tenant_id"]) for row in scope_rows]
    workspace_ids = [str(row["workspace_id"]) for row in scope_rows]
    cursor = await conn.execute(
        """
        with candidate_scopes as (
          select *
          from unnest(%s::text[], %s::text[]) as scope(tenant_id, workspace_id)
        ),
        candidate_rows as (
          select selected.id,
                 selected.expires_at,
                 selected.created_at,
                 selected.tenant_id,
                 selected.workspace_id,
                 selected.scope_rank
          from candidate_scopes scope
          cross join lateral (
            select locked_rows.id,
                   locked_rows.expires_at,
                   locked_rows.created_at,
                   locked_rows.tenant_id,
                   locked_rows.workspace_id,
                   row_number() over (
                     order by locked_rows.expires_at asc, locked_rows.created_at asc, locked_rows.id asc
                   ) as scope_rank
            from (
              select id, expires_at, created_at, tenant_id, workspace_id
              from memory_records
              where memory_records.tenant_id = scope.tenant_id
                and memory_records.workspace_id = scope.workspace_id
                and status = 'active'
                and deleted_at is null
                and expires_at is not null
                and expires_at <= now()
              order by expires_at asc, created_at asc, id asc
              limit %s
              for update skip locked
            ) locked_rows
          ) selected
          order by case when selected.scope_rank = 1 then 0 else 1 end,
                   selected.expires_at asc,
                   selected.created_at asc,
                   selected.tenant_id asc,
                   selected.workspace_id asc,
                   selected.id asc
          limit %s
        )
        update memory_records
        set status = 'deleted',
            deleted_at = now(),
            updated_at = now()
        where id in (select id from candidate_rows)
        returning id, tenant_id, workspace_id, user_id, agent_id, session_id,
                  record_type, status, expires_at, deleted_at, created_at, updated_at
        """,
        (tenant_ids, workspace_ids, per_scope_limit, limit),
    )
    return list(await cursor.fetchall())


async def _list_expired_memory_cleanup_scopes(
    conn: AsyncConnection,
    *,
    after_tenant_id: str | None = None,
    after_workspace_id: str | None = None,
    before_or_at_tenant_id: str | None = None,
    before_or_at_workspace_id: str | None = None,
    limit: int,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select tenant_id, workspace_id
        from memory_records
        where status = 'active'
          and deleted_at is null
          and expires_at is not null
          and expires_at <= now()
          and (
            %s::text is null
            or (tenant_id, workspace_id) > (%s, %s)
          )
          and (
            %s::text is null
            or (tenant_id, workspace_id) <= (%s, %s)
          )
        group by tenant_id, workspace_id
        order by tenant_id asc, workspace_id asc
        limit %s
        """,
        (
            after_tenant_id,
            after_tenant_id,
            after_workspace_id,
            before_or_at_tenant_id,
            before_or_at_tenant_id,
            before_or_at_workspace_id,
            int(limit),
        ),
    )
    return list(await cursor.fetchall())


async def create_tool_permission_request(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    trace_id: str,
    tool_id: str,
    tool_call_id: str,
    action: str,
    risk_level: str,
    write_capable: bool,
    reason: str,
    request_payload_json: dict[str, Any],
    expires_in_seconds: float = TOOL_PERMISSION_REQUEST_TTL_SECONDS,
    absolute_expires_at: datetime | None = None,
) -> dict[str, Any]:
    """Create one pending, expiring permission request only for an open run."""
    request_id = new_id("tpr")
    cursor = await conn.execute(
        """
        with eligible_run as (
          select id
          from runs
          where tenant_id = %s
            and id = %s
            and status = 'running'
            and cancel_requested_at is null
            and permission_terminalization_target is null
          for update
        )
        insert into run_tool_permission_requests(
          id, tenant_id, workspace_id, user_id, session_id, run_id, trace_id,
          tool_id, tool_call_id, action, risk_level, write_capable, reason, request_payload_json, expires_at
        )
        select %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb,
               coalesce(%s::timestamptz, clock_timestamp() + (%s * interval '1 second'))
        from eligible_run
        returning id
        """,
        (
            tenant_id,
            run_id,
            request_id,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            run_id,
            trace_id,
            tool_id,
            tool_call_id,
            action,
            risk_level,
            write_capable,
            reason,
            dumps_json(request_payload_json),
            absolute_expires_at,
            max(float(expires_in_seconds), 0.0),
        ),
    )
    if await cursor.fetchone() is None:
        raise RepositoryConflictError("tool_permission_run_not_open")
    return {
        "id": request_id,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "session_id": session_id,
        "run_id": run_id,
        "trace_id": trace_id,
        "tool_id": tool_id,
        "tool_call_id": tool_call_id,
        "action": action,
        "risk_level": risk_level,
        "write_capable": write_capable,
        "status": "pending",
        "reason": reason,
        "request_payload_json": request_payload_json,
    }


async def expire_tool_permission_request(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    request_id: str,
) -> dict[str, Any] | None:
    """Mark one still-pending request expired when its deadline has elapsed."""
    rows = await expire_pending_tool_permission_requests(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
        request_id=request_id,
        limit=1,
    )
    return rows[0] if rows else None


async def expire_pending_tool_permission_requests(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    limit: int = TOOL_PERMISSION_EXPIRY_BATCH_LIMIT,
) -> list[dict[str, Any]]:
    """Terminalize one bounded, scoped batch of expired requests with audit facts."""
    bounded_limit = max(1, min(int(limit), TOOL_PERMISSION_EXPIRY_BATCH_LIMIT))
    cursor = await conn.execute(
        """
        with locked_runs as materialized (
          select runs.id
          from runs
          where runs.tenant_id = %s
            and runs.permission_terminalization_target is null
            and (%s::text is null or runs.id = %s)
            and exists (
              select 1
              from run_tool_permission_requests as candidate
              where candidate.tenant_id = runs.tenant_id
                and candidate.run_id = runs.id
                and candidate.status = 'pending'
                and (candidate.expires_at is null or candidate.expires_at <= clock_timestamp())
                and (%s::text is null or candidate.user_id = %s)
                and (%s::text is null or candidate.id = %s)
            )
          order by runs.id asc
          limit %s
          for update skip locked
        ), expired_requests as (
          select permission_request.id
          from run_tool_permission_requests as permission_request
          join locked_runs on locked_runs.id = permission_request.run_id
          where permission_request.tenant_id = %s
            and permission_request.status = 'pending'
            and (permission_request.expires_at is null or permission_request.expires_at <= clock_timestamp())
            and (%s::text is null or permission_request.user_id = %s)
            and (%s::text is null or permission_request.run_id = %s)
            and (%s::text is null or permission_request.id = %s)
          order by permission_request.expires_at asc, permission_request.id asc
          limit %s
          for update of permission_request skip locked
        )
        update run_tool_permission_requests as permission_request
        set status = 'expired',
            reason = 'permission_request_expired',
            expires_at = coalesce(permission_request.expires_at, clock_timestamp()),
            updated_at = clock_timestamp()
        from expired_requests
        where permission_request.id = expired_requests.id
        returning permission_request.*
        """,
        (
            tenant_id,
            run_id,
            run_id,
            user_id,
            user_id,
            request_id,
            request_id,
            bounded_limit,
            tenant_id,
            user_id,
            user_id,
            run_id,
            run_id,
            request_id,
            request_id,
            bounded_limit,
        ),
    )
    rows = list(await cursor.fetchall())
    for row in rows:
        await _record_tool_permission_terminalization(
            conn,
            tenant_id=tenant_id,
            run_id=str(row.get("run_id") or run_id or ""),
            row=row,
            terminal_status="expired",
            terminal_reason="permission_request_expired",
            message="工具权限请求已过期",
        )
    return rows


async def _record_tool_permission_terminalization(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    row: dict[str, Any],
    terminal_status: str,
    terminal_reason: str,
    message: str,
) -> None:
    """Write the public terminal fact and its tenant/run/request audit record."""
    request_id = str(row["id"])
    trace_id = str(row.get("trace_id") or "")
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=trace_id,
        event_type="tool_permission_terminalized",
        stage="tool_policy",
        message=message,
        payload={
            "visible_to_user": True,
            "permission_request_id": request_id,
            "tool_id": str(row.get("tool_id") or "tool"),
            "tool_call_id": str(row.get("tool_call_id") or ""),
            "action": str(row.get("action") or "execute"),
            "risk_level": str(row.get("risk_level") or "low"),
            "write_capable": bool(row.get("write_capable")),
            "status": terminal_status,
            "reason": terminal_reason,
        },
    )
    await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=None,
        action="tool_permission.terminalized",
        target_type="tool_permission_request",
        target_id=request_id,
        trace_id=trace_id,
        payload_json={
            "run_id": run_id,
            "request_user_id": str(row.get("user_id") or ""),
            "tool_id": str(row.get("tool_id") or ""),
            "tool_call_id": str(row.get("tool_call_id") or ""),
            "decision": str(row.get("decision") or ""),
            "status": terminal_status,
            "reason": terminal_reason,
        },
    )


async def terminalize_pending_tool_permission_requests(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    request_id: str | None = None,
    terminal_status: str,
    terminal_reason: str,
) -> list[dict[str, Any]]:
    """Close one run-first bounded batch with durable per-request facts."""
    if terminal_status not in {"invalidated", "failed", "cancelled", "expired"}:
        raise ValueError("invalid_tool_permission_terminal_status")
    cursor = await conn.execute(
        """
        with locked_run as materialized (
          select id
          from runs
          where tenant_id = %s and id = %s
          for update
        ), terminalized_requests as (
          select permission_request.id
          from run_tool_permission_requests as permission_request
          join locked_run on locked_run.id = permission_request.run_id
          where permission_request.tenant_id = %s
            and permission_request.run_id = %s
            and permission_request.status in ('pending', 'decided')
            and (%s::text is null or permission_request.id = %s)
          order by permission_request.created_at asc, permission_request.id asc
          limit %s
          for update of permission_request skip locked
        )
        update run_tool_permission_requests as permission_request
        set status = %s,
            reason = %s,
            expires_at = coalesce(expires_at, clock_timestamp()),
            updated_at = clock_timestamp()
        from terminalized_requests
        where permission_request.id = terminalized_requests.id
        returning permission_request.id, permission_request.user_id, permission_request.trace_id,
                  permission_request.tool_id, permission_request.tool_call_id, permission_request.action,
                  permission_request.risk_level, permission_request.write_capable, permission_request.decision
        """,
        (
            tenant_id,
            run_id,
            tenant_id,
            run_id,
            request_id,
            request_id,
            TOOL_PERMISSION_TERMINALIZATION_BATCH_LIMIT,
            terminal_status,
            terminal_reason,
        ),
    )
    rows = list(await cursor.fetchall())
    for row in rows:
        await _record_tool_permission_terminalization(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            row=row,
            terminal_status=terminal_status,
            terminal_reason=terminal_reason,
            message="工具权限请求已终结",
        )
    return rows


async def list_runs_requiring_tool_permission_terminalization(
    conn: AsyncConnection,
    *,
    limit: int = TOOL_PERMISSION_TERMINALIZATION_MAINTENANCE_LIMIT,
) -> list[dict[str, Any]]:
    """Return a bounded set of durable permission-drain work items for worker maintenance."""

    bounded_limit = max(1, min(int(limit), TOOL_PERMISSION_TERMINALIZATION_MAINTENANCE_LIMIT))
    cursor = await conn.execute(
        """
        select runs.tenant_id, runs.id as run_id
        from runs
        where runs.permission_terminalization_target is not null
           or (
             runs.status in ('succeeded', 'failed', 'cancelled')
             and exists (
               select 1
               from run_tool_permission_requests as permission_request
               where permission_request.tenant_id = runs.tenant_id
                 and permission_request.run_id = runs.id
                 and permission_request.status in ('pending', 'decided')
             )
           )
           or exists (
             select 1
             from run_tool_permission_requests as permission_request
             where permission_request.tenant_id = runs.tenant_id
               and permission_request.run_id = runs.id
               and permission_request.status = 'pending'
               and (permission_request.expires_at is null or permission_request.expires_at <= clock_timestamp())
           )
        order by coalesce(runs.finished_at, runs.started_at, runs.created_at) asc, runs.tenant_id asc, runs.id asc
        limit %s
        for update skip locked
        """,
        (bounded_limit,),
    )
    return list(await cursor.fetchall())


async def list_multi_agent_terminal_children_requiring_reconciliation(
    conn: AsyncConnection,
    *,
    limit: int = TOOL_PERMISSION_TERMINALIZATION_MAINTENANCE_LIMIT,
) -> list[dict[str, Any]]:
    """Return bounded terminal children whose durable handed-off parent step still needs reconciliation."""

    bounded_limit = max(1, min(int(limit), TOOL_PERMISSION_TERMINALIZATION_MAINTENANCE_LIMIT))
    cursor = await conn.execute(
        """
        select child.tenant_id, child.id as run_id, child.status
        from runs child
        join run_steps parent_step
          on parent_step.tenant_id = child.tenant_id
         and parent_step.run_id = child.copied_from_run_id
         and parent_step.payload_json->>'dispatch_child_run_id' = child.id
        where child.copied_from_run_id is not null
          and child.status in ('succeeded', 'failed', 'cancelled')
          and parent_step.payload_json->>'dispatch_state' = 'handed_off'
        order by coalesce(child.finished_at, child.started_at, child.created_at) asc,
                 child.tenant_id asc, child.id asc
        limit %s
        for update of child, parent_step skip locked
        """,
        (bounded_limit,),
    )
    return list(await cursor.fetchall())


async def list_multi_agent_parent_runs_requiring_finalization(
    conn: AsyncConnection,
    *,
    limit: int = TOOL_PERMISSION_TERMINALIZATION_MAINTENANCE_LIMIT,
) -> list[dict[str, Any]]:
    """Return bounded, ready multi-agent parents whose exact-once rollup facts are still absent."""

    bounded_limit = max(1, min(int(limit), TOOL_PERMISSION_TERMINALIZATION_MAINTENANCE_LIMIT))
    cursor = await conn.execute(
        """
        select parent.tenant_id, parent.id as run_id
        from runs parent
        where parent.copied_from_run_id is null
          and (
            parent.status in ('running', 'succeeded', 'failed', 'cancelled')
            or (parent.status = 'queued' and parent.cancel_requested_at is not null)
          )
          and (
            parent.input_json#>>'{input,execution_mode}' = 'multi_agent'
            or parent.input_json->>'execution_mode' = 'multi_agent'
          )
          and exists (
            select 1 from run_steps parent_step
            where parent_step.tenant_id = parent.tenant_id
              and parent_step.run_id = parent.id
          )
          and not exists (
            select 1 from run_steps parent_step
            where parent_step.tenant_id = parent.tenant_id
              and parent_step.run_id = parent.id
              and parent_step.status not in ('succeeded', 'failed', 'cancelled')
          )
          and (
            not exists (
              select 1 from run_events parent_event
              where parent_event.tenant_id = parent.tenant_id
                and parent_event.run_id = parent.id
                and parent_event.event_type = 'multi_agent_parent_finalized'
            )
            or not exists (
              select 1 from audit_logs parent_audit
              where parent_audit.tenant_id = parent.tenant_id
                and parent_audit.target_type = 'run'
                and parent_audit.target_id = parent.id
                and parent_audit.action = 'run.multi_agent.parent.finalize'
            )
          )
        order by coalesce(parent.finished_at, parent.started_at, parent.created_at) asc,
                 parent.tenant_id asc, parent.id asc
        limit %s
        for update of parent skip locked
        """,
        (bounded_limit,),
    )
    return list(await cursor.fetchall())


async def _has_unterminalized_run_tool_permissions(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> bool:
    """Return whether a staged run still has an authority-bearing permission row."""

    cursor = await conn.execute(
        """
        select exists(
          select 1
          from run_tool_permission_requests
          where tenant_id = %s
            and run_id = %s
            and status in ('pending', 'decided')
        ) as has_unterminalized
        """,
        (tenant_id, run_id),
    )
    row = await cursor.fetchone()
    return bool(row and row.get("has_unterminalized"))


async def progress_run_tool_permission_terminalization(
    conn: AsyncConnection, *, tenant_id: str, run_id: str
) -> dict[str, Any] | None:
    """Advance one durable, bounded terminalization transaction and finalize only when clear."""

    cursor = await conn.execute(
        """
        select id, trace_id, status, permission_terminalization_target,
               user_id, permission_terminalization_reason, permission_terminalization_result_json,
               permission_terminalization_error_code, permission_terminalization_error_message,
               latency_ms, input_token_count, output_token_count, total_token_count,
               estimated_cost_minor
        from runs
        where tenant_id = %s and id = %s
        for update
        """,
        (tenant_id, run_id),
    )
    staged = await cursor.fetchone()
    if staged is None:
        return None
    target_status = str(staged.get("permission_terminalization_target") or "")
    if target_status not in {"failed", "cancel_requested", "cancelled"}:
        run_status = str(staged.get("status") or "")
        if run_status in {"succeeded", "failed", "cancelled"}:
            terminal_status = "invalidated" if run_status == "succeeded" else run_status
            await terminalize_pending_tool_permission_requests(
                conn, tenant_id=tenant_id, run_id=run_id, terminal_status=terminal_status,
                terminal_reason="legacy_terminal_run_permission_drain",
            )
            return runs_api.RunTerminalizationProgress(
                completed=not await _has_unterminalized_run_tool_permissions(conn, tenant_id=tenant_id, run_id=run_id),
                status=run_status,
            )
        if run_status == "running":
            expired_rows = await expire_pending_tool_permission_requests(
                conn, tenant_id=tenant_id, run_id=run_id)
            if expired_rows:
                return runs_api.RunTerminalizationProgress(completed=False, status="running")
        return runs_api.RunTerminalizationProgress(completed=False, status=None)
    terminal_reason = str(staged.get("permission_terminalization_reason") or "run_terminalized")
    await terminalize_pending_tool_permission_requests(
        conn, tenant_id=tenant_id, run_id=run_id,
        terminal_status="cancelled" if target_status == "cancel_requested" else target_status,
        terminal_reason=terminal_reason,
    )
    if await _has_unterminalized_run_tool_permissions(conn, tenant_id=tenant_id, run_id=run_id):
        return runs_api.RunTerminalizationProgress(completed=False, status=target_status)
    if target_status == "cancel_requested":
        cursor = await conn.execute(
            """
            update runs
            set permission_terminalization_target = null,
                permission_terminalization_reason = '',
                permission_terminalization_result_json = '{}'::jsonb,
                permission_terminalization_error_code = null,
                permission_terminalization_error_message = null
            where tenant_id = %s
              and id = %s
              and permission_terminalization_target = 'cancel_requested'
              and not exists (
                select 1 from run_tool_permission_requests
                where run_tool_permission_requests.tenant_id = runs.tenant_id
                  and run_tool_permission_requests.run_id = runs.id
                  and run_tool_permission_requests.status in ('pending', 'decided')
              )
            returning id, status
            """,
            (tenant_id, run_id),
        )
    elif target_status == "failed":
        cursor = await conn.execute(
            """
            update runs
            set status = 'failed',
                result_json = permission_terminalization_result_json,
                finished_at = now(),
                error_code = permission_terminalization_error_code,
                error_message = permission_terminalization_error_message,
                permission_terminalization_target = null,
                permission_terminalization_reason = '',
                permission_terminalization_result_json = '{}'::jsonb,
                permission_terminalization_error_code = null,
                permission_terminalization_error_message = null
            where tenant_id = %s
              and id = %s
              and permission_terminalization_target = 'failed'
              and not exists (
                select 1 from run_tool_permission_requests
                where run_tool_permission_requests.tenant_id = runs.tenant_id
                  and run_tool_permission_requests.run_id = runs.id
                  and run_tool_permission_requests.status in ('pending', 'decided')
              )
            returning id, status
            """,
            (tenant_id, run_id),
        )
    else:
        cursor = await conn.execute(
            """
            update runs
            set status = 'cancelled',
                result_json = permission_terminalization_result_json,
                finished_at = now(),
                error_code = null,
                error_message = null,
                permission_terminalization_target = null,
                permission_terminalization_reason = '',
                permission_terminalization_result_json = '{}'::jsonb,
                permission_terminalization_error_code = null,
                permission_terminalization_error_message = null
            where tenant_id = %s
              and id = %s
              and permission_terminalization_target = 'cancelled'
              and not exists (
                select 1 from run_tool_permission_requests
                where run_tool_permission_requests.tenant_id = runs.tenant_id
                  and run_tool_permission_requests.run_id = runs.id
                  and run_tool_permission_requests.status in ('pending', 'decided')
              )
            returning id, status
            """,
            (tenant_id, run_id),
        )
    finalized = await cursor.fetchone()
    if finalized is None:
        return runs_api.RunTerminalizationProgress(completed=False, status=target_status)
    if target_status not in {"failed", "cancelled"}:
        return runs_api.RunTerminalizationProgress(completed=False, status="cancel_requested")
    result_payload = (
        staged.get("permission_terminalization_result_json")
        if isinstance(staged.get("permission_terminalization_result_json"), dict)
        else {}
    )
    result_latency, result_input, result_output, result_total, result_cost = _result_observability_values(result_payload)
    latency_ms = result_latency or _coerce_int(staged.get("latency_ms"))
    input_tokens = result_input or _coerce_int(staged.get("input_token_count"))
    output_tokens = result_output or _coerce_int(staged.get("output_token_count"))
    total_tokens = result_total or _coerce_int(staged.get("total_token_count"))
    estimated_cost_minor = result_cost or _coerce_int(staged.get("estimated_cost_minor"))
    artifact_cursor = await conn.execute(
        "select count(*) as artifact_count from artifacts where tenant_id = %s and run_id = %s",
        (tenant_id, run_id),
    )
    artifact_row = await artifact_cursor.fetchone()
    artifact_count = _coerce_int(artifact_row.get("artifact_count")) if artifact_row is not None else 0
    retired_admission_rejection = target_status == "failed" and terminal_reason == RETIRED_PLATFORM_MULTI_AGENT_TERMINAL_REASON
    if target_status == "failed":
        await _fail_open_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
        event_type, stage, message = (
            ("run_failed", "control", "Run rejected: persisted input uses retired platform orchestration.")
            if retired_admission_rejection else ("run_failed", "worker", "Run failed")
        )
    elif target_status == "cancelled":
        await _cancel_open_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
        event_type, stage, message = "run_cancelled", "control", "任务已取消"
    event_payload = {
        "visible_to_user": not retired_admission_rejection,
        "severity": "error" if target_status == "failed" else "warning",
        "artifact_count": artifact_count,
        "result_status": target_status,
        "result": sanitize_public_payload(result_payload),
    }
    if target_status == "failed" and staged.get("permission_terminalization_error_code"):
        event_payload["error_code"] = str(staged["permission_terminalization_error_code"])
        safe_error_message = sanitize_public_text(staged.get("permission_terminalization_error_message"))
        if safe_error_message:
            event_payload["error_message"] = safe_error_message
    if retired_admission_rejection:
        event_payload["retryable"] = False
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=staged.get("trace_id"),
        event_type=event_type,
        stage=stage,
        message=message,
        payload=event_payload,
        visible_to_user=not retired_admission_rejection,
        latency_ms=latency_ms,
        input_token_count=input_tokens,
        output_token_count=output_tokens,
        total_token_count=total_tokens,
        estimated_cost_minor=estimated_cost_minor,
    )
    audit_payload = {
        "reason": terminal_reason,
        "artifact_count": artifact_count,
        "latency_ms": latency_ms,
        "input_token_count": input_tokens,
        "output_token_count": output_tokens,
        "total_token_count": total_tokens,
        "estimated_cost_minor": estimated_cost_minor,
        "error_code": staged.get("permission_terminalization_error_code"),
    }
    if retired_admission_rejection:
        audit_payload["retryable"] = False
    await append_audit_log(
        conn, tenant_id=tenant_id,
        user_id=staged.get("user_id") if retired_admission_rejection else None,
        action="run.admission.rejected" if retired_admission_rejection else f"run.{target_status}",
        target_type="run", target_id=run_id, trace_id=staged.get("trace_id"), payload_json=audit_payload,
    )
    from app.streaming.redis import ensure_run_terminal_intent
    await ensure_run_terminal_intent(
        conn, tenant_id=tenant_id, run_id=run_id, status=target_status
    )
    return runs_api.RunTerminalizationProgress(completed=True, status=target_status, did_transition=True, needs_reconcile=True)


async def has_pending_tool_permission_requests(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> bool:
    """Return whether an open run still has a permission gate that blocks success."""
    await expire_pending_tool_permission_requests(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    cursor = await conn.execute(
        """
        select exists(
          select 1 from run_tool_permission_requests
          where tenant_id = %s and run_id = %s and status = 'pending'
        ) as has_pending
        """,
        (tenant_id, run_id),
    )
    row = await cursor.fetchone()
    return bool(row and row.get("has_pending"))


async def get_tool_permission_request(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    request_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select *
        from run_tool_permission_requests
        where tenant_id = %s and user_id = %s and run_id = %s and id = %s
        """,
        (tenant_id, user_id, run_id, request_id),
    )
    return await cursor.fetchone()


async def get_tool_permission_request_for_tenant(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    request_id: str,
) -> dict[str, Any] | None:
    """Fetch one permission request for tenant-scoped administrator governance."""
    cursor = await conn.execute(
        """
        select permission_request.*, runs.status as run_status,
               runs.cancel_requested_at, runs.permission_terminalization_target
        from run_tool_permission_requests as permission_request
        join runs on runs.tenant_id = permission_request.tenant_id and runs.id = permission_request.run_id
        where permission_request.tenant_id = %s and permission_request.run_id = %s and permission_request.id = %s
        """,
        (tenant_id, run_id, request_id),
    )
    return await cursor.fetchone()


async def get_tool_permission_request_by_id(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    request_id: str,
) -> dict[str, Any] | None:
    """Fetch one current-user permission request without requiring run context."""
    cursor = await conn.execute(
        """
        select *
        from run_tool_permission_requests
        where tenant_id = %s and user_id = %s and id = %s
        """,
        (tenant_id, user_id, request_id),
    )
    return await cursor.fetchone()


async def get_tool_permission_request_by_id_for_tenant(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    request_id: str,
) -> dict[str, Any] | None:
    """Fetch one tenant request for the admin inbox without owner filtering."""
    cursor = await conn.execute(
        """
        select permission_request.*, runs.status as run_status,
               runs.cancel_requested_at, runs.permission_terminalization_target
        from run_tool_permission_requests as permission_request
        join runs on runs.tenant_id = permission_request.tenant_id and runs.id = permission_request.run_id
        where permission_request.tenant_id = %s and permission_request.id = %s
        """,
        (tenant_id, request_id),
    )
    return await cursor.fetchone()


async def list_tool_permission_inbox(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    status: str = "pending",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List current-user permission requests for the standalone approval inbox."""
    await expire_pending_tool_permission_requests(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    cursor = await conn.execute(
        """
        select permission_request.*, runs.status as run_status,
               runs.cancel_requested_at, runs.permission_terminalization_target
        from run_tool_permission_requests as permission_request
        join runs on runs.tenant_id = permission_request.tenant_id and runs.id = permission_request.run_id
        where permission_request.tenant_id = %s and permission_request.user_id = %s
          and (%s = 'all' or permission_request.status = %s)
          and (permission_request.status <> 'pending' or permission_request.expires_at > clock_timestamp())
        order by permission_request.created_at desc, permission_request.id desc
        limit %s
        """,
        (tenant_id, user_id, status, status, int(limit)),
    )
    return list(await cursor.fetchall())


async def list_tool_permission_inbox_for_tenant(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    status: str = "pending",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List tenant permission requests for the administrator governance inbox."""
    await expire_pending_tool_permission_requests(conn, tenant_id=tenant_id)
    cursor = await conn.execute(
        """
        select permission_request.*, runs.status as run_status,
               runs.cancel_requested_at, runs.permission_terminalization_target
        from run_tool_permission_requests as permission_request
        join runs on runs.tenant_id = permission_request.tenant_id and runs.id = permission_request.run_id
        where permission_request.tenant_id = %s
          and (%s = 'all' or permission_request.status = %s)
          and (permission_request.status <> 'pending' or permission_request.expires_at > clock_timestamp())
        order by permission_request.created_at desc, permission_request.id desc
        limit %s
        """,
        (tenant_id, status, status, int(limit)),
    )
    return list(await cursor.fetchall())


async def decide_tool_permission_request(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    request_id: str,
    decision: str,
    reason: str,
    decision_payload_json: dict[str, Any],
    expires_in_seconds: int = int(TOOL_PERMISSION_REQUEST_TTL_SECONDS),
) -> dict[str, Any] | None:
    # Kept for caller compatibility: a decision must never extend the TTL
    # established when this exact request was created.
    _ = expires_in_seconds
    expired = await expire_tool_permission_request(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
        request_id=request_id,
    )
    if expired is not None:
        return None
    cursor = await conn.execute(
        """
        with executable_run as (
          select id
          from runs
          where tenant_id = %s
            and id = %s
            and status = 'running'
            and cancel_requested_at is null
            and permission_terminalization_target is null
          for update
        )
        update run_tool_permission_requests as permission_request
        set status = 'decided',
            decision = %s,
            reason = %s,
            decision_payload_json = %s::jsonb,
            expires_at = permission_request.expires_at,
            decided_at = clock_timestamp(),
            updated_at = clock_timestamp()
        from executable_run
        where permission_request.tenant_id = %s
          and permission_request.user_id = %s
          and permission_request.run_id = %s
          and executable_run.id = permission_request.run_id
          and permission_request.id = %s
          and permission_request.status = 'pending'
          and permission_request.expires_at > clock_timestamp()
        returning permission_request.*
        """,
        (
            tenant_id,
            run_id,
            decision,
            reason,
            dumps_json(decision_payload_json),
            tenant_id,
            user_id,
            run_id,
            request_id,
        ),
    )
    return await cursor.fetchone()


async def get_exact_tool_permission_decision(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    tool_id: str,
    action: str = "execute",
    tool_call_id: str | None = None,
    request_payload_json: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Fetch a decided permission row only for the exact call or stable request fingerprint."""
    exact_clauses: list[str] = []
    params: list[Any] = [tenant_id, user_id, run_id, tool_id, action]
    if tool_call_id:
        exact_clauses.append("(decision in ('allow_once', 'deny') and tool_call_id = %s)")
        params.append(tool_call_id)
    request_payload = request_payload_json if isinstance(request_payload_json, dict) else {}
    for fingerprint_key in ("command_sha256", "input_sha256"):
        fingerprint_value = request_payload.get(fingerprint_key)
        if isinstance(fingerprint_value, str) and fingerprint_value:
            exact_clauses.append("(decision = 'allow_for_run' and request_payload_json ->> %s = %s)")
            params.extend([fingerprint_key, fingerprint_value])
            break
    if not exact_clauses:
        return None
    exact_filter = f"and ({' or '.join(exact_clauses)})" if exact_clauses else ""
    cursor = await conn.execute(
        f"""
        with executable_run as (
          select id
          from runs
          where tenant_id = %s
            and id = %s
            and status = 'running'
            and cancel_requested_at is null
            and permission_terminalization_target is null
          for update
        )
        select permission_request.*
        from run_tool_permission_requests as permission_request
        join executable_run on executable_run.id = permission_request.run_id
        where permission_request.tenant_id = %s
          and permission_request.user_id = %s
          and permission_request.run_id = %s
          and permission_request.tool_id = %s
          and permission_request.action = %s
          and permission_request.status = 'decided'
          and permission_request.expires_at > clock_timestamp()
          {exact_filter}
        order by permission_request.decided_at desc, permission_request.updated_at desc, permission_request.created_at desc
        limit 1
        """,
        tuple([tenant_id, run_id, *params]),
    )
    return await cursor.fetchone()


async def get_latest_tool_permission_decision(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    tool_id: str,
    action: str = "execute",
    tool_call_id: str | None = None,
    request_payload_json: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Compatibility wrapper for callers that still use the legacy function name."""
    return await get_exact_tool_permission_decision(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
        tool_id=tool_id,
        action=action,
        tool_call_id=tool_call_id,
        request_payload_json=request_payload_json,
    )


async def consume_tool_permission_decision(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    request_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        with executable_run as (
          select id
          from runs
          where tenant_id = %s
            and id = %s
            and status = 'running'
            and cancel_requested_at is null
            and permission_terminalization_target is null
          for update
        )
        update run_tool_permission_requests as permission_request
        set status = 'consumed',
            updated_at = clock_timestamp()
        from executable_run
        where permission_request.tenant_id = %s
          and permission_request.user_id = %s
          and permission_request.run_id = %s
          and executable_run.id = permission_request.run_id
          and permission_request.id = %s
          and permission_request.decision = 'allow_once'
          and permission_request.status = 'decided'
          and permission_request.expires_at > clock_timestamp()
        returning permission_request.*
        """,
        (tenant_id, run_id, tenant_id, user_id, run_id, request_id),
    )
    return await cursor.fetchone()


async def get_sandbox_lease(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    lease_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select *
        from sandbox_leases
        where tenant_id = %s and user_id = %s and run_id = %s and id = %s for update
        """,
        (tenant_id, user_id, run_id, lease_id),
    )
    return await cursor.fetchone()


async def renew_sandbox_lease(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    lease_id: str,
    ttl_seconds: int,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update sandbox_leases
        set heartbeat_at = now(),
            expires_at = now() + (%s * interval '1 second'),
            updated_at = now()
        where tenant_id = %s
          and user_id = %s
          and run_id = %s
          and id = %s
          and status = 'active'
          and (expires_at is null or expires_at > now())
        returning *
        """,
        (int(ttl_seconds), tenant_id, user_id, run_id, lease_id),
    )
    return await cursor.fetchone()


async def release_sandbox_lease(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
    lease_id: str,
    reason: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update sandbox_leases
        set status = 'released',
            released_at = coalesce(released_at, now()),
            release_reason = %s,
            updated_at = now()
        where tenant_id = %s
          and user_id = %s
          and run_id = %s
          and id = %s
          and status = 'active'
        returning *
        """,
        (reason, tenant_id, user_id, run_id, lease_id),
    )
    return await cursor.fetchone()


async def release_active_sandbox_leases_for_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    reason: str,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        update sandbox_leases
        set status = 'released',
            released_at = coalesce(released_at, now()),
            release_reason = %s,
            updated_at = now()
        where tenant_id = %s
          and run_id = %s
          and status = 'active'
        returning *
        """,
        (reason, tenant_id, run_id),
    )
    return list(await cursor.fetchall())


async def list_active_sandbox_leases_for_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    """Return only active sandbox lease rows for runtime cleanup."""
    cursor = await conn.execute(
        """
        select *
        from sandbox_leases
        where tenant_id = %s
          and run_id = %s
          and status = 'active'
        order by created_at asc
        """,
        (tenant_id, run_id),
    )
    return list(await cursor.fetchall())


async def list_current_sandbox_runtime_leases_for_attempt(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    attempt_id: str,
) -> list[dict[str, Any]]:
    return await _run_event_repository.list_current_sandbox_runtime_leases_for_attempt(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=attempt_id,
    )


list_terminal_sandbox_runtime_leases_for_attempt = _run_event_repository.list_terminal_sandbox_runtime_leases_for_attempt


async def list_sandbox_leases_for_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    """Return same-run sandbox lease rows for admin runtime provenance."""
    cursor = await conn.execute(
        """
        select *
        from sandbox_leases
        where tenant_id = %s
          and run_id = %s
        order by created_at asc
        """,
        (tenant_id, run_id),
    )
    return list(await cursor.fetchall())


async def release_stopped_sandbox_leases_for_cancel(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    reason: str,
    lease_ids: list[str],
    trace_id: str | None = None,
    requested_by_role: str | None = None,
) -> list[dict[str, Any]]:
    """Release leases after their runtime containers have been stopped."""
    if not lease_ids:
        return []
    cursor = await conn.execute(
        """
        update sandbox_leases
        set status = 'released',
            released_at = coalesce(released_at, now()),
            release_reason = %s,
            updated_at = now()
        where tenant_id = %s
          and run_id = %s
          and id = any(%s)
          and status = 'active'
        returning *
        """,
        (reason, tenant_id, run_id, lease_ids),
    )
    released_leases = list(await cursor.fetchall())
    for lease in released_leases:
        payload: dict[str, Any] = {
            "visible_to_user": True,
            "lease_id": lease.get("id"),
            "reason": reason,
        }
        if requested_by_role:
            payload["requested_by_role"] = requested_by_role
        await append_event(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=lease.get("trace_id") or trace_id,
            event_type="sandbox_lease_released",
            stage="sandbox",
            message="已因取消释放 Sandbox 租约",
            payload=payload,
        )
    return released_leases


async def record_sandbox_runtime_cleanup_outcome(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    trace_id: str | None = None,
    user_id: str | None = None,
    requested_by_role: str | None = None,
    reason: str,
    status: str,
    lease_ids: list[str] | None = None,
    failures: list[dict[str, str]] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "visible_to_user": False,
        "reason": reason,
        "status": status,
        "lease_ids": lease_ids or [],
        "failure_count": len(failures or []),
    }
    if requested_by_role:
        payload["requested_by_role"] = requested_by_role
    if failures:
        payload["failures"] = failures
    event_type = "sandbox_runtime_cleanup_failed" if status == "failed" else "sandbox_runtime_cleanup_succeeded"
    message = "Sandbox runtime cleanup failed" if status == "failed" else "Sandbox runtime cleanup succeeded"
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=trace_id,
        event_type=event_type,
        stage="sandbox",
        message=message,
        payload=payload,
    )
    await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action=f"sandbox.runtime.cleanup.{status}",
        target_type="run",
        target_id=run_id,
        trace_id=trace_id,
        payload_json={
            "run_id": run_id,
            "reason": reason,
            "status": status,
            "lease_ids": lease_ids or [],
            "failures": failures or [],
            "requested_by_role": requested_by_role,
        },
    )


def _sandbox_lease_release_message(reason: str) -> str:
    if reason == "expired":
        return "已释放过期 Sandbox 租约"
    if reason in {"cancel_requested", "admin_cancel_requested"}:
        return "已因取消释放 Sandbox 租约"
    return "已释放 Sandbox 租约"


async def release_stopped_sandbox_leases(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    reason: str,
    lease_ids: list[str],
    trace_id: str | None = None,
) -> list[dict[str, Any]]:
    """Release DB leases only after their runtime stop operation has succeeded."""
    if not lease_ids:
        return []
    cursor = await conn.execute(
        """
        update sandbox_leases
        set status = 'released',
            released_at = coalesce(released_at, now()),
            release_reason = %s,
            updated_at = now()
        where tenant_id = %s
          and id = any(%s)
          and status = 'active'
        returning *
        """,
        (reason, tenant_id, lease_ids),
    )
    released_leases = list(await cursor.fetchall())
    for lease in released_leases:
        await append_event(
            conn,
            tenant_id=tenant_id,
            run_id=str(lease["run_id"]),
            trace_id=lease.get("trace_id") or trace_id,
            event_type="sandbox_lease_released",
            stage="sandbox",
            message=_sandbox_lease_release_message(reason),
            payload={
                "visible_to_user": True,
                "lease_id": lease.get("id"),
                "reason": reason,
            },
        )
    return released_leases


async def list_expired_active_sandbox_leases(
    conn: AsyncConnection,
    *,
    tenant_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return expired active lease rows so callers can stop runtime providers first."""
    cursor = await conn.execute(
        """
        select *
        from sandbox_leases
        where (%s::text is null or tenant_id = %s)
          and status = 'active'
          and expires_at is not null
          and expires_at <= now()
        order by expires_at asc, created_at asc
        limit %s
        """,
        (tenant_id, tenant_id, limit),
    )
    return list(await cursor.fetchall())


async def cleanup_expired_sandbox_leases(
    conn: AsyncConnection,
    *,
    tenant_id: str | None = None,
    reason: str = "expired",
) -> list[dict[str, Any]]:
    """Release expired DB-only leases; runtime providers must be stopped first."""
    cursor = await conn.execute(
        """
        update sandbox_leases
        set status = 'released',
            released_at = coalesce(released_at, now()),
            release_reason = %s,
            updated_at = now()
        where (%s::text is null or tenant_id = %s)
          and status = 'active'
          and expires_at is not null
          and expires_at <= now()
          and provider not in ('fake', 'docker', 'opensandbox')
        returning id, tenant_id, run_id, trace_id, release_reason
        """,
        (reason, tenant_id, tenant_id),
    )
    rows = list(await cursor.fetchall())
    for lease in rows:
        await append_event(
            conn,
            tenant_id=str(lease["tenant_id"]),
            run_id=str(lease["run_id"]),
            trace_id=lease.get("trace_id"),
            event_type="sandbox_lease_released",
            stage="sandbox",
            message="已释放过期 Sandbox 租约",
            payload={
                "visible_to_user": True,
                "lease_id": lease.get("id"),
                "reason": reason,
            },
        )
    return rows


async def list_sandbox_leases(conn: AsyncConnection, *, tenant_id: str, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select *
        from sandbox_leases
        where tenant_id = %s
          and (%s::text is null or status = %s)
        order by created_at desc
        limit %s
        """,
        (tenant_id, status, status, limit),
    )
    return list(await cursor.fetchall())


async def list_run_artifacts(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select id, trace_id, artifact_type, label, content_type, storage_key, size_bytes, manifest_version, manifest_json, created_at
        from artifacts
        where tenant_id = %s and run_id = %s
        order by created_at asc
        """,
        (tenant_id, run_id),
    )
    return list(await cursor.fetchall())


async def upsert_run_skill_snapshot(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    skill_id: str,
    skill_version: str,
    content_hash: str,
    source_json: dict[str, Any],
    dependency_ids: list[str],
    allowed: bool,
    staged: bool,
    used: bool,
    used_skills_source: str = "",
    inferred_used: bool = False,
) -> None:
    cursor = await conn.execute(
        """
        insert into run_skill_snapshots(
          id, tenant_id, run_id, skill_id, skill_version, content_hash,
          source_json, dependency_ids, allowed, staged, used, used_skills_source, inferred_used
        )
        values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s)
        on conflict (tenant_id, run_id, skill_id)
        do update set
          allowed = run_skill_snapshots.allowed or excluded.allowed,
          staged = run_skill_snapshots.staged or excluded.staged,
          used = run_skill_snapshots.used or excluded.used,
          used_skills_source = case
            when excluded.used then excluded.used_skills_source
            when run_skill_snapshots.used then run_skill_snapshots.used_skills_source
            when excluded.used_skills_source <> '' then excluded.used_skills_source
            else run_skill_snapshots.used_skills_source
          end,
          inferred_used = case
            when run_skill_snapshots.used or excluded.used then false
            else run_skill_snapshots.inferred_used or excluded.inferred_used
          end
        where run_skill_snapshots.skill_version = excluded.skill_version
          and run_skill_snapshots.content_hash = excluded.content_hash
          and run_skill_snapshots.source_json = excluded.source_json
          and run_skill_snapshots.dependency_ids = excluded.dependency_ids
        returning id
        """,
        (
            new_id("rss"),
            tenant_id,
            run_id,
            skill_id,
            skill_version,
            content_hash,
            dumps_json(source_json),
            json.dumps(dependency_ids, ensure_ascii=False),
            allowed,
            staged,
            used,
            used_skills_source,
            inferred_used,
        ),
    )
    if await cursor.fetchone() is None:
        raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")


def pin_primary_skill_mcp_tool_ids(
    skill_manifests: list[dict[str, Any]],
    *,
    skill_id: str,
    mcp_tool_ids: list[str],
) -> list[dict[str, Any]]:
    """Attach the authorized MCP execution set to the primary immutable Skill pin."""

    normalized_tool_ids = list(dict.fromkeys(str(item) for item in mcp_tool_ids if str(item)))
    pinned: list[dict[str, Any]] = []
    primary_found = False
    for manifest in skill_manifests:
        item = dict(manifest)
        if str(item.get("skill_id") or "") == skill_id:
            item["mcp_tool_ids"] = normalized_tool_ids
            primary_found = True
        pinned.append(item)
    if not primary_found:
        raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")
    return pinned


async def insert_run_skill_snapshots_at_creation(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    skill_manifests: list[dict[str, Any]],
    release_decision: dict[str, Any],
) -> None:
    """Insert exact run Skill provenance before any execution-side mutation."""

    for manifest in skill_manifests:
        skill_id = str(manifest.get("skill_id") or "")
        skill_version = str(manifest.get("version") or manifest.get("skill_version") or "")
        content_hash = str(manifest.get("content_hash") or "")
        dependency_ids = manifest.get("dependency_ids")
        if (
            not skill_id
            or not skill_version
            or skill_version != content_hash
            or not isinstance(dependency_ids, list)
            or any(not isinstance(item, str) or not item for item in dependency_ids)
        ):
            raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")
        cursor = await conn.execute(
            """
            insert into run_skill_snapshots(
              id, tenant_id, run_id, skill_id, skill_version, content_hash,
              source_json, dependency_ids, allowed, staged, used, used_skills_source, inferred_used
            )
            values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, true, false, false, '', false)
            on conflict (tenant_id, run_id, skill_id) do nothing
            returning id
            """,
            (
                new_id("rss"),
                tenant_id,
                run_id,
                skill_id,
                skill_version,
                content_hash,
                dumps_json(run_skill_snapshot_source_json(manifest, release_decision=release_decision)),
                json.dumps(dependency_ids, ensure_ascii=False),
            ),
        )
        if await cursor.fetchone() is None:
            raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")
        materialization_sha256 = skill_manifest_materialization_sha256(manifest)
        materialized = await conn.execute(
            """
            insert into run_skill_materializations(
              tenant_id, run_id, skill_id, materialization_sha256, manifest_json
            )
            values (%s, %s, %s, %s, %s::jsonb)
            on conflict (tenant_id, run_id, skill_id) do nothing
            returning skill_id
            """,
            (
                tenant_id,
                run_id,
                skill_id,
                materialization_sha256,
                dumps_json(manifest),
            ),
        )
        if await materialized.fetchone() is None:
            raise RepositoryConflictError("run_skill_materialization_identity_mismatch")


def skill_manifest_refs(skill_manifests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the only Skill package form permitted in run input and Redis."""

    try:
        return build_skill_manifest_refs(skill_manifests)
    except SkillVersionMaterializationError as exc:
        raise RepositoryConflictError("run_skill_materialization_identity_mismatch") from exc


def _materialization_refs_match(
    refs: list[dict[str, Any]],
    manifests: list[dict[str, Any]],
) -> bool:
    try:
        return build_skill_manifest_refs(manifests) == refs
    except SkillVersionMaterializationError:
        return False


async def materialize_run_skill_manifests(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    skill_manifest_refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Load exact private packages for bounded, digest-bound references."""

    try:
        exact_refs = validate_skill_manifest_refs(skill_manifest_refs)
    except SkillVersionMaterializationError:
        raise RepositoryConflictError("run_skill_materialization_identity_mismatch")
    if not exact_refs:
        return []
    cursor = await conn.execute(
        """
        select skill_id, materialization_sha256, manifest_json
        from run_skill_materializations
        where tenant_id = %s and run_id = %s
        order by skill_id asc
        """,
        (tenant_id, run_id),
    )
    manifests_by_id: dict[str, dict[str, Any]] = {}
    for row in await cursor.fetchall():
        manifest = row.get("manifest_json")
        if isinstance(manifest, str):
            try:
                manifest = json.loads(manifest)
            except json.JSONDecodeError as exc:
                raise RepositoryConflictError(
                    "run_skill_materialization_identity_mismatch"
                ) from exc
        row_skill_id = str(row.get("skill_id") or "")
        try:
            materialization_sha256 = (
                skill_manifest_materialization_sha256(manifest)
                if isinstance(manifest, dict)
                else ""
            )
        except SkillVersionMaterializationError as exc:
            raise RepositoryConflictError(
                "run_skill_materialization_identity_mismatch"
            ) from exc
        if (
            not isinstance(manifest, dict)
            or str(manifest.get("skill_id") or "") != row_skill_id
            or materialization_sha256 != str(row.get("materialization_sha256") or "")
            or row_skill_id in manifests_by_id
        ):
            raise RepositoryConflictError("run_skill_materialization_identity_mismatch")
        manifests_by_id[row_skill_id] = dict(manifest)
    manifests = [
        manifests_by_id.get(str(ref.get("skill_id") or ""))
        for ref in exact_refs
    ]
    if any(manifest is None for manifest in manifests):
        raise RepositoryConflictError("run_skill_materialization_identity_mismatch")
    exact_manifests = [manifest for manifest in manifests if manifest is not None]
    if not _materialization_refs_match(exact_refs, exact_manifests):
        raise RepositoryConflictError("run_skill_materialization_identity_mismatch")
    return exact_manifests


async def list_run_skill_snapshots(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          skill_id,
          skill_version,
          content_hash,
          source_json,
          dependency_ids,
          allowed,
          staged,
          used,
          used_skills_source,
          inferred_used,
          created_at
        from run_skill_snapshots
        where tenant_id = %s and run_id = %s
        order by skill_id asc
        """,
        (tenant_id, run_id),
    )
    rows = list(await cursor.fetchall())
    snapshots = []
    for row in rows:
        source = _sanitize_skill_snapshot_source(row.get("source_json"))
        dependency_ids = row.get("dependency_ids") if isinstance(row.get("dependency_ids"), list) else []
        used_skills_source = str(row.get("used_skills_source") or "").strip()
        inferred_used = bool(row.get("inferred_used"))
        usage: dict[str, Any] = {}
        if used_skills_source:
            usage["used_skills_source"] = sanitize_public_text(used_skills_source)
        if inferred_used:
            usage["inferred_used"] = True
            usage["inferred_used_skills"] = [str(row["skill_id"])]
        snapshot = {
            "skill_id": row["skill_id"],
            "skill_version": row["skill_version"],
            "content_hash": row["content_hash"],
            "source": source,
            "dependency_ids": [str(item) for item in dependency_ids],
            "allowed": bool(row["allowed"]),
            "staged": bool(row["staged"]),
            "used": bool(row["used"]),
            "created_at": row["created_at"],
        }
        if usage:
            snapshot["usage"] = usage
        snapshots.append(snapshot)
    return snapshots


async def validate_run_skill_snapshots_for_dispatch(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    skill_manifests: list[dict[str, Any]],
    release_decision: dict[str, Any],
) -> None:
    """Require locked manifests to match immutable creation-time snapshot rows."""

    cursor = await conn.execute(
        """
        select skill_id, skill_version, content_hash, source_json, dependency_ids
        from run_skill_snapshots
        where tenant_id = %s and run_id = %s
        order by skill_id asc
        """,
        (tenant_id, run_id),
    )
    rows = [dict(row) for row in await cursor.fetchall()]
    expected: dict[str, dict[str, Any]] = {}
    for manifest in skill_manifests:
        skill_id = str(manifest.get("skill_id") or "")
        version = str(manifest.get("version") or manifest.get("skill_version") or "")
        content_hash = str(manifest.get("content_hash") or "")
        dependency_ids = manifest.get("dependency_ids")
        if (
            not skill_id
            or skill_id in expected
            or not version
            or version != content_hash
            or not isinstance(dependency_ids, list)
        ):
            raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")
        expected[skill_id] = {
            "skill_version": version,
            "content_hash": content_hash,
            "source_json": run_skill_snapshot_source_json(manifest, release_decision=release_decision),
            "dependency_ids": dependency_ids,
        }
    if len(rows) != len(expected):
        raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")
    for row in rows:
        skill_id = str(row.get("skill_id") or "")
        locked = expected.get(skill_id)
        source_json = row.get("source_json")
        dependency_ids = row.get("dependency_ids")
        if isinstance(source_json, str):
            try:
                source_json = json.loads(source_json)
            except json.JSONDecodeError:
                source_json = None
        if isinstance(dependency_ids, str):
            try:
                dependency_ids = json.loads(dependency_ids)
            except json.JSONDecodeError:
                dependency_ids = None
        if locked is None or {
            "skill_version": str(row.get("skill_version") or ""),
            "content_hash": str(row.get("content_hash") or ""),
            "source_json": source_json,
            "dependency_ids": dependency_ids,
        } != locked:
            raise RepositoryConflictError("run_skill_snapshot_identity_mismatch")


def _sanitize_skill_snapshot_source(source_json: object) -> dict[str, Any]:
    source = sanitize_public_payload(source_json if isinstance(source_json, dict) else {})
    if not isinstance(source, dict):
        return {}
    source.pop("version", None)
    governance = source.get("snapshot_governance")
    if isinstance(governance, dict):
        governance_schema = governance.get("schema_version")
        if governance_schema == SKILL_PINNED_SNAPSHOT_GOVERNANCE_SCHEMA_VERSION_V1:
            legacy_boundary = governance.pop("does_not_close_b4_or_211", None)
            if isinstance(legacy_boundary, bool):
                governance["does_not_close_b4_or_deployed_runtime_acceptance"] = (
                    legacy_boundary
                )
        elif governance_schema == SKILL_PINNED_SNAPSHOT_GOVERNANCE_SCHEMA_VERSION_V2:
            governance.pop("does_not_close_b4_or_211", None)
        manifest = governance.get("manifest")
        if isinstance(manifest, dict):
            manifest.pop("digest", None)
        release_lock = governance.get("release_lock")
        if isinstance(release_lock, dict):
            release_lock.pop("track", None)
            release_lock.pop("rollout", None)
    return source


def _sanitize_skill_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    source = _sanitize_skill_snapshot_source(snapshot.get("source"))
    usage = snapshot.get("usage") if isinstance(snapshot.get("usage"), dict) else {}
    sanitized_usage: dict[str, Any] = {}
    for key, value in usage.items():
        if isinstance(value, str):
            sanitized_text = sanitize_public_text(value)
            if sanitized_text:
                sanitized_usage[key] = sanitized_text
        elif isinstance(value, list):
            cleaned = [sanitize_public_text(item) for item in value]
            sanitized_usage[key] = [item for item in cleaned if item]
        elif isinstance(value, (int, bool)):
            sanitized_usage[key] = value
    sanitized = {
        "skill_id": str(snapshot.get("skill_id") or ""),
        "skill_version": str(snapshot.get("skill_version") or ""),
        "content_hash": str(snapshot.get("content_hash") or ""),
        "source": source,
        "dependency_ids": [str(item) for item in snapshot.get("dependency_ids") or []],
        "allowed": bool(snapshot.get("allowed")),
        "staged": bool(snapshot.get("staged")),
        "used": bool(snapshot.get("used")),
        "created_at": snapshot.get("created_at"),
    }
    if sanitized_usage:
        sanitized["usage"] = sanitized_usage
    return sanitized


def _skill_usage_from_events(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    usage_by_skill: dict[str, dict[str, Any]] = {}
    ordered_events = sorted(events, key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")))
    for item in ordered_events:
        if item.get("event_type") != "skill_used":
            continue
        if bool(item.get("visible_to_user", True)):
            continue
        payload = item.get("payload_json") if isinstance(item.get("payload_json"), dict) else {}
        skill_id = str(payload.get("skill_id") or payload.get("skill_name") or "").strip()
        if not skill_id:
            continue
        usage = usage_by_skill.setdefault(
            skill_id,
            {
                "event_source": "",
                "event_count": 0,
                "tool_use_ids": [],
            },
        )
        usage["event_count"] += 1
        event_source = sanitize_public_text(payload.get("source")).strip()
        if event_source and not usage["event_source"]:
            usage["event_source"] = event_source
        tool_use_id = sanitize_public_text(payload.get("tool_use_id")).strip()
        if tool_use_id and tool_use_id not in usage["tool_use_ids"]:
            usage["tool_use_ids"].append(tool_use_id)
    for usage in usage_by_skill.values():
        usage["tool_use_ids"].sort()
    return usage_by_skill


def _attach_skill_usage(
    skill_snapshots: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    usage_by_skill = _skill_usage_from_events(events)
    if not usage_by_skill:
        return skill_snapshots
    enriched: list[dict[str, Any]] = []
    for snapshot in skill_snapshots:
        usage = usage_by_skill.get(str(snapshot.get("skill_id") or ""))
        if usage:
            persisted_usage = snapshot.get("usage") if isinstance(snapshot.get("usage"), dict) else {}
            enriched.append({**snapshot, "usage": {**persisted_usage, **usage}})
        else:
            enriched.append(snapshot)
    return enriched


def _json_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _project_skill_version(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "skill_id": row["skill_id"],
        "version": row["version"],
        "content_hash": row["content_hash"],
        "description": row.get("description") or "",
        "source": _json_dict(row.get("source_json")),
        "dependency_ids": _json_list(row.get("dependency_ids")),
        "status": row.get("status") or "active",
        "created_by": row.get("created_by"),
        "created_at": row.get("created_at"),
    }


def _project_skill_release_policy(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "skill_id": row["skill_id"],
        "channel": row.get("channel") or "stable",
        "current_version": row["current_version"],
        "previous_version": row.get("previous_version"),
        "rollout_percent": int(row.get("rollout_percent") or 0),
        "status": row.get("status") or "active",
        "promoted_by": row.get("promoted_by"),
        "promoted_at": row.get("promoted_at"),
    }


async def upsert_skill_version(
    conn: AsyncConnection,
    *,
    skill_id: str,
    version: str,
    content_hash: str,
    description: str,
    source_json: dict[str, Any],
    dependency_ids: list[str],
    status: str = "active",
    created_by: str | None = None,
) -> bool:
    cursor = await conn.execute(
        """
        insert into skill_versions(
          id, skill_id, version, content_hash, description, source_json,
          dependency_ids, status, created_by
        )
        values (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        on conflict (skill_id, version)
        do nothing
        returning skill_id
        """,
        (
            new_id("skv"),
            skill_id,
            version,
            content_hash,
            description,
            dumps_json(source_json),
            json.dumps(dependency_ids, ensure_ascii=False),
            status,
            created_by,
        ),
    )
    return await cursor.fetchone() is not None


async def create_skill_catalog(
    conn: AsyncConnection,
    *,
    skill_id: str,
    name: str,
    version: str,
    description: str,
    input_modes: list[str],
    output_modes: list[str],
    executor_type: str,
    status: str = "active",
) -> None:
    cursor = await conn.execute(
        """
        insert into skills(
          id, name, version, description, input_modes, output_modes, executor_type, status
        )
        values (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        on conflict (id) do nothing
        returning id
        """,
        (
            skill_id,
            name,
            version,
            description,
            json.dumps(input_modes, ensure_ascii=False),
            json.dumps(output_modes, ensure_ascii=False),
            executor_type,
            status,
        ),
    )
    if await cursor.fetchone() is None:
        raise RepositoryConflictError("skill_catalog_already_exists")


async def update_skill_catalog_version(
    conn: AsyncConnection,
    *,
    skill_id: str,
    version: str,
    description: str,
) -> None:
    await conn.execute(
        """
        update skills
        set version = %s,
            description = %s
        where id = %s
        """,
        (version, description, skill_id),
    )


async def backfill_builtin_skill_version_snapshot(
    conn: AsyncConnection,
    *,
    skill_id: str,
    version: str,
    source_json: dict[str, Any],
    dependency_ids: list[str],
    description: str,
) -> None:
    serialized_source_json = dumps_json(source_json)
    serialized_dependency_ids = json.dumps(dependency_ids, ensure_ascii=False)
    await conn.execute(
        """
        update skill_versions
        set source_json = %s::jsonb,
            dependency_ids = %s::jsonb,
            description = %s
        where skill_id = %s
          and version = %s
          and source_json->>'kind' = 'builtin'
          and (
            not (source_json ? 'files')
            or source_json->'files' is distinct from (%s::jsonb->'files')
            or dependency_ids <> %s::jsonb
            or source_json->'dependency_manifests' is distinct from (%s::jsonb->'dependency_manifests')
          )
        """,
        (
            serialized_source_json,
            serialized_dependency_ids,
            description,
            skill_id,
            version,
            serialized_source_json,
            serialized_dependency_ids,
            serialized_source_json,
        ),
    )


async def update_skill_version_status(
    conn: AsyncConnection,
    *,
    skill_id: str,
    version: str,
    status: str,
) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        update skill_versions
        set status = %s
        where skill_id = %s and version = %s
        returning
          skill_id,
          version,
          content_hash,
          description,
          source_json,
          dependency_ids,
          status,
          created_by,
          created_at
        """,
        (status, skill_id, version),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryNotFoundError("skill_version_not_found")
    return _project_skill_version(row)


async def get_effective_skill_version_for_policy(
    conn: AsyncConnection,
    *,
    skill_id: str,
    version: str,
) -> dict[str, Any] | None:
    return await get_skill_version(conn, skill_id=skill_id, version=version)


async def list_skill_versions(conn: AsyncConnection, *, skill_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          skill_id,
          version,
          content_hash,
          description,
          source_json,
          dependency_ids,
          status,
          created_by,
          created_at
        from skill_versions
        where skill_id = %s
        order by created_at desc, version desc
        """,
        (skill_id,),
    )
    return [_project_skill_version(row) for row in list(await cursor.fetchall())]


async def get_skill_release_policy(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
    channel: str = "stable",
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select
          skill_id,
          channel,
          current_version,
          previous_version,
          rollout_percent,
          status,
          promoted_by,
          promoted_at
        from skill_release_policies
        where tenant_id = %s and skill_id = %s and channel = %s and status = 'active'
        """,
        (tenant_id, skill_id, channel),
    )
    return _project_skill_release_policy(await cursor.fetchone())


async def set_skill_release_policy(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
    version: str,
    previous_version: str | None,
    promoted_by: str | None,
    channel: str = "stable",
    rollout_percent: int = 100,
    status: str = "active",
) -> None:
    await conn.execute(
        """
        insert into skill_release_policies(
          id, tenant_id, skill_id, channel, current_version, previous_version,
          rollout_percent, status, promoted_by, promoted_at, updated_at
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
        on conflict (tenant_id, skill_id, channel)
        do update set
          current_version = excluded.current_version,
          previous_version = excluded.previous_version,
          rollout_percent = excluded.rollout_percent,
          status = excluded.status,
          promoted_by = excluded.promoted_by,
          promoted_at = now(),
          updated_at = now()
        """,
        (
            new_id("skr"),
            tenant_id,
            skill_id,
            channel,
            version,
            previous_version,
            rollout_percent,
            status,
            promoted_by,
        ),
    )


async def diff_skill_versions(
    conn: AsyncConnection,
    *,
    skill_id: str,
    from_version: str,
    to_version: str,
) -> dict[str, Any]:
    source = await get_skill_version(conn, skill_id=skill_id, version=from_version)
    target = await get_skill_version(conn, skill_id=skill_id, version=to_version)
    if source is None or target is None:
        raise RepositoryNotFoundError("skill_version_not_found")
    source_dependencies = set(_json_list(source.get("dependency_ids")))
    target_dependencies = set(_json_list(target.get("dependency_ids")))
    return {
        "skill_id": skill_id,
        "from_version": from_version,
        "to_version": to_version,
        "content_hash_changed": source.get("content_hash") != target.get("content_hash"),
        "description_changed": source.get("description") != target.get("description"),
        "source_changed": source.get("source") != target.get("source"),
        "dependency_added": sorted(target_dependencies - source_dependencies),
        "dependency_removed": sorted(source_dependencies - target_dependencies),
    }


async def list_admin_skill_summaries(
    conn: AsyncConnection,
    *,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Return lifecycle summaries without exposing package or runtime-private source data."""

    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    cursor = await conn.execute(
        """
        select
          skills.id as skill_id,
          skills.name,
          skills.description,
          skills.status as lifecycle_status,
          coalesce(tenant_capability_distributions.status, 'disabled') as distribution_status,
          coalesce(tenant_capability_distributions.visible_to_user, false) as visible_to_user,
          coalesce(tenant_capability_distributions.metadata_json, '{}'::jsonb) as distribution_metadata_json,
          latest_version.version as latest_version,
          latest_version.status as latest_version_status,
          skill_release_policies.current_version,
          skill_release_policies.rollout_percent
        from skills
        left join tenant_capability_distributions
          on tenant_capability_distributions.tenant_id = %s
         and tenant_capability_distributions.capability_kind = 'skill'
         and tenant_capability_distributions.capability_id = skills.id
        left join lateral (
          select skill_versions.version, skill_versions.status
          from skill_versions
          where skill_versions.skill_id = skills.id
          order by skill_versions.created_at desc, skill_versions.version desc
          limit 1
        ) as latest_version on true
        left join skill_release_policies
          on skill_release_policies.tenant_id = %s
         and skill_release_policies.skill_id = skills.id
         and skill_release_policies.channel = 'stable'
         and skill_release_policies.status = 'active'
        order by skills.name asc, skills.id asc
        """,
        (tenant_id, tenant_id),
    )
    rows = []
    for raw_row in list(await cursor.fetchall()):
        row = dict(raw_row)
        distribution_metadata_json = row.pop("distribution_metadata_json", {})
        if is_capability_distribution_archived(
            {"metadata_json": distribution_metadata_json}
        ):
            continue
        rows.append(row)
    return rows


async def get_admin_skill_detail(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    skill_id: str,
) -> dict[str, Any] | None:
    await ensure_tenant_capability_distribution_backfill(conn, tenant_id=tenant_id)
    cursor = await conn.execute(
        """
        select
          skills.id as skill_id,
          skills.name,
          skills.version,
          skills.description,
          skills.input_modes,
          skills.output_modes,
          skills.executor_type,
          skills.status as lifecycle_status,
          coalesce(tenant_capability_distributions.status, 'disabled') as status,
          coalesce(tenant_capability_distributions.visible_to_user, false) as visible_to_user,
          coalesce(tenant_capability_distributions.metadata_json, '{}'::jsonb) as distribution_metadata_json
        from skills
        left join tenant_capability_distributions
          on tenant_capability_distributions.tenant_id = %s
         and tenant_capability_distributions.capability_kind = 'skill'
         and tenant_capability_distributions.capability_id = skills.id
        where skills.id = %s
        """,
        (tenant_id, skill_id),
    )
    raw_skill = await cursor.fetchone()
    if raw_skill is None:
        return None
    skill = dict(raw_skill)
    distribution_metadata_json = skill.pop("distribution_metadata_json", {})
    if is_capability_distribution_archived(
        {"metadata_json": distribution_metadata_json}
    ):
        return None

    versions = await list_skill_versions(conn, skill_id=skill_id)
    release_policy = await get_skill_release_policy(
        conn,
        tenant_id=tenant_id,
        skill_id=skill_id,
    )
    snapshots_cursor = await conn.execute(
        """
        select
          run_id,
          skill_id,
          skill_version,
          content_hash,
          source_json,
          dependency_ids,
          allowed,
          staged,
          used,
          used_skills_source,
          inferred_used,
          created_at
        from run_skill_snapshots
        where tenant_id = %s and skill_id = %s
        order by created_at desc
        limit 20
        """,
        (tenant_id, skill_id),
    )
    snapshots = []
    for row in list(await snapshots_cursor.fetchall()):
        used_skills_source = str(row.get("used_skills_source") or "").strip()
        inferred_used = bool(row.get("inferred_used"))
        snapshot = {
            "run_id": row["run_id"],
            "skill_id": row["skill_id"],
            "source": _sanitize_skill_snapshot_source(row.get("source_json")),
            "dependency_ids": _json_list(row.get("dependency_ids")),
            "allowed": bool(row["allowed"]),
            "staged": bool(row["staged"]),
            "used": bool(row["used"]),
            "created_at": row.get("created_at"),
        }
        usage: dict[str, Any] = {}
        if used_skills_source:
            usage["used_skills_source"] = used_skills_source
        if inferred_used:
            usage["inferred_used"] = True
            usage["inferred_used_skills"] = [str(row["skill_id"])]
        if usage:
            snapshot["usage"] = usage
        snapshots.append(snapshot)

    return {
        "skill": skill,
        "release_policy": release_policy,
        "versions": versions,
        "recent_snapshots": snapshots,
    }


async def upsert_run_step(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    step_key: str,
    step_kind: str,
    status: str,
    title: str,
    role: str | None,
    sequence: int,
    payload_json: dict[str, Any],
) -> str:
    _require_json_size(
        payload_json,
        max_bytes=RUN_STEP_PAYLOAD_MAX_BYTES,
        code="run_step_payload_too_large",
    )
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (f"run-step:{tenant_id}:{run_id}:{step_key}",),
    )
    existing_cursor = await conn.execute(
        """
        select id, payload_json
        from run_steps
        where tenant_id = %s and run_id = %s and step_key = %s
        for update
        """,
        (tenant_id, run_id, step_key),
    )
    existing = await existing_cursor.fetchone()
    existing_payload = existing.get("payload_json") if existing is not None else {}
    if not isinstance(existing_payload, dict):
        existing_payload = {}
    merged_payload = {**existing_payload, **payload_json}
    _require_json_size(
        merged_payload,
        max_bytes=RUN_STEP_PAYLOAD_MAX_BYTES,
        code="run_step_payload_too_large",
    )
    if existing is not None:
        cursor = await conn.execute(
            """
            update run_steps
            set step_kind = %s,
                status = %s,
                title = %s,
                role = %s,
                sequence = %s,
                payload_json = %s::jsonb,
                started_at = coalesce(
                  started_at,
                  case when %s in ('running', 'succeeded', 'failed') then now() else null end
                ),
                finished_at = coalesce(
                  case when %s in ('succeeded', 'failed', 'cancelled') then now() else null end,
                  finished_at
                ),
                updated_at = now()
            where id = %s and tenant_id = %s and run_id = %s
            returning id
            """,
            (
                step_kind,
                status,
                title,
                role,
                sequence,
                compact_json_dumps(merged_payload),
                status,
                status,
                str(existing["id"]),
                tenant_id,
                run_id,
            ),
        )
        row = await cursor.fetchone()
        if row is None:
            raise RepositoryConflictError("run_step_update_conflict")
        return str(row["id"])
    step_id = new_id("step")
    cursor = await conn.execute(
        """
        insert into run_steps(
          id, tenant_id, run_id, step_key, step_kind, status, title, role, sequence,
          payload_json, started_at, finished_at
        )
        values (
          %s, %s, %s, %s, %s, %s, %s, %s, %s,
          %s::jsonb,
          case when %s in ('running', 'succeeded', 'failed') then now() else null end,
          case when %s in ('succeeded', 'failed', 'cancelled') then now() else null end
        )
        returning id
        """,
        (
            step_id,
            tenant_id,
            run_id,
            step_key,
            step_kind,
            status,
            title,
            role,
            sequence,
            compact_json_dumps(merged_payload),
            status,
            status,
        ),
    )
    row = await cursor.fetchone()
    return str(row["id"])


def _terminal_dispatch_state(child_status: str) -> tuple[str, str]:
    if child_status == "succeeded":
        return "succeeded", "completed"
    if child_status == "failed":
        return "failed", "failed"
    if child_status == "cancelled":
        return "cancelled", "cancelled"
    raise ValueError("unsupported_child_status")


def _safe_child_result_output(result_json: dict[str, Any] | None) -> str:
    if not isinstance(result_json, dict):
        return ""
    return sanitize_public_text(result_json.get("message"))


def _safe_child_error_message(
    *,
    child_status: str,
    result_json: dict[str, Any] | None,
    error_message: str | None,
) -> str:
    safe_message = sanitize_public_text(error_message)
    if safe_message:
        return safe_message
    if isinstance(result_json, dict):
        safe_message = sanitize_public_text(result_json.get("message"))
        if safe_message:
            return safe_message
    return "child_run_cancelled" if child_status == "cancelled" else "child_run_failed"


def _safe_child_error_code(error_code: str | None, *, child_status: str) -> str:
    fallback = "child_run_cancelled" if child_status == "cancelled" else "child_run_failed"
    safe_code = sanitize_public_text(error_code)
    if not safe_code:
        return fallback
    normalized = safe_code.strip().lower().replace("-", "_")
    if not normalized or len(normalized) > 80:
        return fallback
    if not all(ch.isalnum() or ch == "_" for ch in normalized):
        return fallback
    return standard_error_code(normalized)


def _parent_multi_agent_message(status: str) -> str:
    if status == "succeeded":
        return "Multi-agent run succeeded"
    if status == "failed":
        return "Multi-agent run failed"
    if status == "cancelled":
        return "Multi-agent run cancelled"
    return "Multi-agent run finalized"


def _safe_parent_step_text(value: Any) -> str:
    sanitized = sanitize_public_text(value)
    if HASH_LIKE_VALUE_PATTERN.fullmatch(sanitized.strip()):
        return ""
    return sanitized


def _safe_parent_step_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    depends_on = payload.get("depends_on")
    if isinstance(depends_on, list):
        cleaned["depends_on"] = [
            safe_dependency
            for item in depends_on
            if (safe_dependency := _safe_parent_step_text(item))
        ]
    else:
        cleaned["depends_on"] = []
    for key in (
        "dispatch_state",
        "dispatch_child_run_id",
        "checkpoint_id",
        "source_step_id",
        "output",
        "error_code",
        "error",
    ):
        safe_value = _safe_parent_step_text(payload.get(key))
        if safe_value:
            target_key = "child_run_id" if key == "dispatch_child_run_id" else key
            cleaned[target_key] = safe_value
    return cleaned


def _multi_agent_parent_step_summary(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    safe_payload = _safe_parent_step_payload(payload)
    return {
        "step_key": _safe_parent_step_text(row.get("step_key")) or str(row.get("id") or ""),
        "status": str(row.get("status") or ""),
        "role": _safe_parent_step_text(row.get("role")) or None,
        "sequence": _coerce_int(row.get("sequence")),
        "depends_on": safe_payload.pop("depends_on", []),
        **safe_payload,
    }


def _multi_agent_parent_counts(steps: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(steps),
        "succeeded": sum(1 for item in steps if str(item.get("status") or "") == "succeeded"),
        "failed": sum(1 for item in steps if str(item.get("status") or "") == "failed"),
        "cancelled": sum(1 for item in steps if str(item.get("status") or "") == "cancelled"),
    }


def _multi_agent_parent_status(parent_run: dict[str, Any], steps: list[dict[str, Any]]) -> str | None:
    if not steps:
        return None
    statuses = {str(item.get("status") or "") for item in steps}
    if not statuses.issubset(runs_api.TERMINAL_RUN_STATUSES):
        return None
    if "failed" in statuses:
        return "failed"
    if parent_run.get("cancel_requested_at") or "cancelled" in statuses:
        return "cancelled"
    return "succeeded"


def _multi_agent_parent_has_open_dispatch(steps: list[dict[str, Any]]) -> bool:
    for row in steps:
        payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
        if str(payload.get("dispatch_state") or "") in {"claimed", "handed_off"}:
            return True
    return False


def _configured_multi_agent_step_keys(configured_steps: object) -> set[str] | None:
    if not isinstance(configured_steps, list):
        return None
    keys: list[str] = []
    for item in configured_steps:
        if not isinstance(item, dict):
            return None
        key = str(item.get("step_key") or item.get("stepKey") or "").strip()
        if not key:
            return None
        keys.append(key)
    key_set = set(keys)
    if len(key_set) != len(keys):
        return None
    return key_set


def _run_execution_input_from_row(run: dict[str, Any]) -> dict[str, Any]:
    source_input = run.get("input_json") if isinstance(run.get("input_json"), dict) else {}
    execution_input = source_input.get("input") if isinstance(source_input.get("input"), dict) else source_input
    return execution_input if isinstance(execution_input, dict) else {}


async def finalize_multi_agent_parent_run_if_ready(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    parent_run_id: str,
    triggered_by_child_run_id: str | None = None,
) -> dict[str, Any] | None:
    """Finalize a multi-agent parent run after all server-owned child steps settle."""
    parent_cursor = await conn.execute(
        """
        select id, tenant_id, copied_from_run_id, trace_id, status, cancel_requested_at, input_json
        from runs
        where tenant_id = %s
          and id = %s
        for update skip locked
        """,
        (tenant_id, parent_run_id),
    )
    parent_run = await parent_cursor.fetchone()
    if parent_run is None:
        return None
    if parent_run.get("copied_from_run_id"):
        return None
    parent_status = str(parent_run.get("status") or "")
    parent_is_terminal = parent_status in runs_api.TERMINAL_RUN_STATUSES
    if not parent_is_terminal and parent_status != "running" and parent_run.get("cancel_requested_at") is None:
        return None
    execution_input = _run_execution_input_from_row(parent_run)
    if str(execution_input.get("execution_mode") or "") != "multi_agent":
        return None
    if "multi_agent_steps" in execution_input:
        configured_keys = _configured_multi_agent_step_keys(execution_input.get("multi_agent_steps"))
    else:
        configured_keys = set()
    if configured_keys is None:
        return None
    configured_count = len(configured_keys)
    steps = await list_run_steps(conn, tenant_id=tenant_id, run_id=parent_run_id)
    if not steps and configured_count <= 0:
        return None
    if len(steps) < configured_count:
        return None
    recorded_keys = {str(row.get("step_key") or "").strip() for row in steps}
    if not configured_keys.issubset(recorded_keys):
        return None
    if _multi_agent_parent_has_open_dispatch(steps):
        return None
    target_status = parent_status if parent_is_terminal else _multi_agent_parent_status(parent_run, steps)
    if target_status is None:
        return None
    active_cursor = await conn.execute(
        """
        select child.id, child.status
        from runs child
        where child.tenant_id = %s
          and child.copied_from_run_id = %s
          and child.status in ('queued', 'running')
          and (
            child.input_json#>>'{input,multi_agent_dispatch,parent_run_id}' = %s
            or child.input_json#>>'{multi_agent_dispatch,parent_run_id}' = %s
          )
        """,
        (tenant_id, parent_run_id, parent_run_id, parent_run_id),
    )
    active_children = list(await active_cursor.fetchall())
    if active_children:
        return None
    summaries = [_multi_agent_parent_step_summary(row) for row in steps]
    counts = _multi_agent_parent_counts(summaries)
    result_json: dict[str, Any] = {
        "message": _parent_multi_agent_message(target_status),
        "multi_agent": {
            "status": target_status,
            "counts": counts,
            "steps": summaries,
        },
    }
    safe_triggered_by = sanitize_public_text(triggered_by_child_run_id)
    if safe_triggered_by:
        result_json["multi_agent"]["triggered_by_child_run_id"] = safe_triggered_by
    terminal_written: bool | runs_api.RunTerminalizationProgress = parent_is_terminal
    if not parent_is_terminal:
        if target_status == "succeeded":
            terminal_written = await complete_run(
                conn,
                tenant_id=tenant_id,
                run_id=parent_run_id,
                result_json=result_json,
            )
            if not terminal_written:
                blocked_reason = await classify_success_commit_block(
                    conn,
                    tenant_id=tenant_id,
                    run_id=parent_run_id,
                )
                if blocked_reason == "tool_permission_pending":
                    terminal_written = await fail_run(
                        conn,
                        tenant_id=tenant_id,
                        run_id=parent_run_id,
                        error_code="tool_permission_pending",
                        error_message="A pending tool-permission request blocked successful completion.",
                        result_json={
                            **result_json,
                            "message": "A pending tool-permission request blocked successful completion.",
                            "error_code": "tool_permission_pending",
                        },
                    )
                    target_status = "failed"
                elif blocked_reason == "cancel_requested":
                    terminal_written = await cancel_run(
                        conn,
                        tenant_id=tenant_id,
                        run_id=parent_run_id,
                        result_json={"message": "任务已取消", "multi_agent": result_json["multi_agent"]},
                    )
                    target_status = "cancelled"
        elif target_status == "failed":
            terminal_written = await fail_run(
                conn,
                tenant_id=tenant_id,
                run_id=parent_run_id,
                error_code="multi_agent_child_failed",
                error_message="Multi-agent child step failed",
                result_json=result_json,
            )
        else:
            terminal_written = await cancel_run(
                conn,
                tenant_id=tenant_id,
                run_id=parent_run_id,
                result_json=result_json,
            )
    if not terminal_written:
        return None
    facts_cursor = await conn.execute(
        """
        select
          exists (
            select 1 from run_events
            where tenant_id = %s and run_id = %s and event_type = 'multi_agent_parent_finalized'
          ) as has_parent_finalized_event,
          exists (
            select 1 from audit_logs
            where tenant_id = %s
              and target_type = 'run'
              and target_id = %s
              and action = 'run.multi_agent.parent.finalize'
          ) as has_parent_finalized_audit
        """,
        (tenant_id, parent_run_id, tenant_id, parent_run_id),
    )
    parent_facts = await facts_cursor.fetchone() or {}
    has_parent_event = bool(parent_facts.get("has_parent_finalized_event"))
    has_parent_audit = bool(parent_facts.get("has_parent_finalized_audit"))
    if has_parent_event and has_parent_audit:
        return None
    event_payload: dict[str, Any] = {
        "visible_to_user": False,
        "status": target_status,
        "counts": counts,
    }
    if safe_triggered_by:
        event_payload["triggered_by_child_run_id"] = safe_triggered_by
    event_id = None
    if not has_parent_event:
        event_id = await append_event(
            conn,
            tenant_id=tenant_id,
            run_id=parent_run_id,
            trace_id=parent_run.get("trace_id"),
            event_type="multi_agent_parent_finalized",
            stage="control",
            message=_parent_multi_agent_message(target_status),
            visible_to_user=False,
            payload=event_payload,
        )
    audit_payload: dict[str, Any] = {"status": target_status, "counts": counts}
    if safe_triggered_by:
        audit_payload["triggered_by_child_run_id"] = safe_triggered_by
    audit_id = None
    if not has_parent_audit:
        audit_id = await append_audit_log(
            conn,
            tenant_id=tenant_id,
            user_id=None,
            action="run.multi_agent.parent.finalize",
            target_type="run",
            target_id=parent_run_id,
            trace_id=parent_run.get("trace_id"),
            payload_json=audit_payload,
        )
    return {
        "parent_run_id": parent_run_id,
        "status": target_status,
        "event_id": event_id,
        "audit_id": audit_id,
        "counts": counts,
    }


def _child_dispatch_metadata(child_run: dict[str, Any]) -> dict[str, Any]:
    source_input = child_run.get("input_json") if isinstance(child_run.get("input_json"), dict) else {}
    execution_input = source_input.get("input") if isinstance(source_input.get("input"), dict) else source_input
    dispatch = execution_input.get("multi_agent_dispatch")
    return dispatch if isinstance(dispatch, dict) else {}


async def reconcile_multi_agent_child_run_terminal_state(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    child_run_id: str,
    child_status: str,
    result_json: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any] | None:
    """Mirror a server-owned terminal child run onto its handed-off parent step."""

    parent_step_status, dispatch_state = _terminal_dispatch_state(child_status)
    child_cursor = await conn.execute(
        """
        select id, tenant_id, copied_from_run_id, trace_id, status, input_json
        from runs
        where tenant_id = %s
          and id = %s
        for update
        """,
        (tenant_id, child_run_id),
    )
    child_run = await child_cursor.fetchone()
    if child_run is None:
        return None
    if str(child_run.get("status") or "") != child_status or child_status not in runs_api.TERMINAL_RUN_STATUSES:
        return None
    parent_run_id = str(child_run.get("copied_from_run_id") or "").strip()
    if not parent_run_id:
        return None
    dispatch = _child_dispatch_metadata(child_run)
    if str(dispatch.get("parent_run_id") or "") != parent_run_id:
        return None
    parent_step_id = str(dispatch.get("parent_step_id") or "").strip()
    dispatch_id = str(dispatch.get("dispatch_id") or "").strip()
    step_key = str(dispatch.get("step_key") or "").strip()
    if not parent_step_id or not dispatch_id or not step_key:
        return None

    step_cursor = await conn.execute(
        """
        select id, run_id, step_key, step_kind, status, title, role, sequence, payload_json
        from run_steps
        where tenant_id = %s
          and run_id = %s
          and id = %s
        for update
        """,
        (tenant_id, parent_run_id, parent_step_id),
    )
    parent_step = await step_cursor.fetchone()
    if parent_step is None:
        return None
    step_payload = parent_step.get("payload_json") if isinstance(parent_step.get("payload_json"), dict) else {}
    if (
        str(parent_step.get("step_key") or "") != step_key
        or str(step_payload.get("dispatch_id") or "") != dispatch_id
        or str(step_payload.get("dispatch_child_run_id") or "") != child_run_id
        or step_payload.get("dispatch_state") != "handed_off"
    ):
        return None

    reconciled_at = datetime.now(timezone.utc).isoformat()
    update_payload: dict[str, Any] = {
        "dispatch_state": dispatch_state,
        "dispatch_child_status": child_status,
        "dispatch_reconciled_at": reconciled_at,
        "dispatch_child_run_id": child_run_id,
    }
    if child_status == "succeeded":
        output = _safe_child_result_output(result_json)
        if output:
            update_payload["output"] = output
            lineage = artifact_lineage_contract(
                {
                    "checkpoint_id": f"checkpoint_{parent_step_id}",
                    "source_step_id": parent_step_id,
                },
                source_run_id=parent_run_id,
            )
            checkpoint_id = lineage.get("checkpoint_id")
            source_step_id = lineage.get("source_step_id")
            if checkpoint_id and source_step_id:
                update_payload["checkpoint_id"] = str(checkpoint_id)
                update_payload["source_step_id"] = str(source_step_id)
    elif child_status in {"failed", "cancelled"}:
        update_payload["error_code"] = _safe_child_error_code(error_code or f"child_run_{child_status}", child_status=child_status)
        update_payload["error"] = _safe_child_error_message(
            child_status=child_status,
            result_json=result_json,
            error_message=error_message,
        )

    update_cursor = await conn.execute(
        """
        update run_steps
        set
          payload_json = payload_json || %s::jsonb,
          status = %s,
          finished_at = coalesce(finished_at, now()),
          updated_at = now()
        where tenant_id = %s
          and run_id = %s
          and id = %s
          and payload_json->>'dispatch_id' = %s
          and payload_json->>'dispatch_child_run_id' = %s
          and payload_json->>'dispatch_state' = 'handed_off'
        returning id
        """,
        (
            dumps_json(update_payload),
            parent_step_status,
            tenant_id,
            parent_run_id,
            parent_step_id,
            dispatch_id,
            child_run_id,
        ),
    )
    updated = await update_cursor.fetchone()
    if updated is None:
        return None

    event_id = await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=parent_run_id,
        trace_id=child_run.get("trace_id"),
        event_type="multi_agent_dispatch_reconciled",
        stage="control",
        message="Multi-agent child run reconciled",
        visible_to_user=False,
        payload={
            "visible_to_user": False,
            "step_key": step_key,
            "dispatch_id": dispatch_id,
            "child_run_id": child_run_id,
            "child_status": child_status,
            "dispatch_state": dispatch_state,
        },
    )
    audit_id = await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=None,
        action="run.multi_agent.dispatch.reconcile",
        target_type="run_step",
        target_id=parent_step_id,
        trace_id=child_run.get("trace_id"),
        payload_json={
            "parent_run_id": parent_run_id,
            "parent_step_id": parent_step_id,
            "step_key": step_key,
            "dispatch_id": dispatch_id,
            "child_run_id": child_run_id,
            "child_status": child_status,
            "result_status": parent_step_status,
        },
    )
    await finalize_multi_agent_parent_run_if_ready(
        conn,
        tenant_id=tenant_id,
        parent_run_id=parent_run_id,
        triggered_by_child_run_id=child_run_id,
    )
    return {
        "parent_run_id": parent_run_id,
        "parent_step_id": parent_step_id,
        "child_run_id": child_run_id,
        "step_key": step_key,
        "status": parent_step_status,
        "dispatch_state": dispatch_state,
        "event_id": event_id,
        "audit_id": audit_id,
    }


async def list_run_steps(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          id, run_id, step_key, step_kind, status, title, role, sequence,
          payload_json, started_at, finished_at, created_at, updated_at
        from run_steps
        where tenant_id = %s and run_id = %s
        order by sequence asc, created_at asc
        """,
        (tenant_id, run_id),
    )
    return list(await cursor.fetchall())


async def list_admin_runs(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    cursor = await conn.execute(
        """
        select
          id as run_id,
          session_id,
          user_id,
          workspace_id,
          status,
          agent_id,
          execution_kind,
          skill_id,
          created_at,
          queued_at,
          started_at,
          finished_at,
          cancel_requested_at,
          cancel_requested_by,
          error_code,
          error_message
        from runs
        where tenant_id = %s
          and (%s::text is null or user_id = %s)
          and (%s::text is null or status = %s)
        order by created_at desc
        limit %s
        """,
        (tenant_id, user_id, user_id, status, status, limit),
    )
    return list(await cursor.fetchall())


async def get_admin_runtime_run_summary(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Return same-tenant run status and redacted recent failure aggregates for Admin Runtime."""
    status_cursor = await conn.execute(
        """
        select status, count(*) as count
        from runs
        where tenant_id = %s
        group by status
        """,
        (tenant_id,),
    )
    status_rows = list(await status_cursor.fetchall())
    by_status = {
        str(row["status"]): _coerce_int(row["count"])
        for row in status_rows
        if row.get("status") is not None
    }
    failure_cursor = await conn.execute(
        """
        select id, user_id, agent_id, error_code, error_message, created_at
        from runs
        where tenant_id = %s
          and status = 'failed'
        order by created_at desc
        limit %s
        """,
        (tenant_id, limit),
    )
    failure_rows = list(await failure_cursor.fetchall())
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "active": sum(by_status.get(status, 0) for status in ACTIVE_RUN_STATUSES),
        "terminal": sum(by_status.get(status, 0) for status in runs_api.TERMINAL_RUN_STATUSES),
        "recent_failures": [
            {
                "run_id": row["id"],
                "user_id": row.get("user_id"),
                "agent_id": row.get("agent_id"),
                "error_code": sanitize_public_text(row.get("error_code")) or None,
                "error_message": sanitize_public_text(row.get("error_message")),
                "created_at": row.get("created_at"),
            }
            for row in failure_rows
        ],
    }


async def get_admin_runtime_admission_summary(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    limit: int,
    top_user_limit: int = 10,
) -> dict[str, Any]:
    """Return same-tenant active-run admission pressure for Admin Runtime."""
    active_limit = max(int(limit), 0)
    top_limit = max(min(int(top_user_limit), 50), 1)
    totals_cursor = await conn.execute(
        """
        with grouped as (
          select user_id, count(*) as active
          from runs
          where tenant_id = %s
            and status in ('queued', 'running')
          group by user_id
        )
        select
          coalesce(sum(active), 0) as active_runs,
          count(*) filter (where user_id is not null) as active_users,
          count(*) filter (where user_id is not null and %s > 0 and active >= %s) as saturated_users
        from grouped
        """,
        (tenant_id, active_limit, active_limit),
    )
    totals = await totals_cursor.fetchone() or {}
    top_cursor = await conn.execute(
        """
        select user_id, count(*) as active
        from runs
        where tenant_id = %s
          and status in ('queued', 'running')
          and user_id is not null
        group by user_id
        order by count(*) desc, user_id asc
        limit %s
        """,
        (tenant_id, top_limit),
    )
    top_rows = list(await top_cursor.fetchall())
    top_users = [
        {
            "user_id": str(row["user_id"]),
            "active": _coerce_int(row["active"]),
            "saturated": active_limit > 0 and _coerce_int(row["active"]) >= active_limit,
        }
        for row in top_rows
        if row.get("user_id")
    ]
    return {
        "policy_active": active_limit > 0,
        "max_active_runs_per_user": active_limit,
        "active_runs": _coerce_int(totals.get("active_runs")),
        "active_users": _coerce_int(totals.get("active_users")),
        "saturated_users": _coerce_int(totals.get("saturated_users")),
        "top_users": top_users,
    }


async def get_admin_runtime_observability_summary(
    conn: AsyncConnection,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    """Return same-tenant observability seed metrics for the Admin Runtime overview."""
    cursor = await conn.execute(
        """
        with event_summary as (
          select
            count(*) as event_count,
            count(*) filter (where error_code is not null and error_code <> '') as event_error_count,
            avg(latency_ms) filter (where latency_ms is not null) as avg_latency_ms,
            max(latency_ms) as max_latency_ms,
            percentile_cont(0.5) within group (order by latency_ms)
              filter (where latency_ms is not null) as p50_latency_ms,
            percentile_cont(0.95) within group (order by latency_ms)
              filter (where latency_ms is not null) as p95_latency_ms,
            percentile_cont(0.99) within group (order by latency_ms)
              filter (where latency_ms is not null) as p99_latency_ms
          from run_events
          where tenant_id = %s
        ),
        artifact_summary as (
          select count(*) as artifact_count
          from artifacts
          where tenant_id = %s
        ),
        run_totals as (
          select
            count(*) filter (where error_code is not null and error_code <> '') as run_error_count,
            coalesce(sum(input_token_count), 0) as run_input_token_count,
            coalesce(sum(output_token_count), 0) as run_output_token_count,
            coalesce(sum(total_token_count), 0) as run_total_token_count,
            coalesce(sum(estimated_cost_minor), 0) as run_estimated_cost_minor
          from runs
          where tenant_id = %s
        ),
        error_types as (
          select coalesce(jsonb_object_agg(error_code, error_count), '{}'::jsonb) as error_types
          from (
            select error_code, count(*) as error_count
            from (
              select error_code
              from runs
              where tenant_id = %s
                and error_code is not null
                and error_code <> ''
              union all
              select error_code
              from run_events
              where tenant_id = %s
                and error_code is not null
                and error_code <> ''
            ) all_errors
            group by error_code
          ) errors
        )
        select
          event_summary.event_count,
          artifact_summary.artifact_count,
          run_totals.run_error_count + event_summary.event_error_count as error_count,
          error_types.error_types,
          event_summary.avg_latency_ms,
          event_summary.max_latency_ms,
          event_summary.p50_latency_ms,
          event_summary.p95_latency_ms,
          event_summary.p99_latency_ms,
          run_totals.run_input_token_count as input_token_count,
          run_totals.run_output_token_count as output_token_count,
          run_totals.run_total_token_count as total_token_count,
          run_totals.run_estimated_cost_minor as estimated_cost_minor
        from event_summary, artifact_summary, run_totals, error_types
        """,
        (tenant_id, tenant_id, tenant_id, tenant_id, tenant_id),
    )
    row = await cursor.fetchone() or {}
    raw_error_types = row.get("error_types") if isinstance(row.get("error_types"), dict) else {}
    error_types = {
        sanitized_key: _coerce_int(value)
        for key, value in raw_error_types.items()
        if (sanitized_key := sanitize_public_text(key))
    }
    avg_latency = row.get("avg_latency_ms")
    max_latency = row.get("max_latency_ms")
    p50_latency = row.get("p50_latency_ms")
    p95_latency = row.get("p95_latency_ms")
    p99_latency = row.get("p99_latency_ms")
    return {
        "event_count": _coerce_int(row.get("event_count")),
        "artifact_count": _coerce_int(row.get("artifact_count")),
        "error_count": _coerce_int(row.get("error_count")),
        "error_types": error_types,
        "error_categories": summarize_error_categories(error_types),
        "latency_ms": {
            "avg": _coerce_int(avg_latency) if avg_latency is not None else None,
            "max": _coerce_int(max_latency) if max_latency is not None else None,
            "p50": _coerce_int(p50_latency) if p50_latency is not None else None,
            "p95": _coerce_int(p95_latency) if p95_latency is not None else None,
            "p99": _coerce_int(p99_latency) if p99_latency is not None else None,
        },
        "token_counts": {
            "input": _coerce_int(row.get("input_token_count")),
            "output": _coerce_int(row.get("output_token_count")),
            "total": _coerce_int(row.get("total_token_count")),
        },
        "estimated_cost_minor": _coerce_int(row.get("estimated_cost_minor")),
    }


_SANDBOX_LEASE_RUNTIME_HANDLE_PROJECTION_KEYS = {
    "container_id",
    "container_name",
    "executor_url",
    "workspace_host_path",
    "workspace_container_path",
    "labels",
    "runtime_container_id",
    "runtime_container_name",
    "runtime_executor_url",
    "runtime_workspace_container_path",
    "runtime_handle_verified_at",
}


def _sandbox_lease_payload_projection(row: dict[str, Any]) -> dict[str, Any]:
    payload = sanitize_public_payload(
        row.get("lease_payload_json") if isinstance(row.get("lease_payload_json"), dict) else {}
    )
    if not isinstance(payload, dict):
        return {}
    return {
        key: value
        for key, value in payload.items()
        if str(key) not in _SANDBOX_LEASE_RUNTIME_HANDLE_PROJECTION_KEYS
    }


def _sandbox_lease_admin_projection(row: dict[str, Any]) -> dict[str, Any]:
    resource_limits = sanitize_public_payload(
        row.get("resource_limits_json") if isinstance(row.get("resource_limits_json"), dict) else {}
    )
    user_visible_payload = sanitize_public_payload(
        row.get("user_visible_payload_json") if isinstance(row.get("user_visible_payload_json"), dict) else {}
    )
    lease_payload = _sandbox_lease_payload_projection(row)
    return {
        "lease_id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "session_id": str(row["session_id"]),
        "run_id": str(row["run_id"]),
        "trace_id": str(row.get("trace_id") or standard_trace_id(str(row["run_id"]))),
        "sandbox_mode": str(row["sandbox_mode"]),
        "provider": str(row.get("provider") or "fake"),
        "status": str(row.get("status") or "active"),
        "browser_enabled": bool(row.get("browser_enabled")),
        "resource_limits": resource_limits if isinstance(resource_limits, dict) else {},
        "workspace": user_visible_payload if isinstance(user_visible_payload, dict) else {},
        "lease_payload": lease_payload,
        "heartbeat_at": row.get("heartbeat_at"),
        "expires_at": row.get("expires_at"),
        "released_at": row.get("released_at"),
        "release_reason": str(row.get("release_reason") or ""),
        "created_at": row.get("created_at"),
    }


async def get_admin_run_detail(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> dict[str, Any] | None:
    run = await get_run(conn, tenant_id=tenant_id, run_id=run_id)
    if run is None:
        return None
    run_contract_version = _required_schema_version(run, "schema_version", RUN_CONTRACT_VERSION, "invalid_run_contract")
    executor_schema_version = _required_schema_version(
        run,
        "executor_schema_version",
        EXECUTOR_RESULT_SCHEMA_VERSION,
        "invalid_executor_result_schema_version",
    )
    events = await list_run_events(conn, tenant_id=tenant_id, run_id=run_id)
    steps = await list_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
    artifacts = await list_run_artifacts(conn, tenant_id=tenant_id, run_id=run_id)
    sandbox_leases = await list_sandbox_leases_for_run(conn, tenant_id=tenant_id, run_id=run_id)
    skill_snapshots = _attach_skill_usage(
        await list_run_skill_snapshots(conn, tenant_id=tenant_id, run_id=run_id),
        events,
    )
    skill_snapshots = [_sanitize_skill_snapshot(snapshot) for snapshot in skill_snapshots]
    cursor = await conn.execute(
        """
        select id, user_id, action, target_type, target_id, trace_id, schema_version, payload_json, created_at
        from audit_logs
        where tenant_id = %s
          and (
            target_id = %s
            or payload_json->>'run_id' = %s
          )
        order by created_at asc
        """,
        (tenant_id, run_id, run_id),
    )
    audit_rows = list(await cursor.fetchall())
    run_input = sanitize_public_payload(run["input_json"] if isinstance(run.get("input_json"), dict) else {})
    if not isinstance(run_input, dict):
        run_input = {}
    run_result = sanitize_public_payload(run["result_json"] if isinstance(run.get("result_json"), dict) else {})
    if not isinstance(run_result, dict):
        run_result = {}
    return {
        "run": {
            "run_id": run["id"],
            "session_id": run["session_id"],
            "user_id": run["user_id"],
            "workspace_id": run["workspace_id"],
            "status": run["status"],
            "agent_id": run["agent_id"],
            "execution_kind": run.get("execution_kind") or RUN_EXECUTION_KIND_SKILL,
            "skill_id": run["skill_id"],
            "created_at": run["created_at"],
            "queued_at": run.get("queued_at"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "cancel_requested_at": run.get("cancel_requested_at"),
            "cancel_requested_by": run.get("cancel_requested_by"),
            "input": run_input,
            "result": run_result,
            "error_code": sanitize_public_text(run.get("error_code")) or None,
            "error_message": sanitize_public_text(run.get("error_message")),
            "trace_id": run.get("trace_id") or standard_trace_id(str(run["id"])),
            "contract_version": run_contract_version,
            "executor_schema_version": executor_schema_version,
        },
        "events": [
            {
                "event_id": item["id"],
                "schema_version": _required_schema_version(
                    item,
                    "schema_version",
                    EVENT_ENVELOPE_SCHEMA_VERSION,
                    "invalid_event_schema_version",
                ),
                "sequence": int(item.get("sequence") or 0),
                "trace_id": item.get("trace_id") or standard_trace_id(str(run["id"])),
                "type": item["event_type"],
                "stage": item["stage"],
                "message": sanitize_public_text(item.get("message")),
                "severity": item.get("severity") or "info",
                "visible_to_user": bool(item.get("visible_to_user", True)),
                "error_code": sanitize_public_text(item.get("error_code")) or None,
                "latency_ms": item.get("latency_ms"),
                "token_counts": {
                    "input": int(item.get("input_token_count") or 0),
                    "output": int(item.get("output_token_count") or 0),
                    "total": int(item.get("total_token_count") or 0),
                },
                "cost": {"estimated_cost_minor": int(item.get("estimated_cost_minor") or 0)},
                "payload": (
                    sanitized_payload
                    if isinstance(
                        sanitized_payload := sanitize_public_payload(item.get("payload_json") if isinstance(item.get("payload_json"), dict) else {}),
                        dict,
                    )
                    else {}
                ),
                "created_at": item["created_at"],
            }
            for item in events
        ],
        "steps": [
            {
                "step_id": item["id"],
                "run_id": item["run_id"],
                "step_key": item["step_key"],
                "step_kind": item["step_kind"],
                "status": item["status"],
                "title": sanitize_public_text(item.get("title")),
                "role": sanitize_public_text(item.get("role")) if item.get("role") is not None else None,
                "sequence": item["sequence"],
                "payload": (
                    sanitized_payload
                    if isinstance(
                        sanitized_payload := sanitize_public_payload(item.get("payload_json") if isinstance(item.get("payload_json"), dict) else {}),
                        dict,
                    )
                    else {}
                ),
                "started_at": item["started_at"],
                "finished_at": item["finished_at"],
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
            }
            for item in steps
        ],
        "artifacts": [
            {
                "artifact_id": item["id"],
                "trace_id": item.get("trace_id") or standard_trace_id(str(run["id"])),
                "artifact_type": item["artifact_type"],
                "label": sanitize_public_text(item.get("label")) or str(item["artifact_type"]),
                "content_type": item["content_type"],
                "size_bytes": item["size_bytes"],
                "manifest": artifact_manifest_contract(
                    artifact_type=str(item["artifact_type"]),
                    manifest=item.get("manifest_json") if isinstance(item.get("manifest_json"), dict) else {},
                    schema_version=_required_schema_version(
                        item,
                        "manifest_version",
                        ARTIFACT_MANIFEST_SCHEMA_VERSION,
                        "invalid_artifact_manifest_schema_version",
                    ),
                ),
                "created_at": item["created_at"],
            }
            for item in artifacts
        ],
        "sandbox_leases": [_sandbox_lease_admin_projection(item) for item in sandbox_leases],
        "skill_snapshots": skill_snapshots,
        "audit": [
            {
                "audit_id": item["id"],
                "schema_version": _required_schema_version(
                    item,
                    "schema_version",
                    AUDIT_EVENT_SCHEMA_VERSION,
                    "invalid_audit_event_schema_version",
                ),
                "trace_id": item.get("trace_id"),
                "user_id": item["user_id"],
                "action": item["action"],
                "target_type": item["target_type"],
                "target_id": item["target_id"],
                "payload": (
                    sanitized_payload
                    if isinstance(
                        sanitized_payload := sanitize_public_payload(item.get("payload_json") if isinstance(item.get("payload_json"), dict) else {}),
                        dict,
                    )
                    else {}
                ),
                "created_at": item["created_at"],
            }
            for item in audit_rows
        ],
    }


async def request_run_cancel(conn: AsyncConnection, *, tenant_id: str, user_id: str, run_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        with eligible_run as (
          select id, tenant_id, status, trace_id,
                 cancel_requested_at is null as cancel_requested_newly
          from runs
          where tenant_id = %s
            and id = %s
            and user_id = %s
            and (
              status in ('queued', 'running')
              or (
                status = 'cancelled'
                and exists (
                  select 1
                  from sandbox_leases
                  where sandbox_leases.tenant_id = runs.tenant_id
                    and sandbox_leases.run_id = runs.id
                    and sandbox_leases.status = 'active'
                )
              )
            )
          for update
        )
        update runs
        set
          cancel_requested_at = coalesce(cancel_requested_at, now()),
          cancel_requested_by = coalesce(cancel_requested_by, %s)
        from eligible_run
        where runs.tenant_id = eligible_run.tenant_id
          and runs.id = eligible_run.id
        returning runs.id, runs.status, eligible_run.trace_id,
                  eligible_run.cancel_requested_newly
        """,
        (tenant_id, run_id, user_id, user_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    if row.get("cancel_requested_newly"):
        await append_event(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=row.get("trace_id"),
            event_type="cancel_requested",
            stage="control",
            message="已请求取消",
            payload={"visible_to_user": True, "severity": "warning", "requested_by": user_id},
        )
    staged = await _stage_run_tool_permission_terminalization(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        target_status="cancelled" if row["status"] == "queued" else "cancel_requested",
        terminal_reason="run_cancel_requested",
    )
    progress = (
        await progress_run_tool_permission_terminalization(conn, tenant_id=tenant_id, run_id=run_id)
        if staged is not None
        else None
    )
    actual_terminal_status = progress.status if progress is not None and progress.is_terminal() else None
    active_sandbox_leases = await list_active_sandbox_leases_for_run(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    status = actual_terminal_status or ("cancelled" if row["status"] == "cancelled" else "cancel_requested")
    await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="run.cancel",
        target_type="run",
        target_id=run_id,
        trace_id=row.get("trace_id"),
        payload_json={
            "run_id": run_id,
            "result_status": status,
            "requested_by_role": "owner",
        },
    )
    result = {"run_id": row["id"], "status": status}
    if progress is not None and progress.did_transition and progress.needs_reconcile:
        result["_permission_terminalization_progress"] = progress
    if active_sandbox_leases:
        result["trace_id"] = row.get("trace_id")
        result["active_sandbox_leases"] = active_sandbox_leases
    return result


async def request_admin_run_cancel(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    admin_user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        with eligible_run as (
          select id, tenant_id, status, user_id, trace_id,
                 cancel_requested_at is null as cancel_requested_newly
          from runs
          where tenant_id = %s
            and id = %s
            and (
              status in ('queued', 'running')
              or (
                status = 'cancelled'
                and exists (
                  select 1
                  from sandbox_leases
                  where sandbox_leases.tenant_id = runs.tenant_id
                    and sandbox_leases.run_id = runs.id
                    and sandbox_leases.status = 'active'
                )
              )
            )
          for update
        )
        update runs
        set
          cancel_requested_at = coalesce(cancel_requested_at, now()),
          cancel_requested_by = coalesce(cancel_requested_by, %s)
        from eligible_run
        where runs.tenant_id = eligible_run.tenant_id
          and runs.id = eligible_run.id
        returning runs.id, runs.status, eligible_run.user_id, eligible_run.trace_id,
                  eligible_run.cancel_requested_newly
        """,
        (tenant_id, run_id, admin_user_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    if row.get("cancel_requested_newly"):
        await append_event(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=row.get("trace_id"),
            event_type="cancel_requested",
            stage="control",
            message="管理员已请求取消",
            payload={
                "visible_to_user": True,
                "severity": "warning",
                "requested_by": admin_user_id,
                "requested_by_role": "admin",
                "target_user_id": row.get("user_id"),
            },
        )
    staged = await _stage_run_tool_permission_terminalization(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        target_status="cancelled" if row["status"] == "queued" else "cancel_requested",
        terminal_reason="run_cancel_requested",
    )
    progress = (
        await progress_run_tool_permission_terminalization(conn, tenant_id=tenant_id, run_id=run_id)
        if staged is not None
        else None
    )
    actual_terminal_status = progress.status if progress is not None and progress.is_terminal() else None
    result_status = actual_terminal_status or ("cancelled" if row["status"] == "cancelled" else "cancel_requested")
    active_sandbox_leases = await list_active_sandbox_leases_for_run(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=admin_user_id,
        action="admin.run.cancel",
        target_type="run",
        target_id=run_id,
        trace_id=row.get("trace_id"),
        payload_json={
            "run_id": run_id,
            "target_user_id": row.get("user_id"),
            "result_status": result_status,
        },
    )
    result = {"run_id": row["id"], "status": result_status}
    if progress is not None and progress.did_transition and progress.needs_reconcile:
        result["_permission_terminalization_progress"] = progress
    if active_sandbox_leases:
        result["trace_id"] = row.get("trace_id")
        result["active_sandbox_leases"] = active_sandbox_leases
    return result


async def is_cancel_requested(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> bool:
    cursor = await conn.execute(
        "select cancel_requested_at from runs where tenant_id = %s and id = %s",
        (tenant_id, run_id),
    )
    row = await cursor.fetchone()
    return bool(row and row.get("cancel_requested_at"))


async def classify_success_commit_block(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> str:
    """Classify a failed success CAS while holding the owning run before permission rows."""

    cursor = await conn.execute(
        """
        select runs.status,
               runs.cancel_requested_at,
               runs.permission_terminalization_target,
               exists (
                 select 1
                 from run_tool_permission_requests as permission_request
                 where permission_request.tenant_id = runs.tenant_id
                   and permission_request.run_id = runs.id
                   and permission_request.status in ('pending', 'decided')
               ) as has_unterminalized_permission
        from runs
        where runs.tenant_id = %s and runs.id = %s
        for update
        """,
        (tenant_id, run_id),
    )
    row = await cursor.fetchone()
    if row is None or str(row.get("status") or "") in runs_api.TERMINAL_RUN_STATUSES:
        return "stale_terminal_state"
    if row.get("cancel_requested_at") or str(row.get("permission_terminalization_target") or "") in {
        "cancel_requested",
        "cancelled",
    }:
        return "cancel_requested"
    if bool(row.get("has_unterminalized_permission")):
        return "tool_permission_pending"
    return "stale_terminal_state"


async def copy_run_as_new_task(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    source = await get_authorized_run(conn, tenant_id=tenant_id, user_id=user_id, run_id=run_id)
    if source is None:
        return None
    source_input = source["input_json"] if isinstance(source.get("input_json"), dict) else {}
    sanitized_source_input = strip_caller_run_auth_snapshot_fields(sanitize_user_control_input(source_input))
    inherited_roles = normalize_roles(source.get("principal_roles") or [])
    inherited_department_id = str(source.get("principal_department_id") or "")
    inherited_auth_source = source.get("auth_source")
    inherited_authz_policy_version = int(source.get("authz_policy_version") or 1)
    inherited_authority_source = str(
        source.get("authority_source") or inherited_auth_source or ""
    )
    inherited_authority_checked_at = source.get("authority_checked_at")
    source_execution_input = (
        source_input.get("input")
        if isinstance(source_input.get("input"), dict)
        else source_input
    )
    if isinstance(source_execution_input, dict):
        source_execution_input = normalize_run_input_for_enqueue(source_execution_input, redact_public=True)
        source_execution_input.pop("resume", None)
    else:
        source_execution_input = {}
    source_execution_snapshot = copied_run_execution_snapshot(source_input)
    source_execution_kind = str(
        source.get("execution_kind")
        or source_execution_snapshot.get("execution_kind")
        or RUN_EXECUTION_KIND_SKILL
    )
    admitted_profile_revision, admitted_profile_hash = (
        admitted_agent_profile_pins_for_copy(
            source,
            source_execution_snapshot,
        )
    )
    upgrade_legacy_chat_to_harness = (
        is_legacy_synthetic_chat_identity(
            agent_id=source.get("agent_id"),
            skill_id=source.get("skill_id"),
            execution_kind=source_execution_kind,
        )
        and admitted_profile_revision is None
        and admitted_profile_hash is None
    )
    execution_kind = (
        RUN_EXECUTION_KIND_HARNESS_CHAT
        if upgrade_legacy_chat_to_harness
        else source_execution_kind
    )
    copied_skill_id = None if upgrade_legacy_chat_to_harness else source.get("skill_id")
    skill_version = str(source_execution_snapshot.get("skill_version") or "")
    skill_refs = (
        source_execution_snapshot["skill_manifests"]
        if "skill_manifests" in source_execution_snapshot
        else []
    )
    skill_manifests = await materialize_run_skill_manifests(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        skill_manifest_refs=skill_refs,
    )
    skill_transport = skill_manifest_refs(skill_manifests)
    release_decision_payload = source_execution_snapshot.get("release_decision") or {}
    executor_type = str(source_execution_snapshot.get("executor_type") or "")
    if upgrade_legacy_chat_to_harness:
        skill_version = None
        skill_transport = []
        skill_manifests = []
        release_decision_payload = {}
        executor_type = HARNESS_CHAT_EXECUTOR_TYPE
    if execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT:
        if (
            copied_skill_id is not None
            or skill_version
            or release_decision_payload
            or skill_manifests
            or executor_type != HARNESS_CHAT_EXECUTOR_TYPE
        ):
            raise RepositoryConflictError("run_execution_skill_identity_mismatch")
        await authorize_selected_chat_mcp_tools(
            conn,
            tenant_id=tenant_id,
            tool_ids=extract_run_mcp_tool_ids(source_execution_input),
            principal_department_id=inherited_department_id,
            principal_roles=inherited_roles,
            is_admin=bool(set(inherited_roles).intersection(ADMIN_ROLE_ALIASES)),
            permissions=[],
        )
    elif execution_kind == RUN_EXECUTION_KIND_SKILL:
        if (
            not isinstance(source.get("skill_id"), str)
            or not str(source.get("skill_id")).strip()
        ):
            raise RepositoryConflictError("run_execution_skill_identity_mismatch")
        require_replay_source_identity(
            pinned_version=skill_version,
            pinned_executor_type=executor_type,
            release_decision=release_decision_payload,
            skill_manifests=skill_manifests,
        )
        await validate_run_skill_snapshots_for_dispatch(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            skill_manifests=skill_manifests,
            release_decision=release_decision_payload,
        )
        await authorize_replay_run_capabilities(
            conn,
            tenant_id=tenant_id,
            agent_id=source["agent_id"],
            skill_id=source["skill_id"],
            pinned_version=skill_version,
            pinned_executor_type=executor_type,
            skill_manifests=skill_manifests,
            normalized_input=source_execution_input,
            principal_department_id=inherited_department_id,
            principal_roles=inherited_roles,
            is_admin=bool(set(inherited_roles).intersection(ADMIN_ROLE_ALIASES)),
            permissions=[],
        )
    else:
        raise RepositoryConflictError("run_execution_skill_identity_mismatch")
    new_run_id = new_id("run")
    copied_execution_input = {**source_execution_input, "copied_from_run_id": run_id}
    completed_step_outputs, completed_step_checkpoints = await _completed_steps_for_resume(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    if completed_step_outputs:
        resume_payload: dict[str, Any] = {
            "copied_from_run_id": run_id,
            "completed_step_outputs": completed_step_outputs,
        }
        if completed_step_checkpoints:
            resume_payload["completed_step_checkpoints"] = completed_step_checkpoints
        copied_execution_input["resume"] = resume_payload
    copied_input_json = {
        **sanitized_source_input,
        "input": copied_execution_input,
        "copied_from_run_id": run_id,
    }
    copied_input_json.update(
        execution_kind=execution_kind,
        executor_type=executor_type,
        skill_version=skill_version,
        release_decision=release_decision_payload,
        skill_manifests=skill_transport,
        context_snapshot_id=None,
        context_snapshot={},
        schema_version=(
            RUN_PAYLOAD_SCHEMA_VERSION_V2
            if execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT
            else str(
                source_execution_snapshot.get("schema_version")
                or RUN_PAYLOAD_SCHEMA_VERSION
            )
        ),
    )
    copied_input_json.update(preserved_server_owned_execution_snapshot(source_execution_snapshot))
    copied_execution_snapshot = copied_run_execution_snapshot(copied_input_json)
    copied_input_json.update(copied_execution_snapshot)
    _require_json_size(copied_input_json, max_bytes=RUN_INPUT_MAX_BYTES, code="run_input_too_large")
    session_generation = await allocate_session_run_generation(
        conn,
        tenant_id=tenant_id,
        workspace_id=str(source["workspace_id"]),
        user_id=user_id,
        session_id=str(source["session_id"]),
        agent_id=str(source["agent_id"]),
    )
    await conn.execute(
        """
        insert into runs(
          id, tenant_id, workspace_id, session_id, user_id, agent_id, execution_kind, skill_id,
          trace_id, schema_version, executor_schema_version,
          principal_roles, principal_department_id, auth_source,
          authz_policy_version, authority_source, authority_checked_at,
          admitted_agent_profile_revision, admitted_agent_profile_hash,
          status, input_json, queued_at, copied_from_run_id, session_generation
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, 'queued', %s::jsonb, now(), %s, %s)
        """,
        (
            new_run_id,
            tenant_id,
            source["workspace_id"],
            source["session_id"],
            user_id,
            source["agent_id"],
            execution_kind,
            copied_skill_id,
            standard_trace_id(new_run_id),
            RUN_CONTRACT_VERSION,
            EXECUTOR_RESULT_SCHEMA_VERSION,
            dumps_json(inherited_roles),
            inherited_department_id,
            inherited_auth_source,
            inherited_authz_policy_version,
            inherited_authority_source,
            inherited_authority_checked_at,
            admitted_profile_revision,
            admitted_profile_hash,
            dumps_json(copied_input_json),
            run_id,
            session_generation,
        ),
    )
    if execution_kind == RUN_EXECUTION_KIND_SKILL:
        await insert_run_skill_snapshots_at_creation(
            conn,
            tenant_id=tenant_id,
            run_id=new_run_id,
            skill_manifests=skill_manifests,
            release_decision=release_decision_payload,
        )
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=new_run_id,
        event_type="run_created",
        stage="control",
        message="已复制为新任务",
        payload={"visible_to_user": True, "copied_from_run_id": run_id},
    )
    await append_message(
        conn,
        tenant_id=tenant_id,
        session_id=source["session_id"],
        run_id=new_run_id,
        role="assistant",
        content="已复制为新任务，将继续执行未完成步骤。",
        metadata_json={
            "type": "copy_run_anchor",
            "copied_from_run_id": run_id,
            "agent_id": source["agent_id"],
            "skill_id": copied_skill_id,
        },
    )
    return {
        "session_id": source["session_id"],
        "run_id": new_run_id,
        "agent_id": source["agent_id"],
        "execution_kind": execution_kind,
        "skill_id": copied_skill_id,
        "workspace_id": source["workspace_id"],
        "principal_roles": inherited_roles,
        "principal_department_id": inherited_department_id,
        "auth_source": inherited_auth_source,
        "release_policy_version": "",
        **copied_execution_snapshot,
    }


async def retry_run_as_new_task(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    source = await get_authorized_run(conn, tenant_id=tenant_id, user_id=user_id, run_id=run_id, for_update=True)
    if source is None:
        return None
    source_status = str(source.get("status") or "")
    if source_status not in RETRYABLE_RUN_STATUSES:
        raise RepositoryConflictError("status_not_retryable")
    active_retry = await get_active_retry_for_source_run(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
    )
    if active_retry is not None:
        raise RepositoryConflictError("retry_already_active")
    copied = await copy_run_as_new_task(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
    )
    if copied is None:
        return None
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=source.get("trace_id"),
        event_type="retry_requested",
        stage="control",
        message="已请求重试",
        payload={"visible_to_user": True, "new_run_id": copied["run_id"]},
    )
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=str(copied["run_id"]),
        event_type="run_retry_created",
        stage="control",
        message="已创建重试任务",
        payload={"visible_to_user": True, "copied_from_run_id": run_id},
    )
    await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="run.retry",
        target_type="run",
        target_id=run_id,
        trace_id=source.get("trace_id"),
        payload_json={
            "source_run_id": run_id,
            "new_run_id": copied["run_id"],
            "source_status": source_status,
        },
    )
    return copied


async def resume_run_as_new_task(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    """Create a queued resume child run from a non-active source with reusable output."""
    source = await get_authorized_run(conn, tenant_id=tenant_id, user_id=user_id, run_id=run_id, for_update=True)
    if source is None:
        return None
    source_status = str(source.get("status") or "")
    if source_status in ACTIVE_RUN_STATUSES:
        raise RepositoryConflictError("active_run")
    completed_step_outputs, _completed_step_checkpoints = await _completed_steps_for_resume(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    if not completed_step_outputs:
        raise RepositoryConflictError("no_checkpoint_outputs")
    active_resume = await get_active_resume_for_source_run(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
    )
    if active_resume is not None:
        raise RepositoryConflictError("resume_already_active")
    copied = await copy_run_as_new_task(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        run_id=run_id,
    )
    if copied is None:
        return None
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=source.get("trace_id"),
        event_type="resume_requested",
        stage="control",
        message="已请求从 checkpoint 恢复",
        payload={"visible_to_user": True, "new_run_id": copied["run_id"]},
    )
    await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=str(copied["run_id"]),
        event_type="run_resume_created",
        stage="control",
        message="已创建恢复任务",
        payload={"visible_to_user": True, "copied_from_run_id": run_id},
    )
    await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="run.resume",
        target_type="run",
        target_id=run_id,
        trace_id=source.get("trace_id"),
        payload_json={
            "source_run_id": run_id,
            "new_run_id": copied["run_id"],
            "source_status": source_status,
        },
    )
    return copied


def copied_run_execution_snapshot(input_json: object) -> dict[str, Any]:
    """Project every QueueRunPayload non-identity field from copied run input JSON."""
    source = input_json if isinstance(input_json, dict) else {}
    file_ids = source.get("file_ids")
    execution_input = source.get("input")
    release_decision = source.get("release_decision")
    skill_manifests = source.get("skill_manifests")
    context_snapshot = source.get("context_snapshot")
    skill_version = source.get("skill_version")
    context_snapshot_id = source.get("context_snapshot_id")
    model_id = source.get("model_id")
    model_value = source.get("model_value")
    agent_profile = source.get("agent_profile")
    schema_version = source.get("schema_version")
    execution_kind = source.get("execution_kind")
    snapshot = {
        "file_ids": list(file_ids) if isinstance(file_ids, list) else [],
        "input": dict(execution_input) if isinstance(execution_input, dict) else {},
        "executor_type": str(source.get("executor_type") or ""),
        "execution_kind": (
            str(execution_kind)
            if execution_kind
            in {RUN_EXECUTION_KIND_HARNESS_CHAT, RUN_EXECUTION_KIND_SKILL}
            else RUN_EXECUTION_KIND_SKILL
        ),
        "skill_version": skill_version if isinstance(skill_version, str) else None,
        "release_decision": dict(release_decision) if isinstance(release_decision, dict) else {},
        "skill_manifests": (
            [dict(item) for item in skill_manifests]
            if isinstance(skill_manifests, list)
            and all(isinstance(item, dict) for item in skill_manifests)
            else skill_manifests
            if "skill_manifests" in source
            else []
        ),
        "context_snapshot_id": context_snapshot_id if isinstance(context_snapshot_id, str) else None,
        "context_snapshot": dict(context_snapshot) if isinstance(context_snapshot, dict) else {},
        "model_id": model_id if isinstance(model_id, str) else None,
        "model_value": model_value if isinstance(model_value, str) else None,
        "schema_version": schema_version
        if isinstance(schema_version, str) and schema_version
        else RUN_PAYLOAD_SCHEMA_VERSION,
    }
    if isinstance(agent_profile, dict):
        snapshot["agent_profile"] = dict(agent_profile)
    return snapshot


def preserved_server_owned_execution_snapshot(source_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return admitted execution facts that sanitization must never reconstruct from a caller."""

    preserved = {
        "model_id": source_snapshot.get("model_id"),
        "model_value": source_snapshot.get("model_value"),
    }
    agent_profile = source_snapshot.get("agent_profile")
    if isinstance(agent_profile, dict):
        preserved["agent_profile"] = dict(agent_profile)
    return preserved


def admitted_agent_profile_pins_for_copy(
    source_run: dict[str, Any],
    source_snapshot: dict[str, Any],
) -> tuple[int | None, str | None]:
    """Require a profile's private snapshot and durable pins to remain identical on descendants."""

    revision = source_run.get("admitted_agent_profile_revision")
    content_hash = source_run.get("admitted_agent_profile_hash")
    profile = source_snapshot.get("agent_profile")
    if profile is None:
        if revision is not None or content_hash is not None:
            raise RepositoryConflictError("agent_profile_snapshot_missing")
        return None, None
    if not isinstance(profile, dict):
        raise RepositoryConflictError("agent_profile_snapshot_invalid")
    profile_revision = profile.get("revision")
    profile_hash = profile.get("content_hash")
    profile_agent_id = profile.get("agent_id")
    if (
        not isinstance(profile_revision, int)
        or isinstance(profile_revision, bool)
        or profile_revision < 1
        or not isinstance(profile_hash, str)
        or not profile_hash
        or profile_agent_id != source_run.get("agent_id")
        or revision != profile_revision
        or content_hash != profile_hash
        or not isinstance(source_snapshot.get("model_id"), str)
        or not source_snapshot.get("model_id")
        or not isinstance(source_snapshot.get("model_value"), str)
        or not source_snapshot.get("model_value")
    ):
        raise RepositoryConflictError("agent_profile_snapshot_invalid")
    return profile_revision, profile_hash


async def update_run_input_execution_snapshot(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    execution_snapshot: dict[str, Any],
) -> None:
    """Merge one canonical copied-run execution snapshot in a tenant-scoped update."""
    canonical_snapshot = copied_run_execution_snapshot(execution_snapshot)
    serialized_snapshot = compact_json_dumps(canonical_snapshot)
    cursor = await conn.execute(
        """
        select id, input_json
        from runs
        where tenant_id = %s
          and id = %s
          and (
            context_snapshot_id is null
            and coalesce(%s::jsonb->>'context_snapshot_id', '') = ''
            and coalesce(%s::jsonb->'context_snapshot'->>'context_snapshot_id', '') = ''
            or (
              context_snapshot_id is not null
              and %s::jsonb->>'context_snapshot_id' = context_snapshot_id
              and %s::jsonb->'context_snapshot'->>'context_snapshot_id' = context_snapshot_id
            )
          )
        for update
        """,
        (
            tenant_id,
            run_id,
            serialized_snapshot,
            serialized_snapshot,
            serialized_snapshot,
            serialized_snapshot,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryConflictError("context_snapshot_binding_invalid")
    existing_input = row.get("input_json")
    if not isinstance(existing_input, dict):
        existing_input = {}
    merged_input = {**existing_input, **canonical_snapshot}
    _require_json_size(
        merged_input,
        max_bytes=RUN_INPUT_MAX_BYTES,
        code="run_input_too_large",
    )
    updated = await conn.execute(
        """
        update runs
        set input_json = %s::jsonb
        where tenant_id = %s and id = %s
        returning id
        """,
        (compact_json_dumps(merged_input), tenant_id, run_id),
    )
    if await updated.fetchone() is None:
        raise RepositoryConflictError("context_snapshot_binding_invalid")


async def _completed_steps_for_resume(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    cursor = await conn.execute(
        """
        select id, step_key, payload_json
        from run_steps
        where tenant_id = %s
          and run_id = %s
          and status = 'succeeded'
        order by sequence asc, created_at asc
        """,
        (tenant_id, run_id),
    )
    rows = await cursor.fetchall()
    outputs: dict[str, str] = {}
    checkpoints: dict[str, dict[str, str]] = {}
    for row in rows:
        payload = row.get("payload_json") or {}
        if not isinstance(payload, dict) or payload.get("output") is None:
            continue
        step_key = str(row["step_key"])
        outputs[step_key] = str(payload["output"])
        lineage = artifact_lineage_contract(
            {
                "checkpoint_id": payload.get("checkpoint_id"),
                "source_step_id": payload.get("source_step_id") or row.get("id"),
            },
            source_run_id=payload.get("copied_from_run_id") or run_id,
        )
        checkpoint_id = lineage.get("checkpoint_id")
        source_step_id = lineage.get("source_step_id")
        source_run_id = lineage.get("source_run_id")
        if checkpoint_id and source_step_id and source_run_id:
            checkpoints[step_key] = {
                "checkpoint_id": str(checkpoint_id),
                "source_step_id": str(source_step_id),
                "copied_from_run_id": str(source_run_id),
            }
    return outputs, checkpoints


async def create_file(
    conn: AsyncConnection,
    *,
    file_id: str,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str | None,
    original_name: str,
    content_type: str,
    size_bytes: int,
    storage_key: str,
    sha256: str,
) -> None:
    await conn.execute(
        """
        insert into files(id, tenant_id, workspace_id, user_id, session_id, original_name, content_type, size_bytes, storage_key, sha256)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            file_id,
            tenant_id,
            workspace_id,
            user_id,
            session_id,
            original_name,
            content_type,
            size_bytes,
            storage_key,
            sha256,
        ),
    )


async def authorize_files_for_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    file_ids: list[str],
    reusable_file_ids: list[str] | None = None,
    input_modes: list[object] | None = None,
    agent_profile_supported_input_types: list[str] | None = None,
    agent_profile_supported_file_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Lock and validate run input files before any run creation side effect."""

    rows: list[dict[str, Any]] = []
    reusable_ids = set(reusable_file_ids or [])
    if not reusable_ids.issubset(file_ids):
        raise RepositoryConflictError("file_scope_mismatch")
    for file_id in file_ids:
        if file_id in reusable_ids:
            cursor = await conn.execute(
                """
                select files.id, files.tenant_id, files.workspace_id, files.user_id,
                       files.session_id, files.run_id, files.original_name,
                       files.content_type, files.size_bytes, files.sha256
                from files
                join runs on runs.id = files.run_id
                  and runs.tenant_id = files.tenant_id
                  and runs.workspace_id = files.workspace_id
                  and runs.user_id = files.user_id
                  and runs.session_id = files.session_id
                  and runs.input_json->>'context_snapshot_id' = runs.context_snapshot_id
                  and runs.input_json->'context_snapshot'->>'context_snapshot_id' = runs.context_snapshot_id
                join run_context_snapshots authorized_snapshot
                  on authorized_snapshot.id = runs.context_snapshot_id
                  and authorized_snapshot.tenant_id = files.tenant_id
                  and authorized_snapshot.workspace_id = files.workspace_id
                  and authorized_snapshot.user_id = files.user_id
                  and authorized_snapshot.session_id = files.session_id
                  and authorized_snapshot.run_id = files.run_id
                  and authorized_snapshot.context_kind = 'executor'
                  and authorized_snapshot.included_file_ids ? files.id
                where files.id = %s and files.lifecycle_state = 'active'
                for update of files
                """,
                (file_id,),
            )
        else:
            cursor = await conn.execute(
                """
            select id, tenant_id, workspace_id, user_id, session_id, run_id,
                   original_name, content_type, size_bytes, sha256
            from files
            where id = %s and lifecycle_state = 'active'
            for update
            """,
                (file_id,),
            )
        row = await cursor.fetchone()
        if row is None:
            raise RepositoryNotFoundError("file_not_found")
        if row["tenant_id"] != tenant_id or row["workspace_id"] != workspace_id:
            raise RepositoryConflictError("file_scope_mismatch")
        if row["user_id"] != user_id:
            raise RepositoryConflictError("file_user_mismatch")
        if file_id in reusable_ids:
            if row["session_id"] != session_id or not row["run_id"]:
                raise RepositoryConflictError("file_session_mismatch")
        else:
            if row["session_id"] and row["session_id"] != session_id:
                raise RepositoryConflictError("file_session_mismatch")
            if row["run_id"] and row["run_id"] != run_id:
                raise RepositoryConflictError("file_already_bound")
        rows.append(dict(row))
    if agent_profile_supported_input_types is not None:
        if rows and "file" not in agent_profile_supported_input_types:
            raise RepositoryConflictError("agent_profile_file_input_not_supported")
        allowed_file_types = agent_profile_supported_file_types or []
        if rows and not all(
            profile_file_type_allowed(row, allowed_file_types=allowed_file_types)
            for row in rows
        ):
            raise RepositoryConflictError("agent_profile_file_type_not_supported")
    if input_modes is not None and has_file_input_mode(input_modes):
        compatible_ids = compatible_reusable_file_ids(rows, input_modes=input_modes)
        if len(compatible_ids) != len(rows):
            raise RepositoryConflictError("file_required_for_skill")
    return rows


def _agent_profile_file_type_allowed(
    row: dict[str, Any],
    *,
    allowed_file_types: list[str],
) -> bool:
    return profile_file_type_allowed(row, allowed_file_types=allowed_file_types)


async def bind_files_to_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    file_ids: list[str],
) -> None:
    await authorize_files_for_run(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        file_ids=file_ids,
    )
    for file_id in file_ids:
        await conn.execute(
            """
            update files
            set session_id = %s, run_id = %s
            where id = %s and lifecycle_state = 'active'
            """,
            (session_id, run_id, file_id),
        )


async def get_file(conn: AsyncConnection, *, tenant_id: str, file_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        select *
        from files
        where tenant_id = %s and id = %s
        """,
        (tenant_id, file_id),
    )
    return await cursor.fetchone()


async def mark_run_running(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> dict[str, Any] | None:
    cursor = await conn.execute(
        """
        update runs
        set status = 'running', started_at = coalesce(started_at, now())
        from sessions
        where runs.tenant_id = %s
          and runs.id = %s
          and runs.status = 'queued'
          and sessions.id = runs.session_id
          and sessions.tenant_id = runs.tenant_id
          and sessions.workspace_id = runs.workspace_id
          and sessions.user_id = runs.user_id
          and sessions.agent_id = runs.agent_id
        returning runs.id, runs.tenant_id, runs.workspace_id, runs.user_id,
                  runs.session_id, runs.agent_id, runs.execution_kind,
                  runs.skill_id, runs.trace_id,
                  runs.principal_roles, runs.principal_department_id, runs.auth_source,
                  runs.admitted_agent_profile_revision,
                  runs.admitted_agent_profile_hash,
                  sessions.admitted_agent_profile_revision
                    as session_admitted_agent_profile_revision,
                  sessions.admitted_agent_profile_hash
                    as session_admitted_agent_profile_hash,
                  runs.input_json
        """,
        (tenant_id, run_id),
    )
    row = await cursor.fetchone()
    return dict(row) if row is not None else None


async def complete_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    result_json: dict[str, Any],
) -> bool:
    _require_json_size(result_json, max_bytes=RUN_RESULT_MAX_BYTES, code="run_result_too_large")
    latency_ms, input_tokens, output_tokens, total_tokens, estimated_cost_minor = _result_observability_values(result_json)
    lock_cursor = await conn.execute(
        """
        select id
        from runs
        where runs.tenant_id = %s and runs.id = %s
          and runs.status not in ('succeeded', 'failed', 'cancelled')
          and runs.cancel_requested_at is null
          and runs.permission_terminalization_target is null
        for update
        """,
        (tenant_id, run_id),
    )
    locked_run = await lock_cursor.fetchone()
    if locked_run is None:
        return False
    clock_cursor = await conn.execute("select clock_timestamp() as authority_now", ())
    clock_row = await clock_cursor.fetchone()
    authority_now = clock_row.get("authority_now") if clock_row is not None else None
    if not isinstance(authority_now, datetime):
        raise RepositoryConflictError("run_completion_authority_clock_missing")
    permission_cursor = await conn.execute(
        """
        select id, status, decision, expires_at
        from run_tool_permission_requests
        where tenant_id = %s
          and run_id = %s
          and status in ('pending', 'decided')
        for update
        """,
        (tenant_id, run_id),
    )
    permission_rows = list(await permission_cursor.fetchall())
    valid_allow_for_run_ids: list[str] = []
    for permission in permission_rows:
        status = str(permission.get("status") or "")
        if status == "pending":
            return False
        expires_at = permission.get("expires_at")
        valid_allow_for_run = status == "decided" and str(permission.get("decision") or "") == "allow_for_run" and isinstance(expires_at, datetime) and expires_at > authority_now
        if not valid_allow_for_run:
            return False
        valid_allow_for_run_ids.append(str(permission.get("id") or ""))
    if any(not request_id for request_id in valid_allow_for_run_ids):
        raise RepositoryConflictError("allow_for_run_id_missing")
    cursor = await conn.execute(
        """
        update runs
        set
          status = 'succeeded',
          result_json = %s::jsonb,
          finished_at = now(),
          error_code = null,
          error_message = null,
          latency_ms = %s,
          input_token_count = %s,
          output_token_count = %s,
          total_token_count = %s,
          estimated_cost_minor = %s
        where runs.tenant_id = %s
          and runs.id = %s
        returning id
        """,
        (
            dumps_json(result_json),
            latency_ms,
            input_tokens,
            output_tokens,
            total_tokens,
            estimated_cost_minor,
            tenant_id,
            run_id,
        ),
    )
    row = await cursor.fetchone()
    if row is None:
        raise RepositoryConflictError("run_completion_lost_after_run_lock")
    if valid_allow_for_run_ids:
        consumed_cursor = await conn.execute(
            """
            update run_tool_permission_requests
            set status = 'consumed',
                reason = case when reason = '' then 'allow_for_run_completed' else reason end,
                updated_at = clock_timestamp()
            where tenant_id = %s
              and run_id = %s
              and id = any(%s::text[])
              and status = 'decided'
              and decision = 'allow_for_run'
            returning id
            """,
            (tenant_id, run_id, valid_allow_for_run_ids),
        )
        consumed_ids = {str(item.get("id") or "") for item in await consumed_cursor.fetchall()}
        if consumed_ids != set(valid_allow_for_run_ids):
            raise RepositoryConflictError("allow_for_run_consumption_mismatch")
    from app.streaming.redis import ensure_run_terminal_intent
    await ensure_run_terminal_intent(conn, tenant_id=tenant_id, run_id=run_id, status="succeeded")
    return True


async def fail_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    error_code: str,
    error_message: str,
    result_json: dict[str, Any] | None = None,
    terminal_reason: str = "run_failed",
) -> runs_api.RunTerminalizationProgress:
    _require_json_size(result_json or {}, max_bytes=RUN_RESULT_MAX_BYTES, code="run_result_too_large")
    latency_ms, input_tokens, output_tokens, total_tokens, estimated_cost_minor = _result_observability_values(result_json)
    staged = await _stage_run_tool_permission_terminalization(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        target_status="failed",
        terminal_reason=terminal_reason,
        result_json=result_json,
        error_code=error_code,
        error_message=error_message,
    )
    if staged is None:
        return runs_api.RunTerminalizationProgress(completed=False, status=None)
    staged_target = str(staged.get("permission_terminalization_target") or "")
    if staged_target != "failed":
        return runs_api.RunTerminalizationProgress(completed=False, status=staged_target or None)
    await conn.execute(
        """
        update runs
        set latency_ms = %s,
            input_token_count = %s,
            output_token_count = %s,
            total_token_count = %s,
            estimated_cost_minor = %s
        where tenant_id = %s
          and id = %s
          and status not in ('succeeded', 'failed', 'cancelled')
        """,
        (latency_ms, input_tokens, output_tokens, total_tokens, estimated_cost_minor, tenant_id, run_id),
    )
    progress = await progress_run_tool_permission_terminalization(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    return runs_api.progress_for_requested_status(progress, requested_status="failed")


async def mark_run_enqueue_failed(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str | None,
    run_id: str,
    trace_id: str | None = None,
) -> runs_api.RunTerminalizationProgress:
    """Compensate one post-commit enqueue failure with a non-queued durable outcome."""

    error_code = "queue_enqueue_failed"
    error_message = "Queue admission failed; retry this run."
    progress = await fail_run(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        error_code=error_code,
        error_message=error_message,
        result_json={"message": error_message, "retryable": True},
    )
    if progress.did_transition:
        await append_event(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            event_type="queue_enqueue_failed",
            stage="queue",
            message="Queue admission failed; the run was marked failed.",
            payload={
                "visible_to_user": False,
                "error_code": error_code,
                "retryable": True,
            },
        )
        await append_audit_log(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            action="run.queue.enqueue_failed",
            target_type="run",
            target_id=run_id,
            trace_id=trace_id or standard_trace_id(run_id),
            payload_json={"error_code": error_code, "retryable": True},
        )
    return progress


async def cancel_run(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    result_json: dict[str, Any] | None = None,
) -> runs_api.RunTerminalizationProgress:
    _require_json_size(result_json or {}, max_bytes=RUN_RESULT_MAX_BYTES, code="run_result_too_large")
    staged = await _stage_run_tool_permission_terminalization(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        target_status="cancelled",
        terminal_reason="run_cancelled",
        result_json=result_json,
    )
    if staged is None:
        return runs_api.RunTerminalizationProgress(completed=False, status=None)
    staged_target = str(staged.get("permission_terminalization_target") or "")
    if staged_target != "cancelled":
        return runs_api.RunTerminalizationProgress(completed=False, status=staged_target or None)
    progress = await progress_run_tool_permission_terminalization(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    return runs_api.progress_for_requested_status(progress, requested_status="cancelled")


async def _cancel_open_run_steps(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> None:
    await conn.execute(
        """
        update run_steps
        set status = 'cancelled',
            finished_at = coalesce(finished_at, now()),
            updated_at = now()
        where tenant_id = %s
          and run_id = %s
          and status in ('pending', 'running')
        """,
        (tenant_id, run_id),
    )


async def _fail_open_run_steps(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> None:
    await conn.execute(
        """
        update run_steps
        set status = case when status = 'running' then 'failed' else 'cancelled' end,
            finished_at = coalesce(finished_at, now()),
            updated_at = now()
        where tenant_id = %s
          and run_id = %s
          and status in ('pending', 'running')
        """,
        (tenant_id, run_id),
    )


async def create_artifact(
    conn: AsyncConnection,
    *,
    artifact_id: str,
    tenant_id: str,
    run_id: str,
    trace_id: str | None = None,
    artifact_type: str,
    label: str,
    content_type: str,
    storage_key: str,
    size_bytes: int,
    manifest_json: dict[str, Any],
) -> None:
    _require_json_size(
        manifest_json,
        max_bytes=ARTIFACT_MANIFEST_MAX_BYTES,
        code="artifact_manifest_too_large",
    )
    resolved_trace_id = trace_id or standard_trace_id(run_id)
    await conn.execute(
        """
        insert into artifacts(
          id, tenant_id, run_id, trace_id, artifact_type, label, content_type, storage_key,
          size_bytes, manifest_version, manifest_json
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            artifact_id,
            tenant_id,
            run_id,
            resolved_trace_id,
            artifact_type,
            label,
            content_type,
            storage_key,
            size_bytes,
            ARTIFACT_MANIFEST_SCHEMA_VERSION,
            dumps_json(manifest_json),
        ),
    )


async def append_audit_log(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str | None,
    action: str,
    target_type: str,
    target_id: str,
    trace_id: str | None = None,
    payload_json: dict[str, Any] | None = None,
) -> str:
    resolved_payload = payload_json or {}
    _require_json_size(resolved_payload, max_bytes=AUDIT_PAYLOAD_MAX_BYTES, code="audit_payload_too_large")
    audit_id = new_id("aud")
    await conn.execute(
        """
        insert into audit_logs(id, tenant_id, user_id, action, target_type, target_id, trace_id, schema_version, payload_json)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            audit_id,
            tenant_id,
            user_id,
            action,
            target_type,
            target_id,
            trace_id,
            AUDIT_EVENT_SCHEMA_VERSION,
            dumps_json(resolved_payload),
        ),
    )
    return audit_id


async def append_capability_authorization_denial_audit(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str | None,
    error: RepositoryAuthorizationError,
    source: str,
) -> str | None:
    """Persist one structured capability denial after its source transaction rolls back."""

    denial = error.denial
    if denial is None:
        return None
    payload = denial.audit_payload()
    payload["source"] = source
    return await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        action="capability_distribution.denied",
        target_type=denial.capability_kind,
        target_id=denial.capability_id,
        trace_id=standard_trace_id(denial.capability_id),
        payload_json=payload,
    )


async def list_authorized_session_runs(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    workspace_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    workspace_filter = "and runs.workspace_id = %s" if workspace_id else ""
    params: list[Any] = [tenant_id, user_id, session_id]
    if workspace_id:
        params.append(workspace_id)
    params.append(limit)
    cursor = await conn.execute(
        f"""
        select runs.id, runs.trace_id, runs.schema_version, runs.agent_id,
               runs.execution_kind, runs.skill_id,
               runs.status, runs.error_code, runs.error_message, runs.created_at, runs.queued_at,
               runs.started_at, runs.finished_at, runs.result_json,
               runs.session_generation, queue_admission.queue_admission_ordinal
        from runs
        left join lateral (
          select case
            when run_events.payload_json->>'queue_admission_ordinal' ~ '^[0-9]+$'
             and length(run_events.payload_json->>'queue_admission_ordinal') <= 19
             and (
               length(run_events.payload_json->>'queue_admission_ordinal') < 19
               or run_events.payload_json->>'queue_admission_ordinal' <= '9223372036854775807'
             )
            then (run_events.payload_json->>'queue_admission_ordinal')::bigint
            else null
          end as queue_admission_ordinal
          from run_events
          where run_events.tenant_id = runs.tenant_id
            and run_events.run_id = runs.id
            and run_events.event_type = 'queued'
          order by run_events.sequence desc
          limit 1
        ) queue_admission on true
        where runs.tenant_id = %s
          and runs.user_id = %s
          and runs.session_id = %s
          {workspace_filter}
        -- A non-null generation is the sole current-run authority.  Legacy
        -- unordered rows remain display-only and never outrank it.
        order by runs.session_generation desc nulls last,
                 runs.created_at desc,
                 queue_admission.queue_admission_ordinal desc nulls last,
                 runs.queued_at desc nulls last,
                 runs.id desc
        limit %s
        """,
        tuple(params),
    )
    return list(await cursor.fetchall())


async def get_latest_authorized_session_run_input(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
) -> dict[str, Any] | None:
    """Read the latest run input for an active principal-owned session scope."""

    cursor = await conn.execute(
        """
        select runs.input_json
        from runs
        join sessions on sessions.id = runs.session_id
          and sessions.tenant_id = runs.tenant_id
          and sessions.workspace_id = runs.workspace_id
          and sessions.user_id = runs.user_id
          and sessions.agent_id = runs.agent_id
        where runs.tenant_id = %s
          and runs.workspace_id = %s
          and runs.user_id = %s
          and runs.session_id = %s
          and sessions.status = 'active'
        order by runs.session_generation desc nulls last,
                 runs.created_at desc,
                 runs.id desc
        limit 1
        """,
        (tenant_id, workspace_id, user_id, session_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    input_json = row.get("input_json")
    return input_json if isinstance(input_json, dict) else None


# MCP catalog persistence and Chat-selection ownership live in app.mcp.repository.
# Keep these established names for Chat, Agent Apps, and worker call sites outside
# the re-chartered path set.
from app.mcp import repository as _mcp_repository  # noqa: E402

authorize_selected_chat_mcp_tools = _mcp_repository.authorize_selected_chat_mcp_tools
get_mcp_tool_registry_entry = _mcp_repository.get_mcp_tool_registry_entry
list_authorized_chat_mcp_tools = _mcp_repository.list_authorized_chat_mcp_tools
list_workbench_mcp_tools = _mcp_repository.list_workbench_mcp_tools
mcp_runtime_metadata_usable = _mcp_repository.mcp_runtime_metadata_usable
