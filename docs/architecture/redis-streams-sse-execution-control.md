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

## Change Contract: Structured-run public commentary

- **Owner:** Execution's Claude SDK adapter owns selecting and sanitizing user-visible commentary; Streaming owns the versioned public event; the existing frontend adapter/reducer owns its work-activity presentation.
- **Bounded paths:** `app/execution/application/claude_agent_events.py`, `app/executors/claude_agent_sdk_runner.py`, `app/executors/claude/prompts.py`, `app/runtime/kernel_contracts.py`, `app/runtime/event_bridge.py`, `app/streaming/events.py`, `schemas/public_run_stream.v4.schema.json`, `tools/generate_sse_v4_contracts.py`, generated protocol files, `app/routes/lambchat_compat.py`, the existing frontend v4 adapter/reducer and work-activity renderer, their owning tests, and this contract.
- **Invariants:** `ResultMessage.structured_output` remains the sole terminal answer and deliverable authority. Commentary comes only from ordinary `TextBlock` values in a complete Assistant message that also contains a tool-use block; raw partial structured JSON, thinking/reasoning, tool arguments/results, paths, runtime approvals, and private identifiers remain excluded. Commentary passes the existing fail-closed public-text gate and is never appended to final assistant answer content.
- **Acceptance:** safe commentary is published before the terminal Result as schema-valid `commentary.delta`, delivered through the existing Redis Stream, rendered in the existing work-activity disclosure while the Run is active, restored through history, and collapsed with the other work activity after terminal convergence. A text-only structured-result message remains suppressed, and projection failure omits commentary without weakening terminal validation.
- **Regression proof:** adapter and runner tests distinguish tool-using commentary from text-only structured output and private content; the callback registry/bridge test proves the event reaches the durable v4 adapter while generic thinking remains rejected; generated-contract tests cover valid and invalid commentary frames; frontend adapter/reducer tests prove commentary becomes a stable summary part without changing assistant answer text; compatibility-history tests preserve the same public projection.
- **Retirement/compatibility:** the blanket suppression of every structured-mode Assistant `TextBlock` is replaced only for safe tool-using commentary. Structured terminal authority, legacy non-structured answer streaming, and all existing event types remain supported; no parallel transport or final-answer path is introduced. Because v4 rejects unknown events, Worker/API/frontend delivery of `commentary.delta` is release-atomic; an older frontend is not a compatibility target for a newer producer.
- **Stop conditions:** do not parse or publish partial structured-output JSON, classify thinking as commentary, expose arbitrary executor payloads, or append commentary to final answer content. Any broader intermediate-text source requires a revised contract and disclosure review.

## Change Contract: Compact terminal history hydration

- **Owner:** LambChat compatibility history projection owns lossless historical answer compaction; the frontend session API owns opting browser history reads into that projection.
- **Bounded paths:** `app/routes/lambchat_compat.py`, `frontend/web/src/services/api/session.ts`, their owning tests, and this contract.
- **Invariants:** SSE v4 bytes, Redis live/replay authority, Run/Attempt and authorization ownership, strict public projection, cross-chunk sanitization, terminal status, event ordering, and accepted sequence fences remain unchanged. Compaction occurs only for a terminal Run, after persisted-v4 validation, and only across answer deltas with the same public message identity and stream incarnation and no intervening public event.
- **Acceptance:** an opted-in terminal history read returns the same public assistant text with consecutive v4 answer deltas represented by one `message:chunk`; active Run history retains its existing incremental projection. The compacted chunk retains the last included delta's public identity, sequence, and timestamp so reconnect suppresses the omitted prefix without lowering its watermark. Existing clients that omit the query flag retain the uncompressed response.
- **Regression proof:** backend tests compare compacted and uncompressed public text, preserve public-event boundaries, and reject private or unauthorized rows; the frontend API test proves browser history requests opt in.
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

- **Owner:** Streaming execution-control owns the startup admission barrier; the existing LambChat compatibility route remains the HTTP/SSE adapter and the existing frontend retains its bounded retry fallback.
- **Bounded paths:** `app/routes/lambchat_compat.py`, this contract, and the owning SSE route tests. Worker admission, Redis publication, frontend protocol handling, cursor recovery, and terminal hydration are outside this change.
- **Invariants:** Run, Attempt, queue Worker lease, stream authority, authorization epoch, and SSE lease boundaries remain unchanged. Redis Streams remains the sole live/replay authority. A pending authority never receives an SSE lease, and no PostgreSQL transaction remains open across Redis I/O. SSE v4 bytes, stream incarnation, cursor semantics, and public event projection remain unchanged.
- **Acceptance:** an authorized SSE request encountering a missing or `admission_pending` authority waits in short independent transactions for a bounded startup window; once the existing authority becomes `confirmed`, `degraded`, or `terminal`, the route acquires the existing lease and enters the unchanged replay/live path. The observed admission-confirmation race no longer produces repeated client-visible 409 responses within that window.
- **Failure and compatibility:** a startup timeout returns the existing retryable 409 code with `Retry-After: 1`; terminal Runs without an authority remain non-retryable; malformed, revoked, unauthorized, lease, Redis, and protocol failures remain fail-closed. The previous immediate 409 behavior remains the bounded timeout fallback, not a retired protocol surface.
- **Regression proof:** tests cover confirmation during the wait, pending-authority timeout and `Retry-After`, slow lease acquisition at the deadline, missing or pending authority on a terminal Run, pending authority without a pending lease, and unchanged replay/live delivery after admission.
- **Stop conditions:** do not permit pending authority leases, add readiness events or a second control plane, poll PostgreSQL during frame delivery, or change Worker timing, Redis publication, SSE schema, cursor, incarnation, or terminal ownership.


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
