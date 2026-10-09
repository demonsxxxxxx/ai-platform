import assert from "node:assert/strict";
import test from "node:test";
import type { Message } from "../../../types";
import type { HistoryEvent } from "../types";
import {
  InvalidAssistantTextPartHistoryError,
  mergeHydratedRunSegment,
  reconstructMessagesFromEvents,
} from "../historyLoader";
import { selectAssistantCopyText } from "../../../types/assistantTextParts";

const runId = "run-history";
const protocolMessageId = "protocol-history";
const streamIncarnation = 4;

function partEvent(
  eventType: string,
  eventId: string,
  sequence: number,
  payload: Record<string, unknown>,
): HistoryEvent {
  return {
    id: `row-${eventId}`,
    event_type: eventType,
    run_id: runId,
    sequence,
    timestamp: `2026-10-09T00:00:${String(sequence).padStart(2, "0")}Z`,
    data: {
      event_id: eventId,
      run_id: runId,
      message_id: protocolMessageId,
      sequence,
      stream_incarnation: streamIncarnation,
      event_type: eventType,
      payload,
    },
  };
}

function baseEvents(): HistoryEvent[] {
  return [
    {
      id: "row-started",
      event_type: "message.started",
      run_id: runId,
      sequence: 1,
      timestamp: "2026-10-09T00:00:01Z",
      data: {
        event_id: "event-started",
        run_id: runId,
        message_id: protocolMessageId,
        sequence: 1,
        stream_incarnation: streamIncarnation,
        event_type: "message.started",
        payload: {},
      },
    },
  ];
}

function reconstruct(events: HistoryEvent[]) {
  const processedEventIds = new Set<string>();
  const messages = reconstructMessagesFromEvents(events, processedEventIds, {
    activeSubagentStack: [],
  });
  return { messages, processedEventIds };
}

test("history applies part events in sequence, preserves IDs and roles, and closes after completion", () => {
  const events = [
    ...baseEvents(),
    partEvent("message.part.delta", "event-a-1", 2, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part_source_a",
      delta: "Work detail",
    }),
    partEvent("message.part.delta", "event-b-1", 3, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part_source_b",
      delta: "Answer",
    }),
    partEvent("message.part.classified", "event-a-role", 4, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part_source_a",
      role: "work",
    }),
    partEvent("message.part.classified", "event-b-role", 5, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part_source_b",
      role: "answer",
    }),
    partEvent("message.part.delta", "event-b-2", 6, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part_source_b",
      delta: " part",
    }),
    {
      ...partEvent("message.completed", "event-completed", 8, {
        delta_count: 1,
        text_length: 11,
      }),
    },
  ];
  const { messages, processedEventIds } = reconstruct(events.reverse());
  const assistant = messages.find((message) => message.role === "assistant");
  assert.ok(assistant);
  assert.equal(assistant.content, "Answer part");
  assert.deepEqual(
    assistant.parts?.filter((part) => part.type === "text").map((part) =>
      part.type === "text"
        ? [part.public_part_id, part.logical_id, part.text_role, part.content]
        : [],
    ),
    [
      ["part_source_a", "part_source_a", "work", "Work detail"],
      ["part_source_b", "part_source_b", "answer", "Answer part"],
    ],
  );
  assert.equal(selectAssistantCopyText(assistant.parts), "Answer part");
  assert.equal(processedEventIds.has("event-b-2"), true);
  assert.equal(processedEventIds.has("event-completed"), true);
});

test("partial history keeps an unclassified public preview visible but out of copy", () => {
  const { messages, processedEventIds } = reconstruct([
    ...baseEvents(),
    partEvent("message.part.delta", "event-partial", 2, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part_partial",
      delta: "Still being classified",
    }),
  ]);
  const assistant = messages.find((message) => message.role === "assistant");
  const partial = assistant?.parts?.find(
    (part) => part.type === "text" && part.public_part_id === "part_partial",
  );
  assert.equal(assistant?.content, "Still being classified");
  assert.equal(partial?.type === "text" ? partial.text_role : null, "pending");
  assert.equal(selectAssistantCopyText(assistant?.parts), "");
  assert.equal(processedEventIds.has("event-partial"), true);
});

test("malformed, foreign, orphan, contradictory, and post-terminal part history fails atomically", () => {
  const wrongOwner = partEvent("message.part.classified", "event-wrong-message", 4, {
    schema_version: "ai-platform.assistant-text-part.v1",
    part_id: "part_valid",
    role: "answer",
  });
  wrongOwner.data = {
    ...(wrongOwner.data as Record<string, unknown>),
    message_id: "protocol-foreign",
  };
  const malformedEvents = [
    ...baseEvents(),
    partEvent("message.part.delta", "event-good", 2, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part_valid",
      delta: "Visible",
    }),
    partEvent("message.part.delta", "event-extra", 3, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part_invalid",
      delta: "Should not show",
      secret: true,
    }),
  ];
  const orphanEvents = [
    ...baseEvents(),
    partEvent("message.part.classified", "event-orphan", 2, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part_missing",
      role: "answer",
    }),
  ];
  const contradictoryEvents = [
    ...baseEvents(),
    partEvent("message.part.delta", "event-work-delta", 2, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part_contradictory",
      delta: "Work",
    }),
    partEvent("message.part.classified", "event-work-role", 3, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part_contradictory",
      role: "work",
    }),
    partEvent("message.part.classified", "event-work-to-answer", 4, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part_contradictory",
      role: "answer",
    }),
  ];
  const terminalEvents = [
    ...baseEvents(),
    partEvent("message.part.delta", "event-terminal-delta", 2, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part_terminal",
      delta: "Before completion",
    }),
    partEvent("message.completed", "event-terminal-completed", 3, {}),
    partEvent("message.part.delta", "event-after-terminal", 4, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part_terminal",
      delta: "After completion",
    }),
  ];

  for (const events of [malformedEvents, [...baseEvents(), wrongOwner], orphanEvents, contradictoryEvents, terminalEvents]) {
    const processedEventIds = new Set(["prior-event"]);
    assert.throws(
      () =>
        reconstructMessagesFromEvents(events, processedEventIds, {
          activeSubagentStack: [],
        }),
      InvalidAssistantTextPartHistoryError,
    );
    assert.deepEqual([...processedEventIds], ["prior-event"]);
  }
});

