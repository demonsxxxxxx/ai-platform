import assert from "node:assert/strict";
import test from "node:test";

import {
  isActiveRunStatus,
  terminalRunStatus,
  terminalRunStatusFromEvent,
} from "../runLifecycle.ts";

test("normalizes failed, cancelled, and succeeded terminal statuses", () => {
  assert.equal(terminalRunStatus("run_failed"), "failed");
  assert.equal(terminalRunStatus("cancelled"), "cancelled");
  assert.equal(terminalRunStatus("succeeded"), "succeeded");
  assert.equal(terminalRunStatus("running"), null);
});

test("prefers an explicit run-event terminal state over the stream envelope", () => {
  assert.equal(
    terminalRunStatusFromEvent("run_event", {
      event_type: "run_cancelled",
    }),
    "cancelled",
  );
  assert.equal(
    terminalRunStatusFromEvent("complete", { run_id: "run-a" }),
    "succeeded",
  );
});

test("only authoritative active statuses are eligible for reconnect", () => {
  assert.equal(isActiveRunStatus("queued"), true);
  assert.equal(isActiveRunStatus("running"), true);
  assert.equal(isActiveRunStatus("failed"), false);
  assert.equal(isActiveRunStatus("succeeded"), false);
  assert.equal(isActiveRunStatus("cancelled"), false);
});
