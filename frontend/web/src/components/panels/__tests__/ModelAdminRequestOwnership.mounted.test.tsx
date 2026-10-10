import assert from "node:assert/strict";
import test from "node:test";
import { installTestDom } from "../../../hooks/useAgent/__tests__/testDom.ts";
import { modelAdminApi, type AdminModelState } from "../../../services/api/modelAdmin.ts";
import { ApiRequestError } from "../../../services/api/fetch.ts";
const dom = installTestDom();
type ReactModule = typeof import("react");
type QueryNode = { getAttribute(name: string): string | null; textContent?: string | null; childNodes?: unknown[] };
type Container = { querySelectorAll(selector: string): QueryNode[] };
type Field = { value: string; checked: boolean };
function text(node: unknown): string {
  const value = node as { data?: unknown; textContent?: unknown; childNodes?: unknown[] };
  if (typeof value.data === "string") return value.data;
  return typeof value.textContent === "string" && value.textContent ? value.textContent : (value.childNodes ?? []).map(text).join("");
}
function props(node: object) {
  const key = Object.keys(node).find((key) => key.startsWith("__reactProps$")); assert.ok(key);
  return (node as Record<string, unknown>)[key] as { disabled?: boolean; onClick?: () => void; onChange?: (event: { target: Field }) => void };
}
function saved(revision = 4): AdminModelState {
  return { connection: { configured: true, revision, base_url: "https://gateway-a.example", key_fingerprint: "synthetic-marker" }, models: [{
    id: "mdl_gpt", value: "mock/gpt", label: "合成模型", provider: "compatible", enabled: true, available: true, is_default: true,
    order: 1, last_seen_revision: revision, last_seen_at: "2026-10-09T00:00:00Z", max_input_tokens: 32000, max_output_tokens: 2048,
  }] };
}
const candidate = (revision = 4) => ({ ...saved(revision), base_url: "https://gateway-a.example", models: [{ ...saved(revision).models[0], enabled: false, is_default: false, max_input_tokens: undefined, max_output_tokens: undefined }] });
function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail; }); return { promise, resolve, reject };
}
interface View {
  React: ReactModule; container: Container;
  input(label: string): Field;
  button(attribute: string): QueryNode;
  change(label: string, value: string): Promise<void>;
  click(attribute: string): Promise<void>;
  render(canManage: boolean): Promise<void>;
  unmount(): Promise<void>;
}
async function withControl(api: Partial<typeof modelAdminApi>, run: (view: View) => Promise<void>) {
  const React = await import("react"); const { createRoot } = await import("react-dom/client");
  const { ModelAdminControl } = await import("../ModelAdminControl.tsx");
  const original = { ...modelAdminApi };
  Object.assign(modelAdminApi, { get: async () => saved(), ...api });
  const container = dom.document.createElement("div"); const root = createRoot(container as never);
  const render = async (canManage: boolean) => { await React.act(async () => { root.render(React.createElement(ModelAdminControl, { canManage })); }); };
  const input = (label: string) => {
    const found = container.querySelectorAll("input").find((node) => node.getAttribute("aria-label") === label); assert.ok(found, label); return found as unknown as Field;
  };
  const button = (attribute: string) => {
    const found = container.querySelectorAll("button").find((node) => node.getAttribute(attribute) !== null); assert.ok(found, attribute); return found;
  };
  try {
    await render(true);
    await run({ React, container, input, button, render,
      unmount: async () => { await React.act(async () => root.render(null)); },
      change: async (label, value) => { await React.act(async () => { const field = input(label); field.value = value; props(field).onChange?.({ target: field }); }); },
      click: async (attribute) => { await React.act(async () => { props(button(attribute)).onClick?.(); await Promise.resolve(); }); },
    });
  } finally { await React.act(async () => root.unmount()); Object.assign(modelAdminApi, original); }
}

test("GET never claims test success; successive saves use acknowledged revisions without rediscovery or key masks", async () => {
  const revisions: Array<number | null> = []; let revision = 4;
  await withControl({ discover: async () => { throw new Error("unexpected discovery"); }, publish: async (_url, credential, expected, models) => {
    assert.equal(credential, undefined); revisions.push(expected); return { ...saved(++revision), models };
  } }, async ({ input, change, click, button, container }) => {
    assert.match(text(container), /已配置 · 尚未测试/); assert.doesNotMatch(text(container), /连接正常|测试通过|synthetic-marker/);
    assert.equal(input("模型 API Key").value, ""); assert.equal(props(button("data-model-admin-publish")).disabled, true);
    await change("mock/gpt 最大输入 Token", "64000"); await click("data-model-admin-publish");
    await change("mock/gpt 最大输出 Token", "4096"); assert.equal(props(button("data-model-admin-publish")).disabled, false);
    await click("data-model-admin-publish"); assert.deepEqual(revisions, [4, 5]); assert.equal(input("mock/gpt 最大输入 Token").value, "64000");
  });
});

