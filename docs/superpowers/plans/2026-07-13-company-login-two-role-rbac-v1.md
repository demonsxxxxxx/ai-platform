# Company Login Two-Role RBAC V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Canonicalize company sessions to `admin` or `user`, enforce exact backend permissions and stale-session rejection, and apply one fail-closed frontend policy to every navigation and route surface with Chinese defaults.

**Architecture:** The backend is authoritative for company role, permission, and policy-version projection. A pure frontend access-policy module consumes only the signed `/auth/me` projection; route guards and every navigation renderer reuse it before protected content is mounted.

**Tech Stack:** Python 3.13, FastAPI, pytest, React 19, TypeScript 5.6, React Router 7, i18next, pnpm, Vite, Playwright-compatible browser smoke.

## Global Constraints

- Edit only revision-75 `writable_paths`; never edit MCP-owned locale or shared acceptance files.
- Use synthetic identities only; do not read, store, log, or use real credentials.
- Do not change external authentication, schema, repositories, settings, dependencies, CI, Docker, deployment, or 211.
- Preserve trusted-header/internal platform roles outside company-login sessions.
- Do not merge the ready PR.

---

### Task 1: Backend canonical role and exact permission policy

**Files:**
- Modify: `tests/test_auth_routes.py`
- Modify: `app/routes/auth.py`

**Interfaces:**
- Consumes: upstream role list and configured administrator identity checks.
- Produces: `_roles_for_login(...) -> list[str]`, `AI_USER_PERMISSIONS`, and `AI_ADMIN_PERMISSIONS` with exact ordered values.

- [ ] **Step 1: Write failing parameterized tests** for upstream `admin`, `developer`, ordinary, unknown, empty, malformed, and user-info failure shapes; assert exact `roles` and permission lists.
- [ ] **Step 2: Verify RED** with `python -m pytest tests/test_auth_routes.py -q --basetemp .pytest-tmp`; expect raw upstream role passthrough and broad ordinary permissions to fail.
- [ ] **Step 3: Implement the minimal policy** so `_roles_for_login` returns only `["admin"]` or `["user"]` and `_ai_permissions_for_login` merges only code-owned exact sets.
- [ ] **Step 4: Verify GREEN** with the same focused pytest command; expect all auth route tests to pass.

### Task 2: Company session policy version

**Files:**
- Modify: `tests/test_auth_principal.py`
- Modify: `tests/test_auth_routes.py`
- Modify: `app/auth.py`

**Interfaces:**
- Produces: `COMPANY_AUTHZ_POLICY_VERSION: int`; company tokens include `authz_policy_version`; `verify_principal_session` raises HTTP 401 `stale_company_session` on missing/mismatch.

- [ ] **Step 1: Write failing tests** for current company token acceptance, legacy company token rejection, mismatched version rejection, and non-company compatibility.
- [ ] **Step 2: Verify RED** with `python -m pytest tests/test_auth_principal.py tests/test_auth_routes.py -q --basetemp .pytest-tmp`; expect legacy company tokens to remain accepted.
- [ ] **Step 3: Add the code-owned policy version** to company token signing and fail-closed verification without changing secrets.
- [ ] **Step 4: Verify GREEN** with the same focused pytest command.

### Task 3: Pure frontend workbench access policy

**Files:**
- Create: `frontend/web/src/components/governance/workbenchAccessPolicy.ts`
- Create: `frontend/web/src/components/governance/__tests__/workbenchAccessPolicy.test.ts`
- Modify: `frontend/web/src/components/panels/SidebarParts/navigationState.ts`
- Modify: `frontend/web/src/components/panels/SidebarParts/__tests__/navigationState.test.ts`

**Interfaces:**
- Produces: `WorkbenchAccessKey`, `canAccessWorkbenchItem(user, key)`, `canAccessWorkbenchPath(user, pathname)`, and guarded navigation helpers.

- [ ] **Step 1: Write failing pure tests** for the complete ordinary/admin access matrix and guarded path mapping.
- [ ] **Step 2: Verify RED** with `corepack pnpm exec tsx --test src/components/governance/__tests__/workbenchAccessPolicy.test.ts src/components/panels/SidebarParts/__tests__/navigationState.test.ts`; expect the new module to be missing.
- [ ] **Step 3: Implement the pure policy** with `user.is_admin === true` as the only admin identity fact.
- [ ] **Step 4: Verify GREEN** with the same command.

### Task 4: Route and navigation enforcement

