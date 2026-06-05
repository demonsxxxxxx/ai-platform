# P1 Memory Context Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ordinary-user memory policy self-management and admin memory policy inventory projections while keeping cross-session long-term memory fail-closed.

**Architecture:** Extend the existing Memory / Context route and repository contracts instead of adding a new module. Reuse `MemoryPolicyRequest`, `_memory_policy_response`, `_is_memory_admin`, `set_memory_policy`, and existing audit/redaction helpers. Add one repository helper for same-tenant stored policy inventory.

**Tech Stack:** FastAPI, psycopg async repositories, Pydantic models, pytest, existing ai-platform redaction and auth helpers.

---

## File Map

- Modify `tests/test_context_routes.py`: route-level TDD for ordinary-user policy update and admin policy inventory.
- Modify `tests/test_repositories.py`: repository-level TDD for `list_admin_memory_policies`.
- Modify `app/repositories.py`: add `list_admin_memory_policies`.
- Modify `app/routes/context.py`: add `PUT /memory/policy` and `GET /admin/memory/policies`.
- Modify `app/schema.sql`: add admin policy inventory read-path indexes.
- Modify `tests/test_schema.py`: pin the new read-path indexes.
- Modify `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`: record P1 Memory / Context progress and remaining frontend/scheduler gaps.

## Task 1: Ordinary User Policy Update Route Tests

**Files:**
- Modify: `tests/test_context_routes.py`
- Later modify: `app/routes/context.py`

- [x] **Step 1: Add failing test for ordinary-user self opt-out**

Append this test near the existing admin memory policy tests:

```python
def test_user_set_memory_policy_updates_own_policy_and_writes_redacted_audit(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_set_memory_policy(conn, **kwargs):
        calls.append(("policy", kwargs))
        return {
            "tenant_id": kwargs["tenant_id"],
            "workspace_id": kwargs["workspace_id"],
            "user_id": kwargs["user_id"],
            "agent_id": kwargs["agent_id"],
            "memory_enabled": kwargs["memory_enabled"],
            "long_term_memory_enabled": kwargs["long_term_memory_enabled"],
            "retention_days": kwargs["retention_days"],
            "source": "stored",
            "reason": kwargs["reason"],
            "updated_by": kwargs["updated_by"],
            "updated_at": "2026-06-05T10:00:00Z",
        }

    async def fake_append_audit_log(conn, **kwargs):
        calls.append(("audit", kwargs))
        return "audit-user-policy"

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.set_memory_policy", fake_set_memory_policy, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.append_audit_log", fake_append_audit_log)
    client = TestClient(create_app())

    response = client.put(
        "/api/ai/memory/policy",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "reason": "user opt-out client_secret=client-secret openai_api_key=sk-openai",
        },
    )

    assert response.status_code == 200
    body = response.json()["memory_policy"]
    assert body["user_id"] == "user-a"
    assert body["memory_enabled"] is False
    assert body["long_term_memory_enabled"] is False
    assert body["retention_days"] == 30
    assert body["reason"] == "user opt-out client_secret=[redacted-secret] openai_api_key=[redacted-secret]"
    assert calls[0] == ("workspace", "tenant-a", "workspace-a")
    assert calls[1][0] == "policy"
    assert calls[1][1]["user_id"] == "user-a"
    assert calls[1][1]["updated_by"] == "user-a"
    assert calls[2][1]["action"] == "memory.policy.updated"
    assert calls[2][1]["target_type"] == "memory_policy"
    assert calls[2][1]["target_id"] == "user-a"
    assert "client-secret" not in str(calls)
    assert "sk-openai" not in str(calls)
```

- [x] **Step 2: Run test to verify RED**

Run:

```powershell
python -m pytest tests/test_context_routes.py::test_user_set_memory_policy_updates_own_policy_and_writes_redacted_audit -q --basetemp .pytest-tmp\run-p1-memory-red-user-policy
```

Expected: FAIL with `405 Method Not Allowed` or route not found, because `PUT /api/ai/memory/policy` does not exist yet.

- [x] **Step 3: Add failing tests for fail-closed user update errors**

Append:

```python
def test_user_set_memory_policy_rejects_long_term_enable(monkeypatch):
    async def fail_set_memory_policy(conn, **kwargs):
        raise AssertionError("long-term memory must remain fail-closed before repository write")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.set_memory_policy", fail_set_memory_policy, raising=False)
    client = TestClient(create_app())

    response = client.put(
        "/api/ai/memory/policy",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "memory_enabled": True,
            "long_term_memory_enabled": True,
            "retention_days": 90,
            "reason": "enable long term",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "long_term_memory_not_available"


def test_user_set_memory_policy_returns_404_for_missing_or_foreign_agent(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_get_agent(conn, *, tenant_id, agent_id):
        calls.append(("agent", tenant_id, agent_id))
        return None

    async def fail_set_memory_policy(conn, **kwargs):
        raise AssertionError("missing agent must not write memory policy")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr("app.routes.context.repositories.get_agent", fake_get_agent, raising=False)
    monkeypatch.setattr("app.routes.context.repositories.set_memory_policy", fail_set_memory_policy, raising=False)
    client = TestClient(create_app())

    response = client.put(
        "/api/ai/memory/policy",
        headers=headers(),
        json={
            "workspace_id": "workspace-a",
            "agent_id": "missing-agent",
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 30,
            "reason": "agent scoped opt-out",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "agent_not_found"
    assert calls == [("workspace", "tenant-a", "workspace-a"), ("agent", "tenant-a", "missing-agent")]
```

- [x] **Step 4: Run new tests to verify RED**

Run:

```powershell
python -m pytest tests/test_context_routes.py::test_user_set_memory_policy_rejects_long_term_enable tests/test_context_routes.py::test_user_set_memory_policy_returns_404_for_missing_or_foreign_agent -q --basetemp .pytest-tmp\run-p1-memory-red-user-policy-errors
```

Expected: FAIL because `PUT /memory/policy` does not exist yet.

## Task 2: Admin Policy Inventory Route Tests

**Files:**
- Modify: `tests/test_context_routes.py`
- Later modify: `app/routes/context.py`

- [x] **Step 1: Add failing test for admin policy inventory projection**

Append:

```python
def test_admin_list_memory_policies_returns_same_tenant_public_projection(monkeypatch):
    calls = []

    async def fake_ensure_workspace(conn, *, tenant_id, workspace_id):
        calls.append(("workspace", tenant_id, workspace_id))

    async def fake_list_admin_memory_policies(conn, *, tenant_id, workspace_id, user_id, agent_id, limit):
        calls.append(("policies", tenant_id, workspace_id, user_id, agent_id, limit))
        return [
            {
                "tenant_id": tenant_id,
                "workspace_id": workspace_id,
                "user_id": "user-b",
                "agent_id": "qa-word-review",
                "memory_enabled": False,
                "long_term_memory_enabled": True,
                "retention_days": 14,
                "source": "stored",
                "reason": "admin note client_secret=client-secret",
                "updated_by": "admin-a",
                "updated_at": "2026-06-05T10:10:00Z",
            }
        ]

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.transaction", fake_transaction)
    monkeypatch.setattr("app.routes.context.repositories.ensure_workspace", fake_ensure_workspace)
    monkeypatch.setattr(
        "app.routes.context.repositories.list_admin_memory_policies",
        fake_list_admin_memory_policies,
        raising=False,
    )
    client = TestClient(create_app())

    response = client.get(
        "/api/ai/admin/memory/policies?workspace_id=workspace-a&user_id=user-b&agent_id=qa-word-review&limit=25",
        headers=admin_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["memory_policies"] == [
        {
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-b",
            "agent_id": "document-review",
            "memory_enabled": False,
            "long_term_memory_enabled": False,
            "retention_days": 14,
            "source": "stored",
            "reason": "admin note client_secret=[redacted-secret]",
            "updated_by": "admin-a",
            "updated_at": "2026-06-05T10:10:00Z",
        }
    ]
    assert body["summary"] == {
        "workspace_id": "workspace-a",
        "user_id": "user-b",
        "agent_id": "document-review",
        "returned_count": 1,
        "limit": 25,
    }
    assert calls == [
        ("workspace", "tenant-a", "workspace-a"),
        ("policies", "tenant-a", "workspace-a", "user-b", "qa-word-review", 25),
    ]
    assert "client-secret" not in response.text
    assert "qa-word-review" not in response.text
```

