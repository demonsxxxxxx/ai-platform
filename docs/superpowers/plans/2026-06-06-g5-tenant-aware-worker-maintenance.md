# G5 Tenant-Aware Worker Maintenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make worker-side expired-memory maintenance tenant-aware with a bounded rotating scope cursor.

**Architecture:** Add a repository cleanup helper that rotates through tenant/workspace scopes with a persistent maintenance cursor, soft-deletes a bounded per-scope batch of expired active memory records, then make `app.worker_main` group returned rows by tenant/workspace and write redacted operational audit rows before queue leasing.

**Tech Stack:** Python async worker, psycopg async repository helpers, Postgres partial index, pytest async tests.

---

### Task 1: RED Worker And Repository Tests

**Files:**
- Modify: `tests/test_worker_main.py`
- Modify: `tests/test_repositories.py`
- Modify: `tests/test_schema.py`

- [ ] **Step 1: Add multi-scope worker cleanup test**

Add `test_run_once_cleans_expired_memory_records_across_tenant_workspaces` in
`tests/test_worker_main.py`. Patch `repositories.cleanup_expired_memory_records_across_scopes`
to return rows for two tenant/workspace pairs. Assert cleanup runs before queue
reclaim, does not access `default_tenant_id/default_workspace_id`, and appends
two sanitized `worker.memory.retention.cleanup` audit rows grouped by scope.

- [ ] **Step 2: Add all-scope repository SQL test**

Add `test_cleanup_expired_memory_records_across_scopes_soft_deletes_bounded_rows`
in `tests/test_repositories.py`. Assert the SQL updates `memory_records`, uses
`expires_at <= now()`, uses `cross join lateral`, includes `for update skip
locked`, returns tenant/workspace/user metadata, and omits `content` and
`metadata_json`.

- [ ] **Step 3: Add bounded cursor repository SQL test**

Add `test_cleanup_expired_memory_records_across_scopes_uses_bounded_scope_cursor`
in `tests/test_repositories.py`. Assert the helper locks the
`worker_maintenance_cursors` row, selects grouped tenant/workspace scopes after
the previous cursor, updates the cursor with `on conflict`, and passes a
per-scope row cap into the lateral cleanup query.

- [ ] **Step 4: Add schema cursor/index assertion**

Update `tests/test_schema.py` to assert
`idx_memory_records_expired_cleanup` exists and is declared as a partial active
non-deleted expiring-record index, and that `worker_maintenance_cursors` exists.

- [ ] **Step 5: Run RED**

Run:

```powershell
python -m pytest tests/test_worker_main.py::test_run_once_cleans_expired_memory_records_across_tenant_workspaces tests/test_repositories.py::test_cleanup_expired_memory_records_across_scopes_soft_deletes_bounded_rows tests/test_repositories.py::test_cleanup_expired_memory_records_across_scopes_uses_bounded_scope_cursor tests/test_schema.py::test_schema_declares_p0_memory_tool_event_and_sandbox_contracts -q --basetemp .pytest-tmp\g5-worker-maintenance-red
```

Expected: worker/repository tests fail because the all-scope cleanup helper and
bounded scope cursor are not implemented yet.

### Task 2: Implement Bounded Scope Cursor Cleanup

**Files:**
- Modify: `app/repositories.py`
- Modify: `app/worker_main.py`
- Modify: `app/schema.sql`

- [ ] **Step 1: Add repository helper**

Add `cleanup_expired_memory_records_across_scopes(conn, *, limit: int = 200)`.
Validate `limit > 0`, lock/read the `memory_retention_cleanup` cursor, select
bounded grouped tenant/workspace scopes after the cursor with wrap-around,
update the cursor to the last selected scope, and soft-delete active
non-deleted expired rows with a per-scope lateral row cap. Prioritize the first
locked row from each selected scope before filling the remaining budget. Return
only operational fields.

- [ ] **Step 2: Add schema index and cursor table**

Add:

```sql
create index if not exists idx_memory_records_expired_cleanup
  on memory_records(expires_at asc, created_at asc, tenant_id, workspace_id, id)
  where status = 'active'
    and deleted_at is null
    and expires_at is not null;

create table if not exists worker_maintenance_cursors (
  cursor_key text primary key,
  tenant_id text,
  workspace_id text,
  updated_at timestamptz not null default now()
);
```

