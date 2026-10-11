import assert from "node:assert/strict";
import test from "node:test";
import { installTestDom } from "../useAgent/__tests__/testDom.ts";
import { sessionApi, type BackendSession } from "../../services/api/session.ts";
import { dispatchSessionTitleUpdated } from "../../utils/sessionTitleEvents.ts";

const dom = installTestDom();
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}
function session(id: string, name?: string): BackendSession {
  return { id, name, agent_id: "general-agent", is_active: true, metadata: {}, created_at: "", updated_at: "" };
}
type Scope = { sessionId?: string; tenantId?: string; userId?: string };
const scope = (sessionId = "a", userId = "user-a", tenantId = "tenant-a"): Scope => ({ sessionId, userId, tenantId });

async function withTitle(
  get: typeof sessionApi.get,
  run: (view: {
    render(value: Scope): Promise<void>;
    flush(action?: () => void): Promise<void>;
    unmount(): Promise<void>;
    title(): string | null;
  }) => Promise<void>,
) {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { useChatSessionTitle } = await import("../useChatSessionTitle.ts");
  const original = sessionApi.get;
  sessionApi.get = get;
  const root = createRoot(dom.document.createElement("div") as never);
  let visible: string | null = null;
  let mounted = true;
  function Probe(props: Scope) { visible = useChatSessionTitle(props); return null; }
  const flush = async (action?: () => void) => { await React.act(async () => { action?.(); await Promise.resolve(); }); };
  try {
    await run({
      render: async (value) => { await React.act(async () => { root.render(React.createElement(Probe, value)); }); },
      flush,
      unmount: async () => { if (mounted) { await React.act(async () => root.unmount()); mounted = false; } },
      title: () => visible,
    });
  } finally {
    if (mounted) await React.act(async () => root.unmount());
    sessionApi.get = original;
  }
}

test("late A hydration cannot replace B, even when the transport ignores abort", async () => {
  const a = deferred<BackendSession | null>();
  const b = deferred<BackendSession | null>();
  const signals: AbortSignal[] = [];
  await withTitle(async (id, options) => {
    signals.push(options!.signal!);
    return id === "a" ? a.promise : b.promise;
  }, async ({ render, flush, title }) => {
    await render(scope());
    await render(scope("b"));
    assert.equal(title(), null);
    assert.equal(signals[0].aborted, true);
    await flush(() => b.resolve(session("b", "B title")));
    assert.equal(title(), "B title");
    await flush(() => a.resolve(session("a", "A private title")));
    assert.equal(title(), "B title");
  });
});

test("tenant and user changes invalidate the same session ID synchronously", async () => {
  for (const next of [scope("a", "user-b"), scope("a", "user-a", "tenant-b")]) {
    const old = deferred<BackendSession | null>();
    const fresh = deferred<BackendSession | null>();
    let calls = 0;
    await withTitle(async () => ++calls === 1 ? old.promise : fresh.promise, async ({ render, flush, title }) => {
      await render(scope());
      await render(next);
      await flush(() => old.resolve(session("a", "old subject")));
      assert.equal(title(), null);
      await flush(() => fresh.resolve(session("a", "current subject")));
      assert.equal(title(), "current subject");
    });
  }
});

test("anonymous and cleared sessions hide the previous title and issue no request", async () => {
  let calls = 0;
  await withTitle(async (id) => { calls += 1; return session(id, "private title"); }, async ({ render, title }) => {
    await render(scope());
    assert.equal(title(), "private title");
    await render({ sessionId: "a" });
    assert.equal(title(), null);
    await render({ tenantId: "tenant-a", userId: "user-a" });
    assert.equal(title(), null);
    assert.equal(calls, 1);
  });
});

test("404, mismatched identity and denied reads never expose past or unverified event titles", async () => {
  for (const outcome of [null, session("other", "wrong session"), new Error("forbidden")]) {
    const pending = deferred<BackendSession | null>();
    dispatchSessionTitleUpdated({ sessionId: "a", title: "past private title" });
    await withTitle(async () => pending.promise, async ({ render, flush, title }) => {
      await render(scope());
      assert.equal(title(), null);
      await flush(() => dispatchSessionTitleUpdated({ sessionId: "a", title: "unverified event title" }));
      assert.equal(title(), null);
      await flush(() => outcome instanceof Error ? pending.reject(outcome) : pending.resolve(outcome));
      assert.equal(title(), null);
      await flush(() => dispatchSessionTitleUpdated({ sessionId: "a", title: "event after denial" }));
      assert.equal(title(), null);
    });
  }
});

test("authorized hydration cannot roll back a newer title event", async () => {
  const pending = deferred<BackendSession | null>();
  await withTitle(async () => pending.promise, async ({ render, flush, title }) => {
    await render(scope());
    await flush(() => dispatchSessionTitleUpdated({ sessionId: "a", title: "new title" }));
    assert.equal(title(), null);
    await flush(() => pending.resolve(session("a", "old title")));
    assert.equal(title(), "new title");
    await flush(() => dispatchSessionTitleUpdated({ sessionId: "b", title: "unrelated title" }));
    assert.equal(title(), "new title");
  });
});

test("already-started poll and unmounted reads cannot publish into later scopes", async () => {
  const originalSetTimeout = globalThis.setTimeout;
  const originalClearTimeout = globalThis.clearTimeout;
  let poll: (() => void) | undefined;
  const timer = {} as ReturnType<typeof setTimeout>;
  globalThis.setTimeout = ((callback: () => void, delay?: number) => {
    if (delay === 3000) { poll = callback; return timer; }
    return originalSetTimeout(callback, delay);
  }) as typeof setTimeout;
  globalThis.clearTimeout = ((id: ReturnType<typeof setTimeout>) => {
    if (id === timer) { poll = undefined; return; }
    originalClearTimeout(id);
  }) as typeof clearTimeout;
  try {
    const late = deferred<BackendSession | null>();
    let aCalls = 0;
    await withTitle(async (id) => {
      if (id === "b") return session("b", "B title");
      return ++aCalls === 1 ? session("a", "") : late.promise;
    }, async ({ render, flush, unmount, title }) => {
      await render(scope());
      assert.equal(title(), null);
      assert.ok(poll);
      await flush(() => poll!());
      assert.equal(aCalls, 2);
      await render(scope("b"));
      assert.equal(title(), "B title");
      await unmount();
      await flush(() => late.resolve(session("a", "late poll")));
      assert.equal(title(), "B title");
    });
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    globalThis.clearTimeout = originalClearTimeout;
  }
});


test("unmount aborts an initial read and removes title listeners", async () => {
  const pending = deferred<BackendSession | null>();
  let signal: AbortSignal | undefined;
  await withTitle(async (_id, options) => { signal = options?.signal; return pending.promise; }, async ({ render, flush, unmount, title }) => {
    await render(scope());
    await unmount();
    assert.equal(signal?.aborted, true);
    await flush(() => {
      pending.resolve(session("a", "late initial title"));
      dispatchSessionTitleUpdated({ sessionId: "a", title: "late event" });
    });
    assert.equal(title(), null);
  });
});