- [x] **Step 2: Run test to verify RED**

Run:

```powershell
python -m pytest tests/test_context_routes.py::test_admin_list_memory_policies_returns_same_tenant_public_projection -q --basetemp .pytest-tmp\run-p1-memory-red-admin-policies
```

Expected: FAIL with `404 Not Found` because `GET /admin/memory/policies` does not exist yet.

- [x] **Step 3: Add admin inventory guard tests**

Append:

```python
def test_admin_list_memory_policies_rejects_non_memory_admin(monkeypatch):
    async def fail_list_admin_memory_policies(conn, **kwargs):
        raise AssertionError("non-admin must not reach policy inventory repository")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.repositories.list_admin_memory_policies", fail_list_admin_memory_policies, raising=False)
    client = TestClient(create_app())

    response = client.get("/api/ai/admin/memory/policies?workspace_id=workspace-a", headers=headers())

    assert response.status_code == 403
    assert response.json()["detail"] == "not_ai_memory_admin"


def test_admin_list_memory_policies_rejects_unsafe_query_ids_with_422(monkeypatch):
    async def fail_list_admin_memory_policies(conn, **kwargs):
        raise AssertionError("unsafe query ids must fail before repository access")

    monkeypatch.setattr("app.auth.get_settings", lambda: Settings(frontend_poc_auth_enabled=True))
    monkeypatch.setattr("app.routes.context.repositories.list_admin_memory_policies", fail_list_admin_memory_policies, raising=False)
    client = TestClient(create_app())

    bad_workspace = client.get("/api/ai/admin/memory/policies?workspace_id=../bad", headers=admin_headers())
    bad_user = client.get("/api/ai/admin/memory/policies?workspace_id=workspace-a&user_id=../bad", headers=admin_headers())
    bad_agent = client.get("/api/ai/admin/memory/policies?workspace_id=workspace-a&agent_id=../bad", headers=admin_headers())

    assert bad_workspace.status_code == 422
    assert bad_user.status_code == 422
    assert bad_agent.status_code == 422
```

- [x] **Step 4: Run admin guard tests to verify RED**

Run:

```powershell
python -m pytest tests/test_context_routes.py::test_admin_list_memory_policies_rejects_non_memory_admin tests/test_context_routes.py::test_admin_list_memory_policies_rejects_unsafe_query_ids_with_422 -q --basetemp .pytest-tmp\run-p1-memory-red-admin-policy-guards
```

Expected: non-admin may pass through existing auth if route is absent as `404`; unsafe-id test should fail until the route exists.

## Task 3: Repository Policy Inventory Test

**Files:**
- Modify: `tests/test_repositories.py`
- Later modify: `app/repositories.py`

- [x] **Step 1: Add failing repository test**

Append near existing memory policy repository tests:

```python
@pytest.mark.asyncio
async def test_list_admin_memory_policies_scopes_filters_clamps_and_closes_long_term():
    conn = FakeConn(
        rows=[
            {
                "id": "mempol-a",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
                "user_id": "user-a",
                "agent_id": "general-agent",
                "memory_enabled": True,
                "long_term_memory_enabled": True,
                "retention_days": 7,
                "reason": "stored",
                "updated_by": "admin-a",
                "updated_at": "2026-06-05T10:00:00Z",
            }
        ]
    )

    policies = await repositories.list_admin_memory_policies(
        conn,
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        user_id="user-a",
        agent_id="general-agent",
        limit=999,
    )

    sql, params = conn.queries[0]
    assert "from memory_policies" in sql
    assert "tenant_id = %s" in sql
    assert "workspace_id = %s" in sql
    assert "(%s::text is null or user_id = %s)" in sql
    assert "(%s::text is null or agent_id = %s)" in sql
    assert "limit %s" in sql
    assert params == (
        "tenant-a",
        "workspace-a",
        "user-a",
        "user-a",
        "general-agent",
        "general-agent",
        500,
    )
    assert policies[0]["tenant_id"] == "tenant-a"
    assert policies[0]["memory_enabled"] is True
    assert policies[0]["long_term_memory_enabled"] is False
    assert policies[0]["retention_days"] == 7
```

