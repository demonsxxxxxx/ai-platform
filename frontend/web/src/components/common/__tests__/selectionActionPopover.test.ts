import assert from "node:assert/strict";
import test from "node:test";
import {
  buildSelectionActionPrompt,
  getSelectionPopoverPosition,
} from "../selectionActionPrompt";

test("getSelectionPopoverPosition places the menu below the selected text and clamps to the viewport", () => {
  const position = getSelectionPopoverPosition({
    selectionRect: {
      left: 760,
      right: 820,
      top: 120,
      bottom: 148,
      width: 60,
      height: 28,
    },
    viewportWidth: 800,
    viewportHeight: 600,
    menuWidth: 180,
    menuHeight: 40,
    scrollX: 0,
    scrollY: 100,
  });

  assert.deepEqual(position, {
    left: 612,
    top: 156,
  });
});

test("getSelectionPopoverPosition flips above the selected text near the bottom edge", () => {
  const position = getSelectionPopoverPosition({
    selectionRect: {
      left: 100,
      right: 200,
      top: 560,
      bottom: 590,
      width: 100,
      height: 30,
    },
    viewportWidth: 800,
    viewportHeight: 600,
    menuWidth: 160,
    menuHeight: 42,
    scrollX: 0,
    scrollY: 0,
  });

  assert.equal(position.top, 510);
});

test("buildSelectionActionPrompt creates localized ask and explain prompts", () => {
  assert.equal(
    buildSelectionActionPrompt({
      action: "ask",
      selectedText: "React Server Components",
      templates: {
        ask: "Question about:\n\n{{text}}",
        explain: "Explain:\n\n{{text}}",
      },
    }),
    "Question about:\n\nReact Server Components",
  );

  assert.equal(
    buildSelectionActionPrompt({
      action: "explain",
      selectedText: "React Server Components",
      templates: {
        ask: "Question about:\n\n{{text}}",
        explain: "Explain:\n\n{{text}}",
      },
    }),
    "Explain:\n\nReact Server Components",
  );
});

test("buildSelectionActionPrompt can wrap selected text in a markdown code block", () => {
  assert.equal(
    buildSelectionActionPrompt({
      action: "ask",
      selectedText: "const answer = `yes`;",
      templates: {
        ask: "Question about:\n\n{{codeBlock}}\n\nMy question is:",
        explain: "Explain:\n\n{{codeBlock}}",
      },
    }),
    "Question about:\n\n```\nconst answer = `yes`;\n```\n\nMy question is:",
  );
});
