# AI Platform Frontend Complete Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the authenticated frontend into a coherent enterprise workbench across all post-login routes, with explicit governance states and backend gaps filed as follow-up issues.

**Architecture:** Keep the chat-first shell from PR #175, reuse the public Skills/Marketplace contracts from PR #177, and add a small shared workbench page/state layer instead of styling each panel independently. Pages must resolve to `logged-out`, `loading`, `no-workspace`, `forbidden`, `degraded`, or `ready`, with route-level forbidden states preserving the shell instead of redirecting users away.

**Tech Stack:** React 19, Vite, TypeScript, Tailwind utility classes, lucide-react icons, repository-native node:test source-contract tests, pnpm build.

## Global Constraints

- Work only in the dedicated frontend-complete worktree on `codex/frontend-complete-pass-20260623`.
- Keep `dist`, `node_modules`, `.superpowers`, `.codex-tmp`, `.pytest-tmp`, smoke artifacts, credentials, and real `.env` values out of git.
- Preserve exact ai-platform status labels: `local partial`, `PR ready`, `reviewed`, `merged`, `211 verified`, `gate closable`.
- Do not claim `211 verified` for this pass until a fresh deploy and smoke proves the new commit.
- The 211 frontend is a Python static service on `10.56.0.211:18001`, not a Docker frontend container.

---

### Task 1: Shared Workbench State And Page Surface

**Files:**
- Modify: `frontend/web/src/components/governance/frontendGovernanceState.ts`
- Create: `frontend/web/src/components/workbench/WorkbenchStateSurface.tsx`
- Modify: `frontend/web/src/components/workbench/WorkbenchUnavailableState.tsx`
- Modify: `frontend/web/src/components/workbench/workbenchSurface.ts`
- Modify: `frontend/web/src/components/layout/AppContent/types.ts`
- Modify: `frontend/web/src/components/layout/AppContent/TabContent.tsx`
- Modify: `frontend/web/src/i18n/locales/en.json`
- Modify: `frontend/web/src/i18n/locales/zh.json`
- Test: `frontend/web/src/components/governance/__tests__/frontendGovernanceState.test.ts`
- Test: `frontend/web/src/components/layout/AppContent/__tests__/routeUnavailableSource.test.ts`

**Interfaces:**
- Consumes: `FrontendGovernanceState` from `frontendGovernanceState.ts`.
- Produces: `WorkbenchStateSurface`, a single state renderer used by route-level and page-level unavailable/degraded surfaces.

- [ ] Extend `RouteUnavailableConfig.state` to include every `FrontendGovernanceState`.
- [ ] Add `WorkbenchStateSurface` with stable data attributes: `data-workbench-state-surface`, `data-frontend-governance-state`, and `data-fail-closed-surface`.
- [ ] Make `WorkbenchUnavailableState` a compatibility wrapper over `WorkbenchStateSurface`.
- [ ] Update `TabContent` route-unavailable rendering to use the shared state surface.
- [ ] Add i18n fallback labels for all six states.
- [ ] Update source-contract tests to assert the full state set and route-level shell behavior.

### Task 2: Authenticated Shell Visual Convergence

**Files:**
- Modify: `frontend/web/src/styles/base.css`
- Modify: `frontend/web/src/styles/components.css`
- Modify: `frontend/web/src/styles/glass.css`
- Modify: `frontend/web/src/components/layout/AppContent/AppShell.tsx`
- Modify: `frontend/web/src/components/layout/AppContent/Header.tsx`
- Modify: `frontend/web/src/components/panels/SessionSidebar.tsx`
- Modify: `frontend/web/src/components/panels/SidebarParts/SessionListContent.tsx`
- Modify: `frontend/web/src/components/panels/SidebarParts/SidebarRail.tsx`
- Test: `frontend/web/src/__tests__/frontendShellParityAcceptance.test.ts`

