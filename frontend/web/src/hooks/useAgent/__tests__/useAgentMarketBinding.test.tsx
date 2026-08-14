import assert from "node:assert/strict";
import test from "node:test";

import type { UseAgentReturn } from "../types.ts";
import { ApiRequestError } from "../../../services/api/fetch.ts";
import { Permission } from "../../../types/auth.ts";

type Listener = (event: { type: string; [key: string]: unknown }) => void;

class TestEventTarget {
  private readonly listeners = new Map<string, Set<Listener>>();

  addEventListener(type: string, listener: Listener) {
    const listeners = this.listeners.get(type) ?? new Set<Listener>();
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

  get firstChild() {
    return this.childNodes[0] ?? null;
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
  readonly style = {
    setProperty: (_name: string, _value: string) => {},
    removeProperty: (_name: string) => {},
  };
  ownerDocument!: TestDocument;
  className = "";
  isContentEditable = false;

  constructor(readonly tagName: string) {
    super();
  }

  get nodeName() {
    return this.tagName.toUpperCase();
  }

  set innerHTML(value: string) {
    this.childNodes = value ? [new TestText(value)] : [];
  }

  get innerHTML() {
    return this.childNodes.map((child) => child.nodeValue ?? "").join("");
  }

  setAttribute(_name: string, _value: string) {}
  removeAttribute(_name: string) {}
  getAttribute(_name: string) {
    return null;
  }
  hasAttribute(_name: string) {
    return false;
  }
}

class TestText extends TestNode {
  readonly nodeType = 3;
  readonly nodeName = "#text";

  constructor(value: string) {
    super();
    this.nodeValue = value;
  }

  get data() {
    return this.nodeValue ?? "";
  }

  set data(value: string) {
    this.nodeValue = value;
  }
}

class TestDocument extends TestNode {
  readonly nodeType = 9;
  readonly nodeName = "#document";
  readonly documentElement: TestElement;
  readonly head: TestElement;
  readonly body: TestElement;
  activeElement: TestElement;
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
    return new TestText(value);
  }
}

class TestLockManager {
  async request<T>(
    _name: string,
    options: { mode: "exclusive" },
    callback: () => Promise<T>,
  ): Promise<T> {
    assert.equal(options.mode, "exclusive");
    return callback();
  }
}

function installDom() {
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
  windowTarget.location = {
    href: "http://test.local/chat",
    pathname: "/chat",
    search: "",
    hash: "",
  };
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
  windowTarget.clearTimeout = clearTimeout;
  windowTarget.setTimeout = setTimeout;
  Object.assign(windowTarget, {
    Element: TestElement,
    HTMLElement: TestElement,
    HTMLIFrameElement: TestElement,
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
    HTMLIFrameElement: TestElement,
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
    value: { userAgent: "node", locks: new TestLockManager() },
  });
  return { document, window: windowTarget };
}

const dom = installDom();

function clearPersistedSubmissionReferences() {
  for (let index = dom.window.localStorage.length - 1; index >= 0; index -= 1) {
    const key = dom.window.localStorage.key(index);
    if (key?.startsWith("ai_platform_chat_submission")) {
      dom.window.localStorage.removeItem(key);
    }
  }
}

async function settle(act: typeof import("react").act) {
  for (let index = 0; index < 8; index += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function loadHarness({
  permissions = [],
}: {
  permissions?: Permission[];
} = {}) {
  clearPersistedSubmissionReferences();
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
  authApi.getCurrentUser = async () => ({
    id: "user-a",
    tenant_id: "tenant-a",
    username: "user-a",
    email: "user-a@example.test",
    roles: [],
    permissions,
    is_admin: false,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  });
  authApi.bootstrapAuthContext = async () => {};

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
      assert.ok(snapshot);
      return snapshot;
    },
    async cleanup() {
      await React.act(async () => root.unmount());
      authApi.getCurrentUser = originalGetCurrentUser;
      authApi.bootstrapAuthContext = originalBootstrapAuthContext;
    },
  };
}

test("useAgent forwards only an explicit Agent profile without inheriting it", async () => {
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalMarkRead = sessionApi.markRead;
  const selectedAgentProfile = {
    agent_id: "agt_support",
    expected_revision: 4,
  } as const;

  const harness = await loadHarness();
  const submissions: unknown[][] = [];
  sessionApi.markRead = async () => {};
  sessionApi.submitChat = (async (...args) => {
    submissions.push(args);
    return {
      session_id: undefined,
      run_id: null,
      status: "needs_confirmation",
      suggestions: [],
    };
  }) as typeof sessionApi.submitChat;

  try {
    let firstOutcome: { status: string } | undefined;
    await harness.act(async () => {
      firstOutcome = await harness.hook.sendMessage(
        "explicit profile",
        undefined,
        undefined,
        null,
        selectedAgentProfile,
      );
    });
    await settle(harness.act);

    assert.equal(firstOutcome?.status, "accepted");
    assert.deepEqual(submissions[0]?.[10], selectedAgentProfile);

    let secondOutcome: { status: string } | undefined;
    await harness.act(async () => {
      secondOutcome = await harness.hook.sendMessage("generic later");
    });
    await settle(harness.act);

    assert.equal(secondOutcome?.status, "accepted");
    assert.equal(submissions.length, 2);
    assert.equal(submissions[1]?.[10], null);
  } finally {
    await harness.cleanup();
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.markRead = originalMarkRead;
  }
});

test("generic Chat never inherits an Agent Market binding", async () => {
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalMarkRead = sessionApi.markRead;
  const harness = await loadHarness();
  const submissions: unknown[][] = [];
  sessionApi.markRead = async () => {};
  sessionApi.submitChat = (async (...args) => {
    submissions.push(args);
    return {
      session_id: undefined,
      run_id: null,
      status: "needs_confirmation",
      suggestions: [],
    };
  }) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      assert.equal((await harness.hook.sendMessage("generic first")).status, "accepted");
      assert.equal((await harness.hook.sendMessage("generic later")).status, "accepted");
    });
    await settle(harness.act);

