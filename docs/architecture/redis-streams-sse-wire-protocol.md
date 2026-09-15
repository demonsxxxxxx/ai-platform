# Redis Streams SSE v4 Wire Protocol

Status: normative contract for `ai-platform.redis-streams-sse-event-channel.v4`; External Acceptance pending

Index: [Redis Streams SSE Event Channel](redis-streams-sse-event-channel.md)

## Scope

This document exclusively owns the active v4 internal/public envelopes, Redis
key and replay/live framing, callback-protocol separation, cursor validation,
strict gap/end controls, and frontend cursor acceptance. Execution,
authorization, direct committed-event publication, schema retirement, and release operations
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
a conflicting receipt fails closed. The callback response acknowledges only
after the PostgreSQL commit and direct Redis batch append. Redis failure leaves
the committed facts intact and returns a callback transport error. The existing
executor buffer owns exact callback retry; no publication queue or terminal
wakeup is involved. Callback transport fields and engine SDK objects are never
browser wire fields.

Streaming body contract is explicit: each public `message.delta` frame is at
most 8,192 code points, and this per-frame bound never becomes a cumulative
answer cutoff. `message.completed` is metadata-only with
`{delta_count,text_length}`; its `causation_event_id` identifies the last delta
and the completion never carries full text.

The Sandbox may enqueue only single-item callbacks containing one adjacent,
already-projected `message.delta` event before this boundary. The worker batches
those callback items without concatenating or rewriting their events: each
keeps its event identity and becomes its own durable row and SSE sequence. It
uses a configured 50-millisecond aggregation delay measured from the first
queued item, stops adding before a batch would exceed 100 events or 8 KiB of
aggregate delta text, and holds at most 100 queued callback items. A barrier or
close wakes the worker immediately; a delta whose deadline expires behind an
in-flight callback is sent without starting a new delay. The text byte limit
excludes envelope/JSON metadata. A larger pre-projected item and every
multi-item callback remain synchronous barriers. Once v4 answer projection is
accepted, the redundant legacy `assistant_delta` callback is suppressed. One
ordered runner-event callback is in flight at a time; queue saturation
backpressures the SDK. Every non-delta runner event, Tool lifecycle transition,
error, cancellation, or terminal transition is a receipt barrier, so no later
fact can overtake uncommitted public answer text. Cancellation discards only
callbacks that have not started and waits for an in-flight delivery to reach
its bounded receipt or rejection before terminal delivery. Supervisor heartbeat
requests follow the same rule; a retryable transport failure makes delivery
uncertain and permanently suppresses later publication. Exhausting retries for
an ordinary stream callback after a retryable failure has the same disposition;
only an explicit rejection permits later terminal delivery.

Application shutdown gives callback drain, in-flight delivery, terminal
notification, supervised-task cleanup, and shared HTTP-client close one
30-second absolute budget. If a started callback has no receipt or explicit
rejection by that deadline, its delivery remains uncertain and terminal
notification is suppressed; the existing Run, Attempt, lease, and Worker
recovery authorities own convergence. Shutdown never fabricates a callback
receipt or terminal Run state. The callback HTTP client is reused for the
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
frontend applies each semantic event once. A Run event source may carry
`callback_sequence`, the highest callback sequence strictly before that event
in the same Run/Attempt/incarnation. Redis checks its existing callback receipt
before accepting a cancellation request or terminal event. Callback publication
first publishes a preceding committed Run event through the same direct path,
so a delayed cancellation cannot be overtaken by later callback text. Receipt
state for these two producers remains separate. The derived prefix is internal,
not persisted publication state, and is stripped at the public boundary.

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

The admitted current Attempt binds one positive `stream_incarnation`. The
physical `v3` prefix is an opaque retained storage namespace; it does not enable
v3 wire negotiation or another transport:

```text
ai-platform:sse:v3:{<tenant_scope>:<run_id>}:<stream_incarnation>:events
ai-platform:sse:v3:{<tenant_scope>:<run_id>}:<stream_incarnation>:events:state
```

