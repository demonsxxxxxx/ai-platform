---
status: superseded
supersedes: 0003-redis-streams-sse-event-channel-v2-correction.md
superseded_by: 0009-redis-streams-sse-v3-live-fanout.md
---

# Correct Redis Streams SSE v2 implementation and release boundaries

Design ID: `ai-platform.redis-streams-sse-event-channel.v2.1`

Source baseline: `c41e48dcb127ea8589b92c0b2211260c0cee3f81`

## Context

ADR 0003 preserved the correct business-authority split: Redis Streams is a
bounded live/replay plane, while PostgreSQL owns runs, final answers, tools,
approvals, artifacts, audit facts, and terminal recovery. Its detailed design
nevertheless mixed decisions, wire format, execution control, stage ownership,
and runtime acceptance in one 1,700-line document. That text left nine unsafe
implementation interpretations:

1. It treated an ASGI send and socket close as if the application could prove
   bytes had or had not reached a browser through every downstream proxy.
2. It required serial stage merges while deleting the PostgreSQL producer before
   replacing its reader and forbidding deployable dual stacks.
3. It did not bind executor callback retries and response-loss recovery to one
   deterministic batch/item identity.
4. It described a PostgreSQL authorization lookup before every payload, which
   would replace delta-write amplification with read amplification.
5. It named a Redis TTL but did not require atomic `XADD` plus expiry refresh or
   distinguish active-idle from terminal replay retention.
6. It pinned terminal semantic IDs but not the exact schema, projection version,
   payload digest, and bytes that an unknown-outcome retry must reproduce.
7. It did not freeze the proxy, compression, timeout, heartbeat, and real-proxy
   acceptance contract as one operational boundary.
8. It listed `assistant_reasoning_delta` even though hidden model reasoning is
   not a public product event.
9. It prescribed a new execution ledger without first proving that the current
   run, attempt, callback-token, sandbox-runtime-lease, and worker-lease fences
   were insufficient.

## Decision

### Durable and live authority

PostgreSQL remains the only authority for run/session status, attempt identity,
final answer, tool/approval/artifact facts, required public semantics, audit,
callback receipts, stream incarnation, authorization epoch, and terminal
publication intent. Redis Streams stores only already-safe, bounded live/replay
events and is never permanent business truth.

The public Chat stream URL remains stable. The implementation uses per-run Redis
Streams with `XADD` and independent `XREAD`; it never uses `XREADGROUP`, a
process-memory replay substitute, or a PostgreSQL text-delta fallback.

### Existing execution authority first

V2.1 reuses the current run/attempt lifecycle, callback token binding, exact
active sandbox runtime lease, queue/worker ownership, and terminal transition
fences. A new execution ledger is prohibited unless an implementation change
first demonstrates, with a failing concurrency/restart test, a concrete
at-most-once property that those authorities cannot express. Any approved
extension must attach to the existing attempt authority rather than create a
second run or terminal state machine.

Redis admission must succeed before SDK dispatch. The existing dispatch owner
records the admitted stream incarnation on the same attempt before invoking the
executor. Redis admission failure or an unknown result that cannot be proven by
deterministic retry fails closed without an SDK call.

### Deterministic callback receipt

Every executor callback batch binds `run_id`, current `attempt_id`, immutable
`batch_id`, and ordered `item_index`. The semantic event ID and source sequence
are deterministic functions of those values and the frozen projection version.
PostgreSQL stores one batch receipt and its input digest, count, first/through
source sequence, and accepted semantic IDs; it does not store each text delta.

An exact retry returns the existing receipt. The same batch identity with
different content, order, projection version, or attempt is a fenced conflict.
HTTP response loss and Redis `XADD` unknown outcomes therefore retry the same
identity and bytes, never mint a replacement event.

### Authorization and revocation boundary

Connection establishment and renewal obtain a PostgreSQL-authorized lease of no
more than 15 seconds, binding principal, tenant, session, run, stream
incarnation, and positive authorization epoch. Every payload checks the cached
lease deadline, epoch, and an instance-local invalidation marker; it does not
round-trip to PostgreSQL per frame. PostgreSQL remains authoritative at renewal
and state transitions. Cross-instance invalidation closes blocked reads and
writers; missed renewal or invalidation uncertainty fails closed.

The guaranteed revocation boundary is the owned SSE application/gateway
boundary: after revocation becomes effective, no new application payload frame
may be created or accepted for gateway write under the old epoch. ASGI send
completion proves only handoff to the protocol server, not browser receipt.
Downstream proxy buffers may already contain bytes, so v2.1 does not promise
commit-time or browser-observed zero bytes. Real proxy acceptance separately
verifies buffering and cancellation behavior.

### Atomic retention

Every event append atomically performs `XADD` with approximate length trimming
and `PEXPIRE` in one Lua script (or an equivalent single Redis transaction with
checked outcomes). Active traffic refreshes an active-idle TTL. Terminal/end
publication switches to a distinct terminal replay TTL. A long-running stream
cannot expire merely because its wall-clock duration exceeds a fixed creation
TTL.

### Terminal intent

