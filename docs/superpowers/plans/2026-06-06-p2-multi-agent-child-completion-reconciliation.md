# P2 Multi-Agent Child Completion Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconcile terminal server-owned multi-agent child runs back onto the parent dispatch step.

**Architecture:** Add a repository helper that validates the persisted parent/child relationship before mutating parent state, then call it from worker terminal paths after the child run is completed, failed, or cancelled. Keep the feature internal, same-tenant, hidden-event/audit backed, and public-safe.

**Tech Stack:** FastAPI backend, async repository helpers, PostgreSQL JSONB, pytest, existing worker terminal flow.

---

## File Structure

- Modify `app/repositories.py`: add `_terminal_dispatch_state`, `_safe_child_result_output`, and `reconcile_multi_agent_child_run_terminal_state` near existing multi-agent dispatch helpers.
- Modify `app/worker.py`: add a small terminal reconciliation helper and call it after `complete_run`, `fail_run`, and `cancel_run`.
- Modify `tests/test_run_control_routes.py`: add repository-level RED tests near existing handoff tests.
- Modify `tests/test_worker.py`: add worker terminal hook RED tests near existing terminal success/failure/cancel tests.
- Modify `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`: after implementation and deployment, record the slice status and evidence.

## Task 1: Repository Reconciliation Contract

**Files:**
- Modify: `app/repositories.py`
- Test: `tests/test_run_control_routes.py`

- [x] **Step 1: Write repository RED tests**

Add tests that call `repositories.reconcile_multi_agent_child_run_terminal_state` directly with fake connections:

```python
@pytest.mark.asyncio
async def test_reconcile_multi_agent_child_success_updates_parent_step_event_and_audit(monkeypatch):
    ...

@pytest.mark.asyncio
async def test_reconcile_multi_agent_child_failure_does_not_copy_private_payload(monkeypatch):
    ...

@pytest.mark.asyncio
async def test_reconcile_multi_agent_child_ignores_forged_unmatched_relationship(monkeypatch):
    ...
```

Expected assertions:

- success returns `{"parent_run_id": "run-parent", "parent_step_id": "step-code", "child_run_id": "run-child", "status": "succeeded", "dispatch_state": "completed"}`;
- SQL update targets `tenant_id`, `parent_step_id`, `dispatch_id`, `dispatch_child_run_id`, and `dispatch_state = handed_off`;
- update payload includes `output`, `checkpoint_id = checkpoint_step-code`, `source_step_id = step-code`, `dispatch_child_status = succeeded`;
- failure payload includes `error_code` and a public error message, but excludes keys such as `executor_payload`, `private_payload`, `worker_path`, and `storage_key`;
- forged unmatched relationship returns `None` and performs no update, event, or audit write.

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_reconcile_multi_agent_child_success_updates_parent_step_event_and_audit tests/test_run_control_routes.py::test_reconcile_multi_agent_child_failure_does_not_copy_private_payload tests/test_run_control_routes.py::test_reconcile_multi_agent_child_ignores_forged_unmatched_relationship -q --basetemp .pytest-tmp\p2-child-reconcile-red-repo
```

Expected: tests fail because the repository helper does not exist.

- [x] **Step 2: Implement the repository helper**

Implementation shape:

```python
def _terminal_dispatch_state(child_status: str) -> tuple[str, str]:
    if child_status == "succeeded":
        return "succeeded", "completed"
    if child_status == "failed":
        return "failed", "failed"
    if child_status == "cancelled":
        return "cancelled", "cancelled"
    raise ValueError("unsupported_child_status")

async def reconcile_multi_agent_child_run_terminal_state(...):
    child = select child run for update
    if not child or not child.copied_from_run_id:
        return None
    dispatch = child.input_json.input.multi_agent_dispatch
    if dispatch.parent_run_id != child.copied_from_run_id:
        return None
    parent_step = select parent step for update by parent_step_id
    if parent step does not match dispatch id, child id, and handed_off state:
        return None
    update parent step status and payload_json
    append hidden parent event
    append audit log
    return reconciliation summary
```

- [x] **Step 3: Run repository GREEN tests**

Run the same focused command.

Expected: `3 passed`.

Actual:

```powershell
python -m pytest tests/test_run_control_routes.py::test_reconcile_multi_agent_child_success_updates_parent_step_event_and_audit tests/test_run_control_routes.py::test_reconcile_multi_agent_child_failure_does_not_copy_private_payload tests/test_run_control_routes.py::test_reconcile_multi_agent_child_ignores_forged_unmatched_relationship -q --basetemp .pytest-tmp\p2-child-reconcile-green-repo
```

Result: `3 passed`.

## Task 2: Worker Terminal Hook

**Files:**
- Modify: `app/worker.py`
- Test: `tests/test_worker.py`

- [x] **Step 1: Write worker RED tests**

Add tests for success, failure, cancel, ordinary-run, executor-exception, and
unknown-executor terminal paths:

```python
@pytest.mark.asyncio
async def test_worker_reconciles_multi_agent_child_after_success(monkeypatch):
    ...

@pytest.mark.asyncio
async def test_worker_reconciles_multi_agent_child_after_failure(monkeypatch):
    ...

@pytest.mark.asyncio
async def test_worker_reconciles_multi_agent_child_after_cancel(monkeypatch):
    ...

@pytest.mark.asyncio
async def test_worker_does_not_reconcile_ordinary_run(monkeypatch):
    ...

@pytest.mark.asyncio
async def test_worker_reconciles_multi_agent_child_after_executor_exception(monkeypatch):
    ...

@pytest.mark.asyncio
async def test_worker_reconciles_multi_agent_child_after_unknown_executor(monkeypatch):
    ...
