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

async function loadReactHarness() {
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

  await React.act(async () => {
    root.render(
      React.createElement(AuthProvider, null, React.createElement(Probe)),
    );
  });

  return {
    act: React.act,
    get hook() {
      assert.ok(snapshot, "useAgent hook should be mounted");
      return snapshot;
    },
    async cleanup() {
      await React.act(async () => root.unmount());
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
  return new Response('event: complete\ndata: {"run_id":"run"}\n\n', {
    headers: { "content-type": "text/event-stream" },
  });
}

function sseEventResponse(event: string, data: Record<string, unknown>) {
  return new Response(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`, {
    headers: { "content-type": "text/event-stream" },
  });
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
    assert.equal(harness.hook.currentRunId, "run-second");
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

test("useAgent requires the authoritative run detail before reconnecting history", async () => {
  const harness = await loadReactHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const originalFetch = dom.window.fetch;
  let sseCalls = 0;
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
    metadata: { current_run_id: "run-history-failed" },
  });
  sessionApi.getEvents = async () => ({ events: [] });
  sessionApi.getStatus = async () => ({
    session_id: "session-history-failed",
    run_id: "run-history-failed",
    status: "failed",
  });

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-history-failed");
    });
    await settle(harness.act);

    assert.equal(harness.hook.currentRunId, null);
    assert.equal(harness.hook.connectionStatus, "disconnected");
    assert.equal(sseCalls, 0);
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    dom.window.fetch = originalFetch;
    await harness.cleanup();
  }
});