These are the Stream and its bounded append-receipt/phase state. The cluster hash
tag places them on the same slot. Exactly one incarnation is current; the key,
envelope and cursor must agree with its authority. A missing issued Stream is
never recreated and there is no successor allocation or builder.

`stream.open` is the first entry. Its stable identity and canonical bytes bind
the admitted Attempt/incarnation/projection version. Redis admission requires
that exact first entry; a mismatch or residual state without the Stream fails
closed.

## Atomic append and retention

The existing transport owns two bounded Lua operations: one for ordered callback
batches and one for open, Run events, terminal and end. Each validates current
protocol/phase, reuses its exact receipt, performs `XADD MAXLEN ~ 10000`, and
refreshes Stream/state TTL atomically. Neither script calls `PUBLISH`.

The callback script writes the complete ordered batch and its latest sequence,
digest and Redis receipt together. The serial executor buffer does not send a
later batch before acknowledgement. A terminal append checks that all callbacks
committed before the terminal fact have reached that receipt. A missing prefix
leaves the Stream open and returns unavailable; it does not roll back Run state
or create retry work. Terminal/end retries reuse the same semantic identities.

The active TTL is an idle TTL refreshed by accepted events. Terminal then end
use the terminal replay TTL, never shorter than the active idle TTL. Current
starting values are `MAXLEN ~ 10000`, two-hour active and terminal TTLs, 16 publish
connections and 256 read connections per transport instance. Receipt state has
fixed fields and expires with the Stream. These are bounds, not capacity claims.

```text
replay_seconds ~= MAXLEN / p99_post_coalesce_events_per_second
redis_bytes ~= retained_runs * min(MAXLEN, event_rate * ttl_seconds)
              * (average_entry_bytes + measured_redis_overhead)
```

## Replay and blocking reads

The API validates retained bounds and the accepted cursor, then replays through
a captured tail. The replay Lua operation checks that a nonzero predecessor
still exists before removing it from the returned page. An initial `0-0` read
uses the legal exclusive range `(0-0`. A trim during replay produces a gap.

After replay, the same decoder and public projector consume exclusive `XREAD`
from that tail. Entries appended between tail capture and the first read remain
in the Stream, so there is no subscription/attach race. Each read returns at
most 128 entries and blocks at most five seconds, shortened for the connection
lease. An empty read can emit an id-less heartbeat after lease revalidation.

Every browser owns its authorization lease, accepted cursor and request
lifetime. The lifespan owns bounded read/publish pools; request cancellation
cancels the blocked read. There is no shared feed, per-browser event queue,
process-memory replay log, `XREADGROUP`, Pub/Sub connection or PostgreSQL live
reader. Transport errors close the affected SSE connection without inventing
progress; reconnect uses the last reducer-accepted cursor.

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
Stream, terminal or active, emits the truthful null-bounds gap with the current
incarnation start cursor and requires durable hydrate. No successor is created.

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
last accepted cursor in `Last-Event-ID`. Terminal hydrate reconciles the same
Run segment and accepted source identities; it does not append duplicate answer
text or replace unrelated narration, Tool, process, or actionable status parts.

## Required focused tests

- callback response loss, exact duplicate receipt, conflicting duplicate,
  ordered canonical rows, and stable semantic IDs;
- transaction-scoped admission before SDK dispatch, callback commit before
  Redis, no PostgreSQL locks during Redis I/O, and exact callback receipts;
- atomic TTL refresh, ordinary and terminal duplicate receipts, terminal-pair
  ordering, and publish-pool cleanup;
- malformed/foreign/future cursor, initial replay, predecessor trim, exclusive
  XREAD after captured tail, active/terminal missing-stream gaps and no recreation;
- terminal/callback commit-to-append race, immutable business facts, partial
  terminal/end retry and final hydration without successful Redis delivery;
- schema-valid v4 gap, semantic duplicate transport acceptance, accepted cursor
  only after reducer or terminal-hydrate commit, matching `stream.end` fence,
  incarnation rejection, and terminal-hydrate reconciliation;

