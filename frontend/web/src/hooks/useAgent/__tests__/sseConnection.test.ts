import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  connectToSSE,
  getSSECloseAction,
  isTerminalSSEEvent,
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

test("treats SSE close as terminal only after done or task error", () => {
  assert.equal(isTerminalSSEEvent("message:chunk"), false);
  assert.equal(isTerminalSSEEvent("done"), true);
  assert.equal(isTerminalSSEEvent("complete"), true);
  assert.equal(isTerminalSSEEvent("user:cancel"), false);
  assert.equal(isTerminalSSEEvent("error", { type: "ValueError" }), true);

  assert.equal(
    getSSECloseAction({
      receivedTerminalEvent: true,
    }),
    "terminal",
  );
});

test("does not treat transport-level SSE errors as terminal task events", () => {
  assert.equal(
    isTerminalSSEEvent("error", { error: "An internal error occurred" }),
    false,
  );
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
