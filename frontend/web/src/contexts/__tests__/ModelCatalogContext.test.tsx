import assert from "node:assert/strict";
import test from "node:test";

import type { AvailableModel } from "../ModelCatalogContext.tsx";
import type { User } from "../../types/index.ts";
import { installBrowserAuthTestDb } from "../../hooks/__tests__/browserAuthTestDb.ts";

type Listener = (event: { type: string }) => void;

class TestEventTarget {
  private readonly listeners = new Map<string, Set<Listener>>();

  addEventListener(type: string, listener: Listener) {
    const listeners = this.listeners.get(type) || new Set<Listener>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: Listener) {
    this.listeners.get(type)?.delete(listener);
  }

  dispatchEvent(event: { type: string }) {
    this.listeners.get(event.type)?.forEach((listener) => listener(event));
    return true;
  }
}

class TestNode extends TestEventTarget {
  parentNode: TestNode | null = null;
  childNodes: TestNode[] = [];
  nodeValue: string | null = null;
  textContent = "";

  get firstChild() {
    return this.childNodes[0] || null;
  }

  appendChild(child: TestNode) {
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }

  insertBefore(child: TestNode, reference: TestNode | null) {
    child.parentNode = this;
    const index = reference ? this.childNodes.indexOf(reference) : -1;
    if (index < 0) this.childNodes.push(child);
    else this.childNodes.splice(index, 0, child);
    return child;
  }

  removeChild(child: TestNode) {
    const index = this.childNodes.indexOf(child);
    if (index >= 0) this.childNodes.splice(index, 1);
    child.parentNode = null;
    return child;
  }
}

class TestElement extends TestNode {
  readonly nodeType = 1;
  readonly namespaceURI = "http://www.w3.org/1999/xhtml";
  readonly style: Record<string, string> = {};
  ownerDocument!: TestDocument;

  constructor(readonly tagName: string) {
    super();
  }

  get nodeName() {
    return this.tagName.toUpperCase();
  }

  setAttribute() {}
  removeAttribute() {}
}

class TestText extends TestNode {
  readonly nodeType = 3;
  readonly nodeName = "#text";
  ownerDocument!: TestDocument;

  constructor(value: string) {
    super();
    this.nodeValue = value;
    this.textContent = value;
  }
}

class TestDocument extends TestNode {
  readonly nodeType = 9;
  readonly nodeName = "#document";
  readonly documentElement: TestElement;
  readonly head: TestElement;
  readonly body: TestElement;
  activeElement: TestElement | null;
  defaultView: typeof window | null = null;

  constructor() {
    super();
    this.documentElement = this.createElement("html");
    this.head = this.createElement("head");
    this.body = this.createElement("body");
    this.documentElement.appendChild(this.head);
    this.documentElement.appendChild(this.body);
    this.appendChild(this.documentElement);
    this.activeElement = this.body;
  }

  createElement(tagName: string) {
    const element = new TestElement(tagName);
    element.ownerDocument = this;
    return element;
  }

  createElementNS(_namespace: string, tagName: string) {
    return this.createElement(tagName);
  }

  createTextNode(value: string) {
    const text = new TestText(value);
    text.ownerDocument = this;
    return text;
  }
}

const document = new TestDocument();
const storage = new Map<string, string>();
class TestLockManager {
  private readonly tails = new Map<string, Promise<void>>();

