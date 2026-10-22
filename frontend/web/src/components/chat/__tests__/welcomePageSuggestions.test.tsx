import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { createLobeServerTestRoot } from "../../common/__tests__/lobeUiTestRoot";

import "../../../i18n";
import { CHAT_START_SUGGESTIONS, WelcomePage } from "../WelcomePage";

const noop = () => {};

function readWelcomeSource(): string {
  return readFileSync(new URL("../WelcomePage.tsx", import.meta.url), "utf8");
}

test("general entry headline, guidance, and the three approved suggestions render", async () => {
  const render = await createLobeServerTestRoot();
  const markup = render(
    createElement(WelcomePage, {
      subtitle: "直接提问，或上传文件一起讨论。",
      composer: createElement("div", { "data-composer": "stub" }),
      onSelectSuggestion: noop,
    }),
  );

  assert.match(markup, /今天想解决什么问题？/);
  assert.match(markup, /直接提问，或上传文件一起讨论。/);
  for (const [key, label] of [
    ["summarize", "总结一份文件"],
    ["draft", "帮我起草内容"],
    ["analyze", "分析一个问题"],
  ]) {
    assert.match(markup, new RegExp(`data-chat-start-suggestion="${key}"`));
    assert.match(markup, new RegExp(label));
  }
  assert.match(markup, /data-composer="stub"/);
});

test("a suggestion carries text into the draft instead of sending it", () => {
  assert.deepEqual(
    CHAT_START_SUGGESTIONS.map((suggestion) => suggestion.key),
    ["summarize", "draft", "analyze"],
  );
  for (const suggestion of CHAT_START_SUGGESTIONS) {
    assert.ok(suggestion.draft.length > 0);
    // The label is the action; the draft is editable text the user still sends.
    assert.notEqual(suggestion.draft, suggestion.label);
  }

  const source = readWelcomeSource();
  assert.match(source, /onClick=\{\(\) => onSelectSuggestion\(suggestion\.draft\)\}/);
  assert.doesNotMatch(source, /onSendMessage|onSend\(/);
});
