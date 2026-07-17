import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("composer and feature menu expose no unavailable context action", () => {
  const commands = read("src/components/chat/chatInputCommands.ts");
  const input = read("src/components/chat/ChatInput.tsx");
  const selectors = read("src/components/chat/ChatInputSelectors.tsx");
  const featureMenu = read("src/components/selectors/FeatureMenu.tsx");

  for (const source of [commands, input, selectors, featureMenu]) {
    assert.doesNotMatch(source, /context-selector|markContextUnavailableCommand/);
  }
  assert.doesNotMatch(commands, /labelKey:\s*"composerCommand\.context/);
  assert.doesNotMatch(featureMenu, /featureMenu\.context/);
});

test("chat actions omit unconsumed share and fork controls while preserving feedback gating", () => {
  const header = read("src/components/layout/AppContent/Header.tsx");
  const message = read("src/components/chat/ChatMessage/index.tsx");
  const userBubble = read("src/components/chat/ChatMessage/UserMessageBubble.tsx");

  assert.doesNotMatch(header, /ShareDialog|Share2|shareDialogOpen/);
  assert.doesNotMatch(message, /ShareButton|GitBranch|onForkMessage\(message\.id\)/);
  assert.doesNotMatch(userBubble, /onFork|GitBranch|chat\.message\.fork/);
  assert.match(message, /showFeedbackAndShareActions &&/);
});

test("session configuration and run-used provenance keep separate labels", () => {
  const en = JSON.parse(read("src/i18n/locales/en.json"));
  const zh = JSON.parse(read("src/i18n/locales/zh.json"));
  const runPlayback = read("src/components/layout/AppContent/RunPlaybackPanel.tsx");

  assert.equal(en.workbench.contextPanel.run, "Current session inputs");
  assert.equal(zh.workbench.contextPanel.run, "当前会话输入");
  assert.equal(en.runPlayback.context.title, "Context provenance");
  assert.equal(zh.runPlayback.context.title, "上下文来源");
  assert.match(runPlayback, /runPlayback\.context\.title/);
});
