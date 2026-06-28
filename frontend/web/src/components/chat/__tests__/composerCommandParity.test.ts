import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import {
  composerSelectionReducer,
  type ComposerSelection,
} from "../composerSelections";
import {
  parseComposerCommand,
  resolveComposerCommandDraft,
  resolveSlashCommandMenu,
  resolveCommandPrefixPanel,
} from "../chatInputCommands";

const root = process.cwd();

const allAvailable = {
  skills: true,
  tools: true,
  agents: true,
  models: true,
  files: true,
  context: true,
};

test("slash command parser maps command words to governed panels", () => {
  assert.deepEqual(parseComposerCommand("/", allAvailable), {
    trigger: "/",
    command: "menu",
    panel: "command-menu",
    query: "",
    unavailable: false,
  });
  assert.deepEqual(parseComposerCommand("/skill qa", allAvailable), {
    trigger: "/",
    command: "skill",
    panel: "skills",
    query: "qa",
    unavailable: false,
  });
  assert.deepEqual(parseComposerCommand("/mcp fetch", allAvailable), {
    trigger: "/",
    command: "mcp",
    panel: "tools",
    query: "fetch",
    unavailable: false,
  });
  assert.deepEqual(parseComposerCommand("/agent code", allAvailable), {
    trigger: "/",
    command: "agent",
    panel: "agent",
    query: "code",
    unavailable: false,
  });
  assert.deepEqual(parseComposerCommand("/model gpt", allAvailable), {
    trigger: "/",
    command: "model",
    panel: "model",
    query: "gpt",
    unavailable: false,
  });
  assert.deepEqual(parseComposerCommand("/file report", allAvailable), {
    trigger: "/",
    command: "file",
    panel: "file",
    query: "report",
    unavailable: true,
  });
  assert.deepEqual(parseComposerCommand("/file", allAvailable), {
    trigger: "/",
    command: "file",
    panel: "file",
    query: "",
    unavailable: false,
  });
  assert.deepEqual(parseComposerCommand("/context memory", allAvailable), {
    trigger: "/",
    command: "context",
    panel: "context",
    query: "memory",
    unavailable: false,
  });
});

test("dollar command is Skills-first and never maps to tools", () => {
  assert.deepEqual(parseComposerCommand("$ qa", allAvailable), {
    trigger: "$",
    command: "skill",
    panel: "skills",
    query: "qa",
    unavailable: false,
  });
  assert.equal(resolveCommandPrefixPanel("$", allAvailable), "skills");
});

test("dollar command fails closed as a Skill chip when Skills are unavailable", () => {
  const unavailableSkills = {
    ...allAvailable,
    skills: false,
  };

  assert.deepEqual(parseComposerCommand("$", unavailableSkills), {
    trigger: "$",
    command: "skill",
    panel: "skills",
    query: "",
    unavailable: true,
  });
  assert.deepEqual(resolveComposerCommandDraft("$", unavailableSkills), {
    command: {
      trigger: "$",
      command: "skill",
      panel: "skills",
      query: "",
      unavailable: true,
    },
    panel: "skills",
    selectorQuery: "",
    shouldExecute: true,
  });
});

test("composer command drafts preserve multi-character typing and seed selector queries", () => {
  assert.deepEqual(resolveComposerCommandDraft("/", allAvailable), {
    command: {
      trigger: "/",
      command: "menu",
      panel: "command-menu",
      query: "",
      unavailable: false,
    },
    panel: "command-menu",
    selectorQuery: "",
    shouldExecute: false,
  });
  assert.deepEqual(resolveComposerCommandDraft("/m", allAvailable), {
    command: {
      trigger: "/",
      command: "menu",
      panel: "command-menu",
      query: "m",
      unavailable: false,
    },
    panel: "command-menu",
    selectorQuery: "m",
    shouldExecute: false,
  });
  assert.deepEqual(resolveComposerCommandDraft("/mcp fetch", allAvailable), {
    command: {
      trigger: "/",
      command: "mcp",
      panel: "tools",
      query: "fetch",
      unavailable: false,
    },
    panel: "tools",
    selectorQuery: "fetch",
    shouldExecute: false,
  });
  assert.deepEqual(resolveComposerCommandDraft("$ qa", allAvailable), {
    command: {
      trigger: "$",
      command: "skill",
      panel: "skills",
      query: "qa",
      unavailable: false,
    },
    panel: "skills",
    selectorQuery: "qa",
    shouldExecute: false,
  });
});

