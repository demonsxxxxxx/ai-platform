import assert from "node:assert/strict";
import { fetchEventSource } from "@microsoft/fetch-event-source";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  connectToSSE,
  getSSECloseAction,
  isNonRetryableSSEAuthenticationError,
  isSSEHeartbeatComment,
  isTerminalSSEEvent,
  MAX_CONSECUTIVE_SSE_RECONNECTS,
  MAX_SSE_STARTUP_RETRIES,
  MAX_STATUS_QUERY_RETRIES,
  queryAuthoritativeRunStatus,
  reconnectSSE,
  recoverReplayGap,
  type SSEConnectionContext,
  type SSEFetchEventSource,
} from "../sseConnection.ts";
import { PublicStreamPresentation } from "../publicStreamPresentation.ts";
import { PUBLIC_RUN_STREAM_SCHEMA } from "../../../generated/publicRunStreamV4.ts";
import type { Message } from "../../../types";

const __dirname = dirname(fileURLToPath(import.meta.url));

test("actual SSE parser ignores colon-space heartbeat comments without state mutation", async () => {
  const events: unknown[] = [];
  let reducerMutations = 0;
  let assistantTextMutations = 0;
  let terminalMutations = 0;
  let warningMutations = 0;
  let cursor: string | null = null;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(": heartbeat\n\n"));
      controller.close();
    },
  });

  const previousDocument = (globalThis as typeof globalThis & {
    document?: unknown;
  }).document;
  const previousWindow = (globalThis as typeof globalThis & {
    window?: unknown;
  }).window;
  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: { addEventListener() {}, removeEventListener() {} },
  });
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      addEventListener() {},
      removeEventListener() {},
      setTimeout,
      clearTimeout,
    },
  });
  try {
    await fetchEventSource("https://example.test/stream", {
      fetch: async () =>
        new Response(body, {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        }),
      openWhenHidden: true,
      onopen: async (response) => {
        assert.equal(response.status, 200);
      },
      onmessage: (event) => {
        if (isSSEHeartbeatComment(event)) return;
        events.push(event);
        reducerMutations += 1;
        assistantTextMutations += 1;
        terminalMutations += 1;
        warningMutations += 1;
        cursor = event.id || null;
      },
      onclose: () => undefined,
    });
  } finally {
    if (previousDocument === undefined) {
      Reflect.deleteProperty(globalThis, "document");
    } else {
      Object.defineProperty(globalThis, "document", {
        configurable: true,
        value: previousDocument,
      });
    }
    if (previousWindow === undefined) {
      Reflect.deleteProperty(globalThis, "window");
    } else {
      Object.defineProperty(globalThis, "window", {
        configurable: true,
        value: previousWindow,
      });
    }
  }

  assert.deepEqual(events, []);
  assert.equal(reducerMutations, 0);
  assert.equal(assistantTextMutations, 0);
  assert.equal(terminalMutations, 0);
  assert.equal(warningMutations, 0);
  assert.equal(cursor, null);
});

function v3Frame({
  cursor,
  runId,
  eventType,
  payload,
  eventId = cursor,
  streamIncarnation = 1,
}: {
  cursor: string;
  runId: string;
  eventType: string;
  payload: Record<string, unknown>;
  eventId?: string;
  streamIncarnation?: number;
}) {
  return {
    id: cursor,
    event: eventType,
    data: JSON.stringify({
      schema: PUBLIC_RUN_STREAM_SCHEMA,
      event_id: eventId,
      run_id: runId,
      stream_incarnation: streamIncarnation,
      emitted_at: "2026-08-09T00:00:00Z",
      event_type: eventType,
      payload,
    }),
  };
}

function v4Frame({
  cursor,
  runId,
  eventType,
  payload,
  eventId = cursor,
  seq = 1,
  messageId,
}: {
  cursor: string;
  runId: string;
  eventType: string;
  payload: Record<string, unknown>;
  eventId?: string;
  seq?: number;
  messageId?: string | null;
}) {
  const isControl = eventType.startsWith("stream.");
  return {
    id: cursor,
    event: eventType,
    data: JSON.stringify({
      schema: isControl
        ? "ai-platform.public-run-stream-control.v4"
        : "ai-platform.public-run-stream-event.v4",
      event_id: eventId,
      run_id: runId,
      message_id: isControl
        ? null
        : messageId === undefined && eventType.startsWith("message.")
          ? "message-1"
          : (messageId ?? null),
      seq: isControl ? null : seq,
      event_type: eventType,
      stream_incarnation: 1,
      replayable:
        eventType !== "stream.heartbeat" && eventType !== "stream.gap",
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-08-09T00:00:00Z",
      payload,
    }),
  };
}

