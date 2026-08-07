---
status: accepted
supersedes: 0002-redis-streams-sse-event-channel.md
---

# Correct Redis Streams SSE staging, revocation fencing, and degraded transport

Design ID: `ai-platform.redis-streams-sse-event-channel.v2`

Source baseline: `046d4b8a91d70dac51fe31d517d8d09c907a3f9f`

## Context

ADR 0002 selected the correct storage and replay split but left five unsafe
implementation interpretations:

1. Its A-F graph allowed a pure StreamBridge stage before the PostgreSQL
   admission authority that must allocate the backend/design pin, attempt,
   generation, and monotonic stream incarnation.
2. Its revocation language treated an authorization transaction commit as if it
   could be atomic with every cross-instance network write.
3. Its mid-run Redis failure policy treated live-transport loss as automatic
   execution failure and did not define success/failure/cancel convergence when
   terminal publication fails or is uncertain.
4. Its correction placed executor dispatch fencing inside A1 without assigning
   the token protocol, durable execution ledger, gateway lookup, executor
   integration, and SDK-loss semantics to an implementation stage.
5. A compatibility reading could leave the PostgreSQL polling/delta runtime
   beside Redis indefinitely. V2 instead selects one hard-cutover runtime and
   preserves business data rather than the old streaming mechanism.

The v1 design ID was publicly accepted. Reinterpreting it in place would make
deployed/configured identity ambiguous, so this correction uses a new v2 design
ID, envelope/gap schema identity, backend pin, and Redis key namespace.

## Decision

### Implementation dependency

The only allowed order is:

`A0 pure envelope/cursor/StreamBridge contract -> A1 PostgreSQL admission and revocation authority -> A2 durable executor-dispatch authority -> B producer/coalescer -> C SSE reader -> D terminal convergence/intent and stop-PG-delta policy -> E frontend -> F real acceptance`

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
run/attempt/incarnation/generation/winning owner epoch. Zero updated rows is a
fenced result; confirmation without the intent rolls back. A1 also owns the
PostgreSQL authorization scope/epoch, requested/committed/effective revocation
state, API-instance registration and acknowledgement, database-clock send leases
and barrier, and the query used to decide current send authority. C consumes
those interfaces but cannot add schema or substitute process memory or Redis.

A2 is mandatory before B. It owns the token-aware `ExecutorTaskRequest`, a
PostgreSQL execution ledger and status/handle lookup, platform gateway claim and
lookup endpoints, sandbox runtime/client/executor integration, worker dispatch,
and the Claude SDK start boundary. The immutable dispatch token and execution
identity bind design, tenant/session/run, attempt, incarnation, generation, and
the winning `admission_owner_epoch`. Every path must acquire or return that ledger
record before accepting an executor request; a stale owner or mismatched binding
is fenced.

The installed Claude SDK accepts a `session_id`, but the current boundary exposes
no durable idempotency key or resumable in-flight handle. V2 therefore promises
at most one SDK start, not successful same-handle resumption. A live executor
instance may look up its own accepted token/status after a lost response; a
different or restarted executor cannot inherit a start authorization. If the
executor is lost before or after SDK start, the durable ledger converges to
`execution_lost` or a truthful failed terminal state and no recovery path starts
a new SDK query for that token.

An open failure or unknown result never authorizes dispatch. The owner retries
the same token; after lease expiry a maintenance owner takes over with a higher
fence. The normative RED set includes a delayed old-owner response after takeover,
unknown XADD with a two-owner retry, and a crash after confirm commit but before
dispatch. Each must prove stale CAS rejection and one confirmation/dispatch
intent. A2 additionally covers executor accept then response loss, retry and
duplicate token, restart before and after SDK start, stale owner, and durable
status/handle lookup. Each proves at most one SDK start and explicit
`execution_lost` rather than invented resumption. Within one admission-
lease expiry plus one maintenance interval after PostgreSQL and Redis are
available, recovery must either confirm that same open or commit a truthful pre-
dispatch admission failure. Once D is present, D owns any corresponding
publication intent. Recovery cannot leave the run permanently running or allocate
a replacement incarnation to hide ambiguity.

A1 must merge and pass an isolated real-PostgreSQL multi-connection gate for both
admission and revocation authority. A2 must then merge and pass its durable-ledger
and executor-protocol gate before B may produce events or dispatch any Redis-
pinned run. A missing gate is evidence blocked, not a pass. Production remains
disabled until the later stages and F acceptance complete.

Frozen Slice A candidate
`b6f3c0878c5c68358e57664174828b7404959a84` is not implementation authority.
It is discarded for v2 and cannot be reused as A0, A1, A2, or any later-stage
authority.

### Hard cutover ownership

The A0-A2+B-E implementation set is release-atomic and produces one live SSE
runtime. A0 defines the v2 Redis cursor/gap contract; A1 creates the only active
stream/admission/revocation authority; A2 creates the only executor-dispatch
authority; B removes PostgreSQL `assistant_delta` writes from worker and runtime-
callback production; C replaces the existing Chat stream URL's poll/sleep/fold
body with XREAD; D removes remaining live PostgreSQL cursor/page/terminal paths
and introduces the cumulative cutover checker with its backend scope; E removes
frontend invented-ID and PostgreSQL status/history reconnect fallbacks and extends
that checker to its full scope; F reruns the full scope against the exact accepted
source/image. D does not require E-owned frontend deletion before E exists.
Intermediate slices cannot be deployed as a compatibility stack, and no feature
flag can run both mechanisms.

### Revocation fencing

