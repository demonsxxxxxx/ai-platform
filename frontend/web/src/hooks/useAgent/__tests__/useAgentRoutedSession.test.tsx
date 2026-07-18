import assert from "node:assert/strict";
import test from "node:test";

import type { UseAgentReturn } from "../types.ts";
import { ApiRequestError } from "../../../services/api/fetch.ts";
import type {
  ChatStreamResponse,
  ChatSubmissionResolution,
} from "../../../services/api/session.ts";

type Listener = (event: { type: string; [key: string]: unknown }) => void;

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

  dispatchEvent(event: { type: string; [key: string]: unknown }) {
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
    if (index < 0) {
      this.childNodes.push(child);
    } else {
      this.childNodes.splice(index, 0, child);
    }
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
    if (value) {
      this.appendChild(this.ownerDocument.createTextNode(value));
    }
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

function installTestDom() {
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
      readonly detail: unknown;

      constructor(
        readonly type: string,
        init?: { detail?: unknown },
      ) {
        this.detail = init?.detail;
      }
    },
    IS_REACT_ACT_ENVIRONMENT: true,
  });
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { userAgent: "node", locks: new TestLockManager() },
  });

  return { document, window: windowTarget };
}

const dom = installTestDom();

function clearPersistedSubmissionReferences() {
  for (let index = dom.window.localStorage.length - 1; index >= 0; index -= 1) {
    const key = dom.window.localStorage.key(index);
    if (key?.startsWith("ai_platform_chat_submission")) {
      dom.window.localStorage.removeItem(key);
    }
  }
}

function persistedSubmissionStorageValues(): string[] {
  const values: string[] = [];
  for (let index = 0; index < dom.window.localStorage.length; index += 1) {
    const key = dom.window.localStorage.key(index);
    if (key?.startsWith("ai_platform_chat_submission")) {
      values.push(dom.window.localStorage.getItem(key) || "");
    }
  }
  return values;
}

async function loadReactHarness({
  strict = false,
  onAuthScopeLayout,
  preserveSubmissionReferences = false,
}: {
  strict?: boolean;
  onAuthScopeLayout?: () => void;
  preserveSubmissionReferences?: boolean;
} = {}) {
  if (!preserveSubmissionReferences) {
    clearPersistedSubmissionReferences();
  }
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { AuthProvider, useAuth } = await import("../../useAuth.tsx");
  const { useAgent } = await import("../../useAgent.ts");
  const { authApi } = await import("../../../services/api/auth.ts");

  let snapshot: UseAgentReturn | null = null;
  let authSnapshot: ReturnType<typeof useAuth> | null = null;
  const container = dom.document.createElement("div");
  const root = createRoot(container as never);
  const originalGetCurrentUser = authApi.getCurrentUser;
  const originalBootstrapAuthContext = authApi.bootstrapAuthContext;
  let currentAuthUser = {
    id: "user-a",
    tenant_id: "tenant-a",
    username: "user-a",
    email: "user-a@example.test",
    roles: [],
    permissions: [],
    is_admin: false,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
  authApi.getCurrentUser = async () => currentAuthUser;
  authApi.bootstrapAuthContext = async () => {};
  const restoreAuthApi = () => {
    authApi.getCurrentUser = originalGetCurrentUser;
    authApi.bootstrapAuthContext = originalBootstrapAuthContext;
  };

  function Probe() {
    authSnapshot = useAuth();
    snapshot = useAgent();
    return null;
  }

  function AuthScopeLayoutProbe() {
    const { isAuthenticated, user } = useAuth();
    const scope =
      isAuthenticated && user
        ? JSON.stringify([user.tenant_id ?? "", user.id])
        : null;
    const previousScopeRef = React.useRef<string | null | undefined>(undefined);

    React.useLayoutEffect(() => {
      if (
        previousScopeRef.current !== undefined &&
        previousScopeRef.current !== scope
      ) {
        onAuthScopeLayout?.();
      }
      previousScopeRef.current = scope;
    }, [scope]);

    return null;
  }

  const probe = React.createElement(Probe);
  const children = React.createElement(
    React.Fragment,
    null,
    probe,
    onAuthScopeLayout ? React.createElement(AuthScopeLayoutProbe) : null,
  );
  try {
    await React.act(async () => {
      root.render(
        React.createElement(
          AuthProvider,
          null,
          strict ? React.createElement(React.StrictMode, null, children) : children,
        ),
      );
    });
  } catch (error) {
    try {
      await React.act(async () => root.unmount());
    } catch {
      // Preserve the mount failure while restoring shared auth seams.
    }
    restoreAuthApi();
    throw error;
  }

  let unmounted = false;
  let cleanedUp = false;
  const unmount = async () => {
    if (unmounted) return;
    unmounted = true;
    await React.act(async () => root.unmount());
  };

  const rotateAuthScope = async (
    userId: string,
    tenantId: string,
    settleAfterCommit: boolean,
  ) => {
    const oldMarker = dom.window.localStorage.getItem(
      "ai_platform_session_present",
    );
    const nextMarker = `marker-${userId}`;
    currentAuthUser = {
      ...currentAuthUser,
      id: userId,
      tenant_id: tenantId,
      username: userId,
      email: `${userId}@example.test`,
    };
    dom.window.localStorage.setItem("ai_platform_session_present", nextMarker);
    await React.act(async () => {
      dom.window.dispatchEvent({
        type: "storage",
        key: "ai_platform_session_present",
        oldValue: oldMarker,
        newValue: nextMarker,
      });
      await Promise.resolve();
    });
    if (settleAfterCommit) {
      await settle(React.act);
    }
  };

  const loginAs = async (
    userId: string,
    tenantId: string,
    settleAfterCommit: boolean,
  ) => {
    currentAuthUser = {
      ...currentAuthUser,
      id: userId,
      tenant_id: tenantId,
      username: userId,
      email: `${userId}@example.test`,
    };
    const auth = authSnapshot;
    if (!auth) throw new Error("Auth context should be mounted");
    await React.act(async () => {
      await auth.login({ username: userId, password: "test-password" });
      await Promise.resolve();
    });
    if (settleAfterCommit) {
      await settle(React.act);
    }
  };

  return {
    act: React.act,
    get hook() {
      assert.ok(snapshot, "useAgent hook should be mounted");
      return snapshot;
    },
    get auth() {
      assert.ok(authSnapshot, "Auth context should be mounted");
      return authSnapshot;
    },
    async rotateAuthScope(userId: string, tenantId: string) {
      await rotateAuthScope(userId, tenantId, true);
    },
    async rotateAuthScopeBeforePassiveEffects(userId: string, tenantId: string) {
      await rotateAuthScope(userId, tenantId, false);
    },
    async loginAsBeforePassiveEffects(userId: string, tenantId: string) {
      await loginAs(userId, tenantId, false);
    },
    async dispatchProductionAuthIncarnation(incarnation: string) {
      const { BROWSER_AUTH_INCARCINATION_EVENT } = await import(
        "../../browserAuthCoordinator.ts"
      );
      await React.act(async () => {
        dom.window.dispatchEvent(
          new CustomEvent(BROWSER_AUTH_INCARCINATION_EVENT, {
            detail: { incarnation },
          }) as unknown as { type: string; [key: string]: unknown },
        );
        await Promise.resolve();
      });
    },
    unmount,
    async cleanup() {
      if (cleanedUp) return;
      cleanedUp = true;
      try {
        await unmount();
      } finally {
        restoreAuthApi();
      }
    },
  };
}

async function settle(act: typeof import("react").act) {
  for (let index = 0; index < 8; index += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

function completedSseResponse() {
  return new Response('event: complete\ndata: {"status":"succeeded"}\n\n', {
    headers: { "content-type": "text/event-stream" },
  });
}

function sseEventResponse(event: string, data: Record<string, unknown>) {
  return new Response(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`, {
    headers: { "content-type": "text/event-stream" },
  });
}

function sseFramesResponse(
  frames: Array<{ event: string; data: Record<string, unknown> }>,
) {
  return new Response(
    frames
      .map(({ event, data }) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
      .join(""),
    { headers: { "content-type": "text/event-stream" } },
  );
}

function nonClosingSseEventResponse(event: string, data: Record<string, unknown>) {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(
          encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`),
        );
      },
    }),
    { headers: { "content-type": "text/event-stream" } },
  );
}

test("useAgent carries the routed agent into a same-tab continuation", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalMarkRead = sessionApi.markRead;
  const originalGenerateTitle = sessionApi.generateTitle;
  const submissions: unknown[][] = [];
  const originalFetch = dom.window.fetch;
  let sseCalls = 0;
  dom.window.fetch = async () => {
    sseCalls += 1;
    return completedSseResponse();
  };
  sessionApi.markRead = async () => {};
  sessionApi.generateTitle = async () => ({
    title: "翻译会话",
    session_id: "session-routed",
  });
  sessionApi.submitChat = (async (...args) => {
    submissions.push(args);
    return submissions.length === 1
      ? {
          session_id: "session-routed",
          run_id: "run-first",
          trace_id: "trace-first",
          status: "queued",
          intent_decision: { agent_id: "document-translation" },
        }
      : {
          session_id: "session-routed",
          run_id: "run-second",
          trace_id: "trace-second",
          status: "queued",
        };
  }) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      await harness.hook.sendMessage("翻译这个文档");
    });
    await settle(harness.act);
    await harness.act(async () => {
      await harness.hook.sendMessage("继续处理");
    });
    await settle(harness.act);

    assert.equal(submissions.length, 2);
    assert.equal(submissions[0]?.at(-1), "general-agent");
    assert.equal(submissions[1]?.at(-1), "document-translation");
    assert.equal(harness.hook.sessionId, "session-routed");
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(sseCalls, 2);
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.markRead = originalMarkRead;
    sessionApi.generateTitle = originalGenerateTitle;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent restores a routed session agent before the next submission", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalMarkRead = sessionApi.markRead;
  const originalSubmitChat = sessionApi.submitChat;
  const submissions: unknown[][] = [];
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-restored",
    agent_id: "document-translation",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async () => ({ events: [] });
  sessionApi.submitChat = (async (...args) => {
    submissions.push(args);
    return {
      session_id: "session-restored",
      run_id: null,
      status: "needs_confirmation",
      suggestions: [],
    };
  }) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-restored");
    });
    await harness.act(async () => {
      await harness.hook.sendMessage("继续处理");
    });

    assert.equal(submissions.length, 1);
    assert.equal(submissions[0]?.at(-1), "document-translation");
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.markRead = originalMarkRead;
    sessionApi.submitChat = originalSubmitChat;
    await harness.cleanup();
  }
});

test("useAgent clears colliding rotated auth scopes before fresh and owned-session submissions", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalMarkRead = sessionApi.markRead;
  const originalSubmitChat = sessionApi.submitChat;
  const originalGenerateTitle = sessionApi.generateTitle;
  const originalGetStatus = sessionApi.getStatus;
  const originalFetch = dom.window.fetch;
  const submissions: unknown[][] = [];
  let resolveStaleSubmit!: (value: ChatStreamResponse) => void;
  const staleSubmit = new Promise<ChatStreamResponse>((resolve) => {
    resolveStaleSubmit = resolve;
  });

  sessionApi.markRead = async () => {};
  sessionApi.get = async (sessionId) => ({
    id: sessionId,
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async () => ({ events: [] });
  sessionApi.getStatus = async () => ({
    session_id: "session-owned-a",
    status: "idle",
    raw_status: "idle",
  });
  sessionApi.generateTitle = async (sessionId) => ({
    title: "新会话",
    session_id: sessionId,
  });
  dom.window.fetch = async () => {
    return completedSseResponse();
  };
  sessionApi.submitChat = (async (...args) => {
    submissions.push(args);
    if (submissions.length === 1) return staleSubmit;
    if (submissions.length === 2) {
      return {
        session_id: "session-fresh-b",
        run_id: "run-fresh-b",
        status: "queued",
      };
    }
    if (submissions.length === 3) {
      return {
        session_id: "session-owned-b",
        run_id: "run-owned-b",
        status: "queued",
      };
    }
    throw new ApiRequestError(
      "session admission rejected",
      400,
      "skill_selector_conflict",
    );
  }) as typeof sessionApi.submitChat;

  try {
    await harness.rotateAuthScope("c", "a:b");
    await harness.act(async () => {
      await harness.hook.loadHistory("session-owned-a");
    });
    let pending: Promise<unknown> | null = null;
    await harness.act(async () => {
      pending = harness.hook.sendMessage("旧身份请求");
      await Promise.resolve();
    });

    await harness.rotateAuthScope("b:c", "a");
    resolveStaleSubmit({
      session_id: "session-owned-a",
      run_id: "run-old-a",
      trace_id: "trace-old-a",
      status: "queued",
    });
    await harness.act(async () => {
      await pending;
    });
    await settle(harness.act);

    assert.equal(harness.hook.sessionId, null);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.messages.length, 0);
    // Resolver/status transport can perform a safe GET, but the stale submit
    // itself must not publish a run/session/message under the replacement owner.

    await harness.act(async () => {
      await harness.hook.sendMessage("新身份新对话");
    });
    await settle(harness.act);
    assert.equal(submissions[1]?.[1], undefined);
    assert.equal(harness.hook.sessionId, "session-fresh-b");

    await harness.act(async () => {
      harness.hook.clearMessages();
      await harness.hook.loadHistory("session-owned-b");
      await harness.hook.sendMessage("已拥有会话的后续请求");
    });
    await settle(harness.act);
    assert.equal(submissions[2]?.[1], "session-owned-b");
    assert.equal(harness.hook.sessionId, "session-owned-b");

    await harness.act(async () => {
      harness.hook.clearMessages();
    });
    await harness.act(async () => {
      const outcome = await harness.hook.sendMessage("可重试失败");
      assert.deepEqual(outcome, { status: "failed" });
    });
    // A legacy 4xx without the server-controlled disposition is still an
    // unknown mutation result, so it remains locked instead of claiming the
    // optimistic user message was never persisted.
    assert.equal(harness.hook.messages.length, 1);
    assert.equal(harness.hook.canRetryPendingSubmission, true);
    assert.equal(harness.hook.currentRunId, null);
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.markRead = originalMarkRead;
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.generateTitle = originalGenerateTitle;
    sessionApi.getStatus = originalGetStatus;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent invalidates old owners in the auth layout boundary before they can publish", async () => {
  let releaseAtAuthScopeLayout: (() => void) | null = null;
  const harness = await loadReactHarness({
    onAuthScopeLayout: () => releaseAtAuthScopeLayout?.(),
  });
  const { sessionApi } = await import("../../../services/api/session.ts");
  const { authApi } = await import("../../../services/api/auth.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalMarkRead = sessionApi.markRead;
  const originalSubmitChat = sessionApi.submitChat;
  const originalGenerateTitle = sessionApi.generateTitle;
  const originalGetStatus = sessionApi.getStatus;
  const originalCancelRun = sessionApi.cancelRun;
  const originalLogin = authApi.login;
  const originalFetch = dom.window.fetch;
  let resolveOldSubmit!: (value: ChatStreamResponse) => void;
  const oldSubmit = new Promise<ChatStreamResponse>((resolve) => {
    resolveOldSubmit = resolve;
  });
  let sseCalls = 0;
  let statusCalls = 0;
  let cancelCalls = 0;
  let pendingSubmission: Promise<unknown> | null = null;

  dom.window.fetch = async () => {
    sseCalls += 1;
    return nonClosingSseEventResponse("message:chunk", {
      run_id: "run-old-history",
      content: "等待旧身份完成",
    });
  };
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-old-history",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async () => ({
    current_run_id: "run-old-history",
    events: [
      {
        id: "evt-old-history",
        run_id: "run-old-history",
        event_type: "user:message",
        timestamp: "2026-07-16T00:00:00Z",
        data: { content: "旧身份会话" },
      },
    ],
  });
  sessionApi.submitChat = (() => oldSubmit) as typeof sessionApi.submitChat;
  sessionApi.generateTitle = async (sessionId) => ({
    title: "旧身份会话",
    session_id: sessionId,
  });
  sessionApi.getStatus = async (sessionId, runId) => {
    statusCalls += 1;
    return { session_id: sessionId, run_id: runId, status: "running" };
  };
  sessionApi.cancelRun = async (runId) => {
    cancelCalls += 1;
    return { run_id: runId, status: "cancelled" };
  };
  authApi.login = async () => {};

  releaseAtAuthScopeLayout = () => {
    resolveOldSubmit({
      session_id: "session-old-owner",
      run_id: "run-old-owner",
      trace_id: "trace-old-owner",
      status: "queued",
    });
    void harness.hook.reconnectSSE();
    void harness.hook.stopGeneration();
  };

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-old-history");
    });
    assert.equal(harness.hook.currentRunId, "run-old-history");
    assert.equal(sseCalls, 1);
    assert.equal(statusCalls, 1);

    await harness.act(async () => {
      pendingSubmission = harness.hook.sendMessage("旧身份的迟到请求");
      await Promise.resolve();
    });
    await harness.loginAsBeforePassiveEffects("user-b", "tenant-b");
    await harness.act(async () => {
      await pendingSubmission;
      await Promise.resolve();
    });
    await settle(harness.act);

    assert.equal(harness.hook.sessionId, null);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.messages.length, 0);
    assert.equal(sseCalls, 1);
    assert.equal(statusCalls, 1);
    assert.equal(cancelCalls, 0);
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.markRead = originalMarkRead;
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.generateTitle = originalGenerateTitle;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.cancelRun = originalCancelRun;
    authApi.login = originalLogin;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent permits a retry only after a typed pre-persistence rejection", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  let submissions = 0;
  sessionApi.submitChat = (async () => {
    submissions += 1;
    if (submissions === 1) {
      throw new ApiRequestError(
        "invalid selector",
        400,
        "skill_selector_conflict",
        "rejected_before_persist",
      );
    }
    return {
      status: "needs_confirmation",
      suggestions: [],
    };
  }) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      assert.deepEqual(await harness.hook.sendMessage("被明确拒绝的请求"), {
        status: "failed",
      });
    });
    assert.equal(harness.hook.messages.length, 0);

    await harness.act(async () => {
      assert.deepEqual(await harness.hook.sendMessage("重新提交"), {
        status: "accepted",
      });
    });
    assert.equal(submissions, 2);
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    await harness.cleanup();
  }
});