test("flushes accepted public text before reconnect status can replay-deduplicate it", async () => {
  let messages: Message[] = [
    {
      id: "assistant-flush",
      role: "assistant" as const,
      content: "A",
      timestamp: new Date(),
      isStreaming: true,
      parts: [{ type: "text" as const, content: "A" }],
    },
  ];
  const presentation = new PublicStreamPresentation({
    now: () => 0,
    requestAnimationFrame: () => 1,
    cancelAnimationFrame: () => undefined,
    setTimeout: () => 1 as unknown as ReturnType<typeof setTimeout>,
    clearTimeout: () => undefined,
  });
  const owner = {
    sessionId: "session-flush",
    runId: "run-flush",
    assistantMessageId: "assistant-flush",
    streamVersion: 4,
  };
  presentation.activate(owner);
  presentation.enqueueAssistantDelta(owner, "B", (content) => {
    messages = messages.map((message) =>
      message.id === owner.assistantMessageId
        ? {
            ...message,
            content: message.content + content,
            parts: [{ type: "text" as const, content: message.content + content }],
          }
        : message,
    );
  });
  let contentObservedByStatus = "";
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: owner.assistantMessageId },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    statusRetryCountRef: { current: 0 },
    messagesRef: { current: messages },
    sessionIdRef: { current: owner.sessionId },
    currentRunIdRef: { current: owner.runId },
    processedEventIdsRef: { current: new Set<string>() },
    acceptedRunEventSequenceRef: {
      current: { sessionId: owner.sessionId, runId: owner.runId, sequence: 8 },
    },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: owner.streamVersion },
    isReconnectFromHistoryRef: { current: false },
    publicStreamPresentation: presentation,
    setSessionId: () => undefined,
    setMessages: (updater) => {
      messages = typeof updater === "function" ? updater(messages) : updater;
      context.messagesRef.current = messages;
    },
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    onRunTerminal: () => true,
  } satisfies SSEConnectionContext & {
    isReconnectFromHistoryRef: { current: boolean };
  };

  await reconnectSSE(context, {
    getStatus: async () => {
      contentObservedByStatus = messages[0]?.content || "";
      return {
        session_id: owner.sessionId,
        run_id: owner.runId,
        status: "completed",
      };
    },
  });

  assert.equal(contentObservedByStatus, "AB");
  assert.equal(messages[0]?.content, "AB");
});

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
    acceptedRunEventSequenceRef: {
      current: { sessionId: null, runId: null, sequence: null },
    },
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

test("retries startup-not-ready inside one SSE owner and persists the accepted cursor", async () => {
  const { context } = createTokenRefreshContext();
  context.messagesRef.current = [
    {
      id: "assistant-old",
      role: "assistant",
      content: "",
      timestamp: new Date(),
      isStreaming: true,
      parts: [],
    },
  ];
  context.setMessages = (updater) => {
    context.messagesRef.current =
      typeof updater === "function"
        ? updater(context.messagesRef.current)
        : updater;
  };
  context.acceptedStreamCursorRef = {
    current: { sessionId: null, runId: null, eventId: null },
  };
  context.v4MessageOwnerRef = {
    current: {
      sessionId: "session-old",
      runId: "run-old",
      streamVersion: 5,
      streamIncarnation: 1,
      protocolMessageId: "message-1",
      reducerMessageId: "assistant-old",
    },
  };
  context.onRunTerminal = () => true;
  const ownerHeaders: Array<Record<string, string>> = [];
  let startupAttempts = 0;
  const fetchStream: SSEFetchEventSource = async (_input, init) => {
    ownerHeaders.push((init.headers || {}) as Record<string, string>);
    if (ownerHeaders.length === 2) return;
    try {
      startupAttempts += 1;
      await init.onopen?.(
        new Response(null, {
          status: 409,
          headers: {
            "X-SSE-Error-Code": "sse_stream_not_admitted",
            "X-SSE-Retryable": "true",
          },
        }),
      );
    } catch (error) {
      assert.equal(typeof init.onerror?.(error as never), "number");
    }
    startupAttempts += 1;
    await init.onopen?.(new Response(null, { status: 200 }));
    init.onmessage?.(
      v4Frame({
        cursor: "run-old:1:1-0",
        runId: "run-old",
        eventType: "message.delta",
        eventId: "delta-1",
        payload: { delta: "accepted" },
      }) as never,
    );
    init.onmessage?.(
      v4Frame({
        cursor: "run-old:1:2-0",
        runId: "run-old",
        eventType: "run.succeeded",
        eventId: "terminal-1",
        payload: {
          terminal_event_id: "terminal-1",
          hydrate_required: true,
        },
        seq: 2,
      }) as never,
    );
    init.onclose?.();
  };
  const tokens = {
    getValidAccessToken: async () => null,
    getRefreshToken: () => null,
  };

  await connectToSSE(
    "session-old",
    "run-old",
    "assistant-old",
    context,
    false,
    fetchStream,
    tokens,
  );
  await connectToSSE(
    "session-old",
    "run-old",
    "assistant-old",
    context,
    false,
    fetchStream,
    tokens,
  );

  assert.equal(startupAttempts, 2);
  assert.equal(ownerHeaders.length, 2);
  assert.equal(ownerHeaders[0]?.["Last-Event-ID"], undefined);
  assert.equal(context.messagesRef.current[0]?.content, "accepted");
  assert.equal(ownerHeaders[1]?.["Last-Event-ID"], "run-old:1:2-0");
});