test("A/B late discovery cannot rewrite connection, mix credentials, or finish B; duplicate handlers are single flight", async () => {
  const a = deferred<ReturnType<typeof candidate>>(); const b = deferred<ReturnType<typeof candidate>>();
  const calls: Array<{ url: string; key?: string; signal?: AbortSignal }> = []; const publications: Array<{ url: string; key?: string; revision: number | null }> = [];
  await withControl({ discover: async (url, key, options) => { calls.push({ url, key, signal: options?.signal }); return calls.length === 1 ? a.promise : b.promise; },
    publish: async (url, key, revision) => { publications.push({ url, key, revision }); return saved(9); },
  }, async ({ React, input, change, click, button, container }) => {
    await change("模型 API Key", "synthetic-a"); await click("data-model-admin-discover");
    assert.equal(props(input("模型 API 地址")).disabled, false);
    await change("模型 API 地址", "https://gateway-b.example/v1"); assert.equal(input("模型 API Key").value, "");
    await change("模型 API Key", "synthetic-b"); assert.equal(calls[0].signal?.aborted, true);
    await click("data-model-admin-discover"); await click("data-model-admin-discover"); assert.equal(calls.length, 2);
    await React.act(async () => a.resolve(candidate()));
    assert.equal(input("模型 API 地址").value, "https://gateway-b.example/v1"); assert.equal(input("模型 API Key").value, "synthetic-b");
    assert.equal(props(button("data-model-admin-publish")).disabled, true); assert.match(text(container), /正在测试当前连接/);
    await React.act(async () => b.resolve(candidate(8))); await click("data-model-admin-publish");
    assert.deepEqual(publications, [{ url: "https://gateway-b.example/v1", key: "synthetic-b", revision: 8 }]);
  });
});

test("late A failure cannot replace successful B result", async () => {
  const a = deferred<ReturnType<typeof candidate>>(); let calls = 0;
  await withControl({ discover: async () => ++calls === 1 ? a.promise : candidate(7) }, async ({ React, change, click, input, container }) => {
    await click("data-model-admin-discover"); await change("模型 API 地址", "https://gateway-b.example"); await change("模型 API Key", "synthetic-b");
    await click("data-model-admin-discover"); await React.act(async () => a.reject(new ApiRequestError("old error", 502, "model_upstream_unavailable")));
    assert.equal(input("模型 API 地址").value, "https://gateway-b.example"); assert.match(text(container), /当前连接测试通过/);
    assert.equal(container.querySelectorAll('[role="alert"]').length, 0);
  });
});

test("test probes the editable draft only; new endpoint needs its own key and discovery before saving", async () => {
  const calls: Array<{ url: string; key?: string }> = [];
  await withControl({ discover: async (url, key) => { calls.push({ url, key }); return { ...candidate(), models: [{ ...saved().models[0], id: "mdl_new", value: "mock/new", label: "新候选", enabled: false, is_default: false }] }; } }, async ({ change, click, input, button }) => {
    await change("模型 API Key", "synthetic-a"); await change("模型 API 地址", "https://gateway-b.example/v1");
    assert.equal(input("模型 API Key").value, ""); assert.equal(props(button("data-model-admin-test")).disabled, true);
    await change("模型 API Key", "synthetic-b"); await click("data-model-admin-test");
    assert.deepEqual(calls[0], { url: "https://gateway-b.example/v1", key: "synthetic-b" });
    assert.equal(input("mock/gpt 显示名称").value, "合成模型"); assert.equal(props(button("data-model-admin-publish")).disabled, true);
    await click("data-model-admin-discover"); assert.equal(input("mock/new 显示名称").value, "新候选");
    assert.equal(input("模型 API 地址").value, "https://gateway-b.example/v1");
  });
});

