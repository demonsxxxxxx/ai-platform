"""Private model-request ledger append, never a public stream publication."""
from typing import Any

from app.streaming.infrastructure.event_ledger_postgres import LedgerEvent, append_event


async def append_run_trigger(conn: Any, *, tenant_id: str, run_id: str, source: str) -> None:
    await append_event(conn, tenant_id=tenant_id, run_id=run_id,
        event_id="evt_trigger_" + run_id,
        event=LedgerEvent(event_type="run_trigger_recorded", stage="admission", visible_to_user=False,
                          payload={"version": 1, "source": source}))


async def append_private_model_call(conn: Any, *, tenant_id: str, run_id: str, attempt_id: str, evidence: dict) -> None:
    await append_event(conn, tenant_id=tenant_id, run_id=run_id,
        event_id="evt_" + evidence["call_id"] + "_" + evidence["state"],
        event=LedgerEvent(event_type="model_call_observed", stage="model_proxy", visible_to_user=False,
                          payload={**evidence, "attempt_id": attempt_id}))
