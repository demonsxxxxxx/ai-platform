import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { resolveSlashCommandMenu } from "../chatInputCommands";
import {
  getWorkbenchNavItemFromPathname,
  getWorkbenchNavPath,
} from "../../panels/SidebarParts/navigationState";

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

test("slash completion does not reserve dollar or unknown message prefixes", () => {
  assert.doesNotMatch(commandSource, /COMMAND_PREFIX_PANEL|parseComposerCommand/);
  assert.match(chatInputSource, /resolveComposerCommandDraft/);
  assert.match(chatInputSource, /setInput\(nextValue\)/);
  for (const text of ["$", "$100", "/unknown", "/workspace/report.xlsx"]) {
    assert.deepEqual(resolveSlashCommandMenu(text, { skills: true, tools: true }), []);
  }
});

test("explicit command choices reflect selector availability", () => {
  const items = resolveSlashCommandMenu("/", { skills: false, tools: true });
  assert.equal(items.find((item) => item.command === "skill")?.unavailable, true);
  assert.equal(items.find((item) => item.command === "mcp")?.unavailable, false);
});

test("composer exposes first-phase command, file, and Thinking affordances", () => {
  assert.match(toolbarSource, /chat\.commandTrigger/);
  assert.match(toolbarSource, /<AgentOptionButton/);
  assert.match(toolbarSource, /agentOptions\?\.enable_thinking/);
  assert.match(chatInputSource, /<ChatInputAttachments/);
  assert.match(chatInputSource, /data-disable-global-file-drop="true"/);
});

test("feature menu exposes file and enhancement capabilities without redundant settings", () => {
  assert.match(featureMenuSource, /featureMenu\.skillsMarketplace/);
  assert.match(featureMenuSource, /featureMenu\.mcpTools/);
  assert.match(toolbarSource, /<Paperclip/);
  assert.doesNotMatch(featureMenuSource, /featureMenu\.settings/);
  assert.doesNotMatch(featureMenuSource, /featureMenu\.model/);
  assert.doesNotMatch(featureMenuSource, /chat\.thinkingIntensity/);
  assert.doesNotMatch(featureMenuSource, /featureMenu\.context/);
});

test("composer renders durable selected context chips", () => {
  assert.match(chatInputSource, /<ComposerChips/);
  assert.match(chatInputSource, /composerSelectionReducer/);
  // Composer file attachments keep one presentation owner.
  assert.doesNotMatch(chatInputSource, /id:\s*`file:\$\{attachment\.id\}`/);
});

test("rail follows the authenticated workbench navigation manifest", () => {
  for (const [path, item] of [
    ["/apps", "apps"],
    ["/skills", "skills"],
    ["/mcp", "mcp"],
  ] as const) {
    assert.equal(getWorkbenchNavItemFromPathname(path), item);
    assert.equal(getWorkbenchNavPath(item), path);
  }

  assert.match(sidebarRailSource, /onOpenLaunchpad/);
  assert.match(sidebarRailSource, /onOpenSkills/);
  assert.doesNotMatch(sidebarRailSource, /onOpenMarketplace/);
  assert.match(sidebarRailSource, /onOpenMcp/);
  assert.match(sessionSidebarSource, /navigate\("\/apps"\)/);
  assert.match(sessionSidebarSource, /navigate\("\/skills"\)/);
  assert.doesNotMatch(sessionSidebarSource, /navigate\("\/marketplace"\)/);
  assert.match(sessionSidebarSource, /navigate\("\/mcp"\)/);
  assert.match(sessionSidebarSource, /getSafeWorkbenchNavPath\(item, user\)/);
  assert.doesNotMatch(sessionSidebarSource, /navigateWorkbenchItem\("files"\)/);
});

test("Chinese shell copy names the PRD surfaces directly", () => {
  assert.match(zhSource, /技能市场/);
  assert.match(zhSource, /MCP 工具/);
  assert.match(zhSource, /公司导航/);
  assert.match(zhSource, /会话分享/);
});
