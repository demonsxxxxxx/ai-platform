# AI Platform Company Launchpad Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an AI Platform native company launchpad page that lists existing internal apps and links users to the existing nonGMPlims, intranet, and external systems.

**Architecture:** Keep AI Platform and nonGMPlims separate. Add a typed static launchpad catalog plus pure destination/search helpers, then render it in a new React panel wired to `/apps`. The page only opens existing destinations and does not port old Vue business modules.

**Tech Stack:** React 19, TypeScript, Vite, Tailwind, React Router, lucide-react, node:test with tsx for targeted tests.

## Global Constraints

- AI Platform is the homepage/entry station; nonGMPlims remains a separate business system.
- Phase 1 links to existing destinations; it does not rebuild nonGMPlims permissions, todo widgets, calendar, workflow, dashboard, or statistics.
- Route: use `/apps` for the launchpad page.
- Unknown system mappings must render a visible unavailable state.
- Do not commit secrets, `.env` values, or local-only credentials.
- Follow existing AI Platform React/Tailwind patterns.

---

### Task 1: Catalog And Destination Helpers

**Files:**
- Create: `frontend/web/src/components/launchpad/catalog.ts`
- Create: `frontend/web/src/components/launchpad/__tests__/catalog.test.ts`

**Interfaces:**
- Produces: `launchpadTabs`, `launchpadGroups`, `filterLaunchpadGroups(groups, query)`, `resolveLaunchpadDestination(entry)`
- Consumes: no application runtime state.

- [ ] **Step 1: Write the failing test**

```ts
import test from "node:test";
import assert from "node:assert/strict";
import {
  filterLaunchpadGroups,
  launchpadGroups,
  resolveLaunchpadDestination,
} from "../catalog.ts";

test("launchpad catalog contains the three copied navigation areas", () => {
  const tabCounts = new Map<string, number>();
  for (const group of launchpadGroups) {
    tabCounts.set(
      group.tab,
      (tabCounts.get(group.tab) ?? 0) + group.entries.length,
    );
  }

  assert.equal(tabCounts.get("lingxi"), 29);
  assert.equal(tabCounts.get("common"), 122);
  assert.equal(tabCounts.get("ai"), 4);
});

test("search filters by app name, description, and group name", () => {
  const result = filterLaunchpadGroups(launchpadGroups, "SOP");

  assert.ok(result.some((group) => group.name === "知识库"));
  assert.ok(result.flatMap((group) => group.entries).some((entry) => entry.name === "SOP问询助手"));
});

test("destination resolver opens urls and maps known nonGMPlims systems", () => {
  const wordTranslate = launchpadGroups
    .flatMap((group) => group.entries)
    .find((entry) => entry.name === "Word文档翻译");
  assert.equal(resolveLaunchpadDestination(wordTranslate!)?.kind, "url");

  const sampleSender = launchpadGroups
    .flatMap((group) => group.entries)
    .find((entry) => entry.systemKey === "SampleSender");
  assert.deepEqual(resolveLaunchpadDestination(sampleSender!), {
    kind: "url",
    href: "http://10.56.0.211:8080/#/RDSampleSender/dashboard/overview",
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pnpm exec tsx --test src/components/launchpad/__tests__/catalog.test.ts`
Expected: FAIL because `../catalog.ts` does not exist.

- [ ] **Step 3: Implement catalog and helpers**

Create a typed catalog copied from `nonGMPlimsUI/webUI` navigation config. Store entries by group. Add `resolveLaunchpadDestination` with known URL entries and `systemKey` mappings to the legacy nonGMPlims launchpad.

- [ ] **Step 4: Run the catalog test**

Run: `pnpm exec tsx --test src/components/launchpad/__tests__/catalog.test.ts`
Expected: PASS.

### Task 2: Launchpad React Panel

**Files:**
- Create: `frontend/web/src/components/launchpad/LaunchpadPanel.tsx`
- Create: `frontend/web/src/components/launchpad/index.ts`
- Create: `frontend/web/src/components/launchpad/__tests__/launchpadSource.test.ts`

