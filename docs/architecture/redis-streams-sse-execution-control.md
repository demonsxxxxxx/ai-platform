# Redis Streams SSE v4 Execution Control

Status: normative contract for `ai-platform.redis-streams-sse-event-channel.v4`; External Acceptance pending

Index: [Redis Streams SSE Event Channel](redis-streams-sse-event-channel.md)

## Scope

This document exclusively owns transaction-scoped stream admission, reuse of
current Run/Attempt/sandbox authority, direct committed-event publication,
authorization leases and revocation, missing-stream gaps, and terminal
convergence. [ADR 0013](../adr/0013-redis-stream-only-sse.md) owns the hard-cut
decision and supersession.

## Change Contract: Agent first-send stream ownership

- **Owner:** Agent Workspace composer coordination and the existing frontend
  session-route synchronizer.
- **Bounded paths:** `ChatAppContent.tsx`, its source-ownership test, the existing
  mounted routed-session/SSE harness, and this contract.
- **Invariants:** one first-send POST owns one SSE connection; Agent identity,
  Thinking, Skill, MCP, model, authorization, cursor, and wire contracts remain
  unchanged; the shared session synchronizer remains the only URL writer after
  binding.
- **Acceptance:** a first send binds before submission and performs no route
  mutation while the submission/SSE owner is active; the existing route
  synchronizer still derives the Agent conversation URL from the bound session.
- **Regression proof:** the source-ownership test rejects a first-send
  coordinator that mutates the route; a mounted Agent-route test holds the SSE
  open while canonicalizing the bound URL and proves no exact-history load or
  abort displaces that owner before terminal state.
- **Evidence ceiling:** local frontend tests cannot establish deployed browser
  behavior; runtime acceptance requires the exact packaged image on s72.
- **Rollback:** restore the removed navigation only if the shared route
  synchronizer no longer canonicalizes bound Agent sessions, together with an
  owning replacement test.
- **Stop conditions:** any need to change SSE v4 bytes, cursor semantics,
  authorization leases, backend publication, or generic-chat routing requires a
  revised contract.

## Change Contract: Chat submission and SSE admission convergence

- **Owner:** Conversations application owns the read-only linked-Run status projection; the existing frontend SSE connection/reconciliation owner owns startup conflict recovery and terminal convergence.
- **Bounded paths:** `app/conversations/application/submission_resolution.py`, `app/conversations/api.py`, the legacy Chat compatibility imports/delegation, `frontend/web/src/hooks/useAgent.ts`, `frontend/web/src/hooks/useAgent/sseConnection.ts`, their owning tests, and this contract.
- **Invariants:** `chat_submissions.state` and `outcome.status` remain admission/ledger facts; `runs.status` remains the execution authority; linked Run lookup is scoped by tenant and user; missing or inaccessible Runs project as `run_status: null`; SSE wire bytes, Redis persistence, Worker authority timing, stream version, incarnation, cursor, and current-owner fences remain unchanged.
- **Acceptance:** a submission response exposes an authorized normalized `run_status` for `queued`, `running`, `succeeded`, `failed`, and `cancelled` (including raw `canceled` normalization); explicit transient `sse_stream_not_admitted` and `sse_stream_not_confirmed` conflicts remain bounded and recoverable; terminal Runs hydrate without further reconnect; explicit non-retryable startup conflicts and invalid public frames fail closed without status reconciliation or reconnect and settle local loading/streaming state.
- **Regression proof:** owning tests cover linked status projection, missing/cross-principal Run fencing, retryable 409 recovery, terminal convergence, bounded active retry exhaustion, non-retryable conflicts, invalid JSON/missing event ID/invalid V4 envelope, stale callbacks, and the absence of lingering reconnect work.
- **Stop conditions:** do not treat admission `queued` as an active Run; do not retry arbitrary 409s or protocol-invalid frames; any change to Worker authority timing, SSE wire/schema, Redis/persistence semantics, or cross-incarnation successor recovery requires a revised contract and independent acceptance.


