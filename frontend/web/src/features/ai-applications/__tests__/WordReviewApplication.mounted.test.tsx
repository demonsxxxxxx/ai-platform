import assert from "node:assert/strict";
import test, { after } from "node:test";
import { registerHooks } from "node:module";
import { installSelectTestDom } from "../../../components/common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../../components/common/__tests__/lobeUiTestRoot";
import type { User } from "../../../types/auth";
import { authApi } from "../../../services/api/auth";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb";
// Styles are the only non-runtime module replaced; the route/controller are real.
registerHooks({ load(url, context, nextLoad) { return url.endsWith(".css") ? { format: "module", source: "export {};", shortCircuit: true } : nextLoad(url, context); } });
const dom = installSelectTestDom();
Object.assign(globalThis, { localStorage: window.localStorage, sessionStorage: window.sessionStorage });
after(() => dom.close());
installBrowserAuthTestDb();
const { act, createElement, useLayoutEffect } = await import("react");
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
function props(node: Element) { const key = Object.keys(node).find(key => key.startsWith("__reactProps$")); assert.ok(key); return (node as unknown as Record<string, unknown>)[key] as { onChange?: (event: { target: { files: File[]; value: string } }) => void; onClick?: (event: MouseEvent) => void }; }
function button(container: HTMLElement, label: string) { const found = Array.from(container.querySelectorAll("button")).find(node => node.textContent?.trim() === label); assert.ok(found, `Missing button ${label}: ${container.textContent}`); return found; }
const historyValues = () => Object.keys(localStorage).filter(key => key.startsWith("wordReviewHistory")).map(key => JSON.parse(localStorage.getItem(key)!));

type Fetcher = (url: string, init: RequestInit) => Promise<Response> | Response;
async function harness(options: { review?: Fetcher; history?: Fetcher; cancel?: Fetcher; onScopeCommit?: (user: User | null) => void } = {}) {
  const old = { fetch: globalThis.fetch, xhr: globalThis.XMLHttpRequest, bootstrap: authApi.bootstrapAuthContext, user: authApi.getCurrentUser };
  let auth!: ReturnType<typeof useAuth>; let user = principal(); let uploads = 0; const calls: Array<{ url: string; init: RequestInit }> = [];
  localStorage.clear();
  authApi.bootstrapAuthContext = async request => ({ status: "ready", protocol_version: 2, generation: request.generation });
  authApi.getCurrentUser = async () => user;
  class Xhr extends window.EventTarget {
    upload = new window.EventTarget(); status = 200; responseText = '{"file_id":"file-a"}'; timeout = 0;
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
  const container = document.createElement("div"); document.body.append(container); const root = await createLobeTestRoot(container);
  function Probe() { const currentAuth = useAuth(); auth = currentAuth; useLayoutEffect(() => { options.onScopeCommit?.(currentAuth.user); }, [currentAuth.user]); return createElement(MemoryRouter, { initialEntries: ["/apps/word-review"] }, createElement(Routes, null, createElement(Route, { path: "/apps/:appKey", element: createElement(AgentApplicationRoute) }))); }
  await act(async () => { root.render(createElement(AuthProvider, null, createElement(Probe))); });
  assert.equal(auth.isAuthenticated, true);
  let unmounted = false;
  return { container, calls, get uploads() { return uploads; },
    async select(file = docx()) { await act(async () => { props(container.querySelector('input[type="file"]')!).onChange?.({ target: { files: [file], value: "" } }); }); },
    async start() { await act(async () => { button(container, "开始审核").click(); }); },
    async cancel(confirm = true) {
      await act(async () => { const trigger = button(container, "中断任务"); trigger.focus(); trigger.click(); });
      const dialog = container.querySelector<HTMLElement>('[role="dialog"]');
      assert.ok(dialog, "cancellation uses the themed library dialog");
      await act(async () => { button(dialog, confirm ? "确认中断" : "取消").click(); });
    },
    async setUser(next: User) { user = next; await act(async () => { await auth.refreshUser(); }); },
    async unmount() { if (unmounted) return; unmounted = true; await act(async () => root.unmount()); container.remove(); },
    async close() { if (!unmounted) { unmounted = true; await act(async () => root.unmount()); container.remove(); } globalThis.fetch = old.fetch; globalThis.XMLHttpRequest = old.xhr; authApi.bootstrapAuthContext = old.bootstrap; authApi.getCurrentUser = old.user; },
  };
}

test("review library tabs support keyboard navigation and keep task progress accessible", async () => {
  const view = await harness();
  try {
    await view.select();
    const current = view.container.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')!;
    assert.match(current.textContent!, /当前任务/);
    const progress = view.container.querySelector('[role="progressbar"]')!;
    assert.equal(progress.getAttribute("aria-label"), "synthetic.docx处理进度");
    assert.equal(progress.getAttribute("aria-valuenow"), "18");
    await act(async () => {
      current.focus();
      current.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    });
    const history = document.activeElement as HTMLButtonElement;
    assert.match(history.textContent!, /历史记录/);
    await act(async () => { history.click(); });
    assert.equal(history.getAttribute("aria-selected"), "true");
    assert.match(view.container.querySelector('[role="tabpanel"]')!.textContent!, /暂无历史记录/);
    await act(async () => { history.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true })); });
    assert.equal(document.activeElement, current);
    await act(async () => { current.click(); });
    assert.ok(view.container.querySelector(".task-card.is-ready"));
  } finally { await view.close(); }
});

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
    await act(async () => { start(new MouseEvent("click", { cancelable: true })); start(new MouseEvent("click", { cancelable: true })); });
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

