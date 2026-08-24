"""PostgreSQL adapter for dormant durable v4 publication ownership."""

from __future__ import annotations

import hashlib
import math
import secrets
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.streaming.application.durable_v4 import V4PublicationClaim
from app.streaming.application.recovery_v4 import (
    V4SuccessorRebuildClaim,
    V4SuccessorRebuildItem,
)
from app.streaming.domain.protocol_v4 import STREAM_DESIGN_ID, STREAM_PROJECTION_VERSION
from app.streaming.domain.public_events_v4 import (
    build_v4_control,
    project_public_v4,
    project_public_v4_successor,
    successor_stream_open_event_id,
)
from app.streaming.domain.transport import canonical_json_bytes


TransactionFactory = Callable[[], AbstractAsyncContextManager[Any]]
ClaimTokenFactory = Callable[[], str]
DEFAULT_CLAIM_TTL = timedelta(seconds=30)
DEFAULT_RETRY_DELAY = timedelta(seconds=5)
_V4_METADATA_KEY = "__stream_v4"
_MAX_ERROR_LENGTH = 120


class V4PublicationAuthorityError(ValueError):
    """The requested claim scope is not the current durable stream authority."""


@dataclass(frozen=True, slots=True)
class _StreamAuthority:
    tenant_id: str
    tenant_scope: str
    run_id: str
    attempt_id: str
    stream_incarnation: int
    authorization_epoch: int