**Interfaces:**
- Consumes: existing CSS variables `--theme-bg`, `--theme-bg-sidebar`, `--theme-bg-card`, and `--theme-border`.
- Produces: one enterprise B2B surface vocabulary for sidebar, header, page body, cards, buttons, and search controls.

- [ ] Normalize the light-mode workbench so main canvas and sidebar are close variants, not a white/grey split.
- [ ] Keep cards at 8px radius or less and use subtle shadows only for repeated item cards.
- [ ] Remove remaining old LambChat/playful branding cues from authenticated chrome.
- [ ] Ensure rail/full sidebar expose Company Apps, Skills Marketplace, and MCP without old persona/files shortcuts.
- [ ] Update source-contract tests for the selected surface tokens.

### Task 3: Page-Level Ready, Empty, Forbidden, And Degraded States

**Files:**
- Modify: `frontend/web/src/components/launchpad/LaunchpadPanel.tsx`
- Modify: `frontend/web/src/components/panels/SkillsHubPanel.tsx`
- Modify: `frontend/web/src/components/panels/MarketplacePanel.tsx`
- Modify: `frontend/web/src/components/panels/SkillsPanel/index.tsx`
- Modify: `frontend/web/src/components/panels/SkillsPanel/SkillsList.tsx`
- Modify: `frontend/web/src/components/panels/MCPPanel.tsx`
- Modify: `frontend/web/src/components/panels/AgentPanel/AgentConfigPanel.tsx`
- Modify: `frontend/web/src/components/panels/ModelPanel/ModelPanel.tsx`
- Modify: `frontend/web/src/components/panels/MemoryPanel/index.tsx`
- Test: `frontend/web/src/components/panels/__tests__/governancePhase1Closure.test.ts`
- Test: `frontend/web/src/__tests__/frontendShellParityAcceptance.test.ts`

**Interfaces:**
- Consumes: public skills/marketplace contracts, role/model/agent APIs, memory APIs, and MCP list projection.
- Produces: polished ready/degraded/forbidden/empty rendering for all user-visible workbench pages.

- [ ] Convert launchpad to shared workbench background, header, search, tab, and card tokens.
- [ ] Keep Skills/Marketplace readable when permissions allow, fail-closed when unavailable, and remove unsupported write affordances.
- [ ] Make MCP lifecycle, credential, and department enablement controls visibly governed instead of looking broken.
- [ ] Route model no-permission and agent ordinary-user states through `WorkbenchStateSurface`.
- [ ] Make Memory use localized copy and the same state/empty/card vocabulary.

### Task 4: Backend Gap Evidence And Issue

**Files:**
- Create or update: `docs/release-evidence/frontend-complete/backend-gap-summary.md`

**Interfaces:**
- Consumes: current frontend service calls, current app route files, PR #177 evidence, and live/current OpenAPI where available.
- Produces: one backend follow-up issue if missing backend work remains.

- [ ] Compare frontend fail-closed controls against actual backend routes/contracts.
- [ ] Separate implemented contracts from intentionally missing phase-two capabilities.
- [ ] File one GitHub issue for backend follow-up covering missing durable skill write storage, department skill policy, MCP lifecycle/tool governance, and approval/request flows if still absent.

### Task 5: Verification, Build, PR, And 211 Preview Deploy

**Files:**
- Modify only generated build provenance inside `frontend/web/dist` during build; do not commit `dist`.

**Interfaces:**
- Consumes: completed frontend code and 211 static-service deployment path.
- Produces: pushed branch, PR update, fresh build artifact, and 211 preview evidence.

- [ ] Run targeted frontend source-contract tests.
- [ ] Run `pnpm run ci:verify` in `frontend/web`.
- [ ] Run `git diff --check`.
- [ ] Browser smoke authenticated shell routes in preview across `/chat`, `/apps`, `/skills`, `/marketplace`, `/mcp`, `/roles`, `/models`, `/memory`.
- [ ] Commit, push, open/update PR.
- [ ] If verification and PR are clean, package frontend dist, deploy to 211 static service, and smoke `http://10.56.0.211:18001/`.
