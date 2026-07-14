import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  connectToSSE,
  getSSECloseAction,
  isNonRetryableSSEAuthenticationError,
  isTerminalSSEEvent,
  MAX_CONSECUTIVE_SSE_RECONNECTS,
  MAX_STATUS_QUERY_RETRIES,
  queryAuthoritativeRunStatus,
  reconnectSSE,
  type SSEConnectionContext,
  type SSEFetchEventSource,
} from "../sseConnection.ts";

const __dirname = dirname(fileURLToPath(import.meta.url));

function createTokenRefreshContext() {
  const connectionStates: string[] = [];
  const context: SSEConnectionContext = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: "assistant-old" },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    messagesRef: { current: [] },
    sessionIdRef: { current: "session-old" },
    currentRunIdRef: { current: "run-old" },
    processedEventIdsRef: { current: new Set<string>() },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 5 },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: (status) => connectionStates.push(status),
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
  };
  return { context, connectionStates };
}

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

type FetchEventSourceInit = Parameters<SSEFetchEventSource>[1];

interface AbortResolvingFetchStep {
  response?: Response;
  error?: Error;
  onStart?: (init: FetchEventSourceInit) => void;
  afterOpen?: (init: FetchEventSourceInit) => Promise<void> | void;
}

/**
 * Mirrors the ownership edge needed here: aborting a stream resolves its
 * fetch-event-source promise even while an async onopen callback is pending.
 */
function createAbortResolvingFetchEventSource(
  steps: AbortResolvingFetchStep[],
): SSEFetchEventSource {
  let callIndex = 0;
  return async (_input, init) =>
    new Promise<void>((resolve, reject) => {
      const step = steps[callIndex++];
      if (!step) {
        reject(new Error("missing fetch-event-source test step"));
        return;
      }
      let settled = false;
      const signal = init.signal;
      const cleanup = () => signal?.removeEventListener("abort", onAbort);
      const finish = () => {
        if (settled) return;
        settled = true;
        cleanup();
        resolve();
      };
      const fail = (error: unknown) => {
        if (settled) return;
        settled = true;
        cleanup();
        reject(error);
      };
      const onAbort = () => finish();

      signal?.addEventListener("abort", onAbort, { once: true });
      step.onStart?.(init);
      if (signal?.aborted) {
        finish();
        return;
      }

      void (async () => {
        try {
          if (step.error) {
            throw step.error;
          }
          if (!step.response) {
            throw new Error("missing fetch-event-source test response");
          }
          await init.onopen?.(step.response);
          await step.afterOpen?.(init);
          finish();
        } catch (error) {
          try {
            init.onerror?.(error as never);
            // A stale stream's onerror intentionally returns. A current
            // stream's onerror rethrows so the owner receives the failure.
            finish();
          } catch (ownerError) {
            fail(ownerError);
          }
        }
      })();
    });
}

test("SSE uses the same explicit cookie-session credential boundary", () => {
  const source = readFileSync(resolve(__dirname, "../sseConnection.ts"), "utf8");
  assert.match(source, /credentials:\s*"include"/);
});

test("retries an SSE close that arrives before a terminal stream event", () => {
  assert.equal(
    getSSECloseAction({
      receivedTerminalEvent: false,
    }),
    "retry",
  );
});

test("treats SSE close as terminal only after an explicit terminal state", () => {
  assert.equal(isTerminalSSEEvent("message:chunk"), false);
  assert.equal(isTerminalSSEEvent("done"), false);
  assert.equal(isTerminalSSEEvent("done", { status: "succeeded" }), true);
  assert.equal(isTerminalSSEEvent("complete"), true);
  assert.equal(isTerminalSSEEvent("user:cancel"), false);
  assert.equal(isTerminalSSEEvent("error", { type: "ValueError" }), false);

  assert.equal(
    getSSECloseAction({
      receivedTerminalEvent: true,
    }),
    "terminal",
  );
});

test("classifies only explicit server-sent terminal errors as application failures", () => {
  assert.equal(
    isTerminalSSEEvent("error", { error: "run_failed" }),
    true,
  );
  assert.equal(isTerminalSSEEvent("error", { error: "stream_timeout" }), false);
});

