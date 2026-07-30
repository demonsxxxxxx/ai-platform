import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

import React from "react";

import type { AgentProfilePublicProjection } from "../../../types/agentProfile.ts";

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
  value = "";
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
    sessionStorage: Storage;
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
  const sessionValues = new Map<string, string>();
  windowTarget.sessionStorage = {
    getItem: (key) => sessionValues.get(key) ?? null,
    setItem: (key, value) => sessionValues.set(key, value),
    removeItem: (key) => sessionValues.delete(key),
    clear: () => sessionValues.clear(),
    key: (index) => [...sessionValues.keys()][index] ?? null,
    get length() {
      return sessionValues.size;
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
    sessionStorage: windowTarget.sessionStorage,
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

test("rendered Marketplace searches cards, opens versioned detail, and gates start Chat", async () => {
  const dom = installDom();
  const ReactDOM = await import("react-dom/client");
  const { MemoryRouter, Route, Routes, useLocation } = await import("react-router-dom");
  const { AgentMarketRoute } = await import("../AgentMarketRoute.tsx");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const shellHarness = await prepareShellHarness();
  const profiles: Array<AgentProfilePublicProjection & Record<string, unknown>> = [
    {
      agent_id: "agt_support",
      expected_revision: 4,
      name: "支持助手",
      description: "已发布的支持服务。",
      avatar_ref: "builtin:assistant",
      category: "support",
    },
    {
      agent_id: "agt_finance",
      expected_revision: 2,
      name: "财务助手",
      description: "核对报销材料。",
      avatar_ref: "builtin:document",
      category: "operations",
      instructions: "PRIVATE_PROMPT",
      model_id: "private-model",
      mcp_tool_ids: ["private-mcp"],
      selected_skill: {
        skill_id: "private-skill",
        expected_version: "private-version",
      },
    },
  ];
  const originalListPublished = agentProfileApi.listPublished;
  const originalGetPublished = agentProfileApi.getPublished;
  const originalCreateConversation = agentProfileApi.createConversation;
  let catalogCalls = 0;
  let catalogRequest: unknown;
  agentProfileApi.listPublished = async (request) => {
    catalogCalls += 1;
    catalogRequest = request;
    return { agent_profiles: profiles };
  };
  let detailCalls = 0;
  agentProfileApi.getPublished = async (agentId) => {
    detailCalls += 1;
    const profile = profiles.find((item) => item.agent_id === agentId);
    if (!profile) throw Object.assign(new Error("missing"), { status: 404 });
    return profile;
  };
  const conversationSelections: unknown[] = [];
  let admissionResult: "denied" | "agent-mismatch" | "revision-mismatch" | "accepted" =
    "denied";
  agentProfileApi.createConversation = async (selection) => {
    conversationSelections.push(selection);
    if (admissionResult === "denied") {
      throw Object.assign(new Error("denied"), { status: 403 });
    }
    return {
      session_id: "session-finance",
      workspace_id: "default",
      agent_id: admissionResult === "agent-mismatch" ? "agt_other" : "agt_finance",
      title: "财务助手",
      agent_conversation: {
        agent_id: "agt_finance",
        revision: admissionResult === "revision-mismatch" ? 3 : 2,
        name: "财务助手",
        description: "核对报销材料。",
        avatar_ref: "builtin:document",
        category: "operations",
      },
    };
  };
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
          { initialEntries: ["/agent-market?q=财务&category=operations"] },
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
                  path: "/agent-market/:agentId/:revision",
                  element: React.createElement(AgentMarketRoute),
                }),
                React.createElement(Route, {
                  path: "/agent-market/:agentId/:revision/chat/:sessionId?",
                  element: React.createElement("div", { "data-agent-workspace": true }),
                }),
              ),
            ),
          ),
        ),
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.ok(container.querySelector("[data-agent-market]"));
    assert.ok(container.querySelector("[data-agent-market-search]"));
    const categoryGroup = container.querySelector("[data-agent-market-filter]");
    assert.ok(categoryGroup);
    assert.equal(categoryGroup.getAttribute("role"), "group");
    assert.equal(categoryGroup.getAttribute("aria-label"), "智能体分类");
    assert.equal(categoryGroup.querySelectorAll('[role="tab"]').length, 0);
    const categoryButtons = categoryGroup.querySelectorAll("button");
    assert.equal(
      categoryButtons.find((button) => button.textContent === "运营效率")?.getAttribute(
        "aria-pressed",
      ),
      "true",
    );
    assert.equal(
      categoryButtons.find((button) => button.textContent === "全部")?.getAttribute(
        "aria-pressed",
      ),
      "false",
    );
    assert.equal(container.querySelectorAll("[data-agent-market-card]").length, 1);
    assert.deepEqual(catalogRequest, { query: "财务", category: "operations" });
    assert.ok(container.querySelector("[data-workbench-header]"), "market must render in AppShell");
    assert.ok(
      container.querySelector("[data-librechat-desktop-sidebar]"),
      "market must retain SessionSidebar",
    );

    const search = container.querySelector("[data-agent-market-search]");
    assert.ok(search);
    assert.equal(search.value, "财务");
    assert.equal(container.querySelectorAll("[data-agent-market-card]").length, 1);
    assert.match(container.textContent, /财务助手/);
    assert.match(container.textContent, /运营效率/);
    assert.doesNotMatch(container.textContent, /支持助手/);

    const card = container
      .querySelectorAll("button")
      .find((button) => button.getAttribute("aria-label") === "查看 财务助手 详情");
    assert.ok(card, "filtered published card should remain actionable");
    await React.act(async () => {
      card.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.equal(currentPath, "/agent-market/agt_finance/2");
    assert.ok(container.querySelector("[data-agent-market-detail]"));
    assert.match(container.textContent, /核对报销材料/);
    assert.doesNotMatch(
      container.textContent,
      /PRIVATE_PROMPT|private-model|private-mcp|private-skill|private-version/,
    );

    const startChat = container
      .querySelectorAll("button")
      .find((button) => button.hasAttribute("data-agent-market-start-chat"));
    assert.ok(startChat, "detail must expose an explicit start-chat command");
    assert.equal(startChat.hasAttribute("disabled"), false);
    await React.act(async () => {
      startChat.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(currentPath, "/agent-market/agt_finance/2");
    assert.match(container.textContent, /当前账号无权使用该智能体/);
    admissionResult = "agent-mismatch";
    await React.act(async () => {
      startChat.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(currentPath, "/agent-market/agt_finance/2");
    assert.match(container.textContent, /发布版本已更新/);

    admissionResult = "revision-mismatch";
    await React.act(async () => {
      startChat.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(currentPath, "/agent-market/agt_finance/2");
    assert.match(container.textContent, /发布版本已更新/);

    admissionResult = "accepted";
    await React.act(async () => {
      startChat.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.deepEqual(conversationSelections, [
      { agent_id: "agt_finance", expected_revision: 2 },
      { agent_id: "agt_finance", expected_revision: 2 },
      { agent_id: "agt_finance", expected_revision: 2 },
      { agent_id: "agt_finance", expected_revision: 2 },
    ]);
    assert.equal(
      currentPath,
      "/agent-market/agt_finance/2/chat/session-finance",
    );
    assert.ok(container.querySelector("[data-agent-workspace]"));
    assert.equal(catalogCalls, 1);
    assert.equal(detailCalls, 1, "detail navigation must re-authorize the current publication");
  } finally {
    agentProfileApi.listPublished = originalListPublished;
    agentProfileApi.getPublished = originalGetPublished;
    agentProfileApi.createConversation = originalCreateConversation;
    shellHarness.restore();
    await React.act(async () => root.unmount());
  }
});

test("a shared detail URL restores the exact current published revision", async () => {
  const dom = installDom();
  const ReactDOM = await import("react-dom/client");
  const { MemoryRouter, Route, Routes, useLocation } = await import("react-router-dom");
  const { AgentMarketRoute } = await import("../AgentMarketRoute.tsx");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const shellHarness = await prepareShellHarness();
  const originalGetPublished = agentProfileApi.getPublished;
  agentProfileApi.getPublished = async () => ({
    agent_id: "agt_support",
    expected_revision: 4,
    name: "支持助手",
    description: "当前发布版本。",
    avatar_ref: "builtin:assistant",
    category: "support",
  });
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
          { initialEntries: ["/agent-market/agt_support/4"] },
          shellHarness.wrap(
            React.createElement(React.Fragment, null,
              React.createElement(LocationProbe),
              React.createElement(
                Routes,
                null,
                React.createElement(Route, {
                  path: "/agent-market",
                  element: React.createElement(AgentMarketRoute),
                }),
                React.createElement(Route, {
                  path: "/agent-market/:agentId/:revision",
                  element: React.createElement(AgentMarketRoute),
                }),
              ),
            ),
          ),
        ),
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.equal(currentPath, "/agent-market/agt_support/4");
    assert.ok(container.querySelector("[data-agent-market-detail]"));
    assert.match(container.textContent, /支持助手/);
    assert.match(container.textContent, /当前发布版本/);
    assert.ok(container.querySelector("[data-workbench-header]"));
    assert.ok(container.querySelector("[data-librechat-desktop-sidebar]"));
  } finally {
    agentProfileApi.getPublished = originalGetPublished;
    shellHarness.restore();
    await React.act(async () => root.unmount());
  }
});

test("a stale detail revision fails closed back to the safe Marketplace", async () => {
  const dom = installDom();
  const ReactDOM = await import("react-dom/client");
  const { MemoryRouter, Route, Routes, useLocation } = await import("react-router-dom");
  const { AgentMarketRoute } = await import("../AgentMarketRoute.tsx");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const shellHarness = await prepareShellHarness();
  const originalGetPublished = agentProfileApi.getPublished;
  agentProfileApi.getPublished = async () => ({
    agent_id: "agt_support",
    expected_revision: 5,
    name: "支持助手",
    description: "更新后的发布版本。",
    avatar_ref: "builtin:assistant",
    category: "support",
  });
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
          { initialEntries: ["/agent-market/agt_support/4"] },
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
                  path: "/agent-market/:agentId/:revision",
                  element: React.createElement(AgentMarketRoute),
                }),
              ),
            ),
          ),
        ),
      );
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.equal(currentPath, "/agent-market");
    assert.ok(container.querySelector("[data-agent-market]"));
    assert.equal(container.querySelector("[data-agent-market-detail]"), null);
    assert.equal(container.querySelector("[data-canonical-chat]"), null);
  } finally {
    agentProfileApi.getPublished = originalGetPublished;
    shellHarness.restore();
    await React.act(async () => root.unmount());
  }
});