test("useAgent retains an unknown submission and blocks an automatic duplicate", async () => {
  const unknownFailures: Array<{ name: string; error: Error }> = [
    { name: "network loss", error: new Error("response lost after acceptance") },
    {
      name: "server failure",
      error: new ApiRequestError("gateway failure", 503, "queue_unavailable"),
    },
  ];

  for (const { name, error } of unknownFailures) {
    const harness = await loadReactHarness();
    const { sessionApi } = await import("../../../services/api/session.ts");
    const originalSubmitChat = sessionApi.submitChat;
    let submissions = 0;
    let serverAccepted = false;
    sessionApi.submitChat = (async () => {
      submissions += 1;
      serverAccepted = true;
      throw error;
    }) as typeof sessionApi.submitChat;

    try {
      await harness.act(async () => {
        assert.deepEqual(await harness.hook.sendMessage(`未知结果：${name}`), {
          status: "failed",
        });
      });
      assert.equal(serverAccepted, true);
      assert.equal(harness.hook.messages.length, 1);
      assert.equal(harness.hook.messages[0]?.role, "user");
      assert.equal(
        harness.hook.error,
        "任务状态暂时无法同步。请刷新当前会话后重试。",
      );

      await harness.act(async () => {
        assert.deepEqual(await harness.hook.sendMessage("不得自动重放"), {
          status: "failed",
        });
      });
      assert.equal(submissions, 1);
      assert.equal(harness.hook.messages.length, 1);
    } finally {
      sessionApi.submitChat = originalSubmitChat;
      await harness.cleanup();
    }
  }
});

test("useAgent preserves a stale fence through New Chat and same-principal login until explicit tombstone recovery", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const { authApi } = await import("../../../services/api/auth.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalGetChatSubmission = sessionApi.getChatSubmission;
  const originalRetryAdmission = sessionApi.retryChatSubmissionAdmission;
  const originalLogout = authApi.logout;
  const originalLogin = authApi.login;
  let submissions = 0;
  let resolverCalls = 0;
  let returnAuthoritativeAbsence = false;
  sessionApi.submitChat = (async () => {
    submissions += 1;
    if (submissions === 1) {
      throw new ApiRequestError("gateway failure", 500, "queue_unavailable");
    }
    return { status: "needs_confirmation", suggestions: [] };
  }) as typeof sessionApi.submitChat;
  sessionApi.getChatSubmission = async (submissionId) => {
    resolverCalls += 1;
    void submissionId;
    throw new ApiRequestError(
      "legacy resolver not found",
      404,
      "chat_submission_not_found",
    );
  };
  sessionApi.retryChatSubmissionAdmission = async (submissionId) => {
    if (!returnAuthoritativeAbsence) {
      throw new ApiRequestError("recovery not found", 404, "chat_submission_not_found");
    }
    return {
      protocol_version: "chat_submission_resolution.v2",
      submission_id: submissionId,
      state: "absent_before_ledger",
    };
  };
  authApi.logout = async () => {};
  authApi.login = async () => {};

  try {
    await harness.act(async () => {
      assert.deepEqual(await harness.hook.sendMessage("migration-lost submission"), {
        status: "failed",
      });
    });
    await settle(harness.act);
    assert.equal(resolverCalls, 1);
    assert.equal(harness.hook.canRetryPendingSubmission, true);

    await harness.act(async () => {
      harness.hook.clearMessages();
      assert.deepEqual(await harness.hook.sendMessage("New Chat must not clear it"), {
        status: "failed",
      });
    });
    assert.equal(submissions, 1);

    await harness.act(async () => {
      assert.equal(await harness.auth.logout(), true);
    });
    await settle(harness.act);
    returnAuthoritativeAbsence = true;
    await harness.act(async () => {
      await harness.auth.login({ username: "user-a", password: "test-password" });
    });
    await settle(harness.act);

    assert.equal(resolverCalls, 2);
    assert.equal(harness.hook.canRetryPendingSubmission, true);
    await harness.act(async () => {
      await harness.hook.retryPendingSubmission();
    });
    assert.equal(harness.hook.canRetryPendingSubmission, false);
    await harness.act(async () => {
      assert.deepEqual(await harness.hook.sendMessage("recovered same principal"), {
        status: "accepted",
      });
    });
    assert.equal(submissions, 2);
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.getChatSubmission = originalGetChatSubmission;
    sessionApi.retryChatSubmissionAdmission = originalRetryAdmission;
    authApi.logout = originalLogout;
    authApi.login = originalLogin;
    await harness.cleanup();
  }
});

test("useAgent clears a stale pre-ledger fence through explicit retry only after the versioned proof", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalGetChatSubmission = sessionApi.getChatSubmission;
  const originalRetryAdmission = sessionApi.retryChatSubmissionAdmission;
  let submissions = 0;
  sessionApi.submitChat = (async () => {
    submissions += 1;
    if (submissions === 1) {
      throw new ApiRequestError("gateway failure", 500, "queue_unavailable");
    }
    return { status: "needs_confirmation", suggestions: [] };
  }) as typeof sessionApi.submitChat;
  sessionApi.getChatSubmission = async () => {
    throw new ApiRequestError("legacy resolver not found", 404, "chat_submission_not_found");
  };
  sessionApi.retryChatSubmissionAdmission = async (submissionId) => ({
    protocol_version: "chat_submission_resolution.v2",
    submission_id: submissionId,
    state: "absent_before_ledger",
  });

  try {
    await harness.act(async () => {
      await harness.hook.sendMessage("retry pre-ledger submission");
    });
    await settle(harness.act);
    assert.equal(harness.hook.canRetryPendingSubmission, true);

    await harness.act(async () => {
      await harness.hook.retryPendingSubmission();
    });
    assert.equal(harness.hook.canRetryPendingSubmission, false);
    await harness.act(async () => {
      assert.deepEqual(await harness.hook.sendMessage("retry recovery is clear"), {
        status: "accepted",
      });
    });
    assert.equal(submissions, 2);
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.getChatSubmission = originalGetChatSubmission;
    sessionApi.retryChatSubmissionAdmission = originalRetryAdmission;
    await harness.cleanup();
  }
});

test("useAgent keeps generic, network, malformed, legacy, mismatched-ID, and unknown-version GET/retry results fenced", async () => {
  const ambiguousResolvers = [
    {
      name: "network failure",
      resolve: async (_submissionId: string) => {
        throw new Error("resolver network loss");
      },
    },
    {
      name: "generic 404",
      resolve: async (_submissionId: string) => {
        throw new ApiRequestError("not found", 404, "not_found");
      },
    },
    {
      name: "legacy submission 404",
      resolve: async (_submissionId: string) => {
        throw new ApiRequestError("not found", 404, "chat_submission_not_found");
      },
    },
    {
      name: "unknown protocol version",
      resolve: async (submissionId: string) =>
        ({
          protocol_version: "chat_submission_resolution.v1",
          submission_id: submissionId,
          state: "absent_before_ledger",
        }) as unknown as ChatSubmissionResolution,
    },
    {
      name: "unversioned absence",
      resolve: async (submissionId: string) =>
        ({
          submission_id: submissionId,
          state: "absent_before_ledger",
        }) as unknown as ChatSubmissionResolution,
    },
    {
      name: "mismatched submission ID",
      resolve: async (_submissionId: string) =>
        ({
          protocol_version: "chat_submission_resolution.v2",
          submission_id: "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
          state: "absent_before_ledger",
        }) as unknown as ChatSubmissionResolution,
    },
    {
      name: "rejection without the exact disposition",
      resolve: async (submissionId: string) =>
        ({
          submission_id: submissionId,
          state: "rejected_before_persist",
          rejection_code: "chat_submission_retired_before_ledger",
        }) as unknown as ChatSubmissionResolution,
    },
    {
      name: "rejection without a nonempty code",
      resolve: async (submissionId: string) =>
        ({
          submission_id: submissionId,
          state: "rejected_before_persist",
          submission_disposition: "rejected_before_persist",
          rejection_code: " ",
        }) as unknown as ChatSubmissionResolution,
    },
    {
      name: "rejection with a contradictory outcome",
      resolve: async (submissionId: string) =>
        ({
          submission_id: submissionId,
          state: "rejected_before_persist",
          submission_disposition: "rejected_before_persist",
          rejection_code: "chat_submission_retired_before_ledger",
          outcome: {
            session_id: "session-contradictory",
            run_id: "run-contradictory",
            status: "queued",
            submission_id: submissionId,
          },
        }) as unknown as ChatSubmissionResolution,
    },
    {
      name: "queued outcome without the exact submission ID",
      resolve: async (submissionId: string) =>
        ({
          submission_id: submissionId,
          state: "queued",
          outcome: {
            session_id: "session-missing-id",
            run_id: "run-missing-id",
            status: "queued",
          },
        }) as unknown as ChatSubmissionResolution,
    },
    {
      name: "pending admission outcome without a run ID",
      resolve: async (submissionId: string) =>
        ({
          submission_id: submissionId,
          state: "accepted_pending_enqueue",
          outcome: {
            session_id: "session-missing-run",
            status: "accepted_pending_enqueue",
            submission_id: submissionId,
          },
        }) as unknown as ChatSubmissionResolution,
    },
    {
      name: "confirmation outcome with malformed suggestions",
      resolve: async (submissionId: string) =>
        ({
          submission_id: submissionId,
          state: "needs_confirmation",
          outcome: {
            status: "needs_confirmation",
            submission_id: submissionId,
            suggestions: [{ capability_id: "skill-a", label: "Skill A" }],
          },
        }) as unknown as ChatSubmissionResolution,
    },
    {
      name: "unvalidated enqueue failure",
      resolve: async (submissionId: string) =>
        ({
          submission_id: submissionId,
          state: "enqueue_failed",
        }) as unknown as ChatSubmissionResolution,
    },
  ];

  for (const { name, resolve } of ambiguousResolvers) {
    const harness = await loadReactHarness();
    const { sessionApi } = await import("../../../services/api/session.ts");
    const originalSubmitChat = sessionApi.submitChat;
    const originalGetChatSubmission = sessionApi.getChatSubmission;
    const originalRetryAdmission = sessionApi.retryChatSubmissionAdmission;
    let submissions = 0;
    sessionApi.submitChat = (async () => {
      submissions += 1;
      throw new ApiRequestError("gateway failure", 500, "queue_unavailable");
    }) as typeof sessionApi.submitChat;
    sessionApi.getChatSubmission = async (submissionId) => resolve(submissionId);
    sessionApi.retryChatSubmissionAdmission = async (submissionId) => resolve(submissionId);

    try {
      await harness.act(async () => {
        await harness.hook.sendMessage(`ambiguous resolver: ${name}`);
      });
      await settle(harness.act);
      assert.equal(harness.hook.canRetryPendingSubmission, true, name);

      await harness.act(async () => {
        harness.hook.clearMessages();
        assert.deepEqual(await harness.hook.sendMessage(`must remain locked: ${name}`), {
          status: "failed",
        });
        await harness.hook.retryPendingSubmission();
      });
      assert.equal(submissions, 1, name);
      assert.equal(harness.hook.canRetryPendingSubmission, true, name);
    } finally {
      sessionApi.submitChat = originalSubmitChat;
      sessionApi.getChatSubmission = originalGetChatSubmission;
      sessionApi.retryChatSubmissionAdmission = originalRetryAdmission;
      await harness.cleanup();
    }
  }
});

test("useAgent blocks A mutations through deferred B hydration and restores only the settled owner fence", async () => {
  const submissionA = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4";
  const submissionB = "82f7e9d6-2d0d-4be7-9d85-8e558fb07d83";
  clearPersistedSubmissionReferences();
  dom.window.localStorage.setItem(
    "ai_platform_chat_submission_references_v1",
    JSON.stringify([
      { version: 1, owner: ["tenant-a", "user-a"], submissionId: submissionA },
      { version: 1, owner: ["tenant-b", "user-b"], submissionId: submissionB },
    ]),
  );
  const { sessionApi } = await import("../../../services/api/session.ts");
  const { authApi } = await import("../../../services/api/auth.ts");
  const originalGetChatSubmission = sessionApi.getChatSubmission;
  const originalRetryAdmission = sessionApi.retryChatSubmissionAdmission;
  const originalSubmitChat = sessionApi.submitChat;
  const originalAuthLogin = authApi.login;
  const originalAuthGetCurrentUser = authApi.getCurrentUser;
  const resolverCalls: string[] = [];
  let retryCalls = 0;
  let submitCalls = 0;
  sessionApi.getChatSubmission = async (submissionId) => {
    resolverCalls.push(submissionId);
    throw new ApiRequestError("not found", 404, "chat_submission_not_found");
  };
  sessionApi.retryChatSubmissionAdmission = async () => {
    retryCalls += 1;
    throw new Error("the incarnation fence should block retry admission");
  };
  sessionApi.submitChat = async () => {
    submitCalls += 1;
    throw new Error("the incarnation fence should block new admission");
  };
  authApi.login = async () => {};
  const harness = await loadReactHarness({ preserveSubmissionReferences: true });

  try {
    await settle(harness.act);
    assert.deepEqual(resolverCalls, [submissionA]);
    assert.equal(harness.hook.canRetryPendingSubmission, true);

    const principalA = harness.auth.user;
    if (principalA === null) {
      throw new Error("the initial A principal should be hydrated");
    }
    const principalB = {
      ...principalA,
      id: "user-b",
      tenant_id: "tenant-b",
      username: "user-b",
      email: "user-b@example.test",
    };
    let resolveBPrincipal!: (value: NonNullable<typeof harness.auth.user>) => void;
    let markBPrincipalRequested!: () => void;
    const bPrincipalRequested = new Promise<void>((resolve) => {
      markBPrincipalRequested = resolve;
    });
    const deferredBPrincipal = new Promise<NonNullable<typeof harness.auth.user>>((resolve) => {
      resolveBPrincipal = resolve;
    });
    authApi.getCurrentUser = (() => {
      markBPrincipalRequested();
      return deferredBPrincipal;
    }) as typeof authApi.getCurrentUser;

    let loginB!: Promise<unknown>;
    await harness.act(async () => {
      loginB = harness.auth.login({ username: "user-b", password: "test-password" });
      await bPrincipalRequested;
    });
    const resolverCallsBeforeBHydrates = resolverCalls.length;
    await harness.act(async () => {
      assert.deepEqual(await harness.hook.sendMessage("must not submit as A"), {
        status: "failed",
      });
      await harness.hook.retryPendingSubmission();
    });
    assert.equal(submitCalls, 0);
    assert.equal(retryCalls, 0);
    assert.equal(resolverCalls.length, resolverCallsBeforeBHydrates);
    assert.match(
      persistedSubmissionStorageValues().join("\n"),
      new RegExp(submissionA),
      "the A durable reference must survive B's deferred principal read",
    );

    await harness.act(async () => {
      resolveBPrincipal(principalB);
      await loginB;
    });
    await settle(harness.act);
    assert.deepEqual(resolverCalls.slice(resolverCallsBeforeBHydrates), [submissionB]);

    let resolveAPrincipal!: (value: NonNullable<typeof harness.auth.user>) => void;
    let markAPrincipalRequested!: () => void;
    const aPrincipalRequested = new Promise<void>((resolve) => {
      markAPrincipalRequested = resolve;
    });
    const deferredAPrincipal = new Promise<NonNullable<typeof harness.auth.user>>((resolve) => {
      resolveAPrincipal = resolve;
    });
    authApi.getCurrentUser = (() => {
      markAPrincipalRequested();
      return deferredAPrincipal;
    }) as typeof authApi.getCurrentUser;
    let loginA!: Promise<unknown>;
    await harness.act(async () => {
      loginA = harness.auth.login({ username: "user-a", password: "test-password" });
      await aPrincipalRequested;
    });
    await harness.act(async () => {
      resolveAPrincipal(principalA);
      await loginA;
    });
    await settle(harness.act);
    assert.deepEqual(resolverCalls, [submissionA, submissionB, submissionA]);
    assert.equal(submitCalls, 0);
    assert.equal(retryCalls, 0);
  } finally {
    sessionApi.getChatSubmission = originalGetChatSubmission;
    sessionApi.retryChatSubmissionAdmission = originalRetryAdmission;
    sessionApi.submitChat = originalSubmitChat;
    authApi.login = originalAuthLogin;
    authApi.getCurrentUser = originalAuthGetCurrentUser;
    await harness.cleanup();
  }
});

