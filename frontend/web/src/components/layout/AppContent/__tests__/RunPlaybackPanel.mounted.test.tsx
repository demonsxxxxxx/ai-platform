import assert from "node:assert/strict";
import test, { after } from "node:test";
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import windows from "../../../../services/api/__tests__/fixtures/runPlaybackContextWindows.json";
import type { RunControlParentIdentity } from "../../../../hooks/useAgent/runControlLifecycle";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://localhost/chat" });
const globals = {
  window: dom.window, document: dom.window.document, navigator: dom.window.navigator,
  localStorage: dom.window.localStorage, sessionStorage: dom.window.sessionStorage,
  HTMLElement: dom.window.HTMLElement, CustomEvent: dom.window.CustomEvent,
  IS_REACT_ACT_ENVIRONMENT: true,
};
const previous = new Map(Object.keys(globals).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
for (const [key, value] of Object.entries(globals)) {
  Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
}
after(() => {
  dom.window.close();
  for (const [key, descriptor] of previous) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor);
    else Reflect.deleteProperty(globalThis, key);
  }
});
const { act, createElement } = await import("react");
const { createRoot } = await import("react-dom/client");
const { RunPlaybackPanel } = await import("../RunPlaybackPanel");
const { RunControlLifecycle } = await import("../../../../hooks/useAgent/runControlLifecycle");
const { sessionApi } = await import("../../../../services/api/session");
await import("../../../../i18n");

function parent(runId = "run-a", generation = 1): RunControlParentIdentity {
  return {
    chatHistoryGeneration: generation, authRevision: 1, sessionId: `session-${runId}`, runId,
    auth: { incarnation: "synthetic-incarnation", sessionMarker: "synthetic-marker", tenantId: "tenant-a", userId: "user-a", roles: ["member"], permissions: ["chat:write"], isAdmin: false, isActive: true },
  };
}
function json(value: unknown) {
  return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(yes => { resolve = yes; });
  return { promise, resolve };
}
async function harness(fetcher: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> | Response) {
  const oldFetch = globalThis.fetch;
  const oldStatus = sessionApi.getStatus;
  globalThis.fetch = (async (input, init) => {
    assert.match(String(input), /\/api\/ai\/runs\/[^/]+\/playback$/);
    assert.equal(init?.method, "GET");
    return fetcher(input, init);
  }) as typeof fetch;
  sessionApi.getStatus = async (sessionId, runId) => ({ session_id: sessionId, run_id: runId, status: "succeeded" });
  const lifecycle = new RunControlLifecycle();
  lifecycle.bindParent(parent());
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => root.render(createElement(RunPlaybackPanel, { lifecycle, panelKey: "context-window-test" })));
  let mounted = true;
  return {
    container, lifecycle,
    async refresh() { await act(async () => { await lifecycle.refresh(); }); },
    async unmount() {
      if (!mounted) return;
      mounted = false;
      await act(async () => {
        lifecycle.bindParent(null);
        root.unmount();
      });
    },
    async close() {
      await this.unmount();
      container.remove();
      globalThis.fetch = oldFetch;
      sessionApi.getStatus = oldStatus;
    },
  };
}

for (const [status, payload] of Object.entries(windows)) {
  test(`backend ${status} window traverses fetch, normalizer, view model and mounted panel`, async () => {
    const view = await harness(() => json(payload));
    try {
      await view.refresh();
      const section = view.container.querySelector(`[data-run-context-window="${status}"]`);
      assert.ok(section);
      assert.equal(view.container.querySelector("[data-run-playback-state]")?.getAttribute("data-run-playback-state"), "ready");
      assert.match(view.container.textContent!, /运行上下文/);
      assert.deepEqual(Array.from(section.querySelectorAll("dd"), node => Number(node.textContent)), [
        payload.context_window.history_candidate_count, payload.context_window.history_authorized_count, payload.context_window.history_omitted_count,
      ]);
      assert.deepEqual(Array.from(section.querySelectorAll("li"), node => node.textContent), payload.context_window.selected_file_names);
      assert.equal(section.textContent!.includes("旧版历史已排除"), payload.context_window.legacy_history_excluded);
      if (status === "degraded") assert.match(section.textContent!, /无法从此运行绑定的快照完整确认上下文/);
      assert.doesNotMatch(section.textContent!, /document_worker|context_snapshot|conversation-turns-v1|执行层级/);
    } finally { await view.close(); }
  });
}

