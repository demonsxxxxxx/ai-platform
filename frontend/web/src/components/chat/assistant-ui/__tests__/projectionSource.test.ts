import test from "node:test";
import assert from "node:assert/strict";
// jsdom 26 ships no declarations; the harness uses only its runtime constructor.
// @ts-expect-error jsdom is the sole pinned test runtime dependency.
import { JSDOM } from "jsdom";
import { act, createElement, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { ThreadPrimitive } from "@assistant-ui/react";
import { AssistantUiProjection } from "../AssistantUiProjection";
import { AssistantUiMessageFrame } from "../MessageFrame";
import type { Message, MessagePart } from "../../../../types";

function setupDom(): { container: HTMLDivElement; root: Root; cleanup: () => void } {
  const dom = new JSDOM("<!doctype html><html><body><div id=\"root\"></div></body></html>", {
    url: "http://localhost/",
  });
  const windowKeys = ["window", "document", "navigator", "HTMLElement", "Node", "Event", "KeyboardEvent"] as const;
  for (const key of windowKeys) {
    Object.defineProperty(globalThis, key, { configurable: true, value: dom.window[key] });
  }
  Object.defineProperty(globalThis, "IS_REACT_ACT_ENVIRONMENT", { configurable: true, value: true, writable: true });
  class TestResizeObserver {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  Object.defineProperty(globalThis, "ResizeObserver", { configurable: true, value: TestResizeObserver });
  Object.defineProperty(globalThis, "MutationObserver", { configurable: true, value: dom.window.MutationObserver });
  Object.defineProperty(dom.window.HTMLElement.prototype, "scrollTo", { configurable: true, value: () => undefined });
  Object.defineProperty(globalThis, "cancelAnimationFrame", { configurable: true, value: () => undefined });
  Object.defineProperty(globalThis, "requestAnimationFrame", {
    configurable: true,
    value: (callback: FrameRequestCallback) => { callback(Date.now()); return 0; },
  });
  const container = dom.window.document.getElementById("root") as HTMLDivElement;
  const root = createRoot(container);
  return {
    container,
    root,
    cleanup: () => {
      act(() => { root.unmount(); });
      dom.window.close();
    },
  };
}

function renderProjection(
  root: Root,
  message: Message,
  actions: { sendMessage: (content: string) => Promise<void>; cancel: () => Promise<void>; reconnect: () => Promise<void>; loadHistory: () => Promise<void> },
): void {
  const renderPart = (part: MessagePart): ReactNode => {
    if (part.type === "subagent") {
      return createElement(
        "div",
        {
          key: part.public_operation_id || part.agent_id,
          role: "group",
          "aria-label": `${part.agent_name} lifecycle`,
          "data-subagent-id": part.agent_id,
          "data-subagent-status": part.status,
        },
        createElement("span", { "data-status-label": true }, part.status === "complete" ? "Completed" : "Running"),
        part.current_category
          ? createElement("span", { "data-category": true }, `Category: ${part.current_category}`)
          : null,
      );
    }
    if (part.type === "tool") {
      return createElement(
        "button",
        {
          key: part.public_operation_id || part.id,
          type: "button",
          "aria-label": `Open ${part.name}`,
          "data-tool-operation": part.public_operation_id,
          onClick: () => void actions.sendMessage(`inspect:${part.public_operation_id}`),
          onKeyDown: (event: KeyboardEvent) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              void actions.sendMessage(`inspect:${part.public_operation_id}`);
            }
          },
        },
        part.name,
      );
    }
    return null;
  };

  act(() => {
    root.render(
      createElement(
        AssistantUiProjection,
        {
          messages: [message],
          isRunning: Boolean(message.isStreaming),
          actions,
          children: createElement(
            ThreadPrimitive.Viewport,
            null,
            createElement(
              ThreadPrimitive.Unstable_MessageById,
              {
                messageId: message.id,
                components: {
                  Message: () => createElement(
                    AssistantUiMessageFrame,
                    null,
                    createElement("p", { "data-message-content": true }, message.content),
                    ...(message.parts || []).map(renderPart),
                  ),
                },
              },
            ),
          ),
        },
      ),
    );
  });
}

function message(parts: MessagePart[]): Message {
  return {
    id: "message-1",
    runId: "run-1",
    role: "assistant",
    content: "visible answer",
    timestamp: new Date("2026-01-01T00:00:00Z"),
    parts,
  };
}

test("mounted projection delegates one tool action and remains keyboard accessible", async () => {
  const dom = setupDom();
  const calls: string[] = [];
  const actions = {
    sendMessage: async (content: string) => { calls.push(content); },
    cancel: async () => undefined,
    reconnect: async () => undefined,
    loadHistory: async () => undefined,
  };
  try {
    renderProjection(dom.root, message([{
      type: "tool",
      name: "Read file",
      args: { category: "read" },
      public_operation_id: "operation-read-1",
      public_category: "read",
      isPending: false,
    }]), actions);
    const projection = dom.container.querySelector("[data-assistant-ui-projection]");
    const frame = dom.container.querySelector("[data-assistant-ui-message]");
    const tool = dom.container.querySelector("[data-tool-operation=operation-read-1]") as HTMLButtonElement | null;
    assert.ok(projection);
    assert.ok(frame);
    assert.equal(projection?.getAttribute("role"), "log");
    assert.equal(projection?.getAttribute("aria-live"), "polite");
    assert.equal(frame?.getAttribute("role"), "group");
    assert.equal(frame?.getAttribute("tabindex"), "0");
    assert.equal(tool?.getAttribute("aria-label"), "Open Read file");
    assert.ok(tool);
    await act(async () => { tool.click(); });
    assert.deepEqual(calls, ["inspect:operation-read-1"]);
    await act(async () => {
      tool.dispatchEvent(new dom.container.ownerDocument.defaultView!.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    assert.deepEqual(calls, ["inspect:operation-read-1", "inspect:operation-read-1"]);
    tool.focus();
    assert.equal(dom.container.ownerDocument.activeElement, tool);
  } finally {
    dom.cleanup();
  }
});

test("mounted projection renders grouped subagent lifecycle with non-color state text", () => {
  const dom = setupDom();
  const actions = { sendMessage: async () => undefined, cancel: async () => undefined, reconnect: async () => undefined, loadHistory: async () => undefined };
  try {
    const started: MessagePart = {
      type: "subagent",
      agent_id: "subagent-1",
      public_operation_id: "operation-subagent-1",
      agent_name: "Research worker",
      input: "",
      depth: 1,
      status: "running",
      current_category: "search",
      progress_percent: 40,
    };
    renderProjection(dom.root, message([started]), actions);
    const lifecycle = dom.container.querySelector("[data-subagent-id=subagent-1]");
    assert.ok(lifecycle);
    assert.equal(lifecycle?.getAttribute("role"), "group");
    assert.equal(lifecycle?.textContent, "RunningCategory: search");
    assert.equal(lifecycle?.getAttribute("data-subagent-status"), "running");

    const completed = { ...started, status: "complete" as const, progress_percent: 100 };
    renderProjection(dom.root, message([completed]), actions);
    assert.equal(lifecycle?.getAttribute("data-subagent-status"), "running");
    const updated = dom.container.querySelector("[data-subagent-id=subagent-1]");
    assert.equal(updated?.getAttribute("data-subagent-status"), "complete");
    assert.equal(updated?.textContent, "CompletedCategory: search");
    assert.match(dom.container.textContent || "", /Completed/);
  } finally {
    dom.cleanup();
  }
});