class PostgresV4PublicationClaims:
    """Short PostgreSQL transactions for v4 event ownership and disposition."""

    def __init__(
        self,
        transaction_factory: TransactionFactory,
        *,
        claim_token_factory: ClaimTokenFactory | None = None,
    ) -> None:
        self._transaction_factory = transaction_factory
        self._claim_token_factory = claim_token_factory or (lambda: secrets.token_urlsafe(32))

    async def claim_next(
        self,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        stream_incarnation: int,
        claim_ttl: timedelta = DEFAULT_CLAIM_TTL,
    ) -> V4PublicationClaim | None:
        _scope(tenant_id, run_id, attempt_id)
        _incarnation(stream_incarnation)
        ttl_seconds = _positive_seconds(claim_ttl, "v4_publication_claim_ttl_invalid")
        claim_token = self._claim_token_factory()
        _token(claim_token)
        async with self._transaction_factory() as conn:
            if not await _lock_run(conn, tenant_id=tenant_id, run_id=run_id):
                return None
            authority_cursor = await conn.execute(
                """
                select tenant_id, tenant_scope, run_id, attempt_id,
                       stream_incarnation, authorization_epoch, design_id,
                       projection_version, state, revocation_state
                from sse_stream_authorities
                where tenant_id = %s and run_id = %s
                for update
                """,
                (tenant_id, run_id),
            )
            authority_row = await authority_cursor.fetchone()
            if authority_row is None:
                raise V4PublicationAuthorityError("v4_publication_authority_missing")
            authority = _stream_authority(authority_row)
            if (
                authority.attempt_id != attempt_id
                or authority.stream_incarnation != stream_incarnation
            ):
                raise V4PublicationAuthorityError("v4_publication_authority_conflict")
            candidate_cursor = await conn.execute(
                """
                select event.id
                from run_events as event
                where event.tenant_id = %s
                  and event.run_id = %s
                  and event.visible_to_user = true
                  and event.payload_json ? '__stream_v4'
                  and event.stream_publication_state = 'pending'
                  and (event.stream_publication_next_attempt_at is null
                       or event.stream_publication_next_attempt_at <= clock_timestamp())
                  and (event.stream_publication_claim_token is null
                       or event.stream_publication_claim_expires_at <= clock_timestamp())
                  and event.payload_json -> '__stream_v4' ->> 'attempt_id' = %s
                  and event.payload_json -> '__stream_v4' ->> 'stream_incarnation' = %s
                  and event.payload_json -> '__stream_v4' ->> 'authorization_epoch' = %s
                  and not exists (
                    select 1
                    from run_events as predecessor
                    where predecessor.tenant_id = event.tenant_id
                      and predecessor.run_id = event.run_id
                      and predecessor.visible_to_user = true
                      and predecessor.payload_json ? '__stream_v4'
                      and predecessor.stream_publication_state = 'pending'
                      and predecessor.payload_json -> '__stream_v4' ->> 'attempt_id' = %s
                      and predecessor.payload_json -> '__stream_v4' ->> 'stream_incarnation' = %s
                      and predecessor.payload_json -> '__stream_v4' ->> 'authorization_epoch' = %s
                      and predecessor.sequence < event.sequence
                  )
                order by event.sequence asc, event.id asc
                limit 1
                for update of event
                """,
                (
                    tenant_id,
                    run_id,
                    authority.attempt_id,
                    str(authority.stream_incarnation),
                    str(authority.authorization_epoch),
                    authority.attempt_id,
                    str(authority.stream_incarnation),
                    str(authority.authorization_epoch),
                ),
            )
            candidate = await candidate_cursor.fetchone()
            if candidate is None:
                return None
            event_id = _row_text(candidate, "id")
            update_cursor = await conn.execute(
                """
                update run_events as event
                set stream_publication_claim_token = %s,
                    stream_publication_claim_expires_at =
                      clock_timestamp() + (%s * interval '1 second')
                where event.id = %s
                  and event.tenant_id = %s
                  and event.run_id = %s
                  and event.visible_to_user = true
                  and event.payload_json ? '__stream_v4'
                  and event.stream_publication_state = 'pending'
                  and (event.stream_publication_next_attempt_at is null
                       or event.stream_publication_next_attempt_at <= clock_timestamp())
                  and (event.stream_publication_claim_token is null
                       or event.stream_publication_claim_expires_at <= clock_timestamp())
                  and event.payload_json -> '__stream_v4' ->> 'attempt_id' = %s
                  and event.payload_json -> '__stream_v4' ->> 'stream_incarnation' = %s
                  and event.payload_json -> '__stream_v4' ->> 'authorization_epoch' = %s
                  and not exists (
                    select 1
                    from run_events as predecessor
                    where predecessor.tenant_id = event.tenant_id
                      and predecessor.run_id = event.run_id
                      and predecessor.visible_to_user = true
                      and predecessor.payload_json ? '__stream_v4'
                      and predecessor.stream_publication_state = 'pending'
                      and predecessor.payload_json -> '__stream_v4' ->> 'attempt_id' = %s
                      and predecessor.payload_json -> '__stream_v4' ->> 'stream_incarnation' = %s
                      and predecessor.payload_json -> '__stream_v4' ->> 'authorization_epoch' = %s
                      and predecessor.sequence < event.sequence
                  )
                returning event.id, event.tenant_id, event.run_id, event.sequence,
                          event.event_type, event.payload_json, event.visible_to_user,
                          event.created_at, event.stream_publication_state,
                          event.stream_publication_claim_expires_at
                """,
                (
                    claim_token,
                    ttl_seconds,
                    event_id,
                    tenant_id,
                    run_id,
                    authority.attempt_id,
                    str(authority.stream_incarnation),
                    str(authority.authorization_epoch),
                    authority.attempt_id,
                    str(authority.stream_incarnation),
                    str(authority.authorization_epoch),
                ),
            )
            claimed = await update_cursor.fetchone()
            if claimed is None:
                return None
            expiry = claimed.get("stream_publication_claim_expires_at")
            if not isinstance(expiry, datetime):
                raise RuntimeError("v4_publication_claim_expiry_missing")
            envelope = project_public_v4(claimed, authority=authority)
            if envelope is None:
                raise RuntimeError("v4_publication_claim_projection_invalid")
            return V4PublicationClaim(
                event_id=_row_text(claimed, "id"),
                tenant_id=_row_text(claimed, "tenant_id"),
                run_id=_row_text(claimed, "run_id"),
                attempt_id=authority.attempt_id,
                tenant_scope=authority.tenant_scope,
                stream_incarnation=authority.stream_incarnation,
                authorization_epoch=authority.authorization_epoch,
                sequence=_row_int(claimed, "sequence"),
                canonical_envelope_bytes=canonical_json_bytes(envelope),
                claim_token=claim_token,
                claim_expires_at=expiry,
            )

    async def mark_published(self, claim: V4PublicationClaim, *, redis_id: str) -> bool:
        _claim_scope(claim)
        _nonempty(redis_id, "v4_publication_receipt_id")
        async with self._transaction_factory() as conn:
            if not await _lock_claim_authority(conn, claim):
                return False
            result = await conn.execute(
                """
                update run_events as event
                set stream_publication_state = 'published',
                    stream_publication_attempts = coalesce(event.stream_publication_attempts, 0) + 1,
                    stream_publication_redis_id = %s,
                    stream_publication_next_attempt_at = null,
                    stream_publication_last_error = null,
                    stream_publication_claim_token = null,
                    stream_publication_claim_expires_at = null,
                    payload_json = jsonb_set(
                      jsonb_set(
                        event.payload_json,
                        '{__stream_v4,publication_state}',
                        to_jsonb('published'::text),
                        true
                      ),
                      '{__stream_v4,publication_attempts}',
                      to_jsonb(coalesce((event.payload_json -> '__stream_v4' ->> 'publication_attempts')::integer, 0) + 1),
                      true
                    )
                where event.id = %s
                  and event.tenant_id = %s
                  and event.run_id = %s
                  and event.sequence = %s
                  and event.visible_to_user = true
                  and event.payload_json ? '__stream_v4'
                  and event.stream_publication_state = 'pending'
                  and event.stream_publication_claim_token = %s
                  and event.stream_publication_claim_expires_at = %s
                  and event.stream_publication_claim_expires_at > clock_timestamp()
                  and event.payload_json -> '__stream_v4' ->> 'attempt_id' = %s
                  and event.payload_json -> '__stream_v4' ->> 'stream_incarnation' = %s
                  and event.payload_json -> '__stream_v4' ->> 'authorization_epoch' = %s
                returning event.id
                """,
                (
                    redis_id,
                    claim.event_id,
                    claim.tenant_id,
                    claim.run_id,
                    claim.sequence,
                    claim.claim_token,
                    claim.claim_expires_at,
                    claim.attempt_id,
                    str(claim.stream_incarnation),
                    str(claim.authorization_epoch),
                ),
            )
            return await result.fetchone() is not None

    async def schedule_retry(
        self,
        claim: V4PublicationClaim,
        *,
        error: str,
        delay: timedelta = DEFAULT_RETRY_DELAY,
    ) -> bool:
        _claim_scope(claim)
        error = _nonempty(error, "v4_publication_retry_error")[:_MAX_ERROR_LENGTH]
        delay_seconds = _nonnegative_seconds(delay, "v4_publication_retry_delay_invalid")
        async with self._transaction_factory() as conn:
            if not await _lock_claim_authority(conn, claim):
                return False
            result = await conn.execute(
                """
                update run_events as event
                set stream_publication_attempts = coalesce(event.stream_publication_attempts, 0) + 1,
                    stream_publication_next_attempt_at =
                      clock_timestamp() + (%s * interval '1 second'),
                    stream_publication_last_error = %s,
                    stream_publication_claim_token = null,
                    stream_publication_claim_expires_at = null,
                    payload_json = jsonb_set(
                      event.payload_json,
                      '{__stream_v4,publication_attempts}',
                      to_jsonb(coalesce((event.payload_json -> '__stream_v4' ->> 'publication_attempts')::integer, 0) + 1),
                      true
                    )
                where event.id = %s
                  and event.tenant_id = %s
                  and event.run_id = %s
                  and event.sequence = %s
                  and event.visible_to_user = true
                  and event.payload_json ? '__stream_v4'
                  and event.stream_publication_state = 'pending'
                  and event.stream_publication_claim_token = %s
                  and event.stream_publication_claim_expires_at = %s
                  and event.stream_publication_claim_expires_at > clock_timestamp()
                  and event.payload_json -> '__stream_v4' ->> 'attempt_id' = %s
                  and event.payload_json -> '__stream_v4' ->> 'stream_incarnation' = %s
                  and event.payload_json -> '__stream_v4' ->> 'authorization_epoch' = %s
                returning event.id
                """,
                (
                    delay_seconds,
                    error,
                    claim.event_id,
                    claim.tenant_id,
                    claim.run_id,
                    claim.sequence,
                    claim.claim_token,
                    claim.claim_expires_at,
                    claim.attempt_id,
                    str(claim.stream_incarnation),
                    str(claim.authorization_epoch),
                ),
            )
            return await result.fetchone() is not None

    async def release(self, claim: V4PublicationClaim) -> bool:
        _claim_scope(claim)
        async with self._transaction_factory() as conn:
            if not await _lock_claim_authority(conn, claim):
                return False
            result = await conn.execute(
                """
                update run_events as event
                set stream_publication_claim_token = null,
                    stream_publication_claim_expires_at = null
                where event.id = %s
                  and event.tenant_id = %s
                  and event.run_id = %s
                  and event.sequence = %s
                  and event.visible_to_user = true
                  and event.payload_json ? '__stream_v4'
                  and event.stream_publication_state = 'pending'
                  and event.stream_publication_claim_token = %s
                  and event.stream_publication_claim_expires_at = %s
                  and event.stream_publication_claim_expires_at > clock_timestamp()
                  and event.payload_json -> '__stream_v4' ->> 'attempt_id' = %s
                  and event.payload_json -> '__stream_v4' ->> 'stream_incarnation' = %s
                  and event.payload_json -> '__stream_v4' ->> 'authorization_epoch' = %s
                returning event.id
                """,
                (
                    claim.event_id,
                    claim.tenant_id,
                    claim.run_id,
                    claim.sequence,
                    claim.claim_token,
                    claim.claim_expires_at,
                    claim.attempt_id,
                    str(claim.stream_incarnation),
                    str(claim.authorization_epoch),
                ),
            )
            return await result.fetchone() is not None


