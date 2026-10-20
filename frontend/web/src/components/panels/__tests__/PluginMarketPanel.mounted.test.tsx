import assert from "node:assert/strict";
import test from "node:test";

import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import {
  PROFILE_DRIVE_CONNECTION_CHANGED_EVENT,
} from "../../../services/api/profileDrive";
function buttonByText(root: ParentNode, label: string): HTMLButtonElement {
  const button = Array.from(root.querySelectorAll("button")).find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  assert.ok(button, `Missing button: ${label}`);
  return button as HTMLButtonElement;
}

function setInputValue(
  window: typeof globalThis.window,
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
  const fixture = installSelectTestDom();
  const dom = { window };
  document.body.innerHTML = '<div id="root"></div>';
  let connectionChanged = 0;
  window.addEventListener(PROFILE_DRIVE_CONNECTION_CHANGED_EVENT, () => { connectionChanged += 1; });

  const [{ act, createElement }, { createLobeTestRoot }, { PluginMarketPanel }] =
    await Promise.all([
      import("react"),
      import("../../common/__tests__/lobeUiTestRoot"),
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
  const root = await createLobeTestRoot(container);

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
    assert.ok(container.contains(dialog), "dialog must stay in the themed app portal");
    assert.equal(password.type, "password");
    const field = password.parentElement;
    assert.ok(field);
    assert.ok(field.querySelector("svg"), "lock prefix belongs to the Input");
    const toggle = field.querySelector('button[aria-label="显示密码"]');
    assert.ok(toggle, "visibility action belongs to the Input suffix");
    await act(async () => { toggle.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true })); });
    assert.equal(password.type, "text");
    assert.ok(field.querySelector('button[aria-label="隐藏密码"]'));
    await act(async () => { toggle.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true })); });
    assert.equal(password.type, "password");

    await act(async () => {
      form.dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
    });
    assert.match(dialog.textContent ?? "", /请输入企业账号密码/);
    assert.equal(password.getAttribute("aria-invalid"), "true");
    assert.equal(password.getAttribute("aria-describedby"), "file-server-password-error");

    await act(async () => {
      setInputValue(dom.window, password, "not-sent");
    });
    await act(async () => {
      form.dispatchEvent(new dom.window.Event("submit", { bubbles: true, cancelable: true }));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
    assert.equal(dom.window.document.querySelector('[role="dialog"]'), null);
    assert.equal(connectionChanged, 1);
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
    const trigger = buttonByText(container, "认证");
    assert.equal(document.activeElement, trigger);
    await act(async () => { trigger.click(); });
    const reopenedPassword = document.querySelector<HTMLInputElement>("#file-server-password");
    assert.ok(reopenedPassword);
    assert.equal(reopenedPassword.value, "");
    assert.equal(reopenedPassword.type, "password");
    await act(async () => { buttonByText(document, "取消").click(); });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 300)); });
    assert.equal(document.querySelector('[role="dialog"]'), null);
    assert.equal(document.activeElement, trigger);
    assert.equal(requests.length, 4);
  } finally {
    await act(async () => root.unmount());
    globalThis.fetch = originalFetch;
    fixture.close();
  }
});
