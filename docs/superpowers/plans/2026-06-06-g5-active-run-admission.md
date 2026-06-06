# G5 Active Run Admission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serialize per-user active-run admission so concurrent create/copy/retry/resume/chat requests cannot exceed `max_active_runs_per_user`.

**Architecture:** Add a repository-level transaction advisory-lock helper and route all user-created run admission through it before the run insert in the same transaction. Keep Redis worker queue quota unchanged; this slice hardens DB-backed run creation admission only.

**Tech Stack:** Python, FastAPI route functions, psycopg async repository helpers, pytest.

---

## File Structure

- Modify `app/repositories.py`: add `enforce_user_active_run_admission(...)` near `count_active_runs_for_user(...)`.
- Modify `app/routes/chat.py`: change route admission helper to call the repository lock helper.
- Modify `app/routes/runs.py`: change route admission helper to call the repository lock helper and add the missing copy-run admission.
- Modify `tests/test_repositories.py`: add RED/GREEN SQL-order tests for the lock helper.
- Modify `tests/test_chat_routes.py`: update active-limit tests to patch/assert the new helper.
- Modify `tests/test_routes.py`: update create-run active-limit tests to patch/assert the new helper.
- Modify `tests/test_run_control_routes.py`: update retry/resume active-limit tests and add copy-run active-limit coverage.
- Modify `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`: record closure only after review, full local verification, PR/merge, and 211 smoke.

### Task 1: RED Repository Admission Tests

**Files:**
- Modify: `tests/test_repositories.py`

- [ ] **Step 1: Add lock-before-count RED test**

Add this test near `test_count_active_runs_for_user_counts_queued_and_running_only`:

```python
@pytest.mark.asyncio
async def test_enforce_user_active_run_admission_locks_before_counting():
    class CountCursor:
        async def fetchone(self):
            return {"count": 2}

    class EmptyCursor:
        async def fetchone(self):
            return None

    class AdmissionConnection:
        def __init__(self):
            self.calls = []

        async def execute(self, sql, params):
            normalized = " ".join(sql.split())
            self.calls.append((normalized, params))
            if "count(*) as count" in normalized:
                return CountCursor()
            return EmptyCursor()

    conn = AdmissionConnection()

    observed = await enforce_user_active_run_admission(
        conn,
        tenant_id="tenant-a",
        user_id="user-a",
        limit=3,
    )

    assert observed == 2
    assert "pg_advisory_xact_lock" in conn.calls[0][0]
    assert conn.calls[0][1] == ("tenant-a:user-a",)
    assert "status in ('queued', 'running')" in conn.calls[1][0]
    assert conn.calls[1][1] == ("tenant-a", "user-a")
```

- [ ] **Step 2: Add rejection and disabled-limit RED tests**

Add:

```python
@pytest.mark.asyncio
async def test_enforce_user_active_run_admission_rejects_at_limit():
    class CountCursor:
        async def fetchone(self):
            return {"count": 3}

    class AdmissionConnection:
        async def execute(self, sql, params):
            return CountCursor() if "count(*)" in " ".join(sql.split()) else CountCursor()

    with pytest.raises(RepositoryConflictError, match="user_active_run_limit_exceeded"):
        await enforce_user_active_run_admission(
            AdmissionConnection(),
            tenant_id="tenant-a",
            user_id="user-a",
            limit=3,
        )


@pytest.mark.asyncio
async def test_enforce_user_active_run_admission_skips_disabled_limit():
    class AdmissionConnection:
        async def execute(self, sql, params):
            raise AssertionError("disabled admission must not lock or count")

    observed = await enforce_user_active_run_admission(
        AdmissionConnection(),
        tenant_id="tenant-a",
        user_id="user-a",
        limit=0,
    )

    assert observed == 0
```

- [ ] **Step 3: Run RED**

Run:

```powershell
python -m pytest tests/test_repositories.py::test_enforce_user_active_run_admission_locks_before_counting tests/test_repositories.py::test_enforce_user_active_run_admission_rejects_at_limit tests/test_repositories.py::test_enforce_user_active_run_admission_skips_disabled_limit -q --basetemp .pytest-tmp\g5-active-admission-red
```

