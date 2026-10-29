import assert from "node:assert/strict";
import test from "node:test";

import {
  getVisibleConnectionStatus,
  getRestoredModelSelection,
  isSessionRunning,
  shouldShowStreamingFooterSkeleton,
} from "../sessionState.ts";

test("a restored concrete model stays explicit even when legacy history says auto", async () => {
  const { buildAgentAppRunBody } = await import("../../../../services/api/session.ts");
  const legacy = { agent_options: { model_id: "model-a", model: "provider/model-a", selection_mode: "auto" } };
  const restored = getRestoredModelSelection(legacy);
  assert.deepEqual(restored, { modelId: "model-a", modelValue: "provider/model-a" });
  const body = buildAgentAppRunBody({ message: "continue", submissionId: "synthetic",
    agentOptions: {model_id: restored.modelId, model: restored.modelValue},
  });
  assert.equal(body.model_id, "model-a");
  assert.equal(body.model, "provider/model-a");
  assert.equal(body.selection_mode, "explicit");
  assert.deepEqual(getRestoredModelSelection({agent_options: { selection_mode: "auto" }}), { modelId: "", modelValue: "" });
});

test("treats loading or visible streaming messages as an active session", () => {
  assert.equal(isSessionRunning([], true), true);
  assert.equal(isSessionRunning([], true, true), false);
  assert.equal(
    isSessionRunning([{ isStreaming: false }, { isStreaming: true }], false),
    true,
  );
  assert.equal(
    isSessionRunning([{ isStreaming: true }], true, true),
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
      sessionRunning: true,
    }),
    "connecting",
  );
  assert.equal(
    getVisibleConnectionStatus({
      connectionStatus: "recovering_gap",
      sessionId: "session-a",
      currentRunId: "run-a",
      sessionRunning: true,
    }),
    "recovering_gap",
  );
  assert.equal(
    getVisibleConnectionStatus({
      connectionStatus: "disconnected",
      sessionId: "session-a",
      currentRunId: "run-a",
      sessionRunning: true,
    }),
    "disconnected",
  );
  assert.equal(
    getVisibleConnectionStatus({
      connectionStatus: "connected",
      sessionId: "session-a",
      currentRunId: "run-a",
      sessionRunning: true,
    }),
    null,
  );
  assert.equal(
    getVisibleConnectionStatus({
      connectionStatus: "disconnected",
      sessionId: "session-a",
      currentRunId: null,
      sessionRunning: false,
    }),
    null,
  );
  assert.equal(
    getVisibleConnectionStatus({
      connectionStatus: "disconnected",
      sessionId: null,
      currentRunId: "run-a",
      sessionRunning: false,
    }),
    null,
  );
});

test("hides transport recovery when a retained Run is no longer running", () => {
  assert.equal(
    getVisibleConnectionStatus({
      connectionStatus: "disconnected",
      sessionId: "session-a",
      currentRunId: "run-a",
      sessionRunning: false,
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
