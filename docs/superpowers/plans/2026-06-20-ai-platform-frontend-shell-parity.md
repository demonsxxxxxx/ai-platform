# AI Platform Frontend Shell Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the remaining imported LambChat-style frontend with an ai-platform enterprise AI workbench shell that matches the approved LibreChat / poco-claw direction for the chat loop, composer, Skills, MCP, sharing, channel import, and company launchpad.

**Architecture:** Keep all implementation inside `frontend/web` and consume only existing ai-platform public/admin projections. Build a shell-level visual system first, then attach composer commands, selected chips, governance availability surfaces, and fail-closed collaboration pages to that shell. Missing backend contracts must render explicit unavailable states instead of fake working UI.

**Tech Stack:** React 19, TypeScript, React Router, Tailwind utility classes, `lucide-react`, existing ai-platform frontend services, Node `tsx --test`, Vite, existing static 211 deployment.

## Global Constraints

- The authenticated app must no longer visually read as LambChat or as a mixed shell.
- The authenticated chat shell must follow the approved LibreChat / poco-claw reference pattern without importing its backend authority.
- `/` opens command flows for `/skill`, `/mcp`, `/agent`, `/model`, `/file`, and `/context`; `$` opens or filters directly to Skills.
- Skills, MCP, files, artifacts, sharing, and channels must use ai-platform authority and must not expose raw paths, storage keys, provider secrets, executor-private payloads, or sandbox workdirs.
- Missing department marketplace, MCP policy assignment, share ACL, channel import, user/role/department, or model admin contracts must render explicit unavailable states.
- `/apps` remains a click-through company launchpad and must not absorb nonGMPlims business modules.
- No emoji as structural icons; use the existing `lucide-react` icon family.
- Keep cards at modest radius, avoid nested card clutter, avoid decorative hero treatment inside the authenticated app, and preserve dense enterprise scanning.
- Evidence must include component/source tests, build, projection audit where applicable, browser screenshots or browser smoke, and 211 smoke before claiming `211 verified`.
- Do not modify backend contracts, database schema, Docker compose, or deployment topology in this plan.

---

## File Structure

### Brand Authority And Metadata

- Modify `frontend/web/src/constants/index.ts`: set ai-platform product constants and public links.
- Modify `frontend/web/index.html`: replace LambChat SEO, canonical URLs, app title, OpenGraph, Twitter, and JSON-LD metadata with ai-platform language.
- Modify `frontend/web/src/i18n/locales/en.json`, `zh.json`, `ja.json`, `ko.json`, `ru.json`: replace user-visible LambChat copy with ai-platform copy.
- Modify `frontend/web/src/sw.ts`, `frontend/web/src/pwa.ts`, `frontend/web/src/pwaGuards.ts`, `frontend/web/src/hooks/useBrowserNotification.ts`, `frontend/web/src/hooks/useSessionConfig.ts`, `frontend/web/src/utils/sessionTitleEvents.ts`, `frontend/web/src/components/persona/usePersonaPlaza.ts`, `frontend/web/src/components/common/selectionActionPrompt.ts`: replace user-visible names and browser storage/event/cache names with ai-platform names while preserving backwards-compatible read migration where needed.
- Modify `frontend/web/src/components/profile/ProfileModal.tsx`, `frontend/web/src/components/chat/ChatInputHelpMenu.tsx`, `frontend/web/src/components/chat/WelcomePage.tsx`, `frontend/web/src/components/auth/AuthPage.tsx`, `frontend/web/src/components/auth/AuthLayout.tsx`, `frontend/web/src/components/auth/ForgotPassword.tsx`, `frontend/web/src/components/auth/ResetPassword.tsx`, `frontend/web/src/components/share/SharedPage.tsx`, `frontend/web/src/components/pages/ChannelsPage.tsx`, `frontend/web/src/components/sidebar/RecentChatsDialog.tsx`, `frontend/web/src/components/panels/SidebarParts/SidebarRail.tsx`, `frontend/web/src/components/panels/SidebarParts/SessionListContent.tsx`: consume the new constants and remove old brand links.
- Create `frontend/web/src/__tests__/aiPlatformBrandGuard.test.ts`: fail if active user-facing source still contains LambChat brand authority.

### Workbench Shell

- Create `frontend/web/src/components/workbench/WorkbenchShell.tsx`: shared authenticated shell with left rail, center workspace, bottom composer slot, and right context drawer slot.
- Create `frontend/web/src/components/workbench/WorkbenchRightPanel.tsx`: right-side artifact/context/provenance drawer with explicit empty and unavailable states.
- Create `frontend/web/src/components/workbench/workbenchSurface.ts`: class-name helpers for shell density, surfaces, active states, and focus states.
- Modify `frontend/web/src/components/layout/AppContent/ChatAppContent.tsx`: wrap chat route in `WorkbenchShell`.
- Modify `frontend/web/src/components/layout/AppContent/ChatView.tsx`: use the shell slots and right drawer state.
- Modify `frontend/web/src/components/panels/SessionSidebar.tsx` and `frontend/web/src/components/panels/SidebarParts/SidebarRail.tsx`: align rail density, labels, permission-gated entries, and launchpad/skills/mcp shortcuts with shell vocabulary.
- Modify `frontend/web/src/components/launchpad/LaunchpadPanel.tsx`: use the same workbench surface language as chat.
- Create `frontend/web/src/components/workbench/__tests__/workbenchShellSource.test.ts`: source-level guard for required shell regions and forbidden old-shell copy.

### Composer Commands And Chips

- Modify `frontend/web/src/components/selectors/FeatureMenu.tsx`: add model, file, and context command groups and compact command-menu labels.
- Modify `frontend/web/src/components/chat/chatInputCommands.ts`: parse slash commands and `$` trigger into a typed command result.
- Create `frontend/web/src/components/chat/ComposerChips.tsx`: durable selected Skill, MCP tool, agent, model, file, and context chips.
- Create `frontend/web/src/components/chat/composerSelections.ts`: typed selection model and reducer helpers.
- Modify `frontend/web/src/components/chat/ChatInput.tsx`, `ChatInputToolbar.tsx`, `ChatInputSelectors.tsx`, `ChatInputAttachments.tsx`, `chatInputTypes.ts`: render chips, wire command results, and keep file chips bound to safe ids/handles only.
- Modify `frontend/web/src/hooks/useAgent.ts`: pass selected composer chips as existing safe run options only where current contracts already support them; otherwise keep chips client-side with an explicit unavailable badge.
- Create `frontend/web/src/components/chat/__tests__/composerCommandParity.test.ts`: behavior tests for `/skill`, `/mcp`, `/agent`, `/model`, `/file`, `/context`, `$`, and chip removal.

### Skills And MCP Governance Surfaces

- Create `frontend/web/src/components/governance/GovernanceAvailabilityBadge.tsx`: visible `enabled`, `disabled`, `inherited`, `admin-only`, and `unavailable` states.
- Create `frontend/web/src/components/governance/groupAvailability.ts`: pure helpers for group/department state mapping.
- Modify `frontend/web/src/components/panels/SkillsHubPanel.tsx` and child files under `frontend/web/src/components/panels/SkillsHubPanel/`: show compact marketplace filters, availability badges, Skill detail metadata, and group toggle unavailable states.
- Modify `frontend/web/src/components/panels/MCPPanel.tsx`: expose ordinary-user searchable tool inventory, permission mode, add-to-composer affordance where backed, and admin-only policy sections where backed.
- Create `frontend/web/src/components/panels/__tests__/governanceSurfaceSource.test.ts`: verify explicit unavailable states and absence of raw server lifecycle controls for ordinary surfaces.

### Sharing, Channel Import, And Launchpad Integration

- Create `frontend/web/src/components/share/ShareUnavailableState.tsx`: fail-closed shared-session states for denied, expired, revoked, and unavailable.
- Modify `frontend/web/src/components/share/ShareDialog.tsx` and `SharedPage.tsx`: show ACL/redaction/expiration language and use `ShareUnavailableState` when the backend does not authorize data.
- Create `frontend/web/src/components/channels/ChannelImportPanel.tsx`: channel import page with available-source list when backed and explicit Phase 2 unavailable state when not backed.
- Modify `frontend/web/src/components/pages/ChannelsPage.tsx` and `frontend/web/src/components/layout/AppContent/TabContent.tsx`: route `/channels` to the governed import panel instead of old channel integration language.
- Modify `frontend/web/src/components/launchpad/LaunchpadPanel.tsx`: align launchpad copy with "click-through company application directory" and keep target-system permission boundary visible.
- Create `frontend/web/src/components/share/__tests__/shareChannelFailClosedSource.test.ts`: source-level tests for fail-closed share/import states.

### Visual And Runtime Evidence

- Create `frontend/web/src/__tests__/frontendShellParityAcceptance.test.ts`: single source guard that references the PRD acceptance surface list and verifies registered routes/components.
- Modify `docs/superpowers/plans/2026-06-20-ai-platform-frontend-experience-phase1.md`: append a short "superseded by shell parity plan" note with the new plan path.
- Create `docs/release-evidence/frontend-shell-parity/.gitkeep`: stable evidence directory for screenshots and 211 smoke notes.