    assert.equal(submissions.length, 2);
    assert.equal(submissions[0]?.[10], null);
    assert.equal(submissions[1]?.[10], null);
  } finally {
    await harness.cleanup();
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.markRead = originalMarkRead;
  }
});

test("a recovered Agent Conversation owns every exact selector and fails closed", async () => {
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalMarkRead = sessionApi.markRead;
  const originalGet = sessionApi.get;
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const originalGetEvents = sessionApi.getEvents;
  const harness = await loadHarness();
  const submissions: unknown[][] = [];
  sessionApi.markRead = async () => {};
  let authoritativeMode: "current" | "agent-mismatch" | "rejected" = "current";
  sessionApi.get = async (sessionId) => ({
    id: sessionId,
    agent_id: sessionId.startsWith("session-agent") ? "agt_support" : "general-agent",
    created_at: "2026-07-29T00:00:00Z",
    updated_at: "2026-07-29T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getAuthoritative = async (sessionId) => {
    if (authoritativeMode === "rejected") {
      throw Object.assign(new Error("stale"), { status: 409 });
    }
    const isAgentSession = sessionId.startsWith("session-agent");
    return {
      session_id: sessionId,
      workspace_id: "default",
      agent_id:
        authoritativeMode === "agent-mismatch"
          ? "agt_other"
          : isAgentSession
            ? "agt_support"
            : "general-agent",
      title: isAgentSession ? "支持助手" : "普通会话",
      purpose: "conversation",
      agent_conversation: isAgentSession
        ? {
            agent_id: "agt_support",
            revision: 7,
            name: "支持助手",
            description: "处理已授权的支持请求。",
            welcome_message: "欢迎使用支持助手。",
            starter_prompts: ["帮我处理支持请求"],
            capability_summary: "在授权范围内处理企业支持请求。",
            recommended_tasks: ["支持请求分流"],
            supported_input_types: ["text"],
            expected_outputs: ["处理建议"],
            permissions_and_data_access_notice: "仅访问当前用户授权的数据。",
            avatar_ref: "builtin:assistant",
            category: "support" as const,
            published_at: "2026-08-04T01:00:00Z",
          }
        : null,
    };
  };
  sessionApi.getEvents = async () => ({ events: [] });
  sessionApi.submitChat = (async (...args) => {
    submissions.push(args);
    return {
      session_id: undefined,
      run_id: null,
      status: "needs_confirmation",
      suggestions: [],
    };
  }) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      await harness.hook.loadHistory("session-agent");
    });
    await settle(harness.act);

    await harness.act(async () => {
      assert.equal(
        (
          await harness.hook.sendMessage(
            "bound first",
            { model_id: "client-model" },
            undefined,
            {
              skill_id: "client-skill",
              expected_version: "client-version",
            },
            { agent_id: "forged-agent", expected_revision: 99 },
          )
        ).status,
        "accepted",
      );
      assert.equal((await harness.hook.sendMessage("bound later")).status, "accepted");
    });
    await settle(harness.act);

    assert.equal(submissions.length, 2);
    for (const submission of submissions) {
      assert.equal(submission[1], "session-agent");
      assert.equal(submission[2], undefined, "model/Prompt options must be omitted");
      assert.equal(submission[4], undefined, "Skill selectors must be omitted");
      assert.equal(submission[6], undefined, "selected Skill must be omitted");
      assert.equal(submission[9], undefined, "MCP selectors must be omitted");
      assert.deepEqual(submission[10], {
        agent_id: "agt_support",
        expected_revision: 7,
      });
    }

    await harness.act(async () => {
      await harness.hook.loadHistory("session-generic");
    });
    await settle(harness.act);
    await harness.act(async () => {
      assert.equal(
        (
          await harness.hook.sendMessage(
            "generic after Agent",
            { model_id: "generic-model" },
            undefined,
            null,
            { agent_id: "forged-agent", expected_revision: 99 },
          )
        ).status,
        "accepted",
      );
    });
    await settle(harness.act);

    assert.equal(submissions.length, 3);
    assert.equal(submissions[2]?.[1], "session-generic");
    assert.deepEqual(submissions[2]?.[2], { model_id: "generic-model" });
    assert.deepEqual(submissions[2]?.[4], []);
    assert.equal(submissions[2]?.[10], null);

    authoritativeMode = "agent-mismatch";
    await harness.act(async () => {
      await harness.hook.loadHistory("session-agent-mismatch");
      assert.equal((await harness.hook.sendMessage("must not submit mismatch")).status, "failed");
    });
    await settle(harness.act);
    assert.equal(submissions.length, 3);

    authoritativeMode = "rejected";
    await harness.act(async () => {
      await harness.hook.loadHistory("session-agent-stale");
      assert.equal((await harness.hook.sendMessage("must not submit stale")).status, "failed");
    });
    await settle(harness.act);
    assert.equal(submissions.length, 3);
  } finally {
    await harness.cleanup();
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.markRead = originalMarkRead;
    sessionApi.get = originalGet;
    sessionApi.getAuthoritative = originalGetAuthoritative;
    sessionApi.getEvents = originalGetEvents;
  }
});

