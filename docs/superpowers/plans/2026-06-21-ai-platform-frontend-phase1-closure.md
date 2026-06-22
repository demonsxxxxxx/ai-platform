# AI Platform Frontend Phase 1 Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining Phase 1B/1C frontend experience gap after PR #167 so the official frontend reads as a LibreChat-style ai-platform workbench while still using only current ai-platform backend authority or explicit fail-closed states.

**Architecture:** Continue on `codex/frontend-shell-parity` / PR #167. Keep all source in `frontend/web`, add a small Phase 1 closure contract, deepen the existing workbench shell and composer surfaces, and align Skills, Marketplace, MCP, share, channel import, and launchpad pages to one enterprise workbench visual system. Do not add backend routes in this plan; backend-missing contracts stay visible as governed unavailable states.

**Tech Stack:** React 19, TypeScript, React Router 7, Tailwind utility classes, `lucide-react`, existing ai-platform service adapters, Node `tsx --test`, Vite, `pnpm run ci:verify`, existing 211 static frontend entry.

## Global Constraints

- Current PR #167 status is `211 verified` for commit `2a62b8b2f8326a7da2b8e0b7ad0dc9fadf2af54d`; new commits in this plan must earn fresh verification.
- Do not claim `gate closable`; backend Phase 2 contracts remain open.
- Do not import LibreChat backend APIs, Mongo data models, auth/session rules, ACL rules, storage contracts, or data providers.
- All active calls must use existing ai-platform service adapters and public/admin projections.
- Missing department Skill policy, MCP policy assignment, share ACL creation, channel import projections, user/role/department management, and full model admin contracts must render explicit unavailable states.
- The authenticated app must avoid LambChat visual identity and should read as a dense, restrained enterprise AI workbench.
- `/` opens command flows; `$` opens or filters directly to Skills.
- `/skill`, `/mcp`, `/agent`, `/model`, `/file`, and `/context` must be visible in the composer command system.
- Selected Skills, MCP tools, agents, models, files, and context must render as stable chips or fail-closed chips before send.
- File chips must bind to safe upload handles, artifact ids, or frontend attachment ids; never raw runtime paths, storage keys, sandbox paths, executor-private payloads, or local absolute paths.
- `/apps` remains a click-through company launchpad and must not absorb nonGMPlims business modules.
- Use `lucide-react` icons; no emoji as structural icons.
- Keep cards at radius `8px` or less unless an existing component already requires a larger modal radius.
- Do not add decorative hero sections, nested cards, gradient orbs, or oversized marketing typography inside the authenticated app.
- Every task must include at least one focused source/component test and a deny/unavailable path when the surface touches governance.
- Before PR readiness, run focused frontend tests, `pnpm run ci:verify`, `python -m compileall -q app tools scripts`, and `git diff --check`.

---

## File Structure

- Create `frontend/web/src/components/workbench/phase1ClosureContract.ts`: machine-readable list of required Phase 1 routes, shell regions, composer commands, fail-closed surfaces, and screenshot names.
- Create `frontend/web/src/__tests__/frontendPhase1ClosureContract.test.ts`: source-level guard that the active browser graph exposes the contract.
- Modify `frontend/web/src/components/workbench/workbenchSurface.ts`: central workbench density, panel, shell, command menu, and unavailable-state class names.
- Modify `frontend/web/src/components/workbench/WorkbenchShell.tsx`: make thread/composer/right-panel layout feel like a single chat shell instead of a card grid.
- Modify `frontend/web/src/components/workbench/WorkbenchRightPanel.tsx`: align context/artifact/run surfaces to compact drawer patterns.
- Modify `frontend/web/src/components/chat/WelcomePage.tsx`: reduce empty-state card weight and keep Skills/MCP/context affordances close to the composer.
- Modify `frontend/web/src/styles/chat.css`: tighten composer, command menu, and chip styling with stable dimensions and non-overlap rules.
- Create `frontend/web/src/components/chat/ComposerModelPanel.tsx`: composer-anchored model selector backed by existing public model projections.
- Create `frontend/web/src/components/chat/ComposerUnavailablePanel.tsx`: consistent fail-closed panel for context and backend-missing command targets.
- Modify `frontend/web/src/components/chat/chatInputTypes.ts`: pass current model selection and available models into `ChatInput`.
- Modify `frontend/web/src/components/chat/ChatInput.tsx`: wire `/model` to the new panel, keep `/context` fail-closed, and render model/context chips.
- Modify `frontend/web/src/components/chat/ChatInputSelectors.tsx`: render model and unavailable panels.
- Modify `frontend/web/src/components/layout/AppContent/ChatAppContent.tsx`: pass `availableModels`, `currentModelId`, and `handleSelectModel` into chat input props through `ChatView`.
- Modify `frontend/web/src/components/layout/AppContent/ChatView.tsx`: thread those props to `ChatInput`.
- Create `frontend/web/src/components/chat/__tests__/composerPhase1Closure.test.ts`: behavior/source tests for model selection, context fail-closed state, and chips.
- Create `frontend/web/src/components/governance/GroupAvailabilityToggleRow.tsx`: read-only and backed toggle row for department/group availability display.
- Modify `frontend/web/src/components/governance/groupAvailability.ts`: expose toggle state mapping.
- Modify `frontend/web/src/components/panels/SkillsHubPanel.tsx`: show department/group availability using the shared toggle row.
- Modify `frontend/web/src/components/panels/MarketplacePanel.tsx`: expose compact marketplace filter and availability shell in embedded and full modes.
- Modify `frontend/web/src/components/panels/MCPPanel.tsx`: show governed selection and lifecycle unavailable states in the same workbench style.
- Create `frontend/web/src/components/panels/__tests__/governancePhase1Closure.test.ts`: tests for group toggle unavailable states and ordinary-user safe MCP/marketplace surfaces.
- Create `frontend/web/src/components/workbench/WorkbenchUnavailableState.tsx`: shared denied, disabled, unavailable, and admin-only panel.
- Modify `frontend/web/src/components/share/ShareUnavailableState.tsx`: consume `WorkbenchUnavailableState`.
- Modify `frontend/web/src/components/channels/ChannelImportPanel.tsx`: consume `WorkbenchUnavailableState` and show retention/redaction summary.
- Modify `frontend/web/src/components/launchpad/LaunchpadPanel.tsx`: align launchpad to workbench shell and keep click-through boundary prominent.
- Create `frontend/web/src/components/workbench/__tests__/workbenchVisualClosure.test.ts`: source tests for shell regions, compact surfaces, and forbidden visual patterns.
- Modify `docs/release-evidence/frontend-shell-parity/smoke-summary.json`: replace after fresh 211 browser smoke only if screenshot evidence is intentionally committed.
- Add or update PR comment evidence only after verification; do not commit credentials, real `.env` values, or local screenshot paths that expose private machine details.

---

### Task 1: Phase 1 Closure Contract

