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

- [x] **Step 1: Request inherited-configuration review**

Dispatch or otherwise request independent review if the available tool inherits the main session permissions. If model and reasoning-effort fields are not exposed, record it as inherited-configuration review.

Result: inherited-configuration review reported no Critical worker behavior
issues. Important follow-ups were to ensure the new spec/plan docs are tracked
and to avoid claiming roadmap closure before 211 smoke. A Minor follow-up to
expose the new knobs in deployment templates was accepted, and a further local
compose check found the settings also had to be forwarded into the worker
service environment.

- [x] **Step 2: Run pre-commit verification**

Run:

```powershell
python -m compileall -q app tools scripts
python -m pytest -q --basetemp .pytest-tmp\p1-memory-worker-cleanup-full
git diff --check
```

Expected: compile, full pytest, and diff check pass.

Result:

- RED worker tests failed before implementation with the expected missing
  cleanup calls.
- Worker behavior tests passed with `4 passed`.
- `tests/test_worker_main.py` passed with `12 passed`.
- Focused memory suite passed with `154 passed`.
- After compose setting forwarding, launch/source-authority/worker focused
  tests passed with `33 passed`.
- `python -m compileall -q app tools scripts` exited 0.
- `git diff --check` exited 0, with only CRLF normalization warnings.
- Full pytest passed after the final compose fix with
  `996 passed, 6 skipped, 2 warnings`.

- [x] **Step 3: Commit and push**

Commit the implementation and docs on `main`, then push to `origin/main`.

Result:

- `fabfc19c1ce802e9058b0043ca4e7f2e8a85f089`
  (`feat: add memory retention worker cleanup`) added the worker tick,
  settings, tests, `.env.example` knobs, spec, and plan.
- `3fbab85ac9005279ccea26943366ca2dfb69b266`
  (`chore: expose memory cleanup worker settings`) forwarded the knobs into
  the worker compose environment and added a deployment-template regression
  test.
- Both commits were pushed to `origin/main`.

- [x] **Step 4: Deploy to 211**

Sync the current source to `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform`, build or runtime-only rebase on 211 as appropriate, and recreate `ai-platform-api` and `ai-platform-worker` with the repo-local compose file. Do not print or copy real `.env` values.

Result:

- Synced source revision:
  `3fbab85ac9005279ccea26943366ca2dfb69b266`.
- Remote source compile passed with `python3 -m compileall -q app tools scripts`.
- 211 runtime-only image built from the previous healthy image without
  dependency download:
  `ai-platform:3fbab85ac900`,
  `sha256:9e44eb075fdf8f3b226ff9510eea8fa7062d8e0c8eed59ac820bcc0a40cb9c18`.
- Labels:
  `ai-platform.source-revision=3fbab85ac9005279ccea26943366ca2dfb69b266`,
  `ai-platform.source_note=p1-memory-retention-worker-cleanup`.
- API and worker were recreated with the repo-local compose file at
  `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform/deploy/ai-platform/docker-compose.yml`.
- The existing 211 runtime env path was referenced by compose without printing
  or copying real env values.
- Worker container environment includes
  `MEMORY_RETENTION_WORKER_CLEANUP_ENABLED=true`,
  `MEMORY_RETENTION_WORKER_CLEANUP_INTERVAL_SECONDS=300`, and
  `MEMORY_RETENTION_WORKER_CLEANUP_LIMIT=200`.
- API health and frontend proxy health returned `{"status":"ok"}`.

- [x] **Step 5: Smoke on 211**

Verify `/api/ai/health`, image labels, seeded expired memory soft-delete through the worker maintenance path, `worker.memory.retention.cleanup` audit evidence with no content/private payload, clean API/worker logs, and smoke data cleanup.

Result:

- Seeded one expired `memory_records` row in the default tenant/workspace with
  unique smoke user, agent, and session ids.
- Ran the worker maintenance path in the live worker container.
- Cleanup returned `cleanup_rows = 1`.
- The memory record became `status = deleted` with `deleted_at` set.
- Audit action was `worker.memory.retention.cleanup`, `user_id = null`,
  `target_type = memory_retention`, and `target_id = default`.
- Audit payload keys were only `deleted_count`, `memory_record_ids`, `reason`,
  `source`, `target_user_ids`, and `workspace_id`.
- The smoke asserted no private marker, `private_payload`, or `api_key` leaked
  into the audit payload.
- Smoke cleanup left zero rows in `audit_logs`, `memory_records`, `sessions`,
  `agents`, and `users` for the smoke ids.
- Recent API and worker logs showed no `Traceback`, `ERROR`, `Exception`, or
  `permission denied` lines for the smoke window.
