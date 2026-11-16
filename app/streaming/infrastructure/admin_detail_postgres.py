"""Bounded persisted public facts for administrator result assembly, not transport DTOs."""
from typing import Any

from psycopg import AsyncConnection

ADMIN_DETAIL_EVENT_LIMIT = 2000
ADMIN_DETAIL_PAYLOAD_BYTES = 512 * 1024


async def read_admin_detail_events(conn: AsyncConnection, *, tenant_id: str, run_id: str) -> dict[str, Any]:
    cursor = await conn.execute("""
        with recent as (
          select e.*, exists (
            select 1 from run_attempts a where a.tenant_id=e.tenant_id and a.run_id=e.run_id
              and a.id=e.payload_json->'__stream_v4'->>'attempt_id'
          ) as v4_attempt_authorized
          from run_events e where e.tenant_id=%s and e.run_id=%s and e.visible_to_user is true
          order by e.sequence desc limit %s
        ), budgeted as (
          select recent.*, sum(octet_length(payload_json::text)) over (order by sequence desc) as payload_bytes
          from recent
        )
        select id, tenant_id, run_id, sequence, event_type, stage, visible_to_user,
               created_at, v4_attempt_authorized,
               case when payload_bytes <= %s then payload_json else null end as retained_payload
        from budgeted order by sequence desc
        """, (tenant_id, run_id, ADMIN_DETAIL_EVENT_LIMIT + 1, ADMIN_DETAIL_PAYLOAD_BYTES))
    rows = list(await cursor.fetchall())
    retained = [row for row in rows[:ADMIN_DETAIL_EVENT_LIMIT] if row.get("retained_payload") is not None]
    truncated = len(retained) < len(rows)
    facts = []
    rejected_attempt_events = 0
    for row in reversed(retained):
        fact = dict(row)
        fact["payload_json"] = fact.pop("retained_payload")
        if "__stream_v4" in fact["payload_json"] and fact.get("v4_attempt_authorized") is not True:
            rejected_attempt_events += 1
            continue
        # Legacy chunk streams have no independently verifiable completion ledger.
        # A bounded slice must never promote a partial legacy message to an answer.
        if truncated and fact["event_type"] in {"assistant_delta", "commentary.delta"}:
            continue
        facts.append(fact)
    return {"rows": facts, "truncated": truncated, "event_limit": ADMIN_DETAIL_EVENT_LIMIT,
            "payload_budget_bytes": ADMIN_DETAIL_PAYLOAD_BYTES, "rejected_attempt_events": rejected_attempt_events}
