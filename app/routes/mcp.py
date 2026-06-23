from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app import repositories
from app.auth import AuthPrincipal, is_ai_admin, require_principal
from app.db import transaction
from app.validation import assert_safe_id

router = APIRouter()


def _require_admin(principal: AuthPrincipal) -> None:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_admin")


def _safe_name(name: str, field_name: str = "mcp_server_name") -> str:
    try:
        return assert_safe_id(name, field_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _lifecycle_not_backed() -> None:
    raise HTTPException(status_code=409, detail="mcp_lifecycle_contract_not_backed")


async def _tool_rows(principal: AuthPrincipal, *, include_disabled: bool = True) -> list[dict[str, Any]]:
    async with transaction() as conn:
        rows = await repositories.list_workbench_mcp_tools(
            conn,
            tenant_id=principal.tenant_id,
            include_disabled=include_disabled,
        )
    return [dict(row) for row in rows]


def _server_name(row: dict[str, Any]) -> str:
    return str(row.get("server_id") or row.get("tool_id") or row.get("id") or "")


def _server_enabled(rows: list[dict[str, Any]]) -> bool:
    return any(str(row.get("effective_status") or row.get("status") or "") == "active" for row in rows)


def _server_response(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    updated_at = next((row.get("updated_at") for row in rows if row.get("updated_at") is not None), None)
    return {
        "name": name,
        "transport": "streamable_http",
        "enabled": _server_enabled(rows),
        "is_system": True,
        "can_edit": False,
        "allowed_roles": ["user"],
        "role_quotas": {},
        "created_at": None,
        "updated_at": updated_at,
    }


def _group_by_server(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        name = _server_name(row)
        if not name:
            continue
        grouped.setdefault(name, []).append(row)
    return grouped


def _find_server(rows: list[dict[str, Any]], *, name: str) -> list[dict[str, Any]]:
    grouped = _group_by_server(rows)
    server_rows = grouped.get(name)
    if not server_rows:
        raise HTTPException(status_code=404, detail="mcp_server_not_found")
    return server_rows


@router.get("/mcp/")
@router.get("/mcp")
async def list_mcp_servers(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    q: str | None = Query(default=None),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Return governed MCP tool servers without exposing unmanaged lifecycle controls."""

    rows = await _tool_rows(principal, include_disabled=True)
    grouped = _group_by_server(rows)
    normalized_query = (q or "").strip().lower()
    server_names = sorted(grouped)
    if normalized_query:
        server_names = [name for name in server_names if normalized_query in name.lower()]
    page_names = server_names[skip : skip + limit]
    return {
        "servers": [_server_response(name, grouped[name]) for name in page_names],
        "total": len(server_names),
        "skip": skip,
        "limit": limit,
    }


@router.post("/mcp/")
@router.post("/mcp")
async def create_mcp_server(
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, Any]:
    """Fail closed for MCP server creation until lifecycle governance is backed."""

    _require_admin(principal)
    _lifecycle_not_backed()


@router.post("/mcp/import")
async def import_mcp_servers(
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, Any]:
    """Fail closed for MCP import until lifecycle governance is backed."""

    _require_admin(principal)
    _lifecycle_not_backed()


@router.get("/mcp/export")
async def export_mcp_servers(
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Export a redacted read-only MCP directory projection."""

    rows = await _tool_rows(principal, include_disabled=True)
    grouped = _group_by_server(rows)
    return {
        "servers": {
            name: _server_response(name, server_rows)
            for name, server_rows in sorted(grouped.items(), key=lambda item: item[0])
        }
    }


@router.get("/mcp/{name}")
async def get_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Return a single governed MCP server projection."""

    safe_name = _safe_name(name)
    rows = await _tool_rows(principal, include_disabled=True)
    return _server_response(safe_name, _find_server(rows, name=safe_name))


@router.put("/mcp/{name}")
async def update_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, Any]:
    """Fail closed for user-scoped MCP server updates."""

    _require_admin(principal)
    _safe_name(name)
    _lifecycle_not_backed()


@router.delete("/mcp/{name}")
async def delete_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Fail closed for user-scoped MCP server deletes."""

    _require_admin(principal)
    _safe_name(name)
    _lifecycle_not_backed()


@router.patch("/mcp/{name}/toggle")
async def toggle_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Fail closed for MCP server availability toggles."""

    _require_admin(principal)
    _safe_name(name)
    _lifecycle_not_backed()


@router.get("/mcp/{name}/tools")
async def discover_mcp_tools(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Return governed tool discovery from the platform registry projection."""

    safe_name = _safe_name(name)
    rows = await _tool_rows(principal, include_disabled=True)
    server_rows = _find_server(rows, name=safe_name)
    tools = []
    for row in server_rows:
        status = str(row.get("effective_status") or row.get("status") or "disabled")
        tools.append(
            {
                "name": str(row.get("tool_id") or row.get("id") or ""),
                "description": str(row.get("description") or ""),
                "parameters": [],
                "system_disabled": status != "active",
                "user_disabled": False,
            }
        )
    return {"server_name": safe_name, "tools": tools, "count": len(tools)}


@router.patch("/mcp/{name}/tools/{tool_name}")
async def toggle_mcp_tool(
    name: str,
    tool_name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, Any]:
    """Fail closed for MCP tool policy toggles outside admin tool policies."""

    _require_admin(principal)
    _safe_name(name)
    _safe_name(tool_name, "mcp_tool_name")
    _lifecycle_not_backed()


@router.post("/admin/mcp/")
@router.post("/admin/mcp")
async def create_admin_mcp_server(
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, Any]:
    """Fail closed for admin MCP server creation until lifecycle governance exists."""

    _require_admin(principal)
    _lifecycle_not_backed()


@router.put("/admin/mcp/{name}")
async def update_admin_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, Any]:
    """Fail closed for admin MCP server updates until lifecycle governance exists."""

    _require_admin(principal)
    _safe_name(name)
    _lifecycle_not_backed()


@router.delete("/admin/mcp/{name}")
async def delete_admin_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, Any]:
    """Fail closed for admin MCP server deletes until lifecycle governance exists."""

    _require_admin(principal)
    _safe_name(name)
    _lifecycle_not_backed()


@router.post("/admin/mcp/{name}/promote")
async def promote_admin_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, Any]:
    """Fail closed for MCP promote operations until lifecycle governance exists."""

    _require_admin(principal)
    _safe_name(name)
    _lifecycle_not_backed()


@router.post("/admin/mcp/{name}/demote")
async def demote_admin_mcp_server(
    name: str,
    principal: AuthPrincipal = Depends(require_principal),
    payload: Any = Body(default=None),
) -> dict[str, Any]:
    """Fail closed for MCP demote operations until lifecycle governance exists."""

    _require_admin(principal)
    _safe_name(name)
    _lifecycle_not_backed()
