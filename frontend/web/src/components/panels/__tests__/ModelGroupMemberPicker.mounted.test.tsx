import assert from "node:assert/strict";
import test, { after } from "node:test";
import { createElement, act } from "react";
import { VirtuosoMockContext } from "react-virtuoso";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot";
import { companyEmployeesApi, type CompanyEmployee } from "../../../services/api/companyEmployees";
import { capabilityDistributionApi } from "../../../services/api/capabilityDistribution";
import type { AdminModelGroupMember } from "../../../services/api/modelGroups";

const dom = installSelectTestDom();
after(() => dom.close());
const sales = Array.from({ length: 103 }, (_, index) => ({ work_id: `W${index}`, display_name: `成员 ${index}`, department_ids: ["456"] }));
const outside = { work_id: "elsewhere", display_name: "其他部门成员", department_ids: ["789"] };
const member = (employee: CompanyEmployee): AdminModelGroupMember => ({ user_id: "", work_id: employee.work_id, display_name: employee.display_name, is_primary: true });

async function mount() {
  const { ModelGroupMemberPicker } = await import("../ModelGroupMemberPicker");
  const original = { list: companyEmployeesApi.list, departments: capabilityDistributionApi.departmentDirectory };
  const calls: Parameters<typeof companyEmployeesApi.list>[0][] = [];
  companyEmployeesApi.list = async (options = {}) => {
    calls.push(options);
    const query = options.search?.toLowerCase();
    const all = options.departmentId === "456" ? sales : [...sales, outside];
    const matches = query ? all.filter((item) => `${item.display_name} ${item.work_id}`.toLowerCase().includes(query)) : all;
    const offset = options.offset ?? 0;
    return { employees: matches.slice(offset, offset + 100), total: matches.length, offset, limit: 100, snapshot_id: "synthetic-snapshot" };
  };
  capabilityDistributionApi.departmentDirectory = async () => [{ directoryId: "123", authorityId: "123", name: "公司", path: "公司", selectable: true, reason: null,
    children: [{ directoryId: "456", authorityId: "456", name: "销售部", path: "公司 / 销售部", children: [], selectable: true, reason: null },
      { directoryId: "789", authorityId: "789", name: "质量部", path: "公司 / 质量部", children: [], selectable: true, reason: null }] }];
  const container = document.createElement("div");
  document.body.append(container);
  const root = await createLobeTestRoot(container);
  const initial = [member(sales[0]), member(outside)];
  let confirmed: AdminModelGroupMember[] | undefined;
  const render = async (disabled = false) => act(async () => root.render(createElement(VirtuosoMockContext.Provider, { value: { viewportHeight: 12000, itemHeight: 60 } }, createElement(ModelGroupMemberPicker, {
    members: initial, disabled, onConfirm: (value: AdminModelGroupMember[]) => { confirmed = value; },
  }))));
  await render();
  const click = async (label: string) => act(async () => {
    const target = [...document.querySelectorAll<HTMLElement>("button,[role=checkbox]")].find((item) => item.getAttribute("aria-label") === label || item.textContent === label);
    assert.ok(target, label); target.click();
  });
  const search = async (value: string) => act(async () => {
    const input = document.querySelector<HTMLInputElement>('input[aria-label="搜索公司员工"]'); assert.ok(input);
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const checkbox = (label: string) => document.querySelector<HTMLElement>(`[role=checkbox][aria-label="${label}"]`)!;
  return { calls, click, search, checkbox, render, get confirmed() { return confirmed; }, cleanup: async () => {
    await act(async () => root.unmount()); container.remove(); companyEmployeesApi.list = original.list; capabilityDistributionApi.departmentDirectory = original.departments;
  } };
}

test("department bulk selection spans every page, global search ignores department and cancel discards only popup drafts", async () => {
  const view = await mount();
  try {
    await view.click("按组织架构选择人员");
    await view.click("公司 / 销售部");
    assert.equal(document.querySelectorAll('[data-model-group-member-directory] [role=checkbox]').length, 103);
    assert.deepEqual(view.calls.filter((call) => call?.departmentId === "456").map((call) => call?.offset), [0, 100]);
    assert.equal(view.checkbox("全选本部门").getAttribute("aria-checked"), "mixed");
    await view.click("全选本部门");
    assert.equal(document.querySelectorAll('[data-model-group-member-directory] [role=checkbox][aria-checked=true]').length, 103);
    assert.match(document.querySelector('.model-member-picker-selected')!.textContent!, /已选 104 人/);
    await view.click("选择成员 成员 102");
    assert.equal(view.checkbox("全选本部门").getAttribute("aria-checked"), "mixed");
    await view.click("全选本部门");
    await view.click("全选本部门");
    assert.match(document.querySelector('.model-member-picker-selected')!.textContent!, /已选 1 人/);
    assert.ok(document.querySelector('[aria-label="移除已选 其他部门成员"]'));
    await view.search("elsewhere");
    assert.equal(view.calls.at(-1)?.departmentId, undefined);
    assert.equal(view.calls.at(-1)?.search, "elsewhere");
    assert.match(document.querySelector('[data-model-group-member-directory]')!.textContent!, /公司 \/ 质量部/);
    assert.equal(view.checkbox("全选搜索结果").getAttribute("aria-checked"), "true");
    await view.search("成员 10");
    await view.click("全选搜索结果");
    assert.match(document.querySelector('.model-member-picker-selected')!.textContent!, /已选 5 人/);
    await view.search("");
    assert.equal(view.calls.at(-1)?.departmentId, "456");
    assert.equal(view.checkbox("全选本部门").getAttribute("aria-checked"), "mixed");
    const popup = document.querySelector('[aria-label="公司组织架构选人"]')!;
    assert.doesNotMatch(popup.textContent!, /选择部门，可逐个勾选|仅直属人员|不授予部门权限/);
    await view.click("取消");
    assert.equal(Boolean(view.confirmed), false);
    await view.click("按组织架构选择人员");
    assert.match(document.querySelector('.model-member-picker-selected')!.textContent!, /已选 2 人/);
    await view.click("清空");
    const confirm = [...document.querySelectorAll<HTMLButtonElement>('button')].find((node) => node.textContent === "添加 0 人")!;
    assert.equal(confirm.disabled, false, 'empty drafts must reach the existing fallback confirmation');
    await view.click("添加 0 人");
    assert.equal(JSON.stringify(view.confirmed), "[]");
    await view.click("按组织架构选择人员");
    await view.click("清空");
    await view.click("选择成员 成员 1");
    await view.click("添加 1 人");
    assert.deepEqual(view.confirmed?.map((item) => item.work_id), ["W1"]);
  } finally { await view.cleanup(); }
});

test("partial pagination failure cannot bulk-select a partial roster and retries without losing selections", async () => {
  const view = await mount();
  const list = companyEmployeesApi.list;
  try {
    companyEmployeesApi.list = async (options) => {
      if (options?.offset === 100) throw new Error("synthetic-second-page-failure");
      return list(options);
    };
    await view.click("按组织架构选择人员");
    assert.match(document.body.textContent!, /公司员工目录加载失败/);
    assert.equal(view.checkbox("全选全部人员").getAttribute("aria-disabled"), "true");
    assert.equal(document.querySelectorAll('[data-model-group-member-directory] [role=checkbox]').length, 0);
    assert.match(document.querySelector('.model-member-picker-selected')!.textContent!, /已选 2 人/);
    companyEmployeesApi.list = list;
    await view.click("重试");
    assert.equal(document.querySelectorAll('[data-model-group-member-directory] [role=checkbox]').length, 104);
    await view.search("nothing-matches");
    assert.match(document.body.textContent!, /没有匹配的公司员工/);
    assert.equal(view.checkbox("全选搜索结果").getAttribute("aria-disabled"), "true");
  } finally { await view.cleanup(); }
});

test("changed snapshot identity rejects mixed pages even when their employee count is unchanged", async () => {
  const view = await mount();
  const list = companyEmployeesApi.list;
  try {
    companyEmployeesApi.list = async (options) => ({ ...await list(options), snapshot_id: options?.offset ? "changed-snapshot" : "initial-snapshot" });
    await view.click("按组织架构选择人员");
    assert.match(document.body.textContent!, /公司员工目录加载失败/);
    assert.equal(document.querySelectorAll('[data-model-group-member-directory] [role=checkbox]').length, 0);
    assert.equal(view.checkbox("全选全部人员").getAttribute("aria-disabled"), "true");
  } finally { await view.cleanup(); }
});

for (const invalidSnapshot of [undefined, "", 42]) test(`invalid first-page snapshot ${String(invalidSnapshot)} cannot enable selection`, async () => {
  const view = await mount();
  const list = companyEmployeesApi.list;
  try {
    companyEmployeesApi.list = async (options) => ({ ...await list(options), snapshot_id: invalidSnapshot as unknown as string });
    await view.click("按组织架构选择人员");
    assert.match(document.body.textContent!, /公司员工目录加载失败/);
    assert.equal(view.checkbox("全选全部人员").getAttribute("aria-disabled"), "true");
    assert.equal(document.querySelectorAll('[data-model-group-member-directory] [role=checkbox]').length, 0);
  } finally { await view.cleanup(); }
});

test("closing or disabling the picker aborts pending pages and late results cannot change the next scope", async () => {
  const view = await mount();
  const list = companyEmployeesApi.list;
  let oldSignal: AbortSignal | undefined;
  let resolveOld!: (value: Awaited<ReturnType<typeof list>>) => void;
  try {
    companyEmployeesApi.list = (options) => { oldSignal = options?.signal; return new Promise((resolve) => { resolveOld = resolve; }); };
    await view.click("按组织架构选择人员");
    await view.search("later-query");
    const pendingSignal = oldSignal;
    await view.click("关闭选人弹窗");
    assert.equal(pendingSignal?.aborted, true);
    companyEmployeesApi.list = list;
    await view.click("按组织架构选择人员");
    await act(async () => resolveOld({ employees: [{ work_id: "late", display_name: "旧结果" }], offset: 0, total: 1, limit: 100, snapshot_id: "late" }));
    assert.doesNotMatch(document.querySelector('[data-model-group-member-directory]')!.textContent!, /旧结果/);
    companyEmployeesApi.list = (options) => { oldSignal = options?.signal; return new Promise(() => {}); };
    await view.search("pending");
    await view.render(true);
    assert.equal(oldSignal?.aborted, true);
    assert.equal(document.querySelector('[aria-label="按组织架构选择人员"]')!.hasAttribute('disabled'), true);
  } finally { await view.cleanup(); }
});
