# P2 Multi-Agent Dispatch Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin-only dispatch claim ledger for safe ready multi-agent steps without starting real autonomous scheduling.

**Architecture:** Reuse the existing run-control readiness projection for validation, then persist a claim through `run_steps`, a hidden standard event, and an audit log. The route remains admin-only and same-tenant; ordinary users only see public-safe state and never receive an admin dispatch href.

**Tech Stack:** FastAPI, Pydantic v2, async PostgreSQL repository helpers, pytest, existing ai-platform run/event/audit contracts.

**Status Note:** This file is the implementation checklist for the slice. Final
verification, PR, and 211 deployment evidence are recorded in the foundation
roadmap after merge and smoke.

---

## File Structure

- Modify `app/models.py`: add request/response models for dispatch claim.
- Modify `app/routes/runs.py`: add contract version, readiness gate updates, claim validation helper, and POST route.
- Modify `app/repositories.py`: add a repository helper that upserts the claimed step, appends the hidden event, appends audit, and returns ledger ids plus the latest step row.
- Modify `tests/test_run_control_routes.py`: add route/readiness tests and update the existing readiness gate expectation.
- Modify `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`: add final deployment evidence after verification.

## Task 1: RED Tests For Readiness Gate And Admin Claim Route

**Files:**
- Modify: `tests/test_run_control_routes.py`

- [ ] **Step 1: Add a test proving admin readiness exposes a claimable dispatch gate**

Add a test near the existing multi-agent readiness tests:

```python
def test_run_control_readiness_enables_admin_multi_agent_dispatch_gate(monkeypatch):
    async def fake_get_run(conn, *, tenant_id, user_id, run_id):
        row = readiness_run_row(status="running")
        row["input_json"] = {
            "input": {
                "message": "build feature",
                "execution_mode": "multi_agent",
                "multi_agent_steps": [
                    {"step_key": "plan", "title": "Plan"},
                    {"step_key": "code", "title": "Code", "depends_on": ["plan"]},
                ],
            }
        }
        return row

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-plan",
                "run_id": run_id,
                "step_key": "plan",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Plan",
                "role": "planner",
                "sequence": 1,
                "payload_json": {"output": "plan output"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "pending",
                "title": "Code",
                "role": "coder",
                "sequence": 2,
                "payload_json": {"depends_on": ["plan"]},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_authorized_run", fake_get_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    client = TestClient(create_app())

    response = client.get("/api/ai/runs/run-ready/control/readiness", headers=admin_headers())

    assert response.status_code == 200
    gate = response.json()["multi_agent"]["gates"]["dispatch"]
    assert gate == {
        "enabled": True,
        "reason": "ready_steps_available",
        "method": "POST",
        "href": "/api/ai/runs/run-ready/multi-agent/dispatch/claims",
    }
```

- [ ] **Step 2: Add a test proving ordinary readiness does not expose the admin href**

Update the existing `test_run_control_readiness_projects_multi_agent_dependency_gates` expectation:

```python
assert body["multi_agent"]["gates"]["dispatch"] == {
    "enabled": False,
    "reason": "admin_only_dispatch",
    "method": None,
    "href": None,
}
```

- [ ] **Step 3: Add a test proving admin claim records step, event, and audit**

Add a route test:

```python
def test_admin_multi_agent_dispatch_claim_records_ledger_event_and_audit(monkeypatch):
    calls = []

    async def fake_get_run(conn, *, tenant_id, run_id, for_update=False):
        assert tenant_id == "default"
        assert run_id == "run-ready"
        assert for_update is True
        row = readiness_run_row(status="running")
        row["trace_id"] = "trace-ready"
        row["input_json"] = {
            "input": {
                "execution_mode": "multi_agent",
                "multi_agent_steps": [
                    {"step_key": "plan", "title": "Plan"},
                    {"step_key": "code", "title": "Code", "role": "coder", "depends_on": ["plan"]},
                ],
            }
        }
        return row

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        if any(item[0] == "claim" for item in calls):
            return [
                {
                    "id": "step-code",
                    "run_id": run_id,
                    "step_key": "code",
                    "step_kind": "agent",
                    "status": "running",
                    "title": "Code",
                    "role": "coder",
                    "sequence": 2,
                    "payload_json": {
                        "depends_on": ["plan"],
                        "dispatch_state": "claimed",
                        "dispatch_kind": "subagent",
                    },
                    "started_at": None,
                    "finished_at": None,
                    "created_at": None,
                    "updated_at": None,
                }
            ]
        return [
            {
                "id": "step-plan",
                "run_id": run_id,
                "step_key": "plan",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Plan",
                "role": "planner",
                "sequence": 1,
                "payload_json": {"output": "plan output"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "pending",
                "title": "Code",
                "role": "coder",
                "sequence": 2,
                "payload_json": {"depends_on": ["plan"]},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        ]

    async def fake_claim(conn, **kwargs):
        calls.append(("claim", kwargs))
        return {
            "dispatch_id": "dispatch-code",
            "event_id": "evt-code",
            "audit_id": "aud-code",
            "step": {
                "id": "step-code",
                "run_id": "run-ready",
                "step_key": "code",
                "step_kind": "agent",
                "status": "running",
                "title": "Code",
                "role": "coder",
                "sequence": 2,
                "payload_json": {
                    "depends_on": ["plan"],
                    "dispatch_state": "claimed",
                    "dispatch_kind": "subagent",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            },
        }

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_run", fake_get_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.claim_multi_agent_dispatch_step", fake_claim, raising=False)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-ready/multi-agent/dispatch/claims",
        json={"step_key": "code"},
        headers=admin_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["contract_version"] == "ai-platform.multi-agent-dispatch-claim.v1"
    assert body["status"] == "claimed"
    assert body["dispatch_id"] == "dispatch-code"
    assert body["event_id"] == "evt-code"
    assert body["audit_id"] == "aud-code"
    assert body["step"]["status"] == "running"
    assert body["step"]["payload"]["dispatch_state"] == "claimed"
    assert calls == [
        (
            "claim",
            {
                "tenant_id": "default",
                "run_id": "run-ready",
                "claimed_by": "admin-a",
                "trace_id": "trace-ready",
                "step_key": "code",
                "step_kind": "agent",
                "title": "Code",
                "role": "coder",
                "sequence": 2,
                "depends_on": ["plan"],
            },
        )
    ]
```

- [ ] **Step 4: Add a test proving unsafe dependencies are rejected without writes**

Add:

```python
def test_admin_multi_agent_dispatch_claim_rejects_unsafe_dependency_without_writes(monkeypatch):
    async def fake_get_run(conn, *, tenant_id, run_id, for_update=False):
        row = readiness_run_row(status="running")
        row["input_json"] = {
            "input": {
                "execution_mode": "multi_agent",
                "multi_agent_steps": [
                    {"step_key": "safe", "title": "Safe"},
                    {"step_key": "blocked", "title": "Blocked", "depends_on": ["qa-file-reviewer"]},
                ],
            }
        }
        return row

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        return [
            {
                "id": "step-safe",
                "run_id": run_id,
                "step_key": "safe",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Safe",
                "role": "worker",
                "sequence": 1,
                "payload_json": {"output": "safe output"},
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def fail_claim(*args, **kwargs):
        raise AssertionError("unsafe dependency must not be claimed")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.get_run", fake_get_run)
    monkeypatch.setattr("app.routes.runs.repositories.list_run_steps", fake_list_run_steps)
    monkeypatch.setattr("app.routes.runs.repositories.claim_multi_agent_dispatch_step", fail_claim, raising=False)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-ready/multi-agent/dispatch/claims",
        json={"step_key": "blocked"},
        headers=admin_headers(),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "unsafe_step_reference"
```

- [ ] **Step 5: Add a test proving non-admin callers cannot claim**

Add:

```python
def test_multi_agent_dispatch_claim_requires_admin(monkeypatch):
    async def fail_get_run(*args, **kwargs):
        raise AssertionError("non-admin claim must fail before loading the run")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.repositories.get_run", fail_get_run, raising=False)
    client = TestClient(create_app())

    response = client.post(
        "/api/ai/runs/run-ready/multi-agent/dispatch/claims",
        json={"step_key": "code"},
        headers=headers(),
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "admin_required"
```

- [ ] **Step 6: Run RED focused tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py -q --basetemp .pytest-tmp\p2-dispatch-red
```

Expected: at least the new tests fail because the dispatch claim model, route,
repository helper, and new readiness gate behavior do not exist yet.

## Task 2: Models And Readiness Gate Implementation

**Files:**
- Modify: `app/models.py`
- Modify: `app/routes/runs.py`

- [ ] **Step 1: Add dispatch claim request and response models**

In `app/models.py`, add:

```python
class MultiAgentDispatchClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_key: str

    @field_validator("step_key")
    @classmethod
    def validate_step_key(cls, value: str):
        return assert_safe_id(value, "step_key")


class MultiAgentDispatchClaimResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str
    run_id: str
    step_key: str
    step_id: str
    status: Literal["claimed"]
    dispatch_id: str
    event_id: str
    audit_id: str
    step: dict[str, Any]
```

- [ ] **Step 2: Import the new models in `app/routes/runs.py`**

Change the model import to include:

```python
MultiAgentDispatchClaimRequest,
MultiAgentDispatchClaimResponse,
```

- [ ] **Step 3: Add a contract version constant**

Near existing run contract constants, add:

```python
MULTI_AGENT_DISPATCH_CLAIM_CONTRACT_VERSION = "ai-platform.multi-agent-dispatch-claim.v1"
```

- [ ] **Step 4: Update `multi_agent_readiness_snapshot` dispatch gate**

Replace the static dispatch gate with logic:

```python
ready_count = sum(1 for item in readiness_steps if item["ready"])
if ready_count <= 0:
    dispatch_gate = _control_action(enabled=False, reason="no_ready_steps", method=None, href=None)
elif is_ai_admin(principal):
    dispatch_gate = _control_action(
        enabled=True,
        reason="ready_steps_available",
        method="POST",
        href=f"/api/ai/runs/{run['id']}/multi-agent/dispatch/claims",
    )
else:
    dispatch_gate = _control_action(enabled=False, reason="admin_only_dispatch", method=None, href=None)
```

Use `ready_count` in the counts block and `dispatch_gate` under
`gates.dispatch`.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_run_control_readiness_projects_multi_agent_dependency_gates tests/test_run_control_routes.py::test_run_control_readiness_enables_admin_multi_agent_dispatch_gate -q --basetemp .pytest-tmp\p2-dispatch-gate
```

Expected: readiness gate tests pass; claim route tests still fail.

## Task 3: Claim Validation Route

**Files:**
- Modify: `app/routes/runs.py`

- [ ] **Step 1: Add a candidate helper**

Add helper functions near the readiness helpers:

