# ADR 0012: Recoverable Agent-Kernel Event Stream v4

Status: accepted; active v4 wire and runtime contract

Date: 2026-08-17

Decision ID: `ai-platform.redis-streams-sse-event-channel.v4`

Supersedes for active wire and event-set decisions: ADR 0009

Owning issue: [#1187](https://github.com/demonsxxxxxx/ai-platform/issues/1187)

## Context

ADR 0009 established Redis Streams replay, Pub/Sub live fan-out, stream
incarnation fencing, and the existing Run, attempt, lease, and terminal
authorities. Its v3 public event set is a transport-oriented token channel and
cannot recover the Agent-kernel work timeline required by the product. The v4
contract extends the existing authorities without adding a second Run, job,
terminal, cursor, or authorization plane.

ADR 0009 remains accepted historical context for the v3 cutover and transport
mechanics. This ADR supersedes only its active wire and event-set decisions.

## Decision

SSE v4 uses one strict, generated protocol authority at
`schemas/public_run_stream.v4.schema.json`. The generator emits standard-
library Python `TypedDict` definitions for the internal and public contracts
and TypeScript types for public application and transport-control events.

The release-atomic B3 cutover makes v4 the only production wire. Producers,
durable publication, successor recovery, the SSE gateway, and the frontend
consumer are composed against the v4 contract. Runtime negotiation, feature-
flag selection, dual publication, and same-incarnation reconstruction are not
supported. Historical v3 source remains only where explicitly retained as a
compatibility artifact with no active production import path.

Public application events use
`ai-platform.public-run-stream-event.v4`. Each event requires a stable event
identity, Run identity, nullable-but-present message and causation references,
committed business `seq`, stream incarnation, replayability, trace reference,
commit time, and a strict event payload. Application `seq` is the committed
Run-local business order and is separate from the Redis SSE cursor and semantic
`event_id`. Message, thinking, model, tool, and subagent events require a
non-null public `message_id`; Agent progress, artifact, policy, and Run events
may use null.

The closed Agent-kernel registry is:

- `message.started`, `message.delta`, `message.completed`;
- `thinking.started`, `thinking.delta`, `thinking.completed`, `model.completed`;
- `agent.progress` for fixed, server-owned execution-phase lifecycle;
- `tool.started`, `tool.completed`, `tool.failed`, `tool.denied`;
- `subagent.started`, `subagent.progress`, `subagent.completed`,
  `subagent.failed`, `subagent.cancelled`;
- `artifact.created`, `artifact.ready`, `artifact.failed`;
- `policy.checking`, `policy.allowed`, `policy.denied`;
- `run.cancel_requested`, `run.succeeded`, `run.cancelled`, `run.failed`.

Every payload is bounded and closed. Public identifiers use disclosure-safe
patterns; model-provided public reasoning summaries, server-owned phase messages,
fixed Tool start/result summaries, final content, durations, turns, progress,
artifact metadata, and reference arrays have explicit size bounds. The Claude SDK
is configured with `thinking.display = summarized`; the Runner admits only the
exact SDK `ThinkingBlock` type and extracts only its `thinking` value. That
complete value is sanitized before it crosses the authenticated callback as one
internal summary fact. The callback authority, rather than the caller, derives
an opaque `thinking_id` and creates the ordered `thinking.started` /
`thinking.delta` / `thinking.completed` sequence with bounded chunks. Sensitive
fragments are redacted without discarding the remaining public summary. The SDK
`signature` is never a callback or public field.
Legacy v4 rows with an empty payload or fixed summary remain replayable, but new
rows do not synthesize fixed reasoning text. Provider-internal reasoning not
returned as public summarized thinking, raw SDK fields, commands, paths,
arguments, outputs, exceptions, and raw capability or task identifiers are not
protocol fields. Render families are registry
metadata only in this phase: `text`, `thinking_state`, `agent_progress`,
`tool_activity`, `subagent_activity`, `artifact`, `policy_result`,
`public_error`, `cancelled`, and `terminal`.

Transport controls use the separate schema
`ai-platform.public-run-stream-control.v4`. The closed controls are
`stream.open`, `stream.heartbeat`, `stream.gap`, and `stream.end`. Controls
carry null `message_id`, null `seq`, and null `trace_ref`; they do not consume a
business sequence. `stream.gap` identifies bounded recovery state and always
uses `reload_durable_state`. SSE `id` remains the transport cursor and is never
synthesized from `seq`.

The internal canonical envelope uses
`ai-platform.stream-event.v4` and adds tenant scope, current attempt identity,
projection version, and a strict source descriptor for the committed Run
row, stream authority, or terminal intent. It is browser-inaccessible. The
browser-generated TypeScript module contains no tenant, attempt, source, or
internal projection fields.

Unknown event names, unknown envelope fields, unknown payload fields, invalid
bounds, and v3/v4 cross-version values fail closed. Callback receipt protocol
v2.1 remains unchanged and separately versioned.

## Consequences

The v4 schema represents recoverable Agent work while retaining the platform's
existing durability and authorization boundaries. Generated Python and
TypeScript contracts cannot silently drift from the schema, and transport
controls do not consume application ordering. Committed public `run_events`
are published through claim-token-fenced, transaction-external Redis I/O.
Missing terminal streams recover by building an inactive successor incarnation,
verifying its persisted receipt and source fingerprint, and atomically
activating it under the current Run and Attempt authority. A historical pending
terminal event whose exact Redis stream and state have both expired, and whose
Attempt authority and active sandbox lease no longer exist, cannot use that
recovery path. Maintenance may instead suppress only that exact claimed
terminal event after revalidating the matching terminal Run and stream
authority. This disposition retains the durable row and an audit reason,
creates no Redis receipt, and never applies to an active, mismatched, or merely
unavailable stream. One failing publication scope does not prevent other bounded
scopes from draining before the failure is reported.

The hard cutover coordinates producer admission, durable draining, successor
activation, gateway replay/live delivery, frontend reduction, packaging, and
release checks. There is no runtime negotiation or concurrent v3/v4
publication. A rollback uses the prior reviewed immutable image and never
interprets v4 cursors or values as v3.

## Rollback

Rollback uses the prior reviewed immutable v3 image set and does not interpret
v4 cursors or event values as v3. Existing durable authorities remain the
source of truth while the coordinated v4 cutover is drained or terminalized.
