# Issue #454: fail-closed tool-permission lifecycle

## Scope

Repair the authoritative SDK permission callback, worker terminalization path, and
the currently routed settings projection.  A required tool permission remains
pending until an administrator makes a tenant/run/request-scoped decision; it
must not permit the tool call or a successful run.  Every pending request is
terminalized when its run reaches succeeded, failed, cancelled, or expiry.

## Failure modes and decisions

| Failure mode | Required behavior |
| --- | --- |
| SDK asks for a risky tool and no exact decision exists | Create one audited pending request, return a fail-closed denial that makes the SDK result fail, and do not emit a completed tool call. |
| Executor claims success with pending requests | The worker rejects success, terminalizes the requests as no-longer-actionable, and records a truthful failure rather than `run_succeeded`. |
| Run fails, is cancelled, or expires while requests are pending | A tenant/run-scoped repository transition terminalizes each pending request with an auditable reason before the run's terminal event is exposed. |
| Admin opens the accepted `/settings` route | The routed workbench settings projection renders the existing tenant-admin inbox component. |
| Ordinary user opens `/settings` | The component retains its strict `is_admin && settings:manage` gate, makes no inbox request, and renders no controls. |
| File-required Skill returns no user-visible output/artifact | The SDK worker returns a failure instead of a successful run, preserving the existing artifact ACL and public projection contracts. |

## Compatibility and ordering

Existing decided/consumed permission rows remain valid only through the exact
decision lookup.  New terminal request statuses are terminal lifecycle facts,
not grants, so decision endpoints continue to accept only `pending` rows.
Run terminalization transitions pending requests in the same database transaction
as the run state where possible; an already-terminal run never gets rewritten.
The frontend uses the existing inbox API and authorization component rather than
creating a parallel route or raw approval surface.

## Verification

Start with focused RED tests for pending SDK callbacks, successful worker output
with pending requests, terminal request cleanup, artifact-required failure, and
admin/ordinary settings routing.  Then run affected Python and frontend tests,
`python -m compileall -q app tools scripts`, the relevant projection/contract
check, and `git diff --check`.  No live 211 request will be read or mutated.

## Generation 2 repair boundary

The first implementation established the basic fail-closed path.  This repair
keeps that authority chain and closes six race/projection gaps without adding a
second permission system:

1. The sandbox callback transport, outer worker-to-sandbox execution, and
   sandbox-brokered SDK run deadlines share one authoritative permission wait
   plus a fixed cancellation margin, so a valid delayed decision is not
   rewritten as a broker failure or truncated by a shorter enclosing timeout.
2. `tool_permission_terminalized` is a public lifecycle event.  Its payload
   must carry only the safe exact request/run/tool identifiers and terminal
   status needed to close an ordinary-user card; it never grants controls.
3. Request creation and decision SQL must lock and test the owning run, reject
   cancellation/terminal states, and reject an elapsed expiry in the same
   update.  A failed race is a denial, never a decision reset.
4. Both queued and running cancellation transitions must terminalize pending
   requests in their existing transaction and record the system action.
5. PreToolUse only records authorization state.  A completed tool event needs
   an actual post-tool source, so there is no synthetic completion fallback.
6. Required artifact types come from the authoritative selected capability
   definition and travel with the executor result; the worker compares declared
   types rather than treating any workspace file as delivery.

Compatibility: `pending`, `decided`, `consumed`, and existing terminal request
rows remain readable.  New terminal cards are display-only and preserve the
existing administrator-only inbox authorization.  Tests use controllable
deadlines/barriers rather than real waits for timing or cancellation races.

## Generation 3 repair boundary

The permission lifecycle keeps one deep authority seam: a structured budget
defines the request TTL, one aggregate wait allowance for a sandbox-brokered
SDK execution, and the nested transports which carry that wait.  The callback
transport exceeds the wait; the sandbox SDK total exceeds its ordinary
execution budget plus the aggregate wait and an inner margin; and the outer
executor transport exceeds that SDK total plus an outer margin.  Non-permission
callbacks retain their short transport timeout.  Repeated permission waits
share the single aggregate allowance: once the SDK's total budget is exhausted,
the active callback is cancelled and fails closed rather than gaining another
full wait.

