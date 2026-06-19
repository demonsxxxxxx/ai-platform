# Frontend Merge Login Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge the LibreChat-style frontend into the primary ai-platform frontend, replace token-localStorage login authority with ai-platform principal/cookie auth, and keep unsupported backend surfaces fail-closed for Phase 2.

**Architecture:** Phase 1 keeps `frontend/web` as the only production frontend and treats ai-platform backend routes as the source of truth. Auth uses `/api/ai/auth/login`, `/api/ai/auth/me`, and `/api/ai/auth/logout` through the existing deployed prefix, with browser cookies carrying the session and `PrincipalResponse` driving route guards, menu visibility, and action gates. Profile identity is read-only from the principal. Theme, language, default agent, and pinned model preferences stay browser-local until ai-platform owns a profile-preference backend projection. Missing department marketplace, MCP lifecycle, company user/role/department CRUD, model/settings writes, and notification dismiss/admin contracts remain hidden or fail-closed until Phase 2 backend routes exist.

**Tech Stack:** React 19, React Router 7, TypeScript, Vite, Tailwind, FastAPI, ai-platform public/admin projections.

---

## Phase Boundary

Phase 1 must make the merged frontend usable against current backend contracts:

- Auth/session/RBAC: use ai-platform principal, roles, permissions, tenant, and admin state.
- Chat/session/run: use existing chat, session, run, event stream, playback, and artifact routes.
- Skills/agents: use existing agent-app and admin skill governance projections; composer skill selection must not invent department marketplace semantics.
- Admin/governance: keep Admin Runtime, memory, tool policy, and skill governance backed by existing routes.
- Unsupported surfaces: show a Phase 2 unavailable state or hide the entry; do not call LibreChat/LambChat product-authority endpoints as if they are complete backend features.

Phase 2 owns backend-backed department skill marketplace, MCP management, company users/roles/departments, profile-preference persistence, model administration, settings, and notification dismiss/admin workflows.

## 2026-06-19 Execution Evidence

Current status: `PR ready` and `reviewed` for the Phase 1 frontend merge/login/RBAC slice, with a partial 211 preview smoke on port 18003. This is not `211 verified` and not `gate closable` because the official 211 frontend entry on port 18001 still serves the previous thin-shell frontend and no real company-account browser login has been completed on the official entry.

Local verification evidence refreshed on 2026-06-19:

- `python -m compileall -q app tools scripts` exited 0.
- `python -m pytest tests/test_source_authority_docs.py tests/test_auth_routes.py tests/test_backend_stage_closure_evidence.py -q --basetemp .pytest-tmp\run-refresh-20260619` reported `67 passed, 3 warnings`.
- `corepack pnpm exec tsx --test src/services/api/__tests__/auth.test.ts src/auth/__tests__/aiPlatformPermissions.test.ts src/components/layout/AppContent/__tests__/phase1SurfacePolicy.test.ts src/services/api/__tests__/phase1Projection.test.ts src/services/api/__tests__/notificationActive.test.ts src/__tests__/aiPlatformLegacyRouteGuard.test.ts` reported `55 passed`.
- `corepack pnpm exec tsc -b --pretty false` exited 0.
- `corepack pnpm run projection:audit` exited 0 with status `pass_with_policy_gaps`; the remaining gaps are Phase 2 backend contract gaps rather than active-browser private projection violations.
- `corepack pnpm run lint` exited 0 with one existing `react-refresh/only-export-components` warning in `frontend/web/src/components/chat/ChatMessage/sessionImageGallery.tsx`.
- `corepack pnpm run build` exited 0 with existing Vite large-chunk warnings.
- `git diff --check` exited 0.

211 preview evidence refreshed on 2026-06-19:

- Preview server: `http://10.56.0.211:18003/`, served from `/home/xinlin.jiang/frontend-pr111-smoke/dist` with provenance commit `5e3a747e031e7f1a1ce7c525d19a0ca2d64519ed` and dirty flag `false`.
- Official entry remains unchanged: `http://10.56.0.211:18001/` still runs `tools/serve_lambchat_thin_shell.py` against `/home/xinlin.jiang/lambchat-poc/frontend-dist-ai-platform`; do not claim official 18001 deployment from the preview evidence.
- 18003 index returned HTTP 200, `Content-Length: 10343`, and `Last-Modified: Fri, 19 Jun 2026 05:58:25 GMT`.
- 18003 static assets referenced by `index.html` returned HTTP 200 for the main JS/CSS/vendor bundles.
- 18003 SPA fallback routes `/auth/login`, `/chat`, `/settings`, `/mcp`, and `/notifications` returned HTTP 200.
- 18003 proxied `GET /api/ai/health` returned `{"status":"ok"}`.
- 18003 unauthenticated `GET /api/ai/auth/me` returned HTTP 401 with `missing_authenticated_principal`.
- 18003 API-level RBAC smoke using a redacted trusted-principal secret verified:
  - Admin principal `GET /api/ai/auth/me` returned HTTP 200 with `is_admin: true`.
  - Ordinary principal `GET /api/ai/admin/runtime/overview?include_maintenance_cleanup=false` returned HTTP 403 with `not_ai_admin`.
  - Admin principal `GET /api/ai/admin/runtime/overview?include_maintenance_cleanup=false` returned HTTP 200.
  - Ordinary principal `GET /api/agents` returned HTTP 200 with an agent projection envelope.

Remaining Phase 1 integration gaps before `211 verified`:

- Switch or deploy the Phase 1 frontend on the official 211 entry `http://10.56.0.211:18001/` with source/provenance evidence, or explicitly approve 18003 as the review-only preview endpoint.
- Complete real browser login with a valid company account through `/api/ai/auth/login`, then verify `/api/ai/auth/me`, chat/session loading, ordinary/admin route gates, and current admin projections from the browser session.
- Avoid direct `git pull`, checkout, reset, or overwrite in `/home/xinlin.jiang/ai-platform-phaseb/services/ai-platform` while that 211 repo remains dirty and behind origin.

## File Structure

- Modify: `frontend/web/src/types/auth.ts`
  - Add ai-platform `PrincipalResponse` shape and permit cookie-authenticated `AuthState.token` to be `null`.
- Modify: `frontend/web/src/services/api/auth.ts`
  - Make `login` return a principal, clear scoped caches, and avoid saving access/refresh tokens.
  - Make `getCurrentUser` normalize `/api/ai/auth/me` principal payload into the existing `User` shape.
  - Make `logout` call backend `/api/ai/auth/logout` and clear local auth caches.
- Modify: `frontend/web/src/services/api/fetch.ts`
  - Preserve cookie credentials for same-origin auth and stop treating a missing access token as unauthenticated.
- Modify: `frontend/web/src/services/api/authenticatedRequest.ts`
  - Use cookie-compatible headers and retry semantics without requiring refresh tokens.
- Modify: `frontend/web/src/services/api/token.ts`
  - Keep legacy token helpers for OAuth callback compatibility, but make `isAuthenticated` no longer the Phase 1 authority.
- Modify: `frontend/web/src/hooks/useAuth.tsx`
  - Initialize by calling `/api/ai/auth/me` directly; set authenticated state from user/principal, not token.
  - Login should use returned principal or `/api/auth/me`, normalize permissions, and set `isAuthenticated` from `user`.
  - Logout clears user, permissions, token cache, and backend cookie.
- Modify: `frontend/web/src/hooks/useSettings.ts`
  - Stop skipping settings solely because localStorage has no token; settings must follow auth route behavior.
- Modify: `frontend/web/src/hooks/useAgent/sseConnection.ts`
  - Preserve cookie credentials for SSE stream and only attach Authorization when a legacy token exists.
- Modify: `frontend/web/src/contexts/ThemeContext.tsx`
- Modify: `frontend/web/src/components/common/LanguageToggle.tsx`
- Modify: `frontend/web/src/components/profile/tabs/ProfilePreferencesTab.tsx`
- Modify: `frontend/web/src/components/layout/AppContent/index.tsx`
- Modify: `frontend/web/src/components/layout/AppContent/Header.tsx`
- Modify: `frontend/web/src/services/api/modelPublic.ts`
  - Keep theme, language, default agent, and pinned model preferences browser-local; do not call `/api/auth/profile*` until a backend preference projection exists.