test("uses raw_status as the authoritative compatibility status", async () => {
  const retryRef = { current: 0 };
  const cases = [
    { wire: { status: "completed", raw_status: "succeeded" }, expected: "succeeded" },
    { wire: { status: "cancelled", raw_status: "cancelled" }, expected: "cancelled" },
    { wire: { status: "failed", raw_status: "failed" }, expected: "failed" },
    { wire: { status: "error", raw_status: "failed" }, expected: "failed" },
  ];

  for (const { wire, expected } of cases) {
    const result = await queryAuthoritativeRunStatus({
      sessionId: "session-1",
      runId: "run-1",
      isCurrent: () => true,
      statusRetryCountRef: retryRef,
      getStatus: async () => ({
        session_id: "session-1",
        run_id: "run-1",
        ...wire,
      }),
    });
    assert.deepEqual(result, {
      kind: "resolved",
      data: { session_id: "session-1", run_id: "run-1", ...wire },
      status: expected,
    });
  }

  const bareError = await queryAuthoritativeRunStatus({
    sessionId: "session-1",
    runId: "run-1",
    isCurrent: () => true,
    statusRetryCountRef: { current: 2 },
    getStatus: async () => ({
      session_id: "session-1",
      run_id: "run-1",
      status: "error",
    }),
  });
  assert.deepEqual(bareError, { kind: "unavailable" });
});

test("times out and aborts every hung authoritative status attempt before bounded convergence", async () => {
  let statusCalls = 0;
  const attemptSignals: AbortSignal[] = [];
  const guard = new Promise<never>((_resolve, reject) => {
    setTimeout(() => reject(new Error("status timeout test guard expired")), 250);
  });

  const result = await Promise.race([
    queryAuthoritativeRunStatus({
      sessionId: "session-hung-status",
      runId: "run-hung-status",
      isCurrent: () => true,
      statusRetryCountRef: { current: 0 },
      attemptTimeoutMs: 5,
      getStatus: async (_sessionId, _runId, options) => {
        statusCalls += 1;
        assert.ok(options?.signal, "each status attempt receives an abort signal");
        attemptSignals.push(options.signal);
        return new Promise((_resolve, reject) => {
          options.signal?.addEventListener(
            "abort",
            () => reject(new DOMException("aborted", "AbortError")),
            { once: true },
          );
        });
      },
    }),
    guard,
  ]);

  assert.deepEqual(result, { kind: "unavailable" });
  assert.equal(statusCalls, MAX_STATUS_QUERY_RETRIES + 1);
  assert.equal(attemptSignals.length, MAX_STATUS_QUERY_RETRIES + 1);
  assert.ok(attemptSignals.every((signal) => signal.aborted));
});

test("a stale generation releases a hung status attempt without unavailable side effects", async () => {
  let current = true;
  let statusCalls = 0;
  let capturedSignal: AbortSignal | undefined;
  const guard = new Promise<never>((_resolve, reject) => {
    setTimeout(() => reject(new Error("stale status timeout test guard expired")), 250);
  });

  const query = queryAuthoritativeRunStatus({
    sessionId: "session-stale-status",
    runId: "run-stale-status",
    isCurrent: () => current,
    statusRetryCountRef: { current: 0 },
    attemptTimeoutMs: 10,
    getStatus: async (_sessionId, _runId, options) => {
      statusCalls += 1;
      capturedSignal = options?.signal;
      return new Promise((_resolve, reject) => {
        options?.signal?.addEventListener(
          "abort",
          () => reject(new DOMException("aborted", "AbortError")),
          { once: true },
        );
      });
    },
  });
  current = false;

  assert.deepEqual(await Promise.race([query, guard]), { kind: "stale" });
  assert.equal(statusCalls, 1);
  assert.equal(capturedSignal?.aborted, true);
});

test("connectToSSE propagates a terminal transport failure to its caller", async () => {
  const connectionStates: string[] = [];
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: null },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    messagesRef: { current: [] },
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: "run-1" },
    processedEventIdsRef: { current: new Set<string>() },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: (status: string) => connectionStates.push(status),
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
  } satisfies SSEConnectionContext;

  await assert.rejects(
    connectToSSE(
      "session-1",
      "run-1",
      "message-1",
      context,
      false,
      async () => {
        throw new Error("terminal transport failure");
      },
    ),
    /terminal transport failure/,
  );
  assert.equal(context.isConnectingRef.current, false);
  assert.equal(connectionStates.at(-1), "disconnected");
});

test("does not let a stale connection target abort the active stream", async () => {
  const activeController = new AbortController();
  let fetchCalls = 0;
  const context = {
    abortControllerRef: { current: activeController },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: "active-message" },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    messagesRef: { current: [] },
    sessionIdRef: { current: "session-new" },
    currentRunIdRef: { current: "run-new" },
    processedEventIdsRef: { current: new Set<string>() },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 3 },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
  } satisfies SSEConnectionContext;

  await connectToSSE(
    "session-old",
    "run-old",
    "old-message",
    context,
    false,
    async () => {
      fetchCalls += 1;
    },
  );

  assert.equal(fetchCalls, 0);
  assert.equal(activeController.signal.aborted, false);
  assert.equal(context.streamingMessageIdRef.current, "active-message");
});