The repository locks the run before looking up, consuming, or reusing a grant.
Only a `running` run without `cancel_requested_at` may use a decision; a
cancelled/terminal run terminalizes both pending and still-usable decided
requests while retaining their audit history.  The worker writes success-only
artifacts, assistant messages, snapshots, and completion in one transaction.
If the final pending guard loses to a newly-created request, that transaction
rolls back and a separate failure transition terminalizes the gate.  Sandbox
PreToolUse never speculates a request event: request creation alone emits the
identifier-bearing public fact.

Inbox expiry is bounded to a deterministic, tenant-scoped locked batch before
listing, so a large expired population cannot turn one administrator GET into
an unbounded write transaction.  Subsequent inbox calls and normal lifecycle
maintenance converge the remaining rows.  `tool_permission_terminalized` is
added to the standard public event taxonomy.  Compatibility is preserved for
ordinary callback/run timeouts and read-only request history; no ordinary-user
decision controls or endpoint reachability changes.

Focused RED/GREEN coverage uses injected clocks and barriers for nested timing,
cancel-versus-consume, late-request rollback, event provenance, and expiry
batch progress.  It is followed by the affected repository/worker/sandbox/SDK
and event-contract tests, compileall, projection audit, and diff/scope/secret
checks.  No live request or deployment action is in scope.

### Generation 3 local evidence

The focused RED import failure established the missing structured budget before
implementation.  The repaired backend scope passed the repository, worker,
SDK adapter, sandbox app/client, and control-plane contract set with
`610 passed, 3 skipped`; the projection, route, callback, and sandbox
integration set passed with `66 passed`.  `python -m compileall -q app tools
scripts`, `tools/frontend_projection_audit.py --format json` (status
`pass_with_policy_gaps`), and `git diff --check` also passed.  The independent
frontend projection-audit unit currently has an unrelated expectation drift for
`/api/env-vars`; it is outside this lifecycle change.  This worktree has no
`frontend/web/node_modules`, so no TypeScript/lint/build command was run and no
dependency or lockfile mutation was made.

## Generation 4 repair boundary

Generation 4 preserves the accepted fail-closed path and deepens its two
authoritative seams.  `tool_permission_lifecycle` owns a measured aggregate
permission-wait allowance for one sandbox-brokered SDK execution.  Each
permission callback consumes the same monotonic allowance; callback transport,
SDK total, and outer executor total are strictly nested with enough post-decision
execution and terminal-callback margin.  Ordinary callbacks and executions that
cannot request governed permissions keep their existing short/normal deadlines.

The repository takes the owning `runs` row lock before permission rows for
expiry, decision, cancellation, lookup, consumption, and terminalization.
Terminalization is durable and progressive: a short run-first transition blocks
new requests, bounded batches terminalize and audit individual rows, and only a
verified empty gate permits the final run state.  Retry after a crash resumes
the same marker without duplicate events or a terminal run coexisting with a
pending request.  Multi-agent parent cancellation uses that same seam for
queued children rather than directly writing `cancelled`; a post-commit drain
continues bounded child progress after queue removal.  The tenant administrator
inbox continues to expose history,
but publishes decision options only for a pending, unexpired request whose run
is still executable; all other records are display-only.  These additions are
additive for stored rows and preserve ordinary-user RBAC and public card data.

The Generation 4 RED set uses injected monotonic clocks and transaction/barrier
fakes for aggregate waits, deadline nesting, expiry/decision/cancel ordering,
batch retry/progress, and inbox truth.  It also keeps the Generation 4a worker
transactional completion and usable-DOCX artifact checks: failed or late-blocked
results leave no completion side effect, and reviewed/translated DOCX artifacts
must contain valid non-empty OpenXML document parts.

### Generation 4 latest-main integration

The candidate is rebased onto `06296b18963e40aa6f9df929103e43370befc14f`, the
#455 chat-submission integration base.  Its additive `chat_submissions` schema
and principal-scoped repository operations remain separate from the `runs`
permission-terminalization marker.  Direct chat-route coverage runs alongside
the permission lifecycle suites after the rebase.  A missing or malformed
permission expiry is now fail-closed for both decision acceptance and published
`allowed_decisions`; historic request cards remain readable but cannot expose a
new approval action.