test("does not retry a hard startup conflict", async () => {
  const { context } = createTokenRefreshContext();
  let attempts = 0;
  const fetchStream: SSEFetchEventSource = async (_input, init) => {
    attempts += 1;
    try {
      await init.onopen?.(
        new Response(null, {
          status: 409,
          headers: {
            "X-SSE-Error-Code": "sse_authority_revoked",
            "X-SSE-Retryable": "false",
          },
        }),
      );
    } catch (error) {
      init.onerror?.(error as never);
    }
  };

  await assert.rejects(
    connectToSSE(
      "session-old",
      "run-old",
      "assistant-old",
      context,
      false,
      fetchStream,
    ),
    /sse_authority_revoked/,
  );
  assert.equal(attempts, 1);
});

test("bounds startup-not-ready retries inside one SSE owner", async () => {
  const { context } = createTokenRefreshContext();
  let attempts = 0;
  let nowMs = 0;
  const scheduledDiagnostics: Array<Record<string, unknown>> = [];
  const exhaustedDiagnostics: Array<Record<string, unknown>> = [];
  const originalInfo = console.info;
  const originalWarn = console.warn;
  console.info = (message: unknown, details?: unknown) => {
    if (message === "[SSE] Startup retry scheduled") {
      scheduledDiagnostics.push(details as Record<string, unknown>);
    }
  };
  console.warn = (message: unknown, details?: unknown) => {
    if (message === "[SSE] Startup retry exhausted") {
      exhaustedDiagnostics.push(details as Record<string, unknown>);
    }
  };
  const fetchStream: SSEFetchEventSource = async (_input, init) => {
    while (true) {
      attempts += 1;
      try {
        await init.onopen?.(
          new Response(null, {
            status: 409,
            headers: {
              "X-SSE-Error-Code": "sse_stream_not_confirmed",
              "X-SSE-Retryable": "true",
            },
          }),
        );
      } catch (error) {
        init.onerror?.(error as never);
      }
    }
  };

  try {
    await assert.rejects(
      connectToSSE(
        "session-old",
        "run-old",
        "assistant-old",
        context,
        false,
        fetchStream,
        { now: () => (nowMs += 100) },
      ),
      /sse_startup_retry_exhausted/,
    );
  } finally {
    console.info = originalInfo;
    console.warn = originalWarn;
  }

  assert.equal(attempts, MAX_SSE_STARTUP_RETRIES + 1);
  assert.equal(scheduledDiagnostics.length, MAX_SSE_STARTUP_RETRIES);
  assert.equal(scheduledDiagnostics[0]?.outcome, "scheduled");
  assert.equal(typeof scheduledDiagnostics[0]?.elapsedMs, "number");
  assert.equal(exhaustedDiagnostics.length, 1);
  assert.equal(exhaustedDiagnostics[0]?.outcome, "exhausted");
  assert.equal(typeof exhaustedDiagnostics[0]?.elapsedMs, "number");
});

