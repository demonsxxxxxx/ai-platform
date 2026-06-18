# LibreChat Frontend Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the Phase 1 existing-interface slice for LibreChat-style frontend absorption by wiring active UI surfaces to current ai-platform projections, replacing RBAC assumptions with ai-platform principal permissions, and moving backend-missing pages into fail-closed Phase 2 placeholders.

**Architecture:** Add a single permission normalization layer and a single surface policy layer, then make route guards, sidebar navigation, user menu navigation, and tab rendering consume those policies. Current ai-platform projections stay active for chat, sessions, upload, memory, model availability, artifacts, run playback, tool permissions, Admin Runtime, admin Skills governance, admin tool policy inventory, admin agent-apps, and active notifications. Surfaces with an existing projection are remapped to Phase 1 read-only or governed panels. Surfaces without a current backend contract, such as department marketplace, file library, persona presets, feedback, company users/roles, MCP server lifecycle, settings CRUD, notification CRUD, and channel administration, are rendered as fail-closed Phase 2 placeholders without API calls.

**Tech Stack:** React 19, TypeScript, Vite, React Router 7, Tailwind, lucide-react, node:test with tsx, ai-platform FastAPI backend projections.

---

## Source Boundary

No LibreChat source code is copied in this Phase 1 patch. LibreChat remains a UI reference only until a future FE-0 issue pins a source commit and records license/provenance. This plan therefore focuses on ai-platform frontend code already under `frontend/web`.

## File Structure

- Create `frontend/web/src/auth/aiPlatformPermissions.ts`
  - Normalizes backend principal permissions into effective frontend permissions.
  - Preserves raw ai-platform permission strings by adding enum values for `agent:use`, `artifact:download`, and `admin:status`.
  - Maps `agent:use` to existing chat/session/agent read permissions and document upload availability where current backend contracts already allow it.
- Modify `frontend/web/src/types/auth.ts`
  - Add ai-platform permission enum values.
- Modify `frontend/web/src/hooks/useAuth.tsx`
  - Replace repeated enum filtering with `normalizePrincipalPermissions`.
- Create `frontend/web/src/components/layout/AppContent/phase1SurfacePolicy.ts`
  - Classifies each `TabType` as `reuse-current`, `remap-current`, `fail-closed-placeholder`, or `phase-2-backend`.
  - Exposes route/nav permission helpers shared by route guards and menus.
- Create `frontend/web/src/components/layout/AppContent/Phase2UnavailablePanel.tsx`
  - Shows a governed unavailable state and does not call backend APIs.
- Create `frontend/web/src/components/panels/AdminRuntimePanel.tsx`
  - Preserves existing Admin Runtime and system health projections without exposing legacy settings management.
- Create `frontend/web/src/services/api/phase1Projection.ts`
  - Centralizes Phase 1 remap calls to existing ai-platform projections.
  - Uses `/api/ai/admin/skills/*`, `/api/ai/admin/tool-policies`, `/api/ai/agent-apps`, `/api/agents`, `/api/agent/models/available`, `/api/agent/models/providers/list`, and `/api/notifications/active`.
  - Treats `/api/ai/agent-apps` as admin/governance-only; ordinary chat capability selection uses `/api/agents` public projections and submits public `agent_id` values through `/api/chat/stream`.
  - Does not call legacy `/api/skills/*`, `/api/github/*`, `/api/mcp/*`, `/api/agent/config/*`, or `/api/notifications/admin` routes.
- Create `frontend/web/src/components/panels/phase1ProjectionPanels.tsx`
  - Adds Phase 1 read-only or governed remap panels for Skills governance, Tool Policies, Agent Apps, Model Catalog, and Active Notifications.
- Modify `frontend/web/src/components/layout/AppContent/TabContent.tsx`
  - Stop lazy loading legacy unsupported panels.
  - Map unsupported tabs to `Phase2UnavailablePanel`.
  - Map `/settings` to `AdminRuntimePanel` because its active supported value is Admin Runtime, not legacy `/api/settings` editing.
  - Map `/skills`, `/mcp`, `/agents`, `/models`, and `/notifications` to Phase 1 projection panels instead of legacy CRUD panels.
