import assert from "node:assert/strict";
import test from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot";
import { authApi, type PrincipalResponseWire } from "../../../services/api/auth";
import { getAccessToken } from "../../../services/api/token";
import { SESSION_REFRESHED_EVENT } from "../../../services/api/tokenManager";
import { roleApi } from "../../../services/api/role";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb";
import type { User } from "../../../types/auth";
import type { MCPPluginResponse } from "../../../types/mcp";

const fixture = installSelectTestDom();
Object.assign(globalThis, { localStorage: window.localStorage, sessionStorage: window.sessionStorage });
installBrowserAuthTestDb();
test.after(() => fixture.close());
const { act, createElement } = await import("react");
const { AuthProvider, useAuth } = await import("../../../hooks/useAuth");
const { PluginMarketPanel } = await import("../PluginMarketPanel");
await import("../../../i18n");
const plugin: MCPPluginResponse = { name: "enterprise", display_name: "企业网关", description: "本人已授权的企业业务",
  enabled: true, status: "active", can_edit: false, installation: "system_preinstalled", user_authorization: "none" };
const principal = (id = "user-a"): User => ({ id, tenant_id: "tenant", username: id, email: "synthetic@example.test",
  roles: ["user"], permissions: [], department_ids: ["100"], is_active: true, created_at: "", updated_at: "" });
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
const page = (servers = [plugin]) => json({ servers, total: servers.length, skip: 0, limit: 200 });
const tools = (name: string) => json({ server_name: name, tools: [{ name: `${name}::lookup`, description: "查询已授权数据", parameters: [] }], count: 1, unavailable_reason: null });

