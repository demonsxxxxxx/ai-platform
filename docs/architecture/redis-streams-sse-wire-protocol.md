# Redis Streams SSE v3 Wire Protocol

Status: normative contract for `ai-platform.redis-streams-sse-event-channel.v3`; External Acceptance pending

Index: [Redis Streams SSE Event Channel](redis-streams-sse-event-channel.md)

## Scope

This document exclusively owns callback batch identity, Redis envelope and key
format, atomic retention, public SSE framing, cursor validation, replay gaps, and
frontend cursor acceptance. It does not own execution/revocation policy or
release operations.

## Generated protocol authority

`schemas/public_run_stream.v3.schema.json` is the only definition source for the
v3 Redis envelope and browser-visible `PublicRunStreamEventV3` discriminated
union. The repository generator emits checked-in Python and TypeScript types;
CI regenerates both and fails on a diff. Handwritten envelope field lists,
event enums, and frontend protocol unions are prohibited.

The Redis envelope may contain keyed tenant scope and attempt identity required
for trusted validation. Its `public_event` member is the bounded generated value
sent to the browser. Internal-only fields are never copied into that member.

## Deterministic executor callback batch

An executor callback request carries an authenticated envelope:

```json
{
  "schema": "ai-platform.executor-callback-batch.v3",
  "run_id": "run_...",
  "attempt_id": "attempt_...",
  "batch_id": "opaque-stable-batch-id",
  "projection_version": "public-stream-v3",
  "items": [
    {
      "item_index": 0,
      "event_type": "assistant_text_delta",
      "payload": {"delta": "bounded public text"}
    }
  ]
}
```

Rules:

- Authentication still binds the callback token to tenant, run, and the exact
  active attempt/runtime lease. Payload fields cannot widen that authority.
- `batch_id` is immutable inside one attempt. `item_index` is zero-based,
  contiguous, unique, and ordered as received from the executor adapter.
- `source_sequence` is deterministic for the attempt/batch/item and is never a
  PostgreSQL live cursor. The callback producer must preserve executor order
  when allocating batch IDs; retry never allocates another sequence.
- `event_id` is a URL-safe base64url or hexadecimal digest of a domain-separated
  canonical tuple containing design ID, projection version, tenant scope,
  run ID, attempt ID, batch ID, and item index. It is stable across HTTP and
  Redis retries and cannot include plaintext tenant/user data.
- The canonical batch digest covers schema, projection version, ordered item
  count, item indices, public event types, and canonical projected payload bytes.
  The receipt persists the digest before acknowledging the batch.
- An exact duplicate returns the original receipt. Reuse of the batch identity
  with a different digest/count/order/attempt is `409 callback_batch_conflict`.
- A receipt records `event_ids`, first/through source sequence, callback received
  time, and durable commit time. It may store required semantic/tool/audit facts,
  but it never stores each `assistant_text_delta` payload in `run_events`.
- A response lost after receipt or Redis publication is resolved by submitting
  the same batch. Neither side mints a replacement batch/event ID.

## Internal Redis envelope

Every Redis entry contains one canonical JSON value:

```json
{
  "schema": "ai-platform.stream-event.v3",
  "event_id": "sev_...",
  "tenant_scope": "keyed-nonreversible-scope",
  "run_id": "run_...",
  "attempt_id": "attempt_...",
  "stream_incarnation": 1,
  "event_type": "assistant_text_delta",
  "emitted_at": "RFC3339 UTC",
  "projection_version": "public-stream-v3",
  "payload": {"delta": "bounded public text"},
  "public_event": {
    "schema": "ai-platform.public-run-stream-event.v3",
    "event_id": "sev_...",
    "run_id": "run_...",
    "stream_incarnation": 1,
    "event_type": "assistant_text_delta",
    "emitted_at": "RFC3339 UTC",
    "payload": {"delta": "bounded public text"}
  }
}
```

Required fields are exact; unknown top-level fields fail closed until a new
schema is accepted. `public_event` must be the exact generated safe projection
of the internal envelope identity and payload. JSON bytes use UTF-8, sorted
object keys, no insignificant whitespace, and the same canonical number/string
rules used by the payload digest. Unknown schema/projection/event types, invalid
UTF-8, oversize payloads, mismatched public projection, or mismatched
scope/run/attempt/incarnation fail before Redis append.

`event_id` is semantic idempotency. The Redis ID is transport ordering inside
one proven incarnation. An unknown `XADD` outcome retries the same envelope
bytes and semantic ID. Duplicate Redis entries are permitted; readers advance
through them and reducers apply the semantic event once.

## Public event types

| Type | Required authority before projection | Coalescing |
| --- | --- | --- |
| `stream_open` | committed admitted run/attempt/incarnation | no |
| `assistant_text_delta` | public answer text projector | bounded, same identity boundary only |
| `semantic_stage` | committed PostgreSQL platform-phase row projected after commit | no |
| `semantic_progress` | committed PostgreSQL strict `execution_step*` row projected after commit | no |
| `terminal` | committed terminal transaction and frozen intent | no |
| `end` | committed terminal and frozen end intent | no |

