import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";

import React from "react";

import { Permission } from "../../../types/auth.ts";
import {
  AGENT_PROFILE_CATEGORIES,
  AGENT_PROFILE_CATEGORY_LABELS,
  type AgentProfilePublicProjection,
} from "../../../types/agentProfile.ts";

const enterpriseProfileFields = {
  welcome_message: "欢迎使用企业专家。",
  starter_prompts: ["帮我处理企业任务"] as string[],
  capability_summary: "在授权范围内处理企业任务。",
  recommended_tasks: ["企业任务处理"] as string[],
  supported_input_types: ["text", "file"] as ["text", "file"],
  expected_outputs: ["处理建议"] as string[],
  permissions_and_data_access_notice: "仅访问当前用户授权的数据。",
  published_at: "2026-08-04T01:00:00Z",
};

register(new URL("./frontendAssetLoader.mjs", import.meta.url), import.meta.url);
await new Promise<void>((resolve) => setImmediate(resolve));

test("Agent Profile category labels cover the canonical category contract", () => {
  assert.deepEqual(
    AGENT_PROFILE_CATEGORIES.map((category) => [
      category,
      AGENT_PROFILE_CATEGORY_LABELS[category],
    ]),
    [
      ["general", "通用专家"],
      ["support", "支持服务"],
      ["writing", "内容写作"],
      ["research", "研究分析"],
      ["operations", "运营效率"],
    ],
  );
});

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
  private scrollTopValue = 0;
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

  closest(selector: string): TestElement | null {
    const match = selector.match(/^([a-z]+)?(?:\[([^=\]]+)(?:="([^"]*)")?\])?$/i);
    const tagMatches = !match?.[1] || this.nodeName === match[1].toUpperCase();
    const attributeMatches =
      !match?.[2] ||
      (this.hasAttribute(match[2]) &&
        (match[3] === undefined || this.getAttribute(match[2]) === match[3]));
    if (match && tagMatches && attributeMatches) return this;
    return this.parentNode instanceof TestElement
      ? this.parentNode.closest(selector)
      : null;
  }

  getBoundingClientRect() {
    return { top: 0, right: 100, bottom: 40, left: 0, width: 100, height: 40 };
  }

  get clientHeight() {
    return 600;
  }

  get clientWidth() {
    return 1000;
  }

  get offsetHeight() {
    return 600;
  }

  get offsetWidth() {
    return 1000;
  }

  get scrollHeight() {
    return 1200;
  }

  get scrollWidth() {
    return 1000;
  }

  get scrollTop() {
    return this.scrollTopValue;
  }

  set scrollTop(value: number) {
    this.scrollTopValue = value;
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }

  scrollIntoView() {}

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
  const CompositionEvent = class {};
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
    innerWidth: number;
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
  windowTarget.innerWidth = 1200;
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
    CompositionEvent,
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
    CompositionEvent,
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
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { userAgent: "node", locks: new TestLockManager() },
  });
  return { document, window: windowTarget };
}