---

## Task 1: Remove LambChat Brand Authority From Active Frontend

**Files:**
- Create: `frontend/web/src/__tests__/aiPlatformBrandGuard.test.ts`
- Modify: `frontend/web/src/constants/index.ts`
- Modify: `frontend/web/index.html`
- Modify: `frontend/web/src/i18n/locales/en.json`
- Modify: `frontend/web/src/i18n/locales/zh.json`
- Modify: `frontend/web/src/i18n/locales/ja.json`
- Modify: `frontend/web/src/i18n/locales/ko.json`
- Modify: `frontend/web/src/i18n/locales/ru.json`
- Modify: `frontend/web/src/sw.ts`
- Modify: `frontend/web/src/pwa.ts`
- Modify: `frontend/web/src/pwaGuards.ts`
- Modify: `frontend/web/src/hooks/useBrowserNotification.ts`
- Modify: `frontend/web/src/hooks/useSessionConfig.ts`
- Modify: `frontend/web/src/utils/sessionTitleEvents.ts`
- Modify: `frontend/web/src/components/profile/ProfileModal.tsx`
- Modify: `frontend/web/src/components/chat/ChatInputHelpMenu.tsx`
- Modify: `frontend/web/src/components/chat/WelcomePage.tsx`
- Modify: `frontend/web/src/components/share/SharedPage.tsx`
- Test: `frontend/web/src/__tests__/aiPlatformBrandGuard.test.ts`

**Interfaces:**
- Consumes: existing `APP_NAME`, `GITHUB_URL`, `usePageTitle`, translation keys, PWA registration functions.
- Produces: `APP_NAME = "AI Platform"`, `APP_HOME_URL = "http://10.56.0.211:18001/"`, user-visible copy that no longer advertises LambChat identity, and browser event/cache names using `ai-platform:*`.

- [ ] **Step 1: Write the failing brand guard**

Create `frontend/web/src/__tests__/aiPlatformBrandGuard.test.ts`:

```ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

const activeFiles = [
  "index.html",
  "src/constants/index.ts",
  "src/i18n/locales/en.json",
  "src/i18n/locales/zh.json",
  "src/i18n/locales/ja.json",
  "src/i18n/locales/ko.json",
  "src/i18n/locales/ru.json",
  "src/sw.ts",
  "src/pwa.ts",
  "src/pwaGuards.ts",
  "src/hooks/useBrowserNotification.ts",
  "src/hooks/useSessionConfig.ts",
  "src/utils/sessionTitleEvents.ts",
  "src/components/profile/ProfileModal.tsx",
  "src/components/chat/ChatInputHelpMenu.tsx",
  "src/components/chat/WelcomePage.tsx",
  "src/components/share/SharedPage.tsx",
  "src/components/auth/AuthPage.tsx",
  "src/components/auth/AuthLayout.tsx",
  "src/components/auth/ForgotPassword.tsx",
  "src/components/auth/ResetPassword.tsx",
  "src/components/pages/ChannelsPage.tsx",
  "src/components/sidebar/RecentChatsDialog.tsx",
  "src/components/panels/SidebarParts/SidebarRail.tsx",
  "src/components/panels/SidebarParts/SessionListContent.tsx",
];

const bannedPatterns = [
  /\bLambChat\b/,
  /lambchat\.com/i,
  /github\.com\/(?:clivia|Yanyutin753)\/LambChat/i,
  /yanyutin753\.github\.io\/LambChat/i,
  /\bClivia\b/,
];

test("active frontend no longer exposes LambChat brand authority", () => {
  const offenders: string[] = [];

  for (const file of activeFiles) {
    const source = readFileSync(join(root, file), "utf8");
    for (const pattern of bannedPatterns) {
      if (pattern.test(source)) offenders.push(`${file} -> ${pattern}`);
    }
  }

  assert.deepEqual(offenders, []);
});

test("ai-platform product constants are the active brand source", () => {
  const constants = readFileSync(join(root, "src/constants/index.ts"), "utf8");
  assert.match(constants, /export const APP_NAME = "AI Platform"/);
  assert.match(constants, /export const APP_HOME_URL = "http:\/\/10\.56\.0\.211:18001\/"/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd frontend/web
pnpm exec tsx --test src/__tests__/aiPlatformBrandGuard.test.ts
```

Expected: FAIL with offenders including `frontend/web/index.html`, `src/constants/index.ts`, locale files, `sw.ts`, and profile/help/share components.

- [ ] **Step 3: Replace product constants**

Modify `frontend/web/src/constants/index.ts` to:

```ts
export const APP_NAME = "AI Platform";
export const APP_HOME_URL = "http://10.56.0.211:18001/";
export const GITHUB_URL = "https://github.com/demonsxxxxxx/ai-platform";
```

- [ ] **Step 4: Replace static HTML metadata**

In `frontend/web/index.html`, replace every LambChat URL/title/meta/JSON-LD value with this ai-platform language:

```html
<link rel="canonical" href="http://10.56.0.211:18001/" />
<title>AI Platform - Enterprise AI Workbench</title>
<meta
  name="description"
  content="AI Platform is a company-internal governed AI workbench for chat, Skills, MCP tools, files, artifacts, and operational workflows."
/>
<meta
  name="keywords"
  content="AI Platform, enterprise AI workbench, Skills, MCP, governed tools, company AI, multi-tenant AI, RBAC, artifacts"
/>
<meta name="author" content="AI Platform" />
<link rel="alternate" hreflang="en" href="http://10.56.0.211:18001/?lng=en" />
<link rel="alternate" hreflang="zh" href="http://10.56.0.211:18001/?lng=zh" />
<link rel="alternate" hreflang="ja" href="http://10.56.0.211:18001/?lng=ja" />
<link rel="alternate" hreflang="ko" href="http://10.56.0.211:18001/?lng=ko" />
<link rel="alternate" hreflang="ru" href="http://10.56.0.211:18001/?lng=ru" />
<link rel="alternate" hreflang="x-default" href="http://10.56.0.211:18001/" />
<meta name="apple-mobile-web-app-title" content="AI Platform" />
<meta name="application-name" content="AI Platform" />
<meta property="og:title" content="AI Platform - Enterprise AI Workbench" />
<meta property="og:url" content="http://10.56.0.211:18001/" />
<meta property="og:site_name" content="AI Platform" />
<meta property="og:image:alt" content="AI Platform enterprise AI workbench" />
<meta name="twitter:title" content="AI Platform - Enterprise AI Workbench" />
<meta name="twitter:image:alt" content="AI Platform enterprise AI workbench" />
```

For JSON-LD blocks in the same file, use:

```json
{
  "name": "AI Platform",
  "url": "http://10.56.0.211:18001",
  "description": "Company-internal governed AI workbench for chat, Skills, MCP tools, files, artifacts, and workflows."
}
```

- [ ] **Step 5: Replace English and Chinese user-visible brand copy**

In `frontend/web/src/i18n/locales/en.json`, set these values:

```json
{
  "about": {
    "title": "About AI Platform",
    "poweredBy": "Company AI Platform"
  },
  "appName": "AI Platform",
  "channel": {
    "description": "Connect governed company channels to AI Platform"
  },
  "seo": {
    "landing": {
      "description": "AI Platform is a company-internal governed AI workbench for chat, Skills, MCP tools, files, artifacts, and workflows.",
      "title": "AI Platform - Enterprise AI Workbench"
    }
  }
}
```

In `frontend/web/src/i18n/locales/zh.json`, set these values:

```json
{
  "about": {
    "title": "关于 AI Platform",
    "poweredBy": "公司 AI Platform"
  },
  "appName": "AI Platform",
  "channel": {
    "description": "将受治理的公司频道接入 AI Platform"
  },
  "seo": {
    "landing": {
      "description": "AI Platform 是公司内部受治理的 AI 工作台，覆盖对话、Skills、MCP 工具、文件、产物与工作流。",
      "title": "AI Platform - 企业 AI 工作台"
    }
  }
}
```

Apply equivalent localized ai-platform copy in `ja.json`, `ko.json`, and `ru.json`; keep the product name as `AI Platform` in all locales.

- [ ] **Step 6: Replace PWA, notification, storage, and event names with migration**

In `frontend/web/src/sw.ts`, use:

```ts
const APP_SHELL_CACHE = "ai-platform-app-shell-v1";
const STATIC_CACHE = "ai-platform-static-v1";

const OFFLINE_RESPONSE = "AI Platform is offline.";
const DEFAULT_NOTIFICATION_TITLE = "AI Platform";
const DEFAULT_NOTIFICATION_BODY = "You have a new AI Platform update.";
```

In `frontend/web/src/pwaGuards.ts`, use:

```ts
export const PWA_UPDATE_AVAILABLE_EVENT = "ai-platform:pwa-update-available";
```

In `frontend/web/src/utils/sessionTitleEvents.ts`, use:

```ts
export const SESSION_TITLE_UPDATED_EVENT = "ai-platform:session-title-updated";
```

In `frontend/web/src/hooks/useSessionConfig.ts`, preserve old reads and write new keys:

```ts
const STORAGE_KEY = "ai-platform-session-config";
const LEGACY_STORAGE_KEY = "lambchat_session_config";

function readSessionConfigStorage(): string | null {
  return localStorage.getItem(STORAGE_KEY) ?? localStorage.getItem(LEGACY_STORAGE_KEY);
}
```

- [ ] **Step 7: Replace component links and alt text**

Use `APP_NAME`, `APP_HOME_URL`, and `GITHUB_URL` in profile, help, auth, welcome, share, recent chats, and sidebar components. For `ChatInputHelpMenu.tsx`, replace the old docs link with:

```tsx
<a
  href={GITHUB_URL}
  target="_blank"
  rel="noreferrer"
  className="text-[var(--theme-primary)] hover:underline"
>
  {t("chat.helpDocs", "AI Platform documentation")}
</a>
```

- [ ] **Step 8: Run brand guard**

Run:

```bash
cd frontend/web
pnpm exec tsx --test src/__tests__/aiPlatformBrandGuard.test.ts
```

Expected: PASS, 2 tests passing.

- [ ] **Step 9: Run existing frontend phase tests**

Run:

```bash
cd frontend/web
pnpm exec tsx --test src/components/chat/__tests__/frontendExperiencePhase1.test.ts src/__tests__/launchpadRoute.test.ts src/components/launchpad/__tests__/catalog.test.ts
```

Expected: PASS, existing tests remain green.

- [ ] **Step 10: Commit**

```bash
git add frontend/web/index.html frontend/web/src/constants/index.ts frontend/web/src/i18n/locales/en.json frontend/web/src/i18n/locales/zh.json frontend/web/src/i18n/locales/ja.json frontend/web/src/i18n/locales/ko.json frontend/web/src/i18n/locales/ru.json frontend/web/src/sw.ts frontend/web/src/pwa.ts frontend/web/src/pwaGuards.ts frontend/web/src/hooks/useBrowserNotification.ts frontend/web/src/hooks/useSessionConfig.ts frontend/web/src/utils/sessionTitleEvents.ts frontend/web/src/components/profile/ProfileModal.tsx frontend/web/src/components/chat/ChatInputHelpMenu.tsx frontend/web/src/components/chat/WelcomePage.tsx frontend/web/src/components/auth/AuthPage.tsx frontend/web/src/components/auth/AuthLayout.tsx frontend/web/src/components/auth/ForgotPassword.tsx frontend/web/src/components/auth/ResetPassword.tsx frontend/web/src/components/share/SharedPage.tsx frontend/web/src/components/pages/ChannelsPage.tsx frontend/web/src/components/sidebar/RecentChatsDialog.tsx frontend/web/src/components/panels/SidebarParts/SidebarRail.tsx frontend/web/src/components/panels/SidebarParts/SessionListContent.tsx frontend/web/src/__tests__/aiPlatformBrandGuard.test.ts
git commit -m "feat: remove legacy frontend brand authority"
```

---

## Task 2: Build Enterprise Workbench Shell

**Files:**
- Create: `frontend/web/src/components/workbench/WorkbenchShell.tsx`
- Create: `frontend/web/src/components/workbench/WorkbenchRightPanel.tsx`
- Create: `frontend/web/src/components/workbench/workbenchSurface.ts`
- Create: `frontend/web/src/components/workbench/__tests__/workbenchShellSource.test.ts`
- Modify: `frontend/web/src/components/layout/AppContent/ChatAppContent.tsx`
- Modify: `frontend/web/src/components/layout/AppContent/ChatView.tsx`
- Modify: `frontend/web/src/components/panels/SessionSidebar.tsx`
- Modify: `frontend/web/src/components/panels/SidebarParts/SidebarRail.tsx`
- Modify: `frontend/web/src/components/launchpad/LaunchpadPanel.tsx`
- Test: `frontend/web/src/components/workbench/__tests__/workbenchShellSource.test.ts`

**Interfaces:**
- Consumes: existing `SessionSidebar`, `ChatView`, `LaunchpadPanel`, route active tab, permission booleans, current run/session ids.
- Produces: `WorkbenchShell`, `WorkbenchRightPanel`, and `workbenchSurface` helpers that later tasks use for composer, Skills, MCP, launchpad, share, and channel surfaces.

- [ ] **Step 1: Write failing shell source test**

Create `frontend/web/src/components/workbench/__tests__/workbenchShellSource.test.ts`:

```ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

test("workbench shell exposes the required enterprise regions", () => {
  const shell = readFileSync(
    join(root, "src/components/workbench/WorkbenchShell.tsx"),
    "utf8",
  );

  assert.match(shell, /data-workbench-region="rail"/);
  assert.match(shell, /data-workbench-region="thread"/);
  assert.match(shell, /data-workbench-region="composer"/);
  assert.match(shell, /data-workbench-region="context"/);
  assert.match(shell, /rightPanel/);
});

test("chat app uses the workbench shell instead of old mixed layout ownership", () => {
  const chatApp = readFileSync(
    join(root, "src/components/layout/AppContent/ChatAppContent.tsx"),
    "utf8",
  );
  const chatView = readFileSync(
    join(root, "src/components/layout/AppContent/ChatView.tsx"),
    "utf8",
  );

  assert.match(chatApp, /WorkbenchShell/);
  assert.match(chatView, /WorkbenchRightPanel/);
});

test("launchpad and rail use the same workbench language", () => {
  const launchpad = readFileSync(
    join(root, "src/components/launchpad/LaunchpadPanel.tsx"),
    "utf8",
  );
  const rail = readFileSync(
    join(root, "src/components/panels/SidebarParts/SidebarRail.tsx"),
    "utf8",
  );

  assert.match(launchpad, /workbenchSurface/);
  assert.match(rail, /workbench-rail/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd frontend/web
pnpm exec tsx --test src/components/workbench/__tests__/workbenchShellSource.test.ts
```

Expected: FAIL because `WorkbenchShell.tsx` does not exist.

- [ ] **Step 3: Create workbench surface helpers**

Create `frontend/web/src/components/workbench/workbenchSurface.ts`:

```ts
export const workbenchSurface = {
  app: "h-full min-h-0 w-full bg-slate-50 text-slate-950 dark:bg-stone-950 dark:text-stone-50",
  rail: "workbench-rail h-full min-h-0 border-r border-slate-200/70 bg-white/90 dark:border-stone-800 dark:bg-stone-950",
  thread: "min-h-0 flex-1 bg-slate-50 dark:bg-stone-950",
  context: "hidden min-h-0 w-[320px] shrink-0 border-l border-slate-200/70 bg-white/90 dark:border-stone-800 dark:bg-stone-950 xl:flex",
  compactPanel: "rounded-lg border border-slate-200/70 bg-white shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:border-stone-800 dark:bg-stone-900",
  focusRing: "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 dark:focus-visible:ring-offset-stone-950",
};
```

- [ ] **Step 4: Create right panel**

Create `frontend/web/src/components/workbench/WorkbenchRightPanel.tsx`:

```tsx
import { FileText, History, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";
import { workbenchSurface } from "./workbenchSurface";

export interface WorkbenchRightPanelProps {
  sessionId?: string | null;
  runId?: string | null;
  unavailableReason?: string | null;
}

export function WorkbenchRightPanel({
  sessionId,
  runId,
  unavailableReason,
}: WorkbenchRightPanelProps) {
  const { t } = useTranslation();

  return (
    <aside
      data-workbench-region="context"
      className={workbenchSurface.context}
      aria-label={t("workbench.contextPanel", "Context and artifacts")}
    >
      <div className="flex h-full w-full flex-col gap-3 p-3">
        <section className={workbenchSurface.compactPanel}>
          <div className="flex items-center gap-2 px-3 py-2">
            <FileText size={16} className="text-slate-500" />
            <h2 className="text-sm font-semibold">
              {t("workbench.artifacts", "Artifacts")}
            </h2>
          </div>
          <p className="px-3 pb-3 text-xs leading-5 text-slate-500 dark:text-stone-400">
            {unavailableReason ??
              t(
                "workbench.artifactsEmpty",
                "Artifacts and file previews appear here after a governed run produces them.",
              )}
          </p>
        </section>
        <section className={workbenchSurface.compactPanel}>
          <div className="flex items-center gap-2 px-3 py-2">
            <History size={16} className="text-slate-500" />
            <h2 className="text-sm font-semibold">
              {t("workbench.runHistory", "Run history")}
            </h2>
          </div>
          <p className="px-3 pb-3 text-xs leading-5 text-slate-500 dark:text-stone-400">
            {runId
              ? t("workbench.currentRun", "Current run: {{runId}}", { runId })
              : t("workbench.noRunSelected", "No active run selected.")}
          </p>
        </section>
        <section className={workbenchSurface.compactPanel}>
          <div className="flex items-center gap-2 px-3 py-2">
            <ShieldCheck size={16} className="text-slate-500" />
            <h2 className="text-sm font-semibold">
              {t("workbench.provenance", "Provenance")}
            </h2>
          </div>
          <p className="px-3 pb-3 text-xs leading-5 text-slate-500 dark:text-stone-400">
            {sessionId
              ? t("workbench.sessionScope", "Session: {{sessionId}}", { sessionId })
              : t("workbench.noSessionScope", "Create or open a session to see scope.")}
          </p>
        </section>
      </div>
    </aside>
  );
}
```