V3 does not define Redis or public `tool_lifecycle`, `approval_required`,
`artifact_ready`, `run_status`, or `reasoning_delta` envelope types. Skill and
tool execution are shown only through the strict `execution_step*` projection
carried by `semantic_progress`. Approval is not a runtime Streaming authority;
artifact and authoritative Run status remain durable API/hydrate facts until a
separately reviewed committed producer exists. Consumer support alone is never
evidence that a producer exists.

For `semantic_stage` and `semantic_progress`, the envelope semantic `event_id`,
the public `sequence`, and `emitted_at` come from the committed `run_events` row
(`id`, PostgreSQL run sequence, and `created_at`). The worker refreshes the
current run/attempt/incarnation authority in a new transaction after that row
commits, closes the transaction, and only then calls Redis. Retry reuses the
same row-derived envelope. Executor-originated arbitrary tool lifecycle or
label payloads are not promoted directly to this channel.

There is no public `assistant_reasoning_delta`. Claude hidden reasoning,
chain-of-thought, raw intermediate messages, and any similarly private model
channel are dropped. A safe explanation is a server-authored `semantic_stage` or
bounded summary with no raw model reasoning.

Raw command strings, tool arguments/results, prompts, credentials, authorization
headers, URLs with secrets, local/runtime paths, storage keys, private trace IDs,
and unclassified objects are prohibited. Projection uses event-specific
allowlists and byte/depth/count bounds before coalescing and Redis publication.

## Key and stream incarnation

PostgreSQL allocates a positive, monotonically increasing
`stream_incarnation` for the current attempt. The Redis key is:

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

`stream_open` is the first entry. Its stable semantic ID and canonical bytes bind
the admitted attempt/incarnation/projection version. Redis admission is proven
only when that exact first entry exists; a mismatched first entry fails closed.

## Atomic append and retention

All appends run through one reviewed Lua script:

1. validate the expected key state when publishing `stream_open` or terminal;
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
data: <canonical PublicRunStreamEventV3 JSON>

```

The public value omits tenant scope, attempt ID, Redis key, credentials, and
private identifiers. The event header must equal `data.event_type`. `terminal`
and `end` each have Redis-backed IDs; `end` references the terminal semantic
event ID.

Heartbeat is a comment frame and has no `id:` or payload event:

```text
: heartbeat

```

`stream_replay_gap` has `event:` and `data:` but no `id:` and then closes.
Neither heartbeat nor gap can advance a browser cursor.

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
- valid same-run older incarnation: id-less gap without reading either stream;
- valid current incarnation whose exact entry was trimmed/missing or whose
  continuity cannot be proven: id-less gap;
- no header: read from the earliest retained entry only when the exact current
  `stream_open` is still the origin; otherwise gap.

Native Redis IDs may overlap across incarnations. They never establish
continuity. Public clients never send `$`.

## Gap and durable hydrate

The bounded gap payload is:

```json
{
  "schema": "ai-platform.stream-gap.v3",
  "reason": "stream_incarnation_mismatch",
  "requested_event_id": "run_...:7:1700000000000-0",
  "requested_stream_incarnation": 7,
  "current_stream_incarnation": 8,
  "earliest_available_event_id": "run_...:8:1700000000100-0",
  "latest_available_event_id": "run_...:8:1700000000200-0",
  "recovery": "reload_durable_state"
}
```

Allowed reasons are `retained_history_unavailable`, `stream_missing`,
`stream_continuity_unproven`, and `stream_incarnation_mismatch`. Bounds may be
omitted when unprovable. The client closes the live fold, discards incomplete
text as a complete answer, and performs authorized durable hydrate. A terminal
hydrate replaces the fold. An active hydrate may return a server-issued covered
cursor; otherwise the UI remains honestly degraded and does not invent progress.

## Frontend accepted cursor

The browser keeps one accepted cursor per authorized run/incarnation. Event
processing order is:

1. parse SSE and require a transport `id` for every non-heartbeat/non-gap event;
2. validate schema, run, incarnation, public event type, and payload bounds;
3. deduplicate by semantic `event_id` while still accepting later transport
   order for a duplicate entry;
4. apply the reducer/event processor and commit visible/client state;
5. only after successful commit, store the transport ID as accepted cursor.

Reducer failure leaves the previous cursor unchanged so reconnect replays the
event. Missing IDs fail closed; no UUID transport fallback exists. Durable
PostgreSQL sequence/history/status values cannot become a Redis cursor, reset a
live reconnect budget, or enter the live reducer. Reconnect sends only the last
accepted cursor in the `Last-Event-ID` header. Final hydrate replaces rather than
appends the live answer.

## Required focused tests

- deterministic callback response loss, exact duplicate receipt, conflicting
  duplicate, item order, and duplicate semantic IDs;
- committed semantic projection identity/sequence/time, transaction rollback,
  post-commit authority refresh, and no Redis call inside a PostgreSQL
  transaction;
- atomic TTL refresh, terminal TTL switch, long-active stream, and publish pool
  cleanup;
- malformed/foreign/future cursor, trim/missing/rebuild gap, overlapping native
  IDs across incarnations, and two independent readers;
- Redis admission/outage and unknown `XADD` outcome without memory or PG-delta
  fallback;
- heartbeat/gap with no ID, missing payload ID fail closed, accepted cursor only
  after reducer commit, duplicate event cursor advancement, and final hydrate
  replacement.