**Files:**
- Create: `frontend/web/src/__tests__/AppRouteFallback.test.ts`
- Modify: `frontend/web/src/App.tsx`
- Modify: `frontend/web/src/components/auth/ProtectedRoute.tsx`
- Modify: `frontend/web/src/components/panels/SessionSidebar.tsx`
- Modify: `frontend/web/src/components/panels/SidebarParts/SidebarRail.tsx`
- Modify: `frontend/web/src/components/panels/SidebarParts/SessionListContent.tsx`
- Modify: `frontend/web/src/components/layout/UserMenu.tsx`
- Modify: `frontend/web/src/hooks/__tests__/useAuth.test.ts`
- Modify: `frontend/web/src/hooks/useAuth.tsx`

**Interfaces:**
- Consumes: Task 3 policy and `/auth/me` `user.is_admin` plus permissions.
- Produces: admin-only routes that redirect to `/chat` before mounting children and filtered desktop/mobile navigation.

- [ ] **Step 1: Write failing source-contract tests** proving `requireAdmin` uses `user.is_admin`, all seven management routes redirect, and every navigation consumer imports the access policy.
- [ ] **Step 2: Verify RED** with the focused `tsx --test` command listed in the operations Phase document.
- [ ] **Step 3: Apply route guards and navigation filters** without changing existing layout, icon, token, or responsive geometry.
- [ ] **Step 4: Verify GREEN** with the same focused frontend tests.

### Task 5: Canonical role presentation and Chinese defaults

**Files:**
- Modify: `frontend/web/src/components/profile/tabs/ProfileInfoTab.tsx`
- Modify: `frontend/web/src/components/panels/SidebarParts/SessionListContent.tsx`
- Modify: `frontend/web/src/i18n/index.ts`
- Modify: `frontend/web/src/hooks/__tests__/useAuth.test.ts`
- Create: `frontend/web/src/__tests__/AppRouteFallback.test.ts`

**Interfaces:**
- Consumes: existing `workbench.governance.roleLabels.admin/user` locale keys.
- Produces: canonical role labels and language detection with SSR/unsupported/fallback `zh` while retaining explicit preference priority.

- [ ] **Step 1: Write failing tests** for saved language priority, supported browser language, no-preference Chinese, unsupported-language Chinese, `fallbackLng: "zh"`, and locale-key role rendering.
- [ ] **Step 2: Verify RED** with focused `tsx --test`; expect current English defaults and raw role display to fail.
- [ ] **Step 3: Implement language and role presentation changes** without editing locale JSON.
- [ ] **Step 4: Verify GREEN** with focused frontend tests.

### Task 6: Mocked browser role matrix and delivery gates

**Files:**
- Create: `frontend/web/scripts/company-rbac-browser-smoke.mjs`
- Modify: `docs/operations/2026-07-13-company-login-two-role-rbac-v1.md`

**Interfaces:**
- Produces: synthetic admin/user desktop 1440x900 and mobile 390x844 screenshots and assertions.

- [ ] **Step 1: Add smoke assertions** for ordinary management entry count zero, direct management redirect without protected content, admin page visibility, default Chinese, and no viewport overflow or element overlap.
- [ ] **Step 2: Run backend gates:** `python -m compileall -q app tools scripts` and focused pytest with `--basetemp .pytest-tmp`.
- [ ] **Step 3: Run frontend gates:** focused tests, `corepack pnpm run lint`, `corepack pnpm exec tsc -b`, `corepack pnpm run build`, and `corepack pnpm run projection:audit`.
- [ ] **Step 4: Run local mocked browser smoke** and inspect all four screenshots.
- [ ] **Step 5: Update Phase evidence** immediately after each command.

### Task 7: Exact-head review and ready PR

**Files:**
- Modify only files already listed if review fixes are required.
- Modify: `docs/operations/2026-07-13-company-login-two-role-rbac-v1.md`

**Interfaces:**
- Consumes: exact commit SHA and approved design.
- Produces: independent security and UX review with no open Critical or Important findings, one ready PR, and CI evidence.

- [ ] **Step 1: Run the large-feature pre-commit gate** and record compile, changed-scope tests, integration/smoke, self-review, and diff summary.
- [ ] **Step 2: Commit the scoped files** with no forbidden or secret-bearing paths.
- [ ] **Step 3: Dispatch an independent exact-head reviewer** for security and UX; fix Critical/Important findings with a new RED/GREEN cycle and request re-review.
- [ ] **Step 4: Push the branch and create a ready PR** linked to Issue #412, including validation and limitations.
- [ ] **Step 5: Post durable review/validation evidence and inspect CI**; do not merge, deploy, or access 211.

## Self-Review

- Spec coverage: all backend role, permission, session, frontend policy, route,
  navigation, role-display, language, viewport, review, and PR requirements map
  to Tasks 1-7.
- Placeholder scan: no TBD/TODO/implement-later placeholders remain.
- Type consistency: `WorkbenchAccessKey` and the three policy functions are
  named consistently across producers and consumers.
- Scope: every source, test, script, and document path is in revision-75
  `writable_paths`; MCP-owned locale and shared acceptance files remain excluded.
