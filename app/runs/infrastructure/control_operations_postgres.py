"""Control operations persistence. Callers own the connection and transaction."""

from __future__ import annotations

from app.platform.postgres.errors import RepositoryConflictError
from app.platform.postgres.values import dumps_json
from app.streaming.infrastructure.run_events_postgres import append_event
from psycopg import AsyncConnection
from typing import Any
import uuid


RUN_CONTROL_OPERATION_ACTIONS = {"retry", "resume"}


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
