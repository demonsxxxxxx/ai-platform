import assert from "node:assert/strict";
import test from "node:test";
import { registerHooks } from "node:module";
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import type { User } from "../../../types/auth";
import { authApi } from "../../../services/api/auth";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb";
// Styles are the only non-runtime module replaced; the route/controller are real.
registerHooks({ load(url, context, nextLoad) { return url.endsWith(".css") ? { format: "module", source: "export {};", shortCircuit: true } : nextLoad(url, context); } });
const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/apps/word-review" });
Object.assign(globalThis, { window: dom.window, document: dom.window.document, localStorage: dom.window.localStorage, sessionStorage: dom.window.sessionStorage, HTMLElement: dom.window.HTMLElement, CustomEvent: dom.window.CustomEvent, IS_REACT_ACT_ENVIRONMENT: true });
Object.defineProperty(globalThis, "navigator", { configurable: true, value: dom.window.navigator });
installBrowserAuthTestDb();
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { MemoryRouter, Routes, Route } = await import("react-router-dom");
const { AuthProvider, useAuth } = await import("../../../hooks/useAuth");
const { AgentApplicationRoute } = await import("../AgentApplicationRoute");
await import("../../../i18n/index");
const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json" } });
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: unknown) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
function stream(text = "") { return new ReadableStream<Uint8Array>({ start(c) { if (text) c.enqueue(new TextEncoder().encode(text)); c.close(); } }); }
const success = 'data: {"task_id":"task-a","files":[{"name":"result.docx","path":"result.docx"}]}\r\n\r\ndata: [DONE]\r\n\r\n';
function principal(id = "user-a", tenant = "tenant-a"): User { return { id, tenant_id: tenant, username: id, email: "synthetic@example.test", roles: [], permissions: [], is_active: true, created_at: "", updated_at: "" }; }
function docx() { return new File([new Uint8Array([0x50, 0x4b, 0x03, 0x04]), "[Content_Types].xml word/document.xml"], "synthetic.docx"); }
function props(node: Element) { const key = Object.keys(node).find(key => key.startsWith("__reactProps$")); assert.ok(key); return (node as unknown as Record<string, unknown>)[key] as { onChange?: (event: { target: { files: File[]; value: string } }) => void; onClick?: () => void }; }
function button(container: HTMLElement, label: string) { const found = Array.from(container.querySelectorAll("button")).find(node => node.textContent?.trim() === label); assert.ok(found, `Missing button ${label}: ${container.textContent}`); return found; }
const historyValues = () => Object.keys(localStorage).filter(key => key.startsWith("wordReviewHistory")).map(key => JSON.parse(localStorage.getItem(key)!));

type Fetcher = (url: string, init: RequestInit) => Promise<Response> | Response;
async function harness(options: { review?: Fetcher; history?: Fetcher; cancel?: Fetcher } = {}) {
  const old = { fetch: globalThis.fetch, xhr: globalThis.XMLHttpRequest, bootstrap: authApi.bootstrapAuthContext, user: authApi.getCurrentUser, confirm: window.confirm };
  let auth!: ReturnType<typeof useAuth>; let user = principal(); let uploads = 0; const calls: Array<{ url: string; init: RequestInit }> = [];
  localStorage.clear(); window.confirm = () => true;
  authApi.bootstrapAuthContext = async request => ({ status: "ready", protocol_version: 2, generation: request.generation });
  authApi.getCurrentUser = async () => user;
  class Xhr extends EventTarget {
    upload = new EventTarget(); status = 200; responseText = '{"file_id":"file-a"}'; timeout = 0;
    open(method: string, url: string) { assert.equal(method, "POST"); assert.ok(url.endsWith("/api/upload")); }
    send() { uploads++; queueMicrotask(() => this.dispatchEvent(new Event("load"))); }
    abort() { this.dispatchEvent(new Event("abort")); }
  }
  globalThis.XMLHttpRequest = Xhr as unknown as typeof XMLHttpRequest;
  globalThis.fetch = (async (raw, init = {}) => {
    const url = String(raw); calls.push({ url, init });
    if (url.endsWith("/api/chat/stream")) return options.review ? options.review(url, init) : new Response(stream(success));
    if (url.includes("/cancel")) return options.cancel ? options.cancel(url, init) : json({ status: "cancelled" });
    if (url.includes("/history?")) return options.history ? options.history(url, init) : json({ items: [] });
    if (url.includes("/stats/")) return json({ total: 0, daily: [] });
    throw new Error(`Unexpected synthetic request ${url}`);
  }) as typeof fetch;
  const container = document.createElement("div"); document.body.append(container); const root = createRoot(container);
  function Probe() { auth = useAuth(); return createElement(MemoryRouter, { initialEntries: ["/apps/word-review"] }, createElement(Routes, null, createElement(Route, { path: "/apps/:appKey", element: createElement(AgentApplicationRoute) }))); }
  await act(async () => { root.render(createElement(AuthProvider, null, createElement(Probe))); });
  assert.equal(auth.isAuthenticated, true);
  let unmounted = false;
  return { container, calls, get uploads() { return uploads; },
    async select(file = docx()) { await act(async () => { props(container.querySelector('input[type="file"]')!).onChange?.({ target: { files: [file], value: "" } }); }); },
    async start() { await act(async () => { button(container, "开始审核").click(); }); },
    async setUser(next: User) { user = next; await act(async () => { await auth.refreshUser(); }); },
    async unmount() { if (unmounted) return; unmounted = true; await act(async () => root.unmount()); container.remove(); },
    async close() { if (!unmounted) { unmounted = true; await act(async () => root.unmount()); container.remove(); } globalThis.fetch = old.fetch; globalThis.XMLHttpRequest = old.xhr; authApi.bootstrapAuthContext = old.bootstrap; authApi.getCurrentUser = old.user; window.confirm = old.confirm; },
  };
}

