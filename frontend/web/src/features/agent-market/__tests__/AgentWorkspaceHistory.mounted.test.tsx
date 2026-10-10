import assert from "node:assert/strict";
import { register } from "node:module";
import test from "node:test";
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import type { BackendSession } from "../../../services/api/session";
import type { AgentConversationSessionProjection, AgentProfilePublicProjection } from "../../../types/agentProfile";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb";

register(new URL("./frontendAssetLoader.mjs", import.meta.url), import.meta.url);
await new Promise<void>((resolve) => setImmediate(resolve));
const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://localhost/agent-market/agt_support/chat/session-a", pretendToBeVisual: true,
});
Object.assign(globalThis, {
  window: dom.window, document: dom.window.document,
  localStorage: dom.window.localStorage, sessionStorage: dom.window.sessionStorage,
  Element: dom.window.Element, HTMLElement: dom.window.HTMLElement,
  Node: dom.window.Node, CustomEvent: dom.window.CustomEvent,
  getComputedStyle: dom.window.getComputedStyle,
  requestAnimationFrame: dom.window.requestAnimationFrame.bind(dom.window),
  cancelAnimationFrame: dom.window.cancelAnimationFrame.bind(dom.window),
  IS_REACT_ACT_ENVIRONMENT: true,
});
Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
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
const { MemoryRouter, Routes, Route, useLocation, useNavigate } = await import("react-router-dom");
const { AuthProvider, useAuth } = await import("../../../hooks/useAuth");
const { ModelCatalogProvider } = await import("../../../contexts/ModelCatalogContext");
const { ThemeProvider } = await import("../../../contexts/ThemeContext");
const { AgentWorkspaceRoute } = await import("../AgentWorkspaceRoute");
const { authApi } = await import("../../../services/api/auth");
const { sessionApi } = await import("../../../services/api/session");
const { agentProfileApi } = await import("../../../services/api/agentProfile");
const { modelPublicApi } = await import("../../../services/api/modelPublic");
const { notificationPublicApi } = await import("../../../services/api/notificationPublic");
const { Permission } = await import("../../../types/auth");
const { default: toast } = await import("react-hot-toast");
const { default: i18n } = await import("../../../i18n");
await i18n.changeLanguage("en");

const profile: AgentProfilePublicProjection = {
  agent_id: "agt_support", name: "Support Agent", description: "Synthetic support service",
  starter_prompts: [], avatar_ref: "builtin:assistant", avatar_seed: "support",
  market_tags: [], is_favorite: false, published_at: "2026-10-01T00:00:00Z",
};
const identity = { ...profile, revision: 7 };
function projection(id: string, title = `Task ${id.at(-1)?.toUpperCase()}`): AgentConversationSessionProjection {
  return {
    session_id: id, agent_id: profile.agent_id, workspace_id: "default", title,
    purpose: "conversation", agent_conversation: identity,
    created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
  };
}
function row(id: string, name = `Task ${id.at(-1)?.toUpperCase()}`): BackendSession {
  return { id, name, agent_id: profile.agent_id, agent_conversation: identity,
    created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z", is_active: true, metadata: {} };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function settle() { await React.act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); }); }
async function click(element: Element | null | undefined) {
  assert.ok(element, "expected a rendered click target");
  await React.act(async () => { element.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true })); });
}
async function expectTranscript(container: HTMLElement, id: string) {
  for (let attempt = 0; attempt < 25 && !container.textContent?.includes(`Transcript ${id}`); attempt += 1) await settle();
  assert.ok(container.textContent?.includes(`Transcript ${id}`), `expected the rendered transcript for ${id}`);
}
function button(scope: ParentNode, label: string) {
  return Array.from(scope.querySelectorAll("button")).find((item) => item.textContent?.trim() === label);
}
function task(scope: ParentNode, title: string) {
  return Array.from(scope.querySelectorAll("div.truncate")).find((item) => item.textContent === title)?.parentElement?.parentElement;
}

async function renameTask(scope: HTMLElement, title: string, next: string) {
  await click(task(scope, title)?.querySelector("button"));
  await click(button(scope, i18n.t("sidebar.rename")));
  const input = scope.querySelector<HTMLInputElement>("input");
  assert.ok(input);
  await React.act(async () => {
    Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, "value")!.set!.call(input, next);
    input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
  });
  await React.act(async () => input.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
}
async function deleteTask(scope: HTMLElement, title: string) {
  await click(task(scope, title)?.querySelector("button"));
  await click(button(scope, i18n.t("common.delete")));
  await click(document.querySelector(".enterprise-modal-shell .btn-danger"));
}