Authorization authority maintains a positive monotonic `authorization_epoch` for
the affected authorization scope. Every SSE connection and send lease binds one
exact epoch and a durable `lease_not_after` of at most 15 seconds. Before every
payload write, the instance derives a cancellable
`transport_completion_not_after` no later than that lease deadline and tracks the
write in its connection-local in-flight registry.

A1 implements this authority in PostgreSQL: the scope/epoch and state row, API
instance incarnation and database-clock registration lease, old-epoch send-lease
set with per-lease transport completion fences, revocation acknowledgements, an
immutable `revocation_deadline`, and a monotonically fenced barrier owner are
durable. Request/commit, acknowledgement, takeover, and effective transition use
row locks and database time. A stale epoch, stale instance incarnation, stale
acknowledgement, expired barrier owner, or authority error fails closed. An API
instance joining after commit can acquire only the new epoch and cannot extend or
ack the old barrier. Barrier-owner takeover inherits the original `committed_at`,
old-writer snapshot, completion fences, and deadline; it never restarts the clock.

`app.auth_sessions.AuthOperation` and its default 90-second Redis operation lease
serialize browser auth-context mutation only. Redis expiry, auth-context epoch,
or a process-local cache is not PostgreSQL SSE revocation authority and cannot
complete, extend, or bypass this barrier.

Revocation has three externally meaningful states:

- `requested`: the change has not committed and the old epoch remains reported
  authority; the API reports `access_revocation_requested`;
- `committed`: the new epoch is durable, old-epoch renewal is denied, and
  cross-instance invalidation/ack is in progress; the API reports
  `access_revocation_pending`;
- `effective`: every old-epoch writer has acknowledged only after all of its
  in-flight writes completed or were cancelled and its writer/socket closed, or
  the durable expiry fence proves that every old write has reached that transport-
  terminal state. The API reports `access_revoked`. Zero payload is guaranteed
  only after this barrier.

Each API instance applies invalidation, cancels blocked `XREAD`, closes every
old-epoch writer, cancels every outstanding socket send at its durable transport
deadline, and acknowledges quiescence only after awaited send completion or
cancellation and socket closure. Merely observing authorization-lease or instance-
registration expiry is not quiescence. The expiry path uses the latest durable
old-epoch transport completion fence and forced close evidence; it cannot advance
`effective` while an old write could still complete. The immutable barrier
deadline is no later than `committed_at + 15 seconds`, and send cancellation,
maintenance, or barrier-owner takeover must finish inside that same budget without
extending it. Authority errors and renewal attempts fail closed.

The durable fence is lease/control metadata, not a PostgreSQL event or delta per
payload. V2 retains the prohibition on steady-state PostgreSQL/Redis text dual-
write; the instance-local in-flight registry exists only to drain before ack.

The SSE writer rechecks its epoch immediately before each payload write. A write
that checked epoch e may race an e+1 commit, but it may finish only before the
effective barrier and no later than its cancellable transport completion deadline.
V2 measures and bounds that race rather than promising impossible commit-time zero
frames. Stage F must inject a slow socket and instance-loss/invalidation during
this exact check/commit/write race: acknowledgement and effective must remain
blocked until the write completes or is cancelled and the socket closes, takeover
must preserve the original deadline, the committed-to-effective interval must stay
at or below 15 seconds, and zero payload may complete after the recorded barrier.

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

## Hard Cutover, Data Retention, And Rollback

ADR 0002 is superseded and amended by this ADR. Its v1 text remains decision
history only; it is not a runnable parser, backend, feature flag, or fallback.
ADR 0003 and the architecture document are the only implementation target.

The final source has one live SSE mechanism: `redis_streams_v2`,
`ai-platform.stream-event.v2`, `ai-platform.stream-gap.v2`, and the
`ai-platform:sse:v2:` key namespace. The existing public Chat stream URL may stay
stable, but its PostgreSQL polling loop, one-second sleep, sequence cursor,
compatibility fold/aliases, and frontend status-poll fallback are replaced, not
proxied. Worker and runtime-callback assistant deltas stop writing PostgreSQL.
There is no production dual-write, dual cursor, dual terminal authority,
`postgres_legacy`/shadow live mode, or memory fallback.

Historical PostgreSQL event rows may remain for audit and retention. Old chats
hydrate from durable session/message/final-answer/tool/approval/artifact/audit
facts; they do not require live replay of historical per-delta aliases. If an
audit finds old terminal chats without a durable final answer, a separately
reviewed offline idempotent migration may backfill only that final business fact.
It is finite, admits no live traffic, exits when the eligible missing-final count
is zero, records counts/checksum, and is removed or disabled before cutover. It
does not copy deltas into Redis or leave a runtime adapter.

Deployment rollback selects a previous immutable image against a backward-
compatible schema; the current image never contains two switchable streaming
stacks. Before image rollback, active v2 runs drain, safely pause, or terminalize,
and pending publication intents close or remain owned by the v2 recovery image.
An older image cannot resume a v2 cursor/execution or reinterpret its incarnation.
Rollback cannot reconstruct omitted text deltas or authorize a hidden current-
image PostgreSQL poller.

## Evidence Boundary

This ADR corrects source authority only. A local independent fixed-SHA review may
satisfy repository review policy when recorded with exact scope and findings;
an empty GitHub review state is not approval. Real PostgreSQL A1 evidence, the A2
ledger/executor protocol gate, and F real Redis/PostgreSQL, multi-instance
revocation race, outage, browser, capacity, and cleanup evidence remain separate
gates. Nothing here authorizes merge, deployment, 72/211 mutation, or a runtime
claim.

The complete contract and stage RED requirements are in
[`../architecture/redis-streams-sse-event-channel.md`](../architecture/redis-streams-sse-event-channel.md).
