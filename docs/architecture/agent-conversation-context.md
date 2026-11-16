# Agent Conversation Context

Status: active source contract
Owner: Context; native engine adaptation is owned by Execution
Last updated: 2026-10-23 (Beijing time)

## Authorities

The production executor uses Claude's native `SessionStore` for model-facing
history. Claude owns transcript ordering, summaries, token accounting and
automatic compaction. Platform Messages are display and audit records.

Context owns the scoped provider epoch, opaque transcript entries, append and
turn receipts, and lineage locking. A Run snapshot freezes a coverage receipt:
`scope`, `through_session_generation`, `message_count`, `source_sha256`, and
`current_message_id`, under `ai-platform.conversation-authority.v2`.
The digest represents committed provider coverage, not a fresh audit of display
history. Successful coverage retains v2 user/assistant meaning. A stopped accepted
partial turn uses explicit v3 `native_accepted_messages` coverage and a verified
private workspace reference; it does not fabricate an assistant message.

## Admission And Resume

1. Admission claims the Session's provider lineage, serializing with the previous
   Run's terminal commit, and reads committed coverage from the current epoch.
2. A new head without an epoch uses a scoped metadata-only existence check to
   reject uncovered prior history. Failed/cancelled Runs with no execution start
   and no started or open Attempt can leave user messages without provider
   history; these do not prevent the initial empty start. Missing or uncertain
   execution facts still reject. An empty source receives the initial scope digest.
3. Worker loads the exact Run-bound snapshot, validates scope and current-message
   membership, and sends an empty historical `messages` array to the adapter.
   Neither admission nor Worker reads prior message content or rebuilds history.
4. The scoped epoch must be ready, writer-free, within capacity, and match the
   frozen digest/count for `native_resume`. An existing running Attempt restores
   its frozen execution context before a new readiness decision.
5. `empty_start` requires no current epoch and the scope's zero-message receipt.
   Missing coverage, dirty/closed epochs, capacity exhaustion or a mismatch
   requires a new conversation; it cannot silently clear model context.
6. The success transaction extends coverage using only the current user and
   assistant messages. Cancelled/failed Attempts can instead settle confirmed
   native state after exact provider stop and private workspace save. A target
   proven undispatched may close without a writer/turn; its closing receipt must
   follow the exact terminal Attempt generation before lineage release. Runs API
   tasks may have no business user-message row; their input still reaches the SDK
   user channel.
7. Each new Attempt copies the selected source's complete bounded native prefix
   and original receipt provenance into a new write epoch, with the same provider
   UUID. A failed child cannot contaminate another continuation from its immutable
   source. Run outcome and safe continuation are independent. The detailed
   [native continuity contract](claude-provider-session-continuity.md) owns
   recovery states, file barriers, capacity and multi-epoch migration limits.

The SDK may read its own persisted transcript during resume. The removed work
is the platform's duplicate full scan of business-message bodies.

## Current Capability Boundary

Each new Run resolves the current published Agent Profile and Skill dependency
closure, independently of the Session creation revision. Each execution reauthorizes
the accepted Agent Profile, Skills, MCP/native tools,
model, credentials, files, artifacts, memory references and Run/Attempt/lease.
Skill dispatch, retry and resume preserve the versions admitted to the Run.
Current aggregate access and distributions are checked independently of current
release selection; exact pinned package integrity and revocation remain checked.
Fixed Skill sets query only their admitted roots and dependency closure. Open
discovery still queries the available catalog.
Historical transcript content does not restore those capabilities. The SDK user
turn contains the current input; current policy and material references use the
controlled system channel. Claude does not receive `read_session_messages`.

Provider IDs, transcript entries, callback credentials and storage/runtime details
remain private. Public Context summaries expose counts and safe provenance.

## Follow-up Question Completion Boundary

Model-generated follow-up questions are a read-only projection over a settled
answer, not Context coverage. `conversations` owns the authorized request: it
takes only `session_id` and `run_id`, rechecks tenant/user/Session scope, and
requires the target Run to be the Session's current generation, `succeeded`, and
without a later user message. The turn input is the bounded recent public
`user`/`assistant` message text (redacted in Conversations infrastructure); drafts,
attachments, tool raw data and Profile instructions are excluded.

The completion uses that Run's own frozen `model_id`, `model_value` and
`model_gateway_revision` through the Execution-owned model adapter, once,
non-streaming, with tools disabled and a bounded output size. Execution resolves
the pinned connection read-only and returns none when that model is disabled or
delisted, rather than selecting another model. This capability creates no Run,
Attempt, lease, message, Session or provider transcript entry, and does not
extend provider coverage: the native conversation lineage stays owned by
`Admission And Resume`.

