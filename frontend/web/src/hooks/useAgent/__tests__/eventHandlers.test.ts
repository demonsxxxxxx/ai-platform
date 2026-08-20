import assert from "node:assert/strict";
import test from "node:test";
import { getVisibleMessageParts } from "../../../components/chat/ChatMessage/messagePartVisibility.ts";
import type { Message } from "../../../types";
import { handleStreamEvent } from "../eventHandlers.ts";
import type { EventHandlerContext } from "../eventHandlers.ts";
import type { HistoryEvent, StreamEvent } from "../types.ts";
import {
  prepareMessagesForRunningRun,
  reconstructMessagesFromEvents,
} from "../historyLoader.ts";
import { PublicStreamPresentation } from "../publicStreamPresentation.ts";

function createContext(
  messages: Message[],
  lastHistoryTimestamp: Date | null,
  dismissQueueToast?: () => void,
): EventHandlerContext & {
  connectionStatuses: string[];
  messages: () => Message[];
  setMessagesCalls: () => number;
} {
  let setMessagesCalls = 0;
  const connectionStatuses: string[] = [];

  return {
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: null },
    processedEventIdsRef: { current: new Set<string>() },
    acceptedRunEventSequenceRef: {
      current: { sessionId: null, runId: null, sequence: null },
    },
    acceptedStreamCursorRef: {
      current: { sessionId: null, runId: null, eventId: null },
    },
    lastHistoryTimestampRef: { current: lastHistoryTimestamp },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    setSessionId: () => undefined,
    setMessages: (updater: React.SetStateAction<Message[]>) => {
      setMessagesCalls += 1;
      if (typeof updater === "function") {
        messages = updater(messages);
      } else {
        messages = updater;
      }
    },
    setConnectionStatus: (status: string) => {
      connectionStatuses.push(status);
    },
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
    dismissQueueToast,
    connectionStatuses,
    messages: () => messages,
    setMessagesCalls: () => setMessagesCalls,
  };
}

test("terminal stream events dismiss a queued admission toast", () => {
  for (const terminalEvent of [
    "complete",
    "done",
    "user:cancel",
    "error",
  ] as const) {
    let dismissCalls = 0;
    const ctx = createContext([], null, () => {
      dismissCalls += 1;
    });
    ctx.currentRunIdRef.current = "run-active";

    handleStreamEvent(
      {
        event: terminalEvent,
        data: JSON.stringify({
          run_id: "run-active",
          ...(terminalEvent === "error" ? { error: "run_failed" } : {}),
        }),
      },
      "assistant-1",
      `terminal-${terminalEvent}`,
      "2026-07-11T01:02:03.000Z",
      ctx,
    );

    assert.equal(dismissCalls, 1, `${terminalEvent} must clear chat-queue`);
  }
});

test("does not let a stale run terminal event finalize the active run", () => {
  const ctx = createContext([], null);
  ctx.currentRunIdRef.current = "run-new";
  const terminalCalls: Array<[string, string, string]> = [];
  ctx.onRunTerminal = (runId, status, messageId) => {
    terminalCalls.push([runId, status, messageId]);
    return true;
  };

  handleStreamEvent(
    {
      event: "run_event",
      data: JSON.stringify({
        run_id: "run-old",
        event_type: "run_failed",
      }),
    } as StreamEvent,
    "assistant-old",
    "evt-old-terminal",
    "2026-07-14T02:00:00.000Z",
    ctx,
  );

  assert.deepEqual(terminalCalls, []);
  assert.equal(ctx.setMessagesCalls(), 0);
});

test("rejects a stale cursor before terminal callbacks or reducer mutation", () => {
  const ctx = createContext([], null);
  ctx.currentRunIdRef.current = "run-active";
  ctx.acceptedStreamCursorRef!.current = {
    sessionId: "session-1",
    runId: "run-active",
    eventId: "run-active:1:2-0",
  };
  const terminalCalls: string[] = [];
  ctx.onRunTerminal = () => {
    terminalCalls.push("terminal");
    return true;
  };

  const accepted = handleStreamEvent(
    {
      event: "run_event",
      data: JSON.stringify({
        run_id: "run-active",
        event_type: "run_failed",
      }),
    } as StreamEvent,
    "assistant-active",
    "run-active:1:1-0",
    "2026-07-14T02:00:00.000Z",
    ctx,
  );

  assert.equal(accepted, false);
  assert.deepEqual(terminalCalls, []);
  assert.equal(ctx.setMessagesCalls(), 0);
});