test("cancel aborts discovery, clears entered key and restores saved models; late completion after close is ignored", async () => {
  const pending = deferred<ReturnType<typeof candidate>>(); let signal: AbortSignal | undefined;
  await withControl({ discover: async (_url, _key, options) => { signal = options?.signal; return pending.promise; } }, async ({ React, change, click, input, render, container }) => {
    await change("mock/gpt 最大输入 Token", "64000"); await change("模型 API 地址", "https://gateway-b.example"); await change("模型 API Key", "synthetic-b");
    await click("data-model-admin-discover"); await click("data-model-admin-cancel"); assert.equal(signal?.aborted, true);
    assert.equal(input("模型 API 地址").value, "https://gateway-a.example"); assert.equal(input("模型 API Key").value, "");
    assert.equal(input("mock/gpt 最大输入 Token").value, "32000"); await render(false);
    await React.act(async () => pending.resolve(candidate(9))); assert.equal(container.querySelectorAll('[data-model-admin-control]').length, 0);
    await render(true); assert.equal(input("模型 API 地址").value, "https://gateway-a.example"); assert.match(text(container), /已配置 · 尚未测试/);
  });
});

test("dirty reload updates saved baseline; discovery preserves edits and failures retain the draft", async () => {
  let gets = 0; let discoveries = 0;
  await withControl({ get: async () => saved(++gets === 1 ? 4 : 7), discover: async () => {
    if (++discoveries === 1) return candidate(7); throw new ApiRequestError("unavailable", 502, "model_upstream_unavailable");
  } }, async ({ change, click, input, container }) => {
    await change("mock/gpt 最大输入 Token", "64000"); await change("模型 API Key", "synthetic-b"); await click("data-model-admin-reload");
    assert.equal(gets, 2); assert.equal(input("模型 API Key").value, ""); assert.equal(input("mock/gpt 最大输入 Token").value, "32000");
    await change("mock/gpt 最大输入 Token", "64000"); await change("mock/gpt 显示名称", "我的模型"); await click("data-model-admin-discover");
    assert.equal(input("mock/gpt 最大输入 Token").value, "64000"); assert.equal(input("启用 我的模型").checked, true);
    await click("data-model-admin-discover"); assert.equal(input("mock/gpt 显示名称").value, "我的模型"); assert.match(text(container), /无法连接模型服务/);
  });
});

test("save conflict keeps edits and requires rediscovery; saving locks all fields and blocks duplicate mutations", async () => {
  const pending = deferred<AdminModelState>(); let calls = 0;
  await withControl({ discover: async () => candidate(7), publish: async () => {
    if (++calls === 1) throw new ApiRequestError("conflict", 409, "model_catalog_revision_conflict"); return pending.promise;
  } }, async ({ React, change, click, input, button, container }) => {
    await change("mock/gpt 最大输入 Token", "64000"); await click("data-model-admin-publish");
    assert.equal(input("mock/gpt 最大输入 Token").value, "64000"); assert.match(text(container), /模型配置已被更新/);
    assert.equal(props(button("data-model-admin-publish")).disabled, true); await click("data-model-admin-discover");
    await click("data-model-admin-publish"); await click("data-model-admin-publish"); assert.equal(calls, 2);
    for (const label of ["模型 API 地址", "模型 API Key", "mock/gpt 显示名称", "mock/gpt 最大输入 Token"]) assert.equal(props(input(label)).disabled, true);
    await React.act(async () => pending.resolve(saved(8)));
  });
});

test("unmount aborts the owned probe and ignores its late completion", async () => {
  const pending = deferred<ReturnType<typeof candidate>>(); let signal: AbortSignal | undefined;
  await withControl({ discover: async (_url, _key, options) => { signal = options?.signal; return pending.promise; } }, async ({ React, click, unmount, container }) => {
    await click("data-model-admin-discover"); await unmount(); assert.equal(signal?.aborted, true);
    await React.act(async () => pending.resolve(candidate(9)));
    assert.equal(container.querySelectorAll('[data-model-admin-control]').length, 0);
  });
});

test("ordinary publish failure preserves the entered key and model edits for retry", async () => {
  const calls: Array<{ key?: string; revision: number | null }> = [];
  await withControl({ discover: async () => candidate(7), publish: async (_url, key, revision, models) => {
    calls.push({ key, revision });
    if (calls.length === 1) throw new ApiRequestError("unavailable", 502, "model_upstream_unavailable");
    return { ...saved(8), models };
  } }, async ({ change, click, input, button, container }) => {
    await change("模型 API Key", "synthetic-retry"); await change("mock/gpt 显示名称", "我的草稿");
    await click("data-model-admin-discover"); await click("data-model-admin-publish");
    assert.equal(input("模型 API Key").value, "synthetic-retry"); assert.equal(input("mock/gpt 显示名称").value, "我的草稿");
    assert.match(text(container), /无法连接模型服务/); assert.equal(props(button("data-model-admin-publish")).disabled, false);
    await click("data-model-admin-publish"); assert.deepEqual(calls, [{ key: "synthetic-retry", revision: 7 }, { key: "synthetic-retry", revision: 7 }]);
    assert.equal(input("模型 API Key").value, "");
  });
});
