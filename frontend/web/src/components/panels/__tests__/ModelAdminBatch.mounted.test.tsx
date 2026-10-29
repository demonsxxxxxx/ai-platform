import { installModelCatalogTestProvider } from "../../../contexts/__tests__/modelCatalogTestProvider.tsx";
import assert from "node:assert/strict";
import test, { after } from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom.ts";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot.tsx";
import { modelAdminApi, type AdminModelState } from "../../../services/api/modelAdmin.ts";
import { modelGroupsApi, type AdminModelGroupDetails, type AdminModelGroupSummary } from "../../../services/api/modelGroups.ts";

const dom = installSelectTestDom();
after(() => dom.close());

const group: AdminModelGroupSummary = { id: "group-sales", code: "sales", name: "销售组", type: "business", enabled: true, revision: 3, remark: "", sort_order: 1, model_count: 0, member_count: 0 };
const modelState: AdminModelState = { connection: { configured: true, revision: 4, base_url: "https://gateway.test", key_fingerprint: "synthetic" }, models: [{ id: "model-a", value: "provider/model-a", label: "模型 A", provider: "test", enabled: true, available: true, order: 1, last_seen_revision: 4, last_seen_at: "2026-01-01T00:00:00Z", auto_compact_window: 200000, max_output_tokens: 2048 }] };
const details: AdminModelGroupDetails = { ...group, models: [], members: [] };

async function flush(React: typeof import("react"), predicate: () => boolean) {
  for (let attempt = 0; attempt < 60 && !predicate(); attempt += 1) {
    await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  }
  assert.ok(predicate());
}

test("mounted batch toolbar previews then atomically applies selected models", async () => {
  const React = await import("react");
  const { ModelAdminControl } = await import("../ModelAdminControl.tsx");
  const originals = { get: modelAdminApi.get, discover: modelAdminApi.discover, publish: modelAdminApi.publish, listAdmin: modelGroupsApi.listAdmin, details: modelGroupsApi.details, batchModels: modelGroupsApi.batchModels };
  const calls: Array<{ preview: boolean; body: unknown }> = [];
  modelAdminApi.get = async () => modelState;
  modelAdminApi.discover = async () => ({ connection: modelState.connection, base_url: modelState.connection.base_url, models: modelState.models });
  modelAdminApi.publish = async () => modelState;
  modelGroupsApi.listAdmin = async () => ({ groups: [group], version: "model-groups.v1" });
  modelGroupsApi.details = async () => details;
  modelGroupsApi.batchModels = async (body) => { calls.push({ preview: body.preview, body }); return { tenant_id: "tenant-a", operation: body.operation, preview: body.preview, changed: true, groups: [{ group_id: group.id, group_type: "business", before: { revision: 3, member_count: 0, models: [] }, after: { revision: 4, member_count: 0, models: [], affected_model_ids: body.model_ids }, changed: true }] }; };
  const container = dom.document.createElement("div");
  const root = await createLobeTestRoot(container);
  const catalog = installModelCatalogTestProvider();
  try {
    await React.act(async () => root.render(catalog.wrap(React.createElement(ModelAdminControl))));
    await flush(React, () => Boolean(container.querySelector("[data-model-admin-control]")));
    await flush(React, () => Boolean(container.querySelector('[data-model-admin-select="model-a"]')));
    const modelCheckbox = container.querySelector<HTMLElement>('[data-model-admin-select="model-a"]');
    assert.ok(modelCheckbox);
    await React.act(async () => modelCheckbox.click());
    await flush(React, () => Boolean(container.querySelector('[aria-label="批量目标 销售组"]')));
    const groupCheckbox = container.querySelector<HTMLElement>('[aria-label="批量目标 销售组"]');
    assert.ok(groupCheckbox);
    await React.act(async () => groupCheckbox.click());
    await React.act(async () => { [...container.querySelectorAll("button")].find((button) => button.textContent === "批量添加")?.click(); });
    await React.act(async () => { [...container.querySelectorAll("button")].find((button) => button.textContent === "预览影响")?.click(); });
    await flush(React, () => calls.length === 1 && calls[0].preview === true);
    assert.deepEqual(calls[0].body, { group_ids: ["group-sales"], model_ids: ["model-a"], operation: "add", expected_group_revisions: { "group-sales": 3 }, preview: true });
    assert.match(container.textContent ?? "", /预览将影响 1 个分组/);
    await React.act(async () => groupCheckbox.click());
    assert.equal(container.querySelector('[data-model-admin-batch-preview]'), null);
    assert.equal(calls.length, 1);
    await React.act(async () => groupCheckbox.click());
    await React.act(async () => { [...container.querySelectorAll("button")].find((button) => button.textContent === "预览影响")?.click(); });
    await flush(React, () => calls.length === 2);
    await React.act(async () => { [...container.querySelectorAll("button")].find((button) => button.textContent === "确认并保存")?.click(); });
    await flush(React, () => calls.length === 3 && calls[2].preview === false);
    assert.deepEqual(calls[2].body, { ...(calls[1].body as object), preview: false });
  } finally {
    await React.act(async () => root.unmount());
    catalog.cleanup();
    modelAdminApi.get = originals.get; modelAdminApi.discover = originals.discover; modelAdminApi.publish = originals.publish;
    modelGroupsApi.listAdmin = originals.listAdmin; modelGroupsApi.details = originals.details; modelGroupsApi.batchModels = originals.batchModels;
  }
});
