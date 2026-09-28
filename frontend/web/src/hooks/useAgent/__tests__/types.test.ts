import assert from "node:assert/strict";
import test from "node:test";
import {
  PUBLIC_EXECUTION_EVENT_V2_SCHEMA_VERSION,
  isPublicExecutionEvent,
  type EventData,
} from "../types.ts";

const validEvent = (): EventData => ({
  schema_version: PUBLIC_EXECUTION_EVENT_V2_SCHEMA_VERSION,
  event_id: "evt_public_1",
  sequence: 7,
  run_id: "run_public_1",
  step_id: "pex_public_1",
  presentation_kind: "write",
  kind: "generation",
  stage: "edit",
  status: "running",
  progress: { current: 0, total: 1 },
  safe_label: "Updating authorized files",
  created_at: "2026-07-27T00:00:00Z",
});

test("accepts only the exact public execution schema and matching lifecycle", () => {
  assert.equal(isPublicExecutionEvent("execution_progress", validEvent()), true);
  assert.equal(
    isPublicExecutionEvent("execution_step_completed", {
      ...validEvent(),
      status: "completed",
      progress: { current: 1, total: 1 },
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
  delete partial.progress;
  assert.equal(isPublicExecutionEvent("execution_progress", partial), false);
  assert.equal(
    isPublicExecutionEvent("execution_progress", {
      ...validEvent(),
      step_id: "C:/private/workspace",
    }),
    false,
  );
});

test("rejects retired v1 and schema-less public execution events", () => {
  const v1 = {
    ...validEvent(),
    schema_version: "ai-platform.public-execution-event.v1",
  };
  const schemaLess = validEvent();
  delete schemaLess.schema_version;

  assert.equal(isPublicExecutionEvent("execution_step", v1), false);
  assert.equal(isPublicExecutionEvent("execution_step", schemaLess), false);
});
