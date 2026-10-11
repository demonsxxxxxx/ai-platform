import assert from "node:assert/strict";
import test from "node:test";

// jsdom 26 ships no declarations; this test uses only its runtime constructor.
// @ts-expect-error jsdom is the pinned mounted-test runtime.
import { JSDOM } from "jsdom";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { url: "http://test.local/" });
const { document } = dom.window;
Object.assign(globalThis, {
  window: dom.window,
  document,
  Node: dom.window.Node,
  HTMLElement: dom.window.HTMLElement,
  IS_REACT_ACT_ENVIRONMENT: true,
});

test("mounted RunPlaybackPanel ignores an aborted stale owner read after run replacement", async () => {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { RunPlaybackPanel } = await import("../RunPlaybackPanel.tsx");
  const { RunControlLifecycle } = await import(
    "../../../../hooks/useAgent/runControlLifecycle.ts"
  );
  const lifecycle = new RunControlLifecycle();
  lifecycle.configure({
    adoptRunControlChild: async () => "superseded",
    reconnectRunControlOwner: async () => {},
  });
  lifecycle.bindParent({
    chatHistoryGeneration: 1,
    authRevision: 1,
    auth: {
      incarnation: "incarnation-a",
      sessionMarker: "marker-a",
      tenantId: "tenant-a",
      userId: "user-a",
      roles: ["member"],
      permissions: ["chat:write"],
      isAdmin: false,
      isActive: true,
    },
    sessionId: "session-a",
    runId: "run-a",
  });

  const originalFetch = globalThis.fetch;
  let resolvePlayback!: (response: Response) => void;
  let oldSignal: AbortSignal | null = null;
  globalThis.fetch = (async (input, options) => {
    const url = String(input);
    if (url.includes("/runs/run-a/playback")) {
      oldSignal = options?.signal as AbortSignal;
      return new Promise<Response>((resolve) => {
        resolvePlayback = resolve;
      });
    }
    if (url.includes("/sessions/session-a/status")) {
      return new Response(
        JSON.stringify({ session_id: "session-a", run_id: "run-a", status: "running" }),
      );
    }
    throw new Error(`unexpected request: ${url}`);
  }) as typeof fetch;

  const container = document.createElement("div");
  const root = createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(
        React.createElement(RunPlaybackPanel, {
          lifecycle,
          panelKey: "run-playback:run-a",
        }),
      );
    });
    await React.act(async () => {
      lifecycle.open();
      await Promise.resolve();
    });
    assert.ok(oldSignal, "mounted panel should start the lifecycle GET");

    await React.act(async () => {
      lifecycle.bindParent({
        chatHistoryGeneration: 2,
        authRevision: 1,
        auth: {
          incarnation: "incarnation-a",
          sessionMarker: "marker-a",
          tenantId: "tenant-a",
          userId: "user-a",
          roles: ["member"],
          permissions: ["chat:write"],
          isAdmin: false,
          isActive: true,
        },
        sessionId: "session-b",
        runId: "run-b",
      });
    });
    assert.equal((oldSignal as AbortSignal).aborted, true);

    resolvePlayback(
      new Response(
        JSON.stringify({ run_id: "run-a", timeline: [], events: [], artifacts: [], steps: [] }),
      ),
    );
    await React.act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    const snapshot = lifecycle.getSnapshot();
    assert.equal(snapshot.owner?.runId, "run-b");
    assert.equal(snapshot.playback, null, "stale run A must not publish over run B");
    const panel = container.firstChild as HTMLElement;
    assert.equal(panel.getAttribute("aria-busy"), "false");
  } finally {
    globalThis.fetch = originalFetch;
    await React.act(async () => root.unmount());
  }
});

test("mounted RunPlaybackPanel rejects a late read after marker and role rotation", async () => {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { RunPlaybackPanel } = await import("../RunPlaybackPanel.tsx");
  const { RunControlLifecycle } = await import(
    "../../../../hooks/useAgent/runControlLifecycle.ts"
  );
  const lifecycle = new RunControlLifecycle();
  lifecycle.configure({
    adoptRunControlChild: async () => "superseded",
    reconnectRunControlOwner: async () => {},
  });
  lifecycle.bindParent({
    chatHistoryGeneration: 1,
    authRevision: 1,
    auth: {
      incarnation: "incarnation-a",
      sessionMarker: "marker-a",
      tenantId: "tenant-a",
      userId: "user-a",
      roles: ["member"],
      permissions: ["chat:write"],
      isAdmin: false,
      isActive: true,
    },
    sessionId: "session-a",
    runId: "run-a",
  });
  const originalFetch = globalThis.fetch;
  let resolvePlayback!: (response: Response) => void;
  let signal: AbortSignal | null = null;
  globalThis.fetch = ((_input, options) => {
    signal = options?.signal as AbortSignal;
    return new Promise<Response>((resolve) => {
      resolvePlayback = resolve;
    });
  }) as typeof fetch;
  const container = document.createElement("div");
  const root = createRoot(container as never);

  try {
    await React.act(async () => {
      root.render(React.createElement(RunPlaybackPanel, { lifecycle, panelKey: "run-playback:run-a" }));
      lifecycle.open();
      await Promise.resolve();
    });
    assert.ok(signal);
    await React.act(async () => {
      lifecycle.bindParent({
        chatHistoryGeneration: 1,
        authRevision: 2,
        auth: {
          incarnation: "incarnation-b",
          sessionMarker: "marker-b",
          tenantId: "tenant-a",
          userId: "user-a",
          roles: ["reviewer"],
          permissions: ["chat:read"],
          isAdmin: false,
          isActive: true,
        },
        sessionId: "session-a",
        runId: "run-a",
      });
    });
    assert.equal((signal as AbortSignal).aborted, true);
    resolvePlayback(
      new Response(
        JSON.stringify({ run_id: "run-a", timeline: [], events: [], artifacts: [], steps: [] }),
      ),
    );
    await React.act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    assert.equal(lifecycle.getSnapshot().owner?.auth.sessionMarker, "marker-b");
    assert.equal(lifecycle.getSnapshot().playback, null);
  } finally {
    globalThis.fetch = originalFetch;
    await React.act(async () => root.unmount());
  }
});