test("file and unavailable commands execute only when the command word is complete", () => {
  assert.equal(resolveComposerCommandDraft("/f", allAvailable)?.shouldExecute, false);
  assert.equal(resolveComposerCommandDraft("/file", allAvailable)?.shouldExecute, true);
  assert.equal(
    resolveComposerCommandDraft("/file", allAvailable)?.command.unavailable,
    false,
  );
  assert.equal(
    resolveComposerCommandDraft("/file report", allAvailable)?.command
      .unavailable,
    true,
  );
  assert.equal(
    resolveComposerCommandDraft("/model opus", {
      ...allAvailable,
      models: false,
    })?.shouldExecute,
    true,
  );
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
      { command: "agent", panel: "agent", unavailable: false },
      { command: "model", panel: "model", unavailable: false },
      { command: "file", panel: "file", unavailable: false },
      { command: "context", panel: "context", unavailable: false },
    ],
  );
  assert.deepEqual(
    resolveSlashCommandMenu("/m", allAvailable).map((item) => item.command),
    ["mcp", "model"],
  );
});

test("missing backend authority returns explicit unavailable command state", () => {
  assert.deepEqual(
    parseComposerCommand("/model opus", { ...allAvailable, models: false }),
    {
      trigger: "/",
      command: "model",
      panel: "model",
      query: "opus",
      unavailable: true,
    },
  );
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

  assert.match(chatInput, /<ComposerChips/);
  assert.match(chatInput, /composerSelectionReducer/);
  assert.match(chatInput, /resolveSlashCommandMenu/);
  assert.match(chatInput, /command-menu/);
  assert.match(featureMenu, /featureMenu\.model/);
  assert.match(featureMenu, /featureMenu\.context/);
  assert.match(featureMenu, /featureMenu\.fileReference/);
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

test("composer first screen keeps slash and dollar commands typed-first", () => {
  const chatInput = readFileSync(
    join(root, "src/components/chat/ChatInput.tsx"),
    "utf8",
  );
  const slashMenu = readFileSync(
    join(root, "src/components/chat/SlashCommandMenu.tsx"),
    "utf8",
  );
  const zh = readFileSync(join(root, "src/i18n/locales/zh.json"), "utf8");
  const en = readFileSync(join(root, "src/i18n/locales/en.json"), "utf8");

  assert.doesNotMatch(chatInput, /<ComposerCommandHintBar/);
  assert.doesNotMatch(chatInput, /data-composer-command-hints/);
  assert.doesNotMatch(chatInput, /handleComposerCommandShortcut/);
  assert.match(chatInput, /resolveComposerCommandDraft/);
  assert.match(chatInput, /resolveSlashCommandMenu/);
  assert.match(slashMenu, /data-composer-command-menu/);
  assert.match(slashMenu, /commandAlias/);
  assert.match(slashMenu, /\$/);
  assert.match(zh, /输入 \//);
  assert.match(zh, /输入 \$/);
  assert.match(zh, /Skills/);
  assert.match(en, /Type \//);
  assert.match(en, /type \$/i);
  assert.match(en, /Skills/);
});

test("composer commands fail closed when a governed surface is unavailable", () => {
  const chatInput = readFileSync(
    join(root, "src/components/chat/ChatInput.tsx"),
    "utf8",
  );

  assert.doesNotMatch(chatInput, /shortcutAvailabilityByCommand/);
  assert.match(chatInput, /draft\.command\.unavailable/);
  assert.match(chatInput, /upsertUnavailableCommandChip/);
  assert.match(chatInput, /setInput\(""\)/);
});

test("typed unavailable commands fail closed before opening missing selectors", () => {
  const chatInput = readFileSync(
    join(root, "src/components/chat/ChatInput.tsx"),
    "utf8",
  );

  assert.match(chatInput, /draft\.command\.unavailable/);
  assert.match(chatInput, /upsertUnavailableCommandChip\(draft\.command\)/);
  assert.match(chatInput, /setInput\(""\)/);
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
  assert.match(unavailableBranch, /setCommandSearchSeed\(null\)/);
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

  assert.match(chatInput, /data-composer-command-menu-anchor/);
  assert.match(chatInput, /<SlashCommandMenu/);
  assert.match(slashMenu, /composer-command-surface/);
  assert.match(slashMenu, /composer-command-list/);
  assert.doesNotMatch(
    chatInput,
    /<div className="px-2\.5 pt-1">[\s\S]*?<SlashCommandMenu[\s\S]*?<\/div>[\s\S]*?<textarea/,
  );
});

test("composer user-facing copy avoids backend implementation jargon", () => {
  const chatInput = readFileSync(
    join(root, "src/components/chat/ChatInput.tsx"),
    "utf8",
  );
  const zh = JSON.parse(
    readFileSync(join(root, "src/i18n/locales/zh.json"), "utf8"),
  );
  const en = JSON.parse(
    readFileSync(join(root, "src/i18n/locales/en.json"), "utf8"),
  );

  for (const source of [
    chatInput,
    JSON.stringify(zh.composerChip),
    JSON.stringify(zh.composerCommand),
    JSON.stringify(en.composerChip),
    JSON.stringify(en.composerCommand),
    JSON.stringify({
      phase2Unavailable: zh.workbench.phase2Unavailable,
      selectionState: zh.workbench.selectionState,
      unavailableShort: zh.workbench.unavailableShort,
    }),
    JSON.stringify({
      phase2Unavailable: en.workbench.phase2Unavailable,
      selectionState: en.workbench.selectionState,
      unavailableShort: en.workbench.unavailableShort,
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
  const slashMenu = readFileSync(
    join(root, "src/components/chat/SlashCommandMenu.tsx"),
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

  assert.match(slashMenu, /data-composer-command-menu/);
  assert.match(slashMenu, /data-composer-command-item=\{item\.command\}/);
  assert.match(skillSelector, /data-composer-skill-selector/);
  assert.match(skillSelector, /data-composer-skill-row=\{skill\.name\}/);
  assert.match(toolSelector, /data-composer-mcp-selector/);
  assert.match(toolSelector, /data-composer-mcp-row=\{tool\.name\}/);
  assert.match(chips, /data-composer-chip-kind=\{selection\.kind\}/);
  assert.match(chips, /data-composer-chip-state=\{selection\.state\}/);
  assert.match(chips, /data-composer-chip-reference=\{selection\.referenceId/);
  assert.match(attachmentList, /data-composer-file-reference/);
  assert.match(attachmentList, /data-composer-file-state/);
  assert.match(chatInput, /data-composer-command-menu-anchor/);
  assert.match(chatInput, /data-librechat-composer-region="chips"/);
});

test("all supported placeholders are slash and dollar skills first", () => {
  for (const locale of ["en", "zh", "ja", "ko", "ru"]) {
    const source = readFileSync(
      join(root, `src/i18n/locales/${locale}.json`),
      "utf8",
    );

    assert.match(source, /"\s*placeholder"\s*:\s*"[^"]*\//, locale);
    assert.match(source, /"\s*placeholder"\s*:\s*"[^"]*\$/, locale);
    assert.match(source, /"\s*placeholder"\s*:\s*"[^"]*Skills/, locale);
  }
});
