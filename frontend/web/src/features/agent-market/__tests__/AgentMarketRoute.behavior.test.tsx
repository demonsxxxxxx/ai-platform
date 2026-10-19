import assert from "node:assert/strict";
import { register } from "node:module";
import test, { after } from "node:test";
import { installSelectTestDom } from "../../../components/common/__tests__/selectTestDom";

import React from "react";

import { Permission } from "../../../types/auth.ts";
import type { AgentProfilePublicProjection } from "../../../types/agentProfile.ts";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb.ts";

const enterpriseProfileFields = {
  starter_prompts: ["帮我处理企业任务"] as string[],
  avatar_seed: "enterprise-profile",
  market_tags: ["企业服务"] as string[],
  is_favorite: false,
  published_at: "2026-08-04T01:00:00Z",
};

register(new URL("./frontendAssetLoader.mjs", import.meta.url), import.meta.url);
await new Promise<void>((resolve) => setImmediate(resolve));

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

const dom = installSelectTestDom();
Object.assign(globalThis, { localStorage: window.localStorage, sessionStorage: window.sessionStorage, CompositionEvent: window.CompositionEvent });
Object.assign(window, { innerWidth: 1200, innerHeight: 800, scrollTo() {} });
after(() => dom.close());

function installDom() {
  document.body.replaceChildren();
  localStorage.clear();
  sessionStorage.clear();
  Object.defineProperty(navigator, "locks", { configurable: true, value: new TestLockManager() });
  installBrowserAuthTestDb();
  return { document, window };
}

function setInputValue(field: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(field, value);
}

