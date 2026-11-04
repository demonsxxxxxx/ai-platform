import assert from "node:assert/strict";
import test from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import type { MCPServerCreate } from "../../../types";
const dom = installSelectTestDom();
const { act, createElement } = await import("react");
const { createLobeTestRoot } = await import("../../common/__tests__/lobeUiTestRoot");
const { capabilityDistributionApi } = await import("../../../services/api/capabilityDistribution");
const { MCPServerForm } = await import("../MCPServerForm");
await import("../../../i18n");

test("mounted registration validates selected field and saves, reloads and revokes public declaration", async () => {
  const container = dom.document.createElement("div");
  dom.document.body.append(container);
  const originalDirectory = capabilityDistributionApi.departmentDirectory;
  capabilityDistributionApi.departmentDirectory = async () => [];
  const root = await createLobeTestRoot(container);
  const saved: MCPServerCreate[] = [];
  const onSave = async (data: MCPServerCreate) => { saved.push(data); return true; };
  const change = async (selector: string, value: string) => {
    const input = container.querySelector<HTMLInputElement>(selector)!;
    assert.ok(input, selector);
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  };
  const submit = async () => { await act(async () => { container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); }); };
  try {
    await act(async () => root.render(createElement(MCPServerForm, { onSave, onCancel: () => {} })));
    await change('input[type="text"]', "vendor");
    await change('input[type="url"]', "https://example.invalid/mcp");
    await act(async () => { container.querySelector<HTMLButtonElement>("[data-public-operation-summaries] button")!.click(); });
    await change('[aria-label="工具名 1"]', "create_project");
    await change('[aria-label="动作名称 1"]', "创建项目");
    await change('[aria-label="业务对象名称 1"]', "项目名称");
    await change('[aria-label="参数字段路径 1"]', "project.password");
    await submit();
    assert.equal(saved.length, 0);
    assert.match(container.querySelector('[role="alert"]')!.textContent!, /非敏感/);
    await change('[aria-label="参数字段路径 1"]', "project.name");
    await submit();
    assert.equal(saved.length, 1);
    assert.deepEqual(saved[0].public_operation_summaries, { create_project: { action_label: "创建项目", object_label: "项目名称", object_path: ["project", "name"] } });
    await act(async () => root.render(createElement(MCPServerForm, { onSave, onCancel: () => {}, server: {
      ...saved[0], enabled: true, is_system: false, can_edit: true, allowed_roles: [], role_quotas: {},
    } })));
    assert.equal(container.querySelector<HTMLInputElement>('[aria-label="参数字段路径 1"]')!.value, "project.name");
    await change('input[type="url"]', "https://example.invalid/mcp");
    await act(async () => { container.querySelector<HTMLButtonElement>('[aria-label="移除公开摘要 1"]')!.click(); });
    await submit();
    assert.deepEqual(saved[1].public_operation_summaries, {});
  } finally {
    await act(async () => root.unmount());
    container.remove();
    capabilityDistributionApi.departmentDirectory = originalDirectory;
  }
});
