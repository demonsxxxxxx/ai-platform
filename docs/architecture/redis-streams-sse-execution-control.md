# Redis Streams SSE v4 Execution Control

Status: PG replay slice source contract for `ai-platform.redis-streams-sse-event-channel.v4`; candidate verification, CI and External Acceptance are separate evidence

Index: [Redis Streams SSE Event Channel](redis-streams-sse-event-channel.md)

## Scope

This document exclusively owns transaction-scoped stream admission, reuse of
current Run/Attempt/sandbox authority, direct committed-event publication,
authorization leases and revocation, durable replay recovery, and terminal
convergence. [ADR 0013](../adr/0013-redis-stream-only-sse.md) owns the hard-cut
decision and supersession, as amended by [ADR 0018](../adr/0018-postgres-replay-redis-live.md).

Earlier scoped change-contract invariants below describe what those changes
preserved at their landing. This PG replay slice supersedes their Redis-only
read/cursor restrictions, not their ownership, authorization or business rules.

## Platform phase progress producer

The executor's existing phase lifecycle owner emits fixed `agent.progress`
through the ordinary serialized callback/receipt path. It does not depend on
legacy public execution-step presentation tuples. A phase must start before
progress or terminal observations, may terminate only once, and never restarts
inside the same executor task. Rejected or uncertain callback delivery keeps the
existing fail-closed execution semantics.

The replaced phase-only legacy step publisher and its unused presentation
configuration are retired. Tool-step compatibility and persisted phase readers
remain unchanged. The orphan attachment-materialization completion is removed:
materialization already finished before executor admission, so this producer
cannot fabricate a start observation for it. Unused artifact phases remain
read-compatible wire values; no new preparation, validation or recovery work is
introduced. Callback identity, schema, Redis ordering, Run outcome authority and
provider ownership are unchanged. Local synthetic tests establish source
behavior only, not deployment or real provider acceptance.

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

<a id="change-contract-structured-run-public-commentary"></a>
## Current v4 public commentary and terminal text

Execution's Claude adapter selects and sanitizes public text. Streaming owns
the closed event schema and the frontend owns presentation. Ordinary chat uses main-Assistant native text increments, not a required
`structured_output.answer` object or Result body backfill. Callback-acknowledged
v4 rows and the validated answer receipt own stored content.
Runs owns business success. Optional files use `attach_file` and Artifact
validation independently of text; neither a JSON object nor prose creates an
artifact record.

The adapter enables native partial messages and gates each unique text suffix
before ordered `message.delta`. Completed blocks reconcile their own stream
without repeating text; ordinary narration and later answers stay in event
order. Thinking and raw Tool fields remain excluded. New output uses receipt
v1 over all public deltas; part/v2 and commentary/worktrace readers remain for
existing stored data. No committed event row is rewritten.

Native message/block identity coordinates partial and completed text, including
completed blocks before block stop. The gate retains only necessary sensitive
suffixes with their exact source ownership. Result never adds text. Every callback still passes
current Attempt/lease authority and exact receipt/ACK, with <=8192 code points
per part delta and <=100 events per batch. Tool completion evidence and Run
success remain independent. Invalid historical part facts and unauthorized history still fail closed.