test("fails closed when reconnect cannot read the authoritative run status", async () => {
  const connectionStates: string[] = [];
  let connectCalls = 0;
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: "assistant-1" },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    messagesRef: { current: [] },
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: "run-1" },
    processedEventIdsRef: { current: new Set<string>() },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: (status: string) => connectionStates.push(status),
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    isReconnectFromHistoryRef: { current: false },
  } satisfies SSEConnectionContext & {
    isReconnectFromHistoryRef: { current: boolean };
  };

  await reconnectSSE(context, {
    getStatus: async () => {
      throw new Error("status unavailable");
    },
    connect: async () => {
      connectCalls += 1;
    },
  });

  assert.equal(connectCalls, 0);
  assert.equal(context.reconnectTimeoutRef.current, null);
  assert.equal(connectionStates.at(-1), "disconnected");
});

test("drops a reconnect when its status response belongs to an old stream generation", async () => {
  let resolveStatus:
    | ((value: { session_id: string; run_id: string; status: string }) => void)
    | undefined;
  let connectCalls = 0;
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: "assistant-old" },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    messagesRef: { current: [] },
    sessionIdRef: { current: "session-old" },
    currentRunIdRef: { current: "run-old" },
    processedEventIdsRef: { current: new Set<string>() },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 1 },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    isReconnectFromHistoryRef: { current: false },
  } satisfies SSEConnectionContext & {
    isReconnectFromHistoryRef: { current: boolean };
  };

  const reconnect = reconnectSSE(context, {
    getStatus: () =>
      new Promise<{ session_id: string; run_id: string; status: string }>((resolve) => {
        resolveStatus = resolve;
      }),
    connect: async () => {
      connectCalls += 1;
    },
  });

  context.sessionIdRef.current = "session-new";
  context.currentRunIdRef.current = "run-new";
  context.streamVersionRef.current += 1;
  resolveStatus?.({
    session_id: "session-old",
    run_id: "run-old",
    status: "running",
  });
  await reconnect;

  assert.equal(connectCalls, 0);
  assert.equal(context.reconnectTimeoutRef.current, null);
});

test("bounds status-query retries before converging to local unavailable state", async () => {
  let statusCalls = 0;
  let unavailableCalls = 0;
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: "assistant-1" },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    statusRetryCountRef: { current: 0 },
    messagesRef: { current: [] },
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: "run-1" },
    processedEventIdsRef: { current: new Set<string>() },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    isReconnectFromHistoryRef: { current: false },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    onRunStatusUnavailable: (runId: string, messageId: string) => {
      unavailableCalls += 1;
      assert.deepEqual([runId, messageId], ["run-1", "assistant-1"]);
      return true;
    },
  } satisfies SSEConnectionContext & {
    isReconnectFromHistoryRef: { current: boolean };
  };

  await reconnectSSE(context, {
    getStatus: async () => {
      statusCalls += 1;
      throw new Error("status unavailable");
    },
  });

  assert.equal(statusCalls, 3);
  assert.equal(context.statusRetryCountRef.current, 2);
  assert.equal(unavailableCalls, 1);
  assert.equal(context.reconnectTimeoutRef.current, null);
});

test("drops a status-query retry after its session generation changes", async () => {
  let unavailableCalls = 0;
  let statusCalls = 0;
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: "assistant-old" },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    statusRetryCountRef: { current: 0 },
    messagesRef: { current: [] },
    sessionIdRef: { current: "session-old" },
    currentRunIdRef: { current: "run-old" },
    processedEventIdsRef: { current: new Set<string>() },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 4 },
    isReconnectFromHistoryRef: { current: false },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    onRunStatusUnavailable: () => {
      unavailableCalls += 1;
      return true;
    },
  } satisfies SSEConnectionContext & {
    isReconnectFromHistoryRef: { current: boolean };
  };

  await reconnectSSE(context, {
    getStatus: async () => {
      statusCalls += 1;
      context.sessionIdRef.current = "session-new";
      context.currentRunIdRef.current = "run-new";
      context.streamVersionRef.current += 1;
      throw new Error("old status request failed");
    },
  });

  assert.equal(statusCalls, 1);
  assert.equal(unavailableCalls, 0);
  assert.equal(context.reconnectTimeoutRef.current, null);
});

