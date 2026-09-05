# Redis Streams SSE v4 Wire Protocol

Status: normative contract for `ai-platform.redis-streams-sse-event-channel.v4`; External Acceptance pending

Index: [Redis Streams SSE Event Channel](redis-streams-sse-event-channel.md)

## Scope

This document exclusively owns the active v4 internal/public envelopes, Redis
key and replay/live framing, callback-protocol separation, cursor validation,
strict gap/end controls, and frontend cursor acceptance. Execution,
authorization, publication claims, successor activation, and release operations
remain with their dedicated owners.

## Generated protocol authority

`schemas/public_run_stream.v4.schema.json` is the only definition source for the
v4 internal envelope and the browser-visible application/control discriminated
unions. The repository generator emits checked-in Python and TypeScript types;
CI regenerates both and fails on a diff. Handwritten envelope field lists,
event enums, and frontend protocol unions are prohibited as independent
protocol authorities. Generated types alone do not prove runtime-validator
parity. The existing handwritten runtime field checks must remain subject to
schema-equivalence coverage until generated or consolidated; semantic identity,
owner and cross-field checks remain explicit. This is an implementation gap
to close, not a reason to bypass validation.

The internal envelope contains tenant scope, current Attempt identity,
projection version, and strict source metadata required for trusted validation.
The browser projection excludes tenant, Attempt, source, and every other
infrastructure-only field.

## Executor callback boundary

The authenticated callback protocol remains independently versioned at v2.1.
It binds tenant, Run, current Attempt, runtime lease, callback index, and ordered
batch items. The v4 platform adapter validates every item before receipt,
assigns deterministic safe identities, and commits canonical public `run_events`
plus the receipt atomically. Exact retries reuse the same rows and identities;
a conflicting receipt fails closed. Callback transport fields and engine SDK
objects are never browser wire fields.

The Sandbox may enqueue only single-item callbacks containing one adjacent,
already-projected `message.delta` event before this boundary. The worker batches
those callback items without concatenating or rewriting their events: each
keeps its event identity and becomes its own durable row and SSE sequence. It
uses a configured 50-millisecond aggregation delay, stops adding before a
batch would exceed 100 events or 8 KiB of aggregate delta text, and holds at
most 100 queued callback items. The text byte limit excludes envelope/JSON
metadata. The configured delay does not bound queue residence, network retry
or end-to-end latency; those require separate measurement. Deadline-based age
handling and a wakeable forced flush are convergence targets, not claims about
the current timer implementation. A larger pre-projected item and every multi-item callback remain
synchronous barriers. Once v4 answer projection is accepted, the redundant
legacy `assistant_delta` callback is suppressed. One ordered runner-event
callback is in flight at a time; queue saturation backpressures the SDK. Every
non-delta runner event, Tool lifecycle transition, error, cancellation, or
terminal transition is a receipt barrier, so no later fact can overtake
uncommitted public answer text. Cancellation discards only callbacks that have
not started and waits for an in-flight delivery to reach its bounded receipt or
rejection before terminal delivery. The callback HTTP client is reused for the
application lifetime; reconnect behavior does not change callback identity or
retry bytes.

## Internal Redis envelope

Every Redis entry contains one canonical JSON value shaped by
`InternalStreamEnvelopeV4`:

```json
{
  "schema": "ai-platform.stream-event.v4",
  "event_id": "evt4_...",
  "tenant_scope": "keyed-nonreversible-scope",
  "run_id": "run_...",
  "attempt_id": "attempt_...",
  "message_id": "msg4_...",
  "seq": 12,
  "event_type": "message.delta",
  "stream_incarnation": 3,
  "replayable": true,
  "trace_ref": null,
  "causation_event_id": null,
  "emitted_at": "RFC3339 UTC",
  "projection_version": "public-stream-v4",
  "payload": {"delta": "bounded public text"},
  "source": {"kind": "run_event", "run_event_id": "evt4_...", "sequence": 12}
}
```

Required fields are exact. JSON bytes use UTF-8, sorted object keys, no
insignificant whitespace, and the canonical number/string rules used by the
persisted digest. Invalid UTF-8, bounds, schema/projection/event values,
identity/source binding, or scope/Run/Attempt/incarnation fail before Redis
append.

