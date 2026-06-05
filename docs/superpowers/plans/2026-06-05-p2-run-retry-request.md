# P2 Run Retry Request Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add owner-scoped `POST /api/ai/runs/{run_id}/retry` so a failed or dead-letter run can be copied into a new queued run with retry-specific events and audit.

**Architecture:** Reuse the existing copy-run queue, context snapshot, skill pinning, and resume-step seeding paths. Add route-level active-run admission, a repository-level retry status/idempotency gate, stale capability fail-closed handling, and retry event/audit recording, then call the shared route helper to enqueue the new run.

**Tech Stack:** FastAPI, Python, psycopg, pytest, existing `app.routes.runs`, `app.repositories`, and queue helpers.

---

## File Structure

- Modify `tests/test_run_control_routes.py`: add failing tests for retry route behavior, active-run admission, retry idempotency, stale capability handling, status gating, unauthorized source handling, seeded-step source metadata, and source/new event/audit recording.
- Modify `tests/test_repositories.py`: add row-lock regression coverage for `get_authorized_run(..., for_update=True)`.
- Modify `app/repositories.py`: add `retry_run_as_new_task` with source run row locking, status gate, active same-source retry check, source/new retry events, and `run.retry` audit.
- Modify `app/routes/runs.py`: extract shared copy/retry enqueue preparation, add `POST /runs/{run_id}/retry`, apply active-run admission, handle stale source capability as 404, and pass copy/retry source into seeded steps.
- Modify `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`: record the P2 retry request only after review and 211 smoke.

## Task 1: Retry Route Tests

- [x] **Step 1: Add the first failing route test**

Add near the existing copy-run route tests in `tests/test_run_control_routes.py`.
The success test must use a valid release decision payload and a primary
manifest pin shape, and must prove the retry route:

- calls active-run admission before repository retry creation;
- builds a queue payload with `source="retry_run"`;
- seeds retry-created steps with `payload_json.seeded_from == "retry_run"`;
- returns `RunControlResponse`.

```python
def test_retry_run_creates_queued_retry_from_failed_source(monkeypatch):
    calls = {"retry": [], "enqueue": [], "step": []}

    async def fake_retry_run_as_new_task(conn, *, tenant_id, user_id, run_id):
        calls["retry"].append((tenant_id, user_id, run_id))
        return {
            "session_id": "ses-old",
            "run_id": "run-retry",
            "agent_id": "general-agent",
            "skill_id": "general-chat",
            "workspace_id": "default",
            "file_ids": [],
            "input": {
                "message": "retry",
                "copied_from_run_id": run_id,
                "multi_agent_steps": [{"step_key": "retry-code", "role": "coding"}],
            },
            "executor_type": "claude-agent-worker",
            "skill_version": "hash-a",
            "release_policy_version": "",
            "release_decision": {
                "schema_version": "ai-platform.skill-release-decision.v1",
                "policy_active": False,
                "selected_version": "hash-a",
                "selected_track": "manifest_pin",
            },
        }

    async def fake_governed_skill_manifest_pins(conn, *, skill_id, input_payload, release_policy_version):
        assert skill_id == "general-chat"
        assert input_payload["copied_from_run_id"] == "run-failed"
        return [{"skill_id": skill_id, "content_hash": "hash-a"}]

    async def fake_record_initial_context_snapshot(conn, **kwargs):
        assert kwargs["source"] == "retry_run"
        return {"context_snapshot_id": "ctx-retry", "source": "retry_run"}

    async def fake_enqueue_run(payload):
        calls["enqueue"].append(payload)
        return 3

    async def fake_count_active_runs_for_user(conn, *, tenant_id, user_id):
        calls["active_limit"] = (tenant_id, user_id)
        return 0

    async def fake_upsert_run_step(conn, **kwargs):
        calls["step"].append(kwargs)
        return f"step-{kwargs['step_key']}"

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.count_active_runs_for_user", fake_count_active_runs_for_user)
    monkeypatch.setattr("app.routes.runs.repositories.retry_run_as_new_task", fake_retry_run_as_new_task, raising=False)
    monkeypatch.setattr("app.routes.runs._governed_skill_manifest_pins", fake_governed_skill_manifest_pins)
    monkeypatch.setattr("app.routes.runs.record_initial_context_snapshot", fake_record_initial_context_snapshot)
    monkeypatch.setattr("app.routes.runs.repositories.upsert_run_step", fake_upsert_run_step)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fake_enqueue_run)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-failed/retry", headers=headers())

    assert response.status_code == 200
    assert response.json()["run_id"] == "run-retry"
    assert response.json()["session_id"] == "ses-old"
    assert response.json()["status"] == "queued"
    assert response.json()["queue_position"] == 3
    assert calls["retry"] == [("default", "user-a", "run-failed")]
    assert calls["active_limit"] == ("default", "user-a")
    assert calls["enqueue"][0]["run_id"] == "run-retry"
    assert calls["enqueue"][0]["context_snapshot_id"] == "ctx-retry"
    assert calls["step"][0]["payload_json"]["seeded_from"] == "retry_run"
```