test("ignores a mismatched explicit terminal frame without suppressing reconnect", async () => {
  let terminalCalls = 0;
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: null },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    statusRetryCountRef: { current: 0 },
    messagesRef: { current: [] },
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: "run-active" },
    processedEventIdsRef: { current: new Set<string>() },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    onRunTerminal: () => {
      terminalCalls += 1;
      return true;
    },
  } satisfies SSEConnectionContext;

  await assert.rejects(
    connectToSSE(
      "session-1",
      "run-active",
      "assistant-active",
      context,
      false,
      async (_input, init) => {
        await init.onopen?.(new Response(null, { status: 200 }));
        init.onmessage?.({
          event: "run_event",
          id: "evt-old-terminal",
          data: JSON.stringify({
            run_id: "run-old",
            event_type: "run_failed",
          }),
        } as never);
        await init.onclose?.();
      },
    ),
    /SSE closed before terminal event/,
  );

  assert.equal(terminalCalls, 0);
});

test("leaves a runless stream timeout for authoritative status reconciliation", async () => {
  let terminalCalls = 0;
  const connectionStates: string[] = [];
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: null },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    messagesRef: { current: [] },
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: "run-active" },
    processedEventIdsRef: { current: new Set<string>() },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: (status: string) => connectionStates.push(status),
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    onRunTerminal: () => {
      terminalCalls += 1;
      return true;
    },
  } satisfies SSEConnectionContext;

  await assert.rejects(
    connectToSSE(
      "session-1",
      "run-active",
      "assistant-active",
      context,
      false,
      async (_input, init) => {
        await init.onopen?.(new Response(null, { status: 200 }));
        init.onmessage?.({
          event: "error",
          id: "evt-timeout-error",
          data: JSON.stringify({ error: "stream_timeout" }),
        } as never);
        init.onmessage?.({
          event: "done",
          id: "evt-timeout-done",
          data: JSON.stringify({ status: "timeout" }),
        } as never);
        await init.onclose?.();
      },
    ),
    /SSE application interruption before terminal event/,
  );

  assert.equal(terminalCalls, 0);
  assert.ok(connectionStates.includes("reconnecting"));
});

test("drops a delayed non-terminal application error after its stream generation changes", async () => {
  const connectionStates: string[] = [];
  let releaseFetchError!: () => void;
  let errorFrameHandled!: () => void;
  const errorFrame = new Promise<void>((resolve) => {
    errorFrameHandled = resolve;
  });
  const delayedFetchError = new Promise<void>((resolve) => {
    releaseFetchError = resolve;
  });
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: null },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    messagesRef: { current: [] },
    sessionIdRef: { current: "session-old" },
    currentRunIdRef: { current: "run-old" },
    processedEventIdsRef: { current: new Set<string>() },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 4 },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: (status: string) => connectionStates.push(status),
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
  } satisfies SSEConnectionContext;

  const connection = connectToSSE(
    "session-old",
    "run-old",
    "assistant-old",
    context,
    false,
    async (_input, init) => {
      await init.onopen?.(new Response(null, { status: 200 }));
      try {
        init.onmessage?.({
          event: "error",
          id: "evt-stream-timeout",
          data: JSON.stringify({ error: "stream_timeout" }),
        } as never);
      } catch {
        errorFrameHandled();
      }
      await delayedFetchError;
      throw new Error("delayed fetch-event-source rejection");
    },
  );

  await errorFrame;
  context.sessionIdRef.current = "session-new";
  context.currentRunIdRef.current = "run-new";
  context.streamVersionRef.current += 1;
  connectionStates.length = 0;
  releaseFetchError();
  await connection;

  assert.deepEqual(connectionStates, []);
  assert.equal(context.isConnectingRef.current, false);
});

