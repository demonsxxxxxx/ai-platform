import assert from "node:assert/strict";
import test from "node:test";

import React from "react";

import type { AgentProfileAdminProjection, PublicSkillResponse } from "../../../types";
import type { AgentBuilderWorkbenchCatalog } from "../AgentBuilderWorkbench";

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
  checked = false;
  disabled = false;
  selected = false;
  defaultSelected = false;
  multiple = false;
  private text = "";

  constructor(readonly tagName: string) {
    super();
  }

  get nodeName() {
    return this.tagName.toUpperCase();
  }

  get options() {
    return this.childNodes.filter(
      (child): child is TestElement =>
        child instanceof TestElement && child.nodeName === "OPTION",
    );
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
    if (name === "disabled") this.disabled = true;
  }

  removeAttribute(name: string) {
    this.attributes.delete(name);
    if (name === "disabled") this.disabled = false;
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
          if (attributeMatch || (tag !== null && child.nodeName === tag)) matches.push(child);
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
    if (tagName.toLowerCase() === "style") element.appendChild(this.createTextNode(""));
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
  const windowTarget = new TestEventTarget() as TestEventTarget & {
    document: TestDocument;
    location: { pathname: string; href: string; search: string; hash: string };
    matchMedia: (query: string) => MediaQueryList;
    requestAnimationFrame: (callback: FrameRequestCallback) => number;
    cancelAnimationFrame: (id: number) => void;
    setTimeout: typeof setTimeout;
    clearTimeout: typeof clearTimeout;
  };
  windowTarget.document = document;
  windowTarget.location = {
    pathname: "/agent-builder",
    href: "http://test.local/agent-builder",
    search: "",
    hash: "",
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
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
  });
  return document;
}

function profile(
  overrides: Partial<AgentProfileAdminProjection> = {},
): AgentProfileAdminProjection {
  return {
    agent_id: "agt_support",
    revision: 4,
    status: "draft",
    name: "支持助手",
    description: "处理授权支持请求。",
    welcome_message: "欢迎使用支持助手。",
    starter_prompts: ["帮我处理支持请求"],
    capability_summary: "在授权范围内处理企业支持请求。",
    recommended_tasks: ["支持请求分流"],
    supported_input_types: ["text", "file"],
    expected_outputs: ["处理建议"],
    permissions_and_data_access_notice: "仅访问当前用户授权的数据。",
    avatar_ref: "builtin:assistant",
    avatar_asset_id: null,
    category: "support",
    visibility: "tenant",
    allowed_department_ids: [],
    allowed_roles: [],
    allowed_user_ids: [],
    instructions: "仅回答公司支持范围内的问题。",
    model_id: "model-id",
    selected_skill: {
      skill_id: "support-skill",
      expected_version: "2026.07.28",
    },
    mcp_tool_ids: ["mcp:support:search"],
    content_hash: "b".repeat(64),
    ...overrides,
  };
}

function publicSkill(): PublicSkillResponse {
  return {
    name: "support-skill",
    expected_version: "2026.07.28",
    input_modes: [],
    requires_file: false,
    description: "Handle support requests.",
    tags: [],
    enabled: true,
    source: "manual",
    files: {},
    file_count: 0,
    installed_from: "manual",
    is_published: false,
    marketplace_is_active: false,
  };
}

function catalog(
  overrides: Partial<AgentBuilderWorkbenchCatalog> = {},
): AgentBuilderWorkbenchCatalog {
  return {
    skills: [publicSkill()],
    tools: [
      {
        id: "mcp:support:search",
        label: "支持知识检索",
        description: "检索授权支持知识。",
      },
    ],
    skillsResolved: true,
    mcpToolsResolved: true,
    effectivePermissionsKnown: true,
    isLoading: false,
    error: null,
    retry: () => {},
    ...overrides,
  };
}

function reactProps(element: TestElement): Record<string, (...args: never[]) => unknown> {
  const key = Object.keys(element).find((name) => name.startsWith("__reactProps$"));
  assert.ok(key, `React props are attached to <${element.tagName}>`);
  return (element as unknown as Record<string, Record<string, (...args: never[]) => unknown>>)[key];
}

