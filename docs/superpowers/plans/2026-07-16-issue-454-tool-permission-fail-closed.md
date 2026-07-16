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