test("does not let a deferred stale 401 refresh mutate a replacement SSE stream", async () => {
  const { context, connectionStates } = createTokenRefreshContext();
  const refreshStarted = createDeferred<void>();
  const refreshed = createDeferred<string>();
  let fetchCalls = 0;
  let refreshCalls = 0;
  let oldSignal: AbortSignal | null | undefined;

  const oldConnection = connectToSSE(
    "session-old",
    "run-old",
    "assistant-old",
    context,
    false,
    createAbortResolvingFetchEventSource([
      {
        response: new Response(null, { status: 401 }),
        onStart: (init) => {
          fetchCalls += 1;
          oldSignal = init.signal;
        },
      },
    ]),
    {
      getValidAccessToken: async () => "old-access",
      getRefreshToken: () => "refresh-marker",
      refreshAccessToken: async () => {
        refreshCalls += 1;
        refreshStarted.resolve();
        return refreshed.promise;
      },
    },
  );

  await refreshStarted.promise;
  const replacementController = new AbortController();
  context.sessionIdRef.current = "session-new";
  context.currentRunIdRef.current = "run-new";
  context.streamVersionRef.current += 1;
  context.abortControllerRef.current = replacementController;
  context.isConnectingRef.current = true;
  context.streamingMessageIdRef.current = "assistant-new";
  connectionStates.length = 0;
  refreshed.resolve("new-access");
  await oldConnection;

  assert.equal(refreshCalls, 1);
  assert.equal(fetchCalls, 1);
  assert.equal(oldSignal?.aborted, false);
  assert.equal(replacementController.signal.aborted, false);
  assert.equal(context.abortControllerRef.current, replacementController);
  assert.equal(context.isConnectingRef.current, true);
  assert.equal(context.streamingMessageIdRef.current, "assistant-new");
  assert.equal(context.reconnectTimeoutRef.current, null);
  assert.deepEqual(connectionStates, []);
});

test("retries a current 401 once and aborts only its captured stream controller", async () => {
  const { context, connectionStates } = createTokenRefreshContext();
  const signals: AbortSignal[] = [];
  let fetchCalls = 0;
  let refreshCalls = 0;

  await connectToSSE(
    "session-old",
    "run-old",
    "assistant-old",
    context,
    false,
    createAbortResolvingFetchEventSource([
      {
        response: new Response(null, { status: 401 }),
        onStart: (init) => {
          fetchCalls += 1;
          if (!init.signal) {
            throw new Error("missing test stream abort signal");
          }
          signals.push(init.signal);
        },
      },
      {
        response: new Response(null, { status: 200 }),
        onStart: (init) => {
          fetchCalls += 1;
          if (!init.signal) {
            throw new Error("missing test stream abort signal");
          }
          signals.push(init.signal);
        },
        afterOpen: async (init) => {
          init.onmessage?.({
            event: "complete",
            data: JSON.stringify({ status: "succeeded" }),
          } as never);
          await init.onclose?.();
        },
      },
    ]),
    {
      getValidAccessToken: async () => "access",
      getRefreshToken: () => "refresh-marker",
      refreshAccessToken: async () => {
        refreshCalls += 1;
        return "refreshed-access";
      },
    },
  );

  assert.equal(refreshCalls, 1);
  assert.equal(fetchCalls, 2);
  assert.equal(signals[0].aborted, true);
  assert.equal(signals[1].aborted, false);
  assert.equal(context.abortControllerRef.current?.signal, signals[1]);
  assert.equal(context.isConnectingRef.current, false);
  assert.equal(connectionStates.at(-1), "disconnected");
});

test("fails closed when the refreshed SSE retry is still unauthorized", async () => {
  const { context, connectionStates } = createTokenRefreshContext();
  let fetchCalls = 0;
  let refreshCalls = 0;

  await assert.rejects(
    connectToSSE(
      "session-old",
      "run-old",
      "assistant-old",
      context,
      false,
      createAbortResolvingFetchEventSource([
        {
          response: new Response(null, { status: 401 }),
          onStart: () => {
            fetchCalls += 1;
          },
        },
        {
          response: new Response(null, { status: 401 }),
          onStart: () => {
            fetchCalls += 1;
          },
        },
      ]),
      {
        getValidAccessToken: async () => "access",
        getRefreshToken: () => "refresh-marker",
        refreshAccessToken: async () => {
          refreshCalls += 1;
          return "refreshed-access";
        },
      },
    ),
    (error: unknown) => {
      assert.equal(isNonRetryableSSEAuthenticationError(error), true);
      if (isNonRetryableSSEAuthenticationError(error)) {
        assert.equal(error.failure, "refresh_retry_exhausted");
      }
      return true;
    },
  );

  assert.equal(refreshCalls, 1);
  assert.equal(fetchCalls, 2);
  assert.equal(context.isConnectingRef.current, false);
  assert.equal(connectionStates.at(-1), "disconnected");
});

test("propagates a post-refresh transport failure through the original owner", async () => {
  const { context, connectionStates } = createTokenRefreshContext();
  let fetchCalls = 0;
  let refreshCalls = 0;

  await assert.rejects(
    connectToSSE(
      "session-old",
      "run-old",
      "assistant-old",
      context,
      false,
      createAbortResolvingFetchEventSource([
        {
          response: new Response(null, { status: 401 }),
          onStart: () => {
            fetchCalls += 1;
          },
        },
        {
          error: new Error("post-refresh transport failure"),
          onStart: () => {
            fetchCalls += 1;
          },
        },
      ]),
      {
        getValidAccessToken: async () => "access",
        getRefreshToken: () => "refresh-marker",
        refreshAccessToken: async () => {
          refreshCalls += 1;
          return "refreshed-access";
        },
      },
    ),
    /post-refresh transport failure/,
  );

  assert.equal(refreshCalls, 1);
  assert.equal(fetchCalls, 2);
  assert.equal(context.isConnectingRef.current, false);
  assert.equal(connectionStates.at(-1), "disconnected");
});

