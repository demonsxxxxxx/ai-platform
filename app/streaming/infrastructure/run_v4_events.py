"""Concrete Streaming adapter for transaction-scoped Run v4 events."""

from __future__ import annotations

from app.runs.application.cancellation import CancelRequestAuthority
from app.streaming import v4 as _v4
from app.streaming.redis import create_or_get_stream_admission_v4, tenant_scope


class PostgresRunCancellationEventWriter:
    def __init__(self, *, authority_secret: str) -> None:
        if not isinstance(authority_secret, str) or not authority_secret:
            raise ValueError("v4_authority_secret_invalid")
        self._authority_secret = authority_secret

    async def prepare_pending_authority(
        self,
        conn: object,
        *,
        tenant_id: str,
        authority: CancelRequestAuthority,
    ) -> None:
        scope = tenant_scope(tenant_id, secret=self._authority_secret)
        created = await create_or_get_stream_admission_v4(
            conn,
            tenant_id=tenant_id,
            run_id=authority.run_id,
            attempt_id=authority.attempt_id,
            tenant_scope=scope,
        )
        if created.attempt_id != authority.attempt_id:
            raise RuntimeError("v4_cancellation_attempt_fenced")

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