test("delegates an active run terminal event once to the lifecycle owner", () => {
  const ctx = createContext([], null);
  ctx.currentRunIdRef.current = "run-active";
  let terminalCalls = 0;
  ctx.onRunTerminal = (runId, status, messageId) => {
    terminalCalls += 1;
    assert.deepEqual([runId, status, messageId], [
      "run-active",
      "failed",
      "assistant-active",
    ]);
    ctx.currentRunIdRef.current = null;
    return true;
  };

  const terminalEvent = {
    event: "run_event",
    data: JSON.stringify({
      run_id: "run-active",
      event_type: "run_failed",
    }),
  } as StreamEvent;
  handleStreamEvent(
    terminalEvent,
    "assistant-active",
    "evt-terminal-1",
    "2026-07-14T02:00:00.000Z",
    ctx,
  );
  handleStreamEvent(
    { ...terminalEvent, data: terminalEvent.data },
    "assistant-active",
    "evt-terminal-2",
    "2026-07-14T02:00:01.000Z",
    ctx,
  );

  assert.equal(terminalCalls, 1);
  assert.equal(ctx.setMessagesCalls(), 0);
});

test("skips replayed SSE events at the history timestamp boundary", () => {
  const timestamp = "2026-04-19T01:02:03.456Z";
  const ctx = createContext(
    [
      {
        id: "assistant-1",
        role: "assistant",
        content: "",
        timestamp: new Date(timestamp),
        parts: [],
        isStreaming: true,
      },
    ],
    new Date(timestamp),
  );

  const event: StreamEvent = {
    event: "message:chunk",
    data: JSON.stringify({ content: "duplicate", _timestamp: timestamp }),
  };

  handleStreamEvent(event, "assistant-1", "redis-event-1", timestamp, ctx);

  assert.equal(ctx.setMessagesCalls(), 0);
});

test("reports acceptance only after current-run validation and deduplication", () => {
  const ctx = createContext(
    [
      {
        id: "assistant-1",
        role: "assistant",
        content: "",
        timestamp: new Date(),
        parts: [],
        isStreaming: true,
      },
    ],
    new Date("2026-04-19T01:02:03.456Z"),
  );
  ctx.currentRunIdRef.current = "run-active";

  const accepted = handleStreamEvent(
    {
      event: "run_event",
      data: JSON.stringify({
        run_id: "run-active",
        sequence: 4,
        event_type: "worker_started",
      }),
    } as StreamEvent,
    "assistant-1",
    "evt-current",
    "2026-04-19T01:02:04.000Z",
    ctx,
    { sessionId: "session-1", runId: "run-active", streamVersion: 0 },
  );
  assert.equal(accepted, true);
  assert.equal(
    handleStreamEvent(
      {
        event: "run_event",
        data: JSON.stringify({
          run_id: "run-active",
          sequence: 5,
          event_type: "worker_started",
        }),
      } as StreamEvent,
      "assistant-1",
      "evt-current",
      "2026-04-19T01:02:05.000Z",
      ctx,
      { sessionId: "session-1", runId: "run-active", streamVersion: 0 },
    ),
    false,
  );
  assert.equal(
    handleStreamEvent(
      {
        event: "run_event",
        data: JSON.stringify({
          run_id: "run-foreign",
          sequence: 6,
          event_type: "worker_started",
        }),
      } as StreamEvent,
      "assistant-1",
      "evt-foreign",
      "2026-04-19T01:02:06.000Z",
      ctx,
      { sessionId: "session-1", runId: "run-active", streamVersion: 0 },
    ),
    false,
  );
  assert.equal(
    handleStreamEvent(
      { event: "run_event", data: "not-json" } as StreamEvent,
      "assistant-1",
      "evt-invalid",
      "2026-04-19T01:02:07.000Z",
      ctx,
      { sessionId: "session-1", runId: "run-active", streamVersion: 0 },
    ),
    false,
  );
  assert.equal(
    handleStreamEvent(
      { event: "done", data: JSON.stringify({ status: "failed" }) },
      "assistant-1",
      "evt-runless-terminal",
      "2026-04-19T01:02:08.000Z",
      ctx,
    ),
    false,
  );
});