```python
def _unsafe_dispatch_reference(value: str, *, raw_terms: set[str]) -> bool:
    return _contains_raw_projection_term(value, raw_terms)


def _dispatch_claim_candidate(
    *,
    run: dict[str, object],
    steps: list[dict[str, object]],
    step_key: str,
    principal: AuthPrincipal,
) -> dict[str, object]:
    run_status = normalize_run_status(str(run.get("status") or ""))
    if run_status not in RUN_CONTROL_ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="run_not_dispatchable")
    configured_steps = _configured_multi_agent_steps(run)
    execution_input = _run_execution_input(run)
    if str(execution_input.get("execution_mode") or "") != "multi_agent":
        raise HTTPException(status_code=409, detail="multi_agent_not_enabled")
    raw_terms = _readiness_raw_projection_terms(run)
    if _unsafe_dispatch_reference(step_key, raw_terms=raw_terms):
        raise HTTPException(status_code=409, detail="unsafe_step_reference")

    configured_by_key = {str(item.get("step_key") or item.get("stepKey")): item for item in configured_steps}
    recorded_by_key = {str(row.get("step_key")): row for row in steps if row.get("step_key") is not None}
    configured = configured_by_key.get(step_key)
    row = recorded_by_key.get(step_key)
    if configured is None and row is None:
        raise HTTPException(status_code=409, detail="step_not_found")

    payload = row.get("payload_json") if row and isinstance(row.get("payload_json"), dict) else {}
    depends_on = _raw_depends_on(
        payload.get("depends_on")
        or (configured or {}).get("depends_on")
        or (configured or {}).get("dependsOn")
    )
    if any(_unsafe_dispatch_reference(dependency, raw_terms=raw_terms) for dependency in depends_on):
        raise HTTPException(status_code=409, detail="unsafe_step_reference")

    status_by_key = {key: normalize_step_status(item.get("status")) for key, item in recorded_by_key.items()}
    dependency_statuses = _dependency_statuses(depends_on, status_by_key, raw_terms=raw_terms, principal=principal)
    status = normalize_step_status(row.get("status") if row else "pending")
    blocked_reason = _multi_agent_blocked_reason(status, dependency_statuses)
    if status != "pending" or blocked_reason is not None:
        raise HTTPException(status_code=409, detail=blocked_reason or "step_not_pending")

    sequence = int((row or {}).get("sequence") or 0) or list(configured_by_key).index(step_key) + 1
    title = public_text_or_fallback((row or {}).get("title") or (configured or {}).get("title"), step_key) or step_key
    role_value = (row or {}).get("role") or (configured or {}).get("role")
    role = public_text_or_fallback(role_value) if role_value is not None else None
    return {
        "step_key": step_key,
        "step_kind": str((row or {}).get("step_kind") or "agent"),
        "title": title,
        "role": role,
        "sequence": sequence,
        "depends_on": depends_on,
    }
```

- [ ] **Step 2: Add the POST route**

Add near other run control routes:

```python
@router.post(
    "/runs/{run_id}/multi-agent/dispatch/claims",
    response_model=MultiAgentDispatchClaimResponse,
)
async def claim_multi_agent_dispatch(
    run_id: str,
    request: MultiAgentDispatchClaimRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> MultiAgentDispatchClaimResponse:
    if not is_ai_admin(principal):
        raise HTTPException(status_code=403, detail="admin_required")
    async with transaction() as conn:
        run = await repositories.get_run(conn, tenant_id=principal.tenant_id, run_id=run_id, for_update=True)
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        steps = await repositories.list_run_steps(conn, tenant_id=principal.tenant_id, run_id=run_id)
        candidate = _dispatch_claim_candidate(
            run=run,
            steps=steps,
            step_key=request.step_key,
            principal=principal,
        )
        result = await repositories.claim_multi_agent_dispatch_step(
            conn,
            tenant_id=principal.tenant_id,
            run_id=run_id,
            claimed_by=principal.user_id,
            trace_id=str(run.get("trace_id") or standard_trace_id(run_id)),
            **candidate,
        )
    return MultiAgentDispatchClaimResponse(
        contract_version=MULTI_AGENT_DISPATCH_CLAIM_CONTRACT_VERSION,
        run_id=run_id,
        step_key=request.step_key,
        step_id=str(result["step"]["id"]),
        status="claimed",
        dispatch_id=str(result["dispatch_id"]),
        event_id=str(result["event_id"]),
        audit_id=str(result["audit_id"]),
        step=run_step_response(result["step"], principal=principal),
    )
```

