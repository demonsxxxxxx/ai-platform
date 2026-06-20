# AI Platform Frontend Experience Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the first-phase ai-platform frontend visibly follow the approved LibreChat-style product loop using current backend contracts.

**Architecture:** Keep the existing React Router and ai-platform service adapters. Rework the chat composer and navigation affordances so users can discover Skills, MCP tools, file references, session share, channel import, and the company launchpad from the chat-first shell. Backend gaps remain fail-closed or routed to existing guarded pages.

**Tech Stack:** React 19, TypeScript, React Router 7, lucide-react, Tailwind utility classes, existing ai-platform hooks and service adapters.

## Global Constraints

- Do not import LibreChat backend APIs, Mongo data models, ACL rules, or data-provider contracts.
- Use existing ai-platform auth, RBAC, route guards, service adapters, and public/admin projections.
- Phase 1 may add frontend affordances and route integrations only where current backend contracts already exist.
- Backend-missing department policy, real channel import projection expansion, and advanced share ACL workflows remain Phase 2.
- Keep `/apps` as a click-through company application launchpad; do not migrate nonGMPlims Vue business modules into ai-platform.
- Do not stage unrelated dirty files: `frontend/web/src/__tests__/pwaGuards.test.ts`, `frontend/web/src/__tests__/serviceWorkerSource.test.ts`, or backend PRD drafts.

---

## File Structure

- `frontend/web/src/components/chat/ChatInput.tsx`: detect `/` and `$` command prefixes and open the right existing selector panel.
- `frontend/web/src/components/chat/ChatInputToolbar.tsx`: show selected file reference chips and pass a dedicated command button label through the toolbar.
- `frontend/web/src/components/selectors/FeatureMenu.tsx`: rename grouped actions to Skills, MCP tools, files, persona, agent, and thinking in user-facing terms.
- `frontend/web/src/components/panels/SidebarParts/SidebarRail.tsx`: expose first-level rail icons for company launchpad, Skills marketplace, and MCP tools instead of hiding all governance surfaces behind More.
- `frontend/web/src/components/panels/SessionSidebar.tsx`: wire the new rail callbacks and labels.
- `frontend/web/src/i18n/locales/en.json` and `frontend/web/src/i18n/locales/zh.json`: add explicit command, marketplace, MCP, file chip, launchpad, share, and channel copy used by the shell.
- `frontend/web/src/components/chat/__tests__/frontendExperiencePhase1.test.ts`: source-level guard for the phase-one UX contract.

## Task 1: Composer Command Surface

**Files:**
- Modify: `frontend/web/src/components/chat/ChatInput.tsx`
- Modify: `frontend/web/src/components/selectors/FeatureMenu.tsx`
- Modify: `frontend/web/src/components/chat/ChatInputToolbar.tsx`
- Test: `frontend/web/src/components/chat/__tests__/frontendExperiencePhase1.test.ts`

**Interfaces:**
- Consumes: existing `FeaturePanel` values `tools`, `skills`, `persona`, `agent`, `thinking`.
- Produces: command-prefix behavior where `/` opens Skills and `$` opens MCP tools without sending the raw command.

- [ ] **Step 1: Write the failing test**

```ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();
const chatInputSource = readFileSync(
  join(root, "src/components/chat/ChatInput.tsx"),
  "utf8",
);
const featureMenuSource = readFileSync(
  join(root, "src/components/selectors/FeatureMenu.tsx"),
  "utf8",
);
const toolbarSource = readFileSync(
  join(root, "src/components/chat/ChatInputToolbar.tsx"),
  "utf8",
);

test("slash and dollar command prefixes open Skills and MCP selectors", () => {
  assert.match(chatInputSource, /COMMAND_PREFIX_PANEL/);
  assert.match(chatInputSource, /"\\/":\s*"skills"/);
  assert.match(chatInputSource, /"\\$":\s*"tools"/);
  assert.match(chatInputSource, /setActivePanel\(commandPanel\)/);
  assert.match(chatInputSource, /setInput\(""\)/);
});

test("composer exposes first-phase command and file reference affordances", () => {
  assert.match(toolbarSource, /chat\.commandTrigger/);
  assert.match(chatInputSource, /chat\.fileReferences/);
  assert.match(chatInputSource, /chat\.fileReferenceChip/);
});

test("feature menu names current ai-platform capabilities in PRD terms", () => {
  assert.match(featureMenuSource, /featureMenu\.skillsMarketplace/);
  assert.match(featureMenuSource, /featureMenu\.mcpTools/);
  assert.match(featureMenuSource, /featureMenu\.fileReference/);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend/web; pnpm exec tsx --test src/components/chat/__tests__/frontendExperiencePhase1.test.ts`

Expected: FAIL because command prefix and copy markers are not implemented yet.

- [ ] **Step 3: Implement command prefix behavior**

In `ChatInput.tsx`, add a local constant:

```ts
const COMMAND_PREFIX_PANEL: Record<string, FeaturePanel> = {
  "/": "skills",
  "$": "tools",
};
```

Update textarea `onChange` so when the entire value is exactly `/` or `$`, it opens the mapped panel, clears input, moves cursor to 0, and does not send the literal prefix.

- [ ] **Step 4: Add file reference chips and command labels**

Render a compact file-reference row above the textarea when `attachments.length > 0`. Use the existing attachment metadata only; do not expose raw local paths. Add the `chat.fileReferences` and `chat.fileReferenceChip` translation keys. Add a command button label in `ChatInputToolbar` through `title={t("chat.commandTrigger")}` on the feature menu trigger wrapper or props.

- [ ] **Step 5: Re-run the focused test**

