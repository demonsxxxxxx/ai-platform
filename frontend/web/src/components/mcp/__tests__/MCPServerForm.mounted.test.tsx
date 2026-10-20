import assert from "node:assert/strict";
import test, { mock } from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot";
import type { MCPServerCreate, MCPServerResponse } from "../../../types";

const dom = installSelectTestDom();
test.after(() => dom.close());
const { act, createElement } = await import("react");
const { MCPServerForm } = await import("../MCPServerForm");
const { capabilityDistributionApi } = await import("../../../services/api/capabilityDistribution");
const directory = ["101", "201"].map((id) => ({
  directoryId: id, authorityId: id, name: "研发", path: `公司 ${id} / 研发`,
  selectable: true, reason: null, children: [],
}));
test.beforeEach(() => { mock.method(capabilityDistributionApi, "departmentDirectory", async () => directory); });
test.afterEach(() => mock.restoreAll());
await import("../../../i18n");

for (const isSystemServer of [false, true]) {
  test(`MCP ${isSystemServer ? "system" : "regular"} form preserves legacy departments until explicit ID reselection`, async () => {
    const container = dom.document.createElement("div");
    dom.document.body.append(container);
    const root = await createLobeTestRoot(container);
    const saved: MCPServerCreate[] = [];
    const server: MCPServerResponse = {
      name: "scoped", transport: "streamable_http", enabled: true,
      url: "https://example.invalid/mcp", headers: { "X-Context": "synthetic" },
      is_system: isSystemServer, can_edit: true, allowed_roles: [], role_quotas: {},
      allowed_departments: ["研发"],
    };
    const submit = () => act(async () => {
      container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    try {
      await act(async () => { root.render(createElement(MCPServerForm, {
        server, isSystemServer, onSave: async (data) => { saved.push(data); return true; }, onCancel: () => {},
      })); });
      assert.match(container.textContent!, /未确认部门会保留显示/);
      await submit();
      assert.equal(saved.length, 0);
      await act(async () => { container.querySelector<HTMLButtonElement>('[aria-label="移除未确认部门 研发"]')!.click(); });
      const trigger = container.querySelector<HTMLButtonElement>('[data-department-directory-selector] [role="combobox"]')!;
      await act(async () => { trigger.click(); });
      const option = document.querySelector<HTMLElement>('[role="option"][aria-label="公司 101 / 研发"]');
      assert.ok(option);
      await act(async () => { option.click(); });
      await act(async () => {
        document.querySelector('[data-department-directory-popup]')!.dispatchEvent(
          new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }),
        );
      });
      assert.equal(trigger.getAttribute("aria-expanded"), "false");
      await submit();
      assert.deepEqual(saved[0].department_ids, ["101"]);
      assert.equal(saved[0].url, server.url);
      assert.deepEqual(saved[0].headers, server.headers);
    } finally {
      await act(async () => root.unmount()); container.remove();
    }
  });
}

