# Claude Native Conversation Continuity

Status: source candidate; deployment and external acceptance are separate
Owner: Context + Execution + Runs
Last updated: 2026-10-22 (Beijing time)
Last updated: 2026-10-25

## Decision

Claude conversation continuity has one production path: the Claude Agent SDK
`SessionStore` persisted through the platform callback boundary.

- A genuinely new conversation uses `empty_start` with one stable provider UUID.
- A later turn uses `native_resume` only when one ready provider epoch exactly
  covers the immutable conversation source receipt and remains below the
  provider-entry and transcript limits.
- The SDK user turn is exactly the current Run `input.message` (or the legacy
  current `input.prompt` alias). A short file-task core, current Agent Profile,
  and bounded Skill/material metadata use the controlled system channel.
  Shared language, file-integrity, workspace, progress and delivery rules live
  once in the platform-owned read-only workspace `CLAUDE.md`; the SDK loads it
  as a project-instruction reminder in the model conversation, not as a second
  platform-authored user request. Agent Profile instructions remain capped at
  16,000 characters; the private executor composition remains bounded at 64,000.
- Admission preserves those top-level scalar user-text fields verbatim, including
  paths and JSON literals. They remain private execution input and cannot grant
  capabilities. Server-owned control fields and current authorization are still
  checked independently; public responses continue to use their own path and
  secret redaction. A public-output filter must not erase the current request or
  cause the legacy prompt alias to replace a nonempty current message.
- Platform Messages, Context snapshots, and source digests remain authorization,
  audit, and provider-coverage receipts. Their historical bodies are never
  reconstructed into the Claude prompt.
- Claude Code owns provider transcript ordering, token accounting, context-window
  handling, summaries, and automatic compaction.

`platform_bootstrap`, platform checkpoint summarization, and fallback
reconstruction are retired. Continuity loss never degrades to `empty_start`
inside an existing conversation.

## File-task prompt composition

The dependency is pinned to Python SDK `0.2.174` (bundled CLI `2.1.310`).
Execution passes a custom prompt object with `snapshot: false`: the CLI must
rebuild the current system prompt when resuming, rather than replay the first
Run's Profile and material instructions. Ordinary strings use snapshot recording
in this CLI and do not meet that contract. A changed system prefix cannot reuse
the old prompt cache; APIs enforcing preserved thinking may discard earlier
thinking blocks. Platform authorization and native transcript continuity remain
independent of those model-cache semantics.

The SDK settings disable `autoMemoryEnabled`, `includeGitInstructions` and
commit/PR attribution. These retire SDK-local file memory and Git workflow prose,
not platform-scoped memory retrieval, Bash, native session storage or compaction.
`setting_sources=["project"]` and the existing workspace scrubbing remain.

Current manifest-backed material prompts contain authorized references and
available retrieval tools, not generated summaries, timestamps or version/count
receipts. Empty material sets add no prompt section. Legacy snapshots without a
manifest retain their sanitized bounded summary when material presence is unknown
or nonempty; explicit zero material counts omit it. That compatibility belongs to
Context until those persisted snapshots are retired. Historical message bodies
never enter either path. Skill rendering uses Unicode and only discovery name,
description, availability and invocation handle, with truncation metadata; internal
canonical snapshots, version pins and digests are unchanged. Only staged Skills
are callable. Existing tool and MCP admission, including NotebookEdit consumers,
are unchanged; no new Agent/browser/network capability is enabled.