async function prepareShellHarness({ authenticated = false } = {}) {
  await import("../../../i18n/index.ts");
  const { AuthProvider, useAuth } = await import("../../../hooks/useAuth.tsx");
  const { ModelCatalogProvider } = await import("../../../contexts/ModelCatalogContext.tsx");
  const { ThemeProvider } = await import("../../../contexts/ThemeContext.tsx");
  const { LobeUiProvider } = await import("../../../contexts/LobeUiProvider");
  const { VirtuosoMockContext } = await import("react-virtuoso");
  const { authApi } = await import("../../../services/api/auth.ts");
  const { sessionApi } = await import("../../../services/api/session.ts");
  const { modelPublicApi } = await import("../../../services/api/modelPublic.ts");
  const originals = {
    bootstrapAuthContext: authApi.bootstrapAuthContext,
    getCurrentUser: authApi.getCurrentUser,
    listSessions: sessionApi.list,
    listAvailableModels: modelPublicApi.listAvailable,
  };
  authApi.bootstrapAuthContext = async (request) => ({
    status: "ready",
    protocol_version: 2,
    generation: request.generation,
  });
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
  sessionApi.list = async () => ({
    sessions: [],
    total: 0,
    skip: 0,
    limit: 20,
    has_more: false,
  });
  modelPublicApi.listAvailable = async () => ({
    models: [{
      id: "model-default",
      value: "model-default",
      label: "默认模型",
    }],
    count: 1,
    enabled_count: 1,
    default_model_id: "model-default",
  });

  function AuthenticatedGate({ children }: { children: React.ReactNode }) {
    return useAuth().hasPermission(Permission.CHAT_WRITE) ? children : null;
  }

  return {
    wrap(children: React.ReactNode) {
      const shellChildren = authenticated
        ? React.createElement(AuthenticatedGate, null, children)
        : children;
      return React.createElement(ThemeProvider, null,
        React.createElement(LobeUiProvider, null,
          React.createElement(AuthProvider, null,
            React.createElement(ModelCatalogProvider, null,
              React.createElement(VirtuosoMockContext.Provider, { value: { viewportHeight: 600, itemHeight: 100 } }, shellChildren)))));
    },
    restore() {
      authApi.bootstrapAuthContext = originals.bootstrapAuthContext;
      authApi.getCurrentUser = originals.getCurrentUser;
      sessionApi.list = originals.listSessions;
      modelPublicApi.listAvailable = originals.listAvailableModels;
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
    name: `支持助手 ${index + 1}`,
    description: "处理支持请求。",
    market_tags: [`标签 ${index + 1}`],
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

  const container = dom.document.body.appendChild(dom.document.createElement("div"));
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

    assert.equal(container.querySelectorAll("[data-agent-market-card]").length, 10);
    const moreTags = container.querySelector("details");
    assert.ok(moreTags, "additional market tags stay reachable without a sidebar");
    const eighthTag = Array.from(moreTags.querySelectorAll("button"))
      .find((button) => (button.textContent ?? "")?.includes("标签 8"));
    assert.ok(eighthTag);
    await React.act(async () => {
      eighthTag.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      await Promise.resolve();
    });
    assert.match(currentPath, /tag=%E6%A0%87%E7%AD%BE\+8/);
    const allTags = Array.from(container.querySelector("[data-agent-market-filter]")?.querySelectorAll("button") ?? [])
      .find((button) => (button.textContent ?? "")?.trim() === "全部");
    assert.ok(allTags);
    await React.act(async () => {
      allTags.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      await Promise.resolve();
    });
    assert.equal(currentPath, "/agent-market");
    assert.equal(container.querySelectorAll("[data-agent-market-card]").length, 10);

    const search = container.querySelector<HTMLInputElement>("[data-agent-market-search]");
    assert.ok(search);
    await React.act(async () => {
      setInputValue(search as HTMLInputElement, "f");
      search.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
      setInputValue(search as HTMLInputElement, "fa");
      search.dispatchEvent(new Event("input", { bubbles: true }));
      await Promise.resolve();
    });
    assert.equal(currentPath, "/agent-market");
    assert.equal(search.value, "fa");

    await React.act(async () => {
      setInputValue(search as HTMLInputElement, "法");
      search.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true, data: "法" }));
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
      name: "支持助手",
      description: "已发布的支持服务。",
      avatar_ref: "builtin:assistant",
      category: "support",
      market_tags: ["客户服务"],
    },
    {
      ...enterpriseProfileFields,
      agent_id: "agt_finance",
      name: "财务助手",
      description: "核对报销材料。",
      avatar_ref: "builtin:document",
      category: "operations",
      market_tags: ["财务"],
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

  const container = dom.document.body.appendChild(dom.document.createElement("div"));
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
                  path: "/agent-market/:agentId",
                  element: React.createElement(AgentMarketRoute),
                }),
                React.createElement(Route, {
                  path: "/agent-market/:agentId/chat/:sessionId?",
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
      Array.from(categoryGroup.querySelectorAll("button")).find((button) => (button.textContent ?? "")?.includes("财务"))?.getAttribute(
        "aria-pressed",
      ),
      "true",
    );
    assert.equal(
      Array.from(categoryGroup.querySelectorAll("button")).find((button) => (button.textContent ?? "") === "全部")?.getAttribute(
        "aria-pressed",
      ),
      "false",
    );
    assert.equal(container.querySelectorAll("[data-agent-market-card]").length, 1);
    const customerServiceTag = Array.from(categoryGroup
      .querySelectorAll("button"))
      .find((button) => (button.textContent ?? "")?.includes("客户服务"));
    assert.ok(customerServiceTag);
    await React.act(async () => {
      customerServiceTag.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(
      currentPath,
      "/agent-market?q=%E8%B4%A2%E5%8A%A1&tag=%E8%B4%A2%E5%8A%A1&tag=%E5%AE%A2%E6%88%B7%E6%9C%8D%E5%8A%A1",
    );
    assert.equal(
      Array.from(categoryGroup.querySelectorAll("button")).find((button) => (button.textContent ?? "")?.includes("财务"))?.getAttribute(
        "aria-pressed",
      ),
      "true",
    );
    assert.equal(customerServiceTag.getAttribute("aria-pressed"), "true");
    assert.equal(container.querySelectorAll("[data-agent-market-card]").length, 1);
    await React.act(async () => {
      customerServiceTag.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(currentPath, "/agent-market?q=%E8%B4%A2%E5%8A%A1&tag=%E8%B4%A2%E5%8A%A1");
    assert.equal(customerServiceTag.getAttribute("aria-pressed"), "false");
    const gridView = container.querySelector('[aria-label="网格视图"]');
    const listView = container.querySelector('[aria-label="列表视图"]');
    assert.ok(gridView);
    assert.ok(listView);
    assert.equal(gridView.getAttribute("aria-pressed"), "true");
    assert.equal(listView.getAttribute("aria-pressed"), "false");
    await React.act(async () => {
      listView.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      await Promise.resolve();
    });
    assert.equal(listView.getAttribute("aria-pressed"), "true");
    assert.equal(gridView.getAttribute("aria-pressed"), "false");
    assert.doesNotMatch(container.textContent ?? "", /企业已发布/);
    const marketCardClassName = container.querySelector("[data-agent-market-card]")?.getAttribute("class") ?? "";
    assert.match(marketCardClassName, /flex-col/);
    assert.match(marketCardClassName, /sm:flex-row/);
    await React.act(async () => {
      gridView.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      await Promise.resolve();
    });
    assert.equal(gridView.getAttribute("aria-pressed"), "true");
    assert.equal(listView.getAttribute("aria-pressed"), "false");
    assert.equal(container.querySelectorAll("[data-agent-market-card]").length, 1);
    assert.match(container.querySelector("[data-agent-market-catalog]")?.className ?? "", /\bgrid\b/);
    const sortGroup = container.querySelector('[data-agent-market-sort]');
    assert.ok(sortGroup);
    assert.equal(sortGroup.querySelectorAll("button")[0].textContent, "我的收藏");
    const taskSort = Array.from(sortGroup
      .querySelectorAll("button"))
      .find((button) => (button.textContent ?? "") === "完成任务最多");
    assert.ok(taskSort);
    await React.act(async () => {
      taskSort.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      await Promise.resolve();
    });
    assert.equal(taskSort.getAttribute("aria-pressed"), "true");
    assert.equal(catalogRequest, undefined);

    const favoritesTab = Array.from(container
      .querySelectorAll("button"))
      .find((button) => (button.textContent ?? "") === "我的收藏");
    assert.ok(favoritesTab);
    await React.act(async () => {
      favoritesTab.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(currentPath, "/agent-market?q=%E8%B4%A2%E5%8A%A1&tab=favorites");
    assert.equal(container.querySelectorAll("[data-agent-market-card]").length, 0);
    assert.match((container.textContent ?? ""), /尚未收藏专家/);

    const tagsTab = container.querySelector("[data-agent-market-favorites]");
    assert.ok(tagsTab);
    await React.act(async () => {
      tagsTab.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(currentPath, "/agent-market?q=%E8%B4%A2%E5%8A%A1");
    assert.equal(container.querySelectorAll("[data-agent-market-card]").length, 1);
    assert.ok(container.querySelector("[data-agent-market]"));

    const search = container.querySelector<HTMLInputElement>("[data-agent-market-search]");
    assert.ok(search);
    assert.equal(search.value, "财务");
    assert.equal(container.querySelectorAll("[data-agent-market-card]").length, 1);
    assert.match((container.textContent ?? ""), /财务助手/);
    assert.match((container.textContent ?? ""), /财务/);
    assert.match((container.textContent ?? ""), /已完成任务/);
    assert.match((container.textContent ?? ""), /7/);
    assert.doesNotMatch((container.textContent ?? ""), /支持助手/);

    const primaryAction = Array.from(container
      .querySelectorAll("button"))
      .find((button) => button.getAttribute("aria-label") === "使用 财务助手 开始合作");
    assert.ok(primaryAction, "filtered published card should open its dedicated workspace");
    assert.equal(primaryAction.nodeName, "BUTTON", "native button semantics preserve keyboard activation");
    assert.equal(primaryAction.getAttribute("type"), "button");
    await React.act(async () => {
      primaryAction.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.equal(currentPath, "/agent-market/agt_finance/chat");
    assert.ok(container.querySelector("[data-agent-workspace]"));
    assert.deepEqual(conversationSelections, []);
    assert.equal(detailCalls, 0, "opening a card must not detour through the detail authorization request");

    const returnToMarket = container.querySelector("[data-agent-workspace]");
    assert.ok(returnToMarket);
    await React.act(async () => {
      returnToMarket.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    const detailAction = Array.from(container
      .querySelectorAll("button"))
      .find((button) => button.getAttribute("aria-label") === "查看 财务助手 详情");
    assert.ok(detailAction, "detail remains an explicit secondary action");
    await React.act(async () => {
      detailAction.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    assert.equal(
      currentPath,
      "/agent-market/agt_finance?q=%E8%B4%A2%E5%8A%A1&tag=%E8%B4%A2%E5%8A%A1",
    );
    assert.ok(container.querySelector("[data-agent-market-detail]"));
    assert.match((container.textContent ?? ""), /核对报销材料/);
    assert.doesNotMatch((container.textContent ?? ""), /企业已发布/);
    assert.doesNotMatch((container.textContent ?? ""), /版本 2/);
    assert.match((container.textContent ?? ""), /已发布的 Skill Set/);
    assert.doesNotMatch(
      (container.textContent ?? ""),
      /PRIVATE_PROMPT|private-model|private-mcp|private-skill|private-version/,
    );

    const startChat = Array.from(container
      .querySelectorAll("button"))
      .find((button) => button.hasAttribute("data-agent-market-start-chat"));
    assert.ok(startChat, "detail must expose an explicit workspace command");
    await React.act(async () => {
      startChat.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.deepEqual(conversationSelections, []);
    assert.equal(currentPath, "/agent-market/agt_finance/chat");
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
        revision: 4,
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
      revision: 4,
      name: profile.name,
      description: profile.description,
      avatar_ref: profile.avatar_ref,
      category: profile.category,
    },
  });
  sessionApi.getEvents = async () => ({ events: [] });
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

  const container = dom.document.body.appendChild(dom.document.createElement("div"));
  const root = ReactDOM.createRoot(container as never);
  async function waitUntil(predicate: () => boolean) {
    for (let attempt = 0; attempt < 40 && !predicate(); attempt += 1) {
      await React.act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    }
  }
  async function draftStarterPrompt(expectedCount: number) {
    const starterPrompt = Array.from(container
      .querySelectorAll("button"))
      .find((button) => (button.textContent ?? "").includes("帮我处理企业任务"));
    assert.ok(starterPrompt);
    assert.equal(starterPrompt.hasAttribute("disabled"), false);
    await React.act(async () => {
      starterPrompt.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
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
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    await waitUntil(
      () => selections.length === expectedCount && submissions.length === expectedCount,
    );

    assert.equal(selections.length, expectedCount);
    assert.equal(
      submissions.length,
      expectedCount,
      `explicit submit must reach submitChat; rendered UI: ${(container.textContent ?? "")}`,
    );
    assert.equal(submissions[expectedCount - 1]?.[0], "帮我处理企业任务");
    assert.equal(submissions[expectedCount - 1]?.[1], expectedSessionId);
    assert.deepEqual(submissions[expectedCount - 1]?.[10], {
      agent_id: profile.agent_id,
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
                  path: "/agent-market/:agentId/chat/:sessionId?",
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
    const marketStart = Array.from(container
      .querySelectorAll("button"))
      .find((button) => button.getAttribute("aria-label") === "使用 支持助手 开始合作");
    assert.ok(marketStart);
    await React.act(async () => {
      marketStart.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    await waitUntil(() => container.querySelector("[data-agent-chat-opening]") !== null);

    assert.equal(currentPath, "/agent-market/agt_support/chat");
    assert.deepEqual(selections, []);
    assert.match((container.textContent ?? ""), /处理企业内部支持请求/);
    assert.ok(container.querySelector("[data-agent-starter-prompts]"));
    assert.ok(container.querySelector("textarea"));

    await draftStarterPrompt(0);
    assert.equal(currentPath, "/agent-market/agt_support/chat");
    await submitDraft(1, "session-support-1");
    assert.equal(currentPath, "/agent-market/agt_support/chat");

    const startNewTask = Array.from(container
      .querySelectorAll("button"))
      .find((button) => button.getAttribute("aria-label") === "开始新任务");
    assert.ok(startNewTask);
    await React.act(async () => {
      startNewTask.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    await waitUntil(
      () =>
        currentPath === "/agent-market/agt_support/chat" &&
        container.querySelector("[data-agent-starter-prompts]") !== null,
    );

    assert.equal(currentPath, "/agent-market/agt_support/chat");
    assert.equal(selections.length, 1, "Start New Task must remain creation-free");
    assert.equal(submissions.length, 1);

    await draftStarterPrompt(1);
    assert.equal(currentPath, "/agent-market/agt_support/chat");
    await submitDraft(2, "session-support-2");
    assert.equal(currentPath, "/agent-market/agt_support/chat");
    assert.deepEqual(
      selections.map(({ selection }) => selection),
      [
        { agent_id: profile.agent_id },
        { agent_id: profile.agent_id },
      ],
    );
    const operationIds = selections.map(({ operationId }) => operationId);
    assert.equal(new Set(operationIds).size, 2);
  } finally {
    agentProfileApi.listPublished = originalListPublished;
    agentProfileApi.getPublished = originalGetPublished;
    agentProfileApi.listConversations = originalListConversations;
    agentProfileApi.createConversation = originalCreateConversation;
    sessionApi.get = originalGet;
    sessionApi.getAuthoritative = originalGetAuthoritative;
    sessionApi.getEvents = originalGetEvents;
    sessionApi.submitChat = originalSubmitChat;
    shellHarness.restore();
    await React.act(async () => root.unmount());
  }
});

test("a prior-publication conversation stays writable while the current Agent is published", async () => {
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
  const originalGet = sessionApi.get;
  const originalGetAuthoritative = sessionApi.getAuthoritative;
  const originalGetEvents = sessionApi.getEvents;
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
  sessionApi.get = async () => ({
    id: "session-v4",
    agent_id: "agt_support",
    created_at: "2026-08-03T01:00:00Z",
    updated_at: "2026-08-04T01:00:00Z",
    is_active: true,
    metadata: {},
  });
  sessionApi.getEvents = async () => ({ events: [] });
  let currentPath = "";
  function LocationProbe() {
    currentPath = useLocation().pathname;
    return null;
  }

  const container = dom.document.body.appendChild(dom.document.createElement("div"));
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/agent-market/agt_support/chat/session-v4"] },
          shellHarness.wrap(
            React.createElement(
              React.Fragment,
              null,
              React.createElement(LocationProbe),
              React.createElement(
                Routes,
                null,
                React.createElement(Route, {
                  path: "/agent-market/:agentId/chat/:sessionId?",
                  element: React.createElement(AgentWorkspaceRoute),
                }),
              ),
            ),
          ),
        ),
      );
      for (let index = 0; index < 12; index += 1) await Promise.resolve();
    });

    assert.equal(currentPath, "/agent-market/agt_support/chat/session-v4");
    assert.deepEqual(historySelections, [{ agent_id: "agt_support" }]);
    assert.deepEqual(conversationSelections, []);
    assert.match((container.textContent ?? ""), /支持助手 V4/);
    assert.doesNotMatch((container.textContent ?? ""), /支持助手 V5/);
    const composer = container.querySelector("textarea");
    assert.ok(composer, "the prior publication keeps its transcript composer frame");
    assert.equal(container.querySelector("[data-agent-workspace-readonly]"), null);
    assert.notEqual(composer.getAttribute("placeholder"), "该历史会话为只读状态");
  } finally {
    agentProfileApi.getPublished = originalGetPublished;
    agentProfileApi.listConversations = originalListConversations;
    agentProfileApi.createConversation = originalCreateConversation;
    sessionApi.get = originalGet;
    sessionApi.getAuthoritative = originalGetAuthoritative;
    sessionApi.getEvents = originalGetEvents;
    shellHarness.restore();
    await React.act(async () => root.unmount());
  }
});

test("a withdrawn or retired Agent direct link stays read-only without navigation history", async () => {
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
      sessions: [],
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

  const container = dom.document.body.appendChild(dom.document.createElement("div"));
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/agent-market/agt_support/chat/session-v4"] },
          shellHarness.wrap(
            React.createElement(
              React.Fragment,
              null,
              React.createElement(LocationProbe),
              React.createElement(
                Routes,
                null,
                React.createElement(Route, {
                  path: "/agent-market/:agentId/chat/:sessionId?",
                  element: React.createElement(AgentWorkspaceRoute),
                }),
              ),
            ),
          ),
        ),
      );
      for (let index = 0; index < 12; index += 1) await Promise.resolve();
    });

    assert.equal(currentPath, "/agent-market/agt_support/chat/session-v4");
    assert.deepEqual(historySelections, [{ agent_id: "agt_support" }]);
    assert.deepEqual(conversationSelections, []);
    assert.match((container.textContent ?? ""), /已下架支持助手 V4/);
    assert.doesNotMatch((container.textContent ?? ""), /V4 历史会话/);
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

test("an Agent-id detail URL resolves the current published profile", async () => {
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
  const container = dom.document.body.appendChild(dom.document.createElement("div"));
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(
        React.createElement(
          MemoryRouter,
          { initialEntries: ["/agent-market/agt_support"] },
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
                  path: "/agent-market/:agentId",
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

    assert.equal(currentPath, "/agent-market/agt_support");
    assert.ok(container.querySelector("[data-agent-market-detail]"));
    assert.match((container.textContent ?? ""), /支持助手/);
    assert.match((container.textContent ?? ""), /当前发布版本/);
    assert.equal(container.querySelector("[data-workbench-header]"), null);
    assert.ok(container.querySelector("[data-librechat-desktop-sidebar]"));
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
    name: "Agent A",
    description: "Agent A published revision.",
    avatar_ref: "builtin:assistant",
    category: "support",
  } as const;
  const agentB = {
    ...enterpriseProfileFields,
    agent_id: "agt_b",
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
            revision: agent.agent_id === "agt_a" ? 1 : 2,
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
        revision: agent.agent_id === "agt_a" ? 1 : 2,
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
    Array.from(container
      .querySelectorAll("[data-chat-transcript]"))
      .some((transcript) => transcript.getAttribute("data-session-id") === sessionId);
  const assertNoAgentAArtifacts = (state: string) => {
    assert.ok(
      container.querySelector("[data-agent-workspace-loading]"),
      `${state} must render the fail-closed workspace state`,
    );
    assert.doesNotMatch(
      (container.textContent ?? ""),
      /Agent A sidebar session/,
      `${state} must not retain Agent A sidebar history`,
    );
    assert.equal(
      hasTranscriptForSession("session-a"),
      false,
      `${state} must not retain an Agent A transcript node`,
    );
    assert.equal(
      Array.from(container.querySelectorAll("button")).some((button) =>
        button.className.split(" ").includes("chat-tool-btn"),
      ),
      false,
      `${state} must not expose a generic tool control`,
    );
  };
  const container = dom.document.body.appendChild(dom.document.createElement("div"));
  const root = ReactDOM.createRoot(container as never);
  let routeLocation = {
    pathname: "/agent-market/agt_a/chat/session-a",
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
            path: "/agent-market/:agentId/chat/:sessionId?",
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

    assert.match((container.textContent ?? ""), /Agent A sidebar session/);
    assert.equal(hasTranscriptForSession("session-a"), true);

    setRoute("/agent-market/agt_b/chat/session-b");
    assertNoAgentAArtifacts("Agent B loading");

    rejectFirstBProfile(new Error("Agent B rejected"));
    await React.act(async () => {
      await settle();
    });
    assertNoAgentAArtifacts("Agent B rejection");

    setRoute("/agent-market/agt_a/chat/session-a");
    await React.act(async () => {
      await settle();
    });
    assert.equal(hasTranscriptForSession("session-a"), true);

    bRequest = "bind";
    setRoute("/agent-market/agt_b/chat/session-b");
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
        revision: 2,
        name: agentB.name,
        description: agentB.description,
        avatar_ref: agentB.avatar_ref,
        category: agentB.category,
      },
    });
    await React.act(async () => {
      await settle();
    });

    assert.match((container.textContent ?? ""), /Agent B sidebar session/);
    assert.equal(hasTranscriptForSession("session-b"), true);
    assert.doesNotMatch((container.textContent ?? ""), /Agent A sidebar session/);
    assert.equal(hasTranscriptForSession("session-a"), false);
  } finally {
    agentProfileApi.getPublished = originalGetPublished;
    agentProfileApi.listConversations = originalListConversations;
    sessionApi.list = originalListSessions;
    sessionApi.get = originalGetSession;
    sessionApi.getAuthoritative = originalGetAuthoritative;
    sessionApi.getEvents = originalGetEvents;
    shellHarness.restore();
    await React.act(async () => root.unmount());
  }
});


test("main chat exposes event-only history failure and a read-only retry", async () => {
  const dom = installDom();
  const ReactDOM = await import("react-dom/client");
  const { MemoryRouter, Route, Routes, useNavigate } = await import("react-router-dom");
  const { ChatAppContent } = await import("../../../components/layout/AppContent/ChatAppContent.tsx");
  const { sessionApi } = await import("../../../services/api/session.ts");
  const { ApiRequestError } = await import("../../../services/api/fetch.ts");
  const shellHarness = await prepareShellHarness({ authenticated: true });
  const originals = { ...sessionApi };
  const calls = { identity: 0, events: 0, submit: 0 };
  sessionApi.get = async (id) => ({
    id, agent_id: "general-agent", is_active: true,
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", metadata: {},
  });
  sessionApi.getAuthoritative = async (sessionId) => {
    calls.identity += 1;
    return { session_id: sessionId, workspace_id: "default", agent_id: "general-agent", title: "Test conversation", purpose: "conversation", agent_conversation: null };
  };
  sessionApi.submitChat = async () => { calls.submit += 1; throw new Error("Unexpected task submission"); };
  sessionApi.getRunInputHistory = async (sessionId) => ({ session_id: sessionId, runs: [], has_more: false, next_before_run_id: null });
  let readEvents: typeof sessionApi.getEvents = async () => { throw new ApiRequestError("PRIVATE_EVENT_HISTORY_FAILURE", 503, "service_unavailable", undefined, "diag_0123456789abcdef"); };
  sessionApi.getEvents = async (...args) => { calls.events += 1; return readEvents(...args); };
  const container = dom.document.body.appendChild(dom.document.createElement("div"));
  const root = ReactDOM.createRoot(container as never);
  let navigate!: ReturnType<typeof useNavigate>;
  function RouteProbe() { navigate = useNavigate(); return null; }
  const renderChat = () => React.createElement(React.Fragment, null,
    React.createElement(RouteProbe),
    React.createElement(ChatAppContent, { sidebarCollapsed: false, setSidebarCollapsed() {}, mobileSidebarOpen: false, setMobileSidebarOpen() {} }),
  );
  try {
    await React.act(async () => {
      root.render(React.createElement(MemoryRouter, { initialEntries: ["/chat/session-a"] }, shellHarness.wrap(
        React.createElement(Routes, null, React.createElement(Route, { path: "/chat/:sessionId?", element:
          renderChat(),
        })),
      )));
    });
    assert.equal(calls.events, 1);
    assert.ok(calls.identity >= 2, "both hook authority and page identity GETs succeeded");
    const alert = container.querySelector("[data-chat-history-read-error]");
    assert.ok(alert, "events-only failure must have a mounted main-chat UI exit");
    assert.match((alert.textContent ?? ""), /历史消息暂时无法读取/);
    assert.match((alert.textContent ?? ""), /HTTP 503/);
    assert.match((alert.textContent ?? ""), /错误代码: service_unavailable/);
    assert.match((alert.textContent ?? ""), /问题编号: diag_0123456789abcdef/);
    assert.doesNotMatch((container.textContent ?? ""), /PRIVATE_EVENT_HISTORY_FAILURE|已删除/);
    assert.equal(calls.submit, 0);
    const button = alert.querySelector("button");
    assert.ok(button);
    assert.match((button.textContent ?? ""), /重试读取历史/);
    let rejectRetry!: (error: Error) => void;
    readEvents = () => new Promise((_, reject) => { rejectRetry = reject; });
    await React.act(async () => {
      button.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      button.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    assert.equal(calls.events, 2, "duplicate clicks share one bounded read attempt");
    assert.equal(calls.submit, 0, "explicit history retry never submits a task");
    await React.act(async () => rejectRetry(new ApiRequestError("PRIVATE_RETRY_FAILURE", 999, "private/path?token=secret", undefined, "diag_private_secret")));
    const retryAlert = container.querySelector("[data-chat-history-read-error]");
    assert.ok(retryAlert);
    assert.doesNotMatch((retryAlert.textContent ?? ""), /HTTP 999|private|secret|PRIVATE_|diag_|service_unavailable/);
    await React.act(async () => { await Promise.resolve(); });
    assert.equal(calls.events, 2, "failure must not automatically retry");
    readEvents = async () => ({ events: [] });
    await React.act(async () => {
      container.querySelector("[data-chat-history-read-error]")!.querySelector("button")!.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    assert.equal(calls.events, 3);
    assert.equal(calls.submit, 0, "successful history retry stays read-only");
    assert.equal(container.querySelector("[data-chat-history-read-error]"), null, "a genuinely empty successful history is not a read error");

    // A pending failure from another route cannot overwrite this empty owner.
    let rejectOldRoute!: (error: Error) => void;
    readEvents = (sessionId) => sessionId === "session-b"
      ? new Promise((_, reject) => { rejectOldRoute = reject; })
      : Promise.resolve({ events: [] });
    await React.act(async () => navigate("/chat/session-b"));
    await React.act(async () => navigate("/chat/session-a"));
    await React.act(async () => rejectOldRoute(new ApiRequestError("PRIVATE_OLD_ROUTE", 503, "old_owner_failure", undefined, "diag_aaaaaaaaaaaaaaaa")));
    assert.equal(container.querySelector("[data-chat-history-read-error]"), null);
    assert.equal(calls.submit, 0);
    assert.doesNotMatch((container.textContent ?? ""), /PRIVATE_|已删除|old_owner_failure|diag_aaaaaaaaaaaaaaaa/);

    readEvents = async () => ({ events: [{ id: "message-c", event_type: "user:message", timestamp: "2026-01-01T00:00:00Z", data: { content: "Readable C transcript" } }] });
    await React.act(async () => navigate("/chat/session-c"));
    assert.ok(Array.from(container.querySelectorAll("[data-chat-transcript]")).some((node) => node.getAttribute("data-session-id") === "session-c"));
    readEvents = async () => { throw new Error("PRIVATE_NETWORK_ERROR /private/path token=secret"); };
    await React.act(async () => navigate("/chat/session-a"));
    assert.ok(container.querySelector("[data-chat-history-read-error]"));
    assert.equal(Array.from(container.querySelectorAll("[data-chat-transcript]")).some((node) => node.getAttribute("data-session-id") === "session-c"), false, "no old transcript crosses Session ownership");
    assert.doesNotMatch((container.querySelector("[data-chat-history-read-error]")!.textContent ?? ""), /PRIVATE_|private|secret|HTTP|diag_|service_unavailable/);

    // A status-only API error retains useful HTTP evidence without inventing
    // missing diagnostic metadata or disclosing its unknown private message.
    readEvents = async () => { throw new ApiRequestError("PRIVATE_STATUS_ONLY /private/path token=secret", 503); };
    const statusOnlyReadCount = calls.events;
    await React.act(async () => {
      container.querySelector("[data-chat-history-read-error]")!.querySelector("button")!.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    const statusOnlyAlert = container.querySelector("[data-chat-history-read-error]");
    assert.ok(statusOnlyAlert);
    assert.match((statusOnlyAlert.textContent ?? ""), /历史消息暂时无法读取/);
    assert.match((statusOnlyAlert.textContent ?? ""), /加载会话失败/);
    assert.match((statusOnlyAlert.textContent ?? ""), /HTTP 503/);
    assert.doesNotMatch((statusOnlyAlert.textContent ?? ""), /PRIVATE_STATUS_ONLY|private|secret|错误代码|问题编号|diag_/);
    assert.equal(calls.events, statusOnlyReadCount + 1);
    assert.equal(calls.submit, 0);
  } finally {
    await React.act(async () => root.unmount());
    Object.assign(sessionApi, originals);
    shellHarness.restore();
  }
});