The implementation begins from current durable authority rather than introducing
a parallel execution state machine:

- the Run row owns lifecycle and truthful terminal state;
- the current Attempt and active sandbox runtime lease fence executor callbacks;
- callback tokens bind tenant/Run/Attempt and cannot authorize another Run;
- queue and Worker leases fence the active dispatcher;
- repository terminal transitions, not executor callbacks, own terminal facts.

V4 stores design ID, projection version, positive stream incarnation, canonical
`stream.open` bytes/digest, admission state, and authorization epoch under that
same authority. The Worker prepares pending stream authority while holding the
existing Run/current Attempt locks, then publishes and confirms the exact open
before SDK dispatch. Immediate enqueue-failure and cancellation paths reuse this
admission contract. There is no pending-admission scan. Redis I/O occurs only
after the PostgreSQL transaction releases its locks.

A separate execution ledger is out of scope. Any authority extension must use
the existing Run/Attempt/lease fences rather than duplicate Run status,
terminal ownership, or executor truth.

## Callback receipt and publication

The authenticated executor callback route validates the exact active Attempt
and runtime lease before receipt. The platform adapter validates the complete
batch, assigns deterministic public identities, and commits canonical public
`run_events` plus the callback receipt in one transaction. Unknown, private, or
malformed SDK values do not become public rows.

Publication occurs after that transaction commits. The callback route appends
the complete canonical batch directly to Redis Stream before acknowledging the
callback. Callback rows do not enter a PostgreSQL publication queue and have no
publication claim, retry counter, or pending/published disposition.

The existing executor callback buffer serializes batches and waits for an exact
acknowledgement before sending the next batch. Redis appends each batch and its
bounded idempotency receipt in one script. A repeated batch reuses its committed
semantic IDs and bytes without appending duplicate records. A Redis outage
returns a retryable callback error through the existing callback delivery policy;
it does not roll back committed callback audit or answer facts. No PostgreSQL
lock is held across Redis I/O.

Run-terminal and cancellation publishers use committed business facts directly.
They do not stage independent terminal intents or persist delivery disposition.
Before writing a callback batch, the same application path loads and directly
publishes any preceding committed Run event. This closes the opposite race in
which a later callback would otherwise overtake a delayed cancellation request.
The lookup is bounded to events before that batch; no scan or retry task owns it.
Callback receipts never overwrite the separate Run-event receipt.

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

Callback delivery uses the existing bounded executor buffer and exact-batch
acknowledgement. Redis retains at most the configured approximate Stream length
and expires its data and receipt state together. The API replays retained
records and then uses exclusive `XREAD BLOCK` on a separate bounded read pool;
each read returns at most 128 events and blocks for at most five seconds.
Heartbeat and gap controls are emitted by the authorized SSE connection.

Redis Stream is the sole live/replay transport. There is no Pub/Sub producer,
shared subscriber hub, or PostgreSQL-to-browser polling path. Committed callback
rows remain the audit and final-answer source, independent of browser delivery.

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
write admission. Heartbeat also requires a current lease but cannot renew it.
Only the per-browser lease authorizes the connection.

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
deadline, then closes the affected writer when renewal is denied. Redis Stream
delivery is not an authorization invalidation bus.

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
balancer, or client buffer may still arrive. V4 therefore makes no browser-byte
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

Redis unavailability before confirmed stream admission prevents SDK dispatch.
There is no in-process stream substitute or pending-admission scan.

After dispatch, a callback append failure returns a retryable callback error
through the existing bounded executor delivery policy. Its committed PostgreSQL
facts remain unchanged. An XREAD failure closes the affected SSE connection;
reconnect uses its accepted cursor. A missing or trimmed Stream emits a gap and
requires authorized hydration. No publisher reconstructs a missing Stream or
allocates a successor.

Transport failure does not revoke cancellation, resource cleanup, egress or
safety authority. Eligible execution can continue only while those existing
controls remain reliable. Runtime approval is not exposed over SSE. There is no
unbounded retry, PostgreSQL browser polling, or background publication drain.