test("review cancellation keeps running until the library confirmation is accepted", async () => {
  const view = await harness({ review: () => new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('data: {"task_id":"task-a"}\n\n')); } })) });
  try {
    await view.select(); await view.start();
    await view.cancel(false);
    assert.equal(view.calls.filter(call => call.url.includes("/cancel")).length, 0);
    assert.ok(view.container.querySelector(".task-card.is-running"));
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 300)); });
    assert.ok(document.activeElement === button(view.container, "中断任务"), "closing confirmation returns focus to its task action");
    await view.cancel();
    assert.equal(view.calls.filter(call => call.url.includes("/cancel")).length, 1);
    assert.ok(view.container.querySelector(".task-card.is-cancelled"));
  } finally { await view.close(); }
});

test("same-render confirmation double click submits cancellation only once", async () => {
  const cancellation = deferred<Response>();
  const view = await harness({
    review: () => new Response(new ReadableStream({ start(controller) {
      controller.enqueue(new TextEncoder().encode('data: {"task_id":"task-a"}\n\n'));
    } })),
    cancel: () => cancellation.promise,
  });
  try {
    await view.select(); await view.start();
    await view.cancel(false);
    assert.equal(view.calls.filter(call => call.url.endsWith("/cancel")).length, 0);
    await act(async () => button(view.container, "中断任务").click());
    const dialog = view.container.querySelector<HTMLElement>('[role="dialog"]');
    assert.ok(dialog);
    const confirm = button(dialog, "确认中断");
    await act(async () => { confirm.click(); confirm.click(); });
    assert.equal(view.calls.filter(call => call.url.endsWith("/task-a/cancel")).length, 1);
    assert.ok(view.container.querySelector(".task-card.is-running"));
    await act(async () => cancellation.resolve(json({ status: "cancelled" })));
    assert.equal(view.calls.filter(call => call.url.endsWith("/cancel")).length, 1);
    assert.ok(view.container.querySelector(".task-card.is-cancelled"));
  } finally { await view.close(); }
});

test("cancel intent without acknowledgement does not turn missing terminal receipt into cancelled", async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const view = await harness({ review: () => new Response(new ReadableStream({ start(c) { controller = c; } })) });
  try {
    await view.select(); await view.start(); await view.cancel();
    await act(async () => controller.close());
    assert.ok(view.container.querySelector(".task-card.is-failed")); assert.equal(view.container.querySelector(".task-card.is-cancelled"), null);
  } finally { await view.close(); }
});

test("acknowledged cancellation aborts a blocked stream and persists cancelled", async () => {
  const view = await harness({ review: () => new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('data: {"task_id":"task-a"}\n\n')); } })) });
  try {
    await view.select(); await view.start(); await view.cancel();
    assert.ok(view.container.querySelector(".task-card.is-cancelled")); assert.equal(historyValues()[0][0].status, "cancelled");
  } finally { await view.close(); }
});

