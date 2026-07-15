import assert from "node:assert/strict";
import test from "node:test";

import type { User } from "../../types/auth.ts";

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

  constructor(readonly tagName: string) {
    super();
  }

  get nodeName() {
    return this.tagName.toUpperCase();
  }

  setAttribute(name: string, value: string) {
    this.attributes.set(name, value);
  }

  removeAttribute(name: string) {
    this.attributes.delete(name);
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
  location: { href: string; pathname: string; search: string; hash: string };
  localStorage: Storage;
  clearTimeout: typeof clearTimeout;
  setTimeout: typeof setTimeout;
};
windowTarget.document = document;
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
    roles: ["admin"],
    permissions: ["settings:manage"],
    is_admin: true,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

async function mountAuthHarness(
  configure: (api: typeof import("../../services/api/auth.ts").authApi) => void,
) {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { AuthProvider, useAuth } = await import("../useAuth.tsx");
  const { authApi } = await import("../../services/api/auth.ts");
  const originals = {
    getCurrentUser: authApi.getCurrentUser,
    login: authApi.login,
    logout: authApi.logout,
    handleOAuthCallback: authApi.handleOAuthCallback,
  };
  configure(authApi);
  storage.clear();
  storage.set("ai_platform_session_present", "test-session-marker");

  let snapshot: ReturnType<typeof useAuth> | null = null;
  function Probe() {
    snapshot = useAuth();
    return null;
  }

  const container = document.createElement("div");
  const root = createRoot(container as never);
  await React.act(async () => {
    root.render(React.createElement(AuthProvider, null, React.createElement(Probe)));
    await Promise.resolve();
  });

  let unmounted = false;
  return {
    React,
    get auth() {
      assert.ok(snapshot, "auth provider probe is mounted");
      return snapshot;
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
      if (!unmounted) await React.act(async () => root.unmount());
      Object.assign(authApi, originals);
      storage.clear();
    },
  };
}

test("a newer login hydration owns auth state over deferred initial principal A", async () => {
  const initialA = deferred<User>();
  let initialSignal: AbortSignal | undefined;
  let loginSignal: AbortSignal | undefined;
  let currentUserCalls = 0;
  const userB = authUser("admin-b", "tenant-b");
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async (options?: { signal?: AbortSignal }) => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) {
        initialSignal = options?.signal;
        return initialA.promise;
      }
      return userB;
    };
    api.login = async (_credentials, _turnstile, signal?: AbortSignal) => {
      loginSignal = signal;
    };
  });
  try {
    await mounted.React.act(async () => {
      await mounted.auth.login({ username: "admin-b", password: "safe-test" });
    });
    initialA.resolve(authUser("admin-a", "tenant-a"));
    await mounted.flush();

    assert.equal(initialSignal?.aborted, true);
    assert.equal(loginSignal?.aborted, false);
    assert.equal(mounted.auth.user?.id, "admin-b");
    assert.equal(mounted.auth.user?.tenant_id, "tenant-b");
  } finally {
    await mounted.cleanup();
  }
});

test("a newer refresh hydration owns auth state over deferred initial principal A", async () => {
  const initialA = deferred<User>();
  let initialSignal: AbortSignal | undefined;
  let currentUserCalls = 0;
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async (options?: { signal?: AbortSignal }) => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) {
        initialSignal = options?.signal;
        return initialA.promise;
      }
      return authUser("admin-b", "tenant-b");
    };
  });
  try {
    await mounted.React.act(async () => mounted.auth.refreshUser());
    initialA.resolve(authUser("admin-a", "tenant-a"));
    await mounted.flush();

    assert.equal(initialSignal?.aborted, true);
    assert.equal(mounted.auth.user?.id, "admin-b");
  } finally {
    await mounted.cleanup();
  }
});

test("logout invalidates deferred initial hydration and leaves no principal", async () => {
  const initialA = deferred<User>();
  let initialSignal: AbortSignal | undefined;
  let logoutSignal: AbortSignal | undefined;
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async (options?: { signal?: AbortSignal }) => {
      initialSignal = options?.signal;
      return initialA.promise;
    };
    api.logout = async (signal?: AbortSignal) => {
      logoutSignal = signal;
    };
  });
  try {
    await mounted.React.act(async () => {
      assert.equal(await mounted.auth.logout(), true);
    });
    initialA.resolve(authUser("admin-a", "tenant-a"));
    await mounted.flush();

    assert.equal(initialSignal?.aborted, true);
    assert.equal(logoutSignal?.aborted, false);
    assert.equal(mounted.auth.user, null);
  } finally {
    await mounted.cleanup();
  }
});

test("OAuth completion hydration owns auth state over deferred initial principal A", async () => {
  const initialA = deferred<User>();
  let initialSignal: AbortSignal | undefined;
  let oauthSignal: AbortSignal | undefined;
  let currentUserCalls = 0;
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async (options?: { signal?: AbortSignal }) => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) {
        initialSignal = options?.signal;
        return initialA.promise;
      }
      return authUser("oauth-b", "tenant-b");
    };
    api.handleOAuthCallback = async (_provider, _code, _state, signal?: AbortSignal) => {
      oauthSignal = signal;
      return { access_token: "cookie-session", token_type: "bearer" };
    };
  });
  try {
    await mounted.React.act(async () => {
      await mounted.auth.handleOAuthCallback("github", "code", "state");
    });
    initialA.resolve(authUser("admin-a", "tenant-a"));
    await mounted.flush();

    assert.equal(initialSignal?.aborted, true);
    assert.equal(oauthSignal?.aborted, false);
    assert.equal(mounted.auth.user?.id, "oauth-b");
  } finally {
    await mounted.cleanup();
  }
});

test("unmount aborts pending initial hydration without an unhandled rejection", async () => {
  let initialSignal: AbortSignal | undefined;
  const unhandled: unknown[] = [];
  const onUnhandled = (error: unknown) => unhandled.push(error);
  process.on("unhandledRejection", onUnhandled);
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async (options?: { signal?: AbortSignal }) => {
      initialSignal = options?.signal;
      return new Promise<User>((_resolve, reject) => {
        options?.signal?.addEventListener("abort", () => {
          reject(new DOMException("aborted", "AbortError"));
        });
      });
    };
  });
  try {
    await mounted.unmount();
    await Promise.resolve();
    await Promise.resolve();

    assert.equal(initialSignal?.aborted, true);
    assert.deepEqual(unhandled, []);
  } finally {
    process.off("unhandledRejection", onUnhandled);
    await mounted.cleanup();
  }
});
