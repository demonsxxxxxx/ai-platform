# P2 Run Control Readiness Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `ai-platform.run-control-readiness.v1` projection that tells authorized users whether cancel, resume/copy, and future retry controls are currently available for a run.

**Architecture:** Reuse existing run authorization, run summary, run step projection, queue insight, and copy/cancel control routes. Add only projection helpers and a `GET /runs/{run_id}/control/readiness` route; do not change worker scheduling, retry policy, sandbox behavior, or copy/cancel execution.

**Tech Stack:** FastAPI, Python, pytest, existing `app.routes.runs` helpers, existing repository read methods.

---

## File Structure

- Modify `app/routes/runs.py`: add `RUN_CONTROL_READINESS_CONTRACT_VERSION`, readiness helper functions, and `GET /runs/{run_id}/control/readiness`.
- Modify `tests/test_run_control_routes.py`: add focused route tests for resume/cancel/retry readiness, redaction, queue insight, and 404 behavior.
- Modify `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`: after implementation and smoke, record the P2 readiness slice status.

## Task 1: Failing Readiness Contract Tests

**Files:**
- Modify: `tests/test_run_control_routes.py`

- [ ] **Step 1: Add focused tests**

Add tests after `test_copy_run_plan_previews_reused_and_rerun_steps`:

```python
def readiness_run_row(*, status="failed", cancel_requested_at=None):
    return {
        "id": "run-ready",
        "session_id": "ses-ready",
        "workspace_id": "default",
        "agent_id": "qa-word-review",
        "skill_id": "qa-file-reviewer",
        "schema_version": "ai-platform.run.v1",
        "executor_schema_version": "ai-platform.executor-result.v1",
        "status": status,
        "trace_id": "trace-ready",
        "input_json": {"message": "review", "skill_id": "qa-file-reviewer"},
        "result_json": {},
        "error_code": None,
        "error_message": None,
        "cancel_requested_at": cancel_requested_at,
        "cancel_requested_by": None,
    }
```

Add a failed-run happy-path test:

```python
def test_run_control_readiness_enables_resume_from_checkpoint_outputs(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("default", "user-a", "run-ready")
        return readiness_run_row(status="failed")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Code",
                "role": "coding",
                "sequence": 1,
                "payload_json": {
                    "output": "raw reusable output must not leak",
                    "skill_ids": ["qa-file-reviewer"],
                    "resource_limits": {"max_seconds": 60},
                    "sandbox_mode": "ephemeral",
                    "work_dir": "/tmp/runtime",
                    "private_payload": {"token": "secret-token"},
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-test",
                "run_id": run_id,
                "step_key": "test",
                "step_kind": "agent",
                "status": "failed",
                "title": "Test",
                "role": "verifier",
                "sequence": 2,
                "payload_json": {"error": "tests failed"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    async def fake_queue_insight(status, tenant_id):
        raise AssertionError("queue insight should only be loaded for queued runs")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.queue_insight_for_status", fake_queue_insight)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-ready/control/readiness", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "ai-platform.run-control-readiness.v1"
    assert body["run"]["run_id"] == "run-ready"
    assert body["run"]["skill_id"] is None
    assert body["actions"]["cancel"] == {
        "enabled": False,
        "reason": "terminal_run",
        "method": "POST",
        "href": "/api/ai/runs/run-ready/cancel",
    }
    assert body["actions"]["resume"] == {
        "enabled": True,
        "reason": "checkpoint_outputs_available",
        "method": "POST",
        "href": "/api/ai/runs/run-ready/copy",
    }
    assert body["actions"]["retry"] == {
        "enabled": False,
        "reason": "retry_runtime_not_enabled",
        "method": None,
        "href": None,
    }
    assert body["checkpoint_candidates"] == [
        {
            "step_id": "step-code",
            "step_key": "code",
            "status": "succeeded",
            "title": "Code",
            "role": "coding",
            "sequence": 1,
            "reusable": True,
            "reason": "output_available",
        }
    ]
    assert body["queue_insight"] is None
    public_dump = str(body)
    assert "raw reusable output" not in public_dump
    assert "qa-file-reviewer" not in public_dump
    assert "resource_limits" not in public_dump
    assert "sandbox_mode" not in public_dump
    assert "/tmp/" not in public_dump
    assert "private_payload" not in public_dump
    assert "secret-token" not in public_dump
```

