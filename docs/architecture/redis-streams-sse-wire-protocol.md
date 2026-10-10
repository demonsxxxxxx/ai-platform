# Redis Streams SSE v4 Wire Protocol

Status: normative contract for `ai-platform.redis-streams-sse-event-channel.v4`; External Acceptance pending

Index: [Redis Streams SSE Event Channel](redis-streams-sse-event-channel.md)

## Scope

This document exclusively owns the active v4 internal/public envelopes, Redis
key and replay/live framing, callback-protocol separation, cursor validation,
strict gap/end controls, and frontend cursor acceptance. Execution,
authorization, direct committed-event publication, schema retirement, and release operations
remain with their dedicated owners.

The [streaming message design](../implementation/streaming-message-parts-design.md)
defines how current v4 Assistant text, public summaries, Tool activity and
artifacts compose in one reply. It does not extend the v4 schema; schema
compatibility alone still does not establish correct content or latency.

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

## Terminology and content boundaries

These names describe different controls, provider objects, and public content;
they are not interchangeable:

| Term | Meaning | Explicit boundary |
| --- | --- | --- |
| `thinking_effort` | Canonical Run input control: `auto`, `low`, `medium`, or `high` | It changes provider effort, not SSE content visibility |
| `agent_options.enable_thinking` | Legacy profile/Chat alias translated to `thinking_effort` at admission | Despite the name, it is not a boolean; legacy `off` means `auto` |
| SDK `TextBlock` | Ordinary Assistant text in a typed content-block observation | An `AssistantMessage` may be one fragment of a shared provider message; text is not evidence of a complete turn or final answer |
| SDK `ThinkingBlock` | Provider model-reasoning content | The current runner discards it and `thinking.display` is `omitted` |
| `ResultMessage.result` | Ordinary-text SDK terminal observation | It cannot overwrite acknowledged public text, validate an artifact, or declare Run success; streamed persistence uses committed rows and an exact receipt |
| `attach_file` | Optional platform-owned response-file selection action | It publishes zero or more validated deliverables independently of terminal answer text |
| `message.delta` | Legacy v4 answer delta / receipt v1 | Retained producers and historical rows keep their original semantics |
| `message.part.delta` / `message.part.classified` | Versioned Assistant text parts and immutable classification facts | Safe pending previews stream before classification; answer/work selection owns receipt v2 |
| `commentary.delta` | Disclosure-safe public summary or marked Claude work narration | Existing summaries remain inline; work narration uses a server-owned `worktrace_` summary ID and folds with work activity. Neither enters the answer receipt |
| `thinking.*` | Legacy public-reasoning compatibility events | The current runner does not emit them; retained readers do not make hidden model reasoning public |
| `model.completed` | Model completion duration, turn-count, and stop-category metadata | It is neither answer content nor Run terminal authority |

