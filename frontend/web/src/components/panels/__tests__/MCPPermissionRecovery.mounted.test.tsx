import assert from "node:assert/strict";
import test from "node:test";
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import type { ReactNode } from "react";
import type { User } from "../../../types/auth";
import { authApi } from "../../../services/api/auth";
import { roleApi } from "../../../services/api/role";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb";
import toast from "react-hot-toast";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/memory" });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, localStorage: dom.window.localStorage, sessionStorage: dom.window.sessionStorage, HTMLElement: dom.window.HTMLElement, CustomEvent: dom.window.CustomEvent, IS_REACT_ACT_ENVIRONMENT: true });
Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
Object.assign(globalThis, {
  getComputedStyle: dom.window.getComputedStyle,
  HTMLFormElement: dom.window.HTMLFormElement,
  HTMLSelectElement: dom.window.HTMLSelectElement,
  DocumentFragment: dom.window.DocumentFragment,
});
installBrowserAuthTestDb();
Object.assign(dom.window.HTMLDialogElement.prototype, {
  showModal(this: HTMLDialogElement) { this.setAttribute("open", ""); },
  close(this: HTMLDialogElement) { this.removeAttribute("open"); },
});
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { AuthProvider, useAuth } = await import("../../../hooks/useAuth");
const { useMCP } = await import("../../../hooks/useMcp");
const { MCPPanel } = await import("../MCPPanel");
await import("../../../i18n/index");

function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function principal(id = "user-a", roles = ["admin"]): User {
  return { id, tenant_id: "tenant-test", username: id, email: "synthetic@example.test", roles, permissions: [], is_active: true, created_at: "", updated_at: "" };
}
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } }); }
async function click(node: HTMLButtonElement) { await act(async () => { node.click(); }); }

type Request = { url: URL; init: RequestInit };
async function harness(child: ReactNode, fetcher?: (request: Request) => Promise<Response> | Response, discover?: (request: Request) => Promise<Response> | Response) {
  const old = { bootstrap: authApi.bootstrapAuthContext, user: authApi.getCurrentUser, roles: roleApi.list, fetch: globalThis.fetch, success: toast.success, error: toast.error };
  let user = principal(); let auth!: ReturnType<typeof useAuth>;
  const notices: string[] = []; const calls: Request[] = []; const discoveryCalls: Request[] = [];
  authApi.bootstrapAuthContext = async request => ({ status: "ready", protocol_version: 2, generation: request!.generation });
  authApi.getCurrentUser = async () => user;
  roleApi.list = async () => ({ roles: [], total: 0, skip: 0, limit: 200 });
  toast.success = ((message: unknown) => { notices.push(`success:${String(message)}`); return "test"; }) as typeof toast.success;
  toast.error = ((message: unknown) => { notices.push(`error:${String(message)}`); return "test"; }) as typeof toast.error;
  globalThis.fetch = (async (url, init = {}) => {
    const request = { url: new URL(String(url), "http://localhost"), init };
    if (request.url.pathname.endsWith("/tools") || request.url.pathname.endsWith("/catalog")) {
      discoveryCalls.push(request);
      return discover ? discover(request) : json({ server_name: decodeURIComponent(request.url.pathname.split("/").at(-2)!), tools: [], count: 0, unavailable_reason: null });
    }
    calls.push(request);
    if (fetcher) return fetcher(request);
    throw new Error(`Unexpected request ${request.url.pathname}`);
  }) as typeof fetch;
  function Probe({ children }: { children: ReactNode }) { auth = useAuth(); return children; }
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  const render = async (next: ReactNode) => { await act(async () => { root.render(createElement(AuthProvider, null, createElement(Probe, { children: next }))); }); };
  await render(child);
  assert.equal(auth.isAuthenticated, true, "real AuthProvider must authenticate synthetic principal");
  let closed = false;
  return { container, calls, discoveryCalls, notices, render, refreshUser: () => auth.refreshUser(), async setUser(next: User) { user = next; await act(async () => { await auth.refreshUser(); }); },
    async close() { if (closed) return; closed = true; await act(async () => root.unmount()); container.remove(); Object.assign(authApi, { bootstrapAuthContext: old.bootstrap, getCurrentUser: old.user }); roleApi.list = old.roles; globalThis.fetch = old.fetch; toast.success = old.success; toast.error = old.error; },
  };
}

