import assert from "node:assert/strict";
import test from "node:test";

import "../../../i18n";
import type { User } from "../../../types";
import type { AdminToolPermissionInboxClient } from "../AdminToolPermissionInboxSection.tsx";
import { isInboxDecisionDisabled } from "../adminToolPermissionInboxState.ts";
import { ApiRequestError } from "../../../services/api/fetch.ts";

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

  get lastChild() {
    return this.childNodes[this.childNodes.length - 1] || null;
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

  contains(node: TestNode | null): boolean {
    return node === this || this.childNodes.some((child) => child.contains(node));
  }
}

class TestElement extends TestNode {
  readonly nodeType = 1;
  readonly namespaceURI = "http://www.w3.org/1999/xhtml";
  readonly style: Record<string, string> = {};
  readonly attributes = new Map<string, string>();
  ownerDocument!: TestDocument;
  className = "";
  id = "";
  private html = "";

  constructor(readonly tagName: string) {
    super();
  }

  get nodeName() {
    return this.tagName.toUpperCase();
  }

  get innerHTML() {
    return this.html;
  }

  set innerHTML(value: string) {
    this.html = value;
    this.childNodes = [];
    if (value) this.appendChild(this.ownerDocument.createTextNode(value));
  }

  setAttribute(name: string, value: string) {
    this.attributes.set(name, value);
  }

  removeAttribute(name: string) {
    this.attributes.delete(name);
  }

  getAttribute(name: string) {
    return this.attributes.get(name) || null;
  }

  hasAttribute(name: string) {
    return this.attributes.has(name);
  }
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

  get data() {
    return this.nodeValue || "";
  }

  set data(value: string) {
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
  hidden = false;
  visibilityState = "visible";
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
const windowTarget = new TestEventTarget() as TestEventTarget & {
  document: TestDocument;
  fetch: typeof fetch;
  location: { href: string; pathname: string; search: string; hash: string };
  localStorage: Storage;
  clearTimeout: typeof clearTimeout;
  setTimeout: typeof setTimeout;
};
windowTarget.document = document;
windowTarget.fetch = globalThis.fetch;
windowTarget.location = { href: "http://test.local/", pathname: "/", search: "", hash: "" };
windowTarget.localStorage = {
  getItem: (key) => storage.get(key) || null,
  setItem: (key, value) => storage.set(key, value),
  removeItem: (key) => storage.delete(key),
  clear: () => storage.clear(),
  key: (index) => [...storage.keys()][index] || null,
  get length() {
    return storage.size;
  },
};
windowTarget.clearTimeout = clearTimeout;
windowTarget.setTimeout = setTimeout;
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
  HTMLInputElement: TestElement,
  HTMLTextAreaElement: TestElement,
  HTMLSelectElement: TestElement,
  SVGElement: TestElement,
  CustomEvent: class {
    constructor(readonly type: string, readonly init?: { detail?: unknown }) {}
  },
  IS_REACT_ACT_ENVIRONMENT: true,
});
Object.defineProperty(globalThis, "navigator", {
  configurable: true,
  value: { userAgent: "node" },
});

