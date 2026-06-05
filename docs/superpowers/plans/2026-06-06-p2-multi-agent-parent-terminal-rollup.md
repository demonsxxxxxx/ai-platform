# P2 Multi-Agent Parent Terminal Rollup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finalize a multi-agent parent run once all server-owned child-dispatch parent steps are terminal.

**Architecture:** Add a repository-level rollup helper that reads persisted parent run and step state, derives a public-safe parent result, writes terminal parent status, and emits hidden event plus audit evidence. Call it only after child reconciliation successfully updates a parent step, so the existing manual claim/handoff/worker reconciliation flow becomes lifecycle-complete without adding an autonomous scheduler.

**Tech Stack:** Python, FastAPI backend, async PostgreSQL repository helpers, existing run event/audit contracts, pytest.

---

## File Structure

- Modify `app/control_plane_contracts.py`: add `multi_agent_parent_finalized` to the standard event taxonomy.
- Modify `app/repositories.py`: add parent rollup helpers and call `finalize_multi_agent_parent_run_if_ready` after successful child reconciliation.
- Modify `app/worker.py`: after a child terminal reconciliation succeeds, perform one post-commit parent rollup retry in a fresh transaction.
- Modify `app/routes/runs.py`: after owner cancel propagation, call the parent rollup helper in the same transaction.
- Modify `app/routes/admin_runs.py`: after admin cancel propagation, call the parent rollup helper in the same transaction.
- Modify `tests/test_control_plane_contracts.py`: cover the new standard event type.
- Modify `tests/test_run_control_routes.py`: add repository RED/GREEN tests for success, failure, cancellation, blocked active-child state, non-multi-agent ignore, and reconciliation hook behavior.
- Modify `tests/test_admin_run_detail.py`: keep admin cancel test fixtures compatible with cancel-path parent rollup no-op probes.
- Modify `tests/test_worker.py`: cover worker post-commit parent rollup retry after child reconciliation.
- Modify `tests/test_routes.py`: add a narrow projection regression proving finalized parent result and playback remain public-safe.
- Modify `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`: record implementation, review, local verification, and 211 deployment evidence after the slice is verified.

---

### Task 1: Event Taxonomy And Repository RED Tests

**Files:**
- Modify: `tests/test_control_plane_contracts.py`
- Modify: `tests/test_run_control_routes.py`

- [ ] **Step 1: Add the standard event RED assertion**

In `tests/test_control_plane_contracts.py`, extend `test_standard_event_taxonomy_covers_g2_lifecycle_events`:

```python
def test_standard_event_taxonomy_covers_g2_lifecycle_events():
    assert "queued" in STANDARD_EVENT_TYPES
    assert "skill_selected" in STANDARD_EVENT_TYPES
    assert "artifact_created" in STANDARD_EVENT_TYPES
    assert "mcp_tool_call_completed" in STANDARD_EVENT_TYPES
    assert "context_snapshot_created" in STANDARD_EVENT_TYPES
    assert "tool_permission_requested" in STANDARD_EVENT_TYPES
    assert "sandbox_lease_created" in STANDARD_EVENT_TYPES
    assert "checkpoint_created" in STANDARD_EVENT_TYPES
    assert "subagent_started" in STANDARD_EVENT_TYPES
    assert "subagent_completed" in STANDARD_EVENT_TYPES
    assert "subagent_failed" in STANDARD_EVENT_TYPES
    assert "multi_agent_parent_finalized" in STANDARD_EVENT_TYPES
    assert is_standard_event_type("run_succeeded") is True
    assert is_standard_event_type("unknown_custom_event") is False
    assert standard_error_code(None) == "unknown_error"
```

- [ ] **Step 2: Add repository RED tests**

Append these tests near the existing multi-agent reconciliation tests in
`tests/test_run_control_routes.py`:

```python
@pytest.mark.asyncio
async def test_finalize_multi_agent_parent_run_success_writes_public_result_event_and_audit(monkeypatch):
    from app import repositories
    import json

    calls = []
    parent_run = {
        "id": "run-parent",
        "tenant_id": "default",
        "trace_id": "trace-parent",
        "status": "running",
        "cancel_requested_at": None,
        "input_json": {
            "input": {
                "execution_mode": "multi_agent",
                "multi_agent_steps": [
                    {"step_key": "plan", "role": "planner", "depends_on": []},
                    {"step_key": "code", "role": "coder", "depends_on": ["plan"]},
                ],
            }
        },
    }
    parent_steps = [
        {
            "id": "step-plan",
            "run_id": "run-parent",
            "step_key": "plan",
            "step_kind": "agent",
            "status": "succeeded",
            "title": "Plan",
            "role": "planner",
            "sequence": 1,
            "payload_json": {
                "depends_on": [],
                "dispatch_state": "completed",
                "dispatch_child_run_id": "run-child-plan",
                "output": "safe plan",
                "checkpoint_id": "checkpoint_step-plan",
                "source_step_id": "step-plan",
                "executor_payload": {"private_payload": "hidden"},
                "storage_key": "tenant/default/private/object",
            },
            "started_at": None,
            "finished_at": None,
            "created_at": None,
            "updated_at": None,
        },
        {
            "id": "step-code",
            "run_id": "run-parent",
            "step_key": "code",
            "step_kind": "agent",
            "status": "succeeded",
            "title": "Code",
            "role": "coder",
            "sequence": 2,
            "payload_json": {
                "depends_on": ["plan"],
                "dispatch_state": "completed",
                "dispatch_child_run_id": "run-child-code",
                "output": "safe code",
                "checkpoint_id": "checkpoint_step-code",
                "source_step_id": "step-code",
            },
            "started_at": None,
            "finished_at": None,
            "created_at": None,
            "updated_at": None,
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
            if normalized.startswith("select id, tenant_id, trace_id"):
                return Cursor(row=parent_run)
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                return Cursor(rows=parent_steps)
            if normalized.startswith("select child.id"):
                return Cursor(rows=[])
            if normalized.startswith("update runs"):
                return Cursor(row={"id": "run-parent", "status": "succeeded"})
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return "evt-parent-finalized"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "aud-parent-finalized"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    result = await repositories.finalize_multi_agent_parent_run_if_ready(
        FakeConnection(),
        tenant_id="default",
        parent_run_id="run-parent",
        triggered_by_child_run_id="run-child-code",
    )

    assert result == {
        "parent_run_id": "run-parent",
        "status": "succeeded",
        "event_id": "evt-parent-finalized",
        "audit_id": "aud-parent-finalized",
        "counts": {"total": 2, "succeeded": 2, "failed": 0, "cancelled": 0},
    }
    update_params = next(params for kind, sql, params in calls if kind == "sql" and sql.startswith("update runs"))
    result_payload = json.loads(update_params[0])
    assert result_payload["message"] == "Multi-agent run succeeded"
    assert result_payload["multi_agent"]["status"] == "succeeded"
    assert result_payload["multi_agent"]["triggered_by_child_run_id"] == "run-child-code"
    assert result_payload["multi_agent"]["steps"][0]["output"] == "safe plan"
    dumped = json.dumps(result_payload, ensure_ascii=False)
    assert "private_payload" not in dumped
    assert "storage_key" not in dumped
    event = next(item[1] for item in calls if item[0] == "event")
    assert event["event_type"] == "multi_agent_parent_finalized"
    assert event["visible_to_user"] is False
    audit = next(item[1] for item in calls if item[0] == "audit")
    assert audit["action"] == "run.multi_agent.parent.finalize"
    assert audit["target_id"] == "run-parent"


@pytest.mark.asyncio
async def test_finalize_multi_agent_parent_run_failure_and_cancel_statuses(monkeypatch):
    from app import repositories

    calls = []
    statuses_seen = []

    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class FakeConnection:
        def __init__(self, parent_statuses):
            self.parent_statuses = parent_statuses

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if normalized.startswith("select id, tenant_id, trace_id"):
                return Cursor(
                    row={
                        "id": "run-parent",
                        "tenant_id": "default",
                        "trace_id": "trace-parent",
                        "status": "running",
                        "cancel_requested_at": self.parent_statuses.get("cancel_requested_at"),
                        "input_json": {"input": {"execution_mode": "multi_agent", "multi_agent_steps": [{"step_key": "step-a"}]}},
                    }
                )
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                return Cursor(rows=self.parent_statuses["steps"])
            if normalized.startswith("select child.id"):
                return Cursor(rows=[])
            if normalized.startswith("update runs"):
                statuses_seen.append(params[-3])
                return Cursor(row={"id": "run-parent", "status": params[-3]})
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        calls.append(("event", kwargs))
        return f"evt-{kwargs['payload']['status']}"

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return f"aud-{kwargs['payload_json']['status']}"

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)

    failed = await repositories.finalize_multi_agent_parent_run_if_ready(
        FakeConnection(
            {
                "cancel_requested_at": None,
                "steps": [
                    {
                        "id": "step-a",
                        "run_id": "run-parent",
                        "step_key": "step-a",
                        "step_kind": "agent",
                        "status": "failed",
                        "title": "Step A",
                        "role": "coder",
                        "sequence": 1,
                        "payload_json": {"error_code": "child_run_failed", "error": "safe failure"},
                        "started_at": None,
                        "finished_at": None,
                        "created_at": None,
                        "updated_at": None,
                    }
                ],
            }
        ),
        tenant_id="default",
        parent_run_id="run-parent",
    )
    cancelled = await repositories.finalize_multi_agent_parent_run_if_ready(
        FakeConnection(
            {
                "cancel_requested_at": "2026-06-06T00:00:00+00:00",
                "steps": [
                    {
                        "id": "step-a",
                        "run_id": "run-parent",
                        "step_key": "step-a",
                        "step_kind": "agent",
                        "status": "succeeded",
                        "title": "Step A",
                        "role": "coder",
                        "sequence": 1,
                        "payload_json": {"output": "done"},
                        "started_at": None,
                        "finished_at": None,
                        "created_at": None,
                        "updated_at": None,
                    }
                ],
            }
        ),
        tenant_id="default",
        parent_run_id="run-parent",
    )

    assert failed["status"] == "failed"
    assert cancelled["status"] == "cancelled"
    assert statuses_seen == ["failed", "cancelled"]


@pytest.mark.asyncio
async def test_finalize_multi_agent_parent_run_blocks_active_children_and_non_multi_agent(monkeypatch):
    from app import repositories

    class Cursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class FakeConnection:
        def __init__(self, *, execution_mode="multi_agent", active_children=None):
            self.execution_mode = execution_mode
            self.active_children = active_children or []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if normalized.startswith("select id, tenant_id, trace_id"):
                return Cursor(
                    row={
                        "id": "run-parent",
                        "tenant_id": "default",
                        "trace_id": "trace-parent",
                        "status": "running",
                        "cancel_requested_at": None,
                        "input_json": {"input": {"execution_mode": self.execution_mode, "multi_agent_steps": [{"step_key": "step-a"}]}},
                    }
                )
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                return Cursor(
                    rows=[
                        {
                            "id": "step-a",
                            "run_id": "run-parent",
                            "step_key": "step-a",
                            "step_kind": "agent",
                            "status": "succeeded",
                            "title": "Step A",
                            "role": "coder",
                            "sequence": 1,
                            "payload_json": {"output": "done"},
                            "started_at": None,
                            "finished_at": None,
                            "created_at": None,
                            "updated_at": None,
                        }
                    ]
                )
            if normalized.startswith("select child.id"):
                return Cursor(rows=self.active_children)
            if normalized.startswith("update runs"):
                raise AssertionError("blocked parent must not be finalized")
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fail_event(conn, **kwargs):
        raise AssertionError("blocked parent must not emit event")

    monkeypatch.setattr("app.repositories.append_event", fail_event)

    active_child_result = await repositories.finalize_multi_agent_parent_run_if_ready(
        FakeConnection(active_children=[{"id": "run-child", "status": "queued"}]),
        tenant_id="default",
        parent_run_id="run-parent",
    )
    non_multi_agent_result = await repositories.finalize_multi_agent_parent_run_if_ready(
        FakeConnection(execution_mode="single_agent"),
        tenant_id="default",
        parent_run_id="run-parent",
    )

    assert active_child_result is None
    assert non_multi_agent_result is None
```