test("missing and invalid fields stay safe through the mounted consumer", async () => {
  let payload: unknown = { ...windows.complete, context_ref: null };
  const view = await harness(() => json(payload));
  try {
    await view.refresh();
    assert.ok(view.container.querySelector('[data-run-context-window="degraded"]'));
    assert.doesNotMatch(view.container.textContent!, /report.pdf|计划.docx/);
    payload = { ...windows.complete, context_ref: { context_window: {
      status: "/private/status", history_candidate_count: -1, history_authorized_count: "4", history_omitted_count: 1.5,
      legacy_history_excluded: "true", selected_file_names: ["C:\\private\\report.pdf", "/private/计划.docx", "report.pdf", "runtime_path.txt", "unsafe%2Fname"],
      file_ids: ["file-private"], skill_ids: ["skill-private"], payload: { secret: true },
    } } };
    await view.refresh();
    const section = view.container.querySelector('[data-run-context-window="degraded"]');
    assert.ok(section);
    assert.deepEqual(Array.from(section.querySelectorAll("dd"), node => node.textContent), ["0", "0", "0"]);
    assert.deepEqual(Array.from(section.querySelectorAll("li"), node => node.textContent), ["report.pdf", "计划.docx"]);
    assert.doesNotMatch(section.textContent!, /private|runtime_path|unsafe|旧版历史已排除/);
  } finally { await view.close(); }
});

test("newer refresh retains its window when an older response arrives last", async () => {
  const old = deferred<Response>();
  let count = 0;
  const view = await harness(() => ++count === 1 ? old.promise : json(windows.trimmed));
  try {
    let pending!: Promise<void>;
    await act(async () => { pending = view.lifecycle.refresh(); });
    await view.refresh();
    await act(async () => { old.resolve(json(windows.complete)); await pending; });
    assert.ok(view.container.querySelector('[data-run-context-window="trimmed"]'));
    assert.equal(view.lifecycle.getSnapshot().playback?.context_ref?.context_window.status, "trimmed");
  } finally { await view.close(); }
});

test("A-to-B-to-A supersession cannot publish the first owner's window", async () => {
  const old = deferred<Response>();
  let count = 0;
  let firstSignal: AbortSignal | null | undefined;
  const view = await harness((_input, init) => {
    if (++count === 1) { firstSignal = init?.signal; return old.promise; }
    return json(windows.degraded);
  });
  try {
    let pending!: Promise<void>;
    await act(async () => { pending = view.lifecycle.refresh(); });
    await act(async () => {
      view.lifecycle.bindParent(parent("run-b", 2));
      view.lifecycle.bindParent(parent("run-a", 3));
    });
    await view.refresh();
    assert.equal(firstSignal?.aborted, true);
    await act(async () => { old.resolve(json(windows.complete)); await pending; });
    assert.ok(view.container.querySelector('[data-run-context-window="degraded"]'));
    assert.equal(view.lifecycle.getSnapshot().owner?.chatHistoryGeneration, 3);
  } finally { await view.close(); }
});

test("unmounted invalidated parent ignores a transport that completes after abort", async () => {
  const old = deferred<Response>();
  let signal: AbortSignal | null | undefined;
  const view = await harness((_input, init) => { signal = init?.signal; return old.promise; });
  try {
    let pending!: Promise<void>;
    await act(async () => { pending = view.lifecycle.refresh(); });
    await view.unmount();
    assert.equal(signal?.aborted, true);
    const invalidated = view.lifecycle.getSnapshot();
    await act(async () => { old.resolve(json(windows.complete)); await pending; });
    assert.equal(view.lifecycle.getSnapshot(), invalidated);
    assert.equal(view.container.innerHTML, "");
  } finally { await view.close(); }
});


test("auth identity replacement clears context and rejects the revoked owner's late data", async () => {
  const old = deferred<Response>();
  let count = 0;
  const view = await harness(() => ++count === 1 ? old.promise : json(windows.degraded));
  try {
    let pending!: Promise<void>;
    await act(async () => { pending = view.lifecycle.refresh(); });
    await act(async () => {
      const identity = parent();
      view.lifecycle.bindParent({ ...identity, authRevision: 2, auth: { ...identity.auth, incarnation: "replacement-incarnation", userId: "user-b" } });
    });
    assert.equal(view.container.querySelector("[data-run-context-window]"), null);
    await view.refresh();
    await act(async () => { old.resolve(json(windows.complete)); await pending; });
    assert.ok(view.container.querySelector('[data-run-context-window="degraded"]'));
    assert.doesNotMatch(view.container.textContent!, /report.pdf|计划.docx/);
    assert.equal(view.lifecycle.getSnapshot().owner?.auth.userId, "user-b");
  } finally { await view.close(); }
});
