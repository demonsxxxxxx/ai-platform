import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import {
  composerSelectionReducer,
  type ComposerSelection,
} from "../composerSelections";
import {
  removeComposerCommandDraft,
  resolveComposerCommandDraft,
  resolveSlashCommandMenu,
} from "../chatInputCommands";

const root = process.cwd();

const allAvailable = {
  skills: true,
  tools: true,
  models: true,
  files: true,
};

test("completion is limited to the current slash token, not paths, currency or sentence arguments", () => {
  assert.deepEqual(resolveComposerCommandDraft("/"), { start: 0, end: 1, query: "" });
  assert.deepEqual(resolveComposerCommandDraft("正文 /mo"), { start: 3, end: 6, query: "mo" });
  for (const input of ["/workspace/.home/Desktop/report.xlsx在哪？", "/api/v1", "$", "$100", "$ qa", "https://example.test/model", "/model is failing", "/skill qa", "/mcp fetch", "/file report"]) {
    assert.deepEqual(resolveSlashCommandMenu(input, allAvailable), [], input);
  }
  assert.deepEqual(resolveSlashCommandMenu("/unknown", allAvailable), []);
  assert.deepEqual(resolveSlashCommandMenu("/context", allAvailable), []);
});

test("confirmed completion removes only the token before the caret and preserves the full task", () => {
  const input = "正文 /model 后续正文";
  const draft = resolveComposerCommandDraft(input, 9);
  assert.deepEqual(draft, { start: 3, end: 9, query: "model" });
  assert.equal(removeComposerCommandDraft(input, draft!), "正文  后续正文");
  assert.deepEqual(resolveSlashCommandMenu(input, allAvailable, 9).map((item) => item.command), ["model"]);
});

test("slash command menu exposes the Phase 1B command groups", () => {
  assert.deepEqual(
    resolveSlashCommandMenu("/", allAvailable).map((item) => ({
      command: item.command,
      panel: item.panel,
      unavailable: item.unavailable,
    })),
    [
      { command: "skill", panel: "skills", unavailable: false },
      { command: "mcp", panel: "tools", unavailable: false },
      { command: "model", panel: "model", unavailable: false },
      { command: "file", panel: "file", unavailable: false },
    ],
  );
  assert.deepEqual(
    resolveSlashCommandMenu("/m", allAvailable).map((item) => item.command),
    ["mcp", "model"],
  );
});

test("unavailable surfaces stay visible as explicit choices, without executing on typing", () => {
  assert.equal(resolveSlashCommandMenu("/model", { ...allAvailable, models: false })[0].unavailable, true);
  assert.equal(resolveSlashCommandMenu("/skill", { ...allAvailable, skills: false })[0].unavailable, true);
});

test("composer selections are durable and removable by stable id", () => {
  const skill: ComposerSelection = {
    kind: "skill",
    id: "skill:qa-review",
    label: "QA Review",
    source: "marketplace",
    state: "enabled",
  };
  const mcp: ComposerSelection = {
    kind: "mcp",
    id: "mcp:fetch",
    label: "fetch",
    source: "policy",
    state: "disabled",
  };
  const file: ComposerSelection = {
    kind: "file",
    id: "file:artifact-123",
    label: "report.pdf",
    source: "artifact",
    state: "pending",
    referenceId: "artifact-123",
  };

  let chips = composerSelectionReducer([], { type: "upsert", selection: skill });
  chips = composerSelectionReducer(chips, { type: "upsert", selection: mcp });
  chips = composerSelectionReducer(chips, { type: "upsert", selection: file });

  assert.deepEqual(
    chips.map((chip) => ({
      id: chip.id,
      kind: chip.kind,
      label: chip.label,
      state: chip.state,
    })),
    [
      {
        id: "skill:qa-review",
        kind: "skill",
        label: "QA Review",
        state: "enabled",
      },
      { id: "mcp:fetch", kind: "mcp", label: "fetch", state: "disabled" },
      {
        id: "file:artifact-123",
        kind: "file",
        label: "report.pdf",
        state: "pending",
      },
    ],
  );
  assert.deepEqual(
    composerSelectionReducer(chips, { type: "remove", id: "mcp:fetch" }),
    [skill, file],
  );
});

test("chat input renders composer chips and expanded command groups", () => {
  const chatInput = readFileSync(
    join(root, "src/components/chat/ChatInput.tsx"),
    "utf8",
  );
  const featureMenu = readFileSync(
    join(root, "src/components/selectors/FeatureMenu.tsx"),
    "utf8",
  );
  const toolbar = readFileSync(
    join(root, "src/components/chat/ChatInputToolbar.tsx"),
    "utf8",
  );
  const zh = readFileSync(join(root, "src/i18n/locales/zh.json"), "utf8");

  assert.match(chatInput, /<ComposerChips/);
  assert.match(chatInput, /composerSelectionReducer/);
  assert.match(chatInput, /resolveSlashCommandMenu/);
  assert.match(chatInput, /removeComposerCommandDraft/);
  assert.match(toolbar, /data-composer-model-trigger/);
  assert.match(toolbar, /<Paperclip size=\{19\}/);
  assert.doesNotMatch(featureMenu, /featureMenu\.model/);
  assert.doesNotMatch(featureMenu, /featureMenu\.context/);
  assert.doesNotMatch(featureMenu, /featureMenu\.fileReference/);
  assert.doesNotMatch(zh, /"fileReference"\s*:/);
});

