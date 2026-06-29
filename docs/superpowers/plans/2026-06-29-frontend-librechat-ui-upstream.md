# Frontend LibreChat UI Upstream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move ai-platform frontend from LibreChat-inspired shell styling to a pinned, testable LibreChat UI upstream module with ai-platform-owned data and governance seams.

**Architecture:** `frontend/web/src/librechat-ui/` is the pure UI upstream module. It records the pinned LibreChat commit, exposes shell/sidebar/composer/right-panel primitives, and defines the `ChatWorkbenchAdapter` interface. Existing workbench, sidebar, and right-panel code consume that module; backend authority remains outside the module.

**Tech Stack:** React 19, TypeScript, Vite, Tailwind, Node `tsx --test`, existing ai-platform frontend source tests.

## Global Constraints

- Do not commit `.env`, credentials, `dist`, `node_modules`, `.pytest-tmp`, `.codex-tmp`, local screenshots, or ad-hoc smoke artifacts.
- Do not print or document login credentials.
- Do not import LibreChat API hooks, data-provider contracts, auth/session/RBAC logic, Mongo schemas, provider config, or secrets.
- Keep status labels exact: `PR ready`, `merged`, `211 verified`, and `gate closable` are separate states.
- `211 verified` requires fresh provenance and smoke evidence; this frontend source PR does not claim 211 deployment unless deployment is explicitly performed.

---

### Task 1: Pin The LibreChat UI Upstream

**Files:**
- Create: `frontend/web/src/librechat-ui/source.ts`
- Create: `frontend/web/src/librechat-ui/NOTICE.md`
- Create: `docs/release-evidence/frontend-shell-parity/librechat-source.md`
- Test: `frontend/web/src/librechat-ui/__tests__/libreChatUiUpstream.test.ts`

**Interfaces:**
- Produces: `LIBRECHAT_UI_SOURCE`, `LIBRECHAT_UI_REFERENCE_NOTICE`

- [x] **Step 1: Write the failing provenance test**

```ts
assert.equal(existsSync(join(root, "src/librechat-ui/source.ts")), true);
assert.match(source, /9e74cc0e57b395926122bd4062c1fcedc48ed465/);
assert.match(source, /MIT/);
```

- [x] **Step 2: Run test to verify it fails**

Run: `pnpm exec tsx --test src/librechat-ui/__tests__/libreChatUiUpstream.test.ts`
Expected: FAIL because `src/librechat-ui/source.ts` does not exist.

- [x] **Step 3: Implement source pin and notice**

Create `source.ts`, `NOTICE.md`, and `docs/release-evidence/frontend-shell-parity/librechat-source.md` with repository, commit, license, allowed intake, forbidden intake, and local mapping.

- [x] **Step 4: Run test to verify it passes**

Run: `pnpm exec tsx --test src/librechat-ui/__tests__/libreChatUiUpstream.test.ts`
Expected: PASS for provenance assertions.

### Task 2: Define The ai-platform Adapter Seam

**Files:**
- Create: `frontend/web/src/librechat-ui/adapter.ts`
- Test: `frontend/web/src/librechat-ui/__tests__/libreChatUiUpstream.test.ts`

**Interfaces:**
- Produces: `ChatWorkbenchAdapter`, `SessionSummary`, `ChatMessage`, `ComposerChip`, `ComposerInput`, `RunEventSubscription`

- [x] **Step 1: Write the failing adapter test**

```ts
assert.match(adapter, /export interface ChatWorkbenchAdapter/);
assert.match(adapter, /sendMessage\(input:\s*ComposerInput\):\s*Promise<void>/);
assert.match(adapter, /subscribeRunEvents\(runId:\s*string\):\s*RunEventSubscription/);
```

- [x] **Step 2: Run test to verify it fails**

Run: `pnpm exec tsx --test src/librechat-ui/__tests__/libreChatUiUpstream.test.ts`
Expected: FAIL because `adapter.ts` does not exist.

- [x] **Step 3: Implement adapter interface**

Create `adapter.ts` with ai-platform-owned types and no LibreChat backend imports.

- [x] **Step 4: Run test to verify it passes**

Run: `pnpm exec tsx --test src/librechat-ui/__tests__/libreChatUiUpstream.test.ts`
Expected: PASS for adapter assertions and forbidden import checks.

### Task 3: Move Pure UI Primitives To `src/librechat-ui`

**Files:**
- Create: `frontend/web/src/librechat-ui/surface.ts`
- Create: `frontend/web/src/librechat-ui/Shell.tsx`
- Create: `frontend/web/src/librechat-ui/Rail.tsx`
- Create: `frontend/web/src/librechat-ui/Panel.tsx`
- Create: `frontend/web/src/librechat-ui/SidePanel.tsx`
- Create: `frontend/web/src/librechat-ui/index.ts`
- Modify: `frontend/web/src/components/librechatShell/*.tsx`
- Test: `frontend/web/src/librechat-ui/__tests__/libreChatUiUpstream.test.ts`