- Modify `frontend/web/src/App.tsx`
  - Use the shared route permissions from `phase1SurfacePolicy`.
  - Keep unsupported routes permission-gated but fail-closed into placeholders after authorization.
- Modify `frontend/web/src/components/panels/SessionSidebar.tsx`
  - Use the shared nav policy for visible sidebar destinations.
  - Ensure memory nav requires both the memory setting and effective principal permission.
- Modify `frontend/web/src/components/layout/UserMenu.tsx`
  - Use the same shared nav policy as the sidebar.
- Modify `frontend/web/src/components/panels/AdminRuntimeCapacitySection.tsx`
  - Gate the Admin Runtime overview on `admin:status` and `settings:manage`.
- Modify `frontend/web/src/components/panels/SystemHealthSection.tsx`
  - Gate system health on the same admin runtime policy.
- Test `frontend/web/src/auth/__tests__/aiPlatformPermissions.test.ts`
  - Backend ai-platform permissions are not dropped.
  - `agent:use` grants effective chat/session/agent-read permissions without granting admin/user/role/MCP permissions.
  - `admin:status` grants Admin Runtime viewing permissions but not legacy settings CRUD.
- Test `frontend/web/src/components/layout/AppContent/__tests__/phase1SurfacePolicy.test.ts`
  - Backend-missing surfaces are classified and guarded consistently.
  - Already-backed admin/public projections are classified as `remap-current`.
  - Admin Runtime `/settings` is treated as a remapped current surface.
  - Ordinary users with `agent:use` can use chat-bound capabilities, while unsupported standalone management pages remain hidden from navigation.
- Modify `frontend/web/src/__tests__/aiPlatformLegacyRouteGuard.test.ts`
  - Assert `TabContent.tsx` no longer imports unsupported legacy panels.
  - Assert unsupported surface service modules remain out of active TabContent and Phase 1 remap panels.
  - Assert Admin Runtime still uses `/api/ai/admin/runtime/overview`.
- Modify `frontend/web/src/components/panels/__tests__/adminRuntimeCapacitySection.test.ts`
  - Update source assertions from `settings:manage` only to `admin:status` plus `settings:manage`.
- Add `docs/frontend/librechat-frontend-phase1-interface-matrix.md`
  - Records Phase 1 surface classification, current route family, and Phase 2 backend backlog.

## Task 1: Permission Normalization

**Files:**
- Create: `frontend/web/src/auth/aiPlatformPermissions.ts`
- Modify: `frontend/web/src/types/auth.ts`
- Modify: `frontend/web/src/hooks/useAuth.tsx`
- Test: `frontend/web/src/auth/__tests__/aiPlatformPermissions.test.ts`

- [ ] **Step 1: Write failing permission tests**

```ts
import test from "node:test";
import assert from "node:assert/strict";
import { Permission } from "../../types/auth.ts";
import {
  normalizePrincipalPermissions,
  hasEffectivePermission,
} from "../aiPlatformPermissions.ts";

test("keeps ai-platform principal permissions and derives existing UI permissions", () => {
  const permissions = normalizePrincipalPermissions([
    "agent:use",
    "artifact:download",
    "admin:status",
  ]);

  assert.ok(permissions.includes(Permission.AGENT_USE));
  assert.ok(permissions.includes(Permission.ARTIFACT_DOWNLOAD));
  assert.ok(permissions.includes(Permission.ADMIN_STATUS));
  assert.ok(permissions.includes(Permission.CHAT_READ));
  assert.ok(permissions.includes(Permission.CHAT_WRITE));
  assert.ok(permissions.includes(Permission.SESSION_READ));
  assert.ok(permissions.includes(Permission.SESSION_WRITE));
  assert.ok(permissions.includes(Permission.AGENT_READ));
  assert.ok(permissions.includes(Permission.FILE_UPLOAD_DOCUMENT));
  assert.ok(!permissions.includes(Permission.USER_READ));
  assert.ok(!permissions.includes(Permission.ROLE_MANAGE));
  assert.ok(!permissions.includes(Permission.MCP_READ));
});

test("admin status unlocks runtime viewing without legacy settings management", () => {
  const permissions = normalizePrincipalPermissions(["admin:status"]);

  assert.ok(hasEffectivePermission(permissions, Permission.ADMIN_STATUS));
  assert.ok(!hasEffectivePermission(permissions, Permission.SETTINGS_MANAGE));
});

test("unknown permissions are ignored but known legacy permissions are retained", () => {
  const permissions = normalizePrincipalPermissions([
    "skill:read",
    "unknown:permission",
  ]);

  assert.deepEqual(permissions, [Permission.SKILL_READ]);
});
```