- [x] **Step 2: Run repository test to verify RED**

Run:

```powershell
python -m pytest tests/test_repositories.py::test_list_admin_memory_policies_scopes_filters_clamps_and_closes_long_term -q --basetemp .pytest-tmp\run-p1-memory-red-repo-policy-list
```

Expected: FAIL with `AttributeError` because `list_admin_memory_policies` is not implemented yet.

## Task 4: Implement Repository Helper

**Files:**
- Modify: `app/repositories.py`
- Test: `tests/test_repositories.py`

- [x] **Step 1: Add `list_admin_memory_policies` after `set_memory_policy`**

Add:

```python
async def list_admin_memory_policies(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str | None = None,
    agent_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    limit = max(min(int(limit), 500), 1)
    cursor = await conn.execute(
        """
        select id, tenant_id, workspace_id, user_id, agent_id,
               memory_enabled, long_term_memory_enabled, retention_days,
               reason, updated_by, updated_at
        from memory_policies
        where tenant_id = %s
          and workspace_id = %s
          and (%s::text is null or user_id = %s)
          and (%s::text is null or agent_id = %s)
        order by updated_at desc, created_at desc
        limit %s
        """,
        (tenant_id, workspace_id, user_id, user_id, agent_id, agent_id, limit),
    )
    return [_memory_policy_from_row(dict(row)) for row in list(await cursor.fetchall())]
```

- [x] **Step 2: Run repository test to verify GREEN**

Run:

```powershell
python -m pytest tests/test_repositories.py::test_list_admin_memory_policies_scopes_filters_clamps_and_closes_long_term -q --basetemp .pytest-tmp\run-p1-memory-green-repo-policy-list
```

Expected: PASS.

## Task 5: Implement Routes

**Files:**
- Modify: `app/routes/context.py`
- Test: `tests/test_context_routes.py`

- [x] **Step 1: Add ordinary-user `PUT /memory/policy` route after `get_memory_policy`**

Add:

```python
@router.put("/memory/policy")
async def set_own_memory_policy(
    request: MemoryPolicyRequest,
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if request.long_term_memory_enabled:
        raise HTTPException(status_code=409, detail="long_term_memory_not_available")
    reason = _audit_reason(request.reason)
    internal_agent_id = internal_agent_id_for_request(request.agent_id) if request.agent_id else None
    public_agent_id = public_agent_id_for_projection(internal_agent_id) if internal_agent_id else None
    try:
        async with transaction() as conn:
            await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=request.workspace_id)
            if internal_agent_id:
                target_agent = await repositories.get_agent(
                    conn,
                    tenant_id=principal.tenant_id,
                    agent_id=internal_agent_id,
                )
                if target_agent is None:
                    raise RepositoryNotFoundError("agent_not_found")
            await repositories.ensure_user(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                display_name=principal.display_name,
            )
            policy = await repositories.set_memory_policy(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=request.workspace_id,
                user_id=principal.user_id,
                agent_id=internal_agent_id,
                memory_enabled=request.memory_enabled,
                long_term_memory_enabled=False,
                retention_days=request.retention_days,
                reason=reason,
                updated_by=principal.user_id,
            )
            await repositories.append_audit_log(
                conn,
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                action="memory.policy.updated",
                target_type="memory_policy",
                target_id=principal.user_id,
                trace_id=standard_trace_id(principal.user_id),
                payload_json=sanitize_public_payload(
                    {
                        "workspace_id": request.workspace_id,
                        "target_user_id": principal.user_id,
                        "agent_id": public_agent_id,
                        "memory_enabled": request.memory_enabled,
                        "long_term_memory_enabled": False,
                        "retention_days": request.retention_days,
                        "reason": reason,
                    }
                ),
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositoryConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"memory_policy": _memory_policy_response(policy)}
```

- [x] **Step 2: Add admin `GET /admin/memory/policies` route before `{target_user_id}` route**