test("fails closed when a current 401 has no refresh marker or refresh fails", async () => {
  for (const scenario of [
    {
      name: "no refresh marker",
      getRefreshToken: () => null,
      refreshAccessToken: async () => "unused",
      expectedFailure: "refresh_unavailable" as const,
    },
    {
      name: "refresh failure",
      getRefreshToken: () => "refresh-marker",
      refreshAccessToken: async () => {
        throw new Error("refresh failed");
      },
      expectedFailure: "refresh_failed" as const,
    },
  ]) {
    const { context, connectionStates } = createTokenRefreshContext();

    await assert.rejects(
      connectToSSE(
        "session-old",
        "run-old",
        "assistant-old",
        context,
        false,
        async (_input, init) => {
          await init.onopen?.(new Response(null, { status: 401 }));
        },
        {
          getValidAccessToken: async () => "access",
          getRefreshToken: scenario.getRefreshToken,
          refreshAccessToken: scenario.refreshAccessToken,
        },
      ),
      (error: unknown) => {
        assert.equal(isNonRetryableSSEAuthenticationError(error), true);
        if (isNonRetryableSSEAuthenticationError(error)) {
          assert.equal(error.failure, scenario.expectedFailure);
        }
        return true;
      },
      scenario.name,
    );

    assert.equal(context.isConnectingRef.current, false);
    assert.equal(connectionStates.at(-1), "disconnected");
  }
});

test("a scheduled reconnect converges non-retryable auth without another status read", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const originalRandom = Math.random;
  Math.random = () => 0;
  let statusCalls = 0;
  let connectCalls = 0;
  let streamCalls = 0;
  let refreshCalls = 0;
  let unavailableCalls = 0;
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: "assistant-auth" },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    statusRetryCountRef: { current: 0 },
    messagesRef: {
      current: [
        {
          id: "assistant-auth",
          role: "assistant",
          content: "",
          timestamp: new Date(),
          isStreaming: true,
        },
      ],
    },
    sessionIdRef: { current: "session-auth" },
    currentRunIdRef: { current: "run-auth" },
    processedEventIdsRef: { current: new Set<string>() },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    isReconnectFromHistoryRef: { current: false },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    onRunStatusUnavailable: () => {
      unavailableCalls += 1;
      return true;
    },
  } satisfies SSEConnectionContext & {
    isReconnectFromHistoryRef: { current: boolean };
  };
  const flushAsync = async () => {
    for (let index = 0; index < 20; index += 1) {
      await Promise.resolve();
    }
  };

  try {
    await reconnectSSE(context, {
      getStatus: async () => {
        statusCalls += 1;
        return { session_id: "session-auth", run_id: "run-auth", status: "running" };
      },
      connect: async (sessionId, runId, messageId, reconnectContext) => {
        connectCalls += 1;
        await connectToSSE(
          sessionId,
          runId,
          messageId,
          reconnectContext,
          false,
          createAbortResolvingFetchEventSource([
            {
              response: new Response(null, { status: 401 }),
              onStart: () => {
                streamCalls += 1;
              },
            },
            {
              response: new Response(null, { status: 401 }),
              onStart: () => {
                streamCalls += 1;
              },
            },
          ]),
          {
            getValidAccessToken: async () => null,
            getRefreshToken: () => "refresh-marker",
            refreshAccessToken: async () => {
              refreshCalls += 1;
              return "refreshed-access";
            },
          },
        );
      },
    });

    t.mock.timers.tick(1_000);
    await flushAsync();
    assert.equal(statusCalls, 1);
    assert.equal(connectCalls, 1);
    assert.equal(streamCalls, 2);
    assert.equal(refreshCalls, 1);
    assert.equal(unavailableCalls, 1);

    t.mock.timers.tick(60_000);
    await flushAsync();
    assert.equal(statusCalls, 1);
    assert.equal(connectCalls, 1);
    assert.equal(streamCalls, 2);
    assert.equal(refreshCalls, 1);
    assert.equal(unavailableCalls, 1);
  } finally {
    Math.random = originalRandom;
  }
});

