import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import type { ComponentProps, ReactNode } from "react";
import type { User } from "../types/auth";
import { installBrowserAuthTestDb } from "../hooks/__tests__/browserAuthTestDb";

register(new URL("./retiredWorkbenchRouteLoader.mjs", import.meta.url), import.meta.url);
await new Promise<void>((resolve) => setImmediate(resolve));

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://localhost/",
  pretendToBeVisual: true,
});
Object.assign(globalThis, {
  window: dom.window,
  document: dom.window.document,
  localStorage: dom.window.localStorage,
  sessionStorage: dom.window.sessionStorage,
  Node: dom.window.Node,
  HTMLElement: dom.window.HTMLElement,
  HTMLInputElement: dom.window.HTMLInputElement,
  HTMLTextAreaElement: dom.window.HTMLTextAreaElement,
  HTMLSelectElement: dom.window.HTMLSelectElement,
  CustomEvent: dom.window.CustomEvent,
  requestAnimationFrame: dom.window.requestAnimationFrame.bind(dom.window),
  cancelAnimationFrame: dom.window.cancelAnimationFrame.bind(dom.window),
  IS_REACT_ACT_ENVIRONMENT: true,
});
Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
Object.defineProperty(dom.window, "matchMedia", {
  configurable: true,
  value: (media: string) => ({
    matches: false,
    media,
    addEventListener() {},
    removeEventListener() {},
  }),
});
installBrowserAuthTestDb();

const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { MemoryRouter, useLocation, useNavigate } = await import("react-router-dom");
const { AuthProvider, useAuth } = await import("../hooks/useAuth");
const { authApi } = await import("../services/api/auth");
const { ApiRequestError } = await import("../services/api/fetch");
const { ThemeProvider } = await import("../contexts/ThemeContext");
const { THEME_STORAGE_KEY } = await import("../utils/themeDom");
const { WorkbenchMenuHost } = await import("../components/layout/AppContent/WorkbenchMenuHost");
const { default: App } = await import("../App");
const { default: i18n } = await import("../i18n/index");

type Principal = "admin" | "ordinary" | "anonymous";
type RetiredFeature = "feedback" | "notifications";

function principal(kind: Exclude<Principal, "anonymous">): User {
  return {
    id: `retirement-test-${kind}`,
    tenant_id: "retirement-test-tenant",
    username: kind,
    email: `${kind}@example.test`,
    is_admin: kind === "admin",
    roles: [kind === "admin" ? "admin" : "user"],
    permissions: [],
    is_active: true,
    created_at: "",
    updated_at: "",
  };
}

function stubNetwork(kind: Principal) {
  const previous = {
    bootstrap: authApi.bootstrapAuthContext,
    getCurrentUser: authApi.getCurrentUser,
    updateMetadata: authApi.updateMetadata,
    fetch: globalThis.fetch,
  };
  const calls: string[] = [];
  authApi.bootstrapAuthContext = async (request) => ({
    status: "ready",
    protocol_version: 2,
    generation: request!.generation,
  });
  authApi.getCurrentUser = async () => {
    if (kind === "anonymous") throw new ApiRequestError("unauthorized", 401);
    return principal(kind);
  };
  authApi.updateMetadata = async () => principal(kind === "anonymous" ? "ordinary" : kind);
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    calls.push(input instanceof Request ? input.url : String(input));
    return new Response(JSON.stringify({ detail: "unexpected-test-request" }), {
      status: 503,
      headers: { "content-type": "application/json" },
    });
  }) as typeof fetch;
  return {
    calls,
    restore() {
      authApi.bootstrapAuthContext = previous.bootstrap;
      authApi.getCurrentUser = previous.getCurrentUser;
      authApi.updateMetadata = previous.updateMetadata;
      globalThis.fetch = previous.fetch;
    },
  };
}

async function settleUntil(predicate: () => boolean, description: string) {
  const deadline = Date.now() + 3000;
  while (!predicate() && Date.now() < deadline) {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
  }
  assert.ok(predicate(), `Timed out waiting for ${description}: ${document.body.textContent}`);
}

async function mounted(child: ReactNode) {
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  let closed = false;
  const view = {
    container,
    async render(next: ReactNode) { await act(async () => root.render(next)); },
    async close() {
      if (closed) return;
      closed = true;
      await act(async () => root.unmount());
      container.remove();
    },
  };
  try {
    await view.render(child);
    return view;
  } catch (error) {
    await view.close();
    throw error;
  }
}