test("administrator sees discovered tools and can inspect their public descriptions", async () => {
  const tools = [
    { name: "synthetic-mcp::alpha", description: "Synthetic alpha", parameters: [] },
    { name: "synthetic-mcp::beta", description: "Synthetic beta", parameters: [] },
  ];
  const view = await harness(createElement(MCPPanel), () => page(), () => json({ server_name: server.name, tools, count: 2, unavailable_reason: null }));
  try {
    const row = view.container.querySelector("[data-mcp-server-row]")!;
    assert.match(row.textContent!, /2 个工具/);
    assert.doesNotMatch(view.container.textContent!, /common\.actions|mcp\.tools\./);
    assert.doesNotMatch(row.textContent!, /运行中|0 个角色/);
    assert.equal(view.discoveryCalls.length, 1);
    assert.ok(view.discoveryCalls[0].url.pathname.endsWith("/catalog"));
    const trigger = row.querySelector<HTMLButtonElement>("[data-mcp-view-tools]")!;
    trigger.focus();
    await click(trigger);
    const detail = view.container.querySelector("[data-mcp-tool-detail]")!;
    assert.ok(detail);
    assert.match(detail.textContent!, /Synthetic alpha/);
    assert.match(detail.textContent!, /Synthetic beta/);
    assert.doesNotMatch(detail.textContent!, /example\.invalid|headers|credential/);
    const first = detail.querySelector<HTMLButtonElement>("[data-mcp-tool-detail-close]")!;
    const last = detail.querySelector<HTMLInputElement>('input[type="search"]')!;
    await act(async () => { last.focus(); last.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true })); });
    assert.equal(document.activeElement, first);
    await act(async () => { first.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true, cancelable: true })); });
    assert.equal(document.activeElement, last);
    await click(first);
    assert.equal(view.container.querySelector("[data-mcp-tool-detail]"), null);
    assert.equal(document.activeElement, trigger);
  } finally { await view.close(); }
});

test("enterprise catalog retains missing definitions without counting them as available", async () => {
  const tools = [
    { name: "synthetic-mcp::ready", description: "Available synthetic tool", parameters: [], available: true },
    { name: "synthetic-mcp::missing", description: "Missing synthetic tool", parameters: [], available: false },
  ];
  const view = await harness(createElement(MCPPanel), () => json({ servers: [{ ...server, integration_kind: "enterprise_gateway" }], total: 1, skip: 0, limit: 20 }),
    () => json({ server_name: server.name, tools, count: 1, unavailable_reason: null }));
  try {
    assert.equal(view.container.querySelector("[data-mcp-tool-count]")!.textContent, "1 个工具");
    assert.ok(view.discoveryCalls.every(call => call.url.pathname.endsWith("/catalog")));
    await click(view.container.querySelector<HTMLButtonElement>("[data-mcp-view-tools]")!);
    const detail = view.container.querySelector("[data-mcp-tool-detail]")!;
    assert.equal(detail.querySelectorAll("li").length, 2);
    assert.match(detail.textContent!, /Gateway 全量目录/);
    assert.match(detail.textContent!, /缺失或已停用/);
    assert.doesNotMatch(detail.textContent!, /当前账户可发现/);
  } finally { await view.close(); }
});