test("real Word route fails empty EOF and DONE-without-files instead of persisting success", async () => {
  for (const text of ["", "data: [DONE]\n\n"]) {
    const view = await harness({ review: () => new Response(stream(text)) });
    try { await view.select(); await view.start(); assert.ok(view.container.querySelector(".task-card.is-failed")); assert.equal(view.container.querySelector(".task-card.is-completed"), null); assert.equal(historyValues()[0][0].status, "failed"); }
    finally { await view.close(); }
  }
});

test("real Word route accepts fragmented terminal result and suppresses duplicate same-render starts", async () => {
  const bytes = new TextEncoder().encode(success);
  const view = await harness({ review: () => new Response(new ReadableStream({ start(c) { for (const byte of bytes) c.enqueue(Uint8Array.of(byte)); c.close(); } })) });
  try {
    await view.select(); const start = props(button(view.container, "开始审核")).onClick!;
    await act(async () => { start(); start(); });
    assert.equal(view.calls.filter(call => call.url.endsWith("/api/chat/stream")).length, 1);
    assert.ok(view.container.querySelector(".task-card.is-completed")); assert.equal(historyValues()[0][0].files[0].name, "result.docx");
  } finally { await view.close(); }
});

test("delayed file validation cannot start an upload after unmount", async () => {
  const bytes = deferred<ArrayBuffer>(); const file = docx(); Object.defineProperty(file, "arrayBuffer", { value: () => bytes.promise });
  const view = await harness();
  try { await view.select(file); await view.unmount(); await act(async () => bytes.resolve(await docx().arrayBuffer())); assert.equal(view.uploads, 0); assert.deepEqual(historyValues(), []); }
  finally { await view.close(); }
});

test("principal, tenant and workspace switches clear tasks and fence pending validation", async () => {
  const view = await harness();
  try {
    await view.select(); assert.ok(view.container.querySelector(".task-card.is-ready"));
    await view.setUser(principal("user-b", "tenant-b")); assert.equal(view.container.querySelector(".task-card"), null);
    const bytes = deferred<ArrayBuffer>(); const file = docx(); Object.defineProperty(file, "arrayBuffer", { value: () => bytes.promise });
    await view.select(file); await view.setUser(principal("user-b", "tenant-c"));
    await act(async () => bytes.resolve(await docx().arrayBuffer())); assert.equal(view.uploads, 1); assert.equal(view.container.querySelector(".task-card"), null);
    await view.select(); localStorage.setItem("workspace_id", "workspace-b"); await view.setUser(principal("user-b", "tenant-c")); assert.equal(view.container.querySelector(".task-card"), null);
  } finally { await view.close(); }
});

test("late stream and history after owner change cannot refill or persist previous owner's tasks", async () => {
  const response = deferred<Response>(); const history = deferred<Response>(); let reads = 0;
  const view = await harness({ review: () => response.promise, history: () => ++reads === 1 ? history.promise : json({ items: [] }) });
  try {
    await view.select(); await view.start(); await view.setUser(principal("user-b", "tenant-b"));
    await act(async () => { response.resolve(new Response(stream(success))); history.resolve(json({ items: [{ name: "old-owner.docx", task_id: "old-task", status: "completed" }] })); });
    assert.equal(view.container.querySelector(".task-card"), null); assert.doesNotMatch(view.container.textContent!, /old-owner/); assert.deepEqual(historyValues(), []);
  } finally { await view.close(); }
});

