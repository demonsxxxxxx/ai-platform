import assert from "node:assert/strict";
import test from "node:test";
import type { Message } from "../../../../types";
import {
  handlePublicRunStreamEventV4Result,
  type EventHandlerContext,
} from "../../../../hooks/useAgent/eventHandlers";
import { processMessageEvent } from "../../../../hooks/useAgent/eventProcessor";
import {
  ASSISTANT_TEXT_PART_SCHEMA_VERSION,
} from "../../../../types/assistantTextParts";
import { adaptPublicRunStreamEventV4 } from "../publicEventAdapter";

const runId = "run-1";
const protocolMessageId = "protocol-message-1";
const assistantMessageId = "assistant-message-1";
const binding = {
  sessionId: "session-1",
  runId,
  streamVersion: 0,
  streamIncarnation: 2,
  generation: 3,
};

function frame(
  eventType: string,
  payload: Record<string, unknown>,
  sequence: number,
  messageId = protocolMessageId,
) {
  return {
    eventHeader: eventType,
    transportCursor: `${runId}:2:${sequence}-0`,
    generation: binding.generation,
    value: {
      schema: "ai-platform.public-run-stream-event.v4",
      event_id: `event-${sequence}`,
      run_id: runId,
      message_id: messageId,
      seq: sequence,
      event_type: eventType,
      stream_incarnation: binding.streamIncarnation,
      replayable: true,
      trace_ref: null,
      causation_event_id: null,
      emitted_at: "2026-10-09T00:00:00Z",
      payload,
    },
  };
}

function partPayload(partId: string, delta: string) {
  return {
    schema_version: ASSISTANT_TEXT_PART_SCHEMA_VERSION,
    part_id: partId,
    delta,
  };
}

function classifyPayload(partId: string, role: "answer" | "work") {
  return {
    schema_version: ASSISTANT_TEXT_PART_SCHEMA_VERSION,
    part_id: partId,
    role,
  };
}

function createContext() {
  const startParts = processMessageEvent(
    "message.started",
    {
      event_id: "event-started",
      message_id: protocolMessageId,
      sequence: 1,
    },
    [],
    "",
    [],
    0,
    [],
    true,
    assistantMessageId,
  ).parts;
  const initialMessage: Message = {
    id: assistantMessageId,
    role: "assistant",
    runId,
    content: "",
    timestamp: new Date("2026-10-09T00:00:00Z"),
    parts: startParts,
    isStreaming: true,
  };
  const messagesRef = { current: [initialMessage] };
  const queuedReactSnapshots: Message[][] = [];
  const acceptedStreamCursorRef = {
    current: {
      sessionId: "session-1",
      runId,
      eventId: null as string | null,
      streamIncarnation: binding.streamIncarnation,
    },
  };
  const acceptedRunEventSequenceRef = {
    current: {
      sessionId: "session-1",
      runId,
      sequence: 1 as number | null,
    },
  };
  const ctx = {
    sessionIdRef: { current: "session-1" },
    currentRunIdRef: { current: runId },
    processedEventIdsRef: { current: new Set<string>() },
    acceptedRunEventSequenceRef,
    acceptedStreamCursorRef,
    v4MessageOwnerRef: {
      current: {
        sessionId: "session-1",
        runId,
        streamVersion: 0,
        streamIncarnation: binding.streamIncarnation,
        protocolMessageId,
        reducerMessageId: assistantMessageId,
      },
    },
    v4MessageCandidateRef: { current: null },
    lastHistoryTimestampRef: { current: null },
    activeSubagentStackRef: { current: [] },
    streamVersionRef: { current: 0 },
    messagesRef,
    setMessages: (next: Message[]) => queuedReactSnapshots.push(next),
    setSessionId: () => undefined,
    setConnectionStatus: () => undefined,
    setIsInitializingSandbox: () => undefined,
    setSandboxError: () => undefined,
  } as unknown as EventHandlerContext;

  return { ctx, messagesRef, queuedReactSnapshots, acceptedStreamCursorRef, acceptedRunEventSequenceRef };
}

