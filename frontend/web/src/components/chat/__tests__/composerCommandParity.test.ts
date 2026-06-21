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

test("composer first screen exposes slash dollar skills and governed shortcuts", () => {
  const chatInput = readFileSync(
    join(root, "src/components/chat/ChatInput.tsx"),
    "utf8",
  );
  const shortcutBar = readFileSync(
    join(root, "src/components/chat/ComposerCommandHintBar.tsx"),
    "utf8",
  );
  const slashMenu = readFileSync(
    join(root, "src/components/chat/SlashCommandMenu.tsx"),
    "utf8",
  );
  const zh = readFileSync(join(root, "src/i18n/locales/zh.json"), "utf8");
  const en = readFileSync(join(root, "src/i18n/locales/en.json"), "utf8");

  assert.match(chatInput, /<ComposerCommandHintBar/);
  assert.match(chatInput, /handleComposerCommandShortcut/);
  assert.match(shortcutBar, /data-composer-command-hints/);
  assert.match(shortcutBar, /composerCommand\.shortcut\.skills/);
  assert.match(shortcutBar, /composerCommand\.shortcut\.mcp/);
  assert.match(shortcutBar, /composerCommand\.shortcut\.file/);
  assert.match(shortcutBar, /composerCommand\.shortcut\.context/);
  assert.match(shortcutBar, /\$ Skills/);
  assert.match(shortcutBar, /\/mcp/);
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

test("composer shortcut hints fail closed when a governed surface is unavailable", () => {
  const shortcutBar = readFileSync(
    join(root, "src/components/chat/ComposerCommandHintBar.tsx"),
    "utf8",
  );
  const chatInput = readFileSync(
    join(root, "src/components/chat/ChatInput.tsx"),
    "utf8",
  );

  assert.match(shortcutBar, /disabled=\{!available\}/);
  assert.match(shortcutBar, /aria-disabled=\{!available\}/);
  assert.match(shortcutBar, /data-governed-unavailable/);
  assert.doesNotMatch(shortcutBar, /cursor-not-allowed/);
  assert.doesNotMatch(shortcutBar, /border-amber-200 bg-amber-50/);
  assert.match(chatInput, /shortcutAvailabilityByCommand/);
  assert.match(chatInput, /if \(!shortcutAvailabilityByCommand\[command\]\) \{/);
  assert.match(chatInput, /upsertUnavailableCommandChip/);
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
