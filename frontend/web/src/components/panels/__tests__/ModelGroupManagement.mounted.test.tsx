import assert from "node:assert/strict";
import test, { after } from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom.ts";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot.tsx";
import { modelGroupsApi, type AdminModelGroupDetails, type AdminModelGroupSummary } from "../../../services/api/modelGroups.ts";
import { modelAdminApi } from "../../../services/api/modelAdmin.ts";
import { companyEmployeesApi } from "../../../services/api/companyEmployees.ts";
import { capabilityDistributionApi } from "../../../services/api/capabilityDistribution.ts";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb.ts";
import { installModelCatalogTestProvider } from "../../../contexts/__tests__/modelCatalogTestProvider.tsx";

const dom = installSelectTestDom();
installBrowserAuthTestDb();
after(() => dom.close());

const summary: AdminModelGroupSummary = {
  id: "group-sales", code: "sales", name: "销售组", type: "business", enabled: true,
  revision: 4, remark: "", sort_order: 1, model_count: 1, member_count: 0,
};
const details: AdminModelGroupDetails = { ...summary, members: [], models: [], default_model_id: null };

async function mount(initialDetails = details, shell = false) {
  const React = await import("react");
  const { ModelGroupManagement } = await import("../ModelGroupManagement.tsx");
  const { ModelCatalogPanel } = await import("../ModelCatalogPanel.tsx");
  const original = { ...modelGroupsApi };
  const catalog = shell ? installModelCatalogTestProvider() : null;
  const originalModelGet = modelAdminApi.get;
  const originalEmployees = companyEmployeesApi.list;
  const originalDepartments = capabilityDistributionApi.departmentDirectory;
  const calls: Array<{ kind: string; body?: unknown }> = [];
  modelGroupsApi.listAdmin = async () => ({ groups: [initialDetails], version: "model-groups.v1" });
  modelGroupsApi.details = async () => initialDetails;
  modelGroupsApi.create = async (payload) => { calls.push({ kind: "create", body: payload }); return summary; };
  modelGroupsApi.update = async (_id, payload) => { calls.push({ kind: "update", body: payload }); return summary; };
  modelGroupsApi.setModels = async (_id, payload) => { calls.push({ kind: "models", body: payload }); return { group_id: summary.id, revision: 5 }; };
  modelGroupsApi.setMembers = async (_id, payload) => { calls.push({ kind: "members", body: payload }); return { group_id: summary.id, revision: 5 }; };
  modelAdminApi.get = async () => ({ connection: { configured: true, revision: 4, base_url: "", key_fingerprint: "" }, models: [{ id: "model-a", value: "model-a", label: "模型 A", provider: "test", enabled: true, available: true, order: 1, last_seen_revision: 4, last_seen_at: "2026-01-01T00:00:00Z" }] });
  companyEmployeesApi.list = async (options) => { calls.push({ kind: "employees", body: options }); return { employees: [{ work_id: "work-a", display_name: "员工 A" }], total: 1, offset: 0, limit: 100 }; };
  capabilityDistributionApi.departmentDirectory = async () => [{ directoryId: "123", authorityId: "123", name: "总公司", path: "总公司", selectable: true, reason: null, children: [{ directoryId: "456", authorityId: "456", name: "销售部", path: "总公司 / 销售部", children: [], selectable: true, reason: null }] }];
  const container = dom.document.createElement("div");
  const root = await createLobeTestRoot(container);
  await React.act(async () => root.render(shell
    ? catalog!.wrap(React.createElement(ModelCatalogPanel))
    : React.createElement(ModelGroupManagement)));
  for (let index = 0; index < 20 && !container.querySelector("[data-model-group-table]"); index += 1) await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  return { React, container, root, calls, restore: () => { catalog?.cleanup(); Object.assign(modelGroupsApi, original); modelAdminApi.get = originalModelGet; companyEmployeesApi.list = originalEmployees; capabilityDistributionApi.departmentDirectory = originalDepartments; } };
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
    await view.React.act(async () => document.querySelector<HTMLElement>('[data-model-group-add-models] summary')?.click());
    const modelCheckbox = document.querySelector('[data-model-group-models] [role="checkbox"]') as HTMLElement | null;
    assert.ok(modelCheckbox);
    await view.React.act(async () => modelCheckbox.click());
    const saveModels = [...document.querySelectorAll("button")].find((button) => button.textContent === "保存模型");
    assert.ok(saveModels);
    await view.React.act(async () => saveModels.click());
    assert.equal(view.calls.some((call) => call.kind === "models"), true);
    await view.React.act(async () => { [...document.querySelectorAll<HTMLElement>('[role="tab"]')].find((tab) => tab.textContent === "成员")?.click(); });
    assert.ok(document.querySelector("[data-model-group-members]"));
    const picker = document.querySelector<HTMLElement>('[aria-label="按组织架构选择人员"]');
    assert.ok(picker);
    await view.React.act(async () => picker.click());
    const department = document.querySelector<HTMLElement>('[aria-label="公司组织架构选人"] [role="combobox"]');
    assert.ok(department);
    await view.React.act(async () => department.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })));
    await view.React.act(async () => document.activeElement!.dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true })));
    await view.React.act(async () => document.activeElement!.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
    assert.equal((view.calls.filter((call) => call.kind === "employees").at(-1)?.body as { departmentId: string }).departmentId, "456");
    assert.match(document.body.textContent ?? "", /总公司 \/ 销售部/);
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
    await view.React.act(async () => document.querySelector<HTMLElement>('[aria-label="关闭选人下拉框"]')!.click());
    const saveMembers = [...document.querySelectorAll("button")].find((button) => button.textContent === "保存成员");
    assert.ok(saveMembers);
    await view.React.act(async () => saveMembers.click());
    assert.equal(view.calls.some((call) => call.kind === "members"), true);
  } finally {
    await view.React.act(async () => view.root.unmount());
    view.restore();
  }
});


