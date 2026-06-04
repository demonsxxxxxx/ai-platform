from __future__ import annotations

from typing import Any

from app import repositories
from app.control_plane_contracts import CONTEXT_SNAPSHOT_SCHEMA_VERSION
from app.projection_redaction import capability_id_from_skill


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
    summary = {
        "schema_version": CONTEXT_SNAPSHOT_SCHEMA_VERSION,
        "source": source,
        "agent_id": agent_id,
        "capability_id": capability_id_from_skill(skill_id, agent_id),
        "input_keys": sorted(str(key) for key in input_payload.keys()),
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
