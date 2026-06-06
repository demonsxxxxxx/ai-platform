# P1 Admin Runtime Admission Backpressure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add admin-only admission and backpressure sections to the existing runtime overview.

**Architecture:** Add one tenant-scoped repository aggregate for active-run admission, then compose a sanitized backpressure projection in `app.routes.admin_runtime` from admission, queue insight, and database pool status. Keep the existing overview route and fail-closed cleanup behavior.

**Tech Stack:** FastAPI, Python dict projections, psycopg repository helpers, pytest.

---

## File Structure

- Modify `app/repositories.py`: add `get_admin_runtime_admission_summary(...)` near existing Admin Runtime aggregate helpers.
- Modify `app/routes/admin_runtime.py`: add sanitizers/builders for admission/backpressure and include them in `/admin/runtime/overview`.
- Modify `tests/test_repositories.py`: add repository helper tests.
- Modify `tests/test_admin_runtime_routes.py`: extend overview tests for the new contract and redaction.
- Update `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md` only after deployment evidence exists.

### Task 1: Repository Admission Summary

**Files:**
- Modify: `tests/test_repositories.py`
- Modify: `app/repositories.py`

- [x] **Step 1: Write failing repository tests**

Add tests near the existing Admin Runtime repository tests:

```python
@pytest.mark.asyncio
async def test_get_admin_runtime_admission_summary_counts_same_tenant_active_users():
    class SummaryCursor:
        def __init__(self, rows):
            self.rows = rows

        async def fetchall(self):
            return self.rows

    class SummaryConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            assert "where tenant_id = %s" in normalized
            assert "status in ('queued', 'running')" in normalized
            assert "input_json" not in normalized
            assert "skill_id" not in normalized
            return SummaryCursor(
                [
                    {"user_id": "user-a", "active": 3},
                    {"user_id": "user-b", "active": 1},
                    {"user_id": None, "active": 1},
                ]
            )

    conn = SummaryConnection()

    summary = await repositories.get_admin_runtime_admission_summary(
        conn,
        tenant_id="tenant-a",
        limit=3,
        top_user_limit=5,
    )

    assert summary == {
        "policy_active": True,
        "max_active_runs_per_user": 3,
        "active_runs": 5,
        "active_users": 2,
        "saturated_users": 1,
        "top_users": [
            {"user_id": "user-a", "active": 3, "saturated": True},
            {"user_id": "user-b", "active": 1, "saturated": False},
        ],
    }
    assert conn.calls[0][1] == ("tenant-a", 5)
```

Add disabled-limit coverage:

```python
@pytest.mark.asyncio
async def test_get_admin_runtime_admission_summary_disables_saturation_when_limit_off():
    class SummaryCursor:
        async def fetchall(self):
            return [{"user_id": "user-a", "active": 7}]

    class SummaryConnection:
        async def execute(self, sql, params):
            return SummaryCursor()

    summary = await repositories.get_admin_runtime_admission_summary(
        SummaryConnection(),
        tenant_id="tenant-a",
        limit=0,
        top_user_limit=10,
    )

    assert summary["policy_active"] is False
    assert summary["max_active_runs_per_user"] == 0
    assert summary["active_runs"] == 7
    assert summary["active_users"] == 1
    assert summary["saturated_users"] == 0
    assert summary["top_users"] == [{"user_id": "user-a", "active": 7, "saturated": False}]
```

- [x] **Step 2: Run RED**

```powershell
python -m pytest tests/test_repositories.py::test_get_admin_runtime_admission_summary_counts_same_tenant_active_users tests/test_repositories.py::test_get_admin_runtime_admission_summary_disables_saturation_when_limit_off -q --basetemp .pytest-tmp\p1-admin-backpressure-red
```

Expected: fail because `get_admin_runtime_admission_summary` does not exist.

- [x] **Step 3: Implement repository helper**

Add near `get_admin_runtime_run_summary`. The implementation uses a totals
query plus a top-users query so `top_user_limit` cannot truncate
`active_runs`, `active_users`, or `saturated_users`:

```python
async def get_admin_runtime_admission_summary(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    limit: int,
    top_user_limit: int = 10,
) -> dict[str, Any]:
    active_limit = max(int(limit), 0)
    top_limit = max(min(int(top_user_limit), 50), 1)
    totals_cursor = await conn.execute(
        """
        with grouped as (
          select user_id, count(*) as active
          from runs
          where tenant_id = %s
            and status in ('queued', 'running')
          group by user_id
        )
        select
          coalesce(sum(active), 0) as active_runs,
          count(*) filter (where user_id is not null) as active_users,
          count(*) filter (where user_id is not null and %s > 0 and active >= %s) as saturated_users
        from grouped
        """,
        (tenant_id, active_limit, active_limit),
    )
    totals = await totals_cursor.fetchone() or {}
    top_cursor = await conn.execute(
        """
        select user_id, count(*) as active
        from runs
        where tenant_id = %s
          and status in ('queued', 'running')
          and user_id is not null
        group by user_id
        order by count(*) desc, user_id asc
        limit %s
        """,
        (tenant_id, top_limit),
    )
    top_rows = list(await top_cursor.fetchall())
    top_users = [
        {
            "user_id": str(row["user_id"]),
            "active": _coerce_int(row["active"]),
            "saturated": active_limit > 0 and _coerce_int(row["active"]) >= active_limit,
        }
        for row in top_rows
    ]
    return {
        "policy_active": active_limit > 0,
        "max_active_runs_per_user": active_limit,
        "active_runs": _coerce_int(totals.get("active_runs")),
        "active_users": _coerce_int(totals.get("active_users")),
        "saturated_users": _coerce_int(totals.get("saturated_users")),
        "top_users": top_users,
    }
```

