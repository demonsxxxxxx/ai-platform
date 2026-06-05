# P2 Multi-Agent Child Completion Reconciliation Design

## Goal

Close the next bounded P2 runtime gap after controlled child-run handoff: when a server-owned multi-agent child run reaches a terminal state, reconcile that state back to the parent dispatch step so dependency readiness, provenance, resume, and checkpoint projections reflect the child result.

## Current State

- `POST /api/ai/runs/{run_id}/multi-agent/dispatch/claims/{dispatch_id}/handoff` creates one queued child run and marks the parent step payload with `dispatch_state = handed_off`, `dispatch_child_run_id`, and `dispatch_handed_off_at`.
- `complete_run`, `fail_run`, and `cancel_run` only update the terminal child run and its own steps.
- There is no existing reconcile route, worker hook, or repository helper that advances the parent step from `handed_off` to a terminal state.

## Contract

Add an internal repository contract:

```python
async def reconcile_multi_agent_child_run_terminal_state(
    conn,
    *,
    tenant_id: str,
    child_run_id: str,
    child_status: Literal["succeeded", "failed", "cancelled"],
    result_json: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any] | None:
    ...
```

Behavior:

- Validate the child run row and its parent relationship from persisted database state, not from queue payload alone.
- Require same `tenant_id`.
- Require the persisted child run `status` to match the requested terminal status and be one of `succeeded`, `failed`, or `cancelled`.
- Require child `copied_from_run_id` to point at the parent run.
- Require child execution input to contain server-owned `multi_agent_dispatch.parent_run_id`, `parent_step_id`, `dispatch_id`, and `step_key`.
- Require the parent step row to match `parent_step_id`, `dispatch_id`, `dispatch_child_run_id = child_run_id`, and `dispatch_state = handed_off`.
- Return `None` without side effects when the run is not a valid server-owned handed-off child.
- On success:
  - update the parent step `status` to the child terminal status mapped as `succeeded`, `failed`, or `cancelled`;
  - set `dispatch_state` to `completed`, `failed`, or `cancelled`;
  - store `dispatch_child_status`, `dispatch_reconciled_at`, and public-safe result metadata;
  - for succeeded child runs with a message, store `output` and a deterministic safe checkpoint id `checkpoint_{parent_step_id}` plus `source_step_id = parent_step_id`;
  - for failed or cancelled child runs, store a public-safe `error_code` with fallback to `child_run_failed` or `child_run_cancelled`;
  - append hidden parent event `multi_agent_dispatch_reconciled`;
  - append audit action `run.multi_agent.dispatch.reconcile`.

## Worker Integration

After the worker persists a terminal child run with `complete_run`, `fail_run`, or `cancel_run`, call the repository reconcile helper in the same transaction. The hook remains internal and does not add a user-facing route.

The hook must be best-effort fail-closed:

- malformed or forged dispatch metadata should produce no parent mutation;
- a stale or already reconciled parent step should not fail the child terminal write;
- worker completion should not expose executor private payload to the parent step.

## Security And Redaction

- No new public frontend entry.
- No new high-risk sandbox or tool capability.
- No executor private payload, storage key, runtime path, command fingerprint, secret-like value, or personal path is written into parent step payload.
- Adapter or worker supplied `error_code` values are sanitized and constrained before copying to parent step payload.
- Parent events are hidden from ordinary user event streams unless a later public projection explicitly adds a safe summary.
- The repository validates database relationship before mutation, so a forged queue payload cannot update an unrelated parent run.

## Test Scope

Focused tests should prove:

- a succeeded child run reconciles the parent step, writes safe output/checkpoint metadata, hidden event, and audit;
- failed and cancelled child runs map to parent step terminal state without copying private result payload;
- non-terminal or status-mismatched child rows do not reconcile even if caller requests a terminal status;
- unsafe child error codes fall back to public-safe platform codes;
- forged child metadata without matching parent step/child relationship returns `None` and does not mutate;
- stale or already reconciled parent step updates do not append event/audit rows;
- worker success/failure/cancel terminal paths call reconciliation for child dispatch payloads and do not call it for ordinary runs.