test("a scheduled reconnect reconciles a post-refresh transport failure", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const originalRandom = Math.random;
  Math.random = () => 0;
  let statusCalls = 0;
  let connectCalls = 0;
  let streamCalls = 0;
  let refreshCalls = 0;
  let terminalCalls = 0;
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: "assistant-transport" },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    statusRetryCountRef: { current: 0 },
    messagesRef: {
      current: [
        {
          id: "assistant-transport",
          role: "assistant",
          content: "",
          timestamp: new Date(),
          isStreaming: true,
        },
      ],
    },
    sessionIdRef: { current: "session-transport" },
    currentRunIdRef: { current: "run-transport" },
    processedEventIdsRef: { current: new Set<string>() },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    isReconnectFromHistoryRef: { current: false },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    onRunTerminal: () => {
      terminalCalls += 1;
      return true;
    },
  } satisfies SSEConnectionContext & {
    isReconnectFromHistoryRef: { current: boolean };
  };
  const flushAsync = async () => {
    for (let index = 0; index < 20; index += 1) {
      await Promise.resolve();
    }
  };

  try {
    await reconnectSSE(context, {
      getStatus: async () => {
        statusCalls += 1;
        return {
          session_id: "session-transport",
          run_id: "run-transport",
          status: statusCalls === 1 ? "running" : "error",
          raw_status: statusCalls === 1 ? "running" : "failed",
        };
      },
      connect: async (sessionId, runId, messageId, reconnectContext) => {
        connectCalls += 1;
        await connectToSSE(
          sessionId,
          runId,
          messageId,
          reconnectContext,
          false,
          createAbortResolvingFetchEventSource([
            {
              response: new Response(null, { status: 401 }),
              onStart: () => {
                streamCalls += 1;
              },
            },
            {
              error: new Error("scheduled post-refresh transport failure"),
              onStart: () => {
                streamCalls += 1;
              },
            },
          ]),
          {
            getValidAccessToken: async () => null,
            getRefreshToken: () => "refresh-marker",
            refreshAccessToken: async () => {
              refreshCalls += 1;
              return "refreshed-access";
            },
          },
        );
      },
    });

    t.mock.timers.tick(1_000);
    await flushAsync();
    assert.equal(statusCalls, 2);
    assert.equal(connectCalls, 1);
    assert.equal(streamCalls, 2);
    assert.equal(refreshCalls, 1);
    assert.equal(terminalCalls, 1);
    assert.equal(context.reconnectTimeoutRef.current, null);
  } finally {
    Math.random = originalRandom;
  }
});

test("bounds replayed active run_event reconnects and converges unavailable once", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let statusCalls = 0;
  let connectCalls = 0;
  let unavailableCalls = 0;
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: "assistant-1" },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    statusRetryCountRef: { current: 0 },
    messagesRef: {
      current: [
        {
          id: "assistant-1",
          role: "assistant",
          content: "",
          timestamp: new Date(),
          isStreaming: true,
        },
      ],
    },
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: "run-1" },
    processedEventIdsRef: { current: new Set(["evt-replayed-progress"]) },
    acceptedRunEventSequenceRef: {
      current: { sessionId: "session-1", runId: "run-1", sequence: 42 },
    },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    isReconnectFromHistoryRef: { current: false },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    onRunStatusUnavailable: () => {
      unavailableCalls += 1;
      return true;
    },
  } satisfies SSEConnectionContext & {
    isReconnectFromHistoryRef: { current: boolean };
  };
  const flushAsync = async () => {
    for (let index = 0; index < 20; index += 1) {
      await Promise.resolve();
    }
  };

  await reconnectSSE(context, {
    reconnectDelay: (retryCount) => 2 ** retryCount * 1000,
    getStatus: async () => {
      statusCalls += 1;
      return { session_id: "session-1", run_id: "run-1", status: "running" };
    },
    connect: async (sessionId, runId, messageId, reconnectContext) => {
      connectCalls += 1;
      await connectToSSE(
        sessionId,
        runId,
        messageId,
        reconnectContext,
        false,
        async (_input, init) => {
          await init.onopen?.(new Response(null, { status: 200 }));
          init.onmessage?.({
            event: "run_event",
            id: "evt-replayed-progress",
            data: JSON.stringify({
              run_id: "run-1",
              sequence: 42,
              event_type: "worker_started",
            }),
          } as never);
          await init.onclose?.();
        },
      );
    },
  });

  for (let attempt = 0; attempt < MAX_CONSECUTIVE_SSE_RECONNECTS; attempt += 1) {
    t.mock.timers.tick(2 ** attempt * 1000);
    await flushAsync();
  }

  assert.equal(connectCalls, MAX_CONSECUTIVE_SSE_RECONNECTS);
  assert.equal(statusCalls, MAX_CONSECUTIVE_SSE_RECONNECTS + 1);
  assert.equal(unavailableCalls, 1);
  assert.equal(context.reconnectTimeoutRef.current, null);

  t.mock.timers.tick(60_000);
  await flushAsync();
  assert.equal(connectCalls, MAX_CONSECUTIVE_SSE_RECONNECTS);
  assert.equal(statusCalls, MAX_CONSECUTIVE_SSE_RECONNECTS + 1);
  assert.equal(unavailableCalls, 1);
});

