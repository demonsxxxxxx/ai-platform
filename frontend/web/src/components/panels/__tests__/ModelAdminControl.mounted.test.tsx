import { installModelCatalogTestProvider } from "../../../contexts/__tests__/modelCatalogTestProvider.tsx";
import assert from "node:assert/strict";
import test, { after } from "node:test";
import { flushSync } from "react-dom";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom.ts";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot.tsx";
import { modelAdminApi } from "../../../services/api/modelAdmin.ts";
import { modelGroupsApi } from "../../../services/api/modelGroups.ts";
import type { AdminModelEntry, AdminModelState } from "../../../services/api/modelAdmin.ts";
import type { ModelAdminControlState } from "../ModelAdminControl.tsx";
import { ApiRequestError } from "../../../services/api/fetch.ts";

const dom = installSelectTestDom();
after(() => dom.close());

type ReactModule = typeof import("react");
type QueryContainer = HTMLElement;
type InputElement = HTMLInputElement;

type ModelOverrides = Partial<AdminModelEntry>;

function model(overrides: ModelOverrides = {}): AdminModelEntry {
  return {
    id: "mdl_gpt",
    value: "openai/gpt-5",
    label: "GPT-5",
    provider: "compatible",
    enabled: false,
    available: true,
    order: 1,
    last_seen_revision: 1,
    last_seen_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function state(overrides: Partial<AdminModelState> = {}): AdminModelState {
  return {
    connection: {
      configured: false,
      revision: null,
      base_url: "",
      key_fingerprint: "",
    },
    models: [],
    ...overrides,
  };
}

function inputByLabel(container: QueryContainer, label: string): InputElement {
  let input = Array.from(dom.document.querySelectorAll("input"))
    .find((candidate) => candidate.getAttribute("aria-label") === label);
  if (!input && label.startsWith("openai/")) {
    flushSync(() => { container.querySelector<HTMLElement>("[data-model-admin-edit]")?.click(); });
    input = Array.from(dom.document.querySelectorAll("input")).find((candidate) => candidate.getAttribute("aria-label") === label);
  }
  assert.ok(input, `expected input ${label}`);
  return input as unknown as InputElement;
}

function buttonByLabel(container: QueryContainer, label: string): HTMLElement {
  const button = Array.from(container.querySelectorAll("button, [role=radio]")).find((node) => node.getAttribute("aria-label") === label);
  assert.ok(button, `expected control ${label}`);
  return button as HTMLElement;
}

function selectByLabel(container: QueryContainer, label: string): HTMLElement {
  const fieldLabel = Array.from(container.querySelectorAll("label[for]")).find((node) => node.textContent === label);
  const select = document.getElementById(fieldLabel?.getAttribute("for") ?? "");
  assert.ok(select, `expected select ${label}`);
  return select;
}

async function changeMountedSelect(React: ReactModule, trigger: HTMLElement, label: string) {
  await React.act(async () => { trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })); });
  const option = Array.from(document.querySelectorAll<HTMLElement>("[role=option]")).find((node) => node.textContent?.trim() === label);
  assert.ok(option, `expected option ${label}`);
  await React.act(async () => {
    option.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, pointerType: "mouse", button: 0 }));
    option.dispatchEvent(new PointerEvent("click", { bubbles: true, pointerType: "mouse", detail: 1 }));
  });
}

