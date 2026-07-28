import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

import React from "react";

register(new URL("./frontendAssetLoader.mjs", import.meta.url), import.meta.url);
await new Promise<void>((resolve) => setImmediate(resolve));

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

  get firstChild() {
    return this.childNodes[0] ?? null;
  }

  get lastChild() {
    return this.childNodes[this.childNodes.length - 1] ?? null;
  }

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

  get innerHTML() {
    return this.childNodes.map((child) => child.nodeValue ?? "").join("");
  }

  set innerHTML(value: string) {
    this.childNodes = value ? [new TestText(value)] : [];
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

  get data() {
    return this.nodeValue ?? "";
  }

  set data(value: string) {
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
    if (tagName.toLowerCase() === "style") {
      element.appendChild(this.createTextNode(""));
    }
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
    requestAnimationFrame: windowTarget.requestAnimationFrame,
    cancelAnimationFrame: windowTarget.cancelAnimationFrame,
    CustomEvent: class {
      readonly bubbles = true;
      cancelBubble = false;
      defaultPrevented = false;

      constructor(
        readonly type: string,
        readonly init: { detail?: unknown } = {},
      ) {}

      get detail() {
        return this.init.detail;
      }
    },
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
  });
  return { document, window: windowTarget };
}

async function prepareShellHarness() {
  await import("../../../i18n/index.ts");
  const { AuthProvider } = await import("../../../hooks/useAuth.tsx");
  const { SettingsProvider } = await import("../../../contexts/SettingsContext.tsx");
  const { ThemeProvider } = await import("../../../contexts/ThemeContext.tsx");
  const { authApi } = await import("../../../services/api/auth.ts");
  const { sessionApi } = await import("../../../services/api/session.ts");
  const { modelPublicApi } = await import("../../../services/api/modelPublic.ts");
  const { notificationPublicApi } = await import("../../../services/api/notificationPublic.ts");
  const originals = {
    bootstrapAuthContext: authApi.bootstrapAuthContext,
    getCurrentUser: authApi.getCurrentUser,
    updateMetadata: authApi.updateMetadata,
    listSessions: sessionApi.list,
    listAvailableModels: modelPublicApi.listAvailable,
    getPinnedModelIds: modelPublicApi.getPinnedModelIds,
    getActiveNotifications: notificationPublicApi.getActive,
  };
  authApi.bootstrapAuthContext = async () => undefined;
  authApi.getCurrentUser = async () => {
    throw new Error("logged out test session");
  };
  authApi.updateMetadata = async () => {
    throw new Error("no backend in mounted route test");
  };
  sessionApi.list = async () => ({
    sessions: [],
    total: 0,
    skip: 0,
    limit: 20,
    has_more: false,
  });
  modelPublicApi.listAvailable = async () => ({
    models: [],
    count: 0,
    enabled_count: 0,
    default_model_id: null,
  });
  modelPublicApi.getPinnedModelIds = async () => [];
  notificationPublicApi.getActive = async () => [];

  return {
    wrap(children: React.ReactNode) {
      return React.createElement(
        ThemeProvider,
        null,
        React.createElement(
          AuthProvider,
          null,
          React.createElement(SettingsProvider, null, children),
        ),
      );
    },
    restore() {
      authApi.bootstrapAuthContext = originals.bootstrapAuthContext;
      authApi.getCurrentUser = originals.getCurrentUser;
      authApi.updateMetadata = originals.updateMetadata;
      sessionApi.list = originals.listSessions;
      modelPublicApi.listAvailable = originals.listAvailableModels;
      modelPublicApi.getPinnedModelIds = originals.getPinnedModelIds;
      notificationPublicApi.getActive = originals.getActiveNotifications;
    },
  };
}

test("rendered Agent Market card uses the shell, navigates to Chat, and stages the exact lock", async () => {
  const dom = installDom();
  const ReactDOM = await import("react-dom/client");
  const { MemoryRouter, Route, Routes, useLocation } = await import("react-router-dom");
  const { AgentMarketRoute } = await import("../AgentMarketRoute.tsx");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { consumePendingAgentMarketSelection } = await import("../agentMarketSelection.ts");
  const shellHarness = await prepareShellHarness();
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
          shellHarness.wrap(
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
        ),
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    const card = container
      .querySelectorAll("button")
      .find((button) => button.getAttribute("aria-label") === "与 支持助手 开始对话");
    assert.ok(card, "published card should render");
    assert.ok(container.querySelector("[data-workbench-header]"), "market must render in AppShell");
    assert.ok(
      container.querySelector("[data-librechat-desktop-sidebar]"),
      "market must retain SessionSidebar",
    );
    assert.equal(container.querySelector("[data-agent-market-card]"), null);
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
    shellHarness.restore();
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
  const shellHarness = await prepareShellHarness();
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
          shellHarness.wrap(
            React.createElement(
              Routes,
              null,
              React.createElement(Route, {
                path: "/agent-market/:agentId/:revision",
                element: React.createElement(AgentMarketRoute),
              }),
            ),
          ),
        ),
      );
      await Promise.resolve();
    });

    assert.ok(container.querySelector("[data-agent-market-invalid]"));
    assert.equal(
      container
        .querySelectorAll("button")
        .some((button) => button.getAttribute("aria-label")?.includes("开始对话")),
      false,
    );
    assert.equal(container.querySelector("textarea"), null);
    assert.equal(catalogCalls, 0, "legacy links must not rehydrate a market catalog");
    assert.equal(consumePendingAgentMarketSelection(), null);
  } finally {
    agentProfileApi.listPublished = originalListPublished;
    shellHarness.restore();
    await React.act(async () => root.unmount());
  }
});
