import assert from "node:assert/strict";
import test, { after, beforeEach } from "node:test";

import { ApiRequestError } from "../../../services/api/fetch.ts";
import { sessionApi } from "../../../services/api/session.ts";
import {
  RunControlLifecycle,
  type RunControlChild,
  type RunControlOwner,
} from "../runControlLifecycle.ts";

class MemorySessionStorage implements Storage {
  private readonly values = new Map<string, string>();

  get length(): number {
    return this.values.size;
  }

  clear(): void {
    this.values.clear();
  }

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  key(index: number): string | null {
    return [...this.values.keys()][index] ?? null;
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

class FailingSessionStorage extends MemorySessionStorage {
  writes = 0;

  constructor(private readonly failOnWrite: number) {
    super();
  }

  override setItem(key: string, value: string): void {
    this.writes += 1;
    if (this.writes === this.failOnWrite) {
      throw new Error("session storage write failed");
    }
    super.setItem(key, value);
  }
}

const originalSessionStorageDescriptor = Object.getOwnPropertyDescriptor(
  globalThis,
  "sessionStorage",
);
const sessionStorage = new MemorySessionStorage();

function setSessionStorage(value: Storage | undefined): void {
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value,
  });
}

setSessionStorage(sessionStorage);
beforeEach(() => {
  sessionStorage.clear();
  setSessionStorage(sessionStorage);
});
after(() => {
  if (originalSessionStorageDescriptor) {
    Object.defineProperty(
      globalThis,
      "sessionStorage",
      originalSessionStorageDescriptor,
    );
  } else {
    Reflect.deleteProperty(globalThis, "sessionStorage");
  }
});

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

test("RunControlLifecycle reports a cancel acknowledgement without inventing terminal state", async () => {
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
    const result = await lifecycle.cancel();
    assert.equal(result, "acknowledged");
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
        JSON.stringify({ run_id: "run-a", timeline: [], events: [], artifacts: [], steps: [] }),
      ),
    );
    sessionApi.cancelRun = originalCancel;
    sessionApi.getStatus = originalStatus;
    globalThis.fetch = originalFetch;
  }
});

test("RunControlLifecycle reports unavailable without sending a cancel request", async () => {
  const lifecycle = new RunControlLifecycle();
  const originalCancel = sessionApi.cancelRun;
  let cancelCalls = 0;
  sessionApi.cancelRun = (async () => {
    cancelCalls += 1;
    return { run_id: "run-a", session_id: "session-a", status: "cancel_requested" };
  }) as typeof sessionApi.cancelRun;

  try {
    assert.equal(await lifecycle.cancel(), "unavailable");
    assert.equal(cancelCalls, 0);
  } finally {
    sessionApi.cancelRun = originalCancel;
  }
});

test("RunControlLifecycle reports an unconfirmed cancel request without fabricating success", async () => {
  const lifecycle = new RunControlLifecycle();
  const originalCancel = sessionApi.cancelRun;
  const originalStatus = sessionApi.getStatus;
  const originalFetch = globalThis.fetch;
  sessionApi.cancelRun = (async () => {
    throw new TypeError("network response unavailable");
  }) as typeof sessionApi.cancelRun;
  sessionApi.getStatus = (async () => ({
    session_id: "session-a",
    run_id: "run-a",
    status: "running",
  })) as typeof sessionApi.getStatus;
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify({ run_id: "run-a", timeline: [], events: [], artifacts: [], steps: [] }),
    )) as typeof fetch;
  lifecycle.configure({
    adoptRunControlChild: async () => "superseded",
    reconnectRunControlOwner: async () => {},
  });
  lifecycle.bindParent(parent());

  try {
    assert.equal(await lifecycle.cancel(), "unconfirmed");
    assert.equal(lifecycle.getSnapshot().phase, "unconfirmed");
  } finally {
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
      JSON.stringify({ run_id: "run-a", timeline: [], events: [], artifacts: [], steps: [] }),
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
    assert.equal(
      lifecycle.getSnapshot().canRetry,
      false,
      "an unknown post-commit failure must keep mutation recovery fail-closed",
    );
    assert.equal(statusReads, 1, "recovery may only read readiness");
    assert.equal(playbackReads, 1, "recovery may only read playback");
  } finally {
    sessionApi.retryRun = originalRetry;
    sessionApi.getStatus = originalStatus;
    globalThis.fetch = originalFetch;
  }
});