test("does not acknowledge a transport cursor until the reducer updater commits", () => {
  const ctx = createContext(
    [
      {
        id: "assistant-1",
        role: "assistant",
        content: "",
        timestamp: new Date(),
        parts: [],
        isStreaming: true,
      },
    ],
    null,
  );
  ctx.currentRunIdRef.current = "run-active";
  let deferredUpdater: React.SetStateAction<Message[]> | null = null;
  ctx.setMessages = (updater) => {
    deferredUpdater = updater;
  };
  const commits: boolean[] = [];

  const accepted = handleStreamEvent(
    {
      event: "message:chunk",
      data: JSON.stringify({
        projection_version: "ai-platform.chat-public-projection.v1",
        projection_kind: "assistant_delta",
        run_id: "run-active",
        event_id: "semantic-delta-1",
        sequence: 4,
        content: "committed later",
      }),
    },
    "assistant-1",
    "run-active:1:1-0",
    undefined,
    ctx,
    { sessionId: "session-1", runId: "run-active", streamVersion: 0 },
    (semanticApplied) => commits.push(semanticApplied),
  );

  assert.equal(accepted, true);
  assert.deepEqual(commits, []);
  assert.equal(ctx.acceptedRunEventSequenceRef!.current.sequence, null);
  assert.equal(ctx.processedEventIdsRef.current.has("semantic-delta-1"), false);
  const commitUpdater = deferredUpdater as React.SetStateAction<Message[]> | null;
  assert.equal(typeof commitUpdater, "function");

  if (typeof commitUpdater === "function") {
    commitUpdater(ctx.messages());
  }
  assert.deepEqual(commits, [true]);
  assert.equal(ctx.acceptedRunEventSequenceRef!.current.sequence, 4);
  assert.equal(ctx.processedEventIdsRef.current.has("semantic-delta-1"), true);
});

test("retains the run-event sequence replay guard after the event-id cap", () => {
  const ctx = createContext(
    [
      {
        id: "assistant-1",
        role: "assistant",
        content: "",
        timestamp: new Date(),
        isStreaming: true,
      },
    ],
    null,
  );
  ctx.currentRunIdRef.current = "run-active";
  ctx.acceptedRunEventSequenceRef!.current = {
    sessionId: "session-1",
    runId: "run-active",
    sequence: 8,
  };
  ctx.processedEventIdsRef.current = new Set(
    Array.from({ length: 10_000 }, (_, index) => `history-${index}`),
  );
  const binding = {
    sessionId: "session-1",
    runId: "run-active",
    streamVersion: 0,
  };

  assert.equal(
    handleStreamEvent(
      {
        event: "run_event",
        data: JSON.stringify({
          run_id: "run-active",
          sequence: 9,
          event_type: "worker_progress",
        }),
      } as StreamEvent,
      "assistant-1",
      "evt-new-after-cap",
      undefined,
      ctx,
      binding,
    ),
    true,
  );
  assert.equal(ctx.processedEventIdsRef.current.size, 1);
  assert.equal(
    handleStreamEvent(
      {
        event: "run_event",
        data: JSON.stringify({
          run_id: "run-active",
          sequence: 8,
          event_type: "worker_started",
        }),
      } as StreamEvent,
      "assistant-1",
      "evt-old-replay-after-cap",
      undefined,
      ctx,
      binding,
    ),
    false,
  );
  assert.equal(ctx.setMessagesCalls(), 1);
});

