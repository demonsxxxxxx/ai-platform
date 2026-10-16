import assert from "node:assert/strict";
import test, { after } from "node:test";
import { registerHooks } from "node:module";
import { installSelectTestDom } from "../../../components/common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../../components/common/__tests__/lobeUiTestRoot";
import type { User } from "../../../types/auth";
import { AUTH_SESSION_MARKER_KEY, getAccessToken } from "../../../services/api/token";
import { authApi } from "../../../services/api/auth";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb";
registerHooks({ load(url, context, nextLoad) { return url.endsWith(".css") ? { format: "module", source: "export {};", shortCircuit: true } : nextLoad(url, context); } });
const dom = installSelectTestDom();
Object.assign(globalThis, { localStorage: window.localStorage, sessionStorage: window.sessionStorage });
after(() => dom.close());
installBrowserAuthTestDb();
const { act, createElement } = await import("react");
const { MemoryRouter } = await import("react-router-dom");
const { AuthProvider, useAuth } = await import("../../../hooks/useAuth");
const { KnowledgeBaseApplication } = await import("../AgentApplicationRoute");
await import("../../../i18n/index");
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(yes => { resolve = yes; }); return { promise, resolve }; }
function response(data: unknown) { return new Response(`data: ${JSON.stringify({ data })}\n\n`); }
function principal(id = "user-a", tenant = "tenant-a"): User { return { id, tenant_id: tenant, username: id, email: "synthetic@example.test", roles: [], permissions: [], is_active: true, created_at: "", updated_at: "" }; }
async function harness(fetcher: (init: RequestInit) => Promise<Response> | Response) {
  const old = { fetch: globalThis.fetch, bootstrap: authApi.bootstrapAuthContext, user: authApi.getCurrentUser };
  let auth!: ReturnType<typeof useAuth>;
  let user = principal();
  authApi.bootstrapAuthContext = async request => ({ status: "ready", protocol_version: 2, generation: request.generation });
  authApi.getCurrentUser = async () => user;
  globalThis.fetch = (async (_url, init = {}) => fetcher(init)) as typeof fetch;
  const container = document.createElement("div"); document.body.append(container); const root = await createLobeTestRoot(container);
  function Probe() { auth = useAuth(); return createElement(MemoryRouter, null, createElement(KnowledgeBaseApplication, { config: { chatId: "synthetic", auth: "synthetic-only" } })); }
  await act(async () => { root.render(createElement(AuthProvider, null, createElement(Probe))); });
  assert.equal(auth.isAuthenticated, true);
  let unmounted = false;
  return { container,
    async submit(value: string) {
      await act(async () => {
        const field = container.querySelector("textarea")!;
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(field, value);
        field.dispatchEvent(new Event("input", { bubbles: true }));
      });
      await act(async () => { container.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    },
    async setUser(next: User) { user = next; await act(async () => { await auth.refreshUser(); }); },
    async unmount() { if (!unmounted) { await act(async () => root.unmount()); unmounted = true; } },
    async close() { if (!unmounted) await act(async () => root.unmount()); container.remove(); globalThis.fetch = old.fetch; authApi.bootstrapAuthContext = old.bootstrap; authApi.getCurrentUser = old.user; },
  };
}

test("SOP unmount aborts initialization and ignores a late session response", async () => {
  const pending = deferred<Response>(); let signal!: AbortSignal; let calls = 0;
  const view = await harness(init => { calls++; signal = init.signal!; return pending.promise; });
  try {
    await view.submit("old question");
    await view.unmount();
    assert.equal(signal.aborted, true);
    await act(async () => { pending.resolve(response({ session_id: "old-session" })); });
    assert.equal(calls, 1);
  } finally { await view.close(); }
});

test("SOP scope changes abort old answers and cannot clear a new request's loading state", async () => {
  const oldAnswer = deferred<Response>(); const newAnswer = deferred<Response>();
  const signals: AbortSignal[] = []; let answerCalls = 0;
  const view = await harness(init => {
    const body = JSON.parse(String(init.body));
    if (!body.session_id) return response({ session_id: `session-${answerCalls}` });
    signals.push(init.signal!);
    answerCalls++;
    if (answerCalls === 3) {
      assert.equal(body.session_id, "new-session");
      return response({ answer: "follow-up answer", session_id: "new-session" });
    }
    return answerCalls === 1 ? oldAnswer.promise : newAnswer.promise;
  });
  try {
    await view.submit("old question");
    await view.setUser(principal("user-b", "tenant-b"));
    assert.equal(signals[0].aborted, true);
    assert.doesNotMatch(view.container.textContent!, /old question/);
    await view.submit("new question");
    await act(async () => { oldAnswer.resolve(response({ answer: "OLD ANSWER", session_id: "old-session" })); });
    assert.doesNotMatch(view.container.textContent!, /OLD ANSWER/);
    assert.equal(view.container.querySelector<HTMLButtonElement>('button[type="submit"]')!.disabled, true);
    await act(async () => { newAnswer.resolve(response({ answer: "NEW ANSWER", session_id: "new-session" })); });
    assert.match(view.container.textContent!, /NEW ANSWER/);
    await view.submit("follow up");
    assert.equal(answerCalls, 3);
  } finally { await view.close(); }
});

test("SOP scope replacement cancels a streaming reader and drops old progress", async () => {
  let streamController!: ReadableStreamDefaultController<Uint8Array>;
  let cancelled = 0;
  const body = new ReadableStream<Uint8Array>({ start(controller) { streamController = controller; }, cancel() { cancelled++; } });
  const view = await harness(init => JSON.parse(String(init.body)).session_id ? new Response(body) : response({ session_id: "old-session" }));
  try {
    await view.submit("old question");
    await act(async () => { streamController.enqueue(new TextEncoder().encode('data: {"data":{"answer":"old progress"}}\n\n')); });
    assert.match(view.container.textContent!, /old progress/);
    await view.setUser(principal("user-b", "tenant-b"));
    assert.equal(cancelled, 1);
    assert.equal(body.locked, false);
    assert.doesNotMatch(view.container.textContent!, /old progress|old question/);
  } finally { await view.close(); }
});

test("SOP late HTTP failure cannot paint an error in the replacement scope", async () => {
  const pending = deferred<Response>();
  const view = await harness(() => pending.promise);
  try {
    await view.submit("old question");
    await view.setUser(principal("user-b", "tenant-b"));
    await act(async () => { pending.resolve(new Response("synthetic failure", { status: 503 })); });
    assert.doesNotMatch(view.container.textContent!, /503|超时|old question/);
  } finally { await view.close(); }
});


test("SOP same-user session replacement fences late results without needing a prior render", async () => {
  const pending = deferred<Response>();
  const payloads: Record<string, unknown>[] = [];
  const view = await harness(init => {
    const body = JSON.parse(String(init.body));
    payloads.push(body);
    if (!body.session_id) return response({ session_id: "synthetic-session" });
    return payloads.length === 2 ? pending.promise : response({ answer: "fresh answer" });
  });
  try {
    await view.submit("old question");
    const oldMarker = getAccessToken();
    localStorage.setItem(AUTH_SESSION_MARKER_KEY, "synthetic-replacement-marker");
    assert.notEqual(getAccessToken(), oldMarker);
    // Do not rerender or refresh useAuth before the old completion arrives.
    await act(async () => { pending.resolve(response({ answer: "STALE SESSION ANSWER", session_id: "stale-session" })); });
    assert.doesNotMatch(view.container.textContent!, /STALE SESSION ANSWER/);
    await view.setUser(principal());
    await view.submit("fresh question");
    assert.equal(payloads.length, 4);
    assert.equal(payloads[2].question, "初始化会话");
    assert.equal(payloads[3].session_id, "synthetic-session");
    assert.match(view.container.textContent!, /fresh answer/);
  } finally { await view.close(); }
});