const adminUser: User = {
  id: "admin-a",
  tenant_id: "tenant-a",
  username: "admin-a",
  email: "admin-a@example.test",
  roles: [],
  permissions: ["settings:manage"],
  is_admin: true,
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function textOf(node: TestNode): string {
  if (node.nodeValue !== null) return node.nodeValue;
  const childText = node.childNodes.map(textOf).join("");
  return childText || node.textContent;
}

function findButton(node: TestNode, label: string): TestElement | null {
  if (node instanceof TestElement && node.tagName === "button" && textOf(node) === label) {
    return node;
  }
  for (const child of node.childNodes) {
    const button = findButton(child, label);
    if (button) return button;
  }
  return null;
}

function findRefreshButton(node: TestNode): TestElement | null {
  if (
    node instanceof TestElement &&
    node.tagName === "button" &&
    node.hasAttribute("aria-label")
  ) {
    return node;
  }
  for (const child of node.childNodes) {
    const button = findRefreshButton(child);
    if (button) return button;
  }
  return null;
}

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function mountInbox(user: User, client: AdminToolPermissionInboxClient) {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { AuthProvider, useAuth } = await import("../../../hooks/useAuth.tsx");
  const { authApi } = await import("../../../services/api/auth.ts");
  const { AdminToolPermissionInboxSection } = await import("../AdminToolPermissionInboxSection.tsx");
  const originalGetCurrentUser = authApi.getCurrentUser;
  let currentUser = user;
  let refreshAuthenticatedUser: (() => Promise<void>) | null = null;
  authApi.getCurrentUser = async () => currentUser;
  const container = document.createElement("div");
  const root = createRoot(container as never);
  function AuthRefreshCapture() {
    refreshAuthenticatedUser = useAuth().refreshUser;
    return null;
  }
  const renderClient = (nextClient: AdminToolPermissionInboxClient) =>
    React.createElement(
      AuthProvider,
      null,
      React.createElement(
        React.Fragment,
        null,
        React.createElement(AuthRefreshCapture),
        React.createElement(AdminToolPermissionInboxSection, { client: nextClient }),
      ),
    );
  await React.act(async () => {
    root.render(renderClient(client));
    await Promise.resolve();
    await Promise.resolve();
  });
  return {
    React,
    container,
    async rerender(nextClient: AdminToolPermissionInboxClient) {
      await React.act(async () => {
        root.render(renderClient(nextClient));
        await Promise.resolve();
        await Promise.resolve();
      });
    },
    async switchUser(nextUser: User) {
      currentUser = nextUser;
      assert.ok(refreshAuthenticatedUser, "auth refresh seam is mounted");
      await React.act(async () => {
        await refreshAuthenticatedUser?.();
        await Promise.resolve();
        await Promise.resolve();
      });
    },
    async cleanup() {
      await React.act(async () => root.unmount());
      authApi.getCurrentUser = originalGetCurrentUser;
    },
  };
}

test("administrator inbox fetches, renders and decides only through its tenant inbox client", async () => {
  let listCalls = 0;
  const decisions: Array<[string, string]> = [];
  const client: AdminToolPermissionInboxClient = {
    list: async () => {
      listCalls += 1;
      return {
        permission_requests: listCalls === 1
          ? [{
              request_id: "tpr-a",
              run_id: "run-owner",
              tool_id: "customer-write",
              tool_display: "customer-write",
              risk_level: "high",
              write_capable: true,
              status: "pending",
              allowed_decisions: ["allow_once", "deny"],
            }]
          : [],
        total: listCalls === 1 ? 1 : 0,
        status: "pending",
        limit: 50,
      };
    },
    decide: async (requestId, decision) => {
      decisions.push([requestId, decision]);
    },
  };
  const mounted = await mountInbox(adminUser, client);
  try {
    assert.equal(listCalls, 1);
    assert.match(textOf(mounted.container), /customer-write/);
    const deny = findButton(mounted.container, "拒绝");
    assert.ok(deny, "administrator sees a governed deny button");
    await mounted.React.act(async () => {
      mounted.container.dispatchEvent({
        type: "click",
        target: deny,
        button: 0,
        preventDefault() {},
      } as never);
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.deepEqual(decisions, [["tpr-a", "deny"]]);
    assert.equal(listCalls, 2);
    assert.doesNotMatch(textOf(mounted.container), /customer-write/);
  } finally {
    await mounted.cleanup();
  }
});

test("administrator inbox ignores an aborted stale refresh generation", async () => {
  const staleResponse = createDeferred<Awaited<ReturnType<AdminToolPermissionInboxClient["list"]>>>();
  let staleSignal: AbortSignal | undefined;
  const firstClient: AdminToolPermissionInboxClient = {
    list: async (signal) => {
      staleSignal = signal;
      return staleResponse.promise;
    },
    decide: async () => undefined,
  };
  const currentClient: AdminToolPermissionInboxClient = {
    list: async () => ({
      permission_requests: [],
      total: 0,
      status: "pending",
      limit: 50,
    }),
    decide: async () => undefined,
  };
  const mounted = await mountInbox(adminUser, firstClient);
  try {
    await mounted.rerender(currentClient);
    staleResponse.resolve({
      permission_requests: [{
        request_id: "tpr-stale",
        run_id: "run-stale",
        tool_id: "stale-write",
        tool_display: "stale-write",
        risk_level: "high",
        write_capable: true,
        status: "pending",
        allowed_decisions: ["allow_once", "deny"],
      }],
      total: 1,
      status: "pending",
      limit: 50,
    });
    await mounted.React.act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.equal(staleSignal?.aborted, true);
    assert.doesNotMatch(textOf(mounted.container), /stale-write/);
  } finally {
    await mounted.cleanup();
  }
});

test("governance subject switch ignores a deferred tenant A 403 refresh", async () => {
  const staleTenantA = createDeferred<Awaited<ReturnType<AdminToolPermissionInboxClient["list"]>>>();
  const currentTenantB = createDeferred<Awaited<ReturnType<AdminToolPermissionInboxClient["list"]>>>();
  const signals: Array<AbortSignal | undefined> = [];
  let listCalls = 0;
  const client: AdminToolPermissionInboxClient = {
    list: async (signal) => {
      listCalls += 1;
      signals.push(signal);
      if (listCalls === 1) {
        return {
          permission_requests: [{
            request_id: "tpr-a",
            run_id: "run-a",
            tool_id: "tenant-a-write",
            tool_display: "tenant-a-write",
            risk_level: "high",
            write_capable: true,
            status: "pending",
            allowed_decisions: ["allow_once", "deny"],
          }],
          total: 1,
          status: "pending",
          limit: 50,
        };
      }
      return listCalls === 2 ? staleTenantA.promise : currentTenantB.promise;
    },
    decide: async () => undefined,
  };
  const mounted = await mountInbox(adminUser, client);
  try {
    assert.match(textOf(mounted.container), /tenant-a-write/);
    const refreshButton = findRefreshButton(mounted.container);
    assert.ok(refreshButton);
    await mounted.React.act(async () => {
      mounted.container.dispatchEvent({
        type: "click",
        target: refreshButton,
        button: 0,
        preventDefault() {},
      } as never);
      await Promise.resolve();
    });
    assert.equal(listCalls, 2);

    await mounted.switchUser({
      ...adminUser,
      id: "admin-b",
      username: "admin-b",
      tenant_id: "tenant-b",
    });

    assert.equal(listCalls, 3);
    assert.equal(signals[1]?.aborted, true);
    assert.doesNotMatch(textOf(mounted.container), /tenant-a-write/);

    staleTenantA.reject(
      new ApiRequestError("private-tenant-a-denial", 403, "not_ai_admin"),
    );
    currentTenantB.resolve({
      permission_requests: [{
        request_id: "tpr-b",
        run_id: "run-b",
        tool_id: "tenant-b-read",
        tool_display: "tenant-b-read",
        risk_level: "low",
        write_capable: false,
        status: "pending",
        allowed_decisions: ["allow_once", "deny"],
      }],
      total: 1,
      status: "pending",
      limit: 50,
    });
    await mounted.React.act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.match(textOf(mounted.container), /tenant-b-read/);
    assert.doesNotMatch(textOf(mounted.container), /private-tenant-a-denial|当前账号无权/);
  } finally {
    await mounted.cleanup();
  }
});

test("current refresh 401 revokes and clears loaded inbox actions", async () => {
  let listCalls = 0;
  let denialSignal: AbortSignal | undefined;
  const client: AdminToolPermissionInboxClient = {
    list: async (signal) => {
      listCalls += 1;
      if (listCalls > 1) {
        denialSignal = signal;
        throw new ApiRequestError("private-auth-detail", 401, "session_expired");
      }
      return {
        permission_requests: [{
          request_id: "tpr-auth-refresh",
          run_id: "run-owner",
          tool_id: "loaded-before-refresh-denial",
          tool_display: "loaded-before-refresh-denial",
          risk_level: "high",
          write_capable: true,
          status: "pending",
          allowed_decisions: ["allow_once", "deny"],
        }],
        total: 1,
        status: "pending",
        limit: 50,
      };
    },
    decide: async () => undefined,
  };
  const mounted = await mountInbox(adminUser, client);
  try {
    assert.match(textOf(mounted.container), /loaded-before-refresh-denial/);
    const refreshButton = findRefreshButton(mounted.container);
    assert.ok(refreshButton);
    await mounted.React.act(async () => {
      mounted.container.dispatchEvent({
        type: "click",
        target: refreshButton,
        button: 0,
        preventDefault() {},
      } as never);
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.equal(denialSignal?.aborted, true);
    assert.doesNotMatch(textOf(mounted.container), /loaded-before-refresh-denial|允许一次|拒绝/);
    assert.match(textOf(mounted.container), /当前账号无权处理工具权限请求/);
    assert.equal(findRefreshButton(mounted.container)?.hasAttribute("disabled"), true);
    assert.doesNotMatch(textOf(mounted.container), /private-auth-detail/);
  } finally {
    await mounted.cleanup();
  }
});

test("current decision 403 revokes and clears loaded inbox actions", async () => {
  let decisionSignal: AbortSignal | undefined;
  const client: AdminToolPermissionInboxClient = {
    list: async () => ({
      permission_requests: [{
        request_id: "tpr-auth-decision",
        run_id: "run-owner",
        tool_id: "loaded-before-decision-denial",
        tool_display: "loaded-before-decision-denial",
        risk_level: "high",
        write_capable: true,
        status: "pending",
        allowed_decisions: ["allow_once", "deny"],
      }],
      total: 1,
      status: "pending",
      limit: 50,
    }),
    decide: async (_requestId, _decision, signal) => {
      decisionSignal = signal;
      throw new ApiRequestError(
        "private-permission-detail",
        403,
        "missing_permission:settings:manage",
      );
    },
  };
  const mounted = await mountInbox(adminUser, client);
  try {
    const deny = findButton(mounted.container, "拒绝");
    assert.ok(deny);
    await mounted.React.act(async () => {
      mounted.container.dispatchEvent({
        type: "click",
        target: deny,
        button: 0,
        preventDefault() {},
      } as never);
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.equal(decisionSignal?.aborted, true);
    assert.doesNotMatch(textOf(mounted.container), /loaded-before-decision-denial|允许一次|拒绝/);
    assert.match(textOf(mounted.container), /当前账号无权处理工具权限请求/);
    assert.equal(findRefreshButton(mounted.container)?.hasAttribute("disabled"), true);
    assert.doesNotMatch(textOf(mounted.container), /private-permission-detail/);
  } finally {
    await mounted.cleanup();
  }
});

test("current-subject already-decided conflict removes the stale pending action", async () => {
  const conflict = createDeferred<never>();
  let listCalls = 0;
  const client: AdminToolPermissionInboxClient = {
    list: async () => {
      listCalls += 1;
      return {
        permission_requests: listCalls === 1
          ? [{
              request_id: "tpr-conflict",
              run_id: "run-owner",
              tool_id: "conflicted-write",
              tool_display: "conflicted-write",
              risk_level: "high",
              write_capable: true,
              status: "pending",
              allowed_decisions: ["allow_once", "deny"],
            }]
          : [],
        total: listCalls === 1 ? 1 : 0,
        status: "pending",
        limit: 50,
      };
    },
    decide: async () => conflict.promise,
  };
  const mounted = await mountInbox(adminUser, client);
  try {
    const deny = findButton(mounted.container, "拒绝");
    assert.ok(deny);
    await mounted.React.act(async () => {
      mounted.container.dispatchEvent({
        type: "click",
        target: deny,
        button: 0,
        preventDefault() {},
      } as never);
      await Promise.resolve();
      conflict.reject(
        new ApiRequestError(
          "private-server-detail",
          409,
          "tool_permission_request_not_pending",
        ),
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.equal(listCalls, 2);
    assert.doesNotMatch(textOf(mounted.container), /conflicted-write|该权限请求已被处理/);
  } finally {
    await mounted.cleanup();
  }
});

test("stale tenant A decision conflict cannot remove tenant B state", async () => {
  const staleDecision = createDeferred<never>();
  let decisionSignal: AbortSignal | undefined;
  let listCalls = 0;
  const client: AdminToolPermissionInboxClient = {
    list: async () => {
      listCalls += 1;
      return {
        permission_requests: [{
          request_id: "tpr-shared",
          run_id: listCalls === 1 ? "run-a" : "run-b",
          tool_id: listCalls === 1 ? "tenant-a-write" : "tenant-b-write",
          tool_display: listCalls === 1 ? "tenant-a-write" : "tenant-b-write",
          risk_level: "high",
          write_capable: true,
          status: "pending",
          allowed_decisions: ["allow_once", "deny"],
        }],
        total: 1,
        status: "pending",
        limit: 50,
      };
    },
    decide: async (_requestId, _decision, signal?: AbortSignal) => {
      decisionSignal = signal;
      return staleDecision.promise;
    },
  };
  const mounted = await mountInbox(adminUser, client);
  try {
    const deny = findButton(mounted.container, "拒绝");
    assert.ok(deny);
    await mounted.React.act(async () => {
      mounted.container.dispatchEvent({
        type: "click",
        target: deny,
        button: 0,
        preventDefault() {},
      } as never);
      await Promise.resolve();
    });

    await mounted.switchUser({
      ...adminUser,
      id: "admin-b",
      username: "admin-b",
      tenant_id: "tenant-b",
    });
    assert.equal(decisionSignal?.aborted, true);
    assert.match(textOf(mounted.container), /tenant-b-write/);

    staleDecision.reject(
      new ApiRequestError(
        "private-server-detail",
        409,
        "tool_permission_request_not_pending",
      ),
    );
    await mounted.React.act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.match(textOf(mounted.container), /tenant-b-write/);
    assert.doesNotMatch(textOf(mounted.container), /该权限请求已被处理/);
  } finally {
    await mounted.cleanup();
  }
});

test("permission loss and unmount abort owned inbox work without restoring state", async () => {
  const permissionLossResponse = createDeferred<Awaited<ReturnType<AdminToolPermissionInboxClient["list"]>>>();
  let permissionLossSignal: AbortSignal | undefined;
  const client: AdminToolPermissionInboxClient = {
    list: async (signal) => {
      permissionLossSignal = signal;
      return permissionLossResponse.promise;
    },
    decide: async () => undefined,
  };
  const mounted = await mountInbox(adminUser, client);
  await mounted.switchUser({ ...adminUser, permissions: [] });
  assert.equal(permissionLossSignal?.aborted, true);
  assert.equal(textOf(mounted.container), "");
  permissionLossResponse.resolve({
    permission_requests: [],
    total: 0,
    status: "pending",
    limit: 50,
  });
  await mounted.React.act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
  assert.equal(textOf(mounted.container), "");
  await mounted.cleanup();

  const unmountResponse = createDeferred<Awaited<ReturnType<AdminToolPermissionInboxClient["list"]>>>();
  let unmountSignal: AbortSignal | undefined;
  const unmounted = await mountInbox(adminUser, {
    list: async (signal) => {
      unmountSignal = signal;
      return unmountResponse.promise;
    },
    decide: async () => undefined,
  });
  await unmounted.cleanup();
  assert.equal(unmountSignal?.aborted, true);
  unmountResponse.resolve({
    permission_requests: [],
    total: 0,
    status: "pending",
    limit: 50,
  });
  await Promise.resolve();
  await Promise.resolve();
});

test("administrator inbox disables every decision throughout refresh", () => {
  assert.equal(isInboxDecisionDisabled(true, null), true);
  assert.equal(isInboxDecisionDisabled(true, "tpr-other"), true);
  assert.equal(isInboxDecisionDisabled(false, "tpr-other"), true);
  assert.equal(isInboxDecisionDisabled(false, null), false);
});

test("a lagging refresh cannot resurrect a request after a successful decision", async () => {
  const laggingRefresh = createDeferred<Awaited<ReturnType<AdminToolPermissionInboxClient["list"]>>>();
  let listCalls = 0;
  const pendingResponse: Awaited<ReturnType<AdminToolPermissionInboxClient["list"]>> = {
    permission_requests: [{
      request_id: "tpr-decided",
      run_id: "run-owner",
      tool_id: "customer-write",
      tool_display: "customer-write",
      risk_level: "high",
      write_capable: true,
      status: "pending",
      allowed_decisions: ["allow_once", "deny"],
    }],
    total: 1,
    status: "pending",
    limit: 50,
  };
  const client: AdminToolPermissionInboxClient = {
    list: async () => {
      listCalls += 1;
      if (listCalls === 1) return pendingResponse;
      return laggingRefresh.promise;
    },
    decide: async () => undefined,
  };
  const mounted = await mountInbox(adminUser, client);
  try {
    const deny = findButton(mounted.container, "拒绝");
    assert.ok(deny);
    await mounted.React.act(async () => {
      mounted.container.dispatchEvent({
        type: "click",
        target: deny,
        button: 0,
        preventDefault() {},
      } as never);
      await Promise.resolve();
      laggingRefresh.resolve(pendingResponse);
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(listCalls, 2);
    assert.doesNotMatch(textOf(mounted.container), /customer-write/);
  } finally {
    await mounted.cleanup();
  }
});

test("administrator inbox keeps 403 and unexpected 409 errors localized and free of raw server text", async () => {
  let mode: "forbidden" | "conflict" = "forbidden";
  const client: AdminToolPermissionInboxClient = {
    list: async () => {
      if (mode === "forbidden") {
        throw new ApiRequestError("private-server-detail", 403, "not_ai_admin");
      }
      return {
        permission_requests: [{
          request_id: "tpr-conflict",
          run_id: "run-owner",
          tool_id: "customer-write",
          tool_display: "customer-write",
          risk_level: "high",
          write_capable: false,
          status: "pending",
          allowed_decisions: ["allow_once", "deny"],
        }],
        total: 1,
        status: "pending",
        limit: 50,
      };
    },
    decide: async () => {
      throw new ApiRequestError(
        "private-server-detail",
        409,
        "unexpected_conflict",
      );
    },
  };
  const forbidden = await mountInbox(adminUser, client);
  try {
    assert.match(textOf(forbidden.container), /当前账号无权处理工具权限请求/);
    assert.doesNotMatch(textOf(forbidden.container), /private-server-detail/);
  } finally {
    await forbidden.cleanup();
  }

  mode = "conflict";
  const conflict = await mountInbox(adminUser, client);
  try {
    const deny = findButton(conflict.container, "拒绝");
    assert.ok(deny);
    await conflict.React.act(async () => {
      conflict.container.dispatchEvent({
        type: "click",
        target: deny,
        button: 0,
        preventDefault() {},
      } as never);
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.match(textOf(conflict.container), /工具权限收件箱暂时不可用，请稍后刷新/);
    assert.doesNotMatch(textOf(conflict.container), /private-server-detail/);
  } finally {
    await conflict.cleanup();
  }
});

test("administrator inbox maps the supported-decision conflict code without exposing server text", async () => {
  const client: AdminToolPermissionInboxClient = {
    list: async () => ({
      permission_requests: [{
        request_id: "tpr-unsupported",
        run_id: "run-owner",
        tool_id: "customer-write",
        tool_display: "customer-write",
        risk_level: "high",
        write_capable: true,
        status: "pending",
        allowed_decisions: ["allow_once", "deny"],
      }],
      total: 1,
      status: "pending",
      limit: 50,
    }),
    decide: async () => {
      throw new ApiRequestError(
        "private-server-detail",
        409,
        "tool_permission_decision_not_supported",
      );
    },
  };
  const mounted = await mountInbox(adminUser, client);
  try {
    const deny = findButton(mounted.container, "拒绝");
    assert.ok(deny);
    await mounted.React.act(async () => {
      mounted.container.dispatchEvent({
        type: "click",
        target: deny,
        button: 0,
        preventDefault() {},
      } as never);
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.match(textOf(mounted.container), /该权限请求不支持此决策/);
    assert.doesNotMatch(textOf(mounted.container), /private-server-detail/);
  } finally {
    await mounted.cleanup();
  }
});

test("ordinary users do not render or fetch the administrator inbox", async () => {
  let listCalls = 0;
  const mounted = await mountInbox(
    { ...adminUser, id: "user-a", username: "user-a", is_admin: false },
    {
      list: async () => {
        listCalls += 1;
        throw new Error("ordinary users must not fetch");
      },
      decide: async () => {
        throw new Error("ordinary users must not decide");
      },
    },
  );
  try {
    assert.equal(listCalls, 0);
    assert.doesNotMatch(textOf(mounted.container), /工具权限治理收件箱|允许一次|拒绝/);
  } finally {
    await mounted.cleanup();
  }
});

test("an admin role without settings manage capability does not render or fetch the inbox", async () => {
  let listCalls = 0;
  const mounted = await mountInbox(
    { ...adminUser, permissions: [] },
    {
      list: async () => {
        listCalls += 1;
        throw new Error("capability-less admins must not fetch");
      },
      decide: async () => {
        throw new Error("capability-less admins must not decide");
      },
    },
  );
  try {
    assert.equal(listCalls, 0);
    assert.doesNotMatch(textOf(mounted.container), /工具权限治理收件箱|允许一次|拒绝/);
  } finally {
    await mounted.cleanup();
  }
});