test("history keeps legacy message delta and commentary projections readable", () => {
  const events: HistoryEvent[] = [
    ...baseEvents(),
    {
      id: "row-legacy-delta",
      event_type: "message.delta",
      run_id: runId,
      sequence: 2,
      timestamp: "2026-10-09T00:00:02Z",
      data: {
        event_id: "event-legacy-delta",
        run_id: runId,
        message_id: protocolMessageId,
        sequence: 2,
        stream_incarnation: streamIncarnation,
        payload: { delta: "Legacy answer" },
      },
    },
    {
      id: "row-legacy-commentary",
      event_type: "commentary.delta",
      run_id: runId,
      sequence: 3,
      timestamp: "2026-10-09T00:00:03Z",
      data: {
        event_id: "event-legacy-commentary",
        run_id: runId,
        message_id: protocolMessageId,
        sequence: 3,
        stream_incarnation: streamIncarnation,
        payload: { summary_id: "summary-legacy", delta: "Visible commentary" },
      },
    },
  ];
  const { messages } = reconstruct(events);
  const assistant = messages.find((message) => message.role === "assistant");
  assert.equal(assistant?.content, "Legacy answer");
  assert.equal(
    assistant?.parts?.some(
      (part) => part.type === "summary" && part.content === "Visible commentary",
    ),
    true,
  );
});

test("flattened backend history rows retain public part identity and exact source grouping", () => {
  const flattened = partEvent(
    "message.part.delta",
    "event-flat-part",
    2,
    {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part_flat",
      delta: "Flattened answer",
    },
  );
  flattened.data = {
    event_id: "event-flat-part",
    run_id: runId,
    message_id: protocolMessageId,
    sequence: 2,
    stream_incarnation: streamIncarnation,
    event_type: "message.part.delta",
    schema_version: "ai-platform.assistant-text-part.v1",
    part_id: "part_flat",
    delta: "Flattened answer",
  };
  const { messages } = reconstruct([...baseEvents(), flattened]);
  const assistant = messages.find((message) => message.role === "assistant");
  assert.equal(assistant?.content, "Flattened answer");
  assert.equal(
    assistant?.parts?.some(
      (part) => part.type === "text" && part.public_part_id === "part_flat",
    ),
    true,
  );
});

test("failed partial hydration trusts same source content and role and keeps only missing safe sources", () => {
  const timestamp = new Date("2026-10-09T00:00:00Z");
  const live: Message = {
    id: "assistant-live",
    runId,
    role: "assistant",
    timestamp,
    content: "Tool preamble\n\nMissing answer",
    parts: [
      {
        type: "text",
        content: "Tool preamble",
        logical_id: "part_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        public_part_id: "part_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        text_role: "answer",
      },
      {
        type: "text",
        content: "Missing answer",
        logical_id: "part_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        public_part_id: "part_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        text_role: "answer",
      },
      {
        type: "artifact",
        artifact_id: "live-artifact",
        artifact_type: "report",
        label: "Live report",
        content_type: "text/plain",
        size_bytes: 1,
      },
    ],
    attachments: [
      {
        id: "live-attachment",
        key: "attachment-key",
        name: "report.txt",
        type: "document",
        mimeType: "text/plain",
        size: 1,
      },
    ],
  };
  const hydrated: Message = {
    id: "assistant-hydrated",
    runId,
    role: "assistant",
    timestamp,
    content: "",
    parts: [
      {
        type: "text",
        content: "Tool preamble with suffix",
        logical_id: "part_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        public_part_id: "part_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        text_role: "work",
      },
      {
        type: "run_status",
        event_id: "failure",
        event_type: "run_failed",
        stage: "agent",
        message: "Failed",
        severity: "error",
      },
    ],
  };

  const merged = mergeHydratedRunSegment([live], [hydrated], runId);
  const assistant = merged.find((message) => message.role === "assistant");
  const textParts = assistant?.parts?.filter((part) => part.type === "text") || [];
  assert.deepEqual(
    textParts.map((part) =>
      part.type === "text"
        ? [part.public_part_id, part.text_role, part.content]
        : [],
    ),
    [
      ["part_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "work", "Tool preamble with suffix"],
      ["part_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "answer", "Missing answer"],
    ],
  );
  assert.equal(assistant?.content, "Missing answer");
  assert.equal(selectAssistantCopyText(assistant?.parts), "Missing answer");
  assert.equal(
    assistant?.parts?.some(
      (part) => part.type === "artifact" && part.artifact_id === "live-artifact",
    ),
    true,
  );
  assert.deepEqual(assistant?.attachments?.map((attachment) => attachment.id), [
    "live-attachment",
  ]);
});