Expected: fail because `enforce_user_active_run_admission` is not implemented/imported.

### Task 2: Implement Repository Admission Helper

**Files:**
- Modify: `app/repositories.py`
- Modify: `tests/test_repositories.py`

- [ ] **Step 1: Import helper in tests**

Add `enforce_user_active_run_admission` and `RepositoryConflictError` to the
existing import list in `tests/test_repositories.py`.

- [ ] **Step 2: Add helper implementation**

Add this helper next to `count_active_runs_for_user(...)`:

```python
async def enforce_user_active_run_admission(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    limit: int,
) -> int:
    limit = int(limit)
    if limit <= 0:
        return 0
    lock_scope = f"{tenant_id}:{user_id}"
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
        (lock_scope,),
    )
    active_count = await count_active_runs_for_user(conn, tenant_id=tenant_id, user_id=user_id)
    if active_count >= limit:
        raise RepositoryConflictError("user_active_run_limit_exceeded")
    return active_count
```

- [ ] **Step 3: Run GREEN repository tests**

Run:

```powershell
python -m pytest tests/test_repositories.py::test_enforce_user_active_run_admission_locks_before_counting tests/test_repositories.py::test_enforce_user_active_run_admission_rejects_at_limit tests/test_repositories.py::test_enforce_user_active_run_admission_skips_disabled_limit tests/test_repositories.py::test_count_active_runs_for_user_counts_queued_and_running_only -q --basetemp .pytest-tmp\g5-active-admission-repo-green
```

Expected: pass.

### Task 3: Route All User Run Creation Through Admission Helper

**Files:**
- Modify: `app/routes/chat.py`
- Modify: `app/routes/runs.py`
- Modify: `tests/test_chat_routes.py`
- Modify: `tests/test_routes.py`
- Modify: `tests/test_run_control_routes.py`

- [ ] **Step 1: Update chat route helper**

Change `app/routes/chat.py::enforce_user_active_run_limit`:

```python
async def enforce_user_active_run_limit(conn, *, tenant_id: str, user_id: str) -> None:
    limit = int(get_settings().max_active_runs_per_user)
    await repositories.enforce_user_active_run_admission(
        conn,
        tenant_id=tenant_id,
        user_id=user_id,
        limit=limit,
    )
```

- [ ] **Step 2: Update runs route helper**

Apply the same change to `app/routes/runs.py::enforce_user_active_run_limit`.

- [ ] **Step 3: Add missing copy-run admission**

In `app/routes/runs.py::copy_run`, call:

```python
await enforce_user_active_run_limit(
    conn,
    tenant_id=principal.tenant_id,
    user_id=principal.user_id,
)
```

before `repositories.copy_run_as_new_task(...)`.

- [ ] **Step 4: Update existing route tests**

In active-run admission tests, patch
`repositories.enforce_user_active_run_admission` instead of
`count_active_runs_for_user`, and assert it receives the configured limit.

Example for `tests/test_routes.py::test_create_run_rejects_when_user_active_run_limit_is_reached`:

```python
async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
    calls.append(("admit", tenant_id, user_id, limit))
    raise RepositoryConflictError("user_active_run_limit_exceeded")
```

Expected call:

```python
assert calls == ["resolve", ("admit", "tenant-a", "user-limit", 3)]
```

- [ ] **Step 5: Add copy-run active limit route test**

Add to `tests/test_run_control_routes.py`:

```python
def test_copy_run_rejects_when_user_active_run_limit_is_reached(monkeypatch):
    calls = []

    class LimitSettings:
        max_active_runs_per_user = 1

    async def fake_enforce_user_active_run_admission(conn, *, tenant_id, user_id, limit):
        calls.append(("admit", tenant_id, user_id, limit))
        raise repositories.RepositoryConflictError("user_active_run_limit_exceeded")

    async def fail_copy_run_as_new_task(*args, **kwargs):
        calls.append(("copy", kwargs))
        raise AssertionError("copy must not create a copied run after admission rejection")

    async def fail_enqueue_run(payload):
        calls.append(("enqueue", payload))
        raise AssertionError("copy must not enqueue after admission rejection")

    monkeypatch.setattr("app.auth.get_settings", auth_settings)
    monkeypatch.setattr("app.routes.runs.get_settings", lambda: LimitSettings())
    monkeypatch.setattr("app.routes.runs.transaction", fake_transaction)
    monkeypatch.setattr(
        "app.routes.runs.repositories.enforce_user_active_run_admission",
        fake_enforce_user_active_run_admission,
        raising=False,
    )
    monkeypatch.setattr("app.routes.runs.repositories.copy_run_as_new_task", fail_copy_run_as_new_task, raising=False)
    monkeypatch.setattr("app.routes.runs.enqueue_run", fail_enqueue_run)
    client = TestClient(create_app())

    response = client.post("/api/ai/runs/run-source/copy", headers=headers())

    assert response.status_code == 409
    assert response.json()["detail"] == "user_active_run_limit_exceeded"
    assert calls == [("admit", "default", "user-a", 1)]
```

- [ ] **Step 6: Run focused route tests**

Run:

```powershell
python -m pytest tests/test_chat_routes.py::test_chat_stream_rejects_when_user_active_run_limit_is_reached tests/test_routes.py::test_create_run_rejects_when_user_active_run_limit_is_reached tests/test_run_control_routes.py::test_copy_run_rejects_when_user_active_run_limit_is_reached tests/test_run_control_routes.py::test_retry_run_rejects_when_user_active_run_limit_is_reached -q --basetemp .pytest-tmp\g5-active-admission-routes-green
```

Expected: pass.

### Task 4: Focused Verification, Review, And Deployment Evidence

**Files:**
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`
- Modify: `docs/superpowers/plans/2026-06-06-g5-active-run-admission.md`

- [ ] **Step 1: Run focused suites**

Run:

```powershell
python -m pytest tests/test_repositories.py tests/test_chat_routes.py tests/test_routes.py tests/test_run_control_routes.py tests/test_source_authority_docs.py -q --basetemp .pytest-tmp\g5-active-admission-focused
```

Expected: affected repository and route suites pass.

- [ ] **Step 2: Inherited-configuration review**

Use available multi-agent review if the tool inherits the main session
permissions. Because the current spawn tool does not expose explicit
`model`/`reasoning_effort`, record this as inherited-configuration review.
Ask reviewers to inspect:

- advisory lock scope and transaction lifetime;
- same-tenant/user boundary;
- copy/retry/resume/create/chat coverage;
- whether server-owned multi-agent child handoff should remain out of scope;
- redaction and queue payload side effects.

- [ ] **Step 3: Final local verification before PR/deploy**

Run:

```powershell
python -m compileall -q app tools scripts
python -m pytest -q --basetemp .pytest-tmp\g5-active-admission-full
git diff --check
```

- [ ] **Step 4: 211 smoke after merge**

Deploy the merged main commit to 211. In the API or worker container, run a
temporary Python smoke that:

1. Seeds one smoke tenant/workspace/user/agent/session.
2. Starts two concurrent DB transactions for the same tenant/user with
   `limit=1`.
3. Transaction A calls `enforce_user_active_run_admission(...)`, inserts one
   queued run, then commits.
4. Transaction B calls the same helper while A is open; it must wait for the
   advisory lock and then raise `user_active_run_limit_exceeded`.
5. Cleanup verifies `0` smoke rows remain.

Also verify API/frontend health, API/worker label parity, and recent logs with
no `traceback`, `exception`, `permission denied`, `error`, or `failed` markers.

## Current Execution Evidence

- 2026-06-06 code inspection found `count_active_runs_for_user(...)` is a
  same-tenant application-level check, but not serialized with the subsequent
  run insert.
- 2026-06-06 code inspection found `copy_run` lacks active-run admission while
  create, chat, retry, and resume already have route-level admission checks.
- This plan intentionally leaves Admin Runtime queue/pool observability and
  multi-tenant pressure testing as follow-up G5 slices after admission
  serialization is proven.

