from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from app import repositories
from app.context_manifest import (
    CONTEXT_MANIFEST_SCHEMA_VERSION,
    ContextPlanner,
    public_context_manifest_projection,
)
from app.control_plane_contracts import CONTEXT_SNAPSHOT_SCHEMA_VERSION, sanitize_public_payload
from app.office_execution_tier import route_office_execution_tier
from app.projection_redaction import capability_id_from_skill
from app.public_context_keys import (
    PUBLIC_CONTEXT_FORBIDDEN_ID_KEY_ALIASES,
    PUBLIC_CONTEXT_FORBIDDEN_KEY_ALIASES,
    PUBLIC_CONTEXT_MATERIAL_COUNT_KEYS,
    PUBLIC_CONTEXT_MEMORY_POLICY_SOURCE_VALUES,
    PUBLIC_CONTEXT_PROVENANCE_KEY_ALIASES,
    PUBLIC_CONTEXT_SOURCE_VALUES,
    PUBLIC_CONTEXT_SUMMARY_KEY_ALIASES,
    PUBLIC_CONTEXT_SUMMARY_PREFIX_ALIASES,
    has_public_context_forbidden_id_tokens,
    normalized_public_context_key_candidates,
    public_context_input_key_findings,
    public_context_key_token_candidates,
    safe_public_context_input_keys,
    safe_public_context_pack_version,
)

PUBLIC_CONTEXT_EXECUTION_TIERS = {"sdk_only_writing", "document_worker", "heavy_sandbox"}
PUBLIC_CONTEXT_ARTIFACT_VERSION_RE = re.compile(r"^v\d+(?:[._:-]\d+){0,3}$", re.IGNORECASE)
PUBLIC_CONTEXT_HASH_LIKE_VALUE_RE = re.compile(r"^[a-f0-9]{32,}$", re.IGNORECASE)
PUBLIC_ARTIFACT_VERSION_KEYS = (
    "artifact_version",
    "document_version",
    "output_version",
    "public_version",
    "revision",
)
EXECUTOR_CONTEXT_PACK_SCHEMA_VERSION = "ai-platform.executor-context-pack.v1"
DEFAULT_CONTEXT_PACK_VERSION = "v1"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _strip_context_private_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [
            item
            for item in (_strip_context_private_fields(entry) for entry in value)
            if item is not None
        ]
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized_keys, decode_budget_exhausted = normalized_public_context_key_candidates(key_text)
            if decode_budget_exhausted:
                continue
            if any(normalized_key in PUBLIC_CONTEXT_PROVENANCE_KEY_ALIASES for normalized_key in normalized_keys):
                continue
            if any(normalized_key in PUBLIC_CONTEXT_SUMMARY_KEY_ALIASES for normalized_key in normalized_keys):
                continue
            if any(
                normalized_key.startswith(prefix)
                for normalized_key in normalized_keys
                for prefix in PUBLIC_CONTEXT_SUMMARY_PREFIX_ALIASES
            ):
                continue
            if any(normalized_key in PUBLIC_CONTEXT_FORBIDDEN_KEY_ALIASES for normalized_key in normalized_keys):
                continue
            if any(normalized_key in PUBLIC_CONTEXT_FORBIDDEN_ID_KEY_ALIASES for normalized_key in normalized_keys):
                continue
            if any(
                has_public_context_forbidden_id_tokens(token_candidate)
                for token_candidate in public_context_key_token_candidates(key_text)
            ):
                continue
            cleaned_item = _strip_context_private_fields(item)
            if cleaned_item is not None:
                cleaned[key_text] = cleaned_item
        return cleaned
    return value


