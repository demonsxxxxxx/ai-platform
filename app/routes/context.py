from typing import Any, Literal
from urllib.parse import unquote, unquote_plus

from fastapi import APIRouter, Depends, HTTPException, Query

from app import repositories
from app.auth import BREAK_GLASS_ADMIN_ROLE, PLATFORM_ADMIN_ROLE, TENANT_ADMIN_ROLE, AuthPrincipal, normalized_roles, require_principal
from app.control_plane_contracts import sanitize_public_payload, standard_trace_id
from app.context_manifest import CONTEXT_MANIFEST_SCHEMA_VERSION, public_context_manifest_projection
from app.db import transaction
from app.memory_redaction import redact_memory_metadata, redact_memory_text
from app.context_builder import ensure_public_context_provenance
from app.models import (
    ContextSnapshotRequest,
    MemoryPolicyRequest,
    MemoryRecordRequest,
    MemoryRedactionPreviewRequest,
    ShareContextSnapshotRequest,
)
from app.projection_redaction import internal_agent_id_for_request, public_agent_id_for_projection
from app.repositories import RepositoryConflictError, RepositoryNotFoundError
from app.validation import assert_safe_id

router = APIRouter()

MEMORY_ADMIN_ROLES = {"admin", TENANT_ADMIN_ROLE, PLATFORM_ADMIN_ROLE, BREAK_GLASS_ADMIN_ROLE}
MEMORY_REDACTION_MODES = {"standard", "strict"}
MEMORY_PREVIEW_FORBIDDEN_TEXT_MARKERS = (
    "executor_payload",
    "executor_private_payload",
    "runtime_private_payload",
    "private_payload",
    "raw_storage_key",
    "storage_key",
    "sandbox_workdir",
)
MEMORY_PREVIEW_INTERNAL_ID_MARKERS = (
    "general-chat",
    "qa-word-review",
    "qa-file-reviewer",
    "baoyu-translate",
    "sop-assistant",
    "ragflow-knowledge-search",
)
MEMORY_PREVIEW_FORBIDDEN_VALUE_MARKERS = (
    "s3://",
    "minio://",
    "gs://",
    "r2://",
    "oss://",
    "cos://",
    "abfs://",
    "wasbs://",
)
MEMORY_PREVIEW_SKILL_PACKAGE_STORAGE_TOKENS = ("skills/", "/versions/", "/package.zip")
MEMORY_PREVIEW_URL_DECODE_DEPTH = 8


def _audit_reason(value: str, *, redaction_mode: str = "standard") -> str:
    return redact_memory_text(value, mode=redaction_mode)


def _is_memory_admin(principal: AuthPrincipal) -> bool:
    return bool(normalized_roles(principal.roles).intersection(MEMORY_ADMIN_ROLES))


def _safe_query_id(value: str, field_name: str) -> str:
    try:
        return assert_safe_id(value, field_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _snapshot_response(
    row: dict[str, Any],
    *,
    provenance_source: str = "stored_context_snapshot",
) -> dict[str, Any]:
    payload = sanitize_public_payload(row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {})
    if not isinstance(payload, dict):
        payload = {}
    context_manifest = payload.get("context_manifest")
    redaction_summary = sanitize_public_payload(
        row.get("redaction_summary_json") if isinstance(row.get("redaction_summary_json"), dict) else {}
    )
    if not isinstance(redaction_summary, dict):
        redaction_summary = {}
    payload = ensure_public_context_provenance(
        payload,
        source=provenance_source,
        message_count=len(row.get("included_message_ids") or []),
        file_count=len(row.get("included_file_ids") or []),
        artifact_count=len(row.get("included_artifact_ids") or []),
        memory_record_count=len(row.get("included_memory_record_ids") or []),
        memory_policy_source="not_recorded",
        long_term_memory_read=False,
        preserve_stored_input_keys=True,
    )
    if isinstance(context_manifest, dict) and context_manifest.get("schema_version") == CONTEXT_MANIFEST_SCHEMA_VERSION:
        payload["context_manifest"] = public_context_manifest_projection(context_manifest)
    return {
        "context_snapshot_id": str(row["id"]),
        "schema_version": str(row.get("schema_version") or "ai-platform.context-snapshot.v1"),
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "session_id": str(row["session_id"]),
        "run_id": str(row["run_id"]),
        "trace_id": str(row.get("trace_id") or standard_trace_id(str(row["run_id"]))),
        "context_kind": str(row.get("context_kind") or "executor"),
        "redaction_summary": redaction_summary,
        "payload": payload,
        "created_at": row.get("created_at"),
    }


def _safe_share_rollback_payload(value: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_public_payload(value if isinstance(value, dict) else {})
    return sanitized if isinstance(sanitized, dict) else {}


def _safe_snapshot_material_ids(row: dict[str, Any], key: str) -> list[str]:
    values = row.get(key)
    if not isinstance(values, list):
        return []
    return [str(item) for item in values if isinstance(item, str)]


def _memory_response(row: dict[str, Any]) -> dict[str, Any]:
    redacted_metadata = redact_memory_metadata(row.get("metadata_json") if isinstance(row.get("metadata_json"), dict) else {})
    metadata = sanitize_public_payload(redacted_metadata, preserve_sensitive_keys=True)
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "memory_record_id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "agent_id": public_agent_id_for_projection(row.get("agent_id")),
        "session_id": row.get("session_id"),
        "record_type": str(row["record_type"]),
        "content": redact_memory_text(row.get("content")),
        "metadata": metadata,
        "status": str(row.get("status") or "active"),
        "expires_at": row.get("expires_at"),
        "deleted_at": row.get("deleted_at"),
        "created_at": row.get("created_at"),
    }


def _memory_delete_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "memory_record_id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "agent_id": public_agent_id_for_projection(row.get("agent_id")),
        "session_id": row.get("session_id"),
        "record_type": str(row["record_type"]),
        "status": str(row.get("status") or "deleted"),
        "deleted_at": row.get("deleted_at"),
        "created_at": row.get("created_at"),
    }