test("aborts a startup request that remains pending past the owner deadline", async () => {
  const { context } = createTokenRefreshContext();
  let fireDeadline: (() => void) | undefined;
  let requestAborted = false;
  const fetchStream: SSEFetchEventSource = async (_input, init) =>
    await new Promise<void>((resolve) => {
      init.signal?.addEventListener(
        "abort",
        () => {
          requestAborted = true;
          resolve();
        },
        { once: true },
      );
    });
  const setStartupTimeout = ((handler: () => void) => {
    fireDeadline = handler;
    return 1;
  }) as unknown as typeof globalThis.setTimeout;
  const clearStartupTimeout = (() => undefined) as typeof globalThis.clearTimeout;

  const connection = connectToSSE(
    "session-old",
    "run-old",
    "assistant-old",
    context,
    false,
    fetchStream,
    {
      now: () => 10_000,
      setStartupTimeout,
      clearStartupTimeout,
    },
  );
  await Promise.resolve();
  assert.ok(fireDeadline);
  fireDeadline();

  await assert.rejects(connection, /sse_startup_retry_exhausted/);
  assert.equal(requestAborted, true);
  assert.equal(context.isConnectingRef.current, false);
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

test("keeps a silent running heartbeat attached without projecting assistant content or resetting retries", async () => {
  const messages = [
    {
      id: "assistant-heartbeat",
      role: "assistant" as const,
      content: "",
      parts: [],
      isStreaming: true,
      timestamp: new Date(),
    },
  ];
  const connectionStates: string[] = [];
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: null },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 2 },
    messagesRef: { current: messages },
    sessionIdRef: { current: "session-heartbeat" },
    currentRunIdRef: { current: "run-heartbeat" },
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

  await connectToSSE(
    "session-heartbeat",
    "run-heartbeat",
    "assistant-heartbeat",
    context,
    false,
    async (_input, init) => {
      await init.onopen?.(new Response(null, { status: 200 }));
      assert.equal(context.retryCountRef.current, 2);
      assert.equal(messages[0]?.content, "");
      assert.deepEqual(messages[0]?.parts, []);
      context.sessionIdRef.current = "session-replaced";
      context.currentRunIdRef.current = "run-replaced";
    },
  );

  assert.ok(connectionStates.includes("connected"));
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

test("resolves an idle session only for runless history reconciliation", async () => {
  let statusCalls = 0;
  const retryRef = { current: MAX_STATUS_QUERY_RETRIES };
  const data = {
    session_id: "session-idle",
    status: "idle",
    raw_status: "idle",
  };

  const result = await queryAuthoritativeRunStatus({
    sessionId: "session-idle",
    runId: "stale-history-candidate",
    isCurrent: () => true,
    statusRetryCountRef: retryRef,
    allowIdle: true,
    getStatus: async () => {
      statusCalls += 1;
      return data;
    },
  });

  assert.deepEqual(result, { kind: "resolved", data, status: "idle" });
  assert.equal(statusCalls, 1);
  assert.equal(retryRef.current, 0);

  const reconnectResult = await queryAuthoritativeRunStatus({
    sessionId: "session-idle",
    runId: "stale-history-candidate",
    isCurrent: () => true,
    statusRetryCountRef: { current: MAX_STATUS_QUERY_RETRIES },
    getStatus: async () => data,
  });
  assert.deepEqual(reconnectResult, { kind: "unavailable" });
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

test("rejects a foreign v3 frame without accepting terminal state", async () => {
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
        init.onmessage?.(
          v3Frame({
            cursor: "run-old:1:1-0",
            runId: "run-old",
            eventType: "terminal",
            eventId: "old-terminal",
            payload: {
              event_id: "old-terminal",
              hydrate_required: true,
              status: "failed",
            },
          }) as never,
        );
        await init.onclose?.();
      },
    ),
    /sse_event_contract_invalid/,
  );

  assert.equal(terminalCalls, 0);
});

test("leaves a stream close without terminal for authoritative status reconciliation", async () => {
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
        await init.onclose?.();
      },
    ),
    /SSE closed before terminal event/,
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
          event: "reasoning.delta",
          id: "run-old:1:1-0",
          data: "{}",
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
  assert.equal(context.isConnectingRef.current, true);
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
  context.onRunTerminal = () => true;
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
          init.onmessage?.(
            v4Frame({
              cursor: "run-old:1:2-0",
              runId: "run-old",
              eventType: "run.succeeded",
              eventId: "terminal-after-refresh",
              payload: {
                terminal_event_id: "terminal-after-refresh",
                hydrate_required: true,
              },
              seq: 2,
            }) as never,
          );
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

test("flushes a paused accepted answer delta exactly once before a 401 refresh handoff", async () => {
  let messages: Message[] = [
    {
      id: "assistant-refresh-flush",
      role: "assistant",
      content: "A",
      timestamp: new Date(),
      parts: [{ type: "text", content: "A" }],
      isStreaming: true,
    },
  ];
  let pendingFrame: FrameRequestCallback | null = null;
  let commitCount = 0;
  const owner = {
    sessionId: "session-refresh-flush",
    runId: "run-refresh-flush",
    assistantMessageId: "assistant-refresh-flush",
    streamVersion: 6,
  };
  const presentation = new PublicStreamPresentation({
    now: () => 0,
    requestAnimationFrame: (callback) => {
      pendingFrame = callback;
      return 1;
    },
    cancelAnimationFrame: () => {
      pendingFrame = null;
    },
    setTimeout: () => 1 as unknown as ReturnType<typeof setTimeout>,
    clearTimeout: () => undefined,
  });
  presentation.activate(owner);
  assert.equal(
    presentation.enqueueAssistantDelta(owner, "B", (content) => {
      commitCount += 1;
      messages = messages.map((message) =>
        message.id === owner.assistantMessageId
          ? {
              ...message,
              content: message.content + content,
              parts: [{ type: "text", content: message.content + content }],
            }
          : message,
      );
    }),
    true,
  );
  assert.notEqual(pendingFrame, null);

  let contentObservedByRefreshedAttempt = "";
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: owner.assistantMessageId },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    messagesRef: { current: messages },
    sessionIdRef: { current: owner.sessionId },
    currentRunIdRef: { current: owner.runId },
    processedEventIdsRef: { current: new Set<string>() },
    acceptedRunEventSequenceRef: {
      current: { sessionId: owner.sessionId, runId: owner.runId, sequence: 8 },
    },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: owner.streamVersion },
    publicStreamPresentation: presentation,
    setSessionId: () => undefined,
    setMessages: (updater: React.SetStateAction<Message[]>) => {
      messages = typeof updater === "function" ? updater(messages) : updater;
      context.messagesRef.current = messages;
    },
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    onRunTerminal: () => true,
  } satisfies SSEConnectionContext;

  await connectToSSE(
    owner.sessionId,
    owner.runId,
    owner.assistantMessageId,
    context,
    false,
    createAbortResolvingFetchEventSource([
      { response: new Response(null, { status: 401 }) },
      {
        response: new Response(null, { status: 200 }),
        onStart: () => {
          contentObservedByRefreshedAttempt = messages[0]?.content || "";
        },
        afterOpen: async (init) => {
          init.onmessage?.(
            v4Frame({
              cursor: "run-refresh-flush:1:2-0",
              runId: owner.runId,
              eventType: "run.succeeded",
              eventId: "terminal-refresh-flush",
              payload: {
                terminal_event_id: "terminal-refresh-flush",
                hydrate_required: true,
              },
              seq: 9,
            }) as never,
          );
          await init.onclose?.();
        },
      },
    ]),
    {
      getValidAccessToken: async () => "access",
      getRefreshToken: () => "refresh-marker",
      refreshAccessToken: async () => "refreshed-access",
    },
  );

  assert.equal(contentObservedByRefreshedAttempt, "AB");
  assert.equal(messages[0]?.content, "AB");
  assert.equal(commitCount, 1);
  assert.equal(pendingFrame, null);
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

