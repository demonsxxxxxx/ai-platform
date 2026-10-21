import assert from "node:assert/strict";
import test from "node:test";
import { writeFileSync } from "node:fs";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb";
import { authApi } from "../../../services/api/auth";
import { roleApi } from "../../../services/api/role";
import { adminUsersApi, type AdminUserSummary, type AdminUserDiagnosticsResponse, type AdminUserListResponse } from "../../../services/api/adminUsers";

const fixture = installSelectTestDom();
Object.assign(globalThis, { localStorage: window.localStorage, sessionStorage: window.sessionStorage });
installBrowserAuthTestDb();
const { act } = await import("react");
const { AuthProvider, useAuth } = await import("../../../hooks/useAuth");
const { WorkbenchUsersProjectionPanel } = await import("../WorkbenchProjectionPages");
await import("../../../i18n");
const snapshots: Record<string, string> = {};
test.after(() => {
  if (process.env.USERS_DOM_SNAPSHOTS) writeFileSync(process.env.USERS_DOM_SNAPSHOTS, JSON.stringify(snapshots, null, 2));
  fixture.close();
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function user(user_id: string): AdminUserSummary {
  return { user_id, display_name: user_id, status: "active", session_count: 1, run_count: 1, queued_run_count: 0, running_run_count: 0, succeeded_run_count: 1, failed_run_count: 0, cancelled_run_count: 0 };
}
const alice = user("synthetic-a"); const bob = user("synthetic-b");
function response(users = [alice, bob]): AdminUserListResponse {
  return { schema_version: "ai-platform.admin-user-diagnostics.v1", users, total: users.length, offset: 0, limit: 50 };
}
function diagnostics(user: AdminUserSummary): AdminUserDiagnosticsResponse {
  return { schema_version: "ai-platform.admin-user-diagnostics.v1", user, sessions: [], runs: [], audit: [], limits: { sessions: 20, runs: 50, audit: 50 } };
}
function snapshot(name: string, container: HTMLElement) {
  // Generated provider/portal IDs are not product state. Keep all text, classes,
  // data selectors, roles, disabled state and the complete element hierarchy.
  snapshots[name] = (container.innerHTML + Array.from(document.querySelectorAll('[role="dialog"]')).filter(node => !container.contains(node)).map(node => node.outerHTML).join("\n")).replace(/«[^»]*»/g, "REACT_ID");
}
async function mount(list: typeof adminUsersApi.list, admin = false) {
  const original = { list: adminUsersApi.list, diagnostics: adminUsersApi.diagnostics, bootstrap: authApi.bootstrapAuthContext, user: authApi.getCurrentUser, roles: roleApi.list };
  const requests: unknown[] = [];
  adminUsersApi.list = async options => { requests.push(options); return list(options); };
  authApi.bootstrapAuthContext = async request => ({ status: "ready", protocol_version: 2, generation: request!.generation });
  authApi.getCurrentUser = async () => ({ id: "synthetic-reader", tenant_id: "synthetic-tenant", username: "reader", email: "reader@example.test", roles: [], permissions: admin ? ["user:read", "user:admin"] : ["user:read"], is_admin: admin, is_active: true, created_at: "", updated_at: "" });
  roleApi.list = async () => ({ roles: [], total: 0, skip: 0, limit: 200 });
  let authenticated = false;
  function Page() { authenticated = useAuth().isAuthenticated; return <WorkbenchUsersProjectionPanel />; }
  const container = document.createElement("div"); document.body.append(container);
  const root = await createLobeTestRoot(container);
  await act(async () => { root.render(<AuthProvider><Page /></AuthProvider>); });
  assert.equal(authenticated, true);
  return { container, requests, async close() {
    await act(async () => root.unmount()); container.remove();
    adminUsersApi.list = original.list; adminUsersApi.diagnostics = original.diagnostics;
    authApi.bootstrapAuthContext = original.bootstrap; authApi.getCurrentUser = original.user; roleApi.list = original.roles;
  } };
}
function state(container: HTMLElement) { return container.querySelector('[data-workbench-projection-page="users"]')?.getAttribute("data-frontend-governance-state"); }
for (const [label, error, expected] of [["permission", "missing_permission:user:read", "forbidden"], ["ordinary", "synthetic service unavailable", "degraded"]] as const) {
  test(`Users loading and ${label} failure preserve bounded presentation`, async () => {
    const pending = deferred<AdminUserListResponse>(); const view = await mount(() => pending.promise);
    try {
      assert.equal(state(view.container), "loading"); snapshot(`${label}-loading`, view.container);
      await act(async () => pending.reject(new Error(error)));
      assert.equal(state(view.container), expected); snapshot(label, view.container);
      assert.equal(view.container.querySelectorAll("[data-user-run-debug]").length, 0);
      assert.deepEqual(view.requests, [{ limit: 50, search: "" }]);
    } finally { await view.close(); }
  });
}
for (const admin of [false, true]) {
  test(`Users successful ${admin ? "admin" : "read-only"} and empty projections retain controls`, async () => {
    const view = await mount(async () => response(), admin);
    try {
      assert.equal(state(view.container), "ready");
      assert.equal(view.container.querySelectorAll("[data-user-run-debug]").length, 2);
      snapshot(admin ? "admin-ready" : "readonly-ready", view.container);
      assert.deepEqual(view.requests, [{ limit: 50, search: "" }]);
    } finally { await view.close(); }
    const empty = await mount(async () => response([]), admin);
    try { assert.ok(empty.container.querySelector("[data-projection-empty-state]")); snapshot(admin ? "admin-empty" : "readonly-empty", empty.container); }
    finally { await empty.close(); }
  });
}
test("Users diagnostics switching and closing reject late responses", async () => {
  const view = await mount(async () => response());
  const first = deferred<AdminUserDiagnosticsResponse>(); const second = deferred<AdminUserDiagnosticsResponse>();
  const calls: string[] = [];
  adminUsersApi.diagnostics = id => { calls.push(id); return id === alice.user_id ? first.promise : second.promise; };
  const clickUser = async (id: string) => { await act(async () => { (view.container.querySelector(`[data-user-run-debug="${id}"]`) as HTMLButtonElement).click(); }); };
  try {
    await clickUser(alice.user_id); snapshot("diagnostics-loading", view.container);
    await clickUser(bob.user_id);
    await act(async () => first.resolve(diagnostics(alice)));
    assert.ok(document.querySelector('[data-admin-user-diagnostics="synthetic-b"]'));
    await act(async () => second.resolve(diagnostics(bob)));
    snapshot("diagnostics-ready", view.container);
    assert.deepEqual(calls, [alice.user_id, bob.user_id]);
    await act(async () => { (document.querySelector('[role="dialog"] button') as HTMLButtonElement).click(); });
    assert.equal(document.querySelector("[data-admin-user-diagnostics]"), null);
    adminUsersApi.diagnostics = async () => { throw new Error("synthetic diagnostics unavailable"); };
    await clickUser(alice.user_id);
    assert.match(document.body.textContent ?? "", /synthetic diagnostics unavailable/);
    snapshot("diagnostics-error", view.container);
    await act(async () => { (document.querySelector('[role="dialog"] button') as HTMLButtonElement).click(); });
    const late = deferred<AdminUserDiagnosticsResponse>(); adminUsersApi.diagnostics = () => late.promise;
    await clickUser(alice.user_id);
    await act(async () => { (document.querySelector('[role="dialog"] button') as HTMLButtonElement).click(); });
    await act(async () => late.reject(new Error("synthetic late error")));
    assert.equal(document.querySelector("[data-admin-user-diagnostics]"), null);
    snapshot("diagnostics-closed", view.container);
  } finally { await view.close(); }
});
