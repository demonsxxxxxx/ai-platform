# ADR 0009: Redis Streams SSE v3 Process-Local Live Fan-Out

Status: accepted; implementation included in the v3 hard-cutover change; External
Acceptance pending

Date: 2026-08-17

Decision ID: `ai-platform.redis-streams-sse-event-channel.v3`

Supersedes after release-atomic cutover: ADR 0004

Owning issue: [#858](https://github.com/demonsxxxxxx/ai-platform/issues/858)

## Context

SSE v2.1 established the correct durable and security boundaries: PostgreSQL
owns Run and terminal truth, Redis Streams is a bounded live/replay plane,
producer authority is fenced by the current attempt and runtime lease, public
projection happens before Redis, terminal bytes are frozen after the durable
transaction, and the browser advances only a reducer-accepted native Redis
cursor.

Its live reader still allocates one blocking `XREAD` connection per browser.
Multiple tabs and reconnect bursts therefore multiply Redis blocking clients for
the same Run stream. The Redis envelope and frontend event shapes are also
maintained manually, so schema drift is prevented by tests rather than by one
generated protocol authority.

The external resumable-generation PRD correctly requires horizontal API
fan-out, generated protocol types, bounded reconnect, and terminal convergence.
It also proposes a second Generation job manager, `generation_id`,
`owner_epoch`, a contiguous application sequence, a generic outbox, runtime
approval events, and hidden reasoning deltas. Those proposals conflict with
existing platform authorities or security policy and are rejected.

## Decision

Evolve the existing single Redis SSE runtime to v3 and remove v2.1 in one
release-atomic cutover.

The resulting path is:

```text
existing Run/attempt authority
  -> safe public projector
  -> canonical v3 envelope
  -> atomic Redis XADD + TTL refresh + PUBLISH
  -> one process-local hub per API instance
  -> bounded browser subscriber queues
  -> SSE and reducer-accepted Last-Event-ID
```

Redis Streams remains the replay authority. Pub/Sub carries only live
notifications and never becomes replay or business truth. Each API process
multiplexes logical Run-stream subscriptions over one Redis Pub/Sub connection.
It subscribes once per active stream, regardless of browser count, and fans out
the same canonical publication locally.

Attach is subscribe-before-replay:

1. authorize the exact tenant/session/Run and current stream incarnation;
2. attach a bounded local subscriber and wait until Redis confirms the logical
   channel subscription;
3. capture the retained Stream bounds and validate `Last-Event-ID`;
4. replay through the captured tail while buffering live notifications;
5. discard buffered overlap at or before the replay tail;
6. drain later buffered publications in Redis-ID order and enter live mode.

A Pub/Sub disconnect, malformed publication, subscriber count/byte overflow,
or uncertain overlap closes the affected SSE connection without advancing its
cursor. The browser reconnects from the last event that its reducer validated
and committed. Redis Stream replay repairs the live notification loss.

## Identity and authority

V3 keeps the established identities:

- `run_id` identifies durable work;
- `attempt_id` and current worker/runtime leases fence production;
- `stream_incarnation` fences one physical stream lifetime;
- `authorization_epoch` and a lease of at most 15 seconds fence readers;
- semantic `event_id` deduplicates one projected fact;
- native Redis Stream ID orders transport inside one incarnation and is the SSE
  cursor.

V3 does not add `generation_id`, `owner_epoch`, or a contiguous application
sequence. A producer cannot atomically allocate a sequence in Lua while also
supplying already-serialized bytes that contain the unknown sequence. Redis
already owns a suitable monotonic transport ID. Lua appends the canonical
envelope bytes, receives the Redis ID, refreshes TTL, and publishes a bounded
wrapper containing that ID plus the same envelope bytes.

The Streaming context owns public projection, protocol validation, Redis
live/replay behavior, cursor/gap policy, and SSE translation. It does not own
Run lifecycle, callback receipts, terminal truth, or raw SDK events.

## Producer ownership

The public Stream is shared transport, not shared semantic ownership. Each fact
has exactly one producer boundary:

| Supported execution path | Public events owned | Producer boundary |
|---|---|---|
| Worker admission and dispatch for all Run types | `stream_open`, Worker-originated `semantic_stage` and `semantic_progress` | Worker process through `RunStreamPublisher` |
| Claude single-Run writing tiers and MCP Runs | `assistant_text_delta`, runtime-originated `semantic_stage` and `semantic_progress` | Authenticated sandbox callback through `runtime_callbacks` and `RedisStreamBridge` |
| Inline completion and detached sandbox reconciliation | `terminal`, `end` | Worker terminalization/reconciler from committed PostgreSQL intent |
| Browser transport controls | heartbeat comments and replay `gap` controls only; no durable public event | API SSE reader |

Claude execution always requires a real sandbox and does not permit local SDK
execution. Therefore `assistant_text_delta` has one ingress: the authenticated
runtime callback. Worker adapters must not publish `assistant_delta`; such an
attempt fails closed instead of falling back to a generic semantic event.
Sandbox code cannot connect directly to PostgreSQL or Redis, and Worker code
does not call its own callback HTTP endpoint.

Adding any second assistant-text ingress requires a new accepted ADR that names
its disjoint execution mode, authority and event identity, plus a negative test
proving that one logical delta cannot be published by both owners. A source
allowlist or fallback branch is not sufficient authority.

## Protocol

`schemas/public_run_stream.v3.schema.json` is the sole wire source. A checked-in
generator emits standard-library Python `TypedDict`/constant definitions and a
TypeScript discriminated union. CI regenerates both and requires zero diff.

The public event allowlist retains only server-owned projections needed by the
current product: stream open, assistant text delta, semantic stage/progress,
terminal, and end. Heartbeat is an id-less SSE comment and replay gap is an
id-less bounded control frame.

Hidden reasoning, chain-of-thought, raw commands, tool arguments/results,
credentials, paths, storage keys, private trace IDs, and unrestricted capability
identifiers are prohibited. Runtime approval is not restored through v3.

## Terminal and persistence

PostgreSQL remains the Run and terminal authority. Existing callback receipts,
`run_events`, and `sse_terminal_publication_intents` are reused. V3 does not add
a generic outbox or persist token deltas in PostgreSQL.

Terminalization still commits truthful Run state, final answer/facts, and exact
terminal/end bytes and semantic IDs before Redis publication. Retry uses the
same bytes and IDs. Final hydrate replaces the provisional browser fold.

An optional Redis snapshot is a bounded public projection cache only. It may be
added after replay measurements demonstrate a need. Its absence, expiry, or
corruption must fall back to Stream replay or authorized PostgreSQL hydrate and
cannot change Run truth.

## Release and rollback

Dormant schema, hub, and reducer foundations may be reviewed separately, but no
intermediate image may select them in production. Producer, reader, terminal,
frontend, generated design ID, and Redis key prefix switch together. The source
contains no long-lived dual-stack feature flag after cutover.

V3 uses a versioned Redis key/channel prefix. Rollback drains or terminalizes
active v3 streams and deploys the previous immutable image; it never interprets
a v3 cursor against a v2.1 key.

## Consequences

Benefits:

- browser count no longer determines Redis blocking-reader connection count;
- live notification loss is repaired from the retained Stream;
- one generated protocol authority prevents backend/frontend drift;
- existing Run, terminal, authorization, and security authorities remain intact.

Costs and risks:

- subscribe/replay overlap and shared-connection failure require explicit,
  bounded state-machine tests;
- slow browsers need independent event and byte limits;
- multi-replica, Nginx, browser, Redis restart, and 50-run capacity claims remain
  External Acceptance and cannot be inferred from source tests.

ADR 0004, 0003, and 0002 remain audit history. They are not runnable fallbacks or
alternative production architectures after v3 cutover.
