import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("composer phase one exposes model and unavailable panels", () => {
  assert.equal(
    existsSync(join(root, "src/components/chat/ComposerModelPanel.tsx")),
    true,
  );
  assert.equal(
    existsSync(join(root, "src/components/chat/ComposerUnavailablePanel.tsx")),
    true,
  );

  const modelPanel = read("src/components/chat/ComposerModelPanel.tsx");
  const unavailablePanel = read(
    "src/components/chat/ComposerUnavailablePanel.tsx",
  );

  assert.match(modelPanel, /data-composer-model-panel/);
  assert.match(modelPanel, /ModelOption/);
  assert.match(modelPanel, /onSelectModel\(model\.id,\s*model\.value\)/);
  assert.match(unavailablePanel, /data-composer-unavailable-panel/);
  assert.match(unavailablePanel, /data-fail-closed-surface=\{surface\}/);
  assert.match(unavailablePanel, /context-selector/);
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

  assert.match(chatApp, /availableModels=\{filteredModels \?\? \[\]\}/);
  assert.match(chatApp, /currentModelId=\{currentModelId\}/);
  assert.match(chatApp, /onSelectModel=\{handleSelectModel\}/);

  assert.match(chatView, /availableModels:\s*ModelOption\[\]/);
  assert.match(chatView, /currentModelId:\s*string/);
  assert.match(chatView, /onSelectModel:\s*\(modelId:\s*string,\s*modelValue:\s*string\)\s*=>\s*void/);
  assert.match(chatView, /availableModels,/);
  assert.match(chatView, /currentModelId,/);
  assert.match(chatView, /onSelectModel,/);
});

test("chat input opens model selector and keeps context fail-closed", () => {
  const input = read("src/components/chat/ChatInput.tsx");
  const selectors = read("src/components/chat/ChatInputSelectors.tsx");

  assert.match(input, /availableModels\s*=\s*\[\]/);
  assert.match(input, /currentModelId/);
  assert.match(input, /onSelectModel/);
  assert.match(input, /models:\s*!!availableModels\?\.length && !!onSelectModel/);
  assert.match(input, /context:\s*true/);
  assert.match(input, /dispatchComposerSelection\(\{ type: "clear-kind", kind: "model" \}\)/);
  assert.doesNotMatch(input, /id:\s*`model:\$\{currentModelId\}`/);
  assert.match(input, /source:\s*"context-selector"/);
  assert.match(input, /handleSelectModelChip/);

  assert.match(selectors, /ComposerModelPanel/);
  assert.match(selectors, /ComposerUnavailablePanel/);
  assert.match(selectors, /activePanel === "model"/);
  assert.match(selectors, /activePanel === "context"/);
  assert.match(selectors, /surface="context-selector"/);
});

