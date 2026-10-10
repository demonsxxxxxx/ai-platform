import assert from "node:assert/strict";
import test from "node:test";
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import type { ReactNode } from "react";
import type { User } from "../../../types/auth";
import type { MemoryPolicy } from "../../../services/api/memory";
import { capabilityDistributionApi, type CapabilityDistribution } from "../../../services/api/capabilityDistribution";
import { authApi } from "../../../services/api/auth";
import { roleApi } from "../../../services/api/role";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb";
import toast from "react-hot-toast";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/memory" });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, localStorage: dom.window.localStorage, sessionStorage: dom.window.sessionStorage, HTMLElement: dom.window.HTMLElement, CustomEvent: dom.window.CustomEvent, IS_REACT_ACT_ENVIRONMENT: true });
Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
installBrowserAuthTestDb();
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { AuthProvider, useAuth } = await import("../../../hooks/useAuth");
const { MemoryPanel } = await import("../MemoryPanel");
const { SkillDistributionGovernancePanel } = await import("../SkillDistributionGovernancePanel");
const { DepartmentDirectorySelector } = await import("../DepartmentDirectorySelector");
await import("../../../i18n/index");

function deferred<T>() {
  let resolve!: (value: T) => void; let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function principal(id = "user-a", roles = ["admin"]): User {
  return { id, tenant_id: "tenant-test", username: id, email: "synthetic@example.test", roles, permissions: [], is_active: true, created_at: "", updated_at: "" };
}
function distribution(id: string, visible = true): CapabilityDistribution {
  return { id: `dist-${id}`, tenantId: "tenant-test", capabilityKind: "skill", capabilityId: id, status: "active", visibleToUser: visible, scopeMode: "allowlist", departmentIds: [], allowedRoles: [`role-${id}`], metadata: {} };
}
function policy(agent = "document-review", days = 90, user = "user-a"): MemoryPolicy {
  return { tenant_id: "tenant-test", workspace_id: "default", user_id: user, agent_id: agent, memory_enabled: true, long_term_memory_enabled: false, retention_days: days, source: "user", reason: "", updated_by: user, updated_at: null };
}
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } }); }
function input(container: HTMLElement, label: string): HTMLInputElement {
  const result = Array.from(container.querySelectorAll("label")).find(node => node.textContent?.includes(label))?.querySelector("input");
  assert.ok(result, `Missing input ${label}`); return result;
}
function button(container: HTMLElement, text: string): HTMLButtonElement {
  const found = Array.from(container.querySelectorAll("button")).find(node => node.textContent?.trim() === text);
  assert.ok(found, `Missing button ${text}: ${container.textContent}`); return found;
}
function reactProps(node: Element) {
  const key = Object.keys(node).find(key => key.startsWith("__reactProps$")); assert.ok(key);
  return (node as unknown as Record<string, unknown>)[key] as { onChange?: (event: { target: { value: string; checked?: boolean } }) => void; onClick?: () => void };
}
async function change(node: HTMLInputElement, value: string) {
  await act(async () => { reactProps(node).onChange?.({ target: { value } }); });
}
async function click(node: HTMLButtonElement) { await act(async () => { node.click(); }); }