test("an ordinary failed file-Skill session preserves generic continuation authority", async () => {
  const harness = await loadHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const originalGetEvents = sessionApi.getEvents;
  const originalGetStatus = sessionApi.getStatus;
  const originalMarkRead = sessionApi.markRead;
  const originalSubmitChat = sessionApi.submitChat;
  const submissions: unknown[][] = [];
  const historyReads: Array<string | null> = [];
  const persistedEvents = [
    {
      id: "message-file-skill",
      event_type: "user:message",
      run_id: "run-file-skill-failed",
      timestamp: "2026-07-30T01:38:00Z",
      data: {
        message_id: "message-file-skill",
        content: "创建受控文件",
        locked_skill_label: "controlled-file",
      },
    },
  ];
  sessionApi.markRead = async () => {};
  sessionApi.get = async (sessionId) => ({
    id: sessionId,
    agent_id: "general-agent",
    created_at: "2026-07-30T01:38:00Z",
    updated_at: "2026-07-30T01:38:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getAuthoritative = async (sessionId) => ({
    session_id: sessionId,
    workspace_id: "default",
    agent_id: "general-agent",
    title: "普通会话",
    purpose: "conversation",
    agent_conversation: null,
  });
  sessionApi.getEvents = async (_sessionId, options) => {
    historyReads.push(options?.run_id || null);
    return { current_run_id: "run-file-skill-failed", events: persistedEvents };
  };
  sessionApi.getStatus = (async () => ({
    session_id: "session-file-skill-failed",
    run_id: "run-file-skill-failed",
    status: "error",
    raw_status: "failed",
    error: "executor_failure",
  })) as typeof sessionApi.getStatus;
  sessionApi.submitChat = (async (...args) => {
    submissions.push(args);
    return { status: "needs_confirmation", suggestions: [] };
  }) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      assert.notEqual(
        await harness.hook.loadHistory("session-file-skill-failed"),
        null,
      );
    });
    await settle(harness.act);

    assert.deepEqual(historyReads, [null, "run-file-skill-failed"]);
    assert.equal(harness.hook.error, null);
    assert.equal(harness.hook.currentRunId, null);
    assert.equal(
      harness.hook.messages.filter((message) => message.role === "user").length,
      1,
    );
    assert.equal(harness.hook.messages[0]?.lockedSkillLabel, "controlled-file");
    assert.equal(
      harness.hook.messages
        .flatMap((message) => message.parts || [])
        .some((part) => part.type === "artifact"),
      false,
    );

    await harness.act(async () => {
      assert.deepEqual(await harness.hook.sendMessage("同会话继续"), {
        status: "accepted",
      });
    });
    assert.equal(submissions.length, 1);
    assert.equal(submissions[0]?.[1], "session-file-skill-failed");
    assert.equal(submissions[0]?.[8], "general-agent");
    assert.equal(submissions[0]?.[10], null);
  } finally {
    sessionApi.get = originalGet;
    sessionApi.getAuthoritative = originalGetAuthoritative;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.getStatus = originalGetStatus;
    sessionApi.markRead = originalMarkRead;
    sessionApi.submitChat = originalSubmitChat;
    await harness.cleanup();
  }
});

