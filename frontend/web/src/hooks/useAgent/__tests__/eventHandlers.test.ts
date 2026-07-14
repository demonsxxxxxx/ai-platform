import assert from "node:assert/strict";
import test from "node:test";
import type { Message } from "../../../types";
import { handleStreamEvent } from "../eventHandlers.ts";
import type { EventHandlerContext } from "../eventHandlers.ts";
import type { StreamEvent } from "../types.ts";
import { prepareMessagesForRunningRun } from "../historyLoader.ts";

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

    handleStreamEvent(
      { event: terminalEvent, data: "{}" },
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
        event_id: "evt-tool",
        sequence: 4,
        event_type: "tool_denied",
        stage: "policy",
        message: "tool permission required",
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