- Modify: `frontend/web/index.html`
- Modify: `frontend/web/public/manifest.json`
- Modify: `frontend/web/public/offline.html`
- Modify: `frontend/web/public/robots.txt`
- Modify: `frontend/web/public/sitemap.xml`
- Modify: `frontend/web/src/i18n/locales/*.json`
  - Replace visible packaged-frontend LambChat metadata and display strings with AI Platform branding.
- Test: `frontend/web/src/services/api/__tests__/auth.test.ts`
  - Red/green tests for principal login, no token storage, current-user normalization, and backend logout.
- Test: `frontend/web/src/auth/__tests__/aiPlatformPermissions.test.ts`
  - Keep effective permission coverage and add principal-derived admin/user deny cases if gaps remain.
- Test: `frontend/web/src/components/layout/AppContent/__tests__/phase1SurfacePolicy.test.ts`
  - Keep unsupported Phase 2 surfaces fail-closed.
- Test: `frontend/web/src/__tests__/aiPlatformLegacyRouteGuard.test.ts`
  - Guard packaged AI Platform branding and verify active preference paths do not call `/api/auth/profile*`.

## Task 1: Principal Auth API Contract

**Files:**
- Modify: `frontend/web/src/types/auth.ts`
- Modify: `frontend/web/src/services/api/auth.ts`
- Modify: `frontend/web/src/services/api/__tests__/auth.test.ts`

- [ ] **Step 1: Write the failing tests**

Add tests proving:

```ts
const principal = {
  user_id: "u001",
  user_name: "u001",
  display_name: "User One",
  tenant_id: "default",
  roles: ["user"],
  permissions: ["agent:use"],
  is_admin: false,
  source: "company-login",
};

const loginResult = await authApi.login({ username: "u001", password: "secret" });
assert.equal(loginResult.user_id, "u001");
assert.equal(stubs.stored.has("access_token"), false);
assert.equal(stubs.stored.has("refresh_token"), false);
assert.deepEqual(stubs.events, ["auth:login"]);
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
corepack pnpm exec tsx --test src/services/api/__tests__/auth.test.ts
```

Expected: failure because current `authApi.login` expects `access_token` and stores token values.

- [ ] **Step 3: Implement minimal principal auth mapping**

