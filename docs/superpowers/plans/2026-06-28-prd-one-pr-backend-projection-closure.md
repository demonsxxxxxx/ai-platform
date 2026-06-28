# PRD One-PR Backend Projection Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining post-login frontend projection gaps for issue #81 by backing the frontend marketplace, persona preset, and revealed files contracts.

**Architecture:** Keep the frontend as the consumer of ai-platform public projections only. Add a focused `/api` projection router for persona presets and revealed files, keep artifact downloads on existing ACL-checked `/api/ai/artifacts/{artifact_id}/download`, and converge `/api/marketplace/` to the object list shape expected by the frontend.

**Tech Stack:** FastAPI, Pydantic v2 response models, pytest route tests, existing repository transaction helpers.

## Global Constraints

- Do not commit credentials, `.env`, `.codex*`, `.pytest-tmp`, `dist`, `node_modules`, screenshots, smoke JSON, or tarballs.
- Keep #81 status language strict: this work can become PR-ready after verification, but not `gate closable` until merge and required 211 evidence exist.
- Do not replace existing admin APIs or bypass tenant/user ACLs.
- Use TDD: route tests must fail before production code is added.

---

### Task 1: Marketplace Object Projection

**Files:**
- Modify: `app/models.py`
- Modify: `app/routes/skills_marketplace.py`
- Test: `tests/test_skills_marketplace_routes.py`

**Interfaces:**
- Consumes: existing `_marketplace_item`, `_available_tags`, and `_effective_permissions`.
- Produces: `MarketplaceListResponse` with `skills`, `total`, `skip`, `limit`, `available_tags`, and `effective_permissions`.

- [x] **Step 1: Write the failing test**

Update the marketplace list route test to assert `response.json()["skills"][0]` and list metadata instead of top-level array indexing.

- [x] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m pytest tests\test_skills_marketplace_routes.py::test_skills_and_marketplace_read_contracts_project_catalog_and_files -q --basetemp .pytest-tmp
```

Expected: FAIL with `TypeError: list indices must be integers or slices, not str`.

- [x] **Step 3: Write minimal implementation**

Add `MarketplaceListResponse` and return the object from `/api/marketplace/`.

- [x] **Step 4: Run test to verify it passes**

Run the same pytest command and expect PASS.

### Task 2: Persona And Revealed Files Public Projections

**Files:**
- Create: `app/routes/frontend_projections.py`
- Modify: `app/models.py`
- Modify: `app/main.py`
- Modify: `app/repositories.py`
- Modify: `app/routes/auth.py`
- Test: `tests/test_frontend_projection_routes.py`
- Test: `tests/test_auth_routes.py`

**Interfaces:**
- Consumes: `AuthPrincipal`, `transaction`, and repository artifact queries.
- Produces: `/api/persona-presets/*` and `/api/files/revealed*` routes with shaped empty responses and fail-closed permission errors.

- [x] **Step 1: Write the failing tests**

Add route tests for persona list/permission denial and revealed files list/grouped/stats/sessions/fail-closed behavior.

- [x] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests\test_frontend_projection_routes.py -q --basetemp .pytest-tmp
```

Expected: FAIL because `app.routes.frontend_projections` does not exist.

- [x] **Step 3: Write minimal implementation**

Add Pydantic models, a focused frontend projection router, artifact repository queries, router registration, and baseline `persona_preset:read` login permission.

- [x] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests\test_frontend_projection_routes.py tests\test_auth_routes.py -q --basetemp .pytest-tmp
```

Expected: PASS.

### Task 3: Verification And PR Publishing

**Files:**
- Review all changed files with `git diff --check`.

**Interfaces:**
- Consumes: completed Tasks 1 and 2.
- Produces: a verified branch ready to push and attach to the one remaining #81 closure PR.

- [x] **Step 1: Run focused backend route tests**

```powershell
python -m pytest tests\test_skills_marketplace_routes.py tests\test_frontend_projection_routes.py tests\test_auth_routes.py -q --basetemp .pytest-tmp
```

- [x] **Step 2: Run frontend static contract tests**

```powershell
pnpm exec tsx --test src/__tests__/frontendShellParityAcceptance.test.ts
```

- [x] **Step 3: Run compile and whitespace checks**

```powershell
python -m compileall -q app tools scripts
git diff --check
```

- [x] **Step 4: Commit and push**

```powershell
git add app frontend docs tests
git commit -m "fix: back frontend projection contracts"
git push -u origin codex/prd-one-pr-closure-20260628
```

## Self-Review

- Spec coverage: covers #233 marketplace object shape and #229 read projection gap for persona/files.
- Placeholder scan: no placeholders remain.
- Type consistency: route models match the current frontend TypeScript API contracts.
