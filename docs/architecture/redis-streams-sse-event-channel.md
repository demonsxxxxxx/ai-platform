# Redis Streams SSE Event Channel v4

Status: index of the active v4 source contract. Deployment and External
Acceptance require separate evidence.
Design ID: `ai-platform.redis-streams-sse-event-channel.v4`.

## Detailed owners

| Concern | Owner |
| --- | --- |
| Rationale and supersession | [ADR 0012](../adr/0012-recoverable-agent-kernel-event-stream-v4.md) |
| Envelopes, SDK/callback boundary, progressive timeline, replay/live, cursor/gap and client acceptance | [Wire protocol](redis-streams-sse-wire-protocol.md) |
| Admission, publication claims, authorization leases, terminal recovery and successor activation | [Execution control](redis-streams-sse-execution-control.md) |
| Gateway, cutover, deployed fault injection and acceptance | [Cutover acceptance](../operations/redis-streams-sse-cutover-acceptance.md) |
| Cross-component convergence proposals | [Runtime convergence](runtime-convergence.md) |

This page intentionally does not repeat the detailed invariants or acceptance
lists. A requirement change belongs in its detailed owner, with generated
schema/code and tests updated together when the wire contract changes.

## Architecture

Safe Engine projection enters the authenticated callback boundary. Canonical
public events and their receipt commit in PostgreSQL. A fenced publisher writes
Redis replay records and Pub/Sub notifications outside that transaction. The
API shares a live subscription across authorized browser subscribers, replays
retained records, and serves SSE. Frontend state accepts only validated events;
terminal content converges through authorized durable hydration.

PostgreSQL owns business facts, semantic order and durable publication work.
Redis is a bounded replay/live plane. Pub/Sub is a wake-up/delivery mechanism,
not a durable business record or a side-effect authorization.

## Version distinctions

SSE v4 is the only active public runtime. The internal callback receipt protocol
has its own v2.1 version. The retained Redis namespace contains `v3` for physical
key compatibility; it does not enable v3 browser negotiation.
ADR 0009 records earlier transport rationale, and earlier SSE ADRs are audit
history. No old version is a hidden fallback or feature flag.

Canonical v4 `message.delta` events are durable PostgreSQL events. The prohibition
on a PostgreSQL text fallback forbids an alternate browser polling/streaming
transport; it does not prohibit these canonical writes. The legacy
`assistant_delta` projection must not become a second durable body producer.

## Recovery boundary

Same-incarnation active recovery requires a validated server anchor plus durable
hydration. An unproven/missing current stream cannot be recreated under an issued
incarnation. Terminal successor rebuilding follows the existing fenced protocol.
Active successor creation requires a separate reviewed contract; this index does
not authorize it. SDK completion, Run completion, stream end, and final browser
hydration are distinct facts.

See the wire contract for the progressive timeline and its exact tool-interval,
sanitization, identity, body consistency, and final-hydration invariants. These
constraints are retained, not relaxed by the documentation consolidation.