test("RunControlLifecycle unlocks retry after deterministic no-side-effect rejections", async () => {
  const lifecycle = new RunControlLifecycle();
  const originalRetry = sessionApi.retryRun;
  const originalStatus = sessionApi.getStatus;
  const originalFetch = globalThis.fetch;
  const statuses = [409, 412, 422];
  let mutationIndex = 0;
  sessionApi.retryRun = (async () => {
    const status = statuses[mutationIndex++];
    throw new ApiRequestError(`deterministic rejection ${status}`, status);
  }) as typeof sessionApi.retryRun;
  sessionApi.getStatus = (async () => ({
    session_id: "session-a",
    run_id: "run-a",
    status: "failed",
  })) as typeof sessionApi.getStatus;
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify({
        run: { run_id: "run-a", status: "failed" },
        timeline: [],
        events: [],
        artifacts: [],
        steps: [],
      }),
    )) as typeof fetch;
  lifecycle.configure({
    adoptRunControlChild: async () => "superseded",
    reconnectRunControlOwner: async () => {},
  });
  lifecycle.bindParent(parent());

  try {
    await lifecycle.refresh();
    assert.equal(lifecycle.getSnapshot().canRetry, true);

    for (const status of statuses) {
      await lifecycle.retry();
      assert.equal(lifecycle.getSnapshot().phase, "rejected");
      assert.equal(
        lifecycle.getSnapshot().canRetry,
        true,
        `${status} must restore retry without rebinding or reloading`,
      );
    }
    assert.equal(mutationIndex, statuses.length);
  } finally {
    sessionApi.retryRun = originalRetry;
    sessionApi.getStatus = originalStatus;
    globalThis.fetch = originalFetch;
  }
});

test("RunControlLifecycle retains direct 401/403/404/410 pending across reload", async () => {
  const originalRetry = sessionApi.retryRun;
  const originalResolve = sessionApi.resolveRunControlOperation;
  let mutations = 0;
  let resolverReads = 0;
  try {
    for (const status of [401, 403, 404, 410]) {
      sessionStorage.clear();
      sessionApi.retryRun = (async () => {
        mutations += 1;
        throw new ApiRequestError(`ambiguous direct rejection ${status}`, status);
      }) as typeof sessionApi.retryRun;

      const firstLifecycle = new RunControlLifecycle();
      firstLifecycle.configure({
        adoptRunControlChild: async () => "superseded",
        reconnectRunControlOwner: async () => {},
      });
      firstLifecycle.bindParent(parent());
      await firstLifecycle.retry();

      assert.equal(firstLifecycle.getSnapshot().phase, "unconfirmed");
      assert.equal(firstLifecycle.getSnapshot().owner?.mutationStarted, true);
      const mutationsAfterInitial = mutations;
      sessionApi.resolveRunControlOperation = (async (
        sourceRunId,
        action,
        operationId,
      ) => {
        resolverReads += 1;
        return {
          source_run_id: sourceRunId,
          action,
          operation_id: operationId,
          run_id: null,
          session_id: null,
          status: "absent",
          queue_admission: null,
        };
      }) as typeof sessionApi.resolveRunControlOperation;

      const reloadedLifecycle = new RunControlLifecycle();
      reloadedLifecycle.configure({
        adoptRunControlChild: async () => "superseded",
        reconnectRunControlOwner: async () => {},
      });
      reloadedLifecycle.bindParent(parent());

      assert.equal(reloadedLifecycle.getSnapshot().phase, "unconfirmed");
      assert.equal(reloadedLifecycle.getSnapshot().owner?.mutationStarted, true);
      reloadedLifecycle.open();
      await new Promise((resolve) => setTimeout(resolve, 0));
      await reloadedLifecycle.retry();
      assert.equal(
        mutations,
        mutationsAfterInitial,
        `${status} must not unlock a second POST after reload`,
      );
    }
    assert.equal(resolverReads, 4, "reload may perform one GET-only resolution");
  } finally {
    sessionApi.retryRun = originalRetry;
    sessionApi.resolveRunControlOperation = originalResolve;
  }
});

test("RunControlLifecycle requires durable storage before the initial mutation POST", async () => {
  const originalRetry = sessionApi.retryRun;
  let mutations = 0;
  sessionApi.retryRun = (async () => {
    mutations += 1;
    return { run_id: "run-child", session_id: "session-a", status: "queued" };
  }) as typeof sessionApi.retryRun;

  try {
    for (const unavailableStorage of [
      undefined,
      new FailingSessionStorage(1),
    ]) {
      setSessionStorage(unavailableStorage);
      const lifecycle = new RunControlLifecycle();
      lifecycle.configure({
        adoptRunControlChild: async () => "superseded",
        reconnectRunControlOwner: async () => {},
      });
      lifecycle.bindParent(parent());

      await lifecycle.retry();

      assert.equal(mutations, 0, "storage failure must prevent the initial POST");
      assert.equal(lifecycle.getSnapshot().phase, "rejected");
      assert.equal(lifecycle.getSnapshot().owner?.mutationStarted, true);
      assert.equal(lifecycle.getSnapshot().canRetry, false);
    }
  } finally {
    setSessionStorage(sessionStorage);
    sessionApi.retryRun = originalRetry;
  }
});

