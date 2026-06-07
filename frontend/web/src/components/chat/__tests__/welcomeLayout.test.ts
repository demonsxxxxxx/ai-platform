import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  getSelectedPersonaStarterPrompts,
  getWelcomePersonaCards,
  getWelcomePersonaCardClass,
  getWelcomePersonaSkeletonCount,
  getWelcomeSuggestionsContainerClass,
  getWelcomeSuggestionButtonClass,
} from "../welcomeLayout.ts";

const currentDir = dirname(fileURLToPath(import.meta.url));
const welcomeCss = readFileSync(
  resolve(currentDir, "../../../styles/welcome.css"),
  "utf8",
);

test("keeps every welcome persona card reachable on mobile", () => {
  const className = getWelcomePersonaCardClass(3);

  assert.equal(className.includes("welcome-persona-card"), true);
  assert.equal(className.includes("hidden sm:flex"), false);
});

test("keeps later starter prompt pills reachable on narrow screens", () => {
  const className = getWelcomeSuggestionButtonClass(2);

  assert.equal(className.includes("welcome-suggestion-pill"), true);
  assert.equal(className.includes("hidden sm:flex"), false);
});

test("caps welcome suggestion prompts to two rows with vertical scrolling", () => {
  assert.match(
    welcomeCss,
    /\.welcome-suggestions-grid-wrapper\s*\{[\s\S]*--welcome-suggestion-row-height: 2\.5rem;[\s\S]*max-height: calc\(\s*var\(--welcome-suggestion-row-height\) \* 2 \+ var\(--welcome-suggestion-row-gap\)\s*\);[\s\S]*overflow-y: auto;/,
  );
});

test("caps welcome persona choices to two rows with vertical scrolling", () => {
  assert.match(
    welcomeCss,
    /@media \(min-width: 640px\) \{[\s\S]*\.welcome-persona-gallery\s*\{[\s\S]*--welcome-persona-card-height: 6rem;[\s\S]*max-height: calc\(\s*var\(--welcome-persona-card-height\) \* 2 \+ var\(--welcome-persona-row-gap\)\s*\);[\s\S]*overflow-y: auto;/,
  );
});

test("keeps starter prompt container narrower than persona gallery", () => {
  assert.match(
    getWelcomeSuggestionsContainerClass("prompts"),
    /sm:max-w-\[38rem\]/,
  );
  assert.match(
    getWelcomeSuggestionsContainerClass("personas"),
    /sm:max-w-\[42rem\]/,
  );
});

test("shows persona cards before a welcome persona is selected", () => {
  const cards = getWelcomePersonaCards(
    [
      { id: "writer", name: "Writer", starter_prompts: [] },
      { id: "coder", name: "Coder", starter_prompts: [] },
      { id: "planner", name: "Planner", starter_prompts: [] },
    ],
    null,
    2,
  );

  assert.deepEqual(
    cards.map((card) => card.id),
    ["writer", "coder"],
  );
});

test("shows all welcome persona cards with pinned and favorite cards first", () => {
  const cards = getWelcomePersonaCards(
    [
      { id: "normal", name: "Normal", starter_prompts: [], usage_count: 10 },
      {
        id: "favorite",
        name: "Favorite",
        starter_prompts: [],
        is_favorite: true,
      },
      {
        id: "pinned",
        name: "Pinned",
        starter_prompts: [],
        is_pinned: true,
      },
    ],
    null,
  );

  assert.deepEqual(
    cards.map((card) => card.id),
    ["pinned", "favorite", "normal"],
  );
});

test("reserves persona skeleton cards while presets are loading", () => {
  assert.equal(getWelcomePersonaSkeletonCount(true, 0), 6);
  assert.equal(getWelcomePersonaSkeletonCount(true, 3), 0);
  assert.equal(getWelcomePersonaSkeletonCount(false, 0), 0);
});

test("uses only the selected persona starter prompts after a welcome persona is selected", () => {
  const prompts = getSelectedPersonaStarterPrompts(
    [
      {
        id: "writer",
        name: "Writer",
        starter_prompts: [{ icon: "✍️", text: "写一段开场白" }],
      },
      {
        id: "coder",
        name: "Coder",
        starter_prompts: [
          { text: { zh: "帮我审查这段代码", en: "Review this code" } },
        ],
      },
    ],
    "coder",
    "zh-CN",
  );

  assert.deepEqual(prompts, [{ icon: null, text: "帮我审查这段代码" }]);
});

test("falls back to default suggestions when selected persona has no starter prompts", () => {
  const prompts = getSelectedPersonaStarterPrompts(
    [{ id: "coder", name: "Coder", starter_prompts: [] }],
    "coder",
    "zh-CN",
    [{ icon: "🐍", text: "创建一个 Python 脚本" }],
  );

  assert.deepEqual(prompts, [{ icon: "🐍", text: "创建一个 Python 脚本" }]);
});
