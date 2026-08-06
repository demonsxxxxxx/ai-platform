---
status: proposed
---

# Use Redis Streams for bounded SSE replay and PostgreSQL for durable final facts

AI Platform will use one current Redis Stream per run, fenced by a durable stream
incarnation, as the bounded live/replay channel for Agent SSE and will keep
PostgreSQL as the durable authority for run/session
state, final answers, tool/approval/artifact facts, and necessary audit or
semantic facts. This removes per-text-delta PostgreSQL writes while retaining
explicit reconnect gaps and durable final-state reconciliation.

The accepted flow is `Claude Agent SDK -> typed event normalizer -> bounded
in-memory coalescer -> Redis Stream XADD -> FastAPI SSE XREAD -> idempotent
frontend reducer`. Multiple browsers use independent `XREAD` calls, not consumer
groups. Production Redis unavailability fails closed and never selects an
in-process fallback.

## Context

At baseline `839f851bc0954d1d97910c07489fc750bdb01b2b`, each safe assistant
delta enters a worker transaction and is appended to PostgreSQL. The public SSE
route then polls run, event, and artifact state about once per second. Existing
PostgreSQL cursor, batch receipt, and terminal-fence primitives are valuable for
legacy and semantic facts, but using the durable database as the live text bus
creates avoidable writes, reads, latency, and multi-API polling load.

The product requires bounded replay rather than permanent delta history. A user
must be told when a reconnect cursor falls outside retention, and the final
answer must remain correct even if Redis or an API process disappears.

## Decision

- Native Redis Stream IDs are ordering authority only inside one proven stream
  incarnation. Public SSE IDs bind all three identities as
  `<run_id>:<stream_incarnation>:<ms>-<seq>`.
- PostgreSQL durably allocates a positive, monotonically increasing
  `stream_incarnation` with the Redis backend/design pin. It is embedded in the
  Redis key and every envelope. Missing-key rebuild, unprovable Redis continuity,
  and reconciler recovery must increment it before publication; an old cursor is
  never compared with overlapping native IDs from a new incarnation.
- Redis holds only bounded, typed, public-safe projections. It is not a
  transcript or business authority.
- PostgreSQL stores the immutable per-run backend pin, final answer and status,
  tool/approval/artifact and required semantic facts, plus an idempotent terminal
  publication intent pinned to one stream incarnation and stable terminal/end
  semantic event IDs. Redis-pinned runs do not persist text deltas to PG.
- The terminal invariant is `flush pending -> persist final facts -> commit PG
  -> XADD terminal -> XADD end`.
- Trim, expiry, restart, missing keys, or an older/unprovable incarnation produce
  an explicit replay gap and authorized durable-state reload. Malformed, foreign,
  future-incarnation, and future-ID cursors fail closed as invalid requests; they
  are not converted into gaps. A partial live fold is never presented as a
  complete final answer.
- Typed normalization, tenant/run authorization, safe public projection, secret
  filtering, and size bounds happen before every `XADD`.
- The frontend stores a cursor only after its reducer accepts the event and
  deduplicates stable semantic event IDs across uncertain publication retries.
- Blocking readers have separate connection capacity from publishers and other
  Redis functions. Slow consumers reconnect or gap; they do not create
  unbounded server buffers.
- An SSE send-authorization lease cannot outlive its `XREAD BLOCK` interval
  (initially 15,000 ms). Revocation cancels a blocked read; before every payload
  frame the adapter obtains a current authority decision or uses a lease whose
  revocation is synchronously invalidated across API instances. Heartbeat cannot
  renew stale authority; post-revocation payload count is zero and close latency
  is at most 15 seconds.

The full contract, diagrams, capacity formulas, migration modes, failure matrix,
and A-F dispatch gates live in
[`../architecture/redis-streams-sse-event-channel.md`](../architecture/redis-streams-sse-event-channel.md).

## Considered Options

### Keep every text delta in PostgreSQL

This provides durable replay but preserves the observed write amplification and
polling/query load. Text transport is not a permanent product fact, and the
durable final answer already provides the required convergence point.

### PostgreSQL plus LISTEN/NOTIFY

Notifications can reduce polling but do not remove per-delta writes or become a
replay store. Missed notifications still require database catch-up. This is a
valid design when lossless durable deltas are required, but that is not the
selected product contract.

### Redis Pub/Sub

Pub/Sub has low latency but no retained cursor or reconnect replay. Adding a
separate replay store would recreate a two-plane ordering problem.

### Process-local memory bridge

It is simple for one process but fails multi-API delivery, restart recovery, and
production fail-closed behavior. It remains only the bounded coalescing layer,
never a fallback channel.

### Run plus native Redis ID without stream incarnation