test("uses the existing cursor and event-id guard for public execution steps", () => {
  const ctx = createContext(
    [
      {
        id: "assistant-1",
        role: "assistant",
        content: "",
        timestamp: new Date(),
        parts: [],
        isStreaming: true,
      },
    ],
    null,
  );
  ctx.currentRunIdRef.current = "run-active";
  const binding = {
    sessionId: "session-1",
    runId: "run-active",
    streamVersion: 0,
  };

  const accepted = handleStreamEvent(
    {
      event: "execution_step",
      data: JSON.stringify({
        schema_version: "ai-platform.public-execution-event.v1",
        event_id: "evt-execution-started",
        run_id: "run-active",
        sequence: 9,
        step_id: "step-prepare-report",
        kind: "processing",
        stage: "prepare",
        status: "running",
        title: "准备报告",
        summary: "正在读取输入",
        progress: { current: 0, total: 4 },
        safe_file_name: null,
        artifact_public_id: null,
        created_at: null,
      }),
    } as StreamEvent,
    "assistant-1",
    "evt-execution-started",
    undefined,
    ctx,
    binding,
  );
  const duplicateEventId = handleStreamEvent(
    {
      event: "execution_progress",
      data: JSON.stringify({
        schema_version: "ai-platform.public-execution-event.v1",
        event_id: "evt-execution-started",
        run_id: "run-active",
        sequence: 10,
        step_id: "step-prepare-report",
        kind: "processing",
        stage: "prepare",
        status: "running",
        title: "准备报告",
        summary: "重复事件不得更新",
        progress: { current: 2, total: 4 },
        safe_file_name: null,
        artifact_public_id: null,
        created_at: null,
      }),
    } as StreamEvent,
    "assistant-1",
    "evt-execution-started",
    undefined,
    ctx,
    binding,
  );
  const staleSequence = handleStreamEvent(
    {
      event: "execution_progress",
      data: JSON.stringify({
        schema_version: "ai-platform.public-execution-event.v1",
        event_id: "evt-execution-stale",
        run_id: "run-active",
        sequence: 8,
        step_id: "step-prepare-report",
        kind: "processing",
        stage: "prepare",
        status: "running",
        title: "准备报告",
        summary: "乱序事件不得更新",
        progress: { current: 1, total: 4 },
        safe_file_name: null,
        artifact_public_id: null,
        created_at: null,
      }),
    } as StreamEvent,
    "assistant-1",
    "evt-execution-stale",
    undefined,
    ctx,
    binding,
  );

  assert.equal(accepted, true);
  assert.equal(duplicateEventId, false);
  assert.equal(staleSequence, false);
  assert.deepEqual(ctx.acceptedRunEventSequenceRef?.current, {
    sessionId: "session-1",
    runId: "run-active",
    sequence: 9,
  });
  assert.equal(ctx.setMessagesCalls(), 1);
});

test("uses the durable sequence for assistant deltas and final replacement", () => {
  const ctx = createContext(
    [
      {
        id: "assistant-1",
        role: "assistant",
        content: "A",
        timestamp: new Date(),
        parts: [{ type: "text", content: "A" }],
        isStreaming: true,
      },
    ],
    null,
  );
  ctx.currentRunIdRef.current = "run-active";
  ctx.acceptedRunEventSequenceRef!.current = {
    sessionId: "session-1",
    runId: "run-active",
    sequence: 7,
  };
  const binding = {
    sessionId: "session-1",
    runId: "run-active",
    streamVersion: 0,
  };
  let frame: FrameRequestCallback | null = null;
  const presentation = new PublicStreamPresentation({
    now: () => 0,
    requestAnimationFrame: (callback) => {
      frame = callback;
      return 1;
    },
    cancelAnimationFrame: () => {
      frame = null;
    },
    setTimeout: () => 1 as unknown as ReturnType<typeof setTimeout>,
    clearTimeout: () => undefined,
  });
  ctx.publicStreamPresentation = presentation;
  presentation.activate({
    sessionId: binding.sessionId,
    runId: binding.runId,
    assistantMessageId: "assistant-1",
    streamVersion: binding.streamVersion,
  });

  const acceptedDelta = handleStreamEvent(
    {
      event: "message:chunk",
      data: JSON.stringify({
        projection_version: "ai-platform.chat-public-projection.v1",
        projection_kind: "assistant_delta",
        run_id: "run-active",
        event_id: "evt-delta-8",
        sequence: 8,
        content: "B",
      }),
    },
    "assistant-1",
    "evt-delta-8",
    undefined,
    ctx,
    binding,
  );
  const rejectedReplay = handleStreamEvent(
    {
      event: "message:chunk",
      data: JSON.stringify({
        projection_version: "ai-platform.chat-public-projection.v1",
        projection_kind: "assistant_delta",
        run_id: "run-active",
        event_id: "evt-delta-8-replayed",
        sequence: 8,
        content: "B",
      }),
    },
    "assistant-1",
    "evt-delta-8-replayed",
    undefined,
    ctx,
    binding,
  );
  // The accepted cursor stays at the last reducer commit while the delta is
  // still buffered for the next presentation frame.
  assert.equal(ctx.acceptedRunEventSequenceRef!.current.sequence, 7);
  assert.equal(ctx.messages()[0]?.content, "A");
  assert.notEqual(frame, null);
  const acceptedFinal = handleStreamEvent(
    {
      event: "message:chunk",
      data: JSON.stringify({
        projection_version: "ai-platform.chat-public-projection.v1",
        projection_kind: "assistant_final",
        run_id: "run-active",
        content: "AB!",
      }),
    },
    "assistant-1",
    "run-active:final",
    undefined,
    ctx,
    binding,
  );

  assert.equal(acceptedDelta, true);
  assert.equal(rejectedReplay, false);
  assert.equal(acceptedFinal, true);
  assert.equal(ctx.acceptedRunEventSequenceRef!.current.sequence, 8);
  assert.equal(ctx.messages()[0]?.content, "AB!");
  assert.deepEqual(ctx.messages()[0]?.parts, [
    { type: "text", content: "AB!" },
  ]);
  assert.equal(ctx.setMessagesCalls(), 2);
});