async function harness(fetcher: (url: URL, init: RequestInit) => Promise<Response> | Response, initialUser = principal()) {
  const old = { fetch: globalThis.fetch, bootstrap: authApi.bootstrapAuthContext, user: authApi.getCurrentUser, roles: roleApi.list };
  let user = initialUser; let auth!: ReturnType<typeof useAuth>;
  const calls: { url: URL; init: RequestInit }[] = [];
  authApi.bootstrapAuthContext = async request => ({ status: "ready", protocol_version: 2, generation: request!.generation });
  authApi.getCurrentUser = async () => user;
  roleApi.list = async () => ({ roles: [], total: 0, skip: 0, limit: 200 });
  globalThis.fetch = (async (input, init = {}) => {
    const url = new URL(String(input), "http://localhost"); calls.push({ url, init }); return fetcher(url, init);
  }) as typeof fetch;
  function Probe() { auth = useAuth(); return createElement(PluginMarketPanel); }
  const container = document.createElement("div"); document.body.append(container);
  const root = await createLobeTestRoot(container);
  await act(async () => root.render(createElement(AuthProvider, null, createElement(Probe))));
  return { container, calls, async setUser(next: User) { user = next; await act(async () => { await auth.refreshUser(); }); },
    async sessionRefresh(isAdmin: boolean) {
      const refreshed: PrincipalResponseWire = { user_id: user.id, tenant_id: user.tenant_id!, display_name: user.username,
        department_id: user.department_id ?? "", direct_department_ids: user.direct_department_ids, department_ids: user.department_ids,
        department_authority_status: user.department_authority_status, roles: user.roles, permissions: user.permissions ?? [],
        is_admin: isAdmin, source: "test" };
      await act(async () => window.dispatchEvent(new CustomEvent(SESSION_REFRESHED_EVENT, {
        detail: { principal: refreshed, request_marker: getAccessToken(), authorization_changed: true },
      })));
      assert.equal(auth.isLoading, false, "session-refresh projection update must not rely on a loading transition");
      assert.equal(auth.user?.is_admin, isAdmin);
    },
    async close() { await act(async () => root.unmount()); container.remove(); globalThis.fetch = old.fetch;
      authApi.bootstrapAuthContext = old.bootstrap; authApi.getCurrentUser = old.user; roleApi.list = old.roles; } };
}
function button(root: ParentNode, text: string) {
  const result = [...root.querySelectorAll<HTMLButtonElement>("button")].find(node => node.textContent === text);
  assert.ok(result, text); return result;
}
function change(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

test("ordinary users see registry-backed preinstalled plugins without installation or connection editing", async () => {
  const view = await harness(url => url.pathname.endsWith("/tools") ? tools("enterprise") : page());
  try {
    assert.match(view.container.textContent!, /企业网关|系统预装 · 已安装|可用/);
    assert.doesNotMatch(view.container.textContent!, /本地文件服务器|添加 MCP|卸载/);
    assert.ok(view.calls[0].url.searchParams.get("view") === "plugins");
    assert.ok(view.calls.every(call => !call.init.method || call.init.method === "GET"));
    assert.equal(view.container.querySelector('input[type="url"]'), null);
    const trigger = button(view.container, "查看详情");
    await act(async () => { trigger.focus(); trigger.click(); });
    assert.match(document.querySelector("[data-mcp-tool-detail]")!.textContent!, /查询已授权数据/);
    await act(async () => document.querySelector<HTMLButtonElement>("[data-mcp-tool-detail-close]")!.click());
    await act(async () => { await new Promise(requestAnimationFrame); });
    assert.equal(document.activeElement, trigger);
  } finally { await view.close(); }
});

test("enterprise Gateway stays installed without a tool lookup, count or tool drawer", async () => {
  const view = await harness(() => page([{ ...plugin, integration_kind: "enterprise_gateway" }]));
  try {
    assert.match(view.container.textContent!, /系统预装 · 已安装/);
    assert.match(view.container.textContent!, /工具由企业网关管理/);
    assert.doesNotMatch(view.container.textContent!, /查看详情|个工具/);
    assert.equal(view.calls.some(call => call.url.pathname.endsWith("/tools")), false);
  } finally { await view.close(); }
});

test("Gateway preference is saved per card, survives reload and preserves the last saved state on failure", async () => {
  let enabled = true;
  let failWrite = false;
  const view = await harness((url, init) => {
    if (url.pathname.endsWith("/conversation-preference")) {
      assert.equal(url.pathname, "/api/mcp/enterprise/conversation-preference");
      assert.equal(init.method, "PUT");
      if (failWrite) return json({ detail: "not_saved" }, 503);
      enabled = JSON.parse(String(init.body)).use_in_conversation;
      return json({ use_in_conversation: enabled });
    }
    return page([{ ...plugin, integration_kind: "enterprise_gateway", use_in_conversation: enabled }]);
  });
  try {
    const control = () => view.container.querySelector<HTMLButtonElement>('[role="switch"]')!;
    assert.equal(control().getAttribute("aria-checked"), "true");
    await act(async () => control().click());
    assert.equal(control().getAttribute("aria-checked"), "false");
    const search = view.container.querySelector<HTMLInputElement>('input[placeholder="搜索插件"]')!;
    await act(async () => change(search, "no match"));
    assert.equal(control(), null);
    await act(async () => change(search, ""));
    assert.equal(control().getAttribute("aria-checked"), "false");
    await act(async () => button(view.container, "刷新").click());
    assert.equal(control().getAttribute("aria-checked"), "false");
    failWrite = true;
    await act(async () => control().click());
    assert.equal(control().getAttribute("aria-checked"), "false");
    assert.match(view.container.querySelector('[role="alert"]')!.textContent!, /未能保存设置/);
    failWrite = false;
    await act(async () => control().click());
    assert.equal(control().getAttribute("aria-checked"), "true");
    assert.equal(view.calls.some(call => call.url.pathname.endsWith("/tools")), false);
  } finally { await view.close(); }
});

test("one unavailable plugin keeps healthy plugins and distinguishes authorization", async () => {
  const view = await harness(url => {
    if (url.pathname.endsWith("/broken/tools")) return json({ server_name: "broken", tools: [], count: 0, unavailable_reason: "discovery_failed" });
    if (url.pathname.endsWith("/auth/tools")) return json({ server_name: "auth", tools: [], count: 0, unavailable_reason: "authorization_required" });
    return url.pathname.endsWith("/tools") ? tools("enterprise") : page([plugin, { ...plugin, name: "broken" }, { ...plugin, name: "auth" }]);
  });
  try {
    assert.equal(view.container.querySelectorAll("[data-plugin-card]").length, 3);
    assert.match(view.container.querySelector('[data-plugin-card="enterprise"]')!.textContent!, /可用/);
    assert.match(view.container.querySelector('[data-plugin-card="broken"]')!.textContent!, /连接异常/);
    assert.match(view.container.querySelector('[data-plugin-card="auth"]')!.textContent!, /待授权/);
    assert.ok(view.calls.every(call => !call.init.method || call.init.method === "GET"));
  } finally { await view.close(); }
});

test("plugin catalog reads every page and rejects an incomplete later page", async () => {
  let incomplete = false;
  const plugins = Array.from({ length: 201 }, (_, index) => ({ ...plugin, name: `plugin-${index}` }));
  const view = await harness(url => {
    const name = url.pathname.split("/").at(-2)!;
    if (url.pathname.endsWith("/tools")) return tools(name);
    const skip = Number(url.searchParams.get("skip"));
    return json({ servers: incomplete && skip > 0 ? [] : plugins.slice(skip, skip + 200), total: 201, skip, limit: 200 });
  });
  try {
    assert.equal(view.container.querySelectorAll("[data-plugin-card]").length, 201);
    assert.deepEqual(view.calls.filter(call => call.url.pathname === "/api/mcp/").map(call => call.url.searchParams.get("skip")), ["0", "200"]);
    incomplete = true;
    await act(async () => button(view.container, "刷新").click());
    assert.match(view.container.textContent!, /插件目录暂时无法加载/);
    assert.equal(view.container.querySelectorAll("[data-plugin-card]").length, 0);
  } finally { await view.close(); }
});

test("failed catalog never appears as successful empty and explicit retry only reads", async () => {
  let failed = true;
  const view = await harness(url => url.pathname.endsWith("/tools") ? tools("enterprise") : failed ? json({ detail: "private" }, 503) : page());
  try {
    assert.match(view.container.textContent!, /插件目录暂时无法加载/);
    assert.doesNotMatch(view.container.textContent!, /暂无分配|private/);
    failed = false;
    await act(async () => button(view.container, "重试").click());
    assert.match(view.container.textContent!, /系统预装 · 已安装/);
  } finally { await view.close(); }
});

test("old identity plugin reads and open details cannot survive authority changes", async () => {
  let resolve!: (response: Response) => void; let reads = 0;
  const pending = new Promise<Response>(done => { resolve = done; });
  const view = await harness(url => url.pathname.endsWith("/tools") ? tools("enterprise") : ++reads === 1 ? pending : page([]));
  try {
    await view.setUser(principal("user-b"));
    assert.match(view.container.textContent!, /暂无分配给你的插件/);
    await act(async () => resolve(page()));
    assert.equal(view.container.querySelectorAll("[data-plugin-card]").length, 0);
  } finally { await view.close(); }
});

test("session-refresh admin revocation fences visible details and old pending directory reads", async () => {
  let reads = 0;
  let resolveOld!: (response: Response) => void;
  let resolveNew!: (response: Response) => void;
  const old = new Promise<Response>(done => { resolveOld = done; });
  const fresh = new Promise<Response>(done => { resolveNew = done; });
  const user = { ...principal(), is_admin: true, department_id: "100", direct_department_ids: ["100"], department_authority_status: "available" as const };
  const view = await harness(url => url.pathname.endsWith("/tools") ? tools("enterprise") : ++reads === 1 ? page() : reads === 2 ? old : fresh, user);
  try {
    await act(async () => button(view.container, "查看详情").click());
    assert.ok(document.querySelector("[data-mcp-tool-detail]"));
    await view.sessionRefresh(false);
    assert.equal(reads, 2, "is_admin-only changes start a fresh permission-scoped read");
    assert.equal(document.querySelector("[data-mcp-tool-detail]"), null);
    assert.equal(view.container.querySelectorAll("[data-plugin-card]").length, 0);
    await view.sessionRefresh(true);
    assert.equal(reads, 3);
    await act(async () => resolveOld(page()));
    assert.equal(view.container.querySelectorAll("[data-plugin-card]").length, 0, "the revoked projection cannot restore a later generation");
    await act(async () => resolveNew(page([])));
    assert.match(view.container.textContent!, /暂无分配给你的插件/);
  } finally { await view.close(); }
});

test("multiple file authorization plugins return focus to the actual invoking card", async () => {
  const view = await harness(url => {
    if (url.pathname.endsWith("company-credential-handoff")) return json({ credential: "synthetic-handoff" });
    if (url.pathname === "/api/profile-drive/status") return json({ status: "connected", connected: true, reauthRequired: false });
    return url.pathname.endsWith("/tools") ? tools("drive") : page(["first", "second"].map(name => ({
      ...plugin, name, display_name: name, user_authorization: "profile_drive" as const,
    })));
  });
  try {
    const trigger = button(view.container.querySelector('[data-plugin-card="first"]')!, "重新授权");
    await act(async () => { trigger.focus(); trigger.click(); });
    await act(async () => document.querySelector<HTMLButtonElement>('[role="dialog"] button[aria-label="关闭本地文件服务器认证"]')!.click());
    await act(async () => { await new Promise(requestAnimationFrame); });
    assert.equal(document.activeElement, trigger);
  } finally { await view.close(); }
});

test("explicit file authorization reuses the authenticated password-only path", async () => {
  const view = await harness((url) => {
    if (url.pathname.endsWith("company-credential-handoff")) return json({ credential: "synthetic-handoff" });
    if (url.pathname === "/api/profile-drive/status") return json({ status: "connected", connected: true, reauthRequired: false });
    if (url.pathname === "/api/profile-drive/connect") return json({ status: "connected", connected: true, reauthRequired: false });
    return url.pathname.endsWith("/tools") ? tools("drive") : page([{ ...plugin, name: "drive", display_name: "文件盘", user_authorization: "profile_drive" }]);
  });
  try {
    const trigger = button(view.container, "重新授权");
    await act(async () => trigger.click());
    const dialog = document.querySelector('[role="dialog"]')!;
    const password = dialog.querySelector<HTMLInputElement>("#file-server-password")!;
    assert.equal(password.type, "password");
    assert.equal(dialog.querySelector("#file-server-account"), null);
    await act(async () => change(password, "synthetic-password"));
    await act(async () => dialog.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
    const request = view.calls.find(call => call.url.pathname === "/api/profile-drive/connect")!;
    assert.deepEqual(JSON.parse(String(request.init.body)), { password: "synthetic-password" });
    assert.equal(new Headers(request.init.headers).get("Authorization"), "Bearer synthetic-handoff");
    assert.equal(request.init.credentials, "omit");
    assert.equal(view.calls.some(call => call.url.pathname.includes("/install")), false);
  } finally { await view.close(); }
});