- [ ] **Step 3: Run the RED command**

Run:

```powershell
python -m pytest tests/test_control_plane_contracts.py::test_standard_event_taxonomy_covers_g2_lifecycle_events tests/test_run_control_routes.py::test_finalize_multi_agent_parent_run_success_writes_public_result_event_and_audit tests/test_run_control_routes.py::test_finalize_multi_agent_parent_run_failure_and_cancel_statuses tests/test_run_control_routes.py::test_finalize_multi_agent_parent_run_blocks_active_children_and_non_multi_agent -q --basetemp .pytest-tmp
```

Expected: fail because `multi_agent_parent_finalized` and `finalize_multi_agent_parent_run_if_ready` do not exist yet.

---

### Task 2: Repository Rollup Implementation

**Files:**
- Modify: `app/control_plane_contracts.py`
- Modify: `app/repositories.py`
- Test: `tests/test_control_plane_contracts.py`
- Test: `tests/test_run_control_routes.py`

- [ ] **Step 1: Add standard event type**

In `app/control_plane_contracts.py`, add:

```python
"multi_agent_parent_finalized",
```

inside `STANDARD_EVENT_TYPES` near the other lifecycle/control events.

- [ ] **Step 2: Add parent rollup helpers**

In `app/repositories.py`, add these helpers near `_safe_child_error_code`:

```python
def _parent_multi_agent_message(status: str) -> str:
    if status == "succeeded":
        return "Multi-agent run succeeded"
    if status == "failed":
        return "Multi-agent run failed"
    if status == "cancelled":
        return "Multi-agent run cancelled"
    return "Multi-agent run finalized"


def _safe_parent_step_text(value: Any) -> str:
    return sanitize_public_text(value)


def _safe_parent_step_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    depends_on = payload.get("depends_on")
    if isinstance(depends_on, list):
        cleaned["depends_on"] = [str(item) for item in depends_on if sanitize_public_text(item)]
    else:
        cleaned["depends_on"] = []
    for key in (
        "dispatch_state",
        "dispatch_child_run_id",
        "checkpoint_id",
        "source_step_id",
        "output",
        "error_code",
        "error",
    ):
        safe_value = sanitize_public_text(payload.get(key))
        if safe_value:
            target_key = "child_run_id" if key == "dispatch_child_run_id" else key
            cleaned[target_key] = safe_value
    return cleaned


def _multi_agent_parent_step_summary(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload_json") if isinstance(row.get("payload_json"), dict) else {}
    safe_payload = _safe_parent_step_payload(payload)
    return {
        "step_key": _safe_parent_step_text(row.get("step_key")) or str(row.get("id") or ""),
        "status": str(row.get("status") or ""),
        "role": _safe_parent_step_text(row.get("role")) or None,
        "sequence": _coerce_int(row.get("sequence")),
        "depends_on": safe_payload.pop("depends_on", []),
        **safe_payload,
    }


def _multi_agent_parent_counts(steps: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(steps),
        "succeeded": sum(1 for item in steps if str(item.get("status") or "") == "succeeded"),
        "failed": sum(1 for item in steps if str(item.get("status") or "") == "failed"),
        "cancelled": sum(1 for item in steps if str(item.get("status") or "") == "cancelled"),
    }


def _multi_agent_parent_status(parent_run: dict[str, Any], steps: list[dict[str, Any]]) -> str | None:
    if not steps:
        return None
    statuses = {str(item.get("status") or "") for item in steps}
    if not statuses.issubset(TERMINAL_RUN_STATUSES):
        return None
    if "failed" in statuses:
        return "failed"
    if parent_run.get("cancel_requested_at") or "cancelled" in statuses:
        return "cancelled"
    return "succeeded"
```

- [ ] **Step 3: Add `finalize_multi_agent_parent_run_if_ready`**

In `app/repositories.py`, add the helper after the functions above:

```python
async def finalize_multi_agent_parent_run_if_ready(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    parent_run_id: str,
    triggered_by_child_run_id: str | None = None,
) -> dict[str, Any] | None:
    parent_cursor = await conn.execute(
        """
        select id, tenant_id, trace_id, status, cancel_requested_at, input_json
        from runs
        where tenant_id = %s and id = %s
        for update
        """,
        (tenant_id, parent_run_id),
    )
    parent_run = await parent_cursor.fetchone()
    if parent_run is None:
        return None
    parent_status = str(parent_run.get("status") or "")
    if parent_status in TERMINAL_RUN_STATUSES:
        return None
    if parent_status != "running" and parent_run.get("cancel_requested_at") is None:
        return None
    execution_input = _run_execution_input_from_row(parent_run)
    if str(execution_input.get("execution_mode") or "") != "multi_agent":
        return None
    configured_steps = execution_input.get("multi_agent_steps")
    configured_count = len(configured_steps) if isinstance(configured_steps, list) else 0
    steps = await list_run_steps(conn, tenant_id=tenant_id, run_id=parent_run_id)
    if not steps and configured_count <= 0:
        return None
    if len(steps) < configured_count:
        return None
    target_status = _multi_agent_parent_status(parent_run, steps)
    if target_status is None:
        return None
    active_cursor = await conn.execute(
        """
        select child.id, child.status
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
        limit 1
        """,
        (tenant_id, parent_run_id, parent_run_id, parent_run_id),
    )
    if await active_cursor.fetchone() is not None:
        return None
    summaries = [_multi_agent_parent_step_summary(row) for row in steps]
    counts = _multi_agent_parent_counts(summaries)
    result_json = {
        "message": _parent_multi_agent_message(target_status),
        "multi_agent": {
            "status": target_status,
            "counts": counts,
            "steps": summaries,
        },
    }
    safe_triggered_by = sanitize_public_text(triggered_by_child_run_id)
    if safe_triggered_by:
        result_json["multi_agent"]["triggered_by_child_run_id"] = safe_triggered_by
    update_cursor = await conn.execute(
        """
        update runs
        set
          status = %s,
          result_json = %s::jsonb,
          finished_at = now(),
          error_code = case when %s = 'failed' then 'multi_agent_child_failed' else null end,
          error_message = case when %s = 'failed' then 'Multi-agent child step failed' else null end
        where tenant_id = %s
          and id = %s
          and status not in ('succeeded', 'failed', 'cancelled')
        returning id, status
        """,
        (
            target_status,
            dumps_json(result_json),
            target_status,
            target_status,
            tenant_id,
            parent_run_id,
        ),
    )
    updated = await update_cursor.fetchone()
    if updated is None:
        return None
    event_payload = {
        "visible_to_user": False,
        "status": target_status,
        "counts": counts,
    }
    if safe_triggered_by:
        event_payload["triggered_by_child_run_id"] = safe_triggered_by
    event_id = await append_event(
        conn,
        tenant_id=tenant_id,
        run_id=parent_run_id,
        trace_id=parent_run.get("trace_id"),
        event_type="multi_agent_parent_finalized",
        stage="control",
        message=_parent_multi_agent_message(target_status),
        visible_to_user=False,
        payload=event_payload,
    )
    audit_payload = {"status": target_status, "counts": counts}
    if safe_triggered_by:
        audit_payload["triggered_by_child_run_id"] = safe_triggered_by
    audit_id = await append_audit_log(
        conn,
        tenant_id=tenant_id,
        user_id=None,
        action="run.multi_agent.parent.finalize",
        target_type="run",
        target_id=parent_run_id,
        trace_id=parent_run.get("trace_id"),
        payload_json=audit_payload,
    )
    return {
        "parent_run_id": parent_run_id,
        "status": target_status,
        "event_id": event_id,
        "audit_id": audit_id,
        "counts": counts,
    }
```

- [ ] **Step 4: Run GREEN tests**

Run:

```powershell
python -m pytest tests/test_control_plane_contracts.py::test_standard_event_taxonomy_covers_g2_lifecycle_events tests/test_run_control_routes.py::test_finalize_multi_agent_parent_run_success_writes_public_result_event_and_audit tests/test_run_control_routes.py::test_finalize_multi_agent_parent_run_failure_and_cancel_statuses tests/test_run_control_routes.py::test_finalize_multi_agent_parent_run_blocks_active_children_and_non_multi_agent -q --basetemp .pytest-tmp
```

Expected: pass.

---

### Task 3: Child Reconciliation Hook

**Files:**
- Modify: `app/repositories.py`
- Modify: `tests/test_run_control_routes.py`

- [ ] **Step 1: Add hook RED tests**

Add these tests near the existing reconciliation tests:

```python
@pytest.mark.asyncio
async def test_reconcile_multi_agent_child_success_invokes_parent_rollup(monkeypatch):
    from app import repositories

    calls = []
    child_run = {
        "id": "run-child",
        "tenant_id": "default",
        "copied_from_run_id": "run-parent",
        "trace_id": "trace-child",
        "status": "succeeded",
        "input_json": {
            "input": {
                "multi_agent_dispatch": {
                    "parent_run_id": "run-parent",
                    "parent_step_id": "step-code",
                    "step_key": "code",
                    "dispatch_id": "dispatch-code",
                }
            }
        },
    }
    parent_step = {
        "id": "step-code",
        "run_id": "run-parent",
        "step_key": "code",
        "step_kind": "agent",
        "status": "running",
        "title": "Code",
        "role": "coder",
        "sequence": 2,
        "payload_json": {
            "dispatch_id": "dispatch-code",
            "dispatch_state": "handed_off",
            "dispatch_child_run_id": "run-child",
        },
    }

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if normalized.startswith("select id, tenant_id, copied_from_run_id"):
                return Cursor(row=child_run)
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                return Cursor(row=parent_step)
            if normalized.startswith("update run_steps"):
                return Cursor(row={"id": "step-code"})
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fake_append_event(conn, **kwargs):
        return "evt-reconcile"

    async def fake_append_audit_log(conn, **kwargs):
        return "aud-reconcile"

    async def fake_finalize(conn, **kwargs):
        calls.append(kwargs)
        return {"parent_run_id": "run-parent", "status": "succeeded"}

    monkeypatch.setattr("app.repositories.append_event", fake_append_event)
    monkeypatch.setattr("app.repositories.append_audit_log", fake_append_audit_log)
    monkeypatch.setattr("app.repositories.finalize_multi_agent_parent_run_if_ready", fake_finalize)

    result = await repositories.reconcile_multi_agent_child_run_terminal_state(
        FakeConnection(),
        tenant_id="default",
        child_run_id="run-child",
        child_status="succeeded",
        result_json={"message": "child output"},
    )

    assert result["status"] == "succeeded"
    assert calls == [
        {
            "tenant_id": "default",
            "parent_run_id": "run-parent",
            "triggered_by_child_run_id": "run-child",
        }
    ]


@pytest.mark.asyncio
async def test_reconcile_multi_agent_child_stale_update_does_not_invoke_parent_rollup(monkeypatch):
    from app import repositories

    child_run = {
        "id": "run-child",
        "tenant_id": "default",
        "copied_from_run_id": "run-parent",
        "trace_id": "trace-child",
        "status": "succeeded",
        "input_json": {
            "input": {
                "multi_agent_dispatch": {
                    "parent_run_id": "run-parent",
                    "parent_step_id": "step-code",
                    "step_key": "code",
                    "dispatch_id": "dispatch-code",
                }
            }
        },
    }
    parent_step = {
        "id": "step-code",
        "run_id": "run-parent",
        "step_key": "code",
        "step_kind": "agent",
        "status": "running",
        "title": "Code",
        "role": "coder",
        "sequence": 2,
        "payload_json": {
            "dispatch_id": "dispatch-code",
            "dispatch_state": "handed_off",
            "dispatch_child_run_id": "run-child",
        },
    }

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        async def fetchone(self):
            return self.row

    class FakeConnection:
        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            if normalized.startswith("select id, tenant_id, copied_from_run_id"):
                return Cursor(row=child_run)
            if normalized.startswith("select id, run_id, step_key") and "from run_steps" in normalized:
                return Cursor(row=parent_step)
            if normalized.startswith("update run_steps"):
                return Cursor(row=None)
            raise AssertionError(f"unexpected sql: {normalized}")

    async def fail_finalize(conn, **kwargs):
        raise AssertionError("stale update must not finalize parent")

    monkeypatch.setattr("app.repositories.finalize_multi_agent_parent_run_if_ready", fail_finalize)

    result = await repositories.reconcile_multi_agent_child_run_terminal_state(
        FakeConnection(),
        tenant_id="default",
        child_run_id="run-child",
        child_status="succeeded",
        result_json={"message": "child output"},
    )

    assert result is None
```

- [ ] **Step 2: Run hook RED tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_reconcile_multi_agent_child_success_invokes_parent_rollup tests/test_run_control_routes.py::test_reconcile_multi_agent_child_stale_update_does_not_invoke_parent_rollup -q --basetemp .pytest-tmp
```

Expected: first test fails because reconciliation does not invoke parent rollup.

- [ ] **Step 3: Implement the hook**

In `reconcile_multi_agent_child_run_terminal_state()`, after audit append succeeds and before returning, add:

```python
    await finalize_multi_agent_parent_run_if_ready(
        conn,
        tenant_id=tenant_id,
        parent_run_id=parent_run_id,
        triggered_by_child_run_id=child_run_id,
    )