test("commits a public delta before a later execution state and keeps history semantically coherent", () => {
  const ctx = createContext(
    [
      {
        id: "assistant-ordered",
        role: "assistant",
        content: "",
        timestamp: new Date(),
        parts: [],
        isStreaming: true,
      },
    ],
    null,
  );
  ctx.currentRunIdRef.current = "run-ordered";
  ctx.acceptedRunEventSequenceRef!.current = {
    sessionId: "session-1",
    runId: "run-ordered",
    sequence: 7,
  };
  const binding = {
    sessionId: "session-1",
    runId: "run-ordered",
    streamVersion: 0,
  };
  let pendingFrame: FrameRequestCallback | null = null;
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
  presentation.activate({
    sessionId: binding.sessionId,
    runId: binding.runId,
    assistantMessageId: "assistant-ordered",
    streamVersion: binding.streamVersion,
  });
  ctx.publicStreamPresentation = presentation;
  const snapshots: Array<{ content: string; partTypes: string[] }> = [];
  const commit = ctx.setMessages;
  ctx.setMessages = (updater) => {
    commit(updater);
    const assistant = ctx.messages()[0];
    snapshots.push({
      content: assistant?.content || "",
      partTypes: (assistant?.parts || []).map((part) => part.type),
    });
  };
  const delta = {
    event: "message:chunk",
    data: JSON.stringify({
      projection_version: "ai-platform.chat-public-projection.v1",
      projection_kind: "assistant_delta",
      run_id: "run-ordered",
      event_id: "evt-delta-8",
      sequence: 8,
      content: "B",
    }),
  } as StreamEvent;
  const started = {
    event: "execution_step",
    data: JSON.stringify({
      schema_version: "ai-platform.public-execution-event.v1",
      event_id: "evt-step-9",
      run_id: "run-ordered",
      sequence: 9,
      step_id: "step-1",
      kind: "processing",
      stage: "private-stage",
      status: "running",
      title: "private title",
      summary: "private summary",
      progress: { current: 0, total: 1 },
      safe_file_name: null,
      artifact_public_id: null,
      created_at: "2026-07-31T01:00:00.000Z",
    }),
  } as StreamEvent;

  assert.equal(
    handleStreamEvent(delta, "assistant-ordered", "evt-delta-8", undefined, ctx, binding),
    true,
  );
  assert.equal(
    handleStreamEvent(started, "assistant-ordered", "evt-step-9", undefined, ctx, binding),
    true,
  );
  assert.deepEqual(snapshots, [
    { content: "B", partTypes: ["text"] },
    { content: "B", partTypes: ["text", "execution_step"] },
  ]);
  assert.equal(pendingFrame, null);

  const history = reconstructMessagesFromEvents(
    [
      {
        id: "evt-delta-8",
        event_type: "message:chunk",
        run_id: "run-ordered",
        sequence: 8,
        timestamp: "2026-07-31T01:00:00.000Z",
        data: JSON.parse(delta.data),
      },
      {
        id: "evt-step-9",
        event_type: "execution_step",
        run_id: "run-ordered",
        sequence: 9,
        timestamp: "2026-07-31T01:00:01.000Z",
        data: JSON.parse(started.data),
      },
    ] satisfies HistoryEvent[],
    new Set<string>(),
    { activeSubagentStack: [] },
  )[0];
  const liveStep = ctx.messages()[0]?.parts?.find(
    (part) => part.type === "execution_step",
  );
  const historyProcess = getVisibleMessageParts(history?.parts || []).find(
    (part) => part.type === "execution_process",
  );
  assert.equal(ctx.messages()[0]?.content, "B");
  assert.equal(history?.content, "B");
  assert.equal(liveStep?.type, "execution_step");
  assert.equal(historyProcess?.type, "execution_process");
  if (liveStep?.type !== "execution_step" || historyProcess?.type !== "execution_process") {
    throw new Error("expected public execution steps");
  }
  assert.deepEqual(historyProcess.steps, [liveStep]);
});