- [x] **Step 4: Run GREEN**

```powershell
python -m pytest tests/test_repositories.py::test_get_admin_runtime_admission_summary_counts_same_tenant_active_users tests/test_repositories.py::test_get_admin_runtime_admission_summary_disables_saturation_when_limit_off -q --basetemp .pytest-tmp\p1-admin-backpressure-repo-green
```

Expected: `2 passed`.

### Task 2: Admin Runtime Projection

**Files:**
- Modify: `tests/test_admin_runtime_routes.py`
- Modify: `app/routes/admin_runtime.py`

- [x] **Step 1: Extend route tests**

In `test_admin_runtime_overview_returns_same_tenant_snapshot`, add a fake admission helper:

```python
async def fake_admission_summary(conn, *, tenant_id, limit, top_user_limit=10):
    calls.append(("admission", tenant_id, limit, top_user_limit))
    return {
        "policy_active": True,
        "max_active_runs_per_user": limit,
        "active_runs": 3,
        "active_users": 2,
        "saturated_users": 1,
        "top_users": [{"user_id": "user-a", "active": 3, "saturated": True}],
    }
```

Patch it and assert:

```python
assert body["admission"]["saturated_users"] == 1
assert body["backpressure"]["reasons"] == ["active_run_limit_saturated", "workers_busy"]
assert body["backpressure"]["queue"]["reason"] == "workers_busy"
assert body["backpressure"]["database_pool"] == {
    "open": True,
    "requests_waiting": 0,
    "max_waiting": 100,
    "waiting_saturated": False,
}
```

Update the call list to include `("admission", "default", 3, 10)` after
`("observability", "default")`.

Add a focused redaction test by returning queue insight with raw keys and a
pool status with secret fields, then assert these strings do not appear in
`body["backpressure"]`:

```python
assert "ai-platform:runs:queued" not in str(body["backpressure"])
assert "raw_queue_payload" not in str(body["backpressure"])
assert "pool-secret-token" not in str(body["backpressure"])
```

- [x] **Step 2: Run route RED**

```powershell
python -m pytest tests/test_admin_runtime_routes.py::test_admin_runtime_overview_returns_same_tenant_snapshot tests/test_admin_runtime_routes.py::test_admin_runtime_overview_sanitizes_summary_payloads -q --basetemp .pytest-tmp\p1-admin-backpressure-route-red
```

Expected: fail because the route does not yet include `admission` or `backpressure`.

- [x] **Step 3: Implement route builders**

Add helpers in `app/routes/admin_runtime.py`:

```python
def _sanitize_admission_summary(value: object) -> dict[str, object]:
    summary = _sanitize_dict(value)
    users = summary.get("top_users") if isinstance(summary.get("top_users"), list) else []
    return {
        "policy_active": bool(summary.get("policy_active")),
        "max_active_runs_per_user": _coerce_int(summary.get("max_active_runs_per_user")),
        "active_runs": _coerce_int(summary.get("active_runs")),
        "active_users": _coerce_int(summary.get("active_users")),
        "saturated_users": _coerce_int(summary.get("saturated_users")),
        "top_users": [
            {
                "user_id": str(user.get("user_id")),
                "active": _coerce_int(user.get("active")),
                "saturated": bool(user.get("saturated")),
            }
            for user in users
            if isinstance(user, dict) and user.get("user_id")
        ],
    }
```

Add `_backpressure_snapshot(...)` that only copies allowlisted numeric/enum
fields from queue insight and DB pool status.

- [x] **Step 4: Wire overview route**

In the existing summary transaction, call:

```python
admission_summary = await repositories.get_admin_runtime_admission_summary(
    conn,
    tenant_id=principal.tenant_id,
    limit=int(get_settings().max_active_runs_per_user),
)
```

Build queue and pool once, then return `admission` and `backpressure`.

- [x] **Step 5: Run route GREEN**

```powershell
python -m pytest tests/test_admin_runtime_routes.py::test_admin_runtime_overview_returns_same_tenant_snapshot tests/test_admin_runtime_routes.py::test_admin_runtime_overview_sanitizes_summary_payloads -q --basetemp .pytest-tmp\p1-admin-backpressure-route-green
```