- [ ] **Step 2: Run the new test and confirm it fails**

Run:

```powershell
corepack pnpm exec tsx --test src/auth/__tests__/aiPlatformPermissions.test.ts
```

Expected: fails because `aiPlatformPermissions.ts` does not exist and the enum lacks ai-platform values.

- [ ] **Step 3: Implement normalization**

Add the three enum values and implement a deterministic normalization helper that deduplicates in input order, then appends derived permissions.

- [ ] **Step 4: Wire `useAuth` to the helper**

Replace all repeated `currentUser.permissions.filter(...)` blocks with `normalizePrincipalPermissions(currentUser.permissions)`.

- [ ] **Step 5: Re-run the permission test**

Run:

```powershell
corepack pnpm exec tsx --test src/auth/__tests__/aiPlatformPermissions.test.ts
```

Expected: all tests pass.

## Task 2: Phase 1 Surface Policy

**Files:**
- Create: `frontend/web/src/components/layout/AppContent/phase1SurfacePolicy.ts`
- Test: `frontend/web/src/components/layout/AppContent/__tests__/phase1SurfacePolicy.test.ts`

- [ ] **Step 1: Write failing surface-policy tests**

```ts
import test from "node:test";
import assert from "node:assert/strict";
import { Permission } from "../../../../types/auth.ts";
import {
  getSurfacePolicy,
  getRoutePermissions,
  canShowSurfaceInNavigation,
  PHASE_2_TABS,
} from "../phase1SurfacePolicy.ts";

test("classifies only backend-missing surfaces as Phase 2", () => {
  assert.deepEqual(
    PHASE_2_TABS,
    [
      "marketplace",
      "users",
      "roles",
      "feedback",
      "channels",
      "files",
      "persona",
    ],
  );
  for (const tab of PHASE_2_TABS) {
    const policy = getSurfacePolicy(tab);
    assert.equal(policy.classification, "phase-2-backend");
    assert.equal(policy.render, "phase2-unavailable");
  }
});

test("remaps already-backed admin projections into Phase 1 panels", () => {
  const remapped = [
    ["skills", Permission.AGENT_ADMIN],
    ["mcp", Permission.ADMIN_STATUS],
    ["agents", Permission.AGENT_ADMIN],
    ["models", Permission.MODEL_ADMIN],
    ["notifications", Permission.ADMIN_STATUS],
  ] as const;

  for (const [tab, permission] of remapped) {
    const policy = getSurfacePolicy(tab);
    assert.equal(policy.classification, "remap-current");
    assert.equal(policy.render, tab);
    assert.deepEqual(getRoutePermissions(tab), [permission]);
    assert.equal(canShowSurfaceInNavigation(tab, [permission]), true);
  }
});

test("settings route is remapped to current Admin Runtime projections", () => {
  const policy = getSurfacePolicy("settings");

  assert.equal(policy.classification, "remap-current");
  assert.equal(policy.render, "admin-runtime");
  assert.deepEqual(getRoutePermissions("settings"), [
    Permission.ADMIN_STATUS,
    Permission.SETTINGS_MANAGE,
  ]);
});

test("agent use is enough for chat but not unsupported independent pages", () => {
  const permissions = [
    Permission.AGENT_USE,
    Permission.CHAT_READ,
    Permission.CHAT_WRITE,
    Permission.SESSION_READ,
    Permission.SESSION_WRITE,
    Permission.SKILL_READ,
  ];

  assert.equal(canShowSurfaceInNavigation("memory", permissions, true), true);
  assert.equal(canShowSurfaceInNavigation("skills", permissions), false);
  assert.equal(canShowSurfaceInNavigation("files", permissions), false);
  assert.equal(canShowSurfaceInNavigation("persona", permissions), false);
  assert.equal(canShowSurfaceInNavigation("settings", permissions), false);
  assert.equal(canShowSurfaceInNavigation("users", permissions), false);
});
```