## Generation 4d repair boundary

The terminal authority seam is the worker repository transaction.  Sandbox and
executor callbacks may report running progress and private execution
observations, but they never emit `run_completed`, `run_failed`, or
`run_cancelled`; those public facts follow the worker's final artifact,
permission, and cancellation guards only.

One callback's monotonic remaining permission allowance is passed through the
resolver into the newly-created request expiry.  Local exhaustion explicitly
terminalizes that exact request, including an unconsumed decision, so a later
database decision cannot authorize an already-failed SDK callback.  Normal
sandbox transport retains a small outer callback margin while governed nesting
continues to use the structured budget.

The additive schema upgrade and lifecycle treat old NULL-expiry pending
permission rows as immediately expired without changing the independent
NULL-expiry memory-record contract. Existing worker maintenance discovers
bounded staged, terminal, and expired-permission work, then invokes the same
durable run-first drain; it is the retry owner after worker crash or
high-cardinality batches, not a new scheduler. Multi-agent child enqueue
failure and parent rollup use the same terminalization seam rather than direct
terminal SQL. A blocked success is classified under the run lock as
cancellation, pending permission, or stale state before a truthful terminal
event is published.

Required reviewed/translated DOCX artifacts are accepted only after bounded
OPC validation: safe entry cardinality and sizes, package and office-document
relationships, content type, and a non-empty main document.  Ordinary owner
permission projections never include decision controls; the existing
administrator inbox remains the only decision surface.

RED coverage uses callback fakes, injected clocks, barrier races, schema SQL
assertions, bounded maintenance fakes, and synthetic OPC archives.  GREEN
verification includes affected runtime, lifecycle, repository, worker,
multi-agent, RBAC, artifact, schema, coexistence, and exact backend-CI suites.

## Generation 4e repair boundary

The permission deadline remains one deep lifecycle seam: a callback owns one
absolute monotonic deadline, every awaited broker boundary rechecks it, and the
database compares permission expiry with `clock_timestamp()` after acquiring
the run-first lock. A completed deadline can create no decision, consumption,
or allowed audit. The ordinary outer executor budget explicitly includes its
initial and final callbacks plus the response margin; governed nesting remains
strictly larger.

Terminalization stays a durable run work item. Its final owner must close
steps, emit the terminal run fact and audit once, reconcile a child, and allow
the existing parent rollup rather than merely writing a run status. A valid
run-wide grant is invalidated as consumed at successful completion while all
other pending or decided authority continues to block success. The worker
unions capability-derived required artifact types with any executor assertion,
never allowing executor output to weaken the selected Skill contract.

DOCX validation is an OPC seam: bounded archive metadata is accepted only for
safe, unique package part names, the canonical OpenXML namespaces and exact
office-document relationship/content type, and a real WordprocessingML body.
The frontend part reducer is monotonic: a terminal card cannot be overwritten
by a stale/replayed decision, including when the decision reducer rather than
the upsert reducer receives the event.

## Generation 4f repair boundary

The broker receives one absolute monotonic deadline and its matching
caller-derived wall-clock expiry.  A private rollback signal crosses the
transaction seam whenever either clock has elapsed after a decision-derived
await; the caller catches it only after rollback and terminalizes the exact
request in a separate transaction.  This prevents a consumed grant or allowed
audit from committing after the callback has already failed.

Run completion uses a run-first, sequential repository protocol rather than a
data-modifying CTE visibility assumption: inspect blockers and grants under the
run lock, consume only valid run-wide grants, then perform the final guarded
completion.  A lost guard must abort the transaction so partial consumption
cannot survive.  The durable terminalization progress seam remains the single
owner for bounded batches and idempotent terminal run side effects.

DOCX relationship sets require exactly one root office-document relationship
whose Id is a unique XML NCName-compatible value.  Checkpoint-resume success
uses the same capability-owned required-artifact gate as ordinary execution.

`CHANGELOG.md` remains unchanged because #454 is still an open Draft PR and
requires independent test and Sol review before issue or release closure.
