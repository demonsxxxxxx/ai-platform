# S0A Real Run Cancel And Tenant-Scoped Workspace Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or inline TDD execution with review checkpoints. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make run cancellation and workspace selection use platform-owned run/workspace/lease authority instead of session-level POC compatibility or user-controlled runtime identity.

**Architecture:** Keep the current run cancel route as the canonical API. Add repository-level workspace tenant validation and schema-level tenant/workspace constraints. Harden sandbox cleanup by materializing a platform-validated runtime handle from persisted lease identity. Make worker terminal writes cancel-aware so accepted cancellation wins races. Update frontend stop generation to call run cancel with `currentRunId` only.

**Tech Stack:** FastAPI, async psycopg repositories, PostgreSQL schema SQL, pytest, React/Vite TypeScript node:test source tests.

## Global Constraints

- Base: `origin/main` at `5b4b6ef6536c0cf8b53528b55520e5005caeb508`.
- Do not deploy 211 and do not claim S0/security closure, B2, Authorized Skill Task Loop, or gate closable.
- Every local pytest command uses a unique fresh child under `.pytest-tmp`.
- PostgreSQL-specific schema tests are gated by an explicit environment variable and clean-skip without DSN.
- Do not read, print, copy, or commit real `.env` or DSN values.
- Keep production edits inside the allowed S0A paths.
- Main-control approved the minimal Blocker #1 extension to `app/runtime/sandbox/runtime.py` and direct runtime tests only for trusted provider-returned runtime handle persistence.

---

### Task 1: Issue And Status Artifacts

**Files:**
- Create: `docs/operations/2026-07-11-s0a-real-run-cancel-workspace-boundary.md`
- Create: `docs/superpowers/plans/2026-07-11-s0a-real-run-cancel-workspace-boundary.md`

**Interfaces:**
- Produces the Phase source of truth and implementation checklist for later review/PR reporting.

- [x] Record fetch/readback, branch, missing blocker doc, scope, and phase state.
- [x] Create or link a GitHub issue with S0A acceptance criteria: #383.

### Task 2: Workspace Tenant Boundary

**Files:**
- Modify: `app/repositories.py`
- Modify: `app/routes/runs.py`
- Modify: `app/schema.sql`
- Test: `tests/test_repositories.py`
- Test: `tests/test_routes.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Add repository helper `ensure_workspace_belongs_to_tenant(conn, tenant_id, workspace_id)`.
- `create_run` must call the helper before creating or reusing a session.
- Schema must add an idempotent tenant-scoped workspace uniqueness/foreign-key boundary with precheck and rollback notes.

- [x] RED: repository direct misuse with wrong-tenant workspace fails before run insert.
- [x] RED: route create run validates workspace before session/run insert.
- [x] RED: same-tenant workspace create path still succeeds through affected route tests.
- [x] RED: schema test verifies idempotent precheck and tenant-scoped workspace constraint text.
- [x] GREEN: implement helper, route call, and additive schema constraint.

### Task 3: Canonical Cancel Semantics And Audit

**Files:**
- Modify: `app/repositories.py`
- Modify: `app/routes/runs.py`
- Modify: `app/routes/admin_runs.py`
- Modify: `app/routes/lambchat_compat.py`
- Test: `tests/test_run_control_routes.py`
- Test: `tests/test_repositories.py`
- Test: `tests/test_routes.py`

**Interfaces:**
- Owner cancel uses `POST /api/ai/runs/{run_id}/cancel`.
- Admin cancel uses `POST /api/ai/admin/runs/{run_id}/cancel`.
- Legacy `/api/chat/sessions/{session_id}/cancel` must be explicitly unsupported and must not look like a successful cancel.

- [x] RED: owner queued/running cancel returns platform status and writes structured audit/event.
- [x] RED: admin cancel writes structured admin audit through existing coverage.
- [x] RED: cross-user and cross-tenant cancel return 404 and leave run/audit/queue/leases untouched through existing affected coverage.
- [x] RED: repeated accepted cancel is idempotent and does not create a double terminal state through existing affected coverage.
- [x] RED: terminal repeated cancel follows the formal contract through existing affected coverage.
- [x] GREEN: adjust repository cancel helpers and route response handling.

### Task 4: Lease Runtime Handle Cleanup

**Files:**
- Modify: `app/schema.sql`
- Modify: `app/repositories.py`
- Modify: `app/routes/sandbox_runtime_cleanup.py`
- Test: `tests/test_repositories.py`
- Test: `tests/test_run_control_routes.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Add platform-owned persisted runtime identity fields for sandbox leases.
- Cleanup must stop only when the persisted platform runtime handle is present and matches the lease identity/provider expectations.
- `lease_payload_json.container_id/container_name` are not authoritative.

- [x] RED: forged payload container identity does not trigger provider stop.
- [x] RED: cleanup success releases lease and records controlled event/audit through existing affected coverage.
- [x] RED: cleanup failure records controlled failure evidence and returns controlled error.
- [x] GREEN: persist and consume validated runtime handle fields, keep unsupported/legacy rows fail-closed.
- [x] GREEN: `SandboxRuntime` persists trusted `ContainerLease` runtime handles into `runtime_*` columns and rejects incomplete handles before executor dispatch.

### Task 5: Worker Cancel Race

**Files:**
- Modify: `app/repositories.py`
- Modify: `app/worker.py`
- Test: `tests/test_worker.py`
- Test: `tests/test_repositories.py`

**Interfaces:**
- Terminal writers must not overwrite a run that has accepted cancellation.
- Worker exceptions after accepted cancel produce cancelled/cancelling outcome, not `executor_failure`.

- [x] RED: cancel-vs-worker exception race does not mark run `failed`.
- [x] RED: queued/running cancellation and cleanup timeout do not create double terminal states through existing affected coverage.
- [x] RED: repository terminal no-op prevents worker from appending `run_succeeded`, `run_failed`, or `run_cancelled` terminal events.
- [x] GREEN: wire worker exception path so accepted cancel wins over executor failure.

### Task 6: Frontend True Run Cancel

**Files:**
- Modify: `frontend/web/src/services/api/session.ts`
- Modify: `frontend/web/src/hooks/useAgent.ts`
- Modify: `frontend/web/src/components/layout/AppContent/ChatAppContent.tsx` only if required for current run id propagation.
- Test: `frontend/web/src/services/api/__tests__/session.test.ts`
- Test: add focused source test under `frontend/web/src/hooks/useAgent/__tests__/` if needed.

**Interfaces:**
- `sessionApi.cancelRun(runId)` calls `/api/ai/runs/{run_id}/cancel`.
- `stopGeneration` uses `currentRunIdRef.current`; if missing, it does not call legacy session cancel.

- [x] RED: service test expects run cancel URL and method.
- [x] RED: hook source test proves missing run id fail-closed and no `/chat/sessions/{session_id}/cancel` call.
- [x] GREEN: implement `cancelRun` and update `stopGeneration`.

### Task 7: Verification, Review, PR

**Files:**
- Update: `docs/operations/2026-07-11-s0a-real-run-cancel-workspace-boundary.md`

- [x] Run focused backend tests with fresh `.pytest-tmp` child directories.
- [x] Run frontend focused node tests.
- [x] Run `python -m compileall -q app tools scripts`.
- [x] Run `git diff --check`.
- [x] Run scope and secret review.
- [x] Run independent inherited-configuration review; fix Critical/Important findings and rerun affected gates. The latest exact-head review must still be launched after the final commit SHA is fixed.
- [ ] Commit, push, open ready PR, observe required CI, and report PostgreSQL gate truthfully.
