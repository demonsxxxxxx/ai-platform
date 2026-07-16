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
