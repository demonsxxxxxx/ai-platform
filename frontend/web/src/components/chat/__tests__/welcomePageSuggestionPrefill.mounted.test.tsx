import assert from "node:assert/strict";
import test from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { act, createElement, type ReactNode } from "react";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot";

import "../../../i18n";
import { CHAT_START_SUGGESTIONS, WelcomePage } from "../WelcomePage";

/** Mounted proof that a suggestion fills the draft and never submits it. */
test("clicking a suggestion fills the mounted composer draft without sending", async () => {
  const dom = installSelectTestDom();
  const container = dom.document.createElement("div");
  const root = await createLobeTestRoot(container);
  const drafts: string[] = [];
  const sends: string[] = [];

  // A stand-in composer mirrors the real one: it owns the textarea value that
  // ChatView's setComposerInput writes into, and only it can submit.
  function ComposerStub({ draft }: { draft: string }): ReactNode {
    return createElement(
      "form",
      {
        "data-composer": "stub",
        onSubmit: (event: { preventDefault: () => void }) => {
          event.preventDefault();
          sends.push(draft);
        },
      },
      createElement("textarea", { readOnly: true, value: draft }),
      createElement("button", { type: "submit" }, "发送"),
    );
  }

  function Harness(): ReactNode {
    return createElement(WelcomePage, {
      subtitle: "直接提问，或上传文件一起讨论。",
      composer: createElement(ComposerStub, { draft: drafts.at(-1) ?? "" }),
      onSelectSuggestion: (draft: string) => {
        drafts.push(draft);
        root.render(createElement(Harness));
      },
    });
  }

  try {
    act(() => root.render(createElement(Harness)));
    const buttons = () =>
      Array.from(
        container.querySelectorAll<HTMLButtonElement>("[data-chat-start-suggestion]"),
      );
    assert.equal(buttons().length, 3);
    assert.equal(
      container.querySelector<HTMLTextAreaElement>("textarea")?.value,
      "",
    );

    const summarize = buttons().find(
      (button) => button.getAttribute("data-chat-start-suggestion") === "summarize",
    );
    assert.ok(summarize);
    act(() => summarize.click());

    assert.deepEqual(drafts, [CHAT_START_SUGGESTIONS[0].draft]);
    assert.equal(
      container.querySelector<HTMLTextAreaElement>("textarea")?.value,
      CHAT_START_SUGGESTIONS[0].draft,
    );
    // Filling a draft must not submit: no send happened, and the send control
    // is still the user's own explicit action.
    assert.deepEqual(sends, []);
  } finally {
    act(() => root.unmount());
    dom.close();
  }
});
