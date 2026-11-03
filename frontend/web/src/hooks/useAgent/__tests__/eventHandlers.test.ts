import assert from "node:assert/strict";
import test from "node:test";
import type { Message } from "../../../types";
import {
  handlePublicRunStreamEventV4Result,
  rebindV4MessageOwner,
  setMessageSnapshot,
} from "../eventHandlers.ts";
import type { EventHandlerContext } from "../eventHandlers.ts";
import { adaptPublicRunStreamEventV4 } from "../../../components/chat/assistant-ui/publicEventAdapter.ts";
import { prepareMessagesForRunningRun } from "../historyLoader.ts";

const adaptV4Frame = (
  frame: Parameters<typeof adaptPublicRunStreamEventV4>[0],
  binding: Parameters<typeof adaptPublicRunStreamEventV4>[1],
) => {
  const event = adaptPublicRunStreamEventV4(frame, binding);
  assert.ok(event);
  return event;
};

const handlePublicRunStreamEventV4 = (
  args: Parameters<typeof handlePublicRunStreamEventV4Result>[0],
) => {
  const result = handlePublicRunStreamEventV4Result(args);
  return result.kind === "applied" || result.kind === "deferred";
};

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
  const messagesRef = { current: messages };

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
    v4TerminalReservationsRef: { current: new Set<string>() },
    v4MessageCandidateRef: { current: null },
    lastHistoryTimestampRef: { current: lastHistoryTimestamp },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    setSessionId: () => undefined,
    messagesRef,
    setMessages: (updater: React.SetStateAction<Message[]>) => {
      setMessagesCalls += 1;
      messages = typeof updater === "function" ? updater(messages) : updater;
      messagesRef.current = messages;
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

test("v4 control ordering ignores wall-clock history timestamps", () => {
  const ctx = createContext([], new Date("2026-04-19T01:02:03.456Z"));
  ctx.currentRunIdRef.current = "run-active";
  const commits: boolean[] = [];
  const accepted = handlePublicRunStreamEventV4({
    event: adaptV4Frame({
      eventHeader: "stream.open",
      transportCursor: "run-active:2:1-0",
      generation: 7,
      value: {
        schema: "ai-platform.public-run-stream-control.v4",
        event_id: "stream-open-old-clock",
        run_id: "run-active",
        message_id: null,
        seq: null,
        event_type: "stream.open",
        stream_incarnation: 2,
        replayable: true,
        trace_ref: null,
        causation_event_id: null,
        emitted_at: "2026-04-19T01:02:02.000Z",
        payload: {
          design_id: "ai-platform.redis-streams-sse-event-channel.v4",
        },
      },
    }, {
      runId: "run-active",
      streamIncarnation: 2,
      generation: 7,
    }),
    messageId: "assistant-1",
    ctx,
    binding: {
      sessionId: "session-1",
      runId: "run-active",
      streamVersion: 0,
      streamIncarnation: 2,
      generation: 7,
    },
    currentGeneration: 7,
    onCommitted: (semanticApplied) => commits.push(semanticApplied),
  });

  assert.equal(accepted, true);
  assert.deepEqual(commits, [true]);
});

test("preserves a terminal card across consecutive value snapshot publications", () => {
  const published: React.SetStateAction<Message[]>[] = [];
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
  ctx.setMessages = (snapshot) => published.push(snapshot);

  setMessageSnapshot(ctx, (messages) =>
    messages.map((message) => ({
      ...message,
      parts: [
        ...(message.parts || []),
        {
          type: "run_status" as const,
          event_id: "terminal-card",
          event_type: "run_failed",
          stage: "agent",
          message: "failed",
          severity: "error" as const,
        },
      ],
    })),
  );
  setMessageSnapshot(ctx, (messages) =>
    messages.map((message) => ({ ...message, isStreaming: false })),
  );

  assert.equal(published.every((snapshot) => Array.isArray(snapshot)), true);
  assert.equal(ctx.messagesRef.current[0]?.isStreaming, false);
  assert.equal(ctx.messagesRef.current[0]?.parts?.[0]?.type, "run_status");
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

test("v4 accepts correlated activity before message owner is declared", () => {
  const ctx = createContext([
    {
      id: "run-owner",
      runId: "run-owner",
      role: "assistant",
      content: "",
      timestamp: new Date("2026-01-01T00:00:00Z"),
      parts: [],
      isStreaming: true,
    },
  ], null);
  ctx.currentRunIdRef.current = "run-owner";
  ctx.v4MessageOwnerRef = { current: null };
  ctx.v4MessageCandidateRef = { current: null };
  const applyMessageUpdate = ctx.setMessages;
  const deferredMessageUpdates: Array<() => void> = [];
  ctx.setMessages = (updater) => {
    deferredMessageUpdates.push(() => applyMessageUpdate(updater));
  };
  const frame = (
    eventType:
      | "artifact.created"
      | "artifact.ready"
      | "thinking.started"
      | "thinking.completed"
      | "commentary.delta"
      | "message.started"
      | "message.delta",
    messageId: string,
    sequence: number,
  ) => ({
    eventHeader: eventType,
    transportCursor: `run-owner:2:${sequence}-0`,
    generation: 7,
    value: {
      schema: "ai-platform.public-run-stream-event.v4",
      event_id: `event-${sequence}`,
      run_id: "run-owner",
      message_id: messageId,
      seq: sequence,
      event_type: eventType,
      stream_incarnation: 2,
      replayable: true,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-01-01T00:00:00Z",
      payload:
        eventType === "message.delta"
          ? { delta: "accepted" }
          : eventType === "commentary.delta"
            ? { summary_id: "worktrace_1", delta: "正在检查授权输入。" }
            : eventType.startsWith("artifact.")
            ? {
                artifact_id: "artifact-1",
                filename: "result.txt",
                media_type: "text/plain",
                size_bytes: 8,
                status: eventType === "artifact.created" ? "created" : "ready",
              }
            : {},
    },
  });
  const accept = (candidate: ReturnType<typeof frame>) =>
    handlePublicRunStreamEventV4({
      event: adaptV4Frame(candidate as never, { runId: "run-owner", streamIncarnation: 2, generation: 7 }),
      messageId: "run-owner",
      ctx,
      binding: {
        sessionId: "session-1",
        runId: "run-owner",
        streamVersion: 0,
        streamIncarnation: 2,
        generation: 7,
      },
      currentGeneration: 7,
    });

  assert.equal(accept(frame("artifact.created", "artifact-ref", 1)), true);
  assert.equal(ctx.v4MessageCandidateRef.current, null);
  assert.equal(accept(frame("thinking.started", "message-1", 2)), true);
  assert.deepEqual(ctx.v4MessageCandidateRef.current, {
    sessionId: "session-1",
    runId: "run-owner",
    streamVersion: 0,
    streamIncarnation: 2,
    protocolMessageId: "message-1",
  });
  assert.equal(accept(frame("commentary.delta", "message-1", 3)), true);
  assert.equal(accept(frame("thinking.completed", "message-1", 4)), true);
  assert.equal(accept(frame("thinking.completed", "message-2", 5)), false);
  assert.equal(accept(frame("message.started", "message-1", 5)), true);
  assert.equal(ctx.v4MessageCandidateRef.current, null);
  assert.equal(accept(frame("message.delta", "message-1", 6)), true);
  assert.equal(accept(frame("artifact.ready", "other-artifact-ref", 7)), true);
  assert.equal(ctx.v4MessageOwnerRef.current?.protocolMessageId, "message-1");
  assert.equal(ctx.messages()[0]?.content, "");
  assert.equal(deferredMessageUpdates.length, 7);
  for (const apply of deferredMessageUpdates) apply();
  assert.equal(ctx.messages()[0]?.content, "accepted");
  const summary = ctx.messages()[0]?.parts?.find((part) => part.type === "summary");
  assert.equal(summary?.type === "summary" ? summary.content : null, "正在检查授权输入。");
  assert.equal(summary?.type === "summary" ? summary.kind : null, "work_trace");
});

test("v4 history-covered message.started restores ownership before live delta", () => {
  const ctx = createContext([
    {
      id: "assistant-history",
      runId: "run-history",
      role: "assistant",
      content: "hydrated",
      timestamp: new Date("2026-01-01T00:00:01Z"),
      parts: [{ type: "text", content: "hydrated" }],
      isStreaming: true,
    },
  ], new Date("2026-01-01T00:00:01Z"));
  ctx.currentRunIdRef.current = "run-history";
  ctx.v4MessageOwnerRef = { current: null };
  ctx.v4MessageCandidateRef = { current: null };
  ctx.processedEventIdsRef.current.add("history-event-1");
  const commits: boolean[] = [];
  const accept = (
    eventType: "message.started" | "message.delta",
    sequence: number,
    emittedAt: string,
  ) =>
    handlePublicRunStreamEventV4({
      event: adaptV4Frame({
        eventHeader: eventType,
        transportCursor: `run-history:2:${sequence}-0`,
        generation: 7,
        value: {
          schema: "ai-platform.public-run-stream-event.v4",
          event_id: `history-event-${sequence}`,
          run_id: "run-history",
          message_id: "protocol-message-1",
          seq: sequence,
          event_type: eventType,
          stream_incarnation: 2,
          replayable: true,
          trace_ref: null,
          causation_event_id: null,
          emitted_at: emittedAt,
          payload: eventType === "message.delta" ? { delta: " live" } : {},
        },
      }, {
        runId: "run-history",
        streamIncarnation: 2,
        generation: 7,
      }),
      messageId: "assistant-history",
      ctx,
      binding: {
        sessionId: "session-1",
        runId: "run-history",
        streamVersion: 0,
        streamIncarnation: 2,
        generation: 7,
      },
      currentGeneration: 7,
      onCommitted: (semanticApplied) => commits.push(semanticApplied),
    });

  ctx.v4MessageCandidateRef.current = {
    sessionId: "session-1",
    runId: "run-history",
    streamVersion: 0,
    streamIncarnation: 2,
    protocolMessageId: "different-protocol-message",
  };
  assert.equal(
    accept("message.started", 1, "2026-01-01T00:00:00Z"),
    false,
  );
  assert.equal(Boolean(ctx.v4MessageOwnerRef.current), false);
  assert.equal(
    ctx.v4MessageCandidateRef.current?.protocolMessageId,
    "different-protocol-message",
  );
  ctx.v4MessageCandidateRef.current.protocolMessageId = "protocol-message-1";
  assert.equal(
    accept("message.started", 1, "2026-01-01T00:00:00Z"),
    false,
  );
  assert.deepEqual(commits, [false, false]);
  assert.equal(
    ctx.v4MessageOwnerRef.current?.protocolMessageId,
    "protocol-message-1",
  );
  assert.equal(accept("message.delta", 2, "2026-01-01T00:00:02Z"), true);
  assert.equal(ctx.messages()[0]?.content, "hydrated live");
});

test("v4 rejects a message delta when its reducer message is missing", () => {
  const ctx = createContext([], null);
  ctx.currentRunIdRef.current = "run-missing-message";
  ctx.v4MessageOwnerRef = {
    current: {
      sessionId: "session-1",
      runId: "run-missing-message",
      streamVersion: 0,
      streamIncarnation: 2,
      protocolMessageId: "protocol-message-1",
      reducerMessageId: "missing-assistant",
    },
  };
  ctx.acceptedRunEventSequenceRef!.current = {
    sessionId: "session-1",
    runId: "run-missing-message",
    sequence: 9,
  };
  const commits: boolean[] = [];
  const binding = {
    sessionId: "session-1",
    runId: "run-missing-message",
    streamVersion: 0,
    streamIncarnation: 2,
    generation: 7,
  };

  const result = handlePublicRunStreamEventV4Result({
    event: adaptV4Frame({
      eventHeader: "message.delta",
      transportCursor: "run-missing-message:2:2-0",
      generation: 7,
      value: {
        schema: "ai-platform.public-run-stream-event.v4",
        event_id: "missing-message-delta",
        run_id: "run-missing-message",
        message_id: "protocol-message-1",
        seq: 2,
        event_type: "message.delta",
        stream_incarnation: 2,
        replayable: true,
        trace_ref: null,
        causation_event_id: null,
        emitted_at: "2026-01-01T00:00:02Z",
        payload: { delta: "must not be silently dropped" },
      },
    }, {
      runId: "run-missing-message",
      streamIncarnation: 2,
      generation: 7,
    }),
    messageId: "missing-assistant",
    ctx,
    binding,
    currentGeneration: 7,
    onCommitted: (semanticApplied) => commits.push(semanticApplied),
  });

  assert.deepEqual(result, { kind: "invalid" });
  assert.deepEqual(commits, []);
  assert.equal(ctx.messages().length, 0);
});

test("v4 message ownership survives reconnect and rejects a second protocol identity", () => {
  const ctx = createContext([
    {
      id: "run-owner",
      runId: "run-owner",
      role: "assistant",
      content: "",
      timestamp: new Date("2026-01-01T00:00:00Z"),
      parts: [],
      isStreaming: true,
    },
  ], null);
  ctx.currentRunIdRef.current = "run-owner";
  ctx.v4MessageOwnerRef = { current: null };
  const frame = (
    eventType: "message.started" | "message.delta",
    messageId: string,
    sequence: number,
    generation: number,
    streamIncarnation = 2,
  ) => ({
    eventHeader: eventType,
    transportCursor: `run-owner:${streamIncarnation}:${sequence}-0`,
    generation,
    value: {
      schema: "ai-platform.public-run-stream-event.v4",
      event_id: `event-${streamIncarnation}-${sequence}`,
      run_id: "run-owner",
      message_id: messageId,
      seq: sequence,
      event_type: eventType,
      stream_incarnation: streamIncarnation,
      replayable: true,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-01-01T00:00:00Z",
      payload: eventType === "message.delta" ? { delta: "accepted" } : {},
    },
  });
  const accept = (
    candidate: ReturnType<typeof frame>,
    generation: number,
    streamIncarnation = 2,
  ) => handlePublicRunStreamEventV4({
    event: adaptV4Frame(candidate as never, { runId: "run-owner", streamIncarnation, generation }),
    messageId: "run-owner",
    ctx,
    binding: {
      sessionId: "session-1",
      runId: "run-owner",
      streamVersion: 0,
      streamIncarnation,
      generation,
    },
    currentGeneration: generation,
    onCommitted: (semanticApplied) => {
      if (!semanticApplied) return;
      ctx.acceptedStreamCursorRef!.current = {
        sessionId: "session-1",
        runId: "run-owner",
        eventId: candidate.transportCursor,
        streamIncarnation,
      };
    },
  });

  assert.equal(accept(frame("message.started", "message-1", 1, 7), 7), true);
  assert.deepEqual(ctx.v4MessageOwnerRef.current, {
    sessionId: "session-1",
    runId: "run-owner",
    streamVersion: 0,
    streamIncarnation: 2,
    protocolMessageId: "message-1",
    reducerMessageId: "run-owner",
  });
  assert.equal(
    ctx.acceptedStreamCursorRef!.current.eventId,
    "run-owner:2:1-0",
  );
  assert.equal(accept(frame("message.delta", "message-1", 2, 8), 8), true);
  assert.equal(ctx.messages().length, 1);
  assert.equal(ctx.messages()[0]?.id, "run-owner");
  assert.equal(ctx.messages()[0]?.content, "accepted");
  assert.equal(
    ctx.acceptedStreamCursorRef!.current.eventId,
    "run-owner:2:2-0",
  );
  assert.equal(
    rebindV4MessageOwner(
      ctx.v4MessageOwnerRef,
      {
        sessionId: "session-1",
        runId: "run-owner",
        streamVersion: 0,
        streamIncarnation: 3,
      },
      "persisted-assistant",
    ),
    false,
  );
  assert.equal(
    rebindV4MessageOwner(
      ctx.v4MessageOwnerRef,
      {
        sessionId: "session-1",
        runId: "run-owner",
        streamVersion: 0,
        streamIncarnation: 2,
      },
      "persisted-assistant",
    ),
    true,
  );
  assert.equal(
    ctx.v4MessageOwnerRef.current?.reducerMessageId,
    "persisted-assistant",
  );

  ctx.processedEventIdsRef.current.add("event-2-3");
  const before = {
    messages: structuredClone(ctx.messages()),
    owner: structuredClone(ctx.v4MessageOwnerRef.current),
    cursor: structuredClone(ctx.acceptedStreamCursorRef!.current),
    sequence: structuredClone(ctx.acceptedRunEventSequenceRef!.current),
    eventIds: [...ctx.processedEventIdsRef.current],
  };
  assert.equal(accept(frame("message.started", "message-2", 3, 8), 8), false);
  assert.equal(accept(frame("message.delta", "message-1", 3, 8, 3), 8, 3), false);
  assert.deepEqual(ctx.messages(), before.messages);
  assert.deepEqual(ctx.v4MessageOwnerRef.current, before.owner);
  assert.deepEqual(ctx.acceptedStreamCursorRef!.current, before.cursor);
  assert.deepEqual(ctx.acceptedRunEventSequenceRef!.current, before.sequence);
  assert.deepEqual([...ctx.processedEventIdsRef.current], before.eventIds);
});

test("v4 stream.end is terminal-fenced and terminal recovery is exactly once", () => {
  const ctx = createContext([], null);
  ctx.currentRunIdRef.current = "run-1";
  ctx.v4TerminalFenceRef = { current: null };
  let terminalCalls = 0;
  let acceptTerminal: ((accepted: boolean) => void) | undefined;
  ctx.onRunTerminal = (_runId, _status, _messageId, onSettled) => {
    terminalCalls += 1;
    acceptTerminal = onSettled;
    return true;
  };
  const terminal = {
    eventHeader: "run.succeeded",
    transportCursor: "run-1:2:1-0",
    generation: 7,
    value: {
      schema: "ai-platform.public-run-stream-event.v4",
      event_id: "event-terminal",
      run_id: "run-1",
      message_id: null,
      seq: 1,
      event_type: "run.succeeded",
      stream_incarnation: 2,
      replayable: true,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-01-01T00:00:00Z",
      payload: { terminal_event_id: "terminal-1", hydrate_required: true },
    },
  } as const;
  const end = {
    eventHeader: "stream.end",
    transportCursor: "run-1:2:2-0",
    generation: 7,
    value: {
      schema: "ai-platform.public-run-stream-control.v4",
      event_id: "event-end",
      run_id: "run-1",
      message_id: null,
      seq: null,
      event_type: "stream.end",
      stream_incarnation: 2,
      replayable: true,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-01-01T00:00:01Z",
      payload: { terminal_event_id: "terminal-1" },
    },
  } as const;
  const binding = { sessionId: "session-1", runId: "run-1", streamVersion: 0, streamIncarnation: 2, generation: 7 } as const;
  const adapterBinding = { runId: "run-1", streamIncarnation: 2, generation: 7 } as const;
  assert.equal(handlePublicRunStreamEventV4({ event: adaptV4Frame(end, adapterBinding), messageId: "assistant-1", ctx, binding, currentGeneration: 7 }), false);
  const commits: boolean[] = [];
  assert.equal(handlePublicRunStreamEventV4({ event: adaptV4Frame(terminal, adapterBinding), messageId: "assistant-1", ctx, binding, currentGeneration: 7, onCommitted: (semanticApplied) => commits.push(semanticApplied) }), true);
  assert.deepEqual(commits, []);
  assert.equal(ctx.acceptedRunEventSequenceRef!.current.sequence, null);
  assert.equal(ctx.v4TerminalReservationsRef?.current.has("terminal-1"), true);
  assert.equal(handlePublicRunStreamEventV4({ event: adaptV4Frame(terminal, adapterBinding), messageId: "assistant-1", ctx, binding, currentGeneration: 7 }), false);
  assert.equal(handlePublicRunStreamEventV4({ event: adaptV4Frame(end, adapterBinding), messageId: "assistant-1", ctx, binding, currentGeneration: 7 }), false);
  acceptTerminal?.(true);
  assert.deepEqual(commits, [false]);
  assert.equal(ctx.acceptedRunEventSequenceRef!.current.sequence, 1);
  assert.equal(ctx.v4TerminalReservationsRef?.current.size, 0);
  const lateCommits: boolean[] = [];
  assert.equal(handlePublicRunStreamEventV4({ event: adaptV4Frame(terminal, adapterBinding), messageId: "assistant-1", ctx, binding, currentGeneration: 7, onCommitted: (semanticApplied) => lateCommits.push(semanticApplied) }), false);
  const higherSequenceTerminal = {
    ...terminal,
    transportCursor: "run-1:2:4-0",
    value: { ...terminal.value, event_id: "event-terminal-replay", seq: 2 },
  } as const;
  assert.equal(handlePublicRunStreamEventV4({ event: adaptV4Frame(higherSequenceTerminal, adapterBinding), messageId: "assistant-1", ctx, binding, currentGeneration: 7, onCommitted: (semanticApplied) => lateCommits.push(semanticApplied) }), false);
  assert.equal(handlePublicRunStreamEventV4({ event: adaptV4Frame(end, adapterBinding), messageId: "assistant-1", ctx, binding, currentGeneration: 7, onCommitted: (semanticApplied) => lateCommits.push(semanticApplied) }), true);
  const duplicateEnd = {
    ...end,
    transportCursor: "run-1:2:3-0",
    value: { ...end.value, event_id: "event-end-duplicate" },
  } as const;
  assert.equal(handlePublicRunStreamEventV4({ event: adaptV4Frame(duplicateEnd, adapterBinding), messageId: "assistant-1", ctx, binding, currentGeneration: 7, onCommitted: (semanticApplied) => lateCommits.push(semanticApplied) }), false);
  assert.deepEqual(lateCommits, [false, false, false, false]);
  assert.equal(terminalCalls, 1);
});

test("v4 terminal waits for committed text before sequence and cursor acceptance", () => {
  const ctx = createContext(
    [{
      id: "assistant-terminal-order",
      role: "assistant",
      content: "",
      timestamp: new Date(),
      parts: [],
      isStreaming: true,
    }],
    null,
  );
  ctx.currentRunIdRef.current = "run-terminal-order";
  ctx.v4TerminalFenceRef = { current: null };
  ctx.v4MessageOwnerRef = {
    current: {
      sessionId: "session-1",
      runId: "run-terminal-order",
      streamVersion: 0,
      streamIncarnation: 2,
      protocolMessageId: "message-terminal-order",
      reducerMessageId: "assistant-terminal-order",
    },
  };
  ctx.acceptedRunEventSequenceRef!.current = {
    sessionId: "session-1",
    runId: "run-terminal-order",
    sequence: 7,
  };
  ctx.acceptedStreamCursorRef!.current = {
    sessionId: "session-1",
    runId: "run-terminal-order",
    eventId: "run-terminal-order:2:0-0",
    streamIncarnation: 2,
  };
  let settleTerminal: ((accepted: boolean) => boolean) | undefined;
  ctx.onRunTerminal = (_runId, _status, _messageId, onSettled) => {
    settleTerminal = onSettled;
    return true;
  };
  const binding = {
    sessionId: "session-1",
    runId: "run-terminal-order",
    streamVersion: 0,
    streamIncarnation: 2,
    generation: 7,
  } as const;
  const adapterBinding = {
    runId: binding.runId,
    streamIncarnation: binding.streamIncarnation,
    generation: binding.generation,
  } as const;
  const delta = {
    eventHeader: "message.delta",
    transportCursor: "run-terminal-order:2:1-0",
    generation: 7,
    value: {
      schema: "ai-platform.public-run-stream-event.v4",
      event_id: "event-delta",
      run_id: "run-terminal-order",
      message_id: "message-terminal-order",
      seq: 8,
      event_type: "message.delta",
      stream_incarnation: 2,
      replayable: true,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-01-01T00:00:00Z",
      payload: { delta: "正文" },
    },
  } as const;
  const terminal = {
    eventHeader: "run.succeeded",
    transportCursor: "run-terminal-order:2:2-0",
    generation: 7,
    value: {
      schema: "ai-platform.public-run-stream-event.v4",
      event_id: "event-terminal",
      run_id: "run-terminal-order",
      message_id: null,
      seq: 30,
      event_type: "run.succeeded",
      stream_incarnation: 2,
      replayable: true,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-01-01T00:00:01Z",
      payload: { terminal_event_id: "terminal-order", hydrate_required: true },
    },
  } as const;

  assert.equal(handlePublicRunStreamEventV4({
    event: adaptV4Frame(delta, adapterBinding),
    messageId: "assistant-terminal-order",
    ctx,
    binding,
    currentGeneration: 7,
    onCommitted: () => {
      ctx.acceptedStreamCursorRef!.current.eventId = delta.transportCursor;
    },
  }), true);
  assert.equal(handlePublicRunStreamEventV4({
    event: adaptV4Frame(terminal, adapterBinding),
    messageId: "assistant-terminal-order",
    ctx,
    binding,
    currentGeneration: 7,
    onCommitted: () => {
      ctx.acceptedStreamCursorRef!.current.eventId = terminal.transportCursor;
    },
  }), true);

  assert.equal(ctx.messages()[0]?.content, "正文");
  assert.equal(ctx.acceptedRunEventSequenceRef!.current.sequence, 8);
  assert.equal(ctx.acceptedStreamCursorRef!.current.eventId, delta.transportCursor);
  settleTerminal?.(true);
  assert.equal(ctx.acceptedRunEventSequenceRef!.current.sequence, 30);
  assert.equal(ctx.acceptedStreamCursorRef!.current.eventId, terminal.transportCursor);

  const staleTerminal = {
    ...terminal,
    transportCursor: "run-terminal-order:2:3-0",
    value: {
      ...terminal.value,
      event_id: "event-terminal-stale",
      seq: 31,
      payload: { terminal_event_id: "terminal-stale", hydrate_required: true },
    },
  } as const;
  assert.equal(handlePublicRunStreamEventV4({
    event: adaptV4Frame(staleTerminal, adapterBinding),
    messageId: "assistant-terminal-order",
    ctx,
    binding,
    currentGeneration: 7,
    onCommitted: () => {
      ctx.acceptedStreamCursorRef!.current.eventId = staleTerminal.transportCursor;
    },
  }), true);
  ctx.currentRunIdRef.current = "run-replacement";
  assert.equal(settleTerminal?.(true), false);
  assert.equal(ctx.acceptedRunEventSequenceRef!.current.sequence, 30);
  assert.equal(ctx.acceptedStreamCursorRef!.current.eventId, terminal.transportCursor);
  assert.equal(ctx.v4TerminalReservationsRef!.current.size, 0);
});

test("v4 admission rejects an application frame from another incarnation before effects", () => {
  let dismissCalls = 0;
  const ctx = createContext([], null, () => {
    dismissCalls += 1;
  });
  ctx.currentRunIdRef.current = "run-authority";
  const frame = {
    eventHeader: "stream.open",
    transportCursor: "run-authority:3:1-0",
    generation: 4,
    value: {
      schema: "ai-platform.public-run-stream-control.v4",
      event_id: "open-incarnation-3",
      run_id: "run-authority",
      message_id: null,
      seq: null,
      event_type: "stream.open",
      stream_incarnation: 3,
      replayable: true,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-01-01T00:00:00Z",
      payload: {
        design_id: "ai-platform.redis-streams-sse-event-channel.v4",
      },
    },
  } as const;
  const event = adaptV4Frame(frame, {
    runId: "run-authority",
    streamIncarnation: 3,
    generation: 4,
  });

  const result = handlePublicRunStreamEventV4Result({
    event,
    messageId: "assistant-authority",
    ctx,
    binding: {
      sessionId: "session-1",
      runId: "run-authority",
      streamVersion: 0,
      streamIncarnation: 2,
      generation: 4,
    },
    currentGeneration: 4,
  });

  assert.deepEqual(result, { kind: "invalid" });
  assert.equal(dismissCalls, 0);
  assert.equal(ctx.processedEventIdsRef.current.size, 0);
  assert.equal(ctx.setMessagesCalls(), 0);
});

test("v4 admission retains the gap exception for a replacement incarnation", () => {
  const ctx = createContext([], null);
  ctx.currentRunIdRef.current = "run-authority";
  ctx.acceptedStreamCursorRef!.current = {
    sessionId: "session-1",
    runId: "run-authority",
    eventId: "run-authority:2:0-1",
    streamIncarnation: 2,
  };
  const frame = {
    eventHeader: "stream.gap",
    transportCursor: "run-authority:3:0-2",
    generation: 4,
    value: {
      schema: "ai-platform.public-run-stream-control.v4",
      event_id: "gap-incarnation-3",
      run_id: "run-authority",
      message_id: null,
      seq: null,
      event_type: "stream.gap",
      stream_incarnation: 3,
      replayable: false,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-01-01T00:00:00Z",
      payload: {
        reason: "stream_incarnation_mismatch",
        requested_event_id: "0-1",
        requested_stream_incarnation: 2,
        earliest_available_event_id: "0-2",
        latest_available_event_id: "0-3",
        current_stream_incarnation: 3,
        recovery: "reload_durable_state",
      },
    },
  } as const;
  let gapCalls = 0;

  const result = handlePublicRunStreamEventV4Result({
    event: adaptV4Frame(frame, {
      runId: "run-authority",
      streamIncarnation: 2,
      generation: 4,
    }),
    messageId: "assistant-authority",
    ctx,
    binding: {
      sessionId: "session-1",
      runId: "run-authority",
      streamVersion: 0,
      streamIncarnation: 2,
      generation: 4,
    },
    currentGeneration: 4,
    onGap: () => {
      gapCalls += 1;
    },
  });

  assert.deepEqual(result, { kind: "invalid" });
  assert.equal(gapCalls, 1);
  assert.equal(ctx.processedEventIdsRef.current.size, 0);
});

test("v4 frames fail closed without exact session, incarnation, and generation authority", () => {
  const ctx = createContext([], null);
  ctx.currentRunIdRef.current = "run-authority";
  const frame = {
    eventHeader: "stream.gap",
    transportCursor: "run-authority:3:1-0",
    generation: 4,
    value: {
      schema: "ai-platform.public-run-stream-control.v4",
      event_id: "authority-gap",
      run_id: "run-authority",
      message_id: null,
      seq: null,
      event_type: "stream.gap",
      stream_incarnation: 3,
      replayable: false,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-01-01T00:00:00Z",
      payload: {
        reason: "stream_missing",
        requested_event_id: "0-1",
        requested_stream_incarnation: 3,
        earliest_available_event_id: "0-2",
        latest_available_event_id: "0-3",
        current_stream_incarnation: 3,
        recovery: "reload_durable_state",
      },
    },
  } as const;
  ctx.acceptedStreamCursorRef!.current = {
    sessionId: "session-1",
    runId: "run-authority",
    eventId: "run-authority:3:0-1",
    streamIncarnation: 3,
  };
  let gapCalls = 0;
  const onGap = () => {
    gapCalls += 1;
  };
  const binding = {
    sessionId: "session-1",
    runId: "run-authority",
    streamVersion: 0,
    streamIncarnation: 3,
    generation: 4,
  } as const;
  const adapterBinding = {
    runId: "run-authority",
    streamIncarnation: 3,
    generation: 4,
  } as const;
  for (const invalidGeneration of [undefined, -1, 1.5, Infinity] as unknown[]) {
    assert.equal(handlePublicRunStreamEventV4({
      event: adaptV4Frame(frame, adapterBinding),
      messageId: "assistant-authority",
      ctx,
      binding,
      currentGeneration: invalidGeneration as number,
      onGap,
    }), false);
    assert.equal(gapCalls, 0);
  }
  assert.equal(handlePublicRunStreamEventV4({
    event: adaptV4Frame(frame, adapterBinding),
    messageId: "assistant-authority",
    ctx,
    binding,
    currentGeneration: 5,
    onGap,
  }), false);
  assert.equal(gapCalls, 0);
  assert.equal(handlePublicRunStreamEventV4({
    event: adaptV4Frame(frame, adapterBinding),
    messageId: "assistant-authority",
    ctx,
    binding,
    currentGeneration: 4,
    onGap,
  }), false);
  assert.equal(gapCalls, 1);
  for (const requested of [
    { eventId: "9-0", incarnation: 3 },
    { eventId: null, incarnation: null },
  ] as const) {
    assert.equal(handlePublicRunStreamEventV4({
      event: adaptV4Frame({
        ...frame,
        value: {
          ...frame.value,
          payload: {
            ...frame.value.payload,
            requested_event_id: requested.eventId,
            requested_stream_incarnation: requested.incarnation,
          },
        },
      }, adapterBinding),
      messageId: "assistant-authority",
      ctx,
      binding,
      currentGeneration: 4,
      onGap,
    }), false);
    assert.equal(gapCalls, 1);
  }
  assert.equal(handlePublicRunStreamEventV4({
    event: adaptV4Frame(frame, adapterBinding),
    messageId: "assistant-authority",
    ctx,
    binding: undefined as never,
    currentGeneration: 4,
  }), false);
  assert.equal(handlePublicRunStreamEventV4({
    event: adaptV4Frame(frame, { runId: "run-authority", streamIncarnation: 3 }),
    messageId: "assistant-authority",
    ctx,
    binding,
    currentGeneration: 4,
  }), false);
  assert.equal(handlePublicRunStreamEventV4({
    event: adaptV4Frame(frame, adapterBinding),
    messageId: "assistant-authority",
    ctx,
    binding: { ...binding, generation: 5 },
    currentGeneration: 4,
  }), false);
  assert.equal(handlePublicRunStreamEventV4({
    event: adaptV4Frame(frame, adapterBinding),
    messageId: "assistant-authority",
    ctx,
    binding: { ...binding, streamIncarnation: 2 },
    currentGeneration: 4,
  }), false);
  assert.equal(handlePublicRunStreamEventV4({
    event: adaptV4Frame({ ...frame, generation: undefined }, { runId: "run-authority", streamIncarnation: 3 }),
    messageId: "assistant-authority",
    ctx,
    binding,
    currentGeneration: 4,
  }), false);
});
