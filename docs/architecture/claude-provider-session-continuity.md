# Claude Native Conversation Continuity

Status: source candidate; deployment and external acceptance are separate
Owner: Context + Execution + Runs
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

No frontend, SSE wire, public message schema, or deployment change is in scope.
The private provider callback has the opt-in child read extension described
below; its request body, action names and append ACK shape are unchanged. The
cancellation drain qualification remains limited to native append persistence.

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
claiming them. No public File/Artifact row or new cleanup worker is introduced.

Compatibility disposition: the previous smaller all-inline private limits and
writer are superseded. The per-entry structured 256 KiB, raw entry 16 MiB, raw
transcript 64 MiB, batch 32 MiB and count limits remain. Inline history remains a
read format, not a second writer. Schema/readiness authority advances to
`2026.10.26.1`; concurrent-index authority is unchanged. Old binaries cannot restore externalized entries or
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

Cancellation advances the Attempt owner generation and revokes ordinary
runtime callbacks. Only provider `append` may drain the already-claimed writer
through its original unexpired, unreleased active lease, while that exact
queue-worker Attempt is `cancel_requested` and its Run remains running with a
cancellation request. The generation must be exactly one ahead of that writer
and lease. This exception cannot claim a writer, load history, invoke a tool,
accept input, survive takeover, or persist after terminalization.

Turn-limit, timeout, missing authoritative terminal and provider-persistence
failures direct users to a new conversation and are not advertised as same-Run
retries. Failed started turns may leave native state dirty or coverage incomplete;
the public advice does not promise that the same Session can resume.

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
append receipts remain the verifier's authority. Schema version `2026.10.26.1`
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

Rollback is source-level only and cannot restore a new canonical-only row with an
old binary. It must not re-enable `platform_bootstrap`, checkpoint summarization,
or prompt reconstruction as a runtime fallback, and it must not delete provider or
checkpoint rows ad hoc. The additive canonical column and representation constraint
remain installed during a binary rollback; reverting the readiness version does not
authorize deleting either column, rewriting old rows, or overwriting migration
ledger entries. Old binaries can read legacy JSONB-only rows, but the migrated
canonical-only writer path must remain disabled until matching binaries are active.
Apply the schema migration before starting the new binary.

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
batch: host dirty-epoch/recovery policy applies. This change does not alter UUID
uniqueness, entry normalization, schema, resource caps or retention. It retires
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
