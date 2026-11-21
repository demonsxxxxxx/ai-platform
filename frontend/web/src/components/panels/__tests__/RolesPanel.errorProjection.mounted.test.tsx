import assert from "node:assert/strict";
import test from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot";
import type { ReactNode } from "react";
import type { RoleGovernanceOverviewResponse } from "../../../types/roleGovernance";
import type { User } from "../../../types/auth";
import { authApi } from "../../../services/api/auth";
import { roleApi } from "../../../services/api/role";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb";

const fixture = installSelectTestDom();
Object.assign(globalThis, { localStorage: window.localStorage, sessionStorage: window.sessionStorage });
test.after(() => fixture.close());
installBrowserAuthTestDb();
const { toast } = await import("@lobehub/ui/base-ui");
const { act, createElement } = await import("react");

const { AuthProvider, useAuth } = await import("../../../hooks/useAuth");
const { RolesPanel } = await import("../RolesPanel");
const { roleGovernanceApi } = await import("../../../services/api/roleGovernance");
const { apiRequestErrorFromResponse } = await import("../../../services/api/fetch");
await import("../../../i18n/index");

function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function principal(id = "user-a", roles = ["admin"]): User {
  return { id, tenant_id: "tenant-test", username: id, email: "synthetic@example.test", roles, permissions: ["role:read", "role:manage", "role:request"], is_admin: true, is_active: true, created_at: "", updated_at: "" };
}
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } }); }
function button(container: HTMLElement, text: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll("button")).find(node => node.textContent?.trim() === text);
  assert.ok(found, `Missing button ${text}: ${container.textContent}`); return found;
}
async function click(node: HTMLButtonElement) { await act(async () => { node.click(); }); }

type Request = { url: URL; init: RequestInit };
async function harness(child: ReactNode, fetcher?: (request: Request) => Promise<Response> | Response, initialUser = principal()) {
  const old = { bootstrap: authApi.bootstrapAuthContext, user: authApi.getCurrentUser, roles: roleApi.list, fetch: globalThis.fetch, success: toast.success, error: toast.error };
  let user = initialUser; let auth!: ReturnType<typeof useAuth>;
  const notices: string[] = []; const calls: Request[] = [];
  authApi.bootstrapAuthContext = async request => ({ status: "ready", protocol_version: 2, generation: request!.generation });
  authApi.getCurrentUser = async () => user;
  roleApi.list = async () => ({ roles: [], total: 0, skip: 0, limit: 200 });
  toast.success = (message) => { notices.push(`success:${String(message)}`); return { id: "test", close() { }, update() { } }; };
  toast.error = (message) => { notices.push(`error:${String(message)}`); return { id: "test", close() { }, update() { } }; };
  globalThis.fetch = (async (url, init = {}) => {
    const request = { url: new URL(String(url), "http://localhost"), init }; calls.push(request);
    if (fetcher) return fetcher(request);
    throw new Error(`Unexpected request ${request.url.pathname}`);
  }) as typeof fetch;
  function Probe({ children }: { children: ReactNode }) { auth = useAuth(); return children; }
  const container = document.createElement("div"); document.body.append(container); const root = await createLobeTestRoot(container);
  const render = async (next: ReactNode) => { await act(async () => { root.render(createElement(AuthProvider, null, createElement(Probe, { children: next }))); }); };
  await render(child);
  assert.equal(auth.isAuthenticated, true, "real AuthProvider must authenticate synthetic principal");
  let closed = false;
  return {
    container, calls, notices, render, async setUser(next: User) { user = next; await act(async () => { await auth.refreshUser(); }); },
    async close() { if (closed) return; closed = true; await act(async () => root.unmount()); container.remove(); Object.assign(authApi, { bootstrapAuthContext: old.bootstrap, getCurrentUser: old.user }); roleApi.list = old.roles; globalThis.fetch = old.fetch; toast.success = old.success; toast.error = old.error; },
  };
}

