import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { resolveCommandPrefixPanel } from "../chatInputCommands";

const root = process.cwd();

const chatInputSource = readFileSync(
  join(root, "src/components/chat/ChatInput.tsx"),
  "utf8",
);
const commandSource = readFileSync(
  join(root, "src/components/chat/chatInputCommands.ts"),
  "utf8",
);
const toolbarSource = readFileSync(
  join(root, "src/components/chat/ChatInputToolbar.tsx"),
  "utf8",
);
const featureMenuSource = readFileSync(
  join(root, "src/components/selectors/FeatureMenu.tsx"),
  "utf8",
);
const sidebarRailSource = readFileSync(
  join(root, "src/components/panels/SidebarParts/SidebarRail.tsx"),
  "utf8",
);
const sessionSidebarSource = readFileSync(
  join(root, "src/components/panels/SessionSidebar.tsx"),
  "utf8",
);
const zhSource = readFileSync(join(root, "src/i18n/locales/zh.json"), "utf8");

test("slash and dollar command prefixes open Skills-first selectors", () => {
  assert.match(commandSource, /COMMAND_PREFIX_PANEL/);
  assert.match(commandSource, /"\/":\s*"skills"/);
  assert.match(commandSource, /"\$":\s*"skills"/);
  assert.match(chatInputSource, /resolveComposerCommandDraft/);
  assert.match(chatInputSource, /setCommandSearchSeed/);
  assert.match(chatInputSource, /setInput\(nextValue\)/);
});

test("command prefixes respect selector availability", () => {
  assert.equal(
    resolveCommandPrefixPanel("/", { skills: false, tools: true }),
    null,
  );
  assert.equal(
    resolveCommandPrefixPanel("$", { skills: true, tools: false }),
    "skills",
  );
  assert.equal(
    resolveCommandPrefixPanel("/", { skills: true, tools: false }),
    "skills",
  );
  assert.equal(
    resolveCommandPrefixPanel("$", { skills: false, tools: true }),
    null,
  );
  assert.equal(
    resolveCommandPrefixPanel("normal input", { skills: true, tools: true }),
    null,
  );
});

test("composer exposes first-phase command and file reference affordances", () => {
  assert.match(toolbarSource, /chat\.commandTrigger/);
  assert.match(chatInputSource, /chat\.fileReferenceChip/);
  assert.match(chatInputSource, /referenceId:\s*attachment\.id/);
});

test("feature menu names current ai-platform capabilities in PRD terms", () => {
  assert.match(featureMenuSource, /featureMenu\.skillsMarketplace/);
  assert.match(featureMenuSource, /featureMenu\.mcpTools/);
  assert.match(featureMenuSource, /featureMenu\.fileReference/);
  assert.match(featureMenuSource, /featureMenu\.model/);
  assert.match(featureMenuSource, /featureMenu\.context/);
});

test("composer renders durable selected context chips", () => {
  assert.match(chatInputSource, /<ComposerChips/);
  assert.match(chatInputSource, /composerSelectionReducer/);
  assert.match(chatInputSource, /referenceId:\s*attachment\.id/);
});

test("rail exposes company navigation admin Skills and MCP as first-level workbench entries", () => {
  assert.match(sidebarRailSource, /onOpenLaunchpad/);
  assert.match(sidebarRailSource, /onOpenSkills/);
  assert.doesNotMatch(sidebarRailSource, /onOpenMarketplace/);
  assert.match(sidebarRailSource, /onOpenMcp/);
  assert.match(sessionSidebarSource, /navigate\("\/apps"\)/);
  assert.match(sessionSidebarSource, /navigate\("\/skills"\)/);
  assert.doesNotMatch(sessionSidebarSource, /navigate\("\/marketplace"\)/);
  assert.match(sessionSidebarSource, /navigate\("\/mcp"\)/);
  assert.match(sessionSidebarSource, /navigateWorkbenchItem\("persona"\)/);
  assert.match(sessionSidebarSource, /navigateWorkbenchItem\("files"\)/);
});

test("Chinese shell copy names the PRD surfaces directly", () => {
  assert.match(zhSource, /技能市场/);
  assert.match(zhSource, /MCP 工具/);
  assert.match(zhSource, /公司导航/);
  assert.match(zhSource, /频道导入/);
  assert.match(zhSource, /会话分享/);
});
