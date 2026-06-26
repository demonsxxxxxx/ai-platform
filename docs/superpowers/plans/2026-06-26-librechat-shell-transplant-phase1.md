# LibreChat Shell Transplant Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the authenticated ai-platform frontend read as a bounded LibreChat-style workbench shell while keeping ai-platform as the only auth, RBAC, session, run, Skill, MCP, marketplace, artifact, and governance authority.

**Architecture:** Introduce a local `librechatShell` component/token layer that ports LibreChat shell structure and interaction geometry into ai-platform-owned React components. Existing `SessionSidebar`, `WorkbenchShell`, `ChatInput`, and `WorkbenchRightPanel` become wrappers/consumers of this layer, preserving current ai-platform hooks and service adapters.

**Tech Stack:** React 19, Vite 6, TypeScript, Tailwind classes, lucide-react, node:test source guards, existing ai-platform frontend hooks and API services.

## Global Constraints

- Work only in isolated worktree `C:\aiwt\librechat-shell-transplant-20260626` on branch `codex/librechat-shell-transplant-20260626`.
- Pinned LibreChat reference commit is `9e74cc0e57b395926122bd4062c1fcedc48ed465` under `C:\aiwt\references\LibreChat-9e74cc0`.
- Do not copy LibreChat backend/API/auth/RBAC/data-provider contracts into ai-platform.
- Do not import `librechat-data-provider`, Recoil store, LibreChat route trees, LibreChat providers, Mongo models, provider secrets, backend, Docker, or file-store code.
- Keep production frontend source under `frontend/web`.
- Follow TDD: write and run a failing test before production code for each behavior change.
- Do not commit `.codex/`, `.superpowers/`, `.pytest-tmp/`, evidence JSON/screenshots, `dist`, `node_modules`, tarballs, or zips.
- Do not write credentials into source, docs, logs, PR body, comments, or evidence.
- Keep status labels exact: `local partial`, `PR ready`, `reviewed`, `merged`, `211 verified`, and `gate closable`.
- 211 frontend is a Python static service, not Docker.

---

## File Structure

- Create `frontend/web/src/components/librechatShell/libreChatSurface.ts`
  - Owns LibreChat-derived shell class tokens, reference metadata, forbidden import regexes, and sidebar geometry constants.
- Create `frontend/web/src/components/librechatShell/LibreChatShell.tsx`
  - Layout owner for thread, composer, and right context panel.
- Create `frontend/web/src/components/librechatShell/LibreChatRail.tsx`
  - Pure rail button/list helpers used by the existing sidebar without replacing ai-platform route handlers.
- Create `frontend/web/src/components/librechatShell/LibreChatPanel.tsx`
  - Expanded panel section helpers for app/governance/session groups.
- Create `frontend/web/src/components/librechatShell/LibreChatSidePanel.tsx`
  - Right panel tabs and state helpers for Context, Artifacts, Run, Permissions.
- Create `frontend/web/src/components/librechatShell/__tests__/libreChatShellSource.test.ts`
  - Source/provenance guard and forbidden import guard.
- Create `frontend/web/src/components/librechatShell/__tests__/libreChatShellLayout.test.ts`
  - Shell/nav/right-panel/composer layout source guard.
- Modify `frontend/web/src/components/workbench/WorkbenchShell.tsx`
  - Re-export/use `LibreChatShell` as active owner.
- Modify `frontend/web/src/components/workbench/workbenchSurface.ts`
  - Map workbench tokens to `libreChatSurface` so catalog pages stay compatible.
- Modify `frontend/web/src/components/workbench/WorkbenchRightPanel.tsx`
  - Use the tabbed `LibreChatSidePanel` model.
- Modify `frontend/web/src/components/panels/SessionSidebar.tsx`
  - Use LibreChat constants for 52px rail and 360px expanded width, with mobile Escape close.