**Interfaces:**
- Consumes: Task 1 catalog helpers.
- Produces: `LaunchpadPanel`.

- [ ] **Step 1: Write the failing source test**

```ts
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const panelSource = readFileSync(
  join(import.meta.dirname, "../LaunchpadPanel.tsx"),
  "utf8",
);

test("launchpad panel opens destinations in a new tab", () => {
  assert.match(panelSource, /window\.open\(destination\.href,\s*"_blank"/);
  assert.match(panelSource, /rel="noreferrer"/);
});

test("launchpad panel has tabs, search, and unavailable rendering", () => {
  assert.match(panelSource, /launchpadTabs/);
  assert.match(panelSource, /filterLaunchpadGroups/);
  assert.match(panelSource, /待接入/);
});
```

- [ ] **Step 2: Run the source test to verify it fails**

Run: `pnpm exec tsx --test src/components/launchpad/__tests__/launchpadSource.test.ts`
Expected: FAIL because `LaunchpadPanel.tsx` does not exist.

- [ ] **Step 3: Implement the panel**

Render the page with tabs, search input, group navigation, grouped cards, stable card dimensions, and visible disabled state for unknown destination mappings.

- [ ] **Step 4: Run the panel source test**

Run: `pnpm exec tsx --test src/components/launchpad/__tests__/launchpadSource.test.ts`
Expected: PASS.

### Task 3: Route, Sidebar, And i18n Wiring

**Files:**
- Modify: `frontend/web/src/App.tsx`
- Modify: `frontend/web/src/components/layout/AppContent/types.ts`
- Modify: `frontend/web/src/components/layout/AppContent/TabContent.tsx`
- Modify: `frontend/web/src/components/panels/SessionSidebar.tsx`
- Modify: `frontend/web/src/i18n/locales/zh.json`
- Modify: `frontend/web/src/i18n/locales/en.json`
- Create: `frontend/web/src/__tests__/launchpadRoute.test.ts`

**Interfaces:**
- Consumes: `LaunchpadPanel` from Task 2.
- Produces: `/apps` protected route and sidebar entry.

- [ ] **Step 1: Write the failing route/source test**

```ts
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

const appSource = readFileSync(resolve(import.meta.dirname, "../App.tsx"), "utf8");
const typesSource = readFileSync(
  resolve(import.meta.dirname, "../components/layout/AppContent/types.ts"),
  "utf8",
);
const tabSource = readFileSync(
  resolve(import.meta.dirname, "../components/layout/AppContent/TabContent.tsx"),
  "utf8",
);
const sidebarSource = readFileSync(
  resolve(import.meta.dirname, "../components/panels/SessionSidebar.tsx"),
  "utf8",
);

test("launchpad route is protected and mapped to AppContent", () => {
  assert.match(appSource, /path="\/apps"/);
  assert.match(appSource, /<LaunchpadPage \/>/);
  assert.match(appSource, /activeTab="apps"/);
});

test("launchpad tab is registered in layout and sidebar", () => {
  assert.match(typesSource, /\|\s*"apps"/);
  assert.match(tabSource, /apps:\s*LaunchpadPanel/);
  assert.match(sidebarSource, /path:\s*"\/apps"/);
  assert.match(sidebarSource, /nav\.apps/);
});
```

- [ ] **Step 2: Run the route/source test to verify it fails**

Run: `pnpm exec tsx --test src/__tests__/launchpadRoute.test.ts`
Expected: FAIL because `/apps` is not wired.

- [ ] **Step 3: Wire the route and nav**

Add `apps` tab type, lazy panel mapping, `LaunchpadPage` route wrapper, `/apps` route, sidebar feature item, and Chinese/English labels.

- [ ] **Step 4: Run the route/source test**

Run: `pnpm exec tsx --test src/__tests__/launchpadRoute.test.ts`
Expected: PASS.

### Task 4: Verification

**Files:**
- No new files.

