"""Concrete Streaming adapter for transaction-scoped Run v4 events."""

from __future__ import annotations

from app.streaming import v4 as _v4


class PostgresRunCancellationEventWriter:
    async def append_cancel_requested(
        self,
        conn: object,
        *,
        tenant_id: str,
        run_id: str,
        source: str,
        trace_ref: str | None,
    ) -> None:
        await _v4.append_run_cancel_requested_v4_row(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            source=source,
            trace_ref=trace_ref,
        )
