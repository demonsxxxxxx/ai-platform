import assert from "node:assert/strict";
import test from "node:test";

import { sessionApi } from "../../services/api/session.ts";
import { RunControlLifecycle } from "./runControlLifecycle.ts";

class MemoryStorage implements Storage {
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

function installSessionStorage(storage: Storage): () => void {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: storage,
  });
  return () => {
    if (previous) {
      Object.defineProperty(globalThis, "sessionStorage", previous);
    } else {
      delete (globalThis as { sessionStorage?: Storage }).sessionStorage;
    }
  };
}

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

async function refreshReload(playbackStatus: string) {
  const lifecycle = new RunControlLifecycle();
  const originalStatus = sessionApi.getStatus;
  const originalFetch = globalThis.fetch;
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
    await lifecycle.refresh();
    return lifecycle.getSnapshot();
  } finally {
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

test("RunControlLifecycle derives terminal controls from cancelled playback over stale readiness", async () => {
  const snapshot = await refreshReload("cancelled");

  assert.equal(snapshot.phase, "ready");
  assert.equal(snapshot.playback?.run?.status, "cancelled");
  assert.equal(snapshot.canCancel, false);
  assert.equal(snapshot.canReconnect, false);
  assert.equal(snapshot.canRetry, true);
  assert.equal(snapshot.canResume, true);
});

test("RunControlLifecycle keeps active controls for non-terminal playback", async () => {
  const snapshot = await refreshReload("running");

  assert.equal(snapshot.phase, "ready");
  assert.equal(snapshot.canCancel, true);
  assert.equal(snapshot.canReconnect, true);
  assert.equal(snapshot.canRetry, false);
  assert.equal(snapshot.canResume, false);
});

test("RunControlLifecycle resolves a lost retry response and adopts the exact child", async () => {
  const storage = new MemoryStorage();
  const restoreStorage = installSessionStorage(storage);
  const lifecycle = new RunControlLifecycle();
  const originalRetry = sessionApi.retryRun;
  const originalResolve = sessionApi.resolveRunControlOperation;
  const operationIds: string[] = [];
  const adopted: Array<{ sessionId: string; runId: string; status: string | null }> = [];
  sessionApi.retryRun = (async (_runId, operationId) => {
    operationIds.push(operationId);
    throw new TypeError("response lost after commit");
  }) as typeof sessionApi.retryRun;
  sessionApi.resolveRunControlOperation = (async (_runId, action, operationId) => {
    operationIds.push(operationId);
    return {
      source_run_id: "run-a",
      action,
      operation_id: operationId,
      run_id: "run-child",
      session_id: "session-a",
      status: "queued",
    };
  }) as typeof sessionApi.resolveRunControlOperation;
  lifecycle.configure({
    adoptRunControlChild: async (_owner, child) => {
      adopted.push(child);
      return "adopted";
    },
    reconnectRunControlOwner: async () => {},
  });
  lifecycle.bindParent(parent());

  try {
    await lifecycle.retry();
    assert.equal(operationIds.length, 2);
    assert.equal(operationIds[0], operationIds[1]);
    assert.match(operationIds[0], /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    assert.deepEqual(adopted, [
      { sessionId: "session-a", runId: "run-child", status: "queued" },
    ]);
    assert.equal(storage.length, 0);
  } finally {
    sessionApi.retryRun = originalRetry;
    sessionApi.resolveRunControlOperation = originalResolve;
    restoreStorage();
  }
});

test("RunControlLifecycle replays only the same id after authoritative absence", async () => {
  const storage = new MemoryStorage();
  const restoreStorage = installSessionStorage(storage);
  const lifecycle = new RunControlLifecycle();
  const originalResume = sessionApi.resumeRun;
  const originalResolve = sessionApi.resolveRunControlOperation;
  const operationIds: string[] = [];
  let postCount = 0;
  sessionApi.resumeRun = (async (_runId, operationId) => {
    operationIds.push(operationId);
    postCount += 1;
    if (postCount === 1) throw new TypeError("request outcome unknown");
    return {
      source_run_id: "run-a",
      action: "resume",
      operation_id: operationId,
      run_id: "run-resumed",
      session_id: "session-a",
      status: "queued",
    };
  }) as typeof sessionApi.resumeRun;
  sessionApi.resolveRunControlOperation = (async (_runId, action, operationId) => ({
    source_run_id: "run-a",
    action,
    operation_id: operationId,
    run_id: null,
    session_id: null,
    status: "absent",
  })) as typeof sessionApi.resolveRunControlOperation;
  lifecycle.configure({
    adoptRunControlChild: async () => "adopted",
    reconnectRunControlOwner: async () => {},
  });
  lifecycle.bindParent(parent());

  try {
    await lifecycle.resume();
    assert.equal(operationIds.length, 2);
    assert.equal(operationIds[0], operationIds[1]);
    assert.equal(lifecycle.getSnapshot().child?.runId, "run-resumed");
    assert.equal(storage.length, 0);
  } finally {
    sessionApi.resumeRun = originalResume;
    sessionApi.resolveRunControlOperation = originalResolve;
    restoreStorage();
  }
});

test("RunControlLifecycle reload resolves the persisted operation without inferring from a terminal parent", async () => {
  const storage = new MemoryStorage();
  const restoreStorage = installSessionStorage(storage);
  const first = new RunControlLifecycle();
  const originalRetry = sessionApi.retryRun;
  const originalResolve = sessionApi.resolveRunControlOperation;
  const originalStatus = sessionApi.getStatus;
  const originalFetch = globalThis.fetch;
  let persistedOperationId = "";
  sessionApi.retryRun = (async (_runId, operationId) => {
    persistedOperationId = operationId;
    throw new TypeError("post response lost");
  }) as typeof sessionApi.retryRun;
  sessionApi.resolveRunControlOperation = (async () => {
    throw new TypeError("resolver temporarily unavailable");
  }) as typeof sessionApi.resolveRunControlOperation;
  sessionApi.getStatus = (async () => ({
    session_id: "session-a",
    run_id: "run-a",
    status: "failed",
  })) as typeof sessionApi.getStatus;
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify({
        run_id: "run-a",
        run: { status: "failed" },
        timeline: [],
        events: [],
        artifacts: [],
        steps: [],
        multi_agent: null,
      }),
    )) as typeof fetch;
  first.configure({
    adoptRunControlChild: async () => "superseded",
    reconnectRunControlOwner: async () => {},
  });
  first.bindParent(parent());

  try {
    await first.retry();
    await first.refresh(first.getSnapshot().owner);
    assert.equal(first.getSnapshot().phase, "unconfirmed");
    assert.equal(storage.length, 1);

    let adoptedResolve!: () => void;
    const adoptedDone = new Promise<void>((resolve) => {
      adoptedResolve = resolve;
    });
    sessionApi.resolveRunControlOperation = (async (_runId, action, operationId) => ({
      source_run_id: "run-a",
      action,
      operation_id: operationId,
      run_id: "run-after-reload",
      session_id: "session-a",
      status: "queued",
    })) as typeof sessionApi.resolveRunControlOperation;
    const reloaded = new RunControlLifecycle();
    reloaded.configure({
      adoptRunControlChild: async (_owner, child) => {
        assert.equal(child.runId, "run-after-reload");
        adoptedResolve();
        return "adopted";
      },
      reconnectRunControlOwner: async () => {},
    });
    reloaded.bindParent(parent());
    reloaded.open();
    await adoptedDone;

    assert.equal(storage.length, 0);
    assert.notEqual(persistedOperationId, "");
  } finally {
    sessionApi.retryRun = originalRetry;
    sessionApi.resolveRunControlOperation = originalResolve;
    sessionApi.getStatus = originalStatus;
    globalThis.fetch = originalFetch;
    restoreStorage();
  }
});