Retirement inventory: the production `claude_code` preset, duplicated language/
delivery/workspace control prose, empty file placeholders and manifest audit
rendering are removed. Owning prompt/worker/catalog tests follow the new semantic
split, and the installed SDK selector is `test_installed_claude_agent_sdk_02174_contract`.
The 0.2.130 upgrade document remains historical, not an active dependency pin.
The owning regressions are `test_context_prompt_continuity.py`,
`test_authorized_skill_catalog.py`, `test_claude_agent_sdk_runner.py`,
`test_claude_agent_worker_adapter.py`, `test_sandbox_workspace_manager.py`,
`test_claude_agent_sdk_installed_contract.py` and `test_packaging_contract.py`.
Review the actual inventory with scoped searches for `preset.*claude_code`,
`_PUBLIC_LANGUAGE_INSTRUCTION`, `_RESPONSE_FILES_INSTRUCTION` and `02130` in
those production/test files; a historical document is not a compatibility path.

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
- the additive nullable JSON transcript representation migration and readiness
  check described below;
- owning tests and these architecture documents.

Lifecycle recovery additionally changes the existing status/history continuation
projection, cancellation settlement and private workspace composition. SSE event
identity and the ordinary public message shape remain unchanged; there is no
deployment in this source change. The provider callback accepts optional
`provider_epoch_id` as an additional exact frozen-binding check. Existing actions,
append ACK shape and the opt-in child read extension below remain compatible.

### Preserved invariants

1. Tenant, workspace, user, Session, Agent, Run, Attempt, lease, model, and tool
   boundaries remain platform-owned.
2. Admission reads the committed provider coverage under the lineage lock.
   Worker binds the scoped snapshot to that digest/count and current message.
   Neither stage reloads prior platform message bodies. Success retains v2 user/
   assistant coverage. A stopped accepted partial turn uses explicit v3
   `native_accepted_messages` coverage, without inventing an assistant message.
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
7. Claude automatic compaction uses the Run-frozen `auto_compact_window`, which
   is validated into the bundled CLI numeric range `100_000..1_000_000` at
   publication and forwarded unchanged. Historical v2 attempts recover the same
   window from their frozen input capacity. The platform does not issue
   `/compact` or apply a second percentage reserve.
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

Missing, unverifiable, over-capacity or coverage-mismatched state cannot silently
rotate to an empty transcript. Concurrent or settling ownership waits for its
original owner. A verified stopped partial epoch can become a recovery candidate;
an unused target closed before any input was dispatched leaves the previous head
unchanged and does not poison first-turn admission.

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

### Per-Attempt native and private file recovery

Every new Attempt writes to a new bounded epoch containing an exact copy of the
selected source's complete ACK prefix, including child paths and original append
receipt provenance. Provider UUID stays constant; its global uniqueness is retired.
Callbacks resolve the epoch from the current frozen ExecutionSpec, not UUID alone.
The source is immutable: a failed child Attempt cannot add its tail to another
Attempt resumed from the original source. The existing 4096-entry/8-MiB budget and
admission reserves remain; full copies have bounded but repeated storage cost.

Active cancellation records intent and revokes new execution. The original live
writer may drain append and terminal receipts under its narrowly scoped previous
generation lease; it cannot load, start tools, accept inputs or survive takeover.
Reconciliation observes the same Attempt before redispatching anything. Stop or
typed 404 must identify the exact provider instance. Host-bind save follows stop;
Context settlement, true Run terminal/events and lease release follow in one short
CAS transaction. Failure to stop retains the gate and retry obligation. A prepared
Attempt stopped before its first callback may close only with explicit undispatched
input/no-unknown-ACK evidence and a verified unchanged target/source; no writer,
turn or append receipt is fabricated.

A runtime failure before the first provider call carries `provider_not_created`,
which is distinct from a provider 404. A failure after creation but before dispatch
carries stopped/not-found evidence only after stopping the exact container. The
Worker preserves the original exception and commits this private receipt, Context
closure, Run/Attempt terminal CAS and lease/lineage release atomically. The runtime's
exact generation must match; the existing terminal CAS bridge advances that proof
only from its actual previous generation. A writer claim rejects this path.
Ambiguous creation or dispatch failures carry no such receipt, and old terminal
Attempts without recorded facts cannot acquire one retroactively. No superseded
implementation or compatibility surface is retired by this additive failure path.

