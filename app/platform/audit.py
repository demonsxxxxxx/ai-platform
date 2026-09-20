"""Small shared writer for redacted product audit facts."""

from __future__ import annotations

import json
import uuid
from typing import Any


class PostgresAuditWriter:
    async def append(
        self,
        conn: Any,
        *,
        tenant_id: str,
        actor_id: str,
        action: str,
        target_type: str,
        target_id: str,
        operation_id: str,
        payload: dict[str, Any],
    ) -> str:
        audit_id = f"aud_{uuid.uuid4().hex}"
        safe_payload = {"operation_id": operation_id, **payload}
        encoded = json.dumps(
            safe_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(encoded.encode("utf-8")) > 8192:
            raise ValueError("audit_payload_too_large")
        await conn.execute(
            """
            insert into audit_logs(
              id, tenant_id, user_id, action, target_type, target_id,
              schema_version, payload_json
            ) values (%s, %s, %s, %s, %s, %s, 'ai-platform.audit-event.v1', %s::jsonb)
            """,
            (
                audit_id,
                tenant_id,
                actor_id,
                action,
                target_type,
                target_id,
                encoded,
            ),
        )
        return audit_id


__all__ = ["PostgresAuditWriter"]