test("useAgent keeps an unknown submission locked until explicit admission retry", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalMarkRead = sessionApi.markRead;
  const originalSubmitChat = sessionApi.submitChat;
  const originalRetryAdmission = sessionApi.retryChatSubmissionAdmission;
  let submissions = 0;
  let retryAdmissions = 0;
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-known-uncertain",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async () => ({ events: [] });
  sessionApi.submitChat = (async () => {
    submissions += 1;
    if (submissions === 1) {
      throw new Error("response lost after server acceptance");
    }
    return { status: "needs_confirmation", suggestions: [] };
  }) as typeof sessionApi.submitChat;
  sessionApi.retryChatSubmissionAdmission = async (submissionId) => {
    retryAdmissions += 1;
    return {
      submission_id: submissionId,
      state: "queued",
      outcome: {
        session_id: "session-known-uncertain",
        run_id: "run-known-uncertain",
        trace_id: "trace-known-uncertain",
        status: "queued",
        submission_id: submissionId,
      },
    };
  };

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-known-uncertain");
      await harness.hook.sendMessage("结果未知的后续请求");
      await harness.hook.sendMessage("刷新前不得重复提交");
    });
    assert.equal(submissions, 1);

    await harness.act(async () => {
      await harness.hook.loadHistory("session-known-uncertain");
      assert.deepEqual(await harness.hook.sendMessage("history 不得解除未知状态"), {
        status: "failed",
      });
    });
    assert.equal(submissions, 1);

    await harness.act(async () => {
      await harness.hook.retryPendingSubmission();
    });
    assert.equal(retryAdmissions, 1);
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.markRead = originalMarkRead;
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.retryChatSubmissionAdmission = originalRetryAdmission;
    await harness.cleanup();
  }
});

test("useAgent fails closed before POST when durable submission storage cannot be confirmed", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const storage = dom.window.localStorage;
  const originalSetItem = storage.setItem;
  let submissions = 0;
  storage.setItem = ((key: string, value: string) => {
    if (key.startsWith("ai_platform_chat_submission")) {
      throw new Error("private-mode storage denied");
    }
    originalSetItem.call(storage, key, value);
  }) as Storage["setItem"];
  sessionApi.submitChat = (async () => {
    submissions += 1;
    return {
      session_id: "session-must-not-submit",
      run_id: "run-must-not-submit",
      trace_id: "trace-must-not-submit",
      status: "queued",
    };
  }) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      assert.deepEqual(await harness.hook.sendMessage("storage must fence POST"), {
        status: "failed",
      });
    });
    assert.equal(submissions, 0);
    assert.equal(harness.hook.messages.length, 0);
  } finally {
    storage.setItem = originalSetItem;
    sessionApi.submitChat = originalSubmitChat;
    await harness.cleanup();
  }
});

test("useAgent retains independently persisted submissions from concurrent tabs", async () => {
  const first = await loadReactHarness();
  const second = await loadReactHarness({ preserveSubmissionReferences: true });
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const storage = dom.window.localStorage;
  const originalGetItem = storage.getItem;
  const submissionIds: string[] = [];
  storage.getItem = ((key: string) =>
    key === "ai_platform_chat_submission_references_v1"
      ? "[]"
      : originalGetItem.call(storage, key)) as Storage["getItem"];
  sessionApi.submitChat = (async (...args) => {
    submissionIds.push(String(args[8]));
    throw new Error("response lost after server acceptance");
  }) as typeof sessionApi.submitChat;

  try {
    await first.act(async () => {
      await first.hook.sendMessage("tab one unknown submission");
    });
    await second.act(async () => {
      await second.hook.sendMessage("tab two unknown submission");
    });
    storage.getItem = originalGetItem;
    const persisted = persistedSubmissionStorageValues();
    assert.equal(submissionIds.length, 2);
    assert.equal(
      submissionIds.every((id) => persisted.some((value) => value.includes(id))),
      true,
    );
  } finally {
    storage.getItem = originalGetItem;
    sessionApi.submitChat = originalSubmitChat;
    await first.cleanup();
    await second.cleanup();
  }
});

test("useAgent installs a persisted submission fence before its resolver settles", async () => {
  const submissionId = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4";
  clearPersistedSubmissionReferences();
  dom.window.localStorage.setItem(
    "ai_platform_chat_submission_references_v1",
    JSON.stringify([{ version: 1, owner: ["tenant-a", "user-a"], submissionId }]),
  );
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGetChatSubmission = sessionApi.getChatSubmission;
  const originalSubmitChat = sessionApi.submitChat;
  let resolverStarted!: () => void;
  const started = new Promise<void>((resolve) => {
    resolverStarted = resolve;
  });
  const unresolved = new Promise<Awaited<ReturnType<typeof sessionApi.getChatSubmission>>>(
    () => {},
  );
  let submissions = 0;
  sessionApi.getChatSubmission = async () => {
    resolverStarted();
    return unresolved;
  };
  sessionApi.submitChat = (async () => {
    submissions += 1;
    throw new Error("the pre-paint fence should prevent this POST");
  }) as typeof sessionApi.submitChat;
  const harness = await loadReactHarness({ preserveSubmissionReferences: true });

  try {
    await started;
    await harness.act(async () => {
      assert.deepEqual(await harness.hook.sendMessage("must stay locked"), {
        status: "failed",
      });
    });
    assert.equal(submissions, 0);
    assert.equal(harness.hook.canRetryPendingSubmission, true);
  } finally {
    sessionApi.getChatSubmission = originalGetChatSubmission;
    sessionApi.submitChat = originalSubmitChat;
    await harness.cleanup();
  }
});

test("useAgent drops an A1 resolver result after A-to-B-to-A2", async () => {
  const a1 = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4";
  const a2 = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c5";
  clearPersistedSubmissionReferences();
  dom.window.localStorage.setItem(
    "ai_platform_chat_submission_references_v1",
    JSON.stringify([{ version: 1, owner: ["tenant-a", "user-a"], submissionId: a1 }]),
  );
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGetChatSubmission = sessionApi.getChatSubmission;
  const originalRetryAdmission = sessionApi.retryChatSubmissionAdmission;
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  let resolveA1!: (value: Awaited<ReturnType<typeof sessionApi.getChatSubmission>>) => void;
  const a1Result = new Promise<Awaited<ReturnType<typeof sessionApi.getChatSubmission>>>(
    (resolve) => {
      resolveA1 = resolve;
    },
  );
  let a1Started!: () => void;
  const started = new Promise<void>((resolve) => {
    a1Started = resolve;
  });
  const retried: string[] = [];
  sessionApi.getChatSubmission = async (submissionId) => {
    if (submissionId === a1) {
      a1Started();
      return a1Result;
    }
    assert.equal(submissionId, a2);
    return {
      submission_id: a2,
      state: "accepted_pending_enqueue",
      outcome: {
        session_id: "session-a2",
        run_id: "run-a2",
        status: "accepted_pending_enqueue",
        submission_id: a2,
      },
    };
  };
  sessionApi.retryChatSubmissionAdmission = async (submissionId) => {
    retried.push(submissionId);
    return {
      submission_id: submissionId,
      state: "accepted_pending_enqueue",
      outcome: {
        session_id: "session-a2",
        run_id: "run-a2",
        status: "accepted_pending_enqueue",
        submission_id: submissionId,
      },
    };
  };
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-a1",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async () => ({ events: [] });
  sessionApi.getStatus = async () => ({
    session_id: "session-a1",
    status: "idle",
    raw_status: "idle",
  });
  const harness = await loadReactHarness({ preserveSubmissionReferences: true });

  try {
    await started;
    await harness.rotateAuthScope("user-b", "tenant-b");
    dom.window.localStorage.setItem(
      "ai_platform_chat_submission_references_v1",
      JSON.stringify([{ version: 1, owner: ["tenant-a", "user-a"], submissionId: a2 }]),
    );
    await harness.rotateAuthScope("user-a", "tenant-a");
    await settle(harness.act);

    resolveA1({
      submission_id: a1,
      state: "queued",
      outcome: {
        session_id: "session-a1",
        run_id: "run-a1",
        trace_id: "trace-a1",
        status: "queued",
        submission_id: a1,
      },
    });
    await settle(harness.act);
    await harness.act(async () => {
      await harness.hook.retryPendingSubmission();
    });

    assert.deepEqual(retried, [a2]);
    assert.notEqual(harness.hook.sessionId, "session-a1");
  } finally {
    sessionApi.getChatSubmission = originalGetChatSubmission;
    sessionApi.retryChatSubmissionAdmission = originalRetryAdmission;
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    await harness.cleanup();
  }
});

test("useAgent keeps recovered confirmation outside the chat transcript", async () => {
  const submissionId = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4";
  clearPersistedSubmissionReferences();
  dom.window.localStorage.setItem(
    "ai_platform_chat_submission_references_v1",
    JSON.stringify([{ version: 1, owner: ["tenant-a", "user-a"], submissionId }]),
  );
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGetChatSubmission = sessionApi.getChatSubmission;
  sessionApi.getChatSubmission = async () => ({
    submission_id: submissionId,
    state: "needs_confirmation",
    outcome: {
      status: "needs_confirmation",
      submission_id: submissionId,
      suggestions: [
        {
          capability_id: "document_review",
          label: "文档审核",
          reason: "需要选择处理方式",
        },
      ],
    },
  });
  const harness = await loadReactHarness({ preserveSubmissionReferences: true });

  try {
    await settle(harness.act);
    assert.equal(harness.hook.messages.length, 0);
    assert.equal(harness.hook.canRetryPendingSubmission, false);
    assert.equal(harness.hook.error, null);
  } finally {
    sessionApi.getChatSubmission = originalGetChatSubmission;
    await harness.cleanup();
  }
});

test("useAgent isolates a recovered confirmation from the next unresolved submission", async () => {
  const confirmationId = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4";
  const unresolvedId = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c5";
  clearPersistedSubmissionReferences();
  dom.window.localStorage.setItem(
    "ai_platform_chat_submission_references_v1",
    JSON.stringify([
      { version: 1, owner: ["tenant-a", "user-a"], submissionId: confirmationId },
      { version: 1, owner: ["tenant-a", "user-a"], submissionId: unresolvedId },
    ]),
  );
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGetChatSubmission = sessionApi.getChatSubmission;
  const resolved: string[] = [];
  sessionApi.getChatSubmission = async (submissionId) => {
    resolved.push(submissionId);
    if (submissionId === confirmationId) {
      return {
        submission_id: confirmationId,
        state: "needs_confirmation",
        outcome: {
          status: "needs_confirmation",
          submission_id: confirmationId,
          suggestions: [],
        },
      };
    }
    assert.equal(submissionId, unresolvedId);
    return {
      submission_id: unresolvedId,
      state: "accepted_pending_enqueue",
      outcome: {
        session_id: "session-unresolved",
        run_id: "run-unresolved",
        status: "accepted_pending_enqueue",
        submission_id: unresolvedId,
      },
    };
  };
  const harness = await loadReactHarness({ preserveSubmissionReferences: true });

  try {
    await settle(harness.act);
    assert.deepEqual(resolved, [confirmationId, unresolvedId]);
    assert.equal(harness.hook.messages.length, 0);
    assert.equal(harness.hook.canRetryPendingSubmission, true);
  } finally {
    sessionApi.getChatSubmission = originalGetChatSubmission;
    await harness.cleanup();
  }
});

test("useAgent quarantines malformed persisted records before resolving a valid successor", async () => {
  const malformedId = "00000000-0000-0000-0000-000000000000";
  const validId = "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4";
  const oversizedOwner = "x".repeat(129);
  clearPersistedSubmissionReferences();
  dom.window.localStorage.setItem(
    "ai_platform_chat_submission_references_v1",
    JSON.stringify([
      { version: 1, owner: ["tenant-a", "user-a"], submissionId: malformedId },
      { version: 1, owner: ["tenant-a", oversizedOwner], submissionId: validId },
      { version: 1, owner: ["tenant-a", "user-a"], submissionId: validId },
    ]),
  );
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGetChatSubmission = sessionApi.getChatSubmission;
  const resolved: string[] = [];
  sessionApi.getChatSubmission = async (submissionId) => {
    resolved.push(submissionId);
    assert.equal(submissionId, validId);
    return {
      submission_id: validId,
      state: "accepted_pending_enqueue",
      outcome: {
        session_id: "session-valid",
        run_id: "run-valid",
        status: "accepted_pending_enqueue",
        submission_id: validId,
      },
    };
  };
  const harness = await loadReactHarness({ preserveSubmissionReferences: true });

  try {
    await settle(harness.act);
    assert.deepEqual(resolved, [validId]);
    const retained = dom.window.localStorage.getItem(
      "ai_platform_chat_submission_references_v1",
    );
    assert.doesNotMatch(retained || "", new RegExp(malformedId));
    assert.doesNotMatch(retained || "", new RegExp(oversizedOwner));
    assert.match(retained || "", new RegExp(validId));
  } finally {
    sessionApi.getChatSubmission = originalGetChatSubmission;
    await harness.cleanup();
  }
});

test("useAgent resolves a fresh unknown submission after reload without another chat POST", async () => {
  const first = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalGetChatSubmission = sessionApi.getChatSubmission;
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  let submissions = 0;
  let resolverCalls = 0;
  let submissionId = "";
  sessionApi.markRead = async () => {};
  sessionApi.submitChat = (async (...args) => {
    submissions += 1;
    submissionId = String(args[8]);
    throw new Error("response lost after commit");
  }) as typeof sessionApi.submitChat;

  try {
    await first.act(async () => {
      await first.hook.sendMessage("fresh reload must resolve");
    });
    assert.equal(submissions, 1);
    const stored = persistedSubmissionStorageValues().join("\n");
    assert.match(stored || "", new RegExp(submissionId));
    assert.doesNotMatch(stored || "", /fresh reload must resolve/);

    sessionApi.getChatSubmission = async (id) => {
      resolverCalls += 1;
      assert.equal(id, submissionId);
      return {
        submission_id: id,
        state: "queued",
        outcome: {
          session_id: "session-fresh-reloaded",
          run_id: "run-fresh-reloaded",
          trace_id: "trace-fresh-reloaded",
          status: "queued",
          submission_id: id,
        },
      };
    };
    sessionApi.get = async () => ({
      id: "session-fresh-reloaded",
      agent_id: "general-agent",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      is_active: true,
      metadata: {},
    });
    sessionApi.getEvents = async () => ({ events: [] });
    sessionApi.getStatus = async () => ({
      session_id: "session-fresh-reloaded",
      run_id: "run-fresh-reloaded",
      status: "idle",
      raw_status: "idle",
    });
    await first.cleanup();

    const reloaded = await loadReactHarness({
      preserveSubmissionReferences: true,
    });
    try {
      await settle(reloaded.act);
      assert.equal(resolverCalls, 1);
      assert.equal(submissions, 1);
      assert.equal(reloaded.hook.sessionId, "session-fresh-reloaded");
    } finally {
      await reloaded.cleanup();
    }
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.getChatSubmission = originalGetChatSubmission;
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
  }
});

