---
status: accepted
supersedes: 0002-redis-streams-sse-event-channel.md
---

# Correct Redis Streams SSE staging, revocation fencing, and degraded transport

Design ID: `ai-platform.redis-streams-sse-event-channel.v2`

Source baseline: `5d5a0c537baa0af2d9c47cb8d010a713c5240dc6`

## Context

ADR 0002 selected the correct storage and replay split but left three unsafe
implementation interpretations:

1. Its A-F graph allowed a pure StreamBridge stage before the PostgreSQL
   admission authority that must allocate the backend/design pin, attempt,
   generation, and monotonic stream incarnation.
2. Its revocation language treated an authorization transaction commit as if it
   could be atomic with every cross-instance network write.
3. Its mid-run Redis failure policy treated live-transport loss as automatic
   execution failure and did not define success/failure/cancel convergence when
   terminal publication fails or is uncertain.

The v1 design ID was publicly accepted. Reinterpreting it in place would make
deployed/configured identity ambiguous, so this correction uses a new v2 design
ID, envelope/gap schema identity, backend pin, and Redis key namespace.

## Decision

### Implementation dependency

The only allowed order is:

`A0 pure envelope/cursor/StreamBridge contract -> A1 PostgreSQL admission authority -> B producer/coalescer -> C SSE reader -> D terminal convergence/intent and stop-PG-delta policy -> E frontend -> F real acceptance`

A0 cannot enable production, admit a Redis run, or dispatch the SDK. A1 must
atomically persist the v2 backend/design pin, monotonic `stream_incarnation`,
attempt/generation authority, current authorization epoch,
`admission_open_pending`, an idempotent open token, owner identity, a positive
monotonic `admission_owner_epoch`, an owner lease, and a bounded deadline. That
transaction commits before `stream_open`. The exact-token open is idempotent and
fails closed if an existing first envelope does not match every pin.

Takeover is allowed only after database-clock lease expiry while admission is
pending. It row-locks the authority, replaces the owner, and increments
`admission_owner_epoch` without changing the token or pins. A delayed old-owner
success or unknown-result retry cannot renew, confirm, create dispatch authority,
or call the SDK after its epoch is stale.

The `stream_open_confirmed` transaction is a compare-and-set requiring pending
state, exact open token and pins, current owner identity/epoch, and a lease still
valid by database time. It atomically records confirmation and inserts the one
immutable `sdk_dispatch_intent`, with a `dispatch_token` unique for the
run/attempt/generation. Zero updated rows is a fenced result; confirmation without
the intent rolls back.

Every SDK start goes through `dispatch_once(dispatch_token)`, which durably
acquires or returns the same `sdk_execution_fence` and execution identity before
external SDK start. The SDK gateway uses that identity as a mandatory idempotency
key and acquires or returns the same execution handle. Retry and crash recovery
may finish that handle without starting another SDK execution. A boundary unable
to prove this property after an uncertain start fails A1 closed; it never falls
back to a fresh SDK call.

An open failure or unknown result never authorizes dispatch. The owner retries
the same token; after lease expiry a maintenance owner takes over with a higher
fence. The normative RED set includes a delayed old-owner response after takeover,
unknown XADD with a two-owner retry, and a crash after confirm commit but before
dispatch. Each must prove stale CAS rejection, one confirmation/dispatch intent,
and exactly one acquire-or-return SDK execution identity. Within one admission-
lease expiry plus one maintenance interval after PostgreSQL and Redis are
available, recovery must either confirm that same open or commit a truthful pre-
dispatch admission failure. Once D is present, D owns any corresponding
publication intent. Recovery cannot leave the run permanently running or allocate
a replacement incarnation to hide ambiguity.

A1 must merge and pass an isolated real-PostgreSQL two-connection gate before B
may produce events or dispatch any Redis-pinned run. A missing database gate is
evidence blocked, not a pass. Production remains disabled until the later stages
and F acceptance complete.

Frozen Slice A candidate
`b6f3c0878c5c68358e57664174828b7404959a84` is not implementation authority.
After v2 merges it may be independently re-reviewed only as A0, or discarded; it
cannot be reused as A1 or later-stage authority.

### Revocation fencing

Authorization authority maintains a positive monotonic `authorization_epoch` for
the affected authorization scope. Every SSE connection and send lease binds one
exact epoch and a deadline of at most 15 seconds.

Revocation has three externally meaningful states:

- `requested`: the change has not committed and the old epoch remains reported
  authority; the API reports `access_revocation_requested`;
- `committed`: the new epoch is durable, old-epoch renewal is denied, and
  cross-instance invalidation/ack is in progress; the API reports
  `access_revocation_pending`;
