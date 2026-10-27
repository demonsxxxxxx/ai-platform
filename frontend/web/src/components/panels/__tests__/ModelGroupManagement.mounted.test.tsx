import assert from "node:assert/strict";
import test, { after } from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom.ts";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot.tsx";
import { modelGroupsApi, type AdminModelGroupDetails, type AdminModelGroupSummary } from "../../../services/api/modelGroups.ts";
import { modelAdminApi } from "../../../services/api/modelAdmin.ts";
import { companyEmployeesApi } from "../../../services/api/companyEmployees.ts";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb.ts";
import { installModelCatalogTestProvider } from "../../../contexts/__tests__/modelCatalogTestProvider.tsx";

const dom = installSelectTestDom();
installBrowserAuthTestDb();
after(() => dom.close());

const summary: AdminModelGroupSummary = {
  id: "group-sales", code: "sales", name: "销售组", type: "business", enabled: true,
  revision: 4, remark: "", sort_order: 1, model_count: 1, member_count: 0,
};
const details: AdminModelGroupDetails = { ...summary, members: [], models: [] };

async function mount(initialDetails = details, shell = false) {
  const React = await import("react");
  const { ModelGroupManagement } = await import("../ModelGroupManagement.tsx");
  const { ModelCatalogPanel } = await import("../ModelCatalogPanel.tsx");
  const original = { ...modelGroupsApi };
  const catalog = shell ? installModelCatalogTestProvider() : null;
  const originalModelGet = modelAdminApi.get;
  const originalEmployees = companyEmployeesApi.list;
  const calls: Array<{ kind: string; body?: unknown }> = [];
  modelGroupsApi.listAdmin = async () => ({ groups: [summary], version: "model-groups.v1" });
  modelGroupsApi.details = async () => initialDetails;
  modelGroupsApi.create = async (payload) => { calls.push({ kind: "create", body: payload }); return summary; };
  modelGroupsApi.update = async (_id, payload) => { calls.push({ kind: "update", body: payload }); return summary; };
  modelGroupsApi.setModels = async (_id, payload) => { calls.push({ kind: "models", body: payload }); return { group_id: summary.id, revision: 5 }; };
  modelGroupsApi.setMembers = async (_id, payload) => { calls.push({ kind: "members", body: payload }); return { group_id: summary.id, revision: 5 }; };
  modelAdminApi.get = async () => ({ connection: { configured: true, revision: 4, base_url: "", key_fingerprint: "" }, models: [{ id: "model-a", value: "model-a", label: "模型 A", provider: "test", enabled: true, available: true, order: 1, last_seen_revision: 4, last_seen_at: "2026-01-01T00:00:00Z" }] });
  companyEmployeesApi.list = async () => ({ employees: [{ work_id: "work-a", display_name: "员工 A" }], total: 1, offset: 0, limit: 100 });
  const container = dom.document.createElement("div");
  const root = await createLobeTestRoot(container);
  await React.act(async () => root.render(shell
    ? catalog!.wrap(React.createElement(ModelCatalogPanel))
    : React.createElement(ModelGroupManagement)));
  for (let index = 0; index < 20 && !container.querySelector("[data-model-group-table]"); index += 1) await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  return { React, container, root, calls, restore: () => { catalog?.cleanup(); Object.assign(modelGroupsApi, original); modelAdminApi.get = originalModelGet; companyEmployeesApi.list = originalEmployees; } };
}