test("RunControlLifecycle executes retry when sessionStorage is unavailable", async () => {
  const restoreStorage = installSessionStorage(null as unknown as Storage);
  const lifecycle = new RunControlLifecycle();
  const originalRetry = sessionApi.retryRun;
  const operationIds: string[] = [];
  sessionApi.retryRun = (async (_runId, operationId) => {
    operationIds.push(operationId);
    return {
      source_run_id: "run-a",
      action: "retry",
      operation_id: operationId,
      run_id: "run-memory-fallback",
      session_id: "session-a",
      status: "queued",
      queue_admission: "admitted",
    };
  }) as typeof sessionApi.retryRun;
  lifecycle.configure({
    adoptRunControlChild: async () => "adopted",
    reconnectRunControlOwner: async () => {},
  });
  lifecycle.bindParent(parent());

  try {
    await lifecycle.retry();
    assert.equal(operationIds.length, 1);
    assert.equal(lifecycle.getSnapshot().child?.runId, "run-memory-fallback");
    assert.notEqual(lifecycle.getSnapshot().phase, "rejected");
  } finally {
    sessionApi.retryRun = originalRetry;
    restoreStorage();
  }
});

test("RunControlLifecycle executes resume when sessionStorage throws", async () => {
  const throwingStorage = new MemoryStorage();
  throwingStorage.setItem = () => {
    throw new DOMException("blocked", "SecurityError");
  };
  const restoreStorage = installSessionStorage(throwingStorage);
  const lifecycle = new RunControlLifecycle();
  const originalResume = sessionApi.resumeRun;
  let mutations = 0;
  sessionApi.resumeRun = (async (_runId, operationId) => {
    mutations += 1;
    return {
      source_run_id: "run-a",
      action: "resume",
      operation_id: operationId,
      run_id: "run-storage-throws",
      session_id: "session-a",
      status: "queued",
      queue_admission: "admitted",
    };
  }) as typeof sessionApi.resumeRun;
  lifecycle.configure({
    adoptRunControlChild: async () => "adopted",
    reconnectRunControlOwner: async () => {},
  });
  lifecycle.bindParent(parent());

  try {
    await lifecycle.resume();
    assert.equal(mutations, 1);
    assert.equal(lifecycle.getSnapshot().child?.runId, "run-storage-throws");
  } finally {
    sessionApi.resumeRun = originalResume;
    restoreStorage();
  }
});

