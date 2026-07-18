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

## Generation 2 review repair

Generation 2 closes the local review findings without broadening #511 into
parser, provider-resume, UI, or tool-policy work.

- The worker resolves only the run's scoped physical binding and terminalizes a
  missing or invalid one as `context_snapshot_unavailable`; it never creates a
  worker-refresh snapshot.
- Manual context requests cannot create an `executor` row. Share/fork reads the
  source run's exact binding, so an unbound source returns
  `context_snapshot_unavailable` rather than choosing a latest row.
- Schema application performs an idempotent populated-database backfill only
  when both legacy JSON mirrors agree and point at the exact scoped executor
  snapshot. Ambiguous or absent rows stay null and are displayed as degraded.
  The nullable columns keep old-application rollback compatibility.
- Queue outcomes after commit use the existing transaction model for chat,
  direct run creation, copy, retry, and resume. A definitive pre-admission
  rejection can transition the run with the public-safe
  `queue_enqueue_failed` code and an event/audit record; outcome-unknown
  keyed chat attempts remain recoverable rather than fabricating either
  `enqueue_failed` or a second enqueue. No outbox or background service is
  added.
- The context planner reserves the current user input exactly once before
  newest-first history eligibility. Count-cap and budget omissions increment
  the public trimmed count, while retained history remains chronological. The
  public context window is limited to status, counts, legacy degradation, and
  authorized safe basenames.
- Ordinary run/playback projections use that allowlisted `context_window`
  only—never snapshot IDs, storage locators, provider state, raw prompts, or
  private payloads. Legacy rows still display in history but cannot produce an
  implicit `current_run_id` or current status.
- Multi-agent child snapshot design remains deferred. Its three public
  admission endpoints reject with `multi_agent_dispatch_not_available` before
  transaction, candidate claim, child creation, or queue effects; the
  obsolete deep repository stop is removed.

The opt-in PostgreSQL test uses the repository's
`AI_PLATFORM_S0A_SCHEMA_TEST_DSN` contract. It applies the schema twice to
populated rows and covers allocator races, same-ID repeat binding, rebind
rejection, deferred FK insertion, and the immutable-binding trigger. Local
absence of that DSN is an explicit skip, not database evidence.

## Generation 4 final architecture repair

Generation 4 refines the local seams without changing the Context v1 scope.

- A keyed chat retry first reads the deterministic immutable Redis message ID.
  If an enqueue call then raises, the same bounded readback distinguishes an
  observed queued/leased/retry admission from an unknown outcome. Unknown
  outcomes remain `accepted_pending_enqueue` and are recoverable through the
  existing retry-admission path; no terminal failure is fabricated and no
  second enqueue command is sent for an observed identity. Only the queue
  module's typed local, pre-admission rejection can transition a queued run
  and submission to `queue_enqueue_failed` in a separate committed
  compensation transaction.
- The deferred multi-agent guard is also invoked by worker maintenance before
  settings, candidate listing, claims, writes, or enqueue. Configuration does
  not override the public `multi_agent_dispatch_not_available` decision.
- Context assembly counts scoped, ordered historical candidates before it
  reads the bounded newest-eight tail. The count query contains no message
  content, so public trimmed/degraded status remains truthful for long
  histories without unbounded material loading.
- Retrieval-only file entries retain an authorized, sanitized basename only.
  They carry no content, storage key, or source path; the allowlisted public
  `context_window.selected_file_names` is derived from that basename metadata.

This repair adds no outbox, schema change, parser evidence, long-term Memory,
provider resume, preview/UI work, or multi-agent child-snapshot design. The
opt-in PostgreSQL gate remains a controller-owned integration requirement.
