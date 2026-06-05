# P1 Admin Runtime Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin-only runtime overview snapshot API for queue, run, sandbox, and basic observability state.

**Architecture:** Keep the API route thin and reuse existing queue and sandbox projection paths. Add repository helpers for tenant-scoped run and observability aggregates, then assemble a redacted same-tenant overview response in `app.routes.admin_runtime`.

**Tech Stack:** FastAPI, Pydantic-style dict responses, PostgreSQL SQL helpers in `app.repositories`, pytest, TestClient, async fake repository tests.

---

## File Structure

- Modify `app/repositories.py`: add two aggregate helpers:
  - `get_admin_runtime_run_summary(conn, *, tenant_id: str, limit: int = 10) -> dict[str, Any]`
  - `get_admin_runtime_observability_summary(conn, *, tenant_id: str) -> dict[str, Any]`
- Modify `app/routes/admin_runtime.py`: add shared sandbox overview helper and `GET /admin/runtime/overview`.
- Modify `tests/test_admin_runtime_routes.py`: add route-level tests for access control, contract shape, cleanup order, fail-closed cleanup, tenant scoping, and redaction.
- Modify `tests/test_repositories.py`: add repository-helper tests for SQL scope and stable numeric defaults.
- Reference `docs/superpowers/specs/2026-06-05-p1-admin-runtime-overview-design.md`: source design for the slice.

## Task 1: Repository Aggregate Helpers

**Files:**
- Modify: `app/repositories.py`
- Test: `tests/test_repositories.py`

- [ ] **Step 1: Write failing repository tests**

Append tests similar to:

```python
@pytest.mark.asyncio
async def test_get_admin_runtime_run_summary_counts_statuses_and_redacts_failures():
    class SummaryCursor:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows or []

        async def fetchone(self):
            return self.row

        async def fetchall(self):
            return self.rows

    class SummaryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((sql, params))
            if "group by status" in sql.lower():
                return SummaryCursor(rows=[
                    {"status": "queued", "count": 2},
                    {"status": "running", "count": 1},
                    {"status": "failed", "count": 1},
                ])
            if "from runs" in sql.lower() and "error_code" in sql.lower():
                return SummaryCursor(rows=[
                    {
                        "id": "run-failed",
                        "user_id": "user-a",
                        "agent_id": "qa-word-review",
                        "error_code": "executor_failure token=run-code-token",
                        "error_message": "failed token=run-message-token /var/lib/ai-platform/x",
                        "created_at": None,
                    }
                ])
            raise AssertionError(sql)

    summary = await repositories.get_admin_runtime_run_summary(
        SummaryConnection(),
        tenant_id="tenant-a",
        limit=5,
    )

    assert summary["total"] == 4
    assert summary["active"] == 3
    assert summary["terminal"] == 1
    assert summary["by_status"] == {"queued": 2, "running": 1, "failed": 1}
    assert "skill_id" not in summary["recent_failures"][0]
    assert summary["recent_failures"][0]["error_code"] == "executor_failure token=[redacted-secret]"
    assert summary["recent_failures"][0]["error_message"] == ""
    assert "qa-file-reviewer" not in str(summary)
    assert "run-code-token" not in str(summary)
    assert "run-message-token" not in str(summary)
    assert "/var/lib/ai-platform" not in str(summary)
```

Add a second test:

```python
@pytest.mark.asyncio
async def test_get_admin_runtime_observability_summary_coerces_nulls_to_defaults():
    class SummaryCursor:
        def __init__(self, row):
            self.row = row

        async def fetchone(self):
            return self.row

    class SummaryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            self.calls.append((sql, params))
            assert params == ("tenant-a",)
            assert "tenant_id = %s" in sql
            return SummaryCursor({
                "event_count": None,
                "artifact_count": None,
                "error_count": None,
                "error_types": None,
                "avg_latency_ms": None,
                "max_latency_ms": None,
                "input_token_count": None,
                "output_token_count": None,
                "total_token_count": None,
                "estimated_cost_minor": None,
            })

    summary = await repositories.get_admin_runtime_observability_summary(
        SummaryConnection(),
        tenant_id="tenant-a",
    )

    assert summary == {
        "event_count": 0,
        "artifact_count": 0,
        "error_count": 0,
        "error_types": {},
        "latency_ms": {"avg": None, "max": None},
        "token_counts": {"input": 0, "output": 0, "total": 0},
        "estimated_cost_minor": 0,
    }
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
python -m pytest tests/test_repositories.py::test_get_admin_runtime_run_summary_counts_statuses_and_redacts_failures tests/test_repositories.py::test_get_admin_runtime_observability_summary_coerces_nulls_to_defaults -q --basetemp .pytest-tmp
```

