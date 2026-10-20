import assert from "node:assert/strict";
import test from "node:test";
import { act, createElement, useState } from "react";
import { installSelectTestDom } from "../../../common/__tests__/selectTestDom";
import type { Message } from "../../../../types";
import type { FollowUpSuggestionsResponse } from "../../../../services/api/session";

const SESSION = "session-follow-up";
const ANSWER: Message = {
  id: "message-answer",
  role: "assistant",
  content: "已整理任务清单。",
  timestamp: new Date(0),
  runId: "run-answer",
  isStreaming: false,
};

function rows(container: Element) {
  return container.querySelectorAll("[data-follow-up-suggestion]");
}

test("generated questions appear once for a settled answer and one click sends them", async () => {
  const dom = installSelectTestDom();
  const { createLobeTestRoot } = await import("../../../common/__tests__/lobeUiTestRoot");
  const { AssistantUiTestProvider } = await import("./assistantUiTestProvider");
  const { useFollowUpSuggestions } = await import("../useFollowUpSuggestions");
  const { FollowUpSuggestionsGate } = await import("../FollowUpSuggestionsGate");
  const { useTestComposerRegistration } = await import("./testComposerRegistration");
  const { sessionApi } = await import("../../../../services/api/session");

  const original = sessionApi.getFollowUpSuggestions;
  const requests: { runId: string; signal?: AbortSignal }[] = [];
  let resolveResponse: ((value: FollowUpSuggestionsResponse) => void) | undefined;
  sessionApi.getFollowUpSuggestions = async (sessionId, runId, signal) => {
    assert.equal(sessionId, SESSION);
    requests.push({ runId, signal });
    return new Promise<FollowUpSuggestionsResponse>((resolve, reject) => {
      resolveResponse = resolve;
      signal?.addEventListener("abort", () => reject(new Error("aborted")));
    });
  };

  const sent: string[] = [];
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);

  // The registrar lives inside the provider, exactly like ChatInput under the app.
  function ComposerRegistrar() {
    useTestComposerRegistration({ onSend: (content) => sent.push(content) });
    return null;
  }

  function Harness({ messages }: { messages: Message[] }) {
    const suggestions = useFollowUpSuggestions("tenant-a:user-a", SESSION, messages, true);
    return createElement(
      AssistantUiTestProvider,
      { messages, suggestions },
      createElement(ComposerRegistrar, { key: "registrar" }),
      createElement(FollowUpSuggestionsGate, { key: "gate" }),
    );
  }

  const render = async (messages: Message[]) =>
    act(async () => root.render(createElement(Harness, { messages })));

  try {
    // Observed streaming here, so this answer completed in this view.
    await render([{ ...ANSWER, isStreaming: true }]);
    await render([ANSWER]);
    assert.equal(requests.length, 1, "exactly one request for the settled answer");
    assert.equal(requests[0].runId, "run-answer");
    assert.equal(rows(container).length, 0, "nothing shown before the model answers");

    await act(async () => {
      resolveResponse?.({
        suggestions: ["还能补充验收标准吗？", "能给出时间表吗？", "  ", "还能补充验收标准吗？", "第四条"],
      });
    });
    assert.equal(rows(container).length, 3, "at most three short, distinct questions");
    assert.match(container.textContent ?? "", /后续建议/);

    await render([ANSWER]);
    assert.equal(requests.length, 1, "re-rendering the same answer never re-requests");

    await act(async () => {
      (rows(container)[0] as HTMLElement).click();
    });
    assert.deepEqual(sent, ["还能补充验收标准吗？"], "the click uses the one send owner");

    // The next turn owns no suggestions, so the previous set is gone.
    await render([
      ANSWER,
      { ...ANSWER, id: "user-next", role: "user", content: "补充验收标准", timestamp: new Date(1) },
    ]);
    assert.equal(rows(container).length, 0);
    assert.equal(requests.length, 1);
  } finally {
    sessionApi.getFollowUpSuggestions = original;
    root.unmount();
    dom.close();
  }
});

test("an account switch aborts the previous receipt and never shows it", async () => {
  const dom = installSelectTestDom();
  const { createLobeTestRoot } = await import("../../../common/__tests__/lobeUiTestRoot");
  const { AssistantUiTestProvider } = await import("./assistantUiTestProvider");
  const { useFollowUpSuggestions } = await import("../useFollowUpSuggestions");
  const { FollowUpSuggestionsGate } = await import("../FollowUpSuggestionsGate");
  const { useTestComposerRegistration } = await import("./testComposerRegistration");
  const { sessionApi } = await import("../../../../services/api/session");

  const original = sessionApi.getFollowUpSuggestions;
  const requests: { signal?: AbortSignal; resolve: (value: FollowUpSuggestionsResponse) => void }[] = [];
  sessionApi.getFollowUpSuggestions = async (_sessionId, _runId, signal) =>
    new Promise<FollowUpSuggestionsResponse>((resolve, reject) => {
      requests.push({ signal, resolve });
      signal?.addEventListener("abort", () => reject(new Error("aborted")));
    });

  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  let change!: (scope: string, streaming: boolean) => void;

  function ComposerRegistrar() {
    useTestComposerRegistration();
    return null;
  }

  function Harness() {
    const [{ scope, streaming }, update] = useState({ scope: "tenant-a:user-a", streaming: true });
    change = (nextScope, nextStreaming) => update({ scope: nextScope, streaming: nextStreaming });
    const messages = streaming ? [{ ...ANSWER, isStreaming: true }] : [ANSWER];
    const suggestions = useFollowUpSuggestions(scope, SESSION, messages, true);
    return createElement(
      AssistantUiTestProvider,
      { messages, suggestions },
      createElement(ComposerRegistrar, { key: "registrar" }),
      createElement(FollowUpSuggestionsGate, { key: "gate" }),
    );
  }

  try {
    await act(async () => root.render(createElement(Harness)));
    await act(async () => change("tenant-a:user-a", false));
    assert.equal(requests.length, 1);

    await act(async () => change("tenant-a:user-b", false));
    assert.equal(requests[0].signal?.aborted, true, "the previous owner's request is aborted");

    // The old owner's late answer must not appear under the new account.
    await act(async () => requests[0].resolve({ suggestions: ["旧账号的建议？"] }));
    assert.equal(rows(container).length, 0);
  } finally {
    sessionApi.getFollowUpSuggestions = original;
    root.unmount();
    dom.close();
  }
});