test("cancel intent without acknowledgement does not turn missing terminal receipt into cancelled", async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const view = await harness({ review: () => new Response(new ReadableStream({ start(c) { controller = c; } })) });
  try {
    await view.select(); await view.start(); await act(async () => button(view.container, "中断任务").click());
    await act(async () => controller.close());
    assert.ok(view.container.querySelector(".task-card.is-failed")); assert.equal(view.container.querySelector(".task-card.is-cancelled"), null);
  } finally { await view.close(); }
});

test("acknowledged cancellation aborts a blocked stream and persists cancelled", async () => {
  const view = await harness({ review: () => new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('data: {"task_id":"task-a"}\n\n')); } })) });
  try {
    await view.select(); await view.start(); await act(async () => button(view.container, "中断任务").click());
    assert.ok(view.container.querySelector(".task-card.is-cancelled")); assert.equal(historyValues()[0][0].status, "cancelled");
  } finally { await view.close(); }
});

test("cancellation response without status is not an acknowledgement; result may still complete", async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const view = await harness({ review: () => new Response(new ReadableStream({ start(c) { controller = c; c.enqueue(new TextEncoder().encode('data: {"task_id":"task-a"}\n\n')); } })), cancel: () => json({}) });
  try {
    await view.select(); await view.start(); await act(async () => button(view.container, "中断任务").click());
    assert.ok(view.container.querySelector(".task-card.is-running")); assert.match(view.container.textContent!, /未收到服务端取消确认/);
    await act(async () => { controller.enqueue(new TextEncoder().encode(success)); controller.close(); });
    assert.ok(view.container.querySelector(".task-card.is-completed"));
  } finally { await view.close(); }
});

test("local history is scoped to tenant, workspace and principal; legacy ambiguous history is not imported", async () => {
  const view = await harness();
  try {
    await view.select(); await view.start(); const firstKeys = Object.keys(localStorage).filter(key => key.startsWith("wordReviewHistory")); assert.equal(firstKeys.length, 1); assert.match(firstKeys[0], /v2:.*tenant-a.*default.*user-a/);
    localStorage.setItem("wordReviewHistory:user-a", JSON.stringify([{ name: "legacy-owner.docx", status: "completed" }]));
    await view.setUser(principal("user-a", "tenant-b"));
    await act(async () => { const tab = Array.from(view.container.querySelectorAll("button")).find(node => node.textContent?.startsWith("历史记录"))!; tab.click(); });
    assert.doesNotMatch(view.container.textContent!, /synthetic.docx|legacy-owner.docx/);
  } finally { await view.close(); }
});

test("ordinary JSON error messages terminate safely instead of showing the entire JSON body", async () => {
  const view = await harness({ review: () => json({ message: "synthetic public reason", ignored: "private fixture detail" }, 400) });
  try { await view.select(); await view.start(); assert.match(view.container.textContent!, /synthetic public reason/); assert.doesNotMatch(view.container.textContent!, /private fixture detail|RangeError/); }
  finally { await view.close(); }
});

test("late cancellation from a failed attempt cannot abort the same task's retry", async () => {
  let first!: ReadableStreamDefaultController<Uint8Array>; let second!: ReadableStreamDefaultController<Uint8Array>;
  const cancellation = deferred<Response>(); let attempts = 0;
  const view = await harness({ review: () => new Response(new ReadableStream({ start(c) {
    if (++attempts === 1) first = c; else second = c;
    c.enqueue(new TextEncoder().encode('data: {"task_id":"task-a"}\n\n'));
  } })), cancel: () => cancellation.promise });
  try {
    await view.select(); await view.start(); await act(async () => button(view.container, "中断任务").click());
    await act(async () => first.close()); assert.ok(view.container.querySelector(".task-card.is-failed"));
    await act(async () => button(view.container, "重试").click());
    await act(async () => cancellation.resolve(json({ status: "cancelled" })));
    assert.ok(view.container.querySelector(".task-card.is-running"));
    await act(async () => { second.enqueue(new TextEncoder().encode(success)); second.close(); });
    assert.ok(view.container.querySelector(".task-card.is-completed")); assert.equal(attempts, 2);
  } finally { await view.close(); }
});