- [x] **Step 2: Verify RED**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_retry_run_creates_queued_retry_from_failed_source tests/test_run_control_routes.py::test_retry_run_rejects_when_user_active_run_limit_is_reached tests/test_run_control_routes.py::test_retry_run_returns_not_found_for_stale_source_capability tests/test_run_control_routes.py::test_retry_run_as_new_task_rejects_when_retry_is_already_active tests/test_run_control_routes.py::test_retry_run_as_new_task_records_retry_events_and_audit -q --basetemp .pytest-tmp\p2-run-retry-review-red
```

Observed: `5 failed`, proving active-run admission, stale source handling,
same-source retry idempotency, and seeded-step source metadata were missing.

- [x] **Step 3: Add repository status/audit tests**

Add async tests for `retry_run_as_new_task` covering:

- `status_not_retryable` before copy;
- source run authorization reads with `for_update=True`;
- `retry_already_active` before copy when a same-owner retry with
  `copied_from_run_id` is already `queued` or `running`;
- `retry_requested`, `run_retry_created`, and `run.retry` audit when retry
  succeeds.

- [x] **Step 4: Verify RED for repository tests**

Covered by the combined RED command above.

## Task 2: Repository Retry Gate

- [x] **Step 1: Add retryable status constants and function**

In `app/repositories.py`:

- keep `RETRYABLE_RUN_STATUSES = {"failed", "dead-letter", "dead_letter", "dead-lettered"}`;
- add optional `for_update=True` support to `get_authorized_run(...)` and use
  it in `retry_run_as_new_task(...)` so concurrent retry requests on the same
  source run serialize inside the transaction;
- add `get_active_retry_for_source_run(...)`;
- add `retry_run_as_new_task(...)` that checks source authorization, retryable
  status, same-source active retry idempotency, then copies and records
  retry-specific events/audit.

- [x] **Step 2: Run repository tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_retry_run_as_new_task_rejects_non_retryable_status tests/test_run_control_routes.py::test_retry_run_as_new_task_records_retry_events_and_audit -q --basetemp .pytest-tmp\p2-run-retry-repository
```

Expected: both pass.

## Task 3: Route Integration

- [x] **Step 1: Extract shared copy/retry preparation helper**

In `app/routes/runs.py`, extract the body of `copy_run` after repository copy
into:

```python
async def prepare_copied_run_for_queue(
    conn,
    *,
    copied: dict[str, Any],
    principal: AuthPrincipal,
    source: str,
) -> dict[str, Any]:
    ...
```

The helper must keep all existing copy-run behavior and use `source` for
`record_initial_context_snapshot`.

- [x] **Step 2: Update `copy_run` to call the helper with `source="copy_run"`**

Existing copy-run tests must keep passing.

- [x] **Step 3: Add `retry_run` route**

Add before `copy/plan` routes. The route must run
`enforce_user_active_run_limit(...)` in the same transaction before
`retry_run_as_new_task(...)`, catch `RepositoryNotFoundError` as 404, catch
`RepositoryConflictError` as 409, and call
`prepare_copied_run_for_queue(..., source="retry_run")`.

- [x] **Step 4: Run route and existing copy tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_retry_run_creates_queued_retry_from_failed_source tests/test_run_control_routes.py::test_copy_run_as_new_task_returns_full_execution_input_for_queue tests/test_run_control_routes.py::test_copy_run_as_new_task_adds_completed_step_outputs_to_resume -q --basetemp .pytest-tmp\p2-run-retry-route
```

Observed after reviewer fixes:

```powershell
python -m pytest tests/test_run_control_routes.py -q --basetemp .pytest-tmp\p2-run-retry-routes-after-review
```

Result: `57 passed`.

Follow-up review found that the serial active-retry check still needed
transactional concurrency protection. Added source-row `FOR UPDATE` coverage:

```powershell
python -m pytest tests/test_repositories.py::test_get_authorized_run_can_lock_row_for_retry_race_window tests/test_run_control_routes.py::test_retry_run_as_new_task_rejects_non_retryable_status tests/test_run_control_routes.py::test_retry_run_as_new_task_rejects_when_retry_is_already_active tests/test_run_control_routes.py::test_retry_run_as_new_task_records_retry_events_and_audit -q --basetemp .pytest-tmp\p2-run-retry-lock-green
```

Result: `4 passed`.

## Task 4: Verification, Review, and Deployment

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p2-run-retry-focused
```

- [ ] **Step 2: Run compile and full local verification**

Run:

```powershell
python -m compileall -q app tools scripts
python -m pytest -q --basetemp .pytest-tmp\p2-run-retry-full
```

- [x] **Step 3: Request inherited-configuration review**

Review focus:

```text
P2 Run Retry Request: check owner scoping, retryable status gate, queue payload
integrity, copied context/snapshot behavior, event/audit correctness, and no
new scheduler/subagent/sandbox/tool behavior.
```

Review result: inherited-configuration subagent review found no Critical
issues and two Important issues: active-run/idempotency gap and stale source
capability fail-closed gap. Both were fixed with regression tests. Minor
seeded-step source metadata was also fixed.

Follow-up inherited-configuration review found one Important issue: concurrent
same-source retry idempotency needed a transactional lock, not only a serial
pre-check. Fixed by locking the authorized source run row with `FOR UPDATE`
before active-retry lookup and copy.

- [ ] **Step 4: Update roadmap only after review and 211 smoke**

Record commit, local tests, review, 211 image label, endpoint smoke, and the
fact that automatic retry policy scheduling is still not opened.

- [ ] **Step 5: Deploy to 211 and smoke**

Use 211 Docker-capable host only. Smoke must verify health, OpenAPI route,
owner retry success, active-run 409, other-user 404, retry events/audit, queue
payload, image label, and smoke data cleanup.