def _memory_operator_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "memory_record_id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "workspace_id": str(row["workspace_id"]),
        "user_id": str(row["user_id"]),
        "agent_id": public_agent_id_for_projection(row.get("agent_id")),
        "session_id": row.get("session_id"),
        "record_type": str(row["record_type"]),
        "status": str(row.get("status") or "active"),
        "expires_at": row.get("expires_at"),
        "deleted_at": row.get("deleted_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _memory_policy_response(policy: dict[str, Any]) -> dict[str, Any]:
    raw_redaction_mode = policy.get("redaction_mode")
    redaction_mode = str(raw_redaction_mode).strip() if raw_redaction_mode is not None else ""
    if not redaction_mode:
        redaction_mode = "strict"
    if redaction_mode not in MEMORY_REDACTION_MODES:
        redaction_mode = "strict"
    return {
        "tenant_id": str(policy["tenant_id"]),
        "workspace_id": str(policy["workspace_id"]),
        "user_id": str(policy["user_id"]),
        "agent_id": public_agent_id_for_projection(policy.get("agent_id")),
        "memory_enabled": bool(policy.get("memory_enabled", True)),
        "long_term_memory_enabled": False,
        "retention_days": int(policy.get("retention_days") or 90),
        "redaction_mode": redaction_mode,
        "source": str(policy.get("source") or "default"),
        "reason": _audit_reason(str(policy.get("reason") or ""), redaction_mode=redaction_mode),
        "updated_by": str(policy.get("updated_by") or ""),
        "updated_at": policy.get("updated_at"),
    }


def _redacted_memory_metadata_preview(value: dict[str, Any], *, redaction_mode: str) -> dict[str, Any]:
    redacted = redact_memory_metadata(value if isinstance(value, dict) else {}, mode=redaction_mode)
    metadata = _redacted_memory_preview_value(redacted, redaction_mode=redaction_mode)
    return metadata if isinstance(metadata, dict) else {}


def _is_allowed_memory_preview_key(key: object) -> bool:
    key_text = str(key)
    normalized_candidates, decode_budget_exhausted = _memory_preview_normalized_candidates(key_text)
    if decode_budget_exhausted:
        return False
    compact_candidates = ["".join(ch for ch in candidate if ch.isalnum()) for candidate in normalized_candidates]
    if any(marker in candidate for candidate in normalized_candidates for marker in MEMORY_PREVIEW_FORBIDDEN_TEXT_MARKERS):
        return False
    if any(marker in candidate for candidate in normalized_candidates for marker in MEMORY_PREVIEW_INTERNAL_ID_MARKERS):
        return False
    if any(marker in candidate for candidate in normalized_candidates for marker in MEMORY_PREVIEW_FORBIDDEN_VALUE_MARKERS):
        return False
    if any(
        all(token in candidate.replace("\\", "/") for token in MEMORY_PREVIEW_SKILL_PACKAGE_STORAGE_TOKENS)
        for candidate in normalized_candidates
    ):
        return False
    if any(
        "".join(ch for ch in marker if ch.isalnum()) in compact_candidate
        for compact_candidate in compact_candidates
        for marker in MEMORY_PREVIEW_FORBIDDEN_TEXT_MARKERS
    ):
        return False
    sanitized = sanitize_public_payload({key_text: "__preview_key__"}, preserve_sensitive_keys=True)
    return isinstance(sanitized, dict) and key_text in sanitized


def _redacted_memory_preview_value(value: Any, *, redaction_mode: str) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _redacted_memory_preview_value(item, redaction_mode=redaction_mode)
            for key, item in value.items()
            if _is_allowed_memory_preview_key(key)
        }
    if isinstance(value, list):
        return [_redacted_memory_preview_value(item, redaction_mode=redaction_mode) for item in value]
    if isinstance(value, str):
        return _redacted_memory_text_preview(value, redaction_mode=redaction_mode)
    return value