test("RunControlLifecycle sends no resolver or replay POST when a fence write fails", async () => {
  const originalRetry = sessionApi.retryRun;
  const originalResolve = sessionApi.resolveRunControlOperation;
  let mutations = 0;
  let resolverReads = 0;
  sessionApi.retryRun = (async () => {
    mutations += 1;
    throw new ApiRequestError("initial response lost", 502);
  }) as typeof sessionApi.retryRun;
  sessionApi.resolveRunControlOperation = (async (
    sourceRunId,
    action,
    operationId,
  ) => {
    resolverReads += 1;
    return {
      source_run_id: sourceRunId,
      action,
      operation_id: operationId,
      run_id: null,
      session_id: null,
      status: "absent",
      queue_admission: null,
    };
  }) as typeof sessionApi.resolveRunControlOperation;

  try {
    setSessionStorage(new FailingSessionStorage(2));
    const resolverFenceFailure = new RunControlLifecycle();
    resolverFenceFailure.configure({
      adoptRunControlChild: async () => "superseded",
      reconnectRunControlOwner: async () => {},
    });
    resolverFenceFailure.bindParent(parent());
    await resolverFenceFailure.retry();

    assert.equal(mutations, 1, "only the durably fenced initial POST is allowed");
    assert.equal(resolverReads, 0, "resolver requires its own durable fence");
    assert.equal(resolverFenceFailure.getSnapshot().phase, "unconfirmed");
    assert.equal(resolverFenceFailure.getSnapshot().owner?.mutationStarted, true);

    mutations = 0;
    resolverReads = 0;
    setSessionStorage(new FailingSessionStorage(3));
    const replayFenceFailure = new RunControlLifecycle();
    replayFenceFailure.configure({
      adoptRunControlChild: async () => "superseded",
      reconnectRunControlOwner: async () => {},
    });
    replayFenceFailure.bindParent(parent());
    await replayFenceFailure.retry();

    assert.equal(resolverReads, 1, "the durably fenced resolver may prove absence");
    assert.equal(
      mutations,
      1,
      "failure to persist the fresh replay operation must prevent a replay POST",
    );
    assert.equal(replayFenceFailure.getSnapshot().phase, "unconfirmed");
    assert.equal(replayFenceFailure.getSnapshot().owner?.mutationStarted, true);
  } finally {
    setSessionStorage(sessionStorage);
    sessionApi.retryRun = originalRetry;
    sessionApi.resolveRunControlOperation = originalResolve;
  }
});