Expected: both tests fail because the repository helper functions do not exist.

- [ ] **Step 3: Implement repository helpers**

Add helper code in `app/repositories.py` near `list_admin_runs`:

```python
ACTIVE_RUN_STATUSES = {"queued", "running"}
TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled"}


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


async def get_admin_runtime_run_summary(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    limit: int = 10,
) -> dict[str, Any]:
    status_cursor = await conn.execute(
        """
        select status, count(*) as count
        from runs
        where tenant_id = %s
        group by status
        """,
        (tenant_id,),
    )
    status_rows = list(await status_cursor.fetchall())
    by_status = {
        str(row["status"]): _coerce_int(row["count"])
        for row in status_rows
        if row.get("status") is not None
    }
    failure_cursor = await conn.execute(
        """
        select id, user_id, agent_id, error_code, error_message, created_at
        from runs
        where tenant_id = %s
          and status = 'failed'
        order by created_at desc
        limit %s
        """,
        (tenant_id, limit),
    )
    failure_rows = list(await failure_cursor.fetchall())
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "active": sum(by_status.get(status, 0) for status in ACTIVE_RUN_STATUSES),
        "terminal": sum(by_status.get(status, 0) for status in TERMINAL_RUN_STATUSES),
        "recent_failures": [
            {
                "run_id": row["id"],
                "user_id": row.get("user_id"),
                "agent_id": row.get("agent_id"),
                "error_code": sanitize_public_text(row.get("error_code")) or None,
                "error_message": sanitize_public_text(row.get("error_message")),
                "created_at": row.get("created_at"),
            }
            for row in failure_rows
        ],
    }


async def get_admin_runtime_observability_summary(
    conn: AsyncConnection,
    *,
    tenant_id: str,
) -> dict[str, Any]:
    cursor = await conn.execute(
        """
        with event_summary as (
          select
            count(*) as event_count,
            count(*) filter (where error_code is not null and error_code <> '') as event_error_count,
            avg(latency_ms) filter (where latency_ms is not null) as avg_latency_ms,
            max(latency_ms) as max_latency_ms
          from run_events
          where tenant_id = %s
        ),
        artifact_summary as (
          select count(*) as artifact_count
          from artifacts
          where tenant_id = %s
        ),
        run_totals as (
          select
            count(*) filter (where error_code is not null and error_code <> '') as run_error_count,
            coalesce(sum(input_token_count), 0) as run_input_token_count,
            coalesce(sum(output_token_count), 0) as run_output_token_count,
            coalesce(sum(total_token_count), 0) as run_total_token_count,
            coalesce(sum(estimated_cost_minor), 0) as run_estimated_cost_minor
          from runs
          where tenant_id = %s
        ),
        error_types as (
          select coalesce(jsonb_object_agg(error_code, error_count), '{}'::jsonb) as error_types
          from (
            select error_code, count(*) as error_count
            from (
              select error_code
              from runs
              where tenant_id = %s
                and error_code is not null
                and error_code <> ''
              union all
              select error_code
              from run_events
              where tenant_id = %s
                and error_code is not null
                and error_code <> ''
            ) all_errors
            group by error_code
          ) errors
        )
        select
          event_summary.event_count,
          artifact_summary.artifact_count,
          run_totals.run_error_count + event_summary.event_error_count as error_count,
          error_types.error_types,
          event_summary.avg_latency_ms,
          event_summary.max_latency_ms,
          run_totals.run_input_token_count as input_token_count,
          run_totals.run_output_token_count as output_token_count,
          run_totals.run_total_token_count as total_token_count,
          run_totals.run_estimated_cost_minor as estimated_cost_minor
        from event_summary, artifact_summary, run_totals, error_types
        """,
        (tenant_id, tenant_id, tenant_id, tenant_id, tenant_id),
    )
    row = await cursor.fetchone() or {}
    error_types = row.get("error_types") if isinstance(row.get("error_types"), dict) else {}
    return {
        "event_count": _coerce_int(row.get("event_count")),
        "artifact_count": _coerce_int(row.get("artifact_count")),
        "error_count": _coerce_int(row.get("error_count")),
        "error_types": {str(key): _coerce_int(value) for key, value in error_types.items()},
        "latency_ms": {
            "avg": _coerce_int(row.get("avg_latency_ms")) if row.get("avg_latency_ms") is not None else None,
            "max": _coerce_int(row.get("max_latency_ms")) if row.get("max_latency_ms") is not None else None,
        },
        "token_counts": {
            "input": _coerce_int(row.get("input_token_count")),
            "output": _coerce_int(row.get("output_token_count")),
            "total": _coerce_int(row.get("total_token_count")),
        },
        "estimated_cost_minor": _coerce_int(row.get("estimated_cost_minor")),
    }
```