test("useAgent keeps persisted submissions structurally isolated across A-to-B-to-A", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGetChatSubmission = sessionApi.getChatSubmission;
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  let resolverCalls = 0;
  dom.window.localStorage.setItem(
    "ai_platform_chat_submission_references_v1",
    JSON.stringify([
      {
        version: 1,
        owner: ["a:b", "c"],
        submissionId: "7ea93033-30f5-40ea-8a33-2f3c6e7b21c4",
      },
    ]),
  );
  sessionApi.getChatSubmission = async (submissionId) => {
    resolverCalls += 1;
    return {
      submission_id: submissionId,
      state: "queued",
      outcome: {
        session_id: "session-owner-a",
        run_id: "run-owner-a",
        trace_id: "trace-owner-a",
        status: "queued",
        submission_id: submissionId,
      },
    };
  };
  sessionApi.get = async () => ({
    id: "session-owner-a",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async () => ({ events: [] });
  sessionApi.getStatus = async () => ({
    session_id: "session-owner-a",
    run_id: "run-owner-a",
    status: "idle",
    raw_status: "idle",
  });

  try {
    await harness.rotateAuthScope("b:c", "a");
    await settle(harness.act);
    assert.equal(resolverCalls, 0);
    assert.equal(harness.hook.sessionId, null);

    await harness.rotateAuthScope("c", "a:b");
    await settle(harness.act);
    assert.equal(resolverCalls, 1);
    assert.equal(harness.hook.sessionId, "session-owner-a");
  } finally {
    sessionApi.getChatSubmission = originalGetChatSubmission;
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    await harness.cleanup();
  }
});

async function assertStaleSubmitCannotOverwriteNewSession({
  clear,
}: {
  clear: boolean;
}) {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalMarkRead = sessionApi.markRead;
  const originalSubmitChat = sessionApi.submitChat;
  const originalFetch = dom.window.fetch;
  let resolveSubmit!: (value: ChatStreamResponse) => void;
  let sseCalls = 0;
  dom.window.fetch = async () => {
    sseCalls += 1;
    return completedSseResponse();
  };
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-new",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async () => ({ events: [] });
  sessionApi.submitChat = (() =>
    new Promise<ChatStreamResponse>((resolve) => {
      resolveSubmit = resolve;
    })) as typeof sessionApi.submitChat;

  try {
    let staleSubmit: Promise<unknown> | null = null;
    await harness.act(async () => {
      staleSubmit = harness.hook.sendMessage("旧会话请求");
      await Promise.resolve();
    });
    if (clear) {
      await harness.act(async () => {
        harness.hook.clearMessages();
      });
    } else {
      await harness.act(async () => {
        await harness.hook.loadHistory("session-new");
      });
    }
    resolveSubmit({
      session_id: "session-old",
      run_id: "run-old",
      trace_id: "trace-old",
      status: "queued",
      intent_decision: { agent_id: "document-translation" },
    });
    await harness.act(async () => {
      await staleSubmit;
    });
    await settle(harness.act);

    assert.equal(harness.hook.sessionId, clear ? null : "session-new");
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.messages.length, 0);
    assert.equal(sseCalls, 0);
    if (clear) {
      assert.equal(harness.hook.isLoading, false);
      assert.equal(harness.hook.isLoadingHistory, false);
      assert.equal(harness.hook.isInitializingSandbox, false);
      assert.equal(harness.hook.connectionStatus, "disconnected");
    }
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.markRead = originalMarkRead;
    sessionApi.submitChat = originalSubmitChat;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
}

test("useAgent ignores a late submit response after switching sessions", async () => {
  await assertStaleSubmitCannotOverwriteNewSession({ clear: false });
});

test("useAgent ignores a late submit response after clearing the session", async () => {
  await assertStaleSubmitCannotOverwriteNewSession({ clear: true });
});

test("useAgent ignores an old title response after clear creates a new session", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalMarkRead = sessionApi.markRead;
  const originalGenerateTitle = sessionApi.generateTitle;
  const originalFetch = dom.window.fetch;
  const titleResolvers: Array<
    (value: { title: string; session_id: string }) => void
  > = [];
  let submissionCount = 0;
  dom.window.fetch = async () => completedSseResponse();
  sessionApi.markRead = async () => {};
  sessionApi.submitChat = (async () => {
    submissionCount += 1;
    const suffix = submissionCount === 1 ? "old" : "new";
    return {
      session_id: `session-${suffix}`,
      run_id: `run-${suffix}`,
      trace_id: `trace-${suffix}`,
      status: "queued",
    };
  }) as typeof sessionApi.submitChat;
  sessionApi.generateTitle = ((_sessionId: string) =>
    new Promise((resolve) => {
      titleResolvers.push(resolve);
    })) as typeof sessionApi.generateTitle;

  try {
    await harness.act(async () => {
      await harness.hook.sendMessage("旧会话");
    });
    await harness.act(async () => {
      harness.hook.clearMessages();
      await harness.hook.sendMessage("新会话");
    });
    assert.equal(titleResolvers.length, 2);

    await harness.act(async () => {
      titleResolvers[1]?.({ title: "新会话标题", session_id: "session-new" });
      await Promise.resolve();
    });
    await harness.act(async () => {
      titleResolvers[0]?.({ title: "旧会话标题", session_id: "session-old" });
      await Promise.resolve();
    });
    await settle(harness.act);

    assert.equal(harness.hook.sessionId, "session-new");
    assert.equal(harness.hook.newlyCreatedSession?.name, "新会话标题");
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.markRead = originalMarkRead;
    sessionApi.generateTitle = originalGenerateTitle;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent clear invalidates delayed history get, events, and status continuations", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  let resolveSession!: (value: Awaited<ReturnType<typeof sessionApi.get>>) => void;
  let resolveEvents!: (value: Awaited<ReturnType<typeof sessionApi.getEvents>>) => void;
  let resolveStatus!: (value: Awaited<ReturnType<typeof sessionApi.getStatus>>) => void;
  const statusCalls: Array<[string, string | undefined]> = [];
  sessionApi.markRead = async () => {};
  sessionApi.get = () =>
    new Promise((resolve) => {
      resolveSession = resolve;
    });
  sessionApi.getEvents = () =>
    new Promise((resolve) => {
      resolveEvents = resolve;
    });
  sessionApi.getStatus = (async (sessionId, runId) => {
    statusCalls.push([sessionId, runId]);
    return new Promise((resolve) => {
      resolveStatus = resolve;
    });
  }) as typeof sessionApi.getStatus;

  try {
    let history: Promise<unknown> | null = null;
    await harness.act(async () => {
      history = harness.hook.loadHistory("session-delayed");
      await Promise.resolve();
    });
    resolveSession({
      id: "session-delayed",
      agent_id: "document-translation",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      is_active: true,
      metadata: {},
    });
    await harness.act(async () => {
      await Promise.resolve();
    });
    resolveEvents({
      current_run_id: "run-delayed",
      events: [
        {
          id: "evt-delayed",
          run_id: "run-delayed",
          event_type: "user:message",
          timestamp: "2026-07-15T00:00:00Z",
          data: { content: "旧历史" },
        },
      ],
    });
    await harness.act(async () => {
      await Promise.resolve();
    });
    assert.deepEqual(statusCalls, [["session-delayed", "run-delayed"]]);

    await harness.act(async () => {
      harness.hook.clearMessages();
    });
    resolveStatus({
      session_id: "session-delayed",
      run_id: "run-delayed",
      status: "running",
    });
    await harness.act(async () => {
      await history;
    });
    await settle(harness.act);

    assert.equal(harness.hook.sessionId, null);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.messages.length, 0);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(harness.hook.isLoadingHistory, false);
    assert.equal(harness.hook.connectionStatus, "disconnected");
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    await harness.cleanup();
  }
});

test("useAgent rejects a deferred A session GET after B history owns the hook", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalMarkRead = sessionApi.markRead;
  let resolveA!: (value: Awaited<ReturnType<typeof sessionApi.get>>) => void;
  let notifyAGetStarted!: () => void;
  const aGetStarted = new Promise<void>((resolve) => {
    notifyAGetStarted = resolve;
  });
  sessionApi.markRead = async () => {};
  sessionApi.get = ((sessionId: string) => {
    if (sessionId === "session-a") {
      return new Promise((resolve) => {
        resolveA = resolve;
        notifyAGetStarted();
      });
    }
    return Promise.resolve({
      id: "session-b",
      agent_id: "agent-b",
      created_at: "2026-07-15T00:00:00Z",
      updated_at: "2026-07-15T00:00:00Z",
      is_active: true,
      metadata: {},
    });
  }) as typeof sessionApi.get;
  sessionApi.getEvents = (async (sessionId: string) => ({
    events: [
      {
        id: `${sessionId}:user`,
        event_type: "user:message",
        timestamp: "2026-07-15T00:00:01Z",
        data: { content: `history-${sessionId}` },
      },
    ],
  })) as typeof sessionApi.getEvents;

  try {
    let loadA!: Promise<unknown>;
    await harness.act(async () => {
      loadA = harness.hook.loadHistory("session-a");
      await aGetStarted;
    });
    let loadB!: Promise<unknown>;
    await harness.act(async () => {
      loadB = harness.hook.loadHistory("session-b");
      await Promise.resolve();
    });
    await harness.act(async () => {
      await loadB;
    });
    resolveA({
      id: "session-a",
      agent_id: "agent-a",
      created_at: "2026-07-15T00:00:00Z",
      updated_at: "2026-07-15T00:00:00Z",
      is_active: true,
      metadata: { current_run_id: "run-a" },
    });
    await harness.act(async () => {
      await loadA;
    });

    assert.equal(harness.hook.sessionId, "session-b");
    assert.equal(harness.hook.currentRunId, null);
    assert.match(JSON.stringify(harness.hook.messages), /history-session-b/);
    assert.doesNotMatch(JSON.stringify(harness.hook.messages), /session-a|run-a/);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(harness.hook.isLoadingHistory, false);
    assert.equal(harness.hook.connectionStatus, "disconnected");
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.markRead = originalMarkRead;
    await harness.cleanup();
  }
});

test("useAgent rejects deferred A events after B history owns the hook", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  let resolveAEvents!: (
    value: Awaited<ReturnType<typeof sessionApi.getEvents>>,
  ) => void;
  let notifyAEventsStarted!: () => void;
  const aEventsStarted = new Promise<void>((resolve) => {
    notifyAEventsStarted = resolve;
  });
  let statusCalls = 0;
  sessionApi.markRead = async () => {};
  sessionApi.get = (async (sessionId: string) => ({
    id: sessionId,
    agent_id: sessionId === "session-a" ? "agent-a" : "agent-b",
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:00Z",
    is_active: true,
    metadata: {},
  })) as typeof sessionApi.get;
  sessionApi.getEvents = ((sessionId: string) => {
    if (sessionId === "session-a") {
      return new Promise((resolve) => {
        resolveAEvents = resolve;
        notifyAEventsStarted();
      });
    }
    return Promise.resolve({
      events: [
          {
            id: "session-b:user",
            event_type: "user:message",
          timestamp: "2026-07-15T00:00:01Z",
          data: { content: "history-session-b" },
        },
      ],
    });
  }) as typeof sessionApi.getEvents;
  sessionApi.getStatus = (async () => {
    statusCalls += 1;
    return { session_id: "session-a", run_id: "run-a", status: "running" };
  }) as typeof sessionApi.getStatus;

  try {
    let loadA!: Promise<unknown>;
    await harness.act(async () => {
      loadA = harness.hook.loadHistory("session-a");
      await aEventsStarted;
    });
    let loadB!: Promise<unknown>;
    await harness.act(async () => {
      loadB = harness.hook.loadHistory("session-b");
      await Promise.resolve();
    });
    await harness.act(async () => {
      await loadB;
    });
    resolveAEvents({
      current_run_id: "run-a",
      events: [
        {
          id: "session-a:user",
          run_id: "run-a",
          event_type: "user:message",
          timestamp: "2026-07-15T00:00:02Z",
          data: { content: "history-session-a" },
        },
      ],
    });
    await harness.act(async () => {
      await loadA;
    });

    assert.equal(statusCalls, 0);
    assert.equal(harness.hook.sessionId, "session-b");
    assert.equal(harness.hook.currentRunId, null);
    assert.match(JSON.stringify(harness.hook.messages), /history-session-b/);
    assert.doesNotMatch(JSON.stringify(harness.hook.messages), /history-session-a/);
    assert.equal(harness.hook.isLoadingHistory, false);
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    await harness.cleanup();
  }
});

test("useAgent rejects a deferred A status after B history owns the hook", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  let resolveAStatus!: (
    value: Awaited<ReturnType<typeof sessionApi.getStatus>>,
  ) => void;
  let statusStarted!: () => void;
  const statusStart = new Promise<void>((resolve) => {
    statusStarted = resolve;
  });
  sessionApi.markRead = async () => {};
  sessionApi.get = (async (sessionId: string) => ({
    id: sessionId,
    agent_id: sessionId === "session-a" ? "agent-a" : "agent-b",
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:00Z",
    is_active: true,
    metadata: {},
  })) as typeof sessionApi.get;
  sessionApi.getEvents = (async (sessionId: string) =>
    sessionId === "session-a"
      ? {
          current_run_id: "run-a",
          events: [
            {
              id: "session-a:user",
              run_id: "run-a",
              event_type: "user:message",
              timestamp: "2026-07-15T00:00:01Z",
              data: { content: "history-session-a" },
            },
          ],
        }
      : {
          events: [
            {
              id: "session-b:user",
              event_type: "user:message",
              timestamp: "2026-07-15T00:00:02Z",
              data: { content: "history-session-b" },
            },
          ],
        }) as typeof sessionApi.getEvents;
  sessionApi.getStatus = (async () => {
    statusStarted();
    return new Promise((resolve) => {
      resolveAStatus = resolve;
    });
  }) as typeof sessionApi.getStatus;

  try {
    let loadA!: Promise<unknown>;
    await harness.act(async () => {
      loadA = harness.hook.loadHistory("session-a");
      await statusStart;
    });
    let loadB!: Promise<unknown>;
    await harness.act(async () => {
      loadB = harness.hook.loadHistory("session-b");
      await Promise.resolve();
    });
    await harness.act(async () => {
      await loadB;
    });
    resolveAStatus({
      session_id: "session-a",
      run_id: "run-a",
      status: "running",
    });
    await harness.act(async () => {
      await loadA;
    });

    assert.equal(harness.hook.sessionId, "session-b");
    assert.equal(harness.hook.currentRunId, null);
    assert.match(JSON.stringify(harness.hook.messages), /history-session-b/);
    assert.doesNotMatch(JSON.stringify(harness.hook.messages), /history-session-a/);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(harness.hook.isLoadingHistory, false);
    assert.equal(harness.hook.connectionStatus, "disconnected");
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    await harness.cleanup();
  }
});

