import assert from "node:assert/strict";
import test from "node:test";

import { installSelectTestDom } from "../../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../../common/__tests__/lobeUiTestRoot";
import { act, createElement } from "react";

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

async function setupDom() {
  const environment = installSelectTestDom();
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  return { container, root, cleanup: () => {
    act(() => root.unmount());
    environment.close();
  } };
}

test("keeps reconnect pending state fenced to the current owner and attempt", async () => {
  const dom = await setupDom();
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
