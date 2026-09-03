# Redis Streams SSE Event Channel v4

Status: normative source contract implemented by the v4 hard cutover; External
Acceptance pending

Design ID: `ai-platform.redis-streams-sse-event-channel.v4`

Decision: [ADR 0012](../adr/0012-recoverable-agent-kernel-event-stream-v4.md)

Source baseline: Issue #1187 fixed cutover SHA

## Authority and precedence

This file is the sole index for the active SSE v4 contract. It defines the
component boundary and points to exactly one normative owner for every detailed
rule:

| Concern | Normative owner |
| --- | --- |
| Decision, rationale, supersession | [ADR 0012](../adr/0012-recoverable-agent-kernel-event-stream-v4.md) |
| Envelopes, callback boundary, Redis replay/live, cursor/gap, SSE frames, frontend acceptance | [Wire protocol](redis-streams-sse-wire-protocol.md) |
| Admission, publication claims, authorization leases, cancellation, recovery, successor activation | [Execution control](redis-streams-sse-execution-control.md) |
| Release-atomic cutover, Nginx/gateway contract, checks, service matrix, External Acceptance | [Cutover and acceptance](../operations/redis-streams-sse-cutover-acceptance.md) |

ADR 0009 is retained as historical context for the Redis replay and live fan-
out transport. ADR 0004, ADR 0003, and ADR 0002 are superseded audit history;
none is a fallback implementation, feature flag, parser, or deployment option.
When a summary here differs from a detailed owner, the detailed owner is
normative.

## Outcome

The active path is:

`Claude SDK -> platform adapter -> committed public run_event -> publication claim -> transaction-external atomic Redis append/publish -> process-local API fan-out -> v4 frontend adapter/reducer`

PostgreSQL remains authoritative for Run/session state, attempts, final answers,
tool and artifact facts, audit, callback receipts, stream authority,
authorization epoch, committed public-event order, publication disposition,
and successor-rebuild claims. Redis Streams is the bounded replay plane;
Pub/Sub is a lossy live notification plane; neither is permanent business
truth.

There is one public Chat stream URL and one active v4 runtime. Each API process
owns one shared Redis Pub/Sub connection and one logical channel subscription
per active Run stream, not per browser. Attach subscribes before it captures and
replays Stream history, discards overlap, and then enters live fan-out. Missing
terminal history is rebuilt into an inactive successor incarnation and becomes
authoritative only after PostgreSQL verifies the complete snapshot and
persisted Redis receipt. There is no v3 negotiation, shadow runtime, or
PostgreSQL text-delta fallback.

## Component architecture

```mermaid
flowchart LR
    U["Authorized Chat run"] --> A["Run and current Attempt authority"]
    A -->|"prepare stream authority in transaction"| PG[("PostgreSQL")]
    A --> X["Sandbox executor and Claude SDK"]
    X --> C["Platform-owned callback adapter"]
    C -->|"commit canonical public run_events"| PG
    PG --> P["Claim-fenced durable publisher"]
    P -->|"no PostgreSQL locks during Redis I/O"| R[("Per-run Redis Stream")]
    P --> PS[("Redis Pub/Sub live notification")]
    R -->|"bounded replay"| S["Process-local Stream hub"]
    PS -->|"one logical subscription per active stream"| S
    S -->|"strict v4 public projection"| F["Frontend adapter and reducer"]
    PG --> H["Authorized durable hydrate"]
    H --> F
    PG --> B["Inactive successor rebuild"]
    B -->|"receipt-fenced CAS activation"| R
```

The Harness boundary remains intact. Engine-specific SDK objects and hidden
events terminate inside the engine adapter. Routes, Redis envelopes, callback
receipts, public projections, and reducers are platform-owned contracts.

## Existing authority reuse

Current main has durable Run/Attempt transitions, callback-token binding, an
exact active sandbox runtime lease, queue/worker ownership fences, callback
receipts, stream authority, authorization epochs, and terminal ownership. V4
reuses those authorities and adds durable public-event publication and fenced
successor recovery. It does not create a parallel Run or terminal state machine.

A new execution ledger requires a failing test proving an unrepresentable
at-most-once property and a separately reviewed authority change. An SDK
`session_id`, Redis key, process-local flag, or response cache is never dispatch
authority.

## Invariants

- Redis admission is proven before SDK dispatch. Admission failure or uncertainty
  that cannot be resolved by deterministic same-identity retry produces zero SDK
  calls.
- Projection, validation, and disclosure filtering happen before a public event
  is committed. Hidden reasoning, raw commands/tool payloads, credentials,
  paths, and runtime approval events never enter the canonical public envelope.
- One JSON Schema is the v4 wire authority. Checked-in Python and TypeScript
  artifacts are generated from it and CI rejects drift or handwritten duplicate
  protocol definitions.
