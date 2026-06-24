# Frontend Complete State Machine Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the first post-login frontend completion slice usable and verifiable by converging route governance states, public Skills/Marketplace contracts, and workbench visual surfaces.

**Architecture:** Keep the existing React/Vite shell and panel boundaries. Add small pure resolvers/tests around governance state and API URL shaping, then update the Skills/Marketplace hub and shared workbench surface tokens without introducing new backend routes or broad component rewrites.

**Tech Stack:** React, TypeScript, Vite, node:test, Tailwind utility classes, existing ai-platform API clients.

## Global Constraints

- Work only in `C:\aiwt\frontend-complete-20260625` on branch `codex/frontend-complete-20260625`.
- Do not touch the dirty main checkout.
- Do not commit secrets, credentials, `.codex/`, `.superpowers/`, `.pytest-tmp/`, `dist`, `node_modules`, tarballs, or smoke evidence.
- Consume the merged PR #177 public `/api/skills/*` and `/api/marketplace/*` contract; do not call admin release-management APIs for ordinary frontend pages.
- Treat persona/files backend gaps as degraded or tracked issues, not as completed frontend pages.
- Use focused frontend tests/build and 211 static-service deployment evidence; do not run full local pytest.

---

### Task 1: Governance Resolver And API Contract Tests

**Files:**
- Modify: `frontend/web/src/components/panels/SkillsHubPanel/state.ts`
- Modify: `frontend/web/src/services/api/marketplace.ts`
- Test: `frontend/web/src/components/panels/__tests__/skillsHubGovernanceState.test.ts`
- Test: `frontend/web/src/services/api/__tests__/marketplace.test.ts`

**Interfaces:**
- Consumes: `resolveSkillsHubGovernance(input): SkillsHubGovernanceState`
- Produces: status-machine coverage for `logged-out`, `loading`, `no-workspace`, `forbidden`, `degraded`, and `ready`; exported `buildMarketplaceListUrl(params)` for URL tests.

- [ ] Write failing resolver tests for all six frontend governance states.
- [ ] Write failing API URL tests for `/api/marketplace/?tags=...&search=...&skip=...&limit=...`.
- [ ] Update resolver priority so `effective_permissions` from public catalog wins over stale auth projection, and permission errors remain fail-closed.
- [ ] Export a small marketplace URL builder and use it in `marketplaceApi.list`.
- [ ] Run the two focused node tests and verify they pass after first failing.

### Task 2: Workbench Visual Convergence

**Files:**
- Modify: `frontend/web/src/components/workbench/workbenchSurface.ts`
- Modify: `frontend/web/src/components/panels/SkillsPanel/index.tsx`
- Modify: `frontend/web/src/components/panels/SkillsPanel/SkillsList.tsx`
- Modify: `frontend/web/src/components/panels/MarketplacePanel.tsx`
- Modify: `frontend/web/src/components/panels/SkillsHubPanel.tsx`
- Test: `frontend/web/src/components/workbench/__tests__/workbenchVisualClosure.test.ts`

**Interfaces:**
- Consumes: `workbenchSurface.panel`, `workbenchSurface.secondaryPanel`, `workbenchSurface.statusTile`, `data-frontend-governance-state`.
- Produces: post-login Skills/Marketplace surfaces using one canvas/surface family and no old `bg-white` or split light/dark page look.

- [ ] Extend visual closure tests for SkillsPanel, MarketplacePanel, SkillsList, and SkillsHubPanel token usage.
- [ ] Update panel roots and empty/error surfaces to use workbench canvas and panel/status tokens.
- [ ] Keep controls dense and enterprise-oriented: compact tabs, 8px radius, Lucide icons, stable 40px toolbar controls.
- [ ] Run the visual closure test and verify no obsolete split-surface patterns remain.

### Task 3: Verification, Deployment, And Evidence

**Files:**
- Build output only under `frontend/web/dist` and excluded from git.
- Evidence only under git-ignored release-evidence paths if needed.

**Interfaces:**
- Consumes: local frontend test/build output and 211 static service.
- Produces: a rebuilt frontend visible at `http://10.56.0.211:18001/` with provenance matching the new commit.

- [ ] Run focused frontend node tests.
- [ ] Run `pnpm --dir frontend/web run build`.
- [ ] Run `git diff --check`.
- [ ] Commit only source/docs/test changes.
- [ ] Package the built frontend with provenance and deploy to 211 static service without Docker.
- [ ] Smoke `/`, `/auth/login`, `/skills`, `/marketplace`, and backend `/api/ai/health`.
- [ ] If live smoke reveals backend 404/403/409 gaps beyond #229, file or update a backend issue with exact route/status evidence.
