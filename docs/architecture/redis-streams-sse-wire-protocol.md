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
event enums, and frontend protocol unions are prohibited.

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