function overview(label: string): RoleGovernanceOverviewResponse { return { governance: { projection: "safe_role_governance", tenant_id: label, workspace_id: "default", degraded: false, audit_required: true, rollback_available: true, secret_material_projected: false }, role_directory: { roles: [{ role_id: "auditor", name: label, description: "fixture", scope: "tenant", capabilities: [], requestable: true, assignable: true }] }, scope: { tenant_id: label, workspace_id: "default", current_department_id: "platform", departments: [], workspaces: [], skill_availability: [] }, requests: [{ request_id: "req-fixture", requester_id: "fixture", target_type: "role", target_id: "auditor", status: "pending", reason: "fixture", created_at: null }], audit: [] }; }
async function roles(run: (view: Awaited<ReturnType<typeof harness>>) => Promise<void>, methods: Partial<typeof roleGovernanceApi>, user = principal()) { const old = { ...roleGovernanceApi }; Object.assign(roleGovernanceApi, methods); const view = await harness(createElement(RolesPanel), undefined, user); try { await run(view); } finally { await view.close(); Object.assign(roleGovernanceApi, old); } }
function state(view: Awaited<ReturnType<typeof harness>>) { return view.container.querySelector('[data-role-plaza-shell]')?.getAttribute('data-frontend-governance-state'); }

const secret = "raw-secret-password-do-not-project";
for (const [status, expected] of [[403, "forbidden"], [401, "degraded"], [503, "degraded"]] as const) {
  test(`overview HTTP ${status} retains safe ${expected} classification`, async () => {
    await roles(async view => {
      assert.equal(state(view), expected);
      assert.doesNotMatch(view.container.textContent!, new RegExp(secret));
      assert.equal(view.notices.length, 0);
    }, {
      getOverview: async () => {
        throw await apiRequestErrorFromResponse(json({ detail: `missing_permission:role:read ${secret}` }, status));
      }
    });
  });
}
for (const [label, error] of [
  ["network", new TypeError(`Failed to fetch ${secret}`)],
  ["unknown", { status: 403, message: `missing_permission:role:read ${secret}` }],
] as const) {
  test(`overview ${label} failure remains degraded without raw diagnostics`, async () => {
    const warnings: unknown[][] = [];
    const oldWarn = console.warn;
    console.warn = (...args: unknown[]) => { warnings.push(args); };
    try {
      await roles(async view => {
        assert.equal(state(view), "degraded");
        assert.doesNotMatch(view.container.textContent!, new RegExp(secret));
        assert.doesNotMatch(`${warnings.flat().map(String).join(" ")} ${JSON.stringify(warnings)}`, new RegExp(secret));
        assert.equal(view.notices.length, 0);
      }, { getOverview: async () => { throw error; } });
    } finally { console.warn = oldWarn; }
  });
}
test("normal overview is ready and request permissions still gate actions", async () => {
  await roles(async view => {
    assert.equal(state(view), "ready");
    assert.match(view.container.textContent!, /normal-overview/);
    assert.ok(button(view.container, "批准"));
    assert.ok(button(view.container, "拒绝"));
  }, { getOverview: async () => overview("normal-overview") });
  await roles(async view => {
    assert.equal(state(view), "ready");
    assert.equal(Array.from(view.container.querySelectorAll("button")).some(node => ["批准", "拒绝"].includes(node.textContent?.trim() ?? "")), false);
    assert.equal(button(view.container, "申请角色").disabled, true);
  }, { getOverview: async () => overview("readonly-overview") }, { ...principal(), roles: [], is_admin: false, permissions: ["role:read"] });
});
test("empty overview remains ready with safe empty states", async () => {
  const empty = overview("empty-overview");
  empty.role_directory.roles = [];
  empty.requests = [];
  await roles(async view => {
    assert.equal(state(view), "ready");
    assert.equal(view.container.querySelectorAll("article").length, 0);
    assert.equal(Array.from(view.container.querySelectorAll("button")).some(node => node.textContent?.trim() === "批准"), false);
  }, { getOverview: async () => empty });
});
test("pending approval remains disabled and rejected operation reports failure", async () => {
  const pending = deferred<never>();
  let calls = 0;
  await roles(async view => {
    const approve = button(view.container, "批准");
    await click(approve);
    assert.equal(approve.disabled, true);
    await click(approve);
    assert.equal(calls, 1);
    assert.equal(view.notices.length, 0);
    const error = await apiRequestErrorFromResponse(json({ detail: secret }, 503));
    await act(async () => pending.reject(error));
    assert.equal(button(view.container, "批准").disabled, false);
    assert.equal(view.notices.length, 1);
    assert.ok(view.notices[0].startsWith("error:"));
    assert.doesNotMatch(view.notices[0], new RegExp(secret));
    assert.equal(state(view), "ready");
  }, { getOverview: async () => overview("pending-overview"), approveRequest: async () => { calls++; return pending.promise; } });
});