test("useAgent finalizes a failed run once with a Chinese product failure card", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalMarkRead = sessionApi.markRead;
  const originalGenerateTitle = sessionApi.generateTitle;
  const originalFetch = dom.window.fetch;
  let sseCalls = 0;
  dom.window.fetch = async () => {
    sseCalls += 1;
    return sseFramesResponse([
      {
        event: "final_detail",
        data: {
          run_id: "run-failed",
          detail_kind: "failed",
          detail_code: "run_failed",
        },
      },
      {
        event: "run_event",
        data: { run_id: "run-failed", event_type: "run_failed" },
      },
    ]);
  };
  sessionApi.markRead = async () => {};
  sessionApi.generateTitle = async () => ({
    title: "失败会话",
    session_id: "session-failed",
  });
  sessionApi.submitChat = (async () => ({
    session_id: "session-failed",
    run_id: "run-failed",
    trace_id: "trace-failed",
    status: "queued",
  })) as typeof sessionApi.submitChat;

  try {
    await settle(harness.act);
    await harness.act(async () => {
      await harness.hook.sendMessage("执行失败的任务");
    });
    await settle(harness.act);

    const terminalCards = harness.hook.messages
      .flatMap((message) => message.parts || [])
      .filter(
        (part) =>
          part.type === "run_status" &&
          part.event_id === "terminal-failure:run-failed",
      );
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(harness.hook.isInitializingSandbox, false);
    assert.equal(harness.hook.connectionStatus, "disconnected");
    assert.equal(terminalCards.length, 1);
    assert.equal(
      terminalCards[0]?.type === "run_status" && terminalCards[0].message,
      "任务未能完成。请稍后重试；如问题持续，请联系管理员。",
    );
    const serializedMessages = JSON.stringify(harness.hook.messages);
    assert.match(serializedMessages, /任务未能完成。请稍后重试/);
    assert.doesNotMatch(serializedMessages, /Executor failed/);
    assert.equal(sseCalls, 1);
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.markRead = originalMarkRead;
    sessionApi.generateTitle = originalGenerateTitle;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent retains final answer and artifact frames that precede a succeeded terminal", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalMarkRead = sessionApi.markRead;
  const originalGenerateTitle = sessionApi.generateTitle;
  const originalFetch = dom.window.fetch;
  dom.window.fetch = async () =>
    sseFramesResponse([
      {
        event: "artifact_card",
        data: {
          run_id: "run-final-success",
          artifact_id: "artifact-final",
          artifact_type: "report",
          label: "最终报告",
          size_bytes: 1,
        },
      },
      {
        event: "message:chunk",
        data: { run_id: "run-final-success", content: "最终答复" },
      },
      {
        event: "run_event",
        data: { run_id: "run-final-success", event_type: "run_succeeded" },
      },
    ]);
  sessionApi.markRead = async () => {};
  sessionApi.generateTitle = async () => ({
    title: "成功终态会话",
    session_id: "session-final-success",
  });
  sessionApi.submitChat = (async () => ({
    session_id: "session-final-success",
    run_id: "run-final-success",
    trace_id: "trace-final-success",
    status: "queued",
  })) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      await harness.hook.sendMessage("生成最终报告");
    });
    await settle(harness.act);

    const assistant = harness.hook.messages.find(
      (message) => message.role === "assistant" && message.runId === "run-final-success",
    );
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(harness.hook.connectionStatus, "disconnected");
    assert.equal(assistant?.content, "最终答复");
    assert.equal(
      assistant?.parts?.some(
        (part) => part.type === "artifact" && part.artifact_id === "artifact-final",
      ),
      true,
    );
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.markRead = originalMarkRead;
    sessionApi.generateTitle = originalGenerateTitle;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent consumes lambchat's runless error then done fallback exactly once", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalMarkRead = sessionApi.markRead;
  const originalGenerateTitle = sessionApi.generateTitle;
  const originalFetch = dom.window.fetch;
  let sseCalls = 0;
  dom.window.fetch = async () => {
    sseCalls += 1;
    return sseFramesResponse([
      { event: "error", data: { error: "run_failed" } },
      { event: "done", data: { status: "failed" } },
    ]);
  };
  sessionApi.markRead = async () => {};
  sessionApi.generateTitle = async () => ({
    title: "失败回退会话",
    session_id: "session-fallback-failed",
  });
  sessionApi.submitChat = (async () => ({
    session_id: "session-fallback-failed",
    run_id: "run-fallback-failed",
    trace_id: "trace-fallback-failed",
    status: "queued",
  })) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      await harness.hook.sendMessage("执行后端失败回退");
    });
    await settle(harness.act);

    const cards = harness.hook.messages
      .flatMap((message) => message.parts || [])
      .filter(
        (part) =>
          part.type === "run_status" &&
          part.event_id === "terminal-failure:run-fallback-failed",
      );
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(harness.hook.connectionStatus, "disconnected");
    assert.equal(cards.length, 1);
    assert.equal(
      cards[0]?.type === "run_status" && cards[0].message,
      "任务未能完成。请稍后重试；如问题持续，请联系管理员。",
    );
    assert.equal(sseCalls, 1);
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.markRead = originalMarkRead;
    sessionApi.generateTitle = originalGenerateTitle;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent waits for a failed done status when the fallback error is public text", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalMarkRead = sessionApi.markRead;
  const originalGenerateTitle = sessionApi.generateTitle;
  const originalGetStatus = sessionApi.getStatus;
  const originalGetEvents = sessionApi.getEvents;
  const originalFetch = dom.window.fetch;
  dom.window.fetch = async () =>
    sseFramesResponse([
      { event: "error", data: { error: "Executor failed" } },
      { event: "done", data: { status: "failed" } },
    ]);
  sessionApi.markRead = async () => {};
  sessionApi.generateTitle = async () => ({
    title: "公开失败回退会话",
    session_id: "session-public-fallback-failed",
  });
  sessionApi.submitChat = (async () => ({
    session_id: "session-public-fallback-failed",
    run_id: "run-public-fallback-failed",
    trace_id: "trace-public-fallback-failed",
    status: "queued",
  })) as typeof sessionApi.submitChat;
  sessionApi.getStatus = (async () => ({
    session_id: "session-public-fallback-failed",
    run_id: "run-public-fallback-failed",
    status: "error",
    raw_status: "failed",
  })) as typeof sessionApi.getStatus;
  sessionApi.getEvents = (async (_sessionId, options) => ({
    events: options?.run_id
      ? [{
          id: "run-public-fallback-failed:final",
          event_type: "final_detail",
          run_id: "run-public-fallback-failed",
          timestamp: "2026-07-15T00:00:01Z",
          data: {
            run_id: "run-public-fallback-failed",
            detail_kind: "failed",
            detail_code: "run_failed",
          },
        }]
      : [],
  })) as typeof sessionApi.getEvents;

  try {
    await harness.act(async () => {
      await harness.hook.sendMessage("执行公开失败回退");
    });
    await settle(harness.act);

    const serializedMessages = JSON.stringify(harness.hook.messages);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(
      harness.hook.messages
        .flatMap((message) => message.parts || [])
        .filter(
          (part) =>
            part.type === "run_status" &&
            part.event_id === "terminal-failure:run-public-fallback-failed",
        ).length,
      1,
    );
    assert.equal(serializedMessages.includes("Executor failed"), false);
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.markRead = originalMarkRead;
    sessionApi.generateTitle = originalGenerateTitle;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.getEvents = originalGetEvents;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent converges a bounded status-query failure to one local unavailable card", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalMarkRead = sessionApi.markRead;
  const originalGenerateTitle = sessionApi.generateTitle;
  const originalGetStatus = sessionApi.getStatus;
  const originalFetch = dom.window.fetch;
  let statusCalls = 0;
  dom.window.fetch = async () =>
    new Response("", { headers: { "content-type": "text/event-stream" } });
  sessionApi.markRead = async () => {};
  sessionApi.generateTitle = async () => ({
    title: "状态不可用会话",
    session_id: "session-status-unavailable",
  });
  sessionApi.submitChat = (async () => ({
    session_id: "session-status-unavailable",
    run_id: "run-status-unavailable",
    trace_id: "trace-status-unavailable",
    status: "queued",
  })) as typeof sessionApi.submitChat;
  sessionApi.getStatus = async () => {
    statusCalls += 1;
    throw new Error("status unavailable");
  };

  try {
    await harness.act(async () => {
      await harness.hook.sendMessage("恢复中断任务");
    });
    await settle(harness.act);

    const parts = harness.hook.messages.flatMap((message) => message.parts || []);
    assert.equal(statusCalls, 3);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(harness.hook.connectionStatus, "disconnected");
    assert.equal(
      parts.filter(
        (part) =>
          part.type === "run_status" &&
          part.event_id === "terminal-status-unavailable:run-status-unavailable",
      ).length,
      1,
    );
    assert.equal(
      parts.some(
        (part) =>
          part.type === "run_status" &&
          part.event_id === "terminal-failure:run-status-unavailable",
      ),
      false,
    );
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.markRead = originalMarkRead;
    sessionApi.generateTitle = originalGenerateTitle;
    sessionApi.getStatus = originalGetStatus;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent fails closed once for a non-retryable SSE authentication error", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalMarkRead = sessionApi.markRead;
  const originalGenerateTitle = sessionApi.generateTitle;
  const originalGetStatus = sessionApi.getStatus;
  const originalFetch = dom.window.fetch;
  let statusCalls = 0;
  let sseCalls = 0;
  dom.window.localStorage.removeItem("ai_platform_session_present");
  dom.window.fetch = async () => {
    sseCalls += 1;
    return new Response(null, { status: 401 });
  };
  sessionApi.markRead = async () => {};
  sessionApi.generateTitle = async () => ({
    title: "认证失效会话",
    session_id: "session-auth-unavailable",
  });
  sessionApi.submitChat = (async () => ({
    session_id: "session-auth-unavailable",
    run_id: "run-auth-unavailable",
    trace_id: "trace-auth-unavailable",
    status: "queued",
  })) as typeof sessionApi.submitChat;
  // A generic stream interruption would query this active projection. A
  // non-retryable authentication rejection must not do so.
  sessionApi.getStatus = (async () => {
    statusCalls += 1;
    return {
      session_id: "session-auth-unavailable",
      run_id: "run-auth-unavailable",
      status: "running",
    };
  }) as typeof sessionApi.getStatus;

  try {
    await harness.act(async () => {
      await harness.hook.sendMessage("认证失效后不应重连");
    });
    await settle(harness.act);
    // The shortest reconnect backoff is one second. Waiting past it proves no
    // stale reconnect timer or second stream attempt was scheduled.
    await new Promise((resolve) => setTimeout(resolve, 1_100));
    await settle(harness.act);

    const parts = harness.hook.messages.flatMap((message) => message.parts || []);
    assert.equal(statusCalls, 0);
    assert.equal(sseCalls, 1);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(harness.hook.connectionStatus, "disconnected");
    assert.equal(
      parts.filter(
        (part) =>
          part.type === "run_status" &&
          part.event_id === "terminal-status-unavailable:run-auth-unavailable",
      ).length,
      1,
    );
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.markRead = originalMarkRead;
    sessionApi.generateTitle = originalGenerateTitle;
    sessionApi.getStatus = originalGetStatus;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent reconciles an ordinary transport interruption authoritatively", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalMarkRead = sessionApi.markRead;
  const originalGenerateTitle = sessionApi.generateTitle;
  const originalGetStatus = sessionApi.getStatus;
  const originalGetEvents = sessionApi.getEvents;
  const originalFetch = dom.window.fetch;
  let statusCalls = 0;
  let sseCalls = 0;
  dom.window.fetch = async () => {
    sseCalls += 1;
    throw new Error("ordinary network interruption");
  };
  sessionApi.markRead = async () => {};
  sessionApi.generateTitle = async () => ({
    title: "网络中断会话",
    session_id: "session-transport-interruption",
  });
  sessionApi.submitChat = (async () => ({
    session_id: "session-transport-interruption",
    run_id: "run-transport-interruption",
    trace_id: "trace-transport-interruption",
    status: "queued",
  })) as typeof sessionApi.submitChat;
  sessionApi.getStatus = (async () => {
    statusCalls += 1;
    return {
      session_id: "session-transport-interruption",
      run_id: "run-transport-interruption",
      status: "error",
      raw_status: "failed",
    };
  }) as typeof sessionApi.getStatus;
  sessionApi.getEvents = (async (_sessionId, options) => ({
    events: options?.run_id
      ? [{
          id: "message-transport-interruption",
          event_type: "user:message",
          run_id: "run-transport-interruption",
          timestamp: "2026-07-15T00:00:00Z",
          data: {
            message_id: "message-transport-interruption",
            run_id: "run-transport-interruption",
            content: "普通网络中断需要核对状态",
          },
        }, {
          id: "run-transport-interruption:final",
          event_type: "final_detail",
          run_id: "run-transport-interruption",
          timestamp: "2026-07-15T00:00:01Z",
          data: {
            run_id: "run-transport-interruption",
            detail_kind: "failed",
            detail_code: "run_failed",
          },
        }]
      : [],
  })) as typeof sessionApi.getEvents;

  try {
    await harness.act(async () => {
      await harness.hook.sendMessage("普通网络中断需要核对状态");
    });
    await settle(harness.act);

    const parts = harness.hook.messages.flatMap((message) => message.parts || []);
    assert.equal(sseCalls, 1);
    assert.equal(statusCalls, 1);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(
      harness.hook.messages.filter(
        (message) =>
          message.role === "user" &&
          message.content === "普通网络中断需要核对状态",
      ).length,
      1,
    );
    assert.equal(
      parts.filter(
        (part) =>
          part.type === "run_status" &&
          part.event_id === "terminal-failure:run-transport-interruption",
      ).length,
      1,
    );
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.markRead = originalMarkRead;
    sessionApi.generateTitle = originalGenerateTitle;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.getEvents = originalGetEvents;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent shares one generation-bound reconciliation owner across concurrent recovery callers", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalMarkRead = sessionApi.markRead;
  const originalGenerateTitle = sessionApi.generateTitle;
  const originalGetStatus = sessionApi.getStatus;
  const originalGetEvents = sessionApi.getEvents;
  const originalFetch = dom.window.fetch;
  let statusCalls = 0;
  let resolveStatus!: (value: Awaited<ReturnType<typeof sessionApi.getStatus>>) => void;
  dom.window.fetch = async () => {
    throw new Error("concurrent recovery transport interruption");
  };
  sessionApi.markRead = async () => {};
  sessionApi.generateTitle = async () => ({
    title: "并发恢复会话",
    session_id: "session-reconcile-owner",
  });
  sessionApi.submitChat = (async () => ({
    session_id: "session-reconcile-owner",
    run_id: "run-reconcile-owner",
    trace_id: "trace-reconcile-owner",
    status: "queued",
  })) as typeof sessionApi.submitChat;
  sessionApi.getStatus = (async () => {
    statusCalls += 1;
    return new Promise((resolve) => {
      resolveStatus = resolve;
    });
  }) as typeof sessionApi.getStatus;
  sessionApi.getEvents = (async (_sessionId, options) => ({
    events: options?.run_id
      ? [{
          id: "run-reconcile-owner:final",
          event_type: "final_detail",
          run_id: "run-reconcile-owner",
          timestamp: "2026-07-15T00:00:01Z",
          data: {
            run_id: "run-reconcile-owner",
            detail_kind: "failed",
            detail_code: "run_failed",
          },
        }]
      : [],
  })) as typeof sessionApi.getEvents;

  try {
    await harness.act(async () => {
      await harness.hook.sendMessage("并发状态恢复");
    });
    await settle(harness.act);
    assert.equal(statusCalls, 1);

    let firstReconnect!: Promise<void>;
    let secondReconnect!: Promise<void>;
    await harness.act(async () => {
      firstReconnect = harness.hook.reconnectSSE();
      secondReconnect = harness.hook.reconnectSSE();
    });
    assert.equal(statusCalls, 1);

    resolveStatus({
      session_id: "session-reconcile-owner",
      run_id: "run-reconcile-owner",
      status: "error",
      raw_status: "failed",
    });
    await harness.act(async () => {
      await Promise.all([firstReconnect, secondReconnect]);
    });
    await settle(harness.act);

    assert.equal(statusCalls, 1);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(
      harness.hook.messages
        .flatMap((message) => message.parts || [])
        .filter(
          (part) =>
            part.type === "run_status" &&
            part.event_id === "terminal-failure:run-reconcile-owner",
        ).length,
      1,
    );
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.markRead = originalMarkRead;
    sessionApi.generateTitle = originalGenerateTitle;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.getEvents = originalGetEvents;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent production 401 fails closed without refresh or status reconciliation", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalMarkRead = sessionApi.markRead;
  const originalGenerateTitle = sessionApi.generateTitle;
  const originalGetStatus = sessionApi.getStatus;
  const originalGetEvents = sessionApi.getEvents;
  const originalFetch = dom.window.fetch;
  const originalGlobalFetch = globalThis.fetch;
  const originalSessionMarker = dom.window.localStorage.getItem(
    "ai_platform_session_present",
  );
  let statusCalls = 0;
  let streamCalls = 0;
  let refreshProbeCalls = 0;
  const fetchWithRefresh: typeof fetch = async (input) => {
    const url = String(input);
    if (url.includes("/api/ai/auth/me")) {
      refreshProbeCalls += 1;
      return new Response("{}", { status: 200 });
    }
    if (url.includes("/stream?run_id=run-initial-post-refresh")) {
      streamCalls += 1;
      if (streamCalls === 1) {
        return new Response(null, { status: 401 });
      }
      throw new Error("initial post-refresh transport interruption");
    }
    throw new Error(`unexpected fetch: ${url}`);
  };
  dom.window.localStorage.setItem("ai_platform_session_present", "present");
  dom.window.fetch = fetchWithRefresh;
  globalThis.fetch = fetchWithRefresh;
  sessionApi.markRead = async () => {};
  sessionApi.generateTitle = async () => ({
    title: "刷新后中断会话",
    session_id: "session-initial-post-refresh",
  });
  sessionApi.submitChat = (async () => ({
    session_id: "session-initial-post-refresh",
    run_id: "run-initial-post-refresh",
    trace_id: "trace-initial-post-refresh",
    status: "queued",
  })) as typeof sessionApi.submitChat;
  sessionApi.getStatus = (async () => {
    statusCalls += 1;
    return {
      session_id: "session-initial-post-refresh",
      run_id: "run-initial-post-refresh",
      status: "error",
      raw_status: "failed",
    };
  }) as typeof sessionApi.getStatus;
  sessionApi.getEvents = (async (_sessionId, options) => ({
    events: options?.run_id
      ? [{
          id: "run-initial-post-refresh:final",
          event_type: "final_detail",
          run_id: "run-initial-post-refresh",
          timestamp: "2026-07-15T00:00:01Z",
          data: {
            run_id: "run-initial-post-refresh",
            detail_kind: "failed",
            detail_code: "run_failed",
          },
        }]
      : [],
  })) as typeof sessionApi.getEvents;

  try {
    await harness.act(async () => {
      await harness.hook.sendMessage("刷新后网络中断需要核对状态");
    });
    await settle(harness.act);

    const parts = harness.hook.messages.flatMap((message) => message.parts || []);
    assert.equal(refreshProbeCalls, 0);
    assert.equal(streamCalls, 1);
    assert.equal(statusCalls, 0);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(
      parts.filter(
        (part) =>
          part.type === "run_status" &&
          part.event_id ===
            "terminal-status-unavailable:run-initial-post-refresh",
      ).length,
      1,
    );
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.markRead = originalMarkRead;
    sessionApi.generateTitle = originalGenerateTitle;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.getEvents = originalGetEvents;
    dom.window.fetch = originalFetch;
    globalThis.fetch = originalGlobalFetch;
    if (originalSessionMarker === null) {
      dom.window.localStorage.removeItem("ai_platform_session_present");
    } else {
      dom.window.localStorage.setItem(
        "ai_platform_session_present",
        originalSessionMarker,
      );
    }
    await harness.cleanup();
  }
});

test("useAgent history restore fails closed for non-retryable SSE authentication", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const originalFetch = dom.window.fetch;
  const originalSessionMarker = dom.window.localStorage.getItem(
    "ai_platform_session_present",
  );
  let statusCalls = 0;
  let streamCalls = 0;
  dom.window.localStorage.removeItem("ai_platform_session_present");
  dom.window.fetch = async () => {
    streamCalls += 1;
    return new Response(null, { status: 401 });
  };
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-history-auth",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async () => ({
    current_run_id: "run-history-auth",
    events: [
      {
        id: "evt-history-auth-user",
        run_id: "run-history-auth",
        event_type: "user:message",
        timestamp: "2026-07-15T00:00:00Z",
        data: { content: "恢复认证失效任务" },
      },
    ],
  });
  sessionApi.getStatus = (async () => {
    statusCalls += 1;
    return {
      session_id: "session-history-auth",
      run_id: "run-history-auth",
      status: "running",
    };
  }) as typeof sessionApi.getStatus;

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-history-auth");
    });
    await settle(harness.act);

    const parts = harness.hook.messages.flatMap((message) => message.parts || []);
    // The one status read establishes that the restored run was active. The
    // typed authentication result must not trigger a second reconciliation.
    assert.equal(statusCalls, 1);
    assert.equal(streamCalls, 1);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(harness.hook.connectionStatus, "disconnected");
    assert.equal(
      parts.filter(
        (part) =>
          part.type === "run_status" &&
          part.event_id === "terminal-status-unavailable:run-history-auth",
      ).length,
      1,
    );
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    dom.window.fetch = originalFetch;
    if (originalSessionMarker === null) {
      dom.window.localStorage.removeItem("ai_platform_session_present");
    } else {
      dom.window.localStorage.setItem(
        "ai_platform_session_present",
        originalSessionMarker,
      );
    }
    await harness.cleanup();
  }
});