async function mountWorkspace(overrides: Partial<typeof sessionApi> = {}) {
  const originals = { session: { ...sessionApi }, profile: { ...agentProfileApi }, auth: { ...authApi }, models: { ...modelPublicApi }, notifications: { ...notificationPublicApi } };
  const originalToastError = toast.error;
  const toastErrors: string[] = [];
  toast.error = (message) => { toastErrors.push(String(message)); return "test-toast"; };
  let account = "user-a";
  authApi.bootstrapAuthContext = async (request) => ({ status: "ready", protocol_version: 2, generation: request.generation });
  authApi.getCurrentUser = async () => ({ id: account, tenant_id: "tenant-test", username: account,
    email: `${account}@example.test`, roles: [], permissions: [Permission.CHAT_READ, Permission.CHAT_WRITE],
    is_admin: false, is_active: true, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" });
  modelPublicApi.listAvailable = async () => ({ models: [{ id: "model-default", value: "model-default", label: "Default" }], count: 1, enabled_count: 1, default_model_id: "model-default" });
  modelPublicApi.getPinnedModelIds = async () => [];
  notificationPublicApi.getActive = async () => [];
  agentProfileApi.getPublished = async () => profile;
  agentProfileApi.listConversations = async () => ({ sessions: [projection("session-a"), projection("session-b"), projection("session-c")], next_cursor: null });
  Object.assign(sessionApi, {
    listAuthoritative: async () => [row("session-a"), row("session-b"), row("session-c")],
    get: async (id: string) => row(id),
    getAuthoritative: async (id: string) => projection(id),
    getEvents: async (id: string) => ({ events: [{ id: `event-${id}`, event_type: "user:message", timestamp: "2026-10-01T00:00:00Z", data: { content: `Transcript ${id}` } }] }),
    getRunInputHistory: async (id: string) => ({ session_id: id, runs: [], has_more: false, next_before_run_id: null }),
    markRead: async () => {}, delete: async () => ({}),
    update: async (id: string, data: { name: string }) => ({ status: "ok", session: row(id, data.name) }),
    ...overrides,
  });
  let currentPath = "";
  let navigate!: ReturnType<typeof useNavigate>;
  let auth!: ReturnType<typeof useAuth>;
  function Probe() {
    currentPath = useLocation().pathname;
    navigate = useNavigate(); auth = useAuth();
    // Deliberately retain the route across principal refresh to exercise owners.
    return auth.isAuthenticated ? <Routes><Route path="/agent-market/:agentId/chat/:sessionId?" element={<AgentWorkspaceRoute />} /><Route path="*" element={<div>Other route</div>} /></Routes> : null;
  }
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  try {
    await React.act(async () => root.render(<MemoryRouter initialEntries={["/agent-market/agt_support/chat/session-a"]}><ThemeProvider><AuthProvider><ModelCatalogProvider><VirtuosoMockContext.Provider value={{ viewportHeight: 800, itemHeight: 100 }}><Probe /></VirtuosoMockContext.Provider></ModelCatalogProvider></AuthProvider></ThemeProvider></MemoryRouter>));
    await settle();
  } catch (error) {
    await React.act(async () => root.unmount()); container.remove();
    toast.error = originalToastError;
    Object.assign(sessionApi, originals.session); Object.assign(agentProfileApi, originals.profile);
    Object.assign(authApi, originals.auth); Object.assign(modelPublicApi, originals.models); Object.assign(notificationPublicApi, originals.notifications);
    throw error;
  }
  const globalHistory = () => container.querySelector<HTMLElement>("[data-workbench-global-history]")!;
  const agentHistory = () => container.querySelector<HTMLElement>("[data-agent-conversation-panel]")!;
  return {
    container, globalHistory, agentHistory, toastErrors, path: () => currentPath,
    async expandGlobal() { const group = globalHistory()?.querySelector('button[aria-expanded="false"]'); if (group) await click(group); },
    async changeAccount(next: string) { account = next; await React.act(async () => { await auth.refreshUser(); }); await settle(); },
    async navigate(path: string | number) { await React.act(async () => { if (typeof path === "number") navigate(path); else navigate(path); }); await settle(); },
    async close() {
      await React.act(async () => root.unmount()); container.remove();
      toast.error = originalToastError;
      Object.assign(sessionApi, originals.session); Object.assign(agentProfileApi, originals.profile);
      Object.assign(authApi, originals.auth); Object.assign(modelPublicApi, originals.models); Object.assign(notificationPublicApi, originals.notifications);
    },
  };
}

test("Agent rename and delete update both rendered history scopes without reopening the workspace", async () => {
  const view = await mountWorkspace();
  try {
    await view.expandGlobal();
    assert.ok(task(view.globalHistory(), "Task B"));
    await renameTask(view.agentHistory(), "Task B", "Renamed B");
    assert.ok(task(view.agentHistory(), "Renamed B"));
    assert.ok(task(view.globalHistory(), "Renamed B"));
    await deleteTask(view.agentHistory(), "Renamed B");
    assert.equal(task(view.agentHistory(), "Renamed B"), undefined);
    assert.equal(task(view.globalHistory(), "Renamed B"), undefined);
    assert.equal(view.path(), "/agent-market/agt_support/chat/session-a");
  } finally { await view.close(); }
});

test("returning to global A supersedes pending task B and restores A's transcript and composer", async () => {
  const pending = deferred<AgentConversationSessionProjection>();
  const view = await mountWorkspace({ getAuthoritative: async id => id === "session-b" ? pending.promise : projection(id) });
  try {
    await view.expandGlobal();
    await expectTranscript(view.container, "session-a");
    await click(task(view.agentHistory(), "Task B"));
    await click(task(view.globalHistory(), "Task A"));
    await settle();
    assert.equal(view.path(), "/agent-market/agt_support/chat/session-a");
    await expectTranscript(view.container, "session-a");
    assert.equal(view.container.querySelector<HTMLTextAreaElement>("textarea")?.disabled, false);
    await React.act(async () => pending.resolve(projection("session-b")));
    assert.equal(view.path(), "/agent-market/agt_support/chat/session-a");
    await expectTranscript(view.container, "session-a");
    assert.doesNotMatch(view.container.textContent!, /Transcript session-b/);
    assert.equal(view.container.querySelector("[data-agent-conversation-loading]"), null);
  } finally { await view.close(); }
});

test("rapid task/global selections and Back keep only the latest route's history", async () => {
  const b = deferred<AgentConversationSessionProjection>();
  const c = deferred<AgentConversationSessionProjection>();
  const view = await mountWorkspace({ getAuthoritative: async id => id === "session-b" ? b.promise : id === "session-c" ? c.promise : projection(id) });
  try {
    await view.expandGlobal();
    await click(task(view.agentHistory(), "Task B"));
    await click(task(view.globalHistory(), "Task C"));
    await click(task(view.agentHistory(), "Task A"));
    await React.act(async () => { c.resolve(projection("session-c")); b.reject(new Error("stale denied B")); });
    assert.equal(view.path(), "/agent-market/agt_support/chat/session-a");
    await expectTranscript(view.container, "session-a");
    await view.navigate(-1);
    assert.equal(view.path(), "/agent-market/agt_support/chat/session-c");
    await expectTranscript(view.container, "session-c");
  } finally { await view.close(); }
});

test("same-workspace account changes retire old lists and mutation callbacks", async () => {
  const oldGlobal = deferred<BackendSession[]>();
  const oldScoped = deferred<{ sessions: AgentConversationSessionProjection[]; next_cursor: null }>();
  const pendingDelete = deferred<unknown>();
  let globalReads = 0;
  const view = await mountWorkspace({ listAuthoritative: async () => ++globalReads === 2 ? oldGlobal.promise : [row("session-a"), row("session-b")], delete: async () => pendingDelete.promise });
  try {
    await view.expandGlobal();
    await deleteTask(view.agentHistory(), "Task A");
    agentProfileApi.listConversations = async () => oldScoped.promise;
    await view.changeAccount("user-b");
    assert.doesNotMatch(view.container.textContent!, /Task B/);
    agentProfileApi.listConversations = async () => ({ sessions: [projection("session-a", "Account C task")], next_cursor: null });
    sessionApi.listAuthoritative = async () => [row("session-a", "Account C task")];
    await view.changeAccount("user-c");
    await view.expandGlobal();
    assert.match(view.container.textContent!, /Account C task/);
    await React.act(async () => {
      oldGlobal.resolve([row("session-b", "Old account global")]);
      oldScoped.resolve({ sessions: [projection("session-b", "Old account scoped")], next_cursor: null });
      pendingDelete.resolve({});
    });
    assert.doesNotMatch(view.container.textContent!, /Old account|Task B/);
    assert.match(view.container.textContent!, /Account C task/);
    assert.equal(view.path(), "/agent-market/agt_support/chat/session-a");
  } finally { await view.close(); }
});


test("global edits propagate to Agent history and deleting A after selecting B keeps B active", async () => {
  const pending = deferred<unknown>();
  const view = await mountWorkspace({ delete: async () => pending.promise });
  try {
    await view.expandGlobal();
    await renameTask(view.globalHistory(), "Task B", "Global rename B");
    assert.ok(task(view.agentHistory(), "Global rename B"));
    await deleteTask(view.globalHistory(), "Task A");
    await click(task(view.agentHistory(), "Global rename B"));
    await expectTranscript(view.container, "session-b");
    await React.act(async () => pending.resolve({}));
    assert.equal(task(view.globalHistory(), "Task A"), undefined);
    assert.equal(task(view.agentHistory(), "Task A"), undefined);
    assert.equal(view.path(), "/agent-market/agt_support/chat/session-b");
    await expectTranscript(view.container, "session-b");
  } finally { await view.close(); }
});

test("a confirmed rename supersedes a pending global list without losing its other rows", async () => {
  const pending = deferred<BackendSession[]>();
  let reads = 0;
  const view = await mountWorkspace({ listAuthoritative: async () => ++reads === 1 ? pending.promise : [row("session-a"), row("session-b", "Acknowledged B")] });
  try {
    await renameTask(view.agentHistory(), "Task B", "Acknowledged B");
    await view.expandGlobal();
    assert.ok(task(view.globalHistory(), "Acknowledged B"));
    assert.ok(task(view.globalHistory(), "Task A"));
    await React.act(async () => pending.resolve([row("session-a"), row("session-b")]));
    assert.ok(task(view.globalHistory(), "Acknowledged B"));
    assert.equal(task(view.globalHistory(), "Task B"), undefined);
    assert.equal(reads, 2, "only the read superseded by the mutation needs replacement");
  } finally { await view.close(); }
});

test("account replacement retires a pending identity failure on the same Agent session URL", async () => {
  const pending = deferred<AgentConversationSessionProjection>();
  let freshPrincipal = false;
  const view = await mountWorkspace({ getAuthoritative: async id => id === "session-b" && !freshPrincipal ? pending.promise : projection(id) });
  try {
    await click(task(view.agentHistory(), "Task B"));
    freshPrincipal = true;
    await view.changeAccount("user-b");
    await expectTranscript(view.container, "session-b");
    await React.act(async () => pending.reject(new Error("old principal denied")));
    assert.equal(view.path(), "/agent-market/agt_support/chat/session-b");
    await expectTranscript(view.container, "session-b");
    assert.equal(view.container.querySelector("[data-agent-conversation-loading]"), null);
  } finally { await view.close(); }
});


test("an acknowledged rename still updates both lists after its editing row is collapsed", async () => {
  const pending = deferred<{ status: string; session: BackendSession }>();
  const view = await mountWorkspace({ update: async () => pending.promise });
  try {
    await view.expandGlobal();
    await renameTask(view.agentHistory(), "Task B", "Collapsed rename B");
    await click(view.agentHistory().querySelector('[aria-label="收起历史会话"]'));
    await React.act(async () => pending.resolve({ status: "ok", session: row("session-b", "Collapsed rename B") }));
    assert.ok(task(view.globalHistory(), "Collapsed rename B"));
    await click(view.agentHistory().querySelector('[aria-label="展开历史会话"]'));
    assert.ok(task(view.agentHistory(), "Collapsed rename B"));
  } finally { await view.close(); }
});


for (const surface of ["task", "global"] as const) {
  test(`same-URL ${surface} selection retries a failed history read without a second selection owner`, async () => {
    let reads = 0;
    const view = await mountWorkspace({ getEvents: async id => {
      reads += 1;
      if (reads === 1) throw new Error("transient history read failure");
      return { events: [{ id: `event-${id}`, event_type: "user:message", timestamp: "2026-10-01T00:00:00Z", data: { content: `Transcript ${id}` } }] };
    } });
    try {
      assert.equal(reads, 1);
      assert.doesNotMatch(view.container.textContent!, /Transcript session-a/);
      if (surface === "global") await view.expandGlobal();
      await click(task(surface === "task" ? view.agentHistory() : view.globalHistory(), "Task A"));
      await expectTranscript(view.container, "session-a");
      assert.equal(view.path(), "/agent-market/agt_support/chat/session-a");
      assert.equal(reads, 2);
    } finally { await view.close(); }
  });
}


test("a current task identity failure stays fail-closed and gives a visible error message", async () => {
  const view = await mountWorkspace({ getAuthoritative: async id => {
    if (id === "session-b") throw new Error("current identity unavailable");
    return projection(id);
  } });
  try {
    await click(task(view.agentHistory(), "Task B"));
    await settle();
    assert.notEqual(view.path(), "/agent-market/agt_support/chat/session-b");
    assert.doesNotMatch(view.container.textContent!, /Transcript session-b/);
    assert.deepEqual(view.toastErrors, ["该历史对话暂时无法打开，请重新选择或重试。"]);
  } finally { await view.close(); }
});