test("cancellation response without status is not an acknowledgement; result may still complete", async () => {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const view = await harness({ review: () => new Response(new ReadableStream({ start(c) { controller = c; c.enqueue(new TextEncoder().encode('data: {"task_id":"task-a"}\n\n')); } })), cancel: () => json({}) });
  try {
    await view.select(); await view.start(); await view.cancel();
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
    await view.select(); await view.start(); await view.cancel();
    await act(async () => first.close()); assert.ok(view.container.querySelector(".task-card.is-failed"));
    await act(async () => button(view.container, "重试").click());
    await act(async () => cancellation.resolve(json({ status: "cancelled" })));
    assert.ok(view.container.querySelector(".task-card.is-running"));
    await act(async () => { second.enqueue(new TextEncoder().encode(success)); second.close(); });
    assert.ok(view.container.querySelector(".task-card.is-completed")); assert.equal(attempts, 2);
  } finally { await view.close(); }
});


test("initial history resolving in the replacement scope layout cannot paint old-owner data", async () => {
  const history = deferred<Response>(); let reads = 0; let resolvedInLayout = false;
  const view = await harness({
    history: () => ++reads === 1 ? history.promise : json({ items: [] }),
    onScopeCommit: user => {
      if (user?.id === "user-b") {
        resolvedInLayout = true;
        history.resolve(json({ items: [{ name: "old-layout-owner.docx", status: "completed", task_id: "old-layout-task" }] }));
      }
    },
  });
  try {
    await view.setUser(principal("user-b", "tenant-b"));
    assert.equal(resolvedInLayout, true);
    await act(async () => { Array.from(view.container.querySelectorAll("button")).find(node => node.textContent?.startsWith("历史记录"))!.click(); });
    assert.doesNotMatch(view.container.textContent!, /old-layout-owner/); assert.deepEqual(historyValues(), []);
  } finally { await view.close(); }
});

test("retry cancellation waits for this attempt receipt before submitting", async () => {
  let attempts = 0;
  let second!: ReadableStreamDefaultController<Uint8Array>;
  const view = await harness({ review: () => {
    attempts++;
    return attempts === 1
      ? new Response(stream('data: {"task_id":"old-attempt"}\n\n'))
      : new Response(new ReadableStream({ start(c) { second = c; } }));
  }});
  try {
    await view.select(); await view.start();
    assert.ok(view.container.querySelector(".task-card.is-failed"));
    await act(async () => button(view.container, "重试").click());
    await view.cancel();
    const cancels = view.calls.filter(call => call.url.endsWith("/cancel"));
    assert.equal(cancels.length, 0);
    assert.ok(view.container.querySelector(".task-card.is-running"));
    await act(async () => second.enqueue(new TextEncoder().encode('data: {"task_id":"new-attempt"}\n\n')));
    const received = view.calls.filter(call => call.url.endsWith("/cancel"));
    assert.equal(received.length, 1);
    assert.ok(received[0].url.includes("/new-attempt/cancel"));
    const bodies = view.calls.filter(call => call.url.endsWith("/api/chat/stream")).map(call => JSON.parse(String(call.init.body)));
    assert.equal(bodies[0].client_task_id, bodies[1].client_task_id);
    assert.equal(bodies[0].session_id, bodies[1].session_id);
    assert.ok(view.container.querySelector(".task-card.is-cancelled"));
  } finally { await view.close(); }
});

test("download discards old owner response after principal switch", async () => {
  const pending = deferred<Response>();
  const oldCreate = URL.createObjectURL; const oldRevoke = URL.revokeObjectURL;
  const oldClick = HTMLAnchorElement.prototype.click;
  let clicks = 0;
  URL.createObjectURL = () => "blob:synthetic-review"; URL.revokeObjectURL = () => {};
  HTMLAnchorElement.prototype.click = function() { clicks++; };
  const view = await harness();
  try {
    await view.select(); await view.start();
    const originalFetch = globalThis.fetch;
    globalThis.fetch = ((url, init) => String(url).includes("/api/file/download") ? pending.promise : originalFetch(url, init)) as typeof fetch;
    await act(async () => button(view.container, "批注 Word").click());
    await view.setUser(principal("user-b", "tenant-b"));
    assert.equal(view.container.querySelector(".task-card"), null);
    await act(async () => pending.resolve(new Response("synthetic old-owner content")));
    assert.equal(clicks, 0);
  } finally {
    await view.close(); URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; HTMLAnchorElement.prototype.click = oldClick;
  }
});


