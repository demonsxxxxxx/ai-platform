"""Explicit, pre-migration retirement of old SSE transport state.

Run from the new backend image with API/Worker producers stopped, before schema
migration. Defaults to a read-only inventory. Business Run/Attempt facts and final
answers are retained; Redis keys expire under their existing TTL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import close_pool, transaction  # noqa: E402

_LEGACY_DESIGNS = (
    "ai-platform.redis-streams-sse-event-channel.v2.1",
    "ai-platform.redis-streams-sse-event-channel.v3",
    "ai-platform.redis-streams-sse-event-channel.v4",
)
_SCOPE = """select tenant_id, run_id from sse_stream_authorities
            where admission_created_at < %s and design_id = any(%s)"""


def cutoff(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--before requires an ISO timestamp with timezone") from exc


async def retire(conn, *, before: datetime, apply: bool) -> dict[str, object]:
    if not apply:
        await conn.execute("set transaction read only")
    await conn.execute("set local lock_timeout = '5s'")
    await conn.execute("set local statement_timeout = '60s'")
    cursor = await conn.execute(
        """select exists (
             select 1 from pg_attribute
             where attrelid = to_regclass(format('%I.run_events', current_schema()))
               and attname = 'stream_publication_state' and not attisdropped
           ) as legacy_schema"""
    )
    if not (await cursor.fetchone())["legacy_schema"]:
        raise RuntimeError("legacy_sse_pre_migration_schema_required")
    if apply:
        # Keep the checked scope and Run statuses stable until retirement commits.
        await conn.execute("lock table runs, sse_stream_authorities in share row exclusive mode")
    params = (before, list(_LEGACY_DESIGNS))
    cursor = await conn.execute(
        f"""with scope as ({_SCOPE})
            select count(*) as streams,
                   count(*) filter (where run.status not in ('succeeded','failed','cancelled')) as active_runs
            from scope join runs as run on run.tenant_id = scope.tenant_id and run.id = scope.run_id""",
        params,
    )
    counts = await cursor.fetchone()
    result = {"before": before.isoformat(), "applied": False, **dict(counts)}
    if not apply:
        return result
    if counts["active_runs"]:
        raise RuntimeError("legacy_sse_active_runs_must_finish_or_be_cancelled_through_runs_authority")
    await conn.execute(
        f"""with scope as ({_SCOPE})
            update sse_authority_leases as lease
            set closed_at = clock_timestamp(), lease_not_after = least(lease_not_after, clock_timestamp()),
                close_reason = 'redis_stream_hard_cutover', updated_at = clock_timestamp()
            from scope where lease.tenant_id = scope.tenant_id and lease.run_id = scope.run_id
              and lease.closed_at is null""", params,
    )
    await conn.execute(
        f"""with scope as ({_SCOPE})
            update sse_terminal_publication_intents as intent
            set state = 'superseded', updated_at = clock_timestamp()
            from scope where intent.tenant_id = scope.tenant_id and intent.run_id = scope.run_id
              and intent.state = 'pending'""", params,
    )
    await conn.execute(
        f"""with scope as ({_SCOPE})
            update sse_stream_rebuilds as rebuild
            set state = 'aborted', failure_code = 'redis_stream_hard_cutover', updated_at = clock_timestamp()
            from scope where rebuild.tenant_id = scope.tenant_id and rebuild.run_id = scope.run_id
              and rebuild.state in ('building','ready')""", params,
    )
    # Preserve canonical bodies, source IDs, sequence, and callback receipts.
    # Previously suppressed rows must never become publicly visible on migration.
    await conn.execute(
        f"""with scope as ({_SCOPE})
            update run_events as event
            set visible_to_user = event.visible_to_user and coalesce((
                    jsonb_typeof(event.payload_json -> '__stream_v4') = 'object'
                    and not ((event.payload_json -> '__stream_v4') ? 'suppression_reason') and (
                    (event.stream_publication_state is null
                     and not ((event.payload_json -> '__stream_v4') ? 'publication_state'))
                    or (event.stream_publication_state in ('pending','published')
                        and event.payload_json -> '__stream_v4' ->> 'publication_state' = event.stream_publication_state))), false),
                stream_publication_state = null,
                stream_publication_attempts = null,
                stream_publication_redis_id = null,
                stream_publication_last_error = null,
                stream_publication_claim_token = null,
                stream_publication_claim_expires_at = null,
                stream_publication_next_attempt_at = null,
                payload_json = case
                    when jsonb_typeof(event.payload_json -> '__stream_v4') = 'object'
                    then jsonb_set(event.payload_json, '{{__stream_v4}}',
                        (event.payload_json -> '__stream_v4') - 'publication_state' - 'publication_attempts' - 'suppression_reason')
                    else event.payload_json end
            from scope where event.tenant_id = scope.tenant_id and event.run_id = scope.run_id
              and (event.payload_json ? '__stream_v4'
                   or event.stream_publication_state is not null
                   or event.stream_publication_attempts is not null
                   or event.stream_publication_redis_id is not null
                   or event.stream_publication_last_error is not null
                   or event.stream_publication_claim_token is not null
                   or event.stream_publication_claim_expires_at is not null
                   or event.stream_publication_next_attempt_at is not null)""", params,
    )
    await conn.execute(
        f"""with scope as ({_SCOPE})
            update sse_stream_authorities as authority
            set revocation_state = 'effective', authorization_epoch = authorization_epoch + 1,
                revocation_committed_at = coalesce(revocation_committed_at, clock_timestamp()),
                revocation_effective_at = clock_timestamp(), updated_at = clock_timestamp()
            from scope where authority.tenant_id = scope.tenant_id and authority.run_id = scope.run_id
              and authority.revocation_state <> 'effective'""", params,
    )
    return {**result, "applied": True}


async def main_async(args) -> int:
    applied: bool | None = False
    try:
        async with transaction() as conn:
            result = await retire(conn, before=args.before, apply=args.apply)
            if args.apply:
                applied = None  # Commit has not yet been acknowledged.
        applied = bool(result["applied"])
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        # Do not expose DSNs, SQL parameters, tenant IDs, or database diagnostics.
        reason = str(exc) if type(exc) is RuntimeError and str(exc).startswith("legacy_sse_") else type(exc).__name__
        if applied is None:
            reason = "legacy_sse_commit_uncertain"
        print(json.dumps({"applied": applied, "error": reason}), file=sys.stderr)
        return 1
    finally:
        try:
            await close_pool()
        except Exception as exc:
            print(json.dumps({"cleanup_error": type(exc).__name__}), file=sys.stderr)
            return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, type=cutoff)
    parser.add_argument("--apply", action="store_true", help="retire the inventoried legacy scope; producers must be stopped")
    raise SystemExit(asyncio.run(main_async(parser.parse_args())))
