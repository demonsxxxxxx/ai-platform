# P2 Multi-Agent Parent Cancel Propagation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Propagate owner/admin parent run cancel requests to same-tenant, server-owned multi-agent child runs.

**Architecture:** Add a repository-level propagation helper called by existing owner/admin cancel routes after parent authorization succeeds. Keep DB state changes transactional; keep Redis queue mutation and sandbox runtime cleanup in routes after commit, and attempt both external cleanups before reporting either failure.

**Tech Stack:** Python, FastAPI, async repository helpers, Redis queue helper, pytest.

---

## File Structure

- Modify `app/repositories.py`: add `propagate_multi_agent_parent_cancel`, return child queued ids and child active sandbox leases.
- Modify `app/routes/runs.py`: call propagation after parent cancel in the same DB transaction, best-effort remove queued parent/child payloads after commit, group stopped sandbox leases by run id, and report queue cleanup failure only after sandbox cleanup has been attempted.
- Modify `app/routes/admin_runs.py`: same route integration for admin cancel, preserving `requested_by_role="admin"`.
- Modify `tests/test_run_control_routes.py`: add repository and route regressions.
- Modify `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`: record the slice after verification.

---

### Task 1: Repository Propagation RED Tests

**Files:**
- Modify: `tests/test_run_control_routes.py`

- [ ] **Step 1: Add failing repository tests**

Add tests near the existing multi-agent reconciliation tests:

```python
@pytest.mark.asyncio
async def test_propagate_multi_agent_parent_cancel_cancels_server_owned_children(monkeypatch):
    from app import repositories
    import json

    calls = []
    child_rows = [
        {
            "id": "run-child-queued",
            "status": "queued",
            "trace_id": "trace-child-queued",
            "cancel_requested_at": None,
            "input_json": {"input": {"multi_agent_dispatch": {"parent_run_id": "run-parent", "parent_step_id": "step-code", "step_key": "code", "dispatch_id": "dispatch-code"}}},
            "parent_step_id": "step-code",
            "step_key": "code",
            "parent_step_payload_json": {"dispatch_state": "handed_off", "dispatch_child_run_id": "run-child-queued", "dispatch_id": "dispatch-code"},
        },
        {
            "id": "run-child-running",
            "status": "running",
            "trace_id": "trace-child-running",
            "cancel_requested_at": None,
            "input_json": {"input": {"multi_agent_dispatch": {"parent_run_id": "run-parent", "parent_step_id": "step-review", "step_key": "review", "dispatch_id": "dispatch-review"}}},
            "parent_step_id": "step-review",
            "step_key": "review",
            "parent_step_payload_json": {"dispatch_state": "handed_off", "dispatch_child_run_id": "run-child-running", "dispatch_id": "dispatch-review"},
        },
    ]

    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []
        async def fetchone(self):
            return self.row
        async def fetchall(self):
            return self.rows

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append(("sql", normalized, params))
            if normalized.startswith("select child.id"):
                assert "child.copied_from_run_id = %s" in normalized
                assert "dispatch_child_run_id" in normalized
                assert "dispatch_state" in normalized
                return Cursor(rows=child_rows)
            if normalized.startswith("update runs"):
                child_id = params[2]
                status = "cancelled" if child_id == "run-child-queued" else "running"
                return Cursor(row={"id": child_id, "status": status, "trace_id": f"trace-{child_id}"})
            if normalized.startswith("update run_steps"):
                return Cursor()
            if normalized.startswith("select * from sandbox_leases"):
                return Cursor(rows=[{"id": "lease-running", "run_id": "run-child-running", "trace_id": "trace-lease"}] if params[1] == "run-child-running" else [])
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return f"evt-{kwargs['event_type']}-{kwargs['run_id']}"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return f"aud-{kwargs['target_id']}"

    async def fake_reconcile(conn, **kwargs):
        calls.append(("reconcile", kwargs))
        return {"event_id": "evt-reconcile", "audit_id": "aud-reconcile"}

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.repositories.reconcile_multi_agent_child_run_terminal_state", fake_reconcile)

    result = await repositories.propagate_multi_agent_parent_cancel(
        FakeConnection(),
        tenant_id="default",
        parent_run_id="run-parent",
        requested_by="user-a",
    )

    assert result["child_run_ids"] == ["run-child-queued", "run-child-running"]
    assert result["queued_child_run_ids"] == ["run-child-queued"]
    assert result["running_child_run_ids"] == ["run-child-running"]
    assert result["active_sandbox_leases"] == [{"id": "lease-running", "run_id": "run-child-running", "trace_id": "trace-lease"}]
    assert any(call[0] == "reconcile" and call[1]["child_run_id"] == "run-child-queued" for call in calls)
    dump = json.dumps([call[1] for call in calls if call[0] in {"event", "audit"}], ensure_ascii=False)
    assert "private_payload" not in dump
    assert "storage_key" not in dump


@pytest.mark.asyncio
async def test_propagate_multi_agent_parent_cancel_ignores_non_server_owned_copies(monkeypatch):
    from app import repositories

    calls = []

    class Cursor:
        async def fetchall(self):
            return []

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            calls.append((normalized, params))
            if normalized.startswith("select child.id"):
                assert "child.copied_from_run_id = %s" in normalized
                assert "payload_json->>'dispatch_child_run_id'" in normalized
                return Cursor()
            raise AssertionError(f"unexpected write for ordinary copied run: {normalized}")

    async def fail_event(conn, **kwargs):
        raise AssertionError("ordinary copied runs must not emit cancel propagation events")

    monkeypatch.setattr("app.repositories.append_event", fail_event)

    result = await repositories.propagate_multi_agent_parent_cancel(
        FakeConnection(),
        tenant_id="default",
        parent_run_id="run-parent",
        requested_by="user-a",
    )

    assert result == {
        "child_run_ids": [],
        "queued_child_run_ids": [],
        "running_child_run_ids": [],
        "active_sandbox_leases": [],
        "event_ids": [],
        "audit_ids": [],
    }
```