  async request<T>(
    name: string,
    options: { mode: "exclusive" },
    callback: () => Promise<T>,
  ): Promise<T> {
    assert.equal(options.mode, "exclusive");
    const previous = this.tails.get(name) ?? Promise.resolve();
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const tail = previous.then(() => gate);
    this.tails.set(name, tail);
    await previous;
    try {
      return await callback();
    } finally {
      release();
      if (this.tails.get(name) === tail) {
        this.tails.delete(name);
      }
    }
  }
}
const windowTarget = new TestEventTarget() as TestEventTarget & {
  document: TestDocument;
  localStorage: Storage;
  location: { pathname: string; search: string };
};
windowTarget.document = document;
windowTarget.localStorage = {
  getItem: (key) => storage.get(key) ?? null,
  setItem: (key, value) => storage.set(key, value),
  removeItem: (key) => storage.delete(key),
  clear: () => storage.clear(),
  key: (index) => [...storage.keys()][index] ?? null,
  get length() {
    return storage.size;
  },
};
windowTarget.location = { pathname: "/chat", search: "" };
Object.assign(windowTarget, {
  Element: TestElement,
  HTMLElement: TestElement,
  HTMLIFrameElement: class TestIFrameElement extends TestElement {},
  Node: TestNode,
});
document.defaultView = windowTarget as unknown as typeof window;
Object.assign(globalThis, {
  window: windowTarget,
  document,
  localStorage: windowTarget.localStorage,
  Node: TestNode,
  Element: TestElement,
  HTMLElement: TestElement,
  HTMLIFrameElement: class TestIFrameElement extends TestElement {},
  SVGElement: TestElement,
  CustomEvent: class {
    constructor(readonly type: string, readonly init?: { detail?: unknown }) {}
  },
  IS_REACT_ACT_ENVIRONMENT: true,
});
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { userAgent: "node", locks: new TestLockManager() },
});
installBrowserAuthTestDb();

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function authUser(id: string, tenantId: string): User {
  return {
    id,
    tenant_id: tenantId,
    username: id,
    email: `${id}@example.test`,
    roles: ["user"],
    permissions: ["agent:use"],
    is_admin: false,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function modelProjection(id: string, label: string) {
  return {
    models: [{ id, value: id, label }],
    count: 1,
    enabled_count: 1,
    default_model_id: id,
  };
}

function availableModelIds(models: AvailableModel[] | null) {
  return models?.map((model) => model.id) ?? [];
}

async function mountModelCatalogHarness(
  configure: (
    authApi: typeof import("../../services/api/auth.ts").authApi,
    modelApi: typeof import("../../services/api/modelPublic.ts").modelPublicApi,
    groupApi: typeof import("../../services/api/modelGroups.ts").modelGroupsApi,
  ) => void,
) {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { AuthProvider, useAuth } = await import("../../hooks/useAuth.tsx");
  const { ModelCatalogProvider, useModelCatalogContext } = await import(
    "../ModelCatalogContext.tsx"
  );
  const { authApi } = await import("../../services/api/auth.ts");
  const { modelPublicApi } = await import("../../services/api/modelPublic.ts");
  const { modelGroupsApi } = await import("../../services/api/modelGroups.ts");
  const originals = {
    getCurrentUser: authApi.getCurrentUser,
    bootstrapAuthContext: authApi.bootstrapAuthContext,
    login: authApi.login,
    logout: authApi.logout,
    listAvailable: modelPublicApi.listAvailable,
    listPublic: modelGroupsApi.listPublic,
  };
  const restoreApis = () => {
    Object.assign(authApi, {
      getCurrentUser: originals.getCurrentUser,
      bootstrapAuthContext: originals.bootstrapAuthContext,
      login: originals.login,
      logout: originals.logout,
    });
    Object.assign(modelPublicApi, {
      listAvailable: originals.listAvailable,
    });
    Object.assign(modelGroupsApi, {
      listPublic: originals.listPublic,
    });
  };
  authApi.bootstrapAuthContext = async (request) => ({
    status: "ready",
    protocol_version: 2,
    generation: request.generation,
  });
  try {
    modelGroupsApi.listPublic = async () => ({ groups: [{ id: "group-test", code: "test", name: "测试组", type: "business", enabled: true, revision: 1, is_primary: true, availability: "available", models: [] }], count: 1, version: "model-groups.v1" });
    configure(authApi, modelPublicApi, modelGroupsApi);
  } catch (error) {
    restoreApis();
    throw error;
  }
  storage.clear();
  storage.set("ai_platform_session_present", "test-session-marker");

  let authSnapshot: ReturnType<typeof useAuth> | null = null;
  let modelCatalogSnapshot: ReturnType<typeof useModelCatalogContext> | null = null;
  function Probe() {
    authSnapshot = useAuth();
    modelCatalogSnapshot = useModelCatalogContext();
    return null;
  }

  const container = document.createElement("div");
  const root = createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(
        React.createElement(
          AuthProvider,
          null,
          React.createElement(
            ModelCatalogProvider,
            null,
            React.createElement(Probe),
          ),
        ),
      );
      await Promise.resolve();
      await Promise.resolve();
    });
  } catch (error) {
    try {
      await React.act(async () => root.unmount());
    } catch {
      // Preserve the mount failure while restoring shared API seams.
    }
    restoreApis();
    storage.clear();
    throw error;
  }

  let unmounted = false;
  let cleanedUp = false;
  return {
    React,
    get auth() {
      assert.ok(authSnapshot);
      return authSnapshot;
    },
    get modelCatalog() {
      assert.ok(modelCatalogSnapshot);
      return modelCatalogSnapshot;
    },
    async flush() {
      await React.act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
    },
    async unmount() {
      if (unmounted) return;
      unmounted = true;
      await React.act(async () => root.unmount());
    },
    async cleanup() {
      if (cleanedUp) return;
      cleanedUp = true;
      try {
        if (!unmounted) await React.act(async () => root.unmount());
      } finally {
        restoreApis();
        storage.clear();
      }
    },
  };
}