Run: `cd frontend/web; pnpm exec tsx --test src/components/chat/__tests__/frontendExperiencePhase1.test.ts`

Expected: PASS.

## Task 2: Navigation And Governance Entry Surface

**Files:**
- Modify: `frontend/web/src/components/panels/SidebarParts/SidebarRail.tsx`
- Modify: `frontend/web/src/components/panels/SessionSidebar.tsx`
- Modify: `frontend/web/src/components/selectors/FeatureMenu.tsx`
- Modify: `frontend/web/src/i18n/locales/en.json`
- Modify: `frontend/web/src/i18n/locales/zh.json`
- Test: `frontend/web/src/components/chat/__tests__/frontendExperiencePhase1.test.ts`

**Interfaces:**
- Consumes: existing guarded routes `/apps`, `/skills`, `/marketplace`, `/mcp`, `/channels`, `/shared/:shareId`.
- Produces: visible rail buttons and menu copy for the first-phase shell.

- [ ] **Step 1: Extend the failing test**

Add source assertions:

```ts
const sidebarRailSource = readFileSync(
  join(root, "src/components/panels/SidebarParts/SidebarRail.tsx"),
  "utf8",
);
const sessionSidebarSource = readFileSync(
  join(root, "src/components/panels/SessionSidebar.tsx"),
  "utf8",
);
const zhSource = readFileSync(join(root, "src/i18n/locales/zh.json"), "utf8");

test("rail exposes company launchpad Skills and MCP as first-level workbench entries", () => {
  assert.match(sidebarRailSource, /onOpenLaunchpad/);
  assert.match(sidebarRailSource, /onOpenSkills/);
  assert.match(sidebarRailSource, /onOpenMcp/);
  assert.match(sessionSidebarSource, /navigate\("\/apps"\)/);
  assert.match(sessionSidebarSource, /navigate\("\/mcp"\)/);
});

test("Chinese shell copy names the PRD surfaces directly", () => {
  assert.match(zhSource, /技能市场/);
  assert.match(zhSource, /MCP 工具/);
  assert.match(zhSource, /公司导航/);
  assert.match(zhSource, /频道导入/);
  assert.match(zhSource, /会话分享/);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend/web; pnpm exec tsx --test src/components/chat/__tests__/frontendExperiencePhase1.test.ts`

Expected: FAIL because new rail callbacks and copy are not yet present.

- [ ] **Step 3: Add first-level rail buttons**

Add callbacks to `SidebarRailProps`: `onOpenLaunchpad`, `onOpenMcp`. Render `LayoutGrid`, `Sparkles`, and `Server` buttons for `/apps`, `/skills`, and `/mcp` before the file library/persona buttons. Keep `More` for lower-frequency admin pages and channels.

- [ ] **Step 4: Wire callbacks in `SessionSidebar.tsx`**

Pass:

```tsx
onOpenLaunchpad={() => navigate("/apps")}
onOpenSkills={() => navigate("/skills")}
onOpenMcp={() => navigate("/mcp")}
```

Keep existing RBAC-driven route guards. Do not bypass protected routes.

- [ ] **Step 5: Update i18n copy**

Add English and Chinese keys for command trigger, file references, file chips, Skills marketplace, MCP tools, channel import, and session share. Prefer existing keys where compatible, but make first-phase labels explicit.

- [ ] **Step 6: Re-run focused test**

Run: `cd frontend/web; pnpm exec tsx --test src/components/chat/__tests__/frontendExperiencePhase1.test.ts`

Expected: PASS.

## Task 3: Verification, Review, And Merge

**Files:**
- No new source files expected beyond Tasks 1-2.
- Review branch diff and PR state.

**Interfaces:**
- Consumes: task outputs from Tasks 1-2.
- Produces: verified commit(s), subagent review result, pushed PR update, and merge if GitHub state permits.

- [ ] **Step 1: Run focused frontend tests**

Run:

```powershell
cd frontend/web
pnpm exec tsx --test src/components/chat/__tests__/frontendExperiencePhase1.test.ts src/__tests__/launchpadRoute.test.ts src/components/launchpad/__tests__/catalog.test.ts src/components/launchpad/__tests__/launchpadSource.test.ts
```

Expected: PASS.

- [ ] **Step 2: Run build**

Run: `cd frontend/web; pnpm run build`

Expected: PASS.

- [ ] **Step 3: Run diff hygiene**

Run: `git diff --check`

Expected: PASS.

- [ ] **Step 4: Subagent review**

Dispatch a reviewer subagent with the branch diff and this plan. Require findings first, ordered by severity. Fix Critical/Important findings before merge.

- [ ] **Step 5: Commit and push only intended files**

Stage only:

```powershell
git add docs/superpowers/plans/2026-06-20-ai-platform-frontend-experience-phase1.md frontend/web/src/components/chat/ChatInput.tsx frontend/web/src/components/chat/ChatInputToolbar.tsx frontend/web/src/components/chat/__tests__/frontendExperiencePhase1.test.ts frontend/web/src/components/selectors/FeatureMenu.tsx frontend/web/src/components/panels/SidebarParts/SidebarRail.tsx frontend/web/src/components/panels/SessionSidebar.tsx frontend/web/src/i18n/locales/en.json frontend/web/src/i18n/locales/zh.json
```

Commit: `feat: surface frontend experience phase one`

Push current branch.

- [ ] **Step 6: Merge PR only when allowed**

Check PR mergeability, checks, and review state. Merge only if branch is mergeable and checks/review requirements are satisfied. If blocked, leave PR updated with evidence and report the blocker precisely.
