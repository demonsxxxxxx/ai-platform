import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("composer exposes the backed model panel", () => {
  assert.equal(
    existsSync(join(root, "src/components/chat/assistant-ui/elements/composer-model-picker.tsx")),
    true,
  );
  const modelPanel = read("src/components/chat/assistant-ui/elements/composer-model-picker.tsx");

  assert.match(modelPanel, /data-composer-model-panel/);
  assert.match(modelPanel, /data-composer-model-option/);
  assert.match(modelPanel, /\[data-composer-model-trigger\]/);
  assert.match(modelPanel, /<ComposerMenuItem/);
  assert.doesNotMatch(modelPanel, /data-composer-model-provider/);
  assert.doesNotMatch(modelPanel, /model\.provider/);
  assert.match(modelPanel, /onSelectModel\(model\.id,\s*model\.value\)/);
  assert.doesNotMatch(modelPanel, /selectionMode|modelSelectionMode|"auto"\s*\|\s*"explicit"/);
});

test("model projections flow from app content into chat input", () => {
  const types = read("src/components/chat/chatInputTypes.ts");
  const chatApp = read("src/components/layout/AppContent/ChatAppContent.tsx");
  const chatView = read("src/components/layout/AppContent/ChatView.tsx");

  assert.match(types, /import type \{ ModelOption \}/);
  assert.match(types, /availableModels\?:\s*ModelOption\[\]/);
  assert.match(types, /currentModelId\?:\s*string/);
  assert.match(
    types,
    /onSelectModel\?:\s*\(modelId:\s*string,\s*modelValue:\s*string\)\s*=>\s*void/,
  );

  assert.match(
    chatApp,
    /availableModels=\{filteredModels \?\? \[\]\}/,
  );
  assert.match(chatApp, /availableModels\?\.length/);
  assert.match(chatApp, /reconcileCurrentModelSelection\(\{/);
  assert.match(chatApp, /currentModelId=\{currentModelId\}/);
  assert.match(chatApp, /onSelectModel=\{handleSelectModel\}/);

  assert.match(chatView, /availableModels:\s*ModelOption\[\]/);
  assert.match(chatView, /currentModelId:\s*string/);
  assert.match(chatView, /onSelectModel:\s*\(modelId:\s*string,\s*modelValue:\s*string\)\s*=>\s*void/);
  for (const source of [types, chatApp, chatView]) assert.doesNotMatch(source, /modelSelectionMode|"auto"\s*\|\s*"explicit"/);
  assert.match(chatView, /availableModels,/);
  assert.match(chatView, /currentModelId,/);
  assert.match(chatView, /onSelectModel,/);
});

test("chat input exposes the existing model panel from a visible composer control", () => {
  const input = read("src/components/chat/ChatInput.tsx");
  const toolbar = read("src/components/chat/ChatInputToolbar.tsx");
  const selectors = read("src/components/chat/ChatInputSelectors.tsx");

  assert.match(input, /availableModels\s*=\s*\[\]/);
  assert.match(input, /currentModelId/);
  assert.match(input, /onSelectModel/);
  assert.match(input, /models:\s*!!onSelectModel/);
  assert.match(input, /availableModels=\{availableModels\}/);
  assert.match(input, /onSelectModel=\{onSelectModel\}/);
  assert.match(input, /\s+showModelSelector\s*\n/);
  assert.match(toolbar, /showModelSelector && onSelectModel/);
  assert.match(toolbar, /data-composer-model-trigger/);
  assert.match(input, /if \(activePanel === "model"\) void onModelSelectorOpen\?\.\(\)/);
  assert.match(input, /dispatchComposerSelection\(\{ type: "clear-kind", kind: "model" \}\)/);
  assert.doesNotMatch(input, /id:\s*`model:\$\{currentModelId\}`/);
  assert.match(input, /handleSelectModelChip/);

  assert.match(selectors, /ComposerModelPicker/);
  assert.match(selectors, /activePanel === "model"/);
  assert.doesNotMatch(selectors, /ComposerUnavailablePanel|context-selector/);
});

test("composer source contains no context command or unavailable context chip flow", () => {
  const input = read("src/components/chat/ChatInput.tsx");
  const commands = read("src/components/chat/chatInputCommands.ts");

  assert.doesNotMatch(input, /markContextUnavailableCommand|context-selector/);
  assert.doesNotMatch(commands, /labelKey:\s*"composerCommand\.context/);
  assert.doesNotMatch(input, /handleComposerCommandShortcut/);
  assert.doesNotMatch(input, /<ComposerCommandHintBar/);
});

test("composer separates task input from its bottom action row", () => {
  const input = read("src/components/chat/ChatInput.tsx");
  const toolbar = read("src/components/chat/ChatInputToolbar.tsx");

  assert.match(input, /data-slot="composer-input"/);
  assert.match(input, /data-slot="composer-toolbar-region"/);
  assert.match(toolbar, /<Paperclip size=\{19\}/);
  assert.match(toolbar, /accept=\{attachmentAccept\}/);
});

test("removing the model chip is local-only and does not silently switch models", () => {
  const input = read("src/components/chat/ChatInput.tsx");

  assert.doesNotMatch(input, /const fallbackModel = availableModels\.find/);
  assert.doesNotMatch(input, /onSelectModel\?\.\(fallbackModel\.id/);
  assert.match(
    input,
    /if \(id\.startsWith\("model:"\)\) \{\s*return;\s*\}/,
  );
});

test("composer model labels remain in the Chinese catalog", () => {
  const zh = read("src/i18n/locales/zh.json");

  assert.match(zh, /"modelSelector"/);
});

test("composer model selector delegates overlays to Lobe", () => {
  const modelPanel = read("src/components/chat/assistant-ui/elements/composer-model-picker.tsx");
  assert.match(modelPanel, /<PopoverPortal>/);
  assert.match(modelPanel, /<PopoverPopup/);
  assert.match(modelPanel, /<Drawer/);
  assert.match(modelPanel, /maxWidth: "calc\(100vw - 16px\)"/);
  assert.doesNotMatch(modelPanel, /createPortal|document\.body/);
});

test("authenticated workbench popovers use the existing Lobe overlay owner", () => {
  for (const path of [
    "src/components/chat/AgentOptionButton.tsx",
    "src/components/layout/UserMenu.tsx", "src/components/selectors/SkillSelector.tsx", "src/components/selectors/ToolSelector.tsx",
  ]) {
    const source = read(path);
    assert.match(source, /from "@lobehub\/ui\/base-ui"/, path);
    assert.match(source, /<(?:Popover|DropdownMenu|Modal|Drawer)/, path);
    assert.doesNotMatch(source, /createPortal\([\s\S]*document\.body/, path);
  }
});

test("governed composer selector sheets share the Lobe theme owner", () => {
  for (const path of ["src/components/chat/assistant-ui/elements/composer-model-picker.tsx", "src/components/selectors/SkillSelector.tsx", "src/components/selectors/ToolSelector.tsx"]) {
    const source = read(path);
    assert.match(source, /from "@lobehub\/ui\/base-ui"/, path);
    assert.doesNotMatch(source, /\b(?:bg|text|border)-(?:slate|stone)-\d/, path);
  }
});

test("governed selector primary controls inherit library foreground contrast", () => {
  const theme = read("src/styles/base.css");
  assert.match(theme, /--theme-primary-foreground:/);
  for (const path of ["src/components/chat/assistant-ui/elements/composer-model-picker.tsx", "src/components/selectors/SkillSelector.tsx", "src/components/selectors/ToolSelector.tsx"]) {
    const source = read(path);
    assert.match(source, /Button/, path);
    assert.doesNotMatch(source, /bg-\[var\(--theme-primary\)\]\s+text-white/, path);
  }
});

test("chat loading surfaces use restrained workbench radius", () => {
  const sources = new Map([
    ["ChatSkeletons", read("src/components/skeletons/ChatSkeletons.tsx")],
  ]);

  for (const [name, source] of sources) {
    assert.doesNotMatch(source, /rounded-2xl|rounded-3xl/, name);
    assert.doesNotMatch(source, /\bbg-white\b|\bdark:bg-stone-(?:800|900)\b/, name);
    assert.doesNotMatch(source, /shadow-xl|shadow-2xl/, name);
  }
});

test("authenticated chat support surfaces use restrained enterprise workbench tokens", () => {
  const sources = new Map([
    ["ViewerToolbar", read("src/components/common/ViewerToolbar.tsx")],
    ["AttachmentCard", read("src/components/common/AttachmentCard.tsx")],
    ["ErrorBoundary", read("src/components/common/ErrorBoundary.tsx")],
    ["SlashCommandMenu", read("src/components/chat/SlashCommandMenu.tsx")],
    ["ChatMessage", read("src/components/chat/ChatMessage/index.tsx")],
    [
      "UserMessageBubble",
      read("src/components/chat/ChatMessage/UserMessageBubble.tsx"),
    ],
    ["ArtifactCard", read("src/components/chat/ChatMessage/elements/ArtifactCard.tsx")],
    [
      "MessagePartRenderer",
      read("src/components/chat/ChatMessage/MessagePartRenderer.tsx"),
    ],
    [
      "ToolResultPanel",
      read("src/components/chat/ChatMessage/items/ToolResultPanel.tsx"),
    ],
    ["RolesPanel", read("src/components/panels/RolesPanel.tsx")],
    ["MCPPanel", read("src/components/panels/MCPPanel.tsx")],
    [
      "SkillsList",
      read("src/components/panels/SkillsPanel/SkillsList.tsx"),
    ],
  ]);

  for (const [name, source] of sources) {
    if (name === "MessagePartRenderer") {
      assert.match(source, /<ArtifactCard/);
      continue;
    }
    if (["SlashCommandMenu", "ChatMessage", "UserMessageBubble"].includes(name)) {
      assert.match(source, /assistant-ui\/elements\//, `${name} uses the migrated view owner`);
      assert.doesNotMatch(source, /LibreChatComposer|LibreChatCommandMenu/);
      continue;
    }
    assert.match(
      source,
      /var\(--theme-bg-card\)|var\(--theme-workbench-panel\)|panel-card|enterprise-modal-shell|workbenchSurface|data-librechat-command-entrypoint/,
      `${name} should use shared enterprise surface tokens`,
    );
    assert.doesNotMatch(source, /rounded-2xl|rounded-3xl/, name);
    assert.doesNotMatch(source, /\bbg-white(?:\b|\/)/, name);
    assert.doesNotMatch(source, /\bbg-black\/(?:50|70)\b/, name);
    assert.doesNotMatch(source, /shadow-xl|shadow-2xl|shadow-lg/, name);
    assert.doesNotMatch(source, /rgba\(0,0,0/, name);
    assert.doesNotMatch(source, /glass-divider/, name);
  }
});
