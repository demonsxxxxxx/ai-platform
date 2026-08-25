# Redis Streams SSE v4 Execution Control

Status: normative contract for `ai-platform.redis-streams-sse-event-channel.v4`; External Acceptance pending

Index: [Redis Streams SSE Event Channel](redis-streams-sse-event-channel.md)

## Scope

This document exclusively owns transaction-scoped stream admission, reuse of
current Run/Attempt/sandbox authority, durable public-event publication,
authorization leases and revocation, retry maintenance, missing-stream
successor recovery, and terminal convergence.

## Authority reuse and admission

The implementation begins from current durable authority rather than introducing
a parallel execution state machine:

- the Run row owns lifecycle and truthful terminal state;
- the current Attempt and active sandbox runtime lease fence executor callbacks;
- callback tokens bind tenant/Run/Attempt and cannot authorize another Run;
- queue and Worker leases fence the active dispatcher;
- repository terminal transitions, not executor callbacks, own terminal facts.

V4 stores design ID, projection version, positive stream incarnation, canonical
`stream.open` bytes/digest, admission state, and authorization epoch under that
same authority. Before a Worker transaction can append any public or terminal
row, it locks the Run/current Attempt and prepares the pending stream authority
on the same connection. Direct and maintenance admission validate the same
canonical receipt contract, while Redis I/O occurs only after the PostgreSQL
transaction releases its locks.

A separate execution ledger is out of scope. Any authority extension must use
the existing Run/Attempt/lease fences rather than duplicate Run status,
terminal ownership, or executor truth.

## Callback receipt and publication

The authenticated executor callback route validates the exact active Attempt
and runtime lease before receipt. The platform adapter validates the complete
batch, assigns deterministic public identities, and commits canonical public
`run_events` plus the callback receipt in one transaction. Unknown, private, or
malformed SDK values do not become public rows.

Publication is an application operation with three phases:

1. claim the oldest eligible rows and commit the opaque claim token;
2. release PostgreSQL locks, append canonical bytes through the Redis transport,
   and require the exact bounded persisted receipt;
3. disposition success or retry under a claim-token and PostgreSQL-clock fence.

Duplicate callback, Redis, or disposition outcomes reuse the same semantic IDs,
bytes, and receipt. Transport outage leaves indexed pending work for bounded
maintenance retry. Unexpected application failure releases the claim when it
can do so safely; expiry permits fenced takeover. No route or adapter holds a
PostgreSQL lock across Redis I/O.

## Committed public-event producer

All closed v4 Agent-kernel application types use the one generated schema and
strict event-specific projector. Message, thinking-state, model, tool,
subagent, artifact, policy, cancellation, and Run-terminal events are ordered by
the committed Run-local `seq`; transport controls consume no business sequence.

Raw SDK values, commands, tool arguments/results, hidden reasoning, paths,
credentials, runtime approval payloads, and executor-selected arbitrary labels
never enter canonical public bytes. Engine-specific values terminate at the
adapter boundary.

## Publication bounds and backpressure

Publication claims are bounded by count, canonical bytes, predecessor order,
and claim TTL. Pending rows are selected through the owned retry index. Redis
append enforces canonical envelope identity and protocol phase, atomically
writes the retained Stream record, refreshes TTL, and publishes the same bytes.
The API hub separately bounds each browser by event count and bytes; subscriber
overflow or shared-feed uncertainty closes the connection without advancing its
accepted cursor.

Safety-critical interaction does not depend on Pub/Sub. A missed notification
is repaired by Stream replay; missing terminal history is repaired only by the
successor protocol below. No unbounded in-memory queue or PostgreSQL-to-browser
polling fallback is permitted.

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

Redis recovery does not retroactively claim lost text replay or rebuild the
current physical stream in place. Loss of continuity is eligible for rebuild
only after both the Run and its current RunAttempt are terminal and the current
stream authority is terminal. Preparation is a PostgreSQL-only transaction: it
locks the Run, RunAttempt, stream authority, and event cursor; records the exact
source authority fingerprint and high-water mark; allocates a monotonically new
incarnation and authorization epoch; and freezes successor canonical bytes for
all eligible public `run_events`. It neither calls Redis nor mutates source
`run_events`, stream authority, or leases.

A later builder owns the candidate through a hashed, expiring claim token. It
may perform Redis I/O only after the preparation transaction commits, and it may
write only the reserved successor key. Crash takeover allocates another new
incarnation rather than reusing a partially built candidate. Activation remains
a separate token-CAS transaction which must revalidate the unchanged source
fingerprint and high-water mark before changing authority. Existing cursors are
never continuous across that change; they receive a gap and durable hydrate.

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
reconciler must use the durable successor operation above. It never changes
current authority before the candidate has a complete `stream.open`, ordered
successor projection, terminal, and linked `stream.end`. The readiness/cutover
transaction rejects an expired token, changed source authority, changed source
high-water mark, or incomplete candidate. Only then may it advance authority;
old cursors see an explicit gap. The original `run_events` semantic identity,
sequence, payload, and commit time remain unchanged, while the successor
physical envelope records the new incarnation.

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
  hash retry, exclusive terminal successor preparation, expired-claim takeover
  without incarnation reuse, candidate completeness, stale-token/source-fingerprint
  rejection, duplicate event, late delta, and final hydrate replacement.
