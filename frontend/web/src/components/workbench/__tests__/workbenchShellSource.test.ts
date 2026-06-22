import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

test("workbench shell exposes the required chat-first regions", () => {
  const shell = readFileSync(
    join(root, "src/components/workbench/WorkbenchShell.tsx"),
    "utf8",
  );

  assert.match(shell, /data-workbench-region="thread"/);
  assert.match(shell, /data-workbench-region="composer"/);
  assert.doesNotMatch(shell, /data-workbench-region="context"/);
  assert.doesNotMatch(shell, /rightPanel/);
});

test("workbench shell does not duplicate the primary sidebar rail", () => {
  const shell = readFileSync(
    join(root, "src/components/workbench/WorkbenchShell.tsx"),
    "utf8",
  );

  assert.doesNotMatch(shell, /data-workbench-region="rail"/);
  assert.doesNotMatch(shell, /railItems/);
  assert.doesNotMatch(shell, /workbenchSurface\.railButton/);
});

test("workbench shell keeps the chat canvas single-column on desktop", () => {
  const surface = readFileSync(
    join(root, "src/components/workbench/workbenchSurface.ts"),
    "utf8",
  );

  assert.match(surface, /grid-cols-1/);
  assert.doesNotMatch(surface, /xl:grid-cols-\[minmax\(0,1fr\)_20rem\]/);
  assert.doesNotMatch(surface, /xl:flex/);
  assert.doesNotMatch(surface, /2xl:grid-cols-\[minmax\(0,1fr\)_20rem\]/);
  assert.doesNotMatch(surface, /2xl:flex/);
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
  assert.doesNotMatch(chatView, /WorkbenchRightPanel/);
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

test("empty chat starts as a LibreChat-style chat-first surface", () => {
  const welcome = readFileSync(
    join(root, "src/components/chat/WelcomePage.tsx"),
    "utf8",
  );
  const surface = readFileSync(
    join(root, "src/components/workbench/workbenchSurface.ts"),
    "utf8",
  );

  assert.match(welcome, /welcome-chat-start/);
  assert.match(welcome, /data-chat-start-surface/);
  assert.doesNotMatch(welcome, /data-composer-selection-summary/);
  assert.doesNotMatch(welcome, /data-composer-command-dock/);
  assert.match(welcome, /data-workbench-empty-state="chat"/);
  assert.doesNotMatch(welcome, /welcome-workbench-cockpit/);
  assert.doesNotMatch(welcome, /WorkbenchQueueList/);
  assert.doesNotMatch(welcome, /workbenchSurface\.cockpit/);
  assert.doesNotMatch(welcome, /workbench\.selectionState/);
  assert.doesNotMatch(welcome, /font-serif/);
  assert.doesNotMatch(surface, /cockpit:/);
  assert.doesNotMatch(surface, /grid-cols-\[minmax\(220px,280px\)_minmax\(0,1fr\)\]/);
  assert.match(surface, /workbench-thread-frame/);
});