def _memory_preview_normalized_candidates(value: str) -> tuple[tuple[str, ...], bool]:
    candidates: list[str] = []
    pending: list[tuple[str, int]] = [(value, 0)]
    decode_budget_exhausted = False
    while pending:
        current, depth = pending.pop(0)
        normalized = current.lower()
        if normalized in candidates:
            continue
        candidates.append(normalized)
        if depth >= MEMORY_PREVIEW_URL_DECODE_DEPTH:
            if any(decoded != current for decoded in {unquote(current), unquote_plus(current)}):
                decode_budget_exhausted = True
            continue
        for decoded in {unquote(current), unquote_plus(current)}:
            if decoded != current:
                pending.append((decoded, depth + 1))
    return tuple(candidates), decode_budget_exhausted


def _redacted_memory_text_preview(value: str, *, redaction_mode: str) -> str:
    preview = redact_memory_text(value, mode=redaction_mode)
    preview = redact_memory_text(preview, mode="strict")
    for marker in MEMORY_PREVIEW_INTERNAL_ID_MARKERS:
        preview = preview.replace(marker, "[redacted-internal-id]")
    sanitized = sanitize_public_payload(preview)
    if not isinstance(sanitized, str):
        return "[redacted-private]"
    normalized_candidates, decode_budget_exhausted = _memory_preview_normalized_candidates(sanitized)
    if decode_budget_exhausted:
        return "[redacted-private]"
    compact_candidates = ["".join(ch for ch in candidate if ch.isalnum()) for candidate in normalized_candidates]
    if any(marker in candidate for candidate in normalized_candidates for marker in MEMORY_PREVIEW_INTERNAL_ID_MARKERS):
        return "[redacted-private]"
    if any(redact_memory_text(candidate, mode="strict") != candidate for candidate in normalized_candidates):
        return "[redacted-private]"
    if any(marker in candidate for candidate in normalized_candidates for marker in MEMORY_PREVIEW_FORBIDDEN_TEXT_MARKERS):
        return "[redacted-private]"
    if any(marker in candidate for candidate in normalized_candidates for marker in MEMORY_PREVIEW_FORBIDDEN_VALUE_MARKERS):
        return "[redacted-private]"
    if any(
        all(token in candidate.replace("\\", "/") for token in MEMORY_PREVIEW_SKILL_PACKAGE_STORAGE_TOKENS)
        for candidate in normalized_candidates
    ):
        return "[redacted-private]"
    if any(
        "".join(ch for ch in marker if ch.isalnum()) in compact_candidate
        for compact_candidate in compact_candidates
        for marker in MEMORY_PREVIEW_FORBIDDEN_TEXT_MARKERS
    ):
        return "[redacted-private]"
    return sanitized


async def _effective_session_agent_id(
    conn,
    *,
    principal: AuthPrincipal,
    workspace_id: str,
    session_id: str | None,
    agent_id: str | None,
) -> str | None:
    internal_agent_id = internal_agent_id_for_request(agent_id) if agent_id else None
    if not session_id:
        return internal_agent_id
    session = await repositories.get_authorized_session(
        conn,
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        session_id=session_id,
    )
    if session is None or str(session.get("workspace_id")) != workspace_id:
        raise RepositoryNotFoundError("session_not_found")
    session_agent_id = str(session.get("agent_id") or "")
    if not session_agent_id:
        raise RepositoryNotFoundError("session_not_found")
    if internal_agent_id and internal_agent_id != session_agent_id:
        raise RepositoryNotFoundError("session_not_found")
    return internal_agent_id or session_agent_id