test("creates a new streaming assistant for a running run after the latest user message", () => {
  const messages: Message[] = [
    {
      id: "user-previous",
      role: "user",
      content: "previous question",
      timestamp: new Date("2026-04-19T01:00:00.000Z"),
      runId: "run-previous",
    },
    {
      id: "assistant-previous",
      role: "assistant",
      content: "previous answer",
      timestamp: new Date("2026-04-19T01:00:01.000Z"),
      runId: "run-previous",
      isStreaming: false,
    },
    {
      id: "user-latest",
      role: "user",
      content: "latest question",
      timestamp: new Date("2026-04-19T01:01:00.000Z"),
      runId: "run-latest",
    },
  ];

  const result = prepareMessagesForRunningRun(
    messages,
    "run-latest",
    () => "assistant-latest",
  );

  assert.equal(result.streamingMessageId, "assistant-latest");
  assert.deepEqual(
    result.messages.map((message) => [
      message.id,
      message.role,
      message.runId,
      message.isStreaming ?? false,
    ]),
    [
      ["user-previous", "user", "run-previous", false],
      ["assistant-previous", "assistant", "run-previous", false],
      ["user-latest", "user", "run-latest", false],
      ["assistant-latest", "assistant", "run-latest", true],
    ],
  );
});

test("user cancel marks message cancelled without closing the SSE connection", () => {
  const ctx = createContext(
    [
      {
        id: "assistant-1",
        role: "assistant",
        content: "",
        timestamp: new Date("2026-04-19T01:02:03.456Z"),
        parts: [{ type: "text", content: "partial" }],
        isStreaming: true,
      },
    ],
    null,
  );
  ctx.currentRunIdRef.current = "run-1";

  handleStreamEvent(
    {
      event: "user:cancel",
      data: JSON.stringify({ run_id: "run-1" }),
    },
    "assistant-1",
    "redis-event-cancel",
    "2026-04-19T01:02:04.000Z",
    ctx,
  );

  assert.equal(ctx.messages()[0]?.cancelled, true);
  assert.equal(ctx.messages()[0]?.isStreaming, false);
  assert.deepEqual(ctx.messages()[0]?.parts?.map((part) => part.type), [
    "text",
    "cancelled",
  ]);
  assert.deepEqual(ctx.connectionStatuses, []);
});

