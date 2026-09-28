# Agent Conversation Context

Status: active source contract
Owner: Context; native engine adaptation is owned by Execution
Last updated: 2026-09-28

## Authorities

The production executor uses Claude's native `SessionStore` for model-facing
history. Claude owns transcript ordering, summaries, token accounting and
automatic compaction. Platform Messages are display and audit records.

Context owns the scoped provider epoch, opaque transcript entries, append and
turn receipts, and lineage locking. A Run snapshot freezes a coverage receipt:
`scope`, `through_session_generation`, `message_count`, `source_sha256`, and
`current_message_id`, under `ai-platform.conversation-authority.v2`.
The digest represents the committed provider coverage, not a fresh audit of
all display-history bodies.

## Admission And Resume

1. Admission claims the Session's provider lineage, serializing with the previous
   Run's terminal commit, and reads committed coverage from the current epoch.
2. A new head without an epoch uses a scoped metadata-only existence check to
   reject prior messages. An empty conversation receives the initial scope digest.
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
   assistant messages, then releases the lineage. Runs API tasks may have no
   business user-message row; their input still reaches the SDK user channel.

The SDK may read its own persisted transcript during resume. The removed work
is the platform's duplicate full scan of business-message bodies.

## Current Capability Boundary

Each Run reauthorizes the current Agent Profile, Skills, MCP/native tools,
model, credentials, files, artifacts, memory references and Run/Attempt/lease.
Historical transcript content does not restore those capabilities. The SDK user
turn contains the current input; current policy and material references use the
controlled system channel. Claude does not receive `read_session_messages`.

Provider IDs, transcript entries, callback credentials and storage/runtime details
remain private. Public Context summaries expose counts and safe provenance.

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
