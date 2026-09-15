import assert from "node:assert/strict";
import test from "node:test";
// jsdom 26 ships no declarations; this test uses only its runtime constructor.
// @ts-expect-error jsdom is the pinned mounted-test runtime.
import { JSDOM } from "jsdom";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import type { MessagePart } from "../../../../types";
import { MessagePartRenderer } from "../../ChatMessage/MessagePartRenderer";
import { MessageWorkActivity } from "../../ChatMessage/MessageWorkActivity";
import {
  closePersistentToolPanel,
  getPersistentToolPanelState,
} from "../../ChatMessage/items/persistentToolPanelState";

function activateNativeButton(
  button: HTMLButtonElement,
  key: "Enter" | " ",
): void {
  button.focus();
  const event = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
  });
  const shouldRunDefault = button.dispatchEvent(event);
  assert.equal(shouldRunDefault, true);
  button.click();
}

test("chat work disclosure collapses on completion and keeps answer content outside", () => {
  const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
    url: "http://localhost/",
  });
  for (const key of [
    "window",
    "document",
    "navigator",
    "HTMLElement",
    "Node",
    "Event",
    "KeyboardEvent",
  ] as const) {
    Object.defineProperty(globalThis, key, {
      configurable: true,
      value: dom.window[key],
    });
  }
  Object.defineProperty(globalThis, "IS_REACT_ACT_ENVIRONMENT", {
    configurable: true,
    value: true,
  });
  const container = dom.window.document.getElementById("root") as HTMLDivElement;
  const root = createRoot(container);
  const thinking: Extract<MessagePart, { type: "thinking" }> = {
    type: "thinking",
    content: "公开思考摘要",
    thinking_id: "thinking-public-1",
    public_reasoning: true,
    isStreaming: true,
  };
  const renderMessage = (isStreaming: boolean, parts: MessagePart[]) =>
    createElement(MessageWorkActivity, {
      messageId: "message-work-details",
      isStreaming,
      parts,
      partKeys: parts.map((part, index) => `${part.type}:${index}`),
      renderPart: (part, index, withinWorkDetails) =>
        createElement(MessagePartRenderer, {
          part,
          messageId: "message-work-details",
          partIndex: index,
          isStreaming,
          isLast: index === parts.length - 1,
          withinWorkDetails,
        }),
    });

  try {
    act(() => {
      root.render(renderMessage(true, [thinking]));
    });
    let toggle = container.querySelector(
      "[data-message-work-details-toggle]",
    ) as HTMLButtonElement;
    assert.equal(toggle.getAttribute("aria-expanded"), "true");
    assert.equal(toggle.hasAttribute("aria-label"), false);
    assert.match(toggle.textContent || "", /工作中.*全部收起/s);
    assert.equal(
      container.querySelector("[data-public-thinking] button")?.getAttribute("aria-expanded"),
      null,
    );

    act(() => {
      root.render(
        renderMessage(false, [
          { ...thinking, isStreaming: false },
          { type: "text", content: "最终正文保持可见" },
          {
            type: "tool",
            id: "tool-public-1",
            name: "读取已授权文件",
            args: {},
            status: "completed",
            success: true,
            isPending: false,
            public_operation_id: "operation-public-1",
            public_category: "read",
          },
        ]),
      );
    });
    toggle = container.querySelector(
      "[data-message-work-details-toggle]",
    ) as HTMLButtonElement;
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
    const controlledIds = (toggle.getAttribute("aria-controls") || "")
      .split(" ")
      .filter(Boolean);
    assert.equal(controlledIds.length, 2);
    controlledIds.forEach((id) => {
      assert.equal(dom.window.document.getElementById(id)?.hidden, true);
    });
    assert.equal(
      container.querySelector("[data-public-thinking] button")?.getAttribute("aria-expanded"),
      null,
    );
    const answer = [...container.querySelectorAll("p")].find((node) =>
      node.textContent?.includes("最终正文保持可见"),
    );
    assert.ok(answer);
    assert.equal(answer.closest("[hidden]"), null);

    act(() => activateNativeButton(toggle, "Enter"));
    assert.equal(toggle.getAttribute("aria-expanded"), "true");
    controlledIds.forEach((id) => {
      assert.equal(dom.window.document.getElementById(id)?.hidden, false);
    });
    const thinkingButton = container.querySelector(
      "[data-public-thinking] button",
    ) as HTMLButtonElement;
    act(() => activateNativeButton(thinkingButton, " "));
    assert.equal(thinkingButton.getAttribute("aria-expanded"), null);
    assert.doesNotMatch(container.textContent || "", /公开思考摘要/);
  } finally {
    closePersistentToolPanel();
    act(() => root.unmount());
    dom.window.close();
  }
});

