import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const chatInputSource = readSource("../ChatInput.tsx");
const slashMenuSource = readSource("../SlashCommandMenu.tsx");

test("ChatInput wires the slash menu and composer chips", () => {
  assert.match(chatInputSource, /import \{ SlashCommandMenu \}/);
  assert.match(chatInputSource, /import \{ ComposerSelectionChips \}/);
  assert.match(chatInputSource, /<SlashCommandMenu/);
  assert.match(chatInputSource, /<ComposerSelectionChips/);
});

test("SlashCommandMenu exposes the required command group copy", () => {
  for (const label of ["Skills", "MCP", "agents", "models", "files", "context"]) {
    assert.match(slashMenuSource, new RegExp(`\\b${label}\\b`));
  }
});

test("ChatInput keeps persona mention behavior separate from slash commands", () => {
  assert.match(chatInputSource, /useMentionState/);
  assert.match(chatInputSource, /MentionPopup/);
  assert.match(chatInputSource, /findSlashCommandMatch/);
});

test("ChatInput submits selected slash skill and MCP token intent", () => {
  assert.match(chatInputSource, /selected_skill_names/);
  assert.match(chatInputSource, /selected_mcp_tools/);
  assert.match(chatInputSource, /buildSubmitOptions/);
});

function readSource(relativePath: string): string {
  return readFileSync(
    fileURLToPath(new URL(relativePath, import.meta.url)),
    "utf8",
  );
}
