"""Private model-call events use the existing tenant/Run event ledger and sequence."""
from typing import Any

from app.streaming.api import append_private_model_call


async def append_model_call_evidence(conn: Any, *, tenant_id: str, run_id: str, attempt_id: str, evidence: dict) -> None:
    await append_private_model_call(conn, tenant_id=tenant_id, run_id=run_id, attempt_id=attempt_id, evidence=evidence)


async def get_recorded_model_call_binding(conn: Any, *, run_id: str, attempt_id: str, call_id: str) -> dict | None:
    """A final observation belongs to its admitted start even after lease release."""
    cursor = await conn.execute("""
        select e.tenant_id from run_events e
        join runs r on r.id=e.run_id and r.tenant_id=e.tenant_id
        join run_attempts a on a.run_id=r.id and a.tenant_id=r.tenant_id and a.id=%s
        where e.run_id=%s and e.event_type='model_call_observed' and e.visible_to_user is false
          and e.payload_json->>'attempt_id'=a.id and e.payload_json->>'call_id'=%s
          and e.payload_json->>'state'='started' limit 1
        """, (attempt_id, run_id, call_id))
    return await cursor.fetchone()


async def read_model_call_evidence(conn: Any, *, tenant_id: str, run_id: str) -> list[dict]:
    cursor = await conn.execute("""
        select payload_json from run_events where tenant_id = %s and run_id = %s
          and event_type = 'model_call_observed' order by sequence desc limit 201
        """, (tenant_id, run_id))
    return list(await cursor.fetchall())