test("ModelCatalogProvider hides A synchronously and accepts only B GET completions", async () => {
  const bModels = deferred<ReturnType<typeof modelProjection>>();
  const modelSignals: AbortSignal[] = [];
  let userCalls = 0;
  let modelCalls = 0;
  const harness = await mountModelCatalogHarness((authApi, modelApi) => {
    authApi.getCurrentUser = async () =>
      ++userCalls === 1
        ? authUser("user-a", "tenant-a")
        : authUser("user-b", "tenant-b");
    authApi.login = async () => undefined;
    modelApi.listAvailable = async (options?: { signal?: AbortSignal }) => {
      if (options?.signal) modelSignals.push(options.signal);
      modelCalls += 1;
      return modelCalls === 1
        ? modelProjection("model-a", "Model A")
        : bModels.promise;
    };
  });

  try {
    await harness.flush();
    assert.equal(harness.modelCatalog.availableModels?.[0]?.id, "model-a");

    await harness.React.act(async () => {
      await harness.auth.login({ username: "user-b", password: "safe-test" });
    });

    assert.equal(harness.auth.user?.id, "user-b");
    assert.equal(harness.modelCatalog.availableModels, null);
    assert.equal(modelSignals[0]?.aborted, true);

    bModels.resolve(modelProjection("model-b", "Model B"));
    await harness.flush();

    assert.deepEqual(availableModelIds(harness.modelCatalog.availableModels), [
      "model-b",
    ]);
  } finally {
    await harness.cleanup();
  }
});

test("deferred A GET results cannot mutate authenticated subject B", async () => {
  const aModels = deferred<ReturnType<typeof modelProjection>>();
  let userCalls = 0;
  let modelCalls = 0;
  const harness = await mountModelCatalogHarness((authApi, modelApi) => {
    authApi.getCurrentUser = async () =>
      ++userCalls === 1
        ? authUser("user-a", "tenant-a")
        : authUser("user-b", "tenant-b");
    authApi.login = async () => undefined;
    modelApi.listAvailable = async () =>
      ++modelCalls === 1
        ? aModels.promise
        : modelProjection("model-b", "Model B");
  });

  try {
    await harness.React.act(async () => {
      await Promise.resolve();
      await harness.auth.login({ username: "user-b", password: "safe-test" });
    });
    await harness.flush();

    assert.equal(harness.modelCatalog.availableModels?.[0]?.id, "model-b");

    aModels.resolve(modelProjection("model-a", "Model A"));
    await harness.flush();

    assert.equal(harness.modelCatalog.availableModels?.[0]?.id, "model-b");
  } finally {
    await harness.cleanup();
  }
});

test("ModelCatalogProvider fails closed on B denial and clears state on logout", async () => {
  let userCalls = 0;
  let modelCalls = 0;
  const harness = await mountModelCatalogHarness((authApi, modelApi) => {
    authApi.getCurrentUser = async () =>
      ++userCalls === 1
        ? authUser("user-a", "tenant-a")
        : authUser("user-b", "tenant-b");
    authApi.login = async () => undefined;
    authApi.logout = async () => undefined;
    modelApi.listAvailable = async () => {
      if (++modelCalls === 1) return modelProjection("model-a", "Model A");
      throw new Error("forbidden private diagnostic");
    };
  });

  try {
    await harness.flush();
    await harness.React.act(async () => {
      await harness.auth.login({ username: "user-b", password: "safe-test" });
    });
    await harness.flush();
    assert.equal(harness.modelCatalog.availableModels, null);

    await harness.React.act(async () => {
      await harness.auth.logout();
    });
    assert.equal(harness.modelCatalog.availableModels, null);
  } finally {
    await harness.cleanup();
  }
});

test("ModelCatalogProvider aborts pending subject work on unmount", async () => {
  const models = deferred<ReturnType<typeof modelProjection>>();
  const signals: AbortSignal[] = [];
  const harness = await mountModelCatalogHarness((authApi, modelApi) => {
    authApi.getCurrentUser = async () => authUser("user-a", "tenant-a");
    modelApi.listAvailable = async (options?: { signal?: AbortSignal }) => {
      if (options?.signal) signals.push(options.signal);
      return models.promise;
    };
  });

  try {
    await harness.unmount();
    assert.equal(signals.length, 1);
    assert.equal(signals.every((signal) => signal.aborted), true);
    models.resolve(modelProjection("model-a", "Model A"));
    await models.promise;
  } finally {
    await harness.cleanup();
  }
});

