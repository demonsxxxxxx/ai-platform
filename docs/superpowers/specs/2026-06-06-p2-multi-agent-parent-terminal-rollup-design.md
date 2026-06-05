# P2 Multi-Agent Parent Terminal Rollup Design

## Goal

Close the next bounded P2 runtime gap after controlled child-run handoff,
terminal child reconciliation, and parent cancel propagation: once all
server-owned multi-agent parent steps are terminal, the platform must safely
finalize the parent run itself.

This keeps the existing manual/admin-controlled dispatch flow operationally
coherent without starting an autonomous scheduler, a polling subagent worker, a
new sandbox provider, or any high-risk tool execution path.

## Current State

- The PRD requires unified cancel, resume, checkpoint, artifact, and event
  semantics before Long Task / Multi-Agent Runtime can be opened more broadly.
- The roadmap records deployed P2 slices for controlled child handoff, terminal
  child reconciliation, and parent cancel propagation.
- `reconcile_multi_agent_child_run_terminal_state()` validates a persisted
  child-to-parent relationship and mirrors terminal child state onto a parent
  dispatch step.
- The worker calls child reconciliation after terminal child success, failure,
  or cancellation.
- Current parent runs can still remain `running` after all handed-off child
  steps have reconciled, because no helper rolls parent step state up to the
  parent run status.

## Contract

Add an internal repository helper:

```python
async def finalize_multi_agent_parent_run_if_ready(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    parent_run_id: str,
    triggered_by_child_run_id: str | None = None,
) -> dict[str, Any] | None:
    raise NotImplementedError("repository contract")
```

The helper is called from `reconcile_multi_agent_child_run_terminal_state()`
after the parent dispatch step update succeeds.

Owner and admin cancel routes also call the helper after parent-cancel
propagation inside the same database transaction. This covers the race where a
final child reconciliation uses `skip locked` while the cancel transaction holds
the parent row lock; once propagation has settled any active children, the
cancel path gets a final in-transaction chance to close an otherwise terminal
parent.

The worker also performs a bounded post-commit retry after successful child
terminal reconciliation. This covers concurrent sibling child completions where
one in-transaction finalization sees another child as still active and the other
attempt skips the locked parent; the post-commit retry uses a fresh transaction
and the same repository helper instead of adding a scheduler.

The helper must return `None` without side effects unless all of these are true:

- the parent run exists in the same `tenant_id`;
- the parent run status is `running` or it has `cancel_requested_at` set;
- the parent execution input has `execution_mode = "multi_agent"`;
- the parent execution input has at least one configured `multi_agent_steps`
  entry or the parent has at least one recorded run step;
- every recorded or configured multi-agent step is terminal;
- there is no active server-owned child run for the parent in `queued` or
  `running` state.

Terminal parent status is derived from public parent step state:

- if any parent step is `failed`, the parent becomes `failed`;
- else if the parent has `cancel_requested_at` or any parent step is
  `cancelled`, the parent becomes `cancelled`;
- else all parent steps are `succeeded`, so the parent becomes `succeeded`.

The returned summary shape is:

```python
{
    "parent_run_id": "run-parent",
    "status": "succeeded" | "failed" | "cancelled",
    "event_id": "evt_parent_finalized",
    "audit_id": "aud_parent_finalized",
    "counts": {
        "total": 2,
        "succeeded": 2,
        "failed": 0,
        "cancelled": 0,
    },
}
```

## Parent Result Payload

The parent run `result_json` is a public-safe operational summary:

```python
{
    "message": "Multi-agent run succeeded",
    "multi_agent": {
        "status": "succeeded",
        "counts": {
            "total": 2,
            "succeeded": 2,
            "failed": 0,
            "cancelled": 0,
        },
        "steps": [
            {
                "step_key": "plan",
                "status": "succeeded",
                "role": "planner",
                "sequence": 1,
                "depends_on": [],
                "checkpoint_id": "checkpoint_step-plan",
                "source_step_id": "step-plan",
                "child_run_id": "run-child-plan",
                "dispatch_state": "completed",
                "output": "safe public output"
            }
        ],
        "triggered_by_child_run_id": "run-child-plan"
    }
}
```

The helper copies only public-safe values already present on parent steps:

- `step_key`, `status`, `role`, `sequence`, `depends_on`;
- safe `dispatch_state`, `dispatch_child_run_id`, `checkpoint_id`,
  `source_step_id`;
- sanitized `output`, `error_code`, and `error`.

The helper must not copy executor private payload, storage keys, runtime paths,
raw command text, command fingerprints, secret-like values, personal paths, or
raw child `result_json`.

## Events And Audit

Finalization appends one hidden operational parent event:

- `event_type = "multi_agent_parent_finalized"`;
- `stage = "control"`;
- `visible_to_user = False`;
- payload includes only parent run id, status, safe counts, and optional
  `triggered_by_child_run_id`.

Finalization appends one audit record:

- `action = "run.multi_agent.parent.finalize"`;
- `target_type = "run"`;
- `target_id = parent_run_id`;
- payload includes status, safe counts, and optional
  `triggered_by_child_run_id`.

Public user-facing status still comes from the normal run, playback, and SSE
projections. No new frontend entry is added.

## Safety Rules

- Do not finalize ordinary copied runs or non-multi-agent runs.
- Do not finalize while any same-tenant server-owned child run is still
  `queued` or `running`.
- Do not finalize while any configured or recorded parent step is pending,
  running, claimed, or handed off.
- Do not cross tenants.
- Do not trust queue payload alone; use persisted parent run and step state.
- Do not re-finalize a parent already in a terminal state.
- Do not expand sandbox, tool, or scheduler privileges.

## Testing

Focused regression coverage must prove:

- all-success terminal parent steps finalize the parent run as `succeeded`;
- a failed parent step finalizes the parent run as `failed`;
- a cancelled parent step or cancel-requested parent finalizes as `cancelled`;
- pending, running, claimed, handed-off, or hidden active-child state prevents
  finalization;
- non-multi-agent and ordinary copied runs are ignored;
- the helper is invoked after child reconciliation succeeds and is not invoked
  when reconciliation returns `None` or the parent-step update is stale;
- parent result/event/audit payloads redact executor private payload, storage
  key, runtime path, command fingerprint, secret-like text, and raw child
  result data;
- existing run/playback/SSE multi-agent snapshots keep returning public
  projection data after parent finalization.

## Non-Goals

- No DB migration.
- No autonomous scheduler.
- No polling subagent dispatcher or new worker process.
- No new frontend entry.
- No new sandbox provider behavior.
- No high-risk tool permission expansion.
- No external project source-of-truth change.