@router.post("/runs/{run_id}/context/snapshots")
async def create_run_context_snapshot(
    run_id: str,
    request: ContextSnapshotRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    async with transaction() as conn:
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        trace_id = str(run.get("trace_id") or standard_trace_id(run_id))
        redaction_summary = sanitize_public_payload(request.redaction_summary)
        if not isinstance(redaction_summary, dict):
            redaction_summary = {}
        payload = ensure_public_context_provenance(
            request.payload,
            source="manual_context_snapshot",
            message_count=len(request.included_message_ids),
            file_count=len(request.included_file_ids),
            artifact_count=len(request.included_artifact_ids),
            memory_record_count=len(request.included_memory_record_ids),
            memory_policy_source="not_recorded",
            long_term_memory_read=False,
        )
        snapshot = await repositories.create_context_snapshot(
            conn,
            tenant_id=principal.tenant_id,
            workspace_id=str(run["workspace_id"]),
            user_id=principal.user_id,
            session_id=str(run["session_id"]),
            run_id=run_id,
            trace_id=trace_id,
            context_kind=request.context_kind,
            included_message_ids=request.included_message_ids,
            included_file_ids=request.included_file_ids,
            included_artifact_ids=request.included_artifact_ids,
            included_memory_record_ids=request.included_memory_record_ids,
            redaction_summary_json=redaction_summary,
            payload_json=payload,
        )
        await repositories.append_event(
            conn,
            tenant_id=principal.tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            event_type="context_snapshot_created",
            stage="context",
            message="已记录运行上下文快照",
            payload={
                "visible_to_user": False,
                "context_snapshot_id": snapshot["id"],
                "context_kind": request.context_kind,
                "message_count": len(request.included_message_ids),
                "file_count": len(request.included_file_ids),
                "artifact_count": len(request.included_artifact_ids),
                "memory_record_count": len(request.included_memory_record_ids),
            },
        )
    return {"context_snapshot": _snapshot_response(snapshot, provenance_source="manual_context_snapshot")}


@router.post("/runs/{run_id}/context/share-snapshots")
async def create_share_context_snapshot(
    run_id: str,
    request: ShareContextSnapshotRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Create a redacted share/fork context snapshot after source and target scope checks."""
    async with transaction() as conn:
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        workspace_id = str(run["workspace_id"])
        source_session_id = str(run["session_id"])
        target_session = await repositories.get_authorized_context_target_session(
            conn,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            user_id=principal.user_id,
            session_id=request.target_session_id,
        )
        if target_session is None:
            raise HTTPException(status_code=404, detail="target_session_not_found")
        trace_id = str(run.get("trace_id") or standard_trace_id(run_id))
        source_snapshot = await repositories.get_latest_authorized_executor_context_snapshot(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if source_snapshot is None:
            raise HTTPException(status_code=409, detail="source_context_snapshot_not_found")
        source_payload = sanitize_public_payload(
            source_snapshot.get("payload_json") if isinstance(source_snapshot.get("payload_json"), dict) else {}
        )
        if not isinstance(source_payload, dict):
            source_payload = {}
        source_message_ids = _safe_snapshot_material_ids(source_snapshot, "included_message_ids")
        source_file_ids = _safe_snapshot_material_ids(source_snapshot, "included_file_ids")
        source_artifact_ids = _safe_snapshot_material_ids(source_snapshot, "included_artifact_ids")
        share_source = f"{request.share_kind}_context_snapshot"
        payload = sanitize_public_payload(request.payload)
        if not isinstance(payload, dict):
            payload = {}
        payload = {
            **payload,
            **source_payload,
            "share_fork_context": {
                "share_kind": request.share_kind,
                "source_session_id": source_session_id,
                "target_session_id": request.target_session_id,
                "redaction_state": "public_redacted",
                "lineage": {
                    "source_binding": "authorized_route_run",
                    "target_binding": "authorized_target_session",
                },
                "rollback": _safe_share_rollback_payload(request.rollback),
            },
        }
        payload = ensure_public_context_provenance(
            payload,
            source=share_source,
            message_count=len(source_message_ids),
            file_count=len(source_file_ids),
            artifact_count=len(source_artifact_ids),
            memory_record_count=0,
            memory_policy_source="not_recorded",
            long_term_memory_read=False,
        )
        redaction_summary = {
            "redaction_state": "public_redacted",
            "share_kind": request.share_kind,
            "source_session_bound": True,
            "target_session_bound": True,
            "long_term_memory_read": False,
        }
        snapshot = await repositories.create_context_snapshot(
            conn,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            user_id=principal.user_id,
            session_id=source_session_id,
            run_id=run_id,
            trace_id=trace_id,
            context_kind="share_fork",
            included_message_ids=source_message_ids,
            included_file_ids=source_file_ids,
            included_artifact_ids=source_artifact_ids,
            included_memory_record_ids=[],
            redaction_summary_json=redaction_summary,
            payload_json=payload,
        )
        await repositories.append_event(
            conn,
            tenant_id=principal.tenant_id,
            run_id=run_id,
            trace_id=trace_id,
            event_type="context_snapshot_created",
            stage="context",
            message="已记录共享上下文快照",
            payload={
                "visible_to_user": False,
                "context_snapshot_id": snapshot["id"],
                "context_kind": "share_fork",
                "share_kind": request.share_kind,
                "redaction_state": "public_redacted",
                "source_session_bound": True,
                "target_session_bound": True,
                "message_count": len(source_message_ids),
                "file_count": len(source_file_ids),
                "artifact_count": len(source_artifact_ids),
                "memory_record_count": 0,
            },
        )
    return {"context_snapshot": _snapshot_response(snapshot, provenance_source=share_source)}


@router.get("/runs/{run_id}/context/snapshots")
async def list_run_context_snapshots(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    async with transaction() as conn:
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        rows = await repositories.list_context_snapshots(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
    return {"run_id": run_id, "context_snapshots": [_snapshot_response(row) for row in rows]}


@router.get("/sessions/{session_id}/context/share-snapshots")
async def list_target_session_share_context_snapshots(
    session_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    async with transaction() as conn:
        session = await repositories.get_authorized_session(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            session_id=session_id,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="target_session_not_found")
        rows = await repositories.list_context_share_snapshots_for_target_session(
            conn,
            tenant_id=principal.tenant_id,
            workspace_id=str(session["workspace_id"]),
            user_id=principal.user_id,
            target_session_id=session_id,
        )
    return {
        "session_id": session_id,
        "context_snapshots": [_snapshot_response(row) for row in rows],
    }


@router.post("/memory/records")
async def create_memory_record(
    request: MemoryRecordRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not request.session_id:
        raise HTTPException(status_code=400, detail="memory_session_id_required")
    internal_agent_id = internal_agent_id_for_request(request.agent_id) if request.agent_id else None
    denied_by_policy = False
    try:
        async with transaction() as conn:
            await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=request.workspace_id)
            await repositories.ensure_user(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name,
            )
            effective_agent_id = await _effective_session_agent_id(
                conn,
                principal=principal,
                workspace_id=request.workspace_id,
                session_id=request.session_id,
                agent_id=internal_agent_id,
            )
            policy = await repositories.get_effective_memory_policy(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=request.workspace_id,
                user_id=principal.user_id,
                agent_id=effective_agent_id,
            )
            if not bool(policy.get("memory_enabled", True)):
                await repositories.append_audit_log(
                    conn,
                    tenant_id=principal.tenant_id,
                    user_id=principal.user_id,
                    action="memory.record.create_denied",
                    target_type="memory_policy",
                    target_id=principal.user_id,
                    trace_id=standard_trace_id(principal.user_id),
                    payload_json=sanitize_public_payload(
                        {
                            "workspace_id": request.workspace_id,
                            "agent_id": public_agent_id_for_projection(effective_agent_id) or effective_agent_id,
                            "session_id": request.session_id,
                            "record_type": request.record_type,
                            "reason": "memory_policy_disabled",
                        }
                    ),
                )
                denied_by_policy = True
            if denied_by_policy:
                record = None
            else:
                record = await repositories.create_memory_record(
                    conn,
                    tenant_id=principal.tenant_id,
                    workspace_id=request.workspace_id,
                    user_id=principal.user_id,
                    agent_id=effective_agent_id,
                    session_id=request.session_id,
                    record_type=request.record_type,
                    content=request.content,
                    metadata_json=request.metadata,
                    retention_days=int(policy.get("retention_days") or 90),
                    redaction_mode=str(policy.get("redaction_mode") or "standard"),
                )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if denied_by_policy:
        raise HTTPException(status_code=403, detail="memory_policy_disabled")
    return {"memory_record": _memory_response(record)}


@router.get("/memory/records")
async def list_memory_records(
    workspace_id: str = "default",
    agent_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    workspace_id = _safe_query_id(workspace_id, "workspace_id")
    agent_id = _safe_query_id(agent_id, "agent_id") if agent_id else None
    internal_agent_id = internal_agent_id_for_request(agent_id) if agent_id else None
    session_id = _safe_query_id(session_id, "session_id") if session_id else None
    if not session_id:
        raise HTTPException(status_code=400, detail="memory_session_id_required")
    try:
        async with transaction() as conn:
            effective_agent_id = await _effective_session_agent_id(
                conn,
                principal=principal,
                workspace_id=workspace_id,
                session_id=session_id,
                agent_id=internal_agent_id,
            )
            policy = await repositories.get_effective_memory_policy(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                user_id=principal.user_id,
                agent_id=effective_agent_id,
            )
            if not bool(policy.get("memory_enabled", True)):
                return {"memory_records": []}
            rows = await repositories.list_memory_records(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                user_id=principal.user_id,
                agent_id=effective_agent_id,
                session_id=session_id,
                limit=max(min(limit, 200), 1),
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"memory_records": [_memory_response(row) for row in rows]}


@router.delete("/memory/records/{record_id}")
async def delete_memory_record(
    record_id: str,
    workspace_id: str = "default",
    agent_id: str | None = None,
    session_id: str | None = None,
    reason: str = Query(default="", max_length=2000),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    record_id = _safe_query_id(record_id, "record_id")
    workspace_id = _safe_query_id(workspace_id, "workspace_id")
    agent_id = _safe_query_id(agent_id, "agent_id") if agent_id else None
    internal_agent_id = internal_agent_id_for_request(agent_id) if agent_id else None
    session_id = _safe_query_id(session_id, "session_id") if session_id else None
    if not session_id:
        raise HTTPException(status_code=400, detail="memory_session_id_required")
    try:
        async with transaction() as conn:
            effective_agent_id = await _effective_session_agent_id(
                conn,
                principal=principal,
                workspace_id=workspace_id,
                session_id=session_id,
                agent_id=internal_agent_id,
            )
            row = await repositories.delete_memory_record(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                user_id=principal.user_id,
                agent_id=effective_agent_id,
                session_id=session_id,
                record_id=record_id,
            )
            if row is None:
                raise HTTPException(status_code=404, detail="memory_record_not_found")
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="memory.record.deleted",
                target_type="memory_record",
                target_id=record_id,
                trace_id=standard_trace_id(record_id),
                payload_json=sanitize_public_payload(
                    {
                        "workspace_id": workspace_id,
                        "agent_id": public_agent_id_for_projection(row.get("agent_id")),
                        "session_id": row.get("session_id"),
                        "record_type": row.get("record_type"),
                        "reason": _audit_reason(reason),
                    }
                ),
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"memory_record": _memory_delete_response(row)}


@router.get("/memory/policy")
async def get_memory_policy(
    workspace_id: str = "default",
    agent_id: str | None = None,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    workspace_id = _safe_query_id(workspace_id, "workspace_id")
    agent_id = _safe_query_id(agent_id, "agent_id") if agent_id else None
    internal_agent_id = internal_agent_id_for_request(agent_id) if agent_id else None
    try:
        async with transaction() as conn:
            await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=workspace_id)
            if internal_agent_id:
                target_agent = await repositories.get_agent(
                    conn,
                    tenant_id=principal.tenant_id,
                    agent_id=internal_agent_id,
                )
                if target_agent is None:
                    raise RepositoryNotFoundError("agent_not_found")
            policy = await repositories.get_effective_memory_policy(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                user_id=principal.user_id,
                agent_id=internal_agent_id,
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"memory_policy": _memory_policy_response(policy)}


@router.put("/memory/policy")
async def update_memory_policy(
    request: MemoryPolicyRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Let the authenticated user manage their own public memory policy."""
    if request.long_term_memory_enabled:
        raise HTTPException(status_code=409, detail="long_term_memory_not_available")
    reason = _audit_reason(request.reason, redaction_mode=request.redaction_mode)
    internal_agent_id = internal_agent_id_for_request(request.agent_id) if request.agent_id else None
    public_agent_id = public_agent_id_for_projection(internal_agent_id) if internal_agent_id else None
    try:
        async with transaction() as conn:
            await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=request.workspace_id)
            if internal_agent_id:
                target_agent = await repositories.get_agent(
                    conn,
                    tenant_id=principal.tenant_id,
                    agent_id=internal_agent_id,
                )
                if target_agent is None:
                    raise RepositoryNotFoundError("agent_not_found")
            await repositories.ensure_user(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name,
            )
            policy = await repositories.set_memory_policy(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=request.workspace_id,
                user_id=principal.user_id,
                agent_id=internal_agent_id,
                memory_enabled=request.memory_enabled,
                long_term_memory_enabled=False,
                retention_days=request.retention_days,
                redaction_mode=request.redaction_mode,
                reason=reason,
                updated_by=principal.user_id,
            )
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="memory.policy.updated",
                target_type="memory_policy",
                target_id=principal.user_id,
                trace_id=standard_trace_id(principal.user_id),
                payload_json=sanitize_public_payload(
                    {
                        "workspace_id": request.workspace_id,
                        "target_user_id": principal.user_id,
                        "agent_id": public_agent_id,
                        "memory_enabled": request.memory_enabled,
                        "long_term_memory_enabled": False,
                        "retention_days": request.retention_days,
                        "redaction_mode": request.redaction_mode,
                        "reason": reason,
                    }
                ),
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"memory_policy": _memory_policy_response(policy)}


