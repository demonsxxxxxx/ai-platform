import assert from "node:assert/strict";
import test from "node:test";

import { ApiRequestError } from "../../../services/api/fetch.ts";
import { sessionApi } from "../../../services/api/session.ts";
import {
  RunControlLifecycle,
  type RunControlChild,
  type RunControlOwner,
} from "../runControlLifecycle.ts";

function parent(
  historyGeneration = 1,
  overrides: Partial<{ sessionId: string; runId: string; authRevision: number }> = {},
) {
  return {
    chatHistoryGeneration: historyGeneration,
    authRevision: overrides.authRevision ?? 1,
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
    sessionId: overrides.sessionId ?? "session-a",
    runId: overrides.runId ?? "run-a",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

test("RunControlLifecycle sends at most one mutation for one owner", async () => {
  const lifecycle = new RunControlLifecycle();
  const originalRetry = sessionApi.retryRun;
  const pending = deferred<Awaited<ReturnType<typeof sessionApi.retryRun>>>();
  let mutations = 0;
  let adoptions = 0;
  sessionApi.retryRun = (() => {
    mutations += 1;
    return pending.promise;
  }) as typeof sessionApi.retryRun;
  lifecycle.configure({
    adoptRunControlChild: async () => {
      adoptions += 1;
      return "superseded";
    },
    reconnectRunControlOwner: async () => {},
  });
  lifecycle.bindParent(parent());

  try {
    const first = lifecycle.retry();
    const second = lifecycle.retry();
    assert.equal(mutations, 1, "double click must not send a second POST");
    pending.resolve({ run_id: "run-child", session_id: "session-a", status: "queued" });
    await Promise.all([first, second]);
    assert.equal(adoptions, 1);
  } finally {
    sessionApi.retryRun = originalRetry;
  }
});

test("RunControlLifecycle silently drops a delayed A action across A-to-B-to-A", async () => {
  const lifecycle = new RunControlLifecycle();
  const originalRetry = sessionApi.retryRun;
  const pending = deferred<Awaited<ReturnType<typeof sessionApi.retryRun>>>();
  let adoptions = 0;
  sessionApi.retryRun = (() => pending.promise) as typeof sessionApi.retryRun;
  lifecycle.configure({
    adoptRunControlChild: async () => {
      adoptions += 1;
      return "adopted";
    },
    reconnectRunControlOwner: async () => {},
  });
  lifecycle.bindParent(parent());

  try {
    const action = lifecycle.retry();
    lifecycle.bindParent(parent(2, { sessionId: "session-b", runId: "run-b" }));
    lifecycle.bindParent(parent(3));
    pending.resolve({ run_id: "run-child-a", session_id: "session-a", status: "queued" });
    await action;
    assert.equal(adoptions, 0, "stale A may not ask the parent to load A-child");
    assert.equal(lifecycle.getSnapshot().owner?.chatHistoryGeneration, 3);
  } finally {
    sessionApi.retryRun = originalRetry;
  }
});

test("RunControlLifecycle preserves a created-but-unopened child for GET-only reopen", async () => {
  const lifecycle = new RunControlLifecycle();
  const originalResume = sessionApi.resumeRun;
  let mutations = 0;
  let adoptionOwner: RunControlOwner | null = null;
  let adoptionChild: RunControlChild | null = null;
  sessionApi.resumeRun = (async () => {
    mutations += 1;
    return { run_id: "run-child", session_id: "session-a", status: "queued" };
  }) as typeof sessionApi.resumeRun;
  lifecycle.configure({
    adoptRunControlChild: async (owner, child) => {
      adoptionOwner = owner;
      adoptionChild = child;
      lifecycle.retainCreatedUnopened(parent(2), child);
      return "created_unopened";
    },
    reconnectRunControlOwner: async () => {},
  });
  lifecycle.bindParent(parent());

  try {
    await lifecycle.resume();
    assert.equal(mutations, 1);
    assert.equal(lifecycle.getSnapshot().phase, "created_unopened");
    assert.deepEqual(lifecycle.getSnapshot().child, {
      sessionId: "session-a",
      runId: "run-child",
      status: "queued",
    });

    await lifecycle.reopenChild();
    assert.equal(mutations, 1, "reopen must not replay the POST");
    assert.ok(adoptionOwner);
    assert.deepEqual(adoptionChild, {
      sessionId: "session-a",
      runId: "run-child",
      status: "queued",
    });
  } finally {
    sessionApi.resumeRun = originalResume;
  }
});

test("RunControlLifecycle keeps cancel acknowledgement separate from terminal convergence", async () => {
  const lifecycle = new RunControlLifecycle();
  const originalCancel = sessionApi.cancelRun;
  const originalStatus = sessionApi.getStatus;
  const originalFetch = globalThis.fetch;
  const playback = deferred<Response>();
  let cancelCalls = 0;
  sessionApi.cancelRun = (async () => {
    cancelCalls += 1;
    return { run_id: "run-a", session_id: "session-a", status: "cancel_requested" };
  }) as typeof sessionApi.cancelRun;
  sessionApi.getStatus = (async () => ({
    session_id: "session-a",
    run_id: "run-a",
    status: "running",
  })) as typeof sessionApi.getStatus;
  globalThis.fetch = (() => playback.promise) as typeof fetch;
  lifecycle.configure({
    adoptRunControlChild: async () => "superseded",
    reconnectRunControlOwner: async () => {},
  });
  lifecycle.bindParent(parent());

  try {
    await lifecycle.cancel();
    assert.equal(cancelCalls, 1);
    assert.equal(lifecycle.getSnapshot().phase, "cancel_requested");
    assert.equal(
      lifecycle.getSnapshot().owner?.runId,
      "run-a",
      "the lifecycle must not invent a terminal transition from an acknowledgement",
    );
  } finally {
    playback.resolve(
      new Response(
        JSON.stringify({ run_id: "run-a", timeline: [], events: [], artifacts: [], steps: [], multi_agent: null }),
      ),
    );
    sessionApi.cancelRun = originalCancel;
    sessionApi.getStatus = originalStatus;
    globalThis.fetch = originalFetch;
  }
});

test("RunControlLifecycle treats a post-commit retry 5xx as unconfirmed and GET-only", async () => {
  const lifecycle = new RunControlLifecycle();
  const originalRetry = sessionApi.retryRun;
  const originalStatus = sessionApi.getStatus;
  const originalFetch = globalThis.fetch;
  let mutations = 0;
  let statusReads = 0;
  let playbackReads = 0;
  sessionApi.retryRun = (async () => {
    mutations += 1;
    throw new ApiRequestError("gateway response lost after commit", 502);
  }) as typeof sessionApi.retryRun;
  sessionApi.getStatus = (async () => {
    statusReads += 1;
    return { session_id: "session-a", run_id: "run-a", status: "running" };
  }) as typeof sessionApi.getStatus;
  globalThis.fetch = (async () => {
    playbackReads += 1;
    return new Response(
      JSON.stringify({ run_id: "run-a", timeline: [], events: [], artifacts: [], steps: [], multi_agent: null }),
    );
  }) as typeof fetch;
  lifecycle.configure({
    adoptRunControlChild: async () => "superseded",
    reconnectRunControlOwner: async () => {},
  });
  lifecycle.bindParent(parent());

  try {
    await lifecycle.retry();
    await Promise.resolve();
    assert.equal(mutations, 1, "unknown 5xx must not replay the POST");
    assert.equal(lifecycle.getSnapshot().phase, "unconfirmed");
    assert.equal(statusReads, 1, "recovery may only read readiness");
    assert.equal(playbackReads, 1, "recovery may only read playback");
  } finally {
    sessionApi.retryRun = originalRetry;
    sessionApi.getStatus = originalStatus;
    globalThis.fetch = originalFetch;
  }
});