type Request = { url: URL; init: RequestInit };
async function harness(child: ReactNode, fetcher?: (request: Request) => Promise<Response> | Response) {
  const old = { bootstrap: authApi.bootstrapAuthContext, user: authApi.getCurrentUser, roles: roleApi.list, fetch: globalThis.fetch, success: toast.success, error: toast.error };
  let user = principal(); let auth!: ReturnType<typeof useAuth>;
  const notices: string[] = []; const calls: Request[] = [];
  authApi.bootstrapAuthContext = async request => ({ status: "ready", protocol_version: 2, generation: request!.generation });
  authApi.getCurrentUser = async () => user;
  roleApi.list = async () => ({ roles: [], total: 0, skip: 0, limit: 200 });
  toast.success = ((message: unknown) => { notices.push(`success:${String(message)}`); return "test"; }) as typeof toast.success;
  toast.error = ((message: unknown) => { notices.push(`error:${String(message)}`); return "test"; }) as typeof toast.error;
  globalThis.fetch = (async (url, init = {}) => {
    const request = { url: new URL(String(url), "http://localhost"), init }; calls.push(request);
    if (fetcher) return fetcher(request);
    throw new Error(`Unexpected request ${request.url.pathname}`);
  }) as typeof fetch;
  function Probe({ children }: { children: ReactNode }) { auth = useAuth(); return children; }
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  const render = async (next: ReactNode) => { await act(async () => { root.render(createElement(AuthProvider, null, createElement(Probe, { children: next }))); }); };
  await render(child);
  assert.equal(auth.isAuthenticated, true, "real AuthProvider must authenticate synthetic principal");
  let closed = false;
  return { container, calls, notices, render, async setUser(next: User) { user = next; await act(async () => { await auth.refreshUser(); }); },
    async close() { if (closed) return; closed = true; await act(async () => root.unmount()); container.remove(); Object.assign(authApi, { bootstrapAuthContext: old.bootstrap, getCurrentUser: old.user }); roleApi.list = old.roles; globalThis.fetch = old.fetch; toast.success = old.success; toast.error = old.error; },
  };
}
const acl = (id: string) => createElement(SkillDistributionGovernancePanel, { selectedSkillId: id, selectedSkill: null });
async function withAcl(run: (view: Awaited<ReturnType<typeof harness>>) => Promise<void>, overrides: Partial<typeof capabilityDistributionApi> = {}) {
  const old = { ...capabilityDistributionApi };
  Object.assign(capabilityDistributionApi, { list: async () => [distribution("a"), distribution("b", false)], departmentDirectory: async () => [], update: async (_kind: unknown, id: string) => distribution(id), ...overrides });
  const view = await harness(acl("a"));
  try { await run(view); } finally { await view.close(); Object.assign(capabilityDistributionApi, old); }
}
function defaultMemory({ url, init }: Request): Response {
  if (url.pathname.endsWith("/admin/memory/policies")) return json({ memory_policies: [], summary: {} });
  if (url.pathname.endsWith("/records")) return json({ memory_records: [] });
  if (url.pathname.endsWith("/policy")) {
    const payload = init.method === "PUT" ? JSON.parse(String(init.body)) : null;
    return json({ memory_policy: policy(payload?.agent_id ?? url.searchParams.get("agent_id") ?? undefined, payload?.retention_days ?? 90) });
  }
  throw new Error(`Unexpected ${url.pathname}`);
}

test("ACL late save cannot replace B draft, finish B save, or claim B success", async () => {
  const a = deferred<CapabilityDistribution>(); const b = deferred<CapabilityDistribution>(); const writes: string[] = [];
  await withAcl(async view => {
    const saveA = view.container.querySelector<HTMLButtonElement>("[data-skill-distribution-save]")!;
    await click(saveA); await click(saveA); assert.deepEqual(writes, ["a"]);
    await view.render(acl("b"));
    assert.equal(view.container.querySelector<HTMLInputElement>("[data-skill-distribution-visible]")!.checked, false);
    await click(view.container.querySelector("[data-skill-distribution-save]")!);
    await act(async () => a.resolve(distribution("a", true)));
    assert.equal(view.container.querySelector<HTMLInputElement>("[data-skill-distribution-visible]")!.checked, false);
    assert.equal(view.container.querySelector<HTMLButtonElement>("[data-skill-distribution-save]")!.disabled, true);
    assert.doesNotMatch(view.container.textContent!, /已保存/);
    await act(async () => b.resolve(distribution("b", false)));
    assert.equal(view.container.querySelector<HTMLButtonElement>("[data-skill-distribution-save]")!.disabled, false);
    assert.deepEqual(writes, ["a", "b"]);
    assert.match(view.container.textContent!, /已保存/);
  }, { update: async (_kind, id) => { writes.push(id); return id === "a" ? a.promise : b.promise; } });
});

test("ACL late failure is silent after selection change; current errors remain sanitized", async () => {
  const pending = deferred<CapabilityDistribution>(); let writes = 0;
  await withAcl(async view => {
    await click(view.container.querySelector("[data-skill-distribution-save]")!);
    await view.render(acl("b")); await act(async () => pending.reject(new Error("private-error-details")));
    assert.equal(view.container.querySelector('[role="alert"]'), null);
    await click(view.container.querySelector("[data-skill-distribution-save]")!);
    assert.ok(view.container.querySelector('[role="alert"]')); assert.doesNotMatch(view.container.textContent!, /private-error-details/);
  }, { update: async () => { if (++writes === 1) return pending.promise; throw new Error("private-error-details"); } });
});

