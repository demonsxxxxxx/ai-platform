import assert from "node:assert/strict";
import test from "node:test";

import {
  getVisibleConnectionStatus,
  isSessionRunning,
  shouldShowStreamingFooterSkeleton,
} from "../sessionState.ts";

test("treats loading or visible streaming messages as an active session", () => {
  assert.equal(isSessionRunning([], true), true);
  assert.equal(
    isSessionRunning([{ isStreaming: false }, { isStreaming: true }], false),
    true,
  );
  assert.equal(isSessionRunning([{ isStreaming: false }], false), false);
});

test("shows transport recovery only for a Run owned by the current session", () => {
  assert.equal(
    getVisibleConnectionStatus({
      connectionStatus: "connecting",
      sessionId: "session-a",
      currentRunId: "run-a",
    }),
    "connecting",
  );
  assert.equal(
    getVisibleConnectionStatus({
      connectionStatus: "recovering_gap",
      sessionId: "session-a",
      currentRunId: "run-a",
    }),
    "recovering_gap",
  );
  assert.equal(
    getVisibleConnectionStatus({
      connectionStatus: "disconnected",
      sessionId: "session-a",
      currentRunId: "run-a",
    }),
    "disconnected",
  );
  assert.equal(
    getVisibleConnectionStatus({
      connectionStatus: "connected",
      sessionId: "session-a",
      currentRunId: "run-a",
    }),
    null,
  );
  assert.equal(
    getVisibleConnectionStatus({
      connectionStatus: "disconnected",
      sessionId: "session-a",
      currentRunId: null,
    }),
    null,
  );
  assert.equal(
    getVisibleConnectionStatus({
      connectionStatus: "disconnected",
      sessionId: null,
      currentRunId: "run-a",
    }),
    null,
  );
});

test("shows the footer skeleton only when reconnecting after a stream disappears", () => {
  assert.equal(
    shouldShowStreamingFooterSkeleton({
      connectionStatus: "reconnecting",
      sessionRunning: true,
      messageCount: 2,
      hasVisibleStreamingMessage: false,
    }),
    true,
  );

  assert.equal(
    shouldShowStreamingFooterSkeleton({
      connectionStatus: "recovering_gap",
      sessionRunning: true,
      messageCount: 2,
      hasVisibleStreamingMessage: false,
    }),
    true,
  );

  assert.equal(
    shouldShowStreamingFooterSkeleton({
      connectionStatus: "connected",
      sessionRunning: true,
      messageCount: 2,
      hasVisibleStreamingMessage: false,
    }),
    false,
  );

  assert.equal(
    shouldShowStreamingFooterSkeleton({
      connectionStatus: "disconnected",
      sessionRunning: true,
      messageCount: 2,
      hasVisibleStreamingMessage: true,
    }),
    false,
  );

  assert.equal(
    shouldShowStreamingFooterSkeleton({
      connectionStatus: "disconnected",
      sessionRunning: false,
      messageCount: 2,
      hasVisibleStreamingMessage: false,
    }),
    false,
  );
});
