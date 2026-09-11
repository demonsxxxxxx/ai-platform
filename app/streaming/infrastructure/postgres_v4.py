"""PostgreSQL adapter for dormant durable v4 publication ownership."""

from __future__ import annotations

import hashlib
import json
import math
import secrets
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.streaming.application.durable_v4 import (
    V4PublicationClaim,
    V4PublicationScope,
)
from app.streaming.application.recovery_v4 import (
    V4ReadySuccessorRebuild,
    V4SuccessorActivation,
    V4SuccessorRebuildClaim,
    V4SuccessorRebuildItem,
    V4SuccessorRebuildReceipt,
)
from app.streaming.domain.protocol_v4 import STREAM_DESIGN_ID, STREAM_PROJECTION_VERSION
from app.streaming.domain.public_events_v4 import (
    build_v4_control,
    project_public_v4,
    project_public_v4_successor,
    successor_stream_open_event_id,
    stream_end_event_id,
    validate_internal_envelope_v4,
)
from app.streaming.domain.transport import canonical_json_bytes
from app.streaming.domain.live import redis_id_tuple, stream_key


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

    async def list_due_scopes(self, *, limit: int) -> tuple[V4PublicationScope, ...]:
        if isinstance(limit, bool) or not 1 <= limit <= 256:
            raise ValueError("v4_publication_due_limit_invalid")
        async with self._transaction_factory() as conn:
            cursor = await conn.execute(
                """
                select event.tenant_id,
                       event.run_id,
                       event.payload_json -> '__stream_v4' ->> 'attempt_id' as attempt_id,
                       (event.payload_json -> '__stream_v4' ->> 'stream_incarnation')::bigint
                         as stream_incarnation
                from run_events as event
                where event.visible_to_user = true
                  and event.payload_json ? '__stream_v4'
                  and event.stream_publication_state = 'pending'
                  and (event.stream_publication_next_attempt_at is null
                       or event.stream_publication_next_attempt_at <= clock_timestamp())
                  and (event.stream_publication_claim_token is null
                       or event.stream_publication_claim_expires_at <= clock_timestamp())
                group by event.tenant_id, event.run_id, attempt_id, stream_incarnation
                order by min(event.sequence), event.tenant_id, event.run_id
                limit %s
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        return tuple(
            V4PublicationScope(
                tenant_id=_row_text(row, "tenant_id"),
                run_id=_row_text(row, "run_id"),
                attempt_id=_row_text(row, "attempt_id"),
                stream_incarnation=_row_int(row, "stream_incarnation"),
            )
            for row in rows
        )

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
            if authority_row.get("state") == "admission_pending":
                return None
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

    async def suppress_expired_terminal_without_attempt(
        self, claim: V4PublicationClaim
    ) -> bool:
        _claim_scope(claim)
        envelope = validate_internal_envelope_v4(
            json.loads(claim.canonical_envelope_bytes.decode("utf-8"))
        )
        event_type = str(envelope["event_type"])
        expected_status = {
            "run.succeeded": "succeeded",
            "run.failed": "failed",
            "run.cancelled": "cancelled",
        }.get(event_type)
        if expected_status is None:
            raise ValueError("v4_expired_terminal_event_invalid")

        async with self._transaction_factory() as conn:
            if not await _lock_claim_authority(conn, claim):
                return False
            fact_cursor = await conn.execute(
                """
                select run.status as run_status, authority.state as authority_state,
                       exists (
                         select 1 from run_attempts as attempt
                         where attempt.tenant_id = run.tenant_id
                           and attempt.run_id = run.id
                           and attempt.id = %s
                       ) as attempt_exists,
                       exists (
                         select 1 from sandbox_leases as lease
                         where lease.tenant_id = run.tenant_id
                           and lease.run_id = run.id
                           and coalesce(
                             lease.attempt_id,
                             lease.lease_payload_json ->> 'attempt_id'
                           ) = %s
                           and lease.status = 'active'
                       ) as active_lease_exists
                from runs as run
                join sse_stream_authorities as authority
                  on authority.tenant_id = run.tenant_id
                 and authority.run_id = run.id
                where run.tenant_id = %s and run.id = %s
                """,
                (
                    claim.attempt_id,
                    claim.attempt_id,
                    claim.tenant_id,
                    claim.run_id,
                ),
            )
            fact = await fact_cursor.fetchone()
            if (
                fact is None
                or fact.get("run_status") != expected_status
                or fact.get("authority_state") != "terminal"
                or fact.get("attempt_exists") is not False
                or fact.get("active_lease_exists") is not False
            ):
                raise V4PublicationAuthorityError(
                    "v4_expired_terminal_authority_unavailable"
                )
            result = await conn.execute(
                """
                update run_events as event
                set stream_publication_state = 'suppressed',
                    stream_publication_attempts = coalesce(event.stream_publication_attempts, 0) + 1,
                    stream_publication_redis_id = null,
                    stream_publication_next_attempt_at = null,
                    stream_publication_last_error = 'terminal_stream_expired',
                    stream_publication_claim_token = null,
                    stream_publication_claim_expires_at = null,
                    payload_json = jsonb_set(
                      jsonb_set(
                        jsonb_set(
                          event.payload_json,
                          '{__stream_v4,publication_state}',
                          to_jsonb('suppressed'::text),
                          true
                        ),
                        '{__stream_v4,publication_attempts}',
                        to_jsonb(coalesce((event.payload_json -> '__stream_v4' ->> 'publication_attempts')::integer, 0) + 1),
                        true
                      ),
                      '{__stream_v4,suppression_reason}',
                      to_jsonb('terminal_stream_expired'::text),
                      true
                    )
                where event.id = %s
                  and event.tenant_id = %s
                  and event.run_id = %s
                  and event.sequence = %s
                  and event.event_type = %s
                  and event.payload_json ->> 'terminal_event_id' = %s
                  and event.visible_to_user = true
                  and event.payload_json ? '__stream_v4'
                  and event.stream_publication_state = 'pending'
                  and event.stream_publication_claim_token = %s
                  and event.stream_publication_claim_expires_at = %s
                  and event.stream_publication_claim_expires_at > clock_timestamp()
                  and event.payload_json -> '__stream_v4' ->> 'attempt_id' = %s
                  and event.payload_json -> '__stream_v4' ->> 'stream_incarnation' = %s
                  and event.payload_json -> '__stream_v4' ->> 'authorization_epoch' = %s
                  and not exists (
                    select 1 from run_attempts as attempt
                    where attempt.tenant_id = event.tenant_id
                      and attempt.run_id = event.run_id
                      and attempt.id = %s
                  )
                  and not exists (
                    select 1 from sandbox_leases as lease
                    where lease.tenant_id = event.tenant_id
                      and lease.run_id = event.run_id
                      and coalesce(
                        lease.attempt_id,
                        lease.lease_payload_json ->> 'attempt_id'
                      ) = %s
                      and lease.status = 'active'
                  )
                returning event.id
                """,
                (
                    claim.event_id,
                    claim.tenant_id,
                    claim.run_id,
                    claim.sequence,
                    event_type,
                    claim.event_id,
                    claim.claim_token,
                    claim.claim_expires_at,
                    claim.attempt_id,
                    str(claim.stream_incarnation),
                    str(claim.authorization_epoch),
                    claim.attempt_id,
                    claim.attempt_id,
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


class PostgresV4SuccessorActivations:
    """Commit a ready successor without opening a Redis or source-event path."""

    def __init__(self, transaction_factory: TransactionFactory) -> None:
        self._transaction_factory = transaction_factory

    async def activate(
        self, ready: V4ReadySuccessorRebuild
    ) -> V4SuccessorActivation | None:
        _ready_activation_scope(ready)
        try:
            successor_open_text = ready.successor_open_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise V4SuccessorRebuildAuthorityError(
                "v4_cutover_successor_open_invalid"
            ) from exc
        claim_token_digest = hashlib.sha256(ready.claim_token.encode("utf-8")).hexdigest()
        async with self._transaction_factory() as conn:
            if not await _lock_run(conn, tenant_id=ready.tenant_id, run_id=ready.run_id):
                raise V4SuccessorRebuildAuthorityError("v4_cutover_run_missing")
            run_cursor = await conn.execute(
                """
                select id, tenant_id, status as run_status
                from runs
                where tenant_id = %s and id = %s
                for update
                """,
                (ready.tenant_id, ready.run_id),
            )
            run_row = await run_cursor.fetchone()
            if run_row is None:
                raise V4SuccessorRebuildAuthorityError("v4_cutover_run_missing")
            run_status = _row_text(run_row, "run_status")
            if run_status not in {"succeeded", "failed", "cancelled"}:
                raise V4SuccessorRebuildAuthorityError("v4_cutover_run_not_terminal")

            attempt_cursor = await conn.execute(
                """
                select id, status as attempt_status, ordinal
                from run_attempts
                where tenant_id = %s and run_id = %s and id = %s
                  and ordinal = (
                    select max(current_attempt.ordinal)
                    from run_attempts as current_attempt
                    where current_attempt.tenant_id = %s
                      and current_attempt.run_id = %s
                  )
                for update
                """,
                (
                    ready.tenant_id,
                    ready.run_id,
                    ready.attempt_id,
                    ready.tenant_id,
                    ready.run_id,
                ),
            )
            attempt_row = await attempt_cursor.fetchone()
            if (
                attempt_row is None
                or _row_text(attempt_row, "id") != ready.attempt_id
                or _row_text(attempt_row, "attempt_status") != run_status
            ):
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_current_attempt_conflict"
                )

            authority_cursor = await conn.execute(
                """
                select tenant_id, tenant_scope, run_id, attempt_id,
                       stream_incarnation, authorization_epoch, design_id,
                       projection_version, state, revocation_state,
                       open_event_id, open_payload_bytes, open_payload_digest
                from sse_stream_authorities
                where tenant_id = %s and run_id = %s
                for update
                """,
                (ready.tenant_id, ready.run_id),
            )
            authority_row = await authority_cursor.fetchone()
            if authority_row is None:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_authority_missing"
                )
            try:
                authority = _stream_authority(authority_row)
            except V4PublicationAuthorityError as exc:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_authority_invalid"
                ) from exc
            if (
                authority.tenant_id != ready.tenant_id
                or authority.tenant_scope != ready.tenant_scope
                or authority.run_id != ready.run_id
                or authority.attempt_id != ready.attempt_id
                or authority.stream_incarnation != ready.source_incarnation
                or authority.authorization_epoch != ready.source_authorization_epoch
                or authority_row.get("state") != "terminal"
            ):
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_source_authority_changed"
                )
            _validated_source_open_digest(authority=authority, row=authority_row)

            cursor_cursor = await conn.execute(
                """
                select next_sequence - 1 as source_cursor_sequence
                from run_event_cursors
                where tenant_id = %s and run_id = %s
                for update
                """,
                (ready.tenant_id, ready.run_id),
            )
            cursor_row = await cursor_cursor.fetchone()
            if (
                cursor_row is None
                or cursor_row.get("source_cursor_sequence")
                != ready.source_cursor_sequence
            ):
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_source_cursor_changed"
                )

            source_rows_cursor = await conn.execute(
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
                """
                ,
                (ready.tenant_id, ready.run_id, ready.source_cursor_sequence),
            )
            source_rows = tuple(await source_rows_cursor.fetchall())
            eligible_rows = _eligible_successor_source_rows(
                source_rows,
                invalid_state_error="v4_cutover_source_fingerprint_changed",
            )
            if len(eligible_rows) != ready.entry_count - 2:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_source_cardinality_changed"
                )
            source_fingerprint = _successor_source_fingerprint(
                authority=authority,
                authority_row=authority_row,
                run_status=run_status,
                source_cursor_sequence=ready.source_cursor_sequence,
                source_through_sequence=ready.source_through_sequence,
                source_open_digest=_validated_source_open_digest(
                    authority=authority,
                    row=authority_row,
                ),
                source_rows_digest=_successor_source_rows_digest(eligible_rows),
                origin_incarnation=ready.origin_incarnation,
                origin_authorization_epoch=ready.origin_authorization_epoch,
            )
            if source_fingerprint != ready.source_authority_fingerprint:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_source_fingerprint_changed"
                )

            rebuild_cursor = await conn.execute(
                """
                select tenant_id, run_id, attempt_id,
                       source_incarnation, source_authorization_epoch,
                       origin_incarnation, origin_authorization_epoch,
                       successor_incarnation, successor_authorization_epoch,
                       source_authority_fingerprint, source_cursor_sequence,
                       source_through_sequence, successor_open_event_id,
                       successor_open_bytes, successor_open_digest, state,
                       claim_token_digest, claim_expires_at, item_count,
                       built_through_sequence, receipt_entry_count,
                       receipt_open_event_id, receipt_terminal_event_id,
                       receipt_end_event_id, receipt_last_redis_id,
                       receipt_last_envelope_bytes, receipt_last_envelope_digest,
                       receipt_digest
                from sse_stream_rebuilds
                where id = %s and tenant_id = %s and run_id = %s
                for update
                """,
                (ready.rebuild_id, ready.tenant_id, ready.run_id),
            )
            rebuild_row = await rebuild_cursor.fetchone()
            if rebuild_row is None:
                raise V4SuccessorRebuildAuthorityError("v4_cutover_rebuild_missing")
            if rebuild_row.get("state") != "ready":
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_rebuild_not_ready"
                )
            if any(
                (
                    rebuild_row.get("attempt_id") != ready.attempt_id,
                    rebuild_row.get("source_incarnation") != ready.source_incarnation,
                    rebuild_row.get("source_authorization_epoch")
                    != ready.source_authorization_epoch,
                    rebuild_row.get("origin_incarnation")
                    != ready.origin_incarnation,
                    rebuild_row.get("origin_authorization_epoch")
                    != ready.origin_authorization_epoch,
                    rebuild_row.get("successor_incarnation")
                    != ready.successor_incarnation,
                    rebuild_row.get("successor_authorization_epoch")
                    != ready.successor_authorization_epoch,
                    rebuild_row.get("source_authority_fingerprint")
                    != ready.source_authority_fingerprint,
                    rebuild_row.get("source_cursor_sequence")
                    != ready.source_cursor_sequence,
                    rebuild_row.get("source_through_sequence")
                    != ready.source_through_sequence,
                    rebuild_row.get("successor_open_event_id")
                    != ready.successor_open_event_id,
                    rebuild_row.get("successor_open_digest")
                    != ready.successor_open_digest,
                    rebuild_row.get("claim_token_digest") != claim_token_digest,
                    rebuild_row.get("claim_expires_at") != ready.claim_expires_at,
                    rebuild_row.get("built_through_sequence")
                    != ready.source_through_sequence,
                    rebuild_row.get("receipt_entry_count") != ready.entry_count,
                    rebuild_row.get("receipt_open_event_id") != ready.open_event_id,
                    rebuild_row.get("receipt_terminal_event_id") != ready.terminal_event_id,
                    rebuild_row.get("receipt_end_event_id") != ready.end_event_id,
                    rebuild_row.get("receipt_last_redis_id") != ready.last_redis_id,
                    rebuild_row.get("receipt_last_envelope_digest") != ready.last_envelope_digest,
                    rebuild_row.get("receipt_digest") != ready.receipt_digest,
                )
            ):
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_ready_claim_changed"
                )
            try:
                persisted_last_bytes = _row_text(
                    rebuild_row, "receipt_last_envelope_bytes"
                ).encode("utf-8")
            except (UnicodeEncodeError, RuntimeError) as exc:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_receipt_last_envelope_invalid"
                ) from exc
            if (
                persisted_last_bytes != ready.last_envelope_bytes
                or hashlib.sha256(persisted_last_bytes).hexdigest()
                != rebuild_row.get("receipt_last_envelope_digest")
                or hashlib.sha256(persisted_last_bytes).hexdigest()
                != ready.last_envelope_digest
            ):
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_receipt_last_envelope_changed"
                )
            try:
                persisted_open = _row_text(
                    rebuild_row, "successor_open_bytes"
                ).encode("utf-8")
            except (UnicodeEncodeError, RuntimeError) as exc:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_successor_open_invalid"
                ) from exc
            if persisted_open != ready.successor_open_bytes:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_successor_open_changed"
                )
            rebuild_items_cursor = await conn.execute(
                """
                select sequence, event_id, event_type,
                       canonical_envelope_bytes, envelope_digest, redis_id
                from sse_stream_rebuild_items
                where rebuild_id = %s
                order by sequence asc
                for update
                """,
                (ready.rebuild_id,),
            )
            rebuild_items = tuple(await rebuild_items_cursor.fetchall())
            if len(rebuild_items) != ready.entry_count - 2:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_rebuild_cardinality_changed"
                )
            expected_incarnation = ready.successor_incarnation
            expected_epoch = ready.successor_authorization_epoch
            origin_authority = _StreamAuthority(
                tenant_id=authority.tenant_id,
                tenant_scope=authority.tenant_scope,
                run_id=authority.run_id,
                attempt_id=authority.attempt_id,
                stream_incarnation=ready.origin_incarnation,
                authorization_epoch=ready.origin_authorization_epoch,
            )
            seen_sequences: set[int] = set()
            seen_event_ids: set[str] = set()
            for item_index, (item, source_row) in enumerate(
                zip(rebuild_items, eligible_rows, strict=True)
            ):
                try:
                    item_bytes = _row_text(item, "canonical_envelope_bytes").encode("utf-8")
                    item_envelope = validate_internal_envelope_v4(
                        json.loads(item_bytes.decode("utf-8"))
                    )
                    expected_envelope = project_public_v4_successor(
                        source_row,
                        source_authority=origin_authority,
                        successor_incarnation=expected_incarnation,
                        successor_authorization_epoch=expected_epoch,
                    )
                    expected_bytes = canonical_json_bytes(expected_envelope)
                except (UnicodeDecodeError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
                    raise V4SuccessorRebuildAuthorityError(
                        "v4_cutover_rebuild_item_invalid"
                    ) from exc
                sequence = item.get("sequence")
                event_id = item.get("event_id")
                if (
                    sequence != source_row.get("sequence")
                    or event_id != source_row.get("id")
                    or item.get("event_type") != source_row.get("event_type")
                    or sequence in seen_sequences
                    or event_id in seen_event_ids
                    or hashlib.sha256(item_bytes).hexdigest() != item.get("envelope_digest")
                    or item_bytes != expected_bytes
                    or item_envelope.get("tenant_scope") != ready.tenant_scope
                    or item_envelope.get("run_id") != ready.run_id
                    or item_envelope.get("attempt_id") != ready.attempt_id
                    or item_envelope.get("stream_incarnation") != expected_incarnation
                    or item.get("redis_id") != ready.item_redis_ids[item_index]
                ):
                    raise V4SuccessorRebuildAuthorityError(
                        "v4_cutover_rebuild_item_changed"
                    )
                seen_sequences.add(sequence)
                seen_event_ids.add(event_id)

            if len(seen_sequences) != len(rebuild_items) or len(seen_event_ids) != len(rebuild_items):
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_rebuild_item_identity_changed"
                )

            clock_cursor = await conn.execute(
                "select clock_timestamp() as current_time"
            )
            clock_row = await clock_cursor.fetchone()
            current_time = clock_row.get("current_time") if clock_row else None
            if (
                not isinstance(current_time, datetime)
                or not isinstance(ready.claim_expires_at, datetime)
                or ready.claim_expires_at <= current_time
            ):
                raise V4SuccessorRebuildAuthorityError("v4_cutover_claim_expired")

            terminal_cursor = await conn.execute(
                """
                select sequence, event_id, event_type, canonical_envelope_bytes
                from sse_stream_rebuild_items
                where rebuild_id = %s
                order by sequence desc
                limit 1
                for update
                """,
                (ready.rebuild_id,),
            )
            terminal_row = await terminal_cursor.fetchone()
            if terminal_row is None or terminal_row.get("event_type") not in {
                "run.succeeded",
                "run.failed",
                "run.cancelled",
            }:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_terminal_receipt_missing"
                )
            try:
                terminal_bytes = _row_text(
                    terminal_row, "canonical_envelope_bytes"
                ).encode("utf-8")
                terminal = validate_internal_envelope_v4(
                    json.loads(terminal_bytes.decode("utf-8"))
                )
            except (UnicodeDecodeError, json.JSONDecodeError, RuntimeError, ValueError) as exc:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_terminal_receipt_invalid"
                ) from exc
            expected_end = canonical_json_bytes(
                build_v4_control(
                    event_id=stream_end_event_id(str(terminal["event_id"])),
                    tenant_scope=ready.tenant_scope,
                    run_id=ready.run_id,
                    attempt_id=ready.attempt_id,
                    stream_incarnation=ready.successor_incarnation,
                    event_type="stream.end",
                    payload={"terminal_event_id": str(terminal["event_id"])},
                    source={
                        "kind": "terminal_intent",
                        "terminal_event_id": str(terminal["event_id"]),
                    },
                    causation_event_id=str(terminal["event_id"]),
                    emitted_at=terminal["emitted_at"],
                )
            )
            if (
                ready.stream_key
                != stream_key(
                    tenant_scope_value=ready.tenant_scope,
                    run_id=ready.run_id,
                    stream_incarnation=ready.successor_incarnation,
                )
                or ready.end_event_id != stream_end_event_id(str(terminal["event_id"]))
                or ready.last_envelope_bytes != expected_end
                or ready.last_redis_id == ""
                or ready.last_redis_id.split("-", 1)[0].isdigit() is False
            ):
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_terminal_receipt_changed"
                )
            redis_id_tuple(ready.last_redis_id)

            for source_row, item_redis_id in zip(
                eligible_rows, ready.item_redis_ids, strict=True
            ):
                if source_row.get("stream_publication_state") == "published":
                    continue
                disposition = await conn.execute(
                    """
                    update run_events as event
                    set stream_publication_state = 'published',
                        stream_publication_attempts =
                          coalesce(event.stream_publication_attempts, 0) + 1,
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
                      and event.tenant_id = %s and event.run_id = %s
                      and event.sequence = %s
                      and event.stream_publication_state = 'pending'
                      and event.payload_json -> '__stream_v4' ->> 'attempt_id' = %s
                      and event.payload_json -> '__stream_v4' ->> 'stream_incarnation' = %s
                      and event.payload_json -> '__stream_v4' ->> 'authorization_epoch' = %s
                    returning event.id
                    """,
                    (
                        item_redis_id,
                        _row_text(source_row, "id"),
                        ready.tenant_id,
                        ready.run_id,
                        _row_int(source_row, "sequence"),
                        ready.attempt_id,
                        str(ready.origin_incarnation),
                        str(ready.origin_authorization_epoch),
                    ),
                )
                if await disposition.fetchone() is None:
                    raise V4SuccessorRebuildAuthorityError(
                        "v4_cutover_source_disposition_changed"
                    )

            cutover = await conn.execute(
                """
                update sse_stream_rebuilds
                set state = 'cutover', updated_at = clock_timestamp()
                where id = %s and tenant_id = %s and run_id = %s
                  and attempt_id = %s and state = 'ready'
                  and claim_token_digest = %s
                  and claim_expires_at = %s
                  and claim_expires_at > clock_timestamp()
                  and source_incarnation = %s
                  and source_authorization_epoch = %s
                  and origin_incarnation = %s
                  and origin_authorization_epoch = %s
                  and successor_incarnation = %s
                  and successor_authorization_epoch = %s
                  and source_authority_fingerprint = %s
                  and source_cursor_sequence = %s
                  and source_through_sequence = %s
                  and successor_open_event_id = %s
                  and successor_open_digest = %s
                  and receipt_entry_count = %s
                  and receipt_open_event_id = %s
                  and receipt_terminal_event_id = %s
                  and receipt_end_event_id = %s
                  and receipt_last_redis_id = %s
                  and receipt_last_envelope_bytes = %s
                  and receipt_last_envelope_digest = %s
                  and receipt_digest = %s
                returning id
                """,
                (
                    ready.rebuild_id,
                    ready.tenant_id,
                    ready.run_id,
                    ready.attempt_id,
                    claim_token_digest,
                    ready.claim_expires_at,
                    ready.source_incarnation,
                    ready.source_authorization_epoch,
                    ready.origin_incarnation,
                    ready.origin_authorization_epoch,
                    ready.successor_incarnation,
                    ready.successor_authorization_epoch,
                    ready.source_authority_fingerprint,
                    ready.source_cursor_sequence,
                    ready.source_through_sequence,
                    ready.successor_open_event_id,
                    ready.successor_open_digest,
                    ready.entry_count,
                    ready.open_event_id,
                    ready.terminal_event_id,
                    ready.end_event_id,
                    ready.last_redis_id,
                    ready.last_envelope_bytes.decode("utf-8"),
                    ready.last_envelope_digest,
                    ready.receipt_digest,
                ),
            )
            if await cutover.fetchone() is None:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_ready_claim_lost"
                )
            await conn.execute(
                """
                update sse_authority_leases
                set closed_at = clock_timestamp(),
                    close_reason = 'stream_cutover',
                    updated_at = clock_timestamp()
                where tenant_id = %s and run_id = %s
                  and authorization_epoch = %s and closed_at is null
                """,
                (ready.tenant_id, ready.run_id, ready.source_authorization_epoch),
            )
            authority_update = await conn.execute(
                """
                update sse_stream_authorities
                set stream_incarnation = %s,
                    authorization_epoch = %s,
                    open_event_id = %s,
                    open_payload_bytes = %s,
                    open_payload_digest = %s,
                    state = 'terminal',
                    revocation_state = 'active',
                    updated_at = clock_timestamp()
                where tenant_id = %s and run_id = %s
                  and attempt_id = %s
                  and stream_incarnation = %s
                  and authorization_epoch = %s
                  and state = 'terminal'
                  and revocation_state = 'active'
                returning tenant_id, run_id
                """,
                (
                    ready.successor_incarnation,
                    ready.successor_authorization_epoch,
                    ready.successor_open_event_id,
                    successor_open_text,
                    ready.successor_open_digest,
                    ready.tenant_id,
                    ready.run_id,
                    ready.attempt_id,
                    ready.source_incarnation,
                    ready.source_authorization_epoch,
                ),
            )
            if await authority_update.fetchone() is None:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_cutover_source_authority_lost"
                )
        return V4SuccessorActivation(
            rebuild_id=ready.rebuild_id,
            tenant_id=ready.tenant_id,
            run_id=ready.run_id,
            attempt_id=ready.attempt_id,
            source_incarnation=ready.source_incarnation,
            source_authorization_epoch=ready.source_authorization_epoch,
            successor_incarnation=ready.successor_incarnation,
            successor_authorization_epoch=ready.successor_authorization_epoch,
            successor_open_event_id=ready.successor_open_event_id,
            end_event_id=ready.end_event_id,
            last_redis_id=ready.last_redis_id,
        )


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
            run_cursor = await conn.execute(
                """
                select id, tenant_id, status as run_status
                from runs
                where tenant_id = %s and id = %s
                for update
                """,
                (tenant_id, run_id),
            )
            run_row = await run_cursor.fetchone()
            if run_row is None:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_terminal_authority_missing"
                )
            attempt_cursor = await conn.execute(
                """
                select status as attempt_status
                from run_attempts as attempt
                where attempt.tenant_id = %s
                  and attempt.run_id = %s
                  and attempt.id = %s
                  and attempt.ordinal = (
                    select max(current_attempt.ordinal)
                    from run_attempts as current_attempt
                    where current_attempt.tenant_id = %s
                      and current_attempt.run_id = %s
                  )
                for update
                """,
                (tenant_id, run_id, attempt_id, tenant_id, run_id),
            )
            attempt_row = await attempt_cursor.fetchone()
            if attempt_row is None:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_terminal_authority_missing"
                )
            run_status = _row_text(run_row, "run_status")
            attempt_status = _row_text(attempt_row, "attempt_status")
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
                       open_event_id, open_payload_bytes, open_payload_digest
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
            source_open_digest = _validated_source_open_digest(
                authority=authority,
                row=authority_row,
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

            lineage_cursor = await conn.execute(
                """
                select attempt_id, successor_authorization_epoch,
                       origin_incarnation, origin_authorization_epoch
                from sse_stream_rebuilds
                where tenant_id = %s and run_id = %s
                  and successor_incarnation = %s and state = 'cutover'
                order by updated_at desc, id desc
                limit 1
                for update
                """,
                (tenant_id, run_id, authority.stream_incarnation),
            )
            lineage = await lineage_cursor.fetchone()
            if lineage is None:
                origin_incarnation = authority.stream_incarnation
                origin_authorization_epoch = authority.authorization_epoch
            else:
                if (
                    lineage.get("attempt_id") != attempt_id
                    or lineage.get("successor_authorization_epoch")
                    != authority.authorization_epoch
                ):
                    raise V4SuccessorRebuildAuthorityError(
                        "v4_rebuild_source_lineage_invalid"
                    )
                origin_incarnation = _row_int(lineage, "origin_incarnation")
                origin_authorization_epoch = _row_int(
                    lineage, "origin_authorization_epoch"
                )
            origin_authority = _StreamAuthority(
                tenant_id=authority.tenant_id,
                tenant_scope=authority.tenant_scope,
                run_id=authority.run_id,
                attempt_id=authority.attempt_id,
                stream_incarnation=origin_incarnation,
                authorization_epoch=origin_authorization_epoch,
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
            eligible_rows = _eligible_successor_source_rows(
                rows,
                invalid_state_error="v4_rebuild_source_publication_state_invalid",
            )
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
            source_rows_digest = _successor_source_rows_digest(eligible_rows)

            source_fingerprint = _successor_source_fingerprint(
                authority=authority,
                authority_row=authority_row,
                run_status=run_status,
                source_cursor_sequence=source_cursor_sequence,
                source_through_sequence=source_through_sequence,
                source_open_digest=source_open_digest,
                source_rows_digest=source_rows_digest,
                origin_incarnation=origin_incarnation,
                origin_authorization_epoch=origin_authorization_epoch,
            )
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
                    source_authority=origin_authority,
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
                  origin_incarnation, origin_authorization_epoch,
                  successor_incarnation, successor_authorization_epoch,
                  source_authority_fingerprint, source_cursor_sequence,
                  source_through_sequence,
                  successor_open_event_id, successor_open_bytes,
                  successor_open_digest, state, claim_token_digest,
                  claim_expires_at, item_count, built_through_sequence
                ) values (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                  %s, %s, %s, %s, %s, %s, 'building', %s, %s, %s, 0
                )
                """,
                (
                    rebuild_id,
                    tenant_id,
                    run_id,
                    attempt_id,
                    source_incarnation,
                    authority.authorization_epoch,
                    origin_incarnation,
                    origin_authorization_epoch,
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
                origin_incarnation=origin_incarnation,
                origin_authorization_epoch=origin_authorization_epoch,
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

    async def mark_ready(
        self,
        claim: V4SuccessorRebuildClaim,
        *,
        receipt: V4SuccessorRebuildReceipt,
    ) -> bool:
        """Mark a complete candidate ready after rechecking all source fences."""

        _successor_claim_scope(claim)
        _validate_successor_receipt(claim, receipt)
        claim_token_digest = hashlib.sha256(
            claim.claim_token.encode("utf-8")
        ).hexdigest()
        async with self._transaction_factory() as conn:
            run_cursor = await conn.execute(
                """
                select id, tenant_id, status as run_status
                from runs
                where tenant_id = %s and id = %s
                for update
                """,
                (claim.tenant_id, claim.run_id),
            )
            run_row = await run_cursor.fetchone()
            if run_row is None:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_readiness_terminal_authority_changed"
                )
            attempt_cursor = await conn.execute(
                """
                select status as attempt_status
                from run_attempts as attempt
                where attempt.tenant_id = %s
                  and attempt.run_id = %s
                  and attempt.id = %s
                  and attempt.ordinal = (
                    select max(current_attempt.ordinal)
                    from run_attempts as current_attempt
                    where current_attempt.tenant_id = %s
                      and current_attempt.run_id = %s
                  )
                for update
                """,
                (
                    claim.tenant_id,
                    claim.run_id,
                    claim.attempt_id,
                    claim.tenant_id,
                    claim.run_id,
                ),
            )
            attempt_row = await attempt_cursor.fetchone()
            if attempt_row is None:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_readiness_terminal_authority_changed"
                )
            run_status = _row_text(run_row, "run_status")
            attempt_status = _row_text(attempt_row, "attempt_status")
            if (
                run_status not in {"succeeded", "failed", "cancelled"}
                or attempt_status != run_status
            ):
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_readiness_run_not_quiesced"
                )

            authority_cursor = await conn.execute(
                """
                select tenant_id, tenant_scope, run_id, attempt_id,
                       stream_incarnation, authorization_epoch, design_id,
                       projection_version, state, revocation_state,
                       open_event_id, open_payload_bytes, open_payload_digest
                from sse_stream_authorities
                where tenant_id = %s and run_id = %s
                for update
                """,
                (claim.tenant_id, claim.run_id),
            )
            authority_row = await authority_cursor.fetchone()
            if authority_row is None:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_readiness_stream_authority_missing"
                )
            try:
                authority = _stream_authority(authority_row)
            except V4PublicationAuthorityError as exc:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_readiness_stream_authority_invalid"
                ) from exc
            if (
                authority.tenant_id != claim.tenant_id
                or authority.tenant_scope != claim.tenant_scope
                or authority.run_id != claim.run_id
                or authority.attempt_id != claim.attempt_id
                or authority.stream_incarnation != claim.source_incarnation
                or authority.authorization_epoch != claim.source_authorization_epoch
                or authority_row.get("state") != "terminal"
            ):
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_readiness_stream_authority_changed"
                )
            source_open_digest = _validated_source_open_digest(
                authority=authority,
                row=authority_row,
            )

            active_cursor = await conn.execute(
                """
                select tenant_id, run_id, attempt_id,
                       source_incarnation, source_authorization_epoch,
                       origin_incarnation, origin_authorization_epoch,
                       successor_incarnation, successor_authorization_epoch,
                       source_authority_fingerprint, source_cursor_sequence,
                       source_through_sequence, successor_open_event_id,
                       successor_open_bytes, successor_open_digest, state, claim_token_digest,
                       claim_expires_at, item_count, built_through_sequence
                from sse_stream_rebuilds
                where id = %s and tenant_id = %s and run_id = %s
                for update
                """,
                (claim.rebuild_id, claim.tenant_id, claim.run_id),
            )
            active = await active_cursor.fetchone()
            if active is None:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_readiness_claim_missing"
                )
            if active.get("state") != "building":
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_readiness_claim_not_building"
                )

            clock_cursor = await conn.execute(
                "select clock_timestamp() as current_time"
            )
            clock_row = await clock_cursor.fetchone()
            current_time = clock_row.get("current_time") if clock_row else None
            if not isinstance(current_time, datetime):
                raise RuntimeError("v4_rebuild_clock_unavailable")
            expiry = active.get("claim_expires_at")
            if not isinstance(expiry, datetime) or expiry <= current_time:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_readiness_claim_expired"
                )
            if expiry != claim.claim_expires_at:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_readiness_claim_changed"
                )
            if active.get("claim_token_digest") != claim_token_digest:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_readiness_token_invalid"
                )
            if not _same_successor_claim_row(active, claim):
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_readiness_claim_changed"
                )
            try:
                persisted_open_bytes = _row_text(
                    active, "successor_open_bytes"
                ).encode("utf-8")
            except (UnicodeEncodeError, RuntimeError) as exc:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_readiness_open_invalid"
                ) from exc
            if persisted_open_bytes != claim.successor_open_bytes:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_readiness_open_changed"
                )

            items_cursor = await conn.execute(
                """
                select sequence, event_id, event_type,
                       canonical_envelope_bytes, envelope_digest
                from sse_stream_rebuild_items
                where rebuild_id = %s
                order by sequence asc
                for update
                """,
                (claim.rebuild_id,),
            )
            persisted_items = tuple(await items_cursor.fetchall())
            if len(persisted_items) != len(claim.items):
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_readiness_items_changed"
                )
            for persisted, expected in zip(persisted_items, claim.items, strict=True):
                try:
                    persisted_bytes = _row_text(
                        persisted, "canonical_envelope_bytes"
                    ).encode("utf-8")
                except (UnicodeEncodeError, RuntimeError) as exc:
                    raise V4SuccessorRebuildAuthorityError(
                        "v4_rebuild_readiness_item_invalid"
                    ) from exc
                if (
                    persisted.get("sequence") != expected.sequence
                    or persisted.get("event_id") != expected.event_id
                    or persisted.get("event_type") != expected.event_type
                    or persisted_bytes != expected.canonical_envelope_bytes
                    or persisted.get("envelope_digest") != expected.envelope_digest
                    or hashlib.sha256(persisted_bytes).hexdigest()
                    != expected.envelope_digest
                ):
                    raise V4SuccessorRebuildAuthorityError(
                        "v4_rebuild_readiness_item_changed"
                    )

            event_cursor = await conn.execute(
                """
                select next_sequence - 1 as source_cursor_sequence
                from run_event_cursors
                where tenant_id = %s and run_id = %s
                for update
                """,
                (claim.tenant_id, claim.run_id),
            )
            event_cursor_row = await event_cursor.fetchone()
            if event_cursor_row is None or event_cursor_row.get("source_cursor_sequence") != claim.source_cursor_sequence:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_readiness_source_cursor_changed"
                )

            source_rows_cursor = await conn.execute(
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
                (
                    claim.tenant_id,
                    claim.run_id,
                    claim.source_cursor_sequence,
                ),
            )
            source_rows = tuple(await source_rows_cursor.fetchall())
            eligible_rows = _eligible_successor_source_rows(
                source_rows,
                invalid_state_error="v4_rebuild_readiness_source_fingerprint_changed",
            )
            source_rows_digest = _successor_source_rows_digest(eligible_rows)

            source_fingerprint = _successor_source_fingerprint(
                authority=authority,
                authority_row=authority_row,
                run_status=run_status,
                source_cursor_sequence=claim.source_cursor_sequence,
                source_through_sequence=claim.source_through_sequence,
                source_open_digest=source_open_digest,
                source_rows_digest=source_rows_digest,
                origin_incarnation=claim.origin_incarnation,
                origin_authorization_epoch=claim.origin_authorization_epoch,
            )
            if source_fingerprint != claim.source_authority_fingerprint:
                raise V4SuccessorRebuildAuthorityError(
                    "v4_rebuild_readiness_source_fingerprint_changed"
                )
            for item, redis_id in zip(
                claim.items, receipt.item_redis_ids, strict=True
            ):
                item_receipt = await conn.execute(
                    """
                    update sse_stream_rebuild_items
                    set redis_id = %s
                    where rebuild_id = %s and sequence = %s
                      and event_id = %s and envelope_digest = %s
                      and redis_id is null
                    returning event_id
                    """,
                    (
                        redis_id,
                        claim.rebuild_id,
                        item.sequence,
                        item.event_id,
                        item.envelope_digest,
                    ),
                )
                if await item_receipt.fetchone() is None:
                    raise V4SuccessorRebuildAuthorityError(
                        "v4_rebuild_readiness_item_receipt_changed"
                    )

            result = await conn.execute(
                """
                update sse_stream_rebuilds
                set state = 'ready',
                    receipt_entry_count = %s,
                    receipt_open_event_id = %s,
                    receipt_terminal_event_id = %s,
                    receipt_end_event_id = %s,
                    receipt_last_redis_id = %s,
                    receipt_last_envelope_bytes = %s,
                    receipt_last_envelope_digest = %s,
                    receipt_digest = %s,
                    built_through_sequence = source_through_sequence,
                    updated_at = clock_timestamp()
                where id = %s
                  and tenant_id = %s and run_id = %s and attempt_id = %s
                  and state = 'building'
                  and claim_token_digest = %s
                  and claim_expires_at = %s
                  and claim_expires_at > clock_timestamp()
                  and source_incarnation = %s
                  and source_authorization_epoch = %s
                  and successor_incarnation = %s
                  and successor_authorization_epoch = %s
                  and source_authority_fingerprint = %s
                  and source_cursor_sequence = %s
                  and source_through_sequence = %s
                  and successor_open_event_id = %s
                  and item_count = %s
                returning id
                """,
                (
                    receipt.entry_count,
                    receipt.open_event_id,
                    receipt.terminal_event_id,
                    receipt.end_event_id,
                    receipt.last_redis_id,
                    receipt.last_envelope_bytes.decode("utf-8"),
                    receipt.last_envelope_digest,
                    receipt.receipt_digest,
                    claim.rebuild_id,
                    claim.tenant_id,
                    claim.run_id,
                    claim.attempt_id,
                    claim_token_digest,
                    claim.claim_expires_at,
                    claim.source_incarnation,
                    claim.source_authorization_epoch,
                    claim.successor_incarnation,
                    claim.successor_authorization_epoch,
                    claim.source_authority_fingerprint,
                    claim.source_cursor_sequence,
                    claim.source_through_sequence,
                    claim.successor_open_event_id,
                    len(claim.items),
                ),
            )
            return await result.fetchone() is not None

def _ready_activation_scope(ready: V4ReadySuccessorRebuild) -> None:
    if not isinstance(ready, V4ReadySuccessorRebuild):
        raise TypeError("v4_ready_rebuild_type_invalid")
    _scope(ready.rebuild_id, ready.tenant_id, ready.run_id, ready.attempt_id)
    _token(ready.claim_token)
    _incarnation(ready.source_incarnation)
    _incarnation(ready.successor_incarnation)
    if ready.successor_incarnation <= ready.source_incarnation:
        raise ValueError("v4_cutover_successor_incarnation_invalid")
    if ready.successor_authorization_epoch <= ready.source_authorization_epoch:
        raise ValueError("v4_cutover_successor_epoch_invalid")


def _successor_claim_scope(claim: V4SuccessorRebuildClaim) -> None:
    if not isinstance(claim, V4SuccessorRebuildClaim):
        raise TypeError("v4_rebuild_claim_type_invalid")
    _scope(claim.rebuild_id, claim.tenant_id, claim.run_id, claim.attempt_id)
    _token(claim.claim_token)
    _incarnation(claim.source_incarnation)
    _incarnation(claim.successor_incarnation)


def _validate_successor_receipt(
    claim: V4SuccessorRebuildClaim,
    receipt: V4SuccessorRebuildReceipt,
) -> None:
    if not isinstance(receipt, V4SuccessorRebuildReceipt):
        raise TypeError("v4_rebuild_receipt_type_invalid")
    terminal_items = tuple(
        item
        for item in claim.items
        if item.event_type in {"run.succeeded", "run.failed", "run.cancelled"}
    )
    if len(terminal_items) != 1 or terminal_items[0] != claim.items[-1]:
        raise ValueError("v4_rebuild_terminal_item_invalid")
    terminal = terminal_items[0]
    expected_key = stream_key(
        tenant_scope_value=claim.tenant_scope,
        run_id=claim.run_id,
        stream_incarnation=claim.successor_incarnation,
    )
    if (
        receipt.stream_key != expected_key
        or receipt.stream_incarnation != claim.successor_incarnation
        or receipt.entry_count != len(claim.items) + 2
        or receipt.open_event_id != claim.successor_open_event_id
        or receipt.terminal_event_id != terminal.event_id
        or receipt.end_event_id != stream_end_event_id(terminal.event_id)
        or receipt.last_envelope_bytes != _successor_end_bytes(claim, terminal)
    ):
        raise ValueError("v4_rebuild_receipt_mismatch")
    redis_id_tuple(receipt.last_redis_id)


def _successor_end_bytes(
    claim: V4SuccessorRebuildClaim,
    terminal: V4SuccessorRebuildItem,
) -> bytes:
    terminal_envelope = validate_internal_envelope_v4(
        json.loads(terminal.canonical_envelope_bytes.decode("utf-8"))
    )
    return canonical_json_bytes(
        build_v4_control(
            event_id=stream_end_event_id(terminal.event_id),
            tenant_scope=claim.tenant_scope,
            run_id=claim.run_id,
            attempt_id=claim.attempt_id,
            stream_incarnation=claim.successor_incarnation,
            event_type="stream.end",
            payload={"terminal_event_id": terminal.event_id},
            source={"kind": "terminal_intent", "terminal_event_id": terminal.event_id},
            causation_event_id=terminal.event_id,
            emitted_at=terminal_envelope["emitted_at"],
        )
    )


def _same_successor_claim_row(
    row: Mapping[str, object], claim: V4SuccessorRebuildClaim
) -> bool:
    return all(
        (
            row.get("tenant_id") == claim.tenant_id,
            row.get("run_id") == claim.run_id,
            row.get("attempt_id") == claim.attempt_id,
            row.get("source_incarnation") == claim.source_incarnation,
            row.get("source_authorization_epoch") == claim.source_authorization_epoch,
            row.get("origin_incarnation") == claim.origin_incarnation,
            row.get("origin_authorization_epoch") == claim.origin_authorization_epoch,
            row.get("successor_incarnation") == claim.successor_incarnation,
            row.get("successor_authorization_epoch") == claim.successor_authorization_epoch,
            row.get("source_authority_fingerprint") == claim.source_authority_fingerprint,
            row.get("source_cursor_sequence") == claim.source_cursor_sequence,
            row.get("source_through_sequence") == claim.source_through_sequence,
            row.get("successor_open_event_id") == claim.successor_open_event_id,
            row.get("successor_open_digest") == claim.successor_open_digest,
            row.get("item_count") == len(claim.items),
            row.get("built_through_sequence") == 0,
        )
    )


def _eligible_successor_source_rows(
    rows: Sequence[Mapping[str, object]], *, invalid_state_error: str
) -> tuple[Mapping[str, object], ...]:
    eligible_rows: list[Mapping[str, object]] = []
    for row in rows:
        state = row.get("stream_publication_state")
        if state == "suppressed":
            continue
        if state not in {"pending", "published"}:
            raise V4SuccessorRebuildAuthorityError(invalid_state_error)
        eligible_rows.append(row)
    return tuple(eligible_rows)


def _successor_source_rows_digest(
    rows: Sequence[Mapping[str, object]],
) -> str:
    canonical_rows = [
        {
            "id": _row_text(row, "id"),
            "tenant_id": _row_text(row, "tenant_id"),
            "run_id": _row_text(row, "run_id"),
            "sequence": _row_int(row, "sequence"),
            "event_type": _row_text(row, "event_type"),
            "visible_to_user": row.get("visible_to_user"),
            "payload_json": row.get("payload_json"),
            "stream_publication_state": row.get("stream_publication_state"),
            "created_at": str(row.get("created_at")),
        }
        for row in rows
    ]
    return hashlib.sha256(canonical_json_bytes(canonical_rows)).hexdigest()


def _successor_source_fingerprint(
    *,
    authority: _StreamAuthority,
    authority_row: Mapping[str, object],
    run_status: str,
    source_cursor_sequence: int,
    source_through_sequence: int,
    source_open_digest: str,
    source_rows_digest: str,
    origin_incarnation: int,
    origin_authorization_epoch: int,
) -> str:
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "tenant_id": authority.tenant_id,
                "tenant_scope": authority.tenant_scope,
                "run_id": authority.run_id,
                "attempt_id": authority.attempt_id,
                "stream_incarnation": authority.stream_incarnation,
                "authorization_epoch": authority.authorization_epoch,
                "origin_incarnation": origin_incarnation,
                "origin_authorization_epoch": origin_authorization_epoch,
                "open_event_id": _row_text(authority_row, "open_event_id"),
                "open_payload_digest": source_open_digest,
                "state": "terminal",
                "run_status": run_status,
                "source_cursor_sequence": source_cursor_sequence,
                "source_through_sequence": source_through_sequence,
                "source_rows_digest": source_rows_digest,
            }
        )
    ).hexdigest()


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


def _validated_source_open_digest(
    *,
    authority: _StreamAuthority,
    row: Mapping[str, object],
) -> str:
    try:
        payload_text = _row_text(row, "open_payload_bytes")
        payload_bytes = payload_text.encode("utf-8")
        payload_digest = _row_text(row, "open_payload_digest")
        opening = validate_internal_envelope_v4(json.loads(payload_text))
    except (TypeError, UnicodeEncodeError, json.JSONDecodeError, ValueError) as exc:
        raise V4SuccessorRebuildAuthorityError(
            "v4_rebuild_source_authority_invalid"
        ) from exc
    open_event_id = _row_text(row, "open_event_id")
    if (
        hashlib.sha256(payload_bytes).hexdigest() != payload_digest
        or canonical_json_bytes(opening) != payload_bytes
        or opening["event_type"] != "stream.open"
        or opening["event_id"] != open_event_id
        or opening["tenant_scope"] != authority.tenant_scope
        or opening["run_id"] != authority.run_id
        or opening["attempt_id"] != authority.attempt_id
        or opening["stream_incarnation"] != authority.stream_incarnation
        or opening["source"]
        != {"kind": "stream_authority", "authority_id": open_event_id}
        or opening["payload"] != {"design_id": STREAM_DESIGN_ID}
    ):
        raise V4SuccessorRebuildAuthorityError(
            "v4_rebuild_source_authority_invalid"
        )
    return payload_digest


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
    "PostgresV4SuccessorActivations",
    "PostgresV4SuccessorRebuilds",
    "V4PublicationAuthorityError",
    "V4SuccessorRebuildAuthorityError",
]
