import assert from "node:assert/strict";
import test from "node:test";
import { act, createElement } from "react";
import { ThreadPrimitive } from "@assistant-ui/react";
import { installSelectTestDom } from "../../../common/__tests__/selectTestDom";

test("native message actions copy the supplied public answer, report success and support HTTP", async () => {
  const environment = installSelectTestDom();
  const { createLobeTestRoot } = await import("../../../common/__tests__/lobeUiTestRoot");
  const { AssistantUiTestProvider } = await import("./assistantUiTestProvider");
  const { AssistantUiMessageActions } = await import("../MessageActions");
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  const writes: string[] = [];
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
    writeText: async (value: string) => { writes.push(value); },
  } });
  const render = async (id: string, text: string) => {
    await act(async () => root.render(createElement(AssistantUiTestProvider, {
      messages: [{ id, role: "assistant", content: "Non-copyable preview", timestamp: new Date(0) }],
    }, createElement(ThreadPrimitive.Unstable_MessageById, {
      messageId: id,
      components: { Message: () => createElement(AssistantUiMessageActions, { key: id, messageId: id, text }) },
    }))));
  };
  try {
    await render("answer", "最终公开回答\n第二段");
    const button = container.querySelector<HTMLButtonElement>("button")!;
    await act(async () => button.click());
    assert.deepEqual(writes, ["最终公开回答\n第二段"]);
    assert.equal(button.getAttribute("data-copied"), "true");
    assert.equal(button.getAttribute("aria-label"), "已复制!");
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 2100)); });
    assert.equal(button.hasAttribute("data-copied"), false);
    await render("empty", "");
    assert.equal(container.querySelector("button"), null);
    await render("denied", "permission denied");
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async () => { throw new Error("denied"); } } });
    await act(async () => container.querySelector<HTMLButtonElement>("button")!.click());
    assert.equal(container.querySelector("[data-copied]"), null, "failed copy must not report success");
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    Object.defineProperty(document, "execCommand", { configurable: true, value: (command: string) => {
      assert.equal(command, "copy");
      writes.push(document.querySelector<HTMLTextAreaElement>("textarea")!.value);
      return true;
    } });
    await render("http", "HTTP 上的回答");
    await act(async () => container.querySelector<HTMLButtonElement>("button")!.click());
    assert.equal(writes.at(-1), "HTTP 上的回答");
    assert.equal(document.querySelector("textarea"), null);
  } finally {
    await act(async () => root.unmount());
    environment.close();
  }
});