function changeMountedInput(input: InputElement, value: string): void {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function nodeText(node: unknown): string {
  const candidate = node as {
    childNodes?: unknown[];
    data?: unknown;
    textContent?: unknown;
  };
  if (typeof candidate.data === "string") return candidate.data;
  if (typeof candidate.textContent === "string" && candidate.textContent) {
    return candidate.textContent;
  }
  return (candidate.childNodes ?? []).map(nodeText).join("");
}

function renderedParagraphText(container: QueryContainer): string {
  return Array.from(container.querySelectorAll("p")).map(nodeText).join(" ");
}

async function waitFor(
  React: ReactModule,
  predicate: () => boolean,
  description: string,
): Promise<void> {
  for (let attempt = 0; attempt < 40 && !predicate(); attempt += 1) {
    await React.act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  assert.ok(predicate(), description);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

test("supplier group editing uses the saved cross-supplier catalog", async () => {
  const React = await import("react");
  const { ModelAdminControl } = await import("../ModelAdminControl.tsx");
  const originals = { ...modelAdminApi };
  const originalGroups = { ...modelGroupsApi };
  const allModels = [
    model({ id: "mdl_a", value: "shared", label: "Model A", provider_id: "supplier-a", provider_name: "Supplier A", enabled: true, max_output_tokens: 2048 }),
    model({ id: "mdl_b", value: "shared", label: "Model B", provider_id: "supplier-b", provider_name: "Supplier B", enabled: true, max_output_tokens: 2048 }),
    model({ id: "mdl_b2", value: "other", label: "Model B2", provider_id: "supplier-b", provider_name: "Supplier B", enabled: true, max_output_tokens: 2048 }),
  ];
  const group = { id: "group-mixed", code: "mixed", name: "Mixed Group", type: "business" as const, enabled: true, revision: 4, sort_order: 1, model_count: 2, member_count: 0, default_model_id: "mdl_a" };
  const providers = [
    { id: "supplier-a", name: "Supplier A", revision: 4, model_count: 1 },
    { id: "supplier-b", name: "Supplier B", revision: 4, model_count: 2 },
  ];
  const catalogCalls: Array<string | undefined> = [];
  modelAdminApi.get = async (options) => {
    catalogCalls.push(options?.providerId);
    const models = options?.providerId
      ? allModels.filter((entry) => entry.provider_id === options.providerId)
      : allModels;
    return state({
      providers,
      connection: { configured: true, revision: 4, base_url: "https://gateway.example", key_fingerprint: "synthetic" },
      models,
    });
  };
  modelGroupsApi.listAdmin = async () => ({ groups: [group], version: "model-groups.v1" });
  modelGroupsApi.details = async () => ({
    ...group,
    members: [],
    models: allModels.slice(0, 2).map((entry, index) => ({
      model_id: entry.id,
      model_value: entry.value,
      label: entry.label,
      enabled: true,
      display_order: index,
      priority: 0,
      weight: 100,
    })),
  });
  const container = dom.document.createElement("div");
  const root = await createLobeTestRoot(container);
  const catalog = installModelCatalogTestProvider();
  try {
    await React.act(async () => root.render(catalog.wrap(React.createElement(ModelAdminControl, { providerId: "supplier-a" }))));
    await waitFor(React, () => Boolean(container.querySelector('[data-model-admin-model="mdl_a"]')), "supplier A catalog should load");
    assert.deepEqual(catalogCalls, ["supplier-a"], "the page catalog request must remain supplier scoped");
    await changeMountedSelect(React, selectByLabel(container, "筛选模型分组"), "分组：Mixed Group");
    const open = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find((button) => button.textContent === "编辑组内模型");
    assert.equal(Boolean(open), true, "the saved group editor should be available");
    await React.act(async () => open!.click());
    await waitFor(React, () => Boolean(document.querySelector('[data-model-group-model="mdl_b"]')), "the saved B association should render");
    assert.deepEqual(catalogCalls, ["supplier-a", undefined], "opening a group must fetch the complete catalog without providerId");

    const otherSupplier = document.querySelector<HTMLElement>('[data-model-group-model="mdl_b"]');
    assert.equal(Boolean(otherSupplier), true);
    assert.equal(Boolean(otherSupplier!.querySelector("[data-group-model-missing]")), false, "saved supplier B must not be reported unavailable");
    assert.match(otherSupplier!.textContent ?? "", /Supplier B/);
    const addModels = document.querySelector<HTMLElement>("[data-model-group-add-models]");
    assert.equal(Boolean(addModels), true);
    await React.act(async () => addModels!.querySelector("summary")!.click());
    const b2 = document.querySelector<HTMLElement>('[data-model-group-catalog-model="mdl_b2"]');
    assert.equal(Boolean(b2), true, "supplier B2 should remain addable");
    const addB2 = b2!.querySelector<HTMLElement>("[role=checkbox]");
    assert.equal(Boolean(addB2), true);
    await React.act(async () => addB2!.click());
    assert.equal(Boolean(document.querySelector('[data-model-group-model="mdl_b2"]')), true, "supplier B2 can be added to the group draft");

    const refresh = deferred<Awaited<ReturnType<typeof modelGroupsApi.listAdmin>>>();
    modelGroupsApi.listAdmin = () => refresh.promise;
    await React.act(async () => root.render(catalog.wrap(React.createElement(ModelAdminControl, { providerId: "supplier-a", groupRevision: 1 }))));
    assert.equal(container.querySelector('[data-model-admin-group-settings] button'), open, "refresh must retain the drawer's focus target");
    await React.act(async () => refresh.resolve({ groups: [group], version: "model-groups.v1" }));
    assert.equal(container.querySelector('[data-model-admin-group-settings] button'), open);
    await React.act(async () => root.render(catalog.wrap(React.createElement(ModelAdminControl, { providerId: "supplier-a", canManage: false, groupRevision: 1 }))));
    assert.equal(container.querySelector('[data-model-admin-group-settings]'), null, "permission loss clears retained groups");
  } finally {
    await React.act(async () => root.unmount());
    catalog.cleanup();
    Object.assign(modelAdminApi, originals);
    Object.assign(modelGroupsApi, originalGroups);
  }
});

test("failed full group catalog load is explicit and can be retried", async () => {
  const React = await import("react");
  const { ModelAdminControl } = await import("../ModelAdminControl.tsx");
  const originals = { ...modelAdminApi };
  const originalGroups = { ...modelGroupsApi };
  const allModels = [
    model({ id: "mdl_a", value: "a", label: "Model A", provider_id: "supplier-a", provider_name: "Supplier A", enabled: true }),
    model({ id: "mdl_b", value: "b", label: "Model B", provider_id: "supplier-b", provider_name: "Supplier B", enabled: true }),
  ];
  const group = { id: "group-mixed", code: "mixed", name: "Mixed Group", type: "business" as const, enabled: true, revision: 4, sort_order: 1, model_count: 1, member_count: 0, default_model_id: "mdl_a" };
  const calls: Array<string | undefined> = [];
  let fullCatalogCalls = 0;
  modelAdminApi.get = async (options) => {
    calls.push(options?.providerId);
    if (options?.providerId) return state({ models: allModels.filter((entry) => entry.provider_id === options.providerId) });
    fullCatalogCalls += 1;
    if (fullCatalogCalls === 1) throw new Error("catalog request failed");
    return state({ models: allModels });
  };
  modelGroupsApi.listAdmin = async () => ({ groups: [group], version: "model-groups.v1" });
  modelGroupsApi.details = async () => ({ ...group, members: [], models: [{ model_id: "mdl_a", model_value: "a", label: "Model A", enabled: true, display_order: 0, priority: 0, weight: 100 }] });
  const container = dom.document.createElement("div");
  const root = await createLobeTestRoot(container);
  const catalog = installModelCatalogTestProvider();
  try {
    await React.act(async () => root.render(catalog.wrap(React.createElement(ModelAdminControl, { providerId: "supplier-a" }))));
    await waitFor(React, () => Boolean(container.querySelector('[data-model-admin-model="mdl_a"]')), "supplier A catalog should load");
    await changeMountedSelect(React, selectByLabel(container, "筛选模型分组"), "分组：Mixed Group");
    const open = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find((button) => button.textContent === "编辑组内模型");
    assert.equal(Boolean(open), true);
    await React.act(async () => open!.click());
    await waitFor(React, () => Boolean(container.querySelector("[data-model-admin-group-catalog-error]")), "full catalog failure should be shown");
    assert.match(container.querySelector("[data-model-admin-group-catalog-error]")?.textContent ?? "", /无法读取已保存的完整模型目录/);
    assert.equal(Boolean(document.querySelector("[data-model-group-models]")), false, "failed load must not mount an editor with an empty catalog");
    assert.equal(Boolean(document.querySelector("[data-group-model-missing]")), false, "failed load must not misreport saved associations as missing");
    assert.deepEqual(calls, ["supplier-a", undefined]);

    const retry = container.querySelector<HTMLElement>("[data-model-admin-group-catalog-retry]");
    assert.equal(Boolean(retry), true);
    await React.act(async () => retry!.click());
    await waitFor(React, () => Boolean(document.querySelector('[data-model-group-model="mdl_a"]')), "retry should open the editor after reading the full catalog");
    assert.deepEqual(calls, ["supplier-a", undefined, undefined]);
    assert.equal(Boolean(document.querySelector('[data-model-group-model="mdl_b"]')), false);
  } finally {
    await React.act(async () => root.unmount());
    catalog.cleanup();
    Object.assign(modelAdminApi, originals);
    Object.assign(modelGroupsApi, originalGroups);
  }
});

test("late full catalog results cannot cross group, provider, draft, close, or unmount boundaries", async () => {
  const React = await import("react");
  const { ModelAdminControl } = await import("../ModelAdminControl.tsx");
  const originals = { ...modelAdminApi };
  const originalGroups = { ...modelGroupsApi };
  const allModels = [
    model({ id: "mdl_a", value: "a", label: "Model A", provider_id: "supplier-a", provider_name: "Supplier A", enabled: true }),
    model({ id: "mdl_b", value: "b", label: "Model B", provider_id: "supplier-b", provider_name: "Supplier B", enabled: true }),
    model({ id: "mdl_b2", value: "b2", label: "Model B2", provider_id: "supplier-b", provider_name: "Supplier B", enabled: true }),
  ];
  const groupOne = { id: "group-one", code: "one", name: "Group One", type: "business" as const, enabled: true, revision: 1, sort_order: 1, model_count: 1, member_count: 0, default_model_id: "mdl_a" };
  const groupTwo = { id: "group-two", code: "two", name: "Group Two", type: "business" as const, enabled: true, revision: 1, sort_order: 2, model_count: 1, member_count: 0, default_model_id: "mdl_b" };
  const fullCatalogRequests: Array<ReturnType<typeof deferred<AdminModelState>>> = [];
  const fullCatalogSignals: Array<AbortSignal | undefined> = [];
  const catalogCalls: Array<string | undefined> = [];
  modelAdminApi.get = (options) => {
    catalogCalls.push(options?.providerId);
    if (options?.providerId) {
      return Promise.resolve(state({ models: allModels.filter((entry) => entry.provider_id === options.providerId) }));
    }
    const request = deferred<AdminModelState>();
    fullCatalogRequests.push(request);
    fullCatalogSignals.push(options?.signal);
    return request.promise;
  };
  modelGroupsApi.listAdmin = async () => ({ groups: [groupOne, groupTwo], version: "model-groups.v1" });
  modelGroupsApi.details = async (groupId) => {
    const group = groupId === groupTwo.id ? groupTwo : groupOne;
    const entry = groupId === groupTwo.id ? allModels[1] : allModels[0];
    return {
      ...group,
      members: [],
      models: [{ model_id: entry.id, model_value: entry.value, label: entry.label, enabled: true, display_order: 0, priority: 0, weight: 100 }],
    };
  };
  const container = dom.document.createElement("div");
  const root = await createLobeTestRoot(container);
  const catalog = installModelCatalogTestProvider();
  let unmounted = false;
  const editCurrentGroup = async () => {
    const edit = Array.from(container.querySelectorAll<HTMLButtonElement>("button")).find((button) => button.textContent === "编辑组内模型");
    assert.equal(Boolean(edit), true, "the selected group should expose its model editor");
    await React.act(async () => edit!.click());
  };
  try {
    await React.act(async () => root.render(catalog.wrap(React.createElement(ModelAdminControl, { providerId: "supplier-a" }))));
    await waitFor(React, () => Boolean(container.querySelector('[data-model-admin-model="mdl_a"]')), "supplier A catalog should load");
    await changeMountedSelect(React, selectByLabel(container, "筛选模型分组"), "分组：Group One");
    await editCurrentGroup();
    await waitFor(React, () => fullCatalogRequests.length === 1, "the first full catalog request should start");

    const closePending = container.querySelector<HTMLElement>("[data-model-admin-group-catalog-close]");
    assert.equal(Boolean(closePending), true);
    await React.act(async () => closePending!.click());
    await editCurrentGroup();
    await waitFor(React, () => fullCatalogRequests.length === 2, "reopening should start a fresh full catalog request");
    await React.act(async () => fullCatalogRequests[1].resolve(state({ models: allModels })));
    await waitFor(React, () => Boolean(document.querySelector('[data-model-group-model="mdl_a"]')), "the reopened group should load its directory");
    await React.act(async () => fullCatalogRequests[0].resolve(state({ models: allModels.slice(0, 2) })));
    assert.equal(Boolean(document.querySelector('[data-model-group-catalog-model="mdl_b2"]')), true, "the closed request must not replace the reopened directory");

    const closeDrawer = document.querySelector<HTMLElement>('[aria-label="关闭分组抽屉"]');
    assert.equal(Boolean(closeDrawer), true);
    await React.act(async () => closeDrawer!.click());
    await changeMountedSelect(React, selectByLabel(container, "筛选模型分组"), "分组：Group One");
    await editCurrentGroup();
    await waitFor(React, () => fullCatalogRequests.length === 3, "the next group edit should start a fresh request");
    await changeMountedSelect(React, selectByLabel(container, "筛选模型分组"), "分组：Group Two");
    await editCurrentGroup();
    await waitFor(React, () => fullCatalogRequests.length === 4, "switching groups should start a new request");
    await React.act(async () => fullCatalogRequests[3].resolve(state({ models: allModels })));
    await waitFor(React, () => Boolean(document.querySelector('[data-model-group-model="mdl_b"]')), "the selected second group should load");
    await React.act(async () => fullCatalogRequests[2].resolve(state({ models: [allModels[0]] })));
    assert.equal(Boolean(document.querySelector('[data-model-group-model="mdl_b"] [data-group-model-missing]')), false, "the previous group's late catalog must not replace the selected group directory");
    assert.match(document.querySelector("[data-model-group-models]")?.textContent ?? "", /Group Two|Model B/);

    const closeSecondDrawer = document.querySelector<HTMLElement>('[aria-label="关闭分组抽屉"]');
    assert.equal(Boolean(closeSecondDrawer), true);
    await React.act(async () => closeSecondDrawer!.click());
    await changeMountedSelect(React, selectByLabel(container, "筛选模型分组"), "分组：Group One");
    await editCurrentGroup();
    await waitFor(React, () => fullCatalogRequests.length === 5, "the supplier switch case should start a pending full catalog request");
    await React.act(async () => root.render(catalog.wrap(React.createElement(ModelAdminControl, { providerId: "supplier-b" }))));
    await waitFor(React, () => fullCatalogRequests.length === 6, "supplier switch should reload the complete directory for the open group");
    await React.act(async () => fullCatalogRequests[5].resolve(state({ models: allModels })));
    await waitFor(React, () => Boolean(document.querySelector('[data-model-group-model="mdl_a"]')), "supplier B page should reopen the selected group with the complete catalog");
    await React.act(async () => fullCatalogRequests[4].resolve(state({ models: [allModels[0]] })));
    assert.equal(Boolean(document.querySelector('[data-model-group-catalog-model="mdl_b2"]')), true, "the previous supplier response must not replace the new full catalog");
    assert.deepEqual(catalogCalls.filter((providerId) => providerId !== undefined), ["supplier-a", "supplier-b"]);

    const closeThirdDrawer = document.querySelector<HTMLElement>('[aria-label="关闭分组抽屉"]');
    assert.equal(Boolean(closeThirdDrawer), true);
    await React.act(async () => closeThirdDrawer!.click());
    await editCurrentGroup();
    await waitFor(React, () => fullCatalogRequests.length === 7, "the draft edit case should start a pending full catalog request");
    await React.act(async () => changeMountedInput(inputByLabel(container, "模型 API 地址"), "https://changed.example"));
    assert.equal(fullCatalogSignals[6]?.aborted, true, "editing supplier configuration invalidates the pending group directory");
    await React.act(async () => fullCatalogRequests[6].resolve(state({ models: allModels })));
    assert.equal(Boolean(document.querySelector('[data-model-group-drawer]')), false);
    await React.act(async () => container.querySelector<HTMLElement>('[data-model-admin-cancel]')!.click());
    assert.equal(Boolean(document.querySelector('[data-model-group-drawer]')), false, "discarding the supplier draft must not reopen an earlier group directory");
    await editCurrentGroup();
    await waitFor(React, () => fullCatalogRequests.length === 8, "the unmount case should start a fresh full catalog request");
    await React.act(async () => root.unmount());
    unmounted = true;
    await React.act(async () => fullCatalogRequests[7].resolve(state({ models: [] })));
  } finally {
    if (!unmounted) await React.act(async () => root.unmount());
    catalog.cleanup();
    Object.assign(modelAdminApi, originals);
    Object.assign(modelGroupsApi, originalGroups);
  }
});

test("Model admin discovery is a draft and only publication changes the active catalog", async () => {
  const React = await import("react");
  const { ModelAdminControl } = await import("../ModelAdminControl.tsx");

  const original = {
    get: modelAdminApi.get,
    discover: modelAdminApi.discover,
    publish: modelAdminApi.publish,
    listAdmin: modelGroupsApi.listAdmin,
    details: modelGroupsApi.details,
  };
  const controlStates: ModelAdminControlState[] = [];
  const onStateChange = (next: ModelAdminControlState) => controlStates.push(next);
  const calls = {
    get: 0,
    discover: [] as Array<{ baseUrl: string; credential?: string }>,
    publish: [] as Array<{
      baseUrl: string; credential?: string; expectedRevision: number | null;
      models: AdminModelEntry[];
    }>,
  };
  const candidate = model({ label: "GPT-5", provider_name: "团队模型服务" });
  const newCandidate = model({ id: "mdl_next", value: "openai/gpt-next", label: "GPT-Next", provider_name: undefined, order: 2 });
  const missingModel = model({ id: "mdl_legacy", value: "openai/retired", label: "旧模型", provider_name: "团队模型服务", enabled: false, available: false, order: 3 });
  const published = state({
    connection: {
      configured: true,
      revision: 4,
      base_url: "https://gateway.example",
      key_fingerprint: "0123456789abcdef",
    },
    models: [model({
      provider_name: "团队模型服务",
      enabled: true,
      auto_compact_window: 200_000, max_output_tokens: 2048,
      last_seen_revision: 4,
    }), missingModel],
  });

  modelGroupsApi.listAdmin = async () => ({ groups: [{ id: "group-sales", code: "sales", name: "销售组", type: "business", enabled: true, revision: 1, remark: "", sort_order: 1, model_count: 1, member_count: 1 }], version: "model-groups.v1" });
  modelGroupsApi.details = async () => ({ id: "group-sales", code: "sales", name: "销售组", type: "business", enabled: true, revision: 1, remark: "", sort_order: 1, model_count: 1, member_count: 1, members: [], models: [{ model_id: "mdl_gpt", model_value: "openai/gpt-5", label: "GPT-5", enabled: true, display_order: 0, priority: 100, weight: 100 }] });
  modelAdminApi.get = async () => {
    calls.get += 1;
    return published;
  };
  modelAdminApi.discover = async (baseUrl, credential) => {
    calls.discover.push({ baseUrl, credential });
    return {
      connection: {
        configured: true,
        revision: 4,
        base_url: "https://gateway.example",
        key_fingerprint: "fedcba9876543210",
      },
      base_url: baseUrl,
      models: [candidate, newCandidate],
    };
  };
  modelAdminApi.publish = async (baseUrl, credential, expectedRevision, models) => {
    calls.publish.push({ baseUrl, credential, expectedRevision, models });
    return {
      ...published,
      models: [
        ...models.map(({ display_name, ...saved }) => ({
          ...saved,
          label: display_name ?? saved.label,
          available: true,
          last_seen_revision: 4,
        })),
        missingModel,
      ],
    };
  };

  const container = dom.document.createElement("div");
  const root = await createLobeTestRoot(container);
  const catalog = installModelCatalogTestProvider();
  try {
    await React.act(async () => {
      root.render(catalog.wrap(React.createElement(ModelAdminControl, {
        canManage: false,
        onStateChange,
      })));
    });
    assert.equal(container.querySelectorAll("[data-model-admin-control]").length, 0);
    assert.equal(calls.get, 0, "non-admin projection must not call the admin API");

    await React.act(async () => {
      root.render(catalog.wrap(React.createElement(ModelAdminControl, {
        canManage: true,
        onStateChange,
      })));
    });
    await waitFor(
      React,
      () => container.querySelectorAll("[data-model-admin-control]").length === 1,
      "admin control should mount after the initial projection loads",
    );
    assert.equal(calls.get, 1);
    assert.deepEqual(controlStates, ["loading", "ready"]);
    assert.match(
      container.querySelectorAll("[data-model-admin-control]")[0].getAttribute("class") ?? "",
      /p-4/,
      "admin controls should keep page-edge padding",
    );
    assert.doesNotMatch(
      nodeText(container),
      /全员模型配置|先获取候选模型|公开模型投影|管理写操作/,
      "admin view should omit explanatory and duplicate catalog surfaces",
    );

    assert.equal(container.querySelector("[data-model-admin-publish]"), null);
    assert.equal(container.querySelector("[data-model-admin-model='mdl_gpt'] [data-model-admin-draft-state]")?.textContent, "已保存");
    assert.match(nodeText(container), /团队模型服务/);
    assert.doesNotMatch(nodeText(container), /提供方|compatible/);
    await React.act(async () => { inputByLabel(container, "openai/gpt-5 压缩窗口 Token"); });
    // The engine owns the actual compaction point; say so once, not per row.
    assert.match(
      nodeText(dom.document.body),
      /实际触发时机由引擎决定，可能早于该值/,
      "admin view must state that compaction timing is engine-owned",
    );
    assert.equal(
      inputByLabel(container, "openai/gpt-5 压缩窗口 Token").getAttribute("aria-describedby"),
      "model-admin-compaction-window-hint",
      "the window input must reference the shared engine-timing hint",
    );
    await React.act(async () => {
      changeMountedInput(inputByLabel(container, "openai/gpt-5 压缩窗口 Token"), "150000");
    });
    let publishButton = container.querySelector<HTMLButtonElement>("[data-model-admin-publish]");
    assert.ok(publishButton);
    await React.act(async () => {
      publishButton!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    await waitFor(
      React,
      () => calls.publish.length === 1,
      "a loaded published catalog should accept token edits without redundant discovery",
    );
    assert.equal(calls.discover.length, 0);
    assert.equal(calls.publish[0].expectedRevision, 4);
    assert.equal(calls.publish[0].credential, undefined);
    assert.equal(calls.publish[0].models[0].auto_compact_window, 150_000);

    assert.equal(catalog.calls(), 2, "successful publish rereads the grouped public catalog");
    assert.match(nodeText(container), /聊天中的模型列表已重新读取/);
    const keyInput = inputByLabel(container, "模型 API Key");
    await React.act(async () => {
      changeMountedInput(keyInput, "super-secret-key");
    });
    publishButton = container.querySelector<HTMLButtonElement>("[data-model-admin-publish]");
    assert.ok(publishButton);
    const publishPropsKey = Object.keys(publishButton)
      .find((key) => key.startsWith("__reactProps$"));
    assert.ok(publishPropsKey);
    const publishProps = (publishButton as unknown as Record<string, unknown>)[publishPropsKey] as {
      disabled?: boolean;
    };
    assert.equal(
      publishProps.disabled,
      true,
      "changing the connection must require discovery before publication",
    );
    assert.equal(container.querySelector("[data-model-admin-test-state]")?.textContent, "连接草稿 · 尚未测试");
    assert.equal(container.querySelector("[data-model-admin-model='mdl_gpt'] [data-model-admin-draft-state]")?.textContent, "待保存修改");
    const discoverButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.getAttribute("data-model-admin-discover") !== null);
    assert.ok(discoverButton);
    await React.act(async () => {
      discoverButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    await waitFor(
      React,
      () => calls.discover.length === 1
        && inputByLabel(container, "openai/gpt-5 平台显示名称").value === "GPT-5",
      "discovery should populate only the editable draft",
    );
    assert.deepEqual(calls.discover, [
      { baseUrl: "https://gateway.example", credential: "super-secret-key" },
    ]);
    assert.equal(calls.publish.length, 1);
    assert.equal(inputByLabel(container, "模型 API Key").value, "super-secret-key");
    assert.equal(calls.publish.length, 1, "getting upstream candidates must not save them");
    assert.match(nodeText(container.querySelector("[data-model-admin-sync-summary]")), /新增候选 1 个 · 上游缺失 1 个/);
    assert.equal(container.querySelector("[data-model-admin-model='mdl_next'] [data-model-admin-draft-state]")?.textContent, "待保存候选");
    assert.match(nodeText(container.querySelector("[data-model-admin-model='mdl_next']")), /未知供应商/);
    assert.equal(container.querySelector("[data-model-admin-model='mdl_gpt'] [data-model-admin-draft-state]")?.textContent, "待保存修改");
    assert.equal(container.querySelector("[data-model-admin-model='mdl_legacy'] [data-model-admin-draft-state]")?.textContent, "待保存修改");

    // Discovery keeps existing selections and user edits for the same model.
    assert.equal(inputByLabel(container, "openai/gpt-5 压缩窗口 Token").value, "150000");
    await React.act(async () => {
      changeMountedInput(inputByLabel(container, "openai/gpt-5 压缩窗口 Token"), "");
      changeMountedInput(inputByLabel(container, "openai/gpt-5 最大输出 Token"), "");
      buttonByLabel(container, "启用 GPT-5").click();
    });
    const statusSelect = selectByLabel(container, "筛选模型状态");
    await changeMountedSelect(React, statusSelect, "状态：上游缺失");
    assert.ok(container.querySelector("[data-model-admin-model='mdl_legacy']"), "the unavailable status filter must retain upstream-missing rows");
    assert.match(nodeText(container.querySelector("[data-model-admin-model='mdl_legacy']")), /上游缺失/);
    await changeMountedSelect(React, statusSelect, "状态：全部");

    const actionBar = container.querySelectorAll("[data-model-admin-action-bar]")[0];
    assert.ok(actionBar);
    assert.match(nodeText(actionBar), /待保存/);
    await React.act(async () => { buttonByLabel(container, "启用 GPT-5").click(); });
    assert.doesNotMatch(nodeText(container), /默认模型|设为默认/);
    assert.match(nodeText(actionBar), /待保存/);
    // An empty window falls back to the platform default; the output cap stays required.
    const windowField = Array.from(container.querySelectorAll("input"))
      .find((candidate) => candidate.getAttribute("aria-label") === "openai/gpt-5 压缩窗口 Token");
    assert.equal(windowField?.getAttribute("placeholder"), "默认 100000");
    const outputField = Array.from(container.querySelectorAll("input"))
      .find((candidate) => candidate.getAttribute("aria-label") === "openai/gpt-5 最大输出 Token");
    assert.equal(outputField?.getAttribute("placeholder"), "必填");
    assert.equal(outputField?.getAttribute("aria-required"), "true");
    await React.act(async () => {
      publishButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    assert.equal(calls.publish.length, 1, "a missing output cap must not reach the publish API");
    assert.match(nodeText(actionBar), /请填写 GPT-5 的最大输出 Token/);
    assert.equal(container.querySelectorAll('[role="alert"]').length, 1);
    await React.act(async () => {
      changeMountedInput(inputByLabel(container, "openai/gpt-5 压缩窗口 Token"), "200000");
      changeMountedInput(inputByLabel(container, "openai/gpt-5 最大输出 Token"), "2048");
    });
    assert.equal(container.querySelectorAll('[role="alert"]').length, 0);
    assert.match(nodeText(actionBar), /待保存/);
    assert.match(nodeText(actionBar), /待保存/);
    assert.match(nodeText(publishButton), /保存并生效/);
    await React.act(async () => {
      publishButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    await waitFor(
      React,
      () => calls.publish.length === 2 && inputByLabel(container, "模型 API Key").value === "",
      "publication should apply the whole draft and clear the write-only key",
    );
    assert.equal(calls.publish[1].expectedRevision, 4);
    assert.equal(calls.publish[1].models[0].enabled, true);
    assert.equal("is_default" in calls.publish[1].models[0], false);
    assert.equal("access_mode" in calls.publish[1].models[0], false);
    assert.equal("allowed_user_ids" in calls.publish[1].models[0], false);
    assert.equal(calls.publish[1].models[0].auto_compact_window, 200_000);
    assert.equal(calls.publish[1].models[0].max_output_tokens, 2048);
    assert.equal(calls.publish[1].models[1].id, "mdl_next");
    assert.equal(container.querySelector("[data-model-admin-action-bar]"), null);
    assert.equal(container.querySelector("[data-model-admin-model='mdl_gpt'] [data-model-admin-draft-state]")?.textContent, "已保存");
    assert.equal(container.querySelector("[data-model-admin-model='mdl_next'] [data-model-admin-draft-state]")?.textContent, "已保存");
    assert.doesNotMatch(renderedParagraphText(container), /super-secret-key/);

    modelAdminApi.discover = async () => {
      throw new ApiRequestError(
        "加载会话失败",
        422,
        "model_connection_endpoint_forbidden",
      );
    };
    await React.act(async () => {
      discoverButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    await waitFor(
      React,
      () => renderedParagraphText(container).includes("该 API 地址未获平台网络策略授权。"),
      "model errors should use model-admin copy",
    );
    assert.doesNotMatch(renderedParagraphText(container), /加载会话失败/);

    modelAdminApi.get = async () => {
      throw new ApiRequestError("模型服务不可用", 503);
    };
    await React.act(async () => {
      root.render(catalog.wrap(React.createElement(ModelAdminControl, {
        canManage: false,
        onStateChange,
      })));
    });
    await React.act(async () => {
      root.render(catalog.wrap(React.createElement(ModelAdminControl, {
        canManage: true,
        onStateChange,
      })));
    });
    await waitFor(
      React,
      () => controlStates[controlStates.length - 1] === "degraded",
      "admin control should report a degraded initial projection",
    );
    assert.deepEqual(controlStates.slice(-2), ["loading", "degraded"]);
  } finally {
    await React.act(async () => {
      root.unmount();
    });
    catalog.cleanup();
    modelAdminApi.get = original.get;
    modelAdminApi.discover = original.discover;
    modelAdminApi.publish = original.publish;
    modelGroupsApi.listAdmin = original.listAdmin;
    modelGroupsApi.details = original.details;
  }
});

test("discovery stages a missing-only change and publication refreshes group choices", async () => {
  const React = await import("react");
  const { ModelAdminControl } = await import("../ModelAdminControl.tsx");
  const originals = { ...modelAdminApi };
  const originalGroups = modelGroupsApi.listAdmin;
  const kept = model({ enabled: true, auto_compact_window: 200000, max_output_tokens: 2048 });
  const gone = model({ id: "mdl_gone", value: "gone", label: "旧模型", order: 2 });
  const saved = state({ connection: { configured: true, revision: 4, base_url: "https://gateway.example", key_fingerprint: "synthetic" }, models: [kept, gone] });
  const calls: AdminModelEntry[][] = [];
  let groupRefreshes = 0;
  modelGroupsApi.listAdmin = async () => ({ groups: [], version: "model-groups.v1" });
  modelAdminApi.get = async () => saved;
  modelAdminApi.discover = async () => ({ connection: saved.connection, base_url: saved.connection.base_url, models: [kept] });
  modelAdminApi.publish = async (_url, _key, _revision, models) => {
    calls.push(models);
    return { ...saved, models: [kept, { ...gone, available: false }] };
  };
  const container = dom.document.createElement("div");
  const root = await createLobeTestRoot(container);
  const catalog = installModelCatalogTestProvider();
  try {
    await React.act(async () => root.render(catalog.wrap(React.createElement(ModelAdminControl, {
      onGroupsChanged: () => { groupRefreshes += 1; },
    }))));
    await React.act(async () => container.querySelector<HTMLElement>("[data-model-admin-discover]")!.click());
    assert.equal(calls.length, 0);
    assert.equal(container.querySelector('[data-model-admin-model="mdl_gone"] [data-model-admin-draft-state]')?.textContent, "待保存修改");
    assert.match(container.querySelector("[data-model-admin-sync-summary]")?.textContent ?? "", /新增候选 0 个 · 上游缺失 1 个/);
    const save = container.querySelector<HTMLButtonElement>("[data-model-admin-publish]");
    assert.ok(save);
    assert.equal(save.disabled, false);
    await React.act(async () => save.click());
    assert.deepEqual(calls[0].map((entry) => entry.id), [kept.id]);
    assert.equal(groupRefreshes, 1);
    assert.equal(container.querySelector("[data-model-admin-action-bar]"), null);
  } finally {
    await React.act(async () => root.unmount());
    catalog.cleanup();
    Object.assign(modelAdminApi, originals);
    modelGroupsApi.listAdmin = originalGroups;
  }
});

test("edited model stays disabled through upstream disappearance, rediscovery and publication", async () => {
  const React = await import("react");
  const { ModelAdminControl } = await import("../ModelAdminControl.tsx");
  const originals = { ...modelAdminApi };
  const originalGroups = modelGroupsApi.listAdmin;
  const edited = model({ enabled: true, auto_compact_window: 200000, max_output_tokens: 2048 });
  const other = model({ id: "mdl_other", value: "other", label: "Other", order: 2, enabled: true, max_output_tokens: 2048 });
  const saved = state({ connection: { configured: true, revision: 4, base_url: "https://gateway.example", key_fingerprint: "synthetic" }, models: [edited, other] });
  let upstream = [other];
  let published: AdminModelEntry[] = [];
  modelGroupsApi.listAdmin = async () => ({ groups: [], version: "model-groups.v1" });
  modelAdminApi.get = async () => saved;
  modelAdminApi.discover = async () => ({ connection: saved.connection, base_url: saved.connection.base_url, models: upstream });
  modelAdminApi.publish = async (_url, _key, _revision, models) => {
    published = models;
    return { ...saved, models };
  };
  const container = dom.document.createElement("div");
  const root = await createLobeTestRoot(container);
  const catalog = installModelCatalogTestProvider();
  try {
    await React.act(async () => root.render(catalog.wrap(React.createElement(ModelAdminControl))));
    await React.act(async () => container.querySelector<HTMLElement>('[data-model-admin-edit="mdl_gpt"]')!.click());
    await React.act(async () => {
      changeMountedInput(inputByLabel(container, "openai/gpt-5 平台显示名称"), "Edited GPT");
      changeMountedInput(inputByLabel(container, "openai/gpt-5 最大输出 Token"), "4096");
    });
    await React.act(async () => document.querySelector<HTMLElement>('[aria-label="关闭模型编辑"]')!.click());
    await React.act(async () => container.querySelector<HTMLElement>('[data-model-admin-model="mdl_gpt"] [role=switch]')!.click());
    const discover = container.querySelector<HTMLElement>("[data-model-admin-discover]")!;
    await React.act(async () => discover.click());
    const missing = container.querySelector<HTMLElement>('[data-model-admin-model="mdl_gpt"]')!;
    assert.match(missing.textContent ?? "", /Edited GPT.*上游缺失/);
    assert.equal(missing.querySelector('[role=switch]')?.getAttribute('aria-checked'), 'false');
    upstream = [edited, other];
    await React.act(async () => discover.click());
    await React.act(async () => container.querySelector<HTMLElement>('[data-model-admin-publish]')!.click());
    const result = published.find((entry) => entry.id === edited.id);
    assert.equal(result?.enabled, false);
    assert.equal(result?.label, "Edited GPT");
    assert.equal(result?.max_output_tokens, 4096);
  } finally {
    await React.act(async () => root.unmount());
    catalog.cleanup();
    Object.assign(modelAdminApi, originals);
    modelGroupsApi.listAdmin = originalGroups;
  }
});