@router.get("/admin/memory/policies")
async def admin_list_memory_policies(
    workspace_id: str = "default",
    user_id: str | None = None,
    agent_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Return same-tenant memory policy inventory for memory admins."""
    if not _is_memory_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_memory_admin")
    workspace_id = _safe_query_id(workspace_id, "workspace_id")
    user_id = _safe_query_id(user_id, "user_id") if user_id else None
    agent_id = _safe_query_id(agent_id, "agent_id") if agent_id else None
    internal_agent_id = internal_agent_id_for_request(agent_id) if agent_id else None
    try:
        async with transaction() as conn:
            await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=workspace_id)
            if internal_agent_id:
                target_agent = await repositories.get_agent(
                    conn,
                    tenant_id=principal.tenant_id,
                    agent_id=internal_agent_id,
                )
                if target_agent is None:
                    raise RepositoryNotFoundError("agent_not_found")
            rows = await repositories.list_admin_memory_policies(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                agent_id=internal_agent_id,
                limit=limit,
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "memory_policies": [_memory_policy_response(row) for row in rows],
        "summary": {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "agent_id": public_agent_id_for_projection(internal_agent_id) if internal_agent_id else None,
            "returned_count": len(rows),
            "limit": limit,
        },
    }


@router.post("/admin/memory/redaction/preview")
async def admin_preview_memory_redaction(
    request: MemoryRedactionPreviewRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Preview memory redaction policy output without writing memory content."""
    if not _is_memory_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_memory_admin")
    internal_agent_id = internal_agent_id_for_request(request.agent_id) if request.agent_id else None
    public_agent_id = public_agent_id_for_projection(internal_agent_id) if internal_agent_id else None
    content_preview = _redacted_memory_text_preview(request.content, redaction_mode=request.redaction_mode)
    metadata_preview = _redacted_memory_metadata_preview(request.metadata, redaction_mode=request.redaction_mode)
    reason_preview = _redacted_memory_text_preview(request.reason, redaction_mode=request.redaction_mode)
    changes = {
        "content_redacted": content_preview != request.content,
        "metadata_redacted": metadata_preview != (request.metadata if isinstance(request.metadata, dict) else {}),
        "reason_redacted": reason_preview != request.reason,
    }
    try:
        async with transaction() as conn:
            await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=request.workspace_id)
            if internal_agent_id:
                target_agent = await repositories.get_agent(
                    conn,
                    tenant_id=principal.tenant_id,
                    agent_id=internal_agent_id,
                )
                if target_agent is None:
                    raise RepositoryNotFoundError("agent_not_found")
            audit_id = await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="admin.memory.redaction.previewed",
                target_type="memory_redaction_policy",
                target_id=request.workspace_id,
                trace_id=standard_trace_id(request.workspace_id),
                payload_json=sanitize_public_payload(
                    {
                        "workspace_id": request.workspace_id,
                        "agent_id": public_agent_id,
                        "redaction_mode": request.redaction_mode,
                        **changes,
                        "reason": reason_preview,
                    }
                ),
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "memory_redaction_preview": {
            "schema_version": "ai-platform.memory-redaction-preview.v1",
            "tenant_id": principal.tenant_id,
            "workspace_id": request.workspace_id,
            "agent_id": public_agent_id,
            "redaction_mode": request.redaction_mode,
            "content_preview": content_preview,
            "metadata_preview": metadata_preview,
            "reason_preview": reason_preview,
            "changes": changes,
            "audit": {
                "action": "admin.memory.redaction.previewed",
                "audit_id": audit_id,
            },
        }
    }