test("useAgent releases the active state for succeeded and cancelled terminals", async () => {
  for (const terminal of [
    { eventType: "run_succeeded", runId: "run-success", cancelled: false },
    { eventType: "run_cancelled", runId: "run-cancelled", cancelled: true },
  ]) {
    const harness = await loadReactHarness();
    const { sessionApi } = await import("../../../services/api/session.ts");
    const originalSubmitChat = sessionApi.submitChat;
    const originalMarkRead = sessionApi.markRead;
    const originalGenerateTitle = sessionApi.generateTitle;
    const originalFetch = dom.window.fetch;
    dom.window.fetch = async () =>
      sseEventResponse("run_event", {
        run_id: terminal.runId,
        event_type: terminal.eventType,
      });
    sessionApi.markRead = async () => {};
    sessionApi.generateTitle = async () => ({
      title: "终态会话",
      session_id: `session-${terminal.runId}`,
    });
    sessionApi.submitChat = (async () => ({
      session_id: `session-${terminal.runId}`,
      run_id: terminal.runId,
      trace_id: `trace-${terminal.runId}`,
      status: "queued",
    })) as typeof sessionApi.submitChat;

    try {
      await harness.act(async () => {
        await harness.hook.sendMessage("执行终态任务");
      });
      await settle(harness.act);

      const parts = harness.hook.messages.flatMap((message) => message.parts || []);
      assert.equal(harness.hook.currentRunId, null, terminal.eventType);
      assert.equal(harness.hook.isLoading, false, terminal.eventType);
      assert.equal(harness.hook.connectionStatus, "disconnected", terminal.eventType);
      assert.equal(
        parts.some(
          (part) =>
            part.type === "run_status" &&
            part.event_id === `terminal-failure:${terminal.runId}`,
        ),
        false,
        terminal.eventType,
      );
      assert.equal(
        parts.some((part) => part.type === "cancelled"),
        terminal.cancelled,
        terminal.eventType,
      );
    } finally {
      sessionApi.submitChat = originalSubmitChat;
      sessionApi.markRead = originalMarkRead;
      sessionApi.generateTitle = originalGenerateTitle;
      dom.window.fetch = originalFetch;
      await harness.cleanup();
    }
  }
});

test("useAgent uses the backend current run subject before normalizing a failed reload card", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const originalFetch = dom.window.fetch;
  let sseCalls = 0;
  const statusCalls: Array<[string, string | undefined]> = [];
  dom.window.fetch = async () => {
    sseCalls += 1;
    return completedSseResponse();
  };
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-history-failed",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async (_sessionId, options) => ({
    current_run_id: "run-history-failed",
    events: options?.run_id
      ? [{
          id: "run-history-failed:final",
          event_type: "final_detail",
          run_id: "run-history-failed",
          timestamp: "2026-07-15T00:00:02Z",
          data: {
            run_id: "run-history-failed",
            detail_kind: "failed",
            detail_code: "run_failed",
          },
        }]
      : [
          {
            id: "evt-history-user",
            run_id: "run-history-failed",
            event_type: "user:message",
            timestamp: "2026-07-15T00:00:00Z",
            data: { content: "执行任务" },
          },
          {
            id: "evt-history-progress",
            sequence: 4,
            run_id: "run-history-failed",
            event_type: "worker_started",
            timestamp: "2026-07-15T00:00:01Z",
            data: {
              event_id: "evt-history-progress",
              run_id: "run-history-failed",
              event_type: "worker_started",
              stage: "worker",
              content: "处理中",
              severity: "info",
            },
          },
        ],
  });
  sessionApi.getStatus = (async (sessionId, runId) => {
    statusCalls.push([sessionId, runId]);
    return {
      session_id: "session-history-failed",
      run_id: "run-history-failed",
      status: "error",
      raw_status: "failed",
    };
  }) as typeof sessionApi.getStatus;

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-history-failed");
    });
    await settle(harness.act);

    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(harness.hook.connectionStatus, "disconnected");
    assert.equal(sseCalls, 0);
    assert.deepEqual(statusCalls, [["session-history-failed", "run-history-failed"]]);
    const runStatusParts = harness.hook.messages.flatMap(
      (message) => message.parts || [],
    );
    assert.equal(
      runStatusParts.filter(
        (part) =>
          part.type === "run_status" &&
          part.event_id === "terminal-failure:run-history-failed",
      ).length,
      1,
    );
    assert.equal(
      runStatusParts.some(
        (part) => part.type === "run_status" && part.message === "Executor failed",
      ),
      false,
    );
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent reconnects only the backend-selected active run after reload", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const originalFetch = dom.window.fetch;
  const statusCalls: Array<[string, string | undefined]> = [];
  const streamUrls: string[] = [];
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-history-active",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async () => ({
    current_run_id: "run-history-active",
    events: [
      {
        id: "evt-history-active",
        run_id: "run-history-active",
        event_type: "user:message",
        timestamp: "2026-07-15T00:00:00Z",
        data: { content: "恢复执行" },
      },
    ],
  });
  sessionApi.getStatus = (async (sessionId, runId) => {
    statusCalls.push([sessionId, runId]);
    return {
      session_id: "session-history-active",
      run_id: "run-history-active",
      status: "running",
    };
  }) as typeof sessionApi.getStatus;
  dom.window.fetch = async (input) => {
    streamUrls.push(String(input));
    return sseEventResponse("run_event", {
      run_id: "run-history-active",
      event_type: "run_succeeded",
    });
  };

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-history-active");
    });
    await settle(harness.act);

    assert.deepEqual(statusCalls, [["session-history-active", "run-history-active"]]);
    assert.equal(streamUrls.length, 1);
    assert.match(streamUrls[0] || "", /run_id=run-history-active/);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent reconciles a reload SSE interruption to its failed run status", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const originalFetch = dom.window.fetch;
  let statusCalls = 0;
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-history-interrupted",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async (_sessionId, options) => ({
    current_run_id: "run-history-interrupted",
    events: options?.run_id
      ? [{
          id: "run-history-interrupted:final",
          event_type: "final_detail",
          run_id: "run-history-interrupted",
          timestamp: "2026-07-15T00:00:02Z",
          data: {
            run_id: "run-history-interrupted",
            detail_kind: "failed",
            detail_code: "run_failed",
          },
        }]
      : [{
          id: "evt-history-interrupted",
          run_id: "run-history-interrupted",
          event_type: "user:message",
          timestamp: "2026-07-15T00:00:00Z",
          data: { content: "恢复后中断" },
        }],
  });
  sessionApi.getStatus = (async () => {
    statusCalls += 1;
    return {
      session_id: "session-history-interrupted",
      run_id: "run-history-interrupted",
      status: statusCalls === 1 ? "running" : "error",
      raw_status: statusCalls === 1 ? "running" : "failed",
    };
  }) as typeof sessionApi.getStatus;
  dom.window.fetch = async () =>
    new Response("", { headers: { "content-type": "text/event-stream" } });

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-history-interrupted");
    });
    await settle(harness.act);

    const parts = harness.hook.messages.flatMap((message) => message.parts || []);
    assert.equal(statusCalls, 2);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(harness.hook.connectionStatus, "disconnected");
    assert.equal(
      parts.filter(
        (part) =>
          part.type === "run_status" &&
          part.event_id === "terminal-failure:run-history-interrupted",
      ).length,
      1,
    );
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent hydrates the exact terminal run compatibility history before converging", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const eventQueries: Array<string | undefined> = [];
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-terminal-hydrate",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = (async (_sessionId, options) => {
    eventQueries.push(options?.run_id);
    if (!options?.run_id) {
      return {
        current_run_id: "run-terminal-hydrate",
        events: [{
          id: "terminal-hydrate:user",
          event_type: "user:message",
          run_id: "run-terminal-hydrate",
          timestamp: "2026-07-15T00:00:00Z",
          data: { content: "恢复终态" },
        }],
      };
    }
    return {
      current_run_id: "run-terminal-hydrate",
      events: [
        {
          id: "terminal-hydrate:user",
          event_type: "user:message",
          run_id: "run-terminal-hydrate",
          timestamp: "2026-07-15T00:00:00Z",
          data: {
            message_id: "terminal-hydrate:user",
            run_id: "run-terminal-hydrate",
            content: "恢复终态",
          },
        },
        {
          id: "terminal-hydrate:progress",
          sequence: 9,
          event_type: "worker_started",
          run_id: "run-terminal-hydrate",
          timestamp: "2026-07-15T00:00:01Z",
          data: {
            event_id: "terminal-hydrate:progress",
            run_id: "run-terminal-hydrate",
            event_type: "worker_started",
            stage: "worker",
            content: "处理完成",
          },
        },
        {
          id: "terminal-hydrate:artifact",
          event_type: "artifact_card",
          run_id: "run-terminal-hydrate",
          timestamp: "2026-07-15T00:00:02Z",
          data: {
            run_id: "run-terminal-hydrate",
            artifact_id: "artifact-terminal-hydrate",
            artifact_type: "report",
            label: "结果报告",
            download_url: "/api/ai/artifacts/artifact-terminal-hydrate/download",
          },
        },
        {
          id: "terminal-hydrate:final",
          event_type: "message:chunk",
          run_id: "run-terminal-hydrate",
          timestamp: "2026-07-15T00:00:03Z",
          data: { run_id: "run-terminal-hydrate", content: "最终答案" },
        },
        {
          id: "run-terminal-hydrate:terminal:succeeded",
          event_type: "done",
          run_id: "run-terminal-hydrate",
          timestamp: "2026-07-15T00:00:04Z",
          data: { run_id: "run-terminal-hydrate", status: "succeeded" },
        },
      ],
    };
  }) as typeof sessionApi.getEvents;
  sessionApi.getStatus = (async () => ({
    session_id: "session-terminal-hydrate",
    run_id: "run-terminal-hydrate",
    status: "error",
    raw_status: "succeeded",
  })) as typeof sessionApi.getStatus;

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-terminal-hydrate");
    });
    await settle(harness.act);

    const assistant = harness.hook.messages.find(
      (message) => message.runId === "run-terminal-hydrate" && message.role === "assistant",
    );
    const user = harness.hook.messages.find(
      (message) => message.runId === "run-terminal-hydrate" && message.role === "user",
    );
    assert.deepEqual(eventQueries, [undefined, "run-terminal-hydrate"]);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(assistant?.content, "最终答案");
    assert.equal(user?.content, "恢复终态");
    assert.equal(
      assistant?.parts?.some(
        (part) =>
          part.type === "artifact" &&
          part.artifact_id === "artifact-terminal-hydrate",
      ),
      true,
    );
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    await harness.cleanup();
  }
});

test("useAgent loads an exact old run as one complete deduplicated segment from the first request", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const eventQueries: Array<string | undefined> = [];
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-exact-old",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-07-15T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = (async (_sessionId, options) => {
    eventQueries.push(options?.run_id);
    if (options?.run_id !== "run-51") {
      return {
        current_run_id: "run-latest",
        events: [{
          id: "run-latest:final",
          event_type: "message:chunk",
          run_id: "run-latest",
          timestamp: "2026-07-15T10:00:00Z",
          data: { run_id: "run-latest", content: "最近五十条中的回答" },
        }],
      };
    }
    return {
      run_id: "run-51",
      current_run_id: "run-51",
      events: [
        {
          id: "message-run-51",
          event_type: "user:message",
          run_id: "run-51",
          timestamp: "2026-01-01T00:00:00Z",
          data: {
            message_id: "message-run-51",
            run_id: "run-51",
            content: "第 51 条旧问题",
          },
        },
        {
          id: "run-51:artifact",
          event_type: "artifact_card",
          run_id: "run-51",
          timestamp: "2026-01-01T00:00:01Z",
          data: {
            run_id: "run-51",
            artifact_id: "artifact-run-51",
            artifact_type: "report",
            label: "旧运行报告",
            download_url: "/api/ai/artifacts/artifact-run-51/download",
          },
        },
        {
          id: "run-51:final",
          event_type: "message:chunk",
          run_id: "run-51",
          timestamp: "2026-01-01T00:00:02Z",
          data: { run_id: "run-51", content: "第 51 条旧回答" },
        },
        {
          id: "run-51:terminal:succeeded",
          event_type: "done",
          run_id: "run-51",
          timestamp: "2026-01-01T00:00:03Z",
          data: { run_id: "run-51", status: "succeeded" },
        },
      ],
    };
  }) as typeof sessionApi.getEvents;
  sessionApi.getStatus = (async (_sessionId, runId) => ({
    session_id: "session-exact-old",
    run_id: runId,
    status: "error",
    raw_status: "succeeded",
  })) as typeof sessionApi.getStatus;

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-exact-old", "run-51");
    });
    await settle(harness.act);
    await harness.act(async () => {
      await harness.hook.loadHistory("session-exact-old", "run-51");
    });
    await settle(harness.act);

    assert.deepEqual(eventQueries, ["run-51", "run-51"]);
    assert.deepEqual(
      harness.hook.messages.map((message) => [
        message.role,
        message.runId,
        message.content,
      ]),
      [
        ["user", "run-51", "第 51 条旧问题"],
        ["assistant", "run-51", "第 51 条旧回答"],
      ],
    );
    assert.equal(
      harness.hook.messages[1]?.parts?.some(
        (part) =>
          part.type === "artifact" &&
          part.artifact_id === "artifact-run-51",
      ),
      true,
    );
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    await harness.cleanup();
  }
});

