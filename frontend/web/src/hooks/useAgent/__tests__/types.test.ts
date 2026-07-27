import assert from "node:assert/strict";
import test from "node:test";
import {
  PUBLIC_EXECUTION_EVENT_SCHEMA_VERSION,
  isPublicExecutionEvent,
  type EventData,
} from "../types.ts";

const validEvent = (): EventData => ({
  schema_version: PUBLIC_EXECUTION_EVENT_SCHEMA_VERSION,
  event_id: "evt_public_1",
  sequence: 7,
  run_id: "run_public_1",
  step_id: "pex_public_1",
  kind: "processing",
  stage: "execution",
  status: "running",
  title: "Process request",
  summary: "Running controlled processing",
  progress: { current: 1, total: 3 },
  safe_file_name: null,
  artifact_public_id: null,
  created_at: "2026-07-27T00:00:00Z",
});

test("accepts only the exact public execution schema and matching lifecycle", () => {
  assert.equal(isPublicExecutionEvent("execution_progress", validEvent()), true);
  assert.equal(
    isPublicExecutionEvent("execution_step_completed", {
      ...validEvent(),
      status: "completed",
      progress: { current: 3, total: 3 },
    }),
    true,
  );
  assert.equal(isPublicExecutionEvent("execution_step_completed", validEvent()), false);
});

test("rejects raw tool fields, partial payloads, and unsafe identifiers", () => {
  assert.equal(
    isPublicExecutionEvent("execution_progress", {
      ...validEvent(),
      args: { command: "private" },
    }),
    false,
  );

  const partial = validEvent();
  delete partial.summary;
  assert.equal(isPublicExecutionEvent("execution_progress", partial), false);
  assert.equal(
    isPublicExecutionEvent("execution_progress", {
      ...validEvent(),
      step_id: "C:/private/workspace",
    }),
    false,
  );
});