test("context command converts to an unavailable chip without leaving command text behind", () => {
  const input = read("src/components/chat/ChatInput.tsx");

  assert.match(input, /markContextUnavailableCommand/);
  assert.match(
    input,
    /if \(item\.panel === "context"\) \{\s*markContextUnavailableCommand\(\);\s*return;/,
  );
  assert.match(
    input,
    /if \(draft\.panel === "context"\) \{\s*markContextUnavailableCommand\(\);\s*return true;/,
  );
  assert.match(
    input,
    /if \(panel === "context"\) \{\s*markContextUnavailableCommand\(\);\s*return;/,
  );
  assert.doesNotMatch(input, /handleComposerCommandShortcut/);
  assert.doesNotMatch(input, /<ComposerCommandHintBar/);
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

test("composer model and context labels are localized", () => {
  const en = read("src/i18n/locales/en.json");
  const zh = read("src/i18n/locales/zh.json");

  assert.match(en, /"modelSelector"/);
  assert.match(en, /"unavailable"/);
  assert.match(en, /"contextSelector"/);
  assert.match(zh, /"modelSelector"/);
  assert.match(zh, /"unavailable"/);
  assert.match(zh, /"contextSelector"/);
});

test("composer model selector uses restrained workbench overlay styling", () => {
  const modelPanel = read("src/components/chat/ComposerModelPanel.tsx");

  assert.match(modelPanel, /data-composer-model-panel/);
  assert.match(modelPanel, /bg-\[var\(--theme-workbench-panel\)\]/);
  assert.match(modelPanel, /bg-\[var\(--theme-bg-sidebar\)\]/);
  assert.match(modelPanel, /shadow-\[0_8px_24px_rgba\(18,38,63,0\.12\)\]/);
  assert.doesNotMatch(modelPanel, /shadow-xl|shadow-2xl/);
  assert.doesNotMatch(modelPanel, /rounded-xl|rounded-2xl|rounded-3xl/);
  assert.doesNotMatch(modelPanel, /bg-black\/(?:30|40)/);
  assert.doesNotMatch(modelPanel, /\bbg-white(?:\s|")/);
});

test("authenticated workbench popovers avoid legacy heavy overlays", () => {
  const activePopoverFiles = [
    "src/components/chat/ComposerUnavailablePanel.tsx",
    "src/components/chat/AgentOptionButton.tsx",
    "src/components/chat/ChatInputShortcuts.tsx",
    "src/components/chat/ChatMessage/FeedbackDialog.tsx",
    "src/components/layout/UserMenu.tsx",
    "src/components/notification/NotificationDialog.tsx",
    "src/components/selectors/AgentModeSelector.tsx",
    "src/components/selectors/SkillSelector.tsx",
    "src/components/selectors/ToolSelector.tsx",
  ];

  for (const path of activePopoverFiles) {
    const source = read(path);
    assert.match(source, /bg-slate-950\/35|bg-\[var\(--theme-overlay\)\]/, path);
    assert.match(
      source,
      /shadow-\[0_8px_24px_rgba\(18,38,63,0\.12\)\]/,
      path,
    );
    assert.doesNotMatch(source, /bg-black\/(?:40|50)/, path);
    assert.doesNotMatch(source, /shadow-xl|shadow-2xl/, path);
    assert.doesNotMatch(source, /rounded-2xl|rounded-3xl/, path);
  }
});

test("governed composer selector sheets use one workbench token palette", () => {
  const selectorFiles = [
    "src/components/chat/ComposerModelPanel.tsx",
    "src/components/chat/ComposerUnavailablePanel.tsx",
    "src/components/selectors/AgentModeSelector.tsx",
    "src/components/selectors/SkillSelector.tsx",
    "src/components/selectors/ToolSelector.tsx",
  ];

  for (const path of selectorFiles) {
    const source = read(path);

    assert.match(source, /var\(--theme-workbench-panel\)/, path);
    assert.match(source, /var\(--theme-text\)/, path);
    assert.match(source, /var\(--theme-text-secondary\)/, path);
    assert.match(source, /var\(--theme-primary\)|var\(--theme-ring\)/, path);
    assert.doesNotMatch(source, /\b(?:bg|text|border)-stone-\d/, path);
    assert.doesNotMatch(source, /\b(?:bg|text|border)-slate-\d/, path);
    assert.doesNotMatch(source, /\bdark:(?:bg|text|border)-stone-\d/, path);
    assert.doesNotMatch(source, /\bdark:(?:bg|text|border)-slate-\d/, path);
  }
});

test("governed selector primary controls use theme foreground contrast", () => {
  const theme = read("src/styles/base.css");
  const selectorFiles = [
    "src/components/chat/ComposerModelPanel.tsx",
    "src/components/selectors/AgentModeSelector.tsx",
    "src/components/selectors/SkillSelector.tsx",
    "src/components/selectors/ToolSelector.tsx",
  ];

  assert.match(theme, /--theme-primary-foreground:/);
  assert.match(theme, /--theme-primary-foreground-muted:/);
  assert.match(theme, /--theme-primary-foreground-subtle:/);

  for (const path of selectorFiles) {
    const source = read(path);
    assert.doesNotMatch(
      source,
      /bg-\[var\(--theme-primary\)\]\s+text-white/,
      path,
    );
    assert.match(source, /text-\[var\(--theme-primary-foreground\)\]/, path);
  }
});

test("chat loading and feedback surfaces use restrained workbench radius", () => {
  const sources = new Map([
    ["ChatSkeletons", read("src/components/skeletons/ChatSkeletons.tsx")],
    ["FeedbackDialog", read("src/components/chat/ChatMessage/FeedbackDialog.tsx")],
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
    ["AttachmentPreview", read("src/components/chat/AttachmentPreview.tsx")],
    ["SlashCommandMenu", read("src/components/chat/SlashCommandMenu.tsx")],
    ["ChatMessage", read("src/components/chat/ChatMessage/index.tsx")],
    [
      "UserMessageBubble",
      read("src/components/chat/ChatMessage/UserMessageBubble.tsx"),
    ],
    [
      "MessagePartRenderer",
      read("src/components/chat/ChatMessage/MessagePartRenderer.tsx"),
    ],
    [
      "FileRevealItem",
      read("src/components/chat/ChatMessage/items/FileRevealItem.tsx"),
    ],
    [
      "ProjectRevealItem",
      read("src/components/chat/ChatMessage/items/ProjectRevealItem.tsx"),
    ],
    [
      "ToolResultPanel",
      read("src/components/chat/ChatMessage/items/ToolResultPanel.tsx"),
    ],
    ["UsersPanel", read("src/components/panels/UsersPanel.tsx")],
    ["RolesPanel", read("src/components/panels/RolesPanel.tsx")],
    ["MCPPanel", read("src/components/panels/MCPPanel.tsx")],
    [
      "SkillsList",
      read("src/components/panels/SkillsPanel/SkillsList.tsx"),
    ],
    [
      "AgentConfigPanel",
      read("src/components/panels/AgentPanel/AgentConfigPanel.tsx"),
    ],
    ["FeedbackPanel", read("src/components/panels/FeedbackPanel.tsx")],
  ]);

  for (const [name, source] of sources) {
    assert.match(
      source,
      /var\(--theme-bg-card\)|var\(--theme-workbench-panel\)|panel-card|enterprise-modal-shell|workbenchSurface|data-librechat-command-entrypoint/,
      `${name} should use shared enterprise surface tokens`,
    );
    assert.doesNotMatch(source, /rounded-xl|rounded-2xl|rounded-3xl/, name);
    assert.doesNotMatch(source, /\bbg-white(?:\b|\/)/, name);
    assert.doesNotMatch(source, /\bbg-black\/(?:50|70)\b/, name);
    assert.doesNotMatch(source, /shadow-xl|shadow-2xl|shadow-lg/, name);
    assert.doesNotMatch(source, /rgba\(0,0,0/, name);
    assert.doesNotMatch(source, /glass-divider/, name);
  }
});