Private versions live outside the mounted workspace, separately from downloadable
artifacts. Save excludes original inputs/context staging, credentials, runtime
configuration and Skill code; it preserves ordinary drafts, deletions, scratch
files and authorized Skill outputs. Restore reserves an absent new workspace,
verifies the complete version, restores ordinary files, then stages current inputs
and Skill code. Authorized Skill outputs are published only after real staging via
`restore_workspace_skill_outputs`; existing packaged output is a conflict, never
a merge or overwrite. A private journal fences retry ownership and directory
identity. No-follow, flock, fsync/atomic publication and file/byte/4096-scan budgets
apply. Storage completion retains physical ownership through cancellation.

A fault candidate starts in actual tool-boundary inspection: selected file reads
and explicitly non-writing MCP queries may run; Bash, code, Agent and unconfirmed
writes cannot bypass it. Before SDK input, the authenticated RunInputs startup
publishes host questions for exact frozen unknown source operations, including
calls whose original native history already contains an error result. Source-only
confirmations have a distinct binding kind; they verify outcomes without asserting
a new native call or granting execution. The SDK waits for those host questions
through the existing input callback. Once the owner answer is applied, a bounded
private snapshot may expose only that current Attempt's freshly authorized public
operation summary, verified outcome and owner-confirmation provenance to the
existing current system context. This preserves truthful reporting without
changing the original user/file input, copying native history or claiming an MCP
query/tool result. Unknown/default/hold answers carry no verified fact, and these
facts never grant execution or lift inspection. Original operation evidence joins are retained
as private `operation_sources` through later stop/save settlement, separately from
the latest native restore epoch. A no-write successful turn cannot clear inherited
inspection, and outcome verification alone is not broad write authorization.
Execution records host-issued operation identities before
external calls. Started/completed sandbox reports do not verify outcomes. Only an
authenticated owner response to an exact host question or a registered authoritative
query can verify an operation. Exact native `tool_use_id` binding uses the frozen
source epoch, never tool name or model text. The service supports conclusive host
query adapters and authenticated operation-specific answers; this candidate has
no registered authoritative query adapter. Administrators can declare a public
action and a single business-object field through existing MCP registration, as
specified in [MCP public recovery summaries](mcp-tool-execution.md#public-recovery-operation-summaries).
Recovery compares source/current frozen declarations with current authorization,
reads the exact invocation from the verified native ACK prefix, and publishes
only that declared value. Lease validity is checked again with database wall-clock
time after external principal lookup. Omitted registration fields preserve the
current declaration under its owning lock; explicit `{}` revokes it without a
concurrent omitted-field update restoring an older declaration.
Missing or invalid declarations still record started/completed facts and retain
safe inspection, but refuse ambiguous manual questions and old answers with a
fixed safe reason. Confirmed success must never authorize replay; verified
non-effect plus explicit retry consent applies
only to the exact current invocation. There is no general compensation engine or
exactly-once guarantee.

Status/history expose only `state`, `action`, allowlisted `reason_code` and
`workspace_saved`; SDK/epoch/version/lease IDs and paths stay private. Settling and
unconfirmed history reads retain the draft and block actual send. Successful legacy
responses with no continuation field retain compatibility; a failed GET is not a
legacy response. Ordinary active supplementary input remains available until real
cancellation/terminal settlement begins. Capacity exhaustion preserves history and
saved files and uses the existing new-conversation action, without an unlimited
continuation or redo promise.

Migration `2026.10.22.2` appends the Context and Execution-owned SQL fragments through
`schema_migrations.schema_sql()`. Additive migration `2026.10.23.1` distinguishes
host source-operation confirmations from current native-invocation bindings in
the existing Execution table; historical bindings retain their original meaning.
Quiesce all old writers before applying it: old
binaries select epochs by UUID and cannot safely coexist with per-Attempt UUID
reuse. Do not binary-rollback a populated multi-epoch lineage to that reader;
retain data and recover with a compatible binary. The old single-epoch mutation,
active-cancel immediate kill and failed-turn-always-new-conversation rules have no
compatibility consumers in this candidate and are retired. Legacy v2 successful
coverage and old callback shapes remain supported under their original meaning.

Owning checks: `test_conversation_recovery*.py`,
`test_provider_unclaimed_continuation*.py`, `test_predispatch_continuation_postgres.py`,
`test_runs_worker_execution_terminal.py`, `test_workspace_versions.py`,
`test_lifecycle_control_settlement.py`, `test_recovery_operations*.py`,
`test_conversation_lifecycle_integration.py`, and
`tests/integration/lifecycle_cli/test_real_cli_postgres_callback.py`. Focused real
CLI/PG/filesystem evidence is separate from a deployed model/Sandbox acceptance.

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
  <-> ordered PostgreSQL provider entries + private MinIO image payloads
```

The provider transcript is the only model-facing conversation history. Context
receipts prove what that transcript must cover; they do not become model input.

The first callback claims the epoch writer and creates its turn receipt in the
same transaction, using the inserted row directly. Later callbacks read and
validate that receipt instead of attempting another insert. A missing or
inconsistent receipt is a conflict; callbacks do not recreate it under an
existing writer. Scoped locks, current Attempt/lease checks, owner generation,
and frozen coverage checks apply to every callback.

Before returning native entries or child paths, the callback verifies the
whole bounded epoch against its existing entry count, contiguous global
sequence, transcript byte count and append-batch digests. Child paths share
that global sequence; a main-only scan cannot prove epoch completeness. This
checks the native records already being restored, not platform message bodies.
A mismatch reports `provider_session_integrity_mismatch`; absent complete
append receipt coverage reports `provider_session_integrity_unavailable`.
Neither condition silently resumes a prefix or fabricates replacement receipts.

### Private image persistence

The pinned Python SDK is `claude-agent-sdk==0.2.174`, with bundled CLI
`2.1.310`. PostgreSQL remains the owner of ordered entry structure, scope,
sequence, original batch digest, and turn receipts. Only recognized image
payload strings are stored in MinIO; entire entries/transcripts are not blobs.
The Execution adapter locates the SDK's typed base64 image blocks and the
native Read `toolUseResult.file.base64` copy. Unknown JSON shapes and ordinary
text remain inline. Identical image strings within one append share one object.

Context stores the exact UTF-8 bytes of each encoded image string, without
base64 re-encoding, conversion, resizing or recompression. The entry's private
`image-payloads-v1` sidecar records the JSON leaf path, image ID, size and digest;
the canonical PostgreSQL JSON representation contains the structure with empty
image leaves, while the sidecar remains JSONB metadata. Restore verifies object
bytes and fills every leaf before checking
the unchanged full-entry batch digest and transcript byte count. The SDK receives
deep-equal original entries, never image IDs, storage keys or URLs. A missing or
corrupt object fails continuity; it cannot become a placeholder or summary.
Legacy inline entries and their existing receipts are read without backfill.

The callback uses two short transactions. The first validates authority,
recognizes already committed ACK retries, rejects sequence/capacity conflicts,
and reserves upload cleanup before object I/O. MinIO I/O uses the existing
bounded storage executor outside those locks. The second validates the current
scope, active Session, Run/Attempt, writer generation, live lease and sequence;
image promotion, entry insertion, original receipt and both byte counters commit
together. Load also rechecks the image metadata snapshot. A committed ACK retry
requires no object upload. Outstanding uncommitted images per epoch are capped
at 128 objects and 64 MiB; durable deletion receipts release that reservation
budget. Cancellation may only persist the existing qualified tail writer.

Admission copy, terminal settlement, unclaimed-copy comparison, original native
operation inspection and explicit checkpoint repair use the same Context-owned
media verification scope. A short authorized snapshot transaction reads the exact
Run/Attempt binding, scoped epoch ledger and image metadata. The bounded storage
executor restores one epoch at a time after that transaction exits and verifies
its original full-entry ACK digests. A task-local proof retains prefix byte
counts, required native call blocks and an integrity stamp of the structured
ledger/metadata; image bytes and reconstructed transcript are released after
verification. This proof is neither persisted nor sent to the SDK.

The owning atomic transaction rereads the ledger and image metadata and rechecks
the current Run/Attempt binding before consuming the proof. Changed ownership,
sequence, entries, receipts, references or storage metadata invalidates it. The
original ACK still authenticates the full native bytes; the temporary integrity
stamp only detects changes across the storage gap. Copies preserve image format,
references, original receipts and both independent byte counters. Callback
credentials and manual-answer authorization keep their existing live rechecks.
Nested checkpoint settlement reuses only that same Run's verification scope and
cannot initiate storage I/O while the outer repair transaction holds locks.

Limits are separate: 8 MiB per encoded image, 128 extracted fields per batch,
16 MiB per raw entry, 32 MiB per append batch and 64 MiB raw cumulative
transcript. Native resume retains 16 MiB within that raw limit, so its admission
boundary is 48 MiB. The image-free entry remains at most 256 KiB; structured byte
totals are retained for integrity accounting but impose no aggregate capacity
limit. The 4096-entry cap and 128-entry/32 MiB append limits remain. CLI stdout
buffering and private callback response limits accommodate the bounded restored
transcript. Public assistant-text limits are unchanged. These are resource
limits, not a promise that every model accepts an image of that size.

`provider_session_images` owns private object metadata and survives parent
session/epoch deletion. The existing deletion outbox owns each provisional
upload before it starts; uncertain/failed writes remain eligible for cleanup,
including repeated deletion after a late write. Session deletion queues active
images in bounded batches, with the existing maintenance loop handling the
remainder and physically removed parents. Images use the disjoint
`provider_image_*` outbox states, preventing old artifact/file workers from
claiming them. Existing scoped `provider_session_entries.image_references` own
shared use across native epoch copies; the image row retains its original creator
and storage identity. Copy verification holds share locks on ledger and image
rows through commit. Cleanup locks images with `SKIP LOCKED` and retires an object
only when no entry in any live, same-scope epoch references it. Source-entry
retirement cannot delete an object still held by a copy; removal of the final
live reference makes it eligible for the existing deletion outbox. A rolled-back
copy adds neither image objects nor durable references. Original source-epoch
provenance constraints and Session retention are unchanged; no automatic source
history deletion is introduced. No public File/Artifact row, schema backfill,
parallel reference table or new cleanup worker is introduced.

Compatibility disposition: the previous smaller all-inline private limits and
writer are superseded. The per-entry structured 256 KiB, raw entry 16 MiB, raw
transcript 64 MiB, batch 32 MiB and count limits remain. Inline history remains a
read format, not a second writer. Schema/readiness authority advances to
`2026.10.27.1`; concurrent-index authority is unchanged. Old binaries cannot restore externalized entries or
process the new deletion target; an image swap is not a supported rollback.
Deploy matching API/Worker/Sandbox binaries after migration under the release
runbook. No migration rewrites historical hashes, failed Runs or dirty epochs.

### Bounded child restore view

The adapter requests `X-AI-Platform-Provider-Read-View: children-v1` only on the
existing `list_subkeys` action. An opted-in host may attach `child_entries`, a
typed mapping from every returned child subpath to its ordered entry list.
The mapping comes from that callback's already verified whole-epoch rows, not
another query or a main-only integrity check. Main entries remain in their
ordinary preload response. The response's existing `next_sequence` must match
the adapter's main preload; the host still binds the exact provider UUID,
epoch, frozen context, Run, Attempt, writer generation and live lease.

Context's `transport/provider_session_responses.py` owns the response model,
typed-result projection, normal JSON encoder and measurement of the actual
`JSONResponse` bytes it returns. The existing `bootstrap/execution.py` composes
that boundary alongside the SessionStore adapter; the legacy callback route
delegates without assembling or encoding a second response. The old
`runtime/sandbox/contracts.py` model import remains an alias to this same class
for the identified router and contract-test consumers. The shared fixed header,
read-view value and cap have one owner in Context's native-session contract and
are exposed through its in-process API; it does not export transport internals.
The 64 MiB + 64 KiB wire cap includes both subpaths and child
entries. Transcript byte accounting alone excludes path/envelope overhead and
cannot establish this bound. If the entire mapping does not fit, the host
omits the whole optional field and returns the ordinary subpath response.
It never truncates entries, children or paths to fit the cap.

The adapter installs a complete, shape-checked mapping atomically under its
existing append lock in its existing per-instance load cache. Cached child
reads consume this one authorized snapshot and yield to delivered task
cancellation. They do not perform additional remote authorization requests;
bytes already delivered cannot be recalled when host revocation precedes
delivery of the executor's cancel signal. Every actual callback and later
tool/input/model operation retains its current authority checks. The successful
terminal coverage transaction still requires the exact live writer, no Run
cancellation, a new current-turn main append and the accepted final sequence.
The narrowly scoped cancellation-tail append exception is unchanged.

Nonempty append invalidates the read cache when its pending batch is established
and after each acknowledged chunk. An uncertain ACK retains the original
pending batch and blocks all reads; a view cannot reset the write cursor or
manufacture an append receipt. Instances never share this cache across Runs,
Attempts or provider epochs. No server cache, persistent snapshot ledger,
SDK override or second prompt-history owner is introduced.

Compatibility disposition: old clients send no opt-in and receive exactly the
original response fields, including the original null/default values. Old hosts
ignore the new header and return ordinary subpaths. Absence of `child_entries`
therefore retains the original per-child load path, also needed for valid
histories whose aggregate wire envelope exceeds the cap. A present malformed
view or a changed sequence fails closed; HTTP denial or timeout is not retried
as version negotiation. These are two wire sizes for the same authorized native
store, not two continuity implementations. With a fitting view, ordinary SDK
resume does two whole-epoch verifications regardless of child count: main
preload and child enumeration. Oversized views retain the prior n+2 bound.

New appends store the canonical structured representation in `entry_canonical_json`
(JSON) as the only writer-owned original, in the same transaction as the existing
full-entry receipt. `entry_json` is nullable and remains only the read-compatible
mirror for historical JSONB-only rows; when both columns exist, canonical JSON is
authoritative and JSONB drift is ignored. Loading extracts the complete canonical
column as text and uses Python `json.loads`, then restores images and verifies the
original full representation. JSONB alone changes scientific notation and negative
zero, so it cannot authenticate the original append digest. The schema requires at
least one representation, without backfilling old rows or changing receipts.

Cancellation advances the Attempt owner generation and revokes ordinary runtime
callbacks. The original unexpired, unreleased active lease may drain provider
`append`, the final executor receipt and an already-persisted operation's
idempotent completion receipt while that exact queue-worker Attempt is
`cancel_requested` and its Run remains running with a cancellation request. The
generation must be exactly one ahead of that writer and lease. Completion is
reported evidence, not verified external effect; conflicting results fail. This
exception cannot claim a writer, load history, start a tool, accept input, survive
takeover or persist after terminalization.

A dispatch-response timeout does not prove that input was undispatched. The host
persists reconciliation identity before sending the request, cancels/observes the
same executor, and may consume an exact durable terminal handoff even after the
live endpoint disappears or reconciliation releases it. Missing, foreign or
unowned receipts retain observation responsibility. No observation reauthorizes
execution or resubmits input. Before actual SDK entry, preparation cancellation
carries explicit undispatched/no-ACK evidence; after entry the SDK owns those facts.

Artifact collection is checkpointed in the existing host-only lease payload before
stop. The private save receipt is checkpointed before terminal conversion. A
conversion or business-transaction retry validates the scope, original generation,
container and trusted snapshot and reuses that completed phase, rather than
connecting to a destroyed sandbox or changing a successful result to failure. An
unclaimed closing receipt advances only with the exact legal terminal Attempt CAS
on the same connection; release retains the exact terminal-generation check.
Normal receipt ingress and operator repair consume the same Sandbox-owned stopped
checkpoint finalizer in the Context/Run terminal transaction. It fences the full
scope, original container/generation, exact persisted stage and current claim;
claim loss rolls back Context, terminal events and lease changes together. A
completed checkpoint bypasses provider cleanup, including recovery of a historical
terminal Run whose checkpoint lease remains active. Invalid completed checkpoints
stay quarantined; conversion failure cannot fall back to a Run-only terminal write.
Receipts without a completed stop/save checkpoint retain ordinary deferred provider
cleanup. The former post-commit B01-only release helper is retired.

Run outcome and conversation continuation are separate. Cancelled or failed Runs
may publish resumable/candidate native state only after exact provider stop and a
private workspace save; a missing Result alone does not discard confirmed native
history. Unverified writers, unsaved workspaces, unavailable native boundaries and
capacity exhaustion have distinct fixed public guidance. Existing retry/resume
entry points retain their authorization and frozen snapshot contracts; a new chat
message is a new Run, never an automatic replay of the cancelled request.

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

The native epoch layout has written append receipts atomically with entries,
sequence/count and byte metadata since its first main implementation in
`3199a8fd0938444cfdbcc62c3bab34fdfaf93918` (#1397). No supported main writer
without those receipts was identified. Existing runtime results without a
`final_sequence` retain their terminal-commit compatibility path; their native
append receipts remain the verifier's authority. Schema version `2026.10.27.1`
makes the canonical representation the new writer authority, keeps `entry_json`
nullable for old JSONB-only rows, and adds the representation constraint without
backfilling old rows or receipts.
Legacy JSONB-only batches may resume when their original digest and byte count
can still be verified. If that digest does not match and numeric representation
is ambiguous (for example, original `1e21` or `-0.0`), the callback reports
`provider_session_integrity_unavailable`, not proof of corruption. It still
refuses to resume. Recovering such an old conversation requires a trusted
original append payload or backup in a separately authorized migration;
otherwise the user must start a new conversation. The verifier does not guess
numeric combinations or accept a newly computed legacy digest. Old binaries can still read legacy JSONB-only rows, but they cannot safely append
new canonical-only rows or restore canonical-only rows; the matching binary is the
compatibility owner for the new writer path.
The unshipped experimental binding
layout already requires explicit data disposition in `schema.sql`. A database
with imported or missing receipts has unverified continuity and is rejected;
this source inventory does not establish the contents of deployed databases.

## Evidence Ceiling

Local tests and static checks prove source behavior only. They do not prove a
packaged image, real PostgreSQL migration behavior, cross-sandbox provider
resume, deployed runtime state, or external acceptance. Those require the exact
candidate artifact and separate release evidence.

## Rollback

Rollback is source-level only. Once a lineage contains multiple epochs sharing a
provider UUID or canonical-only rows, old UUID-only or JSONB-only binaries cannot
safely resume or append that lineage. Recover with a compatible binary and retain
the data. The additive canonical column, representation constraint, and per-Attempt
coverage metadata remain installed; reverting the readiness version does not
authorize deleting them, rewriting rows, or overwriting migration ledger entries.
Rollback must not re-enable `platform_bootstrap`, checkpoint summarization, or
prompt reconstruction as a runtime fallback. Quiesce incompatible writers and
apply the owning schema migration before starting the matching binary.


### SDK mirror batch sizing and retry boundary

The Claude SDK eager mirror may coalesce many buffered frames into one
`SessionStore.append`. The adapter deterministically splits that one call into
host callbacks bounded by the existing 128-entry, 16-MiB raw-entry and 32-MiB
request-envelope limits. The image-free structured representation remains capped
at 256 KiB per entry; extracted image payloads have their separate 8 MiB limit.
It validates the complete input before sending any chunk. A single oversize JSON entry is rejected without splitting or truncating
its opaque SDK data; that is a platform resource limit, not an SDK guarantee.

Chunk sizing reuses the compact UTF-8 JSON lengths already checked on the
frozen entries. For each chunk it encodes the actual empty request envelope at
that chunk's expected sequence, then adds entry lengths and separating commas.
The envelope is recomputed after a split, including any sequence digit growth.
Each final chunk still passes the complete request-size validator, and every
chunk is planned before pending state or the first callback is created. A late
invalid entry or an oversized next singleton therefore cannot write a prefix.
The growing-prefix serialization loop is retired; only the owning differential
test retains that loop as a byte/partition oracle. Small singleton batches can
do more envelope encoding work; this removes repeated entry traversal without
promising a universal latency improvement.

The adapter freezes the original key/entries and chunk payloads, retains its
confirmed-chunk offset, and retries an uncertain ACK with exactly the same
`expected_sequence`. Already acknowledged chunks are not resent when the SDK
retries its whole append call. Another batch, load or subkey enumeration cannot
advance a pending failed append. Same-process concurrent append calls serialize
across subpaths, which share the host epoch sequence. The public accepted final
sequence advances only when the complete SDK append succeeds; the runner's
existing mirror-error/tracker fences still reject partial success.

Chunk progress is transient transport state, not another durable authority.
Host append receipts and existing scope/Attempt/generation/cancellation fences
remain authoritative. Restart does not adopt a disappeared writer's partial
batch: host dirty-epoch/recovery policy applies. Batch sizing itself does not alter
entry normalization, resource caps or retention; epoch schema and provider UUID
reuse follow the per-Attempt recovery contract above. It retires
the assumption that one SDK append must equal one host callback; old single-
chunk callbacks keep the same request/receipt shape.

### Private failure provenance

Native SessionStore preload exceptions and SDK `MirrorErrorMessage.error` are
captured in the existing bounded `runtime_diagnostics` carrier. Preload preserves
the observed exception and standard cause/context chain; mirror capture reads
only the SDK error string, never its SessionKey or transcript entries. Missing
session identity, invalid frozen resume choice, and resume-state mismatch use
fixed, value-free diagnostic sources. Public provider-session failure codes,
new-conversation guidance, and the final mirror/receipt/sequence barriers do not
change. Local callback rejection records the transport's observed fixed reason;
it does not imply knowledge of an uncollected HTTP response body.

The Sandbox diagnostic normalizer owns the existing size budgets; Runs owns
private persistence and its credential-only redaction (policy v2). Arbitrary
SDK exception strings remain administrator-private evidence under that contract:
pattern redaction cannot guarantee removal of arbitrary business text or unknown
secrets embedded by a provider. No prompt, callback configuration, SessionKey,
transcript, or request payload is deliberately added to these observations.
Oversized SDK error values retain the existing truncation summary rather than an
unbounded copy. Required response attachment tool registration failures likewise
retain the constructor exception through this same diagnostic path; optional
registration failure still leaves attachments unavailable without failing a turn.

### Context prompt evidence compatibility

The context-pack evidence generator, verifier, and office readiness consumer use
probe/runtime-acceptance v3. They require the common redaction checks plus the
selected rendering contract: manifest refs and retrieval tools, a bounded legacy
summary, or no material section. Unknown legacy counts are not proof of absence;
only three explicit integer-zero material counts suppress the legacy summary.
`memory_read_flag_absent` checks prompt rendering only, not whether memory was
read; worker-dispatch, scoped material evidence, and non-expansion invariants
remain separately required. Source probes still cannot close runtime acceptance.

Archived v2 reports remain immutable historical evidence for their original
source. They do not satisfy the current v3 readiness contract; a current-runtime
claim requires fresh v3 evidence rather than relabeling an old artifact. The
retired summary/version/timestamp-in-prompt assertions have no current consumers.