def public_context_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Return frontend-safe context payload fields excluding system provenance and private aliases."""
    sanitized_payload = sanitize_public_payload(payload or {})
    if not isinstance(sanitized_payload, dict):
        return {}
    cleaned = _strip_context_private_fields(sanitized_payload)
    return cleaned if isinstance(cleaned, dict) else {}


def _public_context_input_keys_with_material_signals(
    keys: list[str],
    *,
    file_count: int,
) -> list[str]:
    public_keys = set(keys)
    if file_count > 0:
        public_keys.add("attachments")
    return sorted(public_keys)


def _stored_public_context_input_keys(payload: dict[str, Any]) -> list[str]:
    used_summary = payload.get("used_context_summary")
    if isinstance(used_summary, dict):
        input_keys = safe_public_context_input_keys(used_summary.get("input_keys"))
        if input_keys:
            return input_keys
    return safe_public_context_input_keys(payload.get("input_keys"))


def _stored_public_context_memory_policy_source(payload: dict[str, Any]) -> str | None:
    used_summary = payload.get("used_context_summary")
    if isinstance(used_summary, dict) and _stored_public_context_source(payload) is not None:
        summary_source = used_summary.get("memory_policy_source")
        if isinstance(summary_source, str):
            summary_source = summary_source.strip()
            if summary_source in PUBLIC_CONTEXT_MEMORY_POLICY_SOURCE_VALUES:
                return summary_source

    memory_policy = payload.get("memory_policy")
    if not isinstance(memory_policy, dict):
        return None
    source = memory_policy.get("source")
    if not isinstance(source, str):
        return None
    source = source.strip()
    return source if source in PUBLIC_CONTEXT_MEMORY_POLICY_SOURCE_VALUES else None


def _stored_public_context_long_term_memory_read(payload: dict[str, Any]) -> bool | None:
    used_summary = payload.get("used_context_summary")
    if not isinstance(used_summary, dict) or _stored_public_context_source(payload) is None:
        return None
    value = used_summary.get("long_term_memory_read")
    return value if isinstance(value, bool) else None


def _stored_public_context_source(payload: dict[str, Any]) -> str | None:
    used_summary = payload.get("used_context_summary")
    source = used_summary.get("source") if isinstance(used_summary, dict) else None
    if not isinstance(source, str):
        return None
    source = source.strip()
    return source if source in PUBLIC_CONTEXT_SOURCE_VALUES else None


def _stored_public_context_top_level_source(payload: dict[str, Any]) -> str | None:
    source = payload.get("source")
    if not isinstance(source, str):
        return None
    source = source.strip()
    return source if source in PUBLIC_CONTEXT_SOURCE_VALUES else None


def _safe_public_context_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if sanitize_public_payload(value) != value:
        return None
    normalized_candidates, decode_budget_exhausted = normalized_public_context_key_candidates(value)
    if decode_budget_exhausted:
        return None
    forbidden_aliases = PUBLIC_CONTEXT_FORBIDDEN_KEY_ALIASES | PUBLIC_CONTEXT_FORBIDDEN_ID_KEY_ALIASES
    if any(alias in normalized_key for normalized_key in normalized_candidates for alias in forbidden_aliases):
        return None
    if any(
        has_public_context_forbidden_id_tokens(token_candidate)
        for token_candidate in public_context_key_token_candidates(value)
    ):
        return None
    return value


def _stored_public_context_execution_tier(payload: dict[str, Any]) -> str | None:
    value = _safe_public_context_string(payload.get("execution_tier"))
    return value if value in PUBLIC_CONTEXT_EXECUTION_TIERS else None


def _stored_public_context_latest_artifact_version(payload: dict[str, Any]) -> str | None:
    value = _safe_public_context_string(payload.get("latest_artifact_version"))
    if value is None:
        return None
    if PUBLIC_CONTEXT_HASH_LIKE_VALUE_RE.fullmatch(value):
        return None
    return value if PUBLIC_CONTEXT_ARTIFACT_VERSION_RE.fullmatch(value) else None


def _public_artifact_version_from_manifest(artifact: dict[str, Any]) -> str | None:
    manifest = artifact.get("manifest_json")
    if not isinstance(manifest, dict):
        return None
    for key in PUBLIC_ARTIFACT_VERSION_KEYS:
        version = _stored_public_context_latest_artifact_version(
            {"latest_artifact_version": manifest.get(key)}
        )
        if version is not None:
            return version
    return None


def _stored_public_context_pack_version(payload: dict[str, Any]) -> str | None:
    return safe_public_context_pack_version(payload.get("context_pack_version"))


def _safe_public_context_pack_version(value: object) -> str:
    return safe_public_context_pack_version(value) or DEFAULT_CONTEXT_PACK_VERSION


def _stored_public_context_generated_at(payload: dict[str, Any]) -> str | None:
    value = payload.get("context_pack_generated_at")
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if sanitize_public_payload(value) != value:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def public_context_provenance(
    *,
    source: str,
    input_payload: dict[str, Any] | None = None,
    input_keys: list[str] | None = None,
    message_count: int = 0,
    file_count: int = 0,
    artifact_count: int = 0,
    memory_record_count: int = 0,
    memory_policy_source: str = "not_recorded",
    long_term_memory_read: bool = False,
    latest_artifact_version: str | None = None,
    execution_tier: str = "sdk_only_writing",
    context_pack_version: str = DEFAULT_CONTEXT_PACK_VERSION,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the user-visible context provenance contract without exposing raw ids."""
    sanitized_input = public_context_payload(input_payload or {})
    safe_source = source if source in PUBLIC_CONTEXT_SOURCE_VALUES else "stored_context_snapshot"
    safe_memory_policy_source = (
        memory_policy_source
        if memory_policy_source in PUBLIC_CONTEXT_MEMORY_POLICY_SOURCE_VALUES
        else "not_recorded"
    )
    safe_latest_artifact_version = _stored_public_context_latest_artifact_version(
        {"latest_artifact_version": latest_artifact_version}
    )
    safe_execution_tier = (
        execution_tier if execution_tier in PUBLIC_CONTEXT_EXECUTION_TIERS else "sdk_only_writing"
    )
    safe_generated_at = _stored_public_context_generated_at(
        {"context_pack_generated_at": generated_at}
    )
    safe_input_keys = safe_public_context_input_keys(input_keys) if input_keys is not None else []
    if not safe_input_keys:
        safe_input_keys = sorted(str(key) for key in sanitized_input.keys())
    safe_input_keys = _public_context_input_keys_with_material_signals(
        safe_input_keys,
        file_count=max(0, int(file_count)),
    )
    return {
        "referenced_materials": {
            "message_count": max(0, int(message_count)),
            "file_count": max(0, int(file_count)),
            "artifact_count": max(0, int(artifact_count)),
            "memory_record_count": max(0, int(memory_record_count)),
        },
        "used_context_summary": {
            "source": safe_source,
            "input_keys": safe_input_keys,
            "memory_policy_source": safe_memory_policy_source,
            "long_term_memory_read": bool(long_term_memory_read),
        },
        "latest_artifact_version": safe_latest_artifact_version,
        "execution_tier": safe_execution_tier,
        "context_pack_version": _safe_public_context_pack_version(context_pack_version),
        "context_pack_generated_at": safe_generated_at or _utc_now_iso(),
    }