Concurrent and repeated requests for one owner and target Run are collapsed by a
Redis claim plus bounded cache in Conversations infrastructure; a failed or empty
generation is not cached, and provider failure never becomes a Run failure.

## Bounded Attachment Selection

Current attachments retain priority and are all preserved within the existing
request limits. Historical attachments fill only the remaining slots in the
eight-file continuity selection. Runs API snapshots take the newest eligible
generation/time/ID tail after excluding current IDs; an ascending repository
result is reversed before applying the newest-first selection contract.

Pre-admission Session file selection uses 128-row keyset pages ordered by
`created_at DESC, id DESC` on the caller's existing connection and transaction.
It stops when enough eligible files are selected. Skill input matching and
Unicode casefold basename deduplication retain their existing Python owner, so
an incompatible file or a repeated name cannot consume a final selection slot.
The existing owner/session/time index supports this access path. Exhausting
large incompatible or duplicate histories can still require multiple pages;
the change bounds materialized pages, not total worst-case scanning.

When current attachments fill all slots, no historical page is requested.
Explicit current IDs still receive a bounded membership query when needed to
preserve their existing reusable-file classification. Every query retains the
exact source Run snapshot binding and scope predicates; final locked file
authorization remains unchanged. Keyset paging does not open a second
transaction or claim stronger isolation than the caller supplies.

## Run Playback Context Window

Run Playback reads the exact scoped Run-bound executor snapshot. An unavailable
binding produces a `degraded` window; embedded Run input and the latest unbound
snapshot are not fallback authorities. The frontend consumes only
`context_ref.context_window`; the backend's top-level `context_window` mirror
is not a second frontend source.

The existing playback panel displays `complete`, `trimmed`, or `degraded`,
nonnegative history candidate/authorized/omitted counts, the legacy-history
exclusion flag, and up to eight safe selected-file basenames. Filename checks
retain ordinary names such as `manifest.pdf` and `100%预算.xlsx`; lineage-ID
keyword restrictions do not apply to authorized display basenames. Path and
control-character boundaries and explicit credential expressions remain excluded. Zero counts keep
the window visible, including degraded fallback. These are snapshot metadata,
not reconstructed model history or evidence that native transcript was lost.
Missing windows, unknown status, or invalid counts produce a degraded display;
invalid counts become zero placeholders and unsafe names are discarded. No raw snapshot, material IDs,
Skill identifiers, runtime paths, tier/source/version provenance, or historical
message bodies enter this projection. The superseded frontend provenance DTO,
renderer, labels and fixtures are retired without rewriting stored snapshots.
Run-control request ownership and supersession remain lifecycle-owned.

## Bounded Artifact Text Retrieval

`read_run_artifact` authorizes the exact scoped artifact before storage access.
Its text preview reads at most the requested byte budget plus one sentinel byte,
then closes the object body, including on read errors. The sentinel establishes
`truncated` without trusting declared object length. Only budgeted bytes are
decoded; incomplete UTF-8 suffixes and invalid bytes are ignored as before, then
existing content redaction applies. Large objects still return a truncated
preview rather than a whole-object size-limit error.

The Context repository prefix operation replaces the preview's unbounded object
read in both connection and transaction adapters. No unbounded compatibility
fallback remains in this preview path. Preview and uploaded-file JSON staging remain separate from full artifact
transfer. Storage worker timeout and cancellation retain capacity until blocking
I/O ends; the prefix operation closes its body when that operation exits.

## Historical Artifact Workspace Transfer

`stage_run_artifact_to_workspace` keeps its existing tool identity, `artifact_id`
and optional `max_bytes` arguments. A new Run may restore only artifacts in its
exact frozen executor snapshot. The existing scoped repository checks tenant,
workspace, user, Session, source Run, active lifecycle and expiry; knowing an
artifact ID does not grant access. Selection/admission owns which IDs enter that
snapshot. Storage locators and credentials never reach the model.

The canonical per-artifact limit is `artifacts.api.ARTIFACT_MAX_FILE_BYTES`
(64 MiB), shared with sandbox collection. Neither upload limits, uploaded context
file staging (1 MiB), `read_run_artifact` preview nor ProfileDrive changes.
The executor requests the existing private
`POST /runtime/callbacks/context-retrieval` with
`Accept: application/octet-stream` and `Accept-Encoding: identity`. There is no
JSON/base64 fallback in the new executor artifact path. Deployed pre-stream
sandbox executors are the named compatibility consumer of the old JSON staging
response, which remains capped at 16 MiB. Context owns that compatibility;
remove it only after the deployed executor inventory and rollout/rollback window
prove no remaining JSON artifact-stage consumers. The independent uploaded-file
JSON response and artifact text preview remain supported.

