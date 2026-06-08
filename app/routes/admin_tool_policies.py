import json

from fastapi import APIRouter, Depends, HTTPException, Query

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.control_plane_contracts import sanitize_public_payload, sanitize_public_text, standard_trace_id
from app.db import transaction
from app.models import AdminToolPolicyUpdateRequest
from app.tool_policy import RISK_ORDER
from app.validation import assert_safe_id

router = APIRouter()

ADMIN_TOOL_POLICIES_CONTRACT_VERSION = "ai-platform.admin-tool-policies.v1"


def _require_admin(principal: AuthPrincipal) -> None:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")


def _safe_tool_id(tool_id: str) -> str:
    try:
        return assert_safe_id(tool_id, "tool_id")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _policy_requires_decision(policy: dict[str, object]) -> bool:
    if policy.get("effective_status") != "active":
        return False
    risk_level = str(policy.get("risk_level") or "low")
    return bool(policy.get("write_capable")) or RISK_ORDER.get(risk_level, 0) >= RISK_ORDER["medium"]


def _tool_policy_response(policy: dict[str, object]) -> dict[str, object]:
    return {
        "tool_id": str(policy.get("tool_id") or policy.get("id") or ""),
        "server_id": str(policy.get("server_id") or ""),
        "name": str(policy.get("name") or ""),
        "description": str(policy.get("description") or ""),
        "registry_status": str(policy.get("registry_status") or "disabled"),
        "policy_status": str(policy.get("policy_status") or "disabled"),
        "effective_status": str(policy.get("effective_status") or "disabled"),
        "write_capable": bool(policy.get("write_capable")),
        "risk_level": str(policy.get("risk_level") or "low"),
        "visible_to_user": bool(policy.get("visible_to_user")),
        "source": str(policy.get("source") or "registry"),
        "requires_decision": _policy_requires_decision(policy),
        "reason": sanitize_public_text(policy.get("reason")),
        "updated_by": policy.get("updated_by"),
        "updated_at": policy.get("updated_at"),
    }


def _history_payload_response(value: object) -> dict[str, object]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = {}
        value = parsed
    sanitized = sanitize_public_payload(value if isinstance(value, dict) else {})
    if not isinstance(sanitized, dict):
        return {}
    payload: dict[str, object] = {}
    for key in ("tool_id", "status", "risk_level", "reason"):
        item = sanitized.get(key)
        if isinstance(item, str):
            safe_item = sanitize_public_text(item)
            if safe_item:
                payload[key] = safe_item
    for key in ("write_capable", "visible_to_user"):
        item = sanitized.get(key)
        if isinstance(item, bool):
            payload[key] = item
    return payload


def _tool_policy_history_response(row: dict[str, object]) -> dict[str, object]:
    return {
        "audit_id": sanitize_public_text(row.get("id") or row.get("audit_id")),
        "action": sanitize_public_text(row.get("action")),
        "tool_id": sanitize_public_text(row.get("target_id")),
        "updated_by": sanitize_public_text(row.get("user_id")),
        "trace_id": sanitize_public_text(row.get("trace_id")),
        "schema_version": sanitize_public_text(row.get("schema_version")),
        "created_at": row.get("created_at"),
        "payload": _history_payload_response(row.get("payload_json")),
    }


@router.get("/admin/tool-policies")
async def admin_list_tool_policies(
    include_disabled: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Return admin-only tenant-scoped tool policy inventory."""
    _require_admin(principal)
    async with transaction() as conn:
        rows = await repositories.list_admin_tool_policies(
            conn,
            tenant_id=principal.tenant_id,
            include_disabled=include_disabled,
            limit=limit,
        )
    return {
        "contract_version": ADMIN_TOOL_POLICIES_CONTRACT_VERSION,
        "tenant_id": principal.tenant_id,
        "tool_policies": [_tool_policy_response(row) for row in rows],
        "summary": {
            "returned_count": len(rows),
            "limit": limit,
            "include_disabled": include_disabled,
        },
    }


@router.get("/admin/tool-policies/history")
async def admin_list_tool_policy_history(
    tool_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Return admin-only tenant-scoped tool policy audit history."""
    _require_admin(principal)
    safe_tool_id = _safe_tool_id(tool_id) if tool_id else None
    async with transaction() as conn:
        rows = await repositories.list_admin_tool_policy_history(
            conn,
            tenant_id=principal.tenant_id,
            tool_id=safe_tool_id,
            limit=limit,
        )
    return {
        "contract_version": ADMIN_TOOL_POLICIES_CONTRACT_VERSION,
        "tenant_id": principal.tenant_id,
        "history": [_tool_policy_history_response(dict(row)) for row in rows],
        "summary": {
            "returned_count": len(rows),
            "limit": limit,
            "tool_id": safe_tool_id,
        },
    }


@router.put("/admin/tool-policies/{tool_id}")
async def admin_update_tool_policy(
    tool_id: str,
    request: AdminToolPolicyUpdateRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Update the current tenant's tool policy override."""
    _require_admin(principal)
    tool_id = _safe_tool_id(tool_id)
    reason = sanitize_public_text(request.reason)
    try:
        async with transaction() as conn:
            await repositories.ensure_user(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name or principal.user_id,
            )
            row = await repositories.upsert_admin_tool_policy(
                conn,
                tenant_id=principal.tenant_id,
                tool_id=tool_id,
                status=request.status,
                risk_level=request.risk_level,
                write_capable=request.write_capable,
                visible_to_user=request.visible_to_user,
                reason=reason,
                updated_by=principal.user_id,
            )
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="admin.tool_policy.updated",
                target_type="tool_policy",
                target_id=tool_id,
                trace_id=standard_trace_id(tool_id),
                payload_json=sanitize_public_payload(
                    {
                        "tool_id": tool_id,
                        "status": request.status,
                        "risk_level": request.risk_level,
                        "write_capable": request.write_capable,
                        "visible_to_user": request.visible_to_user,
                        "reason": reason,
                    }
                ),
            )
    except repositories.RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "contract_version": ADMIN_TOOL_POLICIES_CONTRACT_VERSION,
        "tenant_id": principal.tenant_id,
        "tool_policy": _tool_policy_response(row),
    }
