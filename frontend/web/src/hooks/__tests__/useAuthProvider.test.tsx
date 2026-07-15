import assert from "node:assert/strict";
import test from "node:test";

import { ApiRequestError } from "../../services/api/fetch.ts";
import { registerAuthScopedCacheClearer } from "../../services/api/authCacheInvalidation.ts";
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
  readonly style = Object.assign({}, {
    setProperty: (name: string, value: string) => {
      (this.style as unknown as Record<string, unknown>)[name] = value;
    },
    removeProperty: (name: string) => {
      delete (this.style as unknown as Record<string, unknown>)[name];
    },
  }) as unknown as CSSStyleDeclaration;
  readonly attributes = new Map<string, string>();
  ownerDocument!: TestDocument;
  className = "";
  id = "";
  value = "";
  checked = false;
  private readonly classes = new Set<string>();
  readonly classList = {
    add: (...names: string[]) => names.forEach((name) => this.classes.add(name)),
    remove: (...names: string[]) => names.forEach((name) => this.classes.delete(name)),
    contains: (name: string) => this.classes.has(name),
    toggle: (name: string, force?: boolean) => {
      const next = force ?? !this.classes.has(name);
      if (next) this.classes.add(name);
      else this.classes.delete(name);
      return next;
    },
  };

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
    if (tagName.toLowerCase() === "style") {
      element.appendChild(this.createTextNode(""));
    }
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
const sessionStorageValues = new Map<string, string>();
const windowTarget = new TestEventTarget() as TestEventTarget & {
  document: TestDocument;
  location: { href: string; pathname: string; search: string; hash: string };
  localStorage: Storage;
  sessionStorage: Storage;
  clearTimeout: typeof clearTimeout;
  setTimeout: typeof setTimeout;
  matchMedia: (query: string) => MediaQueryList;
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
windowTarget.sessionStorage = {
  getItem: (key) => sessionStorageValues.get(key) || null,
  setItem: (key, value) => sessionStorageValues.set(key, value),
  removeItem: (key) => sessionStorageValues.delete(key),
  clear: () => sessionStorageValues.clear(),
  key: (index) => [...sessionStorageValues.keys()][index] || null,
  get length() {
    return sessionStorageValues.size;
  },
};
windowTarget.clearTimeout = clearTimeout;
windowTarget.setTimeout = setTimeout;
windowTarget.matchMedia = (query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addListener() {},
  removeListener() {},
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent: () => true,
});
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
  sessionStorage: windowTarget.sessionStorage,
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

function findElements(root: TestNode, tagName: string): TestElement[] {
  const matches: TestElement[] = [];
  const visit = (node: TestNode) => {
    if (node instanceof TestElement && node.tagName === tagName) matches.push(node);
    node.childNodes.forEach(visit);
  };
  visit(root);
  return matches;
}

function reactProps(element: TestElement): Record<string, (...args: never[]) => unknown> {
  const key = Object.keys(element).find((name) => name.startsWith("__reactProps$"));
  assert.ok(key, `React props are attached to <${element.tagName}>`);
  return (element as unknown as Record<string, Record<string, (...args: never[]) => unknown>>)[key];
}