`event_id` is semantic idempotency; committed application `seq` is Run-local
business order; Redis ID is transport order inside one proven incarnation.
Unknown publication outcomes retry the same canonical bytes and semantic ID.
Readers may encounter transport duplicates and advance through them, while the
frontend applies each semantic event once.

## Public event types

The closed Agent-kernel application registry is:

- `message.started`, `message.delta`, `message.completed`;
- `thinking.started`, `thinking.delta`, `thinking.completed`, `model.completed`;
- `agent.progress` for fixed, server-owned execution-phase lifecycle;
- `tool.started`, `tool.completed`, `tool.failed`, `tool.denied`;
- `subagent.started`, `subagent.progress`, `subagent.completed`,
  `subagent.failed`, `subagent.cancelled`;
- `artifact.created`, `artifact.ready`, `artifact.failed`;
- `policy.checking`, `policy.allowed`, `policy.denied`;
- `run.cancel_requested`, `run.succeeded`, `run.cancelled`, `run.failed`.

The closed transport controls are `stream.open`, `stream.heartbeat`,
`stream.gap`, and `stream.end`. Controls have null `message_id`, `seq`, and
`trace_ref`; they do not consume business order. `stream.gap` always requests
`reload_durable_state`, and `stream.end` references the observed terminal event.

Provider-internal reasoning that is not returned as public summarized thinking,
raw SDK objects, commands, arguments, outputs, credentials, paths, storage keys,
private trace values, and unclassified objects are prohibited. The Claude SDK is
configured with `thinking.display = summarized`. The Runner accepts only the exact
SDK `ThinkingBlock` type and extracts only `ThinkingBlock.thinking`; `signature`
never enters the callback contract. Each complete summary is sanitized before
transport as one internal callback fact. The callback authority derives the
opaque `thinking_id` and creates bounded `thinking.delta` chunks between
`thinking.started` and `thinking.completed`, so caller-supplied public deltas
cannot bypass whole-summary sanitization. Sensitive fragments are redacted while
the remaining public summary is preserved. Legacy empty or fixed-summary
thinking payloads remain replayable, but new rows do not synthesize fixed
reasoning text. Agent progress
carries only fixed server-owned phase messages. Tool input and result summaries
are fixed lifecycle text derived from the validated public display name;
callback-supplied arbitrary summary text fails closed. The strict event-specific
projector applies identity, byte, depth, and count bounds before a canonical
public row can be committed.

## Key and stream incarnation

PostgreSQL allocates a positive, monotonically increasing
`stream_incarnation` for the current attempt. The physical key prefix remains the existing `v3` Redis namespace because v4
reuses the single transport plane rather than creating a parallel stack:

```text
ai-platform:sse:v3:{<tenant_scope>:<run_id>}:<stream_incarnation>:events
ai-platform:sse:v3:{<tenant_scope>:<run_id>}:<stream_incarnation>:live
```

The first key is the replay Stream and the second is its Pub/Sub channel. The
cluster hash tag keeps one run's incarnation and live notification on one slot.
Exactly one
incarnation is current. The key, every envelope, the terminal intent, and every
cursor must agree. A missing/unprovable current key is never recreated under an
already-issued incarnation; rebuild first increments PostgreSQL authority.

`stream.open` is the first entry. Its stable semantic ID and canonical bytes
bind the admitted Attempt/incarnation/projection version. Redis admission is
proven only when that exact first entry exists; a mismatched first entry fails
closed.

## Atomic append and retention

All appends run through one reviewed Lua script:

1. validate the expected key state and open protocol when publishing
   `stream.open`, an application event, or a terminal pair;
2. `XADD key MAXLEN ~ <configured_maxlen> * envelope <canonical-bytes>`;
3. `PEXPIRE` the Stream and state keys with the selected TTL;
4. `PUBLISH` a bounded wrapper containing the returned native Redis ID and the
   exact canonical envelope bytes;
5. return the native Redis ID and applied TTL class.