Add before `@router.put("/admin/memory/policies/{target_user_id}")` so the static route is matched first:

```python
@router.get("/admin/memory/policies")
async def admin_list_memory_policies(
    workspace_id: str = "default",
    user_id: str | None = None,
    agent_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    principal: AuthPrincipal = Depends(require_principal),
) -> dict[str, object]:
    if not _is_memory_admin(principal):
        raise HTTPException(status_code=403, detail="not_ai_memory_admin")
    workspace_id = _safe_query_id(workspace_id, "workspace_id")
    user_id = _safe_query_id(user_id, "user_id") if user_id else None
    agent_id = _safe_query_id(agent_id, "agent_id") if agent_id else None
    internal_agent_id = internal_agent_id_for_request(agent_id) if agent_id else None
    try:
        async with transaction() as conn:
            await repositories.ensure_workspace(conn, tenant_id=principal.tenant_id, workspace_id=workspace_id)
            rows = await repositories.list_admin_memory_policies(
                conn,
                tenant_id=principal.tenant_id,
                workspace_id=workspace_id,
                user_id=user_id,
                agent_id=internal_agent_id,
                limit=limit,
            )
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "memory_policies": [_memory_policy_response(row) for row in rows],
        "summary": {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "agent_id": public_agent_id_for_projection(internal_agent_id) if internal_agent_id else None,
            "returned_count": len(rows),
            "limit": limit,
        },
    }
```

- [x] **Step 3: Run route tests to verify GREEN**

Run:

```powershell
python -m pytest tests/test_context_routes.py::test_user_set_memory_policy_updates_own_policy_and_writes_redacted_audit tests/test_context_routes.py::test_user_set_memory_policy_rejects_long_term_enable tests/test_context_routes.py::test_user_set_memory_policy_returns_404_for_missing_or_foreign_agent tests/test_context_routes.py::test_admin_list_memory_policies_returns_same_tenant_public_projection tests/test_context_routes.py::test_admin_list_memory_policies_rejects_non_memory_admin tests/test_context_routes.py::test_admin_list_memory_policies_rejects_unsafe_query_ids_with_422 -q --basetemp .pytest-tmp\run-p1-memory-green-routes
```

Expected: PASS.

## Task 6: Focused Regression And Roadmap Update

**Files:**
- Modify: `app/schema.sql`
- Modify: `tests/test_schema.py`
- Modify: `docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md`
- Test: focused and full pytest commands

- [x] **Step 1: Run focused Memory / Context tests**

Run:

```powershell
python -m pytest tests/test_context_routes.py tests/test_repositories.py tests/test_schema.py -q --basetemp .pytest-tmp\run-p1-memory-focused
```

Expected: PASS.

- [x] **Step 2: Update roadmap with P1 Memory / Context progress**

Add a short section after the existing P1 Admin Runtime Overview Snapshot note:

```markdown
### P1 Memory / Context Management

The P1 backend management slice adds ordinary-user memory policy self-management
and an admin same-tenant memory policy inventory projection. Existing session
scoping, retention cleanup, redaction, public/admin projections, and
`long_term_memory_enabled = false` fail-closed behavior remain the governing
constraints. Remaining Memory / Context work is frontend UI wiring, scheduled
cleanup, and configurable redaction policy.
```

- [x] **Step 3: Add admin policy inventory read-path indexes**

Update `app/schema.sql` after `idx_memory_policies_scope`:

```sql
create index if not exists idx_memory_policies_workspace_updated
  on memory_policies(tenant_id, workspace_id, updated_at desc, created_at desc);
create index if not exists idx_memory_policies_workspace_agent_updated
  on memory_policies(tenant_id, workspace_id, agent_id, updated_at desc, created_at desc);
```

Update `tests/test_schema.py` so the Memory / Context schema test asserts both
index names and column lists.

- [x] **Step 4: Run compile and full test suite**

Run:

```powershell
python -m compileall -q app tools scripts
python -m pytest -q --basetemp .pytest-tmp\run-p1-memory-full
```

Expected: compile exits 0; full pytest exits 0.

- [x] **Step 5: Self-review before commit**

