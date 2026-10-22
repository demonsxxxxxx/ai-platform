import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb";

register(new URL("./frontendAssetLoader.mjs", import.meta.url), import.meta.url);
await new Promise<void>((resolve) => setImmediate(resolve));

const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://localhost/chat/demo-general",
  pretendToBeVisual: true,
});
Object.assign(globalThis, {
  window: dom.window, document: dom.window.document,
  localStorage: dom.window.localStorage, sessionStorage: dom.window.sessionStorage,
  Element: dom.window.Element, HTMLElement: dom.window.HTMLElement,
  Node: dom.window.Node, CustomEvent: dom.window.CustomEvent,
  MutationObserver: dom.window.MutationObserver, DOMRect: dom.window.DOMRect,
  getComputedStyle: dom.window.getComputedStyle,
  requestAnimationFrame: dom.window.requestAnimationFrame.bind(dom.window),
  cancelAnimationFrame: dom.window.cancelAnimationFrame.bind(dom.window),
  IS_REACT_ACT_ENVIRONMENT: true,
});
Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
Object.defineProperty(window, "MutationObserver", {
  configurable: true,
  value: dom.window.MutationObserver,
});
Object.defineProperty(navigator, "locks", { value: {
  request: async (_name: string, _options: unknown, callback: () => Promise<unknown>) => callback(),
} });
window.matchMedia = (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} } as unknown as MediaQueryList);
class Observer { observe() {} unobserve() {} disconnect() {} }
Object.assign(globalThis, { ResizeObserver: Observer, IntersectionObserver: Observer });
Object.assign(window, { ResizeObserver: Observer, IntersectionObserver: Observer });
dom.window.HTMLElement.prototype.scrollIntoView = () => {};
Object.defineProperty(dom.window.HTMLElement.prototype, "offsetHeight", { get: () => 800 });
Object.defineProperty(dom.window.HTMLElement.prototype, "scrollHeight", { get: () => 1000 });
dom.window.HTMLElement.prototype.scrollTo = function (options: ScrollToOptions) {
  this.scrollTop = options.top ?? 0;
  this.dispatchEvent(new dom.window.Event("scroll"));
};
installBrowserAuthTestDb();

const React = await import("react");
const { createRoot } = await import("react-dom/client");
const { VirtuosoMockContext } = await import("react-virtuoso");
const { MemoryRouter, Routes, Route, useLocation } = await import("react-router-dom");
const { AuthProvider, useAuth } = await import("../../../hooks/useAuth");
const { ModelCatalogProvider } = await import("../../../contexts/ModelCatalogContext");
const { ThemeProvider } = await import("../../../contexts/ThemeContext");
const { LobeUiProvider } = await import("../../../contexts/LobeUiProvider");
const { AppContent } = await import("../../../components/layout/AppContent/index");
const { authApi } = await import("../../../services/api/auth");
const { sessionApi } = await import("../../../services/api/session");
const { modelPublicApi } = await import("../../../services/api/modelPublic");
const { Permission } = await import("../../../types/auth");
await import("../../../i18n");

const GENERIC_SESSION_ID = "demo-general";

async function settle() {
  await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
}
async function click(element: Element | null | undefined) {
  assert.ok(element, "expected a rendered click target");
  await React.act(async () => {
    element.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true }));
  });
}

/**
 * The primary 对话 entry must start a blank general conversation from any
 * sidebar presentation: the expanded panel and the collapsed/mobile rail.
 */