- [ ] **Step 2: Run RED tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_propagate_multi_agent_parent_cancel_cancels_server_owned_children tests/test_run_control_routes.py::test_propagate_multi_agent_parent_cancel_ignores_non_server_owned_copies -q --basetemp .pytest-tmp\red-parent-cancel-repo
```

Expected: fail because `propagate_multi_agent_parent_cancel` does not exist.

---

### Task 2: Repository Propagation Implementation

**Files:**
- Modify: `app/repositories.py`

- [ ] **Step 1: Implement helper**

Add a helper near the existing multi-agent repository helpers:

```python
async def propagate_multi_agent_parent_cancel(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    parent_run_id: str,
    requested_by: str,
    requested_by_role: str | None = None,
) -> dict[str, Any]:
    result = {
        "child_run_ids": [],
        "queued_child_run_ids": [],
        "running_child_run_ids": [],
        "active_sandbox_leases": [],
        "event_ids": [],
        "audit_ids": [],
    }
    cursor = await conn.execute(
        """
        select child.id, child.status, child.trace_id, child.cancel_requested_at,
               child.input_json,
               parent_step.id as parent_step_id,
               parent_step.step_key,
               parent_step.payload_json as parent_step_payload_json
        from runs child
        join run_steps parent_step
          on parent_step.tenant_id = child.tenant_id
         and parent_step.run_id = child.copied_from_run_id
         and parent_step.payload_json->>'dispatch_child_run_id' = child.id
        where child.tenant_id = %s
          and child.copied_from_run_id = %s
          and child.status in ('queued', 'running')
          and (
            child.input_json#>>'{input,multi_agent_dispatch,parent_run_id}' = %s
            or child.input_json#>>'{multi_agent_dispatch,parent_run_id}' = %s
          )
          and parent_step.payload_json->>'dispatch_state' = 'handed_off'
        for update of child, parent_step
        """,
        (tenant_id, parent_run_id, parent_run_id, parent_run_id),
    )
    rows = await cursor.fetchall()
    for row in rows:
        child_run_id = str(row["id"])
        dispatch = _child_dispatch_metadata(row)
        step_payload = row.get("parent_step_payload_json") if isinstance(row.get("parent_step_payload_json"), dict) else {}
        dispatch_id = str(dispatch.get("dispatch_id") or "").strip()
        parent_step_id = str(dispatch.get("parent_step_id") or "").strip()
        step_key = str(dispatch.get("step_key") or "").strip()
        if (
            str(dispatch.get("parent_run_id") or "") != parent_run_id
            or str(row.get("parent_step_id") or "") != parent_step_id
            or str(row.get("step_key") or "") != step_key
            or str(step_payload.get("dispatch_id") or "") != dispatch_id
            or str(step_payload.get("dispatch_child_run_id") or "") != child_run_id
            or step_payload.get("dispatch_state") != "handed_off"
        ):
            continue
        update_cursor = await conn.execute(
            """
            update runs
            set
              cancel_requested_at = coalesce(cancel_requested_at, now()),
              cancel_requested_by = coalesce(cancel_requested_by, %s),
              status = case when status = 'queued' then 'cancelled' else status end,
              finished_at = case when status = 'queued' then now() else finished_at end
            where tenant_id = %s
              and id = %s
              and status in ('queued', 'running')
            returning id, status, trace_id
            """,
            (requested_by, tenant_id, child_run_id),
        )
        updated = await update_cursor.fetchone()
        if updated is None:
            continue
        child_status = str(updated["status"])
        result["child_run_ids"].append(child_run_id)
        if child_status == "cancelled":
            result["queued_child_run_ids"].append(child_run_id)
            await _cancel_open_run_steps(conn, tenant_id=tenant_id, run_id=child_run_id)
        else:
            result["running_child_run_ids"].append(child_run_id)
        leases = await list_active_sandbox_leases_for_run(conn, tenant_id=tenant_id, run_id=child_run_id)
        result["active_sandbox_leases"].extend(leases)
        if row.get("cancel_requested_at") is None:
            event_id = await append_event(
                conn,
                tenant_id=tenant_id,
                run_id=child_run_id,
                trace_id=updated.get("trace_id"),
                event_type="cancel_requested",
                stage="control",
                message="已随父任务请求取消",
                payload={
                    "visible_to_user": True,
                    "severity": "warning",
                    "requested_by": requested_by,
                    "requested_by_role": requested_by_role or "owner",
                    "source": "multi_agent_parent_cancel",
                    "parent_run_id": parent_run_id,
                },
            )
            result["event_ids"].append(event_id)
        if child_status == "cancelled":
            cancelled_event_id = await append_event(
                conn,
                tenant_id=tenant_id,
                run_id=child_run_id,
                trace_id=updated.get("trace_id"),
                event_type="run_cancelled",
                stage="control",
                message="任务已取消",
                payload={"visible_to_user": True, "severity": "warning", "source": "multi_agent_parent_cancel"},
            )
            result["event_ids"].append(cancelled_event_id)
            reconciled = await reconcile_multi_agent_child_run_terminal_state(
                conn,
                tenant_id=tenant_id,
                child_run_id=child_run_id,
                child_status="cancelled",
                result_json={"message": "parent_cancel_requested"},
                error_code="parent_cancel_requested",
                error_message="parent_cancel_requested",
            )
            if reconciled:
                for key in ("event_id", "audit_id"):
                    if reconciled.get(key):
                        result["event_ids" if key == "event_id" else "audit_ids"].append(reconciled[key])
        audit_id = await append_audit_log(
            conn,
            tenant_id=tenant_id,
            user_id=requested_by,
            action="run.multi_agent.dispatch.cancel_propagate",
            target_type="run",
            target_id=child_run_id,
            trace_id=updated.get("trace_id"),
            payload_json={
                "parent_run_id": parent_run_id,
                "child_run_id": child_run_id,
                "parent_step_id": parent_step_id,
                "step_key": step_key,
                "dispatch_id": dispatch_id,
                "requested_by_role": requested_by_role or "owner",
                "result_status": "cancelled" if child_status == "cancelled" else "cancel_requested",
            },
        )
        result["audit_ids"].append(audit_id)
    return result
