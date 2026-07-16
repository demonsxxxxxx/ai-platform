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
