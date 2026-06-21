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
  const tabContent = read("src/components/layout/AppContent/TabContent.tsx");

  assert.doesNotMatch(text, /hero-card|gradient-orb|nested-card/);
  assert.doesNotMatch(text, /rounded-3xl/);
  assert.match(text, /rounded-lg/);
  assert.doesNotMatch(tabContent, /max-w-4xl|sm:max-w-5xl|lg:max-w-6xl/);
  assert.match(tabContent, /data-authenticated-workbench-page/);
});

test("composer and command surfaces use stable dimensions", () => {
  const css = read("src/styles/chat.css");
  assert.match(css, /\.chat-input-container/);
  assert.match(css, /min-height:\s*44px/);
  assert.match(css, /max-height:\s*min\(52dvh,\s*420px\)/);
  assert.match(css, /\.composer-command-surface/);
  assert.match(css, /overflow:\s*hidden/);
});

test("empty chat keeps the command dock compact and composer-first", () => {
  const welcome = read("src/components/chat/WelcomePage.tsx");
  const welcomeLayout = read("src/components/chat/welcomeLayout.ts");

  assert.match(welcome, /data-composer-command-dock/);
  assert.match(welcome, /workbench\.commandDock/);
  assert.match(welcome, /workbench\.commandDockHint/);
  assert.doesNotMatch(welcome, /sm:grid-cols-3/);
  assert.doesNotMatch(welcome, /workbench\.slashSkillsHint/);
  assert.doesNotMatch(welcome, /workbench\.slashMcpHint/);
  assert.doesNotMatch(welcome, /workbench\.slashContextHint/);
  assert.doesNotMatch(welcome, /welcome-card-shimmer/);
  assert.doesNotMatch(welcome, /rounded-2xl/);
  assert.doesNotMatch(welcomeLayout, /rounded-2xl/);
  assert.match(welcomeLayout, /rounded-lg/);
});