- [ ] **Step 2: Run the new test and confirm it fails**

Run:

```powershell
corepack pnpm exec tsx --test src/components/layout/AppContent/__tests__/phase1SurfacePolicy.test.ts
```

Expected: fails because the policy module does not exist.

- [ ] **Step 3: Implement the surface policy**

Policy choices:

- `reuse-current`: `memory`.
- `remap-current`: `settings` as Admin Runtime, `skills` as admin Skills governance, `mcp` as admin Tool Policies, `agents` as admin Agent Apps plus public capability projection, `models` as read-only model catalog, and `notifications` as active notifications.
- `phase-2-backend`: `marketplace`, `users`, `roles`, `feedback`, `channels`, `files`, `persona`.
- `chat`: authenticated only.
- Chat-bound capabilities such as agent list, model availability, upload, run playback, artifacts, tool permissions, and capability suggestions remain active inside the chat workflow when existing ai-platform or compat routes support them.

- [ ] **Step 4: Re-run the policy test**

Run:

```powershell
corepack pnpm exec tsx --test src/components/layout/AppContent/__tests__/phase1SurfacePolicy.test.ts
```

Expected: all tests pass.

## Task 3: Panels And Active Route Graph

**Files:**
- Create: `frontend/web/src/components/layout/AppContent/Phase2UnavailablePanel.tsx`
- Create: `frontend/web/src/components/panels/AdminRuntimePanel.tsx`
- Modify: `frontend/web/src/components/layout/AppContent/TabContent.tsx`
- Modify: `frontend/web/src/components/panels/AdminRuntimeCapacitySection.tsx`
- Modify: `frontend/web/src/components/panels/SystemHealthSection.tsx`
- Modify tests:
  - `frontend/web/src/__tests__/aiPlatformLegacyRouteGuard.test.ts`
  - `frontend/web/src/components/panels/__tests__/adminRuntimeCapacitySection.test.ts`

- [ ] **Step 1: Extend source-guard tests**

Assert `TabContent.tsx` does not import or render `SkillsHubPanel`, `UsersPanel`, `RolesPanel`, `SettingsPanel`, `MCPPanel`, `MarketplacePanel`, `NotificationPanel`, `ChannelPanel`, `AgentConfigPanel`, or `ModelPanel`; assert it imports `Phase2UnavailablePanel`, `AdminRuntimePanel`, and the Phase 1 projection panels.

- [ ] **Step 2: Run the source-guard tests and confirm failure**

Run:

```powershell
corepack pnpm exec tsx --test src/__tests__/aiPlatformLegacyRouteGuard.test.ts src/components/panels/__tests__/adminRuntimeCapacitySection.test.ts
```

Expected: fails because `TabContent.tsx` still imports unsupported legacy panels and Admin Runtime is gated only by settings management.

- [ ] **Step 3: Implement panels and tab mapping**

Use lucide icons, existing theme tokens, accessible headings, and compact enterprise layout. `Phase2UnavailablePanel` receives a `tab` prop, renders no children that call APIs, and links users back to chat. `AdminRuntimePanel` renders `PanelHeader`, `SystemHealthSection`, and `AdminRuntimeCapacitySection`. Phase 1 projection panels are read-only or governed remaps over existing ai-platform APIs and must not import old management panels.

- [ ] **Step 4: Update Admin Runtime guards**

Allow viewing when the normalized permission list contains `admin:status` or `settings:manage`; do not grant settings write behavior from `admin:status`.

- [ ] **Step 5: Re-run source-guard tests**

Run:

```powershell
corepack pnpm exec tsx --test src/__tests__/aiPlatformLegacyRouteGuard.test.ts src/components/panels/__tests__/adminRuntimeCapacitySection.test.ts
```

Expected: all tests pass.

## Task 4: Routes And Navigation

