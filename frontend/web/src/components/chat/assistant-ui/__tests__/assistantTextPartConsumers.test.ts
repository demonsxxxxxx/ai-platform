import assert from "node:assert/strict";
import test from "node:test";
import type { Message } from "../../../../types";
import { extractMessageOutline } from "../../../layout/AppContent/messageOutline";
import { buildTaskNotificationCopy } from "../../../layout/AppContent/taskNotificationContent";
import { toAssistantUiMessage } from "../externalStoreRuntime";

const timestamp = new Date("2026-10-09T00:00:00Z");

function part(
  partId: string,
  role: "pending" | "answer" | "work",
  content: string,
) {
  return {
    type: "text" as const,
    content,
    logical_id: partId,
    public_part_id: partId,
    text_role: role,
  };
}

test("assistant-ui text projection omits work while retaining visible preview and answer sources", () => {
  const converted = toAssistantUiMessage({
    id: "assistant-ui-message",
    role: "assistant",
    runId: "run-ui",
    content: "Preview\n\nAnswer",
    timestamp,
    parts: [
      part("part-work", "work", "Work narration"),
      part("part-pending", "pending", "Preview"),
      part("part-answer-a", "answer", "Answer A"),
      part("part-answer-b", "answer", "Answer B"),
    ],
  });
  assert.deepEqual(converted.content, [
    { type: "text", text: "Preview" },
    { type: "text", text: "\n\nAnswer A" },
    { type: "text", text: "\n\nAnswer B" },
  ]);
  assert.doesNotMatch(JSON.stringify(converted), /Work narration/);
});

test("outline headings include preview and answer text but ignore work text", () => {
  const message: Message = {
    id: "outline-message",
    role: "assistant",
    content: "# Preview\n\n# Answer",
    timestamp,
    parts: [
      part("part-work", "work", "# Internal work"),
      part("part-pending", "pending", "# Preview"),
      part("part-answer", "answer", "# Answer"),
    ],
  };
  assert.deepEqual(
    extractMessageOutline([message])
      .filter((item) => item.kind === "assistant-heading")
      .map((item) => item.label),
    ["Preview", "Answer"],
  );
});

test("successful task notification summarizes only classified answer sources", () => {
  const runId = "run-notification";
  const protocolMessageId = "protocol-notification";
  const streamIncarnation = 2;
  const event = (
    id: string,
    eventType: string,
    sequence: number,
    payload: Record<string, unknown>,
  ) => ({
    id,
    event_type: eventType,
    timestamp: `2026-10-09T00:00:0${sequence}Z`,
    run_id: runId,
    sequence,
    data: {
      event_id: id,
      run_id: runId,
      message_id: protocolMessageId,
      sequence,
      stream_incarnation: streamIncarnation,
      event_type: eventType,
      payload,
    },
  });
  const events = [
    event("started", "message.started", 1, {}),
    event("work-delta", "message.part.delta", 2, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part-notification-work",
      delta: "Work details",
    }),
    event("work-role", "message.part.classified", 3, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part-notification-work",
      role: "work",
    }),
    event("answer-delta", "message.part.delta", 4, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part-notification-answer",
      delta: "Final result",
    }),
    event("answer-role", "message.part.classified", 5, {
      schema_version: "ai-platform.assistant-text-part.v1",
      part_id: "part-notification-answer",
      role: "answer",
    }),
  ];
  const notification = buildTaskNotificationCopy({
    events,
    failureLabel: "Failed",
    successLabel: "Completed",
    status: "completed",
  });
  assert.equal(notification.body, "Final result");
  assert.doesNotMatch(notification.body, /Work details/);
});