function findButton(container: TestElement, text: string): TestElement {
  const button = container.querySelectorAll("button").find((entry) => entry.textContent === text);
  assert.ok(button, `button ${text} should render`);
  return button;
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise<void>((resolve) => setImmediate(resolve));
}

test("mounted workbench hydrates, refreshes, and creates only an explicit local form", async () => {
  const document = installDom();
  const ReactDOM = await import("react-dom/client");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { AgentBuilderWorkbench } = await import("../AgentBuilderWorkbench.tsx");
  const originals = { ...agentProfileApi };
  const responses = [
    [profile()],
    [profile({ revision: 5, name: "支持助手新版" })],
  ];
  let listCalls = 0;
  let catalogRetryCalls = 0;
  agentProfileApi.listAdmin = async () => {
    listCalls += 1;
    return { agent_profiles: responses.shift() ?? [] };
  };
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog({ retry: () => { catalogRetryCalls += 1; } }),
        canManageProfiles: true,
      }));
      await flush();
    });
    assert.equal(listCalls, 1);
    assert.match(container.textContent, /支持助手/);
    const nameInput = container.querySelector('[aria-label="专家名称"]');
    const descriptionInput = container.querySelector('[aria-label="专家简介"]');
    const instructionsInput = container.querySelector('[aria-label="Agent.md 初始指令"]');
    assert.equal(nameInput?.value, "支持助手");
    assert.ok(descriptionInput);
    assert.ok(instructionsInput);
    assert.equal((reactProps(descriptionInput) as unknown as { value: string }).value, "处理授权支持请求。");
    assert.equal((reactProps(instructionsInput) as unknown as { value: string }).value, "仅回答公司支持范围内的问题。");
    assert.equal(container.querySelector('[aria-label="专家模型"]'), null);
    assert.match(container.textContent, /support-skill/);
    assert.match(container.textContent, /support-skill2026\.07\.28/);
    assert.match(container.textContent, /支持知识检索/);
    assert.match(container.textContent, /revision 4/);

    const refreshButton = container.querySelector('[aria-label="刷新专家与授权目录"]');
    assert.ok(refreshButton);
    await React.act(async () => {
      await reactProps(refreshButton).onClick?.({} as never);
      await flush();
    });
    assert.equal(listCalls, 2);
    assert.equal(catalogRetryCalls, 1);
    assert.equal(container.querySelector('[aria-label="专家名称"]')?.value, "支持助手新版");
    assert.match(container.textContent, /revision 5/);

    await React.act(async () => {
      await reactProps(findButton(container, "新建专家")).onClick?.({} as never);
      await Promise.resolve();
    });
    assert.equal(container.querySelector('[aria-label="专家名称"]')?.value, "");
    assert.match(container.textContent, /本地未保存/);
    assert.equal(container.textContent.includes("local-draft-1"), false);
    assert.equal(container.textContent.includes("local-draft-2"), false);
  } finally {
    Object.assign(agentProfileApi, originals);
    await React.act(async () => root.unmount());
  }
});

test("mounted list fields preserve separators while editing and normalize on blur", async () => {
  const document = installDom();
  const ReactDOM = await import("react-dom/client");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { AgentBuilderWorkbench } = await import("../AgentBuilderWorkbench.tsx");
  const originals = { ...agentProfileApi };
  agentProfileApi.listAdmin = async () => ({ agent_profiles: [profile()] });
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog(),
        canManageProfiles: true,
      }));
      await flush();
    });

    const recommendedTasks = container.querySelector('[aria-label="推荐任务（可选）"]');
    assert.ok(recommendedTasks);
    await React.act(async () => {
      reactProps(recommendedTasks).onFocus?.({ target: recommendedTasks } as never);
      recommendedTasks.value = "任务一,任务二";
      reactProps(recommendedTasks).onChange?.({ target: recommendedTasks } as never);
      await Promise.resolve();
    });
    assert.equal(recommendedTasks.value, "任务一,任务二");

    await React.act(async () => {
      reactProps(recommendedTasks).onBlur?.({ target: recommendedTasks } as never);
      await Promise.resolve();
    });
    assert.equal(recommendedTasks.value, "任务一\n任务二");
  } finally {
    Object.assign(agentProfileApi, originals);
    await React.act(async () => root.unmount());
  }
});

