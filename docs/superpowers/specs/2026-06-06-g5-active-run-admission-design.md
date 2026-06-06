# G5 Active Run Admission Design

## Goal

Close the next issue #16 G5 concurrency gap by making per-user active-run
admission serialized inside the database transaction that creates the next run.

## Current Gap

The current routes call `count_active_runs_for_user()` before inserting a queued
run. That check is same-tenant and covered by route tests, but it is not
serialized. Two concurrent requests for the same tenant/user can both observe
the same active count and then both insert queued runs.

The copy-run route also creates a new queued run without active-run admission,
while create, chat, retry, and resume already have route-level checks.

## Proposed Contract

Add a repository helper:

```python
async def enforce_user_active_run_admission(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    limit: int,
) -> int:
    ...
```

Behavior:

- `limit <= 0`: return `0` without locking or counting.
- Otherwise acquire a transaction-scoped advisory lock for the exact
  `(tenant_id, user_id)` admission scope.
- Count same-tenant active runs with status in `('queued', 'running')`.
- If `count >= limit`, raise
  `RepositoryConflictError("user_active_run_limit_exceeded")`.
- Return the observed active count for observability/tests.

Use a text-derived lock key, for example:

```sql
select pg_advisory_xact_lock(hashtextextended(%s, 0))
```

with lock input `tenant_id || ':' || user_id`. This keeps same-user admission
serialized until the surrounding transaction commits or rolls back, without
introducing a new migration table for the first hardening slice.

## Integration Points

- `app/routes/chat.py`: keep the existing local route helper name, but delegate
  to `repositories.enforce_user_active_run_admission(...)`.
- `app/routes/runs.py`: same replacement for create, retry, and resume.
- `app/routes/runs.py`: add missing admission before `copy_run_as_new_task(...)`.
- Do not add admission to server-owned multi-agent child handoff in this slice;
  that path is controlled by the multi-agent gate and should be handled by a
  separate parent/child quota design if needed.

## Non-Goals

- No new frontend behavior.
- No new Admin Runtime widgets in this slice.
- No queue Redis schema change.
- No DB migration table unless review finds advisory locks insufficient.
- No change to `max_active_worker_runs` or tenant/user processing quota lease
  semantics.

## Verification

Local:

- RED repository tests must prove the helper uses an advisory transaction lock
  before counting and rejects at the limit.
- Route tests must prove create/chat/retry/resume call the admission helper.
- A new copy-run route test must prove copy rejects before creating/enqueuing a
  copied run when the limit is reached.
- Focused tests cover repository, chat route, run route, and run-control route.

211:

- Deploy only after full local pytest and inherited-configuration review.
- Smoke in the API or worker container by running two concurrent DB transactions
  for one smoke tenant/user with `limit=1`: the first transaction admits and
  inserts a queued run before commit; the second transaction must wait on the
  advisory lock, then reject after observing the first queued run.
- Verify smoke rows are cleaned and logs have no recent error markers.

