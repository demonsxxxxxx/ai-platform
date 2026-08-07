---
status: superseded
superseded_by: 0003-redis-streams-sse-event-channel-v2-correction.md
amended_by: 0003-redis-streams-sse-event-channel-v2-correction.md
---

# Use Redis Streams for bounded SSE replay and PostgreSQL for durable final facts

ADR 0002 records the historical v1 decision identified as
`ai-platform.redis-streams-sse-event-channel.v1`.

The accepted v1 source was
`73b37ff40f965dcfb7b9f2a9f499d7d5fb32be11` and was merged by
`5d5a0c537baa0af2d9c47cb8d010a713c5240dc6`. The rejected intermediate review
head `0b61acd8e4a819a72dffa9591707d86f598ad3a4` remains historical evidence only.

V1 correctly selected bounded Redis replay, independent `XREAD` readers,
incarnation-bound cursors and explicit gaps, PostgreSQL durable final facts,
PostgreSQL commit before terminal/end, accepted-cursor advancement after reducer
commit, and no steady-state PostgreSQL/Redis text-delta double write.

V1 is superseded because its stage ordering placed PostgreSQL admission authority
too late, its revocation wording implied an unimplementable commit-time zero-frame
guarantee, and its mid-run Redis policy did not distinguish degraded live
transport from bounded execution authority.

ADR 0003 and design ID `ai-platform.redis-streams-sse-event-channel.v2` are the
only current implementation authority. This file preserves the accepted v1
identity and audit history; it does not silently change v1 semantics or authorize
implementation. No v1 parser, PostgreSQL polling loop, delta-write path, runtime
feature flag, or fallback may treat this historical text as a runnable option.
