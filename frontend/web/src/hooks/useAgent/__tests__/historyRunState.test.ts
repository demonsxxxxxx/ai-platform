import assert from "node:assert/strict";
import test from "node:test";

import {
  beginHistoryLoad,
  isCurrentHistoryLoad,
  resolveHistoryCurrentRunId,
} from "../historyRunState.ts";

test("resolveHistoryCurrentRunId clears stale previous run when the target session has no run candidate", () => {
  assert.equal(
    resolveHistoryCurrentRunId({
      previousRunId: "run-old",
      sessionData: { metadata: {} },
      eventsData: { events: [] },
    }),
    null,
  );
});

test("resolveHistoryCurrentRunId prefers explicit and backend-created run subjects without timestamp guessing", () => {
  assert.equal(
    resolveHistoryCurrentRunId({
      targetRunId: "run-explicit",
      sessionData: { metadata: { current_run_id: "run-current" } },
      eventsData: { run_id: "run-events", events: [] },
    }),
    "run-explicit",
  );

  assert.equal(
    resolveHistoryCurrentRunId({
      sessionData: { metadata: { current_run_id: "run-current" } },
      eventsData: {
        current_run_id: "run-latest-created",
        run_id: "run-events",
        events: [],
      },
    }),
    "run-latest-created",
  );

  assert.equal(
    resolveHistoryCurrentRunId({
      sessionData: { metadata: { latest_run_id: "run-latest" } },
      eventsData: { run_id: "run-events", events: [] },
    }),
    "run-events",
  );

  assert.equal(
    resolveHistoryCurrentRunId({
      sessionData: { metadata: { latest_run: { run_id: "run-nested" } } },
      eventsData: { run_id: "run-events", events: [] },
    }),
    "run-events",
  );

  assert.equal(
    resolveHistoryCurrentRunId({
      sessionData: { metadata: {} },
      eventsData: { run_id: "run-events", events: [] },
    }),
    "run-events",
  );

  assert.equal(
    resolveHistoryCurrentRunId({
      sessionData: { metadata: { current_run_id: "run-created-newer" } },
      eventsData: {
        events: [
          {
            event_type: "done",
            run_id: "run-older",
            timestamp: "2026-06-03T03:00:00.000Z",
            data: { status: "failed" },
          },
          {
            event_type: "done",
            run_id: "run-created-newer",
            timestamp: "2026-06-03T02:00:00.000Z",
            data: { status: "succeeded" },
          },
        ],
      },
    }),
    "run-created-newer",
  );

  assert.equal(
    resolveHistoryCurrentRunId({
      sessionData: { metadata: {} },
      eventsData: {
        events: [
          {
            event_type: "done",
            run_id: "run-older-finishes-last",
            timestamp: "2026-06-03T03:00:00.000Z",
            data: { status: "failed" },
          },
          {
            event_type: "done",
            run_id: "run-newer-finishes-first",
            timestamp: "2026-06-03T02:00:00.000Z",
            data: { status: "succeeded" },
          },
        ],
      },
    }),
    null,
  );
});

test("history load token rejects out-of-order completion before run id or messages are written", () => {
  const historyLoadTokenRef = { current: 0 };
  const loadA = beginHistoryLoad(historyLoadTokenRef);
  const loadB = beginHistoryLoad(historyLoadTokenRef);
  let currentRunId: string | null = null;
  let messages: string[] = [];

  const applyHistoryState = (
    token: number,
    nextRunId: string,
    nextMessages: string[],
  ) => {
    if (!isCurrentHistoryLoad(historyLoadTokenRef, token)) {
      return;
    }

    currentRunId = nextRunId;
    messages = nextMessages;
  };

  applyHistoryState(loadB, "run-b", ["message-b"]);
  applyHistoryState(loadA, "run-a", ["message-a"]);

  assert.equal(currentRunId, "run-b");
  assert.deepEqual(messages, ["message-b"]);
  assert.equal(isCurrentHistoryLoad(historyLoadTokenRef, loadA), false);
  assert.equal(isCurrentHistoryLoad(historyLoadTokenRef, loadB), true);
});