async function expectGeneralResetToBlank(params: {
  label: string;
  findEntry: (container: HTMLElement) => HTMLButtonElement | undefined;
  isAdmin?: boolean;
  fromApps?: boolean;
  verifyNavigation?: (container: HTMLElement) => Promise<void>;
}): Promise<void> {
  const originals = {
    session: { ...sessionApi },
    auth: { ...authApi },
    models: { ...modelPublicApi },
  };
  const loads: string[] = [];
  localStorage.setItem("ai-platform-theme", "light");
  localStorage.removeItem("ai-platform-sidebar-management-collapsed");

  authApi.bootstrapAuthContext = async (request) => ({ status: "ready", protocol_version: 2, generation: request.generation });
  authApi.getCurrentUser = async () => ({ id: "user-a", tenant_id: "tenant-test", username: "user-a",
    email: "user-a@example.test", roles: [],
    permissions: [Permission.CHAT_READ, Permission.CHAT_WRITE, Permission.SESSION_READ],
    is_admin: params.isAdmin ?? false, is_active: true, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" });
  modelPublicApi.listAvailable = async () => ({ models: [{ id: "model-default", value: "model-default", label: "Default" }], count: 1, enabled_count: 1, default_model_id: "model-default" });
  Object.assign(sessionApi, {
    listAuthoritative: async () => [{
      id: GENERIC_SESSION_ID, name: "General demo", agent_id: "general-chat",
      created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
      is_active: true, metadata: {},
    }],
    getAuthoritative: async (id: string) => ({
      session_id: id, workspace_id: "default", agent_id: "general-chat",
      title: "General demo", purpose: "conversation" as const, agent_conversation: null,
      created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
    }),
    get: async (id: string) => ({
      id, name: "General demo", agent_id: "general-chat",
      created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
      is_active: true, metadata: {},
    }),
    getEvents: async (id: string) => {
      loads.push(id);
      return { events: [{ id: `event-${id}`, event_type: "user:message",
        timestamp: "2026-10-01T00:00:00Z", data: { content: `Transcript ${id}` } }] };
    },
    getRunInputHistory: async (id: string) => ({ session_id: id, runs: [], has_more: false, next_before_run_id: null }),
    getInputFiles: async (id: string) => ({ session_id: id, files: [] }),
    delete: async () => ({}),
    update: async () => ({ status: "ok", session: null as never }),
  });

  let currentPath = "";
  function Probe() {
    currentPath = useLocation().pathname;
    const auth = useAuth();
    return auth.isAuthenticated ? (
      <Routes>
        <Route path="/chat/:sessionId?" element={<AppContent activeTab="chat" />} />
        <Route path="/apps" element={<AppContent activeTab="apps" />} />
        <Route path="*" element={<div>Other route</div>} />
      </Routes>
    ) : null;
  }

  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  try {
    await React.act(async () => root.render(
      <MemoryRouter initialEntries={[params.fromApps ? "/apps" : `/chat/${GENERIC_SESSION_ID}`]}>
        <ThemeProvider><LobeUiProvider><AuthProvider><ModelCatalogProvider>
          <VirtuosoMockContext.Provider value={{ viewportHeight: 800, itemHeight: 100 }}>
            <Probe />
          </VirtuosoMockContext.Provider>
        </ModelCatalogProvider></AuthProvider></LobeUiProvider></ThemeProvider>
      </MemoryRouter>,
    ));
    await settle();

    assert.equal(currentPath, params.fromApps ? "/apps" : `/chat/${GENERIC_SESSION_ID}`);
    await params.verifyNavigation?.(container);

    // The primary 对话 entry is a general new-conversation entry, not a raw push
    // to /chat, which the URL-sync canonicalizer would rewrite back to the
    // still-bound Session.
    const generalEntry = params.findEntry(container);
    assert.ok(generalEntry, `expected the primary 对话 navigation entry (${params.label})`);
    const loadsBeforeReset = loads.length;
    await click(generalEntry);
    await settle();

    // The defect: the URL stayed on the bound Session and the old transcript
    // rehydrated. Both the route and the read count must settle on a blank entry.
    assert.equal(currentPath, "/chat");
    assert.equal(loads.length, loadsBeforeReset, "reset must not re-read the old Session");
    assert.equal(loads.filter((id) => id === GENERIC_SESSION_ID).length, params.fromApps ? 0 : 1);
  } finally {
    await React.act(async () => root.unmount());
    container.remove();
    Object.assign(sessionApi, originals.session);
    Object.assign(authApi, originals.auth);
    Object.assign(modelPublicApi, originals.models);
  }
};

const expandedEntry = (container: HTMLElement) =>
  Array.from(container.querySelectorAll<HTMLButtonElement>("[data-workbench-nav-item=\"chat\"]"))[0];

const railEntry = (container: HTMLElement) =>
  Array.from(container.querySelectorAll<HTMLButtonElement>("[data-workbench-rail-item=\"chat\"]"))[0];

test("the expanded sidebar 对话 entry starts a blank conversation", async () => {
  await expectGeneralResetToBlank({
    label: "expanded",
    findEntry: expandedEntry,
    async verifyNavigation(container) {
      const header = container.querySelector('[data-workbench-region="header"]')!;
      assert.equal(container.querySelectorAll('[data-workbench-region="header"]').length, 1);
      assert.ok(header.querySelector('[data-general-chat-header]'));
      assert.ok(header.querySelector('[data-librechat-context-toggle]'), "identity and context action share the same header row");
    },
  });
});

test("the collapsed/mobile rail 对话 entry starts a blank conversation", async () => {
  await expectGeneralResetToBlank({ label: "rail", findEntry: railEntry });
});

for (const [label, findEntry] of [["expanded", expandedEntry], ["rail", railEntry]] as const) {
  test(`the ${label} 对话 entry opens general Chat from a navigation-only page`, async () => {
    await expectGeneralResetToBlank({ label, findEntry, fromApps: true });
  });
}

test("management defaults open and preserves explicit choices across page owners", async () => {
  await expectGeneralResetToBlank({
    label: "management remount",
    findEntry: expandedEntry,
    isAdmin: true,
    async verifyNavigation(container) {
      const toggle = () => container.querySelector<HTMLButtonElement>("[data-workbench-management-nav] > button")!;
      const goToApps = async () => {
        await click(container.querySelector('[data-workbench-sidebar-panel] [data-workbench-nav-item="apps"]'));
        await settle();
      };
      const goToChat = async () => {
        await click(expandedEntry(container));
        await settle();
      };
      assert.equal(toggle().getAttribute("aria-expanded"), "true", "management opens on first visit");
      await goToApps();
      assert.equal(toggle().getAttribute("aria-expanded"), "true", "navigation does not collapse management");
      await click(toggle());
      assert.equal(toggle().getAttribute("aria-expanded"), "false");
      await goToChat();
      assert.equal(toggle().getAttribute("aria-expanded"), "false", "explicit collapse survives a new page owner");
      await goToApps();
      assert.equal(toggle().getAttribute("aria-expanded"), "false");
      await click(toggle());
      await goToChat();
      assert.equal(toggle().getAttribute("aria-expanded"), "true", "explicit expansion survives a new page owner");
    },
  });
});

for (const isAdmin of [false, true]) {
  test(`${isAdmin ? "admin management" : "ordinary capabilities"} stays separate from gear settings`, async () => {
    await expectGeneralResetToBlank({
      label: "role navigation",
      findEntry: expandedEntry,
      isAdmin,
      async verifyNavigation(container) {
        const sidebar = container.querySelector<HTMLElement>("[data-workbench-sidebar-panel]")!;
        const group = sidebar.querySelector<HTMLElement>(
          isAdmin ? "[data-workbench-management-nav]" : "[data-workbench-capabilities-nav]",
        );
        assert.ok(group);
        assert.equal(sidebar.querySelector(isAdmin ? "[data-workbench-capabilities-nav]" : "[data-workbench-management-nav]"), null);
        const toggle = group.querySelector<HTMLButtonElement>("button")!;
        assert.equal(toggle.textContent, isAdmin ? "管理" : "可用能力");
        assert.ok(toggle.querySelector(isAdmin ? ".lucide-shield" : ".lucide-layers"));
        assert.equal(toggle.querySelector(".lucide-settings"), null);
        assert.equal(toggle.getAttribute("aria-expanded"), isAdmin ? "true" : "false");
        if (!isAdmin) await click(toggle);
        assert.equal(toggle.getAttribute("aria-expanded"), "true");
        assert.equal(group.querySelector<HTMLElement>("[id]")?.hidden, false);
        const keys = Array.from(group.querySelectorAll("[data-workbench-nav-item]"), item => item.getAttribute("data-workbench-nav-item"));
        assert.deepEqual(
          keys,
          isAdmin
            ? ["skills", "mcp", "agentBuilder", "pluginMarket", "models", "usage", "runs"]
            : ["skills", "mcp"],
        );
        if (!isAdmin) {
          const railKeys = Array.from(container.querySelectorAll("[data-workbench-rail-item]"), item => item.getAttribute("data-workbench-rail-item"));
          for (const key of ["agentBuilder", "pluginMarket", "models", "runs"]) assert.ok(!railKeys.includes(key));
        }
        const gear = sidebar.querySelector<HTMLButtonElement>('button[aria-label="设置"]');
        assert.ok(gear);
        assert.ok(!group.contains(gear));
        await click(gear);
        // The gear opens the shared workbench menu; it owns the theme action and
        // never toggles the platform navigation group.
        const menuButton = Array.from(
          document.body.querySelectorAll<HTMLElement>('[role="menuitem"]'),
        ).find((item) => item.textContent?.includes("切换到深色模式"));
        assert.ok(menuButton, "gear retains the existing theme settings action");
        assert.equal(toggle.getAttribute("aria-expanded"), "true", "gear does not toggle the platform group");
        await click(menuButton);
        assert.ok(document.documentElement.classList.contains("dark"));
      },
    });
  });
}
