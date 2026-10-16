import assert from "node:assert/strict";
import test from "node:test";

import { installSelectTestDom } from "../../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../../common/__tests__/lobeUiTestRoot";

installSelectTestDom();

test("mounted RunPlaybackPanel ignores an aborted stale owner read after run replacement", async () => {
  const React = await import("react");
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
  const root = await createLobeTestRoot(container);
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
    const panel = container.querySelector("[data-run-playback-panel]") as HTMLElement;
    assert.equal(panel.getAttribute("aria-busy"), "false");
  } finally {
    globalThis.fetch = originalFetch;
    await React.act(async () => root.unmount());
  }
});

for (const rotation of ["none", "marker", "role", "marker and role"]) {
  test(`mounted RunPlaybackPanel settles both reads with ${rotation} rotation`, async () => {
    const React = await import("react");
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
    let resolveReadiness!: (response: Response) => void;
    // Await the real allSettled continuation, not only the fetch promises.
    let pendingRead!: Promise<void>;
    const refresh = lifecycle.refresh.bind(lifecycle);
    lifecycle.refresh = (...args) => {
      pendingRead = refresh(...args);
      return pendingRead;
    };
    let signal: AbortSignal | null = null;
    globalThis.fetch = ((input, options) => {
      signal = options?.signal as AbortSignal;
      return new Promise<Response>((resolve) => {
        if (String(input).includes("/runs/run-a/playback")) resolvePlayback = resolve;
        else if (String(input).includes("/sessions/session-a/status")) resolveReadiness = resolve;
        else throw new Error(`unexpected request: ${String(input)}`);
      });
    }) as typeof fetch;
    const container = document.createElement("div");
    const root = await createLobeTestRoot(container);

    try {
      await React.act(async () => {
        root.render(React.createElement(RunPlaybackPanel, { lifecycle, panelKey: "run-playback:run-a" }));
        lifecycle.open();
        await Promise.resolve();
      });
      assert.ok(signal);
      assert.equal(typeof resolvePlayback, "function");
      assert.equal(typeof resolveReadiness, "function");
      assert.ok(pendingRead);
      await React.act(async () => {
        if (rotation !== "none") lifecycle.bindParent({
          chatHistoryGeneration: 1,
          authRevision: 2,
          auth: {
            incarnation: "incarnation-a",
            sessionMarker: rotation.includes("marker") ? "marker-b" : "marker-a",
            tenantId: "tenant-a",
            userId: "user-a",
            roles: rotation.includes("role") ? ["reviewer"] : ["member"],
            permissions: ["chat:write"],
            isAdmin: false,
            isActive: true,
          },
          sessionId: "session-a",
          runId: "run-a",
        });
      });
      assert.equal((signal as AbortSignal).aborted, rotation !== "none");
      resolvePlayback(
        new Response(
          JSON.stringify({ run_id: "run-a", timeline: [], events: [], artifacts: [], steps: [] }),
        ),
      );
      resolveReadiness(new Response(JSON.stringify({ session_id: "session-a", run_id: "run-a", status: "running" })));
      await React.act(async () => { await pendingRead; });
      const snapshot = lifecycle.getSnapshot();
      assert.equal(snapshot.owner?.auth.sessionMarker, rotation.includes("marker") ? "marker-b" : "marker-a");
      assert.deepEqual(snapshot.owner?.auth.roles, rotation.includes("role") ? ["reviewer"] : ["member"]);
      if (rotation === "none") {
        assert.equal(snapshot.playback?.run_id, "run-a", "settled current reads must publish");
        assert.equal(snapshot.readiness?.run_id, "run-a");
      } else {
        assert.equal(snapshot.playback, null, "settled stale playback must not publish");
        assert.equal(snapshot.readiness, null, "settled stale readiness must not publish");
      }
    } finally {
      globalThis.fetch = originalFetch;
      await React.act(async () => root.unmount());
    }
  });
}

for (const warm of [false, true]) {
  test(`mounted playback exposes ${warm ? "stale refresh" : "cold read"} failure despite successful readiness and retries GET only`, async () => {
    const React = await import("react");
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
    let unsafeDetails = false;
    const requests: string[] = [];
    globalThis.fetch = (async (input, options) => {
      requests.push(options?.method ?? "GET");
      const playback = String(input).includes("/playback");
      if ((playback && failPlayback) || (!playback && failReadiness)) {
        return new Response(JSON.stringify({ detail: {
          code: unsafeDetails ? "../private-secret-code" : "service_unavailable",
          diagnostic_id: unsafeDetails ? "private-secret-id" : "diag_0123456789abcdef",
          message: "private-secret-exception",
        } }), { status: 503 });
      }
      return new Response(JSON.stringify(playback ? {
        run_id: "run-a", run: { status: "running" }, timeline: [], events: [{ event_id: "event-a", event_type: "run.started", message: "已保留的回放事件" }], artifacts: [], steps: [],
      } : { session_id: "session-a", run_id: "run-a", status: "running" }));
    }) as typeof fetch;
    const container = document.createElement("div");
    const root = await createLobeTestRoot(container);
    try {
      await React.act(async () => { root.render(React.createElement(RunPlaybackPanel, { lifecycle, panelKey: "run-playback:run-a" })); await lifecycle.refresh(); });
      const previous = lifecycle.getSnapshot().playback;
      if (warm) { failPlayback = true; await React.act(async () => { await lifecycle.refresh(); }); }
      assert.equal(container.querySelector("[data-run-playback-panel]")?.getAttribute("data-run-playback-state"), warm ? "ready" : "error");
      assert.ok(container.querySelector('[role="alert"]'), "read failure must be visible");
      assert.doesNotMatch(container.textContent ?? "", /private-secret-exception/);
      assert.match(container.textContent ?? "", /HTTP 503/);
      assert.match(container.textContent ?? "", /service_unavailable/);
      assert.match(container.textContent ?? "", /diag_0123456789abcdef/);
      if (warm) {
        assert.strictEqual(lifecycle.getSnapshot().playback, previous);
        assert.match(container.textContent ?? "", /上次/);
        assert.match(container.textContent ?? "", /已保留的回放事件/);
      }
      failReadiness = true;
      await React.act(async () => { await lifecycle.refresh(); });
      assert.ok(container.querySelector('[role="alert"]'), "both failed reads must remain visible");
      assert.match(container.textContent ?? "", /运行状态更新失败/);
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
      assert.match(container.textContent ?? "", /HTTP 503/);
      assert.match(container.textContent ?? "", /service_unavailable/);
      assert.match(container.textContent ?? "", /diag_0123456789abcdef/);
      assert.doesNotMatch(container.textContent ?? "", /回放更新失败/);
      assert.match(container.textContent ?? "", /已保留的回放事件/);
      unsafeDetails = true;
      failPlayback = true;
      await React.act(async () => { await lifecycle.refresh(); });
      assert.doesNotMatch(container.textContent ?? "", /private-secret|service_unavailable|diag_0123456789abcdef/);
      assert.match(container.textContent ?? "", /HTTP 503/);
      assert.ok(requests.length >= 4);
      assert.ok(requests.every((method) => method === "GET"));
    } finally { globalThis.fetch = originalFetch; await React.act(async () => root.unmount()); }
  });
}