test("one unavailable server never hides healthy tools in the ordinary directory", async () => {
  const view = await harness(createElement(MCPPanel), () => json({ servers: [server, { ...server, name: "broken" }], total: 2, skip: 0, limit: 200 }), ({ url }) =>
    url.pathname.endsWith("/broken/tools")
      ? json({ server_name: "broken", tools: [], count: 0, unavailable_reason: "discovery_failed" })
      : json({ server_name: server.name, tools: [{ name: "alpha", description: "Healthy synthetic tool", parameters: [] }], count: 1, unavailable_reason: null }),
  );
  try {
    await view.setUser(principal("ordinary", ["user"]));
    assert.match(view.container.querySelector("[data-ordinary-mcp-catalog]")!.textContent!, /Healthy synthetic tool/);
    assert.ok(view.discoveryCalls.slice(-2).every(call => call.url.pathname.endsWith("/tools")));
    assert.match(view.container.textContent!, /暂时无法加载可用工具/);
    assert.doesNotMatch(view.container.textContent!, /common\.retry|mcp\.tools\./);
    assert.equal(view.container.querySelector("[data-mcp-admin-controls]"), null);
  } finally { await view.close(); }
});

test("successful zero differs from unavailable and skipped discovery states", async () => {
  const servers = [server, { ...server, name: "broken" }, { ...server, name: "disabled", enabled: false }, { ...server, name: "unconfigured", credential_state: "not_configured" }, { ...server, name: "legacy", transport: "sandbox" }];
  const view = await harness(createElement(MCPPanel), () => json({ servers, total: servers.length, skip: 0, limit: 20 }), ({ url }) => {
    const name = url.pathname.split("/").at(-2);
    return json({ server_name: name, tools: [], count: 0, unavailable_reason: name === "broken" ? "discovery_failed" : null });
  });
  try {
    const row = (name: string) => view.container.querySelector(`[data-mcp-server-row="${name}"]`)!;
    assert.equal(row(server.name).querySelector("[data-mcp-tool-count]")!.textContent, "0 个工具");
    for (const name of ["broken", "disabled", "unconfigured", "legacy"]) assert.equal(row(name).querySelector("[data-mcp-tool-count]")!.textContent, "—");
    assert.match(row("broken").textContent!, /发现失败/);
    assert.match(row("disabled").textContent!, /已停用，未查询/);
    assert.match(row("unconfigured").textContent!, /连接未配置/);
    assert.match(row("legacy").textContent!, /传输方式不受支持/);
    assert.equal(view.discoveryCalls.length, 2, "disabled, unconfigured and unsupported rows must not open discovery sessions");
  } finally { await view.close(); }
});

test("refresh masks the previous count and updates the open tool detail", async () => {
  const pending = deferred<Response>(); let discoveries = 0;
  const response = (count: number) => json({ server_name: server.name, tools: Array.from({ length: count }, (_, index) => ({ name: `tool-${index}`, description: `Synthetic tool ${index}`, parameters: [] })), count, unavailable_reason: null });
  const view = await harness(createElement(MCPPanel), () => page(), () => ++discoveries === 1 ? response(2) : pending.promise);
  try {
    await click(view.container.querySelector<HTMLButtonElement>("[data-mcp-view-tools]")!);
    await click(view.container.querySelector<HTMLButtonElement>("[data-mcp-tool-detail] [data-mcp-refresh-tools]")!);
    assert.equal(view.container.querySelector("[data-mcp-tool-count]")!.textContent, "—");
    assert.doesNotMatch(view.container.querySelector("[data-mcp-tool-detail]")!.textContent!, /Synthetic tool/);
    await act(async () => pending.resolve(response(1)));
    assert.equal(view.container.querySelector("[data-mcp-tool-count]")!.textContent, "1 个工具");
    assert.equal(view.container.querySelectorAll("[data-mcp-tool-detail] li").length, 1);
    const search = view.container.querySelector<HTMLInputElement>('[data-mcp-tool-detail] input[type="search"]')!;
    await act(async () => props(search).onChange({ target: { value: "no-such-tool" } }));
    assert.equal(view.container.querySelectorAll("[data-mcp-tool-detail] li").length, 0);
    assert.match(view.container.querySelector("[data-mcp-tool-detail]")!.textContent!, /没有匹配的工具/);
  } finally { await view.close(); }
});

