# Redis Streams SSE v2.1 Execution Control

Status: normative for `ai-platform.redis-streams-sse-event-channel.v2.1`

Index: [Redis Streams SSE Event Channel](redis-streams-sse-event-channel.md)

## Scope

This document exclusively owns Redis admission before dispatch, reuse of current
run/attempt/sandbox authority, coalescing and backpressure, authorization leases
and revocation, mid-run Redis failure, and terminal convergence.

## Authority reuse and admission

The implementation begins from current durable authority rather than introducing
a parallel execution state machine:

- the run row owns lifecycle and truthful terminal state;
- the current attempt and active sandbox runtime lease fence executor callbacks;
- callback tokens bind tenant/run/attempt and cannot authorize a different run;
- queue and worker leases fence the active dispatcher;
- repository terminal transitions, not executor callbacks, own terminal facts.

V2.1 adds stream admission fields to that same current attempt/run authority:
design ID, projection version, positive stream incarnation, stable stream-open
event ID/bytes/digest, admission state, and timestamps. The worker holding the
existing dispatch fence performs:

1. lock and verify the runnable current attempt and its worker/runtime fence;
2. allocate and commit the next stream incarnation plus deterministic
   `stream_open` intent;
3. atomically append/refresh the exact `stream_open` envelope in Redis;
4. prove the returned or already-existing entry matches the frozen intent;
5. mark admission confirmed with a fenced PostgreSQL compare-and-set;
6. only then dispatch the executor/SDK under the existing attempt authority.

Failure before step 5 produces zero SDK calls. A Redis timeout retries the same
open identity. Mismatched bytes, stale attempt/lease, or unprovable outcome fail
closed. Crash recovery may finish the same intent only while it owns the current
attempt fence; it cannot allocate a new attempt or call the SDK merely because a
Redis key exists.

A separate execution ledger is out of scope unless a failing restart/race test
first proves that the existing attempt plus runtime/worker leases cannot express
the required fence. Any accepted change must extend the same attempt authority,
not duplicate run status, terminal ownership, or executor truth.

## Callback receipt and publication

The authenticated executor callback route validates the exact active attempt and
runtime lease before receipt. It projects the whole batch before publication,
computes the canonical digest and deterministic item identities defined by the
wire protocol, and records one PostgreSQL receipt.

Required durable callback/tool/audit facts may be written in the receipt
transaction. Text delta bytes are not. Publication preserves item order and uses
the same semantic IDs. A duplicate exact receipt resumes/rechecks publication;
a conflicting receipt does not publish. This handles HTTP response loss without
turning PostgreSQL back into the text transport.

Authenticated callback payloads do not become public Skill/tool lifecycle
frames merely because their type name resembles a public type. The callback
boundary continues to withhold arbitrary executor lifecycle payloads. The live
Skill/tool presentation producer is the worker-owned strict execution projector
described below.

If receipt commit succeeds and Redis publication fails or is unknown, the
bounded live result may be absent or duplicated. Retry uses the same IDs/bytes.
The authoritative final answer still converges through terminal/final hydrate.
No unbounded outbox of text deltas and no `run_events` text fallback is created.

## Committed semantic producer

The worker may publish only the two semantic envelope classes currently owned
by closed platform projectors:

- `semantic_stage` wraps a validated fixed platform phase as `run_event`;
- `semantic_progress` wraps a strict versioned `execution_step*` event, including
  the server-owned Skill/MCP/tool presentation mapping.

The worker first appends the safe row to `run_events`. The returned row identity,
PostgreSQL sequence, and database `created_at` are the sole semantic ID,
presentation sequence, and envelope time. After the transaction commits, the
worker opens a new short PostgreSQL transaction to refresh the exact run,
attempt, and stream incarnation authority. It closes that transaction before
the Redis append. A rollback therefore produces no live semantic frame, and an
unknown Redis outcome can retry the same row-derived bytes without minting an
identity.

There is no generic object-to-Redis adapter. Raw command/tool arguments or
results, hidden reasoning, paths, credentials, and executor-selected arbitrary
labels cannot enter these projections. Approval, artifact, and run-status live
producers are outside the accepted V2.1 source set; their durable facts remain
available through authorized hydrate/API paths.

## Projection and bounded coalescer