for (const warm of [false, true]) {
  test(`mounted playback exposes ${warm ? "stale refresh" : "cold read"} failure despite successful readiness and retries GET only`, async () => {
    const React = await import("react");
    const { createRoot } = await import("react-dom/client");
    const { RunPlaybackPanel } = await import("../RunPlaybackPanel.tsx");
    const { RunControlLifecycle } = await import("../../../../hooks/useAgent/runControlLifecycle.ts");
    const lifecycle = new RunControlLifecycle();
    lifecycle.configure({ adoptRunControlChild: async () => "superseded", reconnectRunControlOwner: async () => {} });
    lifecycle.bindParent({ chatHistoryGeneration: 1, authRevision: 1, auth: {
      incarnation: "incarnation-a", sessionMarker: "marker-a", tenantId: "tenant-a", userId: "user-a",
      roles: ["member"], permissions: ["chat:write"], isAdmin: false, isActive: true,
    }, sessionId: "session-a", runId: "run-a" });
    const originalFetch = globalThis.fetch;
    let failPlayback = !warm;
    let failReadiness = false;
    const requests: string[] = [];
    globalThis.fetch = (async (input, options) => {
      requests.push(options?.method ?? "GET");
      const playback = String(input).includes("/playback");
      if ((playback && failPlayback) || (!playback && failReadiness)) {
        return new Response(JSON.stringify({ detail: { code: "service_unavailable", message: "private-secret-exception" } }), { status: 503 });
      }
      return new Response(JSON.stringify(playback ? {
        run_id: "run-a", run: { status: "running" }, timeline: [], events: [{ event_id: "event-a", event_type: "run.started", message: "已保留的回放事件" }], artifacts: [], steps: [],
      } : { session_id: "session-a", run_id: "run-a", status: "running" }));
    }) as typeof fetch;
    const container = document.createElement("div");
    const root = createRoot(container);
    try {
      await React.act(async () => { root.render(React.createElement(RunPlaybackPanel, { lifecycle, panelKey: "run-playback:run-a" })); await lifecycle.refresh(); });
      const previous = lifecycle.getSnapshot().playback;
      if (warm) { failPlayback = true; await React.act(async () => { await lifecycle.refresh(); }); }
      assert.equal(container.querySelector("[data-run-playback-panel]")?.getAttribute("data-run-playback-state"), warm ? "ready" : "error");
      assert.ok(container.querySelector('[role="alert"]'), "read failure must be visible");
      assert.doesNotMatch(container.textContent ?? "", /private-secret-exception/);
      if (warm) {
        assert.strictEqual(lifecycle.getSnapshot().playback, previous);
        assert.match(container.textContent ?? "", /上次/);
        assert.match(container.textContent ?? "", /已保留的回放事件/);
        failReadiness = true;
        await React.act(async () => { await lifecycle.refresh(); });
        assert.ok(container.querySelector('[role="alert"]'), "both failed reads must not hide behind old playback");
      }
      failPlayback = false;
      failReadiness = false;
      const refresh = [...container.querySelectorAll("button")].find((button) => button.textContent?.includes("刷新"));
      assert.ok(refresh, "failed reads offer an explicit GET-only refresh");
      await React.act(async () => { refresh.click(); await new Promise((resolve) => setTimeout(resolve, 0)); });
      assert.equal(container.querySelector('[role="alert"]'), null);
      assert.equal(lifecycle.getSnapshot().playback?.run_id, "run-a");
      failReadiness = true;
      await React.act(async () => { await lifecycle.refresh(); });
      assert.match(container.textContent ?? "", /运行状态更新失败/);
      assert.doesNotMatch(container.textContent ?? "", /回放更新失败/);
      assert.match(container.textContent ?? "", /已保留的回放事件/);
      assert.ok(requests.length >= 4);
      assert.ok(requests.every((method) => method === "GET"));
    } finally { globalThis.fetch = originalFetch; await React.act(async () => root.unmount()); }
  });
}