- Modify `frontend/web/src/components/panels/SidebarParts/SessionListContent.tsx`
  - Use `LibreChatPanel` section primitives and explicit LibreChat provenance markers.
- Modify `frontend/web/src/components/panels/SidebarParts/SidebarRail.tsx`
  - Use `LibreChatRail` button primitive and keep all ai-platform routes.
- Modify `frontend/web/src/components/chat/ChatInput.tsx`
  - Add LibreChat composer source marker and stable composer regions.
- Modify `frontend/web/src/styles/base.css`
  - Adjust shell tokens to neutral LibreChat-style surfaces.
- Modify `frontend/web/src/styles/chat.css`
  - Tighten composer and command menu geometry to one shell token system.
- Modify existing source tests:
  - `frontend/web/src/components/workbench/__tests__/workbenchVisualClosure.test.ts`
  - `frontend/web/src/components/workbench/__tests__/workbenchShellSource.test.ts`
  - `frontend/web/src/__tests__/frontendShellParityAcceptance.test.ts`
  - `frontend/web/src/components/chat/__tests__/composerCommandParity.test.ts`

---

### Task 1: LibreChat Shell Provenance And Guardrails

**Files:**
- Create: `frontend/web/src/components/librechatShell/libreChatSurface.ts`
- Create: `frontend/web/src/components/librechatShell/__tests__/libreChatShellSource.test.ts`

**Interfaces:**
- Produces:
  - `LIBRECHAT_SHELL_REFERENCE: { repository: string; commit: string; sourcePaths: string[]; intake: string }`
  - `LIBRECHAT_SHELL_GEOMETRY: { railWidthPx: 52; expandedMinWidthPx: 360; mobileMaxWidth: string }`
  - `FORBIDDEN_LIBRECHAT_IMPORTS: RegExp[]`
  - `libreChatSurface: Record<string, string>`

- [ ] **Step 1: Write the failing test**

```ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("librechat shell records pinned source provenance", () => {
  const source = read("src/components/librechatShell/libreChatSurface.ts");
  assert.match(source, /9e74cc0e57b395926122bd4062c1fcedc48ed465/);
  assert.match(source, /client\/src\/components\/UnifiedSidebar\/UnifiedSidebar\.tsx/);
  assert.match(source, /client\/src\/components\/Chat\/Input\/ChatForm\.tsx/);
  assert.match(source, /client\/src\/components\/SidePanel\/Nav\.tsx/);
  assert.match(source, /concept-only where license posture is ambiguous/);
});

test("active frontend graph forbids LibreChat backend authority imports", () => {
  const files = [
    "src/components/librechatShell/libreChatSurface.ts",
    "src/components/librechatShell/LibreChatShell.tsx",
    "src/components/librechatShell/LibreChatRail.tsx",
    "src/components/librechatShell/LibreChatPanel.tsx",
    "src/components/librechatShell/LibreChatSidePanel.tsx",
  ];
  const combined = files
    .filter((file) => read(file))
    .map((file) => read(file))
    .join("\n");

  for (const forbidden of [
    "librechat-data-provider",
    "useRecoilState",
    "~/Providers",
    "~/store",
    "useChatHelpers",
    "useGetStartupConfig",
  ]) {
    assert.doesNotMatch(combined, new RegExp(forbidden.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
});

test("librechat shell geometry keeps the approved rail and panel widths", () => {
  const source = read("src/components/librechatShell/libreChatSurface.ts");
  assert.match(source, /railWidthPx:\s*52/);
  assert.match(source, /expandedMinWidthPx:\s*360/);
  assert.match(source, /mobileMaxWidth:\s*"min\(85vw, 380px\)"/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/web; pnpm exec tsx src/components/librechatShell/__tests__/libreChatShellSource.test.ts`

Expected: FAIL because `src/components/librechatShell/libreChatSurface.ts` does not exist.

- [ ] **Step 3: Write minimal implementation**

