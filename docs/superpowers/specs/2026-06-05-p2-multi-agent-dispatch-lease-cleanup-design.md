# P2 Multi-Agent Dispatch Lease Cleanup Design

## Goal

Add an admin-only cleanup contract for expired multi-agent dispatch claims so a
claimed step cannot remain `running` forever before the platform has a real
subagent scheduler.

## Scope

This slice extends the existing dispatch claim ledger only. It records lease
metadata in the claimed step payload, exposes an Admin Runtime cleanup route,
and reclaims expired claimed steps back to `pending` with audit evidence.

This does not enqueue child runs, start a scheduler, run subagent workers, open
sandbox access, or grant new tool permissions.

## Contract

- `claim_multi_agent_dispatch_step` stores:
  - `dispatch_id`
  - `dispatch_state = claimed`
  - `dispatch_kind = subagent`
  - `dispatch_claimed_by`
  - `dispatch_claimed_at`
  - `dispatch_lease_expires_at`
- `POST /api/ai/admin/runtime/multi-agent/dispatch/cleanup` is admin-only.
- Cleanup is same-tenant and only targets `run_steps` where:
  - `status = running`
  - `payload_json.dispatch_state = claimed`
  - `payload_json.dispatch_lease_expires_at` is older than the cleanup clock
- Cleanup sets the step back to `pending`, changes `dispatch_state` to
  `expired`, records `dispatch_expired_at`, and writes
  `run.multi_agent.dispatch.expire` audit rows.
- Cleanup parses lease timestamps in repository code instead of casting JSON
  text in SQL. Malformed or future lease timestamps are skipped, and cleanup
  scans past skipped candidates until the requested number of actual expired
  claims is reclaimed or candidates are exhausted.

## Safety

The route returns only operational identifiers and counts. It does not return
executor private payload, raw tool payload, storage keys, runtime paths, or
secret-bearing data.

Malformed or missing lease timestamps are not reclaimed by this slice. They
remain visible to admin through existing run-step projections and can be
handled by a later repair slice if needed.

Cleanup uses `skip locked` so an Admin operation does not block behind a
concurrently updated run step.

## Verification

Focused tests cover claim payload lease metadata, stale lease marker cleanup,
admin route authorization, same-tenant cleanup invocation, repository
update/audit behavior, malformed timestamp skip behavior, mixed candidate
scanning, and existing dispatch route behavior.

211 smoke should seed an expired claimed step, call the admin cleanup route,
verify the step returns to `pending` with `dispatch_state = expired`, verify the
audit action, and verify smoke cleanup leaves zero rows.