The Chat history API retains the legacy `message:chunk` and `summary` projections.
New part events retain their public v4 envelope, IDs, sequence, message ownership,
incarnation and closed part payload. Live and history reduce the same part facts.
Old commentary/worktrace rows retain their presentation; new Claude work text is
identified by its explicit part classification. Unknown new events fail closed
in older clients, so schema, producer, Worker, history and frontend release together.
The ordinary-user display matrix is owned by
[Chat Run lifecycle and public error projection](chat-run-lifecycle-and-public-error-projection.md#ordinary-user-execution-presentation).

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
browser wire fields. The additive, admin-only
`claude_sdk_text_checkpoint` callback item carries only an opaque Run/Attempt
scoped `call_ref`, per-response text-delta count, character count, SHA-256 of
raw UTF-8 SDK text, and `final`/`complete`/`coverage` diagnostic flags. Both
proxy and SDK retain exact event 1/128/256/... samples and a final observation;
answer coalescing changes delivery time, not the sampled prefix. The callback
contains no provider message ID, text, reasoning or tool inputs. It requires a batch ID,
shares the Run/Attempt/lease receipt with adjacent answer events, and persists
as a private `executor_sdk_text_checkpoint` Run event; it creates no v4 row or
ordinary-user projection. Older API images reject this newly whitelisted
callback type, so roll out the API before the updated Sandbox executor. No
prior event or selector is replaced; existing callback replay and public SSE
schemas remain unchanged.

Streaming body contract is explicit: each public `message.delta` or `message.part.delta` frame is at
most 8,192 code points, and this per-frame bound never becomes a cumulative
answer cutoff. `message.completed` is metadata-only with
`{delta_count,text_length}`; its `causation_event_id` identifies the last delta
and the completion never carries full text. `commentary.delta` is separately
bounded to 8,192 code points and carries a stable `summary_id`; neither a public
summary nor SDK work narration contributes to answer content or capability evidence.
Every new Claude part suffix passes the stateful safety gate before callback.
Classification later selects answer/work; batches contain at most 100 events. The SDK terminal result closes ordinary assistant text;
optional final files are selected separately through `attach_file` and projected
as ordered artifact parts after storage succeeds. Worker, API, and frontend
support for this closed event is deployed release-atomically because older v4
clients reject unknown events.

The Sandbox may enqueue only single-item callbacks containing one adjacent,
already-projected `message.delta` or `message.part.delta` event before this boundary. The worker batches
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

- `message.started`, legacy `message.delta`, `message.completed`;
- `message.part.delta`, `message.part.classified` with closed `ai-platform.assistant-text-part.v1` payloads;
- `commentary.delta` for a sanitized public summary or Claude tool-turn work
  narration; `worktrace_` summary IDs fold as work activity, never answer text;
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

Provider-internal reasoning, raw SDK objects, execution commands, tool arguments
and outputs, credentials, private runtime paths, storage keys, private trace
values, and unclassified objects are prohibited. Intentional non-sensitive
code, JSON examples and task-file references in Assistant prose follow the
Chat content policy; their spelling alone does not make them tool data.
The Claude SDK is configured with
`thinking.display = omitted`, and the Runner excludes `ThinkingBlock` content
from both the answer and callback projections. The current Runner does not emit
`thinking.*`; an authenticated `claude_sdk_thinking_summary` callback remains
only as legacy write compatibility, and existing persisted `thinking.*` rows
remain replayable. Current Chat renderers display neither source. Agent progress
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
- valid retained cursor: resume from it even when the original `stream.open`
  has been trimmed. The exact cursor row carries terminal/end linkage; no
  preceding history scan is needed. Producer ordering forbids body rows after a
  Run terminal and binds `stream.end` to that terminal.
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
5. store the cursor only after reducer acceptance. A trusted Run terminal
   releases generation and admission immediately; its matching `stream.end`
   shares that accepted terminal fence. Required history hydration runs separately.

Reducer failure leaves the previous cursor unchanged so reconnect replays the
event. Terminal history failure displays a result-unavailable card for that Run;
it does not reopen an accepted terminal. Missing IDs fail closed; no UUID transport fallback exists.
Durable PostgreSQL sequence/history/status values cannot become a Redis cursor,
reset a reconnect budget, or enter the live reducer. Reconnect sends only the
last accepted cursor in `Last-Event-ID`. Terminal hydrate reconciles the same
Run segment and accepted source identities; it does not append duplicate answer
text or replace unrelated narration, Tool, process, or actionable status parts.
A new submission may proceed while the previous terminal history is loading;
late history and admission rollback modify only their own Run or optimistic messages.

The events endpoint returns at most 100 durable Run events per page, with
`next_cursor` fixing the Run set and sequence ceilings for that read. Each page
reauthorizes the session and Runs. User messages appear once before their Run;
artifacts and terminal events appear after its last page.
`terminal_run_statuses` identifies fully delivered terminal Runs; a matching
status and assistant segment avoid a second exact history request. An active
snapshot still requires reconciliation if the Run completes during the read.

An active gap with no observed `message.started` may lack the protocol message
owner needed to resume. Apply the durable history and preserve the Run, then
observe authoritative status and terminal history instead of inventing that
identity. Active and terminal history requests share cancellation and bounded
timeouts. Transient exhaustion releases recovery ownership for a later retry;
authorization failure stops access, and session changes cancel stale requests.
Browser recovery and failed timed reconnects share one reconciliation owner.
The status query releases that owner by identity when it settles, and a consumed
timer clears its own reference before starting the connection attempt.

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
  only after reducer acceptance, matching `stream.end` fence,
  incarnation rejection, and terminal-hydrate reconciliation;

## Change Contract: incremental Assistant parts and final selection

- **Owner:** Execution owns SDK source reconciliation and pre-publication safety;
  Streaming owns closed part facts, ordering and receipt reconstruction; Chat
  owns consistent live/history rendering, copying and disclosure phases.
- **Scope:** generated schema/types, Claude adapter/gate/router, callback and
  persisted ledger, Worker receipt v2, history decoder and frontend consumers.
  Outer v4 envelope, Redis keys/cursors, authorization, Attempt/lease fences,
  callback ACK, Tool evidence and Run terminal authority remain unchanged.
- **Projection:** `message.part.delta` introduces a pending visible preview.
  `message.part.classified` references an existing part in the same message.
  Pending/answer may become work before completion; work cannot become answer.
  Safe text remains in the ledger; classification changes its presentation and
  final selection. Thinking, raw Tool payloads and executor-private values remain
  excluded. Generic sanitizer output with ambiguous cross-source ownership fails closed.
- **Receipt:** v2 keeps the five v1 fields but counts only final answer-part
  deltas. First-observed answer parts join with two LF; length includes those
  separators, and last source delta follows ledger order. Completion carries
  these counts and causation only. Worker reconstructs current authorized facts,
  rejecting orphan/mixed/foreign/after-completion/unclassified facts or mismatches.
  Failed/cancelled terminals never carry an answer receipt. Streamed Sandbox
  terminal inline text remains empty; legacy v1 rows and receipts remain readable.
- **Ordering and recovery:** existing Tool and terminal receipt barriers remain.
  Live, replay, gap and terminal history apply exact part identities. Public
  history replays authorized `message.started` before its parts and
  `message.completed` when persisted, including across pagination; unmarked
  legacy lifecycle rows remain omitted. Invalid history cannot advance accepted
  watermarks or make the Run complete. Hydration preserves authoritative roles
  and newer suffixes within the same Run.
- **Bounds:** 8192 code points per delta and 100 events per callback remain.
  Normal part append uses bounded indexed lifecycle/owner/role/source lookups
  under the Run fence; completion and receipt validate the full persisted ledger.
  Schema `2026.10.09.1` installs exact message/part/source index contracts.
- **UI:** pending/answer appear outside work details; work appears inside.
  First safe preview collapses earlier work; later work opens it. Ordinary token
  updates preserve manual choices within the same phase. Final copy, outline and
  notification answer consumers filter roles consistently.
- **Retirement and compatibility:** Claude's source-complete buffering and new
  worktrace production are replaced. Retained old delta/v1 receipt/commentary
  consumers are owned by Streaming, Worker and Chat. Legacy `assistant_delta`
  public-history fallback and `assistant_final` remain retired. No historical
  row rewrite, second stream, hidden fields or runtime negotiation is introduced.
- **Release/rollback:** deploy all new consumers with producer. Old clients reject
  new events. Retain updated readers and current schema/index readiness while
  new facts/v2 receipts exist; a producer rollback must preserve those contracts.
  Reverting an old incompatible image is not a valid reader rollback.
- **Acceptance:** locked SDK synthetic source/replay/safety/receipt tests, real
  PostgreSQL split callbacks and Redis transport, mounted front live/gap/hydrate,
  copy/folding, generated contracts, typecheck and build. Exact image, real
  provider pacing, network flushing, browser paint and controlled-host restart
  acceptance require separate runtime evidence.

Detailed source and selection rules are in the
[streaming message design](../implementation/streaming-message-parts-design.md).