```ts
import { clsx } from "clsx";

export const LIBRECHAT_SHELL_REFERENCE = {
  repository: "https://github.com/danny-avila/LibreChat",
  commit: "9e74cc0e57b395926122bd4062c1fcedc48ed465",
  sourcePaths: [
    "client/src/components/UnifiedSidebar/UnifiedSidebar.tsx",
    "client/src/components/UnifiedSidebar/Sidebar.tsx",
    "client/src/components/UnifiedSidebar/ExpandedPanel.tsx",
    "client/src/components/Chat/Input/ChatForm.tsx",
    "client/src/components/SidePanel/Nav.tsx",
    "client/src/components/Artifacts/*",
  ],
  intake:
    "Port shell structure and interaction geometry; concept-only where license posture is ambiguous.",
} as const;

export const LIBRECHAT_SHELL_GEOMETRY = {
  railWidthPx: 52,
  expandedMinWidthPx: 360,
  mobileMaxWidth: "min(85vw, 380px)",
} as const;

export const FORBIDDEN_LIBRECHAT_IMPORTS = [
  /librechat-data-provider/,
  /useRecoilState/,
  /~\/Providers/,
  /~\/store/,
  /useChatHelpers/,
  /useGetStartupConfig/,
];

export const libreChatSurface = {
  root: clsx("librechat-shell-root flex min-h-0 flex-1 bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)]"),
  workspace: clsx("librechat-shell-workspace grid min-h-0 w-full flex-1 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_20rem]"),
  thread: clsx("librechat-shell-thread workbench-thread-frame flex min-w-0 flex-1 flex-col border-r border-[var(--theme-border)] bg-[var(--theme-workbench-canvas)]"),
  threadBody: "flex min-h-0 flex-1 flex-col px-3 pb-2 sm:px-4",
  composer: "shrink-0 border-t border-[var(--theme-border)] bg-[var(--theme-workbench-canvas)] px-3 py-2.5",
  context: "hidden min-h-0 w-80 shrink-0 flex-col border-l border-[var(--theme-border)] bg-[var(--theme-workbench-canvas)] xl:flex",
  panel: "rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] shadow-[0_1px_2px_rgba(15,23,42,0.04)]",
  commandSurface: "rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] shadow-[0_18px_40px_rgba(15,23,42,0.12)]",
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend/web; pnpm exec tsx src/components/librechatShell/__tests__/libreChatShellSource.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/web/src/components/librechatShell/libreChatSurface.ts frontend/web/src/components/librechatShell/__tests__/libreChatShellSource.test.ts
git commit -m "feat: add librechat shell provenance guards"
```

---

### Task 2: Shell Owner And Surface Tokens

**Files:**
- Create: `frontend/web/src/components/librechatShell/LibreChatShell.tsx`
- Modify: `frontend/web/src/components/workbench/WorkbenchShell.tsx`
- Modify: `frontend/web/src/components/workbench/workbenchSurface.ts`
- Test: `frontend/web/src/components/librechatShell/__tests__/libreChatShellLayout.test.ts`
- Test: `frontend/web/src/components/workbench/__tests__/workbenchShellSource.test.ts`

**Interfaces:**
- Consumes: `libreChatSurface` from Task 1.
- Produces: `LibreChatShell({ children, composer, rightPanel }: { children: ReactNode; composer?: ReactNode; rightPanel?: ReactNode })`.

- [ ] **Step 1: Write the failing test**

```ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();
const read = (path: string) => readFileSync(join(root, path), "utf8");

test("workbench shell is owned by the local LibreChat shell layer", () => {
  const libreShell = read("src/components/librechatShell/LibreChatShell.tsx");
  const workbenchShell = read("src/components/workbench/WorkbenchShell.tsx");
  const chatApp = read("src/components/layout/AppContent/ChatAppContent.tsx");

  assert.match(libreShell, /data-librechat-shell="phase1"/);
  assert.match(libreShell, /data-workbench-region="thread"/);
  assert.match(libreShell, /data-workbench-region="composer"/);
  assert.match(libreShell, /data-workbench-region="context"/);
  assert.match(workbenchShell, /LibreChatShell/);
  assert.match(chatApp, /WorkbenchShell/);
});

test("surface tokens expose one neutral chat canvas", () => {
  const surface = read("src/components/workbench/workbenchSurface.ts");
  const baseCss = read("src/styles/base.css");

  assert.match(surface, /libreChatSurface/);
  assert.match(baseCss, /--theme-workbench-canvas:\s*#e5e8ed;/);
  assert.match(baseCss, /--theme-workbench-panel:\s*#f3f4f6;/);
  assert.match(baseCss, /--theme-bg-card:\s*#f8fafc;/);
  assert.doesNotMatch(baseCss, /--theme-bg-card:\s*#ffffff;/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/web; pnpm exec tsx src/components/librechatShell/__tests__/libreChatShellLayout.test.ts`

Expected: FAIL because `LibreChatShell.tsx` does not exist and token values have not changed.

- [ ] **Step 3: Write minimal implementation**

```tsx
import type { ReactNode } from "react";
import { libreChatSurface } from "./libreChatSurface";

export interface LibreChatShellProps {
  children: ReactNode;
  composer?: ReactNode;
  rightPanel?: ReactNode;
}

export function LibreChatShell({ children, composer, rightPanel }: LibreChatShellProps) {
  return (
    <section className={libreChatSurface.root} data-librechat-shell="phase1" data-phase1-closure-shell>
      <div className={libreChatSurface.workspace}>
        <div className={libreChatSurface.thread}>
          <div data-workbench-region="thread" className={libreChatSurface.threadBody}>
            {children}
          </div>
          {composer && (
            <div data-workbench-region="composer" className={libreChatSurface.composer}>
              {composer}
            </div>
          )}
        </div>
        <div data-workbench-region="context" className={libreChatSurface.context}>
          {rightPanel}
        </div>
      </div>
    </section>
  );
}
```

`WorkbenchShell.tsx` should become:

```tsx
import { LibreChatShell, type LibreChatShellProps } from "../librechatShell/LibreChatShell";

export type WorkbenchShellProps = LibreChatShellProps;

export function WorkbenchShell(props: WorkbenchShellProps) {
  return <LibreChatShell {...props} />;
}
```

`workbenchSurface.ts` should import `libreChatSurface` and use it for `root`, `workspace`, `thread`, `threadBody`, `composer`, `context`, `panel`, `secondaryPanel`, and `commandSurface`.

`base.css` light tokens should become:

```css
--theme-bg: #e5e8ed;
--theme-workbench-canvas: #e5e8ed;
--theme-workbench-panel: #f3f4f6;
--theme-bg-card: #f8fafc;
--theme-bg-sidebar: #edf0f4;
--theme-border: #d9dde5;
--theme-border-strong: #b8c0cc;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend/web; pnpm exec tsx src/components/librechatShell/__tests__/libreChatShellLayout.test.ts src/components/workbench/__tests__/workbenchShellSource.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/web/src/components/librechatShell/LibreChatShell.tsx frontend/web/src/components/librechatShell/__tests__/libreChatShellLayout.test.ts frontend/web/src/components/workbench/WorkbenchShell.tsx frontend/web/src/components/workbench/workbenchSurface.ts frontend/web/src/styles/base.css frontend/web/src/components/workbench/__tests__/workbenchShellSource.test.ts
git commit -m "feat: make librechat shell the workbench owner"
```

---

### Task 3: Sidebar Rail And Expanded Panel Transplant