The [streaming message design](../implementation/streaming-message-parts-design.md)
and [wire contract](redis-streams-sse-wire-protocol.md#change-contract-incremental-assistant-parts-and-final-selection)
own the closed event family, ordered text/v1 receipt, historical part/v2 readers,
stable UI order and release/rollback requirements. Keep part-capable readers
while stored part rows/v2 receipts exist. Classification-time publication and
text-driven fold phases are retired; regressions cover native increments before
completed blocks/Result, replay dedupe and consistent live/history recovery.

## Change Contract: Compact terminal history hydration

- **Owner:** The browser chat-session history projection (`app/routes/lambchat_compat.py`) owns lossless historical answer compaction; the frontend session API owns opting browser history reads into that projection.
- **Bounded paths:** `app/routes/lambchat_compat.py`, `frontend/web/src/services/api/session.ts`, their owning tests, and this contract.
- **Invariants:** SSE v4 bytes, Redis live/replay authority, Run/Attempt and authorization ownership, strict public projection, cross-chunk sanitization, terminal status, event ordering, and accepted sequence fences remain unchanged. Compaction occurs only for a terminal Run, after persisted-v4 validation, and only across answer deltas with the same public message identity and stream incarnation and no intervening public event.
- **Acceptance:** an opted-in terminal history read returns the same public assistant text with consecutive v4 answer deltas represented by one `message:chunk`; active Run history retains its existing incremental projection. The compacted chunk retains the last included delta's public identity, sequence, and timestamp so reconnect suppresses the omitted prefix without lowering its watermark. Existing clients that omit the query flag retain the uncompressed response.
- **Regression proof:** backend tests compare compacted and uncompressed public text, preserve public-event boundaries, and reject private or unauthorized rows; the frontend API test proves browser history requests opt in. Resumption tests cover retained cursors after `stream.open` is trimmed, including terminal/end cursors. Mounted frontend tests cover gaps before and after message ownership, preserving durable text through terminal convergence. Shared request tests cover timeout, retry, cancellation, and stale results.
- **Retirement/compatibility:** no production transport is retired. The uncompressed compatibility form remains the default for unidentified consumers; the browser switches to the additive compact form. Trace pagination, terminal message materialization, and a terminal history-completeness receipt remain out of scope.
- **Stop conditions:** do not compact across message identities or public events, bypass the strict public projector, infer terminal completeness from Run status alone, or remove exact terminal hydration without a durable same-snapshot completeness proof.

## Change Contract: Chat submission and SSE admission convergence

- **Owner:** Conversations application owns the read-only linked-Run status projection; the existing frontend SSE adapter/reducer and connection/reconciliation owner own startup conflict, history/replay correlation, and terminal convergence.
- **Bounded paths:** `app/conversations/application/submission_resolution.py`, `app/conversations/api.py`, the legacy Chat compatibility imports/delegation, `frontend/web/src/hooks/useAgent.ts`, `frontend/web/src/hooks/useAgent/eventHandlers.ts`, `frontend/web/src/hooks/useAgent/historyLoader.ts`, `frontend/web/src/hooks/useAgent/sseConnection.ts`, their owning tests, and this contract.
- **Invariants:** `chat_submissions.state` and `outcome.status` remain admission/ledger facts; `runs.status` remains the execution authority; linked Run lookup is scoped by tenant and user; missing or inaccessible Runs project as `run_status: null`; SSE wire bytes, Redis persistence, Worker authority timing, stream version, incarnation, accepted cursor/sequence, and current-owner fences remain unchanged.
- **Acceptance:** a submission response exposes an authorized normalized `run_status` for `queued`, `running`, `succeeded`, `failed`, and `cancelled` (including raw `canceled` normalization); explicit transient `sse_stream_not_admitted` and `sse_stream_not_confirmed` conflicts remain bounded and recoverable; history materialization may suppress replayed semantic duplicates, but the replayed `message.started` restores the current protocol-to-reducer message owner before correlated frames are accepted, even when a later public history event has already raised the accepted sequence; earlier authorized replay frames advance only the transport cursor and never duplicate the history reducer state or lower its sequence, while later frames continue normally; a `cancel_requested` history restore may retain the Run as pending cancellation authority but must not mark the historical assistant streaming or expose reconnect/skeleton UI when no live stream is eligible; a temporarily unavailable authority read pauses recovery without clearing the Run, assistant, cursor, or sequence when the bound assistant still exists; an orphaned assistant pointer cannot resume or schedule reconnect; terminal Runs hydrate without further reconnect; explicit non-retryable startup conflicts, status-read or stream authentication failures, invalid public frames, conflicting message identities, and missing active-Run hydration fail closed and settle local loading/streaming state.
- **Regression proof:** owning tests cover linked status projection, missing/cross-principal Run fencing, retryable 409 recovery, history-to-replay message-owner restoration across route unmount/remount and a higher history sequence, cancel-requested history rendering without streaming/reconnect presentation, conflicting duplicate message identity rejection, recoverable unavailable status reads at history/reconnect/gap boundaries, fail-closed status authentication and missing-assistant recovery, terminal convergence, bounded active retry exhaustion, non-retryable conflicts, invalid JSON/missing event ID/invalid V4 envelope, stale callbacks, and the absence of lingering reconnect work.
- **Retirement/compatibility:** two routed tests that injected obsolete `event: error` frames and their single-use helper are retired; the current v4 invalid-frame, ordinary transport-interruption, status-continuation fencing, and gap tests remain the owning checks. The stale assertion that temporary status unavailability terminalizes an otherwise active Run is replaced by the recoverable-owner assertion; the cancel-requested history test now proves that pending cancellation retains authority without streaming/reconnect presentation. No superseded production transport or documentation surface is in scope; the existing v4 handler remains the single live/replay/history reducer boundary.
- **Stop conditions:** do not treat admission `queued` as an active Run; do not retry arbitrary 409s or protocol-invalid frames; any change to Worker authority timing, SSE wire/schema, Redis/persistence semantics, or cross-incarnation successor recovery requires a revised contract and independent acceptance.

## Change Contract: bounded SSE admission barrier

- **Owner:** Streaming execution-control owns the startup admission barrier; the browser chat-session API (`app/routes/lambchat_compat.py`) remains the HTTP/SSE adapter and the existing frontend retains its bounded retry fallback.
- **Bounded paths:** `app/routes/lambchat_compat.py`, this contract, and the owning SSE route tests. Worker admission, Redis publication, frontend protocol handling, cursor recovery, and terminal hydration are outside this change.
- **Invariants:** Run, Attempt, queue Worker lease, stream authority, authorization epoch, and SSE lease boundaries remain unchanged. The original barrier change preserved Redis-only replay; ADR 0018 replaces only that read restriction. A pending authority never receives an SSE lease, and no PostgreSQL transaction remains open across Redis I/O. SSE v4 bytes, stream incarnation, cursor semantics, and public event projection remain unchanged.
- **Acceptance:** an authorized SSE request encountering a missing or `admission_pending` authority waits in short independent transactions for a bounded startup window; once the existing authority becomes `confirmed`, `degraded`, or `terminal`, the route acquires the existing lease and enters the unchanged replay/live path. The observed admission-confirmation race no longer produces repeated client-visible 409 responses within that window.
- **Failure and compatibility:** a startup timeout returns the existing retryable 409 code with `Retry-After: 1`; a completed locked lookup that finds no authority remains `sse_stream_not_admitted`, while pending authority or a locked admission read that reaches the startup deadline remains `sse_stream_not_confirmed`; terminal Runs without an authority remain non-retryable; malformed, revoked, unauthorized, lease, Redis, and protocol failures remain fail-closed. The previous immediate 409 behavior remains the bounded timeout fallback, not a retired protocol surface.
- **Regression proof:** tests cover confirmation during the wait, pending-authority timeout and `Retry-After`, slow lease acquisition at the deadline, missing or pending authority on a terminal Run, pending authority without a pending lease, and unchanged replay/live delivery after admission.
- **Stop conditions:** do not permit pending authority leases, add readiness events or a second control plane, or change Worker timing, Redis publication, SSE schema, cursor, incarnation, or terminal ownership.


The implementation begins from current durable authority rather than introducing
a parallel execution state machine:

- the Run row owns lifecycle and truthful terminal state;
- the current Attempt and active sandbox runtime lease fence executor callbacks;
- callback tokens bind Run/Attempt/owner generation; the active tenant-scoped
  Sandbox lease and current Attempt must match that generation before receipt;
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
`run_events` plus the callback receipt in one transaction. Each private/public
group reserves one contiguous sequence range and uses one bulk insert; existing
public event identities are looked up together for exact retry validation. Unknown, private, or
malformed SDK values do not become public rows.

After that transaction commits, the callback attempts the canonical Redis batch
append under a 250 ms timeout. Redis transport failure or timeout is logged and
still returns the committed receipt; acknowledgement does not require successful
Redis append. This remains a bounded synchronous attempt, not zero-wait ACK or
a detached background task. Cancellation (`CancelledError`), projection,
authority, immutable receipt conflicts, business and database exceptions retain
their failure semantics and are never converted to success. No PostgreSQL lock
is held across Redis I/O.

The executor buffer retains ordered exact acknowledgements, tool barriers and
single-flight supervisor heartbeats. A lost HTTP response retries the same
immutable callback; PG batch/source digest and Redis batch receipts retain
idempotency. Callback rows gain no publication claim, retry counter or
pending/published disposition. There is no background retry scheduler.

Callback predecessor lookup and republishing are retired. PG replay supplies
ordered committed facts even when their Redis append did not succeed.
Run-terminal and cancellation publishers still use committed business facts
directly; their separate receipts are retained and are not overwritten by
callback receipts. Business terminal and usage idempotency remain unchanged.

## Committed public-event producer

All closed v4 Agent-kernel application types use the one generated schema and
strict event-specific projector. Message, thinking-state, model, tool,
subagent, artifact, policy, cancellation, and Run-terminal events are ordered by
the committed Run-local `seq`; transport controls consume no business sequence.

Raw SDK values, execution commands, tool arguments/results, hidden reasoning,
private runtime paths, credentials, runtime approval payloads, and
executor-selected arbitrary activity labels never enter canonical public bytes.
Intentional Assistant prose follows the Chat content policy. Engine-specific
values terminate at the adapter boundary.

## Publication bounds and backpressure

The executor retains its bounded ordered buffer and exact-batch receipt barrier.
PG replay scans bounded 100-row pages through a captured Run high-water; private
rows advance the scan without becoming output. Public and browser-consumed
positions are separate. Redis live reads remain bounded to 128 entries and five
seconds; empty reads, jumps and unavailable transport trigger PG catch-up, with
a one-second minimum retry cycle while Redis is unavailable. Strict validated
continuous live frames may use the scanned-sequence fast path.

The [wire contract](redis-streams-sse-wire-protocol.md#replay-and-blocking-reads)
owns the exact cursor/control framing. No publication queue, retry scheduler,
shared subscriber hub or successor builder is introduced. Redis retention bounds
remain transport capacity limits, not public replay retention guarantees.

## Authorization lease

PostgreSQL owns a positive monotonic authorization epoch for the principal,
tenant, workspace/session, and run scope. Each SSE connection obtains a lease at
open and renews it before expiry. The lease binds:

- principal and tenant/workspace/session/run ownership;
- current stream incarnation and authorization epoch;
- API instance and connection identity;
- `lease_not_after`, calculated from the authority clock and no more than 15
  seconds after issue.

PostgreSQL is queried on connection establishment, renewal, authority state
transitions and bounded durable replay pages. Authorization is not re-queried
for every payload while the issued lease remains valid. Lease acquisition validates the durable
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

Redis unavailability before confirmed stream admission still prevents SDK
dispatch. New Run admission retains PG pending authority, Redis exact-open
confirmation and PG confirmation before execution. This slice does not replace
the Redis queue or make new Runs Redis-independent.

An already admitted Run can open a **new** SSE connection without a retained
Redis stream. Its validated open comes from confirmed durable authority; PG
replay recovers committed text and a terminal event derives its linked id-less
end. No authority is fabricated for an unadmitted Run. Authorization, current
Attempt/incarnation and per-connection lease checks remain mandatory.

Redis loss after dispatch affects live latency, not replay completeness. Callback
ACK follows the post-PG, bounded best-effort semantics above. PG read
or authorization failure closes fail closed without inventing progress. No
publisher recreates the missing stream or allocates a successor.

## Committed Run terminal publication

Existing Run/Attempt/Worker owners commit truthful terminal state, final answers
and audit facts under existing transaction fences. Run-local sequence locking
orders these facts with callbacks. The PG reader observes the ordered terminal
projection and deterministically derives `stream.end` with that terminal's
identity/causation. It does not require Redis terminal/end receipts to close SSE.
A reconnect at an accepted terminal sequence derives the same end without
reapplying terminal content. A rolled-back transaction produces neither fact.

The retained direct Redis publisher may use its existing bounded receipts and
callback prefix checks. Those are live-transport checks, not browser recovery
or business terminal authority. Failure cannot undo committed Run status or
suppress the stored answer. Authorized terminal hydration remains separate from
stream completion. No background retry or terminal publication ledger is added.

## PG-commit ACK acceptance

The read-path gate precedes the ACK change. Owning verification must prove PG
rollback cannot ACK or publish, Redis failure/timeout can ACK committed receipts,
and lost-response retries preserve event and receipt identity. Record actual
fixed-source local, CI and deployed results in the PR rather than treating this
contract as execution evidence. Independent review and release acceptance remain
separate requirements.

Normal SSE completion requires an actually projected terminal and linked end;
observing terminal Run status alone cannot close a connection before its final
PG catch-up. Cross-incarnation message-owner migration is out of scope; legacy
cursor snapshot recovery preserves the current-incarnation owner fences.

## Failure matrix

| Scenario | Required behavior |
| --- | --- |
| Redis new-Run admission unavailable | zero SDK calls; fail closed |
| already admitted Run, Redis absent | new SSE opens from confirmed authority; bounded PG replay |
| callback HTTP response lost | exact-batch retry; no new semantic IDs |
| duplicate callback with changed content | conflict; no publish |
| callback commit followed by Redis failure | committed facts remain replayable; transport failure/timeout after the bounded attempt does not fail ACK |
| Run event races callback | durable sequence/commit order; no public `+1` assumption |
| publication after captured PG high-water | next catch-up captures a later bound |
| Redis read failure | bounded PG catch-up; no invented gap or cursor |
| required PG history absent or malformed | fail closed; no invented retention floor |
| PostgreSQL terminal rollback | no terminal/end; no success claim |
| PostgreSQL terminal commit then Redis failure | PG terminal replay derives end; authorized hydration |
| terminal cursor reconnect | matching id-less end; no duplicate terminal reduction |
| authorization renewal fails | close fail closed before the next payload |
| epoch commits during blocked read | renewal denied; <=15-second lease deadline retained |
| frame admitted before revocation commit | handed-off bytes may arrive; no browser-byte guarantee |
| callback after terminal closure | reject under existing business fences |

## Required focused tests

- pending/unconfirmed admission, stale Attempt ownership and zero SDK calls before confirmation;
- confirmed authority open and new SSE while Redis is missing/unavailable;
- fixed-high-water paging, private-only pages, public holes, exact authorized projection and no DB locks across network I/O;
- Redis fast-path fences, empty-read/jump fallback and bounded unavailable retry;
- durable and legacy cursor handling, wrong Run/future/malformed cursors and consumed history snapshot handoff;
- authorization open/renewal, per-frame deadlines, blocked-read expiry and fail-closed DB errors;
- terminal replay/end linkage and terminal-cursor reconnect without duplicate body;
- callback atomicity, exact retry/conflict, ordered barriers and ACK transport-only failure handling;
- real PostgreSQL/Redis, frontend mounted recovery, CI and packaged s72 acceptance recorded separately.

## Convergence proposals and evidence

The [runtime convergence proposal](runtime-convergence.md) retains only proposals
compatible with this contract. PostgreSQL publication schedulers, wakeups and
successor work are superseded by ADR 0013. Business executor reconciliation and
resource cleanup remain independent owners. Source, local tests, CI, packaged
images and External Acceptance are separate evidence levels.