- Publication claims commit before Redis I/O. `XADD`, active/terminal TTL
  refresh, and `PUBLISH` occur in one Lua operation; claim-token-fenced
  disposition accepts only the exact nonempty persisted receipt.
- Each API process multiplexes active Run channels over one Pub/Sub connection.
  Each browser has bounded event and byte queues; overflow or feed uncertainty
  closes without advancing its accepted cursor.
- Attach subscribes before replay. It captures a Stream tail, replays through
  that tail, discards buffered overlap by native Redis ID and semantic event ID,
  then drains later buffered events in order.
- Each committed public event has stable source order and semantic identity.
  Duplicate callbacks, publication retries, and disposition retries reuse the
  same canonical bytes and Redis receipt rather than creating another semantic
  event.
- Cursor identity binds the authorized Run, positive stream incarnation, and
  native Redis ID. Malformed, foreign, and future cursors fail closed. Trim or
  unprovable continuity emits one strict v4 `stream.gap` control with the
  server-supplied Redis cursor and triggers durable hydrate. A missing terminal
  stream uses successor-incarnation rebuild before replay; same-incarnation
  reconstruction is forbidden.
- The reducer sends and advances only the last cursor whose event was validated
  and successfully committed to client state. Semantic duplicates may advance
  transport state without mutating chat state. Terminal and matching
  `stream.end` cursors wait for terminal-hydration acceptance.
- Connection/renewal performs the PostgreSQL authority lookup. Lease acquisition
  validates the durable epoch; each payload checks the cached authority-clock
  deadline, and PostgreSQL is not read per frame. A committed epoch change
  rejects renewal, so old-epoch admission ends no later than the <=15-second
  lease deadline.
- ASGI handoff is not browser receipt. Revocation guarantees no new application
  frame after the old lease deadline; real proxy behavior is externally
  measured.
- Active appends atomically refresh active-idle TTL. Terminal/`stream.end`
  atomically set terminal replay TTL. Creation time alone does not expire a
  stream while it emits accepted events within the active-idle window.
- Terminal public rows and `stream.end` are committed under the existing Runs
  terminal authority before transaction-external Redis publication. Final
  hydrate remains the sole chat terminal-content authority.
- Redis failure never creates unbounded memory or PostgreSQL text-delta
  fallback. Retry maintenance drains indexed pending rows; recovery uses one
  inactive successor and token-fenced activation.
- Producer, publisher, recovery activation, route, frontend, workflow, checker,
  and release documentation form one release-atomic v4 set.

## User-visible sequences

### Normal and reconnect

1. The authorized attempt prepares stream authority in the same transaction
   before any public or terminal event can commit.
2. Safe canonical events commit to PostgreSQL and are claimed in business order;
   Redis append/publish runs after the claim transaction releases its locks.
3. The gateway authorizes the exact Run, attaches a bounded local subscriber,
   and waits for shared Redis subscription acknowledgement.
4. It validates the cursor, captures the retained tail, replays through that
   tail, discards buffered overlap, and emits later live notifications.
5. The browser validates and commits an event before storing its Redis-backed
   cursor. Legitimate semantic duplicates are transport-only acceptance.
6. Reconnect sends that exact cursor in `Last-Event-ID`; replay returns only
   later retained entries before live fan-out resumes.

### Gap

1. The server authorizes the Run and validates the whole cursor before Redis
   access.
2. Trim or continuity uncertainty produces a strict v4 `stream.gap` control
   with the current incarnation and server-owned Redis bounds.
3. The browser does not mutate chat state for the gap and calls authorized
   durable hydrate.
4. A missing terminal stream claims a fresh successor incarnation, constructs
   an inactive Redis candidate outside PostgreSQL locks, and activates it only
   after source fingerprint, cardinality, claim expiry, and receipt rechecks.
5. Replay restarts on the activated successor. The browser never invents a
   cursor or crosses incarnation state.

### Terminal

1. The Runs owner commits the truthful terminal state and canonical terminal
   public row under its existing transaction and lock order.
2. Durable publication claims and appends the terminal row plus matching
   `stream.end` with stable bytes, semantic IDs, and one persisted receipt.
3. Unknown Redis or disposition outcomes retry identically; no PostgreSQL lock
   is held across Redis I/O.
4. If retained terminal history is missing, successor recovery rebuilds and
   atomically activates a fresh incarnation rather than rewriting the old one.
5. The frontend holds terminal and matching `stream.end` cursors until the
   existing final hydrate callback accepts the authoritative terminal state.

## Evidence boundary

Source inspection and focused tests can prove contract shape, deterministic
identity, attach overlap handling, bounded subscribers, fail-closed branches,
cleanup, and injected fault behavior. They do not prove deployment,
Nginx/browser byte delivery, Redis Pub/Sub behavior across real API replicas, or
50-concurrent-run capacity. Those claims require the exact runtime evidence
listed in the cutover and acceptance document.

