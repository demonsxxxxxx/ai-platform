# Post-177 Skills Workbench Frontend Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for inline execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the post-login Skills and Marketplace workbench consume the merged PR #177 public contract cleanly, with explicit frontend governance states and a more coherent enterprise shell.

**Architecture:** Keep `/skills` and `/marketplace` inside the existing shared `SkillsHubPanel`. Treat backend `effective_permissions` from the public Skills list as the authoritative frontend projection once loaded, while still fail-closing on catalog permission errors. Keep existing API clients; only lift state, refine the shell, and update tests.

**Tech Stack:** React 19, TypeScript, Vite, Node built-in test runner through `tsx`, Tailwind utility classes, existing ai-platform workbench tokens.

## Global Constraints

- Scope is post-login frontend only; do not change login pages or backend routes.
- Do not reimplement PR #177 API clients; use existing `useSkills`, `useMarketplace`, `skillApi`, and `marketplaceApi`.
- Preserve fail-closed behavior: catalog API permission denial maps to `forbidden`; unrelated projection errors map to `degraded`.
- Ordinary readable catalog state should be `ready` once backend effective permissions include `skill:read` or `marketplace:read`.
- Do not reintroduce `/persona` or `/files`; current source redirects them to `/chat`.
- Do not deploy to 211 until local build and verification pass.

---

### Task 1: Lift Backend Effective Permissions Into SkillsHub

**Files:**
- Modify: `frontend/web/src/components/panels/SkillsHubPanel/state.ts`
- Modify: `frontend/web/src/components/panels/SkillsHubPanel.tsx`
- Modify: `frontend/web/src/components/panels/SkillsPanel/index.tsx`
- Modify: `frontend/web/src/components/panels/MarketplacePanel.tsx`
- Test: `frontend/web/src/components/panels/SkillsHubPanel/__tests__/state.test.ts`
- Test: `frontend/web/src/components/panels/__tests__/governancePhase1Closure.test.ts`

**Interfaces:**
- Consumes: `actions.effectivePermissions` from `useSkillsActions`, `userEffectivePermissions` from `useSkills` inside Marketplace.
- Produces: `CatalogState.effectivePermissions: string[]`, `resolveSkillsHubGovernance({ effectivePermissions })`.

- [ ] **Step 1: Write RED tests**

Add assertions that backend effective permissions make `/skills` and `/marketplace` ready even when `useAuth()` has not projected those permissions yet, and that the hub receives `effectivePermissions` from child panels.

- [ ] **Step 2: Run the focused tests and observe failure**

Run: `pnpm exec tsx --test src/components/panels/SkillsHubPanel/__tests__/state.test.ts src/components/panels/__tests__/governancePhase1Closure.test.ts`
Expected before implementation: FAIL because `effectivePermissions` is not supported by the hub state contract.

- [ ] **Step 3: Implement minimal state lifting**

Extend catalog state with `effectivePermissions`, merge it with auth permission checks in `resolveSkillsHubGovernance`, and send the current effective permission list from `SkillsPanel` and `MarketplacePanel` through `onCatalogStateChange`.

- [ ] **Step 4: Re-run focused tests**

Run: `pnpm exec tsx --test src/components/panels/SkillsHubPanel/__tests__/state.test.ts src/components/panels/__tests__/governancePhase1Closure.test.ts`
Expected after implementation: PASS.

### Task 2: Tighten SkillsHub Visual Shell

**Files:**
- Modify: `frontend/web/src/components/panels/SkillsHubPanel.tsx`
- Modify: `frontend/web/src/i18n/locales/zh.json`
- Modify: `frontend/web/src/i18n/locales/en.json`
- Test: `frontend/web/src/components/panels/__tests__/governancePhase1Closure.test.ts`

**Interfaces:**
- Consumes: `hubGovernance.pageState`, `hubGovernance.requiredPermission`, existing `WorkbenchStateSurface` and `workbenchSurface` tokens.
- Produces: consistent light enterprise hub with explicit state details and no obsolete backend-contract wording.

- [ ] **Step 1: Write RED source-level assertions**

Assert that the hub exposes effective permission data attributes, unified surface classes, and state details for `ready`, `degraded`, and permission-limited states.

- [ ] **Step 2: Run the focused governance closure test and observe failure**

Run: `pnpm exec tsx --test src/components/panels/__tests__/governancePhase1Closure.test.ts`
Expected before implementation: FAIL on missing source markers.

- [ ] **Step 3: Implement shell refinements**

Use `workbenchSurface.panel` for the left in-panel sidebar, add concise state detail rows, and update Chinese/English copy to describe user-facing state without implementation jargon.

- [ ] **Step 4: Re-run focused test**

Run: `pnpm exec tsx --test src/components/panels/__tests__/governancePhase1Closure.test.ts`
Expected after implementation: PASS.

### Task 3: Verify, Commit, Push, And Prepare Preview

**Files:**
- Modified files from Tasks 1 and 2 only.

- [ ] **Step 1: Run frontend focused tests**

Run: `pnpm exec tsx --test src/components/panels/SkillsHubPanel/__tests__/state.test.ts src/components/panels/__tests__/governancePhase1Closure.test.ts`
Expected: PASS.

- [ ] **Step 2: Run projection audit**

Run: `pnpm run projection:audit`
Expected: exit 0; policy gaps may remain as JSON status if existing.

- [ ] **Step 3: Run build**

Run: `pnpm run build`
Expected: exit 0 and fresh `dist/` provenance written locally.

- [ ] **Step 4: Check diff hygiene**

Run: `git diff --check`
Expected: exit 0.

- [ ] **Step 5: Inspect staged scope before commit**

Run: `git status --short` and review changed files. Do not stage `dist`, `node_modules`, `.codex`, `.superpowers`, `.pytest-tmp`, generated smoke summaries, or credentials.

- [ ] **Step 6: Commit local frontend slice**

Commit message: `feat: consume public skills permissions in workbench hub`.

- [ ] **Step 7: Resolve GitHub main-base risk before pushing**

Because local `6ab891a` has the same tree as GitHub `9140fa1` but not the same commit object, either fetch `9140fa1` successfully or create/push a branch that GitHub recognizes as based on `9140fa1`. Do not push a branch that would include PR #230 diff again.
