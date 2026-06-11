from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote, unquote_plus

from app import repositories
from app.control_plane_contracts import CONTEXT_SNAPSHOT_SCHEMA_VERSION, sanitize_public_payload
from app.projection_redaction import capability_id_from_skill


PUBLIC_CONTEXT_PROVENANCE_KEYS = {
    "provenance",
    "referenced_materials",
    "used_context_summary",
    "latest_artifact_version",
    "execution_tier",
    "context_pack_generated_at",
    "source",
}

PUBLIC_CONTEXT_SUMMARY_KEYS = {
    "agent_id",
    "artifact_count",
    "capability_id",
    "context_snapshot_id",
    "file_count",
    "included_artifact_ids",
    "included_file_ids",
    "included_memory_record_ids",
    "included_message_ids",
    "input_keys",
    "memory_policy",
    "memory_record_count",
    "message_count",
    "schema_version",
    "skill_id",
    "summary",
}

PUBLIC_CONTEXT_FORBIDDEN_KEY_ALIASES = {
    "absoluteruntimepaths",
    "executorprivatepayload",
    "privatepayload",
    "rawstoragekey",
    "sandboxworkdir",
    "secretlikevalues",
    "storagekey",
}

PUBLIC_CONTEXT_MATERIAL_COUNT_KEYS = {
    "message_count",
    "file_count",
    "artifact_count",
    "memory_record_count",
}
PUBLIC_CONTEXT_KEY_DECODE_DEPTH = 8


def _normalized_public_context_key(value: object) -> str:
    return "".join(ch for ch in str(value) if ch.isalnum()).lower()


def _normalized_public_context_key_candidates(value: object) -> tuple[str, ...]:
    raw = str(value)
    candidates: list[str] = []
    pending: list[tuple[str, int]] = [(raw, 0)]
    while pending:
        current, depth = pending.pop(0)
        normalized = _normalized_public_context_key(current)
        if normalized and normalized not in candidates:
            candidates.append(normalized)
        if depth >= PUBLIC_CONTEXT_KEY_DECODE_DEPTH:
            continue
        for decoded in {unquote(current), unquote_plus(current)}:
            if decoded != current:
                pending.append((decoded, depth + 1))
    return tuple(candidates)


PUBLIC_CONTEXT_PROVENANCE_KEY_ALIASES = {
    _normalized_public_context_key(key)
    for key in PUBLIC_CONTEXT_PROVENANCE_KEYS
}

PUBLIC_CONTEXT_SUMMARY_KEY_ALIASES = {
    _normalized_public_context_key(key)
    for key in PUBLIC_CONTEXT_SUMMARY_KEYS
}

PUBLIC_CONTEXT_SUMMARY_PREFIX_ALIASES = {
    "provenance",
    "summary",
}


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
            normalized_keys = _normalized_public_context_key_candidates(key_text)
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


def public_context_provenance(
    *,
    source: str,
    input_payload: dict[str, Any] | None = None,
    message_count: int = 0,
    file_count: int = 0,
    artifact_count: int = 0,
    memory_record_count: int = 0,
    memory_policy_source: str = "not_recorded",
    long_term_memory_read: bool = False,
    latest_artifact_version: str | None = None,
    execution_tier: str = "sdk_only_writing",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the user-visible context provenance contract without exposing raw ids."""
    sanitized_input = public_context_payload(input_payload or {})
    input_keys = sorted(str(key) for key in sanitized_input.keys())
    return {
        "referenced_materials": {
            "message_count": max(0, int(message_count)),
            "file_count": max(0, int(file_count)),
            "artifact_count": max(0, int(artifact_count)),
            "memory_record_count": max(0, int(memory_record_count)),
        },
        "used_context_summary": {
            "source": str(source),
            "input_keys": input_keys,
            "memory_policy_source": str(memory_policy_source or "not_recorded"),
            "long_term_memory_read": bool(long_term_memory_read),
        },
        "latest_artifact_version": latest_artifact_version,
        "execution_tier": str(execution_tier or "sdk_only_writing"),
        "context_pack_generated_at": generated_at or _utc_now_iso(),
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
) -> dict[str, Any]:
    sanitized_payload = public_context_payload(payload)
    provenance = public_context_provenance(
        source=source,
        input_payload=sanitized_payload,
        message_count=message_count,
        file_count=file_count,
        artifact_count=artifact_count,
        memory_record_count=memory_record_count,
        memory_policy_source=memory_policy_source,
        long_term_memory_read=long_term_memory_read,
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
    memory_record_ids: list[str] | None = None,
    memory_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    memory_ids = list(memory_record_ids or [])
    memory_policy_source = str((memory_policy or {}).get("source") or "not_recorded")
    sanitized_input = public_context_payload(input_payload)
    input_keys = sorted(str(key) for key in sanitized_input.keys())
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
            artifact_count=0,
            memory_record_count=len(memory_ids),
            memory_policy_source=memory_policy_source,
            long_term_memory_read=False,
        )
    )
    return summary


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
) -> dict[str, Any]:
    included_message_ids = list(message_ids or [])
    included_file_ids = list(file_ids or [])
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
        memory_policy=memory_policy,
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
        included_artifact_ids=[],
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
        "context_pack_generated_at": summary["context_pack_generated_at"],
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
