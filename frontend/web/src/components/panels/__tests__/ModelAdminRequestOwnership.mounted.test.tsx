import { installModelCatalogTestProvider } from "../../../contexts/__tests__/modelCatalogTestProvider.tsx";
import assert from "node:assert/strict";
import test, { after } from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom.ts";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot.tsx";
import { modelAdminApi, type AdminModelState } from "../../../services/api/modelAdmin.ts";
import { ApiRequestError } from "../../../services/api/fetch.ts";
const dom = installSelectTestDom();
after(() => dom.close());
type ReactModule = typeof import("react");
type QueryNode = { getAttribute(name: string): string | null; textContent?: string | null; childNodes?: ArrayLike<unknown> };
type Container = { querySelectorAll(selector: string): ArrayLike<QueryNode> };
type Field = { value: string; checked: boolean };
function choice(container: Container, label: string): HTMLElement {
  const found = Array.from(container.querySelectorAll("button, [role=radio]")).find((node) => node.getAttribute("aria-label") === label);
  assert.ok(found, label); return found as HTMLElement;
}
function text(node: unknown): string {
  const value = node as { data?: unknown; textContent?: unknown; childNodes?: ArrayLike<unknown> };
  if (typeof value.data === "string") return value.data;
  return typeof value.textContent === "string" && value.textContent ? value.textContent : Array.from(value.childNodes ?? []).map(text).join("");
}
function props(node: object) {
  const key = Object.keys(node).find((key) => key.startsWith("__reactProps$")); assert.ok(key);
  return (node as Record<string, unknown>)[key] as { disabled?: boolean; onClick?: (event: MouseEvent) => void; onChange?: (event: { target: Field }) => void };
}
function saved(revision = 4): AdminModelState {
  return { connection: { configured: true, revision, base_url: "https://gateway-a.example", key_fingerprint: "synthetic-marker" }, models: [{
    id: "mdl_gpt", value: "mock/gpt", label: "合成模型", provider: "compatible", enabled: true, available: true,
    order: 1, last_seen_revision: revision, last_seen_at: "2026-10-09T00:00:00Z", auto_compact_window: 200_000, max_output_tokens: 2048,
  }] };
}
const candidate = (revision = 4) => ({ ...saved(revision), base_url: "https://gateway-a.example", models: [{ ...saved(revision).models[0], enabled: false, auto_compact_window: undefined, max_output_tokens: undefined }] });
function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail; }); return { promise, resolve, reject };
}
interface View {
  React: ReactModule; container: Container;
  catalog: ReturnType<typeof installModelCatalogTestProvider>;
  input(label: string): Field;
  button(attribute: string): QueryNode;
  change(label: string, value: string): Promise<void>;
  click(attribute: string): Promise<void>;
  render(canManage: boolean): Promise<void>;
  unmount(): Promise<void>;
}
async function withControl(api: Partial<typeof modelAdminApi>, run: (view: View) => Promise<void>) {
  const React = await import("react");
  const { ModelAdminControl } = await import("../ModelAdminControl.tsx");
  const original = { ...modelAdminApi };
  Object.assign(modelAdminApi, { get: async () => saved(), ...api });
  const container = dom.document.createElement("div"); const root = await createLobeTestRoot(container);
  const catalog = installModelCatalogTestProvider();
  const render = async (canManage: boolean) => { await React.act(async () => { root.render(catalog.wrap(React.createElement(ModelAdminControl, { canManage }))); }); };
  const input = (label: string) => {
    const found = Array.from(container.querySelectorAll("input")).find((node) => node.getAttribute("aria-label") === label); assert.ok(found, label); return found as unknown as Field;
  };
  const button = (attribute: string) => {
    const found = Array.from(container.querySelectorAll("button")).find((node) => node.getAttribute(attribute) !== null); assert.ok(found, attribute); return found;
  };
  try {
    await render(true);
    await run({ React, container, input, button, render, catalog,
      unmount: async () => { await React.act(async () => root.render(null)); },
      change: async (label, value) => { await React.act(async () => {
        const field = input(label);
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(field, value);
        (field as unknown as HTMLInputElement).dispatchEvent(new Event("input", { bubbles: true }));
      }); },
      click: async (attribute) => { await React.act(async () => { (button(attribute) as HTMLButtonElement).click(); await Promise.resolve(); }); },
    });
  } finally { await React.act(async () => root.unmount()); catalog.cleanup(); Object.assign(modelAdminApi, original); }
}

