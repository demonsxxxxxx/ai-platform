import assert from "node:assert/strict";
import test from "node:test";
// jsdom 26 ships no declarations; this test uses only its runtime constructor.
// @ts-expect-error jsdom is the pinned mounted-test runtime.
import { JSDOM } from "jsdom";
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import type { MessagePart } from "../../../../types";
import { MessagePartRenderer } from "../../ChatMessage/MessagePartRenderer";
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