- `effective`: every old-epoch writer acknowledged closed or every old bounded
  lease expired; the API reports `access_revoked`. Zero payload is guaranteed
  only after this barrier.

Each API instance applies invalidation, cancels blocked `XREAD`, closes every
old-epoch writer, and acknowledges quiescence. A missing acknowledgement prevents
early effective status. The authorization lease has a normative maximum of 15
seconds, configurable lower but never higher, so lease expiry supplies the
maximum 15-second committed-to-effective window. Authority errors and renewal
attempts fail closed.

The SSE writer rechecks its epoch immediately before each payload write. A write
that checked epoch e may race an e+1 commit and finish before the effective
barrier. V2 measures and bounds that race rather than promising impossible
commit-time zero frames. Stage F must inject this exact check/commit/write race,
including a missing instance acknowledgement, and prove zero payload after the
recorded effective barrier.

### Mid-run Redis failure

Redis unavailability before admission rejects or holds the run without SDK
dispatch. There is no process-memory stream fallback.

After dispatch, Redis failure marks live transport degraded, seals the bounded
coalescer, stops live deltas, and forbids both unbounded buffering and PostgreSQL
text-delta fallback. It does not by itself revoke execution authority.

An eligible non-interactive long task may continue only while cancellation,
resource, egress, and safety authorities remain controllable. If approval, user
interaction, or a control/security event cannot be delivered reliably, execution
pauses before the dependent side effect or fails closed. If a safe bounded pause
or cancellation cannot be maintained, the run terminalizes failure/cancellation.

### Terminal convergence

The terminal coordinator seals live transport, then commits one truthful
PostgreSQL transaction containing:

- success only for completed SDK execution and its authoritative final answer;
- otherwise failure, cancellation, or a safely paused non-success state;
- required semantic/artifact/tool/approval facts;
- the transport-degraded fact when applicable; and
- an immutable terminal/end publication intent pinned to design, incarnation,
  generation, attempt, and stable semantic event IDs.

Only after that transaction commits may Redis receive terminal and end. If
terminal `XADD` fails or has an unknown outcome, the run remains durably terminal
and the intent remains pending. Reconciliation retries idempotently in a proven
incarnation or creates an auditable successor incarnation/intent when continuity
is unproven. It never leaves a run permanently running and never labels incomplete
execution successful. Browsers converge through authorized durable status/final
hydrate while publication is pending.

If the PostgreSQL terminal transaction rolls back, the coordinator neither
acknowledges execution completion nor releases the durable execution lease. It
records or preserves `terminal_recovery_pending` under the same attempt and
generation, retries the truthful terminal transaction, and allows a maintenance
owner to take over after execution-lease expiry. Once PostgreSQL is available,
the run must commit a truthful terminal outcome within one execution-lease expiry
plus one maintenance interval. While PostgreSQL itself is unavailable, durable
run authority is unavailable and the system must not report success.

## Preserved Decisions

V2 retains these v1 decisions unchanged:

- Redis Streams provides bounded live replay, never durable business truth.
- PostgreSQL owns run authority, final answers, required semantic facts, and
  terminal publication intent.
- Browsers use independent `XREAD`, not consumer groups.
- Cursors bind run, monotonic stream incarnation, and native Redis ID; continuity
  loss yields an explicit gap and durable hydrate.
- PostgreSQL commits before terminal/end publication.
- The reducer advances its accepted cursor only after validating and committing
  the event, and final hydrate replaces the provisional live fold.
- Steady-state PostgreSQL/Redis text-delta double writing is prohibited.

## Compatibility And Rollback

V1 and v2 identities are never interchangeable. New v2 runs use
`redis_streams_v2`, `ai-platform.stream-event.v2`,
`ai-platform.stream-gap.v2`, and the `ai-platform:sse:v2:` key namespace.
Existing legacy or future v1-pinned rows remain on their immutable parser and
backend contract; retry, resume, and rollback never rewrite the pin.

Rollback disables v2 admission for new runs. Active v2 runs drain, safely pause,
or terminalize under v2, and the reader/hydrate/reconciler remain until their
recovery window and pending intents close. Rollback cannot reconstruct omitted
text deltas, reuse an incarnation, or reinterpret a v1 cursor as v2.

## Evidence Boundary

This ADR corrects source authority only. A local independent fixed-SHA review may
satisfy repository review policy when recorded with exact scope and findings;
an empty GitHub review state is not approval. Real PostgreSQL A1 evidence and F
real Redis/PostgreSQL, multi-instance revocation race, outage, browser, capacity,
and cleanup evidence remain separate gates. Nothing here authorizes merge,
deployment, 72/211 mutation, or a runtime claim.

The complete contract and stage RED requirements are in
[`../architecture/redis-streams-sse-event-channel.md`](../architecture/redis-streams-sse-event-channel.md).
