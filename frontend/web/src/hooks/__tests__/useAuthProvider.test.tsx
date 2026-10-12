import assert from "node:assert/strict";
import test, { beforeEach } from "node:test";
import { installBrowserAuthTestDb } from "./browserAuthTestDb.ts";

import {
  CompanyADLoginError,
  type AuthContextBootstrapRequest,
} from "../../services/api/auth.ts";
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
  value: {
    userAgent: "node",
    locks: {
      async request<T>(
        _name: string,
        _options: { mode: "exclusive" },
        callback: () => Promise<T>,
      ): Promise<T> {
        return callback();
      },
    },
  },
});

// Each mounted scenario has its own durable V2 coordinator store.
beforeEach(() => installBrowserAuthTestDb());

function readyAuthContext(request: AuthContextBootstrapRequest) {
  return { status: "ready" as const, protocol_version: 2 as const, generation: request.generation };
}

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
  realTransport = false,
) {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { AuthProvider, useAuth } = await import("../useAuth.tsx");
  const { authApi } = await import("../../services/api/auth.ts");
  const originals = {
    bootstrapAuthContext: authApi.bootstrapAuthContext,
    getCurrentUser: authApi.getCurrentUser,
    login: authApi.login,
    logout: authApi.logout,
    beginOAuth: authApi.beginOAuth,
    handleOAuthCallback: authApi.handleOAuthCallback,
  };
  if (!realTransport) {
    authApi.bootstrapAuthContext = async (request) => readyAuthContext(request);
    authApi.beginOAuth = async () => ({ state: "test-oauth-state" });
  }
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
    bootstrapAuthContext: authApi.bootstrapAuthContext,
    getCurrentUser: authApi.getCurrentUser,
    login: authApi.login,
    fetchCompanyADLogin: authApi.fetchCompanyADLogin,
    loginWithAD: authApi.loginWithAD,
    logout: authApi.logout,
    beginOAuth: authApi.beginOAuth,
    getADLoginConfig: authApi.getADLoginConfig,
    getOAuthProviders: authApi.getOAuthProviders,
    updateMetadata: authApi.updateMetadata,
    toastSuccess: toast.success,
    toastError: toast.error,
  };
  authApi.bootstrapAuthContext = async (request) => readyAuthContext(request);
  authApi.beginOAuth = async () => ({ state: "test-oauth-state" });
  authApi.getADLoginConfig = async () => ({ ad_login_url: null });
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
  configure(authApi);
  storage.clear();
  storage.set("ai_platform_session_present", "test-session-marker");

  let snapshot: ReturnType<typeof useAuth> | null = null;
  let setAuthPageVisible: ((visible: boolean) => void) | null = null;
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
  function AuthPageGate() {
    const [visible, setVisible] = React.useState(true);
    setAuthPageVisible = setVisible;
    return visible
      ? React.createElement(AuthPage, {
          onSuccess: (path?: string) => successfulRedirects.push(path),
        })
      : null;
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
            React.createElement(AuthPageGate),
          ),
        ),
      ),
    );
    for (let index = 0; index < 8; index += 1) await Promise.resolve();
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
    async unmountAuthPage() {
      assert.ok(setAuthPageVisible, "auth page gate is mounted");
      await React.act(async () => setAuthPageVisible?.(false));
    },
    async cleanup() {
      await React.act(async () => root.unmount());
      Object.assign(authApi, {
        bootstrapAuthContext: originals.bootstrapAuthContext,
        getCurrentUser: originals.getCurrentUser,
        login: originals.login,
        fetchCompanyADLogin: originals.fetchCompanyADLogin,
        loginWithAD: originals.loginWithAD,
        logout: originals.logout,
        beginOAuth: originals.beginOAuth,
        getADLoginConfig: originals.getADLoginConfig,
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
  const originals = {
    bootstrapAuthContext: authApi.bootstrapAuthContext,
    getCurrentUser: authApi.getCurrentUser,
    logout: authApi.logout,
    handleOAuthCallback: authApi.handleOAuthCallback,
  };
  authApi.bootstrapAuthContext = async (request) => readyAuthContext(request);
  authApi.handleOAuthCallback = async () => undefined;
  configure(authApi);
  storage.clear();
  sessionStorageValues.clear();
  storage.set("ai_platform_session_present", "test-session-marker");
  windowTarget.location.hash = "";

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
          {
            initialEntries: [
              "/auth/oauth/callback?provider=github&code=test-code&state=test-oauth-state",
            ],
          },
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
      Object.assign(authApi, originals);
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
    api.handleOAuthCallback = async () => undefined;
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
    api.handleOAuthCallback = async () => undefined;
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

test("AuthPage gets Windows identity before the login-specific bootstrap and redirects on success", async () => {
  let currentUserCalls = 0;
  const calls: string[] = [];
  const companyJwt = "signed-company-jwt";
  const mounted = await mountAuthPageHarness((api) => {
    api.bootstrapAuthContext = async (request) => {
      calls.push("bootstrap");
      return readyAuthContext(request);
    };
    api.getCurrentUser = async () => {
      calls.push("me");
      currentUserCalls += 1;
      if (currentUserCalls === 1) {
        throw new ApiRequestError("unauthorized", 401, "unauthorized");
      }
      return authUser("ad001", "tenant-a");
    };
    api.getADLoginConfig = async () => ({
      ad_login_url: "http://company.test/api/login/GetADName",
    });
    api.fetchCompanyADLogin = async (loginUrl) => {
      calls.push("GetADName");
      assert.equal(loginUrl, "http://company.test/api/login/GetADName");
      return companyJwt;
    };
    api.loginWithAD = async (token) => {
      calls.push("ad-login");
      assert.equal(token, companyJwt);
    };
  });
  try {
    await mounted.React.act(async () => {
      for (let index = 0; index < 12; index += 1) await Promise.resolve();
    });

    assert.deepEqual(calls, [
      "bootstrap",
      "me",
      "GetADName",
      "bootstrap",
      "ad-login",
      "me",
    ]);
    assert.equal(mounted.auth.isAuthenticated, true);
    assert.equal(mounted.auth.user?.id, "ad001");
    assert.equal(findElements(mounted.container, "input").length, 0);
    assert.deepEqual(mounted.errorToasts, []);
  } finally {
    await mounted.cleanup();
  }
});

test("AuthPage reports GetADName failure before falling back to the password form", async () => {
  let adLoginCalls = 0;
  const mounted = await mountAuthPageHarness((api) => {
    api.getCurrentUser = async () => {
      throw new ApiRequestError("unauthorized", 401, "unauthorized");
    };
    api.getADLoginConfig = async () => ({
      ad_login_url: "http://company.test/api/login/GetADName",
    });
    api.fetchCompanyADLogin = async () => {
      throw new CompanyADLoginError();
    };
    api.loginWithAD = async () => {
      adLoginCalls += 1;
    };
  });
  try {
    await mounted.React.act(async () => {
      for (let index = 0; index < 12; index += 1) await Promise.resolve();
    });

    assert.equal(adLoginCalls, 0);
    assert.equal(mounted.auth.isAuthenticated, false);
    assert.equal(findElements(mounted.container, "input").length >= 2, true);
    assert.deepEqual(mounted.errorToasts, ["免登录失败"]);
  } finally {
    await mounted.cleanup();
  }
});

test("AuthPage suppresses a deferred GetADName failure after it unmounts", async () => {
  const companyLogin = deferred<string>();
  let companyLoginCalls = 0;
  const mounted = await mountAuthPageHarness((api) => {
    api.getCurrentUser = async () => {
      throw new ApiRequestError("unauthorized", 401, "unauthorized");
    };
    api.getADLoginConfig = async () => ({
      ad_login_url: "http://company.test/api/login/GetADName",
    });
    api.fetchCompanyADLogin = async () => {
      companyLoginCalls += 1;
      return companyLogin.promise;
    };
  });
  try {
    assert.equal(companyLoginCalls, 1);
    await mounted.unmountAuthPage();

    companyLogin.reject(new CompanyADLoginError());
    await mounted.React.act(async () => {
      for (let index = 0; index < 8; index += 1) await Promise.resolve();
    });

    assert.deepEqual(mounted.errorToasts, []);
  } finally {
    await mounted.cleanup();
  }
});

test("AuthPage silently falls back when AD configuration is unavailable", async () => {
  const mounted = await mountAuthPageHarness((api) => {
    api.getCurrentUser = async () => {
      throw new ApiRequestError("unauthorized", 401, "unauthorized");
    };
    api.getADLoginConfig = async () => {
      throw new DOMException("timed out", "TimeoutError");
    };
  });
  try {
    await mounted.React.act(async () => {
      for (let index = 0; index < 12; index += 1) await Promise.resolve();
    });

    assert.equal(mounted.auth.isAuthenticated, false);
    assert.equal(findElements(mounted.container, "input").length >= 2, true);
    assert.deepEqual(mounted.errorToasts, []);
  } finally {
    await mounted.cleanup();
  }
});

test("AuthPage cancels deferred AD configuration when another login wins", async () => {
  const adConfiguration = deferred<{
    ad_login_url: string | null;
  }>();
  let currentUserCalls = 0;
  let adLoginCalls = 0;
  let discoverySignal: AbortSignal | undefined;
  const mounted = await mountAuthPageHarness((api) => {
    api.getCurrentUser = async () => {
      currentUserCalls += 1;
      if (currentUserCalls === 1) {
        throw new ApiRequestError("unauthorized", 401, "unauthorized");
      }
      return authUser("password-user", "tenant-a");
    };
    api.getADLoginConfig = async (signal) => {
      discoverySignal = signal;
      return adConfiguration.promise;
    };
    api.login = async () => undefined;
    api.loginWithAD = async () => {
      adLoginCalls += 1;
    };
  });
  try {
    await mounted.React.act(async () => {
      const outcome = await mounted.auth.login({
        username: "password-user",
        password: "safe-test",
      });
      assert.equal(outcome.status, "completed");
    });
    assert.equal(discoverySignal?.aborted, true);

    adConfiguration.resolve({
      ad_login_url: "http://company.test/api/login/GetADName",
    });
    await mounted.React.act(async () => {
      for (let index = 0; index < 8; index += 1) await Promise.resolve();
    });

    assert.equal(mounted.auth.user?.id, "password-user");
    assert.equal(adLoginCalls, 0);
    assert.deepEqual(mounted.successfulRedirects, []);
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

test("current OAuth callback hydration failure clears the previous principal and marker", async () => {
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
    api.handleOAuthCallback = async () => undefined;
  });
  try {
    await mounted.React.act(async () => {
      await assert.rejects(
        () =>
          mounted.auth.handleOAuthCallback(
            "github",
            "test-code",
            "test-state",
          ),
        (error: unknown) => error === hydrationError,
      );
    });
    await mounted.flush();

    assert.equal(mounted.auth.user, null);
    assert.equal(mounted.auth.token, null);
    assert.deepEqual(mounted.auth.permissions, []);
    assert.equal(storage.has("ai_platform_session_present"), false);
    assert.equal(logoutCalls, 1);
  } finally {
    await mounted.cleanup();
  }
});

test("OAuth callback UI keeps hydration diagnostics out of navigation state", async () => {
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

test("stale OAuth callback failure cannot clear a newer login principal", async () => {
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
    api.handleOAuthCallback = async () => undefined;
  });
  try {
    let oauthPromise!: ReturnType<typeof mounted.auth.handleOAuthCallback>;
    await mounted.React.act(async () => {
      oauthPromise = mounted.auth.handleOAuthCallback(
        "github",
        "test-code",
        "test-state",
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

test("AuthPage renders actionable localized copy when secure browser auth coordination is unavailable", async () => {
  const { BrowserAuthCoordinatorError } = await import("../browserAuthCoordinator.ts");
  let bootstrapCalls = 0;
  const mounted = await mountAuthPageHarness((api) => {
    api.getCurrentUser = async () => authUser("admin-a", "tenant-a");
    api.bootstrapAuthContext = async (request) => {
      bootstrapCalls += 1;
      if (bootstrapCalls > 1) {
        throw new BrowserAuthCoordinatorError(
          "auth_context_coordination_unavailable",
        );
      }
      return readyAuthContext(request);
    };
    api.login = async () => undefined;
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
    assert.match(String(mounted.errorToasts[0]), /浏览器|站点数据|联系管理员/);
    assert.match(String(mounted.errorToasts[0]), /站点地址.*HTTPS/);
    assert.deepEqual(mounted.successToasts, []);
    assert.deepEqual(mounted.successfulRedirects, []);
  } finally {
    await mounted.cleanup();
  }
});

test("reversed stale cookie arrival repairs and retries exactly one idempotent principal GET", async () => {
  let cookieJar = "current-generation";
  let bootstrapCalls = 0;
  let principalCalls = 0;
  const mounted = await mountAuthHarness((api) => {
    api.bootstrapAuthContext = async (request) => {
      bootstrapCalls += 1;
      // A confirmed V2 store skips ordinary ensure. Only the forced
      // typed-stale repair contacts bootstrap after initial admission.
      if (bootstrapCalls === 2) cookieJar = "current-generation";
      return readyAuthContext(request);
    };
    api.getCurrentUser = async () => {
      principalCalls += 1;
      if (principalCalls === 1) return authUser("initial-user", "tenant-a");
      if (cookieJar === "old-generation") {
        throw new ApiRequestError("stale auth context", 409, "auth_context_stale");
      }
      return authUser("repaired-user", "tenant-b");
    };
  });
  try {
    await mounted.flush();
    // A late old Set-Cookie physically wins in the jar after the initial
    // principal was projected; it has no server authority in the V2 protocol.
    cookieJar = "old-generation";
    let outcome: Awaited<ReturnType<typeof mounted.auth.refreshUser>>;
    await mounted.React.act(async () => {
      outcome = await mounted.auth.refreshUser();
      await Promise.resolve();
    });

    assert.equal(outcome!.status, "completed");
    assert.equal(mounted.auth.user?.id, "repaired-user");
    assert.equal(principalCalls, 3);
    assert.equal(bootstrapCalls, 2);
    assert.equal(cookieJar, "current-generation");
  } finally {
    await mounted.cleanup();
  }
});

test("typed stale mutation failure never replays login POST", async () => {
  let loginCalls = 0;
  const mounted = await mountAuthHarness((api) => {
    api.getCurrentUser = async () => authUser("initial-user", "tenant-a");
    api.login = async () => {
      loginCalls += 1;
      throw new ApiRequestError("stale auth context", 409, "auth_context_stale");
    };
  });
  try {
    await mounted.flush();
    await mounted.React.act(async () => {
      await assert.rejects(
        () => mounted.auth.login({ username: "test-user", password: "test-password" }),
        (error: unknown) => error instanceof ApiRequestError && error.code === "auth_context_stale",
      );
      await Promise.resolve();
    });
    assert.equal(loginCalls, 1);
  } finally {
    await mounted.cleanup();
  }
});

test("login issues one context bootstrap before one business login mutation", async () => {
  const calls: string[] = [];
  let currentUserCalls = 0;
  const mounted = await mountAuthHarness((api) => {
    api.bootstrapAuthContext = async (request) => {
      calls.push("bootstrap");
      return readyAuthContext(request);
    };
    api.login = async () => {
      calls.push("login");
    };
    api.getCurrentUser = async () => {
      currentUserCalls += 1;
      return currentUserCalls === 1
        ? authUser("initial-user", "tenant-a")
        : authUser("login-user", "tenant-b");
    };
  });
  try {
    await mounted.flush();
    calls.length = 0;
    await mounted.React.act(async () => {
      await mounted.auth.login({ username: "test-user", password: "test-password" });
    });

    assert.deepEqual(calls, ["bootstrap", "login"]);
    assert.equal(calls.filter((call) => call === "login").length, 1);
    assert.equal(mounted.auth.user?.id, "login-user");
  } finally {
    await mounted.cleanup();
  }
});

test("a fail-closed browser context recovery never reaches the login mutation", async () => {
  const { BrowserAuthCoordinatorError } = await import("../browserAuthCoordinator.ts");
  let loginCalls = 0;
  const mounted = await mountAuthHarness((api) => {
    api.bootstrapAuthContext = async () => {
      throw new BrowserAuthCoordinatorError("auth_context_coordination_unavailable");
    };
    api.login = async () => {
      loginCalls += 1;
    };
  });
  try {
    await mounted.React.act(async () => {
      await assert.rejects(
        () => mounted.auth.login({ username: "test-user", password: "test-password" }),
        (error: unknown) => error instanceof BrowserAuthCoordinatorError,
      );
    });
    assert.equal(loginCalls, 0);
  } finally {
    await mounted.cleanup();
  }
});

// Real API wrappers and response parsing; only the HTTP boundary is replaced.
async function mountAuthTransportHarness(initialStatus = 200) {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const events: string[] = [];
  const recordRecovery = () => events.push("auth:force-relogin");
  const json = (body: unknown, status = 200, headers = {}) => new Response(
    JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } },
  );
  const principal = (id = "transport-a") => json({
    user_id: id, user_name: id, tenant_id: "tenant-transport", department_id: "department-transport",
    display_name: id, roles: ["admin"], permissions: ["settings:manage"], is_admin: true, source: "test",
  });
  const transport = {
    me: (_init: RequestInit): Response | Promise<Response> => initialStatus === 200 ? principal() : json({ detail: "unauthorized" }, initialStatus),
    logout: (): Response | Promise<Response> => new Response(null, { status: 204 }),
  };
  globalThis.fetch = (async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    calls.push({ url, init });
    assert.equal("ordinary401Recovery" in init, false, "internal policy must not reach fetch");
    if (url === "/api/ai/auth/bootstrap") {
      return json(readyAuthContext(JSON.parse(String(init.body)) as AuthContextBootstrapRequest));
    }
    if (url === "/api/ai/auth/me") return transport.me(init);
    if (url === "/api/ai/auth/logout") return transport.logout();
    if (url === "/company/GetADName") return json([{ token: "synthetic-company-jwt" }]);
    if (["/api/ai/auth/login", "/api/ai/auth/ad-login", "/api/ai/auth/oauth/test/callback"].includes(url)) return json({});
    throw new Error(`Unexpected auth test request: ${url}`);
  }) as typeof fetch;
  windowTarget.addEventListener("auth:force-relogin", recordRecovery);
  try {
    const mounted = await mountAuthHarness(() => {}, true);
    await mounted.flush();
    return {
      ...mounted, get auth() { return mounted.auth; }, calls, events, transport, json, principal,
      async cleanup() {
        try { await mounted.cleanup(); } finally {
          globalThis.fetch = originalFetch;
          windowTarget.removeEventListener("auth:force-relogin", recordRecovery);
          sessionStorageValues.clear();
        }
      },
    };
  } catch (error) {
    globalThis.fetch = originalFetch;
    windowTarget.removeEventListener("auth:force-relogin", recordRecovery);
    throw error;
  }
}

test("real transport initialization ordinary 401 converges without server logout", async () => {
  const mounted = await mountAuthTransportHarness(401);
  try {
    assert.deepEqual(mounted.calls.map(({ url }) => url), ["/api/ai/auth/bootstrap", "/api/ai/auth/me"]);
    assert.deepEqual(mounted.events, []);
    assert.equal(mounted.auth.isAuthenticated, false);
    assert.equal(mounted.auth.isLoading, false);
    assert.equal(storage.has("ai_platform_session_present"), false);
  } finally { await mounted.cleanup(); }
});

test("real transport ordinary hydration 401 stays with the refresh owner", async () => {
  const mounted = await mountAuthTransportHarness();
  mounted.calls.length = 0;
  mounted.transport.me = () => mounted.json({ detail: "unauthorized" }, 401);
  try {
    await mounted.React.act(async () => {
      assert.deepEqual(await mounted.auth.refreshUser(), { status: "failed" });
    });
    assert.deepEqual(mounted.calls.map(({ url, init }) => [init.method ?? "GET", url]), [["GET", "/api/ai/auth/me"]]);
    assert.deepEqual(mounted.events, []);
    assert.equal(mounted.auth.isAuthenticated, false);
    assert.equal(mounted.auth.isLoading, false);
    assert.equal(storage.has("ai_platform_session_present"), false);
  } finally { await mounted.cleanup(); }
});

for (const operation of ["login", "AD", "OAuth"] as const) {
  for (const rollbackFails of [false, true]) {
    test(`real transport ${operation} hydration 401 preserves its error with rollback failure=${rollbackFails}`, async () => {
      const mounted = await mountAuthTransportHarness();
      mounted.calls.length = 0;
      mounted.transport.me = () => mounted.json({ detail: "unauthorized" }, 401);
      if (rollbackFails) mounted.transport.logout = () => mounted.json({ detail: "unavailable" }, 503);
      try {
        await mounted.React.act(async () => {
          const operationPromise = operation === "login"
            ? mounted.auth.login({ username: "fixture", password: "synthetic-test-only" })
            : operation === "AD" ? mounted.auth.loginWithAD("/company/GetADName")
              : mounted.auth.handleOAuthCallback("test", "synthetic-code", "synthetic-state");
          await assert.rejects(operationPromise, (error: unknown) => error instanceof ApiRequestError && error.status === 401);
        });
        const mutation = operation === "login" ? "/api/ai/auth/login" : operation === "AD" ? "/api/ai/auth/ad-login" : "/api/ai/auth/oauth/test/callback";
        assert.deepEqual(mounted.calls.filter(({ url }) => url !== "/api/ai/auth/bootstrap" && url !== "/company/GetADName").map(({ url, init }) => [init.method ?? "GET", url]), [
          ["POST", mutation], ["GET", "/api/ai/auth/me"], ["POST", "/api/ai/auth/logout"],
        ]);
        assert.deepEqual(mounted.events, []);
        assert.equal(mounted.auth.isAuthenticated, false);
        assert.equal(mounted.auth.isLoading, false);
        assert.equal(storage.has("ai_platform_session_present"), false);
      } finally { await mounted.cleanup(); }
    });
  }
}

test("real transport ordinary hydration 403 remains caller-owned", async () => {
  const mounted = await mountAuthTransportHarness();
  mounted.calls.length = 0;
  mounted.transport.me = () => mounted.json({ detail: "forbidden" }, 403);
  try {
    await mounted.React.act(async () => {
      assert.deepEqual(await mounted.auth.refreshUser(), { status: "failed" });
    });
    assert.deepEqual(mounted.events, []);
    assert.deepEqual(mounted.calls.map(({ url }) => url), ["/api/ai/auth/me"]);
    assert.equal(mounted.auth.isAuthenticated, false);
    assert.equal(mounted.auth.isLoading, false);
  } finally { await mounted.cleanup(); }
});

test("real transport explicit force header retains recovery and cancels the old hydration owner", async () => {
  const mounted = await mountAuthTransportHarness();
  mounted.calls.length = 0;
  mounted.transport.me = () => mounted.json({ detail: "unauthorized" }, 401, { "X-Force-Relogin": "true" });
  try {
    await mounted.React.act(async () => {
      assert.deepEqual(await mounted.auth.refreshUser(), { status: "cancelled" });
    });
    await mounted.flush();
    assert.deepEqual(mounted.events, ["auth:force-relogin"]);
    assert.deepEqual(mounted.calls.map(({ url, init }) => [init.method ?? "GET", url]), [
      ["GET", "/api/ai/auth/me"], ["POST", "/api/ai/auth/logout"],
    ]);
    assert.equal(mounted.auth.isAuthenticated, false);
    assert.equal(mounted.auth.isLoading, false);
    assert.equal(storage.has("ai_platform_session_present"), false);
  } finally { await mounted.cleanup(); }
});

for (const delay of ["response", "body"] as const) {
  test(`real transport delayed forced ${delay} cannot log out a replacement principal`, async () => {
    const mounted = await mountAuthTransportHarness();
    mounted.calls.length = 0;
    const started = deferred<void>();
    const lateResponse = deferred<Response>();
    let bodyController: ReadableStreamDefaultController<Uint8Array> | undefined;
    let first = true;
    mounted.transport.me = () => {
      if (!first) return mounted.principal("transport-b");
      first = false;
      if (delay === "response") {
        started.resolve();
        return lateResponse.promise;
      }
      const response = new Response(new ReadableStream<Uint8Array>({
        start(controller) { bodyController = controller; },
      }), { status: 401, headers: { "X-Force-Relogin": "true" } });
      const readText = response.text.bind(response);
      response.text = () => {
        const reading = readText();
        // Stream pull can prefetch; only native text consumption proves the
        // response-arrival fence ran before the replacement owner signs in.
        started.resolve();
        return reading;
      };
      return response;
    };
    let oldRefresh: ReturnType<typeof mounted.auth.refreshUser> | undefined;
    try {
      await mounted.React.act(async () => {
        oldRefresh = mounted.auth.refreshUser();
        await started.promise;
      });
      await mounted.React.act(async () => {
        assert.equal((await mounted.auth.login({ username: "replacement", password: "synthetic-test-only" })).status, "completed");
      });
      const replacementMarker = storage.get("ai_platform_session_present");
      await mounted.React.act(async () => {
        if (delay === "response") lateResponse.resolve(mounted.json({ detail: "unauthorized" }, 401, { "X-Force-Relogin": "true" }));
        else {
          assert.ok(bodyController);
          bodyController.enqueue(new TextEncoder().encode(JSON.stringify({ detail: "unauthorized" })));
          bodyController.close();
        }
        assert.deepEqual(await oldRefresh, { status: "cancelled" });
      });
      assert.equal(mounted.auth.user?.id, "transport-b");
      assert.equal(storage.get("ai_platform_session_present"), replacementMarker);
      assert.deepEqual(mounted.events, []);
      assert.equal(mounted.calls.filter(({ url }) => url === "/api/ai/auth/logout").length, 0);
      assert.equal(mounted.calls.filter(({ url }) => url === "/api/ai/auth/login").length, 1);
      assert.equal(mounted.calls.filter(({ url }) => url === "/api/ai/auth/me").length, 2);
    } finally { await mounted.cleanup(); }
  });
}

test("real transport unmount aborts hydration without recovery or replay", async () => {
  const mounted = await mountAuthTransportHarness();
  mounted.calls.length = 0;
  const started = deferred<AbortSignal>();
  let abortReason: unknown;
  mounted.transport.me = (init) => new Promise<Response>((_resolve, reject) => {
    assert.ok(init.signal);
    const signal = init.signal;
    signal.addEventListener("abort", () => { abortReason = signal.reason; reject(abortReason); }, { once: true });
    started.resolve(signal);
  });
  let refresh: ReturnType<typeof mounted.auth.refreshUser> | undefined;
  try {
    await mounted.React.act(async () => { refresh = mounted.auth.refreshUser(); await started.promise; });
    const signal = await started.promise;
    await mounted.unmount();
    assert.equal(signal.aborted, true);
    assert.equal(abortReason, signal.reason);
    assert.deepEqual(await refresh, { status: "cancelled" });
    assert.deepEqual(mounted.events, []);
    assert.deepEqual(mounted.calls.map(({ url }) => url), ["/api/ai/auth/me"]);
  } finally { await mounted.cleanup(); }
});