test("GET never claims test success; successive saves use acknowledged revisions without rediscovery or key masks", async () => {
  const revisions: Array<number | null> = []; let revision = 4;
  await withControl({ discover: async () => { throw new Error("unexpected discovery"); }, publish: async (_url, credential, expected, models) => {
    assert.equal(credential, undefined); revisions.push(expected); return { ...saved(++revision), models };
  } }, async ({ input, change, click, button, container }) => {
    assert.match(text(container), /已配置 · 尚未测试/); assert.doesNotMatch(text(container), /连接正常|测试通过|synthetic-marker/);
    assert.equal(input("模型 API Key").value, ""); assert.equal(props(button("data-model-admin-publish")).disabled, true);
    await change("mock/gpt 压缩窗口 Token", "150000"); await click("data-model-admin-publish");
    await change("mock/gpt 最大输出 Token", "4096"); assert.equal(props(button("data-model-admin-publish")).disabled, false);
    await click("data-model-admin-publish"); assert.deepEqual(revisions, [4, 5]); assert.equal(input("mock/gpt 压缩窗口 Token").value, "150000");
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
    await React.act(async () => b.resolve(candidate())); await click("data-model-admin-publish");
    assert.deepEqual(publications, [{ url: "https://gateway-b.example/v1", key: "synthetic-b", revision: 4 }]);
  });
});

test("late A failure cannot replace successful B result", async () => {
  const a = deferred<ReturnType<typeof candidate>>(); let calls = 0;
  await withControl({ discover: async () => ++calls === 1 ? a.promise : candidate() }, async ({ React, change, click, input, container }) => {
    await click("data-model-admin-discover"); await change("模型 API 地址", "https://gateway-b.example"); await change("模型 API Key", "synthetic-b");
    await click("data-model-admin-discover"); await React.act(async () => a.reject(new ApiRequestError("old error", 502, "model_upstream_unavailable")));
    assert.equal(input("模型 API 地址").value, "https://gateway-b.example"); assert.match(text(container), /当前连接测试通过/);
    assert.equal(container.querySelectorAll('[role="alert"]').length, 0);
  });
});

test("test probes the editable draft only; new endpoint needs its own key and discovery before saving", async () => {
  const calls: Array<{ url: string; key?: string }> = [];
  await withControl({ discover: async (url, key) => { calls.push({ url, key }); return { ...candidate(), models: [{ ...saved().models[0], id: "mdl_new", value: "mock/new", label: "新候选", enabled: false }] }; } }, async ({ change, click, input, button }) => {
    await change("模型 API Key", "synthetic-a"); await change("模型 API 地址", "https://gateway-b.example/v1");
    assert.equal(input("模型 API Key").value, ""); assert.equal(props(button("data-model-admin-test")).disabled, true);
    await change("模型 API Key", "synthetic-b"); await click("data-model-admin-test");
    assert.deepEqual(calls[0], { url: "https://gateway-b.example/v1", key: "synthetic-b" });
    assert.equal(input("mock/gpt 平台显示名称").value, "合成模型"); assert.equal(props(button("data-model-admin-publish")).disabled, true);
    await click("data-model-admin-discover"); assert.equal(input("mock/new 平台显示名称").value, "新候选");
    assert.equal(input("模型 API 地址").value, "https://gateway-b.example/v1");
  });
});