for (const boundary of ["unmount", "same-principal refresh", "auth event", "marker replacement", "workspace"] as const) {
  test(`download pending body is fenced on ${boundary}`, async () => {
    const pending = deferred<Blob>();
    const oldCreate = URL.createObjectURL; const oldRevoke = URL.revokeObjectURL;
    const oldClick = HTMLAnchorElement.prototype.click;
    let clicks = 0; let creates = 0; let signal: AbortSignal | undefined;
    URL.createObjectURL = () => { creates++; return "blob:synthetic"; }; URL.revokeObjectURL = () => {};
    HTMLAnchorElement.prototype.click = () => { clicks++; };
    const view = await harness();
    try {
      await view.select(); await view.start();
      const original = globalThis.fetch;
      globalThis.fetch = ((url, init) => {
        if (!String(url).includes("/api/file/download")) return original(url, init);
        signal = init?.signal as AbortSignal;
        const response = new Response("synthetic"); response.blob = () => pending.promise;
        return Promise.resolve(response);
      }) as typeof fetch;
      await act(async () => button(view.container, "批注 Word").click());
      if (boundary === "unmount") await view.unmount();
      else if (boundary === "same-principal refresh") await view.setUser(principal());
      else if (boundary === "auth event") {
        const { publishBrowserAuthIncarnationChange } = await import("../../../hooks/browserAuthCoordinator");
        publishBrowserAuthIncarnationChange();
      } else if (boundary === "marker replacement") {
        const { AUTH_SESSION_MARKER_KEY } = await import("../../../services/api/token");
        localStorage.setItem(AUTH_SESSION_MARKER_KEY, "synthetic-new-session");
      } else {
        localStorage.setItem("workspace_id", "synthetic-new-workspace"); await view.setUser(principal());
      }
      if (boundary !== "marker replacement") assert.equal(signal?.aborted, true);
      await act(async () => pending.resolve(new Blob(["synthetic"])));
      assert.equal(creates, 0); assert.equal(clicks, 0);
    } finally { await view.close(); URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; HTMLAnchorElement.prototype.click = oldClick; }
  });
}

test("download saves normally, removes anchor and revokes URL on owner cleanup", async () => {
  const oldCreate = URL.createObjectURL; const oldRevoke = URL.revokeObjectURL; const oldClick = HTMLAnchorElement.prototype.click;
  const revoked: string[] = []; let clicks = 0;
  URL.createObjectURL = () => "blob:synthetic-save"; URL.revokeObjectURL = url => { revoked.push(url); };
  HTMLAnchorElement.prototype.click = function() { clicks++; assert.equal(this.download, "result.docx"); assert.equal(this.isConnected, true); };
  const view = await harness();
  try {
    await view.select(); await view.start(); const original = globalThis.fetch;
    globalThis.fetch = ((url, init) => String(url).includes("/api/file/download") ? Promise.resolve(new Response("synthetic")) : original(url, init)) as typeof fetch;
    await act(async () => button(view.container, "批注 Word").click());
    assert.equal(clicks, 1); assert.equal(document.querySelector('a[href="blob:synthetic-save"]'), null);
    await view.unmount(); assert.deepEqual(revoked, ["blob:synthetic-save"]);
    await new Promise(resolve => setTimeout(resolve, 1100)); assert.equal(revoked.length, 1);
  } finally { await view.close(); URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; HTMLAnchorElement.prototype.click = oldClick; }
});

