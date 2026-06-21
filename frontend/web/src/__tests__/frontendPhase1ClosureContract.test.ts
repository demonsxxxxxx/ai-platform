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