test("RunControlLifecycle replays the same id when resolver finds a child pending queue admission", async () => {
  const storage = new MemoryStorage();
  const restoreStorage = installSessionStorage(storage);
  const lifecycle = new RunControlLifecycle();
  const originalRetry = sessionApi.retryRun;
  const originalResolve = sessionApi.resolveRunControlOperation;
  const operationIds: string[] = [];
  let mutationCount = 0;
  sessionApi.retryRun = (async (_runId, operationId) => {
    mutationCount += 1;
    operationIds.push(operationId);
    if (mutationCount === 1) throw new TypeError("response lost before enqueue confirmation");
    return {
      source_run_id: "run-a",
      action: "retry",
      operation_id: operationId,
      run_id: "run-queue-recovered",
      session_id: "session-a",
      status: "queued",
      queue_admission: "admitted",
    };
  }) as typeof sessionApi.retryRun;
  sessionApi.resolveRunControlOperation = (async (_runId, action, operationId) => ({
    source_run_id: "run-a",
    action,
    operation_id: operationId,
    run_id: "run-queue-recovered",
    session_id: "session-a",
    status: "queued",
    queue_admission: "pending",
  })) as typeof sessionApi.resolveRunControlOperation;
  lifecycle.configure({
    adoptRunControlChild: async () => "adopted",
    reconnectRunControlOwner: async () => {},
  });
  lifecycle.bindParent(parent());

  try {
    await lifecycle.retry();
    assert.equal(mutationCount, 2);
    assert.equal(operationIds[0], operationIds[1]);
    assert.equal(lifecycle.getSnapshot().child?.runId, "run-queue-recovered");
  } finally {
    sessionApi.retryRun = originalRetry;
    sessionApi.resolveRunControlOperation = originalResolve;
    restoreStorage();
  }
});