async function prepareShellHarness({ authenticated = false } = {}) {
  await import("../../../i18n/index.ts");
  const { AuthProvider, useAuth } = await import("../../../hooks/useAuth.tsx");
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
  authApi.getCurrentUser = authenticated
    ? async () => ({
        id: "user-a",
        tenant_id: "tenant-a",
        username: "user-a",
        email: "user-a@example.test",
        roles: [],
        permissions: [Permission.CHAT_READ, Permission.CHAT_WRITE],
        is_admin: false,
        is_active: true,
        created_at: "2026-01-01T00:00:00Z",
        updated_at: "2026-01-01T00:00:00Z",
      })
    : async () => {
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

  function AuthenticatedGate({ children }: { children: React.ReactNode }) {
    return useAuth().hasPermission(Permission.CHAT_WRITE) ? children : null;
  }

  return {
    wrap(children: React.ReactNode) {
      const shellChildren = authenticated
        ? React.createElement(AuthenticatedGate, null, children)
        : children;
      return React.createElement(
        ThemeProvider,
        null,
        React.createElement(
          AuthProvider,
          null,
          React.createElement(SettingsProvider, null, shellChildren),
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

test("market search commits Chinese IME text only after composition ends", async () => {
  const dom = installDom();
  const ReactDOM = await import("react-dom/client");
  const { MemoryRouter, Route, Routes, useLocation } = await import("react-router-dom");
  const { AgentMarketRoute } = await import("../AgentMarketRoute.tsx");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const shellHarness = await prepareShellHarness();
  const profiles: AgentProfilePublicProjection[] = Array.from({ length: 10 }, (_, index) => ({
    ...enterpriseProfileFields,
    agent_id: `agt_support_${index + 1}`,
    expected_revision: 1,
    name: `支持助手 ${index + 1}`,
    description: "处理支持请求。",
    avatar_ref: "builtin:assistant",
    category: "support",
  }));
  const originalListPublished = agentProfileApi.listPublished;
  agentProfileApi.listPublished = async () => ({ agent_profiles: profiles });
  let currentPath = "";
  function LocationProbe() {
    const location = useLocation();
    currentPath = `${location.pathname}${location.search}`;
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
              ),
            ),
          ),
        ),
      );
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.equal(container.querySelectorAll("[data-agent-market-card]").length, 9);
    const pageTwo = container
      .querySelectorAll("button")
      .find((button) => button.textContent === "2");
    assert.ok(pageTwo);
    await React.act(async () => {
      pageTwo.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
    });
    assert.equal(container.querySelectorAll("[data-agent-market-card]").length, 1);

    const search = container.querySelector("[data-agent-market-search]");
    assert.ok(search);
    await React.act(async () => {
      search.value = "f";
      search.dispatchEvent({ type: "compositionstart", bubbles: true });
      search.value = "fa";
      search.dispatchEvent({ type: "input", bubbles: true });
      await Promise.resolve();
    });
    assert.equal(currentPath, "/agent-market");
    assert.equal(search.value, "fa");

    await React.act(async () => {
      search.value = "法";
      search.dispatchEvent({
        type: "compositionend",
        bubbles: true,
        data: "法",
      });
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(currentPath, "/agent-market?q=%E6%B3%95");
    assert.equal(search.value, "法");
  } finally {
    agentProfileApi.listPublished = originalListPublished;
    shellHarness.restore();
    await React.act(async () => root.unmount());
  }
});

test("rendered Marketplace opens a productized bare workspace without creating a conversation", async () => {
  const dom = installDom();
  const ReactDOM = await import("react-dom/client");
  const { MemoryRouter, Route, Routes, useLocation, useNavigate } = await import("react-router-dom");
  const { AgentMarketRoute } = await import("../AgentMarketRoute.tsx");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const shellHarness = await prepareShellHarness();
  const profiles: Array<AgentProfilePublicProjection & Record<string, unknown>> = [
    {
      ...enterpriseProfileFields,
      agent_id: "agt_support",
      expected_revision: 4,
      name: "支持助手",
      description: "已发布的支持服务。",
      avatar_ref: "builtin:assistant",
      category: "support",
      market_tag: "客户服务",
    },
    {
      ...enterpriseProfileFields,
      agent_id: "agt_finance",
      expected_revision: 2,
      name: "财务助手",
      description: "核对报销材料。",
      avatar_ref: "builtin:document",
      category: "operations",
      market_tag: "财务",
      completed_tasks: 7,
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
  agentProfileApi.createConversation = async (selection) => {
    conversationSelections.push(selection);
    return {
      session_id: "session-finance",
      workspace_id: "default",
      agent_id: "agt_finance",
      title: "财务助手",
      purpose: "conversation",
      agent_conversation: {
        ...enterpriseProfileFields,
        agent_id: "agt_finance",
        revision: 2,
        name: "财务助手",
        description: "核对报销材料。",
        avatar_ref: "builtin:document",
        category: "operations",
      },
    };
  };
  let currentPath = "";
  function LocationProbe() {
    const location = useLocation();
    currentPath = `${location.pathname}${location.search}`;
    return null;
  }
  function WorkspaceProbe() {
    const navigate = useNavigate();
    return React.createElement("button", {
      "data-agent-workspace": true,
      onClick: () => navigate("/agent-market?q=财务&tag=财务"),
      type: "button",
    });
  }

  const container = dom.document.createElement("div");
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/agent-market?q=财务&tag=财务"] },
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
                  element: React.createElement(WorkspaceProbe),
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
    assert.equal(categoryGroup.getAttribute("aria-label"), "市场标签");
    const favoriteControl = container.querySelector("[data-agent-market-favorites]");
    assert.ok(favoriteControl);
    assert.equal(favoriteControl.getAttribute("aria-pressed"), "false");
    assert.ok(container.querySelector("[data-agent-market-view]"));
    assert.ok(container.querySelector("[data-agent-market-sort]"));
    assert.equal(
      categoryGroup.querySelectorAll("button").find((button) => button.textContent?.includes("财务"))?.getAttribute(
        "aria-pressed",
      ),
      "true",
    );
    assert.equal(
      categoryGroup.querySelectorAll("button").find((button) => button.textContent === "全部")?.getAttribute(
        "aria-pressed",
      ),
      "false",
    );
    assert.equal(container.querySelectorAll("[data-agent-market-card]").length, 1);
    const listView = container.querySelector('[aria-label="列表视图"]');
    assert.ok(listView);
    await React.act(async () => {
      listView.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
    });
    assert.equal(listView.getAttribute("aria-pressed"), "true");
    const marketCardClassName = container.querySelector("[data-agent-market-card]")?.getAttribute("class") ?? "";
    assert.match(marketCardClassName, /flex-col/);
    assert.match(marketCardClassName, /sm:flex-row/);
    const taskSort = container
      .querySelector('[data-agent-market-sort]')
      ?.querySelectorAll("button")
      .find((button) => button.textContent === "完成任务最多");
    assert.ok(taskSort);
    await React.act(async () => {
      taskSort.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
    });
    assert.equal(taskSort.getAttribute("aria-pressed"), "true");
    assert.equal(catalogRequest, undefined);

    const favoritesTab = container
      .querySelectorAll("button")
      .find((button) => button.textContent === "我的收藏");
    assert.ok(favoritesTab);
    await React.act(async () => {
      favoritesTab.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(currentPath, "/agent-market?q=%E8%B4%A2%E5%8A%A1&tab=favorites");
    assert.equal(container.querySelectorAll("[data-agent-market-card]").length, 0);
    assert.match(container.textContent, /尚未收藏专家/);

    const tagsTab = container.querySelector("[data-agent-market-favorites]");
    assert.ok(tagsTab);
    await React.act(async () => {
      tagsTab.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(currentPath, "/agent-market?q=%E8%B4%A2%E5%8A%A1");
    assert.equal(container.querySelectorAll("[data-agent-market-card]").length, 1);
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
    assert.match(container.textContent, /财务/);
    assert.match(container.textContent, /已完成任务/);
    assert.match(container.textContent, /7/);
    assert.doesNotMatch(container.textContent, /支持助手/);

    const primaryAction = container
      .querySelectorAll("button")
      .find((button) => button.getAttribute("aria-label") === "使用 财务助手 开始任务");
    assert.ok(primaryAction, "filtered published card should open its dedicated workspace");
    assert.equal(primaryAction.nodeName, "BUTTON", "native button semantics preserve keyboard activation");
    assert.equal(primaryAction.getAttribute("type"), "button");
    await React.act(async () => {
      primaryAction.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.equal(currentPath, "/agent-market/agt_finance/2/chat");
    assert.ok(container.querySelector("[data-agent-workspace]"));
    assert.deepEqual(conversationSelections, []);
    assert.equal(detailCalls, 0, "opening a card must not detour through the detail authorization request");

    const returnToMarket = container.querySelector("[data-agent-workspace]");
    assert.ok(returnToMarket);
    await React.act(async () => {
      returnToMarket.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
      await Promise.resolve();
    });

    const detailAction = container
      .querySelectorAll("button")
      .find((button) => button.getAttribute("aria-label") === "查看 财务助手 详情");
    assert.ok(detailAction, "detail remains an explicit secondary action");
    await React.act(async () => {
      detailAction.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.equal(
      currentPath,
      "/agent-market/agt_finance/2?q=%E8%B4%A2%E5%8A%A1&tag=%E8%B4%A2%E5%8A%A1",
    );
    assert.ok(container.querySelector("[data-agent-market-detail]"));
    assert.match(container.textContent, /核对报销材料/);
    assert.match(container.textContent, /企业已发布/);
    assert.match(container.textContent, /版本 2/);
    assert.match(container.textContent, /适合处理/);
    assert.doesNotMatch(
      container.textContent,
      /PRIVATE_PROMPT|private-model|private-mcp|private-skill|private-version/,
    );

    const startChat = container
      .querySelectorAll("button")
      .find((button) => button.hasAttribute("data-agent-market-start-chat"));
    assert.ok(startChat, "detail must expose an explicit workspace command");
    await React.act(async () => {
      startChat.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.deepEqual(conversationSelections, []);
    assert.equal(currentPath, "/agent-market/agt_finance/2/chat");
    assert.ok(container.querySelector("[data-agent-workspace]"));
    assert.equal(catalogCalls, 2);
    assert.equal(detailCalls, 1, "detail navigation must re-authorize the current publication");
  } finally {
    agentProfileApi.listPublished = originalListPublished;
    agentProfileApi.getPublished = originalGetPublished;
    agentProfileApi.createConversation = originalCreateConversation;
    shellHarness.restore();
    await React.act(async () => root.unmount());
  }
});

test("Agent starter prompts draft before explicit first-message submission", async () => {
  const dom = installDom();
  const ReactDOM = await import("react-dom/client");
  const { MemoryRouter, Route, Routes, useLocation } = await import("react-router-dom");
  const { AgentMarketRoute } = await import("../AgentMarketRoute.tsx");
  const { AgentWorkspaceRoute } = await import("../AgentWorkspaceRoute.tsx");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { sessionApi } = await import("../../../services/api/session.ts");
  const shellHarness = await prepareShellHarness({ authenticated: true });
  const profile = {
    ...enterpriseProfileFields,
    agent_id: "agt_support",
    expected_revision: 4,
    name: "支持助手",
    description: "处理企业内部支持请求。",
    avatar_ref: "builtin:assistant",
    category: "support",
  } as const;
  const originalListPublished = agentProfileApi.listPublished;
  const originalGetPublished = agentProfileApi.getPublished;
  const originalListConversations = agentProfileApi.listConversations;
  const originalCreateConversation = agentProfileApi.createConversation;
  const originalGet = sessionApi.get;
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const originalGetEvents = sessionApi.getEvents;
  const originalMarkRead = sessionApi.markRead;
  const originalSubmitChat = sessionApi.submitChat;
  agentProfileApi.listPublished = async () => ({ agent_profiles: [profile] });
  agentProfileApi.getPublished = async () => profile;
  agentProfileApi.listConversations = async () => ({
    sessions: [],
    next_cursor: null,
  });
  const selections: Array<{ selection: unknown; operationId: string }> = [];
  agentProfileApi.createConversation = async (selection, operationId) => {
    const sessionId = `session-support-${selections.length + 1}`;
    selections.push({ selection, operationId });
    return {
      session_id: sessionId,
      workspace_id: "default",
      agent_id: profile.agent_id,
      title: profile.name,
      purpose: "conversation",
      agent_conversation: {
        ...enterpriseProfileFields,
        agent_id: profile.agent_id,
        revision: profile.expected_revision,
        name: profile.name,
        description: profile.description,
        avatar_ref: profile.avatar_ref,
        category: profile.category,
      },
    };
  };
  sessionApi.get = async (sessionId) => ({
    id: sessionId,
    agent_id: profile.agent_id,
    created_at: "2026-08-17T00:00:00Z",
    updated_at: "2026-08-17T00:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getAuthoritative = async (sessionId) => ({
    session_id: sessionId,
    workspace_id: "default",
    agent_id: profile.agent_id,
    title: profile.name,
    purpose: "conversation",
    agent_conversation: {
      ...enterpriseProfileFields,
      agent_id: profile.agent_id,
      revision: profile.expected_revision,
      name: profile.name,
      description: profile.description,
      avatar_ref: profile.avatar_ref,
      category: profile.category,
    },
  });
  sessionApi.getEvents = async () => ({ events: [] });
  sessionApi.markRead = async () => {};
  const submissions: unknown[][] = [];
  sessionApi.submitChat = (async (...args) => {
    submissions.push(args);
    return {
      session_id: args[1],
      run_id: null,
      status: "needs_confirmation" as const,
      suggestions: [],
    };
  }) as typeof sessionApi.submitChat;
  let currentPath = "";
  function LocationProbe() {
    const location = useLocation();
    currentPath = `${location.pathname}${location.search}`;
    return null;
  }

  const container = dom.document.createElement("div");
  const root = ReactDOM.createRoot(container as never);
  const reliableSessionStorage = dom.window.sessionStorage;
  async function waitUntil(predicate: () => boolean) {
    for (let attempt = 0; attempt < 40 && !predicate(); attempt += 1) {
      await React.act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    }
  }
  async function draftStarterPrompt(expectedCount: number) {
    const starterPrompt = container
      .querySelectorAll("button")
      .find((button) => button.textContent.includes("帮我处理企业任务"));
    assert.ok(starterPrompt);
    assert.equal(starterPrompt.hasAttribute("disabled"), false);
    await React.act(async () => {
      starterPrompt.dispatchEvent({ type: "click", bubbles: true });
    });

    const textarea = container.querySelector("textarea");
    assert.ok(textarea);
    await waitUntil(() => textarea.value === "帮我处理企业任务");
    assert.equal(textarea.value, "帮我处理企业任务");
    assert.equal(
      selections.length,
      expectedCount,
      "drafting a starter prompt must not create a conversation",
    );
    assert.equal(
      submissions.length,
      expectedCount,
      "drafting a starter prompt must not submit a run",
    );
    return textarea;
  }
  async function submitDraft(expectedCount: number, expectedSessionId: string) {
    const textarea = container.querySelector("textarea");
    assert.ok(textarea);
    const form = textarea.closest("form");
    assert.ok(form);
    await React.act(async () => {
      form.dispatchEvent({ type: "submit", bubbles: true });
    });
    await waitUntil(
      () => selections.length === expectedCount && submissions.length === expectedCount,
    );

    assert.equal(selections.length, expectedCount);
    assert.equal(
      submissions.length,
      expectedCount,
      `explicit submit must reach submitChat; rendered UI: ${container.textContent}`,
    );
    assert.equal(submissions[expectedCount - 1]?.[0], "帮我处理企业任务");
    assert.equal(submissions[expectedCount - 1]?.[1], expectedSessionId);
    assert.deepEqual(submissions[expectedCount - 1]?.[10], {
      agent_id: profile.agent_id,
      expected_revision: profile.expected_revision,
    });
  }

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
                  path: "/agent-market/:agentId/:revision/chat/:sessionId?",
                  element: React.createElement(AgentWorkspaceRoute),
                }),
              ),
            ),
          ),
        ),
      );
    });
    await waitUntil(() => container.querySelector("[data-agent-market-card]") !== null);

    assert.equal(currentPath, "/agent-market");
    const marketStart = container
      .querySelectorAll("button")
      .find((button) => button.getAttribute("aria-label") === "使用 支持助手 开始任务");
    assert.ok(marketStart);
    await React.act(async () => {
      marketStart.dispatchEvent({ type: "click", bubbles: true });
    });
    await waitUntil(() => container.querySelector("[data-agent-chat-opening]") !== null);

    assert.equal(currentPath, "/agent-market/agt_support/4/chat");
    assert.deepEqual(selections, []);
    assert.match(container.textContent, /欢迎使用企业专家/);
    assert.ok(container.querySelector("[data-agent-starter-prompts]"));
    assert.ok(container.querySelector("textarea"));

    await draftStarterPrompt(0);
    assert.equal(currentPath, "/agent-market/agt_support/4/chat");
    await submitDraft(1, "session-support-1");
    assert.equal(currentPath, "/agent-market/agt_support/4/chat");

    const startNewTask = container
      .querySelectorAll("button")
      .find((button) => button.getAttribute("aria-label") === "开始新任务");
    assert.ok(startNewTask);
    await React.act(async () => {
      startNewTask.dispatchEvent({ type: "click", bubbles: true });
    });
    await waitUntil(
      () =>
        currentPath === "/agent-market/agt_support/4/chat" &&
        container.querySelector("[data-agent-starter-prompts]") !== null,
    );

    assert.equal(currentPath, "/agent-market/agt_support/4/chat");
    assert.equal(selections.length, 1, "Start New Task must remain creation-free");
    assert.equal(submissions.length, 1);

    await draftStarterPrompt(1);
    assert.equal(currentPath, "/agent-market/agt_support/4/chat");
    await submitDraft(2, "session-support-2");
    assert.equal(currentPath, "/agent-market/agt_support/4/chat");
    assert.deepEqual(
      selections.map(({ selection }) => selection),
      [
        { agent_id: profile.agent_id, expected_revision: profile.expected_revision },
        { agent_id: profile.agent_id, expected_revision: profile.expected_revision },
      ],
    );
    const operationIds = selections.map(({ operationId }) => operationId);
    assert.equal(new Set(operationIds).size, 2);
  } finally {
    dom.window.sessionStorage = reliableSessionStorage;
    agentProfileApi.listPublished = originalListPublished;
    agentProfileApi.getPublished = originalGetPublished;
    agentProfileApi.listConversations = originalListConversations;
    agentProfileApi.createConversation = originalCreateConversation;
    sessionApi.get = originalGet;
    sessionApi.getAuthoritative = originalGetAuthoritative;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.markRead = originalMarkRead;
    sessionApi.submitChat = originalSubmitChat;
    shellHarness.restore();
    await React.act(async () => root.unmount());
  }
});

test("an owned revision N conversation remains on N after the Agent publishes N+1", async () => {
  const dom = installDom();
  const ReactDOM = await import("react-dom/client");
  const { MemoryRouter, Route, Routes, useLocation } = await import("react-router-dom");
  const { AgentWorkspaceRoute } = await import("../AgentWorkspaceRoute.tsx");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { sessionApi } = await import("../../../services/api/session.ts");
  const shellHarness = await prepareShellHarness();
  const currentProfile = {
    ...enterpriseProfileFields,
    agent_id: "agt_support",
    expected_revision: 5,
    name: "支持助手 V5",
    description: "当前发布版本。",
    avatar_ref: "builtin:assistant",
    category: "support",
  } as const;
  const historicalIdentity = {
    ...enterpriseProfileFields,
    agent_id: "agt_support",
    revision: 4,
    name: "支持助手 V4",
    description: "创建会话时固定的版本。",
    avatar_ref: "builtin:assistant",
    category: "support",
  } as const;
  const originalGetPublished = agentProfileApi.getPublished;
  const originalListConversations = agentProfileApi.listConversations;
  const originalCreateConversation = agentProfileApi.createConversation;
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const historySelections: unknown[] = [];
  const conversationSelections: unknown[] = [];
  agentProfileApi.getPublished = async () => currentProfile;
  agentProfileApi.listConversations = async (selection) => {
    historySelections.push(selection);
    return {
      sessions: [
        {
          session_id: "session-v4",
          workspace_id: "default",
          agent_id: "agt_support",
          title: "V4 历史会话",
          purpose: "conversation",
          created_at: "2026-08-03T01:00:00Z",
          updated_at: "2026-08-04T01:00:00Z",
          agent_conversation: historicalIdentity,
        },
      ],
      next_cursor: null,
    };
  };
  agentProfileApi.createConversation = async (selection) => {
    conversationSelections.push(selection);
    throw new Error("must_not_create_during_historical_recovery");
  };
  sessionApi.getAuthoritative = async () => ({
    session_id: "session-v4",
    workspace_id: "default",
    agent_id: "agt_support",
    title: "V4 历史会话",
    purpose: "conversation",
    agent_conversation: historicalIdentity,
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
          { initialEntries: ["/agent-market/agt_support/4/chat/session-v4"] },
          shellHarness.wrap(
            React.createElement(
              React.Fragment,
              null,
              React.createElement(LocationProbe),
              React.createElement(
                Routes,
                null,
                React.createElement(Route, {
                  path: "/agent-market/:agentId/:revision/chat/:sessionId?",
                  element: React.createElement(AgentWorkspaceRoute),
                }),
              ),
            ),
          ),
        ),
      );
      for (let index = 0; index < 12; index += 1) await Promise.resolve();
    });

    assert.equal(currentPath, "/agent-market/agt_support/4/chat/session-v4");
    assert.deepEqual(historySelections, [
      { agent_id: "agt_support", expected_revision: 4 },
    ]);
    assert.deepEqual(conversationSelections, []);
    assert.match(container.textContent, /支持助手 V4/);
    assert.doesNotMatch(container.textContent, /支持助手 V5/);
    const composer = container.querySelector("textarea");
    assert.ok(composer, "the superseded revision keeps its transcript composer frame");
    assert.equal(composer.hasAttribute("disabled"), true);
    assert.equal(composer.getAttribute("placeholder"), "该历史会话为只读状态");
  } finally {
    agentProfileApi.getPublished = originalGetPublished;
    agentProfileApi.listConversations = originalListConversations;
    agentProfileApi.createConversation = originalCreateConversation;
    sessionApi.getAuthoritative = originalGetAuthoritative;
    shellHarness.restore();
    await React.act(async () => root.unmount());
  }
});

test("a withdrawn Agent keeps its owned pinned conversation visible and read-only", async () => {
  const dom = installDom();
  const ReactDOM = await import("react-dom/client");
  const { MemoryRouter, Route, Routes, useLocation } = await import("react-router-dom");
  const { AgentWorkspaceRoute } = await import("../AgentWorkspaceRoute.tsx");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { sessionApi } = await import("../../../services/api/session.ts");
  const shellHarness = await prepareShellHarness();
  const historicalIdentity = {
    ...enterpriseProfileFields,
    agent_id: "agt_support",
    revision: 4,
    name: "已下架支持助手 V4",
    description: "创建会话时固定的版本。",
    avatar_ref: "builtin:assistant",
    category: "support",
  } as const;
  const originalGetPublished = agentProfileApi.getPublished;
  const originalListConversations = agentProfileApi.listConversations;
  const originalCreateConversation = agentProfileApi.createConversation;
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const historySelections: unknown[] = [];
  const conversationSelections: unknown[] = [];
  agentProfileApi.getPublished = async () => {
    throw Object.assign(new Error("agent withdrawn"), { status: 404 });
  };
  agentProfileApi.listConversations = async (selection) => {
    historySelections.push(selection);
    return {
      sessions: [
        {
          session_id: "session-v4",
          workspace_id: "default",
          agent_id: "agt_support",
          title: "V4 历史会话",
          purpose: "conversation",
          created_at: "2026-08-03T01:00:00Z",
          updated_at: "2026-08-04T01:00:00Z",
          agent_conversation: historicalIdentity,
        },
      ],
      next_cursor: null,
    };
  };
  agentProfileApi.createConversation = async (selection) => {
    conversationSelections.push(selection);
    throw new Error("withdrawn Agent must not create another conversation");
  };
  sessionApi.getAuthoritative = async () => ({
    session_id: "session-v4",
    workspace_id: "default",
    agent_id: "agt_support",
    title: "V4 历史会话",
    purpose: "conversation",
    agent_conversation: historicalIdentity,
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
          { initialEntries: ["/agent-market/agt_support/4/chat/session-v4"] },
          shellHarness.wrap(
            React.createElement(
              React.Fragment,
              null,
              React.createElement(LocationProbe),
              React.createElement(
                Routes,
                null,
                React.createElement(Route, {
                  path: "/agent-market/:agentId/:revision/chat/:sessionId?",
                  element: React.createElement(AgentWorkspaceRoute),
                }),
              ),
            ),
          ),
        ),
      );
      for (let index = 0; index < 12; index += 1) await Promise.resolve();
    });

    assert.equal(currentPath, "/agent-market/agt_support/4/chat/session-v4");
    assert.deepEqual(historySelections, [
      { agent_id: "agt_support", expected_revision: 4 },
    ]);
    assert.deepEqual(conversationSelections, []);
    assert.match(container.textContent, /已下架支持助手 V4/);
    const composer = container.querySelector("textarea");
    assert.ok(composer, "the historical transcript keeps its composer frame");
    assert.equal(composer.hasAttribute("disabled"), true);
    assert.equal(composer.getAttribute("placeholder"), "该历史会话为只读状态");
  } finally {
    agentProfileApi.getPublished = originalGetPublished;
    agentProfileApi.listConversations = originalListConversations;
    agentProfileApi.createConversation = originalCreateConversation;
    sessionApi.getAuthoritative = originalGetAuthoritative;
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
    ...enterpriseProfileFields,
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
    ...enterpriseProfileFields,
    agent_id: "agt_support",
    expected_revision: 5,
    name: "支持助手",
    description: "更新后的发布版本。",
    avatar_ref: "builtin:assistant",
    category: "support",
  });
  let currentPath = "";
  function LocationProbe() {
    const location = useLocation();
    currentPath = `${location.pathname}${location.search}`;
    return null;
  }
  const container = dom.document.createElement("div");
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/agent-market/agt_support/4?q=合同&category=support"] },
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

    assert.equal(decodeURI(currentPath), "/agent-market?q=合同&category=support");
    assert.ok(container.querySelector("[data-agent-market]"));
    assert.equal(container.querySelector("[data-agent-market-detail]"), null);
    assert.equal(container.querySelector("[data-canonical-chat]"), null);
  } finally {
    agentProfileApi.getPublished = originalGetPublished;
    shellHarness.restore();
    await React.act(async () => root.unmount());
  }
});