Add a queued-run test:

```python
def test_run_control_readiness_enables_cancel_and_includes_queue_insight(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        return readiness_run_row(status="queued")

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return []

    async def fake_queue_insight(status, tenant_id):
        assert (status, tenant_id) == ("queued", "default")
        return {"tenant_id": tenant_id, "queued": 2, "running": 1}

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.queue_insight_for_status", fake_queue_insight)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-ready/control/readiness", headers=headers())

    assert response.status_code == 200
    body = response.json()
    assert body["actions"]["cancel"]["enabled"] is True
    assert body["actions"]["cancel"]["reason"] == "cancel_available"
    assert body["actions"]["resume"]["enabled"] is False
    assert body["actions"]["resume"]["reason"] == "active_run"
    assert body["actions"]["retry"]["reason"] == "status_not_retryable"
    assert body["queue_insight"] == {"tenant_id": "default", "queued": 2, "running": 1}
```

Add a missing-run test:

```python
def test_run_control_readiness_returns_not_found_without_loading_steps(monkeypatch):
    async def fake_get_authorized_run(conn, *, tenant_id, user_id, run_id):
        assert (tenant_id, user_id, run_id) == ("default", "user-a", "missing-run")
        return None

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        raise AssertionError("steps must not be listed for missing run")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_authorized_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/missing-run/control/readiness", headers=headers())

    assert response.status_code == 404
    assert response.json() == {"detail": "run_not_found"}
```

- [ ] **Step 2: Run red tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_run_control_readiness_enables_resume_from_checkpoint_outputs tests/test_run_control_routes.py::test_run_control_readiness_enables_cancel_and_includes_queue_insight tests/test_run_control_routes.py::test_run_control_readiness_returns_not_found_without_loading_steps -q --basetemp .pytest-tmp\p2-run-control-readiness-red
```

Expected: all fail with `404 Not Found` because the route does not exist.

## Task 2: Readiness Projection Route

**Files:**
- Modify: `app/routes/runs.py`
- Test: `tests/test_run_control_routes.py`

- [ ] **Step 1: Add constants and helper functions**

In `app/routes/runs.py`, add:

```python
RUN_CONTROL_READINESS_CONTRACT_VERSION = "ai-platform.run-control-readiness.v1"
RUN_CONTROL_ACTIVE_STATUSES = {"queued", "running"}
RUN_CONTROL_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
RUN_CONTROL_RETRY_PREVIEW_STATUSES = {"failed", "dead-letter", "dead_letter", "dead-lettered"}
```

Add helper functions near the other run projection helpers:

```python
def _control_action(*, enabled: bool, reason: str, method: str | None, href: str | None) -> dict[str, object]:
    return {"enabled": enabled, "reason": reason, "method": method, "href": href}


def _checkpoint_candidate_from_step(row: dict[str, object], principal: AuthPrincipal) -> dict[str, object] | None:
    payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    status = normalize_step_status(row.get("status"))
    if status != "succeeded" or payload.get("output") is None:
        return None
    public_step = run_step_response(row, principal=principal)
    return {
        "step_id": str(public_step["step_id"]),
        "step_key": str(public_step["step_key"]),
        "status": str(public_step["status"]),
        "title": public_step.get("title"),
        "role": public_step.get("role"),
        "sequence": int(public_step.get("sequence") or 0),
        "reusable": True,
        "reason": "output_available",
    }