No producer publishes outside this script. A Pub/Sub notification without the
corresponding retained Stream entry is a contract failure; a retained entry
without an observed notification is repaired by replay.

The active TTL is an idle TTL refreshed by every accepted active event. It is
not a fixed absolute duration from stream creation. Terminal then end use the
terminal replay TTL, also in the atomic append, and terminal TTL is never shorter
than active idle TTL. A long active task therefore stays retained while it emits
within the active-idle window.

Starting values are not capacity claims: `MAXLEN ~ 10000`, active idle TTL two
hours, terminal replay TTL two hours, `XREAD COUNT 128`, block at most 15 seconds,
and heartbeat at 15 seconds. External Acceptance must set values from measured
event rate, reconnect target, entry bytes, Redis memory, and slow-consumer data.

The approximate checks are:

```text
replay_seconds ~= MAXLEN / p99_post_coalesce_events_per_second
redis_bytes ~= retained_runs * min(MAXLEN, event_rate * ttl_seconds)
              * (average_entry_bytes + measured_redis_overhead)
```

## Shared live feed and bounded subscribers

Each API process owns one Pub/Sub connection and dynamically subscribes to one
logical live channel per active Run stream. Browsers for the same stream share
that Redis subscription. Every browser still has an independent bounded queue,
authorization lease, reducer cursor, and connection lifetime.

Attach subscribes and observes the Redis subscription acknowledgement before it
captures retained Stream bounds. It then replays through that captured tail,
buffers concurrent live notifications, discards overlap at or before the replay
tail, drains later buffered events in Redis-ID order, and enters live mode.
Semantic event IDs make an exact retry reducer-idempotent.

A feed disconnect, malformed wrapper, foreign key/incarnation, event-count or
byte overflow, or uncertain ordering closes affected connections. It never
drops a frame and then advances the browser cursor. Reconnect uses Stream replay.
There is no process-memory replay log and no `XREADGROUP`.

Starting local bounds, pending External Acceptance, are 256 queued events and 1
MiB per browser, 256 browsers per API process, and 32 browsers per Run stream.
Values are configuration with strict positive upper bounds and are not capacity
claims.

## Public SSE frames

Payload frames use the generated public event, never an independently shaped
data object:

```text
id: <run_id>:<stream_incarnation>:<redis-milliseconds>-<redis-sequence>
event: <public-event-type>
data: <canonical PublicRunStreamEventV4 or PublicStreamControlV4 JSON>

```

The public value omits tenant scope, Attempt ID, Redis key, credentials, and
private identifiers. The event header equals `data.event_type`. Run-terminal
and `stream.end` each have Redis-backed IDs; `stream.end` references the
observed terminal semantic event ID.

Heartbeat is a comment frame and has no `id:` or payload event:

```text
: heartbeat

```

`stream.gap` is a strict v4 control frame with a server-supplied cursor. It is
not committed as chat state; the client invokes authorized durable hydrate and
does not persist the gap cursor as application progress. Heartbeat remains an
id-less comment.

Response headers are fixed:

```text
Content-Type: text/event-stream
Cache-Control: no-cache, no-transform
X-Accel-Buffering: no
Connection: keep-alive
```

Compression is disabled on the SSE location/response. Proxy buffering and cache
are disabled. Timeout ownership and real proxy checks live in the operations
contract.

## Canonical cursor

The SSE ID and `Last-Event-ID` are exactly:

```text
<run_id>:<positive-stream-incarnation>:<redis-ms>-<redis-sequence>
```

Numeric fields use canonical unsigned decimal with no sign, whitespace, or
leading zero. Redis comparison occurs only after tenant/run authorization and
after the durable/current, key, and envelope incarnations all agree.

Validation results:

- malformed form, foreign run, zero/negative/future incarnation, or Redis ID
  later than the proven tail: fail closed as an invalid request without reset;
- valid same-Run older incarnation: emit strict `stream.gap` without reading
  either old stream;
- valid current incarnation whose exact entry was trimmed/missing or whose
  continuity cannot be proven: emit strict `stream.gap`;
- no header: read from the earliest retained entry only when exact current
  `stream.open` is still the origin; otherwise emit `stream.gap`.

