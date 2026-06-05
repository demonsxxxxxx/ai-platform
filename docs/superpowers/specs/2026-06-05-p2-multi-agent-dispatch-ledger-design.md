# P2 Multi-Agent Dispatch Ledger Design

## Goal

Add a narrow P2 dispatch ledger contract for already-ready multi-agent steps. The
slice lets an admin/runtime operator claim a ready step and records that claim in
run steps, events, and audit logs without starting an autonomous scheduler,
opening new sandbox/tool privileges, or changing worker execution.

## Fact Sources

- Current `main` lineage through `649bea9` / `963c245`.
- PRD release gates G7 through G10, especially public/admin redaction and the
  Long Task / Multi-Agent ordering.
- Foundation roadmap P2 readiness and typed callback slices.
- Guardrails requiring focused tests, same-tenant boundaries, no private payload
  leakage, and 211 Docker/runtime verification only on a Docker-capable host.
- 211 runtime currently healthy at API port `8020`, deployed at
  `963c245a0404fef6109e78107aa179ba10e99ab3` with source note
  `p2-runtime-typed-callback-events`.

## Current State

`/api/ai/runs/{run_id}/control/readiness` already computes a public-safe
multi-agent dependency projection for `execution_mode = multi_agent`. It shows
configured and recorded steps, dependency statuses, ready/blocked counts, and a
dispatch gate that is always disabled with `runtime_dispatch_not_enabled`.

The worker and runtime callback paths already understand typed
`agent_step_*`, `subagent_*`, and checkpoint events. Existing `run_steps`
payloads can carry non-schema-breaking metadata, so this slice can avoid a DB
migration.

## Chosen Approach

Implement a controlled dispatch ledger claim, not a real scheduler.

1. Add an admin-only, same-tenant claim route:
   `POST /api/ai/runs/{run_id}/multi-agent/dispatch/claims`
   with body `{"step_key": "<safe step key>"}`.
2. Reuse the existing readiness calculation to validate that the run is
   explicitly multi-agent, active, the requested step exists, dependencies have
   succeeded, and the step is still pending.
3. Add a stricter claim-time guard that rejects raw/unsafe step references that
   would be hidden from ordinary public projections.
4. Claim the step by upserting the existing `run_steps` row to `running` with
   public-safe ledger metadata such as `dispatch_state = claimed`.
5. Append a hidden `agent_step_started` event and an audit log entry containing
   the dispatch id and admin claimant.
6. Update readiness so admin users see the dispatch gate as enabled only when
   at least one safe ready step exists. Ordinary users keep a disabled gate and
   no admin-only href.

## Rejected Approaches

### Keep Readiness Read-Only

This is safest but leaves no write-side contract between readiness and the
future scheduler. It would not advance P2 runtime control beyond the previous
slice.

### Start Real Subagent Scheduling

This moves too fast. It would require queue semantics, subprocess or worker
ownership, sandbox/tool escalation, retry/resume rules, and cancellation
semantics. Those are later P2 items and should not be opened before the ledger
contract is reviewed and deployed.

## Contract

### Request

`POST /api/ai/runs/{run_id}/multi-agent/dispatch/claims`

```json
{
  "step_key": "code"
}
```

The route requires an admin principal in the same tenant. Non-admin callers get
`403 admin_required`. Invalid unsafe ids fail validation before repository
writes.

### Success Response

```json
{
  "contract_version": "ai-platform.multi-agent-dispatch-claim.v1",
  "run_id": "run-a",
  "step_key": "code",
  "step_id": "step-code",
  "status": "claimed",
  "dispatch_id": "dispatch_x",
  "event_id": "evt_x",
  "audit_id": "aud_x",
  "step": {
    "step_id": "step-code",
    "status": "running",
    "payload": {
      "depends_on": ["plan"],
      "dispatch_state": "claimed",
      "dispatch_kind": "subagent"
    }
  }
}
```

### Conflict Responses

- `multi_agent_not_enabled`: the run is not explicitly `execution_mode =
  multi_agent`.
- `run_not_dispatchable`: the run is not `queued` or `running`.
- `step_not_found`: the step is neither configured nor recorded for the run.
- `unsafe_step_reference`: the target step key or dependency contains a raw
  skill/agent marker that must not become a public dispatch handle.
- `terminal_step`, `already_running`, `missing_dependencies`,
  `waiting_on_dependencies`, or `hidden_dependencies`: the readiness state is
  not claimable.

### Event And Audit

The claim appends a standard `agent_step_started` event with
`visible_to_user = false`. The payload may include raw dispatch ids or step keys
because the event is not visible to ordinary users. Admin projection can inspect
it.

The audit action is `run.multi_agent.dispatch.claim`, target type `run_step`,
and target id equal to the claimed step id. The audit payload records
`run_id`, `step_key`, `dispatch_id`, and `result_status = claimed`.

## Redaction And Tenant Boundaries

- The claim route is admin-only and same-tenant.
- Ordinary readiness keeps public step labels and raw skill redaction.
- Ordinary users never receive a dispatch claim href.
- Ordinary event/playback views do not receive the claim event.
- The step payload stores only public-safe dispatch state; claimant and dispatch
  ids live in the route response and audit/event records.

## Non-Goals

- No autonomous subagent process.
- No scheduler, queue consumer, or background job.
- No new sandbox/tool permissions.
- No Docker validation on the Windows workstation.
- No executor private payload, real `.env`, secret, or personal path in docs or
  code.

## Tests

Focused tests should cover:

- Admin readiness enables the dispatch gate when a safe ready step exists.
- Ordinary readiness keeps the gate disabled and does not expose an admin href.
- Admin claim writes a running step ledger, hidden event, and audit entry.
- Claim rejects hidden/unsafe dependency handles without writing ledger entries.
- Claim rejects non-admin callers.
- Existing non-multi-agent and redaction tests keep passing.

## Rollout

Local verification must run focused tests first, then compile, then full pytest
with `--basetemp .pytest-tmp`. Deployment and Docker smoke run only on 211. The
211 smoke must prove API health, container labels, OpenAPI route exposure, a
safe step claim, hidden event behavior for ordinary users, audit/step ledger
evidence, unsafe dependency rejection, and smoke data cleanup.
