import re
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import partial as _partial
from typing import Any

from pydantic import ValidationError

from app.artifacts.infrastructure import records_postgres as artifacts_records_postgres
from app.conversations.infrastructure import postgres as conversations_postgres
from app.identity.infrastructure import audit_postgres as identity_audit_postgres
from app.identity.infrastructure import capability_distributions_postgres as identity_capability_distributions_postgres
from app.platform.postgres import errors as platform_errors
from app.platform.postgres import values as platform_values
from app.runs.infrastructure import capability_admission_postgres as runs_capability_admission_postgres
from app.runs.infrastructure import postgres as runs_postgres
from app.skills.infrastructure import postgres as skills_postgres
from app.skills.infrastructure import resolution_postgres as skills_resolution_postgres
from app.skills.infrastructure import run_snapshots_postgres as skills_run_snapshots_postgres
from app.streaming.infrastructure import run_events_postgres as streaming_run_events_postgres

from app.bootstrap.worker_attempt_lifecycle import (
    build_worker_attempt_lifecycle_ports,
)
from app.agent_apps.capability_state import (
    bind_validated_controlled_skill_evidence, exact_invoked_skills, project_agent_capability_state,
)
from app.agent_apps.api import reauthorize_bound_profile_for_worker_dispatch
from app.auth import AuthPrincipal, is_ai_admin, normalize_roles
from app.capabilities import required_artifact_types_for_skill
from app.capability_distribution import (
    CapabilityAccessContext,
    CapabilityAccessDecision,
    CapabilityDistributionSubject,
    capability_distribution_audit_payload,
    resolve_capability_access,
)
from app.bootstrap.context import materialize_queued_worker_context_snapshot
from app.context_builder import (
    ensure_public_context_provenance,
    executor_context_pack_from_snapshot,
)
from app.context_manifest import (
    CONTEXT_MANIFEST_SCHEMA_VERSION,
    sanitize_context_manifest_payload,
)
from app.control_plane_contracts import (
    CONTEXT_SNAPSHOT_SCHEMA_VERSION,
    LEGACY_SYNTHETIC_CHAT_SKILL_ID,
    RUN_EXECUTION_KIND_HARNESS_CHAT,
    artifact_lineage_contract,
    artifact_manifest_contract,
    sanitize_public_text,
    standard_trace_id,
)
from app.db import transaction
from app.execution.api import (
    WorkerAttemptLifecycle,
    WorkerExecutorReconciliation,
    WorkerQueueLease, WorkerRunCancelled, AnswerPersistenceLimits, assistant_artifact_metadata, sanitize_assistant_message,
    bind_worker_attempt_lifecycle,
    build_artifact_execution_owner,
    build_artifact_records,
    fail_run_for_worker as _fail_run_for_worker,
    executor_exception_failure as _executor_exception_failure,
    locked_run_payload_candidate as _locked_run_payload_candidate,
    materialize_worker_answer,
    promote_artifact_reservations,
    predispatch_failure_result as _pre_dispatch_failure_result, reconciliation_agent_profile_binding_matches as _reconciliation_agent_profile_binding_matches,
    restored_executor_reconciliation_queue_payload as _restored_executor_reconciliation_queue_payload,
    submit_run_until_cancelled as _submit_run_until_cancelled_with_owner,
    time,
    with_locked_run_model_snapshot as _with_locked_run_model_snapshot,
    WorkerRuntimeSandboxLease as _WorkerRuntimeSandboxLease,
    create_worker_runtime_sandbox_lease as _create_worker_runtime_sandbox_lease,
    release_worker_runtime_sandbox_lease as _release_worker_runtime_sandbox_lease,
)
from app.execution_boundary import (
    decide_worker_execution_boundary as _worker_execution_boundary_decision,
    ordinary_worker_run_uses_runtime_sandbox as _ordinary_run_uses_runtime_sandbox,
)
from app.executors.base import (
    ExecutorDispatchAccepted,
    ExecutorResult,
    RunExecutionOwner,
    RunPayload,
    project_execution_spec_to_run_payload,
)
from app.executors.registry import AdapterRegistry
from app.models import QueueRunPayload
from app.mcp import api as mcp_api
from app.platform.postgres.limits import MESSAGE_CONTENT_MAX_BYTES, RUN_RESULT_MAX_BYTES, json_size_bytes
from app.persistence.artifacts import promote_provisional_artifact_cleanup, reserve_provisional_artifact_cleanup
from app.principal_authority import (
    CURRENT_PRINCIPAL_DENIAL_REASON,
    resolve_current_principal,
)
from app.queue import QUEUE_ATTEMPT_ID_FIELD
from app.runs.api import (
    RunAttemptLifecycleService,
    RunLifecycleService,
    compile_execution_spec_for_dispatch, worker_dispatch_fence, persist_assistant_with_provider_coverage,
    load_run_model_snapshot as _load_run_model_snapshot,
)
from app.required_tool_contract import (
    RequiredCapabilityDecision,
    builtin_capability_subjects,
    required_tool_authorization_for_run,
    required_tool_completion_for_run,
    with_boundary_sandbox_local_tool_subjects, with_harness_local_tool_subjects,
)
from app.platform.postgres import sandbox_leases as sandbox_lease_repository
from app.runtime.sandbox.executor_client import (
    canonical_executor_reported_failure_code,
    executor_reported_failure_message,
    normalize_executor_reported_failure,
)
from app.settings import get_settings
from app.streaming.api import (
    WorkerV4Capabilities,
    admit_v4_stream,
    persist_worker_event,
    publish_run_event,
)
from app.streaming.worker_projection import persist_worker_failure_event
from app.skills.api import restore_admitted_skill_manifest_authority
from app.skills.catalog import (
    RUNTIME_AUTHORIZED_SKILL_CATALOG_KEY,
    RUNTIME_AUTHORIZED_SKILL_MANIFESTS_KEY,
    AuthorizedSkillCatalogBinding,
    AuthorizedSkillCatalogError,
    AuthorizedSkillCatalogResolution,
    resolve_authorized_skill_catalog,
)
from app.skills.execution_profiles import effective_skill_execution_profile
from app.tool_policy import evaluate_tool_policy
from app.validation import assert_canonical_sha256, assert_safe_id
from app.worker_principal_authority import (
    _identity_mismatch_fields,
    _locked_run_identity,
    _payload_identity,
    _resolve_current_principal_before_dispatch,
)


_ANSWER_PERSISTENCE_LIMITS = AnswerPersistenceLimits(MESSAGE_CONTENT_MAX_BYTES, RUN_RESULT_MAX_BYTES, json_size_bytes)


_submit_run_until_cancelled = _partial(
    _submit_run_until_cancelled_with_owner,
    owner_factory=RunExecutionOwner,
)


@dataclass(frozen=True)
class WorkerOutcome:
    status: str
    run_id: str | None
    error_code: str | None = None
    error_message: str | None = None


class WorkerDirectAssistantDeltaError(RuntimeError):
    """Reject an unsupported second ingress for public assistant text."""


class _WorkerSuccessCommitBlocked(Exception):
    """Abort success-visible writes when the final run transition loses its guard."""


@dataclass(frozen=True)
class _WorkerTerminalAfterTransaction:
    outcome: WorkerOutcome
    payload: QueueRunPayload


@dataclass(frozen=True)
class _WorkerCapabilityDecision:
    capability_kind: str
    capability_id: str
    decision: CapabilityAccessDecision


@dataclass(frozen=True)
class _WorkerToolPolicyAudit:
    tool_id: str
    allowed: bool
    reason: str
    risk_level: str
    write_capable: bool
    decision: str


@dataclass(frozen=True)
class _WorkerCapabilityAuthorization:
    payload: QueueRunPayload
    principal: AuthPrincipal
    decisions: tuple[_WorkerCapabilityDecision, ...]
    denial: _WorkerCapabilityDecision | None = None
    tool_policy_audits: tuple[_WorkerToolPolicyAudit, ...] = ()
    required_tool_decision: RequiredCapabilityDecision | None = None


@dataclass(frozen=True)
class _WorkerAdminBypassAudit:
    tenant_id: str
    user_id: str
    target_type: str
    target_id: str
    trace_id: str
    payload_json: dict[str, Any]


_EXECUTOR_ERROR_REQUEST_ID_RE = re.compile(
    r"\brequest[_ -]?id\s*[:=]\s*[A-Za-z0-9._~+/=-]+\b",
    re.IGNORECASE,
)
def _public_executor_failure_message(result: ExecutorResult) -> str:
    generic_message = "Executor reported failure"
    if result.executor_payload.get("sandbox_runtime_used") is True:
        safe_code = canonical_executor_reported_failure_code(result.result.get("error_code"))
        return executor_reported_failure_message(safe_code)
    for candidate in (
        result.result.get("message"),
        result.result.get("sdk_error"),
        result.executor_payload.get("sdk_error"),
    ):
        raw_text = _EXECUTOR_ERROR_REQUEST_ID_RE.sub(
            "request id: [redacted-id]",
            str(candidate or ""),
        )
        safe_text = sanitize_public_text(raw_text)
        if safe_text and safe_text != generic_message:
            return safe_text
    return generic_message


def _normalize_sandbox_reported_failure(result: ExecutorResult) -> ExecutorResult:
    if (
        result.status != "failed"
        or result.executor_payload.get("sandbox_runtime_used") is not True
    ):
        return result
    safe_code = canonical_executor_reported_failure_code(result.result.get("error_code"))
    safe_result = normalize_executor_reported_failure(
        {**result.result, "status": "failed", "error_code": safe_code}
    )
    safe_result.pop("status", None)
    safe_result.pop("error_message", None)
    safe_executor_payload = dict(result.executor_payload)
    if "sdk_error" in safe_executor_payload:
        safe_executor_payload["sdk_error"] = safe_code
    return replace(
        result,
        result=safe_result,
        executor_payload=safe_executor_payload,
    )


def parse_queue_payload(raw: dict[str, Any]) -> QueueRunPayload:
    return QueueRunPayload.model_validate(raw)


class InvalidLeasedQueueEnvelope(ValueError):
    """The queue-private lease identity is missing or cannot be trusted."""


@dataclass(frozen=True)
class LeasedQueueEnvelope:
    """Validated business payload paired with its immutable queue attempt."""

    payload: QueueRunPayload
    attempt_id: str


def parse_leased_queue_envelope(raw: dict[str, Any]) -> LeasedQueueEnvelope:
    """Validate queue authority before removing its private attempt field."""

    attempt_id = raw.get(QUEUE_ATTEMPT_ID_FIELD)
    if not isinstance(attempt_id, str) or not attempt_id:
        raise InvalidLeasedQueueEnvelope("Queue lease attempt identity is required.")
    try:
        assert_safe_id(attempt_id, "attempt_id")
    except ValueError as exc:
        raise InvalidLeasedQueueEnvelope("Queue lease attempt identity is invalid.") from exc
    parseable_raw = dict(raw)
    parseable_raw.pop(QUEUE_ATTEMPT_ID_FIELD)
    return LeasedQueueEnvelope(payload=parse_queue_payload(parseable_raw), attempt_id=attempt_id)


async def _fail_run_with_write(
    conn,
    *,
    payload: QueueRunPayload,
    tenant_id: str,
    run_id: str,
    error_code: str,
    error_message: str,
    result_json: dict[str, Any] | None = None,
    v4_capabilities: WorkerV4Capabilities,
    attempt_lifecycle: WorkerAttemptLifecycle,
) -> tuple[bool, Any | None]:
    result_json = result_json or _pre_dispatch_failure_result(
        error_code, "worker", reason=error_code
    )
    return await _fail_run_for_worker(
        conn,
        payload=payload,
        tenant_id=tenant_id,
        run_id=run_id,
        error_code=error_code,
        error_message=error_message,
        capabilities=v4_capabilities,
        attempt_lifecycle=attempt_lifecycle,
        result_json=result_json,
    )


def _artifact_download_url(artifact_id: str) -> str:
    return f"/api/ai/artifacts/{artifact_id}/download"