test("chat input routes the available /file command to the safe upload picker", () => {
  const chatInput = readFileSync(
    join(root, "src/components/chat/ChatInput.tsx"),
    "utf8",
  );

  assert.match(chatInput, /executeAvailableFileCommand/);
  assert.match(chatInput, /openFileCommandRef\.current\?\.\(\)/);
  assert.doesNotMatch(
    chatInput,
    /item\.panel === "file"[\s\S]{0,160}upsertUnavailableCommandChip/,
  );
});

test("composer keeps explicit slash suggestions without dollar aliases or prefix hints", () => {
  const chatInput = readFileSync(
    join(root, "src/components/chat/ChatInput.tsx"),
    "utf8",
  );
  const slashMenu = readFileSync(
    join(root, "src/components/chat/SlashCommandMenu.tsx"),
    "utf8",
  );
  const commandMenu = readFileSync(
    join(root, "src/components/chat/assistant-ui/elements/composer.tsx"),
    "utf8",
  );
  const zh = readFileSync(join(root, "src/i18n/locales/zh.json"), "utf8");

  assert.doesNotMatch(chatInput, /<ComposerCommandHintBar/);
  assert.doesNotMatch(chatInput, /data-composer-command-hints/);
  assert.doesNotMatch(chatInput, /handleComposerCommandShortcut/);
  assert.match(chatInput, /resolveComposerCommandDraft/);
  assert.match(chatInput, /resolveSlashCommandMenu/);
  assert.match(slashMenu, /<ComposerMenu/);
  assert.match(commandMenu, /data-composer-command-menu/);
  assert.match(commandMenu, /data-slot="composer-menu"/);
  assert.doesNotMatch(slashMenu, /commandAlias/);
  assert.doesNotMatch(zh, /输入 \$/);
  assert.match(zh, /技能/);
});

test("Agent workspaces disable composer commands without changing ordinary Chat", () => {
  const chatInput = readFileSync(
    join(root, "src/components/chat/ChatInput.tsx"),
    "utf8",
  );
  const chatView = readFileSync(
    join(root, "src/components/layout/AppContent/ChatView.tsx"),
    "utf8",
  );

  assert.match(chatInput, /disableSlashCommands = false/);
  assert.match(chatInput, /!disableSlashCommands && slashMenuOpen/);
  assert.doesNotMatch(chatInput, /handleComposerCommandSubmit|openCommandPanel/);
  assert.match(
    chatView,
    /disableSlashCommands: presentation\?\.disableSlashCommands \?\? false/,
  );
});

test("unavailable command choices preserve the draft instead of opening missing selectors", () => {
  const chatInput = readFileSync(join(root, "src/components/chat/ChatInput.tsx"), "utf8");
  const branch = chatInput.match(/if \(item\.unavailable\) \{[\s\S]*?return;\n\s*\}/)?.[0];
  assert.ok(branch);
  assert.match(branch, /upsertUnavailableCommandChip/);
  assert.match(branch, /setActivePanel\(null\)/);
  assert.doesNotMatch(branch, /setInput/);
});

test("slash menu unavailable commands clear stale selector state", () => {
  const chatInput = readFileSync(
    join(root, "src/components/chat/ChatInput.tsx"),
    "utf8",
  );
  const unavailableBranch = chatInput.match(
    /if \(item\.unavailable\) \{[\s\S]*?return;\n\s*\}/,
  )?.[0];

  assert.ok(unavailableBranch);
  assert.match(unavailableBranch, /upsertUnavailableCommandChip/);
  assert.match(unavailableBranch, /setActivePanel\(null\)/);
  assert.doesNotMatch(unavailableBranch, /setInput/);
});

test("slash command menu is anchored outside the clipped textarea region", () => {
  const chatInput = readFileSync(
    join(root, "src/components/chat/ChatInput.tsx"),
    "utf8",
  );
  const slashMenu = readFileSync(
    join(root, "src/components/chat/SlashCommandMenu.tsx"),
    "utf8",
  );
  const commandMenu = readFileSync(
    join(root, "src/components/chat/assistant-ui/elements/composer.tsx"),
    "utf8",
  );

  assert.match(chatInput, /data-composer-command-menu-anchor/);
  assert.match(chatInput, /<SlashCommandMenu/);
  assert.match(slashMenu, /ComposerMenu/);
  assert.match(commandMenu, /PopoverPortal/);
  assert.match(commandMenu, /PopoverPositioner/);
  assert.doesNotMatch(
    chatInput,
    /<div className="px-2\.5 pt-1">[\s\S]*?<SlashCommandMenu[\s\S]*?<\/div>[\s\S]*?<textarea/,
  );
});