test("late tool discovery cannot publish across a same-user department change", async () => {
  const old = deferred<Response>(); let discoveries = 0;
  const view = await harness(createElement(MCPPanel), () => page(), () => ++discoveries === 1 ? old.promise : json({ server_name: server.name, tools: [{ name: "current-tool", description: "Current synthetic tool", parameters: [] }], count: 1, unavailable_reason: null }));
  try {
    await click(view.container.querySelector<HTMLButtonElement>("[data-mcp-view-tools]")!);
    await view.setUser({ ...principal(), department_id: "synthetic-department" });
    assert.equal(view.container.querySelector("[data-mcp-tool-detail]"), null);
    assert.equal(view.container.querySelector("[data-mcp-tool-count]")!.textContent, "1 个工具");
    await act(async () => old.resolve(json({ server_name: server.name, tools: [{ name: "old-tool", description: "Old private description", parameters: [] }], count: 1, unavailable_reason: null })));
    await click(view.container.querySelector<HTMLButtonElement>("[data-mcp-view-tools]")!);
    assert.match(view.container.querySelector("[data-mcp-tool-detail]")!.textContent!, /Current synthetic tool/);
    assert.doesNotMatch(view.container.textContent!, /Old private description/);
    assert.equal(view.discoveryCalls.length, 2);
  } finally { await view.close(); }
});

test("paused authentication and unmount fence retained tool refresh callbacks", async () => {
  const pending = deferred<User>();
  const view = await harness(createElement(MCPPanel), () => page());
  try {
    await click(view.container.querySelector<HTMLButtonElement>("[data-mcp-view-tools]")!);
    const refreshButton = view.container.querySelector<HTMLButtonElement>("[data-mcp-refresh-tools]")!;
    const refreshKey = Object.keys(refreshButton).find(key => key.startsWith("__reactProps$"))!;
    const retainedRefresh = (refreshButton as unknown as Record<string, { onClick: () => void }>)[refreshKey].onClick;
    let refresh!: ReturnType<typeof view.refreshUser>;
    authApi.getCurrentUser = () => pending.promise;
    await act(async () => { refresh = view.refreshUser(); });
    assert.equal(view.container.querySelector("[data-mcp-tool-detail]"), null);
    const reads = view.discoveryCalls.length;
    await act(async () => retainedRefresh());
    assert.equal(view.discoveryCalls.length, reads);
    await act(async () => { pending.resolve(principal()); await refresh; });
    assert.equal(view.container.querySelector("[data-mcp-tool-detail]"), null);
    await view.render(null);
    const finished = view.discoveryCalls.length;
    await act(async () => retainedRefresh());
    assert.equal(view.discoveryCalls.length, finished);
  } finally { await view.close(); }
});

const forbidden = () => json({ detail: "missing_permission:mcp:read" }, 403);
const empty = (limit = 20) => json({ servers: [], total: 0, skip: 0, limit });
function state(view: Awaited<ReturnType<typeof harness>>) {
  return view.container.querySelector("[data-mcp-directory-shell]")?.getAttribute("data-frontend-governance-state");
}
test("permission denial remains visible after effects settle instead of a ready empty directory", async () => {
  const view = await harness(createElement(MCPPanel), () => forbidden());
  try {
    await act(async () => {});
    assert.equal(state(view), "forbidden");
    assert.equal(view.container.querySelector("[data-mcp-admin-controls]"), null);
    assert.doesNotMatch(view.container.textContent!, /missing_permission|mcp:read/);
    assert.equal(view.calls.length, 1, "denial must not self-retry");
    assert.equal(view.discoveryCalls.length, 0, "denial must not query the management catalog");
  } finally { await view.close(); }
});