test("resets reconnect budget only after a unique current-run progress frame", async () => {
  const currentContext = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: null },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: MAX_CONSECUTIVE_SSE_RECONNECTS },
    messagesRef: { current: [] },
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: "run-1" },
    processedEventIdsRef: { current: new Set<string>() },
    acceptedRunEventSequenceRef: {
      current: { sessionId: "session-1", runId: "run-1", sequence: null },
    },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
  } satisfies SSEConnectionContext;

  await assert.rejects(
    connectToSSE(
      "session-1",
      "run-1",
      "assistant-1",
      currentContext,
      false,
      async (_input, init) => {
        await init.onopen?.(new Response(null, { status: 200 }));
        init.onmessage?.({
          event: "run_event",
          id: "evt-current-progress",
          data: JSON.stringify({
            run_id: "run-1",
            sequence: 43,
            event_type: "worker_progress",
          }),
        } as never);
        await init.onclose?.();
      },
    ),
    /SSE closed before terminal event/,
  );
  assert.equal(currentContext.retryCountRef.current, 0);
  assert.equal(currentContext.acceptedRunEventSequenceRef.current.sequence, 43);

  const nonProgressContext = {
    ...currentContext,
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    retryCountRef: { current: MAX_CONSECUTIVE_SSE_RECONNECTS },
    processedEventIdsRef: { current: new Set<string>() },
  } satisfies SSEConnectionContext;
  await assert.rejects(
    connectToSSE(
      "session-1",
      "run-1",
      "assistant-1",
      nonProgressContext,
      false,
      async (_input, init) => {
        await init.onopen?.(new Response(null, { status: 200 }));
        init.onmessage?.({ event: "ping", data: "{}" } as never);
        init.onmessage?.({
          event: "message:chunk",
          id: "evt-current-synthetic-progress",
          data: JSON.stringify({ run_id: "run-1", content: "并非权威进度" }),
        } as never);
        init.onmessage?.({
          event: "run_event",
          data: JSON.stringify({ run_id: "run-foreign", event_type: "worker_started" }),
        } as never);
        await init.onclose?.();
      },
    ),
    /SSE closed before terminal event/,
  );
  assert.equal(
    nonProgressContext.retryCountRef.current,
    MAX_CONSECUTIVE_SSE_RECONNECTS,
  );
});

test("drops a queued reconnect timer after session switch or unmount", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const originalRandom = Math.random;
  Math.random = () => 0;
  let connectCalls = 0;
  let unavailableCalls = 0;
  const context = {
    isMountedRef: { current: true as boolean },
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: "assistant-old" },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    statusRetryCountRef: { current: 0 },
    messagesRef: {
      current: [
        {
          id: "assistant-old",
          role: "assistant",
          content: "",
          timestamp: new Date(),
          isStreaming: true,
        },
      ],
    },
    sessionIdRef: { current: "session-old" },
    currentRunIdRef: { current: "run-old" },
    processedEventIdsRef: { current: new Set<string>() },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 2 },
    isReconnectFromHistoryRef: { current: false },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    onRunStatusUnavailable: () => {
      unavailableCalls += 1;
      return true;
    },
  } satisfies SSEConnectionContext & {
    isReconnectFromHistoryRef: { current: boolean };
  };

  try {
    await reconnectSSE(context, {
      getStatus: async () => ({
        session_id: "session-old",
        run_id: "run-old",
        status: "running",
      }),
      connect: async () => {
        connectCalls += 1;
      },
    });
    context.sessionIdRef.current = "session-new";
    context.currentRunIdRef.current = "run-new";
    context.streamVersionRef.current += 1;
    context.isMountedRef.current = false;
    t.mock.timers.tick(1_000);
    await Promise.resolve();

    assert.equal(connectCalls, 0);
    assert.equal(unavailableCalls, 0);
  } finally {
    Math.random = originalRandom;
  }
});