async function mountAuthPageHarness(
  configure: (api: typeof import("../../services/api/auth.ts").authApi) => void,
) {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { AuthProvider, useAuth } = await import("../useAuth.tsx");
  const { ThemeProvider } = await import("../../contexts/ThemeContext.tsx");
  const { AuthPage } = await import("../../components/auth/AuthPage.tsx");
  const { authApi } = await import("../../services/api/auth.ts");
  const toast = (await import("react-hot-toast")).default;
  const originals = {
    getCurrentUser: authApi.getCurrentUser,
    login: authApi.login,
    logout: authApi.logout,
    getOAuthProviders: authApi.getOAuthProviders,
    updateMetadata: authApi.updateMetadata,
    toastSuccess: toast.success,
    toastError: toast.error,
  };
  configure(authApi);
  authApi.getOAuthProviders = async () => ({
    providers: [],
    registration_enabled: true,
    turnstile: {
      enabled: false,
      site_key: "",
      require_on_login: false,
      require_on_register: false,
      require_on_password_change: false,
    },
  });
  authApi.updateMetadata = async () => authUser("admin-a", "tenant-a");
  storage.clear();
  storage.set("ai_platform_session_present", "test-session-marker");

  let snapshot: ReturnType<typeof useAuth> | null = null;
  const successfulRedirects: Array<string | undefined> = [];
  const successToasts: unknown[] = [];
  const errorToasts: unknown[] = [];
  toast.success = ((message: unknown) => {
    successToasts.push(message);
    return "test-toast";
  }) as typeof toast.success;
  toast.error = ((message: unknown) => {
    errorToasts.push(message);
    return "test-error-toast";
  }) as typeof toast.error;
  function Probe() {
    snapshot = useAuth();
    return null;
  }

  const container = document.createElement("div");
  const root = createRoot(container as never);
  await React.act(async () => {
    root.render(
      React.createElement(
        ThemeProvider,
        null,
        React.createElement(
          AuthProvider,
          null,
          React.createElement(
            React.Fragment,
            null,
            React.createElement(Probe),
            React.createElement(AuthPage, {
              onSuccess: (path?: string) => successfulRedirects.push(path),
            }),
          ),
        ),
      ),
    );
    await Promise.resolve();
    await Promise.resolve();
  });

  return {
    React,
    container,
    successfulRedirects,
    successToasts,
    errorToasts,
    get auth() {
      assert.ok(snapshot, "auth page provider probe is mounted");
      return snapshot;
    },
    async cleanup() {
      await React.act(async () => root.unmount());
      Object.assign(authApi, {
        getCurrentUser: originals.getCurrentUser,
        login: originals.login,
        logout: originals.logout,
        getOAuthProviders: originals.getOAuthProviders,
        updateMetadata: originals.updateMetadata,
      });
      toast.success = originals.toastSuccess;
      toast.error = originals.toastError;
      storage.clear();
    },
  };
}

async function mountOAuthCallbackHarness(
  configure: (api: typeof import("../../services/api/auth.ts").authApi) => void,
) {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { MemoryRouter, useLocation } = await import("react-router-dom");
  const { AuthProvider } = await import("../useAuth.tsx");
  const { OAuthCallback } = await import("../../components/auth/OAuthCallback.tsx");
  const { authApi } = await import("../../services/api/auth.ts");
  const originalGetCurrentUser = authApi.getCurrentUser;
  const originalLogout = authApi.logout;
  configure(authApi);
  storage.clear();
  sessionStorageValues.clear();
  storage.set("ai_platform_session_present", "test-session-marker");
  windowTarget.location.hash = "#access_token=test-access&refresh_token=test-refresh";

  let setVisible: ((visible: boolean) => void) | null = null;
  let currentPath = "";
  let currentSearch = "";
  function LocationProbe() {
    const location = useLocation();
    currentPath = location.pathname;
    currentSearch = location.search;
    return null;
  }
  function CallbackGate() {
    const [visible, setVisibleState] = React.useState(false);
    setVisible = setVisibleState;
    return visible ? React.createElement(OAuthCallback) : null;
  }

  const container = document.createElement("div");
  const root = createRoot(container as never);
  await React.act(async () => {
    root.render(
      React.createElement(
        AuthProvider,
        null,
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/auth/oauth/callback"] },
          React.createElement(
            React.Fragment,
            null,
            React.createElement(LocationProbe),
            React.createElement(CallbackGate),
          ),
        ),
      ),
    );
    await Promise.resolve();
    await Promise.resolve();
  });

  return {
    React,
    get currentPath() {
      return currentPath;
    },
    get currentSearch() {
      return currentSearch;
    },
    async show() {
      assert.ok(setVisible);
      await React.act(async () => {
        setVisible?.(true);
        await Promise.resolve();
        await Promise.resolve();
      });
    },
    async hide() {
      assert.ok(setVisible);
      await React.act(async () => {
        setVisible?.(false);
        await Promise.resolve();
      });
    },
    async logout() {
      await React.act(async () => {
        windowTarget.dispatchEvent({ type: "auth:logout" });
        await Promise.resolve();
      });
    },
    async flush() {
      await React.act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
    },
    async cleanup() {
      await React.act(async () => root.unmount());
      authApi.getCurrentUser = originalGetCurrentUser;
      authApi.logout = originalLogout;
      storage.clear();
      sessionStorageValues.clear();
      windowTarget.location.hash = "";
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

test("a stale initial 401 cannot log out a newer authenticated principal", async () => {
  const initialA = deferred<User>();
  let currentUserCalls = 0;
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async () => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) return initialA.promise;
      return authUser("admin-b", "tenant-b");
    };
    api.login = async () => undefined;
  });
  try {
    await mounted.React.act(async () => {
      await mounted.auth.login({ username: "admin-b", password: "safe-test" });
    });
    initialA.reject(new ApiRequestError("unauthorized", 401, "unauthorized"));
    await mounted.flush();

    assert.equal(mounted.auth.user?.id, "admin-b");
    assert.equal(mounted.auth.user?.tenant_id, "tenant-b");
    assert.equal(storage.has("ai_platform_session_present"), true);
  } finally {
    await mounted.cleanup();
  }
});