test("ACL latest refresh owns list and unmounted save cannot alter replacement editor", async () => {
  const oldLoad = deferred<CapabilityDistribution[]>(); const currentLoad = deferred<CapabilityDistribution[]>(); const saved = deferred<CapabilityDistribution>(); let reads = 0;
  await withAcl(async view => {
    const refresh = view.container.querySelector<HTMLButtonElement>('button[aria-label="刷新访问范围"]') ?? view.container.querySelector<HTMLButtonElement>("button")!;
    const callback = reactProps(refresh).onClick!;
    await act(async () => { callback(); callback(); });
    await act(async () => currentLoad.resolve([distribution("a", false)]));
    await act(async () => oldLoad.resolve([distribution("a", true)]));
    assert.equal(view.container.querySelector<HTMLInputElement>("[data-skill-distribution-visible]")!.checked, false);
    await click(view.container.querySelector("[data-skill-distribution-save]")!);
    await view.render(null); await view.render(acl("b"));
    await act(async () => saved.resolve(distribution("a")));
    assert.doesNotMatch(view.container.textContent!, /role-a/);
  }, { list: async () => { reads++; return reads === 2 ? oldLoad.promise : reads === 3 ? currentLoad.promise : [distribution("a"), distribution("b", false)]; }, update: () => saved.promise });
});

test("open department choices become disabled during save", async () => {
  let changes = 0; const props = { directory: [{ authorityId: "d", directoryId: "1", name: "d", path: "d", children: [], reason: null, selectable: true }], loadError: null, selectedAuthorityIds: [], onChange: () => { changes++; } };
  const view = await harness(createElement(DepartmentDirectorySelector, props));
  try {
    await click(view.container.querySelector('button[aria-haspopup="listbox"]')!);
    await view.render(createElement(DepartmentDirectorySelector, { ...props, disabled: true }));
    const option = view.container.querySelector<HTMLButtonElement>('[role="option"]')!;
    assert.equal(option.disabled, true); await click(option); assert.equal(changes, 0);
  } finally { await view.close(); }
});

test("Memory old policy and admin reads cannot replace a new agent scope", async () => {
  const oldPolicy = deferred<Response>(); const oldAdmin = deferred<Response>(); let policyReads = 0; let adminReads = 0;
  const view = await harness(createElement(MemoryPanel), request => {
    if (request.url.pathname.endsWith("/memory/policy") && ++policyReads === 2) return oldPolicy.promise;
    if (request.url.pathname.endsWith("/admin/memory/policies") && ++adminReads === 2) return oldAdmin.promise;
    return defaultMemory(request);
  });
  try {
    await click(button(view.container, "刷新"));
    await change(input(view.container, "专家公开 ID"), "agent-b");
    await act(async () => { oldPolicy.resolve(json({ memory_policy: policy("document-review", 365) })); oldAdmin.resolve(json({ memory_policies: [policy("old-admin", 365)], summary: {} })); });
    assert.equal(input(view.container, "保留天数").value, "90"); assert.doesNotMatch(view.container.textContent!, /old-admin/);
    assert.match(view.container.textContent!, /agent-b/);
  } finally { await view.close(); }
});

test("clearing session releases loading and suppresses the old records result", async () => {
  const records = deferred<Response>();
  const view = await harness(createElement(MemoryPanel), request => request.url.pathname === "/api/ai/memory/records" ? records.promise : defaultMemory(request));
  try {
    await change(input(view.container, "会话 ID"), "session-a");
    assert.equal(button(view.container, "刷新").disabled, true);
    await change(input(view.container, "会话 ID"), "");
    assert.equal(button(view.container, "刷新").disabled, false);
    await act(async () => records.resolve(json({ memory_records: [{ memory_record_id: "stale-record", content: "old record" }] })));
    assert.doesNotMatch(view.container.textContent!, /old record/); assert.equal(button(view.container, "刷新").disabled, false);
  } finally { await view.close(); }
});

test("Memory save is single flight; late receipt after navigation has no toast or old-scope refresh", async () => {
  const save = deferred<Response>(); let writes = 0;
  const view = await harness(createElement(MemoryPanel), request => { if (request.init.method === "PUT") { writes++; return save.promise; } return defaultMemory(request); });
  try {
    const callback = reactProps(button(view.container, "保存策略")).onClick!;
    await act(async () => { callback(); callback(); }); assert.equal(writes, 1);
    await change(input(view.container, "专家公开 ID"), "agent-b"); const count = view.calls.length;
    await act(async () => save.resolve(json({ memory_policy: policy("document-review", 365) })));
    assert.equal(view.calls.length, count); assert.equal(input(view.container, "保留天数").value, "90"); assert.deepEqual(view.notices, []);
  } finally { await view.close(); }
});

