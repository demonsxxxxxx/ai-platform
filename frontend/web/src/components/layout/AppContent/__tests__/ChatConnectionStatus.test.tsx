import assert from "node:assert/strict";
import test from "node:test";

// jsdom 26 ships no declarations; this mounted harness uses its runtime only.
// @ts-expect-error jsdom is the pinned browser test runtime.
import { JSDOM } from "jsdom";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";

import { ChatConnectionStatus } from "../ChatConnectionStatus.tsx";

function deferred(): {
  promise: Promise<void>;
  resolve: () => void;
} {
  let resolve!: () => void;
  const promise = new Promise<void>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

function setupDom(): {
  container: HTMLDivElement;
  root: Root;
  cleanup: () => void;
} {
  const dom = new JSDOM(
    '<!doctype html><html><body><div id="root"></div></body></html>',
    { url: "http://localhost/" },
  );
  for (const key of [
    "window",
    "document",
    "navigator",
    "HTMLElement",
    "Node",
    "Event",
    "MouseEvent",
  ] as const) {
    Object.defineProperty(globalThis, key, {
      configurable: true,
      value: dom.window[key],
    });
  }
  Object.defineProperty(globalThis, "IS_REACT_ACT_ENVIRONMENT", {
    configurable: true,
    value: true,
    writable: true,
  });
  const container = dom.window.document.getElementById("root") as HTMLDivElement;
  const root = createRoot(container);
  return {
    container,
    root,
    cleanup: () => {
      act(() => root.unmount());
      dom.window.close();
    },
  };
}

test("keeps reconnect pending state fenced to the current owner and attempt", async () => {
  const dom = setupDom();
  const first = deferred();
  const second = deferred();
  const calls: string[] = [];

  const renderDisconnected = (owner: string, flight: Promise<void>) => {
    act(() => {
      dom.root.render(
        createElement(ChatConnectionStatus, {
          status: "disconnected",
          owner,
          label: "实时更新已断开",
          reconnectLabel: "重新连接",
          reconnectingLabel: "正在连接…",
          onReconnect: () => {
            calls.push(owner);
            return flight;
          },
        }),
      );
    });
  };

  try {
    renderDisconnected("session-a:run-a", first.promise);
    const firstButton = dom.container.querySelector("button");
    assert.ok(firstButton);
    assert.equal(firstButton.disabled, false);
    assert.equal(
      dom.container.querySelector('[role="status"]')?.getAttribute("aria-live"),
      "polite",
    );

    await act(async () => {
      firstButton.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    assert.deepEqual(calls, ["session-a:run-a"]);
    assert.equal(dom.container.querySelector("button")?.hasAttribute("disabled"), true);

    renderDisconnected("session-b:run-b", second.promise);
    const secondButton = dom.container.querySelector("button");
    assert.ok(secondButton);
    assert.equal(secondButton.disabled, false);
    await act(async () => {
      secondButton.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    assert.deepEqual(calls, ["session-a:run-a", "session-b:run-b"]);
    assert.equal(dom.container.querySelector("button")?.hasAttribute("disabled"), true);

    await act(async () => first.resolve());
    assert.equal(dom.container.querySelector("button")?.hasAttribute("disabled"), true);

    await act(async () => second.resolve());
    assert.equal(dom.container.querySelector("button")?.hasAttribute("disabled"), false);

    act(() => {
      dom.root.render(
        createElement(ChatConnectionStatus, {
          status: "recovering_gap",
          owner: "session-b:run-b",
          label: "正在校准",
          reconnectLabel: "重新连接",
          reconnectingLabel: "正在连接…",
          onReconnect: async () => undefined,
        }),
      );
    });
    assert.equal(dom.container.querySelector("button"), null);
    assert.equal(
      dom.container
        .querySelector('[role="status"]')
        ?.getAttribute("aria-busy"),
      "true",
    );
  } finally {
    dom.cleanup();
  }
});
