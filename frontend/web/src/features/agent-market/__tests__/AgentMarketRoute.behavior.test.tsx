import assert from "node:assert/strict";
import { mock, test } from "node:test";

import React from "react";

type Listener = (event: Record<string, unknown>) => void;

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

  getListeners(type: string) {
    return this.listeners.get(type);
  }

  dispatchEvent(event: Record<string, unknown>) {
    if (event.bubbles === undefined) event.bubbles = true;
    if (event.target === undefined) event.target = this;
    if (event.defaultPrevented === undefined) event.defaultPrevented = false;
    event.preventDefault ??= () => {
      event.defaultPrevented = true;
    };
    event.stopPropagation ??= () => {
      event.cancelBubble = true;
    };
    dispatchEventFromTarget(this, event);
    return true;
  }
}

function dispatchEventFromTarget(target: TestEventTarget, event: Record<string, unknown>) {
  let current: TestEventTarget | null = target;
  while (current) {
    event.currentTarget = current;
    current.getListeners(String(event.type))?.forEach((listener) => listener(event));
    if (event.cancelBubble || event.bubbles !== true) break;
    current = current instanceof TestNode ? current.parentNode : null;
  }
}

class TestNode extends TestEventTarget {
  parentNode: TestNode | null = null;
  childNodes: TestNode[] = [];
  nodeValue: string | null = null;

  get textContent() {
    return this.childNodes.map((child) => child.textContent ?? "").join("");
  }

  set textContent(_value: string) {
    this.childNodes = [];
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
  readonly attributes = new Map<string, string>();
  ownerDocument!: TestDocument;
  className = "";
  isContentEditable = false;
  private text = "";

  constructor(readonly tagName: string) {
    super();
  }

  get nodeName() {
    return this.tagName.toUpperCase();
  }

  get textContent() {
    return this.text || this.childNodes.map((child) => child.textContent ?? "").join("");
  }

  set textContent(value: string) {
    this.text = value;
    this.childNodes = [];
  }

  setAttribute(name: string, value: string) {
    this.attributes.set(name, value);
  }

  removeAttribute(name: string) {
    this.attributes.delete(name);
  }

  getAttribute(name: string) {
    return this.attributes.get(name) ?? null;
  }

  hasAttribute(name: string) {
    return this.attributes.has(name);
  }

  getBoundingClientRect() {
    return { top: 0, right: 100, bottom: 40, left: 0, width: 100, height: 40 };
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }

  querySelector(selector: string): TestElement | null {
    return this.querySelectorAll(selector)[0] ?? null;
  }

  querySelectorAll(selector: string): TestElement[] {
    const match = selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
    const tag = /^[a-z]+$/i.test(selector) ? selector.toUpperCase() : null;
    const matches: TestElement[] = [];
    const visit = (node: TestNode) => {
      for (const child of node.childNodes) {
        if (child instanceof TestElement) {
          const attributeMatch = match
            ? child.hasAttribute(match[1]) &&
              (match[2] === undefined || child.getAttribute(match[1]) === match[2])
            : false;
          if (attributeMatch || (tag !== null && child.nodeName === tag)) {
            matches.push(child);
          }
          visit(child);
        }
      }
    };
    visit(this);
    return matches;
  }
}

class TestText extends TestNode {
  readonly nodeType = 3;
  readonly nodeName = "#text";

  constructor(value: string) {
    super();
    this.nodeValue = value;
  }

  get textContent() {
    return this.nodeValue ?? "";
  }