Native Redis IDs may overlap across incarnations. They never establish
continuity. Public clients never send `$`.

## Gap and durable hydrate

The gap is a complete `PublicStreamControlV4` envelope. Its payload is:

```json
{
  "reason": "stream_incarnation_mismatch",
  "requested_event_id": "1700000000000-0",
  "requested_stream_incarnation": 7,
  "current_stream_incarnation": 8,
  "earliest_available_event_id": "1700000000100-0",
  "latest_available_event_id": "1700000000200-0",
  "recovery": "reload_durable_state"
}
```

The payload IDs are native Redis IDs; the SSE `id:` carries the complete
Run/incarnation/Redis cursor. Bounds are null when Redis has none. A missing
terminal stream is recovered into a fresh successor before replay. A
nonterminal missing stream emits the truthful null-bounds gap with the current
incarnation start cursor and requires durable hydrate.

## Frontend accepted cursor

The browser keeps one accepted cursor per authorized run/incarnation. Event
processing order is:

1. parse SSE and require a valid transport `id` for every non-heartbeat frame;
2. validate schema, Run, incarnation, public event type, and payload bounds;
3. classify semantic duplicates as transport-only acceptance while preserving
   chat state;
4. apply the one public-event adapter and reducer;
5. store the cursor only after reducer acceptance. Run-terminal and an
   immediately matching `stream.end` both wait for successful terminal hydrate.

Reducer or hydrate failure leaves the previous cursor unchanged so reconnect
replays the event. Missing IDs fail closed; no UUID transport fallback exists.
Durable PostgreSQL sequence/history/status values cannot become a Redis cursor,
reset a reconnect budget, or enter the live reducer. Reconnect sends only the
last accepted cursor in `Last-Event-ID`. Final hydrate replaces rather than
appends the live answer.

## Required focused tests

- callback response loss, exact duplicate receipt, conflicting duplicate,
  ordered canonical rows, and stable semantic IDs;
- transaction-scoped admission before public/terminal commit, claim commit
  before Redis, no PostgreSQL locks during Redis I/O, receipt-fenced disposition,
  and indexed retry;
- atomic TTL refresh, ordinary and terminal duplicate receipts, terminal-pair
  ordering, and publish-pool cleanup;
- malformed/foreign/future cursor, trim gap, nonterminal missing-stream gap,
  exclusive successor rebuild, stale-token/fingerprint rejection, and atomic
  activation;
- schema-valid v4 gap, semantic duplicate transport acceptance, accepted cursor
  only after reducer or terminal-hydrate commit, matching `stream.end` fence,
  incarnation rejection, and final hydrate replacement.

## Change Contract: progressive public Run timeline

- **Owner:** Streaming owns committed public-event order; the Engine adapter owns
  SDK normalization; Execution owns capability evidence; Runs owns business Run
  success; the frontend reducer owns applied sequence and cursor acceptance.
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
  expose raw Tool output. A terminal receipt releases only the exact matching
  capability kind, canonical identity, and invocation ID; unrelated or duplicate
  terminal receipts fail closed. A failed Assistant-body projection remains
  permanently closed, but does not invalidate that exact receipt or suppress the
  corresponding public Tool terminal event. An exact producer-attributed policy
  rejection commits `tool.denied` and projects as a denied Tool with
  blocked/permission semantics; aggregate admission failures never synthesize
  Tool identity. `message.delta` is provisional user-visible narration; it is
  never evidence that a capability ran or that a Run succeeded.
- **Safety invariant:** hidden reasoning, raw tool input or output, commands,
  paths, credentials, storage keys, private Tool, Skill, MCP, task, Attempt, and
  stream identities remain prohibited. A private Skill identity may project only
  to its catalog-authorized public name, with ASCII characters converted to
  non-colliding full-width forms; opaque and dynamic identities use a generic
  non-ASCII marker. Exact invocation-interval text is not a public Assistant
  source. Stateful cross-chunk sanitization, cumulative bounds, strict callback
  validation, tool admission, capability receipts, and platform-owned
  terminalization remain fail closed.
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
