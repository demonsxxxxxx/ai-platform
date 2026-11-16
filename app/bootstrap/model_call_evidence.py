"""Bind model proxy observations to the current RunAttempt before private capture."""
import logging

import anyio

from app.db import transaction
from app.runs.domain.model_call_evidence import project_model_call
from app.runs.infrastructure.diagnostics_postgres import PostgresRunDiagnosticsRepository
from app.runs.infrastructure.model_call_evidence_postgres import append_model_call_evidence, get_recorded_model_call_binding

_logger = logging.getLogger(__name__)


async def record_model_call(*, run_id: str, attempt_id: str, evidence: object) -> None:
    safe = project_model_call(evidence)
    if safe is None:
        return
    try:
        with anyio.move_on_after(4, shield=True):
            async with transaction() as conn:
                await conn.execute("set local statement_timeout = '3s'")
                if safe["state"] == "started":
                    binding = await PostgresRunDiagnosticsRepository().get_execution_debug_binding(conn, run_id=run_id, attempt_id=attempt_id)
                else:
                    binding = await get_recorded_model_call_binding(conn, run_id=run_id, attempt_id=attempt_id, call_id=safe["call_id"])
                if binding:
                    await append_model_call_evidence(conn, tenant_id=binding["tenant_id"], run_id=run_id, attempt_id=attempt_id, evidence=safe)
    except Exception:
        _logger.warning("model_call_evidence_capture_unavailable")
