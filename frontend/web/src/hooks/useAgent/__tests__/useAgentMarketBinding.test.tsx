import assert from "node:assert/strict";
import test from "node:test";

import type { UseAgentReturn } from "../types.ts";
import { ApiRequestError } from "../../../services/api/fetch.ts";

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

async function loadHarness() {
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
    permissions: [],
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

test("useAgent sends the market lock on the first successful Chat submission and does not reuse it", async () => {
  const { setPendingAgentMarketSelection, consumePendingAgentMarketSelection } =
    await import("../../../features/agent-market/agentMarketSelection.ts");
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalMarkRead = sessionApi.markRead;
  const selectedAgentProfile = {
    agent_id: "agt_support",
    expected_revision: 4,
  } as const;

  setPendingAgentMarketSelection(selectedAgentProfile);
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
      firstOutcome = await harness.hook.sendMessage("market first");
    });
    await settle(harness.act);

    assert.equal(firstOutcome?.status, "accepted");
    assert.deepEqual(submissions[0]?.[10], selectedAgentProfile);

    let secondOutcome: { status: string } | undefined;
    await harness.act(async () => {
      secondOutcome = await harness.hook.sendMessage("normal second");
    });
    await settle(harness.act);

    assert.equal(secondOutcome?.status, "accepted");
    assert.equal(submissions.length, 2);
    assert.equal(submissions[1]?.[10], null);
    assert.equal(consumePendingAgentMarketSelection(), null);
  } finally {
    await harness.cleanup();
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.markRead = originalMarkRead;
    consumePendingAgentMarketSelection();
  }
});

test("useAgent sends the Agent Market lock once and clears it across rejection and failure paths", async () => {
  const { setPendingAgentMarketSelection, consumePendingAgentMarketSelection } =
    await import("../../../features/agent-market/agentMarketSelection.ts");
  const { sessionApi } = await import("../../../services/api/session.ts");
  const originalSubmitChat = sessionApi.submitChat;
  const originalMarkRead = sessionApi.markRead;
  const selectedAgentProfile = {
    agent_id: "agt_support",
    expected_revision: 4,
  } as const;
  const cases: Array<{
    label: string;
    error: Error;
    allowsFreshRetry: boolean;
  }> = [
    ["stale revision", "agent_profile_revision_stale"],
    ["unauthorized profile", "agent_profile_not_authorized"],
    ["archived profile", "agent_profile_archived"],
    ["draft profile", "agent_profile_draft"],
  ].map(([label, code]) => ({
    label,
    error: new ApiRequestError(
      `${label} rejected`,
      409,
      code,
      "rejected_before_persist",
    ),
    allowsFreshRetry: true,
  }));
  cases.push({
    label: "submission transport failure",
    error: new Error("connection failed"),
    allowsFreshRetry: false,
  });

  try {
    for (const { label, error, allowsFreshRetry } of cases) {
      setPendingAgentMarketSelection(selectedAgentProfile);
      const harness = await loadHarness();
      const submissions: unknown[][] = [];
      sessionApi.markRead = async () => {};
      sessionApi.submitChat = (async (...args) => {
        submissions.push(args);
        if (submissions.length === 1) throw error;
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
          firstOutcome = await harness.hook.sendMessage(`${label} first`);
        });
        await settle(harness.act);
        assert.equal(firstOutcome?.status, "failed");
        assert.deepEqual(submissions[0]?.[10], selectedAgentProfile);

        let secondOutcome: { status: string } | undefined;
        await harness.act(async () => {
          secondOutcome = await harness.hook.sendMessage(`${label} second`);
        });
        await settle(harness.act);

        if (allowsFreshRetry) {
          assert.equal(secondOutcome?.status, "accepted");
          assert.equal(submissions.length, 2);
          assert.ok(submissions[1]?.[10] == null);
        } else {
          assert.equal(secondOutcome?.status, "failed");
          assert.equal(submissions.length, 1);
        }
        assert.equal(consumePendingAgentMarketSelection(), null);
      } finally {
        await harness.cleanup();
      }
    }
  } finally {
    sessionApi.submitChat = originalSubmitChat;
    sessionApi.markRead = originalMarkRead;
    consumePendingAgentMarketSelection();
  }
});
