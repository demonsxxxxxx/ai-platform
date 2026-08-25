"""Concrete Streaming adapter for transaction-scoped Run v4 events."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.runs.api import CancelRequestAuthority, RunTerminalEventFact
from app.streaming.infrastructure import v4 as _v4
from app.streaming.redis import (
    create_or_get_stream_admission_v4,
    ensure_run_terminal_intent,
    tenant_scope,
)


async def append_current_run_terminal_v4_row(
    conn: object,
    *,
    tenant_id: str,
    run_id: str,
    load_terminal_event_fact: Callable[..., Awaitable[RunTerminalEventFact | None]],
) -> str | None:
    fact = await load_terminal_event_fact(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    if fact is None:
        return None
    intent = await ensure_run_terminal_intent(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        status=fact.status,
    )
    if intent is None:
        return None
    return await _v4.append_run_terminal_v4_row(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        attempt_id=intent.attempt_id,
        status=fact.status,
        terminal_event_id=intent.terminal_event_id,
        error_code=fact.error_code,
        reason_code=(
            "timeout"
            if "timeout" in fact.terminal_reason.lower()
            else "policy_cancelled"
            if "policy" in fact.terminal_reason.lower()
            else "user_cancelled"
        ),
        trace_ref=fact.trace_ref,
    )


class PostgresRunCancellationEventWriter:
    def __init__(
        self,
        *,
        authority_secret: str,
        load_terminal_event_fact: Callable[..., Awaitable[RunTerminalEventFact | None]],
    ) -> None:
        if not isinstance(authority_secret, str) or not authority_secret:
            raise ValueError("v4_authority_secret_invalid")
        self._authority_secret = authority_secret
        self._load_terminal_event_fact = load_terminal_event_fact

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

    async def append_terminal(
        self,
        conn: object,
        *,
        tenant_id: str,
        run_id: str,
    ) -> None:
        await append_current_run_terminal_v4_row(
            conn,
            tenant_id=tenant_id,
            run_id=run_id,
            load_terminal_event_fact=self._load_terminal_event_fact,
        )

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