**Files:**
- Create: `frontend/web/src/components/workbench/phase1ClosureContract.ts`
- Create: `frontend/web/src/__tests__/frontendPhase1ClosureContract.test.ts`

**Interfaces:**
- Consumes: current routes in `frontend/web/src/App.tsx`, surface mapping in `frontend/web/src/components/layout/AppContent/TabContent.tsx`, and workbench components created by PR #167.
- Produces: `PHASE1_CLOSURE_ROUTES`, `PHASE1_COMPOSER_COMMANDS`, `PHASE1_CLOSURE_SCREENSHOTS`, and `PHASE1_FAIL_CLOSED_SURFACES` used by source tests and browser smoke notes.

- [ ] **Step 1: Write the contract file**

Create `frontend/web/src/components/workbench/phase1ClosureContract.ts`:

```ts
export const PHASE1_CLOSURE_ROUTES = [
  "/apps",
  "/chat",
  "/skills",
  "/marketplace",
  "/mcp",
  "/channels",
  "/shared/:shareId",
] as const;

export const PHASE1_COMPOSER_COMMANDS = [
  "/skill",
  "$",
  "/mcp",
  "/agent",
  "/model",
  "/file",
  "/context",
] as const;

export const PHASE1_FAIL_CLOSED_SURFACES = [
  "department-skill-policy",
  "mcp-lifecycle",
  "share-acl-create",
  "channel-import-projection",
  "context-selector",
] as const;

export const PHASE1_CLOSURE_SCREENSHOTS = [
  "login.png",
  "apps.png",
  "chat-empty.png",
  "chat-slash-menu.png",
  "chat-dollar-skills.png",
  "chat-selected-skill-chip.png",
  "chat-model-selector.png",
  "chat-file-chip.png",
  "skills.png",
  "marketplace.png",
  "mcp.png",
  "channels.png",
  "shared-denied.png",
  "ordinary-admin-denied.png",
  "admin-governance.png",
] as const;

export const PHASE1_FORBIDDEN_VISUAL_MARKERS = [
  "LambChat",
  "lambchat.com",
  "gradient-orb",
  "hero-card",
  "nested-card",
] as const;
```

- [ ] **Step 2: Write the failing contract test**

Create `frontend/web/src/__tests__/frontendPhase1ClosureContract.test.ts`:

```ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import {
  PHASE1_CLOSURE_ROUTES,
  PHASE1_COMPOSER_COMMANDS,
  PHASE1_FAIL_CLOSED_SURFACES,
  PHASE1_FORBIDDEN_VISUAL_MARKERS,
} from "../components/workbench/phase1ClosureContract";

const root = process.cwd();

function source(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("phase one closure routes are registered in the active app graph", () => {
  const app = source("src/App.tsx");
  const tabs = source("src/components/layout/AppContent/TabContent.tsx");

  for (const route of PHASE1_CLOSURE_ROUTES) {
    if (route === "/shared/:shareId") {
      assert.match(app, /path="\/shared\/:shareId"/);
      continue;
    }
    assert.match(app, new RegExp(`path="${route.replace("/", "\\/")}`));
  }

  assert.match(tabs, /apps:\s*LaunchpadPanel/);
  assert.match(tabs, /skills:\s*SkillsHubPanel/);
  assert.match(tabs, /marketplace:\s*SkillsHubPanel/);
  assert.match(tabs, /mcp:\s*MCPPanel/);
  assert.match(tabs, /channels:\s*ChannelImportPanel/);
});

test("phase one composer command names are active source concepts", () => {
  const commands = source("src/components/chat/chatInputCommands.ts");
  const input = source("src/components/chat/ChatInput.tsx");

  for (const command of PHASE1_COMPOSER_COMMANDS) {
    if (command === "$") {
      assert.match(commands, /trigger === "\$"/);
      continue;
    }
    assert.match(commands, new RegExp(command.slice(1)));
  }

  assert.match(input, /ComposerChips/);
});

test("backend-missing phase one surfaces are explicit fail-closed states", () => {
  const serialized = [
    source("src/components/panels/SkillsHubPanel.tsx"),
    source("src/components/panels/MCPPanel.tsx"),
    source("src/components/channels/ChannelImportPanel.tsx"),
    source("src/components/share/ShareUnavailableState.tsx"),
    source("src/components/chat/ComposerUnavailablePanel.tsx"),
  ].join("\n");

  for (const surface of PHASE1_FAIL_CLOSED_SURFACES) {
    assert.match(serialized, new RegExp(surface));
  }
});

test("active phase one source avoids forbidden visual and brand markers", () => {
  const active = [
    "index.html",
    "src/components/workbench/WorkbenchShell.tsx",
    "src/components/workbench/workbenchSurface.ts",
    "src/components/chat/WelcomePage.tsx",
    "src/components/chat/ChatInput.tsx",
    "src/components/panels/SkillsHubPanel.tsx",
    "src/components/panels/MCPPanel.tsx",
    "src/components/launchpad/LaunchpadPanel.tsx",
  ];

  const offenders: string[] = [];
  for (const file of active) {
    const text = source(file);
    for (const marker of PHASE1_FORBIDDEN_VISUAL_MARKERS) {
      if (text.includes(marker)) offenders.push(`${file}:${marker}`);
    }
  }

  assert.deepEqual(offenders, []);
});
```

- [ ] **Step 3: Run the test to confirm the current gap**

Run:

```powershell
Push-Location frontend/web
pnpm exec tsx --test src/__tests__/frontendPhase1ClosureContract.test.ts
Pop-Location
```

Expected: FAIL because `ComposerUnavailablePanel.tsx` does not exist and backend-missing surface markers are not yet centralized.

- [ ] **Step 4: Do not commit this task alone**

Keep Task 1 local until Tasks 2-5 make the contract pass. This avoids committing a permanently failing test.

---

### Task 2: Workbench Visual Density And Shell Closure

**Files:**
- Modify: `frontend/web/src/components/workbench/workbenchSurface.ts`
- Modify: `frontend/web/src/components/workbench/WorkbenchShell.tsx`
- Modify: `frontend/web/src/components/workbench/WorkbenchRightPanel.tsx`
- Modify: `frontend/web/src/components/chat/WelcomePage.tsx`
- Modify: `frontend/web/src/styles/chat.css`
- Create: `frontend/web/src/components/workbench/__tests__/workbenchVisualClosure.test.ts`

**Interfaces:**
- Consumes: `WorkbenchShell({ children, composer, rightPanel })`, `WorkbenchRightPanel({ sessionId, currentRunId, messageCount })`.
- Produces: compact workbench class names, `data-workbench-region` markers, and drawer/card styles used by chat, Skills, MCP, share, channel, and launchpad surfaces.

- [ ] **Step 1: Write the visual closure test**

Create `frontend/web/src/components/workbench/__tests__/workbenchVisualClosure.test.ts`:

```ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("workbench shell exposes dense chat regions", () => {
  const shell = read("src/components/workbench/WorkbenchShell.tsx");
  const surface = read("src/components/workbench/workbenchSurface.ts");

  assert.match(shell, /data-workbench-region="thread"/);
  assert.match(shell, /data-workbench-region="composer"/);
  assert.match(shell, /data-workbench-region="context"/);
  assert.match(surface, /workspace:/);
  assert.match(surface, /thread:/);
  assert.match(surface, /composer:/);
  assert.match(surface, /context:/);
  assert.match(surface, /commandSurface:/);
  assert.match(surface, /unavailable:/);
});

