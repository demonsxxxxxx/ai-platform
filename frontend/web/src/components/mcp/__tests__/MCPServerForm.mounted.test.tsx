import assert from "node:assert/strict";
import test from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot";
import type { MCPServerCreate, MCPServerResponse } from "../../../types";

const dom = installSelectTestDom();
test.after(() => dom.close());
const { act, createElement } = await import("react");
const { MCPServerForm } = await import("../MCPServerForm");
await import("../../../i18n");

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
      allowed_roles: [], role_quotas: {},
    };
    const submit = () => act(async () => {
      container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    try {
      await act(async () => { root.render(createElement(MCPServerForm, {
        server, onSave: async (data) => { saved.push(data); return true; }, onCancel: () => {},
      })); });
      await act(async () => change(container.querySelector<HTMLInputElement>('input[type="url"]')!, "https://example.invalid/mcp"));
      await submit();
      assert.equal(saved[0].integration_kind, integrationKind);
      const add = Array.from(container.querySelectorAll("button")).find(button => button.textContent === "添加")!;
      await act(async () => add.click());
      await act(async () => {
        change(container.querySelector<HTMLInputElement>('input[placeholder="头名称"]')!, "Authorization");
        change(container.querySelector<HTMLInputElement>('input[placeholder="值"]')!, "Bearer synthetic");
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