test("download failures notify only current owner and never create URLs", async () => {
  const { toast } = await import("@lobehub/ui/base-ui");
  const oldError = toast.error; const oldCreate = URL.createObjectURL;
  const errors: unknown[] = []; let creates = 0;
  toast.error = ((message: unknown) => { errors.push(message); }) as typeof toast.error;
  URL.createObjectURL = () => { creates++; return "blob:synthetic"; };
  const view = await harness();
  try {
    await view.select(); await view.start(); const original = globalThis.fetch;
    globalThis.fetch = ((url, init) => String(url).includes("/api/file/download") ? Promise.resolve(json({ message: "download failed" }, 500)) : original(url, init)) as typeof fetch;
    await act(async () => button(view.container, "批注 Word").click()); assert.deepEqual(errors, ["download failed"]);
    const pending = deferred<Response>();
    globalThis.fetch = ((url, init) => String(url).includes("/api/file/download") ? pending.promise : original(url, init)) as typeof fetch;
    await act(async () => button(view.container, "批注 Word").click()); await view.setUser(principal("user-b", "tenant-b"));
    await act(async () => pending.reject(new Error("old owner failure")));
    assert.equal(errors.length, 1); assert.equal(creates, 0);
  } finally { await view.close(); toast.error = oldError; URL.createObjectURL = oldCreate; }
});

test("batch download stops requesting files after owner changes during first request", async () => {
  const pending = deferred<Response>(); let requests = 0;
  const view = await harness({ history: () => json({ items: [{ task_id: "history-a", name: "batch.docx", status: "completed", files: [{ name: "first.docx", path: "first.docx" }, { name: "second.docx", path: "second.docx" }] }] }) });
  try {
    await act(async () => Array.from(view.container.querySelectorAll("button")).find(node => node.textContent?.startsWith("历史记录"))!.click());
    await act(async () => (view.container.querySelector('[role="checkbox"]') as HTMLElement).click());
    const original = globalThis.fetch;
    globalThis.fetch = ((url, init) => { if (String(url).includes("/download")) { requests++; return pending.promise; } return original(url, init); }) as typeof fetch;
    await act(async () => button(view.container, "批量下载").click()); assert.equal(requests, 1);
    await view.setUser(principal("user-b", "tenant-b")); await act(async () => pending.resolve(new Response("synthetic")));
    await act(async () => new Promise(resolve => setTimeout(resolve, 200))); assert.equal(requests, 1);
  } finally { await view.close(); }
});

test("late cancellation failure across principal change cannot paint replacement owner", async () => {
  const pending = deferred<Response>();
  const view = await harness({ review: () => new Response(new ReadableStream({ start(c) { c.enqueue(new TextEncoder().encode('data: {"task_id":"task-a"}\n\n')); } })), cancel: () => pending.promise });
  try {
    await view.select(); await view.start(); await view.cancel();
    await view.setUser(principal("user-b", "tenant-b")); await view.select();
    await act(async () => pending.reject(new Error("stale cancellation failure")));
    assert.ok(view.container.querySelector(".task-card.is-ready")); assert.doesNotMatch(view.container.textContent!, /stale cancellation failure/);
  } finally { await view.close(); }
});

for (const invalidateAtCreate of [false, true]) {
  test(`download URL is revoked ${invalidateAtCreate ? "when owner changes at creation" : "after normal save delay"}`, async () => {
    const { publishBrowserAuthIncarnationChange } = await import("../../../hooks/browserAuthCoordinator");
    const oldCreate = URL.createObjectURL; const oldRevoke = URL.revokeObjectURL; const oldClick = HTMLAnchorElement.prototype.click;
    let clicks = 0; const revoked: string[] = [];
    URL.createObjectURL = () => { if (invalidateAtCreate) publishBrowserAuthIncarnationChange(); return "blob:synthetic-timer"; };
    URL.revokeObjectURL = url => { revoked.push(url); }; HTMLAnchorElement.prototype.click = () => { clicks++; };
    const view = await harness();
    try {
      await view.select(); await view.start(); const original = globalThis.fetch;
      globalThis.fetch = ((url, init) => String(url).includes("/api/file/download") ? Promise.resolve(new Response("synthetic")) : original(url, init)) as typeof fetch;
      await act(async () => button(view.container, "批注 Word").click());
      assert.equal(clicks, invalidateAtCreate ? 0 : 1);
      await new Promise(resolve => setTimeout(resolve, 1100)); assert.deepEqual(revoked, ["blob:synthetic-timer"]);
      await view.unmount(); assert.equal(revoked.length, 1);
    } finally { await view.close(); URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; HTMLAnchorElement.prototype.click = oldClick; }
  });
}