@router.put("/admin/memory/policies/{target_user_id}")
async def admin_set_memory_policy(
    target_user_id: str,
    request: MemoryPolicyRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not _is_memory_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_memory_admin")
    if request.long_term_memory_enabled:
        raise HTTPException(status_code=409, detail="long_term_memory_not_available")
    target_user_id = assert_safe_id(target_user_id, "target_user_id")
    reason = _audit_reason(request.reason, redaction_mode=request.redaction_mode)
    internal_agent_id = internal_agent_id_for_request(request.agent_id) if request.agent_id else None
    public_agent_id = public_agent_id_for_projection(internal_agent_id) if internal_agent_id else None
    try:
        async with transaction() as conn:
            await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=request.workspace_id)
            target_user = await repositories.get_user(conn, tenant_id=principal.tenant_id, user_id=target_user_id)
            if target_user is None:
                raise RepositoryNotFoundError("user_not_found")
            if internal_agent_id:
                target_agent = await repositories.get_agent(
                    conn,
                    tenant_id=principal.tenant_id,
                    agent_id=internal_agent_id,
                )
                if target_agent is None:
                    raise RepositoryNotFoundError("agent_not_found")
            policy = await repositories.set_memory_policy(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=request.workspace_id,
                user_id=target_user_id,
                agent_id=internal_agent_id,
                memory_enabled=request.memory_enabled,
                long_term_memory_enabled=False,
                retention_days=request.retention_days,
                redaction_mode=request.redaction_mode,
                reason=reason,
                updated_by=principal.user_id,
            )
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="admin.memory.policy.updated",
                target_type="memory_policy",
                target_id=target_user_id,
                trace_id=standard_trace_id(target_user_id),
                payload_json=sanitize_public_payload(
                    {
                        "workspace_id": request.workspace_id,
                        "target_user_id": target_user_id,
                        "agent_id": public_agent_id,
                        "memory_enabled": request.memory_enabled,
                        "long_term_memory_enabled": False,
                        "retention_days": request.retention_days,
                        "redaction_mode": request.redaction_mode,
                        "reason": reason,
                    }
                ),
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"memory_policy": _memory_policy_response(policy)}