test("the current auth owner handles 401 by clearing only its local session state", async () => {
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async () => {
      throw new ApiRequestError("unauthorized", 401, "unauthorized");
    };
  });
  try {
    assert.equal(mounted.auth.user, null);
    assert.equal(mounted.auth.isAuthenticated, false);
    assert.equal(storage.has("ai_platform_session_present"), false);
  } finally {
    await mounted.cleanup();
  }
});

test("cross-tab marker replacement clears principal and caches before hydrating the new subject", async () => {
  const replacement = deferred<User>();
  let currentUserCalls = 0;
  let replacementSignal: AbortSignal | undefined;
  let cacheClears = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    cacheClears += 1;
  });
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async (options?: { signal?: AbortSignal }) => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) return authUser("admin-a", "tenant-a");
      replacementSignal = options?.signal;
      return replacement.promise;
    };
  });
  try {
    assert.equal(mounted.auth.user?.id, "admin-a");
    await mounted.React.act(async () => {
      storage.set("ai_platform_session_present", "marker-b");
      windowTarget.dispatchEvent(
        Object.assign(
          { type: "storage" },
          {
            key: "ai_platform_session_present",
            oldValue: "test-session-marker",
            newValue: "marker-b",
          },
        ),
      );
      await Promise.resolve();
    });

    assert.equal(mounted.auth.user, null);
    assert.equal(mounted.auth.token, null);
    assert.deepEqual(mounted.auth.permissions, []);
    assert.equal(cacheClears, 1);
    assert.equal(storage.get("ai_platform_session_present"), "marker-b");
    assert.equal(replacementSignal?.aborted, false);

    replacement.resolve(authUser("admin-b", "tenant-b"));
    await mounted.flush();
    assert.equal((mounted.auth.user as User | null)?.id, "admin-b");
    assert.equal((mounted.auth.user as User | null)?.tenant_id, "tenant-b");
    assert.equal(storage.get("ai_platform_session_present"), "marker-b");
  } finally {
    unregister();
    await mounted.cleanup();
  }
});

test("current cross-tab replacement hydration failure removes the replacement marker", async () => {
  let currentUserCalls = 0;
  let cacheClears = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    cacheClears += 1;
  });
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async () => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) return authUser("admin-a", "tenant-a");
      throw new Error("replacement transport unavailable");
    };
  });
  try {
    await mounted.React.act(async () => {
      storage.set("ai_platform_session_present", "marker-b-failed");
      windowTarget.dispatchEvent(
        Object.assign(
          { type: "storage" },
          {
            key: "ai_platform_session_present",
            oldValue: "test-session-marker",
            newValue: "marker-b-failed",
          },
        ),
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.equal(mounted.auth.user, null);
    assert.equal(mounted.auth.token, null);
    assert.deepEqual(mounted.auth.permissions, []);
    assert.equal(storage.has("ai_platform_session_present"), false);
    assert.equal(cacheClears, 2);
  } finally {
    unregister();
    await mounted.cleanup();
  }
});

test("marker epoch fences a stale 401 before its storage event can clear the new subject", async () => {
  const initial = deferred<User>();
  let cacheClears = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    cacheClears += 1;
  });
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async () => initial.promise;
  });
  try {
    storage.set("ai_platform_session_present", "marker-b");
    initial.reject(new ApiRequestError("safe", 401, "unauthorized"));
    await mounted.flush();

    assert.equal(storage.get("ai_platform_session_present"), "marker-b");
    assert.equal(cacheClears, 0);
    assert.equal(mounted.auth.user, null);
  } finally {
    unregister();
    await mounted.cleanup();
  }
});

