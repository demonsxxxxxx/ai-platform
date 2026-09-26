"""Audit persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.control_plane_contracts import AUDIT_EVENT_SCHEMA_VERSION
from app.control_plane_contracts import standard_trace_id
from app.platform.postgres.errors import RepositoryAuthorizationError
from app.platform.postgres.limits import AUDIT_PAYLOAD_MAX_BYTES
from app.platform.postgres.values import _require_json_size
from app.platform.postgres.values import dumps_json
from app.platform.postgres.values import new_id
from psycopg import AsyncConnection
from typing import Any


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