The broker uses shared `SandboxCallbackAuthority` to lock Run then exact Attempt
and active lease, loads the bound snapshot/tool membership, and resolves scoped
object identity in a short transaction. It downloads to a private temporary file
outside those locks using bounded storage admission and 64 KiB reads. Declared
size must match exactly; a historical row/manifest SHA-256, when present, must
match. Historical artifacts without a recorded digest are not claimed to have
one: the transfer header contains the digest computed during this download.
A second short transaction repeats authority, snapshot, source identity and
immutable lease checks before recording the private retrieval event and handing
off the response. Cancellation, changed generation/lease/snapshot, expiry or
source disappearance cannot publish a successful transfer. This read-only
operation creates no remote effect or new lifecycle claim.

The private response has exact `Content-Length`, identity encoding, `no-store`,
and an ASCII JSON `X-Context-Artifact` header containing `artifact_id`, safe
`name`, `source_run_id`, `size_bytes`, actual `sha256` and `max_bytes`. Its body
contains only bytes. The response owns the server tempfile through ASGI send,
including send failure/disconnect. Abandoned storage I/O joins physical completion
before cleaning a late returned path or closing its client; the shared storage
slot remains occupied until the physical operation ends.

Executor and inline staging publish a complete mutable copy at
`context/<artifact_id>/<safe-basename>`. The tool returns that exact
workspace-relative `workspace_path`, `artifact_id`, `name`, `source_run_id`,
`bytes_staged`, `max_bytes`, `reused`, audit and redaction facts. The model uses
the returned path, not the original name. Same-name artifacts have separate ID
directories. A bounded streamed temporary file is checked for exact length and
SHA-256 and flushed/fsynced while its descriptor stays open. Atomic no-clobber
publication links that verified descriptor through `/proc/self/fd`, never the
replaceable temporary pathname. Publication does not roll back by deleting a
mutable final path; edits or replacement after that atomic link remain intact.
Directory traversal uses no-follow descriptors; symlinks, hardlinks, foreign
preexisting targets and filesystems without the required descriptor-link support
fail closed. No permissive Windows fallback is provided.

Sandbox owns the executor's streaming orchestration, with the existing caller
supplying its HTTP client factory. The worker's inline Context factory binds the
bounded physical-download port in `bootstrap.context`; direct inline consumers
supply that port explicitly. File staging, preview and existing JSON broker
callers retain their entry points without this new inline-download prerequisite.

Within the current Attempt's retrieval owner, a successful restore records only
its path and immutable source identity. Repeated/concurrent calls reuse a
controlled regular target and preserve local edits (including editor replacement),
without comparing edited bytes to the source digest. This is not a durable
workspace manifest or cross-Attempt cache: a fresh owner does not adopt an
unproven existing target. The source S3 object remains immutable; later output
publication creates a new artifact through the existing collection authority.
Failed, partial, cancelled, over-budget or hash-mismatched downloads cannot replace
an existing target. Descriptor and temporary-file ownership is local to each
transfer, not a workspace scan or a retention policy.

Focused tests in `test_context_artifact_transfer.py` cover synthetic 20/64 MiB
bounded downloads, transfer integrity, callback fencing, concurrent publication,
edit-preserving reuse and resource cleanup. Its optional real MinIO test uses
`AI_PLATFORM_ARTIFACT_MINIO_URL` with synthetic-only credentials and a unique
bucket, restores after deleting the first Attempt workspace and checks the
original object after publishing a new version. Real PostgreSQL concurrency,
real MinIO and POSIX filesystem acceptance require their actual facilities;
Windows skips do not establish those results.

## Retirement And Acceptance

The generic v1 history renderer, explicit historical-ID fallback, checkpoint
loader, range/tail verifier and paginated full-history source queries are removed.
No production non-native adapter consumes those paths. A future adapter must
provide its own continuation contract. The independent scoped retrieval API
remains for authorized retrieval; database checkpoint rows are left intact.

Tests must cover initial empty start, covered native resume, unavailable epochs,
wrong scope/current-message binding, frozen Attempt recovery, and metadata-only
coverage admission. Display-history body tampering is no longer detected by a
per-turn rescan; it does not alter the native transcript used by the SDK.

See [Claude continuity](claude-provider-session-continuity.md) for the full
contract, rollback and capacity boundaries. Source tests do not establish real
provider resume, deployed behavior or browser acceptance.