test("mounted edit disables publish until save materializes a revision, then adopts publish status", async () => {
  const document = installDom();
  const ReactDOM = await import("react-dom/client");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { AgentBuilderWorkbench } = await import("../AgentBuilderWorkbench.tsx");
  const originals = { ...agentProfileApi };
  const saveCalls: unknown[][] = [];
  const publishCalls: unknown[][] = [];
  agentProfileApi.listAdmin = async () => ({ agent_profiles: [profile()] });
  agentProfileApi.saveDraft = async (...args) => {
    saveCalls.push(args);
    return {
      agent_profile: profile({ revision: 5, name: "支持助手已编辑" }),
      audit_id: "audit-save",
    };
  };
  agentProfileApi.publish = async (...args) => {
    publishCalls.push(args);
    return {
      agent_profile: profile({ revision: 6, status: "published", name: "支持助手已编辑" }),
      audit_id: "audit-publish",
    };
  };
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog(),
        canManageProfiles: true,
      }));
      await flush();
    });
    const nameInput = container.querySelector('[aria-label="专家名称"]');
    assert.ok(nameInput);
    await React.act(async () => {
      nameInput.value = "支持助手已编辑";
      reactProps(nameInput).onChange?.({ target: nameInput } as never);
      await Promise.resolve();
    });
    const publishBeforeSave = findButton(container, "发布");
    assert.equal(publishBeforeSave.disabled || publishBeforeSave.hasAttribute("disabled"), true);
    assert.match(container.textContent, /有未保存的更改/);

    const saveButton = findButton(container, "保存草稿");
    assert.equal(saveButton.disabled || saveButton.hasAttribute("disabled"), false);
    await React.act(async () => {
      await reactProps(saveButton).onClick?.({} as never);
      await flush();
    });
    assert.equal(saveCalls.length, 1);
    assert.equal((saveCalls[0][0] as { expected_draft_revision: number }).expected_draft_revision, 4);
    assert.equal(saveCalls[0][1], "agt_support");
    assert.match(container.textContent, /revision 5/);
    assert.match(container.textContent, /草稿已保存为服务端 revision 5/);
    const publishAfterSave = findButton(container, "发布");
    assert.equal(publishAfterSave.disabled || publishAfterSave.hasAttribute("disabled"), false);

    await React.act(async () => {
      await reactProps(publishAfterSave).onClick?.({} as never);
      await flush();
    });
    assert.deepEqual(publishCalls, [["agt_support", 5]]);
    assert.match(container.textContent, /revision 6/);
    assert.match(container.textContent, /已发布/);
    assert.match(container.textContent, /发布成功，当前服务端 revision 为 6/);
    const publishedButton = findButton(container, "发布");
    assert.equal(publishedButton.disabled || publishedButton.hasAttribute("disabled"), true);
  } finally {
    Object.assign(agentProfileApi, originals);
    await React.act(async () => root.unmount());
  }
});