The truthful PostgreSQL terminal transaction commits before Redis terminal/end.
It freezes terminal and end semantic event IDs, envelope schema, projection
version, canonical payload bytes and digest, stream incarnation, attempt, and
publication state. Unknown Redis outcomes retry those exact bytes and IDs.
Rebuild after continuity loss allocates a new incarnation and produces a gap;
it does not publish different payload under an old semantic ID. Authorized final
hydrate replaces the partial live fold.

### Public projection

Projection, allowlisting, bounding, and secret/path/command filtering occur
before coalescing and `XADD`. Raw Claude events, raw tool inputs/outputs,
commands, credentials, storage paths/keys, and hidden chain-of-thought are never
public stream events. Public progress is limited to server-owned semantic stage,
bounded progress, and safe summary events. Text deltas contain only public answer
text.

### Release-atomic cutover

Pure contracts, pools, repositories, schema, and dormant adapters may be merged
or committed without admitting a Redis-backed run. The behavior-changing
producer, reader, terminal, and frontend switch (B-E) is one release-atomic
cutover set. CI and release tooling reject an image containing only part of that
set. No intermediate main image may enable v2.1 admission, and no feature flag
may select a PostgreSQL/Redis dual live stack.

The final source removes PostgreSQL `assistant_delta` production and live
poll/reconnect consumption together. Historical rows remain audit/history data;
they are not copied into Redis or used as a live cursor.

## Fixed-SHA reference evidence

Reference implementations informed the decision but do not establish
AI Platform runtime behavior.

LobeHub/lobe-chat was inspected at
[`afa1cfe48ecd3d5ce5c79a556991f39bc4a87ef4`](https://github.com/lobehub/lobe-chat/commit/afa1cfe48ecd3d5ce5c79a556991f39bc4a87ef4).
Its `StreamEventManager` uses per-operation Redis Streams, `XADD MAXLEN ~ 1000`,
a refreshed two-hour expiry, independent duplicated blocking connections, and
`XREAD BLOCK`. Its SSE client sends `Last-Event-ID`, but the inspected route reads
a query parameter instead; its history path compares an event timestamp with a
Redis ID and begins realtime subscription after history, so ownership,
resume/race logic, fixed retention values, and memory fallback are not copied.
Its separate `XADD` then `EXPIRE` commands also do not satisfy v2.1 atomic TTL
refresh.

LibreChat was inspected at
[`45cc53c40b47645b887c3bb996168e06aaa83f4c`](https://github.com/danny-avila/LibreChat/commit/45cc53c40b47645b887c3bb996168e06aaa83f4c).
Its realtime transport is Redis Pub/Sub, not Redis Streams; resumability depends
on a JobStore/chunk log (whose Redis implementation uses Streams), Lua sequence
allocation, snapshot/subscribe gap capture, generation `createdAt` fencing,
bounded early/reorder buffers, owner/tenant checks, and central abort. Its
`resume=true` protocol is not standard `Last-Event-ID`. V2.1 adopts the general
fencing, bounded buffer, and race-closure lessons while retaining direct
per-run Redis Streams and the canonical cursor in the wire contract.

## Contract ownership

This ADR owns the decision and rationale only. Normative details have one owner:

- [`../architecture/redis-streams-sse-event-channel.md`](../architecture/redis-streams-sse-event-channel.md)
  is the authority index and component overview.
- [`../architecture/redis-streams-sse-wire-protocol.md`](../architecture/redis-streams-sse-wire-protocol.md)
  owns envelopes, callback identity, Redis keys/retention, cursors, gaps, and
  frontend reducer semantics.
- [`../architecture/redis-streams-sse-execution-control.md`](../architecture/redis-streams-sse-execution-control.md)
  owns admission, coalescing, authorization leases, outage behavior, and
  terminal convergence.
- [`../operations/redis-streams-sse-cutover-acceptance.md`](../operations/redis-streams-sse-cutover-acceptance.md)
  owns release-atomic cutover, gateway configuration, checks, measurement, and
  External Acceptance.

If a detail appears in more than one file, the owning file above is normative;
other appearances are summaries and must link back rather than diverge.

## Consequences

- Fifty active runs at a 40 ms coalescer ceiling can produce roughly 1,250
  payload frames per second before semantic events and reconnects. Authorization
  must therefore use bounded leases and local per-frame checks; stage F measures
  actual QPS, latency, Redis memory, pool use, and PostgreSQL renewal load.
- Text visible only in Redis may be lost after retention or producer failure.
  This is an explicit live-transport property; the durable final answer restores
  product correctness.
- A Redis outage after dispatch can degrade eligible non-interactive execution,
  but cannot create unbounded buffering, PostgreSQL delta writes, or an unsafe
  approval/control path.
- Proxy and browser observations remain runtime evidence, not conclusions from
  ASGI unit tests.

## Evidence boundary

This ADR and its companion documents establish source authority only. Local
tests can prove deterministic contracts and fault injection. They cannot prove a
deployment, multi-replica invalidation, real proxy behavior, a browser chain, or
50-concurrent-run capacity. Those remain External Acceptance on an exact source,
image, configuration, Redis/PostgreSQL topology, and browser build.
