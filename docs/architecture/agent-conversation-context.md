# Agent Conversation Context

Status: active source contract
Owner: Context; engine adaptation is owned by each executor integration
Last updated: 2026-09-22

## Purpose

Platform conversation data has two responsibilities that must not be confused:

1. authorize and audit which same-session messages a Run may continue from; and
2. supply model-facing conversation history when an Engine does not own durable
   native continuation.

Claude uses only the first responsibility. Its model-facing history is the
native Claude `SessionStore`. Other engines may still consume an
executor-private platform message projection under their own adapter contract.

## Authorities

**Platform Messages**
: Immutable public conversation rows ordered by `(created_at, id)`. They remain
  the UI, authorization-source, audit, and provider-coverage authority.

**Conversation Source Receipt**
: The immutable Run-bound scope, range, count, and digest of authorized prior
  Messages. It proves coverage; it is not itself model context.

**Claude Provider Epoch**
: One scoped, ordered, opaque native transcript with a stable provider UUID,
  state, capacity, and exact source-coverage receipt.

**Executor Conversation Context**
: An engine-private projection. For Claude v2 it contains receipts and an empty
  `messages` array; for a non-native engine it may contain verified platform
  message bodies.

**Current Run Context**
: Current Agent Profile, Skill/tool policy, credentials, model binding, files,
  artifacts, memory refs, and the exact current input. It is rebuilt for every
  Run and is never restored from historical provider entries.

## Source And Verification

1. Context admission claims the scoped provider lineage and constructs one
   source receipt over all prior user/assistant Messages authorized for the
   Session and current Run generation.
2. The receipt excludes the current Run's user message from prior history and
   records that current message identity separately.
3. Worker materialization reloads every authorized row in durable order,
   validates scope, role, range, count, and digest, and fails closed on a
   missing, duplicate, reordered, out-of-scope, or changed row.
4. Message bodies used for verification are not public projections or logs.
5. A retry of the same immutable snapshot verifies the same source receipt.
6. Historical system messages and historical tool calls never enter the
   authorized user/assistant source range.

## Claude Adaptation

Claude has no platform-history bootstrap mode.

1. Worker source verification retains no historical message body in the Claude
   executor context.
2. A ready provider epoch is resumable only when its coverage digest and message
   count exactly equal the verified source receipt and its entry/transcript
   capacity has sufficient headroom.
3. Exact coverage selects `native_resume`.
4. A zero-prior-message source selects `empty_start`.
5. Any non-empty source without one exact usable native epoch raises
   `provider_session_requires_new_conversation` before ExecutionSpec/Attempt
   binding.
6. Dirty, closed, missing, over-capacity, or coverage-mismatched epochs cannot
   rotate to a fresh transcript under the same platform conversation.
7. The SDK user turn is exactly the current `input.message` (or the legacy
   current `input.prompt` alias). Platform control instructions, Agent Profile,
   authorized Skill metadata, file/material refs, language policy, and response
   policy use the controlled system channel.
8. Claude Code alone owns transcript ordering, token accounting, context-window
   behavior, summaries, and automatic compaction.

The platform does not issue `/compact`, inspect context usage before dispatch,
pre-count ordinary `/v1/messages`, inject stored message bodies, or generate a
conversation summary. Explicit SDK-generated `/v1/messages/count_tokens`
traffic remains authorized Run/Attempt-bound proxy traffic.

## Non-Claude Adaptation

An Engine without owned native continuation may materialize verified prior
Messages into its executor-private context. That path:

- preserves complete ordered user/assistant turns;
- excludes historical system/tool capability state;
- never exposes private bodies through public context projections; and
- remains independent from Claude provider epochs.

No new platform checkpoint is built for any Engine by this contract. A future
engine-specific summarization feature requires its own owner, schema, limits,
and Change Contract.

## Current Capability Boundary

Historical context never restores capabilities. Every Run derives these from
current platform authority:

- Agent Profile and profile revision;
- Skill Set, selected Skill, dependencies, and release decisions;
- MCP/native tool authorization and permission policy;
- model value, gateway revision, and capacity;
- credentials and callback capability;
- files, artifacts, and memory references; and
- Run/Attempt/lease ownership.

Provider transcript content is opaque and cannot override these bindings.

## Projection And Non-Disclosure

- Ordinary-user projections exclude source message IDs, provider UUIDs, raw
  provider entries, checkpoint summaries, storage keys, runtime paths, callback
  credentials, and executor-private payloads.
- Public context summaries may expose bounded counts and safe provenance only.
- Context failures use stable error categories and do not echo historical text.
- Provider transcript entries are never interpreted or rendered by platform
  code.

## Acceptance Criteria

- Claude first turn uses `empty_start` and sends only the current message in the
  SDK user channel.
- Claude later turn uses `native_resume` only with exact ready coverage.
- Platform historical bodies and checkpoint summaries are absent from every
  Claude prompt channel; Claude is not authorized to call
  `read_session_messages` as a retrieval tool.
- Worker still detects a changed authorized Message before dispatch.
- Missing, dirty, closed, over-capacity, or mismatched Claude native state
  requires a new conversation before Attempt binding.
- Resume applies current tools, profile, credentials, model, and file/material
  authority rather than historical capabilities.
- Non-Claude private materialization and public redaction behavior remain green.

## Removed Behavior

The active execution path no longer uses:

- `platform_bootstrap`;
- platform-generated conversation checkpoint summaries;
- checkpoint token counting or summarization model calls;
- Worker checkpoint preparation or expired-build maintenance;
- checkpoint usage merged into terminal Run token counts;
- stored platform message bodies rendered into Claude prompts;
- message retrieval as automatic recovery for missing Claude continuity; or
- silent `empty_start` fallback for an existing conversation.

Legacy checkpoint rows and the shared non-Claude context-retrieval API remain
read-only compatibility surfaces. Historical non-Claude snapshots may still
load a ready checkpoint they explicitly name, and identified non-Claude clients
may still use scoped message retrieval. Historical Claude snapshots ignore a
checkpoint summary, verify the full source range, and never expose
`read_session_messages` to the SDK.

## Delivery Boundary

This source change does not claim deployment, packaged-image behavior,
PostgreSQL runtime migration, or cross-sandbox external acceptance. It changes
no public SSE, message, callback-receipt, or frontend contract.
