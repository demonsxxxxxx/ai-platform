# Issue #511 Generation 1: Session Context Authority

## Scope

Generation 1 makes the database-owned run generation and executor context
snapshot binding authoritative for newly created session runs. It is a local
implementation slice, not #511 closure: fresh 211 sandbox/worker and browser
acceptance remains required.

The platform database and object storage remain the only continuity authority.
Claude/SDK resume IDs, worker memory, sandbox files, and queue copies are not
context authority.

## Invariants

1. Every normal persisted session run receives its `session_generation` through
   `repositories.allocate_session_run_generation`. The allocator atomically
   increments `sessions.next_run_generation` under the owning session row;
   routes never supply a generation.
2. `runs.context_snapshot_id` is the physical authority. A new run must bind
   its executor snapshot in the creation transaction before enqueue. The JSON
   field is a compatibility mirror and must exactly match the physical value.
3. The same-ID bind is idempotent. Binding a different ID, clearing a bound ID,
   binding a wrong-scope/non-executor/missing snapshot, or a JSON/physical
   mismatch fails as `context_snapshot_binding_invalid`.
4. Retrieval code loads the physical binding only. It cannot select the latest
   snapshot. A missing/mismatched callback binding returns the non-oracular
   `context_snapshot_unavailable` before storage access.
5. History candidates must have a non-null generation lower than the current
   run. The planner evaluates newest-first with the existing UTF-8 byte/token
   estimator, then renders retained rows chronologically. The current input is
   not part of history and appears exactly once.
6. Legacy runs are not backfilled from timestamps or UUIDs. Their unordered
   material is excluded from new-context eligibility and the public context
   window says `degraded` with `legacy_history_excluded=true`.

## Transaction model

`chat_stream` already keeps run creation, user-message persistence, file
binding, `record_initial_context_snapshot`, and its final queue payload inside
one database transaction. This generation strengthens that seam: snapshot
binding is a compare-and-set repository update, and queue enqueue occurs only
after the transaction returns. A planning or binding exception therefore has
no queue side effect or accepted response.

The physical binding is also joined by worker retrieval repositories. For a
newly bound run, a queue copy that omits or changes the ID cannot resolve a
different snapshot; a subsequent worker refresh cannot rebind a different
snapshot through the compare-and-set seam.

## Additive migration and rollback

`app/schema.sql` adds `sessions.next_run_generation`, nullable
`runs.session_generation`, and nullable `runs.context_snapshot_id`; the latter
uses a deferred composite FK to the exact scoped `run_context_snapshots` row.
A partial unique generation index prevents duplicate non-null generations. An
update trigger permits null-to-exact-ID and exact-ID repeat writes, but rejects
rebinding/clearing and JSON/physical mismatch.

Existing rows remain nullable and readable. No timestamp/UUID backfill is run.
Rollback is application-only after quiescing run creation: all schema changes
are additive and are retained; no down migration or history rewrite is needed.

## Explicit non-goals and later gates

- #508: no parser-evidence schema, typed parser path, file-parser contract, or
  historical XLSX evidence. A later slice can add versioned parser evidence to
  the fixed snapshot seam.
- #512: no run-control route or UI changes.
- #513: no preview route, browser parser, file DTO, or frontend changes.
- #509: no intent, capability, tool-policy, sandbox, or terminal-output change.
- No session deletion behavior, cross-session Memory, provider resume, or
  multi-agent redesign. The old child-dispatch creation route has no safe
  pre-enqueue ContextBuilder seam, so it fails closed instead of minting an
  unbound new-authority run.

## Verification boundary

Focused local tests cover newest-first CJK/emoji budgeting, chronological
rendering, legacy degradation, allocator serialization contract, binding happy
and error paths, fixed callback denial, schema declarations, and existing
chat/context/repository regressions. This evidence is `local partial` only.
Independent review and exact-main 211/browser proof remain required for #511.