**Interfaces:**
- Consumes: `LIBRECHAT_UI_SOURCE`
- Produces: `LIBRECHAT_SHELL_REFERENCE`, `LIBRECHAT_SHELL_GEOMETRY`, `libreChatSurface`, `LibreChatShell`, `LibreChatRailButton`, `LibreChatPanelSection`, `LibreChatSidePanel`

- [x] **Step 1: Write the failing module-shape test**

```ts
for (const path of [
  "src/librechat-ui/surface.ts",
  "src/librechat-ui/Shell.tsx",
  "src/librechat-ui/Rail.tsx",
  "src/librechat-ui/Panel.tsx",
  "src/librechat-ui/SidePanel.tsx",
]) {
  assert.equal(existsSync(join(root, path)), true);
}
```

- [x] **Step 2: Run test to verify it fails**

Run: `pnpm exec tsx --test src/librechat-ui/__tests__/libreChatUiUpstream.test.ts`
Expected: FAIL because UI primitive files do not exist.

- [x] **Step 3: Implement primitives and compatibility re-exports**

Move pure UI implementation into `src/librechat-ui/*`. Keep `src/components/librechatShell/*` as compatibility re-exports only.

- [x] **Step 4: Run test to verify it passes**

Run: `pnpm exec tsx --test src/librechat-ui/__tests__/libreChatUiUpstream.test.ts`
Expected: PASS for module shape and forbidden import checks.

### Task 4: Point Active Workbench At The New Module

**Files:**
- Modify: `frontend/web/src/components/workbench/WorkbenchShell.tsx`
- Modify: `frontend/web/src/components/workbench/WorkbenchRightPanel.tsx`
- Modify: `frontend/web/src/components/workbench/workbenchSurface.ts`
- Modify: `frontend/web/src/components/panels/SessionSidebar.tsx`
- Modify: `frontend/web/src/components/panels/SidebarParts/SessionListContent.tsx`
- Modify: `frontend/web/src/components/panels/SidebarParts/SidebarRail.tsx`
- Test: `frontend/web/src/librechat-ui/__tests__/libreChatUiUpstream.test.ts`

**Interfaces:**
- Consumes: `frontend/web/src/librechat-ui/*`
- Produces: active workbench imports with no dependency on legacy `librechatShell`

- [x] **Step 1: Write the failing active-consumer test**

```ts
assert.match(source, /librechat-ui/);
assert.doesNotMatch(source, /librechatShell/);
```

- [x] **Step 2: Run test to verify it fails**

Run: `pnpm exec tsx --test src/librechat-ui/__tests__/libreChatUiUpstream.test.ts`
Expected: FAIL because active files still import `librechatShell`.

- [x] **Step 3: Update imports**

Point active workbench/sidebar files to `src/librechat-ui` submodules.

- [x] **Step 4: Run test to verify it passes**

Run: `pnpm exec tsx --test src/librechat-ui/__tests__/libreChatUiUpstream.test.ts`
Expected: PASS for active-consumer checks.

### Task 5: Verify Governance State Coverage

**Files:**
- Inspect: `frontend/web/src/components/governance/frontendGovernanceState.ts`
- Inspect: `frontend/web/src/components/governance/__tests__/frontendGovernanceState.test.ts`

**Interfaces:**
- Consumes: `FrontendGovernanceState`, `FRONTEND_GOVERNANCE_SMOKE_STATES`
- Produces: evidence that `logged-out`, `loading`, `no-workspace`, `forbidden`, `degraded`, and `ready` each have smoke selectors

- [x] **Step 1: Inspect state machine coverage**

Confirm tests cover all six states and smoke attributes.

- [x] **Step 2: Run governance test**

Run: `pnpm exec tsx --test src/components/governance/__tests__/frontendGovernanceState.test.ts`
Expected: PASS.

### Task 6: Final Verification And PR

**Files:**
- No generated `dist` or smoke screenshots are committed.

**Interfaces:**
- Produces: pushed branch and PR with exact status language.

- [x] **Step 1: Run focused frontend tests**

Run:
```powershell
pnpm exec tsx --test src/librechat-ui/__tests__/libreChatUiUpstream.test.ts
pnpm exec tsx --test src/components/governance/__tests__/frontendGovernanceState.test.ts
pnpm exec tsx src/components/librechatShell/__tests__/libreChatShellSource.test.ts
pnpm exec tsx src/components/librechatShell/__tests__/libreChatShellLayout.test.ts
pnpm exec tsx src/components/workbench/__tests__/workbenchVisualClosure.test.ts
```

- [x] **Step 2: Run build and CI verification**

Run:
```powershell
pnpm run build
pnpm run ci:verify
python -m compileall -q app tools scripts
git diff --check
```

- [ ] **Step 3: Commit and push**

Commit message:
```text
[codex] Add LibreChat UI upstream seam
```

- [ ] **Step 4: Open PR**

PR status language:
```text
local verified
not deployed to 211
not 211 verified
not gate closable
```