test("mounted dirty editor requires confirmation before switching profiles", async () => {
  const document = installDom();
  const ReactDOM = await import("react-dom/client");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { AgentBuilderWorkbench } = await import("../AgentBuilderWorkbench.tsx");
  const originals = { ...agentProfileApi };
  agentProfileApi.listAdmin = async () => ({
    agent_profiles: [
      profile(),
      profile({ agent_id: "agt_other", name: "其他助手" }),
    ],
  });
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog(),
        canManageProfiles: true,
      }));
      await flush();
    });
    const nameInput = container.querySelector('[aria-label="专家名称"]');
    const otherProfile = container.querySelector('[aria-label="编辑专家 其他助手，草稿，revision 4"]');
    assert.ok(nameInput);
    assert.ok(otherProfile);
    await React.act(async () => {
      nameInput.value = "未保存名称";
      reactProps(nameInput).onChange?.({ target: nameInput } as never);
      await Promise.resolve();
    });
    await React.act(async () => {
      await reactProps(otherProfile).onClick?.({} as never);
      await Promise.resolve();
    });
    assert.match(document.body.textContent, /放弃未保存更改/);
    assert.equal(container.querySelector('[aria-label="专家名称"]')?.value, "未保存名称");

    await React.act(async () => {
      await reactProps(findButton(document.body, "继续编辑")).onClick?.({} as never);
      await Promise.resolve();
    });
    assert.doesNotMatch(document.body.textContent, /放弃未保存更改/);
    assert.equal(container.querySelector('[aria-label="专家名称"]')?.value, "未保存名称");

    const otherProfileAfterCancel = container.querySelector('[aria-label="编辑专家 其他助手，草稿，revision 4"]');
    assert.ok(otherProfileAfterCancel);
    await React.act(async () => {
      await reactProps(otherProfileAfterCancel).onClick?.({} as never);
      await Promise.resolve();
    });
    assert.match(document.body.textContent, /放弃未保存更改/);
    await React.act(async () => {
      await reactProps(findButton(document.body, "放弃并切换")).onClick?.({} as never);
      await Promise.resolve();
    });
    assert.equal(container.querySelector('[aria-label="专家名称"]')?.value, "其他助手");
    await React.act(async () => {
      await reactProps(findButton(container, "新建专家")).onClick?.({} as never);
      await Promise.resolve();
    });
    assert.equal(container.querySelector('[aria-label="专家名称"]')?.value, "");
  } finally {
    Object.assign(agentProfileApi, originals);
    await React.act(async () => root.unmount());
  }
});

test("mounted unresolved catalogs preserve server pins without stale or empty claims", async () => {
  const document = installDom();
  const ReactDOM = await import("react-dom/client");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { AgentBuilderWorkbench } = await import("../AgentBuilderWorkbench.tsx");
  const originals = { ...agentProfileApi };
  agentProfileApi.listAdmin = async () => ({ agent_profiles: [profile()] });
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container as never);
  try {
    const unresolved = catalog({
      skills: [],
      tools: [],
      skillsResolved: false,
      mcpToolsResolved: false,
      effectivePermissionsKnown: false,
      isLoading: true,
    });
    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: unresolved,
        canManageProfiles: true,
      }));
      await flush();
    });

    assert.match(container.textContent, /support-skill2026\.07\.28/);
    assert.match(container.textContent, /已保留服务端工具身份/);
    assert.doesNotMatch(container.textContent, /当前不可用|没有这一精确版本|需要明确移除/);

    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog({ skills: [], tools: [] }),
        canManageProfiles: true,
      }));
      await Promise.resolve();
    });
    assert.match(container.textContent, /当前授权目录中不可用/);
    assert.match(container.textContent, /需要明确移除/);
    const saveButton = findButton(container, "保存草稿");
    const publishButton = findButton(container, "发布");
    assert.equal(saveButton.disabled || saveButton.hasAttribute("disabled"), true);
    assert.equal(publishButton.disabled || publishButton.hasAttribute("disabled"), true);
    const removeTool = container.querySelector('[aria-label="移除 MCP 工具 mcp:support:search"]');
    assert.ok(removeTool);
    await React.act(async () => {
      await reactProps(removeTool).onClick?.({} as never);
      await Promise.resolve();
    });
    assert.doesNotMatch(container.textContent, /需要明确移除/);
  } finally {
    Object.assign(agentProfileApi, originals);
    await React.act(async () => root.unmount());
  }
});

