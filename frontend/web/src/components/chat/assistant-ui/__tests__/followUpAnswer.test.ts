import assert from "node:assert/strict";
import test from "node:test";
import { followUpOwnerKey, settledFollowUpAnswer } from "../followUpAnswer";
import type { Message } from "../../../../types";

const answer: Message = {
  id: "message-answer",
  role: "assistant",
  content: "已整理任务清单。",
  timestamp: new Date(0),
  runId: "run-answer",
};

function terminal(eventType: string, message: Message = answer): Message {
  return {
    ...message,
    parts: [
      { type: "run_status", event_id: `event-${eventType}`, event_type: eventType, severity: "error", stage: "terminal", message: "" },
    ],
  };
}

test("only a settled successful last answer owns follow-up suggestions", () => {
  assert.equal(settledFollowUpAnswer([{ ...answer, role: "user", runId: "run-user" }]), null);
  assert.equal(settledFollowUpAnswer([{ ...answer, isStreaming: true }]), null);
  assert.equal(settledFollowUpAnswer([{ ...answer, isSynchronizing: true }]), null);
  assert.equal(settledFollowUpAnswer([{ ...answer, cancelled: true }]), null);
  assert.equal(settledFollowUpAnswer([answer])?.runId, "run-answer");
  // A later user turn means the previous answer is no longer the current one.
  assert.equal(
    settledFollowUpAnswer([answer, { ...answer, id: "user-next", role: "user", runId: "run-next", content: "再改一下" }]),
    null,
  );
});

test("terminal failure, cancellation and unavailable results suppress suggestions", () => {
  for (const eventType of [
    "run_failed",
    "run_timeout",
    "run_cancelled",
    "run_budget_exhausted",
    "input_context_too_large",
    "terminal_reconciliation_failed",
    "terminal_result_unavailable",
  ]) {
    assert.equal(settledFollowUpAnswer([terminal(eventType)]), null, eventType);
  }
});

test("benign public activity parts do not disqualify a completed answer", () => {
  const withActivity: Message = {
    ...answer,
    parts: [
      { type: "run_status", event_id: "event-progress", event_type: "run_progress", severity: "info", stage: "public_activity", message: "" },
      { type: "run_status", event_id: "event-started", event_type: "run_started", severity: "info", stage: "public_activity", message: "" },
      { type: "run_status", event_id: "event-ok", event_type: "run_succeeded", severity: "info", stage: "terminal", message: "" },
    ],
  };

  assert.equal(settledFollowUpAnswer([withActivity])?.runId, "run-answer");
});

test("owner key separates accounts, sessions and answers without concatenation ambiguity", () => {
  const first = followUpOwnerKey("tenant-a:user-a", "session-a", answer);
  assert.ok(first);
  assert.notEqual(first, followUpOwnerKey("tenant-b:user-a", "session-a", answer));
  assert.notEqual(first, followUpOwnerKey("tenant-a:user-b", "session-a", answer));
  assert.notEqual(first, followUpOwnerKey("tenant-a:user-a", "session-b", answer));
  assert.notEqual(first, followUpOwnerKey("tenant-a:user-a", "session-a", { ...answer, runId: "run-b" }));
  assert.equal(followUpOwnerKey("", "session-a", answer), null);
  assert.equal(followUpOwnerKey("tenant-a:user-a", null, answer), null);
  assert.equal(followUpOwnerKey("tenant-a:user-a", "session-a", null), null);
});