def run_control_readiness_snapshot(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    principal: AuthPrincipal,
    queue_insight: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return read-only readiness for platform-controlled run actions."""
    run_id = str(run["id"])
    status = normalize_run_status(str(run["status"]))
    checkpoint_candidates = [
        item for item in (_checkpoint_candidate_from_step(row, principal) for row in steps) if item is not None
    ]
    cancel_requested = bool(run.get("cancel_requested_at"))
    if cancel_requested:
        cancel_reason = "cancel_already_requested"
    elif status in RUN_CONTROL_ACTIVE_STATUSES:
        cancel_reason = "cancel_available"
    elif status in RUN_CONTROL_TERMINAL_STATUSES:
        cancel_reason = "terminal_run"
    else:
        cancel_reason = "status_not_cancellable"
    cancel_enabled = cancel_reason == "cancel_available"

    if status in RUN_CONTROL_ACTIVE_STATUSES:
        resume_reason = "active_run"
    elif checkpoint_candidates:
        resume_reason = "checkpoint_outputs_available"
    else:
        resume_reason = "no_checkpoint_outputs"
    resume_enabled = resume_reason == "checkpoint_outputs_available"

    retry_reason = "retry_runtime_not_enabled" if status in RUN_CONTROL_RETRY_PREVIEW_STATUSES else "status_not_retryable"
    return {
        "contract_version": RUN_CONTROL_READINESS_CONTRACT_VERSION,
        "run": run_playback_summary(run, principal),
        "actions": {
            "cancel": _control_action(
                enabled=cancel_enabled,
                reason=cancel_reason,
                method="POST",
                href=f"/api/ai/runs/{run_id}/cancel",
            ),
            "resume": _control_action(
                enabled=resume_enabled,
                reason=resume_reason,
                method="POST",
                href=f"/api/ai/runs/{run_id}/copy",
            ),
            "retry": _control_action(
                enabled=False,
                reason=retry_reason,
                method=None,
                href=None,
            ),
        },
        "checkpoint_candidates": checkpoint_candidates,
        "queue_insight": queue_insight,
    }
```

- [ ] **Step 2: Add the route**

Add this route before `/runs/{run_id}/cancel`:

```python
@router.get("/runs/{run_id}/control/readiness")
async def get_run_control_readiness(
    run_id: str,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    """Return read-only readiness for platform-controlled run actions."""
    async with transaction() as conn:
        run = await repositories.get_authorized_run(
            conn,
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            run_id=run_id,
        )
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        steps = await repositories.list_run_steps(conn, tenant_id=principal.tenant_id, run_id=run_id)
    queue_insight = await queue_insight_for_status(str(run["status"]), principal.tenant_id)
    return run_control_readiness_snapshot(
        run=run,
        steps=steps,
        principal=principal,
        queue_insight=queue_insight,
    )
```

- [ ] **Step 3: Run green tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_run_control_readiness_enables_resume_from_checkpoint_outputs tests/test_run_control_routes.py::test_run_control_readiness_enables_cancel_and_includes_queue_insight tests/test_run_control_routes.py::test_run_control_readiness_returns_not_found_without_loading_steps -q --basetemp .pytest-tmp\p2-run-control-readiness-green
```

Expected: `3 passed`.

## Task 3: Focused Verification and Roadmap Sync

**Files:**
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`
- Test: `tests/test_run_control_routes.py`, `tests/test_source_authority_docs.py`

- [ ] **Step 1: Run route tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py -q --basetemp .pytest-tmp\p2-run-control-readiness-routes
```

Expected: all route tests pass.

- [ ] **Step 2: Add roadmap status paragraph**

After the P2 Run Provenance Snapshot paragraph, add:

```markdown
### P2 Run Control Readiness Snapshot

Status: started as a read-only P2 foundation slice. This adds the
`ai-platform.run-control-readiness.v1` owner-scoped projection for existing
run cancel, copy/resume, checkpoint reuse, and future retry readiness. It does
not start retry scheduling, autonomous multi-agent dispatch, high-risk tool
execution, or new sandbox behavior.
```

- [ ] **Step 3: Verify docs**

Run:

```powershell
python -m pytest tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p2-run-control-readiness-docs
```

Expected: `8 passed`.

## Final Verification

- [ ] Run compile:

```powershell
python -m compileall -q app tools scripts
```

- [ ] Run focused tests:

```powershell
python -m pytest tests/test_run_control_routes.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p2-run-control-readiness-focused
```

- [ ] Run full suite:

```powershell
python -m pytest -q --basetemp .pytest-tmp\p2-run-control-readiness-full
```

- [ ] Request inherited-configuration multi-agent code review.
- [ ] Fix any Critical or Important review findings and rerun focused/full tests.
- [ ] Commit, push, merge main according to the current repo workflow, deploy to 211, and smoke:

```bash
curl -sS -o /tmp/ai-health.json -w '%{http_code}' http://127.0.0.1:8020/api/ai/health
curl -sS http://127.0.0.1:8020/openapi.json | python3 -c 'import sys,json; data=json.load(sys.stdin); print("/api/ai/runs/{run_id}/control/readiness" in data.get("paths", {}))'
```

Expected: health `200`, OpenAPI route present, seeded or existing same-tenant run returns `ai-platform.run-control-readiness.v1`, and ordinary-user response contains no forbidden private/runtime markers.