Expected: `2 passed`.

### Task 3: Verification, Review, And Deployment

**Files:**
- Modify: `docs/superpowers/plans/2026-06-06-p1-admin-runtime-admission-backpressure.md`
- Modify after 211: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`

- [x] **Step 1: Run focused verification**

```powershell
python -m pytest tests/test_admin_runtime_routes.py tests/test_repositories.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p1-admin-backpressure-focused
python -m compileall -q app tools scripts
git diff --check
```

- [x] **Step 2: Request inherited-configuration review**

Ask for review of:

- same-tenant boundary;
- admission/backpressure field allowlist;
- redaction of queue keys/raw payloads/pool secret-like fields;
- route fail-closed behavior;
- whether the projection is sufficient for P1 Admin Runtime without starting
  Long Task / Multi-Agent Runtime.

- [x] **Step 3: Final local verification**

```powershell
python -m pytest -q --basetemp .pytest-tmp\p1-admin-backpressure-full
git diff --check
```

- [ ] **Step 4: Merge, push, deploy to 211, and smoke**

Deploy through the current 211 runtime path. Verify:

- API and frontend health return `{"status":"ok"}`;
- API/worker image labels and source markers match the deployed commit;
- admin `/api/ai/admin/runtime/overview` includes `admission` and
  `backpressure`;
- ordinary user overview returns `403`;
- response text contains no raw queue payload, runtime private payload,
  storage key, sandbox work directory, or secret-like marker.

## Current Execution Evidence

- RED repository verification passed as expected with
  `python -m pytest tests/test_repositories.py::test_get_admin_runtime_admission_summary_counts_same_tenant_active_users tests/test_repositories.py::test_get_admin_runtime_admission_summary_disables_saturation_when_limit_off -q --basetemp .pytest-tmp\p1-admin-backpressure-red`: both tests failed because
  `get_admin_runtime_admission_summary` did not exist.
- Repository GREEN passed with
  `python -m pytest tests/test_repositories.py::test_get_admin_runtime_admission_summary_counts_same_tenant_active_users tests/test_repositories.py::test_get_admin_runtime_admission_summary_disables_saturation_when_limit_off -q --basetemp .pytest-tmp\p1-admin-backpressure-repo-green-2`
  at `2 passed`.
- Route RED passed as expected with
  `python -m pytest tests/test_admin_runtime_routes.py::test_admin_runtime_overview_returns_same_tenant_snapshot tests/test_admin_runtime_routes.py::test_admin_runtime_overview_sanitizes_summary_payloads -q --basetemp .pytest-tmp\p1-admin-backpressure-route-red`: the overview lacked `admission` and `backpressure`.
- Route GREEN passed with
  `python -m pytest tests/test_admin_runtime_routes.py::test_admin_runtime_overview_returns_same_tenant_snapshot tests/test_admin_runtime_routes.py::test_admin_runtime_overview_sanitizes_summary_payloads -q --basetemp .pytest-tmp\p1-admin-backpressure-route-green`
  at `2 passed`.
- Focused verification passed with
  `python -m pytest tests/test_admin_runtime_routes.py tests/test_repositories.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p1-admin-backpressure-focused`
  at `133 passed`; `python -m compileall -q app tools scripts` passed; `git diff --check` exited clean.
- A follow-up contract correction kept `worker_available` visible as
  `backpressure.queue.reason` but omitted it from `backpressure.reasons`,
  because it is not a pressure state. The targeted regression
  `python -m pytest tests/test_admin_runtime_routes.py::test_admin_runtime_backpressure_omits_worker_available_from_reasons -q --basetemp .pytest-tmp\p1-admin-worker-available`
  passed at `1 passed`.
- Fresh focused verification after the review feedback coverage updates passed
  with
  `python -m pytest tests/test_admin_runtime_routes.py tests/test_repositories.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\p1-admin-backpressure-focused-3`
  at `135 passed`; `python -m compileall -q app tools scripts` passed;
  `git diff --check` exited clean.
- Inherited-configuration multi-agent review found no Critical or Important
  issues. It identified two Low residual coverage items: separately simulate
  `get_queue_insight` failure and inject a literal `storage_key` marker into
  redaction coverage. Both were addressed in tests, and
  `python -m pytest tests/test_admin_runtime_routes.py::test_admin_runtime_overview_sanitizes_summary_payloads tests/test_admin_runtime_routes.py::test_admin_runtime_overview_does_not_mask_queue_failure tests/test_admin_runtime_routes.py::test_admin_runtime_backpressure_omits_worker_available_from_reasons -q --basetemp .pytest-tmp\p1-admin-review-feedback`
  passed at `4 passed`. The review tool did not expose explicit model or
  reasoning-effort fields, so this is recorded as inherited/default
  configuration review only.
- Final local verification passed with
  `python -m pytest -q --basetemp .pytest-tmp\p1-admin-backpressure-full-2`
  at `1074 passed, 6 skipped, 2 warnings`.