**Files:**
- Modify: `frontend/web/src/App.tsx`
- Modify: `frontend/web/src/components/panels/SessionSidebar.tsx`
- Modify: `frontend/web/src/components/layout/UserMenu.tsx`
- Test: covered by `phase1SurfacePolicy.test.ts` and source guards.

- [ ] **Step 1: Use shared route permissions in `App.tsx`**

Replace route-level hardcoded permission arrays with `getRoutePermissions(tab)`. Keep `/models` protected instead of authenticated-only.

- [ ] **Step 2: Use shared navigation decisions in `SessionSidebar.tsx`**

Replace local duplicated `canManageUsers`, `canReadMCP`, `canReadMemory`, and related checks with `canShowSurfaceInNavigation`.

- [ ] **Step 3: Use shared navigation decisions in `UserMenu.tsx`**

Apply the same policy used by the sidebar. Memory must require both `enableMemory` and principal permission.

- [ ] **Step 4: Re-run policy and guard tests**

Run:

```powershell
corepack pnpm exec tsx --test src/auth/__tests__/aiPlatformPermissions.test.ts src/components/layout/AppContent/__tests__/phase1SurfacePolicy.test.ts src/__tests__/aiPlatformLegacyRouteGuard.test.ts
```

Expected: all tests pass.

## Task 5: Interface Matrix

**Files:**
- Create: `docs/frontend/librechat-frontend-phase1-interface-matrix.md`

- [ ] **Step 1: Document active Phase 1 mapping**

Include each active surface, classification, current frontend service, backend route family, Phase 1 behavior, and Phase 2 trigger.

- [ ] **Step 2: Document Phase 2 backlog**

List department Skill marketplace, file-library projection, persona presets, feedback, company users/roles, model admin CRUD, settings CRUD, notification CRUD, MCP server lifecycle, and channel administration as backend-backed Phase 2 work. Existing Phase 1 remaps for admin Skills governance, admin tool policies, agent-app projections, model catalog reads, and active notification reads must remain separate from those Phase 2 write/lifecycle contracts.

- [ ] **Step 3: Check for personal absolute paths**

Run:

```powershell
rg "C:\\\\Users|C:\\\\aiwt" docs/frontend/librechat-frontend-phase1-interface-matrix.md docs/superpowers/plans/2026-06-18-librechat-frontend-phase1.md
```

Expected: no matches.

## Task 6: Verification

**Files:** no new files.

- [ ] **Step 1: Run focused frontend tests**

Run:

```powershell
corepack pnpm exec tsx --test src/auth/__tests__/aiPlatformPermissions.test.ts src/components/layout/AppContent/__tests__/phase1SurfacePolicy.test.ts src/__tests__/aiPlatformLegacyRouteGuard.test.ts src/components/panels/__tests__/adminRuntimeCapacitySection.test.ts src/components/layout/AppContent/__tests__/skillAvailability.test.ts
```

Expected: all tests pass.

- [ ] **Step 2: Run projection audit**

Run:

```powershell
corepack pnpm run projection:audit
```

Expected: exits 0. Existing `pass_with_policy_gaps` is acceptable only if the command exits 0 and does not introduce new active-browser private projection violations.

- [ ] **Step 3: Run lint**

Run:

```powershell
corepack pnpm run lint
```

Expected: exits 0.

- [ ] **Step 4: Run build**

Run:

```powershell
corepack pnpm run build
```

Expected: exits 0.

- [ ] **Step 5: Run diff hygiene check**

Run:

```powershell
git diff --check
```

Expected: exits 0.

## Self-Review Checklist

- [ ] No Phase 2 backend-missing surface calls legacy product-authority APIs from active `TabContent`.
- [ ] Phase 1 remap panels call only existing ai-platform public/admin projections.
- [ ] ai-platform principal permissions are not silently dropped.
- [ ] `agent:use` does not imply user/role/MCP/settings/model admin permissions.
- [ ] `admin:status` can view Admin Runtime but does not grant settings CRUD.
- [ ] Route guards and navigation use the same policy source.
- [ ] Interface matrix uses repo-relative paths only.
- [ ] No frontend code reads raw runtime paths, raw storage keys, sandbox workdirs, private executor payloads, provider secrets, raw queue payloads, or raw tool decision payloads.
