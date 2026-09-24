import assert from "node:assert/strict";
import test from "node:test";

// jsdom is the pinned mounted-test runtime and does not ship declarations here.
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
function buttonByText(root: ParentNode, label: string): HTMLButtonElement {
  const button = Array.from(root.querySelectorAll("button")).find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  assert.ok(button, `Missing button: ${label}`);
  return button as HTMLButtonElement;
}

function setInputValue(
  window: JSDOM["window"],
  input: HTMLInputElement,
  value: string,
) {
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value",
  )?.set;
  assert.ok(setter);
  setter.call(input, value);
  input.dispatchEvent(new window.Event("input", { bubbles: true }));
}

test("personal file server authentication sends only the password through the authenticated backend path", async () => {
  const dom = new JSDOM(
    "<!doctype html><html><body><div id='root'></div></body></html>",
    { url: "http://localhost/plugins", pretendToBeVisual: true },
  );
  const originalNavigator = Object.getOwnPropertyDescriptor(
    globalThis,
    "navigator",
  );
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    HTMLElement: dom.window.HTMLElement,
    HTMLInputElement: dom.window.HTMLInputElement,
    Event: dom.window.Event,
    KeyboardEvent: dom.window.KeyboardEvent,
    MouseEvent: dom.window.MouseEvent,
    IS_REACT_ACT_ENVIRONMENT: true,
  });
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: dom.window.navigator,
  });
  const legacyInputEvents = dom.window.HTMLElement.prototype as unknown as {
    attachEvent?: () => void;
    detachEvent?: () => void;
  };
  legacyInputEvents.attachEvent = () => {};
  legacyInputEvents.detachEvent = () => {};

  const [{ act, createElement }, { createRoot }, { PluginMarketPanel }] =
    await Promise.all([
      import("react"),
      import("react-dom/client"),
      import("../PluginMarketPanel"),
    ]);
  const originalFetch = globalThis.fetch;
  const requests: Array<{ url: string; init: RequestInit }> = [];
  globalThis.fetch = (async (input, init = {}) => {
    const url = String(input);
    requests.push({ url, init });
    if (url.endsWith("/api/ai/auth/company-credential-handoff")) {
      return new Response(JSON.stringify({ credential: "handoff-jwt" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("/api/profile-drive/status")) {
      return new Response(
        JSON.stringify({
          status: "connected",
          connected: true,
          reauthRequired: false,
          connectedAtUtc: "2026-09-20T00:00:00Z",
          lastUsedAtUtc: "2026-09-20T00:00:00Z",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (url.endsWith("/api/profile-drive/connect")) {
      return new Response(
        JSON.stringify({
          status: "connected",
          connected: true,
          reauthRequired: false,
          connectedAtUtc: null,
          lastUsedAtUtc: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    throw new Error(`Unexpected request: ${url}`);
  }) as typeof fetch;

  const container = dom.window.document.getElementById("root");
  assert.ok(container);
  const root = createRoot(container);

  try {
    await act(async () => {
      root.render(createElement(PluginMarketPanel));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    assert.match(container.textContent ?? "", /本地文件服务器/);
    assert.match(container.textContent ?? "", /列出文件/);
    assert.match(container.textContent ?? "", /搜索文件/);
    assert.match(container.textContent ?? "", /读取文本/);
    assert.match(container.textContent ?? "", /已连接/);

    await act(async () => {
      buttonByText(container, "认证").dispatchEvent(
        new dom.window.MouseEvent("click", { bubbles: true }),
      );
    });

    const dialog = dom.window.document.querySelector('[role="dialog"]');
    assert.ok(dialog);
    const password = dialog.querySelector(
      "#file-server-password",
    ) as HTMLInputElement | null;
    const form = dialog.querySelector("form");
    assert.equal(dialog.querySelector("#file-server-account"), null);
    assert.ok(password);
    assert.ok(form);

    await act(async () => {
      form.dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
    });
    assert.match(dialog.textContent ?? "", /请输入企业账号密码/);

    await act(async () => {
      setInputValue(dom.window, password, "not-sent");
    });
    await act(async () => {
      form.dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    assert.equal(dom.window.document.querySelector('[role="dialog"]'), null);
    assert.match(container.textContent ?? "", /已连接/);
    assert.equal(requests.length, 4);
    const statusHandoffRequest = requests[0];
    assert.match(statusHandoffRequest.url, /company-credential-handoff$/);
    const statusRequest = requests[1];
    assert.match(statusRequest.url, /profile-drive\/status$/);
    assert.equal(statusRequest.init.credentials, "omit");
    assert.equal(
      new Headers(statusRequest.init.headers).get("Authorization"),
      "Bearer handoff-jwt",
    );
    const connectHandoffRequest = requests[2];
    assert.match(connectHandoffRequest.url, /company-credential-handoff$/);
    const connectRequest = requests[3];
    assert.match(connectRequest.url, /profile-drive\/connect$/);
    assert.equal(connectRequest.init.credentials, "omit");
    assert.equal(
      new Headers(connectRequest.init.headers).get("Authorization"),
      "Bearer handoff-jwt",
    );
    assert.deepEqual(JSON.parse(String(connectRequest.init.body)), {
      password: "not-sent",
    });
  } finally {
    await act(async () => root.unmount());
    globalThis.fetch = originalFetch;
    if (originalNavigator) {
      Object.defineProperty(globalThis, "navigator", originalNavigator);
    } else {
      delete (globalThis as { navigator?: Navigator }).navigator;
    }
    dom.window.close();
  }
});