test("required session projection failures remain fail-closed with bounded diagnostics", async () => {
  for (const failingRead of ["session", "authoritative"] as const) {
    const harness = await loadHarness();
    const { sessionApi } = await import("../../../services/api/session.ts");
    const originalGet = sessionApi.get;
    const originalGetAuthoritative = sessionApi.getAuthoritative;
    const originalMarkRead = sessionApi.markRead;
    const originalSubmitChat = sessionApi.submitChat;
    const originalConsoleError = console.error;
    const diagnostics: unknown[][] = [];
    let submissions = 0;
    console.error = (...args: unknown[]) => diagnostics.push(args);
    sessionApi.markRead = async () => {};
    sessionApi.get = async (sessionId) => {
      if (failingRead === "session") {
        throw new ApiRequestError(
          "private compatibility response",
          503,
          "session_projection_unavailable",
        );
      }
      return {
        id: sessionId,
        agent_id: "general-agent",
        created_at: "2026-07-30T01:38:00Z",
        updated_at: "2026-07-30T01:38:00Z",
        is_active: true,
        metadata: {},
      };
    };
    sessionApi.getAuthoritative = async (sessionId) => {
      if (failingRead === "authoritative") {
        throw new ApiRequestError(
          "private authoritative response",
          503,
          "session_projection_unavailable",
        );
      }
      return {
        session_id: sessionId,
        workspace_id: "default",
        agent_id: "general-agent",
        title: "普通会话",
        purpose: "conversation",
        agent_conversation: null,
      };
    };
    sessionApi.submitChat = (async () => {
      submissions += 1;
      return { status: "needs_confirmation", suggestions: [] };
    }) as typeof sessionApi.submitChat;

    try {
      await harness.act(async () => {
        assert.equal(await harness.hook.loadHistory(`session-${failingRead}`), null);
      });
      await settle(harness.act);
      assert.equal(harness.hook.error, "加载会话失败");
      await harness.act(async () => {
        assert.deepEqual(await harness.hook.sendMessage("不得提交"), {
          status: "failed",
        });
      });
      assert.equal(submissions, 0);
      assert.deepEqual(diagnostics, [
        [
          "[loadHistory] failed",
          {
            phase: "session_projection",
            status: 503,
            code: "session_projection_unavailable",
          },
        ],
      ]);
    } finally {
      console.error = originalConsoleError;
      sessionApi.get = originalGet;
      sessionApi.getAuthoritative = originalGetAuthoritative;
      sessionApi.markRead = originalMarkRead;
      sessionApi.submitChat = originalSubmitChat;
      await harness.cleanup();
    }
  }
});