test("login hydration rollback preserves the original error and fails closed when logout fails", async () => {
  const hydrationError = new ApiRequestError("safe hydration failure", 500);
  const logoutError = new Error("logout transport failure");
  let currentUserCalls = 0;
  let logoutCalls = 0;
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async () => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) return authUser("admin-a", "tenant-a");
      throw hydrationError;
    };
    api.login = async () => undefined;
    api.logout = async () => {
      logoutCalls += 1;
      throw logoutError;
    };
  });
  try {
    await mounted.React.act(async () => {
      await assert.rejects(
        () => mounted.auth.login({ username: "admin-b", password: "safe-test" }),
        (error: unknown) => error === hydrationError,
      );
    });

    assert.equal(logoutCalls, 1);
    assert.equal(mounted.auth.user, null);
    assert.equal(mounted.auth.token, null);
    assert.deepEqual(mounted.auth.permissions, []);
    assert.equal(storage.has("ai_platform_session_present"), false);
  } finally {
    await mounted.cleanup();
  }
});

test("current login rollback fails closed for logout success, 500, network, and abort", async () => {
  const logoutOutcomes: Array<unknown | null> = [
    null,
    new ApiRequestError("safe logout failure", 500),
    new Error("network unavailable"),
    new DOMException("aborted", "AbortError"),
  ];

  for (const [index, logoutFailure] of logoutOutcomes.entries()) {
    const hydrationError = new ApiRequestError(
      `safe hydration failure ${index}`,
      500,
    );
    let currentUserCalls = 0;
    let logoutCalls = 0;
    const mounted = await mountAuthHarness((api) => {
      api.getCurrentUser = async () => {
        currentUserCalls += 1;
        if (currentUserCalls === 1) return authUser("admin-a", "tenant-a");
        throw hydrationError;
      };
      api.login = async () => undefined;
      api.logout = async () => {
        logoutCalls += 1;
        if (logoutFailure) throw logoutFailure;
      };
    });
    try {
      await mounted.React.act(async () => {
        await assert.rejects(
          () => mounted.auth.login({ username: "admin-b", password: "safe-test" }),
          (error: unknown) => error === hydrationError,
        );
      });
      assert.equal(logoutCalls, 1);
      assert.equal(mounted.auth.user, null);
      assert.equal(mounted.auth.token, null);
      assert.deepEqual(mounted.auth.permissions, []);
      assert.equal(storage.has("ai_platform_session_present"), false);
    } finally {
      await mounted.cleanup();
    }
  }
});

test("OAuth code-state hydration rollback preserves the original error when logout fails", async () => {
  const hydrationError = new ApiRequestError("safe OAuth hydration failure", 500);
  let currentUserCalls = 0;
  let logoutCalls = 0;
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async () => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) return authUser("admin-a", "tenant-a");
      throw hydrationError;
    };
    api.handleOAuthCallback = async () => ({
      access_token: "cookie-session",
      token_type: "bearer",
    });
    api.logout = async () => {
      logoutCalls += 1;
      throw new Error("OAuth rollback logout failure");
    };
  });
  try {
    await mounted.React.act(async () => {
      await assert.rejects(
        () => mounted.auth.handleOAuthCallback("github", "code", "state"),
        (error: unknown) => error === hydrationError,
      );
    });

    assert.equal(logoutCalls, 1);
    assert.equal(mounted.auth.user, null);
    assert.equal(mounted.auth.token, null);
    assert.deepEqual(mounted.auth.permissions, []);
    assert.equal(storage.has("ai_platform_session_present"), false);
  } finally {
    await mounted.cleanup();
  }
});

