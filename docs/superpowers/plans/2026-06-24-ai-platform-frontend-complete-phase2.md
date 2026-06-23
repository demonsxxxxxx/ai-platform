# AI Platform Frontend Complete Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** converge the authenticated post-login frontend into one enterprise workbench that uses PR #177 public Skills/Marketplace contracts and clearly separates backed pages from governed backend gaps.

**Architecture:** Keep the current dark rail plus light enterprise workbench shell as the only visual direction. Backed public contracts render real pages; missing or admin-only backend contracts render the shared frontend governance state machine instead of loading legacy admin pages. The `/agents` route uses a public read-only directory backed by `/api/agents`, while `/users`, `/settings`, `/feedback`, and `/notifications` stay visible as governed state surfaces until audited backend projections exist.

**Tech Stack:** React 19, Vite, TypeScript, Tailwind utility classes, existing `authFetch` API clients, Node `node:test` static acceptance checks.

## Global Constraints

- Work only in `C:\aiwt\postmerge-199-main` on branch `codex/frontend-complete-phase2-20260624`.
- Preserve status labels: `local partial`, `PR ready`, `reviewed`, `merged`, `211 verified`, `gate closable`.
- Do not reintroduce LambChat, glass, persona plaza, file library, or "more" menu surfaces.
- Keep `/api/skills/*` and `/api/marketplace/*` on PR #177 public contracts; do not call `/api/ai/admin/skills/*`.
- Do not expose legacy `/api/users`, `/api/settings`, `/api/feedback`, `/api/notifications/admin`, `/api/agent/config`, or `/api/roles` from ordinary-user reachable pages unless the route is permission-gated or remapped to an ai-platform public projection.
- Use the frontend governance states `logged-out`, `loading`, `no-workspace`, `forbidden`, `degraded`, and `ready`.
- Keep enterprise UI restrained: no nested cards, no decorative gradient/orb backgrounds, no large hero treatment inside tools, no card radius above 8px.

---

### Task 1: Public Agent Directory

**Files:**
- Modify: `frontend/web/src/__tests__/frontendShellParityAcceptance.test.ts`
- Modify: `frontend/web/src/App.tsx`
- Modify: `frontend/web/src/components/layout/AppContent/TabContent.tsx`
- Create: `frontend/web/src/components/panels/AgentDirectoryPanel.tsx`

**Interfaces:**
- Consumes: `agentApi.list(): Promise<AgentListResponse>` from `frontend/web/src/services/api/agent.ts`.
- Produces: a lazily registered `AgentDirectoryPanel` mapped to `activeTab="agents"` without importing legacy `agentConfigApi`, `roleApi`, or `/api/agent/config`.

- [ ] **Step 1: Write failing tests**

Add assertions that `/agents` renders through `AppContent`, `TabContent` maps `agents: AgentDirectoryPanel`, and `AgentDirectoryPanel` only uses `agentApi.list()`.

- [ ] **Step 2: Run RED test**

Run: `pnpm exec tsx src/__tests__/frontendShellParityAcceptance.test.ts`

Expected: FAIL because `AgentDirectoryPanel` is not registered.

- [ ] **Step 3: Implement directory panel**

Create a read-only panel with `PanelHeader`, `WorkbenchStateSurface`, and compact agent cards. It must render loading, forbidden, degraded, empty, and ready states.

- [ ] **Step 4: Run GREEN test**

Run: `pnpm exec tsx src/__tests__/frontendShellParityAcceptance.test.ts`

Expected: PASS.

### Task 2: Governed Phase 2 Capability Matrix

**Files:**
- Modify: `frontend/web/src/__tests__/frontendShellParityAcceptance.test.ts`
- Modify: `frontend/web/src/App.tsx`
- Modify: `frontend/web/src/components/layout/AppContent/types.ts`
- Modify: `frontend/web/src/components/layout/AppContent/TabContent.tsx`
- Modify: `frontend/web/src/components/workbench/WorkbenchStateSurface.tsx`

**Interfaces:**
- Consumes: existing localized capability strings under `workbench.phaseTwo.*.capabilities`.
- Produces: `routeUnavailable.capabilities` rows with governance badge states and `data-workbench-state-capability`.

- [ ] **Step 1: Write failing tests**

Assert each governed phase2 page has capability states and the state surface renders `GovernanceAvailabilityBadge`.

- [ ] **Step 2: Run RED test**

Run: `pnpm exec tsx src/__tests__/frontendShellParityAcceptance.test.ts`

Expected: FAIL because capability rows do not exist yet.

- [ ] **Step 3: Implement capability matrix**

Add typed capability rows and render them in `WorkbenchStateSurface` under the existing copy.

- [ ] **Step 4: Run GREEN test**

Run: `pnpm exec tsx src/__tests__/frontendShellParityAcceptance.test.ts`

Expected: PASS.

### Task 3: Launchpad Narrow-Viewport Navigation

**Files:**
- Modify: `frontend/web/src/__tests__/frontendShellParityAcceptance.test.ts`
- Modify: `frontend/web/src/components/launchpad/LaunchpadPanel.tsx`

**Interfaces:**
- Produces: `data-launchpad-tab-strip` with `overflow-x-auto`, stable button sizes, and no text clipping when the viewport is narrow.

- [ ] **Step 1: Write failing tests**

Assert the top launchpad tab strip has an overflow-safe wrapper and tabs use shrink-resistant dimensions.

- [ ] **Step 2: Run RED test**

Run: `pnpm exec tsx src/__tests__/frontendShellParityAcceptance.test.ts`

Expected: FAIL because the top tab strip lacks the data hook and explicit overflow-safe container.

- [ ] **Step 3: Implement overflow-safe strip**

Wrap the segmented control in an overflow container, keep buttons `shrink-0`, and preserve current enterprise styling.

- [ ] **Step 4: Run GREEN test**

Run: `pnpm exec tsx src/__tests__/frontendShellParityAcceptance.test.ts`

Expected: PASS.

### Task 4: Verification And Backend Gap Report

**Files:**
- Modify only if needed: `docs/frontend/skills-marketplace-public-api.md`

**Interfaces:**
- Consumes: `tools/frontend_projection_audit.py --format json`.
- Produces: evidence that no ordinary-user page reopens legacy admin APIs; backend gap list for any confirmed missing projections.

- [ ] **Step 1: Run targeted frontend checks**

Run:
`pnpm exec tsx src/__tests__/frontendShellParityAcceptance.test.ts`
`pnpm exec tsx src/components/panels/__tests__/governancePhase1Closure.test.ts`
`pnpm run projection:audit`

- [ ] **Step 2: Run build**

Run: `pnpm run build`

- [ ] **Step 3: Report backend gaps**

If `/users`, `/settings`, `/feedback`, or `/notifications` still lack public or permission-gated backend contracts, report the exact missing routes and do not claim them as ready pages.