test("production cookie-session SSE 401 never probes auth or opens a refreshed stream", async () => {
  const originalFetch = Object.getOwnPropertyDescriptor(globalThis, "fetch");
  const originalLocalStorage = Object.getOwnPropertyDescriptor(
    globalThis,
    "localStorage",
  );
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  let authProbeCalls = 0;
  let streamCalls = 0;
  const markerStore = new Map([
    ["ai_platform_session_present", "session-marker"],
  ]);
  Object.defineProperty(globalThis, "fetch", {
    configurable: true,
    value: async () => {
      authProbeCalls += 1;
      return new Response(JSON.stringify({ user_id: "stale-user" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => markerStore.get(key) ?? null,
      setItem: (key: string, value: string) => markerStore.set(key, value),
      removeItem: (key: string) => markerStore.delete(key),
    },
  });
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { dispatchEvent: () => true },
  });
  const { context } = createTokenRefreshContext();

  try {
    await assert.rejects(
      connectToSSE(
        "session-old",
        "run-old",
        "assistant-old",
        context,
        false,
        async (_input, init) => {
          streamCalls += 1;
          await init.onopen?.(new Response(null, { status: 401 }));
        },
      ),
      (error: unknown) => {
        assert.equal(isNonRetryableSSEAuthenticationError(error), true);
        return true;
      },
    );

    assert.equal(streamCalls, 1);
    assert.equal(authProbeCalls, 0);
    assert.equal(
      markerStore.get("ai_platform_session_present"),
      "session-marker",
    );
  } finally {
    if (originalFetch) Object.defineProperty(globalThis, "fetch", originalFetch);
    else delete (globalThis as { fetch?: typeof fetch }).fetch;
    if (originalLocalStorage) {
      Object.defineProperty(globalThis, "localStorage", originalLocalStorage);
    } else delete (globalThis as { localStorage?: Storage }).localStorage;
    if (originalWindow) Object.defineProperty(globalThis, "window", originalWindow);
    else delete (globalThis as { window?: Window }).window;
  }
});

test("SSE failures log only fixed phases and bounded safe codes", async () => {
  const originalError = console.error;
  const originalWarn = console.warn;
  const logs: unknown[][] = [];
  console.error = (...args: unknown[]) => logs.push(args);
  console.warn = (...args: unknown[]) => logs.push(args);
  const statusRetryCountRef = { current: MAX_STATUS_QUERY_RETRIES };
  const diagnostic = new Error(
    "C:\\private\\status.log?token=secret <html>proxy</html>",
  );
  const codedDiagnostic = Object.assign(diagnostic, {
    code: "safe_status_unavailable",
  });

  try {
    const result = await queryAuthoritativeRunStatus({
      sessionId: "session-safe-log",
      runId: "run-safe-log",
      isCurrent: () => true,
      statusRetryCountRef,
      getStatus: async () => {
        throw codedDiagnostic;
      },
    });

    assert.equal(result.kind, "unavailable");
    assert.ok(logs.length > 0);
    for (const entry of logs) {
      assert.equal(entry.length, 1);
      assert.equal(typeof entry[0], "string");
      assert.doesNotMatch(
        String(entry[0]),
        /private|token|proxy|html|status\.log/i,
      );
    }
    assert.match(logs.map(String).join(" "), /safe_status_unavailable/);
  } finally {
    console.error = originalError;
    console.warn = originalWarn;
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

test("bounds heartbeat-then-close reconnect loops without assistant text or new work", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let statusCalls = 0;
  let connectCalls = 0;
  let unavailableCalls = 0;
  const messages = [
    {
      id: "assistant-heartbeat-loop",
      role: "assistant" as const,
      content: "",
      timestamp: new Date(),
      isStreaming: true,
      parts: [],
    },
  ];
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: "assistant-heartbeat-loop" },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    statusRetryCountRef: { current: 0 },
    messagesRef: { current: messages },
    sessionIdRef: { current: "session-heartbeat-loop" },
    currentRunIdRef: { current: "run-heartbeat-loop" },
    processedEventIdsRef: { current: new Set<string>() },
    acceptedRunEventSequenceRef: {
      current: {
        sessionId: "session-heartbeat-loop",
        runId: "run-heartbeat-loop",
        sequence: null,
      },
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
    reconnectDelay: () => 1_000,
    getStatus: async () => {
      statusCalls += 1;
      return {
        session_id: "session-heartbeat-loop",
        run_id: "run-heartbeat-loop",
        status: "running",
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
        async (_input, init) => {
          await init.onopen?.(new Response(null, { status: 200 }));
          init.onmessage?.({
            event: "heartbeat",
            id: `run-heartbeat-loop:heartbeat:${connectCalls}`,
            data: JSON.stringify({
              run_id: "run-heartbeat-loop",
              status: "running",
            }),
          } as never);
          await init.onclose?.();
        },
      );
    },
  });

  for (let attempt = 0; attempt < MAX_CONSECUTIVE_SSE_RECONNECTS; attempt += 1) {
    t.mock.timers.tick(1_000);
    await flushAsync();
  }

  assert.equal(connectCalls, MAX_CONSECUTIVE_SSE_RECONNECTS);
  assert.equal(statusCalls, MAX_CONSECUTIVE_SSE_RECONNECTS + 1);
  assert.equal(unavailableCalls, 1);
  assert.equal(context.retryCountRef.current, MAX_CONSECUTIVE_SSE_RECONNECTS);
  assert.equal(context.reconnectTimeoutRef.current, null);
  assert.deepEqual(messages, [
    {
      id: "assistant-heartbeat-loop",
      role: "assistant",
      content: "",
      timestamp: messages[0]?.timestamp,
      isStreaming: true,
      parts: [],
    },
  ]);

  t.mock.timers.tick(60_000);
  await flushAsync();
  assert.equal(connectCalls, MAX_CONSECUTIVE_SSE_RECONNECTS);
  assert.equal(statusCalls, MAX_CONSECUTIVE_SSE_RECONNECTS + 1);
  assert.equal(unavailableCalls, 1);
});

test("resets reconnect budget only after a unique current-run progress frame", async () => {
  const currentMessagesRef: { current: Message[] } = {
    current: [
      {
        id: "assistant-1",
        role: "assistant" as const,
        content: "",
        timestamp: new Date(),
        parts: [],
        isStreaming: true,
      },
    ],
  };
  const currentContext = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: null },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: MAX_CONSECUTIVE_SSE_RECONNECTS },
    messagesRef: currentMessagesRef,
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
    setMessages: (updater: React.SetStateAction<Message[]>) => {
      currentMessagesRef.current =
        typeof updater === "function"
          ? updater(currentMessagesRef.current)
          : updater;
    },
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
        init.onmessage?.(
          v4Frame({
            cursor: "run-1:1:43-0",
            runId: "run-1",
            eventType: "run.cancel_requested",
            eventId: "current-progress",
            payload: { source: "user" },
            seq: 43,
          }) as never,
        );
        await init.onclose?.();
      },
    ),
    /SSE closed before terminal event/,
  );
  assert.equal(currentContext.retryCountRef.current, 0);
  assert.equal(currentContext.acceptedRunEventSequenceRef.current.sequence, 43);

  const deltaProgressContext = {
    ...currentContext,
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    retryCountRef: { current: MAX_CONSECUTIVE_SSE_RECONNECTS },
    processedEventIdsRef: { current: new Set<string>() },
    v4MessageOwnerRef: {
      current: {
        sessionId: "session-1",
        runId: "run-1",
        streamVersion: 0,
        streamIncarnation: 1,
        protocolMessageId: "message-1",
        reducerMessageId: "assistant-1",
      },
    },
  } satisfies SSEConnectionContext;
  await assert.rejects(
    connectToSSE(
      "session-1",
      "run-1",
      "assistant-1",
      deltaProgressContext,
      false,
      async (_input, init) => {
        await init.onopen?.(new Response(null, { status: 200 }));
        init.onmessage?.(
          v4Frame({
            cursor: "run-1:1:44-0",
            runId: "run-1",
            eventType: "message.delta",
            eventId: "current-delta",
            payload: { delta: "新进度" },
            seq: 44,
          }) as never,
        );
        await init.onclose?.();
      },
    ),
    /SSE closed before terminal event/,
  );
  assert.equal(deltaProgressContext.retryCountRef.current, 0);
  assert.equal(
    deltaProgressContext.acceptedRunEventSequenceRef.current.sequence,
    44,
  );

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
        init.onmessage?.(
          v4Frame({
            cursor: "run-1:1:45-0",
            runId: "run-1",
            eventType: "stream.open",
            eventId: "stream-open-current",
            payload: {
              design_id: "ai-platform.redis-streams-sse-event-channel.v4",
            },
          }) as never,
        );
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