- [ ] **Step 3: Run route tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_admin_multi_agent_dispatch_claim_records_ledger_event_and_audit tests/test_run_control_routes.py::test_admin_multi_agent_dispatch_claim_rejects_unsafe_dependency_without_writes tests/test_run_control_routes.py::test_multi_agent_dispatch_claim_requires_admin -q --basetemp .pytest-tmp\p2-dispatch-route
```

Expected: tests fail only because the repository helper is missing.

## Task 4: Repository Claim Helper

**Files:**
- Modify: `app/repositories.py`

- [ ] **Step 1: Let `get_run` optionally lock the row**

Change `get_run` to:

```python
async def get_run(conn: AsyncConnection, *, tenant_id: str, run_id: str, for_update: bool = False) -> dict[str, Any] | None:
    lock_clause = "for update" if for_update else ""
    cursor = await conn.execute(
        f"select * from runs where tenant_id = %s and id = %s {lock_clause}",
        (tenant_id, run_id),
    )
    return await cursor.fetchone()
```

- [ ] **Step 2: Add `claim_multi_agent_dispatch_step`**

Add near `upsert_run_step`:

```python
async def claim_multi_agent_dispatch_step(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    run_id: str,
    claimed_by: str,
    trace_id: str,
    step_key: str,
    step_kind: str,
    title: str,
    role: str | None,
    sequence: int,
    depends_on: list[str],
) -> dict[str, Any]:
    dispatch_id = new_id("dispatch")
    step_id = await upsert_run_step(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        step_key=step_key,
        step_kind=step_kind,
        status="running",
        title=title,
        role=role,
        sequence=sequence,
        payload_json={
            "depends_on": depends_on,
            "dispatch_state": "claimed",
            "dispatch_kind": "subagent",
        },
    )
    event_id = await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=run_id,
        trace_id=trace_id,
        event_type="agent_step_started",
        stage="agent",
        message="Multi-agent step dispatch claimed",
        visible_to_user=False,
        payload={
            "visible_to_user": False,
            "step_key": step_key,
            "step_index": sequence,
            "dispatch_state": "claimed",
            "dispatch_id": dispatch_id,
        },
    )
    audit_id = await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=claimed_by,
        action="run.multi_agent.dispatch.claim",
        target_type="run_step",
        target_id=step_id,
        trace_id=trace_id,
        payload_json={
            "run_id": run_id,
            "step_key": step_key,
            "dispatch_id": dispatch_id,
            "result_status": "claimed",
        },
    )
    steps = await list_run_steps(conn, tenant_id=tenant_id, run_id=run_id)
    step = next((item for item in steps if str(item.get("step_key")) == step_key), None)
    if step is None:
        raise RepositoryConflictError("dispatch_step_not_persisted")
    return {"dispatch_id": dispatch_id, "event_id": event_id, "audit_id": audit_id, "step": step}
```

- [ ] **Step 3: Run focused route tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py -q --basetemp .pytest-tmp\p2-dispatch-focused
```

Expected: all run control route tests pass.

## Task 5: Repository Unit Coverage

**Files:**
- Modify: `tests/test_run_control_routes.py`

- [ ] **Step 1: Add a direct repository helper test**

Add an async test near other repository run control tests:

```python
@pytest.mark.asyncio
async def test_claim_multi_agent_dispatch_step_writes_step_event_and_audit(monkeypatch):
    from app import repositories

    calls = []

    async def fake_upsert_run_step(conn, **kwargs):
        calls.append(("step", kwargs))
        return "step-code"

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-code"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-code"

    async def fake_list_run_steps(conn, *, tenant_id, run_id):
        calls.append(("list_steps", tenant_id, run_id))
        return [
            {
                "id": "step-code",
                "run_id": run_id,
                "step_key": "code",
                "step_kind": "agent",
                "status": "running",
                "title": "Code",
                "role": "coder",
                "sequence": 2,
                "payload_json": {
                    "depends_on": ["plan"],
                    "dispatch_state": "claimed",
                    "dispatch_kind": "subagent",
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ]

    monkeypatch.setattr("app.repositories.upsert_run_step", fake_upsert_run_step)
    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.repositories.list_run_steps", fake_list_run_steps)

    result = await repositories.claim_multi_agent_dispatch_step(
        object(),
        tenant_id="default",
        run_id="run-ready",
        claimed_by="admin-a",
        trace_id="trace-ready",
        step_key="code",
        step_kind="agent",
        title="Code",
        role="coder",
        sequence=2,
        depends_on=["plan"],
    )

    assert result["event_id"] == "evt-code"
    assert result["audit_id"] == "aud-code"
    assert result["step"]["payload_json"]["dispatch_state"] == "claimed"
    step_call = calls[0][1]
    assert step_call["status"] == "running"
    assert step_call["payload_json"] == {
        "depends_on": ["plan"],
        "dispatch_state": "claimed",
        "dispatch_kind": "subagent",
    }
    event_call = calls[1][1]
    assert event_call["event_type"] == "agent_step_started"
    assert event_call["visible_to_user"] is False
    assert event_call["payload"]["dispatch_state"] == "claimed"
    audit_call = calls[2][1]
    assert audit_call["action"] == "run.multi_agent.dispatch.claim"
    assert audit_call["target_id"] == "step-code"
    assert audit_call["payload_json"]["result_status"] == "claimed"
```

- [ ] **Step 2: Run focused tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py -q --basetemp .pytest-tmp\p2-dispatch-focused-2
```

Expected: all tests in the file pass.

## Task 6: Verification, Review, And Docs

**Files:**
- Modify after deployment: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`

- [ ] **Step 1: Run local compile**

Run:

```powershell
python -m compileall -q app tools scripts
```

Expected: exit code 0.

- [ ] **Step 2: Run focused tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py tests/test_control_plane_contracts.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p2-dispatch-focused-final
```

Expected: all selected tests pass.

- [ ] **Step 3: Run full tests**

Run:

```powershell
python -m pytest -q --basetemp .pytest-tmp\p2-dispatch-full
```

Expected: full suite passes or any failures are triaged and fixed before commit.

- [ ] **Step 4: Run diff hygiene**

Run:

```powershell
git diff --check
```

Expected: no whitespace errors.

- [ ] **Step 5: Run inherited-configuration review**

Use the available multi-agent path only if it is confirmed to inherit the main
session permission posture. Record that the tool did not expose explicit
model/reasoning fields if applicable, and verify review feedback against current
code before changing anything.

- [ ] **Step 6: Commit, push, PR, and merge**

After all local gates and review are resolved:

```powershell
git add app/models.py app/routes/runs.py app/repositories.py tests/test_run_control_routes.py docs/superpowers/specs/2026-06-05-p2-multi-agent-dispatch-ledger-design.md docs/superpowers/plans/2026-06-05-p2-multi-agent-dispatch-ledger.md
git commit -m "feat: add multi-agent dispatch ledger"
git push -u origin p2-multi-agent-dispatch-ledger
```

Create a PR into `main`, merge after review gates, then update local `main`.

- [ ] **Step 7: Deploy and smoke on 211**

Sync the merged source archive to
`/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform`. Rebuild/recreate
API and worker on 211 only. Verify:

- `/api/ai/health` returns ok.
- API/worker labels show the new `main` revision and `p2-multi-agent-dispatch-ledger` source note.
- OpenAPI includes `/api/ai/runs/{run_id}/multi-agent/dispatch/claims`.
- A safe `plan -> code` multi-agent smoke run lets admin claim `code`.
- The step row is `running` with `dispatch_state = claimed`.
- The hidden `agent_step_started` event is visible to admin and absent from ordinary user event projection.
- Audit log records `run.multi_agent.dispatch.claim`.
- An unsafe dependency claim returns `409 unsafe_step_reference`.
- Smoke DB/Redis data is cleaned up.

- [ ] **Step 8: Record roadmap evidence**

Append a `P2 Multi-Agent Dispatch Ledger` section to the foundation roadmap with
commit, PR, local verification counts, 211 image labels, smoke evidence, and
explicit non-goals.