function applyFrame(
  ctx: EventHandlerContext,
  acceptedStreamCursorRef: ReturnType<typeof createContext>["acceptedStreamCursorRef"],
  eventType: string,
  payload: Record<string, unknown>,
  sequence: number,
) {
  const adapted = adaptPublicRunStreamEventV4(frame(eventType, payload, sequence), {
    runId,
    streamIncarnation: binding.streamIncarnation,
    generation: binding.generation,
  });
  assert.ok(adapted);
  return handlePublicRunStreamEventV4Result({
    event: adapted,
    messageId: assistantMessageId,
    ctx,
    binding,
    currentGeneration: binding.generation,
    onCommitted: (semanticApplied) => {
      if (!semanticApplied) return;
      acceptedStreamCursorRef.current = {
        sessionId: binding.sessionId,
        runId,
        eventId: adapted.transportCursor,
        streamIncarnation: binding.streamIncarnation,
      };
    },
  });
}

test("v4 adapter accepts only the closed assistant text-part event payloads", () => {
  const valid = adaptPublicRunStreamEventV4(
    frame("message.part.delta", partPayload("part_0123456789abcdef0123456789abcdef", "safe text"), 2),
    { runId, streamIncarnation: binding.streamIncarnation, generation: binding.generation },
  );
  assert.equal(valid?.eventType, "message.part.delta");
  assert.equal(valid?.messageId, protocolMessageId);

  const invalidFrames = [
    frame("message.part.delta", { ...partPayload("part-1", "text"), extra: true }, 3),
    frame("message.part.delta", { ...partPayload("part-1", "" ) }, 4),
    frame("message.part.delta", partPayload("bad/ref", "text"), 5),
    frame("message.part.delta", { ...partPayload("part-1", "text"), schema_version: "wrong" }, 6),
    frame("message.part.classified", classifyPayload("part-1", "pending" as "answer"), 7),
    frame("message.part.unknown", partPayload("part-1", "text"), 8),
  ];
  for (const invalid of invalidFrames) {
    assert.equal(
      adaptPublicRunStreamEventV4(invalid, {
        runId,
        streamIncarnation: binding.streamIncarnation,
        generation: binding.generation,
      }),
      null,
    );
  }
});

test("replayed updates use the immediate message snapshot and invalid parts do not advance the cursor", () => {
  const {
    ctx,
    messagesRef,
    queuedReactSnapshots,
    acceptedStreamCursorRef,
    acceptedRunEventSequenceRef,
  } = createContext();

  const delta = applyFrame(
    ctx,
    acceptedStreamCursorRef,
    "message.part.delta",
    partPayload("part_answer", "A preview"),
    2,
  );
  assert.equal(delta.kind, "applied");
  assert.equal(queuedReactSnapshots.length, 1);
  assert.equal(queuedReactSnapshots[0]?.[0]?.content, "A preview");

  const classified = applyFrame(
    ctx,
    acceptedStreamCursorRef,
    "message.part.classified",
    classifyPayload("part_answer", "answer"),
    3,
  );
  assert.equal(classified.kind, "applied");
  assert.equal(queuedReactSnapshots.length, 2);
  const immediatePart = messagesRef.current[0]?.parts?.find(
    (part) => part.type === "text" && part.public_part_id === "part_answer",
  );
  assert.equal(immediatePart?.type, "text");
  assert.equal(immediatePart?.text_role, "answer");
  assert.equal(acceptedRunEventSequenceRef.current.sequence, 3);
  assert.equal(acceptedStreamCursorRef.current.eventId, `${runId}:2:3-0`);

  const invalid = applyFrame(
    ctx,
    acceptedStreamCursorRef,
    "message.part.classified",
    classifyPayload("part_foreign", "answer"),
    4,
  );
  assert.equal(invalid.kind, "invalid");
  assert.equal(acceptedRunEventSequenceRef.current.sequence, 3);
  assert.equal(acceptedStreamCursorRef.current.eventId, `${runId}:2:3-0`);
});

test("part ids already owned by another message are rejected before commit", () => {
  const { ctx, messagesRef, acceptedStreamCursorRef } = createContext();
  messagesRef.current.push({
    id: "assistant-message-2",
    role: "assistant",
    runId: "run-older",
    content: "",
    timestamp: new Date("2026-10-08T00:00:00Z"),
    parts: [{
      type: "text",
      content: "Other source",
      logical_id: "part_foreign",
      public_part_id: "part_foreign",
      text_role: "answer",
    }],
  });

  const invalid = applyFrame(
    ctx,
    acceptedStreamCursorRef,
    "message.part.classified",
    classifyPayload("part_foreign", "answer"),
    2,
  );
  assert.equal(invalid.kind, "invalid");
  assert.equal(acceptedStreamCursorRef.current.eventId, null);
});