test("restored history never requests suggestions", async () => {
  const dom = installSelectTestDom();
  const { createLobeTestRoot } = await import("../../../common/__tests__/lobeUiTestRoot");
  const { AssistantUiTestProvider } = await import("./assistantUiTestProvider");
  const { useFollowUpSuggestions } = await import("../useFollowUpSuggestions");
  const { FollowUpSuggestionsGate } = await import("../FollowUpSuggestionsGate");
  const { useTestComposerRegistration } = await import("./testComposerRegistration");
  const { sessionApi } = await import("../../../../services/api/session");

  const original = sessionApi.getFollowUpSuggestions;
  let requests = 0;
  sessionApi.getFollowUpSuggestions = async () => {
    requests += 1;
    return { suggestions: ["不应出现的建议？"] };
  };

  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  function ComposerRegistrar() {
    useTestComposerRegistration();
    return null;
  }

  function Harness() {
    const suggestions = useFollowUpSuggestions("tenant-a:user-a", SESSION, [ANSWER], true);
    return createElement(
      AssistantUiTestProvider,
      { messages: [ANSWER], suggestions },
      createElement(ComposerRegistrar, { key: "registrar" }),
      createElement(FollowUpSuggestionsGate, { key: "gate" }),
    );
  }

  try {
    await act(async () => root.render(createElement(Harness)));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 40)); });
    assert.equal(requests, 0, "history restore is not a completion in this view");
    assert.equal(rows(container).length, 0);
  } finally {
    sessionApi.getFollowUpSuggestions = original;
    root.unmount();
    dom.close();
  }
});

test("a failed generation leaves the settled answer usable and unlabelled", async () => {
  const dom = installSelectTestDom();
  const { createLobeTestRoot } = await import("../../../common/__tests__/lobeUiTestRoot");
  const { AssistantUiTestProvider } = await import("./assistantUiTestProvider");
  const { useFollowUpSuggestions } = await import("../useFollowUpSuggestions");
  const { FollowUpSuggestionsGate } = await import("../FollowUpSuggestionsGate");
  const { useTestComposerRegistration } = await import("./testComposerRegistration");
  const { sessionApi } = await import("../../../../services/api/session");

  const original = sessionApi.getFollowUpSuggestions;
  let requests = 0;
  sessionApi.getFollowUpSuggestions = async () => {
    requests += 1;
    throw new Error("synthetic upstream refused");
  };

  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  let finish!: () => void;

  function ComposerRegistrar() {
    useTestComposerRegistration();
    return null;
  }

  function Harness() {
    const [messages, update] = useState<Message[]>([{ ...ANSWER, isStreaming: true }]);
    finish = () => update([ANSWER]);
    const suggestions = useFollowUpSuggestions("tenant-a:user-a", SESSION, messages, true);
    return createElement(
      AssistantUiTestProvider,
      { messages, suggestions },
      createElement(ComposerRegistrar, { key: "registrar" }),
      createElement(FollowUpSuggestionsGate, { key: "gate" }),
    );
  }

  try {
    await act(async () => root.render(createElement(Harness)));
    await act(async () => finish());
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 40)); });

    assert.equal(requests, 1);
    assert.equal(rows(container).length, 0);
    assert.equal(container.querySelector("[data-follow-up-suggestions-host]"), null);
  } finally {
    sessionApi.getFollowUpSuggestions = original;
    root.unmount();
    dom.close();
  }
});


test("the row appears only with a settled, sendable, empty composer", async () => {
  const { followUpSuggestionsVisible } = await import("../followUpSuggestions");
  const visible = {
    suggestionCount: 1,
    isRunning: false,
    isSendDisabled: false,
    draft: "",
    attachmentCount: 0,
  };

  assert.equal(followUpSuggestionsVisible(visible), true);
  // Whitespace-only text is not a draft, so it does not block the row.
  assert.equal(followUpSuggestionsVisible({ ...visible, draft: "   " }), true);
  for (const hidden of [
    { ...visible, suggestionCount: 0 },
    { ...visible, isRunning: true },
    { ...visible, isSendDisabled: true },
    { ...visible, draft: "我正在输入其他内容" },
    { ...visible, attachmentCount: 1 },
  ]) {
    assert.equal(followUpSuggestionsVisible(hidden), false, JSON.stringify(hidden));
  }
});