FORBIDDEN_ARTIFACT_MARKERS = ("/tmp/", "tenants/", "workspaces/", ":\\", ":/")
FORBIDDEN_ARTIFACT_KEYS = {
    "storage_key",
    "local_path",
    "review_result",
    "artifact_path",
    "output_path",
    "runner",
    "runner_path",
    "executable_path",
    "cwd",
}
def _sanitize_artifact_manifest(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in FORBIDDEN_ARTIFACT_KEYS:
                continue
            sanitized = _sanitize_artifact_manifest(item)
            if sanitized is not None:
                cleaned[key] = sanitized
        return cleaned
    if isinstance(value, list):
        cleaned_items = [_sanitize_artifact_manifest(item) for item in value]
        return [item for item in cleaned_items if item is not None]
    if isinstance(value, str) and any(marker in value for marker in FORBIDDEN_ARTIFACT_MARKERS):
        return None
    return value


async def append_user_event(
    conn,
    *,
    tenant_id: str,
    run_id: str,
    event_type: str,
    stage: str,
    message: str,
    payload: dict[str, Any] | None = None,
    trace_id: str | None = None,
    latency_ms: int | None = None,
    input_token_count: int | None = None,
    output_token_count: int | None = None,
    total_token_count: int | None = None,
    estimated_cost_minor: int | None = None,
) -> None:
    # fail_run/cancel_run terminalize through the repository-owned durable
    # progress seam.  The repository writes the one authoritative terminal
    # event/audit when its final transition succeeds; worker branches retain
    # diagnostics but must not duplicate that run-level fact.
    if event_type in {"run_failed", "run_cancelled"}:
        return
    merged = {"visible_to_user": True, "severity": "info"}
    if payload:
        merged.update(payload)
    event_kwargs: dict[str, Any] = {}
    if trace_id is not None:
        event_kwargs["trace_id"] = trace_id
    if latency_ms is not None:
        event_kwargs["latency_ms"] = latency_ms
    if input_token_count is not None:
        event_kwargs["input_token_count"] = input_token_count
    if output_token_count is not None:
        event_kwargs["output_token_count"] = output_token_count
    if total_token_count is not None:
        event_kwargs["total_token_count"] = total_token_count
    if estimated_cost_minor is not None:
        event_kwargs["estimated_cost_minor"] = estimated_cost_minor
    await streaming_run_events_postgres.append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        event_type=event_type,
        stage=stage,
        message=message,
        payload=merged,
        **event_kwargs,
    )