**Files:**
- Create: `frontend/web/src/components/librechatShell/LibreChatRail.tsx`
- Create: `frontend/web/src/components/librechatShell/LibreChatPanel.tsx`
- Modify: `frontend/web/src/components/panels/SessionSidebar.tsx`
- Modify: `frontend/web/src/components/panels/SidebarParts/SessionListContent.tsx`
- Modify: `frontend/web/src/components/panels/SidebarParts/SidebarRail.tsx`
- Modify: `frontend/web/src/styles/base.css`
- Test: `frontend/web/src/components/librechatShell/__tests__/libreChatShellLayout.test.ts`
- Test: `frontend/web/src/__tests__/frontendShellParityAcceptance.test.ts`

**Interfaces:**
- Consumes: `LIBRECHAT_SHELL_GEOMETRY`.
- Produces:
  - `LibreChatRailButton({ itemKey, active, children, ...buttonProps })`
  - `LibreChatPanelSection({ group, label, children })`

- [ ] **Step 1: Write the failing test**

Append to `libreChatShellLayout.test.ts`:

```ts
test("sidebar transplants LibreChat rail geometry and mobile close behavior", () => {
  const sessionSidebar = read("src/components/panels/SessionSidebar.tsx");
  const list = read("src/components/panels/SidebarParts/SessionListContent.tsx");
  const rail = read("src/components/panels/SidebarParts/SidebarRail.tsx");
  const surface = read("src/components/librechatShell/libreChatSurface.ts");

  assert.match(sessionSidebar, /LIBRECHAT_SHELL_GEOMETRY/);
  assert.match(sessionSidebar, /--sidebar-rail-width:\s*\`\$\{LIBRECHAT_SHELL_GEOMETRY\.railWidthPx\}px\`/);
  assert.match(sessionSidebar, /keydown/);
  assert.match(sessionSidebar, /Escape/);
  assert.match(sessionSidebar, /data-librechat-mobile-sidebar/);
  assert.match(list, /LibreChatPanelSection/);
  assert.match(list, /data-librechat-expanded-panel/);
  assert.match(rail, /LibreChatRailButton/);
  assert.match(rail, /data-librechat-rail/);
  assert.match(surface, /expandedMinWidthPx:\s*360/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/web; pnpm exec tsx src/components/librechatShell/__tests__/libreChatShellLayout.test.ts`

Expected: FAIL because the helper components and markers do not exist.

- [ ] **Step 3: Write minimal implementation**

`LibreChatRail.tsx`:

```tsx
import type { ButtonHTMLAttributes, ReactNode } from "react";

interface LibreChatRailButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  itemKey?: string;
  active?: boolean;
  children: ReactNode;
}

export function LibreChatRailButton({ itemKey, active = false, children, className = "", ...props }: LibreChatRailButtonProps) {
  return (
    <button
      {...props}
      data-active={active ? "true" : "false"}
      data-workbench-rail-item={itemKey}
      className={`sidebar-rail-btn workbench-rail-btn flex h-11 w-11 items-center justify-center rounded-lg text-slate-200 transition-colors mx-1 touch-manipulation ${className}`}
    >
      {children}
    </button>
  );
}
```

`LibreChatPanel.tsx`:

```tsx
import type { ReactNode } from "react";

interface LibreChatPanelSectionProps {
  group: string;
  label: string;
  children: ReactNode;
}

export function LibreChatPanelSection({ group, label, children }: LibreChatPanelSectionProps) {
  return (
    <div data-workbench-nav-group={group} data-librechat-expanded-panel={group} className="space-y-1">
      <p className="px-[9px] pb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </p>
      {children}
    </div>
  );
}
```

In `SessionSidebar.tsx`, import `LIBRECHAT_SHELL_GEOMETRY`, set CSS variables on the desktop wrapper, use `data-librechat-mobile-sidebar`, and add Escape handling:

```tsx
useEffect(() => {
  if (!mobileOpen) return undefined;
  const handleEscape = (event: KeyboardEvent) => {
    if (event.key === "Escape") onMobileClose?.();
  };
  document.addEventListener("keydown", handleEscape);
  return () => document.removeEventListener("keydown", handleEscape);
}, [mobileOpen, onMobileClose]);
```