test("retry without a new receipt fails without reusing old task ID in history", async () => {
  let attempts = 0;
  const view = await harness({ review: () => new Response(stream(++attempts === 1 ? 'data: {"task_id":"old-attempt"}\n\n' : "")) });
  try {
    await view.select(); await view.start(); await act(async () => button(view.container, "重试").click());
    assert.ok(view.container.querySelector(".task-card.is-failed"));
    assert.equal(historyValues()[0][0].taskId, "");
    assert.match(view.container.textContent!, /审核结果流提前结束，未收到完成回执/);
  } finally { await view.close(); }
});

test("normal batch download saves both files and cleans both URLs", async () => {
  const oldCreate = URL.createObjectURL; const oldRevoke = URL.revokeObjectURL; const oldClick = HTMLAnchorElement.prototype.click;
  let creates = 0; const saved: string[] = []; const revoked: string[] = [];
  URL.createObjectURL = () => `blob:synthetic-batch-${++creates}`; URL.revokeObjectURL = url => { revoked.push(url); };
  HTMLAnchorElement.prototype.click = function() { saved.push(this.download); };
  const view = await harness({ history: () => json({ items: [{ task_id: "history-a", name: "batch.docx", status: "completed", files: [{ name: "first.docx", path: "first.docx" }, { name: "second.docx", path: "second.docx" }] }] }) });
  try {
    await act(async () => Array.from(view.container.querySelectorAll("button")).find(node => node.textContent?.startsWith("历史记录"))!.click());
    await act(async () => (view.container.querySelector('[role="checkbox"]') as HTMLElement).click());
    const original = globalThis.fetch;
    globalThis.fetch = ((url, init) => String(url).includes("/download") ? Promise.resolve(new Response("synthetic")) : original(url, init)) as typeof fetch;
    await act(async () => button(view.container, "批量下载").click());
    await act(async () => new Promise(resolve => setTimeout(resolve, 400)));
    assert.deepEqual(saved, ["first.docx", "second.docx"]);
    await view.unmount(); assert.deepEqual(revoked.sort(), ["blob:synthetic-batch-1", "blob:synthetic-batch-2"]);
  } finally { await view.close(); URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; HTMLAnchorElement.prototype.click = oldClick; }
});

test("late cancellation error cannot paint a retry reusing the same receipt", async () => {
  let first!: ReadableStreamDefaultController<Uint8Array>;
  const cancellation = deferred<Response>(); let attempts = 0;
  const view = await harness({ review: () => new Response(new ReadableStream({ start(c) {
    if (++attempts === 1) first = c;
    c.enqueue(new TextEncoder().encode('data: {"task_id":"same-receipt"}\n\n'));
  } })), cancel: () => cancellation.promise });
  try {
    await view.select(); await view.start(); await view.cancel();
    await act(async () => first.close());
    await act(async () => button(view.container, "重试").click());
    await act(async () => cancellation.reject(new Error("obsolete cancellation failure")));
    assert.ok(view.container.querySelector(".task-card.is-running"));
    assert.doesNotMatch(view.container.textContent!, /obsolete cancellation failure/);
  } finally { await view.close(); }
});

for (const outcome of ["resolve", "reject"] as const) {
  test(`active cancellation follows a single replacement receipt after stale ${outcome}`, async () => {
    let current!: ReadableStreamDefaultController<Uint8Array>;
    const cancellation = deferred<Response>();
    const view = await harness({ review: () => new Response(new ReadableStream({ start(c) {
      current = c; c.enqueue(new TextEncoder().encode('data: {"task_id":"receipt-a"}\n\n'));
    } })), cancel: url => url.includes("/receipt-a/") ? cancellation.promise : json({ status: "cancelled" }) });
    try {
      await view.select(); await view.start(); await view.cancel();
      await act(async () => current.enqueue(new TextEncoder().encode('data: {"task_id":"receipt-b"}\n\n')));
      await act(async () => {
        if (outcome === "resolve") cancellation.resolve(json({ status: "cancelled" }));
        else cancellation.reject(new Error("obsolete receipt failure"));
      });
      assert.doesNotMatch(view.container.textContent!, /obsolete receipt failure/);
      assert.equal(view.calls.filter(call => call.url.endsWith("/receipt-a/cancel")).length, 1);
      assert.equal(view.calls.filter(call => call.url.endsWith("/receipt-b/cancel")).length, 1);
      assert.ok(view.container.querySelector(".task-card.is-cancelled"));
    } finally { await view.close(); }
  });
}