test("mounted save conflict is safe, explicitly recoverable, and retryable", async () => {
  const document = installDom();
  const ReactDOM = await import("react-dom/client");
  const { ApiRequestError } = await import("../../../services/api/fetch.ts");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { AgentBuilderWorkbench } = await import("../AgentBuilderWorkbench.tsx");
  const originals = { ...agentProfileApi };
  let saveAttempts = 0;
  let listCalls = 0;
  agentProfileApi.listAdmin = async () => {
    listCalls += 1;
    return {
      agent_profiles: [listCalls === 1
        ? profile()
        : profile({ revision: 5, name: "服务端最新名称" })],
    };
  };
  agentProfileApi.saveDraft = async (draft) => {
    saveAttempts += 1;
    if (saveAttempts === 1) {
      throw new ApiRequestError(
        "raw database and private payload",
        409,
        "agent_profile_revision_stale",
      );
    }
    return {
      agent_profile: profile({ revision: 6, name: draft.name }),
      audit_id: "audit-save-retry",
    };
  };
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog(),
        canManageProfiles: true,
      }));
      await flush();
    });
    const nameInput = container.querySelector('[aria-label="专家名称"]');
    assert.ok(nameInput);
    await React.act(async () => {
      nameInput.value = "重试后的名称";
      reactProps(nameInput).onChange?.({ target: nameInput } as never);
      await Promise.resolve();
    });

    await React.act(async () => {
      await reactProps(findButton(container, "保存草稿")).onClick?.({} as never);
      await flush();
    });
    assert.equal(saveAttempts, 1);
    assert.match(container.textContent, /HTTP 409/);
    assert.match(container.textContent, /agent_profile_revision_stale/);
    assert.doesNotMatch(container.textContent, /raw database|private payload/);
    assert.equal(container.querySelector('[aria-label="专家名称"]')?.value, "重试后的名称");
    assert.match(container.textContent, /revision 4/);

    await React.act(async () => {
      await reactProps(findButton(container, "加载服务端版本")).onClick?.({} as never);
      await Promise.resolve();
    });
    assert.match(document.body.textContent, /放弃本地更改并刷新/);
    await React.act(async () => {
      await reactProps(findButton(document.body, "放弃本地更改并加载服务端版本")).onClick?.({} as never);
      await flush();
    });
    assert.equal(listCalls, 2);
    assert.equal(container.querySelector('[aria-label="专家名称"]')?.value, "服务端最新名称");
    assert.match(container.textContent, /revision 5/);
    assert.doesNotMatch(container.textContent, /agent_profile_revision_stale/);

    const recoveredNameInput = container.querySelector('[aria-label="专家名称"]');
    assert.ok(recoveredNameInput);
    await React.act(async () => {
      recoveredNameInput.value = "恢复后再次编辑";
      reactProps(recoveredNameInput).onChange?.({ target: recoveredNameInput } as never);
      await reactProps(findButton(container, "保存草稿")).onClick?.({} as never);
      await flush();
    });
    assert.equal(saveAttempts, 2);
    assert.match(container.textContent, /草稿已保存为服务端 revision 6/);
  } finally {
    Object.assign(agentProfileApi, originals);
    await React.act(async () => root.unmount());
  }
});

test("mounted loading, empty, error, and non-admin states are explicit and safe", async () => {
  const document = installDom();
  const ReactDOM = await import("react-dom/client");
  const { ApiRequestError } = await import("../../../services/api/fetch.ts");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { AgentBuilderWorkbench } = await import("../AgentBuilderWorkbench.tsx");
  const originals = { ...agentProfileApi };
  let resolveList: ((value: { agent_profiles: AgentProfileAdminProjection[] }) => void) | undefined;
  let mode: "pending" | "error" = "pending";
  let calls = 0;
  agentProfileApi.listAdmin = () => {
    calls += 1;
    if (mode === "error") {
      return Promise.reject(new ApiRequestError("raw private payload", 409, "agent_profile_revision_stale"));
    }
    return new Promise((resolve) => {
      resolveList = resolve;
    });
  };
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog(),
        canManageProfiles: true,
      }));
      await Promise.resolve();
    });
    assert.match(container.textContent, /正在加载专家/);
    await React.act(async () => {
      resolveList?.({ agent_profiles: [] });
      await flush();
    });
    assert.match(container.textContent, /当前没有服务端专家/);

    mode = "error";
    const refreshButton = container.querySelector('[aria-label="刷新专家与授权目录"]');
    assert.ok(refreshButton);
    await React.act(async () => {
      await reactProps(refreshButton).onClick?.({} as never);
      await flush();
    });
    assert.equal(calls, 2);
    assert.match(container.textContent, /HTTP 409/);
    assert.match(container.textContent, /agent_profile_revision_stale/);
    assert.doesNotMatch(container.textContent, /raw private payload/);

    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog(),
        canManageProfiles: false,
      }));
      await Promise.resolve();
    });
    assert.ok(container.querySelector("[data-agent-builder-access-denied]"));
    assert.match(container.textContent, /仅管理员可访问/);
  } finally {
    Object.assign(agentProfileApi, originals);
    await React.act(async () => root.unmount());
  }
});