- [ ] **Step 3: Update worker cleanup tick**

Replace the default-tenant cleanup call with
`cleanup_expired_memory_records_across_scopes`. Group returned rows by
`tenant_id/workspace_id`; write one sanitized audit row per group. Keep the
existing interval/enable/limit semantics.

- [ ] **Step 4: Run GREEN**

Run the RED command again and confirm it passes. Also run the cursor-specific
RED command once after implementing the cursor query:

```powershell
python -m pytest tests/test_repositories.py::test_cleanup_expired_memory_records_across_scopes_uses_bounded_scope_cursor -q --basetemp .pytest-tmp\g5-worker-maintenance-cursor-green
```

### Task 3: Focused Verification And Roadmap

**Files:**
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`
- Modify: `docs/superpowers/plans/2026-06-06-g5-tenant-aware-worker-maintenance.md`

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m pytest tests/test_worker_main.py tests/test_repositories.py tests/test_schema.py tests/test_context_routes.py -q --basetemp .pytest-tmp\g5-worker-maintenance-focused
```

Expected: affected worker, repository, schema, and manual context cleanup tests pass.

- [ ] **Step 2: Update roadmap and plan evidence**

Record this as the G5 tenant-aware worker maintenance follow-up after local
verification and review. Keep remaining issue #16 blockers explicit and keep
execution evidence in this plan or release evidence, not as detailed roadmap
history.

### Task 4: Review, Full Verification, PR, And 211 Smoke

**Files:**
- Review all touched files.

- [ ] **Step 1: Request inherited-configuration review**

Use available multi-agent review if the tool inherits the main session
permissions. If model/reasoning fields are not exposed, record the review as
inherited-configuration review and do not claim a model-specific gate.

- [ ] **Step 2: Run final local verification**

Run:

```powershell
python -m compileall -q app tools scripts
python -m pytest -q --basetemp .pytest-tmp\g5-worker-maintenance-full
git diff --check
```

- [ ] **Step 3: Commit, push, and create PR**

Commit on a feature branch, push, create a PR against `main`, merge only after
review and full verification pass.

- [ ] **Step 4: Deploy and smoke on 211**

Deploy the merged main commit to 211. Verify image/source label alignment,
`/api/ai/health`, frontend proxy health, multi-scope memory cleanup, per-scope
sanitized audit evidence, clean API/worker logs, and smoke data cleanup.

## Current Execution Evidence

- RED worker/repository/schema tests initially failed because the worker still
  used default scope, the all-scope helper was missing, and the schema index was
  missing.
- After the first review, RED cursor coverage failed against the global
  oldest-first batch and the implementation was changed to use a persistent
  `worker_maintenance_cursors` row.
- After the second review, RED first-row-per-scope coverage failed against the
  global candidate `limit`; the SQL now prioritizes `scope_rank = 1` before
  filling the remaining budget.
- 211 temporary-table SQL validation for the fairness query returned
  `TEMP_FAIRNESS_COUNTS=tenant-a:2,tenant-b:1,tenant-c:1,tenant-d:1` with
  `limit=5`, proving the fourth selected scope is not starved by older backlog
  from earlier scopes.
- Local focused verification passed with
  `python -m pytest tests/test_worker_main.py tests/test_repositories.py tests/test_schema.py tests/test_context_routes.py -q --basetemp .pytest-tmp\g5-worker-maintenance-focused-precommit-2`
  at `195 passed`.
- Local source-authority docs verification passed with
  `python -m pytest tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\g5-worker-maintenance-docs-final`
  at `8 passed`.
- Local compile and diff checks passed:
  `python -m compileall -q app tools scripts` and `git diff --check`.
- Final full local pytest passed with
  `python -m pytest -q --basetemp .pytest-tmp\g5-worker-maintenance-precommit-full-2`
  at `1066 passed, 6 skipped, 2 warnings`.
- Inherited-configuration review is used because the available subagent tool
  inherits current configuration and does not expose explicit `model` or
  `reasoning_effort` fields.
- Final inherited-configuration review reported no Critical or Important
  findings after the scope-cursor and first-row-per-scope fairness fixes.