`style` on the desktop sidebar wrapper must include:

```tsx
{
  "--sidebar-rail-width": `${LIBRECHAT_SHELL_GEOMETRY.railWidthPx}px`,
  "--sidebar-width": `${LIBRECHAT_SHELL_GEOMETRY.expandedMinWidthPx}px`,
  width: isCollapsed ? "var(--sidebar-rail-width)" : "var(--sidebar-width)",
} as React.CSSProperties
```

Use `LibreChatPanelSection` in `SessionListContent.tsx` for task/governance groups and `LibreChatRailButton` in `SidebarRail.tsx` for first-level rail buttons.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend/web; pnpm exec tsx src/components/librechatShell/__tests__/libreChatShellLayout.test.ts src/__tests__/frontendShellParityAcceptance.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/web/src/components/librechatShell/LibreChatRail.tsx frontend/web/src/components/librechatShell/LibreChatPanel.tsx frontend/web/src/components/librechatShell/__tests__/libreChatShellLayout.test.ts frontend/web/src/components/panels/SessionSidebar.tsx frontend/web/src/components/panels/SidebarParts/SessionListContent.tsx frontend/web/src/components/panels/SidebarParts/SidebarRail.tsx frontend/web/src/styles/base.css frontend/web/src/__tests__/frontendShellParityAcceptance.test.ts
git commit -m "feat: transplant librechat sidebar shell"
```

---

### Task 4: Composer And Right Context Panel Transplant

**Files:**
- Create: `frontend/web/src/components/librechatShell/LibreChatSidePanel.tsx`
- Modify: `frontend/web/src/components/workbench/WorkbenchRightPanel.tsx`
- Modify: `frontend/web/src/components/chat/ChatInput.tsx`
- Modify: `frontend/web/src/styles/chat.css`
- Test: `frontend/web/src/components/librechatShell/__tests__/libreChatShellLayout.test.ts`
- Test: `frontend/web/src/components/chat/__tests__/composerCommandParity.test.ts`

**Interfaces:**
- Produces:
  - `LibreChatSidePanel({ sessionId, currentRunId, messageCount })`
  - Right panel tabs: `context`, `artifacts`, `run`, `permissions`.

- [ ] **Step 1: Write the failing test**

Append to `libreChatShellLayout.test.ts`:

```ts
test("composer and right panel expose LibreChat-style regions without backend authority imports", () => {
  const sidePanel = read("src/components/librechatShell/LibreChatSidePanel.tsx");
  const rightPanel = read("src/components/workbench/WorkbenchRightPanel.tsx");
  const chatInput = read("src/components/chat/ChatInput.tsx");
  const chatCss = read("src/styles/chat.css");

  assert.match(sidePanel, /data-librechat-side-panel/);
  assert.match(sidePanel, /data-librechat-side-tab="context"/);
  assert.match(sidePanel, /data-librechat-side-tab="artifacts"/);
  assert.match(sidePanel, /data-librechat-side-tab="run"/);
  assert.match(sidePanel, /data-librechat-side-tab="permissions"/);
  assert.match(rightPanel, /LibreChatSidePanel/);
  assert.match(chatInput, /data-librechat-composer="phase1"/);
  assert.match(chatInput, /data-librechat-composer-region="chips"/);
  assert.match(chatInput, /data-librechat-composer-region="textarea"/);
  assert.match(chatInput, /data-librechat-composer-region="toolbar"/);
  assert.match(chatCss, /\.librechat-composer-shell/);
  assert.doesNotMatch(sidePanel + rightPanel + chatInput, /librechat-data-provider|useRecoilState|~\/Providers|~\/store/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend/web; pnpm exec tsx src/components/librechatShell/__tests__/libreChatShellLayout.test.ts`

Expected: FAIL because `LibreChatSidePanel.tsx` and composer markers do not exist.

- [ ] **Step 3: Write minimal implementation**

`LibreChatSidePanel.tsx`:

```tsx
import { Activity, FileText, History, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { workbenchSurface } from "../workbench/workbenchSurface";

export interface LibreChatSidePanelProps {
  sessionId: string | null;
  currentRunId: string | null;
  messageCount: number;
}

const tabs = [
  { id: "context", icon: History, labelKey: "workbench.contextLabel" },
  { id: "artifacts", icon: FileText, labelKey: "workbench.artifacts" },
  { id: "run", icon: Activity, labelKey: "workbench.runState" },
  { id: "permissions", icon: ShieldCheck, labelKey: "workbench.permissions" },
] as const;

export function LibreChatSidePanel({ sessionId, currentRunId, messageCount }: LibreChatSidePanelProps) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<(typeof tabs)[number]["id"]>("context");
  return (
    <aside data-librechat-side-panel className="flex h-full min-h-0 flex-col gap-3 bg-[var(--theme-workbench-canvas)] p-3">
      <div className={`${workbenchSurface.secondaryPanel} p-2`}>
        <div className="grid grid-cols-4 gap-1" role="tablist" aria-label={t("workbench.runSurfaces", "Run surfaces")}>
          {tabs.map(({ id, icon: Icon, labelKey }) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={activeTab === id}
              data-librechat-side-tab={id}
              data-active={activeTab === id ? "true" : "false"}
              className="flex h-9 items-center justify-center rounded-md text-[var(--theme-text-secondary)] transition-colors data-[active=true]:bg-[var(--theme-bg-sidebar)] data-[active=true]:text-[var(--theme-text)]"
              onClick={() => setActiveTab(id)}
              title={t(labelKey)}
            >
              <Icon size={15} />
            </button>
          ))}
        </div>
      </div>
      <section className={`${workbenchSurface.secondaryPanel} flex min-h-0 flex-1 flex-col p-4`}>
        <p className={workbenchSurface.label}>{t("workbench.workspaceContext", "Workspace context")}</p>
        <dl className="mt-4 space-y-3">
          <div className="flex items-center justify-between gap-3 text-xs">
            <dt className={workbenchSurface.mutedText}>{t("workbench.session", "Session")}</dt>
            <dd className="max-w-36 truncate font-medium text-[var(--theme-text)]">{sessionId ?? t("workbench.unsaved", "Unsaved")}</dd>
          </div>
          <div className="flex items-center justify-between gap-3 text-xs">
            <dt className={workbenchSurface.mutedText}>{t("workbench.messages", "Messages")}</dt>
            <dd className="font-medium text-[var(--theme-text)]">{messageCount}</dd>
          </div>
          <div className="flex items-center justify-between gap-3 text-xs">
            <dt className={workbenchSurface.mutedText}>{t("workbench.runState", "Run state")}</dt>
            <dd className="max-w-36 truncate font-medium text-[var(--theme-text)]">{currentRunId ?? t("workbench.noRun", "No active run")}</dd>
          </div>
        </dl>
        <div className={`mt-auto ${workbenchSurface.unavailable}`}>
          {t("workbench.phase2Unavailable", "Artifacts, selected context, and run details stay read-only until your workspace enables them.")}
        </div>
      </section>
    </aside>
  );
}
```

`WorkbenchRightPanel.tsx` should delegate to `LibreChatSidePanel`.

Add the following markers in `ChatInput.tsx`:

```tsx
<div className="chat-input-shell librechat-composer-shell sm:px-4 pb-3" data-librechat-composer="phase1" ...>
...
<div data-librechat-composer-region="chips">...</div>
<div data-librechat-composer-region="textarea">...</div>
<div data-librechat-composer-region="toolbar">...</div>
```

`chat.css` should include:

```css
.librechat-composer-shell {
  background: var(--theme-workbench-canvas);
}

.librechat-composer-shell .chat-input-container {
  background: var(--theme-bg-card);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend/web; pnpm exec tsx src/components/librechatShell/__tests__/libreChatShellLayout.test.ts src/components/chat/__tests__/composerCommandParity.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/web/src/components/librechatShell/LibreChatSidePanel.tsx frontend/web/src/components/librechatShell/__tests__/libreChatShellLayout.test.ts frontend/web/src/components/workbench/WorkbenchRightPanel.tsx frontend/web/src/components/chat/ChatInput.tsx frontend/web/src/styles/chat.css frontend/web/src/components/chat/__tests__/composerCommandParity.test.ts
git commit -m "feat: transplant librechat composer and side panel"
```

---

### Task 5: Verification, Build, PR, And 211 Preview Evidence

**Files:**
- Modify: PR body only through GitHub, if a PR is opened.
- Do not commit evidence directories, screenshots, `dist`, or tarballs.

**Interfaces:**
- Consumes all previous tasks.
- Produces exact status evidence for `PR ready`; only claim `211 verified` after deployment/smoke against `http://10.56.0.211:18001/`.

- [ ] **Step 1: Run focused frontend tests**

Run:

```bash
cd frontend/web
pnpm exec tsx src/components/librechatShell/__tests__/libreChatShellSource.test.ts
pnpm exec tsx src/components/librechatShell/__tests__/libreChatShellLayout.test.ts
pnpm exec tsx src/components/workbench/__tests__/workbenchShellSource.test.ts
pnpm exec tsx src/components/workbench/__tests__/workbenchVisualClosure.test.ts
pnpm exec tsx src/components/chat/__tests__/composerCommandParity.test.ts
pnpm exec tsx src/__tests__/frontendShellParityAcceptance.test.ts
```

Expected: all PASS.

- [ ] **Step 2: Run repository checks**

Run:

```bash
python -m compileall -q app tools scripts
git diff --check
cd frontend/web
pnpm run ci:verify
```

Expected: exit 0 for all commands.

- [ ] **Step 3: Browser smoke locally**

Run the built frontend through a local preview or static server. Capture screenshots for `/chat`, `/skills`, `/marketplace`, `/mcp`, `/roles`, and `/apps`. If in-app browser is blocked by `missing field sandboxPolicy`, use Chrome/CDP fallback and record that fallback in local notes, not in committed source.

- [ ] **Step 4: Commit remaining verification docs only if intentionally authored**

No generated evidence, `dist`, screenshots, tarballs, or local scratch files are committed.

- [ ] **Step 5: Push and open or update PR**

Run:

```bash
git status --short
git push -u origin codex/librechat-shell-transplant-20260626
gh pr create --draft --title "Transplant LibreChat-style authenticated shell" --body-file <prepared-pr-body.md>
```

Expected: PR exists as draft unless all required local verification and browser smoke are present.

- [ ] **Step 6: Deploy to 211 only after build provenance is new**

Before upload, verify active 211 provenance differs from this branch head. Use the fixed remote frontend release upload directory, not the 211 home root. Deploy to the existing Python static service only if the artifact is new. Then smoke `/`, `/auth/login`, `/chat`, `/skills`, `/marketplace`, `/mcp`, `/roles`, `/apps`, and backend `/api/ai/health`.

Expected: only after this step may the work be described as `211 verified`.

---

## Self-Review

- Spec coverage: Tasks cover provenance, forbidden imports, shell owner, sidebar, composer, right panel, focused verification, PR, and 211 deployment boundary.
- Placeholder scan: no `TBD`, `TODO`, `implement later`, or unspecified test commands are present.
- Type consistency: `LibreChatShellProps`, `LIBRECHAT_SHELL_GEOMETRY`, `libreChatSurface`, `LibreChatRailButton`, `LibreChatPanelSection`, and `LibreChatSidePanel` are defined before later tasks consume them.
- Scope: This plan implements Phase 1 shell transplant only; it does not implement backend RBAC, department marketplace policy, MCP lifecycle, role approval, channel import backends, or memory retention changes.
