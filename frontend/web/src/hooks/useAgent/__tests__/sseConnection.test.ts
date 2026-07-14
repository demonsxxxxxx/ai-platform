import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  connectToSSE,
  getSSECloseAction,
  isTerminalSSEEvent,
  queryAuthoritativeRunStatus,
  reconnectSSE,
  type SSEConnectionContext,
} from "../sseConnection.ts";

const __dirname = dirname(fileURLToPath(import.meta.url));

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