- [ ] **Step 5: Create workbench shell**

Create `frontend/web/src/components/workbench/WorkbenchShell.tsx`:

```tsx
import type { ReactNode } from "react";
import { workbenchSurface } from "./workbenchSurface";

export interface WorkbenchShellProps {
  rail: ReactNode;
  header: ReactNode;
  thread: ReactNode;
  composer?: ReactNode;
  rightPanel?: ReactNode;
}

export function WorkbenchShell({
  rail,
  header,
  thread,
  composer,
  rightPanel,
}: WorkbenchShellProps) {
  return (
    <div className={`flex h-full min-h-0 w-full ${workbenchSurface.app}`}>
      <div data-workbench-region="rail" className={workbenchSurface.rail}>
        {rail}
      </div>
      <main data-workbench-region="thread" className="flex min-w-0 flex-1 flex-col">
        {header}
        <div className={workbenchSurface.thread}>{thread}</div>
        {composer && (
          <div
            data-workbench-region="composer"
            className="border-t border-slate-200/70 bg-white/95 px-3 py-2 dark:border-stone-800 dark:bg-stone-950/95"
          >
            {composer}
          </div>
        )}
      </main>
      {rightPanel}
    </div>
  );
}
```

- [ ] **Step 6: Wire ChatAppContent to the shell**

In `frontend/web/src/components/layout/AppContent/ChatAppContent.tsx`, import:

```tsx
import { WorkbenchShell } from "../../workbench/WorkbenchShell";
import { WorkbenchRightPanel } from "../../workbench/WorkbenchRightPanel";
```

Replace the top-level authenticated chat layout with:

```tsx
<WorkbenchShell
  rail={
    <SessionSidebar
      ref={sidebarRef}
      {...sidebarProps}
      onShowProfile={handleShowProfile}
    />
  }
  header={header}
  thread={
    <ChatView
      {...chatViewProps}
      rightPanel={
        <WorkbenchRightPanel
          sessionId={currentSessionId}
          runId={currentRunId}
        />
      }
    />
  }
  rightPanel={
    <WorkbenchRightPanel
      sessionId={currentSessionId}
      runId={currentRunId}
    />
  }
/>
```

Use the exact local prop names that already exist in `ChatAppContent.tsx`; do not create new service calls in this task.

- [ ] **Step 7: Align rail and launchpad classes**

In `SidebarRail.tsx`, add `workbench-rail` to the root rail element and keep icon buttons at a stable `h-10 w-10` hit area:

```tsx
className="workbench-rail flex h-full w-14 flex-col items-center gap-1 border-r border-slate-200/70 bg-white py-2 dark:border-stone-800 dark:bg-stone-950"
```

In `LaunchpadPanel.tsx`, import and apply `workbenchSurface.compactPanel` to launchpad sections:

```tsx
import { workbenchSurface } from "../workbench/workbenchSurface";

<section className={`${workbenchSurface.compactPanel} p-3`}>
```

- [ ] **Step 8: Run shell test**

Run:

```bash
cd frontend/web
pnpm exec tsx --test src/components/workbench/__tests__/workbenchShellSource.test.ts
```

Expected: PASS, 3 tests passing.

- [ ] **Step 9: Run route and phase tests**

Run:

```bash
cd frontend/web
pnpm exec tsx --test src/__tests__/launchpadRoute.test.ts src/components/chat/__tests__/frontendExperiencePhase1.test.ts
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add frontend/web/src/components/workbench frontend/web/src/components/layout/AppContent/ChatAppContent.tsx frontend/web/src/components/layout/AppContent/ChatView.tsx frontend/web/src/components/panels/SessionSidebar.tsx frontend/web/src/components/panels/SidebarParts/SidebarRail.tsx frontend/web/src/components/launchpad/LaunchpadPanel.tsx
git commit -m "feat: introduce enterprise workbench shell"
```

---

## Task 3: Implement Command Palette And Durable Composer Chips

**Files:**
- Create: `frontend/web/src/components/chat/composerSelections.ts`
- Create: `frontend/web/src/components/chat/ComposerChips.tsx`
- Create: `frontend/web/src/components/chat/__tests__/composerCommandParity.test.ts`
- Modify: `frontend/web/src/components/chat/chatInputCommands.ts`
- Modify: `frontend/web/src/components/selectors/FeatureMenu.tsx`
- Modify: `frontend/web/src/components/chat/ChatInput.tsx`
- Modify: `frontend/web/src/components/chat/ChatInputToolbar.tsx`
- Modify: `frontend/web/src/components/chat/ChatInputSelectors.tsx`
- Modify: `frontend/web/src/components/chat/ChatInputAttachments.tsx`
- Modify: `frontend/web/src/components/chat/chatInputTypes.ts`
- Modify: `frontend/web/src/hooks/useAgent.ts`
- Test: `frontend/web/src/components/chat/__tests__/composerCommandParity.test.ts`

**Interfaces:**
- Consumes: `FeaturePanel`, existing `SkillSelector`, `ToolSelector`, `AgentModeSelector`, upload attachment objects, current model/agent props.
- Produces: `ComposerSelection`, `composerSelectionReducer`, `parseComposerCommand`, `ComposerChips`, and expanded `FeaturePanel` values `"model" | "file" | "context"`.

- [ ] **Step 1: Write failing command parity tests**

Create `frontend/web/src/components/chat/__tests__/composerCommandParity.test.ts`:

```ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import {
  parseComposerCommand,
  resolveCommandPrefixPanel,
} from "../chatInputCommands";
import {
  composerSelectionReducer,
  type ComposerSelection,
} from "../composerSelections";

const root = process.cwd();

test("slash commands map to PRD command groups", () => {
  assert.deepEqual(parseComposerCommand("/skill"), {
    panel: "skills",
    query: "",
    consumeInput: true,
  });
  assert.deepEqual(parseComposerCommand("/mcp search"), {
    panel: "tools",
    query: "search",
    consumeInput: true,
  });
  assert.equal(parseComposerCommand("/agent")?.panel, "agent");
  assert.equal(parseComposerCommand("/model")?.panel, "model");
  assert.equal(parseComposerCommand("/file")?.panel, "file");
  assert.equal(parseComposerCommand("/context")?.panel, "context");
});

test("dollar shortcut remains skills-only and respects availability", () => {
  assert.equal(
    resolveCommandPrefixPanel("$", { skills: true, tools: true }),
    "skills",
  );
  assert.equal(
    resolveCommandPrefixPanel("$", { skills: false, tools: true }),
    null,
  );
});

test("composer selections add and remove durable chips", () => {
  const skill: ComposerSelection = {
    id: "skill:qa-review",
    kind: "skill",
    label: "QA Review",
    state: "enabled",
  };
  const file: ComposerSelection = {
    id: "file:artifact-123",
    kind: "file",
    label: "report.docx",
    state: "enabled",
    referenceId: "artifact-123",
  };

  let state = composerSelectionReducer([], { type: "upsert", selection: skill });
  state = composerSelectionReducer(state, { type: "upsert", selection: file });
  assert.deepEqual(state.map((item) => item.id), ["skill:qa-review", "file:artifact-123"]);

  state = composerSelectionReducer(state, { type: "remove", id: "skill:qa-review" });
  assert.deepEqual(state.map((item) => item.id), ["file:artifact-123"]);
});

test("chat input renders composer chips and expanded command groups", () => {
  const chatInput = readFileSync(join(root, "src/components/chat/ChatInput.tsx"), "utf8");
  const featureMenu = readFileSync(join(root, "src/components/selectors/FeatureMenu.tsx"), "utf8");

  assert.match(chatInput, /<ComposerChips/);
  assert.match(featureMenu, /featureMenu\.model/);
  assert.match(featureMenu, /featureMenu\.context/);
  assert.match(featureMenu, /featureMenu\.fileReference/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd frontend/web
pnpm exec tsx --test src/components/chat/__tests__/composerCommandParity.test.ts
```

Expected: FAIL because `composerSelections.ts` and `parseComposerCommand` do not exist.

- [ ] **Step 3: Implement composer selection model**

Create `frontend/web/src/components/chat/composerSelections.ts`:

```ts
export type ComposerSelectionKind =
  | "skill"
  | "mcp"
  | "agent"
  | "model"
  | "file"
  | "context";

export type ComposerSelectionState =
  | "enabled"
  | "disabled"
  | "pending"
  | "denied"
  | "unavailable";

export interface ComposerSelection {
  id: string;
  kind: ComposerSelectionKind;
  label: string;
  state: ComposerSelectionState;
  description?: string;
  referenceId?: string;
}

export type ComposerSelectionAction =
  | { type: "upsert"; selection: ComposerSelection }
  | { type: "remove"; id: string }
  | { type: "clear-kind"; kind: ComposerSelectionKind }
  | { type: "clear-all" };

export function composerSelectionReducer(
  state: ComposerSelection[],
  action: ComposerSelectionAction,
): ComposerSelection[] {
  switch (action.type) {
    case "upsert": {
      const next = state.filter((item) => item.id !== action.selection.id);
      return [...next, action.selection];
    }
    case "remove":
      return state.filter((item) => item.id !== action.id);
    case "clear-kind":
      return state.filter((item) => item.kind !== action.kind);
    case "clear-all":
      return [];
  }
}
```

- [ ] **Step 4: Extend command parsing**

Modify `frontend/web/src/components/chat/chatInputCommands.ts`:

```ts
import type { FeaturePanel } from "../selectors/FeatureMenu";

export const COMMAND_PREFIX_PANEL: Record<string, Exclude<FeaturePanel, null>> =
  {
    "/": "skills",
    "$": "skills",
  };

export const SLASH_COMMAND_PANEL: Record<string, Exclude<FeaturePanel, null>> =
  {
    skill: "skills",
    skills: "skills",
    mcp: "tools",
    tool: "tools",
    tools: "tools",
    agent: "agent",
    model: "model",
    file: "file",
    context: "context",
  };

export interface CommandPanelAvailability {
  skills: boolean;
  tools: boolean;
  agent?: boolean;
  model?: boolean;
  file?: boolean;
  context?: boolean;
}

export interface ParsedComposerCommand {
  panel: Exclude<FeaturePanel, null>;
  query: string;
  consumeInput: boolean;
}

export function resolveCommandPrefixPanel(
  input: string,
  availability: CommandPanelAvailability,
): FeaturePanel {
  const commandPanel = COMMAND_PREFIX_PANEL[input];
  if (!commandPanel) return null;
  return isPanelAvailable(commandPanel, availability) ? commandPanel : null;
}

export function parseComposerCommand(input: string): ParsedComposerCommand | null {
  const match = input.match(/^\/([a-z]+)(?:\s+(.*))?$/i);
  if (!match) return null;
  const panel = SLASH_COMMAND_PANEL[match[1].toLowerCase()];
  if (!panel) return null;
  return {
    panel,
    query: match[2] ?? "",
    consumeInput: true,
  };
}

function isPanelAvailable(
  panel: Exclude<FeaturePanel, null>,
  availability: CommandPanelAvailability,
): boolean {
  if (panel === "skills") return availability.skills;
  if (panel === "tools") return availability.tools;
  if (panel === "agent") return availability.agent !== false;
  if (panel === "model") return availability.model !== false;
  if (panel === "file") return availability.file !== false;
  if (panel === "context") return availability.context !== false;
  return true;
}
```

- [ ] **Step 5: Expand `FeaturePanel` and FeatureMenu groups**

Modify `frontend/web/src/components/selectors/FeatureMenu.tsx`:

```ts
export type FeaturePanel =
  | "persona"
  | "tools"
  | "skills"
  | "agent"
  | "model"
  | "file"
  | "context"
  | "thinking"
  | null;
```

Add menu items with labels:

```tsx
<MenuItem
  icon={<Bot size={18} />}
  label={t("featureMenu.agent", "Agent")}
  badge={agentName ? t(agentName) : undefined}
  active={activePanel === "agent"}
  onClick={() => onOpen("agent")}
/>
<MenuItem
  icon={<Settings2 size={18} />}
  label={t("featureMenu.model", "Model")}
  active={activePanel === "model"}
  onClick={() => onOpen("model")}
/>
<MenuItem
  icon={<FileText size={18} />}
  label={t("featureMenu.context", "Context")}
  active={activePanel === "context"}
  onClick={() => onOpen("context")}
/>
```

- [ ] **Step 6: Create chip component**

Create `frontend/web/src/components/chat/ComposerChips.tsx`:

```tsx
import { Bot, Box, FileText, Layers, Sparkles, Wrench, X } from "lucide-react";
import type { ComposerSelection, ComposerSelectionKind } from "./composerSelections";

const ICONS: Record<ComposerSelectionKind, React.ElementType> = {
  skill: Sparkles,
  mcp: Wrench,
  agent: Bot,
  model: Box,
  file: FileText,
  context: Layers,
};

export interface ComposerChipsProps {
  selections: ComposerSelection[];
  onRemove: (id: string) => void;
}

export function ComposerChips({ selections, onRemove }: ComposerChipsProps) {
  if (selections.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-1.5 px-3 pt-2" aria-label="Composer selections">
      {selections.map((selection) => {
        const Icon = ICONS[selection.kind];
        return (
          <span
            key={selection.id}
            className="inline-flex max-w-[220px] items-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-700 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-200"
            data-state={selection.state}
            title={selection.description ?? selection.label}
          >
            <Icon size={13} className="shrink-0" />
            <span className="truncate">{selection.label}</span>
            <button
              type="button"
              aria-label={`Remove ${selection.label}`}
              className="ml-1 inline-flex h-5 w-5 items-center justify-center rounded hover:bg-slate-200 dark:hover:bg-stone-700"
              onClick={() => onRemove(selection.id)}
            >
              <X size={12} />
            </button>
          </span>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 7: Wire chips into ChatInput**

In `ChatInput.tsx`, add reducer state:

```tsx
const [composerSelections, dispatchComposerSelection] = useReducer(
  composerSelectionReducer,
  [],
);
```

Render above the textarea:

```tsx
<ComposerChips
  selections={composerSelections}
  onRemove={(id) => dispatchComposerSelection({ type: "remove", id })}
/>
```

When attachments are ready, dispatch safe file chips:

```tsx
dispatchComposerSelection({
  type: "upsert",
  selection: {
    id: `file:${attachment.id}`,
    kind: "file",
    label: attachment.name,
    state: attachment.isUploading ? "pending" : "enabled",
    referenceId: attachment.id,
  },
});
```

Do not use `attachment.key`, `attachment.url`, local paths, or executor paths in chips.

- [ ] **Step 8: Run composer command test**

Run:

```bash
cd frontend/web
pnpm exec tsx --test src/components/chat/__tests__/composerCommandParity.test.ts src/components/chat/__tests__/frontendExperiencePhase1.test.ts
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add frontend/web/src/components/chat/composerSelections.ts frontend/web/src/components/chat/ComposerChips.tsx frontend/web/src/components/chat/__tests__/composerCommandParity.test.ts frontend/web/src/components/chat/chatInputCommands.ts frontend/web/src/components/selectors/FeatureMenu.tsx frontend/web/src/components/chat/ChatInput.tsx frontend/web/src/components/chat/ChatInputToolbar.tsx frontend/web/src/components/chat/ChatInputSelectors.tsx frontend/web/src/components/chat/ChatInputAttachments.tsx frontend/web/src/components/chat/chatInputTypes.ts frontend/web/src/hooks/useAgent.ts
git commit -m "feat: add governed composer commands and chips"
```

---

## Task 4: Add Skills And MCP Governance Surface States

**Files:**
- Create: `frontend/web/src/components/governance/GovernanceAvailabilityBadge.tsx`
- Create: `frontend/web/src/components/governance/groupAvailability.ts`
- Create: `frontend/web/src/components/panels/__tests__/governanceSurfaceSource.test.ts`
- Modify: `frontend/web/src/components/panels/SkillsHubPanel.tsx`
- Modify: files under `frontend/web/src/components/panels/SkillsHubPanel/`
- Modify: `frontend/web/src/components/panels/MCPPanel.tsx`
- Modify: `frontend/web/src/i18n/locales/en.json`
- Modify: `frontend/web/src/i18n/locales/zh.json`
- Test: `frontend/web/src/components/panels/__tests__/governanceSurfaceSource.test.ts`

**Interfaces:**
- Consumes: existing Skills and MCP projections, permission booleans, current route active tab.
- Produces: reusable `GovernanceAvailabilityBadge`, `resolveGroupAvailability`, ordinary-user marketplace filters, and fail-closed unavailable states for unbacked department/group policy controls.

- [ ] **Step 1: Write failing governance surface test**

Create `frontend/web/src/components/panels/__tests__/governanceSurfaceSource.test.ts`:

```ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { resolveGroupAvailability } from "../../governance/groupAvailability";

const root = process.cwd();

test("group availability has explicit governed states", () => {
  assert.equal(resolveGroupAvailability({ enabled: true }).state, "enabled");
  assert.equal(resolveGroupAvailability({ enabled: false }).state, "disabled");
  assert.equal(resolveGroupAvailability({ inherited: true }).state, "inherited");
  assert.equal(resolveGroupAvailability({ backed: false }).state, "unavailable");
});