Private SDK/callback events pass through typed normalization and an event-specific
public projector before the coalescer. Unknown/private events are dropped or
rejected with redacted diagnostics. Hidden reasoning is never a public delta.

Each run/attempt/incarnation/event type has at most one active buffer. Initial
bounds, pending External Acceptance, are:

- maximum flush age: 40 ms;
- maximum encoded text payload: 8 KiB;
- maximum pending bytes: 64 KiB per run and 8 MiB per process;
- no coalescing across run, attempt, incarnation, event type, projection
  version, semantic boundary, or policy boundary.

Flush occurs on age/size limits, newline or semantic boundary, transition to a
non-coalescible event, memory high-water mark, cancellation, error, SDK
completion, shutdown, or terminal request.

At a hard bound, publication is synchronous within a bounded Redis timeout. If
it fails, the coalescer seals, discards only unpublished live bytes, records
transport degradation, and refuses later live deltas. It never drops an older
buffer while claiming continuity, queues without a byte/count deadline, or
writes text deltas to PostgreSQL.

With 50 active runs and a 40 ms age ceiling, the basic upper pressure is about
`50 / 0.04 = 1250` payload frames per second before semantic events,
reconnections, and retries. This is a sizing hypothesis for External Acceptance,
not a passed load result.

## Authorization lease

PostgreSQL owns a positive monotonic authorization epoch for the principal,
tenant, workspace/session, and run scope. Each SSE connection obtains a lease at
open and renews it before expiry. The lease binds:

- principal and tenant/workspace/session/run ownership;
- current stream incarnation and authorization epoch;
- API instance and connection identity;
- `lease_not_after`, calculated from the authority clock and no more than 15
  seconds after issue.

PostgreSQL is queried on connection establishment, renewal, and authority state
transitions, not for every payload. Each payload frame, including gap, terminal,
and end, checks the connection-local lease epoch/deadline and the instance-local
invalidation epoch immediately before gateway write admission. Heartbeat also
requires a current lease but cannot renew it.

The effective read/write block is capped by remaining lease duration. Missed or
uncertain renewal closes fail closed. A database error does not extend local
authority. A process-local timestamp without the durable epoch and invalidation
channel is insufficient.

Required bounded metrics include renewal result/latency, local per-frame rejects,
invalidation closes, lease expiry closes, active connections, and frames by
bounded event class. IDs and payload text are never metric labels.

## Cross-instance invalidation and revocation

An authority transition commits a new epoch in PostgreSQL and publishes a
multi-instance invalidation signal. Each API instance updates its local invalid
epoch, cancels blocked `XREAD`, rejects new old-epoch frames, and closes affected
writers. A new or restarted instance can obtain only the current epoch.

Revocation states are:

- `requested`: change has not committed; previous epoch remains authoritative;
- `committed`: new epoch is durable, old renewal is denied, invalidation is in
  progress;
- `effective`: the owned application/gateway boundary has invalidated every
  registered old-epoch connection or its <=15-second lease expired, and no new
  old-epoch application frame can be admitted for gateway write.

The guarantee is intentionally bounded: after `effective`, the application and
owned SSE gateway produce/accept no new payload under the old epoch. An ASGI send
return means bytes reached the protocol server boundary, not that the browser
received them. Bytes already handed to a protocol server, kernel, Nginx, load
balancer, or client buffer may still arrive. V2.1 therefore makes no browser-byte
or commit-time-zero-frame promise.

The owned gateway must support cancellation and connection close; Nginx must
disable buffering/cache/compression for the SSE location. External Acceptance
injects revocation during blocked read and slow downstream delivery, observes
gateway close and proxy behavior, and records the precise measurable boundary.

If invalidation delivery is lost, lease expiry bounds application authority.
Renewal is denied from current PostgreSQL state. Failures must not report
`effective` before the owned boundary is closed, and timeout/uncertainty stays
pending or fails closed rather than claiming browser quiescence.

## Mid-run Redis failure

Redis unavailability before confirmed stream admission rejects or holds the run
without SDK dispatch. There is no in-process stream substitute.

After dispatch, a Redis failure:

1. seals the bounded coalescer and stops live publications;
2. records transport degradation as a durable run/terminal fact;
3. forbids unbounded retry and PostgreSQL text-delta fallback;
4. preserves cancellation, resource, egress, and safety control.