test("useAgent renders a payload-free exact cancelled run as a complete user and assistant segment", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-exact-cancelled",
    agent_id: "general-agent",
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T00:00:01Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = (async (_sessionId, options) => ({
    run_id: options?.run_id,
    current_run_id: "run-exact-cancelled",
    events: [{
      id: "message-exact-cancelled",
      event_type: "user:message",
      run_id: "run-exact-cancelled",
      timestamp: "2026-07-15T00:00:00Z",
      data: {
        message_id: "message-exact-cancelled",
        run_id: "run-exact-cancelled",
        content: "取消这个任务",
      },
    }, {
      id: "run-exact-cancelled:terminal:cancelled",
      event_type: "done",
      run_id: "run-exact-cancelled",
      timestamp: "2026-07-15T00:00:01Z",
      data: { run_id: "run-exact-cancelled", status: "cancelled" },
    }],
  })) as typeof sessionApi.getEvents;
  sessionApi.getStatus = (async (_sessionId, runId) => ({
    session_id: "session-exact-cancelled",
    run_id: runId,
    status: "error",
    raw_status: "cancelled",
  })) as typeof sessionApi.getStatus;

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory(
        "session-exact-cancelled",
        "run-exact-cancelled",
      );
    });
    await settle(harness.act);

    assert.deepEqual(
      harness.hook.messages.map((message) => [
        message.role,
        message.runId,
        message.content,
      ]),
      [
        ["user", "run-exact-cancelled", "取消这个任务"],
        ["assistant", "run-exact-cancelled", ""],
      ],
    );
    assert.equal(
      harness.hook.messages[1]?.parts?.filter(
        (part) => part.type === "cancelled",
      ).length,
      1,
    );
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    await harness.cleanup();
  }
});

test("useAgent keeps overlapping run segments separate and trusts latest-created current_run_id", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const statusRunIds: Array<string | undefined> = [];
  const eventQueries: Array<string | undefined> = [];
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-overlapping-runs",
    agent_id: "general-agent",
    created_at: "2026-07-15T00:00:00Z",
    updated_at: "2026-07-15T03:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = (async (_sessionId, options) => {
    eventQueries.push(options?.run_id);
    if (options?.run_id) {
      return {
        current_run_id: "run-created-newer",
        events: [
          {
            id: "newer:final",
            event_type: "message:chunk",
            run_id: "run-created-newer",
            timestamp: "2026-07-15T02:00:00Z",
            data: { run_id: "run-created-newer", content: "新运行最终答案" },
          },
          {
            id: "newer:terminal",
            event_type: "done",
            run_id: "run-created-newer",
            timestamp: "2026-07-15T02:01:00Z",
            data: { run_id: "run-created-newer", status: "succeeded" },
          },
        ],
      };
    }
    return {
      current_run_id: "run-created-newer",
      events: [
        {
          id: "older:final",
          event_type: "message:chunk",
          run_id: "run-created-older",
          timestamp: "2026-07-15T03:00:00Z",
          data: { run_id: "run-created-older", content: "旧运行稍后结束" },
        },
        {
          id: "older:terminal",
          event_type: "done",
          run_id: "run-created-older",
          timestamp: "2026-07-15T03:01:00Z",
          data: { run_id: "run-created-older", status: "failed" },
        },
        {
          id: "newer:initial-final",
          event_type: "message:chunk",
          run_id: "run-created-newer",
          timestamp: "2026-07-15T02:00:00Z",
          data: { run_id: "run-created-newer", content: "新运行最终答案" },
        },
        {
          id: "newer:initial-terminal",
          event_type: "done",
          run_id: "run-created-newer",
          timestamp: "2026-07-15T02:01:00Z",
          data: { run_id: "run-created-newer", status: "succeeded" },
        },
      ],
    };
  }) as typeof sessionApi.getEvents;
  sessionApi.getStatus = (async (_sessionId, runId) => {
    statusRunIds.push(runId);
    return {
      session_id: "session-overlapping-runs",
      run_id: runId,
      status: "completed",
      raw_status: "succeeded",
    };
  }) as typeof sessionApi.getStatus;

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-overlapping-runs");
    });
    await settle(harness.act);

    const assistants = harness.hook.messages.filter(
      (message) => message.role === "assistant",
    );
    assert.deepEqual(statusRunIds, ["run-created-newer"]);
    assert.deepEqual(eventQueries, [undefined, "run-created-newer"]);
    assert.deepEqual(
      assistants.map((message) => message.runId),
      ["run-created-older", "run-created-newer"],
    );
    assert.deepEqual(
      assistants.map((message) => message.content),
      ["旧运行稍后结束", "新运行最终答案"],
    );
    assert.equal(
      assistants.filter((message) => message.runId === "run-created-newer").length,
      1,
    );
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    await harness.cleanup();
  }
});

test("useAgent presents a safe local card when terminal history hydration fails", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  let eventQueries = 0;
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-terminal-hydrate-failure",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = (async () => {
    eventQueries += 1;
    if (eventQueries === 1) {
      return {
        current_run_id: "run-terminal-hydrate-failure",
        events: [{
          id: "terminal-hydrate-failure:user",
          event_type: "user:message",
          run_id: "run-terminal-hydrate-failure",
          timestamp: "2026-07-15T00:00:00Z",
          data: { content: "恢复失败终态" },
        }],
      };
    }
    throw new Error("history unavailable");
  }) as typeof sessionApi.getEvents;
  sessionApi.getStatus = (async () => ({
    session_id: "session-terminal-hydrate-failure",
    run_id: "run-terminal-hydrate-failure",
    status: "failed",
  })) as typeof sessionApi.getStatus;

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-terminal-hydrate-failure");
    });
    await settle(harness.act);

    const cards = harness.hook.messages
      .flatMap((message) => message.parts || [])
      .filter(
        (part) =>
          part.type === "run_status" &&
          part.event_id ===
            "terminal-result-unavailable:run-terminal-hydrate-failure",
      );
    assert.equal(eventQueries, 2);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(cards.length, 1);
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    await harness.cleanup();
  }
});

test("useAgent fails closed after initial reload status retries are exhausted", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const originalFetch = dom.window.fetch;
  let statusCalls = 0;
  let sseCalls = 0;
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-initial-status-unavailable",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async () => ({
    current_run_id: "run-initial-status-unavailable",
    events: [
      {
        id: "evt-initial-status-unavailable",
        run_id: "run-initial-status-unavailable",
        event_type: "user:message",
        timestamp: "2026-07-15T00:00:00Z",
        data: { content: "恢复状态" },
      },
    ],
  });
  sessionApi.getStatus = (async () => {
    statusCalls += 1;
    return {
      session_id: "session-initial-status-unavailable",
      run_id: "run-initial-status-unavailable",
      status: "error",
    };
  }) as typeof sessionApi.getStatus;
  dom.window.fetch = async () => {
    sseCalls += 1;
    return completedSseResponse();
  };

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-initial-status-unavailable");
    });
    await settle(harness.act);

    const parts = harness.hook.messages.flatMap((message) => message.parts || []);
    assert.equal(statusCalls, 3);
    assert.equal(sseCalls, 0);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(harness.hook.connectionStatus, "disconnected");
    assert.equal(
      parts.filter(
        (part) =>
          part.type === "run_status" &&
          part.event_id ===
            "terminal-status-unavailable:run-initial-status-unavailable",
      ).length,
      1,
    );
    assert.equal(
      parts.some(
        (part) =>
          part.type === "run_status" &&
          part.event_id === "terminal-failure:run-initial-status-unavailable",
      ),
      false,
    );
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent immediately reconciles a non-terminal application error without server close", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalGetStatus = sessionApi.getStatus;
  const originalGetEvents = sessionApi.getEvents;
  const originalMarkRead = sessionApi.markRead;
  const originalGenerateTitle = sessionApi.generateTitle;
  const originalFetch = dom.window.fetch;
  let sseCalls = 0;
  let statusCalls = 0;
  dom.window.fetch = async () => {
    sseCalls += 1;
    return nonClosingSseEventResponse("error", { error: "stream_timeout" });
  };
  sessionApi.markRead = async () => {};
  sessionApi.generateTitle = async () => ({
    title: "中断会话",
    session_id: "session-nonterminal-error",
  });
  sessionApi.submitChat = (async () => ({
    session_id: "session-nonterminal-error",
    run_id: "run-nonterminal-error",
    trace_id: "trace-nonterminal-error",
    status: "queued",
  })) as typeof sessionApi.submitChat;
  sessionApi.getStatus = (async () => {
    statusCalls += 1;
    return {
      session_id: "session-nonterminal-error",
      run_id: "run-nonterminal-error",
      status: "error",
      raw_status: "failed",
    };
  }) as typeof sessionApi.getStatus;
  sessionApi.getEvents = (async (_sessionId, options) => ({
    events: options?.run_id
      ? [{
          id: "run-nonterminal-error:final",
          event_type: "final_detail",
          run_id: "run-nonterminal-error",
          timestamp: "2026-07-15T00:00:01Z",
          data: {
            run_id: "run-nonterminal-error",
            detail_kind: "failed",
            detail_code: "run_failed",
          },
        }]
      : [],
  })) as typeof sessionApi.getEvents;

  try {
    await harness.act(async () => {
      await harness.hook.sendMessage("流中断后查询状态");
    });
    await settle(harness.act);

    const cards = harness.hook.messages
      .flatMap((message) => message.parts || [])
      .filter(
        (part) =>
          part.type === "run_status" &&
          part.event_id === "terminal-failure:run-nonterminal-error",
      );
    assert.equal(sseCalls, 1);
    assert.equal(statusCalls, 1);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(harness.hook.connectionStatus, "disconnected");
    assert.equal(cards.length, 1);
    assert.equal(
      cards[0]?.type === "run_status" && cards[0].message,
      "任务未能完成。请稍后重试；如问题持续，请联系管理员。",
    );
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.markRead = originalMarkRead;
    sessionApi.generateTitle = originalGenerateTitle;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent drops an application-error status continuation after clear", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const originalGenerateTitle = sessionApi.generateTitle;
  const originalFetch = dom.window.fetch;
  let resolveStatus!: (value: Awaited<ReturnType<typeof sessionApi.getStatus>>) => void;
  let statusCalls = 0;
  dom.window.fetch = async () =>
    sseEventResponse("error", { error: "stream_timeout" });
  sessionApi.markRead = async () => {};
  sessionApi.generateTitle = async () => ({
    title: "待清空会话",
    session_id: "session-error-clear",
  });
  sessionApi.submitChat = (async () => ({
    session_id: "session-error-clear",
    run_id: "run-error-clear",
    trace_id: "trace-error-clear",
    status: "queued",
  })) as typeof sessionApi.submitChat;
  sessionApi.getStatus = (async () => {
    statusCalls += 1;
    return new Promise((resolve) => {
      resolveStatus = resolve;
    });
  }) as typeof sessionApi.getStatus;

  try {
    await harness.act(async () => {
      await harness.hook.sendMessage("错误帧后清空");
    });
    await settle(harness.act);
    assert.equal(statusCalls, 1);

    await harness.act(async () => {
      harness.hook.clearMessages();
    });
    resolveStatus({
      session_id: "session-error-clear",
      run_id: "run-error-clear",
      status: "error",
      raw_status: "failed",
    });
    await settle(harness.act);

    assert.equal(harness.hook.sessionId, null);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.isLoading, false);
    assert.equal(harness.hook.connectionStatus, "disconnected");
    assert.equal(harness.hook.messages.length, 0);
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    sessionApi.generateTitle = originalGenerateTitle;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent ignores pending submit continuations after unmount", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalFetch = dom.window.fetch;
  let resolveSubmit!: (value: ChatStreamResponse) => void;
  let sseCalls = 0;
  sessionApi.submitChat = (() =>
    new Promise<ChatStreamResponse>((resolve) => {
      resolveSubmit = resolve;
    })) as typeof sessionApi.submitChat;
  dom.window.fetch = async () => {
    sseCalls += 1;
    return completedSseResponse();
  };

  try {
    let submission: Promise<unknown> | null = null;
    await harness.act(async () => {
      submission = harness.hook.sendMessage("卸载中的请求");
      await Promise.resolve();
    });
    await harness.unmount();
    resolveSubmit({
      session_id: "session-unmounted-submit",
      run_id: "run-unmounted-submit",
      trace_id: "trace-unmounted-submit",
      status: "queued",
    });
    await submission;
    assert.equal(sseCalls, 0);
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent ignores pending history continuations after unmount", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  let resolveSession!: (value: Awaited<ReturnType<typeof sessionApi.get>>) => void;
  let eventCalls = 0;
  sessionApi.get = () =>
    new Promise((resolve) => {
      resolveSession = resolve;
    });
  sessionApi.getEvents = async () => {
    eventCalls += 1;
    return { events: [] };
  };

  try {
    let history: Promise<unknown> | null = null;
    await harness.act(async () => {
      history = harness.hook.loadHistory("session-unmounted-history");
      await Promise.resolve();
    });
    await harness.unmount();
    resolveSession({
      id: "session-unmounted-history",
      agent_id: "general-agent",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      is_active: true,
      metadata: {},
    });
    await history;
    assert.equal(eventCalls, 0);
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    await harness.cleanup();
  }
});

