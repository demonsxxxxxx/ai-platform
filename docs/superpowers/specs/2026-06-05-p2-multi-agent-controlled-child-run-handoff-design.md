# P2 Multi-Agent Controlled Child Run Handoff Design

## Goal

Add an admin-only handoff contract that turns an existing claimed multi-agent
dispatch step into one queued child run, while preserving parent linkage,
tenant boundary, owner identity, context snapshot, and audit evidence.

## Scope

This slice starts the smallest write-side bridge after the dispatch ledger and
lease cleanup. It creates a child run only when an admin explicitly hands off a
previously claimed step. It does not add an autonomous scheduler, polling
subagent dispatcher, new worker process, sandbox privilege expansion, tool
permission bypass, DB migration, or frontend entry.

## Approaches Considered

Recommended: add a separate handoff route under the existing dispatch claim:
`POST /api/ai/runs/{run_id}/multi-agent/dispatch/claims/{dispatch_id}/handoff`.
This keeps claim and execution handoff separate, makes retry/duplicate behavior
auditable, and allows lease cleanup to continue reclaiming claims that were
never handed off.

Alternative A: extend the claim route to immediately create the child run. This
couples ledger creation to execution and removes the useful intermediate state
that 211 already verifies.

Alternative B: add another read-only projection first. This is lower risk, but
the current ledger already has an explicit claimed state and the next missing
runtime primitive is controlled child-run creation.

## Contract

- The route is admin-only and same-tenant.
- The route takes `dispatch_id` from the path and an empty or omitted JSON body.
- The route requires the parent run to remain active (`queued` or `running`).
- The claimed parent step must be `running`, have
  `dispatch_state = claimed`, match the path `dispatch_id`, and have an
  unexpired `dispatch_lease_expires_at`.
- If the claimed step already has `dispatch_child_run_id`, the route fails with
  `409 dispatch_already_handed_off`.
- If the claim lease is malformed, missing, or expired, the route fails before
  any child run or queue payload is created.
- The child run is queued under the parent run's original `user_id`,
  `session_id`, `workspace_id`, `agent_id`, and `skill_id`, not under the admin
  user who triggered the handoff.
- The child run stores `copied_from_run_id = parent_run_id` and a public-safe
  input payload containing:
  - original user input after existing `sanitize_user_control_input`;
  - `execution_mode = multi_agent`;
  - a single `multi_agent_steps` entry for the handed-off step;
  - server-owned `multi_agent_dispatch` metadata with parent run, parent step,
    step key, and dispatch id;
  - server-owned `resume.completed_step_outputs` and
    `resume.completed_step_checkpoints` for already succeeded dependencies.
- The route reuses `prepare_copied_run_for_queue` with source
  `multi_agent_dispatch_handoff`, using the child run owner identity for
  context snapshot and queue payload creation.
- The parent step payload is updated with `dispatch_state = handed_off`,
  `dispatch_child_run_id`, and `dispatch_handed_off_at`.
- The parent run records a hidden control event
  `multi_agent_dispatch_handoff`.
- The child run records a visible control event
  `run_multi_agent_child_created`.
- Audit records `run.multi_agent.dispatch.handoff` with parent run id, parent
  step id, step key, dispatch id, child run id, and admin user id.

## Safety

No executor private payload, raw command, storage key, runtime path, command
fingerprint, real `.env`, or secret-like value is returned. Ordinary users do
not receive an admin handoff link. The child run only uses the existing queue
and worker identity checks, so mismatched queue payloads still fail closed.

The helper does not trust user-controlled `resume` or `multi_agent_dispatch`
input from the parent run. It rebuilds those blocks from run-step rows and the
validated dispatch claim.

## Verification

Focused local tests cover admin-only routing, happy-path handoff with owner
identity queue payload/context source, duplicate handoff rejection, expired or
malformed lease rejection without enqueue, unsafe/private payload redaction,
and repository event/audit/parent-step updates.

211 smoke should create a parent multi-agent run, seed a succeeded dependency,
claim a ready step, hand it off, verify the child run is queued and linked,
verify parent step payload state, verify audit/event evidence, verify ordinary
user cannot call the route, and clean up smoke DB/Redis rows.