test("composer styles and primitive keep only the governed command menu surface", () => {
  const styles = readFileSync(join(root, "src/styles/chat.css"), "utf8");
  const composer = readFileSync(join(root, "src/components/chat/assistant-ui/elements/composer.tsx"), "utf8");
  const featureMenu = readFileSync(
    join(root, "src/components/selectors/FeatureMenu.tsx"),
    "utf8",
  );

  assert.doesNotMatch(styles, /\.composer-command-surface\s*\{/);
  assert.doesNotMatch(styles, /\.composer-command-list\s*\{/);
  assert.match(featureMenu, /<DropdownMenu/);
  assert.match(featureMenu, /type: "group", key: "enhance"/);
  assert.doesNotMatch(styles, /feature-menu-/);
  assert.doesNotMatch(styles, /mention-popup|mention-skeleton|feature-menu-divider/);
  assert.doesNotMatch(composer, /mentionActive|data-mention-active/);
});

test("composer user-facing copy avoids backend implementation jargon", () => {
  const chatInput = readFileSync(
    join(root, "src/components/chat/ChatInput.tsx"),
    "utf8",
  );
  const zh = JSON.parse(
    readFileSync(join(root, "src/i18n/locales/zh.json"), "utf8"),
  );

  for (const source of [
    chatInput,
    JSON.stringify(zh.composerChip),
    JSON.stringify(zh.composerCommand),
    JSON.stringify({
      phase2Unavailable: zh.workbench.phase2Unavailable,
      selectionState: zh.workbench.selectionState,
      unavailableShort: zh.workbench.unavailableShort,
    }),
  ]) {
    assert.doesNotMatch(source, /backend contract/i);
    assert.doesNotMatch(source, /projection/i);
    assert.doesNotMatch(source, /投影/);
    assert.doesNotMatch(source, /后端合约/);
  }
});

test("composer workflow exposes stable browser smoke selectors for PRD evidence", () => {
  const chatInput = readFileSync(
    join(root, "src/components/chat/ChatInput.tsx"),
    "utf8",
  );
  const chips = readFileSync(
    join(root, "src/components/chat/ComposerChips.tsx"),
    "utf8",
  );
  const chipPrimitive = readFileSync(
    join(root, "src/components/chat/assistant-ui/elements/composer-chips.tsx"),
    "utf8",
  );
  const composerPrimitive = readFileSync(
    join(root, "src/components/chat/assistant-ui/elements/composer.tsx"),
    "utf8",
  );
  const slashMenu = readFileSync(
    join(root, "src/components/chat/SlashCommandMenu.tsx"),
    "utf8",
  );
  const commandMenu = readFileSync(
    join(root, "src/components/chat/assistant-ui/elements/composer.tsx"),
    "utf8",
  );
  const skillSelector = readFileSync(
    join(root, "src/components/selectors/SkillSelector.tsx"),
    "utf8",
  );
  const toolSelector = readFileSync(
    join(root, "src/components/selectors/ToolSelector.tsx"),
    "utf8",
  );
  const attachmentList = readFileSync(
    join(root, "src/components/chat/ChatInputAttachments.tsx"),
    "utf8",
  );

  assert.match(slashMenu, /<ComposerMenu/);
  assert.match(commandMenu, /data-composer-command-menu/);
  assert.match(slashMenu, /data-composer-command-item=\{item.command\}/);
  assert.match(skillSelector, /data-composer-skill-selector/);
  assert.match(skillSelector, /data-composer-skill-row=\{skill\.name\}/);
  assert.match(toolSelector, /data-composer-mcp-selector/);
  assert.match(toolSelector, /data-composer-mcp-row=\{tool\.name\}/);
  assert.match(chips, /kind=\{selection\.kind\}/);
  assert.match(chips, /state=\{selection\.state\}/);
  assert.match(chips, /referenceId=\{selection\.referenceId\}/);
  assert.match(chipPrimitive, /data-composer-chip-kind=\{kind\}/);
  assert.match(chipPrimitive, /data-composer-chip-state=\{state\}/);
  assert.match(chipPrimitive, /data-composer-chip-reference=\{referenceId \?\? id\}/);
  assert.match(attachmentList, /data-composer-file-reference/);
  assert.match(attachmentList, /data-composer-file-state/);
  assert.match(chatInput, /data-composer-command-menu-anchor/);
  assert.match(chatInput, /data-slot="composer-chips"/);
  assert.match(composerPrimitive, /data-slot="composer-bar"/);
});

test("Chinese placeholders prioritize messages instead of reserving dollar prefixes", () => {
  const locale = "zh";
  const source = readFileSync(
    join(root, "src/i18n/locales/zh.json"),
    "utf8",
  );

  assert.match(source, /"\s*placeholder"\s*:\s*"[^"]*描述/, locale);
  assert.doesNotMatch(source, /输入 \$/, locale);
});