test("group management loads, creates, opens drawer and saves model/member lists through atomic endpoints", async () => {
  const view = await mount();
  try {
    assert.match(view.container.textContent ?? "", /销售组/);
    await view.React.act(async () => {
      const createButton = [...view.container.querySelectorAll("button")].find((button) => button.textContent?.includes("新建业务组"));
      assert.ok(createButton); createButton.click();
    });
    const code = view.container.querySelector<HTMLInputElement>('input[aria-label="新分组 code"]');
    assert.ok(code);
    await view.React.act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(code, "support");
      code.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const name = view.container.querySelector<HTMLInputElement>('input[aria-label="新分组名称"]');
    assert.ok(name);
    await view.React.act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(name, "支持组");
      name.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await view.React.act(async () => { [...view.container.querySelectorAll("button")].find((button) => button.textContent === "创建")?.click(); });
    assert.equal(view.calls[0]?.kind, "create");
    await view.React.act(async () => { [...view.container.querySelectorAll("button")].find((button) => button.textContent?.includes("销售组"))?.click(); });
    for (let index = 0; index < 20 && !document.querySelector("[data-model-group-drawer]"); index += 1) await view.React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    await view.React.act(async () => { (document.querySelector('[role="tab"][data-value="models"]') as HTMLElement | null
      ?? [...document.querySelectorAll<HTMLElement>('[role="tab"]')].find((tab) => tab.textContent === "模型"))?.click(); });
    assert.ok(document.querySelector("[data-model-group-models]"));
    const modelCheckbox = document.querySelector('[data-model-group-models] [role="checkbox"]') as HTMLElement | null;
    assert.ok(modelCheckbox);
    await view.React.act(async () => modelCheckbox.click());
    const saveModels = [...document.querySelectorAll("button")].find((button) => button.textContent === "保存模型");
    assert.ok(saveModels);
    await view.React.act(async () => saveModels.click());
    assert.equal(view.calls.some((call) => call.kind === "models"), true);
    await view.React.act(async () => { [...document.querySelectorAll<HTMLElement>('[role="tab"]')].find((tab) => tab.textContent === "成员")?.click(); });
    assert.ok(document.querySelector("[data-model-group-members]"));
    const employeeSearch = document.querySelector<HTMLInputElement>('input[aria-label="搜索公司员工"]');
    assert.ok(employeeSearch);
    await view.React.act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(employeeSearch, "员工");
      employeeSearch.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await view.React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    const employee = document.querySelector('[data-model-group-member-directory] [role="checkbox"]') as HTMLElement | null;
    assert.ok(employee);
    await view.React.act(async () => employee.click());
    const saveMembers = [...document.querySelectorAll("button")].find((button) => button.textContent === "保存成员");
    assert.ok(saveMembers);
    await view.React.act(async () => saveMembers.click());
    assert.equal(view.calls.some((call) => call.kind === "members"), true);
  } finally {
    await view.React.act(async () => view.root.unmount());
    view.restore();
  }
});


test("drawer keeps drafts across tabs, reorders models and preserves each member's primary", async () => {
  const view = await mount({ ...summary, member_count: 2, members: [
    { user_id: "a", display_name: "甲", is_primary: true },
    { user_id: "b", display_name: "乙", is_primary: false },
  ], models: ["a", "b"].map((id, index) => ({ model_id: id, model_value: id, label: id, enabled: true, display_order: index, priority: 0, weight: 100 })) });
  const click = async (label: string) => view.React.act(async () => {
    const button = [...document.querySelectorAll<HTMLElement>("button")].find((b) => b.textContent === label || b.getAttribute("aria-label") === label);
    assert.ok(button, label); button.click();
  });
  try {
    await click("编辑 销售组");
    await click("模型");
    await click("上移 b");
    assert.deepEqual([...document.querySelectorAll("[data-model-group-model]")].map((node) => node.getAttribute("data-model-group-model")), ["b", "a"]);
    await click("成员");
    await click("将本组设为 乙 的主组");
    await click("基本信息");
    await click("模型");
    await click("保存模型");
    const saved = view.calls.find((call) => call.kind === "models")?.body as { models: { model_id: string }[] };
    assert.deepEqual(saved.models.map((model) => model.model_id), ["b", "a"]);
    await click("成员");
    await click("保存成员");
    const members = view.calls.find((call) => call.kind === "members")?.body as { members: { user_id: string; is_primary: boolean }[] };
    assert.deepEqual(members.members, [{ user_id: "a", is_primary: true }, { user_id: "b", is_primary: true }]);
    await click("移除成员 甲");
    await click("保存成员");
    assert.equal(view.calls.filter((call) => call.kind === "members").length, 1);
    assert.match(document.body.textContent ?? "", /失去对话资格/);
    await click("确认移除");
    const removal = view.calls.filter((call) => call.kind === "members")[1].body as { confirm_default_fallback: boolean };
    assert.equal(removal.confirm_default_fallback, true);
  } finally {
    await view.React.act(async () => view.root.unmount()); view.restore();
  }
});


test("model catalog tabs keep unsaved model names mounted", async () => {
  const view = await mount(details, true);
  try {
    for(let i=0; i<40 && !view.container.querySelector('input[aria-label="model-a 平台显示名称"]'); i++) {
      await view.React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    }
    const field = view.container.querySelector<HTMLInputElement>('input[aria-label="model-a 平台显示名称"]');
    assert.ok(field);
    await view.React.act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(field, "未保存名称");
      field.dispatchEvent(new Event("input", {bubbles:true}));
    });
    for(const value of ["groups", "catalog"]) {
      await view.React.act(async () => view.container.querySelector<HTMLElement>('[data-model-admin-tab="'+value+'"]')?.click());
    }
    assert.equal(view.container.querySelector<HTMLInputElement>('input[aria-label="model-a 平台显示名称"]')?.value, "未保存名称");
  } finally { await view.React.act(async () => view.root.unmount()); view.restore(); }
});
