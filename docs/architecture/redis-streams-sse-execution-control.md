# Redis Streams SSE v3 Execution Control

Status: normative contract for `ai-platform.redis-streams-sse-event-channel.v3`; External Acceptance pending

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

V3 reuses the stream admission fields already attached to that current
attempt/run authority: design ID, projection version, positive stream
incarnation, stable stream-open event ID/bytes/digest, admission state, and
timestamps. The worker holding the existing dispatch fence performs:

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

The worker may publish only the two semantic envelope classes currently owned by
closed platform projectors:

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
labels cannot enter these projections. Runtime approval is not a Streaming
producer. Artifact and Run-status live producers remain outside v3 unless a
separately reviewed committed source contract is accepted.

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
transport degradation, and refuses later live deltas. The atomic append writes
the retained Stream entry and publishes the same canonical envelope to live
subscribers. A missed live notification is repaired from the Stream; no producer
writes Pub/Sub separately.

The coalescer never drops an older buffer while claiming continuity, queues
without a byte/count deadline, or writes text deltas to PostgreSQL. The API live
hub separately bounds each browser by event count and bytes. Subscriber overflow
or shared-feed uncertainty closes the connection; it never drops an event while
continuing from a later cursor.

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
transitions, not for every payload. Lease acquisition validates the durable
authorization epoch. Each payload frame, including replay gap, terminal, and
end, checks the authority-clock `lease_not_after` immediately before gateway
write admission. Heartbeat also requires a current lease but cannot renew it. A
Pub/Sub subscription does not authorize a principal; only the per-browser lease
does.

A committed epoch change immediately fences renewal. A lease issued before that
commit remains authoritative only until its `lease_not_after`, so the effective
read/write block is capped by the remaining lease duration and never exceeds 15
seconds. Missed or uncertain renewal closes fail closed. A database error does
not extend local authority. A process-local timestamp without a durable,
epoch-backed lease is insufficient.

Required bounded metrics include renewal result/latency, per-frame deadline
rejects, lease expiry closes, active connections, and frames by bounded event
class. IDs and payload text are never metric labels.

## Cross-instance invalidation and revocation

An authority transition commits a new epoch in PostgreSQL. Later lease
acquisition or renewal can obtain only that current epoch. Each API instance
continues to admit an already issued lease only until its authority-clock
deadline, then closes the affected writer when renewal is denied. Redis Pub/Sub
is the run-event transport and is not presented as an authorization invalidation
bus.

Revocation states are:

- `requested`: change has not committed; previous epoch remains authoritative;
- `committed`: new epoch is durable and old-epoch lease renewal is denied;
- `effective`: every registered old-epoch connection has closed or its
  <=15-second lease expired, and no new old-epoch application frame can be
  admitted for gateway write.

The guarantee is intentionally bounded: after `effective`, the application and
owned SSE gateway produce/accept no new payload under the old epoch. An ASGI send
return means bytes reached the protocol server boundary, not that the browser
received them. Bytes already handed to a protocol server, kernel, Nginx, load
balancer, or client buffer may still arrive. V3 therefore makes no browser-byte
or commit-time-zero-frame promise.

The owned gateway must support cancellation and connection close; Nginx must
disable buffering/cache/compression for the SSE location. External Acceptance
injects revocation during blocked read and slow downstream delivery, verifies
that renewal is denied, observes no old-epoch application frame after the
recorded lease deadline, and records the precise measurable boundary.

Lease expiry bounds application authority even if a browser is blocked in a
live wait. Renewal is denied from current PostgreSQL state. Failures must not
report `effective` before the owned boundary is closed, and timeout/uncertainty
stays pending or fails closed rather than claiming browser quiescence.

## Mid-run Redis failure

Redis unavailability before confirmed stream admission rejects or holds the run
without SDK dispatch. There is no in-process stream substitute.

After dispatch, a Redis append or shared live-feed failure:

1. seals the bounded producer coalescer when append continuity is uncertain;
2. closes affected browser subscribers when only live notification is lost, so
   they reconnect and replay from Redis Stream;
3. records transport degradation when retained append is unavailable;
4. forbids unbounded retry and PostgreSQL text-delta fallback;
5. preserves cancellation, resource, egress, and safety control.

Eligible non-interactive work may continue only while those control authorities
remain reliable. V3 does not expose runtime approval over this stream. Any
future safety-critical interaction must first define its own durable authority
and fail-closed behavior; Pub/Sub delivery alone can never authorize a side
effect.

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
| Pub/Sub disconnect or local queue overflow | close affected SSE without cursor advance; reconnect repairs from Stream |
| attach races publication | subscribe acknowledgement before bounded replay; overlap dedupe; no missed semantic event |
| PostgreSQL terminal rollback | no terminal/end; no success claim |
| PostgreSQL commit then Redis failure | terminal remains durable; intent pending; final hydrate converges |
| terminal Redis outcome unknown | retry exact frozen bytes/IDs |
| continuity lost during terminal retry | successor incarnation, gap, same semantic intent bytes |
| authorization renewal fails | close fail closed before next payload |
| authorization epoch commits during blocked/live wait | renewal is denied; no old-epoch application frame is admitted after the <=15-second lease deadline |
| frame checked before revocation commit | may already be downstream; the issued lease remains authority only through its recorded deadline |
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
- Redis outage before/mid-run, eligible continuation, no PG delta fallback, and
  bounded memory;
- authorization open/renewal, local deadline check without PG query, expiry,
  restart, blocked-read lease expiry, and fail-closed DB errors;
- terminal PG rollback, commit plus Redis failure/unknown outcome, exact payload
  hash retry, successor incarnation, duplicate event, late delta, and final
  hydrate replacement.