- [ ] **Step 4: Run repository tests to verify GREEN**

Run:

```powershell
python -m pytest tests/test_repositories.py::test_get_admin_runtime_run_summary_counts_statuses_and_redacts_failures tests/test_repositories.py::test_get_admin_runtime_observability_summary_coerces_nulls_to_defaults -q --basetemp .pytest-tmp
```

Expected: both tests pass.

## Task 2: Admin Runtime Overview Route

**Files:**
- Modify: `app/routes/admin_runtime.py`
- Test: `tests/test_admin_runtime_routes.py`

- [ ] **Step 1: Write failing route tests**

Add tests similar to:

```python
def test_admin_runtime_overview_requires_admin(monkeypatch):
    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/overview", headers=user_headers())

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_admin"
```

Add an admin contract test:

```python
def test_admin_runtime_overview_returns_same_tenant_snapshot(monkeypatch):
    calls = []

    class FakeProvider:
        async def cleanup_orphan_containers(self, filters, *, reason):
            calls.append(("provider_cleanup", filters, reason))
            return []

        async def list_runtime_containers(self, filters):
            calls.append(("containers", filters))
            return [
                ContainerStatus(
                    container_id="exec-run-a",
                    container_name="executor-exec-run-a",
                    provider="docker",
                    status="running",
                    tenant_id="default",
                    workspace_id="workspace-a",
                    user_id="user-a",
                    session_id="session-a",
                    run_id="run-a",
                    sandbox_mode="ephemeral",
                )
            ]

    @asynccontextmanager
    async def overview_transaction():
        yield object()

    async def fake_cleanup_expired_sandbox_runtime_leases(conn, *, tenant_id=None, reason="expired", **kwargs):
        calls.append(("runtime_cleanup", tenant_id, reason))
        return []

    async def fake_cleanup_expired_sandbox_leases(conn, *, tenant_id=None, reason="expired"):
        calls.append(("db_cleanup", tenant_id, reason))
        return []

    async def fake_list_sandbox_leases(conn, *, tenant_id, status=None, limit=100):
        calls.append(("leases", tenant_id, status, limit))
        if status == "active":
            return [{"id": "lease-active", "tenant_id": "default", "status": "active"}]
        return [
            {"id": "lease-active", "tenant_id": "default", "status": "active"},
            {"id": "lease-released", "tenant_id": "default", "status": "released"},
            {"id": "lease-expired", "tenant_id": "default", "status": "expired"},
        ]

    async def fake_get_queue_status():
        calls.append(("queue_status",))
        return {"depths": {"queued": 2}}

    async def fake_get_queue_insight(tenant_id):
        calls.append(("queue_insight", tenant_id))
        return {"tenant_id": tenant_id, "reason": "workers_busy"}

    async def fake_run_summary(conn, *, tenant_id, limit=10):
        calls.append(("run_summary", tenant_id, limit))
        return {
            "total": 3,
            "by_status": {"queued": 1, "running": 1, "failed": 1},
            "active": 2,
            "terminal": 1,
            "recent_failures": [],
        }

    async def fake_observability_summary(conn, *, tenant_id):
        calls.append(("observability", tenant_id))
        return {
            "event_count": 4,
            "artifact_count": 1,
            "error_count": 1,
            "error_types": {"executor_failure": 1},
            "latency_ms": {"avg": 20, "max": 30},
            "token_counts": {"input": 10, "output": 12, "total": 22},
            "estimated_cost_minor": 7,
        }

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.admin_runtime.create_container_provider", lambda: FakeProvider())
    monkeypatch.setattr("app.routes.admin_runtime.transaction", overview_transaction)
    monkeypatch.setattr("app.routes.admin_runtime.cleanup_expired_sandbox_runtime_leases", fake_cleanup_expired_sandbox_runtime_leases)
    monkeypatch.setattr("app.routes.admin_runtime.repositories.cleanup_expired_sandbox_leases", fake_cleanup_expired_sandbox_leases)
    monkeypatch.setattr("app.routes.admin_runtime.repositories.list_sandbox_leases", fake_list_sandbox_leases)
    monkeypatch.setattr("app.routes.admin_runtime.get_queue_status", fake_get_queue_status)
    monkeypatch.setattr("app.routes.admin_runtime.get_queue_insight", fake_get_queue_insight)
    monkeypatch.setattr("app.routes.admin_runtime.repositories.get_admin_runtime_run_summary", fake_run_summary)
    monkeypatch.setattr("app.routes.admin_runtime.repositories.get_admin_runtime_observability_summary", fake_observability_summary)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/runtime/overview", headers=admin_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "default"
    assert body["queue"]["status"]["depths"]["queued"] == 2
    assert body["queue"]["tenant_insight"]["reason"] == "workers_busy"
    assert body["runs"]["active"] == 2
    assert body["sandbox"]["containers"]["running"] == 1
    assert body["sandbox"]["leases"] == {
        "active": 1,
        "released": 1,
        "expired": 1,
        "history_included": True,
    }
    assert body["observability"]["token_counts"]["total"] == 22
    assert calls[0] == ("provider_cleanup", {"tenant_id": "default"}, "admin_runtime")
```