test("streams ai-platform run event and artifact card into message parts", () => {
  const ctx = createContext(
    [
      {
        id: "assistant-1",
        role: "assistant",
        content: "",
        timestamp: new Date("2026-06-02T01:02:03.456Z"),
        parts: [],
        isStreaming: true,
      },
    ],
    null,
  );

  handleStreamEvent(
    {
      event: "run_event",
      data: JSON.stringify({
        projection_version: "ai-platform.chat-public-projection.v1",
        event_id: "evt-tool",
        sequence: 4,
        event_type: "agent_step_blocked",
        stage: "wait",
        message: "当前处理步骤未获授权，正在等待权限调整",
        severity: "warning",
      }),
    } as StreamEvent,
    "assistant-1",
    "evt-tool",
    "2026-06-02T01:02:04.000Z",
    ctx,
  );
  handleStreamEvent(
    {
      event: "artifact_card",
      data: JSON.stringify({
        artifact_id: "art-reviewed",
        artifact_type: "reviewed_docx",
        label: "审核 Word",
        size_bytes: 123,
        download_url: "/api/ai/artifacts/art-reviewed/download",
        status: "available",
      }),
    } as StreamEvent,
    "assistant-1",
    "art-reviewed:artifact",
    "2026-06-02T01:02:05.000Z",
    ctx,
  );

  assert.deepEqual(
    ctx.messages()[0]?.parts?.map((part) => part.type),
    ["run_status", "artifact"],
  );
  assert.equal(ctx.setMessagesCalls(), 2);
});

test("keeps a controlled failed final detail before exactly-once terminal convergence", () => {
  const ctx = createContext(
    [
      {
        id: "assistant-final",
        role: "assistant",
        content: "",
        timestamp: new Date("2026-07-15T01:00:00.000Z"),
        parts: [],
        isStreaming: true,
      },
    ],
    null,
  );
  ctx.currentRunIdRef.current = "run-final";
  let terminalCalls = 0;
  ctx.onRunTerminal = () => {
    terminalCalls += 1;
    ctx.currentRunIdRef.current = null;
    return true;
  };

  const acceptedDetail = handleStreamEvent(
    {
      event: "final_detail",
      data: JSON.stringify({
        run_id: "run-final",
        detail_kind: "failed",
        detail_code: "run_failed",
      }),
    } as StreamEvent,
    "assistant-final",
    "run-final:final",
    "2026-07-15T01:00:01.000Z",
    ctx,
  );
  const acceptedTerminal = handleStreamEvent(
    {
      event: "run_event",
      data: JSON.stringify({
        run_id: "run-final",
        event_type: "run_failed",
      }),
    } as StreamEvent,
    "assistant-final",
    "evt-final-failed",
    "2026-07-15T01:00:02.000Z",
    ctx,
  );

  assert.equal(acceptedDetail, true);
  assert.equal(acceptedTerminal, true);
  assert.equal(terminalCalls, 1);
  assert.match(ctx.messages()[0]?.content || "", /任务未能完成/);
  assert.equal(ctx.messages()[0]?.isStreaming, true);
  assert.doesNotMatch(ctx.messages()[0]?.content || "", /Executor failed/);
});

test("stream error handler never renders unknown backend diagnostics", () => {
  const ctx = createContext(
    [
      {
        id: "assistant-error",
        role: "assistant",
        content: "",
        timestamp: new Date("2026-07-15T01:00:00.000Z"),
        parts: [],
        isStreaming: true,
      },
    ],
    null,
  );

  handleStreamEvent(
    {
      event: "error",
      data: JSON.stringify({
        error: "C:\\private\\executor.log?token=secret <html>proxy</html>",
      }),
    },
    "assistant-error",
    "evt-safe-error",
    "2026-07-15T01:00:01.000Z",
    ctx,
  );

  const content = ctx.messages()[0]?.content || "";
  assert.ok(content.length > 0);
  assert.doesNotMatch(content, /private|token|proxy|html|executor\.log/i);
});

test("sandbox error side effects never expose unknown backend diagnostics", () => {
  const ctx = createContext([], null);
  const sandboxErrors: Array<string | null> = [];
  ctx.currentRunIdRef.current = "run-sandbox-error";
  ctx.setSandboxError = (value) => sandboxErrors.push(value);

  handleStreamEvent(
    {
      event: "sandbox:error",
      data: JSON.stringify({
        run_id: "run-sandbox-error",
        error: "C:\\private\\sandbox.log?token=secret <html>proxy</html>",
      }),
    },
    "assistant-sandbox-error",
    "evt-sandbox-error",
    "2026-07-15T01:00:00.000Z",
    ctx,
  );

  assert.equal(sandboxErrors.length, 1);
  assert.ok(sandboxErrors[0]);
  assert.doesNotMatch(
    String(sandboxErrors[0]),
    /private|token|secret|proxy|html|sandbox\.log/i,
  );
});
