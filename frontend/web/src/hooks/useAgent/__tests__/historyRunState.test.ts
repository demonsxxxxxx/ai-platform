import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  beginHistoryLoad,
  isCurrentHistoryLoad,
  resolveHistoryCurrentRunId,
} from "../historyRunState.ts";

const __dirname = dirname(fileURLToPath(import.meta.url));

function getLoadHistorySource(): string {
  const source = readFileSync(resolve(__dirname, "../../useAgent.ts"), "utf8");
  const loadHistoryStart = source.indexOf("const loadHistory = useCallback");
  const sendMessageStart = source.indexOf("// Send message", loadHistoryStart);

  assert.notEqual(loadHistoryStart, -1);
  assert.notEqual(sendMessageStart, -1);

  return source.slice(loadHistoryStart, sendMessageStart);
}

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

test("loadHistory clears stale currentRunId before fetching the target session", () => {
  const source = getLoadHistorySource();
  const loadHistoryStart = 0;
  const sessionFetch = source.indexOf(
    "const sessionData = await sessionApi.get",
    loadHistoryStart,
  );

  assert.notEqual(sessionFetch, -1);

  const beforeTargetSessionFetch = source.slice(loadHistoryStart, sessionFetch);
  assert.match(beforeTargetSessionFetch, /setCurrentRunId\(null\)/);
  assert.match(beforeTargetSessionFetch, /currentRunIdRef\.current = null/);
});

test("loadHistory guards awaited history writes with the current load token", () => {
  const source = getLoadHistorySource();
  assert.match(source, /beginHistoryLoad\(historyLoadTokenRef\)/);
  assert.match(source, /isCurrentHistoryLoad\(historyLoadTokenRef,\s*historyLoadToken\)/);

  const sessionFetch = source.indexOf("const sessionData = await sessionApi.get");
  const sessionGuard = source.indexOf("if (!isCurrentHistoryLoadRequest())", sessionFetch);
  const sessionWrite = source.indexOf("setSessionId(targetSessionId)", sessionFetch);

  assert.notEqual(sessionFetch, -1);
  assert.notEqual(sessionGuard, -1);
  assert.notEqual(sessionWrite, -1);
  assert.ok(
    sessionFetch < sessionGuard && sessionGuard < sessionWrite,
    "session state must only be written by the current history load",
  );

  const eventsAwait = source.indexOf("const [eventsData, feedbackList] = await Promise.all");
  const eventsGuard = source.indexOf("if (!isCurrentHistoryLoadRequest())", eventsAwait);
  const statusAwait = source.indexOf("const statusResult = historyCurrentRunId", eventsAwait);
  const statusGuard = source.indexOf("if (!isCurrentHistoryLoadRequest())", statusAwait);
  const messagesWrite = source.indexOf("setMessages(reconstructedMessages)", statusAwait);

  assert.notEqual(eventsAwait, -1);
  assert.notEqual(eventsGuard, -1);
  assert.notEqual(statusAwait, -1);
  assert.notEqual(statusGuard, -1);
  assert.notEqual(messagesWrite, -1);
  assert.ok(
    eventsAwait < eventsGuard && eventsGuard < statusAwait,
    "event history must determine the status lookup candidate",
  );
  assert.ok(
    statusAwait < statusGuard && statusGuard < messagesWrite,
    "messages must only be written after the current status lookup",
  );

  const finallyStart = source.indexOf("} finally {");
  const finallyEnd = source.indexOf("}\n\n      return null;", finallyStart);
  const finallyBlock = source.slice(finallyStart, finallyEnd);
  assert.match(finallyBlock, /if \(isCurrentHistoryLoadRequest\(\)\)/);
});
