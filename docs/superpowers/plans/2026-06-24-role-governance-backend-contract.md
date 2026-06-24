# Role Governance Backend Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe backend projection for the frontend `/roles` workbench without reviving legacy `/api/roles` CRUD.

**Architecture:** Implement a new authenticated `/api/role-governance/*` route set that projects role, department, workspace, request, approval, audit, and rollback state from the current principal, static platform role taxonomy, and bounded tenant-scoped `audit_logs` history. Write paths only queue audited governance operations through `audit_logs`; they do not directly grant roles or expose raw permissions.

**Tech Stack:** FastAPI routes, Pydantic v2 models, existing `AuthPrincipal`, existing `transaction()` and `repositories.append_audit_log`, pytest with FastAPI `TestClient`.

## Global Constraints

- Keep #215 as a backend contract slice; do not use `Closes` or `Fixes` auto-close wording.
- Do not modify legacy `/api/roles` behavior or make it the `/roles` product authority.
- All routes require an authenticated principal.
- Ordinary users can read safe projections with `role:read` and request access with `role:request`.
- Admin-only approval/reject/rollback writes require `role:manage` or platform admin role.
- No raw permission catalog, secrets, private payload, host paths, or credential values may be projected or accepted as role-governance targets.
- Local pytest commands must use a child under `.pytest-tmp`.

---

### Task 1: Add RED Contract Tests

**Files:**
- Create: `tests/test_role_governance_routes.py`

**Interfaces:**
- Consumes: `create_app()`, trusted principal headers, monkeypatched `app.routes.role_governance.transaction`, and monkeypatched `repositories.append_audit_log`.
- Produces: expected `/api/role-governance/*` route behavior for implementation.

- [ ] **Step 1: Write failing tests**

Create tests that assert the overview route projects safe role directory/scope/audit data, ordinary users can queue access requests, admin-only approval/reject/rollback writes fail closed, and secret-bearing or unsafe payloads are rejected.

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
python -m pytest tests\test_role_governance_routes.py -q --basetemp .pytest-tmp\issue215-role-governance-red
```

Expected: fail because `app.routes.role_governance` and `/api/role-governance/*` do not exist.

### Task 2: Implement Models And Routes

**Files:**
- Modify: `app/models.py`
- Create: `app/routes/role_governance.py`
- Modify: `app/main.py`

**Interfaces:**
- Consumes: existing `AuthPrincipal`, `is_ai_admin`, `require_principal`, `transaction`, `repositories.append_audit_log`, `repositories.list_role_governance_audit_history`, and `assert_safe_id`.
- Produces: `/api/role-governance/overview`, `/api/role-governance/requests`, `/api/role-governance/requests/{request_id}`, `/api/role-governance/approvals/{request_id}/approve`, `/api/role-governance/approvals/{request_id}/reject`, and `/api/role-governance/audit/{audit_id}/rollback`.

- [ ] **Step 1: Add Pydantic response/request models**

Add models for role directory entries, scope projection, request/audit items, overview response, request body, decision body, and operation response. All models use `extra="forbid"` and avoid raw permissions.

- [ ] **Step 2: Add route implementation**

Implement a deterministic, tenant-scoped projection from principal context, platform role taxonomy, and bounded role-governance audit history. Use permission expansion only internally; project capability labels and requestability, not raw permission strings.

- [ ] **Step 3: Register router**

Import `role_governance_router` in `app/main.py` and include it under prefix `/api`.

- [ ] **Step 4: Run GREEN tests**

Run:

```powershell
python -m pytest tests\test_role_governance_routes.py -q --basetemp .pytest-tmp\issue215-role-governance-green
```

Expected: all tests pass.

### Task 3: Document Frontend Contract

**Files:**
- Create: `docs/frontend/role-governance-public-api.md`

**Interfaces:**
- Consumes: implemented route set and #215 acceptance.
- Produces: frontend-facing contract documentation.

- [ ] **Step 1: Document auth, routes, payloads, and boundaries**

Document route names, permission behavior, write audit semantics, and explicitly state that legacy `/api/roles` remains compatibility-only.

- [ ] **Step 2: Run doc/diff checks**

Run:

```powershell
git diff --check
```

Expected: exit 0.

### Task 4: Verify And Prepare PR

**Files:**
- All files above.

**Interfaces:**
- Consumes: test and documentation changes.
- Produces: commit and PR-ready evidence.

- [ ] **Step 1: Run targeted verification**

Run:

```powershell
python -m pytest tests\test_role_governance_routes.py tests\test_workbench_projection_routes.py tests\test_frontend_projection_audit.py -q --basetemp .pytest-tmp\issue215-role-governance-final
python -m pytest tests\test_repositories.py::test_list_role_governance_audit_history_uses_bounded_tenant_scoped_query tests\test_repositories.py::test_list_role_governance_audit_history_clamps_limit_for_direct_callers -q --basetemp .pytest-tmp\issue215-role-governance-repository
python -m compileall -q app tools scripts
git diff --check
```

- [ ] **Step 2: Self-review staged scope**

Confirm no secrets, no personal paths in reusable docs, new public classes/routes have docstrings, happy and deny paths are tested, and docs mention the frontend contract boundary.

- [ ] **Step 3: Commit and open PR**

Commit with:

```powershell
git add app/models.py app/routes/role_governance.py app/main.py tests/test_role_governance_routes.py docs/frontend/role-governance-public-api.md docs/superpowers/plans/2026-06-24-role-governance-backend-contract.md
git commit -m "feat: add role governance projection contract"
```

Open a PR linked to #215 without auto-close keywords.
