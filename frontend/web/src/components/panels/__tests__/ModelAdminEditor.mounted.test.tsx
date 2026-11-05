import assert from "node:assert/strict";
import test, { after } from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom.ts";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot.tsx";
import { installModelCatalogTestProvider } from "../../../contexts/__tests__/modelCatalogTestProvider.tsx";
import { modelAdminApi, type AdminModelState } from "../../../services/api/modelAdmin.ts";
import { modelGroupsApi } from "../../../services/api/modelGroups.ts";

const dom = installSelectTestDom();
after(() => dom.close());

test("model editor stages edits, cancellation restores them, and publish sends the draft", async () => {
  const React = await import("react");
  const { ModelAdminControl } = await import("../ModelAdminControl.tsx");
  const saved: AdminModelState = { connection: { configured: true, revision: 4, base_url: "https://gateway.example", key_fingerprint: "synthetic" }, models: [{ id: "m1", value: "demo/model", label: "模型一", provider: "compatible", provider_name: "内部模型服务", enabled: true, available: true, order: 1, last_seen_revision: 4, last_seen_at: "2026-01-01", auto_compact_window: 200000, max_output_tokens: 2048 }] };
  const original = { ...modelAdminApi }; const list = modelGroupsApi.listAdmin;
  let published: Parameters<typeof modelAdminApi.publish>[3] | undefined;
  modelAdminApi.get = async () => saved;
  modelAdminApi.publish = async (_url, _key, revision, models) => { assert.equal(revision, 4); published = models; return { ...saved, models }; };
  modelGroupsApi.listAdmin = async () => ({ groups: [], version: "model-groups.v1" });
  const container = dom.document.createElement("div"); const root = await createLobeTestRoot(container); const catalog = installModelCatalogTestProvider();
  const click = async (selector: string) => { const node = dom.document.querySelector<HTMLElement>(selector); assert.ok(node, selector); await React.act(async () => { node.click(); }); };
  const change = async (value: string) => { const input = dom.document.querySelector<HTMLInputElement>('[aria-label="demo/model 压缩窗口 Token"]'); assert.ok(input); await React.act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value); input.dispatchEvent(new Event("input", { bubbles: true })); }); };
  try {
    await React.act(async () => { root.render(catalog.wrap(React.createElement(ModelAdminControl))); });
    assert.equal(container.querySelector("h1, h2"), null);
    assert.equal(container.querySelector("[data-model-admin-reload]"), null);
    assert.equal(container.querySelector("[data-model-admin-discover]")?.textContent, "获取上游模型");
    assert.equal(container.querySelector("[data-model-admin-action-bar]"), null);
    assert.equal(container.querySelector("[data-model-admin-model='m1'] [data-model-admin-draft-state]")?.textContent, "已保存");
    assert.match(container.textContent ?? "", /内部模型服务/);
    assert.doesNotMatch(container.textContent ?? "", /提供方|compatible/);
    assert.equal(container.querySelector('[aria-label="demo/model 压缩窗口 Token"]'), null);
    await click('[data-model-admin-edit="m1"]');
    assert.ok(dom.document.querySelector("[data-model-admin-editor]"));
    await change("150000");
    assert.match(container.textContent ?? "", /待保存 1 项/);
    assert.equal(container.querySelector("[data-model-admin-model='m1'] [data-model-admin-draft-state]")?.textContent, "待保存修改");
    await click('[aria-label="关闭模型编辑"]');
    await click('[data-model-admin-cancel]');
    assert.equal(container.querySelector("[data-model-admin-action-bar]"), null);
    assert.equal(container.querySelector("[data-model-admin-model='m1'] [data-model-admin-draft-state]")?.textContent, "已保存");
    await click('[data-model-admin-edit="m1"]');
    assert.equal(dom.document.querySelector<HTMLInputElement>('[aria-label="demo/model 压缩窗口 Token"]')?.value, "200000");
    await change("150000"); await click('[aria-label="关闭模型编辑"]'); await click('[data-model-admin-publish]');
    assert.equal(published?.[0].auto_compact_window, 150000);
    assert.equal(container.querySelector("[data-model-admin-action-bar]"), null);
    assert.equal(container.querySelector("[data-model-admin-model='m1'] [data-model-admin-draft-state]")?.textContent, "已保存");
  } finally { await React.act(async () => root.unmount()); catalog.cleanup(); Object.assign(modelAdminApi, original); modelGroupsApi.listAdmin = list; }
});