```

Do not include the finalization result in the existing reconciliation response,
so existing response contracts remain stable.

- [ ] **Step 4: Run hook GREEN tests**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py::test_reconcile_multi_agent_child_success_invokes_parent_rollup tests/test_run_control_routes.py::test_reconcile_multi_agent_child_stale_update_does_not_invoke_parent_rollup -q --basetemp .pytest-tmp
```

Expected: pass.

---

### Task 4: Public Projection Regression

**Files:**
- Modify: `tests/test_routes.py`

- [ ] **Step 1: Add projection test**

Add a test near existing multi-agent snapshot tests:

```python
def test_multi_agent_snapshot_redacts_parent_finalized_private_payload():
    from app.auth import AuthPrincipal
    from app.routes.runs import multi_agent_snapshot_from_steps

    principal = AuthPrincipal(user_id="user-a", display_name="User", tenant_id="default", roles=["user"], source="test")
    snapshot = multi_agent_snapshot_from_steps(
        "run-parent",
        [
            {
                "id": "step-code",
                "run_id": "run-parent",
                "step_key": "code",
                "step_kind": "agent",
                "status": "succeeded",
                "title": "Code",
                "role": "coder",
                "sequence": 1,
                "payload_json": {
                    "depends_on": [],
                    "dispatch_state": "completed",
                    "dispatch_child_run_id": "run-child",
                    "output": "safe output",
                    "private_payload": "hidden",
                    "storage_key": "tenant/default/private/object",
                    "worker_path": "/app/private.py",
                    "command_sha256": "a" * 64,
                },
                "started_at": None,
                "finished_at": None,
                "created_at": None,
                "updated_at": None,
            }
        ],
        principal=principal,
    )

    assert snapshot["counts"]["succeeded"] == 1
    dumped = json.dumps(snapshot, ensure_ascii=False)
    assert "safe output" in dumped
    assert "private_payload" not in dumped
    assert "storage_key" not in dumped
    assert "/app/private.py" not in dumped
    assert "command_sha256" not in dumped
```

Ensure `json` is imported in `tests/test_routes.py`; if it is already imported,
do not add a duplicate import.

- [ ] **Step 2: Run projection test**

Run:

```powershell
python -m pytest tests/test_routes.py::test_multi_agent_snapshot_redacts_parent_finalized_private_payload -q --basetemp .pytest-tmp
```

Expected: pass after existing projection redaction is confirmed, or fail with a concrete leaked key that must be fixed in `run_step_response()`.

---

### Task 5: Focused Verification And Roadmap Evidence

**Files:**
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`

- [ ] **Step 1: Run focused local tests**

Run:

```powershell
python -m pytest tests/test_control_plane_contracts.py::test_standard_event_taxonomy_covers_g2_lifecycle_events tests/test_worker.py::test_worker_retries_multi_agent_parent_rollup_after_child_transaction_commit tests/test_worker.py::test_worker_reconciles_multi_agent_child_after_success tests/test_worker.py::test_worker_reconciles_multi_agent_child_after_failure tests/test_worker.py::test_worker_reconciles_multi_agent_child_after_cancel tests/test_run_control_routes.py::test_cancel_run_finalizes_multi_agent_parent_after_cancel_propagation tests/test_run_control_routes.py::test_admin_cancel_run_finalizes_multi_agent_parent_after_cancel_propagation tests/test_run_control_routes.py::test_finalize_multi_agent_parent_run_success_writes_public_result_event_and_audit tests/test_run_control_routes.py::test_finalize_multi_agent_parent_run_failure_and_cancel_statuses tests/test_run_control_routes.py::test_finalize_multi_agent_parent_run_blocks_active_children_and_non_multi_agent tests/test_run_control_routes.py::test_finalize_multi_agent_parent_run_blocks_open_dispatch_state tests/test_run_control_routes.py::test_finalize_multi_agent_parent_run_blocks_missing_configured_step tests/test_run_control_routes.py::test_finalize_multi_agent_parent_run_blocks_duplicate_or_malformed_configured_steps tests/test_run_control_routes.py::test_finalize_multi_agent_parent_run_blocks_ordinary_copied_run_and_uses_skip_locked tests/test_run_control_routes.py::test_reconcile_multi_agent_child_success_invokes_parent_rollup tests/test_run_control_routes.py::test_reconcile_multi_agent_child_stale_update_does_not_invoke_parent_rollup tests/test_routes.py::test_multi_agent_snapshot_redacts_parent_finalized_private_payload -q --basetemp .pytest-tmp
```

Expected: pass.

- [ ] **Step 2: Run broader affected suites**

Run:

```powershell
python -m pytest tests/test_run_control_routes.py tests/test_admin_run_detail.py tests/test_worker.py tests/test_routes.py tests/test_control_plane_contracts.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp
```

Expected: pass.

- [ ] **Step 3: Run compile check**

Run:

```powershell
python -m compileall -q app tools scripts
```

Expected: exit code 0.

- [ ] **Step 4: Update roadmap after implementation verification**

Append a new section after `P2 Multi-Agent Parent Cancel Propagation`:

```markdown
### P2 Multi-Agent Parent Terminal Rollup

