import assert from "node:assert/strict";
import test from "node:test";
import {
  buildSelectionActionPrompt,
} from "../selectionActionPrompt";

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
