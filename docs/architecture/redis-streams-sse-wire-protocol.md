# Redis Streams SSE v4 Wire Protocol

Status: PG replay slice source contract for `ai-platform.redis-streams-sse-event-channel.v4`; candidate verification, CI and External Acceptance are separate evidence

Index: [Redis Streams SSE Event Channel](redis-streams-sse-event-channel.md)

## Scope

This document exclusively owns the active v4 internal/public envelopes, Redis
key and PostgreSQL replay/live framing, callback-protocol separation, cursor validation,
strict gap/end controls, and frontend cursor acceptance. Execution,
authorization, direct committed-event publication, schema retirement, and release operations
remain with their dedicated owners.

The [streaming message design](../implementation/streaming-message-parts-design.md)
defines how current v4 Assistant text, public summaries, Tool activity and
artifacts compose in one reply. Current Claude output uses native text increments
through existing message deltas and receipt v1; part/v2 readers remain for
historical compatibility as specified in the [change contract below](#change-contract-incremental-assistant-parts-and-final-selection).
The outer v4 envelope is unchanged; new event types still require coordinated
readers and producers. Schema compatibility alone does not establish correct
content or latency.

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

Runtime enum checks must preserve JSON type identity: a boolean constant such as
`hydrate_required: true` does not accept Python-equal numbers `1` or `1.0`.
Candidate and Streaming boundaries independently enforce this rule, including
revalidation after callback/deserialization or mutation. Enum mismatches retain
the existing bounded validation errors without echoing input. Counter limits,
reference grammar, redaction, fixed-copy and authority checks are unchanged.
This correction replaces only Python-equality enum membership; consolidation of
the remaining handwritten payload-shape algorithms is still pending. A future
shared runtime engine must respect the source architecture's dependency rules;
this change introduces no third-party domain dependency or compatibility path.

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
| `message.delta` | Ordered public Assistant text / receipt v1 | Current Claude native increments and retained producers use this existing chain |
| `message.part.delta` / `message.part.classified` | Historical Assistant text parts and immutable role annotations | Part/v2 receipt materialization remains compatible; role no longer controls Chat placement or copying |
| `commentary.delta` | Disclosure-safe public summary or marked Claude work narration | Existing summaries remain inline; work narration uses a server-owned `worktrace_` summary ID and folds with work activity. Neither enters the answer receipt |
| `thinking.*` | Legacy public-reasoning compatibility events | The current runner does not emit them; retained readers do not make hidden model reasoning public |
| `model.completed` | Model completion duration, turn-count, and stop-category metadata | It is neither answer content nor Run terminal authority |

The Chat history API retains the legacy `message:chunk` and `summary` projections.
New part events retain their public v4 envelope, IDs, sequence, message ownership,
incarnation and closed part payload. Live and history reduce the same part facts.
Old commentary/worktrace rows retain their presentation; new Claude narration
uses ordinary ordered message deltas, without work/answer classification. Unknown new events fail closed
in older clients, so schema, producer, Worker, history and frontend release together.
The ordinary-user display matrix is owned by
[Chat Run lifecycle and public error projection](chat-run-lifecycle-and-public-error-projection.md#ordinary-user-execution-presentation).

## Executor callback boundary

The authenticated callback protocol remains independently versioned at v2.1.
It binds tenant, Run, current Attempt, runtime lease, callback index, and ordered
batch items. The v4 platform adapter validates every item before receipt,
assigns deterministic safe identities, and commits canonical public `run_events`
plus the receipt atomically. Exact retries reuse the same rows and identities;
a conflicting receipt fails closed. After PG commit the callback makes a bounded
250 ms best-effort Redis append, then acknowledges the durable receipt even if
that transport attempt fails or times out. This is not zero-wait ACK or a detached
background publish. Cancellation, projection, business, authority and database
errors are not swallowed as transport success. The existing
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
Every new Claude native text suffix passes the stateful safety gate before
callback; batches contain at most 100 events. No semantic classification follows. The SDK terminal result closes ordinary assistant text;
optional final files are selected separately through `attach_file` and projected
as ordered artifact parts after storage succeeds. Worker, API, and frontend
support for this closed event is deployed release-atomically because older v4
clients reject unknown events.

The Sandbox may enqueue only single-item callbacks containing one adjacent,
already-projected `message.delta` or `message.part.delta` event before this boundary. The worker batches
those callback items without concatenating or rewriting their events: each
keeps its event identity and becomes its own durable row and SSE sequence. It
uses the existing zero-delay aggregation window, stops adding before a batch
would exceed 100 events or 8 KiB of
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
before accepting a cancellation request or terminal event. Callback predecessor lookup/republishing is retired; durable PG sequence order
and catch-up prevent a missing Redis predecessor from skipping public facts.
Direct Run-event publication and its separate receipts remain. The derived prefix is internal,
not persisted publication state, and is stripped at the public boundary.

## Public event types

The closed Agent-kernel application registry is:

- `message.started`, ordered `message.delta`, `message.completed`;
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
and outputs, credentials, private runtime paths embedded in tool/provider data,
storage keys, private trace values, and unclassified objects are prohibited.
The current workspace cwd may appear literally when the Assistant includes it
in ordinary prose under the Chat content policy; it is not normalized or aliased.
Intentional non-sensitive code, JSON examples and task-file references in
Assistant prose follow the Chat content policy; their spelling alone does not
make them tool data.
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

PostgreSQL `run_events` is the durable replay authority. Each connection captures
an authorized high-water sequence, scans ascending bounded pages (100 rows), and
projects rows through `project_public_v4` under current Run/Attempt/incarnation
and authorization fences. Each page is bounded by that fixed high-water;
concurrent writes belong to a later catch-up. Transactions finish before network
writes or Redis waits.

Three positions must not be confused:

- **scanned sequence:** last durable row inspected, including private, historical
  non-v4 or otherwise non-public rows;
- **public sequence:** last projected application event sent by the gateway;
- **consumed cursor:** last event successfully accepted by the browser reducer,
  or a fully consumed authorized history snapshot's fixed maximum.

Empty public pages still advance the scan and continue to the captured bound.
Public sequence holes are normal, not missing history. Never test continuity
with `last_public_sequence + 1`. Redis's strictly validated live frame may use
`scanned_sequence + 1` as a fast path; a jump triggers PG catch-up rather than
skipping rows or emitting a gap. Private rows never cross the public boundary.

Redis `XREAD` is a bounded low-latency accelerator (128 entries, up to five
seconds), not a prerequisite for opening an already admitted Run. Empty reads,
sequence jumps and transport unavailability trigger another bounded PG catch-up.
Unavailable Redis is rate-limited to at least one second per retry cycle to
avoid busy-spin. Redis trim, loss and expiry do not imply PG history loss.
Every live envelope still passes strict scope, identity and payload validation;
malformed/foreign bytes do not become a fallback success.

Every browser retains its own authorization lease and request lifetime.
Cancellation cancels its wait. Lease renewal and per-frame deadline checks apply
to both replay and live output. No subscriber hub, process-memory replay log,
publication queue, background publisher or successor rebuild is introduced.

## Public SSE frames

Payload frames use the generated public event, never an independently shaped
data object:

```text
id: <run_id>:<sequence>
event: <public-event-type>
data: <canonical PublicRunStreamEventV4 or PublicStreamControlV4 JSON>

```

The public value omits tenant scope, Attempt ID, Redis key, credentials, and
private identifiers. The event header equals `data.event_type`. Application frames carry the durable
Run-local sequence ID. `stream.open` is reconstructed from confirmed authority's
validated canonical `open_payload_bytes`, not invented as a `run_events` row.
`stream.end` is derived deterministically from the observed durable terminal
semantic event and references it in payload and causation. Both controls have
**no SSE `id:`** and cannot advance durable progress. Reconnection at a terminal
sequence returns the matching end without replaying terminal content.

Heartbeat is a comment frame and has no `id:` or payload event:

```text
: heartbeat

```

`stream.gap` remains a strict v4 recovery control, not a durable application cursor. It is
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

Application SSE IDs and `Last-Event-ID` use `<run_id>:<sequence>`: a Run-local
nonnegative decimal sequence, never a Redis entry ID. `run_id:0` starts the
available durable scan. A history handoff uses only the fully consumed,
authorized fixed `snapshot_max_sequence`; an intermediate pagination maximum
must not skip unread history.

Malformed, foreign-Run and future sequence cursors fail closed. A well-formed
legacy `<run_id>:<incarnation>:<redis-ms>-<redis-sequence>` cursor returns HTTP
409 with `X-SSE-Error-Code: sse_cursor_snapshot_required`. The current frontend
loads authorized history and resumes from its fixed snapshot maximum within
the same incarnation. Cross-incarnation owner/message identity migration is not
implemented: existing message-owner fences reject unsafe continuation and may
leave only terminal observation. The adapter does
not reinterpret Redis IDs as sequence numbers or silently reset them to zero.

Incarnation is absent from the cursor but remains in the validated envelope,
confirmed authority, Attempt binding and authorization lease. Changing cursor
syntax does not retire these fences or allow cross-incarnation replay.

## Gap and durable hydrate

Redis loss alone never emits `stream.gap`. Private/non-v4 rows and sequence gaps
between public frames are not proof of retention loss. The present product has
no `run_events` retention/deletion policy; missing required PG history fails
closed, rather than fabricating a retention watermark or silently skipping it.
A future retention policy must define a trustworthy retained floor and update
this contract and gap/hydration tests before using the existing gap control.
The existing strict gap decoder remains compatibility support, not a second
replay authority or a successor-creation path.

## Frontend accepted cursor

The browser keeps one accepted durable cursor per authorized Run, with incarnation validation. Event
processing order is:

1. parse SSE and require a valid durable `id` for application frames; `stream.open` and `stream.end` are explicitly id-less controls;
2. validate schema, Run, incarnation, public event type, and payload bounds;
3. classify semantic duplicates as transport-only acceptance while preserving
   chat state;
4. apply the one public-event adapter and reducer;
5. store the cursor only after reducer acceptance. A trusted Run terminal
   releases generation and admission immediately; its matching `stream.end`
   shares that accepted terminal fence. Required history hydration runs separately.

Reducer failure leaves the previous cursor unchanged so reconnect replays the
event. Terminal history failure displays a result-unavailable card for that Run;
it does not reopen an accepted terminal. Missing application IDs fail closed; no UUID transport fallback exists.
An authorized, fully consumed history snapshot may seed the durable cursor;
Run status alone cannot invent consumed progress or bypass the live reducer. Reconnect sends only the
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

After a complete authorized history snapshot, an active Run resumes PG replay
at that snapshot's maximum even without a former Redis anchor. Preserve current
message ownership and semantic dedupe; do not manufacture a message identity
or silently fall back to status-only polling. Active and terminal history reads
retain cancellation, bounded timeouts and stale-session fencing. Authorization
failure stops access. One reconciliation owner coordinates history and retry.

## Required focused tests

- callback response loss, exact/conflicting receipt and ordered canonical rows;
- confirmed admission before SDK dispatch and no DB locks during network I/O;
- PG fixed-high-water paging, private-only pages and public sequence holes;
- Redis fast path, trim/absence/timeout fallback and bounded retry;
- malformed/foreign/future durable cursor and legacy snapshot handoff;
- snapshot completion before durable cursor storage and reducer failure replay;
- open from confirmed authority and terminal-derived id-less end, including
  terminal-cursor reconnect and terminal hydration without Redis;
- public projection, incarnation/Attempt fences, per-frame lease expiry and
  authorization revocation during catch-up or live wait;
- callback ACK transport-only failure handling within the 250 ms budget.

## Change Contract: incremental Assistant parts and final selection

The original part/v2 extension remains a stored-data compatibility contract.
Current Claude output retires its semantic classification producer and follows
these rules:

- **Owner:** Execution reconciles native SDK deltas/completed blocks and applies
  the existing safety gate. Streaming owns durable ordered facts/receipts; Chat
  owns stable rendering, current-text copy and independent activity disclosures.
- **Scope:** enable SDK partial messages and publish text increments through the
  existing `message.started → message.delta* → message.completed` chain. No new
  schema, event type, option, runtime negotiation or second stream is introduced.
  Run/Attempt/lease, authorization, callback ACK, Tool evidence and terminal
  authority remain unchanged.
- **Projection:** ordinary main-Assistant text is public after redaction, not
  after answer/work classification. Completed SDK text checks its own streamed
  block without duplicate publication. Thinking, child prose, raw Tool payloads
  and executor-private values remain excluded. Result never backfills text.
- **Receipt:** new output uses receipt v1 over all ordered public message deltas.
  Counts, length, final delta identity and causation must match current authorized
  durable facts. Failed/cancelled terminals produce no success receipt. Sandbox
  inline terminal text stays empty. Part/v2 receipt reconstruction retains its
  original role selection only when reading existing stored facts.
- **Recovery:** live, replay, gap and terminal hydration retain the same Run,
  message, Attempt/incarnation/epoch and cursor fences. Old part/classified
  events remain validated and replayable; their role is no longer a visibility
  or copy gate. Orphan/malformed/foreign/closed-state events still fail closed.
- **Bounds:** 8192 code points per public delta, 100 events per callback and the
  existing indexed append/receipt checks stay in force. Native frame/typed
  observation replay is Attempt-local; new public event IDs include Attempt.
- **UI:** accepted text stays in event order outside explicit activity folds.
  Each activity owns its disclosure; token updates and terminality never reset
  a manual choice. Current text can be copied while generating/synchronizing.
  Result-first relocation and consecutive-success tool subgroups are retired.
- **Retirement/compatibility:** remove Claude classification/router role state,
  part producer and role-gated UI/copy/recovery. Streaming, Worker and Chat retain
  part schema/decoder, v2 receipts and their indexes for existing historical
  data; no row rewrite or deletion occurs. Existing non-Claude message deltas,
  v1 receipts and commentary/worktrace history keep their identified readers.
  `assistant_delta` public-history fallback and `assistant_final` stay retired.
- **Release/rollback:** backend and frontend must be updated for consistent UX.
  Keep part/v2-capable readers and current schema/index readiness after producer
  rollback. Publishing/deployment still requires the release runbook authority.
- **Acceptance:** synthetic native increments before completed blocks/Result,
  exact replay and body conflicts, scope/redaction/ACK/unknown-write boundaries,
  receipt/history/admin consistency, mounted copy/order/independent disclosure,
  type/build and rendered browser checks. Real provider pacing, reverse-proxy
  flushing and s72 acceptance require separate observed runtime evidence.

Detailed source and retirement inventory is in the
[streaming message design](../implementation/streaming-message-parts-design.md).