**Interfaces:**
- Consumes all previous tasks.
- Produces verification evidence.

- [ ] **Step 1: Run targeted tests**

Run:
```bash
pnpm exec tsx --test src/components/launchpad/__tests__/catalog.test.ts src/components/launchpad/__tests__/launchpadSource.test.ts src/__tests__/launchpadRoute.test.ts
```
Expected: all tests pass.

- [ ] **Step 2: Run TypeScript/build verification**

Run: `pnpm run build`
Expected: exit 0.

- [ ] **Step 3: Check whitespace**

Run from repo root: `git diff --check`
Expected: no whitespace errors.

### Task 5: Deployable Delivery Handoff

**Files:**
- No new files.

**Interfaces:**
- Consumes all previous tasks.
- Produces operator-facing delivery evidence and a safe 211 deployment path.

- [x] **Step 1: Local verification evidence**

Completed local verification for the first delivery PR:

```bash
cd frontend/web
pnpm exec tsx --test src\components\launchpad\__tests__\catalog.test.ts src\components\launchpad\__tests__\launchpadSource.test.ts src\__tests__\launchpadRoute.test.ts
pnpm run build
cd ../..
git diff --check
```

Observed evidence:

- Launchpad targeted tests: 11 tests passed.
- Frontend production build: exit 0; only existing Vite large chunk warnings.
- Diff whitespace check: exit 0; only CRLF conversion warnings.
- Browser smoke on `http://127.0.0.1:5174/apps`:
  - desktop rendered `公司导航` with 29 Lingxi cards.
  - search `SOP` returned one `SOP问询助手` card and `知识库1` navigation.
  - no-result search rendered `没有找到匹配的入口` without stale group links.
  - 390px viewport rendered 29 cards, mobile category strip, and no horizontal overflow.

- [x] **Step 2: Delivery boundary**

The first delivery is PR-ready source and build output, not a live 211 rollout.
Do not label this work `211 verified` until the merged frontend has been copied
to the real 211 static frontend root and smoke-tested on the official entry.

The page is intentionally an AI Platform homepage entry. It links to existing
systems and does not migrate nonGMPlims Vue business modules, target-system
permissions, todo widgets, dashboards, workflow pages, or statistics into AI
Platform.

- [x] **Step 3: 211 deployment procedure after merge**

Use the real 211 runtime target. Previous evidence showed port `18001` is served
by a Python static frontend process rather than an Open WebUI container. Before
touching the host, verify the current listener and process again.

Recommended operator sequence:

```bash
# on 211
ss -ltnp | grep ':18001'
ps -ef | grep -E 'serve_ai_platform_frontend|18001' | grep -v grep

cd /home/xinlin.jiang/ai-platform-phaseb/services/ai-platform
git fetch --all --prune
git checkout main
git pull --ff-only

cd frontend/web
pnpm install --frozen-lockfile
pnpm run build
```

Then replace the current static frontend only after taking a timestamped backup
of the existing dist root used by the live `18001` process. Keep the backup on
the host until smoke passes.

If the live process still uses the known Python static server pattern, restart
that process with the same API base as the existing command. Do not assume a
Docker container owns the frontend port.

- [x] **Step 4: 211 smoke checklist**

After the static frontend is replaced and the service is restarted, verify:

- `http://10.56.0.211:18001/auth/login` renders.
- Company login redirects to `/apps`.
- `http://10.56.0.211:18001/apps` renders `公司导航`.
- Lingxi tab shows 29 entries.
- Search `SOP` returns `SOP问询助手`.
- Empty search result has no stale category anchors.
- A known systemKey entry, for example `SampleSender`, opens a legacy
  nonGMPlims URL under `http://10.56.0.211:8080/#/RDSampleSender/dashboard/overview`
  unless `VITE_LEGACY_NONGMP_URL` has been configured differently.
- `/chat` still opens the existing chat page.
- Backend health is still reachable through the frontend environment's API base.

Only after these checks pass on `http://10.56.0.211:18001/` should the work be
reported as `211 verified`.