def _int_payload_value(payload: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(payload.get(key) or default)
    except (TypeError, ValueError):
        return default


def _int_mapping_value(payload: dict[str, Any], *keys: str) -> int:
    for key in keys:
        try:
            value = payload.get(key)
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _usd_cost_to_minor_units(value: Any) -> int:
    try:
        minor_units = (Decimal(str(value)) * Decimal(100)).quantize(
            Decimal(1),
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError):
        return 0
    return max(int(minor_units), 0)


def _sdk_usage_observability(executor_payload: dict[str, Any]) -> dict[str, Any]:
    usage = executor_payload.get("sdk_usage")
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = _int_mapping_value(usage, "input_tokens", "input")
    input_tokens += _int_mapping_value(usage, "cache_creation_input_tokens")
    input_tokens += _int_mapping_value(usage, "cache_read_input_tokens")
    output_tokens = _int_mapping_value(usage, "output_tokens", "output")
    total_tokens = _int_mapping_value(usage, "total_tokens", "total")
    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens
    estimated_cost_minor = _int_mapping_value(usage, "estimated_cost_minor", "cost_minor")
    if estimated_cost_minor <= 0:
        estimated_cost_minor = _usd_cost_to_minor_units(
            usage.get("total_cost_usd") or usage.get("cost_usd") or usage.get("estimated_cost_usd")
        )
    return {
        "token_counts": {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
        },
        "cost": {"estimated_cost_minor": estimated_cost_minor},
    }


def _has_sdk_observability(executor_payload: dict[str, Any]) -> bool:
    sdk_observability = _sdk_usage_observability(executor_payload)
    token_counts = sdk_observability["token_counts"]
    return (
        token_counts["input"] > 0
        or token_counts["output"] > 0
        or token_counts["total"] > 0
        or sdk_observability["cost"]["estimated_cost_minor"] > 0
    )


def _executor_observability(
    executor_payload: dict[str, Any],
    *,
    latency_ms: int,
) -> dict[str, Any]:
    sdk_observability = _sdk_usage_observability(executor_payload)
    sdk_token_counts = sdk_observability["token_counts"]
    input_tokens = _int_payload_value(executor_payload, "input_token_count", sdk_token_counts["input"])
    output_tokens = _int_payload_value(executor_payload, "output_token_count", sdk_token_counts["output"])
    total_default = sdk_token_counts["total"] or (input_tokens + output_tokens)
    total_tokens = _int_payload_value(executor_payload, "total_token_count", total_default)
    return {
        "latency_ms": latency_ms,
        "token_counts": {
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
        },
        "cost": {
            "estimated_cost_minor": _int_payload_value(
                executor_payload,
                "estimated_cost_minor",
                sdk_observability["cost"]["estimated_cost_minor"],
            ),
        },
    }


def _event_observability_kwargs(observability: dict[str, Any], executor_payload: dict[str, Any]) -> dict[str, Any]:
    metric_keys = {
        "input_token_count",
        "output_token_count",
        "total_token_count",
        "estimated_cost_minor",
    }
    if not any(key in executor_payload for key in metric_keys) and not _has_sdk_observability(
        executor_payload
    ):
        return {}
    token_counts = observability["token_counts"]
    return {
        "latency_ms": observability["latency_ms"],
        "input_token_count": token_counts["input"],
        "output_token_count": token_counts["output"],
        "total_token_count": token_counts["total"],
        "estimated_cost_minor": observability["cost"]["estimated_cost_minor"],
    }


def _sdk_import_status() -> str:
    try:
        import claude_agent_sdk  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - optional SDK imports may fail arbitrarily.
        return f"unavailable:{exc.__class__.__name__}"
    return "ok"


def _worker_runtime_evidence(*, worker_id: str | None, executor_type: str) -> dict[str, Any]:
    settings = get_settings()
    return {
        "worker_id": worker_id,
        "executor_type": executor_type,
        "claude_agent_sdk_enabled": bool(settings.claude_agent_sdk_enabled),
        "claude_agent_model": settings.claude_agent_model,
        "claude_agent_sdk_import": _sdk_import_status(),
    }


def _skill_snapshot_from_result(result: ExecutorResult) -> dict[str, list[str]]:
    source = {**result.executor_payload, **result.result}
    snapshot: dict[str, list[str]] = {
        "allowed_skills": [],
        "staged_skills": [],
        "used_skills": [],
    }
    for key in ("allowed_skills", "staged_skills"):
        value = source.get(key)
        if isinstance(value, list):
            snapshot[key] = [str(item) for item in value]
    snapshot["used_skills"] = _native_used_skills_from_result(result)
    return snapshot


def _native_used_skills_from_result(result: ExecutorResult) -> list[str]:
    semantic_evidence = {**result.result, **result.executor_payload}
    exact_used = exact_invoked_skills(semantic_evidence)
    raw = semantic_evidence.get("used_skills")
    if not isinstance(raw, list):
        return []
    used: list[str] = []
    for item in raw:
        skill_name = str(item).strip()
        if skill_name in exact_used and skill_name not in used:
            used.append(skill_name)
    return used


def _required_agent_skill_id(payload: QueueRunPayload) -> str | None:
    # Agent Profiles register an authorized Skill Set. Invocation is an SDK
    # decision; exact hook evidence is still validated for every actual call.
    return None


def _skill_manifests_from_result(result: ExecutorResult) -> list[dict[str, Any]]:
    source = {**result.executor_payload, **result.result}
    raw = source.get("skill_manifests")
    if not isinstance(raw, list):
        return []
    used_skills = set(_native_used_skills_from_result(result))
    used_skills_source = str(result.executor_payload.get("used_skills_source") or "").strip()
    manifests: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        manifest = dict(item)
        skill_id = str(manifest.get("skill_id") or "").strip()
        manifest["used"] = bool(skill_id and skill_id in used_skills)
        if manifest["used"]:
            manifest["used_skills_source"] = used_skills_source
        manifests.append(manifest)
    return manifests


def _skill_manifests_for_persistence(
    result: ExecutorResult,
    payload: QueueRunPayload,
) -> list[dict[str, Any]]:
    return restore_admitted_skill_manifest_authority(
        _skill_manifests_from_result(result),
        admitted_manifests=payload.skill_manifests,
    )


def _source_json_from_skill_manifest(
    item: dict[str, Any],
    *,
    release_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return skills_postgres.run_skill_snapshot_source_json(item, release_decision=release_decision)


def _without_skill_snapshot_files(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_skill_snapshot_files(item)
            for key, item in value.items()
            if str(key) != "files"
        }
    if isinstance(value, list):
        return [_without_skill_snapshot_files(item) for item in value]
    return value


def _dependency_ids_from_manifest(item: dict[str, Any]) -> list[str]:
    raw = item.get("dependency_ids")
    if not isinstance(raw, list):
        return []
    return [str(value) for value in raw]


def _payload_from_locked_run(
    locked_run: object,
    *,
    run_identity: dict[str, str],
) -> QueueRunPayload | None:
    candidate = _locked_run_payload_candidate(
        locked_run,
        run_identity=run_identity,
        harness_execution_kind=RUN_EXECUTION_KIND_HARNESS_CHAT,
    )
    if candidate is None:
        return None
    try:
        return QueueRunPayload.model_validate(candidate)
    except ValidationError:
        return None


def _locked_agent_profile_identity_valid(
    agent_profile: dict[str, Any],
    locked_run: object,
) -> bool:
    if not isinstance(locked_run, dict):
        return False
    pin_fields = (
        "admitted_agent_profile_revision",
        "admitted_agent_profile_hash",
        "session_admitted_agent_profile_revision",
        "session_admitted_agent_profile_hash",
    )
    if not all(field in locked_run for field in pin_fields):
        return False
    pinned_revision = locked_run.get("admitted_agent_profile_revision")
    pinned_hash = locked_run.get("admitted_agent_profile_hash")
    session_pinned_revision = locked_run.get("session_admitted_agent_profile_revision")
    session_pinned_hash = locked_run.get("session_admitted_agent_profile_hash")
    if not agent_profile:
        return all(
            value is None
            for value in (
                pinned_revision,
                pinned_hash,
                session_pinned_revision,
                session_pinned_hash,
            )
        )
    try:
        if (
            not isinstance(pinned_revision, int)
            or isinstance(pinned_revision, bool)
            or pinned_revision < 1
            or not isinstance(session_pinned_revision, int)
            or isinstance(session_pinned_revision, bool)
            or session_pinned_revision < 1
        ):
            return False
        assert_canonical_sha256(pinned_hash, "agent_profile_hash_invalid")
        assert_canonical_sha256(session_pinned_hash, "agent_profile_hash_invalid")
    except ValueError:
        return False
    return (
        agent_profile.get("agent_id") == locked_run.get("agent_id")
        and agent_profile.get("revision") == pinned_revision
        and agent_profile.get("content_hash") == pinned_hash
        and pinned_revision == session_pinned_revision
        and pinned_hash == session_pinned_hash
    )


def _agent_profile_snapshot_matches_authority(
    payload: QueueRunPayload,
    admission: object,
) -> bool:
    private_execution_input = getattr(admission, "private_execution_input", None)
    authority_mcp_tool_ids = getattr(admission, "mcp_tool_ids", None)
    if (
        not isinstance(private_execution_input, dict)
        or not isinstance(authority_mcp_tool_ids, tuple)
    ):
        return False
    try:
        queued_mcp_tool_ids = tuple(runs_capability_admission_postgres.extract_run_mcp_tool_ids(payload.input))
    except (
        platform_errors.RepositoryAuthorizationError,
        platform_errors.RepositoryConflictError,
    ):
        return False
    if queued_mcp_tool_ids != authority_mcp_tool_ids:
        return False
    expected = dict(private_execution_input)
    if payload.execution_kind != RUN_EXECUTION_KIND_HARNESS_CHAT:
        authority_skill = getattr(admission, "skill", None)
        if (
            not isinstance(authority_skill, dict)
            or str(authority_skill.get("skill_id") or "") != str(payload.skill_id or "")
            or str(authority_skill.get("skill_version") or "")
            != str(payload.skill_version or "")
            or not payload.skill_version
        ):
            return False
    return payload.agent_profile == expected


def _locked_run_trace_id(payload: QueueRunPayload, locked_run: object) -> str:
    if isinstance(locked_run, dict) and locked_run.get("trace_id"):
        return str(locked_run["trace_id"])
    return standard_trace_id(payload.run_id)


def _locked_run_principal(locked_run: object, run_identity: dict[str, str]) -> AuthPrincipal:
    locked = locked_run if isinstance(locked_run, dict) else {}
    raw_roles = locked.get("principal_roles")
    roles = normalize_roles(raw_roles if isinstance(raw_roles, (list, tuple, set)) else [])
    return AuthPrincipal(
        user_id=run_identity["user_id"],
        display_name=run_identity["user_id"],
        tenant_id=run_identity["tenant_id"],
        department_id=str(locked.get("principal_department_id") or ""),
        roles=roles,
        permissions=[],
        source=str(locked.get("auth_source") or ""),
    )


def _worker_capability_context(principal: AuthPrincipal) -> CapabilityAccessContext:
    return CapabilityAccessContext(
        tenant_id=principal.tenant_id,
        department_id=principal.department_id,
        roles=principal.roles,
        is_admin=is_ai_admin(principal),
        permissions=principal.permissions,
    )


def _denied_capability_decision(
    reason: str,
    *,
    source: CapabilityAccessDecision | None = None,
) -> CapabilityAccessDecision:
    return CapabilityAccessDecision(
        visible=False,
        usable=False,
        manageable=False,
        admin_bypass=False,
        decision_reason=reason,
        department_scope_ids=list(source.department_scope_ids) if source is not None else [],
        role_scope_ids=list(source.role_scope_ids) if source is not None else [],
        scope_mode=source.scope_mode if source is not None else "allowlist",
    )


def _worker_capability_record(
    capability_kind: str,
    capability_id: str,
    decision: CapabilityAccessDecision,
) -> _WorkerCapabilityDecision:
    return _WorkerCapabilityDecision(
        capability_kind=capability_kind,
        capability_id=capability_id,
        decision=decision,
    )


def _mcp_tool_lifecycle_status(tool: dict[str, Any]) -> str:
    if (
        str(tool.get("effective_status") or "disabled") == "active"
        and str(tool.get("server_status") or "disabled") == "active"
        and bool(tool.get("visible_to_user", True))
    ):
        return "active"
    return "disabled"


_builtin_capability_subjects = _partial(
    builtin_capability_subjects,
    canonical_manifest=effective_skill_execution_profile,
    canonical_identities=skills_postgres.canonical_builtin_tool_identities,
)


def _mcp_capability_subject(tool: dict[str, Any], distribution: CapabilityAccessDecision) -> dict[str, Any] | None:
    server_id = str(tool.get("server_id") or "")
    tool_id = str(tool.get("tool_id") or "")
    allowed_tools = tool.get("allowed_tools")
    if not mcp_api.mcp_runtime_metadata_usable(tool):
        return None
    tool_identifier = allowed_tools[0]
    subject: dict[str, Any] = {
        "identity": f"mcp__{server_id}__{tool_identifier}",
        "mcp_server": server_id,
        "mcp_tool": tool_identifier,
        "public_tool_label": (sanitize_public_text(tool.get("name")) or tool_id)[:120],
        "public_tool_category": "mcp",
        "registered": True,
        "declared": True,
        "active": all(str(tool.get(key) or "") == "active" for key in ("registry_status", "policy_status", "server_status")),
        "distributed": distribution.usable,
        "identity_authorized": True,
        "object_authorized": True,
        "parameters_authorized": True,
        "risk_level": str(tool.get("risk_level") or "low"),
        "write_capable": bool(tool.get("write_capable")),
        "parameter_delegation": "external_mcp",
    }
    subject.update(capability_id=tool_id)
    return subject


def _canonical_authorized_mcp_scope(
    container: dict[str, Any],
    *,
    allowed_tool_ids: set[str],
) -> dict[str, Any]:
    rebuilt = dict(container)
    requested: list[str] = []
    selector_present = False
    for key in ("mcp_tool_ids", "mcpToolIds"):
        if key not in container:
            continue
        selector_present = True
        for value in container[key]:
            tool_id = str(value).strip()
            if tool_id and tool_id in allowed_tool_ids and tool_id not in requested:
                requested.append(tool_id)
        rebuilt.pop(key, None)
    if selector_present:
        rebuilt["mcp_tool_ids"] = requested
    return rebuilt


def _payload_with_authorized_mcp_registration(
    payload: QueueRunPayload,
    *,
    allowed_entries: list[dict[str, Any]],
    tool_policy_subjects: list[dict[str, Any]],
) -> QueueRunPayload:
    allowed_tool_ids = {
        str(entry.get("tool_id") or "").strip()
        for entry in allowed_entries
        if str(entry.get("tool_id") or "").strip()
    }
    rebuilt_input = _canonical_authorized_mcp_scope(payload.input, allowed_tool_ids=allowed_tool_ids)
    rebuilt_input["_runtime_tool_policy_subjects"] = tool_policy_subjects
    return payload.model_copy(update={"input": rebuilt_input})


async def _reauthorize_mcp_capabilities(
    conn,
    *,
    payload: QueueRunPayload,
    run_identity: dict[str, str],
    principal: AuthPrincipal,
    context: CapabilityAccessContext,
    decisions: list[_WorkerCapabilityDecision],
    requested_tool_ids: list[str],
    tool_policy_subjects: list[dict[str, Any]],
    required_tool_decision: RequiredCapabilityDecision,
) -> _WorkerCapabilityAuthorization:
    allowed_entries: list[dict[str, Any]] = []
    tool_policy_audits: list[_WorkerToolPolicyAudit] = []
    for tool_id in requested_tool_ids:
        tool = await mcp_api.get_mcp_tool_registry_entry(
            conn,
            tenant_id=run_identity["tenant_id"],
            tool_id=tool_id,
        )
        if tool is None or str(tool.get("tool_id") or "").strip() != tool_id:
            denial = _worker_capability_record(
                "mcp_tool",
                tool_id,
                _denied_capability_decision("distribution_missing"),
            )
            return _WorkerCapabilityAuthorization(
                payload, principal, tuple(decisions), denial
            )
        server_id = str(tool.get("server_id") or "").strip()
        if not server_id:
            denial = _worker_capability_record(
                "mcp_tool",
                tool_id,
                _denied_capability_decision("distribution_inheritance_missing"),
            )
            return _WorkerCapabilityAuthorization(
                payload, principal, tuple(decisions), denial
            )
        try:
            server_distribution = await identity_capability_distributions_postgres.get_capability_distribution_row(
                conn,
                tenant_id=run_identity["tenant_id"],
                capability_kind="mcp_server",
                capability_id=server_id,
            )
        except platform_errors.RepositoryConflictError:
            denial = _worker_capability_record(
                "mcp_tool",
                tool_id,
                _denied_capability_decision("distribution_scope_invalid"),
            )
            return _WorkerCapabilityAuthorization(
                payload, principal, tuple(decisions), denial
            )
        distribution_decision = resolve_capability_access(
            context,
            CapabilityDistributionSubject(
                capability_kind="mcp_tool",
                capability_id=tool_id,
                lifecycle_status=_mcp_tool_lifecycle_status(tool),
                distribution=server_distribution,
                inherited_distribution_source=f"mcp_server:{server_id}",
            ),
            intent="use",
        )
        tool_record = _worker_capability_record(
            "mcp_tool", tool_id, distribution_decision
        )
        decisions.append(tool_record)
        if not distribution_decision.usable:
            return _WorkerCapabilityAuthorization(
                payload, principal, tuple(decisions), tool_record
            )

        mcp_subject = _mcp_capability_subject(tool, distribution_decision)
        if mcp_subject is None:
            denial = _worker_capability_record(
                "mcp_tool",
                tool_id,
                _denied_capability_decision(
                    "mcp_runtime_metadata_invalid",
                    source=distribution_decision,
                ),
            )
            return _WorkerCapabilityAuthorization(
                payload, principal, tuple(decisions), denial
            )

        tool_gate = evaluate_tool_policy(
            tool={
                "requested_identity": mcp_subject["identity"],
                "declared_identities": [mcp_subject["identity"]],
                "registered": mcp_subject["registered"],
                "declared": mcp_subject["declared"],
                "active": mcp_subject["active"],
                "distributed": mcp_subject["distributed"],
                "identity_authorized": mcp_subject["identity_authorized"],
                "object_authorized": mcp_subject["object_authorized"],
                "parameters_authorized": mcp_subject["parameters_authorized"],
                "risk_level": mcp_subject["risk_level"],
                "write_capable": mcp_subject["write_capable"],
            }
        )
        tool_policy_audits.append(
            _WorkerToolPolicyAudit(
                tool_id=tool_id,
                allowed=tool_gate.allowed,
                reason=tool_gate.reason,
                risk_level=tool_gate.risk_level,
                write_capable=tool_gate.write_capable,
                decision=tool_gate.outcome,
            )
        )
        if not tool_gate.allowed:
            denial = _worker_capability_record(
                "mcp_tool",
                tool_id,
                _denied_capability_decision(
                    tool_gate.reason, source=distribution_decision
                ),
            )
            return _WorkerCapabilityAuthorization(
                payload,
                principal,
                tuple(decisions),
                denial,
                tool_policy_audits=tuple(tool_policy_audits),
            )
        allowed_entries.append(tool)
        tool_policy_subjects.append(mcp_subject)

    if allowed_entries and payload.executor_type != "claude-agent-worker":
        denial = _worker_capability_record(
            "mcp_tool",
            str(allowed_entries[0].get("tool_id") or "mcp_tool"),
            _denied_capability_decision("mcp_sandbox_executor_required"),
        )
        return _WorkerCapabilityAuthorization(
            payload,
            principal,
            tuple(decisions),
            denial,
            tool_policy_audits=tuple(tool_policy_audits),
        )

    authorized_payload = _payload_with_authorized_mcp_registration(
        payload,
        allowed_entries=allowed_entries,
        tool_policy_subjects=tool_policy_subjects,
    )
    return _WorkerCapabilityAuthorization(
        authorized_payload,
        principal,
        tuple(decisions),
        tool_policy_audits=tuple(tool_policy_audits),
        required_tool_decision=required_tool_decision,
    )


def _authorized_skill_catalog_binding(
    run_identity: dict[str, str],
) -> AuthorizedSkillCatalogBinding:
    return AuthorizedSkillCatalogBinding(
        tenant_id=run_identity["tenant_id"],
        workspace_id=run_identity["workspace_id"],
        user_id=run_identity["user_id"],
        session_id=run_identity["session_id"],
        run_id=run_identity["run_id"],
        agent_id=run_identity["agent_id"],
        selected_skill_id=run_identity["skill_id"],
    )


def _payload_with_authorized_skill_catalog(
    payload: QueueRunPayload,
    *,
    resolution: AuthorizedSkillCatalogResolution,
) -> QueueRunPayload:
    rebuilt_input = dict(payload.input)
    rebuilt_input.pop(RUNTIME_AUTHORIZED_SKILL_CATALOG_KEY, None)
    rebuilt_input.pop(RUNTIME_AUTHORIZED_SKILL_MANIFESTS_KEY, None)
    rebuilt_input.update(
        resolution.runtime_input_updates(pinned_manifests=payload.skill_manifests)
    )
    return payload.model_copy(update={"input": rebuilt_input})


async def _reauthorize_worker_capabilities(
    conn,
    *,
    payload: QueueRunPayload,
    run_identity: dict[str, str],
    attempt_id: str = "",
    current_principal: AuthPrincipal | None = None,
) -> _WorkerCapabilityAuthorization:
    decisions: list[_WorkerCapabilityDecision] = []
    principal = current_principal
    if principal is None:
        principal = AuthPrincipal(
            user_id=run_identity["user_id"],
            display_name=run_identity["user_id"],
            tenant_id=run_identity["tenant_id"],
            roles=[],
            permissions=[],
            source="company-user-info-current",
        )
        denial = _worker_capability_record(
            "principal_authority",
            "current_principal",
            _denied_capability_decision(CURRENT_PRINCIPAL_DENIAL_REASON),
        )
        return _WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
    context = _worker_capability_context(principal)

    if payload.execution_kind == RUN_EXECUTION_KIND_HARNESS_CHAT:
        try:
            requested_tool_ids = runs_capability_admission_postgres.extract_run_mcp_tool_ids(payload.input)
        except platform_errors.RepositoryAuthorizationError:
            denial = _worker_capability_record(
                "mcp_tool",
                "mcp_tool_ids",
                _denied_capability_decision("invalid_capability_selector"),
            )
            return _WorkerCapabilityAuthorization(
                payload,
                principal,
                tuple(decisions),
                denial,
            )
        tool_policy_subjects = with_harness_local_tool_subjects(
            decision=_worker_execution_boundary_decision(payload),
            sandbox_provider=get_settings().sandbox_container_provider,
        )
        required_tool_decision = required_tool_authorization_for_run(
            payload=payload,
            run_identity=run_identity,
            attempt_id=attempt_id or "missing-attempt",
            subjects=tool_policy_subjects,
            admin_bypass=False,
            admin_non_bypass_authorized=False,
        )
        if not required_tool_decision.allowed:
            denial = _worker_capability_record(
                "builtin_tool",
                required_tool_decision.identity or "required_tool",
                _denied_capability_decision(required_tool_decision.reason),
            )
            return _WorkerCapabilityAuthorization(
                payload,
                principal,
                tuple(decisions),
                denial,
                required_tool_decision=required_tool_decision,
            )
        return await _reauthorize_mcp_capabilities(
            conn,
            payload=payload,
            run_identity=run_identity,
            principal=principal,
            context=context,
            decisions=decisions,
            requested_tool_ids=requested_tool_ids,
            tool_policy_subjects=tool_policy_subjects,
            required_tool_decision=required_tool_decision,
        )

    try:
        await skills_run_snapshots_postgres.validate_run_skill_snapshots_for_dispatch(
            conn,
            tenant_id=run_identity["tenant_id"],
            run_id=run_identity["run_id"],
            skill_manifests=payload.skill_manifests,
            release_decision=payload.release_decision,
        )
    except platform_errors.RepositoryConflictError:
        denial = _worker_capability_record(
            "skill",
            run_identity["skill_id"],
            _denied_capability_decision("skill_snapshot_identity_mismatch"),
        )
        return _WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
    try:
        profile_skill_set = (
            payload.agent_profile.get("skill_set")
            if isinstance(payload.agent_profile, dict)
            else None
        )
        pinned_mcp_tool_ids = await skills_postgres.validate_replay_skill_manifests(
            conn,
            skill_id=run_identity["skill_id"],
            pinned_version=str(payload.skill_version or ""),
            pinned_executor_type=payload.executor_type,
            skill_manifests=payload.skill_manifests,
            skill_set=profile_skill_set if isinstance(profile_skill_set, list) else None,
        )
    except (platform_errors.RepositoryAuthorizationError, platform_errors.RepositoryConflictError):
        denial = _worker_capability_record(
            "skill",
            run_identity["skill_id"],
            _denied_capability_decision("skill_historical_pin_revoked"),
        )
        return _WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)

    skill: dict[str, Any] = {}
    skill_lifecycle_status = "disabled"
    try:
        skill = await skills_resolution_postgres.resolve_selected_skill(
            conn,
            tenant_id=run_identity["tenant_id"],
            agent_id=run_identity["agent_id"],
            skill_id=run_identity["skill_id"],
        )
        skill_lifecycle_status = str(skill.get("skill_status") or "disabled")
    except (platform_errors.RepositoryNotFoundError, platform_errors.RepositoryConflictError):
        pass
    try:
        skill_distribution = await identity_capability_distributions_postgres.get_capability_distribution_row(
            conn,
            tenant_id=run_identity["tenant_id"],
            capability_kind="skill",
            capability_id=run_identity["skill_id"],
        )
    except platform_errors.RepositoryConflictError:
        denial = _worker_capability_record(
            "skill",
            run_identity["skill_id"],
            _denied_capability_decision("distribution_scope_invalid"),
        )
        return _WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
    skill_subject = CapabilityDistributionSubject(
        capability_kind="skill",
        capability_id=run_identity["skill_id"],
        lifecycle_status=skill_lifecycle_status,
        distribution=skill_distribution,
    )
    skill_decision = resolve_capability_access(
        context,
        skill_subject,
        intent="use",
    )
    skill_record = _worker_capability_record("skill", run_identity["skill_id"], skill_decision)
    decisions.append(skill_record)
    if not skill_decision.usable:
        return _WorkerCapabilityAuthorization(payload, principal, tuple(decisions), skill_record)
    required_builtin_distribution = (
        resolve_capability_access(replace(context, is_admin=False), skill_subject, intent="use")
        if skill_decision.admin_bypass
        else skill_decision
    )

    authorized_skill_catalog: AuthorizedSkillCatalogResolution | None = None
    if payload.executor_type == "claude-agent-worker":
        try:
            authorized_skill_catalog = await resolve_authorized_skill_catalog(
                conn,
                binding=_authorized_skill_catalog_binding(run_identity),
                department_id=principal.department_id,
                roles=principal.roles,
                permissions=principal.permissions,
                pinned_manifests=payload.skill_manifests,
                skill_set=profile_skill_set if isinstance(profile_skill_set, list) else None,
            )
        except (AuthorizedSkillCatalogError, platform_errors.RepositoryConflictError):
            denial = _worker_capability_record(
                "skill",
                run_identity["skill_id"],
                _denied_capability_decision("authorized_skill_catalog_unavailable"),
            )
            return _WorkerCapabilityAuthorization(
                payload, principal, tuple(decisions), denial
            )
        selected_catalog_entry = authorized_skill_catalog.snapshot.entry(
            run_identity["skill_id"]
        )
        if run_identity["skill_id"] != LEGACY_SYNTHETIC_CHAT_SKILL_ID and (
            selected_catalog_entry is None or not selected_catalog_entry.available
        ):
            denial = _worker_capability_record(
                "skill",
                run_identity["skill_id"],
                _denied_capability_decision("selected_skill_catalog_unavailable"),
            )
            return _WorkerCapabilityAuthorization(payload, principal, tuple(decisions), denial)
        payload = _payload_with_authorized_skill_catalog(
            payload,
            resolution=authorized_skill_catalog,
        )

    try:
        requested_tool_ids = runs_capability_admission_postgres.run_mcp_tool_ids_for_skill(skill, payload.input)
        for tool_id in pinned_mcp_tool_ids or []:
            if tool_id not in requested_tool_ids:
                requested_tool_ids.append(tool_id)
    except platform_errors.RepositoryAuthorizationError:
        denial = _worker_capability_record(
            "mcp_tool",
            "mcp_tool_ids",
            _denied_capability_decision("invalid_capability_selector"),
        )
        return _WorkerCapabilityAuthorization(
            payload, principal, tuple(decisions), denial
        )
    tool_policy_subjects = _builtin_capability_subjects(
        payload=payload,
        run_identity=run_identity,
        skill=skill,
        skill_decision=required_builtin_distribution,
        authorized_skill_manifests=(
            authorized_skill_catalog.manifests
            if authorized_skill_catalog is not None
            else []
        ),
        authorized_skill_names=(
            list(authorized_skill_catalog.snapshot.materialized_skill_ids)
            if authorized_skill_catalog is not None
            else [run_identity["skill_id"]]
        ),
    )
    tool_policy_subjects = with_boundary_sandbox_local_tool_subjects(
        tool_policy_subjects, decision=_worker_execution_boundary_decision(payload),
        sandbox_provider=get_settings().sandbox_container_provider,
    )
    required_tool_decision = required_tool_authorization_for_run(
        payload=payload,
        run_identity=run_identity,
        attempt_id=attempt_id or "missing-attempt",
        subjects=tool_policy_subjects,
        admin_bypass=skill_decision.admin_bypass,
        admin_non_bypass_authorized=(
            skill_decision.admin_bypass and required_builtin_distribution.usable
        ),
    )
    if not required_tool_decision.allowed:
        denial = _worker_capability_record(
            "builtin_tool",
            required_tool_decision.identity or "required_tool",
            _denied_capability_decision(required_tool_decision.reason),
        )
        return _WorkerCapabilityAuthorization(
            payload,
            principal,
            tuple(decisions),
            denial,
            required_tool_decision=required_tool_decision,
        )
    return await _reauthorize_mcp_capabilities(
        conn,
        payload=payload,
        run_identity=run_identity,
        principal=principal,
        context=context,
        decisions=decisions,
        requested_tool_ids=requested_tool_ids,
        tool_policy_subjects=tool_policy_subjects,
        required_tool_decision=required_tool_decision,
    )


def _worker_capability_audit_payload(
    record: _WorkerCapabilityDecision,
    *,
    principal: AuthPrincipal,
    run_identity: dict[str, str],
) -> dict[str, Any]:
    return {
        **capability_distribution_audit_payload(
            decision=record.decision,
            actor_department_id=principal.department_id,
            actor_roles=principal.roles,
            capability_kind=record.capability_kind,
            capability_id=record.capability_id,
        ),
        "run_id": run_identity["run_id"],
        "session_id": run_identity["session_id"],
        "agent_id": run_identity["agent_id"],
        "skill_id": run_identity["skill_id"],
    }


def _worker_admin_bypass_audits(
    *,
    authorization: _WorkerCapabilityAuthorization,
    run_identity: dict[str, str],
    trace_id: str,
) -> tuple[_WorkerAdminBypassAudit, ...]:
    audits: list[_WorkerAdminBypassAudit] = []
    for record in authorization.decisions:
        if not record.decision.admin_bypass:
            continue
        audits.append(
            _WorkerAdminBypassAudit(
                tenant_id=run_identity["tenant_id"],
                user_id=run_identity["user_id"],
                target_type=record.capability_kind,
                target_id=record.capability_id,
                trace_id=trace_id,
                payload_json=_worker_capability_audit_payload(
                    record,
                    principal=authorization.principal,
                    run_identity=run_identity,
                ),
            )
        )
    return tuple(audits)


async def _append_worker_admin_bypass_audits(
    conn,
    *,
    audits: tuple[_WorkerAdminBypassAudit, ...],
) -> None:
    for audit in audits:
        await identity_audit_postgres.append_audit_log(
            conn,
            tenant_id=audit.tenant_id,
            user_id=audit.user_id,
            action="capability_distribution.admin_bypass",
            target_type=audit.target_type,
            target_id=audit.target_id,
            trace_id=audit.trace_id,
            payload_json=audit.payload_json,
        )


async def _append_worker_tool_policy_audits(
    conn,
    *,
    authorization: _WorkerCapabilityAuthorization,
    run_identity: dict[str, str],
    trace_id: str,
) -> None:
    for audit in authorization.tool_policy_audits:
        await identity_audit_postgres.append_audit_log(
            conn,
            tenant_id=run_identity["tenant_id"],
            user_id=run_identity["user_id"],
            action="mcp_tool_policy_allowed" if audit.allowed else "mcp_tool_policy_denied",
            target_type="mcp_tool",
            target_id=audit.tool_id,
            trace_id=trace_id,
            payload_json={
                "run_id": run_identity["run_id"],
                "session_id": run_identity["session_id"],
                "agent_id": run_identity["agent_id"],
                "skill_id": run_identity["skill_id"],
                "reason": audit.reason,
                "risk_level": audit.risk_level,
                "write_capable": audit.write_capable,
                "outcome": audit.decision,
            },
        )


async def _append_worker_capability_denial_evidence(
    conn,
    *,
    denial: _WorkerCapabilityDecision,
    principal: AuthPrincipal,
    run_identity: dict[str, str],
    trace_id: str,
    policy: str,
    error_message: str,
) -> None:
    await streaming_run_events_postgres.append_event(
        conn,
        tenant_id=run_identity["tenant_id"],
        run_id=run_identity["run_id"],
        event_type="capability_not_authorized",
        stage="authorization",
        message=error_message,
        payload={
            "capability_kind": denial.capability_kind,
            "capability_id": denial.capability_id,
            "policy": policy,
            "reason": denial.decision.decision_reason,
            "visible_to_user": True,
            "severity": "error",
        },
    )
    await identity_audit_postgres.append_audit_log(
        conn,
        tenant_id=run_identity["tenant_id"],
        user_id=run_identity["user_id"],
        action="capability_distribution.denied",
        target_type=denial.capability_kind,
        target_id=denial.capability_id,
        trace_id=trace_id,
        payload_json=_worker_capability_audit_payload(
            denial,
            principal=principal,
            run_identity=run_identity,
        ),
    )


async def _fail_worker_pre_dispatch_error(
    conn,
    *,
    payload: QueueRunPayload,
    run_identity: dict[str, str],
    error_code: str,
    error_message: str,
    event_stage: str,
    event_payload: dict[str, Any],
    v4_capabilities: WorkerV4Capabilities,
    attempt_lifecycle: WorkerAttemptLifecycle,
) -> _WorkerTerminalAfterTransaction:
    terminal_written = await _fail_run_with_write(
        conn,
        payload=payload,
        tenant_id=run_identity["tenant_id"],
        run_id=run_identity["run_id"],
        error_code=error_code,
        error_message=error_message,
        v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
    )
    if not terminal_written:
        return _WorkerTerminalAfterTransaction(
            WorkerOutcome(
                "skipped",
                run_identity["run_id"],
                "stale_terminal_state",
                "Run already reached a terminal state",
            ),
            payload,
        )
    await streaming_run_events_postgres.append_event(
        conn,
        tenant_id=run_identity["tenant_id"],
        run_id=run_identity["run_id"],
        event_type="error",
        stage=event_stage,
        message=error_message,
        payload=event_payload,
    )
    return _WorkerTerminalAfterTransaction(
        WorkerOutcome("failed", run_identity["run_id"], error_code, error_message),
        payload,
    )


async def _fail_locked_run_snapshot(
    conn,
    *,
    payload: QueueRunPayload,
    locked_run: object,
    run_identity: dict[str, str],
    trace_id: str,
    v4_capabilities: WorkerV4Capabilities,
    attempt_lifecycle: WorkerAttemptLifecycle,
) -> _WorkerTerminalAfterTransaction:
    error_code = "capability_not_authorized"
    error_message = "Capability is not authorized for this run"
    principal = _locked_run_principal(locked_run, run_identity)
    denial = _worker_capability_record(
        "skill",
        run_identity["skill_id"],
        _denied_capability_decision("locked_snapshot_invalid"),
    )
    terminal_written = await _fail_run_with_write(
        conn,
        payload=payload,
        tenant_id=run_identity["tenant_id"],
        run_id=run_identity["run_id"],
        error_code=error_code,
        error_message=error_message,
        v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
    )
    if not terminal_written:
        return _WorkerTerminalAfterTransaction(
            WorkerOutcome(
                "skipped",
                run_identity["run_id"],
                "stale_terminal_state",
                "Run already reached a terminal state",
            ),
            payload,
        )
    await _append_worker_capability_denial_evidence(
        conn,
        denial=denial,
        principal=principal,
        run_identity=run_identity,
        trace_id=trace_id,
        policy="locked_run_snapshot",
        error_message=error_message,
    )
    return _WorkerTerminalAfterTransaction(
        WorkerOutcome("failed", run_identity["run_id"], error_code, error_message),
        payload,
    )


async def _fail_worker_capability_authorization(
    conn,
    *,
    payload: QueueRunPayload,
    authorization: _WorkerCapabilityAuthorization,
    run_identity: dict[str, str],
    trace_id: str,
    v4_capabilities: WorkerV4Capabilities,
    attempt_lifecycle: WorkerAttemptLifecycle,
    policy: str = "capability_distribution",
) -> _WorkerTerminalAfterTransaction:
    denial = authorization.denial
    if denial is None:
        raise RuntimeError("worker_capability_denial_missing")
    required_tool_denial = denial.decision.decision_reason.startswith("required_tool_")
    error_code = "required_tool_unavailable" if required_tool_denial else "capability_not_authorized"
    error_message = "Capability is not authorized for this run"
    terminal_written = await _fail_run_with_write(
        conn,
        payload=payload,
        tenant_id=run_identity["tenant_id"],
        run_id=run_identity["run_id"],
        error_code=error_code,
        error_message=error_message,
        v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
    )
    if not terminal_written:
        return _WorkerTerminalAfterTransaction(
            WorkerOutcome(
                "skipped",
                run_identity["run_id"],
                "stale_terminal_state",
                "Run already reached a terminal state",
            ),
            payload,
        )
    await _append_worker_capability_denial_evidence(
        conn,
        denial=denial,
        principal=authorization.principal,
        run_identity=run_identity,
        trace_id=trace_id,
        policy=policy,
        error_message=error_message,
    )
    return _WorkerTerminalAfterTransaction(
        WorkerOutcome("failed", run_identity["run_id"], error_code, error_message),
        payload,
    )


def _result_prefers_cancelled_after_failure(result: ExecutorResult) -> bool:
    sandbox_provider = str(result.executor_payload.get("sandbox_provider") or "").strip()
    runtime_terminal_status = str(result.executor_payload.get("runtime_terminal_status") or "").strip().lower()
    return sandbox_provider in {"docker", "opensandbox"} and runtime_terminal_status in {"cancelled", "canceled"}


def _has_context_snapshot(payload: QueueRunPayload) -> bool:
    return bool(payload.context_snapshot_id)


def _included_count(row: dict[str, Any], field: str, payload: dict[str, Any], payload_field: str) -> int:
    raw = row.get(field)
    if isinstance(raw, list):
        return len(raw)
    try:
        return int(payload.get(payload_field) or 0)
    except (TypeError, ValueError):
        return 0


def _safe_context_memory_policy(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    source = str(raw.get("source") or "default").strip()
    if source not in {"default", "stored", "not_recorded"}:
        source = "stored"
    try:
        retention_days = int(raw.get("retention_days") or 90)
    except (TypeError, ValueError):
        retention_days = 90
    if retention_days <= 0:
        retention_days = 90
    return {
        "source": source,
        "memory_enabled": bool(raw.get("memory_enabled", True)),
        "long_term_memory_enabled": False,
        "retention_days": retention_days,
    }


def _context_snapshot_ref_from_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    public_payload = ensure_public_context_provenance(
        payload,
        source="stored_context_snapshot",
        message_count=_included_count(row, "included_message_ids", payload, "message_count"),
        file_count=_included_count(row, "included_file_ids", payload, "file_count"),
        artifact_count=_included_count(row, "included_artifact_ids", payload, "artifact_count"),
        memory_record_count=_included_count(row, "included_memory_record_ids", payload, "memory_record_count"),
        memory_policy_source="not_recorded",
        long_term_memory_read=False,
        preserve_stored_input_keys=True,
    )
    context_ref: dict[str, Any] = {
        "schema_version": str(row.get("schema_version") or payload.get("schema_version") or CONTEXT_SNAPSHOT_SCHEMA_VERSION),
        "context_snapshot_id": str(row["id"]),
        "source": public_payload["used_context_summary"]["source"],
        "message_count": public_payload["referenced_materials"]["message_count"],
        "file_count": public_payload["referenced_materials"]["file_count"],
        "memory_record_count": public_payload["referenced_materials"]["memory_record_count"],
        "referenced_materials": public_payload["referenced_materials"],
        "used_context_summary": public_payload["used_context_summary"],
        "latest_artifact_version": public_payload["latest_artifact_version"],
        "execution_tier": public_payload["execution_tier"],
        "context_pack_version": public_payload["context_pack_version"],
        "context_pack_generated_at": public_payload["context_pack_generated_at"],
    }
    memory_policy = _safe_context_memory_policy(payload.get("memory_policy"))
    if memory_policy is not None:
        context_ref["memory_policy"] = memory_policy
    context_manifest = payload.get("context_manifest")
    if isinstance(context_manifest, dict) and context_manifest.get("schema_version") == CONTEXT_MANIFEST_SCHEMA_VERSION:
        context_ref["context_manifest"] = sanitize_context_manifest_payload(context_manifest)
    return context_ref


async def process_run_payload(
    raw: dict[str, Any],
    registry: AdapterRegistry | None = None,
    *,
    worker_id: str | None = None,
    reconciliation: WorkerExecutorReconciliation | None = None,
    queue_lease: WorkerQueueLease | None = None,
    transaction_factory: Any | None = None,
    v4_capabilities: WorkerV4Capabilities,
    run_attempt_lifecycle: RunAttemptLifecycleService,
    run_lifecycle: RunLifecycleService,
) -> WorkerOutcome:
    transaction_factory = transaction_factory if transaction_factory is not None else transaction
    try:
        envelope = parse_leased_queue_envelope(raw)
    except InvalidLeasedQueueEnvelope as exc:
        return WorkerOutcome(
            status="dead_letter",
            run_id=None,
            error_code="invalid_queue_attempt",
            error_message=str(exc),
        )
    except ValidationError as exc:
        return WorkerOutcome(
            status="dead_letter",
            run_id=None,
            error_code="invalid_queue_payload",
            error_message=str(exc),
        )
    payload = envelope.payload
    if v4_capabilities is None:
        raise RuntimeError("worker_v4_capabilities_unavailable")
    attempt_lifecycle = bind_worker_attempt_lifecycle(
        payload,
        leased_attempt_id=envelope.attempt_id,
        worker_id=worker_id,
        reconciliation=reconciliation,
        ports=build_worker_attempt_lifecycle_ports(run_attempt_lifecycle, run_lifecycle),
        queue_lease=queue_lease,
    )
    attempt_id = attempt_lifecycle.attempt_id
    trace_id = standard_trace_id(payload.run_id)

    adapter_registry = registry if registry is not None else AdapterRegistry()
    adapter = None
    run_identity = _payload_identity(payload)
    runtime_sandbox_lease: _WorkerRuntimeSandboxLease | None = None
    runtime_sandbox_lease_released = False
    runtime_sandbox_execution_detached = False

    terminal_after_transaction: _WorkerTerminalAfterTransaction | None = None
    capability_authorization: _WorkerCapabilityAuthorization | None = None
    admin_bypass_audits: tuple[_WorkerAdminBypassAudit, ...] = ()
    try:
        current_principal = await _resolve_current_principal_before_dispatch(
            payload,
            transaction_factory=transaction_factory,
            run_loader=runs_postgres.get_run,
            principal_resolver=resolve_current_principal,
        )
        async with transaction_factory() as conn:
            locked = (
                await runs_postgres.get_run(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                )
                if reconciliation is not None
                else await run_attempt_lifecycle.lock_queued_run(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                )
            )
            if reconciliation is not None and locked is not None:
                if str(locked.get("status") or "") != "running":
                    return WorkerOutcome(
                        "skipped",
                        payload.run_id,
                        "stale_terminal_state",
                        "Run already reached a terminal state",
                    )
                if str(reconciliation.lease_row.get("attempt_id") or "") != attempt_id:
                    return WorkerOutcome(
                        "skipped",
                        payload.run_id,
                        "stale_reconciliation_attempt",
                        "Executor reconciliation attempt is stale",
                    )
                attempt_lifecycle = (
                    await attempt_lifecycle.restore_reconciliation_authority(conn)
                )
            if locked is not None:
                await v4_capabilities.pending_admissions.prepare_pending_authority_in_transaction(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    attempt_id=attempt_id,
                )
            if not locked:
                existing_run = await runs_postgres.get_run(conn, tenant_id=payload.tenant_id, run_id=payload.run_id)
                if existing_run is None:
                    return WorkerOutcome(
                        "skipped",
                        payload.run_id,
                        "stale_queue_payload",
                        "Run no longer exists for leased queue payload",
                    )
                if str(existing_run.get("status") or "") == "queued":
                    await v4_capabilities.pending_admissions.prepare_pending_authority_in_transaction(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        attempt_id=attempt_id,
                    )
                    error_code = "queue_payload_identity_mismatch"
                    error_message = "Queued run identity is invalid"
                    terminal_written = await _fail_run_with_write(
                        conn,
                        v4_capabilities=v4_capabilities,
                        payload=payload,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        error_code=error_code,
                        error_message=error_message,
                        attempt_lifecycle=attempt_lifecycle,
                    )
                    if not terminal_written:
                        terminal_after_transaction = _WorkerTerminalAfterTransaction(
                            WorkerOutcome(
                                "skipped",
                                payload.run_id,
                                "stale_terminal_state",
                                "Run already reached a terminal state",
                            ),
                            payload,
                        )
                        return terminal_after_transaction.outcome
                    await streaming_run_events_postgres.append_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        event_type="error",
                        stage="worker",
                        message=error_message,
                        payload={"visible_to_user": False, "severity": "error", "reason": "scope_guard_rejected_lock"},
                    )
                    terminal_after_transaction = _WorkerTerminalAfterTransaction(
                        WorkerOutcome("failed", payload.run_id, error_code, error_message),
                        payload,
                    )
                    return terminal_after_transaction.outcome
                await streaming_run_events_postgres.append_event(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    event_type="skip",
                    stage="worker",
                    message="Run is not queued; skipping duplicate or stale payload",
                )
                return WorkerOutcome("skipped", payload.run_id)
            run_identity = _locked_run_identity(payload, locked)
            mismatch_fields = _identity_mismatch_fields(payload, run_identity)
            if mismatch_fields:
                terminal_after_transaction = await _fail_worker_pre_dispatch_error(
                    conn,
                    payload=payload,
                    run_identity=run_identity,
                    v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
                    error_code="queue_payload_identity_mismatch",
                    error_message="Queue payload identity does not match run record",
                    event_stage="worker",
                    event_payload={
                        "visible_to_user": False,
                        "severity": "error",
                        "mismatch_fields": mismatch_fields,
                    },
                )
                return terminal_after_transaction.outcome
            locked = await _with_locked_run_model_snapshot(
                locked,
                conn=conn,
                run_identity=run_identity,
                load_run_model_snapshot=_load_run_model_snapshot,
            )
            trace_id = _locked_run_trace_id(payload, locked)
            locked_payload = _payload_from_locked_run(
                locked,
                run_identity=run_identity,
            )
            if locked_payload is None:
                terminal_after_transaction = await _fail_locked_run_snapshot(
                    conn,
                    payload=payload,
                    locked_run=locked,
                    run_identity=run_identity,
                    trace_id=trace_id,
                    v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
                )
                return terminal_after_transaction.outcome
            if not _locked_agent_profile_identity_valid(locked_payload.agent_profile or {}, locked) or (
                reconciliation is not None
                and not _reconciliation_agent_profile_binding_matches(payload.input, locked_payload.agent_profile or {})):
                terminal_after_transaction = await _fail_locked_run_snapshot(
                    conn,
                    payload=locked_payload,
                    locked_run=locked,
                    run_identity=run_identity,
                    trace_id=trace_id,
                    v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
                )
                return terminal_after_transaction.outcome
            if locked_payload.agent_profile and current_principal is not None:
                pinned_revision = int(locked_payload.agent_profile["revision"])
                pinned_hash = str(locked_payload.agent_profile["content_hash"])
                profile_admission = await reauthorize_bound_profile_for_worker_dispatch(
                    conn,
                    principal=current_principal,
                    agent_id=run_identity["agent_id"],
                    revision=pinned_revision,
                    content_hash=pinned_hash,
                )
                profile_denial_reason = None
                if profile_admission is None:
                    profile_denial_reason = "profile_not_authorized"
                elif not _agent_profile_snapshot_matches_authority(
                    locked_payload,
                    profile_admission,
                ):
                    profile_denial_reason = "profile_snapshot_invalid"
                if profile_denial_reason is not None:
                    profile_denial = _worker_capability_record(
                        "agent_profile",
                        run_identity["agent_id"],
                        _denied_capability_decision(profile_denial_reason),
                    )
                    terminal_after_transaction = await _fail_worker_capability_authorization(
                        conn,
                        payload=locked_payload,
                        authorization=_WorkerCapabilityAuthorization(
                            locked_payload,
                            current_principal,
                            (),
                            profile_denial,
                        ),
                        run_identity=run_identity,
                        trace_id=trace_id,
                        v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
                        policy="agent_profile_authority",
                    )
                    return terminal_after_transaction.outcome
            payload = locked_payload
            try:
                materialized_skill_manifests = await skills_run_snapshots_postgres.materialize_run_skill_manifests(
                    conn,
                    tenant_id=run_identity["tenant_id"],
                    run_id=run_identity["run_id"],
                    skill_manifest_refs=payload.skill_manifests,
                )
            except platform_errors.RepositoryConflictError:
                terminal_after_transaction = await _fail_locked_run_snapshot(
                    conn,
                    payload=payload,
                    locked_run=locked,
                    run_identity=run_identity,
                    trace_id=trace_id,
                    v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
                )
                return terminal_after_transaction.outcome
            payload = payload.model_copy(
                update={"skill_manifests": materialized_skill_manifests}
            )
            capability_authorization = await _reauthorize_worker_capabilities(
                conn,
                payload=payload,
                run_identity=run_identity,
                attempt_id=attempt_id,
                current_principal=current_principal,
            )
            admin_bypass_audits = _worker_admin_bypass_audits(
                authorization=capability_authorization,
                run_identity=run_identity,
                trace_id=trace_id,
            )
            await _append_worker_admin_bypass_audits(conn, audits=admin_bypass_audits)
            await _append_worker_tool_policy_audits(
                conn,
                authorization=capability_authorization,
                run_identity=run_identity,
                trace_id=trace_id,
            )
            if capability_authorization.denial is not None:
                terminal_after_transaction = await _fail_worker_capability_authorization(
                    conn,
                    payload=payload,
                    authorization=capability_authorization,
                    run_identity=run_identity,
                    trace_id=trace_id,
                    v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
                )
                return terminal_after_transaction.outcome
            payload = capability_authorization.payload
            if await attempt_lifecycle.is_cancel_requested(conn):
                cancel_result = {"message": "任务已取消"}
                terminal_written = await attempt_lifecycle.cancel(
                    conn,
                    capabilities=v4_capabilities,
                    result_json=cancel_result,
                )
                if not terminal_written:
                    terminal_after_transaction = _WorkerTerminalAfterTransaction(
                        WorkerOutcome(
                            "skipped",
                            run_identity["run_id"],
                            "stale_terminal_state",
                            "Run already reached a terminal state",
                        ),
                        payload,
                    )
                    return terminal_after_transaction.outcome
                terminal_after_transaction = _WorkerTerminalAfterTransaction(
                    WorkerOutcome("cancelled", run_identity["run_id"]),
                    payload,
                )
                return terminal_after_transaction.outcome
            try:
                if payload.executor_type in {"ragflow", "runtime211"}:
                    raise KeyError(f"Unknown executor type: {payload.executor_type}")
                adapter = adapter_registry.get(payload.executor_type)
            except KeyError as exc:
                terminal_written = await _fail_run_with_write(
                    conn,
                    v4_capabilities=v4_capabilities,
                    payload=payload,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    error_code="unknown_executor_type",
                    error_message=str(exc),
                    result_json=_pre_dispatch_failure_result(
                        "unknown_executor_type", "executor_resolution", error=exc
                    ),
                    attempt_lifecycle=attempt_lifecycle,
                )
                if not terminal_written:
                    terminal_after_transaction = _WorkerTerminalAfterTransaction(
                        WorkerOutcome(
                            "skipped",
                            payload.run_id,
                            "stale_terminal_state",
                            "Run already reached a terminal state",
                        ),
                        payload,
                    )
                    return terminal_after_transaction.outcome
                await streaming_run_events_postgres.append_event(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    event_type="error",
                    stage="worker",
                    message="Unknown executor type",
                    payload={"executor_type": payload.executor_type},
                )
                terminal_after_transaction = _WorkerTerminalAfterTransaction(
                    WorkerOutcome("failed", payload.run_id, "unknown_executor_type", str(exc)),
                    payload,
                )
                return terminal_after_transaction.outcome
        async with transaction_factory() as conn:
            fence = await worker_dispatch_fence(
                conn, run_identity=run_identity, locked_run=locked,
                context_snapshot_id=str(payload.context_snapshot_id or ""),
                reconciliation=reconciliation is not None,
            )
            if fence == "stale":
                return WorkerOutcome("skipped", payload.run_id, "stale_terminal_state")
            context_ref, context_error_code = (
                await materialize_queued_worker_context_snapshot(
                    conn, payload=payload, run_identity=run_identity,
                    context_projector=_context_snapshot_ref_from_row,
                ) if fence == "ready" else (None, None)
            )
            if context_ref is None:
                error_code = "worker_dispatch_fence_invalid" if fence == "invalid" else context_error_code or "context_snapshot_unavailable"
                error_message = (
                    "Run dispatch authority changed" if fence == "invalid"
                    else "A new conversation is required because native provider context is unavailable"
                    if context_error_code else "Run context snapshot is unavailable"
                )
                terminal_after_transaction = await _fail_worker_pre_dispatch_error(
                    conn,
                    payload=payload,
                    run_identity=run_identity,
                    v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
                    error_code=error_code,
                    error_message=error_message,
                    event_stage="context",
                    event_payload={"visible_to_user": False, "error_code": error_code},
                )
                return terminal_after_transaction.outcome
            payload = payload.model_copy(update={"file_ids": context_ref["file_ids"]})
            try:
                execution_spec = compile_execution_spec_for_dispatch(
                    run_identity=run_identity,
                    queue_payload=payload,
                    trace_id=trace_id,
                    context_snapshot_id=str(context_ref["context_snapshot_id"]),
                    context_snapshot=context_ref["context_snapshot"],
                    context_pack={**executor_context_pack_from_snapshot(context_ref["context_snapshot"]),
                                  "conversation_context": context_ref["conversation_context"]},
                    run_model_snapshot=locked,
                )
                run_payload = project_execution_spec_to_run_payload(
                    execution_spec, attempt_id=attempt_id
                )
                run_payload = await mcp_api.attach_mcp_server_configs(
                    conn, principal=capability_authorization.principal, run_payload=run_payload
                )
            except ValueError as exc:
                mcp_error = exc if isinstance(exc, mcp_api.McpRuntimeContextError) else None
                error_code = mcp_error.code if mcp_error else "execution_spec_invalid"
                terminal_after_transaction = await _fail_worker_pre_dispatch_error(
                    conn,
                    payload=payload,
                    run_identity=run_identity,
                    v4_capabilities=v4_capabilities, attempt_lifecycle=attempt_lifecycle,
                    error_code=error_code,
                    error_message="MCP runtime configuration is unavailable" if mcp_error else "Execution specification is invalid",
                    event_stage="authorization" if mcp_error else "worker",
                    event_payload={
                        "visible_to_user": bool(mcp_error),
                        "severity": "error",
                        "error_code": error_code,
                    },
                )
                return terminal_after_transaction.outcome
            bound_attempt = await attempt_lifecycle.bind_execution_spec(conn, execution_spec)
            if reconciliation is None:
                if bound_attempt is None:
                    raise platform_errors.RepositoryConflictError("run_attempt_binding_missing")
                run_payload = replace(
                    run_payload,
                    owner_generation=int(bound_attempt["owner_generation"]),
                )
            await append_user_event(
                conn,
                tenant_id=run_identity["tenant_id"],
                run_id=run_identity["run_id"],
                event_type="worker_started",
                stage="worker",
                message="Run started",
                payload=_worker_runtime_evidence(
                    worker_id=worker_id,
                    executor_type=payload.executor_type,
                ),
            )
            if reconciliation is None and not _ordinary_run_uses_runtime_sandbox(
                payload,
                context_snapshot=context_ref["context_snapshot"],
            ):
                runtime_sandbox_lease = await _create_worker_runtime_sandbox_lease(
                    conn,
                    executor_type=payload.executor_type,
                    run_identity=run_identity,
                    trace_id=trace_id,
                    attempt_id=attempt_id,
                    owner_generation=run_payload.owner_generation,
                    worker_id=worker_id,
                    ttl_seconds=get_settings().sandbox_lease_ttl_seconds,
                    create_lease=sandbox_lease_repository.create_sandbox_lease,
                )
    finally:
        if terminal_after_transaction is not None:
            await admit_v4_stream(
                v4_capabilities,
                tenant_id=terminal_after_transaction.payload.tenant_id,
                run_id=terminal_after_transaction.payload.run_id,
                attempt_id=attempt_id,
            )
            await publish_run_event(
                v4_capabilities,
                tenant_id=terminal_after_transaction.payload.tenant_id,
                run_id=terminal_after_transaction.payload.run_id,
            )

    async def event_sink(
        *,
        event_type: str,
        stage: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if event_type == "assistant_delta":
            raise WorkerDirectAssistantDeltaError
        if await persist_worker_event(
            v4_capabilities,
            run_payload=run_payload,
            persist_event=True,
            event_type=event_type,
            stage=stage,
            message=message,
            payload=payload,
        ):
            raise WorkerRunCancelled

    async def release_runtime_sandbox_lease(conn, *, reason: str) -> None:
        nonlocal runtime_sandbox_lease_released
        if reconciliation is not None:
            return
        if runtime_sandbox_lease is None or runtime_sandbox_lease_released:
            return
        await _release_worker_runtime_sandbox_lease(
            conn,
            runtime_sandbox_lease,
            reason=reason,
            release_lease=sandbox_lease_repository.release_sandbox_lease,
        )
        runtime_sandbox_lease_released = True

    async def cleanup_runtime_sandbox_lease_after_interruption() -> None:
        if runtime_sandbox_execution_detached:
            return
        if runtime_sandbox_lease is None or runtime_sandbox_lease_released:
            return
        try:
            async with transaction_factory() as conn:
                await release_runtime_sandbox_lease(conn, reason="run_terminal_interrupted")
        except Exception:  # noqa: BLE001 - interruption cleanup is best effort.
            return

    try:
        if adapter is None:
            raise RuntimeError("executor_adapter_not_resolved")

        if reconciliation is not None:
            started_at = time.monotonic()
            result: ExecutorResult | ExecutorDispatchAccepted = reconciliation.result
        else:
            await admit_v4_stream(
                v4_capabilities,
                tenant_id=run_payload.tenant_id,
                run_id=run_payload.run_id,
                attempt_id=run_payload.attempt_id,
            )

            async def cancel_requested() -> bool:
                async with transaction_factory() as conn:
                    return await attempt_lifecycle.is_cancel_requested(conn)

            execution_owner = build_artifact_execution_owner(
                run_payload, transaction_factory, reserve_provisional_artifact_cleanup, RunExecutionOwner
            )
            started_at = time.monotonic()
            result = await _submit_run_until_cancelled(
                adapter,
                run_payload,
                event_sink=event_sink,
                cancel_requested=cancel_requested,
                execution_owner=execution_owner,
            )
        if isinstance(result, ExecutorDispatchAccepted):
            if not result.lease_id:
                raise ValueError("executor_dispatch_acceptance_lease_missing")
            runtime_sandbox_execution_detached = True
            return WorkerOutcome(status="running", run_id=run_payload.run_id)
        latency_ms = max(int((time.monotonic() - started_at) * 1000), 0)
        result.validate()
        result = _normalize_sandbox_reported_failure(result)
        result = replace(result, executor_payload=bind_validated_controlled_skill_evidence(payload, result, attempt_id, adapter))
        if capability_authorization is None:
            raise RuntimeError("worker_capability_authorization_missing")
        required_tool_decision = capability_authorization.required_tool_decision or RequiredCapabilityDecision(
            True,
            "required_tool_not_declared",
            "",
            "",
        )
        required_completion = required_tool_completion_for_run(
            payload=payload,
            run_identity=run_identity,
            attempt_id=attempt_id,
            authorization=required_tool_decision,
            executor_payload=result.executor_payload,
        )
        if result.status == "succeeded" and not required_completion.allowed:
            result = replace(
                result,
                status="failed",
                artifacts=[],
                result={
                    **result.result,
                    "message": "Required execution capability evidence is unavailable.",
                    "error_code": required_completion.reason,
                },
            )
        required_agent_skill_id = _required_agent_skill_id(payload)
        if (
            result.status == "succeeded"
            and required_agent_skill_id is not None
            and required_agent_skill_id
            not in exact_invoked_skills({**result.result, **result.executor_payload})
        ):
            result = replace(
                result,
                status="failed",
                artifacts=[],
                result={
                    **result.result,
                    "message": "Required Agent capability execution evidence is unavailable.",
                    "error_code": "agent_app_required_skill_not_invoked",
                },
            )
    except WorkerRunCancelled:
        cancelled_outcome = WorkerOutcome(
            "skipped",
            payload.run_id,
            "stale_terminal_state",
            "Run already reached a terminal state",
        )
        async with transaction_factory() as conn:
            cancel_result = {"message": "任务已取消"}
            terminal_written = await attempt_lifecycle.cancel(
                conn,
                capabilities=v4_capabilities,
                result_json=cancel_result,
            )
            if terminal_written:
                await release_runtime_sandbox_lease(conn, reason="run_cancelled")
                cancelled_outcome = WorkerOutcome("cancelled", payload.run_id)
        await publish_run_event(v4_capabilities, tenant_id=payload.tenant_id, run_id=payload.run_id)
        return cancelled_outcome
    except Exception as exc:  # noqa: BLE001 - worker boundary terminalizes all failures.
        failure_code, failure_message, failure_result = _executor_exception_failure(exc)
        outcome_after_exception = WorkerOutcome(
            "failed", payload.run_id, failure_code, failure_message
        )
        async with transaction_factory() as conn:
            await v4_capabilities.pending_admissions.prepare_pending_authority_in_transaction(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                attempt_id=attempt_id,
            )
            if await attempt_lifecycle.is_cancel_requested(conn):
                cancel_result = {"message": "任务已取消"}
                terminal_written = await attempt_lifecycle.cancel(
                    conn,
                    capabilities=v4_capabilities,
                    result_json=cancel_result,
                )
                if not terminal_written:
                    outcome_after_exception = WorkerOutcome(
                        "skipped",
                        payload.run_id,
                        "stale_terminal_state",
                        "Run already reached a terminal state",
                    )
                else:
                    await release_runtime_sandbox_lease(conn, reason="run_cancelled")
                    outcome_after_exception = WorkerOutcome("cancelled", payload.run_id)
            else:
                terminal_written = await _fail_run_with_write(
                    conn,
                    v4_capabilities=v4_capabilities,
                    payload=payload,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    error_code=failure_code,
                    error_message=failure_message,
                    result_json=failure_result,
                    attempt_lifecycle=attempt_lifecycle,
                )
                if not terminal_written:
                    outcome_after_exception = WorkerOutcome(
                        "skipped",
                        payload.run_id,
                        "stale_terminal_state",
                        "Run already reached a terminal state",
                    )
                else:
                    await streaming_run_events_postgres.append_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        event_type="error",
                        stage="executor",
                        message="Executor failed",
                        payload={
                            "error": failure_message,
                            "executor_type": payload.executor_type,
                            "visible_to_user": False,
                        },
                    )
                    await release_runtime_sandbox_lease(conn, reason="run_failed")
        await publish_run_event(v4_capabilities, tenant_id=payload.tenant_id, run_id=payload.run_id)
        return outcome_after_exception

    observability = _executor_observability(result.executor_payload, latency_ms=latency_ms)
    event_observability_kwargs = _event_observability_kwargs(observability, result.executor_payload)
    terminal_event_kwargs = {"trace_id": trace_id, **event_observability_kwargs} if event_observability_kwargs else {}

    artifact_records = build_artifact_records(
        result.artifacts, reconciliation is not None, platform_values.new_id, _artifact_download_url
    )
    skill_snapshot = _skill_snapshot_from_result(result)
    agent_capability_state = (
        project_agent_capability_state(
            required_skill_id=required_agent_skill_id or "",
            executor_payload={**result.result, **result.executor_payload},
            run_succeeded=result.status == "succeeded",
            durable_artifact_count=0,
        )
        if required_agent_skill_id is not None
        else None
    )
    public_result = {
        key: value
        for key, value in (result.result | ({"runtime_diagnostics": result.executor_payload["runtime_diagnostics"]} if "runtime_diagnostics" in result.executor_payload else {})).items()
        if key not in {"skill_manifests", "used_skills", "used_skills_source", "inferred_used_skills"}
    }
    if required_agent_skill_id is None and (
        "used_skills" in result.result or "used_skills" in result.executor_payload
    ):
        public_result["used_skills"] = skill_snapshot["used_skills"]
    result_payload = {
        **public_result,
        **observability,
        "message": sanitize_assistant_message(str(result.result.get("message") or "")),
        "artifacts": [
            {
                "id": item["id"],
                "artifact_type": item["artifact_type"],
                "label": item["label"],
                "content_type": item["content_type"],
                "size_bytes": item["size_bytes"],
                "download_url": item["download_url"],
            }
            for item in artifact_records
        ],
        "executor": {
            "schema_version": result.schema_version,
            "adapter_version": result.adapter_version,
            "executor_type": result.executor_type,
            "executor_version": result.executor_version,
            "capabilities": result.capabilities,
        },
    }
    if skill_snapshot and required_agent_skill_id is None:
        result_payload["skills"] = skill_snapshot
    if agent_capability_state is not None:
        result_payload["capability_state"] = agent_capability_state.public_projection()
    assistant_message_for_persistence: str | None = None
    assistant_message_metadata: dict[str, Any] = {}
    try:
        async with transaction_factory() as conn:
            locked_run = await runs_postgres.get_run(
                conn,
                tenant_id=payload.tenant_id,
                run_id=payload.run_id,
                for_update=True,
            )
            if locked_run is None or str(locked_run.get("status") or "") in {"succeeded", "failed", "cancelled"}:
                raise _WorkerSuccessCommitBlocked()
            if reconciliation is not None and not await sandbox_lease_repository.is_sandbox_executor_reconciliation_claim_current(
                conn,
                lease_id=str(reconciliation.lease_row["id"]),
                claim_token=reconciliation.claim_token,
            ):
                raise RuntimeError("executor_reconciliation_claim_lost")
            # The selected platform Skill owns this contract.  Preserve an
            # adapter's additional declared requirements, but never let an
            # executor omit the capability requirement on resume or retry.
            required_artifact_types = set(required_artifact_types_for_skill(payload.skill_id)) | {
                str(value)
                for value in result.executor_payload.get("required_artifact_types", [])
                if isinstance(value, str) and value
            }
            produced_artifact_types = {artifact.artifact_type for artifact in result.artifacts}
            missing_required_artifact_types = required_artifact_types - produced_artifact_types
            missing_required_artifact = result.status == "succeeded" and bool(missing_required_artifact_types)
            if missing_required_artifact:
                error_code = "required_artifact_missing"
                error_message = "The file-required Skill did not produce every required artifact type."
                result = replace(
                    result,
                    status="failed",
                    artifacts=[],
                    result={
                        **result.result,
                        "message": error_message,
                        "error_code": error_code,
                        "missing_required_artifact_types": sorted(missing_required_artifact_types),
                    },
                )
                artifact_records = []
                result_payload = {
                    **result_payload,
                    "message": error_message,
                    "error_code": error_code,
                    "artifacts": [],
                }
            answer_receipt = result.executor_payload.get("answer_receipt")
            if result.status == "succeeded" and answer_receipt is not None:
                materialized = await materialize_worker_answer(v4_capabilities, conn, result=result, result_payload=result_payload, artifact_records=artifact_records, tenant_id=payload.tenant_id, run_id=payload.run_id, attempt_id=attempt_id, answer_receipt=answer_receipt, limits=_ANSWER_PERSISTENCE_LIMITS)
                result = materialized.result
                result_payload = materialized.result_payload
                artifact_records = materialized.artifact_records
                assistant_message_for_persistence = materialized.assistant_message_for_persistence
                assistant_message_metadata = materialized.assistant_message_metadata
            cancel_requested = await attempt_lifecycle.is_cancel_requested(conn)
            if result.status == "succeeded" and cancel_requested:
                result_payload = {
                    **result_payload,
                    "cancel_status": "cancel_requested_but_completed",
                }
            if agent_capability_state is not None:
                agent_capability_state = project_agent_capability_state(
                    required_skill_id=required_agent_skill_id or "",
                    executor_payload={**result.result, **result.executor_payload},
                    run_succeeded=result.status == "succeeded",
                    durable_artifact_count=0,
                )
                result_payload["capability_state"] = agent_capability_state.public_projection()
                semantic_events = (
                    (
                        "capability_staged",
                        agent_capability_state.staged,
                        "Agent capability loaded",
                        "staged",
                    ),
                    (
                        "capability_sdk_registered",
                        agent_capability_state.sdk_registered,
                        "Agent capability registered",
                        "sdk_registered",
                    ),
                    (
                        "capability_actually_invoked",
                        agent_capability_state.actually_invoked,
                        "Agent capability invoked",
                        "actually_invoked",
                    ),
                    (
                        "capability_completed",
                        agent_capability_state.completed,
                        "Agent capability completed",
                        "completed",
                    ),
                )
                for event_type, present, message, public_state in semantic_events:
                    if not present:
                        continue
                    await append_user_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        event_type=event_type,
                        stage="capability",
                        message=message,
                        payload={"capability_state": public_state},
                    )
                if agent_capability_state.optional_not_invoked_count:
                    await append_user_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        event_type="capability_optional_not_invoked",
                        stage="capability",
                        message="Optional Agent capabilities were not invoked",
                        payload={
                            "capability_state": "optional_not_invoked",
                            "count": agent_capability_state.optional_not_invoked_count,
                        },
                    )
            await promote_artifact_reservations(
                conn, artifact_records, payload, promote_provisional_artifact_cleanup
            )
            for artifact in artifact_records:
                manifest_json = artifact_manifest_contract(
                    artifact_type=artifact["artifact_type"],
                    manifest=_sanitize_artifact_manifest(artifact["manifest_json"]),
                )
                lineage = artifact_lineage_contract(manifest_json, source_run_id=payload.run_id)
                await artifacts_records_postgres.create_artifact(
                    conn,
                    artifact_id=artifact["id"],
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    artifact_type=artifact["artifact_type"],
                    label=artifact["label"],
                    content_type=artifact["content_type"],
                    storage_key=artifact["storage_key"],
                    size_bytes=artifact["size_bytes"],
                    trace_id=trace_id,
                    manifest_json=manifest_json,
                )
                await append_user_event(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    event_type="artifact_ready",
                    stage="artifact",
                    message="Artifact is ready",
                    payload={
                        "artifact_id": artifact["id"],
                        "artifact_type": artifact["artifact_type"],
                        "download_url": artifact["download_url"],
                        "lineage": lineage,
                    },
                )
            if agent_capability_state is not None:
                agent_capability_state = project_agent_capability_state(
                    required_skill_id=required_agent_skill_id or "",
                    executor_payload={**result.result, **result.executor_payload},
                    run_succeeded=result.status == "succeeded",
                    durable_artifact_count=len(artifact_records),
                )
                result_payload["capability_state"] = agent_capability_state.public_projection()
            for item in _skill_manifests_for_persistence(result, payload):
                skill_id = str(item.get("skill_id") or "").strip()
                if not skill_id:
                    continue
                await skills_run_snapshots_postgres.upsert_run_skill_snapshot(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    skill_id=skill_id,
                    skill_version=str(item.get("version") or item.get("skill_version") or ""),
                    content_hash=str(item.get("content_hash") or item.get("version") or ""),
                    source_json=_source_json_from_skill_manifest(
                        item,
                        release_decision=payload.release_decision,
                    ),
                    dependency_ids=_dependency_ids_from_manifest(item),
                    allowed=bool(item.get("allowed")),
                    staged=bool(item.get("staged")),
                    used=bool(item.get("used")),
                    used_skills_source=str(item.get("used_skills_source") or "").strip(),
                )
            if result.status == "succeeded":
                await persist_assistant_with_provider_coverage(
                    conn, append_message=conversations_postgres.append_message,
                    tenant_id=payload.tenant_id, session_id=payload.session_id,
                    run_id=payload.run_id, attempt_id=attempt_id,
                    executor_type=payload.executor_type,
                    content=(assistant_message_for_persistence
                             if assistant_message_for_persistence is not None
                             else str(result_payload.get("message") or "")),
                    metadata_json={
                        **assistant_artifact_metadata(artifact_records),
                        "executor_type": result.executor_type,
                        "adapter_version": result.adapter_version,
                        **assistant_message_metadata,
                        **(
                            {"capability_state": agent_capability_state.public_projection()}
                            if agent_capability_state is not None
                            else {"skills": skill_snapshot}
                        ),
                    },
                    provider_final_sequence=result.executor_payload.get("provider_session_final_sequence"),
                )

                await append_user_event(
                    conn,
                    tenant_id=payload.tenant_id,
                    run_id=payload.run_id,
                    event_type="assistant_message_created",
                    stage="message",
                    message="Assistant response is ready",
                    payload={
                        "artifact_count": len(result.artifacts),
                        **(
                            {"capability_state": agent_capability_state.public_projection()}
                            if agent_capability_state is not None
                            else {"skills": skill_snapshot}
                        ),
                    },
                )
                if cancel_requested:
                    await append_user_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        event_type="cancel_requested_but_completed",
                        stage="control",
                        message="取消请求已记录，但任务已完成",
                        payload={"severity": "warning"},
                    )
                terminal_written = await attempt_lifecycle.complete(
                    conn,
                    capabilities=v4_capabilities,
                    result_json=result_payload,
                )
                if not terminal_written:
                    raise _WorkerSuccessCommitBlocked()
                else:
                    await append_user_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        event_type="run_succeeded",
                        stage="worker",
                        message="Run succeeded",
                        payload={
                            "artifact_count": len(result.artifacts),
                            **(
                                {"capability_state": agent_capability_state.public_projection()}
                                if agent_capability_state is not None
                                else {"skills": skill_snapshot}
                            ),
                        },
                        **terminal_event_kwargs,
                    )
                    await streaming_run_events_postgres.append_event(
                        conn,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        event_type="status",
                        stage="worker",
                        message="Run succeeded",
                        payload={"artifact_count": len(result.artifacts), "visible_to_user": False},
                    )
                    await release_runtime_sandbox_lease(conn, reason="run_succeeded")
                    terminal_outcome = WorkerOutcome("succeeded", payload.run_id)
            else:
                reported_error_code = str(result.result.get("error_code") or "executor_reported_failure")
                reported_error_message = _public_executor_failure_message(result)
                if cancel_requested and _result_prefers_cancelled_after_failure(result):
                    cancel_result = {"message": "任务已取消"}
                    terminal_written = await attempt_lifecycle.cancel(
                        conn,
                        capabilities=v4_capabilities,
                        result_json=cancel_result,
                    )
                    if not terminal_written:
                        terminal_outcome = WorkerOutcome(
                            "skipped",
                            payload.run_id,
                            "stale_terminal_state",
                            "Run already reached a terminal state",
                        )
                    else:
                        await release_runtime_sandbox_lease(conn, reason="run_cancelled")
                        terminal_outcome = WorkerOutcome("cancelled", payload.run_id)
                else:
                    terminal_written = await _fail_run_with_write(
                        conn,
                        v4_capabilities=v4_capabilities,
                        payload=payload,
                        tenant_id=payload.tenant_id,
                        run_id=payload.run_id,
                        error_code=reported_error_code,
                        error_message=reported_error_message,
                        result_json=result_payload,
                        attempt_lifecycle=attempt_lifecycle,
                    )
                    if not terminal_written:
                        terminal_outcome = WorkerOutcome(
                            "skipped",
                            payload.run_id,
                            "stale_terminal_state",
                            "Run already reached a terminal state",
                        )
                    else:
                        await persist_worker_failure_event(
                            conn,
                            tenant_id=payload.tenant_id,
                            run_id=payload.run_id,
                            result=result,
                            attempt_id=attempt_id,
                            trace_id=trace_id,
                            error_code=reported_error_code,
                        )
                        await release_runtime_sandbox_lease(conn, reason="run_failed")
                        terminal_outcome = WorkerOutcome("failed", payload.run_id, reported_error_code, reported_error_message)
    except _WorkerSuccessCommitBlocked:
        async with transaction_factory() as conn:
            blocked_reason = await attempt_lifecycle.classify_success_commit_block(conn)
            if blocked_reason == "cancel_requested":
                cancel_result = {"message": "任务已取消"}
                terminal_written = await attempt_lifecycle.cancel(
                    conn,
                    capabilities=v4_capabilities,
                    result_json=cancel_result,
                )
                if terminal_written:
                    await release_runtime_sandbox_lease(conn, reason="run_cancelled")
                    terminal_outcome = WorkerOutcome("cancelled", payload.run_id)
                else:
                    terminal_outcome = WorkerOutcome(
                        "skipped",
                        payload.run_id,
                        "stale_terminal_state",
                        "Run already reached a terminal state",
                    )
            else:
                terminal_outcome = WorkerOutcome(
                    "skipped",
                    payload.run_id,
                    "stale_terminal_state",
                    "Run already reached a terminal state",
                )
    finally:
        await cleanup_runtime_sandbox_lease_after_interruption()
    await publish_run_event(v4_capabilities, tenant_id=payload.tenant_id, run_id=payload.run_id)
    return terminal_outcome


async def reconcile_executor_terminal_result(
    *,
    lease_row: dict[str, Any],
    result: ExecutorResult,
    registry: AdapterRegistry | None = None,
    worker_id: str | None = None,
    claim_token: str,
    transaction_factory: Any | None = None,
    v4_capabilities: WorkerV4Capabilities,
    run_attempt_lifecycle: RunAttemptLifecycleService,
    run_lifecycle: RunLifecycleService,
) -> WorkerOutcome:
    queue_payload, attempt_id = _restored_executor_reconciliation_queue_payload(
        lease_row.get("executor_reconciliation_context_json"),
        result=result.result,
        run_payload_factory=RunPayload,
        queue_payload_factory=QueueRunPayload,
    )
    return await process_run_payload(
        {
            **queue_payload.model_dump(mode="json"),
            "_queue_attempt_id": attempt_id,
        },
        registry,
        worker_id=worker_id,
        reconciliation=WorkerExecutorReconciliation(result, lease_row, claim_token),
        transaction_factory=transaction_factory,
        v4_capabilities=v4_capabilities,
        run_attempt_lifecycle=run_attempt_lifecycle,
        run_lifecycle=run_lifecycle,
    )