test("marker replacement before rollback skips server logout and preserves the replacement subject", async () => {
  const loginHydration = deferred<User>();
  const hydrationError = new ApiRequestError("safe hydration failure", 500);
  let currentUserCalls = 0;
  let logoutCalls = 0;
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async () => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) return authUser("admin-a", "tenant-a");
      return loginHydration.promise;
    };
    api.login = async () => undefined;
    api.logout = async () => {
      logoutCalls += 1;
    };
  });
  try {
    let loginPromise!: ReturnType<typeof mounted.auth.login>;
    await mounted.React.act(async () => {
      loginPromise = mounted.auth.login({
        username: "admin-b",
        password: "safe-test",
      });
      await Promise.resolve();
      await Promise.resolve();
    });
    storage.set("ai_platform_session_present", "marker-cross-tab-b");
    loginHydration.reject(hydrationError);
    const outcome = await loginPromise;
    await mounted.flush();

    assert.deepEqual(outcome, { status: "cancelled" });
    assert.equal(logoutCalls, 0);
    assert.equal(storage.get("ai_platform_session_present"), "marker-cross-tab-b");
    assert.equal(mounted.auth.user?.id, "admin-a");
  } finally {
    await mounted.cleanup();
  }
});

test("a login hydration superseded by logout returns an explicit cancelled outcome", async () => {
  const loginHydration = deferred<User>();
  let currentUserCalls = 0;
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async () => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) return authUser("admin-a", "tenant-a");
      return loginHydration.promise;
    };
    api.login = async () => undefined;
    api.logout = async () => undefined;
  });
  try {
    let loginPromise!: ReturnType<typeof mounted.auth.login>;
    await mounted.React.act(async () => {
      loginPromise = mounted.auth.login({
        username: "admin-b",
        password: "safe-test",
      });
      await Promise.resolve();
    });
    await mounted.React.act(async () => {
      await mounted.auth.logout();
    });
    loginHydration.resolve(authUser("admin-b", "tenant-b"));
    const outcome = await loginPromise;
    await mounted.flush();

    assert.deepEqual(outcome, { status: "cancelled" });
    assert.equal(mounted.auth.user, null);
  } finally {
    await mounted.cleanup();
  }
});

test("a refresh superseded by login returns cancelled instead of normal completion", async () => {
  const staleRefresh = deferred<User>();
  let currentUserCalls = 0;
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async () => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) return authUser("admin-a", "tenant-a");
      if (currentUserCalls === 2) return staleRefresh.promise;
      return authUser("admin-b", "tenant-b");
    };
    api.login = async () => undefined;
  });
  try {
    let refreshPromise!: ReturnType<typeof mounted.auth.refreshUser>;
    await mounted.React.act(async () => {
      refreshPromise = mounted.auth.refreshUser();
      await Promise.resolve();
    });
    await mounted.React.act(async () => {
      await mounted.auth.login({ username: "admin-b", password: "safe-test" });
    });
    staleRefresh.resolve(authUser("admin-stale", "tenant-stale"));
    const outcome = await refreshPromise;
    await mounted.flush();

    assert.deepEqual(outcome, { status: "cancelled" });
    assert.equal(mounted.auth.user?.id, "admin-b");
  } finally {
    await mounted.cleanup();
  }
});

test("an OAuth hydration superseded by logout returns an explicit cancelled outcome", async () => {
  const oauthHydration = deferred<User>();
  let currentUserCalls = 0;
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async () => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) return authUser("admin-a", "tenant-a");
      return oauthHydration.promise;
    };
    api.handleOAuthCallback = async () => ({
      access_token: "cookie-session",
      token_type: "bearer",
    });
    api.logout = async () => undefined;
  });
  try {
    let oauthPromise!: ReturnType<typeof mounted.auth.handleOAuthCallback>;
    await mounted.React.act(async () => {
      oauthPromise = mounted.auth.handleOAuthCallback("github", "code", "state");
      await Promise.resolve();
    });
    await mounted.React.act(async () => {
      await mounted.auth.logout();
    });
    oauthHydration.resolve(authUser("oauth-stale", "tenant-stale"));
    const outcome = await oauthPromise;
    await mounted.flush();

    assert.deepEqual(outcome, { status: "cancelled" });
    assert.equal(mounted.auth.user, null);
  } finally {
    await mounted.cleanup();
  }
});