for (const restoredId of ["group-second", "removed-group"]) {
  test(`late group response validates restored intent ${restoredId} without replacing it`, async () => {
    const groups = deferred<Awaited<ReturnType<typeof import("../../services/api/modelGroups.ts").modelGroupsApi.listPublic>>>();
    const listed: string[] = [];
    const harness = await mountModelCatalogHarness((authApi, modelApi, groupApi) => {
      authApi.getCurrentUser = async () => authUser("user-a", "tenant-a");
      groupApi.listPublic = () => groups.promise;
      modelApi.listAvailable = async (options) => { listed.push(options?.groupId ?? ""); return modelProjection("model-a", "A"); };
    });
    try {
      await harness.React.act(async () => harness.modelCatalog.selectGroup(restoredId));
      groups.resolve({ groups: ["group-primary", "group-second"].map((id, index) => ({
        id, code: id, name: id, type: "business", enabled: true, revision: 1,
        is_primary: index === 0, availability: "available", models: [],
      })), count: 2, version: "model-groups.v1" });
      await harness.flush();
      assert.equal(harness.modelCatalog.selectedGroupId, restoredId === "group-second" ? restoredId : "");
      assert.deepEqual(listed, restoredId === "group-second" ? [restoredId] : []);
      assert.equal(Boolean(harness.modelCatalog.selectionError), restoredId === "removed-group");
    } finally { await harness.cleanup(); }
  });
}

test("a disabled primary never silently selects another business group", async () => {
  const harness = await mountModelCatalogHarness((authApi, _modelApi, groupApi) => {
    authApi.getCurrentUser = async () => authUser("user-a", "tenant-a");
    groupApi.listPublic = async () => ({ groups: [true, false].map((is_primary) => ({
      id: is_primary ? "primary" : "other", code: is_primary ? "primary" : "other", name: "业务组",
      type: "business", enabled: !is_primary, revision: 1, is_primary,
      availability: is_primary ? "group_disabled" : "available", models: [],
    })), count: 2, version: "model-groups.v1" });
  });
  try {
    await harness.flush();
    assert.equal(harness.modelCatalog.selectedGroupId, "");
    assert.match(harness.modelCatalog.selectionError ?? "", /主组已不可用/);
  } finally { await harness.cleanup(); }
});

for (const [defaultModelId, expectedValue] of [
  ["model-b", "provider/model-b"],
  ["unavailable-model", "provider/model-a"],
] as const) {
  test(`catalog hydration is read-only and resolves public default ${defaultModelId}`, async () => {
    const originalFetch = globalThis.fetch;
    const requests: { path: string; method: string; cache?: RequestCache }[] = [];
    const user = {
      ...authUser("user-a", "tenant-a"),
      metadata: { pinned_model_ids: ["deleted-model"], unrelated_preference: true },
    };
    const metadataBefore = structuredClone(user.metadata);
    globalThis.fetch = async (input, options) => {
      const path = new URL(String(input), "https://example.test").pathname;
      requests.push({ path, method: options?.method ?? "GET", cache: options?.cache });
      if (path === "/api/agent/models/available") {
        return Response.json({
          models: [
            { id: "model-a", value: "provider/model-a", label: "Model A" },
            { id: "model-b", value: "provider/model-b", label: "Model B" },
          ],
          count: 2,
          enabled_count: 2,
          default_model_id: defaultModelId,
        });
      }
      // Record unexpected profile reads/writes, even when the provider catches
      // an error. Historical metadata must not trigger orphan-pin cleanup.
      return Response.json(user);
    };
    let harness: Awaited<ReturnType<typeof mountModelCatalogHarness>> | undefined;
    try {
      harness = await mountModelCatalogHarness((authApi) => {
        authApi.getCurrentUser = async () => user;
      });
      await harness.flush();
      assert.deepEqual(requests, [{
        path: "/api/agent/models/available", method: "GET", cache: "no-store",
      }]);
      assert.equal(harness.modelCatalog.defaultModel, expectedValue);
      assert.equal(harness.modelCatalog.isLoading, false);
      assert.deepEqual(availableModelIds(harness.modelCatalog.availableModels), ["model-a", "model-b"]);
      assert.deepEqual(user.metadata, metadataBefore);
    } finally {
      await harness?.cleanup();
      globalThis.fetch = originalFetch;
    }
  });
}