Check:

```powershell
git diff -- app/routes/context.py app/repositories.py app/schema.sql tests/test_context_routes.py tests/test_repositories.py tests/test_schema.py docs/superpowers/specs/2026-06-05-p1-memory-context-management-design.md docs/superpowers/plans/2026-06-05-p1-memory-context-management.md docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md
git diff --check
```

Confirm:

- No real `.env`, secret, runtime private payload, or personal path is staged.
- Public projections map internal agent ids through `public_agent_id_for_projection`.
- Long-term memory remains fail-closed at route, repository, and schema levels.
- New route behavior has happy-path and error-path tests.

- [x] **Step 6: Commit**

Run:

```powershell
git add app/routes/context.py app/repositories.py app/schema.sql tests/test_context_routes.py tests/test_repositories.py tests/test_schema.py docs/superpowers/specs/2026-06-05-p1-memory-context-management-design.md docs/superpowers/plans/2026-06-05-p1-memory-context-management.md docs/superpowers/plans/2026-06-02-ai-platform-foundation-roadmap.md
git commit -m "Add memory context management projections"
```

## Task 7: Review, PR, Merge, And 211 Verification

**Files:**
- No new local files unless review feedback requires code changes.

- [x] **Step 1: Request multi-agent review**

Dispatch independent review slices if the available tool exposes suitable inherited permissions. If the tool does not expose per-agent model and reasoning fields, record that the review used inherited configuration and do not claim model-specific gates.

Review slices:

- API contract and redaction review for `app/routes/context.py`.
- Repository SQL and tenant-scope review for `app/repositories.py`.
- Test coverage and PRD/roadmap alignment review.

- [x] **Step 2: Apply accepted review feedback with focused tests**

For each accepted finding:

```powershell
python -m pytest tests/test_context_routes.py tests/test_repositories.py -q --basetemp .pytest-tmp\run-p1-memory-review-fix
```

Expected: PASS after fixes.

- [ ] **Step 3: Push branch and create PR**

Run:

```powershell
git push -u origin codex/p1-memory-context-management
gh pr create --base main --head codex/p1-memory-context-management --title "Add P1 memory context management projections" --body "Adds ordinary-user memory policy self-management and admin memory policy inventory projections while keeping long-term memory fail-closed."
```

- [ ] **Step 4: Merge after review gates pass**

Run:

```powershell
gh pr merge --squash --delete-branch
git switch main
git pull --ff-only
```

- [ ] **Step 5: Deploy and smoke on 211**

Use the actual 211 Docker-capable deployment path and current compose ports. Verify:

```bash
curl -sS -o /tmp/ai-health.json -w '%{http_code}' http://127.0.0.1:8020/api/ai/health
curl -sS -o /tmp/ai-user-policy.json -w '%{http_code}' -X PUT -H 'Content-Type: application/json' -H 'X-AI-User-ID: p1-memory-smoke' -H 'X-AI-Roles: user' -H 'X-AI-Tenant-ID: default' --data '{"workspace_id":"default","memory_enabled":false,"long_term_memory_enabled":false,"retention_days":30,"reason":"p1 smoke opt-out"}' http://127.0.0.1:8020/api/ai/memory/policy
curl -sS -o /tmp/ai-admin-policies-user.json -w '%{http_code}' -H 'X-AI-User-ID: p1-memory-smoke' -H 'X-AI-Roles: user' -H 'X-AI-Tenant-ID: default' 'http://127.0.0.1:8020/api/ai/admin/memory/policies?workspace_id=default'
curl -sS -o /tmp/ai-admin-policies-admin.json -w '%{http_code}' -H 'X-AI-User-ID: dev-admin' -H 'X-AI-Roles: admin' -H 'X-AI-Tenant-ID: default' 'http://127.0.0.1:8020/api/ai/admin/memory/policies?workspace_id=default&limit=5'
```

Expected:

- Health returns `200`.
- Ordinary user policy update returns `200`.
- Ordinary user admin policy inventory returns `403`.
- Admin policy inventory returns `200`.
- Smoke outputs contain no raw secret, raw memory content, runtime private
  payload, storage key, or runtime path.
