import assert from "node:assert/strict";
import test from "node:test";
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import { agentProfileApi } from "../../../services/api/agentProfile";
import type { AgentProfileAdminProjection } from "../../../types";
import { hydrateAgentProfileEditor } from "../agentBuilderAdapter";
import { AgentBuilderLifecycle } from "../AgentBuilderLifecycle";
const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/agent-builder" });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, HTMLElement: dom.window.HTMLElement, IS_REACT_ACT_ENVIRONMENT: true });
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
function profile(overrides: Partial<AgentProfileAdminProjection> = {}): AgentProfileAdminProjection {
  return { agent_id: "agent-a", revision: 7, published_revision: 7, status: "published", name: "Synthetic", description: "Synthetic", starter_prompts: [], instructions: "Synthetic", skill_set: [{ skill_id: "synthetic-skill" }], mcp_tool_ids: [], avatar_ref: "builtin:agent", avatar_seed: "synthetic", market_tags: [], visibility: "tenant", allowed_department_ids: [], allowed_roles: [], allowed_user_ids: [], content_hash: "a".repeat(64), created_at: "2026-10-01T00:00:00Z", published_at: "2026-10-01T00:00:00Z", ...overrides };
}
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
async function harness(loader: typeof agentProfileApi.listHistory, initial = profile()) {
  const old = agentProfileApi.listHistory; agentProfileApi.listHistory = loader;
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container); const unpublished: number[] = [];
  const render = async (value: AgentProfileAdminProjection) => { await act(async () => root.render(createElement(AgentBuilderLifecycle, { editor: hydrateAgentProfileEditor(value), disabled: false, mutation: { phase: "idle" }, onRunTest: () => undefined, onUnpublish: revision => unpublished.push(revision), onRetire: () => undefined }))); };
  await render(initial);
  return { container, render, unpublished, unpublish: () => container.querySelector<HTMLButtonElement>('button[title="下架当前发布版本"]')!, async close() { await act(async () => root.unmount()); container.remove(); agentProfileApi.listHistory = old; } };
}

test("new unpublished agent never inherits old rows or old publication revision while loading", async () => {
  const pending = deferred<{ agent_profiles: AgentProfileAdminProjection[] }>(); const a = profile(); const b = profile({ agent_id: "agent-b", revision: 1, published_revision: null, status: "draft", published_at: null });
  const view = await harness(async id => id === "agent-a" ? { agent_profiles: [a] } : pending.promise);
  try {
    assert.match(view.container.textContent!, /当前发布/); assert.equal(view.unpublish().disabled, false);
    await view.render(b); assert.match(view.container.textContent!, /正在加载版本历史/); assert.doesNotMatch(view.container.textContent!, /当前发布|aaaaaaaaaaaa/);
    assert.equal(view.container.querySelectorAll("tbody tr").length, 0); assert.equal(view.unpublish().disabled, true);
    await act(async () => view.unpublish().click()); assert.deepEqual(view.unpublished, []);
    // Even this agent's old immutable published snapshot cannot override null.
    await act(async () => pending.resolve({ agent_profiles: [profile({ agent_id: "agent-b", revision: 3 })] }));
    assert.equal(view.unpublish().disabled, true); assert.doesNotMatch(view.container.textContent!, /当前发布/);
  } finally { await view.close(); }
});

test("authoritative current revision remains actionable without history and never falls back", async () => {
  const pending = deferred<{ agent_profiles: AgentProfileAdminProjection[] }>();
  const view = await harness(() => pending.promise, profile({ revision: 9, published_revision: 7, status: "draft" }));
  try {
    assert.equal(view.unpublish().disabled, false); await act(async () => view.unpublish().click()); assert.deepEqual(view.unpublished, [7]);
    await act(async () => pending.reject(new Error("private history error")));
    assert.match(view.container.textContent!, /版本历史暂不可用/); assert.doesNotMatch(view.container.textContent!, /private history error/);
    assert.equal(view.unpublish().disabled, false);
  } finally { await view.close(); }
});

test("same-agent revision changes hide previous rows until owned history resolves", async () => {
  const pending = deferred<{ agent_profiles: AgentProfileAdminProjection[] }>(); let reads = 0;
  const view = await harness(async () => ++reads === 1 ? { agent_profiles: [profile()] } : pending.promise);
  try {
    await view.render(profile({ revision: 8, published_revision: null, status: "withdrawn" }));
    assert.equal(view.container.querySelectorAll("tbody tr").length, 0); assert.equal(view.unpublish().disabled, true);
    await act(async () => pending.resolve({ agent_profiles: [profile()] }));
    assert.match(view.container.textContent!, /已下架/); assert.doesNotMatch(view.container.textContent!, /当前发布/); assert.equal(view.unpublish().disabled, true);
  } finally { await view.close(); }
});

test("late A history cannot replace B's owned history", async () => {
  const pending = deferred<{ agent_profiles: AgentProfileAdminProjection[] }>();
  const view = await harness(async id => id === "agent-a" ? pending.promise : { agent_profiles: [profile({ agent_id: "agent-b", revision: 2, published_revision: 2, content_hash: "b".repeat(64) })] });
  try {
    await view.render(profile({ agent_id: "agent-b", revision: 2, published_revision: 2 }));
    await act(async () => pending.resolve({ agent_profiles: [profile()] }));
    assert.match(view.container.textContent!, /bbbbbbbbbbbb/); assert.doesNotMatch(view.container.textContent!, /aaaaaaaaaaaa/);
    await act(async () => view.unpublish().click()); assert.deepEqual(view.unpublished, [2]);
  } finally { await view.close(); }
});

test("mismatched history projection is rejected rather than displayed under the selected agent", async () => {
  const view = await harness(async () => ({ agent_profiles: [profile({ agent_id: "other-agent" })] }));
  try { assert.match(view.container.textContent!, /版本历史暂不可用/); assert.equal(view.container.querySelectorAll("tbody tr").length, 0); }
  finally { await view.close(); }
});

test("history completed after unmount has no mounted owner to update", async () => {
  const pending = deferred<{ agent_profiles: AgentProfileAdminProjection[] }>(); const view = await harness(() => pending.promise);
  await view.close(); await act(async () => pending.resolve({ agent_profiles: [profile()] })); assert.equal(view.container.childElementCount, 0);
});