function retry(view: Awaited<ReturnType<typeof harness>>) {
  const node = view.container.querySelector<HTMLButtonElement>("[data-mcp-retry]");
  assert.ok(node, "unavailable directory must offer an explicit read retry");
  return node;
}
const server = { name: "synthetic-mcp", transport: "streamable_http" as const, url: "https://example.invalid/mcp", enabled: true, is_system: true, can_edit: true, allowed_roles: [], role_quotas: {}, credential_state: "configured" };
const page = () => json({ servers: [server], total: 1, skip: 0, limit: 20 });

test("explicit retry stays unavailable until a successful read and repeated 403 never auto retries", async () => {
  const pending = deferred<Response>(); let reads = 0;
  const view = await harness(createElement(MCPPanel), () => ++reads < 3 ? forbidden() : pending.promise);
  try {
    await click(retry(view));
    assert.equal(state(view), "forbidden"); assert.equal(reads, 2);
    await click(retry(view));
    assert.equal(state(view), "loading");
    assert.equal(view.container.querySelector("[data-mcp-admin-controls]"), null);
    await act(async () => pending.resolve(empty()));
    assert.equal(state(view), "ready");
    assert.ok(view.container.querySelector("[data-mcp-admin-controls]"));
    assert.equal(view.calls.filter(call => call.init.method && call.init.method !== "GET").length, 0);
    assert.equal(reads, 3);
  } finally { await view.close(); }
});

test("a mutation denial remains forbidden, closes the editor and never replays the mutation on retry", async () => {
  let reads = 0; let writes = 0;
  const view = await harness(createElement(MCPPanel), ({ init }) => {
    if (init.method === "PATCH") { writes++; return forbidden(); }
    reads++; return page();
  });
  try {
    const cardToggle = view.container.querySelector<HTMLButtonElement>("[data-mcp-admin-toggle]");
    assert.ok(cardToggle); await click(cardToggle);
    assert.equal(state(view), "forbidden");
    assert.equal(view.container.querySelector("[data-mcp-admin-controls]"), null);
    assert.equal(view.container.querySelector('[role="dialog"]'), null);
    assert.equal(writes, 1); assert.equal(reads, 1);
    await click(retry(view)); assert.equal(state(view), "ready");
    assert.equal(writes, 1); assert.equal(reads, 2);
    assert.deepEqual(view.notices, []);
  } finally { await view.close(); }
});

test("ordinary directory retains unavailable copy and read-only recovery", async () => {
  let denied = true;
  const view = await harness(createElement(MCPPanel), ({ url }) => denied ? forbidden() : empty(Number(url.searchParams.get("limit"))));
  try {
    await view.setUser(principal("ordinary", ["user"]));
    assert.ok(view.container.querySelector("[data-ordinary-mcp-catalog]"));
    assert.ok(retry(view)); assert.equal(view.container.querySelector("[data-mcp-admin-controls]"), null);
    denied = false; await click(retry(view));
    assert.equal(view.container.querySelector("[data-mcp-retry]"), null);
    assert.equal(view.container.querySelector("[data-mcp-admin-controls]"), null);
  } finally { await view.close(); }
});

test("identity change recovers without unmounting and late old denial cannot poison the new identity", async () => {
  const old = deferred<Response>(); let reads = 0;
  const view = await harness(createElement(MCPPanel), () => ++reads === 1 ? old.promise : empty());
  try {
    assert.equal(state(view), "loading");
    await view.setUser(principal("user-b")); assert.equal(state(view), "ready");
    await act(async () => old.resolve(forbidden()));
    assert.equal(state(view), "ready"); assert.equal(reads, 2);
    assert.ok(view.container.querySelector("[data-mcp-admin-controls]"));
  } finally { await view.close(); }
});

