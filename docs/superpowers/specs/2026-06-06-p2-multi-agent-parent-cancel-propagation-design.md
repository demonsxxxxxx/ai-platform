# P2 Multi-Agent Parent Cancel Propagation Design

## Goal

When an owner or admin cancels a multi-agent parent run, the platform must propagate a safe cancel request to server-owned handed-off child runs in the same tenant. This closes the control-plane gap after controlled child handoff and child terminal reconciliation without starting autonomous scheduling, new worker roles, high-risk tool access, or new sandbox behavior.

## Current Facts

- PRD requires queue lifecycle cancel semantics to be backend-controlled and forbids continued side effects after run cancel.
- Roadmap P2 has already deployed controlled child handoff and child terminal reconciliation on 211.
- Current routes only cancel the target run and remove the target queued payload.
- Current worker checks cancel state only for its own run id.
- Handoff child runs are identified by `copied_from_run_id` plus server-owned `input.multi_agent_dispatch` metadata and matching parent step payload.

## Contract

Add a repository helper:

```python
async def propagate_multi_agent_parent_cancel(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    parent_run_id: str,
    requested_by: str,
    requested_by_role: str | None = None,
) -> dict[str, Any]:
    ...
```

The helper returns:

```python
{
    "child_run_ids": list[str],
    "queued_child_run_ids": list[str],
    "running_child_run_ids": list[str],
    "active_sandbox_leases": list[dict[str, Any]],
    "event_ids": list[str],
    "audit_ids": list[str],
}
```

Only active child runs are eligible:

- same `tenant_id`;
- `runs.copied_from_run_id = parent_run_id`;
- status is `queued` or `running`;
- child execution input contains `multi_agent_dispatch.parent_run_id = parent_run_id`;
- matching parent step has `dispatch_state = handed_off` and `dispatch_child_run_id = child_run_id`.

Queued children become terminal `cancelled`, open child steps are cancelled, their queued Redis payloads are removed by the route after the DB transaction commits, and existing child terminal reconciliation mirrors the cancelled child onto the parent step.

Running children keep status `running`, receive `cancel_requested_at/by`, and return any active sandbox leases so the cancel route can stop and release those leases with the same sandbox cleanup semantics as direct run cancellation. The worker then observes the child run's own cancel flag and finishes the child as cancelled.

## Safety Rules

- Do not cancel ordinary copied runs without the server-owned multi-agent dispatch relationship.
- Do not cross tenants.
- Do not read or emit executor private payload, raw command, runtime path, storage key, or secret-like data.
- Parent propagation events are hidden operational evidence.
- Child cancel events are ordinary safe cancel events for the child owner.
- Admin propagation preserves `requested_by_role = "admin"` in audit and event payloads.
- Redis queue mutation stays outside the DB transaction and runs only for child ids returned as queued after DB cancellation.

## Route Integration

Owner and admin cancel routes continue to call their existing repository cancel helpers. The routes call propagation in the same DB transaction after the parent cancel update succeeds. After the transaction commits, the route then:

1. Attempts queued Redis payload cleanup for the parent if it became `cancelled` and for returned queued child ids.
2. Records queue cleanup errors without skipping sandbox runtime cleanup.
3. Stops parent and child active sandbox leases returned by the repository.
4. Releases successfully stopped leases grouped by run id.
5. Returns `sandbox_runtime_cleanup_failed` if sandbox stop fails; otherwise returns `queue_cleanup_failed` if queued Redis cleanup failed.

## Testing

Focused regression coverage must prove:

- server-owned queued and running child runs are propagated safely;
- ordinary copied runs or forged dispatch payloads are ignored;
- owner and admin routes remove queued child Redis payloads after DB cancel;
- owner and admin routes still stop/release child sandbox leases when queued Redis cleanup fails;
- owner and admin routes still try queued child Redis cleanup when sandbox stop fails;
- running child sandbox leases returned by propagation are stopped and released by route cleanup;
- event/audit payloads contain only safe ids and cancel metadata.

## Non-Goals

- No DB migration.
- No autonomous scheduler.
- No polling subagent worker process.
- No new frontend entry.
- No new sandbox provider or tool permission expansion.
- No external project source-of-truth change.