class V4SuccessorRebuildAuthorityError(ValueError):
    """The requested successor snapshot is not a quiesced current authority."""


class PostgresV4SuccessorRebuilds:
    """Prepare one terminal successor snapshot in a short PostgreSQL transaction."""

    def __init__(
        self,
        transaction_factory: TransactionFactory,
        *,
        claim_token_factory: ClaimTokenFactory | None = None,
    ) -> None:
        self._transaction_factory = transaction_factory
        self._claim_token_factory = claim_token_factory or (
            lambda: secrets.token_urlsafe(32)
        )

    async def prepare(
        self,
        *,
        tenant_id: str,
        run_id: str,
        attempt_id: str,
        source_incarnation: int,
        claim_ttl: timedelta = DEFAULT_CLAIM_TTL,
    ) -> V4SuccessorRebuildClaim | None:
        _scope(tenant_id, run_id, attempt_id)
        _incarnation(source_incarnation)
        _positive_seconds(claim_ttl, "v4_rebuild_claim_ttl_invalid")
        claim_token = self._claim_token_factory()
        _token(claim_token)
        claim_token_digest = hashlib.sha256(claim_token.encode("utf-8")).hexdigest()

        async with self._transaction_factory() as conn:
            terminal_cursor = await conn.execute(
                """
                select run_record.status as run_status,
                       attempt.status as attempt_status
                from runs as run_record
                join run_attempts as attempt
                  on attempt.tenant_id = run_record.tenant_id
                 and attempt.run_id = run_record.id
                 and attempt.id = %s
                where run_record.tenant_id = %s and run_record.id = %s
                for update of run_record, attempt
                """,
                (attempt_id, tenant_id, run_id),
            )
            terminal = await terminal_cursor.fetchone()
            if terminal is None:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_terminal_authority_missing"
                )
            run_status = _row_text(terminal, "run_status")
            attempt_status = _row_text(terminal, "attempt_status")
            if (
                run_status not in {"succeeded", "failed", "cancelled"}
                or attempt_status != run_status
            ):
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_run_not_quiesced"
                )

            authority_cursor = await conn.execute(
                """
                select tenant_id, tenant_scope, run_id, attempt_id,
                       stream_incarnation, authorization_epoch, design_id,
                       projection_version, state, revocation_state,
                       open_event_id, open_payload_digest
                from sse_stream_authorities
                where tenant_id = %s and run_id = %s
                for update
                """,
                (tenant_id, run_id),
            )
            authority_row = await authority_cursor.fetchone()
            if authority_row is None:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_stream_authority_missing"
                )
            authority = _stream_authority(authority_row)
            if (
                authority.attempt_id != attempt_id
                or authority.stream_incarnation != source_incarnation
                or authority_row.get("state") != "terminal"
            ):
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_stream_authority_conflict"
                )

            clock_cursor = await conn.execute(
                "select clock_timestamp() as current_time"
            )
            clock_row = await clock_cursor.fetchone()
            current_time = clock_row.get("current_time") if clock_row else None
            if not isinstance(current_time, datetime):
                raise RuntimeError("v4_rebuild_clock_unavailable")

            active_cursor = await conn.execute(
                """
                select id, claim_expires_at
                from sse_stream_rebuilds
                where tenant_id = %s and run_id = %s
                  and state in ('building', 'ready')
                for update
                """,
                (tenant_id, run_id),
            )
            active = await active_cursor.fetchone()
            if active is not None:
                expires_at = active.get("claim_expires_at")
                if not isinstance(expires_at, datetime):
                    raise RuntimeError("v4_rebuild_expiry_unavailable")
                if expires_at > current_time:
                    return None
                await conn.execute(
                    """
                    update sse_stream_rebuilds
                    set state = 'expired', updated_at = clock_timestamp()
                    where id = %s and state in ('building', 'ready')
                      and claim_expires_at <= clock_timestamp()
                    """,
                    (_row_text(active, "id"),),
                )

            incarnation_cursor = await conn.execute(
                """
                select greatest(
                  %s::bigint,
                  coalesce(max(successor_incarnation), %s::bigint)
                ) + 1 as successor_incarnation
                from sse_stream_rebuilds
                where tenant_id = %s and run_id = %s
                """,
                (
                    authority.stream_incarnation,
                    authority.stream_incarnation,
                    tenant_id,
                    run_id,
                ),
            )
            incarnation_row = await incarnation_cursor.fetchone()
            successor_incarnation = _row_int(
                incarnation_row or {}, "successor_incarnation"
            )
            successor_epoch = authority.authorization_epoch + 1

            event_cursor = await conn.execute(
                """
                select next_sequence - 1 as source_through_sequence
                from run_event_cursors
                where tenant_id = %s and run_id = %s
                for update
                """,
                (tenant_id, run_id),
            )
            event_cursor_row = await event_cursor.fetchone()
            source_cursor_sequence = _row_int(
                event_cursor_row or {}, "source_through_sequence"
            )
            rows_cursor = await conn.execute(
                """
                select id, tenant_id, run_id, sequence, event_type,
                       visible_to_user, payload_json,
                       stream_publication_state, created_at
                from run_events
                where tenant_id = %s and run_id = %s
                  and sequence <= %s
                  and visible_to_user = true
                  and payload_json ? '__stream_v4'
                order by sequence asc, id asc
                for update
                """,
                (tenant_id, run_id, source_cursor_sequence),
            )
            rows = tuple(await rows_cursor.fetchall())
            eligible_rows: list[Mapping[str, object]] = []
            for row in rows:
                state = row.get("stream_publication_state")
                if state == "suppressed":
                    continue
                if state not in {"pending", "published"}:
                    raise V4SuccessorRebuildAuthorityError(
                        "v4_rebuild_source_publication_state_invalid"
                    )
                eligible_rows.append(row)
            if not eligible_rows:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_source_events_missing"
                )
            expected_terminal = f"run.{run_status}"
            terminal_positions = [
                index
                for index, row in enumerate(eligible_rows)
                if row.get("event_type")
                in {"run.succeeded", "run.failed", "run.cancelled"}
            ]
            if terminal_positions != [len(eligible_rows) - 1] or (
                eligible_rows[-1].get("event_type") != expected_terminal
            ):
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_terminal_barrier_invalid"
                )
            terminal_sequence = _row_int(eligible_rows[-1], "sequence")
            source_through_sequence = terminal_sequence

            source_fingerprint = hashlib.sha256(
                canonical_json_bytes(
                    {
                        "tenant_id": authority.tenant_id,
                        "tenant_scope": authority.tenant_scope,
                        "run_id": authority.run_id,
                        "attempt_id": authority.attempt_id,
                        "stream_incarnation": authority.stream_incarnation,
                        "authorization_epoch": authority.authorization_epoch,
                        "open_event_id": _row_text(authority_row, "open_event_id"),
                        "open_payload_digest": _row_text(
                            authority_row, "open_payload_digest"
                        ),
                        "state": "terminal",
                        "run_status": run_status,
                        "source_cursor_sequence": source_cursor_sequence,
                        "source_through_sequence": source_through_sequence,
                    }
                )
            ).hexdigest()
            open_event_id = successor_stream_open_event_id(
                tenant_scope=authority.tenant_scope,
                run_id=run_id,
                attempt_id=attempt_id,
                stream_incarnation=successor_incarnation,
            )
            opening = build_v4_control(
                event_id=open_event_id,
                tenant_scope=authority.tenant_scope,
                run_id=run_id,
                attempt_id=attempt_id,
                stream_incarnation=successor_incarnation,
                event_type="stream.open",
                payload={"design_id": STREAM_DESIGN_ID},
                source={
                    "kind": "stream_authority",
                    "authority_id": open_event_id,
                },
                emitted_at=current_time,
            )
            open_bytes = canonical_json_bytes(opening)
            open_digest = hashlib.sha256(open_bytes).hexdigest()
            items: list[V4SuccessorRebuildItem] = []
            for row in eligible_rows:
                envelope = project_public_v4_successor(
                    row,
                    source_authority=authority,
                    successor_incarnation=successor_incarnation,
                    successor_authorization_epoch=successor_epoch,
                )
                envelope_bytes = canonical_json_bytes(envelope)
                items.append(
                    V4SuccessorRebuildItem(
                        event_id=_row_text(row, "id"),
                        sequence=_row_int(row, "sequence"),
                        event_type=_row_text(row, "event_type"),
                        canonical_envelope_bytes=envelope_bytes,
                        envelope_digest=hashlib.sha256(envelope_bytes).hexdigest(),
                    )
                )

            rebuild_id = "srb_" + hashlib.sha256(
                canonical_json_bytes(
                    [
                        tenant_id,
                        run_id,
                        attempt_id,
                        source_incarnation,
                        successor_incarnation,
                        claim_token_digest,
                    ]
                )
            ).hexdigest()
            claim_expires_at = current_time + claim_ttl
            await conn.execute(
                """
                insert into sse_stream_rebuilds(
                  id, tenant_id, run_id, attempt_id,
                  source_incarnation, source_authorization_epoch,
                  successor_incarnation, successor_authorization_epoch,
                  source_authority_fingerprint, source_cursor_sequence,
                  source_through_sequence,
                  successor_open_event_id, successor_open_bytes,
                  successor_open_digest, state, claim_token_digest,
                  claim_expires_at, item_count, built_through_sequence
                ) values (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, 'building', %s, %s, %s, 0
                )
                """,
                (
                    rebuild_id,
                    tenant_id,
                    run_id,
                    attempt_id,
                    source_incarnation,
                    authority.authorization_epoch,
                    successor_incarnation,
                    successor_epoch,
                    source_fingerprint,
                    source_cursor_sequence,
                    source_through_sequence,
                    open_event_id,
                    open_bytes.decode("utf-8"),
                    open_digest,
                    claim_token_digest,
                    claim_expires_at,
                    len(items),
                ),
            )
            for item in items:
                await conn.execute(
                    """
                    insert into sse_stream_rebuild_items(
                      rebuild_id, sequence, event_id, event_type,
                      canonical_envelope_bytes, envelope_digest
                    ) values (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        rebuild_id,
                        item.sequence,
                        item.event_id,
                        item.event_type,
                        item.canonical_envelope_bytes.decode("utf-8"),
                        item.envelope_digest,
                    ),
                )
            return V4SuccessorRebuildClaim(
                rebuild_id=rebuild_id,
                tenant_id=tenant_id,
                run_id=run_id,
                attempt_id=attempt_id,
                tenant_scope=authority.tenant_scope,
                source_incarnation=authority.stream_incarnation,
                source_authorization_epoch=authority.authorization_epoch,
                successor_incarnation=successor_incarnation,
                successor_authorization_epoch=successor_epoch,
                source_authority_fingerprint=source_fingerprint,
                source_cursor_sequence=source_cursor_sequence,
                source_through_sequence=source_through_sequence,
                successor_open_event_id=open_event_id,
                successor_open_bytes=open_bytes,
                successor_open_digest=open_digest,
                items=tuple(items),
                claim_token=claim_token,
                claim_expires_at=claim_expires_at,
            )


async def _lock_run(conn: Any, *, tenant_id: str, run_id: str) -> bool:
    cursor = await conn.execute(
        """
        select id, tenant_id
        from runs
        where tenant_id = %s and id = %s
        for update
        """,
        (tenant_id, run_id),
    )
    return await cursor.fetchone() is not None


async def _lock_claim_authority(conn: Any, claim: V4PublicationClaim) -> bool:
    if not await _lock_run(conn, tenant_id=claim.tenant_id, run_id=claim.run_id):
        return False
    cursor = await conn.execute(
        """
        select tenant_id, tenant_scope, run_id, attempt_id,
               stream_incarnation, authorization_epoch, design_id,
               projection_version, state, revocation_state
        from sse_stream_authorities
        where tenant_id = %s and run_id = %s
        for update
        """,
        (claim.tenant_id, claim.run_id),
    )
    row = await cursor.fetchone()
    if row is None:
        return False
    authority = _stream_authority(row)
    return (
        authority.tenant_id == claim.tenant_id
        and authority.tenant_scope == claim.tenant_scope
        and authority.run_id == claim.run_id
        and authority.attempt_id == claim.attempt_id
        and authority.stream_incarnation == claim.stream_incarnation
        and authority.authorization_epoch == claim.authorization_epoch
    )


def _stream_authority(row: Mapping[str, object]) -> _StreamAuthority:
    if (
        row.get("design_id") != STREAM_DESIGN_ID
        or row.get("projection_version") != STREAM_PROJECTION_VERSION
        or row.get("state") not in {"confirmed", "degraded", "terminal"}
        or row.get("revocation_state") != "active"
    ):
        raise V4PublicationAuthorityError("v4_publication_authority_unavailable")
    return _StreamAuthority(
        tenant_id=_row_text(row, "tenant_id"),
        tenant_scope=_row_text(row, "tenant_scope"),
        run_id=_row_text(row, "run_id"),
        attempt_id=_row_text(row, "attempt_id"),
        stream_incarnation=_row_int(row, "stream_incarnation"),
        authorization_epoch=_row_int(row, "authorization_epoch"),
    )


def _scope(*values: str) -> None:
    for value in values:
        _nonempty(value, "v4_publication_scope")


def _claim_scope(claim: V4PublicationClaim) -> None:
    if not isinstance(claim, V4PublicationClaim):
        raise TypeError("v4_publication_claim_type_invalid")
    _scope(claim.event_id, claim.tenant_id, claim.run_id, claim.attempt_id, claim.claim_token)
    _incarnation(claim.stream_incarnation)
    if claim.sequence < 1:
        raise ValueError("v4_publication_claim_sequence_invalid")


def _incarnation(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("v4_publication_claim_incarnation_invalid")


def _token(value: str) -> None:
    _nonempty(value, "v4_publication_claim_token")


def _nonempty(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name}_invalid")
    return value


def _positive_seconds(value: timedelta, error: str) -> float:
    if not isinstance(value, timedelta):
        raise ValueError(error)
    seconds = value.total_seconds()
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(error)
    return seconds


def _nonnegative_seconds(value: timedelta, error: str) -> float:
    if not isinstance(value, timedelta):
        raise ValueError(error)
    seconds = value.total_seconds()
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(error)
    return seconds


def _row_text(row: Mapping[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"v4_publication_row_{key}_invalid")
    return value


def _row_int(row: Mapping[str, object], key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuntimeError(f"v4_publication_row_{key}_invalid")
    return value


__all__ = [
    "DEFAULT_CLAIM_TTL",
    "DEFAULT_RETRY_DELAY",
    "PostgresV4PublicationClaims",
    "PostgresV4SuccessorRebuilds",
    "V4PublicationAuthorityError",
    "V4SuccessorRebuildAuthorityError",
]
