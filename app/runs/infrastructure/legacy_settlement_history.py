"""One-time preservation of pre-Attempt history; no runtime authority or writer."""
from __future__ import annotations

import json
from typing import Any

from app.runs.domain.settlement_retirement import LEGACY_ATTEMPT_SETTLEMENT_COLUMNS


async def preserve_pre_attempt_settlement_history(conn: Any) -> None:
    """Preserve exact retired fields only when no Attempt exists for the Run."""
    intent_present = bool(await (await conn.execute(
        """select 1 from pg_attribute where attrelid=to_regclass('sandbox_leases')
           and attname='executor_reconciliation_intent_context_json' and not attisdropped""",
    )).fetchone())
    after = None
    while True:
        rows = await (await conn.execute(
            f"""select lease.*,run.status as run_status,run.result_json as run_result,
                      lease.executor_terminal_json is null as executor_terminal_sql_null,
                      lease.executor_reconciliation_context_json is null as executor_context_sql_null,
                      {"lease.executor_reconciliation_intent_context_json is null" if intent_present else "true"} as executor_intent_sql_null,
                      run.finished_at as run_finished_at
               from sandbox_leases lease join runs run
                 on run.tenant_id=lease.tenant_id and run.id=lease.run_id
               where not exists(select 1 from run_attempts attempt
                 where attempt.tenant_id=lease.tenant_id and attempt.run_id=lease.run_id)
                 and (%s::text is null or lease.id>%s)
               order by lease.id limit 800""", (after, after),
        )).fetchall()
        for row in rows:
            await _preserve_history_row(conn, row)
        if len(rows) < 800:
            return
        after = rows[-1]["id"]


async def _preserve_history_row(conn: Any, row: dict[str, Any]) -> None:
    payload = row.get("lease_payload_json")
    if not isinstance(payload, dict):
        raise RuntimeError("attempt_settlement_upgrade_pre_attempt_history_invalid")
    labels = payload.get("labels")
    receipt = row.get("executor_terminal_json")
    context = row.get("executor_reconciliation_context_json")
    if any(row.get(field) is None and row.get(flag) is False for field, flag in (
        ("executor_terminal_json", "executor_terminal_sql_null"),
        ("executor_reconciliation_context_json", "executor_context_sql_null"),
        ("executor_reconciliation_intent_context_json", "executor_intent_sql_null"),
    )):
        raise RuntimeError("attempt_settlement_upgrade_pre_attempt_history_invalid")
    finalized = (
        row.get("executor_reconciliation_status") == "finalized"
        and isinstance(receipt, dict) and bool({k: v for k, v in receipt.items() if k != "diagnostics"})
        and receipt.get("run_id") == row["run_id"]
        and receipt.get("status") in {"completed", "succeeded", "failed", "cancelled"}
        and row.get("executor_status") in {"completed", "failed", "cancelled"}
        and isinstance(context, dict)
        and row.get("executor_reconciled_at") is not None
        and row.get("executor_terminal_received_at") is not None
    )
    undispatched = (
        row.get("executor_status") == "pending"
        and row.get("executor_reconciliation_status") == "waiting_terminal"
        and receipt is None and context is None
        and row.get("executor_reconciliation_intent_context_json") is None
        and row.get("executor_terminal_received_at") is None
    )
    if (
        row.get("status") != "released" or row.get("released_at") is None
        or row.get("run_status") not in {"succeeded", "failed", "cancelled", "canceled", "dead-letter", "dead_letter", "dead-lettered"}
        or not isinstance(row.get("run_result"), dict) or row.get("run_finished_at") is None
        or payload.get("source") != "sandbox_runtime"
        or payload.get("evidence_class") != "runtime_lease_projection"
        or payload.get("attempt_id") != row.get("attempt_id")
        or not isinstance(labels, dict)
        or any(labels.get(f"ai-platform.{key}") != row.get(key) for key in ("tenant_id", "run_id", "attempt_id"))
        or not (finalized or undispatched)
        or any(row.get(key) is not None for key in (
            "executor_reconciliation_claim_token", "executor_reconciliation_claimed_at",
            "executor_probe_claim_token", "executor_probe_claimed_at",
            "executor_cleanup_claim_token", "executor_cleanup_claimed_at",
        ))
    ):
        raise RuntimeError("attempt_settlement_upgrade_pre_attempt_history_invalid")
    snapshot = {key: row.get(key) for key in LEGACY_ATTEMPT_SETTLEMENT_COLUMNS}
    snapshot["sql_null_fields"] = [field for field, flag in (
        ("executor_terminal_json", "executor_terminal_sql_null"),
        ("executor_reconciliation_context_json", "executor_context_sql_null"),
        ("executor_reconciliation_intent_context_json", "executor_intent_sql_null"),
    ) if row.get(flag) is True]
    # JSONB normalizes its own values; timestamps use stable ISO strings.
    for key, value in snapshot.items():
        if hasattr(value, "isoformat"):
            snapshot[key] = value.isoformat()
    values = (row["id"], row["tenant_id"], row["run_id"], row.get("attempt_id"),
              json.dumps(snapshot), row["run_status"], json.dumps(row["run_result"]))
    await conn.execute(
        """insert into run_legacy_settlement_history(
             lease_id,tenant_id,run_id,legacy_attempt_id,snapshot_json,run_status,result_json)
           values (%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb)
           on conflict(lease_id) do nothing""", values,
    )
    match = await (await conn.execute(
        """select 1 from run_legacy_settlement_history where lease_id=%s and tenant_id=%s
           and run_id=%s and legacy_attempt_id is not distinct from %s
           and snapshot_json=%s::jsonb and run_status=%s and result_json=%s::jsonb""", values,
    )).fetchone()
    if not match:
        raise RuntimeError("attempt_settlement_upgrade_pre_attempt_history_conflict")