test("event history failure keeps verified generic authority for explicit retry", async () => {
  const harness = await loadHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const originalGetEvents = sessionApi.getEvents;
  const originalMarkRead = sessionApi.markRead;
  const originalSubmitChat = sessionApi.submitChat;
  const originalConsoleError = console.error;
  const diagnostics: unknown[][] = [];
  const submissions: unknown[][] = [];
  let historyAvailable = false;
  console.error = (...args: unknown[]) => diagnostics.push(args);
  sessionApi.markRead = async () => {};
  sessionApi.get = async (sessionId) => ({
    id: sessionId,
    agent_id: "general-agent",
    created_at: "2026-07-30T01:38:00Z",
    updated_at: "2026-07-30T01:38:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getAuthoritative = async (sessionId) => ({
    session_id: sessionId,
    workspace_id: "default",
    agent_id: "general-agent",
    title: "普通会话",
    purpose: "conversation",
    agent_conversation: null,
  });
  sessionApi.getEvents = async () => {
    if (!historyAvailable) {
      throw new ApiRequestError(
        "private event payload",
        502,
        "history_events_unavailable",
      );
    }
    return { events: [] };
  };
  sessionApi.submitChat = (async (...args) => {
    submissions.push(args);
    return { status: "needs_confirmation", suggestions: [] };
  }) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      assert.equal(await harness.hook.loadHistory("session-history-retry"), null);
    });
    await settle(harness.act);
    await harness.act(async () => {
      assert.deepEqual(await harness.hook.sendMessage("继续同一会话"), {
        status: "accepted",
      });
    });
    assert.equal(submissions[0]?.[1], "session-history-retry");
    assert.equal(submissions[0]?.[10], null);
    assert.deepEqual(diagnostics, [
      [
        "[loadHistory] failed",
        {
          phase: "event_history",
          status: 502,
          code: "history_events_unavailable",
        },
      ],
    ]);

    historyAvailable = true;
    await harness.act(async () => {
      assert.notEqual(
        await harness.hook.loadHistory("session-history-retry"),
        null,
      );
    });
    await settle(harness.act);
    assert.equal(harness.hook.error, null);
  } finally {
    console.error = originalConsoleError;
    sessionApi.get = originalGet;
    sessionApi.getAuthoritative = originalGetAuthoritative;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.markRead = originalMarkRead;
    sessionApi.submitChat = originalSubmitChat;
    await harness.cleanup();
  }
});