test("a route-param change never wires Agent A into Agent B while B is loading or rejected", async () => {
  const dom = installDom();
  const ReactDOM = await import("react-dom/client");
  const { flushSync } = await import("react-dom");
  const { Router, Route, Routes } = await import("react-router-dom");
  const { AgentWorkspaceRoute } = await import("../AgentWorkspaceRoute.tsx");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { sessionApi } = await import("../../../services/api/session.ts");
  const shellHarness = await prepareShellHarness();
  const agentA = {
    ...enterpriseProfileFields,
    agent_id: "agt_a",
    expected_revision: 1,
    name: "Agent A",
    description: "Agent A published revision.",
    avatar_ref: "builtin:assistant",
    category: "support",
  } as const;
  const agentB = {
    ...enterpriseProfileFields,
    agent_id: "agt_b",
    expected_revision: 2,
    name: "Agent B",
    description: "Agent B published revision.",
    avatar_ref: "builtin:assistant",
    category: "support",
  } as const;
  let rejectFirstBProfile!: (reason?: unknown) => void;
  const firstBProfile = new Promise<typeof agentB>((_resolve, reject) => {
    rejectFirstBProfile = reject;
  });
  let resolveSecondBProfile!: (value: typeof agentB) => void;
  const secondBProfile = new Promise<typeof agentB>((resolve) => {
    resolveSecondBProfile = resolve;
  });
  let resolveBSessionBinding!: (value: Record<string, unknown>) => void;
  const bSessionBinding = new Promise<Record<string, unknown>>((resolve) => {
    resolveBSessionBinding = resolve;
  });
  let bRequest = "reject" as "reject" | "bind";
  const originalGetPublished = agentProfileApi.getPublished;
  const originalListConversations = agentProfileApi.listConversations;
  const originalListSessions = sessionApi.list;
  const originalMarkRead = sessionApi.markRead;
  const originalGetSession = sessionApi.get;
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const originalGetEvents = sessionApi.getEvents;
  agentProfileApi.getPublished = ((agentId: string) => {
    if (agentId === agentA.agent_id) return Promise.resolve(agentA);
    return bRequest === "reject" ? firstBProfile : secondBProfile;
  }) as typeof agentProfileApi.getPublished;
  agentProfileApi.listConversations = (async (selection) => {
    const agent = selection.agent_id === agentA.agent_id ? agentA : agentB;
    const suffix = agent === agentA ? "a" : "b";
    return {
      sessions: [
        {
          session_id: `session-${suffix}`,
          workspace_id: "default",
          agent_id: agent.agent_id,
          title: `${agent.name} sidebar session`,
          purpose: "conversation",
          created_at: "2026-07-30T00:00:00Z",
          updated_at: "2026-07-30T00:00:00Z",
          agent_conversation: {
            ...enterpriseProfileFields,
            agent_id: agent.agent_id,
            revision: agent.expected_revision,
            name: agent.name,
            description: agent.description,
            avatar_ref: agent.avatar_ref,
            category: agent.category,
          },
        },
      ],
      next_cursor: null,
    };
  }) as typeof agentProfileApi.listConversations;
  sessionApi.list = (async () => ({
    sessions: [
      {
        id: "session-a",
        name: "Agent A sidebar session",
        agent_id: agentA.agent_id,
        created_at: "2026-07-30T00:00:00Z",
        updated_at: "2026-07-30T00:00:00Z",
        is_active: true,
      },
      {
        id: "session-b",
        name: "Agent B sidebar session",
        agent_id: agentB.agent_id,
        created_at: "2026-07-30T00:00:00Z",
        updated_at: "2026-07-30T00:00:00Z",
        is_active: true,
      },
    ],
    total: 2,
    skip: 0,
    limit: 20,
    has_more: false,
  })) as typeof sessionApi.list;
  sessionApi.markRead = (async () => {}) as typeof sessionApi.markRead;
  sessionApi.get = (async (sessionId: string) => {
    const agent = sessionId === "session-a" ? agentA : agentB;
    return {
      id: sessionId,
      agent_id: agent.agent_id,
      created_at: "2026-07-30T00:00:00Z",
      updated_at: "2026-07-30T00:00:00Z",
      is_active: true,
      metadata: {},
    };
  }) as typeof sessionApi.get;
  sessionApi.getAuthoritative = ((sessionId: string) => {
    const agent = sessionId === "session-a" ? agentA : agentB;
    const value = {
      session_id: sessionId,
      workspace_id: "default",
      agent_id: agent.agent_id,
      title: agent.name,
      purpose: "conversation",
      agent_conversation: {
        ...enterpriseProfileFields,
        agent_id: agent.agent_id,
        revision: agent.expected_revision,
        name: agent.name,
        description: agent.description,
        avatar_ref: agent.avatar_ref,
        category: agent.category,
      },
    };
    return sessionId === "session-b" ? bSessionBinding : Promise.resolve(value);
  }) as typeof sessionApi.getAuthoritative;
  sessionApi.getEvents = (async (sessionId: string) => ({
    events: [
      {
        id: `event-${sessionId}`,
        run_id: `run-${sessionId}`,
        event_type: "user:message",
        timestamp: "2026-07-30T00:00:00Z",
        data: {
          content:
            sessionId === "session-a"
              ? "Agent A transcript node"
              : "Agent B transcript node",
        },
      },
    ],
  })) as typeof sessionApi.getEvents;

  const settle = async () => {
    for (let index = 0; index < 8; index += 1) {
      await Promise.resolve();
    }
  };
  const hasTranscriptForSession = (sessionId: string) =>
    container
      .querySelectorAll("[data-chat-transcript]")
      .some((transcript) => transcript.getAttribute("data-session-id") === sessionId);
  const assertNoAgentAArtifacts = (state: string) => {
    assert.ok(
      container.querySelector("[data-agent-workspace-loading]"),
      `${state} must render the fail-closed workspace state`,
    );
    assert.doesNotMatch(
      container.textContent,
      /Agent A sidebar session/,
      `${state} must not retain Agent A sidebar history`,
    );
    assert.equal(
      hasTranscriptForSession("session-a"),
      false,
      `${state} must not retain an Agent A transcript node`,
    );
    assert.equal(
      container.querySelectorAll("button").some((button) =>
        button.className.split(" ").includes("chat-tool-btn"),
      ),
      false,
      `${state} must not expose a generic tool control`,
    );
  };
  const container = dom.document.createElement("div");
  const root = ReactDOM.createRoot(container as never);
  let routeLocation = {
    pathname: "/agent-market/agt_a/1/chat/session-a",
    search: "",
    hash: "",
    state: null,
    key: "agent-a",
  };
  const routeNavigator = {
    createHref: (to: { pathname?: string; search?: string; hash?: string }) =>
      `${to.pathname ?? ""}${to.search ?? ""}${to.hash ?? ""}`,
    encodeLocation: (to: unknown) => to,
    go: () => {},
    push: () => {},
    replace: () => {},
  };
  const renderWorkspaceRoute = () =>
    React.createElement(
      Router,
      {
        location: routeLocation,
        navigationType: "POP" as never,
        navigator: routeNavigator as never,
      },
      shellHarness.wrap(
        React.createElement(
          Routes,
          null,
          React.createElement(Route, {
            path: "/agent-market/:agentId/:revision/chat/:sessionId?",
            element: React.createElement(AgentWorkspaceRoute),
          }),
        ),
      ),
    );
  const setRoute = (pathname: string) => {
    routeLocation = { pathname, search: "", hash: "", state: null, key: pathname };
    flushSync(() => root.render(renderWorkspaceRoute()));
  };
  try {
    await React.act(async () => {
      root.render(renderWorkspaceRoute());
      await settle();
    });

    assert.match(container.textContent, /Agent A sidebar session/);
    assert.equal(hasTranscriptForSession("session-a"), true);

    setRoute("/agent-market/agt_b/2/chat/session-b");
    assertNoAgentAArtifacts("Agent B loading");

    rejectFirstBProfile(new Error("Agent B rejected"));
    await React.act(async () => {
      await settle();
    });
    assertNoAgentAArtifacts("Agent B rejection");

    setRoute("/agent-market/agt_a/1/chat/session-a");
    await React.act(async () => {
      await settle();
    });
    assert.equal(hasTranscriptForSession("session-a"), true);

    bRequest = "bind";
    setRoute("/agent-market/agt_b/2/chat/session-b");
    assertNoAgentAArtifacts("Agent B revalidation");

    resolveSecondBProfile(agentB);
    await React.act(async () => {
      await settle();
    });
    assert.equal(
      hasTranscriptForSession("session-b"),
      false,
      "Agent B transcript must wait for its authoritative Session binding",
    );

    resolveBSessionBinding({
      session_id: "session-b",
      workspace_id: "default",
      agent_id: agentB.agent_id,
      title: agentB.name,
      purpose: "conversation",
      agent_conversation: {
        ...enterpriseProfileFields,
        agent_id: agentB.agent_id,
        revision: agentB.expected_revision,
        name: agentB.name,
        description: agentB.description,
        avatar_ref: agentB.avatar_ref,
        category: agentB.category,
      },
    });
    await React.act(async () => {
      await settle();
    });

    assert.match(container.textContent, /Agent B sidebar session/);
    assert.equal(hasTranscriptForSession("session-b"), true);
    assert.doesNotMatch(container.textContent, /Agent A sidebar session/);
    assert.equal(hasTranscriptForSession("session-a"), false);
  } finally {
    agentProfileApi.getPublished = originalGetPublished;
    agentProfileApi.listConversations = originalListConversations;
    sessionApi.list = originalListSessions;
    sessionApi.markRead = originalMarkRead;
    sessionApi.get = originalGetSession;
    sessionApi.getAuthoritative = originalGetAuthoritative;
    sessionApi.getEvents = originalGetEvents;
    shellHarness.restore();
    await React.act(async () => root.unmount());
  }
});
