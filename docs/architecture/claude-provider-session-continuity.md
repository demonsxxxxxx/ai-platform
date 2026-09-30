# Claude Native Conversation Continuity

Status: source candidate; deployment and external acceptance are separate
Owner: Context + Execution + Runs
Last updated: 2026-09-28

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

- **Context** owns immutable conversation coverage receipts, scoped epoch
  binding, provider epoch lifecycle, opaque `SessionStore` entries, append
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
2. Admission reads the committed provider coverage under the lineage lock.
   Worker binds the scoped snapshot to that digest/count and current message.
   Neither stage reloads prior platform message bodies. The success transaction
   extends coverage with only this Run's user and assistant messages.
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

Admission claims the scoped lineage and reads the committed current epoch's
coverage digest/count. For a head with no current epoch, a scoped metadata-only
existence query rejects uncovered prior history. User messages from terminal
failed/cancelled Runs that never started and have no started or open Attempt
do not represent provider history. They may precede the first `empty_start`;
missing Run facts, assistant messages and started/uncertain Attempts still
require a new conversation. No historical content query or hash scan runs here.

Before ExecutionSpec/Attempt binding, the Worker:

1. loads the exact Run-bound Context snapshot and validates its scope and current
   message identity (Runs API tasks may have no business user-message row);
2. restores an already-running Attempt's frozen context when recovering that Run;
3. otherwise requires the current ready, writer-free epoch to match coverage and
   capacity before selecting `native_resume`;
4. selects `empty_start` only when there is no current epoch and the receipt has
   zero prior messages and the scope's initial digest;
5. rejects unavailable native continuity with
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
Committed provider epoch coverage
  -> Context coverage receipt under lineage lock
  -> Worker validates scoped snapshot/current message
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

The first callback claims the epoch writer and creates its turn receipt in the
same transaction, using the inserted row directly. Later callbacks read and
validate that receipt instead of attempting another insert. A missing or
inconsistent receipt is a conflict; callbacks do not recreate it under an
existing writer. Scoped locks, current Attempt/lease checks, owner generation,
and frozen coverage checks apply to every callback.

Execution captures the final provider sequence after the SDK's closing mirror
flush and after all message/control producers have stopped. The sandbox carries
that sequence through its terminal result to the existing coverage transaction.
Physical CLI/MCP teardown may continue under the executor's lifecycle owner;
it cannot append transcript entries or publish callbacks after this barrier.
Mirror failures still fail the turn. Process teardown failures after the barrier
are cleanup diagnostics and cannot replace the business result.

## Retirement And Compatibility

Removed production surfaces:

- checkpoint builder application and PostgreSQL build repository;
- checkpoint token-count and summarization model-control operations;
- Worker checkpoint preparation and expired-build maintenance;
- `platform_bootstrap` dispatch and historical Claude prompt rendering;
- checkpoint usage merge into Run terminal token counts;
- tests that exclusively asserted the retired build/lease/summarization path.

Legacy checkpoint tables and rows remain in the schema, but their loader and
Worker materialization path are removed. Explicit historical-message lists,
range/tail/checkpoint verification, and both admission and Worker full-body
scans are retired. New v2 coverage receipts contain only scope, Run generation,
message count, digest, and current-message identity. Shared scoped message
retrieval remains a separate authorized retrieval API; it is not a Worker
continuation fallback and Claude does not receive that capability.

Platform display-history edits are no longer audited by rereading all bodies
before each turn. Native transcript entries and atomic provider coverage are
the continuity authority; this is an intentional contract change.

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