test("pending detail cannot reopen editor or report a failure after identity change", async () => {
  for (const fail of [false, true]) {
    const detail = deferred<Response>();
    const view = await harness(createElement(MCPPanel), ({ url }) => url.pathname.endsWith("/synthetic-mcp") ? detail.promise : page());
    try {
      const edit = view.container.querySelector<HTMLButtonElement>("[data-mcp-admin-edit]")!;
      await click(edit); await view.setUser(principal("user-b"));
      await act(async () => detail.resolve(fail ? forbidden() : json(server)));
      assert.equal(state(view), "ready"); assert.equal(view.container.querySelector('[role="dialog"]'), null);
      assert.deepEqual(view.notices, []);
    } finally { await view.close(); }
  }
});

const params = { skip: 0, limit: 20 };
let hook!: ReturnType<typeof useMCP>;
function HookProbe() {
  const auth = useAuth();
  hook = useMCP({ enabled: auth.isAuthenticated, paused: auth.isLoading, listParams: params, canManageServers: auth.user?.roles.includes("admin"), identityScope: JSON.stringify([auth.user?.tenant_id, auth.user?.id]) });
  return null;
}

test("retained CRUD callbacks cannot issue requests after denial or while recovery is pending", async () => {
  const pending = deferred<Response>(); let reads = 0;
  const view = await harness(createElement(HookProbe), ({ init }) => init.method === "PATCH" ? forbidden() : ++reads === 1 ? page() : pending.promise);
  try {
    const old = hook;
    await act(async () => { await hook.toggleServer(server.name); });
    assert.equal(hook.permissionDenied, true); assert.equal(hook.isAvailable, false);
    const tryOldOperations = async () => {
      assert.equal(await old.createServer({ name: "other", transport: "streamable_http", url: "https://example.invalid/mcp" }), null);
      assert.equal(await old.updateServer(server.name, { enabled: false }), null);
      assert.equal(await old.deleteServer(server.name), false);
      assert.equal(await old.toggleServer(server.name), null);
      assert.equal(await old.getServer(server.name), null);
    };
    await act(tryOldOperations); assert.equal(view.calls.length, 2);
    await act(async () => { assert.equal(await hook.fetchServers(), false); });
    assert.equal(view.calls.length, 2, "a plain directory GET cannot reset a denied permission owner");
    await view.setUser(principal("user-b"));
    await act(tryOldOperations); assert.equal(view.calls.length, 3); assert.equal(hook.isAvailable, false);
    await act(async () => { pending.resolve(page()); });
    assert.equal(hook.isAvailable, true);
  } finally { await view.close(); }
});

test("old identity callbacks and in-flight mutation completions cannot refresh or poison replacement state", async () => {
  for (const fail of [false, true]) {
    const pending = deferred<Response>();
    const view = await harness(createElement(HookProbe), ({ init }) => init.method === "PATCH" ? pending.promise : page());
    try {
      const old = hook; let mutation!: Promise<unknown>;
      await act(async () => { mutation = old.toggleServer(server.name); });
      await view.setUser(principal("user-b"));
      await act(async () => {
        assert.equal(await old.deleteServer(server.name), false);
        pending.resolve(fail ? forbidden() : json({ server }));
        assert.equal(await mutation, null);
      });
      assert.equal(hook.isAvailable, true); assert.equal(hook.error, null);
      assert.equal(view.calls.length, 3, "only old list, old mutation, and new identity list");
    } finally { await view.close(); }
  }
});

test("newest explicit read fences older list success and denial", async () => {
  for (const fail of [false, true]) {
    const pending = deferred<Response>(); let reads = 0;
    const view = await harness(createElement(HookProbe), () => ++reads === 2 ? pending.promise : empty());
    try {
      let prior!: Promise<boolean>;
      await act(async () => { prior = hook.fetchServers(); });
      await act(async () => { await hook.fetchServers(); });
      await act(async () => { pending.resolve(fail ? forbidden() : page()); assert.equal(await prior, false); });
      assert.equal(hook.isAvailable, true); assert.equal(hook.error, null); assert.deepEqual(hook.servers, []);
    } finally { await view.close(); }
  }
});