```

- [ ] **Step 2: Wire helper from cancel routes**

In the owner cancel route, after `request_run_cancel` succeeds inside the transaction:

```python
    propagation = await propagate_multi_agent_parent_cancel(
        conn,
        tenant_id=tenant_id,
        parent_run_id=run_id,
        requested_by=user_id,
    )
    if propagation["queued_child_run_ids"]:
        result["queued_child_run_ids"] = propagation["queued_child_run_ids"]
    if propagation["active_sandbox_leases"]:
        result["active_sandbox_leases"] = [*result.get("active_sandbox_leases", []), *propagation["active_sandbox_leases"]]
```

In the admin cancel route, use `requested_by=admin_user_id` and `requested_by_role="admin"`.

- [ ] **Step 3: Run GREEN repository tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_propagate_multi_agent_parent_cancel_cancels_server_owned_children tests/test_run_control_routes.py::test_propagate_multi_agent_parent_cancel_ignores_non_server_owned_copies -q --basetemp .pytest-tmp\green-parent-cancel-repo
```

Expected: both tests pass.

---

### Task 3: Route RED Tests

**Files:**
- Modify: `tests/test_run_control_routes.py`

- [ ] **Step 1: Add failing route tests**

Add tests near the existing cancel route tests:

```python
def test_cancel_run_removes_propagated_queued_child_payloads(monkeypatch):
    calls = []

    async def fake_request_run_cancel(conn, *, tenant_id, user_id, run_id):
        return {"run_id": run_id, "status": "cancel_requested", "queued_child_run_ids": ["run-child-queued"]}

    async def fake_remove_queued_run(*, tenant_id, run_id):
        calls.append(("remove", tenant_id, run_id))
        return 1

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.runs.repositories.request_run_cancel", fake_request_run_cancel)
    monkeypatch.setattr("app.routes.runs.remove_queued_run", fake_remove_queued_run, raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-parent/cancel", headers=headers())

    assert response.status_code == 200
    assert calls == [("remove", "default", "run-child-queued")]


def test_admin_cancel_run_releases_propagated_child_sandbox_lease(monkeypatch):
    calls = []
    release_calls = []

    async def fake_request_admin_run_cancel(conn, *, tenant_id, admin_user_id, run_id):
        return {
            "run_id": run_id,
            "status": "cancel_requested",
            "active_sandbox_leases": [sandbox_lease_row(run_id="run-child-running", user_id="target-user")],
        }

    async def fake_release_stopped_sandbox_leases_for_cancel(conn, **kwargs):
        release_calls.append(kwargs)
        return []

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.admin_runs.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.admin_runs.repositories.request_admin_run_cancel", fake_request_admin_run_cancel)
    monkeypatch.setattr("app.routes.admin_runs.repositories.release_stopped_sandbox_leases_for_cancel", fake_release_stopped_sandbox_leases_for_cancel, raising=False)
    monkeypatch.setattr("app.routes.admin_runs.create_container_provider", lambda provider_name=None: RecordingSandboxProvider(calls), raising=False)
    client = TestClient(create_app())

    response = client.post("/api/ai/admin/runs/run-parent/cancel", headers=admin_headers())

    assert response.status_code == 200
    assert calls[0][1].run_id == "run-child-running"
    assert release_calls == [{
        "tenant_id": "default",
        "run_id": "run-child-running",
        "reason": "admin_cancel_requested",
        "lease_ids": ["lease-run-child-running"],
        "trace_id": None,
        "requested_by_role": "admin",
    }]
```