Status: implemented locally as the parent lifecycle closure follow-up to child
handoff, child terminal reconciliation, and parent cancel propagation.

This slice finalizes a same-tenant multi-agent parent run once all persisted
server-owned parent steps are terminal and no active handed-off child run
remains. Parent result, hidden event, and audit payloads expose only public-safe
step summaries and counts. The slice does not start an autonomous scheduler,
polling subagent dispatcher, new worker process, sandbox/tool privilege
expansion, frontend entry, or DB migration.
```

After 211 deployment, extend the section with exact commit, image id, labels,
local verification output, review result, and 211 smoke evidence.

---

### Task 6: Review, Commit, PR, And 211 Deployment

**Files:**
- All files touched by previous tasks.

- [ ] **Step 1: Run full local verification**

Run:

```powershell
python -m pytest -q --basetemp .pytest-tmp
python -m compileall -q app tools scripts
git diff --check
```

Expected: tests pass, compile exits 0, diff check exits 0.

- [ ] **Step 2: Run multi-agent review**

Use the available inherited-configuration review path. Record that explicit
model and reasoning-effort fields were not externally configurable if the tool
does not expose them. Validate every finding against current PRD, roadmap,
guardrails, code, and tests before changing code.

- [ ] **Step 3: Commit**

Run:

```powershell
git status --short
git add app/control_plane_contracts.py app/repositories.py app/worker.py app/routes/runs.py app/routes/admin_runs.py tests/test_control_plane_contracts.py tests/test_run_control_routes.py tests/test_admin_run_detail.py tests/test_worker.py tests/test_routes.py docs/superpowers/specs/2026-06-06-p2-multi-agent-parent-terminal-rollup-design.md docs/superpowers/plans/2026-06-06-p2-multi-agent-parent-terminal-rollup.md docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md
git commit -m "feat: finalize multi-agent parent runs"
```

Expected: commit succeeds.

- [ ] **Step 4: Create PR and merge after review gate**

Use the repository's existing GitHub workflow. Keep the PR private-repo safe:
no real `.env`, secrets, runtime private payload, or personal paths in the PR
body or files.

- [ ] **Step 5: Deploy and smoke on 211**

On 211 only, sync the merged main source snapshot, set `.codex-source-revision`
and `.codex-source-note` to the new commit and
`p2-multi-agent-parent-terminal-rollup`, build or rebase the runtime image as
appropriate, and recreate API/worker with:

```bash
sudo -n env AI_PLATFORM_IMAGE=ai-platform:<short-sha> docker compose --env-file <211-runtime-env-path> -f <211-repo-local-compose-file> up -d --no-build ai-platform-api ai-platform-worker
```

Smoke evidence must include:

- API and frontend proxy `/api/ai/health`;
- API/worker image id and matching `ai-platform.source-revision`;
- remote source `.codex-source-revision` and `.codex-source-note`;
- a live route or in-container repository smoke proving terminal child
  reconciliation finalizes a parent run;
- public projection redaction for private payload, storage key, runtime path,
  command fingerprint, and secret-like values;
- clean recent API/worker logs;
- smoke tenant cleanup.