def _safe_material_count(value: object) -> int:
    return max(0, int(value)) if isinstance(value, int) and not isinstance(value, bool) else 0


def _stored_public_context_referenced_materials(payload: dict[str, Any]) -> dict[str, int]:
    materials = payload.get("referenced_materials")
    if not isinstance(materials, dict):
        materials = {}
    return {
        key: _safe_material_count(materials.get(key))
        for key in PUBLIC_CONTEXT_MATERIAL_COUNT_KEYS
    }


def executor_context_pack_from_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Return the bounded context pack that executor prompts may consume."""
    payload = snapshot if isinstance(snapshot, dict) else {}
    context_manifest = payload.get("context_manifest")
    if isinstance(context_manifest, dict) and context_manifest.get("schema_version") == CONTEXT_MANIFEST_SCHEMA_VERSION:
        return ContextPlanner().executor_context_pack(context_manifest)
    referenced_materials = _stored_public_context_referenced_materials(payload)
    sanitized_payload = ensure_public_context_provenance(
        payload,
        source="stored_context_snapshot",
        preserve_stored_input_keys=True,
    )
    used_summary = sanitized_payload.get("used_context_summary")
    if not isinstance(used_summary, dict):
        used_summary = {}
    input_keys = safe_public_context_input_keys(used_summary.get("input_keys"))
    memory_policy_source = str(used_summary.get("memory_policy_source") or "not_recorded")
    long_term_memory_read = False
    source = str(used_summary.get("source") or sanitized_payload.get("source") or "stored_context_snapshot")
    latest_artifact_version = _stored_public_context_latest_artifact_version(sanitized_payload)
    execution_tier = _stored_public_context_execution_tier(sanitized_payload) or "sdk_only_writing"
    context_pack_version = _stored_public_context_pack_version(sanitized_payload) or DEFAULT_CONTEXT_PACK_VERSION
    generated_at = _stored_public_context_generated_at(sanitized_payload) or _utc_now_iso()
    prompt_summary = (
        "Context pack: "
        f"{referenced_materials['message_count']} message(s), "
        f"{referenced_materials['file_count']} file(s), "
        f"{referenced_materials['artifact_count']} artifact(s), "
        f"{referenced_materials['memory_record_count'] if long_term_memory_read else 0} long-term memory record(s). "
        f"Inputs: {', '.join(input_keys) if input_keys else 'none'}. "
        f"Execution tier: {execution_tier}."
    )
    prompt_summary += f" Context pack version: {context_pack_version}."
    if latest_artifact_version:
        prompt_summary += f" Latest artifact version: {latest_artifact_version}."
    return {
        "schema_version": EXECUTOR_CONTEXT_PACK_SCHEMA_VERSION,
        "source": source if source in PUBLIC_CONTEXT_SOURCE_VALUES else "stored_context_snapshot",
        "referenced_materials": referenced_materials,
        "used_context_summary": {
            "source": source if source in PUBLIC_CONTEXT_SOURCE_VALUES else "stored_context_snapshot",
            "input_keys": input_keys,
            "memory_policy_source": memory_policy_source
            if memory_policy_source in PUBLIC_CONTEXT_MEMORY_POLICY_SOURCE_VALUES
            else "not_recorded",
            "long_term_memory_read": long_term_memory_read,
        },
        "latest_artifact_version": latest_artifact_version,
        "execution_tier": execution_tier,
        "context_pack_version": context_pack_version,
        "context_pack_generated_at": generated_at,
        "prompt_summary": prompt_summary,
    }


def ensure_public_context_provenance(
    payload: dict[str, Any],
    *,
    source: str,
    message_count: int = 0,
    file_count: int = 0,
    artifact_count: int = 0,
    memory_record_count: int = 0,
    memory_policy_source: str = "not_recorded",
    long_term_memory_read: bool = False,
    preserve_stored_input_keys: bool = False,
) -> dict[str, Any]:
    sanitized_payload = public_context_payload(payload)
    input_keys = _stored_public_context_input_keys(payload) if preserve_stored_input_keys else None
    stored_source = (
        _stored_public_context_source(payload) or _stored_public_context_top_level_source(payload)
        if preserve_stored_input_keys
        else None
    )
    stored_memory_policy_source = (
        _stored_public_context_memory_policy_source(payload) if preserve_stored_input_keys else None
    )
    stored_long_term_memory_read = (
        _stored_public_context_long_term_memory_read(payload) if preserve_stored_input_keys else None
    )
    stored_generated_at = _stored_public_context_generated_at(payload) if preserve_stored_input_keys else None
    stored_execution_tier = _stored_public_context_execution_tier(payload) if preserve_stored_input_keys else None
    stored_context_pack_version = (
        _stored_public_context_pack_version(payload) if preserve_stored_input_keys else None
    )
    stored_latest_artifact_version = (
        _stored_public_context_latest_artifact_version(payload) if preserve_stored_input_keys else None
    )
    provenance = public_context_provenance(
        source=stored_source or source,
        input_payload=sanitized_payload,
        input_keys=input_keys,
        message_count=message_count,
        file_count=file_count,
        artifact_count=artifact_count,
        memory_record_count=memory_record_count,
        memory_policy_source=stored_memory_policy_source or memory_policy_source,
        long_term_memory_read=stored_long_term_memory_read
        if stored_long_term_memory_read is not None
        else long_term_memory_read,
        latest_artifact_version=stored_latest_artifact_version,
        execution_tier=stored_execution_tier or "sdk_only_writing",
        context_pack_version=stored_context_pack_version or DEFAULT_CONTEXT_PACK_VERSION,
        generated_at=stored_generated_at,
    )
    return {**sanitized_payload, **provenance}


def initial_context_summary(
    *,
    source: str,
    agent_id: str,
    skill_id: str,
    input_payload: dict[str, Any],
    message_ids: list[str],
    file_ids: list[str],
    artifact_count: int = 0,
    latest_artifact_version: str | None = None,
    memory_record_ids: list[str] | None = None,
    memory_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    memory_ids = list(memory_record_ids or [])
    memory_policy_source = str((memory_policy or {}).get("source") or "not_recorded")
    tier_decision = route_office_execution_tier(
        agent_id=agent_id,
        skill_id=skill_id,
        input_payload=input_payload,
        file_ids=file_ids,
    )
    sanitized_input = public_context_payload(input_payload)
    input_keys = _public_context_input_keys_with_material_signals(
        sorted(str(key) for key in sanitized_input.keys()),
        file_count=len(file_ids),
    )
    summary = {
        "schema_version": CONTEXT_SNAPSHOT_SCHEMA_VERSION,
        "source": source,
        "agent_id": agent_id,
        "capability_id": capability_id_from_skill(skill_id, agent_id),
        "input_keys": input_keys,
        "message_count": len(message_ids),
        "file_count": len(file_ids),
        "memory_record_count": len(memory_ids),
    }
    if memory_policy is not None:
        summary["memory_policy"] = {
            "source": str(memory_policy.get("source") or "default"),
            "memory_enabled": bool(memory_policy.get("memory_enabled", True)),
            "long_term_memory_enabled": False,
            "retention_days": int(memory_policy.get("retention_days") or 90),
        }
    summary.update(
        public_context_provenance(
            source=source,
            input_payload=input_payload,
            message_count=len(message_ids),
            file_count=len(file_ids),
            artifact_count=artifact_count,
            memory_record_count=len(memory_ids),
            memory_policy_source=memory_policy_source,
            long_term_memory_read=False,
            latest_artifact_version=latest_artifact_version,
            execution_tier=str(tier_decision["execution_tier"]),
        )
    )
    return summary


def _manifest_current_message(input_payload: dict[str, Any]) -> str:
    message = input_payload.get("message")
    if isinstance(message, str):
        return message
    return ""


def _manifest_context_chips(input_payload: dict[str, Any]) -> list[str]:
    chips = input_payload.get("context_chips")
    if isinstance(chips, list):
        return [str(chip) for chip in chips if isinstance(chip, str)]
    return []


def _manifest_file_refs(file_ids: list[str]) -> list[dict[str, Any]]:
    return [{"id": file_id, "requires_retrieval": True} for file_id in file_ids if file_id]


def _manifest_artifact_refs(artifact_ids: list[str]) -> list[dict[str, Any]]:
    return [{"id": artifact_id, "requires_retrieval": True} for artifact_id in artifact_ids if artifact_id]


def _build_initial_context_manifest(
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    agent_id: str,
    skill_id: str,
    input_payload: dict[str, Any],
    prior_messages: list[dict[str, Any]],
    file_ids: list[str],
    artifact_ids: list[str],
    memory_record_ids: list[str],
    source_run_ids: list[str],
) -> dict[str, Any]:
    return ContextPlanner().plan(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        agent_id=agent_id,
        skill_id=skill_id,
        current_message=_manifest_current_message(input_payload),
        recent_messages=prior_messages,
        context_chips=_manifest_context_chips(input_payload),
        files=_manifest_file_refs(file_ids),
        artifacts=_manifest_artifact_refs(artifact_ids),
        memory_records=[{"id": memory_id, "status": "active"} for memory_id in memory_record_ids if memory_id],
        source_run_ids=source_run_ids,
    )


async def record_initial_context_snapshot(
    conn,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    session_id: str,
    run_id: str,
    trace_id: str,
    agent_id: str,
    skill_id: str,
    input_payload: dict[str, Any],
    message_ids: list[str] | None = None,
    file_ids: list[str] | None = None,
    source: str,
    source_run_id: str | None = None,
    include_session_history: bool = False,
) -> dict[str, Any]:
    included_message_ids = list(message_ids or [])
    prior_messages: list[dict[str, Any]] = []
    included_file_ids = list(file_ids or [])
    included_artifact_ids: list[str] = []
    source_run_ids: list[str] = []
    source_artifacts: list[dict[str, Any]] = []
    if include_session_history:
        session_messages = await repositories.list_session_context_messages(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            limit=8,
        )
        included_message_ids = list(
            dict.fromkeys(
                [
                    str(row.get("id") or "")
                    for row in session_messages
                    if isinstance(row, dict) and row.get("id")
                ]
                + included_message_ids
            )
        )[-8:]
        included_message_id_set = set(included_message_ids)
        current_message_ids = {message_id for message_id in message_ids or [] if message_id}
        prior_messages = [
            dict(row)
            for row in session_messages
            if isinstance(row, dict)
            and str(row.get("id") or "") in included_message_id_set
            and str(row.get("id") or "") not in current_message_ids
            and str(row.get("run_id") or "") != run_id
        ]
        session_files = await repositories.list_session_context_files(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            limit=8,
        )
        included_file_ids = list(
            dict.fromkeys(
                [
                    str(row.get("id") or "")
                    for row in session_files
                    if isinstance(row, dict) and row.get("id")
                ]
                + included_file_ids
            )
        )[-8:]
        session_artifacts = await repositories.list_session_context_artifacts(
            conn,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            session_id=session_id,
            exclude_run_id=run_id,
            limit=8,
        )
        source_artifacts.extend(row for row in session_artifacts if isinstance(row, dict))
    if source_run_id:
        authorized_source_run = await repositories.get_authorized_run(
            conn,
            tenant_id=tenant_id,
            user_id=user_id,
            run_id=source_run_id,
        )
        if (
            authorized_source_run is not None
            and authorized_source_run.get("tenant_id") == tenant_id
            and authorized_source_run.get("user_id") == user_id
            and authorized_source_run.get("workspace_id") == workspace_id
            and authorized_source_run.get("session_id") == session_id
        ):
            explicit_source_artifacts = await repositories.list_run_artifacts(
                conn,
                tenant_id=tenant_id,
                run_id=source_run_id,
            )
            source_artifacts.extend(
                artifact for artifact in explicit_source_artifacts if isinstance(artifact, dict)
            )
    included_artifact_ids = list(
        dict.fromkeys(
            str(artifact.get("id") or "")
            for artifact in source_artifacts
            if artifact.get("id")
        )
    )
    source_run_ids = list(
        dict.fromkeys(
            [
                str(artifact.get("run_id") or "")
                for artifact in source_artifacts
                if artifact.get("run_id")
            ]
            + ([source_run_id] if source_run_id and included_artifact_ids else [])
        )
    )
    latest_artifact_version = next(
        (
            version
            for version in reversed(
                [_public_artifact_version_from_manifest(artifact) for artifact in source_artifacts]
            )
            if version is not None
        ),
        None,
    )
    memory_policy = await repositories.get_effective_memory_policy(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        agent_id=agent_id,
    )
    summary = initial_context_summary(
        source=source,
        agent_id=agent_id,
        skill_id=skill_id,
        input_payload=input_payload,
        message_ids=included_message_ids,
        file_ids=included_file_ids,
        artifact_count=len(included_artifact_ids),
        latest_artifact_version=latest_artifact_version,
        memory_policy=memory_policy,
    )
    summary["context_manifest"] = _build_initial_context_manifest(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        agent_id=agent_id,
        skill_id=skill_id,
        input_payload=input_payload,
        prior_messages=prior_messages,
        file_ids=included_file_ids,
        artifact_ids=included_artifact_ids,
        memory_record_ids=[],
        source_run_ids=source_run_ids,
    )
    memory_policy_summary = {
        "memory_policy_source": str(memory_policy.get("source") or "default"),
        "memory_enabled": bool(memory_policy.get("memory_enabled", True)),
        "long_term_memory_enabled": False,
        "retention_days": int(memory_policy.get("retention_days") or 90),
    }
    snapshot = await repositories.create_context_snapshot(
        conn,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        session_id=session_id,
        run_id=run_id,
        trace_id=trace_id,
        context_kind="executor",
        included_message_ids=included_message_ids,
        included_file_ids=included_file_ids,
        included_artifact_ids=included_artifact_ids,
        included_memory_record_ids=[],
        redaction_summary_json={
            "input_payload_stored": False,
            "raw_skill_selector_stored": False,
            "long_term_memory_read": False,
            **memory_policy_summary,
        },
        payload_json=summary,
    )
    context_ref = {
        "schema_version": CONTEXT_SNAPSHOT_SCHEMA_VERSION,
        "context_snapshot_id": snapshot["id"],
        "source": source,
        "message_count": len(included_message_ids),
        "file_count": len(included_file_ids),
        "memory_record_count": 0,
        "memory_policy": {
            "source": memory_policy_summary["memory_policy_source"],
            "memory_enabled": memory_policy_summary["memory_enabled"],
            "long_term_memory_enabled": memory_policy_summary["long_term_memory_enabled"],
            "retention_days": memory_policy_summary["retention_days"],
        },
        "referenced_materials": summary["referenced_materials"],
        "used_context_summary": summary["used_context_summary"],
        "latest_artifact_version": summary["latest_artifact_version"],
        "execution_tier": summary["execution_tier"],
        "context_pack_version": summary["context_pack_version"],
        "context_pack_generated_at": summary["context_pack_generated_at"],
        "context_manifest": public_context_manifest_projection(summary["context_manifest"]),
    }
    await repositories.update_run_context_snapshot_ref(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        context_snapshot_id=str(snapshot["id"]),
        context_snapshot=context_ref,
    )
    await repositories.append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=trace_id,
        event_type="context_snapshot_created",
        stage="context",
        message="已记录运行上下文快照",
        payload={
            "visible_to_user": False,
            **context_ref,
        },
    )
    return context_ref
