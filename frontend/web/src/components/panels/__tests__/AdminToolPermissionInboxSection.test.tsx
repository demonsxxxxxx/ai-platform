import assert from "node:assert/strict";
import test from "node:test";

import "../../../i18n";
import type { User } from "../../../types";
import type { AdminToolPermissionInboxClient } from "../AdminToolPermissionInboxSection.tsx";

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
  username: "admin-a",
  email: "admin-a@example.test",
  roles: [],
  permissions: [],
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

async function mountInbox(user: User, client: AdminToolPermissionInboxClient) {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { AuthProvider } = await import("../../../hooks/useAuth.tsx");
  const { authApi } = await import("../../../services/api/auth.ts");
  const { AdminToolPermissionInboxSection } = await import("../AdminToolPermissionInboxSection.tsx");
  const originalGetCurrentUser = authApi.getCurrentUser;
  authApi.getCurrentUser = async () => user;
  const container = document.createElement("div");
  const root = createRoot(container as never);
  await React.act(async () => {
    root.render(
      React.createElement(
        AuthProvider,
        null,
        React.createElement(AdminToolPermissionInboxSection, { client }),
      ),
    );
    await Promise.resolve();
    await Promise.resolve();
  });
  return {
    React,
    container,
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
              permission_request_id: "tpr-a",
              run_id: "run-owner",
              tool_id: "customer-write",
              tool_call_id: "call-a",
              risk_level: "high",
              write_capable: true,
              status: "pending",
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

test("administrator inbox keeps 403 and 409 errors localized and free of raw server text", async () => {
  let mode: "forbidden" | "conflict" = "forbidden";
  const client: AdminToolPermissionInboxClient = {
    list: async () => {
      if (mode === "forbidden") throw { status: 403, message: "private-server-detail" };
      return {
        permission_requests: [{
          permission_request_id: "tpr-conflict",
          run_id: "run-owner",
          tool_id: "customer-write",
          tool_call_id: "call-conflict",
          risk_level: "high",
          write_capable: false,
          status: "pending",
        }],
        total: 1,
        status: "pending",
        limit: 50,
      };
    },
    decide: async () => {
      throw { status: 409, message: "private-server-detail" };
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
    assert.match(textOf(conflict.container), /该权限请求已被处理/);
    assert.doesNotMatch(textOf(conflict.container), /private-server-detail/);
  } finally {
    await conflict.cleanup();
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