@router.post("/admin/memory/retention/cleanup")
async def admin_cleanup_expired_memory_records(
    workspace_id: str = "default",
    limit: int = Query(default=200, ge=1, le=500),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not _is_memory_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_memory_admin")
    workspace_id = assert_safe_id(workspace_id, "workspace_id")
    try:
        async with transaction() as conn:
            await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=workspace_id)
            rows = await repositories.cleanup_expired_memory_records(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                limit=limit,
            )
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="admin.memory.retention.cleanup",
                target_type="memory_retention",
                target_id=workspace_id,
                trace_id=standard_trace_id(workspace_id),
                payload_json=sanitize_public_payload(
                    {
                        "workspace_id": workspace_id,
                        "deleted_count": len(rows),
                        "memory_record_ids": [str(row.get("id")) for row in rows],
                        "target_user_ids": sorted({str(row.get("user_id")) for row in rows if row.get("user_id")}),
                        "reason": "retention_expired",
                    }
                ),
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "deleted_count": len(rows),
        "memory_records": [_memory_delete_response(row) for row in rows],
    }


@router.get("/admin/memory/records")
async def admin_list_memory_records(
    workspace_id: str = "default",
    user_id: str | None = None,
    status: Literal["active", "deleted", "all"] = "active",
    limit: int = Query(default=50, ge=1, le=500),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not _is_memory_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_memory_admin")
    workspace_id = _safe_query_id(workspace_id, "workspace_id")
    user_id = _safe_query_id(user_id, "user_id") if user_id else None
    try:
        async with transaction() as conn:
            await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=workspace_id)
            rows = await repositories.list_admin_memory_records(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                status=status,
                limit=limit,
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "memory_records": [_memory_operator_response(row) for row in rows],
        "summary": {
            "workspace_id": workspace_id,
            "status": status,
            "returned_count": len(rows),
            "limit": limit,
        },
    }


@router.delete("/admin/memory/records/{record_id}")
async def admin_delete_memory_record(
    record_id: str,
    workspace_id: str = "default",
    reason: str = Query(default="", max_length=2000),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not _is_memory_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_memory_admin")
    record_id = assert_safe_id(record_id, "record_id")
    workspace_id = assert_safe_id(workspace_id, "workspace_id")
    async with transaction() as conn:
        row = await repositories.admin_delete_memory_record(
            conn,
            tenant_id=principal.tenant_id,
            workspace_id=workspace_id,
            record_id=record_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="memory_record_not_found")
        await repositories.append_audit_log(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            action="admin.memory.record.deleted",
            target_type="memory_record",
            target_id=record_id,
            trace_id=standard_trace_id(record_id),
            payload_json=sanitize_public_payload(
                {
                    "workspace_id": workspace_id,
                    "target_user_id": row.get("user_id"),
                    "agent_id": row.get("agent_id"),
                    "session_id": row.get("session_id"),
                    "record_type": row.get("record_type"),
                    "reason": _audit_reason(reason),
                }
            ),
        )
    return {"memory_record": _memory_delete_response(row)}
