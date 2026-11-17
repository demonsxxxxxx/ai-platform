# Redis Streams SSE Event Channel v4

Status: index of the active v4 source contract. Deployment and External
Acceptance require separate evidence.
Design ID: `ai-platform.redis-streams-sse-event-channel.v4`.

## Detailed owners

| Concern | Owner |
| --- | --- |
| Rationale and supersession | [ADR 0013](../adr/0013-redis-stream-only-sse.md), amended by [ADR 0018](../adr/0018-postgres-replay-redis-live.md) |
| Envelopes, SDK/callback boundary, progressive timeline, replay/live, cursor/gap and client acceptance | [Wire protocol](redis-streams-sse-wire-protocol.md) |
| Admission, committed callback/Run publication, authorization leases and terminal convergence | [Execution control](redis-streams-sse-execution-control.md) |
| Gateway, cutover, deployed fault injection and acceptance | [Cutover acceptance](../operations/redis-streams-sse-cutover-acceptance.md) |
| Cross-component convergence proposals | [Runtime convergence](runtime-convergence.md) |
| Public Assistant text, Tool separation, and optional file delivery | [Streaming message design](../implementation/streaming-message-parts-design.md) |

This page intentionally does not repeat the detailed invariants or acceptance
lists. A requirement change belongs in its detailed owner, with generated
schema/code and tests updated together when the wire contract changes.

## Architecture

Safe Engine projection enters the authenticated callback boundary. Canonical
public events and their receipt commit in PostgreSQL. PG bounded sequence replay
is the durable authority; Redis remains a strictly validated low-latency path.
Callbacks acknowledge committed PG receipts after a 250 ms bounded best-effort
Redis attempt; the execution contract owns failure classification and evidence.
Frontend state accepts only validated events and stores a consumed durable
cursor; terminal hydration remains independently authorized.

New Run admission still requires Redis open confirmation before dispatch.
Already admitted Runs recover from PG even when Redis is missing. No publication
queue, background publisher or successor recovery is introduced.

## Version distinctions

SSE v4 is the only active public runtime. The internal callback receipt protocol
has its own v2.1 version. The retained Redis namespace contains `v3` for physical
key compatibility; it does not enable v3 browser negotiation.
ADR 0009 records earlier transport rationale, and earlier SSE ADRs are audit
history. No old version is a hidden fallback or feature flag.

Canonical v4 `message.delta` events are durable PostgreSQL events. PG replay
uses this one canonical source, not a legacy `assistant_delta` fallback.

## Recovery boundary

PG replay preserves current authority, Attempt/incarnation and authorization
lease fences. Confirmed authority supplies open; observed PG terminal supplies
end. Redis absence is not a durable history gap. SDK completion, Run completion,
stream end and browser hydration remain distinct facts. Source, CI and external
acceptance are separate evidence; see the owning contracts for slice status.

See the wire contract for the progressive timeline, sanitization, identity,
body consistency, and final-hydration invariants. The streaming message design
uses the adopted coordinated v4 extension for `message.part.delta`,
`message.part.classified` and final answer selection with answer receipt v2;
it does not alter the transport above. The wire contract's
[part extension and release/rollback rules](redis-streams-sse-wire-protocol.md#change-contract-incremental-assistant-parts-and-final-selection)
require coordinated producers/readers: old clients reject the new events, and
updated readers must remain while new facts or v2 receipts exist. Legacy v1
receipt readers remain supported.