- [ ] **Step 2: Run RED route tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_cancel_run_removes_propagated_queued_child_payloads tests/test_run_control_routes.py::test_admin_cancel_run_releases_propagated_child_sandbox_lease -q --basetemp .pytest-tmp\red-parent-cancel-routes
```

Expected: first test fails because child queued payload is not removed; second test may fail until lease release is grouped by child run id.

---

### Task 4: Route Integration Implementation

**Files:**
- Modify: `app/routes/runs.py`
- Modify: `app/routes/admin_runs.py`

- [ ] **Step 1: Add lease grouping helper in each route module or one shared local function**

Use a small private helper close to the cancel route:

```python
def _lease_ids_by_run_id(leases: list[dict[str, Any]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for lease in leases:
        run_id = str(lease.get("run_id") or "")
        lease_id = str(lease.get("id") or "")
        if not run_id or not lease_id:
            continue
        grouped.setdefault(run_id, []).append(lease_id)
    return grouped
```

- [ ] **Step 2: Release stopped leases by run id**

Replace the single-run release call with a loop over `_lease_ids_by_run_id(stopped_sandbox_leases)` and pass the matching `run_id`.

- [ ] **Step 3: Remove propagated queued child payloads without skipping sandbox cleanup**

After the DB transaction commits, attempt parent and child queued removals before sandbox cleanup, but catch queue cleanup errors so sandbox stop/release still runs:

```python
    queue_cleanup_failures = await _remove_cancelled_queue_payloads(
        tenant_id=principal.tenant_id,
        parent_run_id=run_id,
        result=result,
    )
```

After sandbox stop/release succeeds, raise `HTTPException(status_code=502, detail="queue_cleanup_failed")` if `queue_cleanup_failures` is not empty. If sandbox stop fails, keep the existing `sandbox_runtime_cleanup_failed` response after releasing any successfully stopped leases.

- [ ] **Step 4: Run GREEN route tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_cancel_run_removes_propagated_queued_child_payloads tests/test_run_control_routes.py::test_admin_cancel_run_releases_propagated_child_sandbox_lease -q --basetemp .pytest-tmp\green-parent-cancel-routes
```

Expected: both tests pass.

---

### Task 5: Focused Verification And Roadmap

**Files:**
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`

- [ ] **Step 1: Run focused tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py -q --basetemp .pytest-tmp\verify-parent-cancel-routes
```

Expected: route/control tests pass.

- [ ] **Step 2: Run compile check**

Run:

```powershell
python -m compileall -q app tools scripts
```

Expected: exit 0.

- [ ] **Step 3: Update roadmap**

Append a concise `### P2 Multi-Agent Parent Cancel Propagation` section after child completion reconciliation with local verification evidence and non-goals.

- [ ] **Step 4: Run full local verification**

Run:

```powershell
python -m pytest -q --basetemp .pytest-tmp\full-parent-cancel
```

Expected: full suite passes or any failures are diagnosed before commit.

---

### Task 6: Review, Commit, Deploy, Smoke

**Files:**
- Review all changed files.

- [ ] **Step 1: Request inherited-configuration multi-agent review**

Ask a subagent to review the diff for cancel propagation safety, tenant scoping, sandbox cleanup, Redis queue cleanup, and payload redaction. Because the current `spawn_agent` tool has no `model` or `reasoning_effort` fields, record this as inherited-configuration review only.

- [ ] **Step 2: Fix accepted review feedback**

Only apply feedback that is validated against PRD, roadmap, guardrails, code, and tests.

- [ ] **Step 3: Commit and push branch**

Run:

```powershell
git diff --check
git status --short
git add app/repositories.py app/routes/runs.py app/routes/admin_runs.py tests/test_run_control_routes.py docs/superpowers/specs/2026-06-06-p2-multi-agent-parent-cancel-propagation-design.md docs/superpowers/plans/2026-06-06-p2-multi-agent-parent-cancel-propagation.md docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md
git commit -m "feat: propagate multi-agent parent cancel"
git push -u origin p2-multi-agent-parent-cancel-propagation
```

- [ ] **Step 4: Create PR, merge after gates, deploy to 211**

Deploy only on 211 or another Docker-capable host. Do not print or copy real `.env` values.

- [ ] **Step 5: 211 smoke**

Verify:

- API health on `http://127.0.0.1:8020/api/ai/health` and frontend proxy health on `http://127.0.0.1:18001/api/ai/health`;
- API/worker image label parity with merged commit;
- owner/admin parent cancel propagates to server-owned queued/running child rows;
- queued child Redis payload is removed;
- running child active sandbox lease is stopped/released when present;
- no private payload appears in propagation events or audit rows;
- recent API/worker logs are clean;
- smoke rows are cleaned up.