test("AuthPage silently stops a deferred login that loses auth ownership", async () => {
  const loginHydration = deferred<User>();
  let currentUserCalls = 0;
  let loginCalls = 0;
  const mounted = await mountAuthPageHarness((api) => {
    api.getCurrentUser = async () => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) return authUser("admin-a", "tenant-a");
      return loginHydration.promise;
    };
    api.login = async () => {
      loginCalls += 1;
    };
    api.logout = async () => undefined;
  });
  try {
    const inputs = findElements(mounted.container, "input");
    const usernameInput = inputs[0];
    const passwordInput = inputs[1];
    const form = findElements(mounted.container, "form")[0];
    assert.ok(usernameInput && passwordInput && form);

    await mounted.React.act(async () => {
      usernameInput.value = "admin-b";
      reactProps(usernameInput).onChange?.({ target: usernameInput } as never);
      passwordInput.value = "safe-test";
      reactProps(passwordInput).onChange?.({ target: passwordInput } as never);
      await Promise.resolve();
    });
    await mounted.React.act(async () => {
      reactProps(form).onSubmit?.({
        preventDefault() {},
      } as never);
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(loginCalls, 1);

    await mounted.React.act(async () => {
      await mounted.auth.logout();
    });
    loginHydration.resolve(authUser("admin-b", "tenant-b"));
    await mounted.React.act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.deepEqual(mounted.successToasts, []);
    assert.deepEqual(mounted.successfulRedirects, []);
    const submit = findElements(form, "button").at(-1);
    assert.equal(submit?.hasAttribute("disabled"), false);
  } finally {
    await mounted.cleanup();
  }
});

test("OAuth callback silently stops when deferred hydration loses auth ownership", async () => {
  const oauthHydration = deferred<User>();
  let currentUserCalls = 0;
  const mounted = await mountOAuthCallbackHarness((api) => {
    api.getCurrentUser = async () => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) return authUser("admin-a", "tenant-a");
      return oauthHydration.promise;
    };
  });
  try {
    await mounted.show();
    await mounted.logout();
    oauthHydration.resolve(authUser("oauth-stale", "tenant-stale"));
    await mounted.flush();

    assert.equal(mounted.currentPath, "/auth/oauth/callback");
  } finally {
    await mounted.cleanup();
  }
});

test("OAuth callback unmount fence prevents deferred completion navigation", async () => {
  const oauthHydration = deferred<User>();
  let currentUserCalls = 0;
  const mounted = await mountOAuthCallbackHarness((api) => {
    api.getCurrentUser = async () => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) return authUser("admin-a", "tenant-a");
      return oauthHydration.promise;
    };
  });
  try {
    await mounted.show();
    await mounted.hide();
    oauthHydration.resolve(authUser("oauth-b", "tenant-b"));
    await mounted.flush();

    assert.equal(mounted.currentPath, "/auth/oauth/callback");
  } finally {
    await mounted.cleanup();
  }
});

test("current OAuth fragment hydration failure clears the previous principal and marker", async () => {
  const hydrationError = new Error(
    "C:\\private\\oauth.log?token=secret <html>proxy</html>",
  );
  let currentUserCalls = 0;
  let logoutCalls = 0;
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async () => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) return authUser("admin-a", "tenant-a");
      throw hydrationError;
    };
    api.logout = async () => {
      logoutCalls += 1;
      throw new Error("rollback logout failure");
    };
  });
  try {
    let outcome!: Awaited<ReturnType<typeof mounted.auth.completeOAuthSession>>;
    await mounted.React.act(async () => {
      outcome = await mounted.auth.completeOAuthSession(
        "oauth-access",
        "oauth-refresh",
      );
    });
    await mounted.flush();

    assert.equal(outcome.status, "failed");
    assert.equal(outcome.status === "failed" && outcome.error, hydrationError);
    assert.equal(mounted.auth.user, null);
    assert.equal(mounted.auth.token, null);
    assert.deepEqual(mounted.auth.permissions, []);
    assert.equal(storage.has("ai_platform_session_present"), false);
    assert.equal(logoutCalls, 1);
  } finally {
    await mounted.cleanup();
  }
});

test("OAuth fragment UI keeps hydration diagnostics out of navigation state", async () => {
  const diagnostic = "C:\\private\\oauth.log?token=secret <html>proxy</html>";
  let currentUserCalls = 0;
  const mounted = await mountOAuthCallbackHarness((api) => {
    api.getCurrentUser = async () => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) return authUser("admin-a", "tenant-a");
      throw new Error(diagnostic);
    };
    api.logout = async () => undefined;
  });

  try {
    await mounted.show();
    await mounted.flush();

    assert.equal(mounted.currentPath, "/auth/login");
    assert.equal(mounted.currentSearch, "?error=oauth_processing_failed");
    assert.doesNotMatch(
      `${mounted.currentPath}${mounted.currentSearch}`,
      /private|token|proxy|html|oauth\.log/i,
    );
  } finally {
    await mounted.cleanup();
  }
});