`<run_id>:<ms>-<seq>` is shorter, but Redis key loss/rebuild can produce native
IDs that lexically overlap an accepted cursor. Run binding alone cannot prove
that the cursor and replacement key share replay history. We reject this option:
PostgreSQL allocates the incarnation and any same-run older incarnation yields a
gap before Redis is read.

### Redis consumer groups

Consumer groups distribute entries among competing workers. Browsers need
independent replay of the same run, so `XREADGROUP` has the wrong ownership and
pending-entry semantics.

### Authorization only at connection start or heartbeat write

Connection-start authorization leaves a stale-authority window for long blocked
reads; checking only when emitting a heartbeat can also allow a payload returned
by `XREAD` to race revocation. The selected revocable lease is bounded by the
block deadline, supports cross-instance cancellation, and is authoritatively
checked before each payload frame. A process-local TTL cache is rejected.

### Direct model proxy or WebSocket-only transport

A direct proxy/parser provides live bytes but not background-run replay, gap
proof, or durable final reconciliation. WebSocket can carry the same protocol
but does not supply those properties by itself. SSE remains the public adapter.

### Kafka, NATS JetStream, or another durable log

These can provide stronger durable-log semantics but add an operational system
and exceed the selected bounded, per-run replay requirement. Reconsider only if
measured retention, fan-out, or cross-region needs exceed Redis safely.

## Consequences

Positive consequences:

- PostgreSQL no longer receives high-frequency text-delta writes for new
  Redis-pinned runs.
- API instances can independently serve reconnecting browsers with low-latency
  blocking reads.
- Replay coverage and gaps are explicit instead of inferred from local dedupe.
- Final product correctness survives Redis loss because PostgreSQL remains the
  final authority.
- Claude-specific events terminate at an adapter; the public event channel stays
  replaceable with another Harness.

Costs and risks:

- Redis connection and memory budgets become product capacity constraints.
- A producer crash may lose only the bounded, not-yet-flushed memory buffer.
- Redis trim or restart can interrupt live continuity and force an honest gap.
- PostgreSQL gains a small per-run incarnation authority and terminal intent
  lineage. Incarnations can only increase; old and new native Redis IDs are never
  stitched together.
- API, worker, frontend, and migration versions must be coordinated through the
  per-run backend pin and accepted design version.
- Terminal publication needs an idempotent durable intent because PostgreSQL and
  Redis cannot share one atomic transaction.
- Long-lived SSE authorization adds bounded refresh load. Required metrics track
  authorization rechecks, revocation closes/latency, and any payload attempted
  after revocation; the latter must remain zero.

## Migration, Irreversible Boundary, And Rollback

`redis_streams_v1` admission remains disabled until PostgreSQL can atomically
persist backend/design pin plus current incarnation, terminal intent lineage can
pin an incarnation, and producer/API/frontend all enforce the three-component
cursor. Shadow mode is non-public and cannot create a client cursor. Legacy
PostgreSQL runs retain their existing cursor parser; no backend may guess or
translate a cursor owned by another pin.

The irreversible boundary is the first `redis_streams_v1` run for which text
deltas are intentionally not written to PostgreSQL. Those deltas cannot be
reconstructed later. Only the durable final answer and semantic facts are
guaranteed history.

A second irreversible protocol boundary is issuing a cursor for an incarnation:
that incarnation number can never be reset, decremented, reused, or translated
to the former two-component cursor grammar. This design was not deployed before
the incarnation field, so there is no valid Redis cursor migration from
`<run_id>:<ms>-<seq>`.

Rollback changes only admission of new runs to `postgres_legacy`. Existing
Redis-pinned runs stay on their pinned contract until terminal reconciliation
and recovery retention finish. The Redis reader cannot be removed while such
runs remain. Rollback never silently switches an active run or backfills
invented deltas.

Rollback also preserves current incarnation and immutable-target terminal
intents for Redis-pinned runs. A retry may reuse a target only while its key and
envelopes prove the same incarnation. Otherwise an auditable successor intent
increments the incarnation and reuses the same terminal/end semantic event IDs;
it never pretends the rebuilt stream continues an old cursor. Authorization
revocation semantics require no data migration and remain mandatory while any
Redis-pinned reader is retained.

Steady-state dual writing is prohibited. A short-lived non-public shadow mode
may compare envelopes and capacity while PostgreSQL remains authoritative, but
it cannot serve public SSE and must have an explicit expiry. Startup fails when
Redis-primary and PG text-delta persistence are enabled together.

## Evidence Boundary

The DeerFlow, LobeHub, and Open WebUI fixed sources linked by the architecture
document are upstream source evidence only. This ADR and focused tests cannot
prove real Redis/PostgreSQL concurrency, multi-API behavior, browser reconnect,
capacity, deployment, or runtime acceptance. Those remain stage F and
independent review gates.
