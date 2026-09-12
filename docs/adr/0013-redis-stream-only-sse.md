# ADR 0013: Redis Stream-only SSE v4

Status: accepted architecture; candidate implementation, release and External Acceptance require separate evidence

Date: 2026-09-11

Design ID: `ai-platform.redis-streams-sse-event-channel.v4`

Supersedes the publication and recovery architecture in [ADR 0012](0012-recoverable-agent-kernel-event-stream-v4.md) and the live fan-out architecture in [ADR 0009](0009-redis-streams-sse-v3-live-fanout.md). The v4 public event set and existing business authorities remain in force.

## Decision

The production path is:

```text
Worker / Agent -> platform public projection -> Redis Stream -> API SSE
                                                        -> existing frontend adapter / reducer
```

Redis Stream is the sole live and replay transport. The API replays retained entries and continues from the same cursor with bounded `XREAD BLOCK`. There is no Pub/Sub fan-out, subscriber hub, PostgreSQL publication queue, publication claim worker, independent terminal intent, or successor rebuild.

PostgreSQL retains Run/Attempt state, authorization, cancellation, immutable callback facts and receipts, final answers, and audit facts. Those records are not a browser-event delivery queue. Authorized final hydration and historical reads remain available independently of Redis retention.

The authenticated callback route commits its complete public projection and callback receipt together, then appends the batch directly to Redis before acknowledging the callback. The existing executor buffer sends batches serially and retries the same immutable callback after an uncertain response. Redis owns the append receipt. This does not introduce another retry scheduler.

Committed Worker and Run events use the existing business lifecycle entry points. Run terminal publication derives deterministic terminal/end identities from the committed Run fact. A transport failure cannot roll back that Run or suppress its final answer. A later invocation can reuse the Redis receipt, but no background publication scan promises eventual SSE delivery.

Cancellation and terminal events check the existing callback receipt against their strictly preceding callback sequence. Conversely, a callback directly publishes any preceding committed Run event before appending its own batch. The bounded predecessor lookup and separate Redis receipts preserve order and exact retry without another publisher, claim or scheduling state.

A missing or trimmed Stream produces a gap and authorized hydration. It is never rebuilt, and no successor authority is created. `Last-Event-ID` remains bound to the Run and incarnation accepted by the browser reducer.

## Change contract

- **Owners:** Streaming owns transport, projection, cursors, and SSE delivery. Runs owns terminalization and its transaction ordering. The executor adapter owns SDK translation. The existing frontend adapter/reducer owns browser acceptance and hydration.
- **Scope:** production publishers/readers, lifecycle composition, public protocol artifacts, schema retirement, selectors, tests, and owning operations documents change together.
- **Preserved boundaries:** tenant/workspace/user/Run/Attempt identity; current execution and queue ownership; authorization epochs and bounded connection leases; private-data redaction; callback ordering and deduplication; cancellation/resource cleanup; immutable final answers and historical business facts.
- **Acceptance:** real Redis/PostgreSQL checks cover admission, committed callback delivery and retry, terminal publication and failure, missing-stream handling, receipts, replay, and migration. Frontend checks cover cursor acceptance, gap/reconnect, cancellation, and terminal hydration. Independent review inspects the complete candidate and its retirement inventory.
- **Stop conditions:** a second publication control plane, Redis I/O under a PostgreSQL transaction, unauthorized public output, automatic reconstruction, or a migration that can erase unretired state blocks completion.

## Retirement and compatibility

The release is a hard cut. Old producers stop before an explicit inventory/apply operation retires v2.1/v3/v4 transport authorities. Suppression is materialized into `visible_to_user = false` before publication metadata is removed. Run/Attempt records, callback receipts, final answers, and audit facts remain unchanged.

Schema version `2026.09.11.1` refuses unretired state before dropping publication columns/indexes and terminal/rebuild relations. Earlier backend binaries are not compatible after this migration. The package deployment entry already stops admission after a migration/startup failure without guessing at image or database rollback. Recovery uses a compatible package or the separately authorized backup procedure.

The physical Redis key prefix and the separate `callback-receipt-v2.1` protocol do not enable old SSE wire versions. Historical message/ledger decoders remain only for stored business records and authorized hydration; they cannot become live producers or PostgreSQL browser polling. Old Redis keys expire under their existing TTL and are not rewritten as new authority.

## Owning contracts

- [Wire protocol](../architecture/redis-streams-sse-wire-protocol.md)
- [Execution control](../architecture/redis-streams-sse-execution-control.md)
- [Cutover operations and retirement inventory](../operations/redis-streams-sse-cutover-acceptance.md)
- [Release operations](../operations/release-operations-runbook.md)