test("stale OAuth fragment failure cannot clear a newer login principal", async () => {
  const staleOAuthHydration = deferred<User>();
  let currentUserCalls = 0;
  let logoutCalls = 0;
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async () => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) return authUser("admin-a", "tenant-a");
      if (currentUserCalls === 2) return staleOAuthHydration.promise;
      return authUser("admin-b", "tenant-b");
    };
    api.login = async () => undefined;
    api.logout = async () => {
      logoutCalls += 1;
    };
  });
  try {
    let oauthPromise!: ReturnType<typeof mounted.auth.completeOAuthSession>;
    await mounted.React.act(async () => {
      oauthPromise = mounted.auth.completeOAuthSession(
        "oauth-stale-access",
        "oauth-stale-refresh",
      );
      await Promise.resolve();
    });
    await mounted.React.act(async () => {
      await mounted.auth.login({ username: "admin-b", password: "safe-test" });
    });
    staleOAuthHydration.reject(new Error("stale transport failure"));
    const outcome = await oauthPromise;
    await mounted.flush();

    assert.deepEqual(outcome, { status: "cancelled" });
    assert.equal(mounted.auth.user?.id, "admin-b");
    assert.equal(mounted.auth.user?.tenant_id, "tenant-b");
    assert.equal(storage.has("ai_platform_session_present"), true);
    assert.equal(logoutCalls, 0);
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

test("unmount resolves a pending login hydration as cancelled without an unhandled rejection", async () => {
  let currentUserCalls = 0;
  const unhandled: unknown[] = [];
  const onUnhandled = (error: unknown) => unhandled.push(error);
  process.on("unhandledRejection", onUnhandled);
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async (options?: { signal?: AbortSignal }) => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) return authUser("admin-a", "tenant-a");
      return new Promise<User>((_resolve, reject) => {
        options?.signal?.addEventListener("abort", () => {
          reject(new DOMException("aborted", "AbortError"));
        });
      });
    };
    api.login = async () => undefined;
  });
  try {
    let loginPromise!: ReturnType<typeof mounted.auth.login>;
    await mounted.React.act(async () => {
      loginPromise = mounted.auth.login({
        username: "admin-b",
        password: "safe-test",
      });
      await Promise.resolve();
    });
    await mounted.unmount();
    const outcome = await loginPromise;
    await Promise.resolve();

    assert.deepEqual(outcome, { status: "cancelled" });
    assert.deepEqual(unhandled, []);
  } finally {
    process.off("unhandledRejection", onUnhandled);
    await mounted.cleanup();
  }
});

test("AuthPage projects unknown transport diagnostics to existing localized generic copy", async () => {
  const mounted = await mountAuthPageHarness((api) => {
    api.getCurrentUser = async () => authUser("admin-a", "tenant-a");
    api.login = async () => {
      throw new Error("/private/auth.log token=secret proxy diagnostic");
    };
  });
  try {
    const inputs = findElements(mounted.container, "input");
    const usernameInput = inputs[0];
    const passwordInput = inputs[1];
    const form = findElements(mounted.container, "form")[0];
    assert.ok(usernameInput && passwordInput && form);

    await mounted.React.act(async () => {
      usernameInput.value = "admin-b";
      reactProps(usernameInput).onChange?.({ target: usernameInput } as never);
      passwordInput.value = "safe-test";
      reactProps(passwordInput).onChange?.({ target: passwordInput } as never);
      await Promise.resolve();
    });
    await mounted.React.act(async () => {
      reactProps(form).onSubmit?.({ preventDefault() {} } as never);
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.equal(mounted.errorToasts.length, 1);
    assert.doesNotMatch(
      String(mounted.errorToasts[0]),
      /private|token|secret|proxy|diagnostic/i,
    );
    assert.deepEqual(mounted.successToasts, []);
    assert.deepEqual(mounted.successfulRedirects, []);
  } finally {
    await mounted.cleanup();
  }
});
