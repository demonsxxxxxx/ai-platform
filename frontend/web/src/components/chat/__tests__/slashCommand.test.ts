import test from "node:test";
import assert from "node:assert/strict";

import {
  applySlashCommandSelection,
  buildSlashCommandOptions,
  dedupeComposerTokens,
  findSlashCommandMatch,
  moveSlashCommandHighlight,
} from "../slashCommand.ts";

import type { SkillResponse, ToolState } from "../../../types/index.ts";

const skills: SkillResponse[] = [
  {
    name: "reference-fact-extraction",
    description: "Extract facts from uploaded source files.",
    tags: ["docs", "review"],
    enabled: true,
    source: "manual",
    files: {},
    file_count: 1,
    installed_from: "manual",
    is_published: false,
    marketplace_is_active: false,
  },
  {
    name: "disabled-skill",
    description: "Disabled skill.",
    tags: [],
    enabled: false,
    source: "marketplace",
    files: {},
    file_count: 1,
    installed_from: "marketplace",
    is_published: true,
    marketplace_is_active: true,
  },
];

const tools: ToolState[] = [
  {
    name: "browser.search",
    description: "Search via MCP browser.",
    category: "mcp",
    server: "browser",
    parameters: [],
    enabled: true,
  },
  {
    name: "shell.exec",
    description: "Builtin shell tool.",
    category: "builtin",
    parameters: [],
    enabled: true,
  },
  {
    name: "finance.quote",
    description: "Disabled MCP finance tool.",
    category: "mcp",
    server: "finance",
    parameters: [],
    enabled: false,
    user_disabled: true,
  },
];

test("findSlashCommandMatch activates slash at the beginning of the input", () => {
  assert.deepEqual(findSlashCommandMatch("/", 1), {
    slashIndex: 0,
    query: "",
  });
});

test("findSlashCommandMatch captures slash query text", () => {
  assert.deepEqual(findSlashCommandMatch("/ski", 4), {
    slashIndex: 0,
    query: "ski",
  });
});

test("findSlashCommandMatch activates slash after whitespace", () => {
  assert.deepEqual(findSlashCommandMatch("hello /mcp", 10), {
    slashIndex: 6,
    query: "mcp",
  });
});

test("findSlashCommandMatch ignores slash inside a word", () => {
  assert.equal(findSlashCommandMatch("abc/skill", 9), null);
});

test("findSlashCommandMatch closes after whitespace following slash command", () => {
  assert.equal(findSlashCommandMatch("/skill now", 10), null);
});

test("buildSlashCommandOptions includes required command groups", () => {
  const options = buildSlashCommandOptions({
    query: "",
    skills: [],
    tools: [],
    agents: [],
    uploadCategories: [],
  });

  assert.deepEqual(
    options.filter((option) => option.kind === "command").map((o) => o.group),
    ["skill", "mcp", "agent", "model", "file", "context"],
  );
});

test("buildSlashCommandOptions filters base commands by query", () => {
  const options = buildSlashCommandOptions({
    query: "ski",
    skills,
    tools,
    agents: [],
    uploadCategories: [],
  });

  assert.deepEqual(
    options.map((option) => option.id),
    ["command:skill", "skill:reference-fact-extraction", "skill:disabled-skill"],
  );
});

test("applySlashCommandSelection returns a skill token and removes slash text", () => {
  const match = findSlashCommandMatch("run /ski", 8);
  assert.ok(match);

  const option = buildSlashCommandOptions({
    query: "ski",
    skills,
    tools: [],
    agents: [],
    uploadCategories: [],
  }).find((candidate) => candidate.id === "skill:reference-fact-extraction");

  assert.ok(option);
  assert.deepEqual(applySlashCommandSelection("run /ski", match, option), {
    input: "run ",
    cursorPosition: 4,
    token: {
      id: "reference-fact-extraction",
      type: "skill",
      label: "reference-fact-extraction",
      description: "Extract facts from uploaded source files.",
      state: "selected",
    },
    nextPanel: null,
  });
});

test("applySlashCommandSelection returns an MCP token and removes slash text", () => {
  const match = findSlashCommandMatch("/mcp", 4);
  assert.ok(match);

  const option = buildSlashCommandOptions({
    query: "mcp",
    skills: [],
    tools,
    agents: [],
    uploadCategories: [],
  }).find((candidate) => candidate.id === "mcp:browser.search");

  assert.ok(option);
  assert.equal(option.disabled, false);
  assert.deepEqual(applySlashCommandSelection("/mcp", match, option), {
    input: "",
    cursorPosition: 0,
    token: {
      id: "browser.search",
      type: "mcp",
      label: "browser.search",
      description: "Search via MCP browser.",
      state: "selected",
    },
    nextPanel: null,
  });
});

test("applySlashCommandSelection keeps unavailable context fail-closed", () => {
  const match = findSlashCommandMatch("/context", 8);
  assert.ok(match);

  const option = buildSlashCommandOptions({
    query: "context",
    skills: [],
    tools: [],
    agents: [],
    uploadCategories: [],
  }).find((candidate) => candidate.id === "command:context");

  assert.ok(option);
  assert.equal(option.disabled, true);
  assert.deepEqual(applySlashCommandSelection("/context", match, option), {
    input: "/context",
    cursorPosition: 8,
    token: null,
    nextPanel: null,
  });
});

test("moveSlashCommandHighlight wraps down through available options", () => {
  assert.equal(moveSlashCommandHighlight(2, "down", 3), 0);
  assert.equal(moveSlashCommandHighlight(0, "down", 3), 1);
});

test("moveSlashCommandHighlight wraps up through available options", () => {
  assert.equal(moveSlashCommandHighlight(0, "up", 3), 2);
  assert.equal(moveSlashCommandHighlight(2, "up", 3), 1);
});

test("dedupeComposerTokens replaces tokens by type and id", () => {
  const tokens = [
    {
      id: "reference-fact-extraction",
      type: "skill" as const,
      label: "reference-fact-extraction",
      state: "selected" as const,
    },
    {
      id: "browser.search",
      type: "mcp" as const,
      label: "browser.search",
      state: "selected" as const,
    },
  ];

  assert.deepEqual(
    dedupeComposerTokens(tokens, {
      id: "browser.search",
      type: "mcp",
      label: "Browser Search",
      description: "Updated label",
      state: "selected",
    }),
    [
      tokens[0],
      {
        id: "browser.search",
        type: "mcp",
        label: "Browser Search",
        description: "Updated label",
        state: "selected",
      },
    ],
  );
});