test("generic public tools expose distinct safe failed and denied states", () => {
  const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
    url: "http://localhost/",
  });
  for (const key of [
    "window",
    "document",
    "navigator",
    "HTMLElement",
    "Node",
    "Event",
    "KeyboardEvent",
  ] as const) {
    Object.defineProperty(globalThis, key, {
      configurable: true,
      value: dom.window[key],
    });
  }
  Object.defineProperty(globalThis, "IS_REACT_ACT_ENVIRONMENT", {
    configurable: true,
    value: true,
  });
  const container = dom.window.document.getElementById("root") as HTMLDivElement;
  const root = createRoot(container);
  const parts: MessagePart[] = [
    {
      type: "tool",
      id: "operation-failed",
      name: "Read authorized files",
      args: { category: "read" },
      status: "failed",
      success: false,
      isPending: false,
      error: "private-failure-token",
      public_operation_id: "operation-failed",
    },
    {
      type: "tool",
      id: "operation-denied",
      name: "Execute approved command",
      args: { category: "execute" },
      status: "denied",
      success: false,
      isPending: false,
      error: "private-denial-token",
      public_operation_id: "operation-denied",
    },
  ];

  try {
    act(() => {
      root.render(
        createElement(
          "div",
          null,
          parts.map((part, index) =>
            createElement(MessagePartRenderer, {
              key: part.type === "tool" ? part.id : index,
              part,
              messageId: "reducer-message",
              partIndex: index,
              isLast: index === parts.length - 1,
            }),
          ),
        ),
      );
    });

    const buttons = [...container.querySelectorAll("button")];
    assert.equal(buttons.length, 2);
    assert.equal(buttons[0]?.tagName, "BUTTON");
    assert.equal(buttons[1]?.tagName, "BUTTON");
    assert.equal(buttons[0]?.getAttribute("aria-expanded"), "false");
    assert.equal(buttons[1]?.getAttribute("aria-expanded"), "false");
    assert.ok(buttons[0]?.querySelector(".lucide-circle-x"));
    assert.ok(buttons[1]?.querySelector(".lucide-ban"));

    const statuses = [...container.querySelectorAll('[role="status"]')];
    assert.equal(statuses.length >= 2, true);
    const labels = statuses
      .map((status) => status.getAttribute("aria-label"))
      .filter((label): label is string => Boolean(label));
    assert.equal(
      new Set(labels).size >= 2,
      true,
      statuses.map((status) => status.outerHTML).join("\n"),
    );
    assert.doesNotMatch(
      `${container.textContent || ""}|${labels.join("|")}`,
      /private-failure-token|private-denial-token|operation-failed|operation-denied/,
    );

    activateNativeButton(buttons[0] as HTMLButtonElement, "Enter");
    assert.ok(getPersistentToolPanelState());
    closePersistentToolPanel();
    activateNativeButton(buttons[1] as HTMLButtonElement, " ");
    assert.ok(getPersistentToolPanelState());
    closePersistentToolPanel();
  } finally {
    closePersistentToolPanel();
    act(() => root.unmount());
    dom.window.close();
  }
});