test("useAgent drops title events that resolve after unmount", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const { SESSION_TITLE_UPDATED_EVENT } = await import("../../../utils/sessionTitleEvents.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalGenerateTitle = sessionApi.generateTitle;
  const originalMarkRead = sessionApi.markRead;
  const originalFetch = dom.window.fetch;
  let resolveTitle!: (value: { title: string; session_id: string }) => void;
  let titleEvents = 0;
  const onTitle = () => {
    titleEvents += 1;
  };
  dom.window.addEventListener(SESSION_TITLE_UPDATED_EVENT, onTitle);
  dom.window.fetch = async () => completedSseResponse();
  sessionApi.markRead = async () => {};
  sessionApi.submitChat = (async () => ({
    session_id: "session-unmounted-title",
    run_id: "run-unmounted-title",
    trace_id: "trace-unmounted-title",
    status: "queued",
  })) as typeof sessionApi.submitChat;
  sessionApi.generateTitle = (() =>
    new Promise((resolve) => {
      resolveTitle = resolve;
    })) as typeof sessionApi.generateTitle;

  try {
    await harness.act(async () => {
      await harness.hook.sendMessage("卸载中的标题");
    });
    await settle(harness.act);
    await harness.unmount();
    resolveTitle({ title: "不应发布的标题", session_id: "session-unmounted-title" });
    await Promise.resolve();
    assert.equal(titleEvents, 0);
  } finally {
    dom.window.removeEventListener(SESSION_TITLE_UPDATED_EVENT, onTitle);
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.generateTitle = originalGenerateTitle;
    sessionApi.markRead = originalMarkRead;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent remains live after the StrictMode cleanup/remount cycle", async () => {
  const harness = await loadReactHarness({ strict: true });
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalMarkRead = sessionApi.markRead;
  const originalGenerateTitle = sessionApi.generateTitle;
  const originalFetch = dom.window.fetch;
  dom.window.fetch = async () => completedSseResponse();
  sessionApi.markRead = async () => {};
  sessionApi.generateTitle = async () => ({
    title: "重新挂载会话",
    session_id: "session-strict-mode",
  });
  sessionApi.submitChat = (async () => ({
    session_id: "session-strict-mode",
    run_id: "run-strict-mode",
    trace_id: "trace-strict-mode",
    status: "queued",
  })) as typeof sessionApi.submitChat;

  try {
    await settle(harness.act);
    await harness.act(async () => {
      await harness.hook.sendMessage("重新挂载后仍可提交");
    });
    await settle(harness.act);
    assert.equal(harness.hook.sessionId, "session-strict-mode");
    assert.equal(harness.hook.isLoading, false);
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.markRead = originalMarkRead;
    sessionApi.generateTitle = originalGenerateTitle;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent clears a pending reconnect timer when switching sessions", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const originalGenerateTitle = sessionApi.generateTitle;
  const originalFetch = dom.window.fetch;
  const originalRandom = Math.random;
  let sseCalls = 0;
  Math.random = () => 0;
  sessionApi.markRead = async () => {};
  sessionApi.generateTitle = async () => ({
    title: "待切换会话",
    session_id: "session-reconnect-old",
  });
  sessionApi.submitChat = (async () => ({
    session_id: "session-reconnect-old",
    run_id: "run-reconnect-old",
    trace_id: "trace-reconnect-old",
    status: "queued",
  })) as typeof sessionApi.submitChat;
  sessionApi.getStatus = (async () => ({
    session_id: "session-reconnect-old",
    run_id: "run-reconnect-old",
    status: "running",
  })) as typeof sessionApi.getStatus;
  sessionApi.get = async () => ({
    id: "session-reconnect-new",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async () => ({ events: [] });
  dom.window.fetch = async () => {
    sseCalls += 1;
    return new Response("", { headers: { "content-type": "text/event-stream" } });
  };

  try {
    await harness.act(async () => {
      await harness.hook.sendMessage("建立将被切换的连接");
    });
    await harness.act(async () => {
      await harness.hook.loadHistory("session-reconnect-new");
    });
    await new Promise((resolve) => setTimeout(resolve, 1100));
    await settle(harness.act);

    assert.equal(harness.hook.sessionId, "session-reconnect-new");
    assert.equal(sseCalls, 1);
  } finally {
    Math.random = originalRandom;
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    sessionApi.generateTitle = originalGenerateTitle;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent preserves replay-safe reconnect budget from production-shaped history across a same-session reload", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const originalFetch = dom.window.fetch;
  const originalRandom = Math.random;
  let sseCalls = 0;
  Math.random = () => 0;
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-reconnect-budget",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async () => ({
    current_run_id: "run-reconnect-budget",
    events: [
      {
        id: "evt-reconnect-budget",
        run_id: "run-reconnect-budget",
        sequence: 9,
        event_type: "worker_started",
        timestamp: "2026-07-15T00:00:00Z",
        data: {
          event_id: "evt-reconnect-budget",
          run_id: "run-reconnect-budget",
          event_type: "worker_started",
        },
      },
    ],
  });
  sessionApi.getStatus = (async () => ({
    session_id: "session-reconnect-budget",
    run_id: "run-reconnect-budget",
    status: "running",
  })) as typeof sessionApi.getStatus;
  dom.window.fetch = async () => {
    sseCalls += 1;
    return sseEventResponse("run_event", {
      event_id: "evt-reconnect-budget",
      run_id: "run-reconnect-budget",
      sequence: 9,
      event_type: "worker_started",
    });
  };

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-reconnect-budget");
    });
    await settle(harness.act);
    await harness.act(async () => {
      await harness.hook.loadHistory("session-reconnect-budget");
    });
    await settle(harness.act);

    // The second interruption is the same run, so it must retain retry #1
    // and schedule retry #2 (two seconds), not restart at one second.
    await new Promise((resolve) => setTimeout(resolve, 1100));
    await settle(harness.act);
    assert.equal(sseCalls, 2);
  } finally {
    Math.random = originalRandom;
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent adopts a current retry child through one parent history load", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const originalRetryRun = sessionApi.retryRun;
  const originalFetch = globalThis.fetch;
  let childLoads = 0;
  let retries = 0;
  sessionApi.markRead = async () => {};
  sessionApi.get = async (sessionId) => {
    if (sessionId === "session-retry-child") childLoads += 1;
    return {
      id: sessionId,
      agent_id: "general-agent",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      is_active: true,
      metadata: {},
    };
  };
  sessionApi.getEvents = async (sessionId) => {
    const runId = sessionId === "session-retry-child" ? "run-retry-child" : "run-parent";
    return {
      current_run_id: runId,
      events: [
        {
          id: `evt-${runId}`,
          run_id: runId,
          event_type: "user:message",
          timestamp: "2026-07-19T00:00:00Z",
          data: { content: runId },
        },
      ],
    };
  };
  sessionApi.getStatus = (async (sessionId, runId) => ({
    session_id: sessionId,
    run_id: runId,
    status: "failed",
    raw_status: "failed",
  })) as typeof sessionApi.getStatus;
  sessionApi.retryRun = (async () => {
    retries += 1;
    return {
      session_id: "session-retry-child",
      run_id: "run-retry-child",
      status: "queued",
    };
  }) as typeof sessionApi.retryRun;
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify({
        run_id: "run-retry-child",
        timeline: [],
        events: [],
        artifacts: [],
        steps: [],
        multi_agent: null,
      }),
    )) as typeof fetch;

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-parent", "run-parent");
    });
    await harness.act(async () => {
      await harness.hook.runControlLifecycle.retry();
    });
    await settle(harness.act);

    assert.equal(retries, 1);
    assert.equal(childLoads, 1, "the parent must call loadHistory for the child once");
    assert.equal(harness.hook.sessionId, "session-retry-child");
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    sessionApi.retryRun = originalRetryRun;
    globalThis.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent drops a delayed A retry before it can load A-child after B replaces history", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const originalRetryRun = sessionApi.retryRun;
  let resolveRetry!: (value: Awaited<ReturnType<typeof sessionApi.retryRun>>) => void;
  const pendingRetry = new Promise<Awaited<ReturnType<typeof sessionApi.retryRun>>>(
    (resolve) => {
      resolveRetry = resolve;
    },
  );
  let staleChildLoads = 0;
  sessionApi.markRead = async () => {};
  sessionApi.get = async (sessionId) => {
    if (sessionId === "session-a-child") staleChildLoads += 1;
    return {
      id: sessionId,
      agent_id: "general-agent",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      is_active: true,
      metadata: {},
    };
  };
  sessionApi.getEvents = async (sessionId) => {
    const runId = sessionId === "session-b" ? "run-b" : "run-a";
    return {
      current_run_id: runId,
      events: [
        {
          id: `evt-${runId}`,
          run_id: runId,
          event_type: "user:message",
          timestamp: "2026-07-19T00:00:00Z",
          data: { content: runId },
        },
      ],
    };
  };
  sessionApi.getStatus = (async (sessionId, runId) => ({
    session_id: sessionId,
    run_id: runId,
    status: "failed",
    raw_status: "failed",
  })) as typeof sessionApi.getStatus;
  sessionApi.retryRun = (() => pendingRetry) as typeof sessionApi.retryRun;

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-a", "run-a");
    });
    let action!: Promise<void>;
    await harness.act(async () => {
      action = harness.hook.runControlLifecycle.retry();
      await Promise.resolve();
    });
    await harness.act(async () => {
      await harness.hook.loadHistory("session-b", "run-b");
    });
    resolveRetry({
      session_id: "session-a-child",
      run_id: "run-a-child",
      status: "queued",
    });
    await action;
    await settle(harness.act);

    assert.equal(staleChildLoads, 0);
    assert.equal(harness.hook.sessionId, "session-b");
    assert.equal(harness.hook.messages[0]?.runId, "run-b");
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    sessionApi.retryRun = originalRetryRun;
    await harness.cleanup();
  }
});

test("useAgent aborts a deferred run-control GET before unmount can publish it", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const originalFetch = globalThis.fetch;
  let resolvePlayback!: (value: Response) => void;
  let playbackSignal: AbortSignal | null = null;
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-unmount-control",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async () => ({
    current_run_id: "run-unmount-control",
    events: [
      {
        id: "evt-unmount-control",
        run_id: "run-unmount-control",
        event_type: "user:message",
        timestamp: "2026-07-19T00:00:00Z",
        data: { content: "unmount" },
      },
    ],
  });
  sessionApi.getStatus = (async (sessionId, runId) => ({
    session_id: sessionId,
    run_id: runId,
    status: "failed",
    raw_status: "failed",
  })) as typeof sessionApi.getStatus;
  globalThis.fetch = ((_input, init) =>
    new Promise<Response>((resolve) => {
      playbackSignal = init?.signal as AbortSignal;
      resolvePlayback = resolve;
    })) as typeof fetch;

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-unmount-control", "run-unmount-control");
      harness.hook.runControlLifecycle.open();
      await Promise.resolve();
    });
    assert.ok(playbackSignal);

    await harness.unmount();
    assert.equal((playbackSignal as AbortSignal).aborted, true);
    resolvePlayback(
      new Response(
        JSON.stringify({ run_id: "run-unmount-control", timeline: [], events: [], artifacts: [], steps: [], multi_agent: null }),
      ),
    );
    await Promise.resolve();
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    globalThis.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent synchronously aborts a deferred run-control GET from the production auth-incarnation event", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const originalFetch = globalThis.fetch;
  let resolvePlayback!: (value: Response) => void;
  let playbackSignal: AbortSignal | null = null;
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-auth-event-control",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async () => ({
    current_run_id: "run-auth-event-control",
    events: [{
      id: "evt-auth-event-control",
      run_id: "run-auth-event-control",
      event_type: "user:message",
      timestamp: "2026-07-19T00:00:00Z",
      data: { content: "auth role refresh" },
    }],
  });
  sessionApi.getStatus = (async (sessionId, runId) => ({
    session_id: sessionId,
    run_id: runId,
    status: "failed",
    raw_status: "failed",
  })) as typeof sessionApi.getStatus;
  globalThis.fetch = ((_input, init) =>
    new Promise<Response>((resolve) => {
      playbackSignal = init?.signal as AbortSignal;
      resolvePlayback = resolve;
    })) as typeof fetch;

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory(
        "session-auth-event-control",
        "run-auth-event-control",
      );
      harness.hook.runControlLifecycle.open();
      await Promise.resolve();
    });
    assert.ok(playbackSignal);

    // This is the production same-tab event, deliberately not a synthetic
    // storage event. It models the synchronous fence for login/logout/role
    // refresh before React publishes the new principal.
    await harness.dispatchProductionAuthIncarnation("incarnation-role-refresh");
    assert.equal((playbackSignal as AbortSignal).aborted, true);
    assert.equal(harness.hook.runControlLifecycle.getSnapshot().owner, null);

    resolvePlayback(
      new Response(
        JSON.stringify({ run_id: "run-auth-event-control", timeline: [], events: [], artifacts: [], steps: [], multi_agent: null }),
      ),
    );
    await settle(harness.act);
    assert.equal(harness.hook.runControlLifecycle.getSnapshot().playback, null);
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    globalThis.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent fences the old owner before login's deferred principal GET resolves", async () => {
  const harness = await loadReactHarness();
  const { BROWSER_AUTH_INCARCINATION_EVENT } = await import(
    "../../browserAuthCoordinator.ts"
  );
  const { authApi } = await import("../../../services/api/auth.ts");
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalAuthLogin = authApi.login;
  const originalAuthGetCurrentUser = authApi.getCurrentUser;
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const originalFetch = globalThis.fetch;
  const freshPrincipal = await originalAuthGetCurrentUser();
  let resolvePrincipal!: (value: typeof freshPrincipal) => void;
  const deferredPrincipal = new Promise<typeof freshPrincipal>((resolve) => {
    resolvePrincipal = resolve;
  });
  let principalSignal: AbortSignal | undefined;
  let resolvePlayback!: (value: Response) => void;
  let playbackSignal: AbortSignal | null = null;
  let incarnationEvents = 0;
  const countIncarnationEvent = () => {
    incarnationEvents += 1;
  };
  dom.window.addEventListener(
    BROWSER_AUTH_INCARCINATION_EVENT,
    countIncarnationEvent,
  );
  authApi.login = (async () => {}) as typeof authApi.login;
  authApi.getCurrentUser = ((options?: { signal?: AbortSignal }) => {
    principalSignal = options?.signal;
    return deferredPrincipal;
  }) as typeof authApi.getCurrentUser;
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-login-order-control",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async () => ({
    current_run_id: "run-login-order-control",
    events: [{
      id: "evt-login-order-control",
      run_id: "run-login-order-control",
      event_type: "user:message",
      timestamp: "2026-07-19T00:00:00Z",
      data: { content: "old owner" },
    }],
  });
  sessionApi.getStatus = (async (sessionId, runId) => ({
    session_id: sessionId,
    run_id: runId,
    status: "failed",
    raw_status: "failed",
  })) as typeof sessionApi.getStatus;
  globalThis.fetch = ((_input, init) =>
    new Promise<Response>((resolve) => {
      playbackSignal = init?.signal as AbortSignal;
      resolvePlayback = resolve;
    })) as typeof fetch;

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory(
        "session-login-order-control",
        "run-login-order-control",
      );
      harness.hook.runControlLifecycle.open();
      await Promise.resolve();
    });
    assert.ok(playbackSignal);

    let login!: Promise<unknown>;
    await harness.act(async () => {
      login = harness.auth.login({ username: "user-a", password: "test-password" });
      await Promise.resolve();
    });

    assert.ok(principalSignal, "the new principal GET should be pending");
    assert.equal(principalSignal?.aborted, false);
    assert.equal(incarnationEvents, 1, "marker establishment publishes one event");
    assert.equal(
      (playbackSignal as AbortSignal).aborted,
      true,
      "marker establishment must fence the old run before principal hydration resolves",
    );
    assert.equal(harness.hook.runControlLifecycle.getSnapshot().owner, null);

    await harness.act(async () => {
      resolvePrincipal(freshPrincipal);
      await login;
    });
    assert.equal(incarnationEvents, 1, "principal hydration must not duplicate the event");
  } finally {
    resolvePlayback?.(
      new Response(
        JSON.stringify({ run_id: "run-login-order-control", timeline: [], events: [], artifacts: [], steps: [], multi_agent: null }),
      ),
    );
    authApi.login = originalAuthLogin;
    authApi.getCurrentUser = originalAuthGetCurrentUser;
    dom.window.removeEventListener(
      BROWSER_AUTH_INCARCINATION_EVENT,
      countIncarnationEvent,
    );
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    globalThis.fetch = originalFetch;
    await harness.cleanup();
  }
});

test("useAgent fences an A retry before pending B submission can issue its POST", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const originalRetryRun = sessionApi.retryRun;
  const originalSubmitChat = sessionApi.submitChat;
  let rejectSubmission!: (reason?: unknown) => void;
  const pendingSubmission = new Promise<ChatStreamResponse>((_resolve, reject) => {
    rejectSubmission = reject;
  });
  let retryPosts = 0;
  sessionApi.markRead = async () => {};
  sessionApi.get = async () => ({
    id: "session-a-admission",
    agent_id: "general-agent",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async () => ({
    current_run_id: "run-a-admission",
    events: [{
      id: "evt-a-admission",
      run_id: "run-a-admission",
      event_type: "user:message",
      timestamp: "2026-07-19T00:00:00Z",
      data: { content: "run A" },
    }],
  });
  sessionApi.getStatus = (async (sessionId, runId) => ({
    session_id: sessionId,
    run_id: runId,
    status: "failed",
    raw_status: "failed",
  })) as typeof sessionApi.getStatus;
  sessionApi.retryRun = (async () => {
    retryPosts += 1;
    return { session_id: "session-a-admission", run_id: "run-a-child", status: "queued" };
  }) as typeof sessionApi.retryRun;
  sessionApi.submitChat = (() => pendingSubmission) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-a-admission", "run-a-admission");
    });
    let submission!: Promise<unknown>;
    await harness.act(async () => {
      submission = harness.hook.sendMessage("begin B") as Promise<unknown>;
      await Promise.resolve();
    });

    assert.equal(
      harness.hook.runControlLifecycle.getSnapshot().owner,
      null,
      "B admission must invalidate A before its submit POST settles",
    );
    await harness.act(async () => {
      await harness.hook.runControlLifecycle.retry();
    });
    assert.equal(retryPosts, 0, "a pending B admission must fence A's retry POST");

    await harness.act(async () => {
      rejectSubmission(new Error("test ends the deferred B admission"));
      await submission;
    });
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    sessionApi.retryRun = originalRetryRun;
    sessionApi.submitChat = originalSubmitChat;
    await harness.cleanup();
  }
});