## Change Contract: progressive public Run timeline

- **Owner:** Streaming owns committed public-event order; the Engine adapter owns
  SDK normalization; Execution owns capability evidence; Runs owns business Run
  success; the frontend reducer owns applied sequence and cursor acceptance.
- **Bounded paths:** `app/executors/claude_agent_sdk_runner.py`,
  `app/executors/public_answer_stream.py`, the existing Claude public-event
  adapter, the durable Chat history projector, `frontend/web/src/hooks/useAgent/`,
  this document, ADR 0012, and their focused tests. The Redis envelope, key,
  cursor, authorization and Run terminal authorities remain unchanged.
  Publication now follows ADR 0013 and the Stream-only execution contract.
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
    source. `message.delta` uses stateful cross-chunk sanitization and bounded
  per-frame validation; it has no cumulative answer shutdown. Strict callback
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
  and cross-incarnation recovery without a validated current anchor require
  durable hydration without stream reconstruction. The frontend never invents
  a cursor or successor incarnation.
- **Single-body invariant:** v4 `message.delta` is the public incremental body
  authority and accepted deltas remain the durable source for streamed answers.
  `message.completed` closes the sequence with counts only and carries no answer
  text. Terminal-only answers are split into per-frame deltas and may span
  multiple callback batches; each batch respects the callback event-count bound,
  and the receipt exists only after every delta batch and the final completion
  batch are acknowledged. A successful streamed Sandbox terminal carries
  only a versioned `AssistantAnswerReceipt` containing `schema_version`,
  `message_id`, `delta_count`, `text_length`, and `last_delta_event_id`; its
  legacy `message` field is exactly empty, and failed or cancelled terminals
  cannot carry an answer receipt. The Worker
  accepts only committed, visible, current-Attempt v4 rows matching the exact
  receipt, message identity, order, counts and lengths. Redis delivery and retired
  publication metadata are not answer authority. Invalid or unauthorized rows
  fail closed. For streamed answers,
  short compatibility content remains inline when persistence limits allow,
  otherwise history stores a bounded `run_events_v4` reference, without
  truncating the answer. Legacy non-streaming bounded terminal messages use the
  same stable-source `assistant_delta` compatibility shape only when no streamed
  answer exists; obsolete `assistant_final` is retired. Terminal hydrate uses the
  same Run segment and source-local reconciliation, preserving unrelated accepted
  sources and actionable parts.
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
  proxy flushing, Redis delivery, browser paint, or restart recovery. Those claims
  require an immutable candidate image on the controlled Linux environment and
  the applicable External Acceptance matrix.
- **Rollback:** the original progressive-projection repair introduced no schema
  migration. After the Stream-only cutover, recovery follows the release runbook;
  an older backend image is not compatible with the migrated schema.
- **Stop conditions:** stop before code expands the public schema, weakens
  sanitizer or admission controls, treats narration as capability evidence,
  trusts callback-owned publication metadata, creates a second body or terminal
  authority, requires same-incarnation Redis reconstruction, or depends on a
  product choice not fixed above. Active successor-incarnation recovery is a
  stop condition for this Change Contract.

## Retirement and compatibility disposition

Removed from the active streaming path: punctuation-based withholding; the
4,096 and 262,144 cumulative answer shutdowns; aggregate
`message.completed.content`; the streamed Sandbox terminal full-body path;
obsolete `assistant_final`; and obsolete frontend final-text replacement.
Retained: bounded per-frame and queue limits, legacy non-streaming bounded
terminal messages projected as the stable-source `assistant_delta`
compatibility shape only when no streamed answer exists, and current history
compatibility projection. Their consumers are stored conversation records and
authorized history/final hydration, never a second SSE producer. Run/Attempt,
lease and callback-receipt authorities remain; publication claims and Pub/Sub
are retired by ADR 0013. This source disposition is not deployment or latency
evidence.