Add a redaction test where run summary returns a tokenized error and assert the response string does not contain the raw token or runtime path.

- [ ] **Step 2: Run route tests to verify RED**

Run:

```powershell
python -m pytest tests/test_admin_runtime_routes.py::test_admin_runtime_overview_requires_admin tests/test_admin_runtime_routes.py::test_admin_runtime_overview_returns_same_tenant_snapshot -q --basetemp .pytest-tmp
```

Expected: overview route tests fail because `/admin/runtime/overview` does not exist.

- [ ] **Step 3: Implement route**

In `app/routes/admin_runtime.py`:

- Add `_sandbox_runtime_snapshot(provider, principal)` or equivalent to avoid duplicating response assembly.
- Reuse provider orphan cleanup and expired lease cleanup before listing.
- Add `admin_runtime_overview`.
- Include `queue`, `runs`, `sandbox`, and `observability` keys.

Use the same fail-closed error details as `/admin/runtime/containers`.

- [ ] **Step 4: Run route tests to verify GREEN**

Run:

```powershell
python -m pytest tests/test_admin_runtime_routes.py::test_admin_runtime_overview_requires_admin tests/test_admin_runtime_routes.py::test_admin_runtime_overview_returns_same_tenant_snapshot -q --basetemp .pytest-tmp
```

Expected: tests pass.

## Task 3: Focused Verification And Documentation

**Files:**
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`
- Test: focused and full suite commands

- [ ] **Step 1: Update roadmap with P1 progress**

Add a short P1 progress note under "后续顺序" or near the relevant Admin Runtime section:

```markdown
### P1 Admin Runtime Overview Snapshot

Status: in progress on `codex/p1-admin-runtime-overview`.

The first P1 operational slice adds an admin-only overview contract for queue,
run status, sandbox lease/container state, and basic observability aggregates.
It is intentionally smaller than the full Observability / Quality dashboard and
does not start Long Task / Multi-Agent Runtime.
```

- [ ] **Step 2: Run focused tests**

Run:

```powershell
python -m pytest tests/test_admin_runtime_routes.py tests/test_repositories.py -q --basetemp .pytest-tmp
```

Expected: all selected tests pass.

- [ ] **Step 3: Run compile check**

Run:

```powershell
python -m compileall -q app tools scripts
```

Expected: command exits 0.

- [ ] **Step 4: Run full local suite**

Run:

```powershell
python -m pytest -q --basetemp .pytest-tmp
```

Expected: local suite exits 0. If stale `.pytest-tmp` permissions fail, rerun once with a fresh child under `.pytest-tmp`, for example `--basetemp .pytest-tmp\run-p1-overview-<timestamp>`, and report why.

- [ ] **Step 5: Multi-agent review**

Dispatch inherited-configuration review agents because the current delegation tool does not expose explicit `model` or `reasoning_effort` fields. Reviewers must check:

- spec compliance against `docs/superpowers/specs/2026-06-05-p1-admin-runtime-overview-design.md`;
- secret/runtime payload redaction;
- tenant scoping;
- cleanup fail-closed behavior;
- repository aggregate SQL correctness.

Apply only feedback validated against current PRD, roadmap, guardrails, code, and tests.

- [ ] **Step 6: Commit and push branch**

After verification and review:

```powershell
git status --short --branch
git add app/repositories.py app/routes/admin_runtime.py tests/test_admin_runtime_routes.py tests/test_repositories.py docs/superpowers/specs/2026-06-05-p1-admin-runtime-overview-design.md docs/superpowers/plans/2026-06-05-p1-admin-runtime-overview.md docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md
git commit -m "Add admin runtime overview snapshot"
git push -u origin codex/p1-admin-runtime-overview
```

Expected: branch is pushed and ready for PR creation against `main`.

## Self-Review

- Spec coverage: The tasks cover repository aggregates, admin route contract, access control, tenant scoping, sandbox cleanup, redaction, focused tests, full tests, review, commit, and push.
- Open-ended text scan: No vague implementation gaps are present; all function names, files, and commands are concrete.
- Type consistency: `get_admin_runtime_run_summary`, `get_admin_runtime_observability_summary`, and `/admin/runtime/overview` names match the design.