test("organization picker ignores late employee results and preserves selections on directory failure", async () => {
  const view = await mount();
  const click = async (label: string) => view.React.act(async () => {
    const button = [...document.querySelectorAll<HTMLElement>("button")].find((node) => node.textContent === label || node.getAttribute("aria-label") === label);
    assert.ok(button, label); button.click();
  });
  const initialEmployees = companyEmployeesApi.list;
  let resolveOld: ((value: Awaited<ReturnType<typeof companyEmployeesApi.list>>) => void) | undefined;
  let oldSignal: AbortSignal | undefined;
  try {
    await click("编辑 销售组"); await click("成员");
    companyEmployeesApi.list = (options) => { oldSignal = options?.signal; return new Promise((resolve) => { resolveOld = resolve; }); };
    await click("按组织架构选择人员");
    companyEmployeesApi.list = initialEmployees;
    const search = document.querySelector<HTMLInputElement>('input[aria-label="搜索公司员工"]');
    assert.ok(search);
    await view.React.act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(search, "员工 A");
      search.dispatchEvent(new Event("input", { bubbles: true }));
    });
    assert.equal(oldSignal?.aborted, true);
    await view.React.act(async () => resolveOld!({ employees: [{ work_id: "late", display_name: "旧结果" }], total: 1, offset: 0, limit: 100 }));
    assert.doesNotMatch(document.querySelector("[data-model-group-member-directory]")?.textContent ?? "", /旧结果/);
    await view.React.act(async () => document.querySelector<HTMLElement>('[aria-label="选择成员 员工 A"]')!.click());
    await click("关闭选人下拉框");
    companyEmployeesApi.list = async () => { throw new Error("公司员工目录暂时不可用"); };
    await click("按组织架构选择人员");
    assert.match(document.body.textContent ?? "", /公司员工目录暂时不可用/);
    assert.equal(document.querySelectorAll("[data-model-group-selected-member]").length, 1);
    companyEmployeesApi.list = initialEmployees;
    await click("重试");
    assert.match(document.querySelector("[data-model-group-member-directory]")?.textContent ?? "", /员工 A/);
  } finally { await view.React.act(async () => view.root.unmount()); view.restore(); }
});


test("drawer keeps drafts across tabs, reorders models and saves single-group members", async () => {
  const view = await mount({ ...summary, member_count: 2, default_model_id: "a", members: [
    { user_id: "a", display_name: "甲", is_primary: true },
    { user_id: "b", display_name: "乙", is_primary: true },
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
    const radio = document.querySelector<HTMLInputElement>('input[aria-label="设为默认模型 b"]');
    assert.ok(radio);
    await view.React.act(async () => radio.click());
    await click("成员");
    assert.doesNotMatch(document.body.textContent ?? "", /设为主组/);
    await click("基本信息");
    await click("模型");
    await click("保存模型");
    const saved = view.calls.find((call) => call.kind === "models")?.body as { models: { model_id: string }[]; default_model_id: string };
    assert.deepEqual(saved.models.map((model) => model.model_id), ["b", "a"]);
    assert.equal(saved.default_model_id, "b");
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


test("default group explains computed users and keeps model assignment available", async () => {
  const view = await mount({ ...details, id: "group-default", code: "default", name: "普通默认组",
    type: "ordinary_default", member_count: 32, members_are_implicit: true });
  const click = async (label: string) => view.React.act(async () => {
    const button = [...document.querySelectorAll<HTMLElement>("button")].find((b) => b.textContent === label || b.getAttribute("aria-label") === label);
    assert.ok(button, label); button.click();
  });
  try {
    assert.match(view.container.textContent ?? "", /32自动归属/);
    await click("编辑 普通默认组");
    await click("模型");
    const drawer = document.querySelector("[data-model-group-drawer]");
    assert.ok(drawer);
    assert.match(drawer.textContent ?? "", /自动归属 32 人/);
    assert.match(drawer.textContent ?? "", /不是钉钉全员或历史登录人数/);
    assert.match(drawer.textContent ?? "", /暂无已加入模型/);
    assert.equal([...drawer.querySelectorAll('[role="tab"]')].some((tab) => tab.textContent === "成员"), false);
    assert.equal(drawer.querySelector('[aria-label="搜索公司员工"]'), null);
    const addModels = drawer.querySelector<HTMLDetailsElement>("[data-model-group-add-models]");
    assert.ok(addModels);
    assert.equal(addModels.open, false);
    await view.React.act(async () => addModels.querySelector("summary")!.click());
    const checkbox = addModels.querySelector<HTMLElement>('[role="checkbox"]');
    assert.ok(checkbox);
    await view.React.act(async () => checkbox.click());
    assert.equal(drawer.querySelectorAll("[data-model-group-model]").length, 1);
    await click("保存模型");
    assert.equal(view.calls.some((call) => call.kind === "models"), true);
  } finally { await view.React.act(async () => view.root.unmount()); view.restore(); }
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