test("cancel aborts discovery, clears entered key and restores saved models; late completion after close is ignored", async () => {
  const pending = deferred<ReturnType<typeof candidate>>(); let signal: AbortSignal | undefined;
  await withControl({ discover: async (_url, _key, options) => { signal = options?.signal; return pending.promise; } }, async ({ React, change, click, input, render, container }) => {
    await change("mock/gpt 压缩窗口 Token", "150000"); await change("模型 API 地址", "https://gateway-b.example"); await change("模型 API Key", "synthetic-b");
    await click("data-model-admin-discover"); await click("data-model-admin-cancel"); assert.equal(signal?.aborted, true);
    assert.equal(input("模型 API 地址").value, "https://gateway-a.example"); assert.equal(input("模型 API Key").value, "");
    assert.equal(input("mock/gpt 压缩窗口 Token").value, "200000"); await render(false);
    await React.act(async () => pending.resolve(candidate(9))); assert.equal(container.querySelectorAll('[data-model-admin-control]').length, 0);
    await render(true); assert.equal(input("模型 API 地址").value, "https://gateway-a.example"); assert.match(text(container), /已配置 · 尚未测试/);
  });
});

test("dirty reload updates saved baseline; discovery preserves edits and failures retain the draft", async () => {
  let gets = 0; let discoveries = 0;
  await withControl({ get: async () => saved(++gets === 1 ? 4 : 7), discover: async () => {
    if (++discoveries === 1) return candidate(7); throw new ApiRequestError("unavailable", 502, "model_upstream_unavailable");
  } }, async ({ change, click, input, container }) => {
    await change("mock/gpt 压缩窗口 Token", "150000"); await change("模型 API Key", "synthetic-b"); await click("data-model-admin-reload");
    assert.equal(gets, 2); assert.equal(input("模型 API Key").value, ""); assert.equal(input("mock/gpt 压缩窗口 Token").value, "200000");
    await change("mock/gpt 压缩窗口 Token", "150000"); await change("mock/gpt 平台显示名称", "我的模型"); await click("data-model-admin-discover");
    assert.equal(input("mock/gpt 压缩窗口 Token").value, "150000"); assert.equal(choice(container, "启用 我的模型").getAttribute("aria-checked"), "true");
    await click("data-model-admin-discover"); assert.equal(input("mock/gpt 平台显示名称").value, "我的模型"); assert.match(text(container), /无法连接模型服务/);
  });
});

