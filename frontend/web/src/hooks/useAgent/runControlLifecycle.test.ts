import assert from "node:assert/strict";
import test from "node:test";

import { sessionApi } from "../../services/api/session.ts";
import { RunControlLifecycle } from "./runControlLifecycle.ts";

function parent() {
  return {
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
  };
}

async function refreshAfterCancel(playbackStatus: string) {
  const lifecycle = new RunControlLifecycle();
  const originalCancel = sessionApi.cancelRun;
  const originalStatus = sessionApi.getStatus;
  const originalFetch = globalThis.fetch;
  sessionApi.cancelRun = (async () => ({
    run_id: "run-a",
    session_id: "session-a",
    status: "cancel_requested",
  })) as typeof sessionApi.cancelRun;
  sessionApi.getStatus = (async () => ({
    session_id: "session-a",
    run_id: "run-a",
    status: "running",
  })) as typeof sessionApi.getStatus;
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify({
        run_id: "run-a",
        run: { status: playbackStatus },
        timeline: [],
        events: [],
        artifacts: [],
        steps: [],
        multi_agent: null,
      }),
    )) as typeof fetch;
  lifecycle.configure({
    adoptRunControlChild: async () => "superseded",
    reconnectRunControlOwner: async () => {},
  });
  lifecycle.bindParent(parent());

  try {
    await lifecycle.cancel();
    await lifecycle.refresh(lifecycle.getSnapshot().owner);
    return lifecycle.getSnapshot();
  } finally {
    sessionApi.cancelRun = originalCancel;
    sessionApi.getStatus = originalStatus;
    globalThis.fetch = originalFetch;
  }
}

test("RunControlLifecycle lets terminal cancelled playback clear stale cancel_requested", async () => {
  const snapshot = await refreshAfterCancel("cancelled");

  assert.equal(snapshot.phase, "ready");
  assert.equal(snapshot.playback?.run?.status, "cancelled");
});

test("RunControlLifecycle keeps cancel_requested for non-terminal playback", async () => {
  const snapshot = await refreshAfterCancel("running");

  assert.equal(snapshot.phase, "cancel_requested");
  assert.equal(snapshot.playback?.run?.status, "running");
});
