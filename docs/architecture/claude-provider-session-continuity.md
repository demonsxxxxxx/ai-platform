# Claude Native Conversation Continuity

Status: source candidate; deployment and external acceptance are separate
Owner: Context + Execution + Runs
Last updated: 2026-09-22

## Decision

Claude conversation continuity has one production path: the Claude Agent SDK
`SessionStore` persisted through the platform callback boundary.

- A genuinely new conversation uses `empty_start` with one stable provider UUID.
- A later turn uses `native_resume` only when one ready provider epoch exactly
  covers the immutable conversation source receipt and remains below the
  provider-entry and transcript limits.
- The SDK user turn is exactly the current Run `input.message` (or the legacy
  current `input.prompt` alias). Current platform, Agent Profile, Skill catalog,
  file/material, language, and response instructions use the controlled system
  channel. Agent Profile instructions remain capped at 16,000 characters; the
  composed private executor system channel is separately capped at 64,000 so
  bounded control material can be appended without weakening profile admission.
- Platform Messages, Context snapshots, and source digests remain authorization,
  audit, and provider-coverage receipts. Their historical bodies are never
  reconstructed into the Claude prompt.
- Claude Code owns provider transcript ordering, token accounting, context-window
  handling, summaries, and automatic compaction.

`platform_bootstrap`, platform checkpoint summarization, and fallback
reconstruction are retired. Continuity loss never degrades to `empty_start`
inside an existing conversation.

## Change Contract

### Owner

- **Context** owns immutable conversation source receipts, exact scoped source
  verification, provider epoch lifecycle, opaque `SessionStore` entries, append
  receipts, turn receipts, and lineage release.
- **Execution** owns the frozen `empty_start`/`native_resume` dispatch contract,
  the current user/system channel split, model-proxy authorization, and the
  SDK `SessionStore` adapter.
- **Runs** owns Run/Attempt lifecycle, model capacity binding, ExecutionSpec,
  lease fencing, terminal state, and public terminal projection.
- **Claude Code** owns model-facing history, transcript compaction, compaction
  summaries, token counting, and model context-window behavior.

### Scope

This change is limited to:

- `app/context/**` and Context composition;
- provider-session dispatch and model proxy code under `app/execution/**`;
- Claude prompt/adapter composition;
- Worker pre-dispatch and maintenance composition;
- Runs terminal composition previously coupled to checkpoint usage;
- owning tests and these architecture documents.

No frontend, SSE wire, public message schema, authorization policy, provider
callback protocol, database migration, or deployment change is in scope.

### Preserved invariants

1. Tenant, workspace, user, Session, Agent, Run, Attempt, lease, model, and tool
   boundaries remain platform-owned.
2. Worker verification reads every message authorized by the exact immutable
   source receipt, validates scope/order/count/digest, and retains no historical
   body for Claude model input.
3. Historical tool calls never restore current capabilities. Every Run rebuilds
   Agent Profile, Skill Set, MCP/tool authorization, credentials, files, and
   system policy from current authority. Claude's current capability set excludes
   `read_session_messages`, so platform message bodies cannot re-enter through a
   retrieval tool.
4. Provider UUIDs, transcript entries, callback credentials, runtime paths, and
   storage details remain private.
5. `SessionStore` append/load remains callback-authenticated, ordered,
   idempotent where the SDK supplies UUIDs, and bounded by entry/batch/transcript
   limits.
6. Ordinary `/v1/messages` requests are forwarded without platform token-count
   preflight. Explicit Claude `/v1/messages/count_tokens` requests remain
   Run/Attempt-bound authorized proxy traffic.
7. Claude automatic compaction uses the Run-frozen `max_input_tokens`, clamped
   to the bundled CLI numeric range `100_000..1_000_000`. The platform does not
   issue `/compact` or apply a second percentage reserve.
8. Successful structured SDK completion may use accepted streamed assistant
   text when `ResultMessage.result` is empty; a wholly empty terminal remains
   fail-closed.

### Continuity admission

Before ExecutionSpec/Attempt binding, the Worker:

1. loads the exact scoped Context snapshot;
2. streams all authorized historical message rows through the source-digest
   verifier without projecting them into the Claude prompt;
3. requires a ready provider epoch whose coverage digest and message count equal
   the verified receipt and whose entry/transcript headroom is valid;
4. selects `native_resume` when that exact epoch exists;
5. selects `empty_start` only when the verified prior-message count is zero; and
6. otherwise terminalizes the Run with
   `provider_session_requires_new_conversation`.

Missing, dirty, closed, over-capacity, coverage-mismatched, or concurrently
owned native state therefore requires a new platform conversation. It cannot
rotate to an empty provider transcript under the existing Session.

### Acceptance criteria

- A first Claude turn reaches the SDK as `empty_start`; its user content equals
  only the current input message.
- A covered later turn reaches the SDK as `native_resume` with the same provider
  UUID and persisted ordered `SessionStore` entries.
- No platform message body, checkpoint summary, or checkpoint-generated text is
  present in Claude user input or system input.
- Dirty, closed, missing, over-capacity, or digest/count-mismatched native state
  fails before Attempt binding with the stable new-conversation error.
- A different tenant/workspace/user/Session/Agent/Run/Attempt/lease cannot load
  or append provider entries.
- Current profile, Skill, tool, file, credential, model, and authorization
  changes still apply on resume.
- Existing explicit count-token proxy behavior, terminal streamed-text fallback,
  public non-disclosure, and lineage commit checks remain green.

### Stop conditions

Stop and revise this contract if implementation requires:

- reconstructing Claude history from platform Messages or checkpoint summaries;
- silently starting fresh after native continuity loss;
- trusting sandbox-supplied scope or provider identity;
- exposing opaque transcript entries publicly;
- restoring historical tools or credentials;
- changing SSE, callback receipt, or public message protocols; or
- retaining two active Claude continuity paths.

## Runtime Flow

```text
Platform Messages (immutable authority/audit)
  -> Context source receipt
  -> Worker streams rows only to verify scope/order/digest/count
  -> exact provider epoch match
       prior count = 0              -> empty_start
       exact ready covered epoch    -> native_resume
       anything else                -> requires new conversation

Current Run authority
  -> controlled system channel (profile/policy/skills/material refs)
  -> current input.message only (SDK user channel)

Claude SessionStore
  <-> authenticated host callback
  <-> ordered opaque PostgreSQL provider entries
```

The provider transcript is the only model-facing conversation history. Context
receipts prove what that transcript must cover; they do not become model input.

## Retirement And Compatibility

Removed production surfaces:

- checkpoint builder application and PostgreSQL build repository;
- checkpoint token-count and summarization model-control operations;
- Worker checkpoint preparation and expired-build maintenance;
- `platform_bootstrap` dispatch and historical Claude prompt rendering;
- checkpoint usage merge into Run terminal token counts;
- tests that exclusively asserted the retired build/lease/summarization path.

Legacy checkpoint tables and rows remain in the schema. No new checkpoint is
built or billed. The ready-checkpoint loader remains temporarily for historical
non-Claude snapshots and compatibility inspection. The shared scoped message
retrieval API remains only for identified non-Claude consumers; Claude capability
planning filters `read_session_messages`. Historical Claude snapshots with a
checkpoint ancestry are verified from their full authorized message range; their
checkpoint summary is ignored.

This is an intentional compatibility break for existing conversations without
one exact usable native epoch: users must start a new conversation. There is no
fallback owner and no planned removal date for the retained read-only data;
future schema retirement requires a separate migration contract and inventory.

## Evidence Ceiling

Local tests and static checks prove source behavior only. They do not prove a
packaged image, real PostgreSQL migration behavior, cross-sandbox provider
resume, deployed runtime state, or external acceptance. Those require the exact
candidate artifact and separate release evidence.

## Rollback

Rollback means reverting the complete source change. It must not re-enable
`platform_bootstrap`, checkpoint summarization, or prompt reconstruction as a
runtime fallback, and it must not delete provider or checkpoint rows ad hoc.