Eligible non-interactive work may continue only while those control authorities
remain reliable. If approval, user interaction, or a safety/control event is
required but cannot be delivered, execution pauses before the dependent side
effect or terminalizes failure/cancellation. If a bounded safe pause cannot be
maintained, it fails closed.

Redis recovery does not retroactively claim lost text replay. It may resume live
publication only through a proven current incarnation. Loss of continuity
allocates a new incarnation; existing cursors receive a gap and durable hydrate.

## Terminal publication intent

The terminal coordinator first enters a closing state that rejects later deltas.
If Redis is healthy it flushes pending text; otherwise it discards unpublished
live bytes and marks degradation. It then commits one PostgreSQL transaction
containing:

- truthful terminal status: success only after completed execution and a
  durable authoritative final answer; otherwise failure/cancellation/safe pause;
- final answer and required semantic/tool/approval/artifact/audit facts;
- transport-degraded fact when applicable;
- current design, attempt, stream incarnation, envelope schema, and projection
  version;
- stable terminal and end semantic event IDs;
- exact canonical terminal/end payload bytes, byte counts, and cryptographic
  hashes;
- publication state and retry metadata.

Only after commit may Redis receive terminal then end. The publisher recomputes
and verifies bytes/digests before each attempt. An unknown outcome retries the
same bytes and IDs. A duplicate Redis entry is reducer-idempotent.

If the transaction rolls back, neither terminal nor end is published and the run
is not reported successful. Existing attempt/worker maintenance ownership retries
the truthful transaction; a stale owner is fenced by current run/attempt leases.

If the target Redis incarnation is missing or continuity is unprovable, the
reconciler locks the current run/attempt authority, allocates a new incarnation,
and records a successor physical publication referencing the same frozen
semantic intent. It publishes `stream_open`, terminal, and end in the new
incarnation. It does not change payload bytes under the same semantic event ID.
Old cursors see an explicit gap.

Authorized final hydrate reads PostgreSQL and replaces the provisional live fold.
Pending Redis publication does not leave a terminal run permanently `running`.

## Failure matrix

| Scenario | Required behavior |
| --- | --- |
| Redis admission unavailable | zero SDK calls; bounded retry of exact open or fail closed |
| callback HTTP response lost | exact batch retry returns receipt; no new IDs |
| duplicate batch with changed item | fenced conflict; no publish |
| Redis event `XADD` unknown | retry same canonical bytes/event ID; reducer applies once |
| memory cap reached and Redis unavailable | seal/discard unpublished live bytes; no PG delta or unbounded queue |
| approval/control event cannot publish | pause before side effect or fail/cancel |
| PostgreSQL terminal rollback | no terminal/end; no success claim |
| PostgreSQL commit then Redis failure | terminal remains durable; intent pending; final hydrate converges |
| terminal Redis outcome unknown | retry exact frozen bytes/IDs |
| continuity lost during terminal retry | successor incarnation, gap, same semantic intent bytes |
| authorization renewal fails | close fail closed before next payload |
| invalidation during blocked `XREAD` | cancel read and close old-epoch connection |
| frame checked before revocation commit | may already be downstream; no new frame admitted after effective owned boundary |
| late delta races closing | reject and measure; never append after terminal |

## Required focused tests

- admission failure/unknown outcome, stale worker/attempt lease, and zero SDK
  calls before confirmation;
- deterministic callback receipt, response loss, conflict, ordering, and Redis
  unknown outcome;
- committed Skill/tool execution projection after PG commit, stable row-derived
  identity/sequence/time, stale authority fencing, and structural detection of
  direct or nested Redis access inside a PG transaction;
- coalescer age/size/order/bounds, shutdown/terminal flush, secret/private event
  rejection, and no hidden reasoning;
- Redis outage before/mid-run, eligible continuation, approval fail-closed, no
  PG delta fallback, and bounded memory;
- authorization open/renewal, local per-frame check without PG query,
  invalidation, expiry, restart, blocked-read cancellation, and fail-closed DB
  errors;
- terminal PG rollback, commit plus Redis failure/unknown outcome, exact payload
  hash retry, successor incarnation, duplicate event, late delta, and final
  hydrate replacement.