test("holds duplicate terminal transport and immediate stream.end behind hydration acceptance", async () => {
  let acceptTerminal: (() => void) | undefined;
  let terminalCalls = 0;
  const connectionStates: string[] = [];
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: "assistant-1" },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    messagesRef: { current: [] as Message[] },
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: "run-1" },
    processedEventIdsRef: { current: new Set<string>() },
    acceptedRunEventSequenceRef: {
      current: { sessionId: "session-1", runId: "run-1", sequence: null },
    },
    acceptedStreamCursorRef: {
      current: { sessionId: null, runId: null, eventId: null, streamIncarnation: null },
    },
    v4TerminalEventIdsRef: { current: new Set<string>() },
    v4TerminalFenceRef: { current: null },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: (status: string) => connectionStates.push(status),
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    onRunTerminal: (_runId, _status, _messageId, onAccepted) => {
      terminalCalls += 1;
      acceptTerminal = onAccepted;
      return true;
    },
  } satisfies SSEConnectionContext;

  await connectToSSE(
    "session-1",
    "run-1",
    "assistant-1",
    context,
    false,
    async (_input, init) => {
      await init.onopen?.(new Response(null, { status: 200 }));
      init.onmessage?.(
        v4Frame({
          cursor: "run-1:1:1-0",
          runId: "run-1",
          eventType: "run.succeeded",
          eventId: "terminal-event-1",
          payload: { terminal_event_id: "terminal-1", hydrate_required: true },
        }) as never,
      );
      init.onmessage?.(
        v4Frame({
          cursor: "run-1:1:2-0",
          runId: "run-1",
          eventType: "run.succeeded",
          eventId: "terminal-event-1",
          payload: { terminal_event_id: "terminal-1", hydrate_required: true },
        }) as never,
      );
      init.onmessage?.(
        v4Frame({
          cursor: "run-1:1:3-0",
          runId: "run-1",
          eventType: "stream.end",
          eventId: "stream-end-1",
          payload: { terminal_event_id: "terminal-1" },
        }) as never,
      );
      await init.onclose?.();
    },
  );

  assert.equal(terminalCalls, 1);
  assert.equal(context.acceptedStreamCursorRef.current.eventId, null);
  assert.deepEqual(connectionStates, ["connecting", "connected"]);
  acceptTerminal?.();
  await Promise.resolve();
  assert.equal(context.acceptedStreamCursorRef.current.eventId, "run-1:1:3-0");
  assert.equal(connectionStates.at(-1), "disconnected");
});