test("legacy permission errors cannot disappear into a ready empty catalog", async () => {
  const view = await harness(createElement(MCPPanel), () => { throw new Error("missing_permission:mcp:read private-debug"); });
  try {
    await act(async () => {});
    assert.equal(state(view), "forbidden");
    assert.ok(retry(view));
    assert.ok(!view.container.querySelector("[data-mcp-admin-controls]"));
    assert.doesNotMatch(view.container.textContent!, /missing_permission|private-debug/);
    assert.equal(view.calls.length, 1);
  } finally { await view.close(); }
});

test("unmounted hook rejects retained callbacks and suppresses late mutation effects", async () => {
  const pending = deferred<Response>();
  const view = await harness(createElement(HookProbe), ({ init }) => init.method === "PATCH" ? pending.promise : page());
  try {
    const old = hook; let operation!: Promise<unknown>;
    await act(async () => { operation = old.toggleServer(server.name); });
    await view.render(null);
    await act(async () => { pending.resolve(json({ server })); assert.equal(await operation, null); });
    assert.equal(await old.fetchServers(), false);
    assert.equal(await old.deleteServer(server.name), false);
    assert.equal(await old.getServer(server.name), null);
    assert.equal(view.calls.length, 2);
  } finally { await view.close(); }
});

test("failed principal refresh preserves the denial even when a directory GET could succeed", async () => {
  let listDenied = true;
  const view = await harness(createElement(MCPPanel), () => listDenied ? forbidden() : empty());
  try {
    listDenied = false;
    authApi.getCurrentUser = async () => { throw new Error("private-auth-refresh-failure"); };
    await click(retry(view));
    assert.equal(state(view), "forbidden");
    assert.ok(!view.container.querySelector("[data-mcp-admin-controls]"));
    assert.equal(view.calls.length, 1, "failed authority refresh must not trigger a catalog read");
    assert.doesNotMatch(view.container.textContent!, /private-auth-refresh-failure/);
    authApi.getCurrentUser = async () => principal();
    await click(retry(view));
    assert.equal(state(view), "ready"); assert.equal(view.calls.length, 2);
  } finally { await view.close(); }
});

test("permission recovery refreshes revoked administrator roles before reopening lifecycle controls", async () => {
  const view = await harness(createElement(MCPPanel), ({ init }) => init.method === "PATCH" ? forbidden() : page());
  try {
    await click(view.container.querySelector<HTMLButtonElement>("[data-mcp-admin-toggle]")!);
    assert.equal(state(view), "forbidden");
    authApi.getCurrentUser = async () => principal("user-a", ["user"]);
    const previousFetch = globalThis.fetch;
    globalThis.fetch = (async (url, init) => {
      const address = new URL(String(url), "http://localhost");
      if (address.pathname === "/api/mcp/") return empty(200);
      return previousFetch(url, init);
    }) as typeof fetch;
    await click(retry(view));
    assert.ok(view.container.querySelector("[data-ordinary-mcp-catalog]"));
    assert.ok(!view.container.querySelector("[data-mcp-admin-controls]"));
  } finally { await view.close(); }
});

