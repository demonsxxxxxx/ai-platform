# ADR 0012: Recoverable Agent-Kernel Event Stream v4

Status: accepted; WP1 protocol contract

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

Public application events use
`ai-platform.public-run-stream-event.v4`. Each event requires a stable event
identity, Run identity, nullable-but-present message and causation references,
committed business `seq`, stream incarnation, replayability, trace reference,
commit time, and a strict event payload. Application `seq` is the committed
Run-local business order and is separate from the Redis SSE cursor and semantic
`event_id`. Message, thinking, model, tool, and subagent events require a
non-null public `message_id`; artifact, policy, and Run events may use null.

The closed Agent-kernel registry is:

- `message.started`, `message.delta`, `message.completed`;
- `thinking.started`, `thinking.completed`, `model.completed`;
- `tool.started`, `tool.completed`, `tool.failed`, `tool.denied`;
- `subagent.started`, `subagent.progress`, `subagent.completed`,
  `subagent.failed`, `subagent.cancelled`;
- `artifact.created`, `artifact.ready`, `artifact.failed`;
- `policy.checking`, `policy.allowed`, `policy.denied`;
- `run.cancel_requested`, `run.succeeded`, `run.cancelled`, `run.failed`.

Every payload is bounded and closed. Public identifiers use disclosure-safe
patterns; summaries, final content, durations, turns, progress, artifact
metadata, and reference arrays have explicit size bounds. Hidden reasoning,
raw SDK fields, commands, paths, arguments, outputs, exceptions, and raw
capability or task identifiers are not protocol fields. Render families are
registry metadata only in this phase: `text`, `thinking_state`,
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

The v4 schema can represent recoverable Agent work while retaining the
platform's existing durability and authorization boundaries. Generated Python
and TypeScript contracts cannot silently drift from the schema, and transport
controls cannot consume application ordering. Runtime producers, persistence,
Redis publication, reducers, and package cutover remain owned by later work
packages and must consume this contract without introducing a permanent v3/v4
dual stack.

The v4 hard cutover requires coordinated producer, gateway, frontend, and
packaging changes. Local WP1 checks prove only schema, generator, and fixture
behavior; runtime recovery, durable publication, and external acceptance
remain outside this protocol work package.

## Rollback

Rollback uses the prior reviewed immutable v3 image set and does not interpret
v4 cursors or event values as v3. Existing durable authorities remain the
source of truth while the coordinated v4 cutover is drained or terminalized.