test("duplicate semantic Redis entry advances only the transport cursor", async () => {
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: null },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: MAX_CONSECUTIVE_SSE_RECONNECTS },
    messagesRef: { current: [] },
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: "run-1" },
    processedEventIdsRef: { current: new Set(["semantic-progress-1"]) },
    acceptedRunEventSequenceRef: {
      current: { sessionId: "session-1", runId: "run-1", sequence: 8 },
    },
    acceptedStreamCursorRef: {
      current: { sessionId: "session-1", runId: "run-1", eventId: "run-1:1:1-0", streamIncarnation: 1 },
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
      context,
      false,
      async (_input, init) => {
        await init.onopen?.(new Response(null, { status: 200 }));
        init.onmessage?.(
          v4Frame({
            cursor: "run-1:1:2-0",
            runId: "run-1",
            eventType: "run.cancel_requested",
            eventId: "semantic-progress-1",
            payload: { source: "user" },
            seq: 9,
          }) as never,
        );
        await init.onclose?.();
      },
    ),
    /SSE closed before terminal event/,
  );

  assert.equal(context.acceptedStreamCursorRef.current.eventId, "run-1:1:2-0");
  assert.equal(context.acceptedRunEventSequenceRef.current.sequence, 8);
  assert.equal(context.retryCountRef.current, MAX_CONSECUTIVE_SSE_RECONNECTS);
});

test("replay gap preserves partial output until an active run reaches terminal hydration", async () => {
  let statusCalls = 0;
  let hydrateCalls = 0;
  let capturedInit: Parameters<SSEFetchEventSource>[1] | undefined;
  const connectionStates: string[] = [];
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: "assistant-1" },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    statusRetryCountRef: { current: 0 },
    replayGapRecoveryRef: { current: null },
    messagesRef: {
      current: [
        {
          id: "assistant-1",
          role: "assistant" as const,
          content: "partial",
          timestamp: new Date(),
          isStreaming: true,
        },
      ] as Message[],
    },
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: "run-1" },
    processedEventIdsRef: { current: new Set<string>() },
    acceptedRunEventSequenceRef: {
      current: { sessionId: "session-1", runId: "run-1", sequence: 8 },
    },
    acceptedStreamCursorRef: {
      current: { sessionId: "session-1", runId: "run-1", eventId: "run-1:1:1-0", streamIncarnation: 1 },
    },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    setSessionId: () => undefined,
    setMessages: (update: Message[] | ((current: Message[]) => Message[])) => {
      context.messagesRef.current =
        typeof update === "function" ? update(context.messagesRef.current) : update;
    },
    setConnectionStatus: (status: string) => connectionStates.push(status),
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    hydrateTerminalRun: async () => {
      hydrateCalls += 1;
    },
  } satisfies SSEConnectionContext;

  await connectToSSE(
    "session-1",
    "run-1",
    "assistant-1",
    context,
    false,
    async (_input, init) => {
      capturedInit = init;
      await init.onopen?.(new Response(null, { status: 200 }));
      init.onmessage?.(
        v4Frame({
          cursor: "run-1:1:2-0",
          runId: "run-1",
          eventType: "stream.gap",
          eventId: "gap-1",
          payload: {
            reason: "retained_history_unavailable",
            recovery: "reload_durable_state",
            requested_event_id: "1-0",
            requested_stream_incarnation: 1,
            current_stream_incarnation: 1,
            earliest_available_event_id: "2-0",
            latest_available_event_id: "2-0",
          },
        }) as never,
      );
    },
    {
      replayGapDependencies: {
        getStatus: async () => {
          statusCalls += 1;
          return {
            session_id: "session-1",
            run_id: "run-1",
            status: statusCalls <= 4 ? "running" : "succeeded",
          };
        },
        replayGapStatusPollDelayMs: 0,
      },
    },
  );

  assert.equal(statusCalls, 5);
  assert.equal(hydrateCalls, 1);
  assert.doesNotThrow(() =>
    capturedInit?.onmessage?.({ event: "message", data: "not-json" } as never),
  );
  assert.deepEqual(context.acceptedStreamCursorRef.current, {
    sessionId: null,
    runId: null,
    eventId: null,
    streamIncarnation: null,
  });
  assert.deepEqual(context.acceptedRunEventSequenceRef.current, {
    sessionId: null,
    runId: null,
    sequence: null,
  });
  assert.equal(context.messagesRef.current[0]?.content, "partial");
  assert.equal(context.messagesRef.current[0]?.isStreaming, false);
  assert.deepEqual(connectionStates, ["connecting", "connected", "recovering_gap"]);
  assert.equal(context.currentRunIdRef.current, "run-1");
});