## Committed Run terminal publication

The existing Run/Attempt/Worker owners commit truthful terminal state, final
answers and required audit facts under their existing transaction fences. Only
then does the direct publisher load the committed Run fact and derive its stable
terminal and linked `stream.end` identities. It needs no live execution lease to
publish an already committed terminal fact.

The Run event source includes the highest callback sequence strictly before
that event in the same Run/Attempt/incarnation. This stable prefix is derived
from business facts, not persisted publication state. Redis checks its existing
callback batch receipt before accepting cancellation requests or closing the
Stream. Neither can overtake a callback between PostgreSQL commit and Redis
append. A later callback cannot change the frozen cancellation payload. If
the callback has not reached Redis, terminal publication returns unavailable
without changing the open phase or undoing Run finalization. The in-flight
callback can still append. SSE closes from authoritative terminal state and
hydrates; no publication scheduler is introduced.

With a delivered callback prefix, Redis appends terminal then end. Repeating
the direct operation reuses its receipts. If terminal succeeds but end fails,
a later invocation reuses the terminal receipt and appends the deterministic
missing end. Missing/expired Streams remain missing; neither receipts nor
business facts authorize reconstruction.

PostgreSQL rollback publishes no terminal/end. Redis failure after commit never
rolls back Run status or suppresses stored answers. Authorized hydration reads
the committed business state independently of delivery. No background retry
promises eventual SSE terminal delivery.

## Failure matrix

| Scenario | Required behavior |
| --- | --- |
| Redis admission unavailable | zero SDK calls; fail closed |
| callback HTTP response lost | existing exact-batch retry; no new semantic IDs |
| duplicate callback with changed content | conflict; no publish |
| callback commit followed by Redis failure | retain facts; callback delivery reports failure |
| Run event races a committed callback append | existing receipts and bounded predecessor lookup preserve order in both directions; business facts stay committed |
| publication occurs after replay tail capture | exclusive XREAD from that tail observes later retained entries |
| XREAD failure or slow downstream | close affected SSE; retain last accepted cursor |
| PostgreSQL terminal rollback | no terminal/end; no success claim |
| PostgreSQL terminal commit then Redis failure | truthful final state; authorized hydration |
| terminal succeeded but end failed | same terminal receipt and deterministic end on a later invocation |
| Stream missing or continuity lost | gap/hydrate; no recreation or successor |
| authorization renewal fails | close fail closed before the next payload |
| epoch commits during blocked read | renewal denied; no new old-epoch application frame after the <=15-second lease deadline |
| frame admitted before revocation commit | already handed-off bytes may arrive; no browser-byte guarantee |
| callback after terminal closure | reject; never append after terminal/end |

## Required focused tests

- admission failure, stale Run/Attempt ownership, and zero SDK calls before confirmation;
- callback receipt atomicity, exact retry/conflict, commit-before-append, batch order and bounded buffering;
- public projection, private-data rejection, committed answer reconstruction without Redis, and no Redis I/O under PostgreSQL locks;
- real Redis initial replay, predecessor trim, exclusive XREAD after captured tail, malformed/foreign cursors, terminal/missing-stream gaps and pool cleanup;
- authorization open/renewal, per-frame deadline checks, expiry during a blocked read, restart and fail-closed database errors;
- terminal rollback, committed callback/terminal race, partial terminal/end retry, expired Stream, truthful final state and durable hydration;
- retired-state refusal, suppression preservation, schema-local migration, old-binary readiness refusal and obsolete index-ledger cleanup.

## Convergence proposals and evidence

The [runtime convergence proposal](runtime-convergence.md) retains only proposals
compatible with this contract. PostgreSQL publication schedulers, wakeups and
successor work are superseded by ADR 0013. Business executor reconciliation and
resource cleanup remain independent owners. Source, local tests, CI, packaged
images and External Acceptance are separate evidence levels.