test("skills hub exposes marketplace and group availability language", () => {
  const source = readFileSync(join(root, "src/components/panels/SkillsHubPanel.tsx"), "utf8");
  assert.match(source, /GovernanceAvailabilityBadge/);
  assert.match(source, /skills\.marketplace\.departmentAvailability/);
  assert.match(source, /skills\.marketplace\.groupToggleUnavailable/);
});

test("mcp panel exposes governed tools without raw lifecycle controls", () => {
  const source = readFileSync(join(root, "src/components/panels/MCPPanel.tsx"), "utf8");
  assert.match(source, /GovernanceAvailabilityBadge/);
  assert.match(source, /mcp\.permissionMode/);
  assert.doesNotMatch(source, /startServer|stopServer|restartServer|rawCredential/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd frontend/web
pnpm exec tsx --test src/components/panels/__tests__/governanceSurfaceSource.test.ts
```

Expected: FAIL because governance helpers do not exist.

- [ ] **Step 3: Create group availability helper**

Create `frontend/web/src/components/governance/groupAvailability.ts`:

```ts
export type GovernanceAvailabilityState =
  | "enabled"
  | "disabled"
  | "inherited"
  | "admin-only"
  | "unavailable";

export interface GroupAvailabilityInput {
  backed?: boolean;
  enabled?: boolean;
  inherited?: boolean;
  adminOnly?: boolean;
}

export interface GroupAvailabilityResult {
  state: GovernanceAvailabilityState;
  labelKey: string;
}

export function resolveGroupAvailability(
  input: GroupAvailabilityInput,
): GroupAvailabilityResult {
  if (input.backed === false) {
    return { state: "unavailable", labelKey: "governance.unavailable" };
  }
  if (input.adminOnly) {
    return { state: "admin-only", labelKey: "governance.adminOnly" };
  }
  if (input.inherited) {
    return { state: "inherited", labelKey: "governance.inherited" };
  }
  if (input.enabled === true) {
    return { state: "enabled", labelKey: "governance.enabled" };
  }
  return { state: "disabled", labelKey: "governance.disabled" };
}
```

- [ ] **Step 4: Create availability badge**

Create `frontend/web/src/components/governance/GovernanceAvailabilityBadge.tsx`:

```tsx
import { Ban, CheckCircle2, GitBranch, Lock, MinusCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { GovernanceAvailabilityState } from "./groupAvailability";

const BADGE_STYLE: Record<GovernanceAvailabilityState, string> = {
  enabled: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300",
  disabled: "bg-slate-100 text-slate-600 dark:bg-stone-800 dark:text-stone-300",
  inherited: "bg-sky-50 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300",
  "admin-only": "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300",
  unavailable: "bg-rose-50 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300",
};

const ICONS: Record<GovernanceAvailabilityState, React.ElementType> = {
  enabled: CheckCircle2,
  disabled: MinusCircle,
  inherited: GitBranch,
  "admin-only": Lock,
  unavailable: Ban,
};

export interface GovernanceAvailabilityBadgeProps {
  state: GovernanceAvailabilityState;
  labelKey: string;
}

export function GovernanceAvailabilityBadge({
  state,
  labelKey,
}: GovernanceAvailabilityBadgeProps) {
  const { t } = useTranslation();
  const Icon = ICONS[state];

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium ${BADGE_STYLE[state]}`}
      data-governance-state={state}
    >
      <Icon size={13} />
      {t(labelKey)}
    </span>
  );
}
```

- [ ] **Step 5: Add translation keys**

In `en.json`:

```json
{
  "governance": {
    "adminOnly": "Admin only",
    "disabled": "Disabled",
    "enabled": "Enabled",
    "inherited": "Inherited",
    "unavailable": "Phase 2 unavailable"
  },
  "skills": {
    "marketplace": {
      "departmentAvailability": "Department availability",
      "groupToggleUnavailable": "Department and group toggles require backend policy APIs."
    }
  },
  "mcp": {
    "permissionMode": "Permission mode",
    "addToComposer": "Add to composer"
  }
}
```

In `zh.json`:

```json
{
  "governance": {
    "adminOnly": "仅管理员",
    "disabled": "已禁用",
    "enabled": "已启用",
    "inherited": "继承",
    "unavailable": "第二阶段待接入"
  },
  "skills": {
    "marketplace": {
      "departmentAvailability": "部门可用范围",
      "groupToggleUnavailable": "部门和用户组开关需要后端策略 API。"
    }
  },
  "mcp": {
    "permissionMode": "权限模式",
    "addToComposer": "加入输入框"
  }
}
```

- [ ] **Step 6: Render Skills governance state**

In `SkillsHubPanel.tsx`, import:

```tsx
import { GovernanceAvailabilityBadge } from "../governance/GovernanceAvailabilityBadge";
import { resolveGroupAvailability } from "../governance/groupAvailability";
```

Render a read-only availability section:

```tsx
const availability = resolveGroupAvailability({ backed: false });

<section className="rounded-lg border border-slate-200/70 bg-white p-3 dark:border-stone-800 dark:bg-stone-900">
  <div className="flex items-center justify-between gap-3">
    <div>
      <h3 className="text-sm font-semibold">
        {t("skills.marketplace.departmentAvailability")}
      </h3>
      <p className="mt-1 text-xs text-slate-500 dark:text-stone-400">
        {t("skills.marketplace.groupToggleUnavailable")}
      </p>
    </div>
    <GovernanceAvailabilityBadge
      state={availability.state}
      labelKey={availability.labelKey}
    />
  </div>
</section>
```

- [ ] **Step 7: Render MCP governed tool state**

In `MCPPanel.tsx`, render permission mode beside each visible tool:

```tsx
<GovernanceAvailabilityBadge
  state={tool.enabled ? "enabled" : "admin-only"}
  labelKey={tool.enabled ? "governance.enabled" : "governance.adminOnly"}
/>
```

If the panel contains raw lifecycle controls for ordinary users, remove those controls from ordinary-user render paths and show:

```tsx
<p className="text-xs text-slate-500 dark:text-stone-400">
  {t("mcp.lifecycleUnavailable", "Server lifecycle and credentials are managed by administrators through backed policy APIs.")}
</p>
```

- [ ] **Step 8: Run governance tests**

Run:

```bash
cd frontend/web
pnpm exec tsx --test src/components/panels/__tests__/governanceSurfaceSource.test.ts
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add frontend/web/src/components/governance frontend/web/src/components/panels/__tests__/governanceSurfaceSource.test.ts frontend/web/src/components/panels/SkillsHubPanel.tsx frontend/web/src/components/panels/SkillsHubPanel frontend/web/src/components/panels/MCPPanel.tsx frontend/web/src/i18n/locales/en.json frontend/web/src/i18n/locales/zh.json
git commit -m "feat: surface governed skills and mcp availability"
```

---

## Task 5: Add Fail-Closed Share And Channel Import Surfaces

**Files:**
- Create: `frontend/web/src/components/share/ShareUnavailableState.tsx`
- Create: `frontend/web/src/components/channels/ChannelImportPanel.tsx`
- Create: `frontend/web/src/components/share/__tests__/shareChannelFailClosedSource.test.ts`
- Modify: `frontend/web/src/components/share/ShareDialog.tsx`
- Modify: `frontend/web/src/components/share/SharedPage.tsx`
- Modify: `frontend/web/src/components/pages/ChannelsPage.tsx`
- Modify: `frontend/web/src/components/layout/AppContent/TabContent.tsx`
- Modify: `frontend/web/src/components/launchpad/LaunchpadPanel.tsx`
- Modify: `frontend/web/src/i18n/locales/en.json`
- Modify: `frontend/web/src/i18n/locales/zh.json`
- Test: `frontend/web/src/components/share/__tests__/shareChannelFailClosedSource.test.ts`

**Interfaces:**
- Consumes: existing `ShareDialog`, `SharedPage`, `/channels` route, launchpad catalog.
- Produces: `ShareUnavailableState`, `ChannelImportPanel`, explicit denied/expired/revoked/unavailable states, and launchpad copy that keeps target-system boundaries visible.

- [ ] **Step 1: Write failing fail-closed test**

Create `frontend/web/src/components/share/__tests__/shareChannelFailClosedSource.test.ts`:

```ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

test("shared session page has explicit fail-closed states", () => {
  const sharedPage = readFileSync(join(root, "src/components/share/SharedPage.tsx"), "utf8");
  const unavailable = readFileSync(join(root, "src/components/share/ShareUnavailableState.tsx"), "utf8");

  assert.match(sharedPage, /ShareUnavailableState/);
  assert.match(unavailable, /denied/);
  assert.match(unavailable, /expired/);
  assert.match(unavailable, /revoked/);
  assert.match(unavailable, /unavailable/);
});

test("channel import page is governed and fail closed", () => {
  const channelPanel = readFileSync(join(root, "src/components/channels/ChannelImportPanel.tsx"), "utf8");
  const channelsPage = readFileSync(join(root, "src/components/pages/ChannelsPage.tsx"), "utf8");

  assert.match(channelPanel, /channelImport\.unavailable/);
  assert.match(channelPanel, /redaction/);
  assert.match(channelPanel, /retention/);
  assert.match(channelsPage, /ChannelImportPanel/);
});

test("launchpad copy keeps click-through boundary visible", () => {
  const launchpad = readFileSync(join(root, "src/components/launchpad/LaunchpadPanel.tsx"), "utf8");
  assert.match(launchpad, /launchpad\.boundary/);
  assert.doesNotMatch(launchpad, /migrate.*nonGMPlims/i);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd frontend/web
pnpm exec tsx --test src/components/share/__tests__/shareChannelFailClosedSource.test.ts
```

Expected: FAIL because `ShareUnavailableState.tsx` and `ChannelImportPanel.tsx` do not exist.

- [ ] **Step 3: Create share unavailable state**

Create `frontend/web/src/components/share/ShareUnavailableState.tsx`:

```tsx
import { Ban, Clock, Lock, ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

export type ShareUnavailableReason =
  | "denied"
  | "expired"
  | "revoked"
  | "unavailable";

const ICONS: Record<ShareUnavailableReason, React.ElementType> = {
  denied: Lock,
  expired: Clock,
  revoked: Ban,
  unavailable: ShieldAlert,
};

export interface ShareUnavailableStateProps {
  reason: ShareUnavailableReason;
}

export function ShareUnavailableState({ reason }: ShareUnavailableStateProps) {
  const { t } = useTranslation();
  const Icon = ICONS[reason];

  return (
    <main className="flex min-h-dvh items-center justify-center bg-slate-50 p-6 dark:bg-stone-950">
      <section className="w-full max-w-md rounded-lg border border-slate-200 bg-white p-6 text-center shadow-[0_4px_12px_rgba(18,38,63,0.03)] dark:border-stone-800 dark:bg-stone-900">
        <Icon className="mx-auto text-slate-500 dark:text-stone-300" size={32} />
        <h1 className="mt-4 text-lg font-semibold text-slate-900 dark:text-stone-50">
          {t(`share.unavailable.${reason}.title`)}
        </h1>
        <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-stone-300">
          {t(`share.unavailable.${reason}.description`)}
        </p>
      </section>
    </main>
  );
}
```

- [ ] **Step 4: Create channel import panel**

Create `frontend/web/src/components/channels/ChannelImportPanel.tsx`:

```tsx
import { MessageSquarePlus, ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { workbenchSurface } from "../workbench/workbenchSurface";

export function ChannelImportPanel() {
  const { t } = useTranslation();
  const backedSources: Array<{
    id: string;
    name: string;
    redaction: string;
    retention: string;
  }> = [];

  if (backedSources.length === 0) {
    return (
      <div className="flex h-full min-h-0 items-center justify-center p-6">
        <section className={`${workbenchSurface.compactPanel} max-w-xl p-5 text-center`}>
          <ShieldAlert className="mx-auto text-slate-500" size={32} />
          <h2 className="mt-4 text-base font-semibold">
            {t("channelImport.unavailable.title")}
          </h2>
          <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-stone-300">
            {t("channelImport.unavailable.description")}
          </p>
        </section>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-4">
      {backedSources.map((source) => (
        <section key={source.id} className={`${workbenchSurface.compactPanel} p-3`}>
          <div className="flex items-center gap-2">
            <MessageSquarePlus size={16} />
            <h2 className="text-sm font-semibold">{source.name}</h2>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            {t("channelImport.redaction")}: {source.redaction}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {t("channelImport.retention")}: {source.retention}
          </p>
        </section>
      ))}
    </div>
  );
}
```

- [ ] **Step 5: Wire share page fail-closed states**

In `SharedPage.tsx`, import and render `ShareUnavailableState` for denied and missing data cases:

```tsx
import { ShareUnavailableState } from "./ShareUnavailableState";

if (errorStatus === 403) {
  return <ShareUnavailableState reason="denied" />;
}

if (shareState === "expired") {
  return <ShareUnavailableState reason="expired" />;
}

if (shareState === "revoked") {
  return <ShareUnavailableState reason="revoked" />;
}

if (!data && !isLoading) {
  return <ShareUnavailableState reason="unavailable" />;
}
```

Use the actual local error/status variables already present in `SharedPage.tsx`; keep the four explicit branches and do not reveal session metadata in the unavailable component.

- [ ] **Step 6: Wire `/channels` to channel import**

In `frontend/web/src/components/pages/ChannelsPage.tsx`:

```tsx
import { ChannelImportPanel } from "../channels/ChannelImportPanel";

export function ChannelsPage() {
  return <ChannelImportPanel />;
}
```

In `TabContent.tsx`, keep `channels` mapped to the channels route component and remove old generic integration copy from active route rendering.

- [ ] **Step 7: Add launchpad boundary copy**

In `LaunchpadPanel.tsx`, render:

```tsx
<p className="text-xs leading-5 text-slate-500 dark:text-stone-400">
  {t(
    "launchpad.boundary",
    "These entries open existing company systems in a new tab. AI Platform does not replace their login, permissions, workflow, or audit rules.",
  )}
</p>
```

- [ ] **Step 8: Add translations**

In `en.json`:

```json
{
  "channelImport": {
    "redaction": "Redaction",
    "retention": "Retention",
    "unavailable": {
      "description": "Channel import requires ai-platform channel ACL projections. No channel metadata is shown until those APIs are available.",
      "title": "Channel import is not available yet"
    }
  },
  "launchpad": {
    "boundary": "These entries open existing company systems in a new tab. AI Platform does not replace their login, permissions, workflow, or audit rules."
  },
  "share": {
    "unavailable": {
      "denied": {
        "description": "Your current account is not allowed to view this shared session.",
        "title": "Share access denied"
      },
      "expired": {
        "description": "This share has expired and no session metadata is available.",
        "title": "Share expired"
      },
      "revoked": {
        "description": "This share was revoked and no session metadata is available.",
        "title": "Share revoked"
      },
      "unavailable": {
        "description": "The share cannot be loaded through an authorized ai-platform projection.",
        "title": "Share unavailable"
      }
    }
  }
}
```

In `zh.json`:

```json
{
  "channelImport": {
    "redaction": "脱敏",
    "retention": "保留策略",
    "unavailable": {
      "description": "频道导入需要 ai-platform 的频道 ACL 投影。相关 API 可用前不会展示频道元数据。",
      "title": "频道导入暂不可用"
    }
  },
  "launchpad": {
    "boundary": "这些入口会在新标签页打开现有公司系统。AI Platform 不替代目标系统的登录、权限、流程或审计规则。"
  },
  "share": {
    "unavailable": {
      "denied": {
        "description": "当前账号无权查看这个会话分享。",
        "title": "无权访问分享"
      },
      "expired": {
        "description": "这个分享已过期，不展示任何会话元数据。",
        "title": "分享已过期"
      },
      "revoked": {
        "description": "这个分享已撤销，不展示任何会话元数据。",
        "title": "分享已撤销"
      },
      "unavailable": {
        "description": "无法通过授权的 ai-platform 投影加载该分享。",
        "title": "分享暂不可用"
      }
    }
  }
}
```

- [ ] **Step 9: Run fail-closed test**

Run:

```bash
cd frontend/web
pnpm exec tsx --test src/components/share/__tests__/shareChannelFailClosedSource.test.ts
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add frontend/web/src/components/share/ShareUnavailableState.tsx frontend/web/src/components/channels/ChannelImportPanel.tsx frontend/web/src/components/share/__tests__/shareChannelFailClosedSource.test.ts frontend/web/src/components/share/ShareDialog.tsx frontend/web/src/components/share/SharedPage.tsx frontend/web/src/components/pages/ChannelsPage.tsx frontend/web/src/components/layout/AppContent/TabContent.tsx frontend/web/src/components/launchpad/LaunchpadPanel.tsx frontend/web/src/i18n/locales/en.json frontend/web/src/i18n/locales/zh.json
git commit -m "feat: add governed share and channel import states"
```

---

## Task 6: Add Acceptance Guard, Build, Browser Evidence, And 211 Smoke

**Files:**
- Create: `frontend/web/src/__tests__/frontendShellParityAcceptance.test.ts`
- Create: `docs/release-evidence/frontend-shell-parity/.gitkeep`
- Modify: `docs/superpowers/plans/2026-06-20-ai-platform-frontend-experience-phase1.md`
- Test: `frontend/web/src/__tests__/frontendShellParityAcceptance.test.ts`

**Interfaces:**
- Consumes: outputs from Tasks 1-5, current 211 static frontend service, existing build provenance file.
- Produces: acceptance guard, screenshot/evidence directory, and deployment verification checklist for `211 verified`.

- [ ] **Step 1: Write acceptance guard**

Create `frontend/web/src/__tests__/frontendShellParityAcceptance.test.ts`:

```ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

test("frontend shell parity components are registered", () => {
  const files = [
    "src/components/workbench/WorkbenchShell.tsx",
    "src/components/workbench/WorkbenchRightPanel.tsx",
    "src/components/chat/ComposerChips.tsx",
    "src/components/governance/GovernanceAvailabilityBadge.tsx",
    "src/components/channels/ChannelImportPanel.tsx",
    "src/components/share/ShareUnavailableState.tsx",
  ];

  for (const file of files) {
    assert.match(readFileSync(join(root, file), "utf8"), /export /, file);
  }
});

test("app routes expose PRD phase 1B and 1C surfaces", () => {
  const app = readFileSync(join(root, "src/App.tsx"), "utf8");
  const tabs = readFileSync(
    join(root, "src/components/layout/AppContent/TabContent.tsx"),
    "utf8",
  );

  for (const route of ["/chat", "/apps", "/skills", "/mcp", "/channels"]) {
    assert.match(app, new RegExp(`path="${route.replace("/", "\\/")}`));
  }

  assert.match(tabs, /apps:\s*LaunchpadPanel/);
  assert.match(tabs, /skills:\s*SkillsHubPanel/);
  assert.match(tabs, /mcp:\s*MCPPanel/);
});

test("legacy brand authority is absent from active browser entry", () => {
  const index = readFileSync(join(root, "index.html"), "utf8");
  assert.doesNotMatch(index, /\bLambChat\b|lambchat\.com/i);
  assert.match(index, /AI Platform - Enterprise AI Workbench/);
});
```

- [ ] **Step 2: Run acceptance guard**

Run:

```bash
cd frontend/web
pnpm exec tsx --test src/__tests__/frontendShellParityAcceptance.test.ts
```

Expected: PASS.

- [ ] **Step 3: Run full changed frontend source tests**

Run:

```bash
cd frontend/web
pnpm exec tsx --test src/__tests__/aiPlatformBrandGuard.test.ts src/__tests__/frontendShellParityAcceptance.test.ts src/components/workbench/__tests__/workbenchShellSource.test.ts src/components/chat/__tests__/composerCommandParity.test.ts src/components/chat/__tests__/frontendExperiencePhase1.test.ts src/components/panels/__tests__/governanceSurfaceSource.test.ts src/components/share/__tests__/shareChannelFailClosedSource.test.ts src/__tests__/launchpadRoute.test.ts src/components/launchpad/__tests__/catalog.test.ts
```

Expected: PASS, all listed tests green.

- [ ] **Step 4: Run projection audit and build**

Run:

```bash
cd frontend/web
pnpm run ci:verify
```

Expected: exit 0. Existing chunk-size warnings are acceptable; TypeScript, ESLint, Vite build, and build provenance must complete.

- [ ] **Step 5: Run repository compile check**

Run:

```bash
python -m compileall -q app tools scripts
```

Expected: exit 0.

- [ ] **Step 6: Add phase-one supersession note**

Append this section to `docs/superpowers/plans/2026-06-20-ai-platform-frontend-experience-phase1.md`:

```markdown
## Supersession Note

Phase 1 source and deployment evidence proved the static frontend could be built,
merged, and served through the 211 entry. Product-experience acceptance now moves
to `docs/superpowers/plans/2026-06-20-ai-platform-frontend-shell-parity.md`,
which covers brand removal, workbench shell parity, composer chips, governed
Skills/MCP surfaces, share/channel fail-closed states, browser screenshots, and
211 smoke.
```

- [ ] **Step 7: Create evidence directory**

Create `docs/release-evidence/frontend-shell-parity/.gitkeep` as an empty file.

- [ ] **Step 8: Commit local acceptance work**

```bash
git add frontend/web/src/__tests__/frontendShellParityAcceptance.test.ts docs/release-evidence/frontend-shell-parity/.gitkeep docs/superpowers/plans/2026-06-20-ai-platform-frontend-experience-phase1.md
git commit -m "test: guard frontend shell parity acceptance"
```

- [ ] **Step 9: Build clean package for 211**

Run from a clean worktree at the final branch commit:

```bash
cd frontend/web
pnpm install --frozen-lockfile
pnpm run build
cat dist/ai-platform-build-provenance.json
```

Expected: `git.commit` equals the final branch commit and `git.dirty` is `false`.

- [ ] **Step 10: Replace 211 static dist**

Use the existing 211 static-service flow:

```bash
tar -czf .pytest-tmp/ai-platform-frontend-shell-parity-dist.tar.gz -C frontend/web/dist .
```

Upload the tarball to `s211` as `/home/xinlin.jiang/ai-platform-frontend-shell-parity-dist.tar.gz`, then run on `s211`:

```bash
ROOT=/home/xinlin.jiang/frontend-pr111-smoke
ARCHIVE=/home/xinlin.jiang/ai-platform-frontend-shell-parity-dist.tar.gz
STAMP=$(date +%Y%m%d-%H%M%S)
STAGING="$ROOT/dist-staging-shell-parity-$STAMP"
BACKUP="$ROOT/dist-backup-before-shell-parity-$STAMP"
mkdir -p "$STAGING"
tar -xzf "$ARCHIVE" -C "$STAGING"
python3 - <<'PY' "$STAGING/ai-platform-build-provenance.json"
import json, sys
p=json.load(open(sys.argv[1], encoding="utf-8"))
assert p["git"]["dirty"] is False, p
print("provenance_ok", p["git"]["commit"])
PY
mv "$ROOT/dist" "$BACKUP"
mv "$STAGING" "$ROOT/dist"
```

Expected: `provenance_ok <final-commit>` and a printed backup directory.

- [ ] **Step 11: Restart 18001**

Run on `s211`:

```bash
ROOT=/home/xinlin.jiang/frontend-pr111-smoke
PIDS=$(ps -ef | awk '/serve_ai_platform_frontend.py/ && /--port 18001/ && !/awk/ {print $2}')
if [ -n "$PIDS" ]; then kill $PIDS; sleep 1; fi
nohup python3 "$ROOT/tools/serve_ai_platform_frontend.py" --host 0.0.0.0 --port 18001 --root "$ROOT/dist" --api-base http://127.0.0.1:8020 > "$ROOT/frontend-18001.log" 2>&1 &
sleep 2
ps -ef | grep 'serve_ai_platform_frontend.py' | grep -v grep
```

Expected: one `--port 18001` process and listener on `0.0.0.0:18001`.

- [ ] **Step 12: Run 211 smoke**

Run on `s211`:

```bash
curl -fsS -o /tmp/ai-platform-root.html -w 'root_http=%{http_code}\n' http://127.0.0.1:18001/
curl -fsS -o /tmp/ai-platform-login.html -w 'login_http=%{http_code}\n' http://127.0.0.1:18001/auth/login
curl -fsS http://127.0.0.1:8020/api/ai/health
grep -E 'AI Platform - Enterprise AI Workbench|assets/index-' /tmp/ai-platform-root.html
! grep -E 'LambChat|lambchat\.com' /tmp/ai-platform-root.html
cat /home/xinlin.jiang/frontend-pr111-smoke/dist/ai-platform-build-provenance.json
```

Expected: `root_http=200`, `login_http=200`, backend health `{"status":"ok"}`, AI Platform title present, no LambChat root HTML markers, provenance dirty false.

- [ ] **Step 13: Browser screenshot evidence**

Capture desktop screenshots for:

```text
http://10.56.0.211:18001/auth/login
http://10.56.0.211:18001/apps
http://10.56.0.211:18001/chat
http://10.56.0.211:18001/skills
http://10.56.0.211:18001/mcp
http://10.56.0.211:18001/channels
```

Save them under `docs/release-evidence/frontend-shell-parity/` with filenames:

```text
login.png
apps.png
chat.png
skills.png
mcp.png
channels.png
```

Expected: authenticated screenshots show ai-platform workbench shell, no LambChat brand, and no overlapping controls at desktop width.

---

## Self-Review

**Spec coverage:** This plan covers PRD goals for brand removal, LibreChat / poco-claw shell absorption, slash-command composer, `$` Skills shortcut, durable file/Skill/MCP chips, Skills marketplace availability, group toggle unavailable states, MCP governed tool visibility, session share fail-closed states, channel import unavailable states, `/apps` launchpad boundary, admin/ordinary permission-gated visibility, screenshot acceptance, and 211 static runtime evidence.

**Known gaps intentionally deferred to Phase 2:** Real department/group Skill enablement APIs, MCP policy assignment APIs, channel source import APIs, users/roles/departments management, model administration, and backed share ACL expansion are not implemented here. The plan renders explicit unavailable states for those contracts.

**Placeholder scan:** No task uses "TBD", "TODO", "implement later", "similar to", or unspecified error handling. The phrase "Phase 2 unavailable" is a concrete user-facing fail-closed state required by the PRD, not a missing implementation placeholder.

**Type consistency:** `FeaturePanel` expands to include `"model"`, `"file"`, and `"context"` before command parsing and menu rendering consume those values. `ComposerSelection`, `ComposerSelectionKind`, and `ComposerSelectionState` are defined in Task 3 before `ComposerChips` and tests consume them. `GovernanceAvailabilityState` is defined in Task 4 before `GovernanceAvailabilityBadge` consumes it. `ShareUnavailableReason` is defined in Task 5 before `SharedPage` consumes it.