function props(node: Element) {
  const key = Object.keys(node).find(key => key.startsWith("__reactProps$")); assert.ok(key);
  return (node as unknown as Record<string, unknown>)[key] as { onChange: (event: { target: { value: string } }) => void; onSubmit: (event: { preventDefault: () => void }) => Promise<void> };
}
test("ordinary operation errors keep the actual editor draft and safe error visible for correction", async () => {
  for (const status of [400, 500]) {
    let writes = 0;
    const view = await harness(createElement(MCPPanel), ({ init }) => {
      if (init.method === "POST") {
        writes++;
        return writes === 1 ? json({ detail: "private-validation-detail" }, status) : json(server);
      }
      return empty();
    });
    try {
      await click(view.container.querySelector<HTMLButtonElement>("[data-mcp-admin-controls] button")!);
      const name = view.container.querySelector<HTMLInputElement>('form input[type="text"]')!;
      const url = view.container.querySelector<HTMLInputElement>('form input[type="url"]')!;
      await act(async () => { props(name).onChange({ target: { value: "kept-draft" } }); props(url).onChange({ target: { value: "https://example.invalid/mcp" } }); });
      await act(async () => { await props(view.container.querySelector("form")!).onSubmit({ preventDefault() {} }); });
      assert.ok(view.container.querySelector('[role="dialog"]'), "non-permission errors must preserve the editor");
      assert.equal(view.container.querySelector<HTMLInputElement>('form input[type="text"]')!.value, "kept-draft");
      assert.ok(view.container.querySelector('[role="dialog"] [role="alert"]'));
      assert.doesNotMatch(view.container.textContent!, /private-validation-detail/);
      assert.equal(state(view), "ready");
      await act(async () => { await props(view.container.querySelector("form")!).onSubmit({ preventDefault() {} }); });
      assert.equal(writes, 2); assert.ok(!view.container.querySelector('[role="dialog"]'));
      assert.equal(view.notices.length, 1);
    } finally { await view.close(); }
  }
});

test("ordinary identity hook cannot issue detail or lifecycle mutations even with a backed catalog", async () => {
  const view = await harness(createElement(HookProbe), () => empty());
  try {
    await view.setUser(principal("ordinary", ["user"]));
    const reads = view.calls.length;
    assert.equal(await hook.getServer(server.name), null);
    assert.equal(await hook.createServer({ name: "other", transport: "streamable_http", url: "https://example.invalid/mcp" }), null);
    assert.equal(await hook.updateServer(server.name, { enabled: false }), null);
    assert.equal(await hook.deleteServer(server.name), false);
    assert.equal(await hook.toggleServer(server.name), null);
    assert.equal(view.calls.length, reads);
  } finally { await view.close(); }
});

test("a deferred authority refresh blocks retained read and CRUD callbacks", async () => {
  const pending = deferred<User>();
  const view = await harness(createElement(HookProbe), () => page());
  try {
    const old = hook; let refresh!: ReturnType<typeof view.refreshUser>;
    authApi.getCurrentUser = () => pending.promise;
    await act(async () => { refresh = view.refreshUser(); });
    assert.equal(hook.isAvailable, false);
    await act(async () => {
      assert.equal(await old.fetchServers(), false);
      assert.equal(await old.getServer(server.name), null);
      assert.equal(await old.createServer({ name: "other", transport: "streamable_http", url: "https://example.invalid/mcp" }), null);
      assert.equal(await old.updateServer(server.name, { enabled: false }), null);
      assert.equal(await old.deleteServer(server.name), false);
      assert.equal(await old.toggleServer(server.name), null);
    });
    assert.equal(view.calls.length, 1);
    await act(async () => { pending.resolve(principal("user-b")); await refresh; });
    assert.equal(hook.isAvailable, true); assert.equal(view.calls.length, 2);
  } finally { await view.close(); }
});

function DisabledHookProbe() {
  hook = useMCP({ enabled: false, canManageServers: true, listParams: params });
  return null;
}
test("disabled MCP hook never issues read, detail, or CRUD requests", async () => {
  const view = await harness(createElement(DisabledHookProbe), () => page());
  try {
    assert.equal(await hook.fetchServers(), false);
    assert.equal(await hook.getServer(server.name), null);
    assert.equal(await hook.createServer({ name: "other", transport: "streamable_http", url: "https://example.invalid/mcp" }), null);
    assert.equal(await hook.updateServer(server.name, { enabled: false }), null);
    assert.equal(await hook.deleteServer(server.name), false);
    assert.equal(await hook.toggleServer(server.name), null);
    assert.equal(view.calls.length, 0);
  } finally { await view.close(); }
});
