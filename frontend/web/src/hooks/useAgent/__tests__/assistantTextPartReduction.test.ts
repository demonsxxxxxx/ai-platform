import assert from "node:assert/strict";
import test from "node:test";
import type { MessagePart } from "../../../types";
import { processMessageEvent } from "../eventProcessor";
import {
  ASSISTANT_TEXT_PART_SCHEMA_VERSION,
  composeAssistantPreviewText,
  reduceAssistantTextPartEvent,
  selectAssistantCopyText,
} from "../../../types/assistantTextParts";

const messageId = "protocol-message-1";

function startedParts(): MessagePart[] {
  return processMessageEvent(
    "message.started",
    { event_id: "event-started", message_id: messageId, sequence: 1 },
    [],
    "",
    [],
    0,
    [],
    true,
    "assistant-1",
  ).parts;
}

function delta(parts: MessagePart[], partId: string, text: string, sequence: number) {
  return reduceAssistantTextPartEvent(
    "message.part.delta",
    {
      schema_version: ASSISTANT_TEXT_PART_SCHEMA_VERSION,
      part_id: partId,
      delta: text,
      event_id: `event-${sequence}`,
      message_id: messageId,
      sequence,
    },
    parts,
  );
}

function classify(parts: MessagePart[], partId: string, role: "answer" | "work") {
  return reduceAssistantTextPartEvent(
    "message.part.classified",
    {
      schema_version: ASSISTANT_TEXT_PART_SCHEMA_VERSION,
      part_id: partId,
      role,
    },
    parts,
  );
}

test("source deltas upsert exact stable parts and join provider messages with a blank line", () => {
  const first = delta(startedParts(), "part_0123456789abcdef0123456789abcdef", "First", 2);
  assert.equal(first.accepted, true);
  assert.equal(first.content, "First");
  const second = delta(first.parts, "part_fedcba9876543210fedcba9876543210", "Second", 3);
  assert.equal(second.accepted, true);
  const firstSuffix = delta(second.parts, "part_0123456789abcdef0123456789abcdef", " source", 4);

  assert.equal(firstSuffix.accepted, true);
  assert.equal(firstSuffix.parts.length, 3);
  assert.deepEqual(
    firstSuffix.parts.filter((part) => part.type === "text").map((part) => part.type === "text" ? [part.public_part_id, part.logical_id, part.text_role, part.content] : []),
    [
      ["part_0123456789abcdef0123456789abcdef", "part_0123456789abcdef0123456789abcdef", "pending", "First source"],
      ["part_fedcba9876543210fedcba9876543210", "part_fedcba9876543210fedcba9876543210", "pending", "Second"],
    ],
  );
  assert.equal(firstSuffix.content, "First source\n\nSecond");
  assert.equal(
    composeAssistantPreviewText(firstSuffix.parts),
    "First source\n\nSecond",
  );
});

test("classification targets one source, keeps pending visible, and excludes work from copy", () => {
  const pending = delta(startedParts(), "part_pending", "Preview", 2);
  assert.equal(pending.content, "Preview");
  assert.equal(selectAssistantCopyText(pending.parts), "");

  const work = classify(pending.parts, "part_pending", "work");
  assert.equal(work.accepted, true);
  assert.equal(work.content, "");
  const contradictory = classify(work.parts, "part_pending", "answer");
  assert.equal(contradictory.accepted, false);
  assert.equal(contradictory.parts, work.parts);

  const nextSource = delta(work.parts, "part_answer", "Final answer", 3);
  const answer = classify(nextSource.parts, "part_answer", "answer");
  assert.equal(answer.accepted, true);
  assert.equal(answer.content, "Final answer");
  assert.equal(selectAssistantCopyText(answer.parts), "Final answer");
  assert.equal(
    (answer.parts.find((part) => part.type === "text" && part.public_part_id === "part_pending") as Extract<MessagePart, { type: "text" }>).text_role,
    "work",
  );

  const reclassifiedAsWork = classify(answer.parts, "part_answer", "work");
  assert.equal(reclassifiedAsWork.accepted, true);
  assert.equal(reclassifiedAsWork.content, "");
  assert.equal(selectAssistantCopyText(reclassifiedAsWork.parts), "");
  assert.equal(
    classify(reclassifiedAsWork.parts, "part_answer", "answer").accepted,
    false,
  );
});

test("orphan, malformed, unknown, legacy-mixed, and post-completion mutations are rejected", () => {
  const started = startedParts();
  const orphan = classify(started, "part_missing", "answer");
  assert.equal(orphan.accepted, false);

  for (const [eventType, data] of [
    ["message.part.delta", { schema_version: "wrong", part_id: "part-a", delta: "x" }],
    ["message.part.delta", { schema_version: ASSISTANT_TEXT_PART_SCHEMA_VERSION, part_id: "bad/ref", delta: "x" }],
    ["message.part.delta", { schema_version: ASSISTANT_TEXT_PART_SCHEMA_VERSION, part_id: "part-a", delta: "" }],
    ["message.part.unknown", { schema_version: ASSISTANT_TEXT_PART_SCHEMA_VERSION, part_id: "part-a", delta: "x" }],
  ] as const) {
    assert.equal(reduceAssistantTextPartEvent(eventType, data, started).accepted, false);
  }

  const legacy = processMessageEvent(
    "message:chunk",
    { content: "legacy" },
    started,
    "",
    [],
    0,
    [],
    true,
    "assistant-1",
  );
  assert.equal(legacy.accepted, undefined);
  assert.equal(
    delta(legacy.parts, "part_mixed", "new", 3).accepted,
    false,
  );

  const withText = delta(started, "part_closed", "public", 2);
  const completed = processMessageEvent(
    "message.completed",
    { event_id: "event-completed", message_id: messageId, sequence: 3 },
    withText.parts,
    withText.content,
    [],
    0,
    [],
    false,
    "assistant-1",
  );
  assert.equal(
    delta(completed.parts, "part_closed", " late", 4).accepted,
    false,
  );
});

test("copy keeps legacy unmarked text and drops pending and work sources", () => {
  const parts: MessagePart[] = [
    { type: "text", content: "Old answer." },
    {
      type: "text",
      content: "Visible preview.",
      logical_id: "part-pending",
      public_part_id: "part-pending",
      text_role: "pending",
    },
    {
      type: "text",
      content: "Private work narration.",
      logical_id: "part-work",
      public_part_id: "part-work",
      text_role: "work",
    },
    {
      type: "text",
      content: "New answer.",
      logical_id: "part-answer",
      public_part_id: "part-answer",
      text_role: "answer",
    },
  ];
  assert.equal(selectAssistantCopyText(parts), "Old answer.\n\nNew answer.");
  assert.equal(composeAssistantPreviewText(parts), "Old answer.\n\nVisible preview.\n\nNew answer.");
});
