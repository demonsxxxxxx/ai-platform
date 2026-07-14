import assert from "node:assert/strict";
import test from "node:test";

import type { UseAgentReturn } from "../types.ts";
import type { ChatStreamResponse } from "../../../services/api/session.ts";

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
    value: { userAgent: "node" },
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
  authApi.getCurrentUser = async () => ({
    id: "user-a",
    username: "user-a",
    email: "user-a@example.test",
    roles: [],
    permissions: [],
    is_admin: false,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  });

  function Probe() {
    snapshot = useAgent();
    return null;
  }

  const probe = React.createElement(Probe);
  await React.act(async () => {
    root.render(
      React.createElement(
        AuthProvider,
        null,
        strict ? React.createElement(React.StrictMode, null, probe) : probe,
      ),
    );
  });

  let unmounted = false;
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
    unmount,
    async cleanup() {
      await unmount();
      authApi.getCurrentUser = originalGetCurrentUser;
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
    return sseEventResponse("run_event", {
      run_id: "run-failed",
      event_type: "run_failed",
    });
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
    assert.equal(sseCalls, 1);
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

test("useAgent derives an events-only failed reload run before normalizing its product card", async () => {
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
  sessionApi.getEvents = async () => ({
    events: [
      {
        id: "evt-history-user",
        run_id: "run-history-failed",
        event_type: "user:message",
        timestamp: "2026-07-15T00:00:00Z",
        data: { content: "执行任务" },
      },
      {
        id: "evt-history-failed",
        run_id: "run-history-failed",
        event_type: "run_event",
        timestamp: "2026-07-15T00:00:01Z",
        data: {
          event_type: "run_failed",
          message: "Executor failed",
          severity: "error",
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

test("useAgent reconnects only the events-derived active run after reload", async () => {
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
  sessionApi.getEvents = async () => ({
    events: [
      {
        id: "evt-history-interrupted",
        run_id: "run-history-interrupted",
        event_type: "user:message",
        timestamp: "2026-07-15T00:00:00Z",
        data: { content: "恢复后中断" },
      },
    ],
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