function buttons() {
  return Array.from(document.body.querySelectorAll<HTMLButtonElement>("button"));
}
function buttonLabels() {
  return buttons().map((button) => button.textContent?.trim());
}
async function openMenu() {
  await act(async () => {
    window.dispatchEvent(new CustomEvent("workbench-menu-open", { detail: { bottom: 12, right: 12 } }));
  });
  // The outside-click listener is intentionally installed after the opening event.
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}
async function clickLabel(label: string) {
  const button = buttons().find((candidate) => candidate.textContent?.trim() === label);
  assert.ok(button, `Missing menu action ${label}: ${buttonLabels().join(", ")}`);
  await act(async () => button.click());
}
function assertNoAnnouncementSurface(calls: string[]) {
  assert.equal(document.querySelector('[role="dialog"]'), null, "no announcement popup is mounted");
  assert.equal(document.querySelector("svg.lucide-bell"), null, "no announcement bell is rendered");
  assert.equal(document.querySelector(".bg-red-500"), null, "no announcement count badge is rendered");
  assert.ok(buttons().every((button) => !/公告|通知|feedback|notification/i.test(button.textContent ?? "")));
  assert.deepEqual(calls, [], "mounts, rerenders, and menu actions must not read retired APIs");
}
function assertUnavailable(feature: RetiredFeature) {
  const panel = document.querySelector(`[data-workbench-unavailable="${feature}"]`);
  assert.ok(panel, `real TabContent renders unavailable ${feature}`);
  assert.equal(panel.querySelector("h1")?.textContent, feature === "feedback" ? "反馈功能不可用" : "公告功能不可用");
  assert.equal(panel.querySelector("button, input, textarea, select, table, form"), null);
  assert.doesNotMatch(panel.textContent ?? "", /重试|刷新|发布公告|提交反馈|总数|未读|待处理|DEMO|示例数据/);
  assert.equal(document.querySelector("[data-workbench-projection-page]"), null);
}

async function mountRoute(kind: Principal, path: string) {
  localStorage.clear();
  sessionStorage.clear();
  const network = stubNetwork(kind);
  let currentPath = "";
  let auth: ReturnType<typeof useAuth> | undefined;
  let navigate: ReturnType<typeof useNavigate> | undefined;
  function RouteProbe() {
    currentPath = useLocation().pathname;
    navigate = useNavigate();
    auth = useAuth();
    return null;
  }
  const tree = () => createElement(AuthProvider, null,
    createElement(MemoryRouter, { initialEntries: [path] },
      createElement(RouteProbe), createElement(App),
    ),
  );
  let view: Awaited<ReturnType<typeof mounted>> | undefined;
  try {
    view = await mounted(tree());
    await settleUntil(() => auth !== undefined && !auth.isLoading, "production AuthProvider to settle");
    assert.equal(auth?.isAuthenticated, kind !== "anonymous");
    return {
      ...view,
      calls: network.calls,
      path: () => currentPath,
      async rerender() { await view!.render(tree()); },
      async navigate(pathname: string) { await act(async () => { await navigate!(pathname); }); },
      async close() { try { await view!.close(); } finally { network.restore(); } },
    };
  } catch (error) {
    try { await view?.close(); } finally { network.restore(); }
    throw error;
  }
}

for (const feature of ["feedback", "notifications"] as const) {
  for (const kind of ["admin", "ordinary", "anonymous"] as const) {
    test(`actual App /${feature} preserves ${kind} route access and retires its data surface`, async () => {
      const view = await mountRoute(kind, `/${feature}`);
      try {
        if (kind === "anonymous") {
          await settleUntil(() => view.path() === "/auth/login" && !!document.querySelector('[data-retired-route-test-screen="AuthPage"]'), "anonymous login redirect");
          assert.equal(document.querySelector("[data-workbench-unavailable]"), null);
        } else if (kind === "ordinary" && feature === "feedback") {
          await settleUntil(() => view.path() === "/agent-market" && !!document.querySelector('[data-retired-route-test-screen="AgentMarketRoute"]'), "ordinary-user admin-only redirect");
          assert.equal(document.querySelector("[data-workbench-unavailable]"), null);
        } else {
          await settleUntil(() => !!document.querySelector(`[data-workbench-unavailable="${feature}"]`), "retired route panel");
          assert.equal(view.path(), `/${feature}`);
          assertUnavailable(feature);
          assertNoAnnouncementSurface(view.calls);
          await openMenu();
          assert.deepEqual(buttonLabels(), [i18n.t("theme.switchToDark")]);
          assertNoAnnouncementSurface(view.calls);
          await view.rerender();
          assertUnavailable(feature);
          assertNoAnnouncementSurface(view.calls);
          await clickLabel(i18n.t("theme.switchToDark"));
          assert.deepEqual(buttonLabels(), []);
          await openMenu();
          assert.deepEqual(buttonLabels(), [i18n.t("theme.switchToLight")]);
          assertNoAnnouncementSurface(view.calls);
        }
        assert.deepEqual(view.calls, []);
      } finally { await view.close(); }
    });
  }
}

