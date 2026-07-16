import assert from "node:assert/strict";
import test from "node:test";

import type { UseAgentReturn } from "../types.ts";
import type { ChatStreamResponse } from "../../../services/api/session.ts";

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
      constructor(
        readonly type: string,
        readonly init?: { detail?: unknown },
      ) {}
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

async function loadReactHarness({ strict = false }: { strict?: boolean } = {}) {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { AuthProvider } = await import("../../useAuth.tsx");
  const { useAgent } = await import("../../useAgent.ts");
  const { authApi } = await import("../../../services/api/auth.ts");

  let snapshot: UseAgentReturn | null = null;
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
    snapshot = useAgent();
    return null;
  }

  const probe = React.createElement(Probe);
  try {
    await React.act(async () => {
      root.render(
        React.createElement(
          AuthProvider,
          null,
          strict ? React.createElement(React.StrictMode, null, probe) : probe,
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

  return {
    act: React.act,
    get hook() {
      assert.ok(snapshot, "useAgent hook should be mounted");
      return snapshot;
    },
    async rotateAuthScope(userId: string, tenantId: string) {
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
      await settle(React.act);
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
  let sseCalls = 0;
  const originalFetch = dom.window.fetch;
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

test("useAgent clears a rotated auth scope before fresh and owned-session submissions", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalMarkRead = sessionApi.markRead;
  const originalSubmitChat = sessionApi.submitChat;
  const originalGenerateTitle = sessionApi.generateTitle;
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
  sessionApi.generateTitle = async (sessionId) => ({
    title: "新会话",
    session_id: sessionId,
  });
  dom.window.fetch = async () => completedSseResponse();
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
    throw new Error("session_admission_retryable");
  }) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-owned-a");
    });
    let pending: Promise<unknown> | null = null;
    await harness.act(async () => {
      pending = harness.hook.sendMessage("旧身份请求");
      await Promise.resolve();
    });

    await harness.rotateAuthScope("user-b", "tenant-b");
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
    assert.equal(harness.hook.messages.length, 0);
    assert.equal(harness.hook.currentRunId, null);
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.markRead = originalMarkRead;
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.generateTitle = originalGenerateTitle;
    dom.window.fetch = originalFetch;
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