## Change Contract: progressive public Run timeline

- **Owner:** Streaming owns committed public-event order; the Engine adapter owns
  SDK normalization; Execution owns capability evidence and Run success; the
  frontend reducer owns applied sequence and cursor acceptance.
- **Bounded paths:** `app/executors/claude_agent_sdk_runner.py`,
  `app/executors/public_answer_stream.py`, the existing Claude public-event
  adapter, the durable Chat history projector, `frontend/web/src/hooks/useAgent/`,
  this document, ADR 0012, and their focused tests. The Redis envelope, key,
  cursor, publication-claim, authorization, and Run terminal authorities are
  unchanged.
- **Public timeline invariant:** a disclosure-safe Assistant text prefix outside
  an active Tool invocation becomes a durable `message.delta` without waiting
  for an `AssistantMessage`, tool completion, or `ResultMessage`. Tool
  authorization does not disable the SDK stream projector. The exact
  `PreToolUse` to acknowledged terminal-hook interval remains closed for every
  admitted read-only or effectful Tool so anomalous in-flight SDK text cannot
  expose raw Tool output. A terminal receipt reopens only the exact matching
  capability kind, canonical identity, and invocation ID; unrelated or duplicate
  terminal receipts fail closed. An exact producer-attributed policy rejection
  commits `tool.denied` and projects as a denied Tool with blocked/permission
  semantics; aggregate admission failures never synthesize Tool identity.
  `message.delta` is provisional user-visible narration; it is never evidence
  that a capability ran or that a Run succeeded.
- **Safety invariant:** hidden reasoning, raw tool input or output, commands,
  paths, credentials, storage keys, private Tool, Skill, MCP, task, Attempt, and
  stream identities remain prohibited. Exact invocation-interval text is not a
  public Assistant source. Stateful cross-chunk sanitization, cumulative bounds,
  strict callback validation, tool admission, capability receipts, and
  platform-owned terminalization remain fail closed.
- **Ordering invariant:** public Tool lifecycle events bracket the actual
  invocation. A start commits before execution; a completion or failure commits
  only after its verified receipt. Subsequent Assistant text commits later in
  the same PostgreSQL Run-local sequence. The frontend may coalesce only
  adjacent text and may advance sequence or cursor only after reducer or
  terminal-hydration acceptance.
- **Gap recovery invariant:** active same-incarnation
  `retained_history_unavailable` and `stream_continuity_unproven` gaps resume
  only after PostgreSQL V4 hydration has applied and only from the
  server-provided latest retained cursor. Durable history owns state through
  that anchor; Redis replay/live owns later events. `stream_missing`,
  cross-incarnation recovery without a validated current anchor, and active
  successor-incarnation creation remain terminal-only recovery until the
  Run/Attempt owner defines a separate active-successor contract; the frontend
  never synthesizes one.
- **Single-body invariant:** V4 `message.delta` is the public incremental body
  authority. Complete SDK messages are fallback or reconciliation inputs. A
  successful Result body must byte-exactly extend the selected streamed or
  complete Assistant body; only its unsent suffix may enter the same stateful
  public gate, and any conflict fails closed. Terminal results cannot duplicate
  already committed text. Failed and cancelled durable history reconstructs the
  same accepted V4 body and never trusts an unmanaged metadata marker as
  publication authority.
- **Acceptance:** focused tests prove text-only, read-only Tool, effectful local
  Tool, Skill, MCP, sequential capability, denial/failure, terminal race,
  reconnect, and failed-history behavior. Tests delay both animation-frame and
  React functional-updater execution where ordering depends on application.
  Serialized ordinary-user responses contain no internal marker or identity.
- **Falsifiable regression proof:** an effectful local Tool Run emits a safe
  `message.delta` before `ResultMessage`; a higher-sequence Tool or terminal
  event cannot erase an earlier received delta; and refreshing a failed V4-only
  Run preserves that delta exactly once.
- **Evidence ceiling:** source and local/CI tests cannot prove real SDK timing,
  proxy flushing, Redis fan-out, browser paint, or restart recovery. Those claims
  require an immutable candidate image on the controlled Linux environment and
  the applicable External Acceptance matrix.
- **Rollback:** restore the prior reviewed image. No schema, cursor, Redis key,
  or durable migration is introduced by this repair.
- **Stop conditions:** stop before code expands the public schema, weakens
  sanitizer or admission controls, treats narration as capability evidence,
  trusts callback-owned publication metadata, creates a second body or terminal
  authority, requires same-incarnation Redis reconstruction, or depends on a
  product choice not fixed above. Active successor-incarnation recovery is a
  stop condition for this Change Contract.
