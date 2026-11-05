import assert from "node:assert/strict";
import test, { after } from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot";
import { installModelCatalogTestProvider } from "../../../contexts/__tests__/modelCatalogTestProvider";
import { modelAdminApi, type AdminModelProvider, type ModelProviderImpact } from "../../../services/api/modelAdmin";
import { modelGroupsApi } from "../../../services/api/modelGroups";
const dom = installSelectTestDom();
after(() => dom.close());
const React = await import("react");
const { toast } = await import("@lobehub/ui/base-ui");
const { ModelProviderManagement } = await import("../ModelProviderManagement");
const noop = () => {};
const button = (label: string) => {
  const node = [...document.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.trim() === label);
  const action = node ?? [...document.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.getAttribute("aria-label")?.startsWith(label));
  assert.ok(action, label); return action;
};
async function click(label: string) { await React.act(async () => button(label).click()); }
async function select(label: string) { await click(`供应商 ${label}`); }
function input(label: string, value: string) {
  const field = document.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!;
  assert.ok(field);
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(field, value);
  field.dispatchEvent(new Event("input", { bubbles: true }));
}

test("suppliers create/select isolated drafts, confirm discard, block referenced deletion and retire unused supplier", async () => {
  const original = { ...modelAdminApi }; const groups = modelGroupsApi.listAdmin;
  const originalToast = toast.success;
  const notices: string[] = [];
  toast.success = (message) => { notices.push(String(message)); return { id: "provider-notice", close: noop, update: noop }; };
  const restoreCatalog = installModelCatalogTestProvider();
  let providers: AdminModelProvider[] = [{ id: "a", name: "Supplier A", revision: 1, model_count: 1, configured: true, enabled_model_count: 1, missing_model_count: 0 }, { id: "b", name: "Supplier B", revision: 2, model_count: 1, configured: true, enabled_model_count: 0, missing_model_count: 1 }];
  const calls: string[] = []; let blocked = true;
  let deferInventory = false;
  let resolveInventory: (() => void) | undefined;
  let staleEditorSignal: AbortSignal | undefined;
  let deletedB = false;
  modelGroupsApi.listAdmin = async () => ({ groups: [], version: "model-groups.v1" });
  modelAdminApi.get = async (options = {}) => {
    calls.push(`get:${options.providerId ?? "all"}`);
    if (deferInventory && !options.providerId) await new Promise<void>((resolve) => { resolveInventory = resolve; });
    if (deletedB && options.providerId === "b") { staleEditorSignal = options.signal; await new Promise<void>((resolve) => options.signal?.addEventListener("abort", () => resolve(), { once: true })); }
    const p = providers.find((p) => p.id === options.providerId) ?? providers[0];
    return { providers, connection: { configured: p?.revision != null, revision: p?.revision ?? null, base_url: "https://same.example", key_fingerprint: "opaque" }, models: p ? [{ id: `${p.id}-model`, value: "shared-model", label: "Shared", provider: "compatible", provider_id: p.id, enabled: false, available: true, order: 1, last_seen_revision: p.revision ?? 0, last_seen_at: "2026-01-01" }] : [] };
  };
  modelAdminApi.createProvider = async (name) => { providers = [...providers, { id: "c", name, revision: null, model_count: 0 }]; return { id: "c", name }; };
  modelAdminApi.providerImpact = async (id, options) => ({ ...providers.find((p) => p.id === id)!, group_count: blocked ? 2 : 0, default_group_count: blocked ? 1 : 0, active_run_count: blocked ? 1 : 0, cleanable_count: options?.missingOnly ? 26 : blocked ? 0 : 1, blocked_models: options?.missingOnly ? [{ id: "missing", label: "Missing model", groups: [{ name: "Test group" }], group_count: 1, default_group_count: 0, active_run_count: 0 }] : [] }) as ModelProviderImpact;
  modelAdminApi.retireProvider = async (id, revision, options) => { if (options?.missingOnly) { calls.push(`cleanup:${id}:${revision}`); return { id, deleted: true, model_count: 26, retained_count: 1 }; } calls.push(`delete:${id}:${revision}`); if (id === "b") deletedB = true; providers = providers.filter((p) => p.id !== id); return { id, deleted: true }; };
  const container = document.createElement("div"); document.body.append(container);
  const root = await createLobeTestRoot(container);
  try {
    await React.act(async () => root.render(restoreCatalog.wrap(<ModelProviderManagement groupRevision={0} onGroupsChanged={noop} onStateChange={noop} />)));
    assert.ok(!calls.includes("get:a"), "initial supplier rows do not load an editor");
    assert.equal(container.querySelectorAll('[data-provider-expanded]').length, 0);
    assert.match(container.querySelector('[data-model-provider-row="a"] [data-provider-summary]')?.textContent ?? "", /连接已保存.*1 模型.*启用 1/);
    assert.match(container.querySelector('[data-model-provider-row="b"] [data-provider-summary]')?.textContent ?? "", /启用 0.*上游缺失 1/);
    assert.doesNotMatch(container.querySelector('[data-provider-summary]')?.textContent ?? "", /测试通过/);
    await select("Supplier A");
    assert.ok(calls.includes("get:a"));
    await select("Supplier A");
    assert.equal(container.querySelectorAll('[data-provider-expanded]').length, 0);
    await select("Supplier A");
    await React.act(async () => input("模型 API Key", "synthetic-a-draft"));
    await select("Supplier B");
    assert.match(document.querySelector('[role=dialog]')?.textContent ?? "", /未保存/);
    await click("放弃修改并切换");
    assert.ok(calls.includes("get:b"));
    assert.equal(document.querySelector<HTMLInputElement>('input[aria-label="模型 API Key"]')?.value, "");
    await React.act(async () => container.querySelector<HTMLButtonElement>('[data-model-provider-row="b"] [data-provider-header] button:not([aria-label])')!.click());
    assert.match(document.querySelector('[role=dialog]')?.textContent ?? "", /26 个无引用.*1 个被引用/);
    assert.match(document.querySelector('[role=dialog]')?.textContent ?? "", /Missing model：分组 Test group/);
    await click("取消");
    await React.act(async () => container.querySelector<HTMLButtonElement>('[data-model-provider-row="b"] [data-provider-header] button:not([aria-label])')!.click());
    await click("确认清理");
    assert.ok(calls.includes("cleanup:b:2"));
    assert.ok(notices.includes("已清理 26 个模型，保留 1 个被引用模型。"));
    assert.doesNotMatch(container.textContent ?? "", /已清理 26 个模型/);
    await click("删除供应商 Supplier B");
    assert.match(container.querySelector('[data-provider-delete-blocked]')?.textContent ?? "", /2 个分组、1 个默认模型，1 个未完成任务/);
    assert.ok(!calls.some((c) => c.startsWith("delete:")));
    await select("Supplier A");
    assert.equal(container.querySelectorAll('[data-provider-expanded]').length, 1);
    assert.equal(container.querySelector('[data-provider-delete-blocked]'), null, "switching clears the former supplier's delete impact");
    assert.ok(!calls.some((c) => c.startsWith("delete:")));
    await select("Supplier B");
    blocked = false;
    deferInventory = true;
    await click("删除供应商 Supplier B");
    await React.act(async () => document.querySelector<HTMLButtonElement>('[role=dialog] button:last-child')!.click());
    assert.ok(resolveInventory);
    assert.equal(container.querySelectorAll('[data-provider-expanded]').length, 0, "deleted editor closes before slow inventory resolves");
    assert.equal(container.querySelector('[aria-label="模型 API 地址"]'), null, "deleted connection cannot start another request");
    assert.equal(staleEditorSignal, undefined, "deleting must not remount an editor before inventory reload");
    deferInventory = false;
    await React.act(async () => resolveInventory!());
    assert.ok(calls.includes("delete:b:2"));
    assert.ok(notices.includes("已删除供应商 Supplier B。"));
    assert.doesNotMatch(container.textContent ?? "", /已删除供应商/);
    assert.equal(button("新增供应商").disabled, false, "buttons recover after deleting the expanded supplier");
    assert.equal(container.querySelectorAll('[data-provider-expanded]').length, 0, "deleting a supplier does not expand another");
    await click("删除供应商 Supplier A");
    await React.act(async () => document.querySelector<HTMLButtonElement>('[role=dialog] button:last-child')!.click());
    assert.ok(calls.includes("delete:a:1"), "collapsed row deletes its own supplier");
    assert.equal(container.querySelectorAll('[data-model-provider-row]').length, 0);
    assert.equal(button("新增供应商").disabled, false);
    assert.match(container.textContent ?? "", /暂无供应商/);
    await click("新增供应商");
    await React.act(async () => input("供应商名称", "Supplier C"));
    await click("创建供应商");
    assert.ok(calls.includes("get:c"));
    assert.match(container.querySelector('[data-model-provider-row="c"] [data-provider-summary]')?.textContent ?? "", /待配置连接/);
    assert.equal(document.querySelector<HTMLInputElement>('input[aria-label="模型 API Key"]')?.value, "");
  } finally { await React.act(async () => root.unmount()); container.remove(); Object.assign(modelAdminApi, original); modelGroupsApi.listAdmin = groups; toast.success = originalToast; restoreCatalog.cleanup(); }
});
