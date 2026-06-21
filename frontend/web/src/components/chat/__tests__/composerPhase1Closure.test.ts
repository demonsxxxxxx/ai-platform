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
  assert.match(input, /id:\s*`model:\$\{currentModelId\}`/);
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
  assert.match(
    input,
    /if \(command === "\/context"\) \{\s*markContextUnavailableCommand\(\);\s*return;/,
  );
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