test("Memory save failure after unmount is silent; current generic errors omit private details", async () => {
  const save = deferred<Response>(); let writes = 0;
  const view = await harness(createElement(MemoryPanel), request => { if (request.init.method === "PUT") { if (++writes === 1) return save.promise; throw new Error("private-network-details"); } return defaultMemory(request); });
  try {
    await click(button(view.container, "保存策略")); await view.render(null);
    await act(async () => save.reject(new Error("private-network-details"))); assert.deepEqual(view.notices, []);
    await view.render(createElement(MemoryPanel)); await click(button(view.container, "保存策略"));
    assert.equal(view.notices.length, 1); assert.doesNotMatch(view.notices[0], /private-network-details/);
    assert.doesNotMatch(view.container.textContent!, /private-network-details/);
  } finally { await view.close(); }
});

test("Memory auth replacement and admin revocation clear old projections and fence old reads", async () => {
  const admin = deferred<Response>(); let adminReads = 0;
  const view = await harness(createElement(MemoryPanel), request => { if (request.url.pathname.endsWith("/admin/memory/policies") && ++adminReads === 2) return admin.promise; return defaultMemory(request); });
  try {
    await click(button(view.container, "刷新")); await view.setUser(principal("user-b", []));
    await act(async () => admin.resolve(json({ memory_policies: [policy("stale-admin")], summary: {} })));
    assert.doesNotMatch(view.container.textContent!, /stale-admin/);
    assert.equal(view.container.querySelector('[data-frontend-governance-state="ready"]') !== null, true);
  } finally { await view.close(); }
});

test("ACL A-B-A navigation rejects the earlier A receipt rather than replacing the new A draft", async () => {
  const save = deferred<CapabilityDistribution>();
  await withAcl(async view => {
    await click(view.container.querySelector("[data-skill-distribution-save]")!);
    await view.render(acl("b")); await view.render(acl("a"));
    await act(async () => { view.container.querySelector<HTMLInputElement>("[data-skill-distribution-visible]")!.click(); });
    await act(async () => save.resolve(distribution("a", true)));
    assert.equal(view.container.querySelector<HTMLInputElement>("[data-skill-distribution-visible]")!.checked, false);
    assert.doesNotMatch(view.container.textContent!, /已保存/);
  }, { update: () => save.promise });
});

test("Memory cleanup receipt after workspace change does not refresh or toast for the new scope", async () => {
  const cleanup = deferred<Response>();
  const view = await harness(createElement(MemoryPanel), request => request.init.method === "POST" ? cleanup.promise : defaultMemory(request));
  try {
    await click(button(view.container, "保留期清理")); await change(input(view.container, "工作区"), "workspace-b");
    const count = view.calls.length;
    await act(async () => cleanup.resolve(json({ deleted_count: 8, memory_records: [] })));
    assert.deepEqual(view.notices, []); assert.equal(view.calls.length, count);
    assert.equal(button(view.container, "保留期清理").disabled, false);
  } finally { await view.close(); }
});

test("Memory record deletion cannot notify or refetch after a session A-B-A navigation", async () => {
  const deletion = deferred<Response>(); const oldConfirm = window.confirm; window.confirm = () => true;
  const view = await harness(createElement(MemoryPanel), request => {
    if (request.init.method === "DELETE") return deletion.promise;
    if (request.url.pathname === "/api/ai/memory/records") return json({ memory_records: [{ memory_record_id: "record-a", content: "synthetic record", status: "active" }] });
    return defaultMemory(request);
  });
  try {
    await change(input(view.container, "会话 ID"), "session-a");
    await click(view.container.querySelector('button[aria-label="删除记忆记录"]')!);
    await change(input(view.container, "会话 ID"), "session-b"); await change(input(view.container, "会话 ID"), "session-a");
    const count = view.calls.length;
    await act(async () => deletion.resolve(json({ memory_record: { memory_record_id: "record-a", status: "deleted" } })));
    assert.equal(view.calls.length, count); assert.deepEqual(view.notices, []);
  } finally { await view.close(); window.confirm = oldConfirm; }
});
