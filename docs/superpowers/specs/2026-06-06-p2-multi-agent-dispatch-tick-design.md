# P2 Multi-Agent Dispatch Tick Design

## Goal

Add one bounded admin-only multi-agent dispatch tick that claims, hands off, and
enqueues a single safe ready parent step. This moves the runtime from manual
claim/handoff primitives toward operational scheduling without starting a
background scheduler or opening new sandbox/tool privileges.

## Current Context

The platform already has the required primitives:

- Read-only multi-agent dependency readiness.
- Admin-only dispatch claim with lease metadata and hidden event/audit evidence.
- Admin-only dispatch cleanup for expired claims.
- Admin-only child run handoff with context snapshot and queue payload creation.
- Worker-side child terminal reconciliation and parent terminal rollup.

The next narrow gap is an operator/runtime control that performs exactly one
safe claim and handoff for an active parent run.

## Contract

Add `POST /api/ai/runs/{run_id}/multi-agent/dispatch/tick`.

The route is admin-only. It locks the parent run, reads current parent steps,
selects the first safe ready step from the existing readiness projection, calls
the existing claim repository function, immediately calls the existing handoff
repository function, prepares the child queue payload through the existing
copied-run queue path, commits DB changes, then enqueues the child run.
Non-ready configured or recorded steps are skipped while scanning; structural
run errors still fail closed.

Response contract: `ai-platform.multi-agent-dispatch-tick.v1`.

Returned fields include parent run id, selected step key/id, dispatch id, child
run id, session id, queue position, queue insight, and claim/handoff event/audit
ids. It must not return executor private payload, raw runtime payload, storage
keys, command fingerprints, or sandbox internals.

## Fail-Closed Rules

- Non-admin callers get `403 admin_required`.
- Missing parent run gets `404 run_not_found`.
- Non-active parent runs get `409 run_not_dispatchable`.
- Non-`multi_agent` parent runs get `409 multi_agent_not_enabled`.
- Parent runs with no ready step get `409 no_ready_steps`.
- Parent runs whose only ready steps are unsafe/raw projection references get
  `409 no_safe_ready_steps`.
- Repository conflicts from claim or handoff are surfaced as `409`.
- Claim writes are conditional: a stale concurrent writer cannot overwrite a
  step that stopped being `pending` or already moved into dispatch handoff.
- Unsafe step/dependency references include empty or sanitized-changing values,
  hash-like values, forbidden private-key aliases, invalid public ids, and raw
  private projection terms.

## Boundaries

This slice does not add a polling scheduler, worker loop, DB migration,
frontend entry, high-risk tool execution, or sandbox privilege expansion. It
does not bypass the existing context snapshot, queue, dispatch lease, audit, or
redaction paths.

## Verification

Focused local tests must prove admin-only access, no-ready fail-closed behavior,
unsafe-ready fail-closed behavior, successful claim/handoff/enqueue sequencing,
and backward compatibility with existing claim/handoff route tests.

211 smoke must prove the live route creates exactly one queued child run from a
safe ready step, writes claim/handoff evidence, uses the existing queue path,
keeps ordinary-user projections free of hidden control events, and cleans smoke
rows after verification.