test("save conflict keeps edits until explicit reload; saving locks fields and blocks duplicate mutations", async () => {
  const pending = deferred<AdminModelState>(); let calls = 0;
  await withControl({ get: async () => saved(calls ? 7 : 4), discover: async () => candidate(7), publish: async () => {
    if (++calls === 1) throw new ApiRequestError("conflict", 409, "model_catalog_revision_conflict"); return pending.promise;
  } }, async ({ React, change, click, input, button, container }) => {
    await change("mock/gpt 压缩窗口 Token", "150000"); await click("data-model-admin-publish");
    assert.equal(input("mock/gpt 压缩窗口 Token").value, "150000"); assert.match(text(container), /模型配置已被更新/);
    assert.equal(props(button("data-model-admin-publish")).disabled, true); await click("data-model-admin-discover");
    assert.equal(input("mock/gpt 压缩窗口 Token").value, "150000");
    assert.equal(props(button("data-model-admin-publish")).disabled, true);
    await click("data-model-admin-publish"); assert.equal(calls, 1);
    await click("data-model-admin-reload");
    await change("mock/gpt 压缩窗口 Token", "150000");
    await click("data-model-admin-publish"); await click("data-model-admin-publish"); assert.equal(calls, 2);
    for (const label of ["模型 API 地址", "模型 API Key", "mock/gpt 平台显示名称", "mock/gpt 压缩窗口 Token"]) assert.equal(props(input(label)).disabled, true);
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
  await withControl({ discover: async () => candidate(), publish: async (_url, key, revision, models) => {
    calls.push({ key, revision });
    if (calls.length === 1) throw new ApiRequestError("unavailable", 502, "model_upstream_unavailable");
    return { ...saved(8), models };
  } }, async ({ change, click, input, button, container }) => {
    await change("模型 API Key", "synthetic-retry"); await change("mock/gpt 平台显示名称", "我的草稿");
    await click("data-model-admin-discover"); await click("data-model-admin-publish");
    assert.equal(input("模型 API Key").value, "synthetic-retry"); assert.equal(input("mock/gpt 平台显示名称").value, "我的草稿");
    assert.match(text(container), /无法连接模型服务/); assert.equal(props(button("data-model-admin-publish")).disabled, false);
    await click("data-model-admin-publish"); assert.deepEqual(calls, [{ key: "synthetic-retry", revision: 4 }, { key: "synthetic-retry", revision: 4 }]);
    assert.equal(input("模型 API Key").value, "");
  });
});

for (const discoveredRevision of [3, 5]) {
  test(`discovery at revision ${discoveredRevision} cannot rebase another administrator's stale draft`, async () => {
    const pending = deferred<ReturnType<typeof candidate>>(); let publications = 0;
    await withControl({ discover: async () => pending.promise, publish: async () => { publications += 1; return saved(6); } },
      async ({ React, change, click, input, button, container }) => {
        await change("mock/gpt 平台显示名称", "我的未保存名称");
        await change("mock/gpt 压缩窗口 Token", "150000");
        await change("mock/gpt 最大输出 Token", "4096");
        await change("模型 API Key", "synthetic-unsaved");
        await click("data-model-admin-discover");
        // Another administrator's published configuration arrives after this editor loaded revision 4.
        const concurrent = candidate(discoveredRevision);
        concurrent.models[0].label = "其他管理员的新名称";
        await React.act(async () => pending.resolve(concurrent));
        assert.equal(input("mock/gpt 平台显示名称").value, "我的未保存名称");
        assert.equal(input("mock/gpt 压缩窗口 Token").value, "150000");
        assert.equal(input("mock/gpt 最大输出 Token").value, "4096");
        assert.equal(choice(container, "启用 我的未保存名称").getAttribute("aria-checked"), "true");
        assert.doesNotMatch(text(container), /设为默认/);
        assert.equal(input("模型 API Key").value, "synthetic-unsaved");
        assert.equal(props(button("data-model-admin-publish")).disabled, true);
        assert.match(text(container), /未保存编辑已保留，请重新加载配置后再发布/);
        await click("data-model-admin-publish"); assert.equal(publications, 0);
        await click("data-model-admin-discover");
        assert.equal(props(button("data-model-admin-publish")).disabled, true);
      });
  });
}

test("first-configuration discovery cannot acknowledge a concurrent first publication", async () => {
  let publications = 0;
  await withControl({ get: async () => ({ connection: { configured: false, revision: null, base_url: "", key_fingerprint: "" }, models: [] }),
    discover: async () => candidate(1), publish: async () => { publications += 1; return saved(2); },
  }, async ({ change, click, input, button, container }) => {
    await change("模型 API 地址", "https://new-gateway.example"); await change("模型 API Key", "synthetic-first");
    await click("data-model-admin-discover");
    assert.equal(input("模型 API 地址").value, "https://new-gateway.example");
    assert.equal(input("模型 API Key").value, "synthetic-first");
    assert.match(text(container), /未保存编辑已保留/);
    assert.equal(props(button("data-model-admin-publish")).disabled, true);
    await click("data-model-admin-publish"); assert.equal(publications, 0);
  });
});

test("a connectivity test at a newer revision never changes the draft publication baseline", async () => {
  const revisions: Array<number | null> = [];
  await withControl({ discover: async () => candidate(7), publish: async (_url, _key, revision) => {
    revisions.push(revision); throw new ApiRequestError("conflict", 409, "model_catalog_revision_conflict");
  } }, async ({ change, click, input, container }) => {
    await change("mock/gpt 平台显示名称", "我的草稿"); await click("data-model-admin-test");
    assert.equal(input("mock/gpt 平台显示名称").value, "我的草稿");
    await click("data-model-admin-publish"); assert.deepEqual(revisions, [4]);
    assert.match(text(container), /模型配置已被更新/);
  });
});

function savedWithSecondModel(): AdminModelState {
  const state = saved();
  return { ...state, models: [...state.models, {
    ...state.models[0], id: "mdl_second", value: "mock/second", label: "第二模型",
    order: 2,
  }] };
}

// An empty window is valid: it takes the platform default of 100000.
for (const [field, label, invalidValues] of [
  ["压缩窗口", "mock/second 压缩窗口 Token", ["0", "-1", "1.5", "99000", "1000001"]],
  ["最大输出", "mock/second 最大输出 Token", ["", "0", "-1", "1.5", "10000001"]],
] as const) {
  for (const invalid of invalidValues) {
    test(`disabled model rejects ${field} capacity ${invalid || "empty"} before publication`, async () => {
      let publications = 0;
      await withControl({ get: async () => savedWithSecondModel(), publish: async () => {
        publications += 1; return savedWithSecondModel();
      } }, async ({ React, change, click, input, container }) => {
        await change(label, invalid);
        await React.act(async () => {
          choice(container, "启用 第二模型").click();
        });
        await click("data-model-admin-publish");
        assert.equal(publications, 0, "invalid disabled capacity must not reach the publish API");
        assert.match(text(container), /第二模型.*最大输出 Token.*同时留空/);
        assert.equal(input(label).value, invalid);
        for (const repair of ["mock/second 压缩窗口 Token", "mock/second 最大输出 Token"]) {
          assert.equal(props(input(repair)).disabled, false,
            "disabled capacity must remain editable so the blocking pair can be repaired");
        }
      });
    });
  }
}

for (const clearCapacity of [false, true]) {
  test(`disabled capacity ${clearCapacity ? "explicit clearing" : "preservation"} survives toggles and serializes honestly`, async () => {
    const initial = savedWithSecondModel();
    const originalFetch = globalThis.fetch;
    const requests: Array<{ expected_revision: number; credential: string; models: Array<Record<string, unknown>> }> = [];
    globalThis.fetch = async (_url, init) => {
      const body = JSON.parse(String(init?.body)); requests.push(body);
      return new Response(JSON.stringify({ ...initial, models: initial.models.map((model, index) => ({
        ...model, ...body.models[index], label: body.models[index].display_name,
        auto_compact_window: body.models[index].auto_compact_window,
        max_output_tokens: body.models[index].max_output_tokens,
      })) }), { status: 200, headers: { "Content-Type": "application/json" } });
    };
    try {
      await withControl({ get: async () => initial, discover: async () => ({ ...initial, base_url: initial.connection.base_url }) },
        async ({ React, change, click, input, container }) => {
          const toggle = async (checked: boolean) => { await React.act(async () => {
            const enabled = choice(container, "启用 停用草稿");
            if ((enabled.getAttribute("aria-checked") === "true") !== checked) enabled.click();
          }); };
          await change("模型 API Key", "synthetic-capacity-key");
          await change("mock/second 平台显示名称", "停用草稿");
          await change("mock/second 压缩窗口 Token", "");
          await click("data-model-admin-discover");
          await toggle(false);
          await click("data-model-admin-publish");
          // An empty window is valid: it takes the platform default of 100000.
          assert.equal(requests.length, 1);
          assert.equal(requests[0].models[1].auto_compact_window, undefined);
          assert.equal(input("模型 API Key").value, "");
          assert.equal(input("mock/second 平台显示名称").value, "停用草稿");
          assert.equal(input("mock/second 最大输出 Token").value, "2048",
            "disabling must not erase the remaining draft capacity");
          await change("mock/second 压缩窗口 Token", "150000");
          await change("mock/second 最大输出 Token", "10000000");
          for (const checked of [true, false]) {
            await toggle(checked);
            assert.equal(input("mock/second 压缩窗口 Token").value, "150000");
            assert.equal(input("mock/second 最大输出 Token").value, "10000000");
          }
          if (clearCapacity) {
            await change("mock/second 压缩窗口 Token", "");
            await change("mock/second 最大输出 Token", "");
            await toggle(true);
            await click("data-model-admin-publish");
            assert.equal(requests.length, 1, "enabled models still require an output cap");
            await toggle(false);
          }
          await click("data-model-admin-publish");
          assert.equal(requests.length, 2);
          assert.equal(requests[1].expected_revision, 4);
          assert.equal(requests[1].credential, "undefined" === typeof requests[1].credential ? undefined : requests[1].credential);
          assert.equal("is_default" in requests[1].models[1], false);
          assert.equal("access_mode" in requests[1].models[1], false);
          assert.equal("allowed_user_ids" in requests[1].models[1], false);
          assert.deepEqual(requests[1].models[1], {
            id: "mdl_second", value: "mock/second", display_name: "停用草稿",
            enabled: false, order: 2,
            ...(clearCapacity ? {} : { auto_compact_window: 150_000, max_output_tokens: 10000000 }),
          });
          assert.equal(input("模型 API Key").value, "");
          assert.match(text(container), /模型配置已保存并生效/);
        });
    } finally { globalThis.fetch = originalFetch; }
  });
}

test("disabled capacity fields still honor request and upstream-availability locks", async () => {
  const initial = savedWithSecondModel();
  initial.models[1].enabled = false;
  initial.models.push({ ...initial.models[1], id: "mdl_missing", value: "mock/missing", available: false, order: 3 });
  const pending = deferred<AdminModelState>();
  await withControl({ get: async () => initial, publish: async () => pending.promise },
    async ({ React, input, change, click }) => {
      for (const label of ["压缩窗口 Token", "最大输出 Token"]) {
        assert.equal(props(input(`mock/second ${label}`)).disabled, false);
        assert.equal(props(input(`mock/missing ${label}`)).disabled, true);
      }
      await change("mock/gpt 压缩窗口 Token", "150000");
      await click("data-model-admin-publish");
      for (const label of ["压缩窗口 Token", "最大输出 Token"]) {
        assert.equal(props(input(`mock/second ${label}`)).disabled, true);
      }
      await React.act(async () => pending.resolve(initial));
    });
});

test("successful publication remains successful when the grouped catalog refresh fails", async () => {
  await withControl({ publish: async (_url, _key, _revision, models) => ({ ...saved(5), models }) },
    async ({ catalog, change, click, container, button }) => {
      catalog.failRefresh();
      await change("mock/gpt 压缩窗口 Token", "150000");
      await click("data-model-admin-publish");
      assert.equal(catalog.calls(), 2);
      assert.match(text(container), /模型配置已保存并生效；模型列表暂未刷新/);
      assert.equal(container.querySelectorAll('[role="alert"]').length, 0);
      assert.equal(props(button("data-model-admin-publish")).disabled, true);
    });
});

test("publication refresh completion cannot write into a replacement admin owner", async () => {
  const pending = deferred<{ models: []; count: number; enabled_count: number }>();
  await withControl({ publish: async (_url, _key, _revision, models) => ({ ...saved(5), models }) },
    async ({ change, click, render, React, container }) => {
      const { modelPublicApi } = await import("../../../services/api/modelPublic.ts");
      modelPublicApi.listAvailable = () => pending.promise;
      await change("mock/gpt 压缩窗口 Token", "150000");
      await click("data-model-admin-publish");
      await render(false);
      await render(true);
      await React.act(async () => pending.resolve({ models: [], count: 0, enabled_count: 0 }));
      assert.doesNotMatch(text(container), /聊天中的模型列表已重新读取|模型列表暂未刷新/);
    });
});
