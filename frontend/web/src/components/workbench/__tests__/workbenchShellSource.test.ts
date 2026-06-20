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