test("RunControlLifecycle keeps resolver 409/412/422 pending and never replays the POST", async () => {
  for (const status of [409, 412, 422]) {
    sessionStorage.clear();
    const lifecycle = new RunControlLifecycle();
    const originalRetry = sessionApi.retryRun;
    const originalResolve = sessionApi.resolveRunControlOperation;
    const originalStatus = sessionApi.getStatus;
    const originalFetch = globalThis.fetch;
    let mutations = 0;
    let resolverReads = 0;
    sessionApi.retryRun = (async () => {
      mutations += 1;
      throw new ApiRequestError("initial response lost", 502);
    }) as typeof sessionApi.retryRun;
    sessionApi.resolveRunControlOperation = (async (
      sourceRunId,
      action,
      operationId,
    ) => {
      resolverReads += 1;
      if (resolverReads === 1) {
        throw new ApiRequestError(`resolver rejection ${status}`, status);
      }
      return {
        source_run_id: sourceRunId,
        action,
        operation_id: operationId,
        run_id: null,
        session_id: null,
        status: "absent",
        queue_admission: null,
      };
    }) as typeof sessionApi.resolveRunControlOperation;
    sessionApi.getStatus = (async () => ({
      session_id: "session-a",
      run_id: "run-a",
      status: "failed",
    })) as typeof sessionApi.getStatus;
    globalThis.fetch = (async () =>
      new Response(
        JSON.stringify({
          run: { run_id: "run-a", status: "failed" },
          timeline: [],
          events: [],
          artifacts: [],
          steps: [],
        }),
      )) as typeof fetch;
    lifecycle.configure({
      adoptRunControlChild: async () => "superseded",
      reconnectRunControlOwner: async () => {},
    });
    lifecycle.bindParent(parent());

    try {
      await lifecycle.retry();
      assert.equal(lifecycle.getSnapshot().phase, "unconfirmed");
      assert.equal(lifecycle.getSnapshot().canRetry, false);
      assert.equal(mutations, 1);
      assert.equal(resolverReads, 1);

      const reloadedLifecycle = new RunControlLifecycle();
      reloadedLifecycle.configure({
        adoptRunControlChild: async () => "superseded",
        reconnectRunControlOwner: async () => {},
      });
      reloadedLifecycle.bindParent(parent());
      assert.equal(reloadedLifecycle.getSnapshot().phase, "unconfirmed");
      assert.equal(reloadedLifecycle.getSnapshot().owner?.mutationStarted, true);
      reloadedLifecycle.open();
      await new Promise((resolve) => setTimeout(resolve, 0));
      assert.equal(resolverReads, 2, `${status} must retain the pending operation`);
      assert.equal(mutations, 1, `${status} must block replay after resolver rejection`);

      await reloadedLifecycle.retry();
      assert.equal(mutations, 1, `${status} must keep manual retry locked`);
    } finally {
      sessionApi.retryRun = originalRetry;
      sessionApi.resolveRunControlOperation = originalResolve;
      sessionApi.getStatus = originalStatus;
      globalThis.fetch = originalFetch;
    }
  }
});

test("RunControlLifecycle keeps replay 409/412/422 pending and blocks another POST", async () => {
  for (const status of [409, 412, 422]) {
    sessionStorage.clear();
    const lifecycle = new RunControlLifecycle();
    const originalRetry = sessionApi.retryRun;
    const originalResolve = sessionApi.resolveRunControlOperation;
    const originalStatus = sessionApi.getStatus;
    let mutations = 0;
    let resolverReads = 0;
    const operationIds: string[] = [];
    sessionApi.retryRun = (async (_runId, operationId) => {
      mutations += 1;
      operationIds.push(operationId);
      if (mutations === 1) {
        throw new ApiRequestError("initial response lost", 502);
      }
      throw new ApiRequestError(`replay rejection ${status}`, status);
    }) as typeof sessionApi.retryRun;
    sessionApi.resolveRunControlOperation = (async (
      sourceRunId,
      action,
      operationId,
    ) => {
      resolverReads += 1;
      return {
        source_run_id: sourceRunId,
        action,
        operation_id: operationId,
        run_id: null,
        session_id: null,
        status: "absent",
        queue_admission: null,
      };
    }) as typeof sessionApi.resolveRunControlOperation;
    sessionApi.getStatus = (async () => ({
      session_id: "session-a",
      run_id: "run-a",
      status: "failed",
    })) as typeof sessionApi.getStatus;
    lifecycle.configure({
      adoptRunControlChild: async () => "superseded",
      reconnectRunControlOwner: async () => {},
    });
    lifecycle.bindParent(parent());

    try {
      await lifecycle.retry();
      assert.equal(lifecycle.getSnapshot().phase, "unconfirmed");
      assert.equal(lifecycle.getSnapshot().canRetry, false);
      assert.equal(mutations, 2, "one replay is allowed only after exact absence");
      assert.notEqual(
        operationIds[0],
        operationIds[1],
        "authoritative absence must create a fresh durable replay operation",
      );
      assert.equal(resolverReads, 1);

      await lifecycle.retry();
      assert.equal(mutations, 2, `${status} must keep manual retry locked`);

      const reloadedLifecycle = new RunControlLifecycle();
      reloadedLifecycle.configure({
        adoptRunControlChild: async () => "superseded",
        reconnectRunControlOwner: async () => {},
      });
      reloadedLifecycle.bindParent(parent());
      assert.equal(reloadedLifecycle.getSnapshot().phase, "unconfirmed");
      assert.equal(reloadedLifecycle.getSnapshot().owner?.mutationStarted, true);
      reloadedLifecycle.open();
      await new Promise((resolve) => setTimeout(resolve, 0));
      assert.equal(resolverReads, 2, `${status} must retain the pending operation`);
      assert.equal(mutations, 2, `${status} must not issue another replay POST`);
    } finally {
      sessionApi.retryRun = originalRetry;
      sessionApi.resolveRunControlOperation = originalResolve;
      sessionApi.getStatus = originalStatus;
    }
  }
});