test("authenticated workbench source avoids marketing and nested-card patterns", () => {
  const text = [
    read("src/components/workbench/WorkbenchShell.tsx"),
    read("src/components/workbench/WorkbenchRightPanel.tsx"),
    read("src/components/chat/WelcomePage.tsx"),
    read("src/styles/chat.css"),
  ].join("\n");

  assert.doesNotMatch(text, /hero-card|gradient-orb|nested-card/);
  assert.doesNotMatch(text, /rounded-3xl/);
  assert.match(text, /rounded-lg/);
});

test("composer and command surfaces use stable dimensions", () => {
  const css = read("src/styles/chat.css");
  assert.match(css, /\.chat-input-container/);
  assert.match(css, /min-height:\s*44px/);
  assert.match(css, /max-height:\s*min\(52dvh,\s*420px\)/);
  assert.match(css, /\.composer-command-surface/);
  assert.match(css, /overflow:\s*hidden/);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
Push-Location frontend/web
pnpm exec tsx --test src/components/workbench/__tests__/workbenchVisualClosure.test.ts
Pop-Location
```

Expected: FAIL because `commandSurface`, `unavailable`, CSS stable max-height, and `rounded-3xl` removal are not complete.

- [ ] **Step 3: Update workbench surface tokens**

Replace `frontend/web/src/components/workbench/workbenchSurface.ts` with:

```ts
import { clsx } from "clsx";

export const workbenchSurface = {
  root: clsx(
    "flex min-h-0 flex-1 bg-slate-50 text-slate-950",
    "dark:bg-stone-950 dark:text-stone-100",
  ),
  workspace: clsx(
    "grid min-h-0 w-full flex-1 grid-cols-1",
    "xl:grid-cols-[minmax(0,1fr)_20rem]",
  ),
  thread: clsx(
    "workbench-thread-frame flex min-w-0 flex-1 flex-col",
    "border-r border-slate-200/80 bg-white",
    "dark:border-stone-800 dark:bg-stone-950",
  ),
  threadBody: "flex min-h-0 flex-1 flex-col px-3 pb-2 sm:px-4",
  composer: clsx(
    "shrink-0 border-t border-slate-200/80 bg-white/98 px-3 py-2.5",
    "dark:border-stone-800 dark:bg-stone-950",
  ),
  context: clsx(
    "hidden min-h-0 w-80 shrink-0 flex-col bg-slate-50",
    "dark:bg-stone-950 xl:flex",
  ),
  panel: clsx(
    "rounded-lg border border-slate-200 bg-white",
    "shadow-[0_4px_12px_rgba(18,38,63,0.03)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  compactPanel: clsx(
    "rounded-lg border border-slate-200 bg-white",
    "shadow-[0_4px_12px_rgba(18,38,63,0.03)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  commandSurface: clsx(
    "rounded-lg border border-slate-200 bg-white",
    "shadow-[0_18px_40px_rgba(15,23,42,0.12)]",
    "dark:border-stone-800 dark:bg-stone-900",
  ),
  unavailable: clsx(
    "rounded-lg border border-dashed border-slate-300 bg-slate-50 p-4",
    "text-sm leading-6 text-slate-600",
    "dark:border-stone-700 dark:bg-stone-950 dark:text-stone-300",
  ),
  statusTile: clsx(
    "rounded-md bg-slate-50 p-3",
    "dark:bg-stone-950/70",
  ),
  mutedText: "text-slate-500 dark:text-stone-400",
  label:
    "text-[11px] font-semibold uppercase text-slate-400 dark:text-stone-500",
};
```

- [ ] **Step 4: Update `WorkbenchShell.tsx` layout**

Change the shell root to avoid centered card layout:

```tsx
export function WorkbenchShell({
  children,
  composer,
  rightPanel,
}: WorkbenchShellProps) {
  return (
    <section className={workbenchSurface.root} data-phase1-closure-shell>
      <div className={workbenchSurface.workspace}>
        <div className={workbenchSurface.thread}>
          <div
            data-workbench-region="thread"
            className={workbenchSurface.threadBody}
          >
            {children}
          </div>
          {composer && (
            <div
              data-workbench-region="composer"
              className={workbenchSurface.composer}
            >
              {composer}
            </div>
          )}
        </div>
        <div
          data-workbench-region="context"
          className={workbenchSurface.context}
        >
          {rightPanel}
        </div>
      </div>
    </section>
  );
}
```

- [ ] **Step 5: Tighten right drawer cards**

In `WorkbenchRightPanel.tsx`, keep the current data but replace nested card blocks with `workbenchSurface.statusTile`:

```tsx
<section className={`${workbenchSurface.panel} flex min-h-0 flex-1 flex-col p-3`}>
  <p className={workbenchSurface.label}>
    {t("workbench.runSurfaces", "Run surfaces")}
  </p>
  <div className="mt-3 space-y-2">
    {statusItems.map((item) => {
      const Icon = item.icon;
      return (
        <div key={item.label} className={workbenchSurface.statusTile}>
          <div className="flex items-center gap-2">
            <Icon size={15} className="text-slate-500 dark:text-stone-400" />
            <span className="text-xs font-medium text-slate-800 dark:text-stone-100">
              {item.label}
            </span>
          </div>
          <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-stone-400">
            {item.value}
          </p>
        </div>
      );
    })}
  </div>
  <div className={`mt-auto ${workbenchSurface.unavailable}`}>
    {t(
      "workbench.phase2Unavailable",
      "Artifact playback, selected context, and provenance drawer stay read-only until backed by ai-platform projections.",
    )}
  </div>
</section>
```

- [ ] **Step 6: Tighten composer CSS**

In `frontend/web/src/styles/chat.css`, add or replace these rules:

```css
.chat-input-container {
  border-radius: 8px;
  min-height: 44px;
  max-height: min(52dvh, 420px);
  overflow: hidden;
}

.chat-input-container:focus-within {
  border-color: var(--theme-ring);
  box-shadow:
    0 0 0 3px color-mix(in srgb, var(--theme-ring) 12%, transparent),
    0 8px 22px rgba(15, 23, 42, 0.08);
}

.composer-command-surface {
  max-height: min(48dvh, 360px);
  overflow: hidden;
  border-radius: 8px;
}

.composer-command-list {
  max-height: min(42dvh, 312px);
  overflow-y: auto;
}
```

Then change `ChatInput.tsx` container classes from `rounded-3xl` to `rounded-lg`.

- [ ] **Step 7: Re-run visual closure test**

Run:

```powershell
Push-Location frontend/web
pnpm exec tsx --test src/components/workbench/__tests__/workbenchVisualClosure.test.ts
Pop-Location
```

Expected: PASS.

---

### Task 3: Composer Model Selector And Context Fail-Closed Flow

**Files:**
- Create: `frontend/web/src/components/chat/ComposerModelPanel.tsx`
- Create: `frontend/web/src/components/chat/ComposerUnavailablePanel.tsx`
- Modify: `frontend/web/src/components/chat/chatInputTypes.ts`
- Modify: `frontend/web/src/components/chat/ChatInput.tsx`
- Modify: `frontend/web/src/components/chat/ChatInputSelectors.tsx`
- Modify: `frontend/web/src/components/layout/AppContent/ChatAppContent.tsx`
- Modify: `frontend/web/src/components/layout/AppContent/ChatView.tsx`
- Create: `frontend/web/src/components/chat/__tests__/composerPhase1Closure.test.ts`

**Interfaces:**
- Consumes: `ModelOption` from `frontend/web/src/services/api/modelPublic.ts`, `availableModels/currentModelId/onSelectModel` from `ChatAppContent`, existing `ComposerSelection` reducer.
- Produces: composer-backed `/model` selection, model chips, and `/context` unavailable panel with stable fail-closed behavior.

- [ ] **Step 1: Write composer closure test**

Create `frontend/web/src/components/chat/__tests__/composerPhase1Closure.test.ts`:

```ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("composer has a backed model panel and a fail-closed context panel", () => {
  const selectors = read("src/components/chat/ChatInputSelectors.tsx");
  const modelPanel = read("src/components/chat/ComposerModelPanel.tsx");
  const unavailable = read("src/components/chat/ComposerUnavailablePanel.tsx");

  assert.match(selectors, /ComposerModelPanel/);
  assert.match(selectors, /ComposerUnavailablePanel/);
  assert.match(modelPanel, /ModelOption/);
  assert.match(modelPanel, /onSelectModel/);
  assert.match(unavailable, /data-fail-closed-surface/);
  assert.match(unavailable, /context-selector/);
});

test("chat input receives model authority from ai-platform projections", () => {
  const types = read("src/components/chat/chatInputTypes.ts");
  const chatApp = read("src/components/layout/AppContent/ChatAppContent.tsx");
  const chatView = read("src/components/layout/AppContent/ChatView.tsx");

  assert.match(types, /availableModels\?:\s*ModelOption\[\]/);
  assert.match(types, /currentModelId\?:\s*string/);
  assert.match(types, /onSelectModel\?:/);
  assert.match(chatApp, /availableModels=\{filteredModels ?? \[\]\}/);
  assert.match(chatView, /availableModels/);
});

test("selected model and fail-closed context render durable composer chips", () => {
  const input = read("src/components/chat/ChatInput.tsx");

  assert.match(input, /id:\s*`model:\$\{currentModelId\}`/);
  assert.match(input, /kind:\s*"model"/);
  assert.match(input, /context-selector/);
  assert.match(input, /kind:\s*"context"/);
  assert.match(input, /state:\s*"unavailable"/);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
Push-Location frontend/web
pnpm exec tsx --test src/components/chat/__tests__/composerPhase1Closure.test.ts
Pop-Location
```

Expected: FAIL because the two new panels and model props do not exist.

- [ ] **Step 3: Create model panel**

Create `frontend/web/src/components/chat/ComposerModelPanel.tsx`:

```tsx
import { Check, Search, Settings2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { workbenchSurface } from "../workbench/workbenchSurface";
import type { ModelOption } from "../../services/api/modelPublic";

export interface ComposerModelPanelProps {
  models: ModelOption[];
  currentModelId: string;
  searchSeed?: string;
  onSelectModel: (modelId: string, modelValue: string) => void;
  onClose: () => void;
}

export function ComposerModelPanel({
  models,
  currentModelId,
  searchSeed = "",
  onSelectModel,
  onClose,
}: ComposerModelPanelProps) {
  const { t } = useTranslation();
  const [query, setQuery] = useState(searchSeed);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return models;
    return models.filter((model) =>
      [model.label, model.value, model.provider ?? ""]
        .join(" ")
        .toLowerCase()
        .includes(needle),
    );
  }, [models, query]);

  return (
    <div className={`${workbenchSurface.commandSurface} composer-command-surface mx-auto mt-2 w-full max-w-3xl p-2`}>
      <div className="flex items-center gap-2 border-b border-slate-100 px-2 pb-2 dark:border-stone-800">
        <Search size={16} className="text-slate-400" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="h-9 min-w-0 flex-1 bg-transparent text-sm outline-none"
          placeholder={t("composerCommand.model.search", "Search allowed models")}
          autoFocus
        />
      </div>
      <div className="composer-command-list mt-2 space-y-1">
        {filtered.length === 0 ? (
          <div className={workbenchSurface.unavailable}>
            {t("composerCommand.model.empty", "No allowed model matches this search.")}
          </div>
        ) : (
          filtered.map((model) => {
            const selected = model.id === currentModelId;
            return (
              <button
                key={model.id}
                type="button"
                onClick={() => {
                  onSelectModel(model.id, model.value);
                  onClose();
                }}
                className="flex w-full items-start gap-3 rounded-md px-3 py-2 text-left text-sm hover:bg-slate-50 dark:hover:bg-stone-800"
                aria-pressed={selected}
              >
                <Settings2 size={16} className="mt-0.5 shrink-0 text-slate-500" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-slate-900 dark:text-stone-100">
                    {model.label}
                  </span>
                  <span className="block truncate text-xs text-slate-500 dark:text-stone-400">
                    {model.provider ?? "ai-platform"} · {model.value}
                  </span>
                </span>
                {selected && <Check size={16} className="mt-0.5 text-slate-500" />}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create unavailable panel**

Create `frontend/web/src/components/chat/ComposerUnavailablePanel.tsx`:

```tsx
import { ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { workbenchSurface } from "../workbench/workbenchSurface";

export interface ComposerUnavailablePanelProps {
  surface: "context-selector" | "department-skill-policy" | "mcp-lifecycle";
  onClose: () => void;
}

export function ComposerUnavailablePanel({
  surface,
  onClose,
}: ComposerUnavailablePanelProps) {
  const { t } = useTranslation();
  return (
    <div
      data-fail-closed-surface={surface}
      className={`${workbenchSurface.commandSurface} mx-auto mt-2 w-full max-w-3xl p-3`}
    >
      <div className="flex items-start gap-3">
        <ShieldAlert size={18} className="mt-0.5 shrink-0 text-amber-600" />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-slate-900 dark:text-stone-100">
            {t(`composerCommand.unavailable.${surface}.title`)}
          </h3>
          <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-stone-400">
            {t(`composerCommand.unavailable.${surface}.description`)}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 dark:text-stone-400 dark:hover:bg-stone-800"
        >
          {t("common.close")}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Extend chat input types**

In `frontend/web/src/components/chat/chatInputTypes.ts`, import `ModelOption` and add props:

```ts
import type { ModelOption } from "../../services/api/modelPublic";

export interface ChatInputProps {
  availableModels?: ModelOption[];
  currentModelId?: string;
  onSelectModel?: (modelId: string, modelValue: string) => void;
}
```

Merge these fields into the existing `ChatInputProps` interface instead of creating a second interface.

- [ ] **Step 6: Pass model props from chat app to chat input**

In `ChatViewProps`, add:

```ts
availableModels: ModelOption[];
currentModelId: string;
onSelectModel: (modelId: string, modelValue: string) => void;
```

Import `ModelOption` from `../../../services/api/modelPublic`.

In `ChatAppContent.tsx`, pass:

```tsx
availableModels={filteredModels ?? []}
currentModelId={currentModelId}
onSelectModel={handleSelectModel}
```

In the `chatInputProps` object in `ChatView.tsx`, add:

```ts
availableModels,
currentModelId,
onSelectModel,
```

- [ ] **Step 7: Render model and context panels**

In `ChatInputSelectors.tsx`, import the new panels and render:

```tsx
{activePanel === "model" && onSelectModel && (
  <ComposerModelPanel
    models={availableModels}
    currentModelId={currentModelId || ""}
    searchSeed={
      commandSearchSeed?.panel === "model"
        ? commandSearchSeed.query
        : undefined
    }
    onSelectModel={onSelectModel}
    onClose={() => onActivePanelChange(null)}
  />
)}
{activePanel === "context" && (
  <ComposerUnavailablePanel
    surface="context-selector"
    onClose={() => onActivePanelChange(null)}
  />
)}
```

Add corresponding props to `ChatInputSelectorsProps`.

- [ ] **Step 8: Update command availability and chips**

In `ChatInput.tsx`, set:

```ts
models: !!availableModels?.length && !!onSelectModel,
context: true,
```

Add a model chip effect:

```ts
useEffect(() => {
  dispatchComposerSelection({ type: "clear-kind", kind: "model" });
  const selected = availableModels?.find((model) => model.id === currentModelId);
  if (!selected || !currentModelId) return;
  dispatchComposerSelection({
    type: "upsert",
    selection: {
      id: `model:${currentModelId}`,
      kind: "model",
      label: selected.label,
      state: "enabled",
      source: selected.provider ?? "ai-platform",
      description: selected.value,
      referenceId: currentModelId,
    },
  });
}, [availableModels, currentModelId]);
```

When `/context` is selected, open the unavailable panel and insert a fail-closed chip:

```ts
dispatchComposerSelection({
  type: "upsert",
  selection: {
    id: "context:phase1-unavailable",
    kind: "context",
    label: t("composerCommand.context.label"),
    state: "unavailable",
    source: "context-selector",
    description: t("composerCommand.unavailable.context-selector.description"),
  },
});
```

- [ ] **Step 9: Add translations**

In `frontend/web/src/i18n/locales/en.json`, add:

```json
{
  "composerCommand": {
    "model": {
      "empty": "No allowed model matches this search.",
      "search": "Search allowed models"
    },
    "unavailable": {
      "context-selector": {
        "description": "Context selection needs ai-platform context projections. No private workspace or executor paths are exposed from this composer.",
        "title": "Context selection is not backed yet"
      },
      "department-skill-policy": {
        "description": "Department Skill policy is visible as a governed state until backend policy APIs are available.",
        "title": "Department availability is read-only"
      },
      "mcp-lifecycle": {
        "description": "MCP server lifecycle and credentials require admin-backed policy APIs.",
        "title": "MCP lifecycle is unavailable"
      }
    }
  }
}
```

In `frontend/web/src/i18n/locales/zh.json`, add:

```json
{
  "composerCommand": {
    "model": {
      "empty": "没有匹配的可用模型。",
      "search": "搜索可用模型"
    },
    "unavailable": {
      "context-selector": {
        "description": "上下文选择需要 ai-platform 的上下文投影。输入框不会暴露私有工作区或执行器路径。",
        "title": "上下文选择暂未接入后端"
      },
      "department-skill-policy": {
        "description": "部门 Skill 策略先以受治理状态展示，等待后端策略 API 接入。",
        "title": "部门可用性当前只读"
      },
      "mcp-lifecycle": {
        "description": "MCP 服务生命周期和凭据管理需要管理员策略 API 支撑。",
        "title": "MCP 生命周期暂不可用"
      }
    }
  }
}
```

- [ ] **Step 10: Run composer closure test**

Run:

```powershell
Push-Location frontend/web
pnpm exec tsx --test src/components/chat/__tests__/composerPhase1Closure.test.ts src/components/chat/__tests__/composerCommandParity.test.ts
Pop-Location
```

Expected: PASS.

---

### Task 4: Skills, Marketplace, And MCP Governance Surface Closure

**Files:**
- Create: `frontend/web/src/components/governance/GroupAvailabilityToggleRow.tsx`
- Modify: `frontend/web/src/components/governance/groupAvailability.ts`
- Modify: `frontend/web/src/components/panels/SkillsHubPanel.tsx`
- Modify: `frontend/web/src/components/panels/MarketplacePanel.tsx`
- Modify: `frontend/web/src/components/panels/MCPPanel.tsx`
- Create: `frontend/web/src/components/panels/__tests__/governancePhase1Closure.test.ts`

**Interfaces:**
- Consumes: `resolveGroupAvailability`, current Skills and Marketplace hooks, current MCP hook.
- Produces: consistent read-only department/group toggle UI and MCP lifecycle unavailable state, both marked as fail-closed Phase 1 surfaces.

- [ ] **Step 1: Write governance closure test**

Create `frontend/web/src/components/panels/__tests__/governancePhase1Closure.test.ts`:

```ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("skills and marketplace expose department group toggle UI as governed state", () => {
  const row = read("src/components/governance/GroupAvailabilityToggleRow.tsx");
  const skillsHub = read("src/components/panels/SkillsHubPanel.tsx");
  const marketplace = read("src/components/panels/MarketplacePanel.tsx");

  assert.match(row, /data-group-toggle-ui/);
  assert.match(row, /department-skill-policy/);
  assert.match(row, /enabled/);
  assert.match(row, /disabled/);
  assert.match(row, /inherited/);
  assert.match(row, /unavailable/);
  assert.match(skillsHub, /GroupAvailabilityToggleRow/);
  assert.match(marketplace, /GroupAvailabilityToggleRow/);
});

test("mcp page exposes governed selection without ordinary lifecycle writes", () => {
  const mcp = read("src/components/panels/MCPPanel.tsx");

  assert.match(mcp, /mcp-lifecycle/);
  assert.match(mcp, /lifecycleAvailability/);
  assert.match(mcp, /data-phase1c-surface="mcp"/);
  assert.doesNotMatch(mcp, /deleteServer\(|createServer\(|updateCredentials\(/);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
Push-Location frontend/web
pnpm exec tsx --test src/components/panels/__tests__/governancePhase1Closure.test.ts
Pop-Location
```

Expected: FAIL because `GroupAvailabilityToggleRow.tsx` does not exist and Marketplace does not use it.

- [ ] **Step 3: Create group toggle row**

Create `frontend/web/src/components/governance/GroupAvailabilityToggleRow.tsx`:

```tsx
import { Building2, Lock } from "lucide-react";
import { useTranslation } from "react-i18next";
import { GovernanceAvailabilityBadge } from "./GovernanceAvailabilityBadge";
import { resolveGroupAvailability } from "./groupAvailability";

export type GroupAvailabilityToggleState =
  | "enabled"
  | "disabled"
  | "inherited"
  | "unavailable";

export interface GroupAvailabilityToggleRowProps {
  label: string;
  description: string;
  state: GroupAvailabilityToggleState;
  backed: boolean;
}

export function GroupAvailabilityToggleRow({
  label,
  description,
  state,
  backed,
}: GroupAvailabilityToggleRowProps) {
  const { t } = useTranslation();
  const availability = resolveGroupAvailability({
    backed,
    enabled: state === "enabled",
    inherited: state === "inherited",
  });
  const disabled = !backed || state === "unavailable";

  return (
    <div
      data-group-toggle-ui
      data-fail-closed-surface="department-skill-policy"
      className="flex items-start justify-between gap-3 rounded-lg border border-slate-200 bg-white p-3 dark:border-stone-800 dark:bg-stone-900"
    >
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Building2 size={16} className="text-slate-500" />
          <h3 className="text-sm font-semibold text-slate-900 dark:text-stone-100">
            {label}
          </h3>
        </div>
        <p className="mt-1 text-xs leading-5 text-slate-500 dark:text-stone-400">
          {description}
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <GovernanceAvailabilityBadge
          state={availability.state}
          labelKey={availability.labelKey}
        />
        <button
          type="button"
          disabled={disabled}
          aria-disabled={disabled}
          className="inline-flex h-8 min-w-16 items-center justify-center gap-1 rounded-md border border-slate-200 px-2 text-xs text-slate-500 disabled:cursor-not-allowed disabled:opacity-50 dark:border-stone-700 dark:text-stone-400"
          title={
            backed
              ? t("governance.toggleBacked")
              : t("skills.marketplace.groupToggleUnavailable")
          }
        >
          {!backed && <Lock size={12} />}
          {t(`governance.${state}`)}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Add governance labels**

In `frontend/web/src/i18n/locales/en.json`, add:

```json
{
  "governance": {
    "disabled": "Disabled",
    "enabled": "Enabled",
    "inherited": "Inherited",
    "toggleBacked": "Backed by ai-platform policy"
  }
}
```

In `frontend/web/src/i18n/locales/zh.json`, add:

```json
{
  "governance": {
    "disabled": "已禁用",
    "enabled": "已启用",
    "inherited": "继承",
    "toggleBacked": "由 ai-platform 策略支撑"
  }
}
```

- [ ] **Step 5: Wire SkillsHub**

In `SkillsHubPanel.tsx`, import and render:

```tsx
import { GroupAvailabilityToggleRow } from "../governance/GroupAvailabilityToggleRow";
```

Inside the existing availability section, replace the second department block with:

```tsx
<GroupAvailabilityToggleRow
  label={t("skills.marketplace.departmentAvailability")}
  description={t("skills.marketplace.groupToggleUnavailable")}
  state="unavailable"
  backed={false}
/>
```

- [ ] **Step 6: Wire Marketplace**

In `MarketplacePanel.tsx`, import and render the same row above the skills list:

```tsx
<div className="px-4 pt-3">
  <GroupAvailabilityToggleRow
    label={t("skills.marketplace.departmentAvailability")}
    description={t("skills.marketplace.groupToggleUnavailable")}
    state="unavailable"
    backed={false}
  />
</div>
```

Place it after error rendering and before `skill-content-area`.

- [ ] **Step 7: Mark MCP lifecycle fail-closed**

In `MCPPanel.tsx`, add the marker to the lifecycle section:

```tsx
<div
  data-fail-closed-surface="mcp-lifecycle"
  className="flex items-start justify-between gap-3 rounded-md bg-slate-50 p-3 dark:bg-stone-950/40"
>
```

Keep lifecycle create/update/delete controls absent from ordinary-user source.

- [ ] **Step 8: Run governance closure test**

Run:

```powershell
Push-Location frontend/web
pnpm exec tsx --test src/components/panels/__tests__/governancePhase1Closure.test.ts src/components/panels/SkillsHubPanel/__tests__/state.test.ts
Pop-Location
```

Expected: PASS.

---

### Task 5: Share, Channel Import, And Launchpad Visual Alignment

**Files:**
- Create: `frontend/web/src/components/workbench/WorkbenchUnavailableState.tsx`
- Modify: `frontend/web/src/components/share/ShareUnavailableState.tsx`
- Modify: `frontend/web/src/components/channels/ChannelImportPanel.tsx`
- Modify: `frontend/web/src/components/launchpad/LaunchpadPanel.tsx`
- Modify: `frontend/web/src/components/share/__tests__/shareChannelFailClosedSource.test.ts`

**Interfaces:**
- Consumes: existing share/channel fail-closed pages and launchpad catalog.
- Produces: one shared unavailable-state component for denied/unavailable surfaces and launchpad boundary copy inside the workbench visual language.

- [ ] **Step 1: Extend fail-closed test**

Update `frontend/web/src/components/share/__tests__/shareChannelFailClosedSource.test.ts`:

```ts
test("share channel and launchpad use shared workbench unavailable language", () => {
  const unavailable = readFileSync(
    join(root, "src/components/workbench/WorkbenchUnavailableState.tsx"),
    "utf8",
  );
  const share = readFileSync(join(root, "src/components/share/ShareUnavailableState.tsx"), "utf8");
  const channel = readFileSync(join(root, "src/components/channels/ChannelImportPanel.tsx"), "utf8");
  const launchpad = readFileSync(join(root, "src/components/launchpad/LaunchpadPanel.tsx"), "utf8");

  assert.match(unavailable, /data-workbench-unavailable/);
  assert.match(share, /WorkbenchUnavailableState/);
  assert.match(channel, /WorkbenchUnavailableState/);
  assert.match(channel, /channel-import-projection/);
  assert.match(launchpad, /launchpad\.boundary/);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
Push-Location frontend/web
pnpm exec tsx --test src/components/share/__tests__/shareChannelFailClosedSource.test.ts
Pop-Location
```

Expected: FAIL because `WorkbenchUnavailableState.tsx` is not present.

- [ ] **Step 3: Create shared unavailable component**

Create `frontend/web/src/components/workbench/WorkbenchUnavailableState.tsx`:

```tsx
import type { ElementType } from "react";
import { ShieldAlert } from "lucide-react";
import { workbenchSurface } from "./workbenchSurface";

export interface WorkbenchUnavailableStateProps {
  title: string;
  description: string;
  icon?: ElementType;
  surface: string;
}

export function WorkbenchUnavailableState({
  title,
  description,
  icon: Icon = ShieldAlert,
  surface,
}: WorkbenchUnavailableStateProps) {
  return (
    <section
      data-workbench-unavailable
      data-fail-closed-surface={surface}
      className={`${workbenchSurface.compactPanel} mx-auto w-full max-w-xl p-5 text-center`}
    >
      <Icon className="mx-auto text-slate-500 dark:text-stone-300" size={32} />
      <h1 className="mt-4 text-base font-semibold text-slate-900 dark:text-stone-100">
        {title}
      </h1>
      <p className="mt-2 text-sm leading-6 text-slate-600 dark:text-stone-300">
        {description}
      </p>
    </section>
  );
}
```

- [ ] **Step 4: Refactor share unavailable state**

In `ShareUnavailableState.tsx`, keep `ShareUnavailableReason` and icon mapping, but render:

```tsx
return (
  <main className="flex min-h-dvh items-center justify-center bg-slate-50 p-6 dark:bg-stone-950">
    <WorkbenchUnavailableState
      surface={`share-${reason}`}
      icon={Icon}
      title={t(`share.unavailable.${reason}.title`)}
      description={t(`share.unavailable.${reason}.description`)}
    />
  </main>
);
```

- [ ] **Step 5: Refactor channel import unavailable state**

In `ChannelImportPanel.tsx`, replace the unavailable section with:

```tsx
<WorkbenchUnavailableState
  surface="channel-import-projection"
  title={t("channelImport.unavailable.title")}
  description={t("channelImport.unavailable.description")}
/>
```

Keep visible `channelImport.redaction` and `channelImport.retention` labels in the source for future backed rows:

```tsx
const importMetadataLabels = {
  redaction: t("channelImport.redaction"),
  retention: t("channelImport.retention"),
};
void importMetadataLabels;
```

- [ ] **Step 6: Tighten launchpad boundary**

In `LaunchpadPanel.tsx`, ensure the boundary copy is near the top of the panel:

```tsx
<p className="max-w-3xl text-xs leading-5 text-slate-500 dark:text-stone-400">
  {t(
    "launchpad.boundary",
    "These entries open existing company systems in a new tab. AI Platform does not replace their login, permissions, workflow, or audit rules.",
  )}
</p>
```

Use `workbenchSurface.compactPanel` for repeated app entries and keep card radius at `rounded-lg`.

- [ ] **Step 7: Re-run fail-closed test**

Run:

```powershell
Push-Location frontend/web
pnpm exec tsx --test src/components/share/__tests__/shareChannelFailClosedSource.test.ts
Pop-Location
```

Expected: PASS.

---

### Task 6: Acceptance Verification And PR Readiness

**Files:**
- Modify: `docs/superpowers/plans/2026-06-21-ai-platform-frontend-phase1-closure.md` only if verification notes need to be appended after execution.
- Do not commit unreviewed local screenshot evidence unless the PR intentionally records screenshot artifacts.

**Interfaces:**
- Consumes: outputs from Tasks 1-5.
- Produces: verified PR #167 update with status `PR ready` only after local gates pass; status `211 verified` only after 211 deploy and browser smoke pass for the final commit.

- [ ] **Step 1: Run focused source tests**

Run:

```powershell
Push-Location frontend/web
pnpm exec tsx --test `
  src/__tests__/frontendPhase1ClosureContract.test.ts `
  src/__tests__/frontendShellParityAcceptance.test.ts `
  src/components/workbench/__tests__/workbenchShellSource.test.ts `
  src/components/workbench/__tests__/workbenchVisualClosure.test.ts `
  src/components/chat/__tests__/composerCommandParity.test.ts `
  src/components/chat/__tests__/composerPhase1Closure.test.ts `
  src/components/chat/__tests__/frontendExperiencePhase1.test.ts `
  src/components/panels/__tests__/governancePhase1Closure.test.ts `
  src/components/share/__tests__/shareChannelFailClosedSource.test.ts `
  src/__tests__/launchpadRoute.test.ts `
  src/components/launchpad/__tests__/catalog.test.ts `
  src/components/launchpad/__tests__/launchpadSource.test.ts
Pop-Location
```

Expected: PASS. If any test fails, fix the implementation before continuing.

- [ ] **Step 2: Run frontend CI verification**

Run:

```powershell
Push-Location frontend/web
pnpm run ci:verify
Pop-Location
```

Expected: exit 0. Existing Vite chunk-size warnings are acceptable; ESLint, TypeScript, projection audit, Vite build, and provenance writing must complete.

- [ ] **Step 3: Run backend compile check**

Run:

```powershell
python -m compileall -q app tools scripts
```

Expected: exit 0.

- [ ] **Step 4: Run diff hygiene**

Run:

```powershell
git diff --check
git status --short --branch
```

Expected: `git diff --check` exits 0. `git status` shows only intended tracked changes plus any intentionally retained untracked local evidence.

- [ ] **Step 5: Stage intended files**

Run:

```powershell
git add `
  docs/superpowers/plans/2026-06-21-ai-platform-frontend-phase1-closure.md `
  frontend/web/src/components/workbench/phase1ClosureContract.ts `
  frontend/web/src/__tests__/frontendPhase1ClosureContract.test.ts `
  frontend/web/src/components/workbench/workbenchSurface.ts `
  frontend/web/src/components/workbench/WorkbenchShell.tsx `
  frontend/web/src/components/workbench/WorkbenchRightPanel.tsx `
  frontend/web/src/components/workbench/WorkbenchUnavailableState.tsx `
  frontend/web/src/components/workbench/__tests__/workbenchVisualClosure.test.ts `
  frontend/web/src/components/chat/WelcomePage.tsx `
  frontend/web/src/styles/chat.css `
  frontend/web/src/components/chat/ComposerModelPanel.tsx `
  frontend/web/src/components/chat/ComposerUnavailablePanel.tsx `
  frontend/web/src/components/chat/chatInputTypes.ts `
  frontend/web/src/components/chat/ChatInput.tsx `
  frontend/web/src/components/chat/ChatInputSelectors.tsx `
  frontend/web/src/components/chat/__tests__/composerPhase1Closure.test.ts `
  frontend/web/src/components/layout/AppContent/ChatAppContent.tsx `
  frontend/web/src/components/layout/AppContent/ChatView.tsx `
  frontend/web/src/components/governance/GroupAvailabilityToggleRow.tsx `
  frontend/web/src/components/governance/groupAvailability.ts `
  frontend/web/src/components/panels/SkillsHubPanel.tsx `
  frontend/web/src/components/panels/MarketplacePanel.tsx `
  frontend/web/src/components/panels/MCPPanel.tsx `
  frontend/web/src/components/panels/__tests__/governancePhase1Closure.test.ts `
  frontend/web/src/components/share/ShareUnavailableState.tsx `
  frontend/web/src/components/channels/ChannelImportPanel.tsx `
  frontend/web/src/components/launchpad/LaunchpadPanel.tsx `
  frontend/web/src/components/share/__tests__/shareChannelFailClosedSource.test.ts `
  frontend/web/src/i18n/locales/en.json `
  frontend/web/src/i18n/locales/zh.json
```

Expected: only intended files are staged. Do not stage `.superpowers/`, local credentials, or unreviewed screenshot files.

- [ ] **Step 6: Commit**

Run:

```powershell
git commit -m "feat: close frontend phase one experience"
```

Expected: commit succeeds.

- [ ] **Step 7: Push PR branch**

Run:

```powershell
git push origin codex/frontend-shell-parity
```

Expected: push succeeds and PR #167 updates.

- [ ] **Step 8: Check PR state**

Run:

```powershell
gh pr view 167 --repo demonsxxxxxx/ai-platform --json number,state,isDraft,mergeStateStatus,statusCheckRollup,headRefOid,reviewDecision,url
```

Expected: PR remains open. Do not call `PR ready` if checks are pending, review requires changes, or the branch is dirty.

- [ ] **Step 9: Deploy final frontend to 211 only after local gates pass**

Use the existing 211 static frontend flow. Build a clean dist:

```powershell
Push-Location frontend/web
pnpm install --frozen-lockfile
pnpm run build
Get-Content dist/ai-platform-build-provenance.json
Pop-Location
```

Expected: provenance commit equals the final branch commit and `dirty` is `false`.

Package the dist:

```powershell
tar -czf .pytest-tmp/ai-platform-frontend-phase1-closure-dist.tar.gz -C frontend/web/dist .
```

Upload to `s211` and replace `/home/xinlin.jiang/frontend-pr111-smoke/dist` using the established backup/staging flow:

```bash
ROOT=/home/xinlin.jiang/frontend-pr111-smoke
ARCHIVE=/home/xinlin.jiang/ai-platform-frontend-phase1-closure-dist.tar.gz
STAMP=$(date +%Y%m%d-%H%M%S)
STAGING="$ROOT/dist-staging-phase1-closure-$STAMP"
BACKUP="$ROOT/dist-backup-before-phase1-closure-$STAMP"
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

Expected: `provenance_ok <final-commit>` and a backup directory exists.

- [ ] **Step 10: Restart 18001 static frontend**

Run on `s211`:

```bash
ROOT=/home/xinlin.jiang/frontend-pr111-smoke
PIDS=$(ps -ef | awk '/serve_ai_platform_frontend.py/ && /--port 18001/ && !/awk/ {print $2}')
if [ -n "$PIDS" ]; then kill $PIDS; sleep 1; fi
nohup python3 "$ROOT/tools/serve_ai_platform_frontend.py" --host 0.0.0.0 --port 18001 --root "$ROOT/dist" --api-base http://127.0.0.1:8020 > "$ROOT/frontend-18001.log" 2>&1 &
sleep 2
ps -ef | grep 'serve_ai_platform_frontend.py' | grep -v grep
```

Expected: exactly one `--port 18001` process is running.

- [ ] **Step 11: Run 211 HTTP smoke**

Run on `s211`:

```bash
curl -fsS -o /tmp/ai-platform-root.html -w 'root_http=%{http_code}\n' http://127.0.0.1:18001/
curl -fsS -o /tmp/ai-platform-login.html -w 'login_http=%{http_code}\n' http://127.0.0.1:18001/auth/login
curl -fsS http://127.0.0.1:8020/api/ai/health
grep -E 'AI Platform - Enterprise AI Workbench|assets/index-' /tmp/ai-platform-root.html
! grep -E 'LambChat|lambchat\.com' /tmp/ai-platform-root.html
cat /home/xinlin.jiang/frontend-pr111-smoke/dist/ai-platform-build-provenance.json
```

Expected: `root_http=200`, `login_http=200`, backend health contains `"status":"ok"`, AI Platform title/assets are present, no LambChat root HTML marker exists, and provenance shows the final commit with `dirty=false`.

- [ ] **Step 12: Run browser smoke and screenshot evidence**

Use the in-app browser against `http://10.56.0.211:18001`. Capture screenshots named by `PHASE1_CLOSURE_SCREENSHOTS` for:

```text
/auth/login
/apps
/chat empty state
/chat with "/" menu
/chat with "$" Skills shortcut
/chat with selected Skill chip or unavailable Skill chip
/chat with /model selector
/chat with file chip normal or denied state
/skills
/marketplace
/mcp
/channels
/shared/<denied-or-unavailable-share>
ordinary-user denied admin route
admin governance route
```

Expected: screenshots show a coherent ai-platform workbench shell, no LambChat brand, no overlapping controls, no fake department/MCP/share/channel success when backend contracts are missing.

- [ ] **Step 13: Record PR evidence**

Post a PR #167 comment with:

```text
Phase 1 closure evidence:
- focused frontend tests: PASS
- frontend ci:verify: PASS
- python compileall: PASS
- git diff --check: PASS
- 211 HTTP smoke: PASS
- 211 browser smoke: PASS
- provenance commit: <final-commit>, dirty=false
- status: 211 verified for this frontend increment
- not gate closable: backend Phase 2 marketplace/MCP/share/channel contracts remain open
```

Do not include account passwords, real `.env` values, or private machine temp paths.

---

## Self-Review

**Spec coverage:** This plan covers the Phase 1B/1C requirements from `2026-06-19-ai-platform-chat-experience-parity-prd.md`: LibreChat-style shell density, slash and dollar composer commands, model/file/context chips, Skills and Marketplace department availability UI, MCP governed visibility, share and channel fail-closed states, company launchpad boundary, screenshot acceptance, projection/build verification, and 211 smoke.

**Known backend gaps:** True department/group Skill marketplace policy, MCP lifecycle and credential governance, share ACL creation, channel import backend projections, user/role/department administration, and full model administration remain backend-backed expansion work. This plan makes those gaps visible and fail-closed in Phase 1.

**Placeholder scan:** The plan contains no `TBD`, `TODO`, `implement later`, or unspecified error-handling steps. Backend-missing surfaces are concrete fail-closed UI states with data markers and tests.

**Type consistency:** `ModelOption` flows from `ChatAppContent` to `ChatView` to `ChatInput` to `ComposerModelPanel`. `ComposerSelection.kind` already supports `model` and `context` in PR #167. `GroupAvailabilityToggleState` is defined before `GroupAvailabilityToggleRow` consumers use it. `WorkbenchUnavailableState` is defined before share and channel surfaces consume it.