Change `authApi.login` to return `PrincipalResponse`, clear auth scoped caches, dispatch `auth:login`, and not call `setTokens`. Add a local `principalToUser` mapper for `/api/ai/auth/me`.

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
corepack pnpm exec tsx --test src/services/api/__tests__/auth.test.ts
```

Expected: all auth API tests pass.

## Task 2: Cookie-Principal Auth State

**Files:**
- Modify: `frontend/web/src/hooks/useAuth.tsx`
- Modify: `frontend/web/src/services/api/fetch.ts`
- Modify: `frontend/web/src/services/api/authenticatedRequest.ts`
- Modify: `frontend/web/src/hooks/useSettings.ts`

- [ ] **Step 1: Write or update focused tests**

Use existing service tests where possible and add a source inspection test if the hook test harness is not available:

```ts
assert.match(useAuthSource, /authApi\.getCurrentUser\(\)/);
assert.doesNotMatch(useAuthSource, /if \(!accessToken\)[\s\S]*return/);
assert.match(useAuthSource, /isAuthenticated:\s*!!user/);
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
corepack pnpm exec tsx --test src/services/api/__tests__/auth.test.ts src/auth/__tests__/aiPlatformPermissions.test.ts
```

Expected: failure on token-based initialization/source assertions before implementation.

- [ ] **Step 3: Implement cookie-compatible auth state**

Make `AuthProvider` call `authApi.getCurrentUser()` during initialization even with no local token. Set auth state from `user`, keep `token` as optional legacy state, and make `refreshUser` fetch the current user without checking `isAuthenticated()`.

- [ ] **Step 4: Run tests to verify pass**

Run:

```powershell
corepack pnpm exec tsx --test src/services/api/__tests__/auth.test.ts src/auth/__tests__/aiPlatformPermissions.test.ts
```

Expected: tests pass.

## Task 3: Existing Interface Surface Audit

**Files:**
- Modify: `docs/frontend/librechat-frontend-phase1-interface-matrix.md`
- Modify where needed: `frontend/web/src/components/layout/AppContent/phase1SurfacePolicy.ts`
- Modify where needed: `frontend/web/src/services/api/phase1Projection.ts`
- Modify where needed: browser-local preference paths and packaged frontend metadata listed above.

- [ ] **Step 1: Classify active routes**

Update the matrix with `reuse-current`, `compat-adapter`, `fail-closed-placeholder`, or `phase-2-backend` for active login, chat, run, artifact, admin runtime, memory, tool permission, skill governance, marketplace, MCP, users, roles, settings, and notification surfaces.

Also record that Phase 1 preference writes are browser-local only. Active code must not persist theme, language, default agent, or pinned model preferences through `/api/auth/profile*` until ai-platform exposes an authoritative preference projection.

- [ ] **Step 2: Run fail-closed tests**

Run:

```powershell
corepack pnpm exec tsx --test src/components/layout/AppContent/__tests__/phase1SurfacePolicy.test.ts src/services/api/__tests__/phase1Projection.test.ts
```

Expected: unsupported surfaces fail closed; existing projection calls remain mapped, active preferences stay browser-local, and packaged frontend metadata no longer exposes legacy LambChat branding.

## Task 4: Focused Verification

**Files:**
- No production changes unless verification finds a defect.

- [ ] **Step 1: Backend compile and auth route tests**

Run:

```powershell
python -m compileall -q app tools scripts
python -m pytest tests/test_auth_routes.py tests/test_auth_principal.py -q --basetemp .pytest-tmp
```

Expected: exit 0.

- [ ] **Step 2: Frontend focused verification**

Run:

```powershell
corepack pnpm exec tsx --test src/services/api/__tests__/auth.test.ts src/auth/__tests__/aiPlatformPermissions.test.ts src/components/layout/AppContent/__tests__/phase1SurfacePolicy.test.ts src/services/api/__tests__/phase1Projection.test.ts src/__tests__/aiPlatformLegacyRouteGuard.test.ts src/__tests__/pwaGuards.test.ts src/__tests__/pwaRouting.test.ts
corepack pnpm exec tsc -b --pretty false
corepack pnpm run projection:audit
corepack pnpm run lint
corepack pnpm run build
```

Expected: exit 0 for each command.

- [ ] **Step 3: Local smoke**

Run backend and Vite preview or dev server if needed, then verify:

- Unauthenticated `/chat` redirects to login or shows auth gate.
- Login page renders.
- After login with valid company credentials, `/api/ai/auth/me` returns principal and UI shows user menu without token storage dependency.
- Ordinary user cannot open admin-only routes.
- Admin user can open current admin runtime/skill/memory/tool policy surfaces.

## Task 5: 211 Verification

**Files:**
- No local source changes unless deployment smoke finds a defect.

- [ ] **Step 1: Sync/deploy only after local verification**

Use the 211 Docker-capable host and repo-local deploy composition. Keep runtime labels/source paths aligned before claiming `211 verified`.

- [ ] **Step 2: Smoke 211 frontend**

Verify `http://10.56.0.211:18001/` against current backend:

- Login with company account.
- `/api/ai/auth/me` returns current principal.
- Chat/session list loads.
- A basic chat/run queues or reaches the expected backend state.
- Admin/ordinary RBAC gates behave correctly.

## Self-Review

- Spec coverage: The plan covers old frontend replacement by merging PR #83 into `frontend/web`, login interface changes, RBAC replacement, existing-interface Phase 1, unsupported Phase 2 surfaces, and local/211 verification.
- Placeholder scan: No implementation step depends on unspecified behavior; Phase 2 surfaces are explicitly named.
- Type consistency: `PrincipalResponse`, `User`, and `AuthState` are the key types across API and hook tasks.