```

Expected assertions:

- success calls `repositories.reconcile_multi_agent_child_run_terminal_state(... child_status="succeeded", result_json=...)` after `complete_run`;
- failure calls it with `child_status="failed"`, `error_code`, and `error_message` after `fail_run`;
- cancel calls it with `child_status="cancelled"` after `cancel_run`;
- executor exception and unknown executor failure paths call it after `fail_run`;
- ordinary run does not call the helper.

Run:

```powershell
python -m pytest tests/test_worker.py::test_worker_reconciles_multi_agent_child_after_success tests/test_worker.py::test_worker_reconciles_multi_agent_child_after_failure tests/test_worker.py::test_worker_reconciles_multi_agent_child_after_cancel tests/test_worker.py::test_worker_does_not_reconcile_ordinary_run -q --basetemp .pytest-tmp\p2-child-reconcile-red-worker
```

Expected: tests fail because the worker hook does not exist.

- [x] **Step 2: Implement worker hook**

Implementation shape:

```python
def _multi_agent_dispatch_from_payload(payload: QueueRunPayload) -> dict[str, Any] | None:
    dispatch = payload.input.get("multi_agent_dispatch")
    return dispatch if isinstance(dispatch, dict) else None

async def _reconcile_multi_agent_child_terminal_state(conn, *, payload, child_status, result_json=None, error_code=None, error_message=None):
    if not _multi_agent_dispatch_from_payload(payload):
        return None
    return await repositories.reconcile_multi_agent_child_run_terminal_state(...)
```

Call the helper inside the existing terminal transaction immediately after
`complete_run`, `_fail_run_and_reconcile`/`fail_run`, and `cancel_run`.

- [x] **Step 3: Run worker GREEN tests**

Run the same focused worker command.

Expected: worker focused tests pass.

Actual:

```powershell
python -m pytest tests/test_worker.py::test_worker_reconciles_multi_agent_child_after_success tests/test_worker.py::test_worker_reconciles_multi_agent_child_after_failure tests/test_worker.py::test_worker_reconciles_multi_agent_child_after_cancel tests/test_worker.py::test_worker_does_not_reconcile_ordinary_run tests/test_worker.py::test_worker_reconciles_multi_agent_child_after_executor_exception tests/test_worker.py::test_worker_reconciles_multi_agent_child_after_unknown_executor -q --basetemp .pytest-tmp\p2-child-reconcile-worker-review-green
```

Result: `6 passed`.

## Task 3: Integration Verification And Docs

**Files:**
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`
- Optional modify after review: implementation files from Tasks 1-2.

- [x] **Step 1: Run focused regression**

```powershell
python -m pytest tests/test_run_control_routes.py tests/test_repositories.py tests/test_worker.py -q --basetemp .pytest-tmp\p2-child-reconcile-focused
```

Expected: focused tests pass.

Actual:

```powershell
python -m pytest tests/test_run_control_routes.py tests/test_repositories.py tests/test_worker.py -q --basetemp .pytest-tmp\p2-child-reconcile-focused3
```

Result: `237 passed`.

- [x] **Step 2: Run compile check**

```powershell
python -m compileall -q app tools scripts
```

Expected: no output and exit code 0.

Actual: command completed with exit code 0 and no output.

- [x] **Step 3: Request inherited-configuration review**

Ask a review agent to inspect repository validation, worker terminal sequencing, redaction, idempotency, and whether the slice opens any new public capability. Because the available dispatch tool may not expose explicit `model` and `reasoning_effort`, record it as inherited-configuration review rather than claiming explicit model gate.

Actual: inherited-configuration review reported no Critical issues and two
Important contract issues: validate persisted child terminal status and sanitize
unsafe parent-step `error_code`.

- [x] **Step 4: Fix valid review findings and rerun focused tests**

If review finds valid issues, patch them and rerun the focused regression command from Step 1.

Actual: added repository regression tests for persisted child status mismatch,
unsafe `error_code` fallback, and stale update idempotency. Implemented DB
status validation and safe error-code fallback. Review regression command:

```powershell
python -m pytest tests/test_run_control_routes.py::test_reconcile_multi_agent_child_requires_persisted_terminal_child_status tests/test_run_control_routes.py::test_reconcile_multi_agent_child_sanitizes_unsafe_error_code tests/test_run_control_routes.py::test_reconcile_multi_agent_child_skips_event_and_audit_when_update_is_stale -q --basetemp .pytest-tmp\p2-child-reconcile-review-green
```

Result: `3 passed`.

- [x] **Step 5: Run broader pytest before PR**

```powershell
python -m pytest -q --basetemp .pytest-tmp\p2-child-reconcile-full
```

Expected: full suite passes or any pre-existing unrelated failure is documented with evidence.

Actual: initial relative basetemp run failed before assertions because pytest
does not create the intermediate `.pytest-tmp` parent directory for full-suite
`tmp_path` setup. Re-ran with an existing repository-external absolute basetemp:

```powershell
$base = Join-Path $env:TEMP 'ai-platform-pytest'; New-Item -ItemType Directory -Force -Path $base | Out-Null; python -m pytest -q --basetemp (Join-Path $base 'p2-child-reconcile-full')
```

Result: `961 passed, 6 skipped, 2 warnings`.

- [ ] **Step 6: Update roadmap evidence**

Add a `P2 Multi-Agent Child Completion Reconciliation` section with local test, review, PR, merge, and 211 smoke evidence after those gates actually complete.

- [ ] **Step 7: Commit, push, PR, merge, deploy to 211, smoke**

Use the normal feature branch and PR flow. Deploy on 211 only, verify health, OpenAPI/source label parity, and a live smoke that a terminal child run reconciles a parent step and writes hidden event/audit without leaking private payload.