test("optional feedback failure leaves the session authority usable", async () => {
  const harness = await loadHarness({ permissions: [Permission.FEEDBACK_READ] });
  const { sessionApi } = await import("../../../services/api/session.ts");
  const { feedbackApi } = await import("../../../services/api/feedback.ts");
  const originalGet = sessionApi.get;
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const originalGetEvents = sessionApi.getEvents;
  const originalMarkRead = sessionApi.markRead;
  const originalSubmitChat = sessionApi.submitChat;
  const originalFeedbackList = feedbackApi.list;
  const originalConsoleError = console.error;
  const diagnostics: unknown[][] = [];
  const submissions: unknown[][] = [];
  console.error = (...args: unknown[]) => diagnostics.push(args);
  sessionApi.markRead = async () => {};
  sessionApi.get = async (sessionId) => ({
    id: sessionId,
    agent_id: "general-agent",
    created_at: "2026-07-30T01:38:00Z",
    updated_at: "2026-07-30T01:38:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getAuthoritative = async (sessionId) => ({
    session_id: sessionId,
    workspace_id: "default",
    agent_id: "general-agent",
    title: "普通会话",
    purpose: "conversation",
    agent_conversation: null,
  });
  sessionApi.getEvents = async () => ({ events: [] });
  feedbackApi.list = async () => {
    throw new ApiRequestError(
      "private feedback payload",
      502,
      "feedback_projection_unavailable",
    );
  };
  sessionApi.submitChat = (async (...args) => {
    submissions.push(args);
    return { status: "needs_confirmation", suggestions: [] };
  }) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      assert.notEqual(await harness.hook.loadHistory("session-feedback"), null);
    });
    await settle(harness.act);
    await harness.act(async () => {
      assert.deepEqual(await harness.hook.sendMessage("仍可继续"), {
        status: "accepted",
      });
    });
    assert.equal(submissions[0]?.[1], "session-feedback");
    assert.equal(submissions[0]?.[10], null);
    assert.deepEqual(diagnostics, [
      [
        "[loadHistory] feedback failed",
        {
          phase: "feedback",
          status: 502,
          code: "feedback_projection_unavailable",
        },
      ],
    ]);
  } finally {
    console.error = originalConsoleError;
    sessionApi.get = originalGet;
    sessionApi.getAuthoritative = originalGetAuthoritative;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.markRead = originalMarkRead;
    sessionApi.submitChat = originalSubmitChat;
    feedbackApi.list = originalFeedbackList;
    await harness.cleanup();
  }
});

test("an authoritative identity mismatch remains fail-closed", async () => {
  const harness = await loadHarness();
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalGet = sessionApi.get;
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const originalMarkRead = sessionApi.markRead;
  const originalSubmitChat = sessionApi.submitChat;
  const originalConsoleError = console.error;
  const diagnostics: unknown[][] = [];
  let submissions = 0;
  console.error = (...args: unknown[]) => diagnostics.push(args);
  sessionApi.markRead = async () => {};
  sessionApi.get = async (sessionId) => ({
    id: sessionId,
    agent_id: "general-agent",
    created_at: "2026-07-30T01:38:00Z",
    updated_at: "2026-07-30T01:38:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getAuthoritative = async () => ({
    session_id: "different-session",
    workspace_id: "default",
    agent_id: "general-agent",
    title: "冲突会话",
    purpose: "conversation",
    agent_conversation: null,
  });
  sessionApi.submitChat = (async () => {
    submissions += 1;
    return { status: "needs_confirmation", suggestions: [] };
  }) as typeof sessionApi.submitChat;

  try {
    await harness.act(async () => {
      assert.equal(await harness.hook.loadHistory("session-identity-mismatch"), null);
    });
    await settle(harness.act);
    await harness.act(async () => {
      assert.deepEqual(await harness.hook.sendMessage("不得提交"), {
        status: "failed",
      });
    });
    assert.equal(submissions, 0);
    assert.deepEqual(diagnostics, [
      [
        "[loadHistory] failed",
        {
          phase: "identity_validation",
          status: null,
          code: "agent_conversation_identity_mismatch",
        },
      ],
    ]);
  } finally {
    console.error = originalConsoleError;
    sessionApi.get = originalGet;
    sessionApi.getAuthoritative = originalGetAuthoritative;
    sessionApi.markRead = originalMarkRead;
    sessionApi.submitChat = originalSubmitChat;
    await harness.cleanup();
  }
});