test("actual App client navigation between retired routes keeps the unavailable state without fetching", async () => {
  const view = await mountRoute("admin", "/feedback");
  try {
    await settleUntil(() => !!document.querySelector('[data-workbench-unavailable="feedback"]'), "feedback panel");
    for (const feature of ["notifications", "feedback", "notifications"] as const) {
      await view.navigate(`/${feature}`);
      assert.equal(view.path(), `/${feature}`);
      assertUnavailable(feature);
      assert.equal(document.querySelectorAll("[data-workbench-unavailable]").length, 1);
      assertNoAnnouncementSurface(view.calls);
    }
  } finally { await view.close(); }
});

test("real workbench menu preserves theme, new chat, outline, and playback across rerenders and reopening", async () => {
  localStorage.clear();
  const network = stubNetwork("admin");
  const actions: string[] = [];
  const props: ComponentProps<typeof WorkbenchMenuHost> = {
    activeTab: "chat",
    onNewSession: () => actions.push("new-chat"),
    currentRunId: "synthetic-run",
    onOpenRunPlayback: () => actions.push("playback"),
    showOutlineButton: true,
    onToggleOutline: () => actions.push("outline"),
  };
  const tree = (overrides: Partial<typeof props> = {}) => createElement(ThemeProvider, null,
    createElement(WorkbenchMenuHost, { ...props, ...overrides }),
  );
  const view = await mounted(tree());
  try {
    assert.deepEqual(buttonLabels(), []);
    assertNoAnnouncementSurface(network.calls);
    await view.render(tree());
    await openMenu();
    assert.deepEqual(buttonLabels(), [i18n.t("chat.outline"), i18n.t("runPlayback.menuLabel"), i18n.t("sidebar.newChat"), i18n.t("theme.switchToDark")]);
    assertNoAnnouncementSurface(network.calls);
    for (const label of [i18n.t("chat.outline"), i18n.t("runPlayback.menuLabel"), i18n.t("sidebar.newChat")]) {
      await clickLabel(label);
      assert.deepEqual(buttonLabels(), [], "each preserved action closes the menu");
      await openMenu();
      assertNoAnnouncementSurface(network.calls);
    }
    assert.deepEqual(actions, ["outline", "playback", "new-chat"]);
    await clickLabel(i18n.t("theme.switchToDark"));
    assert.equal(localStorage.getItem(THEME_STORAGE_KEY), "dark");
    assert.ok(document.documentElement.classList.contains("dark"));
    await openMenu();
    await clickLabel(i18n.t("theme.switchToLight"));
    assert.equal(localStorage.getItem(THEME_STORAGE_KEY), "light");
    assert.equal(document.documentElement.classList.contains("dark"), false);
    await view.render(tree({ allowNewSessionAction: false, currentRunId: null, showOutlineButton: false }));
    await openMenu();
    assert.deepEqual(buttonLabels(), [i18n.t("theme.switchToDark")], "existing action eligibility remains intact");
    assertNoAnnouncementSurface(network.calls);
    await act(async () => document.body.click());
    assert.deepEqual(buttonLabels(), [], "outside click still closes the menu");
    await view.render(tree({ newSessionActionLabel: "开始新任务" }));
    await openMenu();
    await clickLabel("开始新任务");
    assert.deepEqual(actions, ["outline", "playback", "new-chat", "new-chat"]);
    assertNoAnnouncementSurface(network.calls);
  } finally {
    try { await view.close(); } finally { network.restore(); }
  }
});