test("MCP directory outage retains the unresolved grant and blocks nonempty submission", async (t) => {
  t.mock.method(capabilityDistributionApi, "departmentDirectory", async () => { throw new Error("private upstream"); });
  const container = dom.document.createElement("div");
  dom.document.body.append(container);
  const root = await createLobeTestRoot(container);
  const saved: MCPServerCreate[] = [];
  try {
    await act(async () => { root.render(createElement(MCPServerForm, {
      server: { name: "scoped", transport: "streamable_http", enabled: true, url: "https://example.invalid/mcp",
        is_system: false, can_edit: true, allowed_roles: [], role_quotas: {}, allowed_departments: ["101"] },
      onSave: async (data) => { saved.push(data); return true; }, onCancel: () => {},
    })); });
    assert.match(container.textContent!, /101/);
    assert.doesNotMatch(container.textContent!, /private upstream/);
    await act(async () => { container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    assert.equal(saved.length, 0);
  } finally {
    await act(async () => root.unmount()); container.remove();
  }
});

function change(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

for (const integrationKind of ["mcp", "enterprise_gateway"] as const) {
  test(`MCP form preserves ${integrationKind} on edit and validates its identity headers`, async () => {
    const container = dom.document.createElement("div");
    dom.document.body.append(container);
    const root = await createLobeTestRoot(container);
    const saved: MCPServerCreate[] = [];
    const server: MCPServerResponse = {
      name: "synthetic", transport: "streamable_http", enabled: true,
      integration_kind: integrationKind, is_system: false, can_edit: true,
      url: "https://example.invalid/mcp", headers: { "X-Context": "synthetic-existing" },
      allowed_roles: [], role_quotas: {},
    };
    const submit = () => act(async () => {
      container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    try {
      await act(async () => { root.render(createElement(MCPServerForm, {
        server, onSave: async (data) => { saved.push(data); return true; }, onCancel: () => {},
      })); });
      const enabled = container.querySelector<HTMLElement>('[role="checkbox"]')!;
      assert.equal(enabled.style.getPropertyValue("--lobe-checkbox-bg"), "var(--theme-success)");
      assert.equal(enabled.getAttribute("aria-checked"), "true");
      assert.equal(container.querySelector<HTMLInputElement>('input[type="url"]')!.value, server.url);
      assert.equal(container.querySelector<HTMLInputElement>('input[placeholder="头名称"]')!.value, "X-Context");
      assert.equal(container.querySelector<HTMLInputElement>('input[placeholder="值"]')!.value, "synthetic-existing");
      await submit();
      assert.equal(saved[0].integration_kind, integrationKind);
      assert.equal(saved[0].url, server.url);
      assert.deepEqual(saved[0].headers, server.headers);
      const add = Array.from(container.querySelectorAll("button")).find(button => button.textContent === "添加")!;
      await act(async () => add.click());
      await act(async () => {
        change(Array.from(container.querySelectorAll<HTMLInputElement>('input[placeholder="头名称"]')).at(-1)!, "Authorization");
        change(Array.from(container.querySelectorAll<HTMLInputElement>('input[placeholder="值"]')).at(-1)!, "Bearer synthetic");
      });
      await submit();
      if (integrationKind === "enterprise_gateway") {
        assert.equal(saved.length, 1);
        assert.match(container.textContent!, /请移除 Authorization/);
      } else {
        assert.equal(saved.length, 2);
        assert.equal(saved[1].headers?.Authorization, "Bearer synthetic");
      }
    } finally {
      await act(async () => root.unmount());
      container.remove();
    }
  });
}

test("MCP form replaces connection fields when switching servers and clears them for creation", async () => {
  const container = dom.document.createElement("div");
  dom.document.body.append(container);
  const root = await createLobeTestRoot(container);
  const render = (server: MCPServerResponse | null) => act(async () => {
    root.render(createElement(MCPServerForm, {
      server, onSave: async () => true, onCancel: () => {},
    }));
  });
  const server: MCPServerResponse = {
    name: "first", transport: "streamable_http", enabled: true,
    url: "https://first.example.invalid/mcp", headers: { "X-First": "first" },
    is_system: false, can_edit: true, allowed_roles: [], role_quotas: {},
  };
  try {
    await render(null);
    await render(server);
    assert.equal(container.querySelector<HTMLInputElement>('input[type="url"]')!.value, server.url);
    assert.equal(container.querySelector<HTMLInputElement>('input[placeholder="头名称"]')!.value, "X-First");
    await render({ ...server, name: "second", url: "https://second.example.invalid/mcp", headers: { "X-Second": "second" } });
    assert.equal(container.querySelector<HTMLInputElement>('input[type="url"]')!.value, "https://second.example.invalid/mcp");
    assert.equal(container.querySelectorAll('input[placeholder="头名称"]').length, 1);
    assert.equal(container.querySelector<HTMLInputElement>('input[placeholder="头名称"]')!.value, "X-Second");
    assert.equal(container.querySelector<HTMLInputElement>('input[placeholder="值"]')!.value, "second");
    await render(null);
    assert.equal(container.querySelector<HTMLInputElement>('input[type="url"]')!.value, "");
    assert.equal(container.querySelectorAll('input[placeholder="头名称"]').length, 0);
  } finally {
    await act(async () => root.unmount());
    container.remove();
  }
});