  set textContent(value: string) {
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

function installDom() {
  const document = new TestDocument();
  const storage = new Map<string, string>();
  const windowTarget = new TestEventTarget() as TestEventTarget & {
    document: TestDocument;
    location: { pathname: string; href: string; search: string; hash: string };
    localStorage: Storage;
    matchMedia: (query: string) => MediaQueryList;
    requestAnimationFrame: (callback: FrameRequestCallback) => number;
    cancelAnimationFrame: (id: number) => void;
    setTimeout: typeof setTimeout;
    clearTimeout: typeof clearTimeout;
    innerHeight: number;
    scrollY: number;
    scrollTo: () => void;
  };
  windowTarget.document = document;
  windowTarget.location = {
    pathname: "/agent-market",
    href: "http://test.local/agent-market",
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
  windowTarget.matchMedia = () => ({
    matches: false,
    media: "",
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent() {
      return false;
    },
  }) as MediaQueryList;
  windowTarget.requestAnimationFrame = (callback) =>
    setTimeout(() => callback(Date.now()), 0) as unknown as number;
  windowTarget.cancelAnimationFrame = (id) => clearTimeout(id);
  windowTarget.setTimeout = setTimeout;
  windowTarget.clearTimeout = clearTimeout;
  windowTarget.innerHeight = 800;
  windowTarget.scrollY = 0;
  windowTarget.scrollTo = () => {};
  Object.assign(windowTarget, {
    Element: TestElement,
    HTMLElement: TestElement,
    HTMLInputElement: TestElement,
    HTMLTextAreaElement: TestElement,
    HTMLSelectElement: TestElement,
    HTMLIFrameElement: TestElement,
    SVGElement: TestElement,
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
    HTMLInputElement: TestElement,
    HTMLTextAreaElement: TestElement,
    HTMLSelectElement: TestElement,
    HTMLIFrameElement: TestElement,
    SVGElement: TestElement,
    IS_REACT_ACT_ENVIRONMENT: true,
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
  });
  return { document, window: windowTarget };
}

mock.module(new URL("../../../components/layout/AppContent/AppShell.tsx", import.meta.url).href, {
  namedExports: {
    AppShell: ({ sidebar, children }: { sidebar: React.ReactNode; children: React.ReactNode }) =>
      React.createElement("div", { "data-app-shell": true }, sidebar, children),
  },
});
mock.module(new URL("../../../components/panels/SessionSidebar.tsx", import.meta.url).href, {
  namedExports: {
    SessionSidebar: () => React.createElement("aside", { "data-session-sidebar": true }),
  },
});

test("rendered Agent Market card uses the shell, navigates to Chat, and stages the exact lock", async () => {
  const dom = installDom();
  const ReactDOM = await import("react-dom/client");
  const { MemoryRouter, Route, Routes, useLocation } = await import("react-router-dom");
  const { AgentMarketRoute } = await import("../AgentMarketRoute.tsx");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { consumePendingAgentMarketSelection } = await import("../agentMarketSelection.ts");
  const profile = {
    agent_id: "agt_support",
    expected_revision: 4,
    name: "支持助手",
    description: "已发布的支持服务。",
  };
  const originalListPublished = agentProfileApi.listPublished;
  agentProfileApi.listPublished = async () => ({ agent_profiles: [profile] });
  let currentPath = "";
  function LocationProbe() {
    currentPath = useLocation().pathname;
    return null;
  }

  const container = dom.document.createElement("div");
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/agent-market"] },
          React.createElement(
            React.Fragment,
            null,
            React.createElement(LocationProbe),
            React.createElement(
              Routes,
              null,
              React.createElement(Route, {
                path: "/agent-market",
                element: React.createElement(AgentMarketRoute),
              }),
              React.createElement(Route, {
                path: "/chat",
                element: React.createElement("main", { "data-canonical-chat": true }),
              }),
            ),
          ),
        ),
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    const card = container.querySelector('[data-agent-market-card="agt_support"]');
    assert.ok(card, "published card should render");
    assert.ok(container.querySelector("[data-app-shell]"), "market must render in AppShell");
    assert.ok(
      container.querySelector("[data-session-sidebar]"),
      "market must retain SessionSidebar",
    );
    await React.act(async () => {
      card.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
    });

    assert.equal(currentPath, "/chat");
    assert.ok(container.querySelector("[data-canonical-chat]"));
    assert.deepEqual(consumePendingAgentMarketSelection(), {
      agent_id: "agt_support",
      expected_revision: 4,
    });
    assert.equal(consumePendingAgentMarketSelection(), null);
  } finally {
    agentProfileApi.listPublished = originalListPublished;
    await React.act(async () => root.unmount());
  }
});

test("legacy Agent Market chat URLs fail closed without local Chat or selection", async () => {
  const dom = installDom();
  const ReactDOM = await import("react-dom/client");
  const { MemoryRouter, Route, Routes } = await import("react-router-dom");
  const { AgentMarketRoute } = await import("../AgentMarketRoute.tsx");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { consumePendingAgentMarketSelection } = await import("../agentMarketSelection.ts");
  const originalListPublished = agentProfileApi.listPublished;
  let catalogCalls = 0;
  agentProfileApi.listPublished = async () => {
    catalogCalls += 1;
    return { agent_profiles: [] };
  };
  const container = dom.document.createElement("div");
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/agent-market/agt_support/4"] },
          React.createElement(
            Routes,
            null,
            React.createElement(Route, {
              path: "/agent-market/:agentId/:revision",
              element: React.createElement(AgentMarketRoute),
            }),
          ),
        ),
      );
      await Promise.resolve();
    });

    assert.ok(container.querySelector("[data-agent-market-invalid]"));
    assert.equal(container.querySelector("[data-agent-market-card]"), null);
    assert.equal(container.querySelector("textarea"), null);
    assert.equal(catalogCalls, 0, "legacy links must not rehydrate a market catalog");
    assert.equal(consumePendingAgentMarketSelection(), null);
  } finally {
    agentProfileApi.listPublished = originalListPublished;
    await React.act(async () => root.unmount());
  }
});