test("fresh no-cursor gap enters durable recovery without committing its cursor", async () => {
  let hydrateCalls = 0;
  const connectionStates: string[] = [];
  const context = {
    abortControllerRef: { current: null },
    isConnectingRef: { current: false },
    streamingMessageIdRef: { current: "assistant-1" },
    reconnectTimeoutRef: { current: null },
    retryCountRef: { current: 0 },
    statusRetryCountRef: { current: 0 },
    replayGapRecoveryRef: { current: null },
    messagesRef: { current: [] as Message[] },
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: "run-1" },
    processedEventIdsRef: { current: new Set<string>() },
    acceptedRunEventSequenceRef: {
      current: { sessionId: null, runId: null, sequence: null },
    },
    acceptedStreamCursorRef: {
      current: { sessionId: null, runId: null, eventId: null, streamIncarnation: null },
    },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    setSessionId: () => undefined,
    setMessages: () => undefined,
    setConnectionStatus: (status: string) => connectionStates.push(status),
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    hydrateTerminalRun: async () => {
      hydrateCalls += 1;
    },
  } satisfies SSEConnectionContext;

  await connectToSSE(
    "session-1",
    "run-1",
    "assistant-1",
    context,
    false,
    async (_input, init) => {
      await init.onopen?.(new Response(null, { status: 200 }));
      init.onmessage?.(
        v4Frame({
          cursor: "run-1:1:0-0",
          runId: "run-1",
          eventType: "stream.gap",
          eventId: "gap-1",
          payload: {
            reason: "retained_history_unavailable",
            recovery: "reload_durable_state",
            requested_event_id: null,
            requested_stream_incarnation: null,
            current_stream_incarnation: 1,
            earliest_available_event_id: null,
            latest_available_event_id: null,
          },
        }) as never,
      );
    },
    {
      replayGapDependencies: {
        getStatus: async () => ({
          session_id: "session-1",
          run_id: "run-1",
          status: "succeeded",
        }),
      },
    },
  );

  assert.equal(hydrateCalls, 1);
  assert.deepEqual(context.acceptedStreamCursorRef.current, {
    sessionId: null,
    runId: null,
    eventId: null,
    streamIncarnation: null,
  });
  assert.deepEqual(connectionStates, ["connecting", "connected", "recovering_gap"]);
});


test("does not mutate shared state when a stale owner receives a replay gap", async () => {
  const states: string[] = [];
  const context = {
    isMountedRef: { current: true },
    sessionIdRef: { current: "session-new" },
    currentRunIdRef: { current: "run-new" },
    streamVersionRef: { current: 2 },
    acceptedStreamCursorRef: {
      current: {
        sessionId: "session-old",
        runId: "run-old",
        eventId: "run-old:1:1-0",
        streamIncarnation: 1,
      },
    },
    acceptedRunEventSequenceRef: {
      current: { sessionId: "session-old", runId: "run-old", sequence: 8 },
    },
    replayGapRecoveryRef: { current: null },
    setMessages: () => assert.fail("stale gap must not mutate messages"),
    setConnectionStatus: (status: string) => states.push(status),
    setIsInitializingSandbox: () => assert.fail("stale gap must not mutate sandbox state"),
  } as unknown as SSEConnectionContext;

  await recoverReplayGap(context, {
    sessionId: "session-old",
    runId: "run-old",
    messageId: "assistant-old",
    streamVersion: 1,
  });

  assert.deepEqual(states, []);
  assert.equal(context.acceptedStreamCursorRef!.current.eventId, "run-old:1:1-0");
  assert.equal(context.acceptedRunEventSequenceRef!.current.sequence, 8);
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
