# P1 Memory Retention Worker Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bounded worker-side expired-memory cleanup so P1 Memory / Context retention cleanup no longer depends only on the admin manual route.

**Architecture:** Insert a process-local maintenance tick in `app.worker_main.run_once` after sandbox cleanup and before queue reclaim. The tick reuses `repositories.cleanup_expired_memory_records`, respects settings, writes only operational audit evidence when rows are deleted, and keeps maintenance failures fail-closed.

**Tech Stack:** Python async worker, Pydantic settings, psycopg async repository helpers, pytest async tests.

---

### Task 1: Add RED Worker Cleanup Tests

**Files:**
- Modify: `tests/test_worker_main.py`

- [ ] **Step 1: Write the failing due-cleanup test**

Add an async test that monkeypatches worker settings, transaction, repository cleanup, audit, queue reclaim, and queue lease. The test should call `run_once`, expect cleanup before queue reclaim, and assert the audit payload contains only operational ids/counts.

- [ ] **Step 2: Write the failing interval-skip test**

Add an async test that calls `run_once` twice with a long interval and asserts the repository cleanup helper runs once while queue reclaim and lease run twice.

- [ ] **Step 3: Write the failing disabled-cleanup test**

Add an async test that sets `memory_retention_worker_cleanup_enabled = False`, patches cleanup to raise if called, and verifies queue reclaim and lease still run.

- [ ] **Step 4: Run RED**

Run:

```powershell
python -m pytest tests/test_worker_main.py::test_run_once_cleans_expired_memory_records_when_due tests/test_worker_main.py::test_run_once_skips_memory_cleanup_until_interval_elapsed tests/test_worker_main.py::test_run_once_skips_memory_cleanup_when_disabled -q --basetemp .pytest-tmp\p1-memory-worker-cleanup-red
```

Expected: failures because `app.worker_main` has no memory cleanup tick or settings yet.

### Task 2: Implement Settings And Worker Tick

**Files:**
- Modify: `app/settings.py`
- Modify: `app/worker_main.py`
- Modify: `deploy/ai-platform/.env.example`

- [ ] **Step 1: Add settings**

Add these fields near other worker/runtime settings:

```python
memory_retention_worker_cleanup_enabled: bool = Field(default=True)
memory_retention_worker_cleanup_interval_seconds: float = Field(default=300.0)
memory_retention_worker_cleanup_limit: int = Field(default=200)
```

Add the matching non-secret deployment template knobs to `deploy/ai-platform/.env.example`:

```text
MEMORY_RETENTION_WORKER_CLEANUP_ENABLED=true
MEMORY_RETENTION_WORKER_CLEANUP_INTERVAL_SECONDS=300
MEMORY_RETENTION_WORKER_CLEANUP_LIMIT=200
```

- [ ] **Step 2: Add process-local tick state**

In `app.worker_main`, import `time` and add a module-level timestamp such as `_next_memory_cleanup_at = 0.0`.

- [ ] **Step 3: Add cleanup helper**

Create `cleanup_expired_memory_records_for_worker(settings=None, now=None)` that:

- Returns early when disabled.
- Returns early when interval has not elapsed.
- Treats a non-positive limit or interval as disabled.
- Opens `transaction()`.
- Calls `repositories.cleanup_expired_memory_records(conn, tenant_id=settings.default_tenant_id, workspace_id=settings.default_workspace_id, limit=settings.memory_retention_worker_cleanup_limit)`.
- Appends `worker.memory.retention.cleanup` audit only when rows were deleted.
- Updates `_next_memory_cleanup_at` to `now + interval` only after a completed scan.

- [ ] **Step 4: Invoke helper in run_once**

Call the helper after `cleanup_expired_sandbox_leases()` and before `queue.reclaim_expired_leases()`.

- [ ] **Step 5: Run GREEN**

Run the three new tests with the same command. Expected: all pass.

### Task 3: Focused Verification And Docs

**Files:**
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`

- [ ] **Step 1: Run affected tests**

Run:

```powershell
python -m pytest tests/test_worker_main.py tests/test_context_routes.py tests/test_repositories.py -q --basetemp .pytest-tmp\p1-memory-worker-cleanup-focused
```

Expected: worker, context route, and repository tests pass.

- [ ] **Step 2: Update roadmap**

Update the P1 Memory / Context Management section to record that backend scheduled cleanup is implemented in this slice once local verification and 211 smoke pass. Keep configurable redaction policy listed as a remaining follow-up until implemented.

### Task 4: Review, Full Verification, Commit, And 211 Smoke

**Files:**
- Review all touched files.

- [ ] **Step 1: Request inherited-configuration review**

Dispatch or otherwise request independent review if the available tool inherits the main session permissions. If model and reasoning-effort fields are not exposed, record it as inherited-configuration review.

- [ ] **Step 2: Run pre-commit verification**

Run:

```powershell
python -m compileall -q app tools scripts
python -m pytest -q --basetemp .pytest-tmp\p1-memory-worker-cleanup-full
git diff --check
```

Expected: compile, full pytest, and diff check pass.

- [ ] **Step 3: Commit and push**

Commit the implementation and docs on `main`, then push to `origin/main`.

- [ ] **Step 4: Deploy to 211**

Sync the current source to `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform`, build or runtime-only rebase on 211 as appropriate, and recreate `ai-platform-api` and `ai-platform-worker` with the repo-local compose file. Do not print or copy real `.env` values.

- [ ] **Step 5: Smoke on 211**

Verify `/api/ai/health`, image labels, seeded expired memory soft-delete through the worker maintenance path, `worker.memory.retention.cleanup` audit evidence with no content/private payload, clean API/worker logs, and smoke data cleanup.
