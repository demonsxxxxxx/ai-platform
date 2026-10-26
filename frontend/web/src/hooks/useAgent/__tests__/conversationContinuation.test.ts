import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { canContinueConversation, parsePublicContinuation, sessionApi } from "../../../services/api/session.ts";
import { publicContinuationMessage } from "../publicTerminalPresentation.ts";
import { RunControlLifecycle, type RunControlParentIdentity } from "../runControlLifecycle.ts";

const parent: RunControlParentIdentity = {
  sessionId: "session-safe", runId: "run-safe", chatHistoryGeneration: 1, authRevision: 1,
  auth: { incarnation: "incarnation-safe", sessionMarker: "marker-safe", tenantId: "tenant-safe",
    userId: "user-safe", roles: [], permissions: [], isAdmin: false, isActive: true },
};

const saved = { state: "resumable", action: "continue", reason_code: "saved", workspace_saved: true } as const;
const settling = { state: "settling", action: "wait", reason_code: "settling", workspace_saved: false } as const;

test("continuation admits only the public block and never treats old or malformed DTOs as saved", () => {
  assert.equal(parsePublicContinuation(undefined), undefined);
  assert.equal(parsePublicContinuation(null), undefined);
  assert.equal(canContinueConversation(undefined), true, "legacy admission stays compatible");
  assert.match(publicContinuationMessage(undefined), /无法确认工作文件已保存/);
  assert.doesNotMatch(publicContinuationMessage(undefined), /恢复成功/);
  const parsed = parsePublicContinuation({ ...saved, reason_code: "private /runtime/secret", provider_epoch_id: "private-epoch" });
  assert.deepEqual(parsed, { ...saved, reason_code: "unavailable" });
  assert.equal(canContinueConversation(parsed), true);
  assert.doesNotMatch(JSON.stringify(parsed), /private|runtime/);
  for (const value of [{}, { ...saved, state: "private-state" }, { ...saved, workspace_saved: false }, { ...saved, action: "wait" }]) {
    assert.equal(canContinueConversation(parsePublicContinuation(value)), false);
  }
  assert.match(publicContinuationMessage(parsePublicContinuation({ ...saved, workspace_saved: false })), /尚未完整保存/);
});

test("continuation provides specific fixed guidance and fail-closed sending for every state", () => {
  for (const [reason, pattern] of [
    ["writer_unverified", /停止或隔离/], ["workspace_unsaved", /尚未完整保存/],
    ["native_unavailable", /原生记录不可用/], ["capacity_exhausted", /容量已用尽/],
  ] as const) {
    const continuation = parsePublicContinuation({ state: reason === "capacity_exhausted" ? reason : "blocked", action: "new_conversation", reason_code: reason, workspace_saved: false });
    assert.equal(canContinueConversation(continuation), false);
    assert.match(publicContinuationMessage(continuation), pattern);
  }
  assert.equal(canContinueConversation(parsePublicContinuation(settling)), false);
  assert.match(publicContinuationMessage(parsePublicContinuation(settling)), /草稿已保留/);
  const candidate = parsePublicContinuation({ ...saved, state: "recovery_candidate" });
  assert.equal(canContinueConversation(candidate), true);
  assert.match(publicContinuationMessage(candidate), /先检查恢复状态/);
  assert.match(publicContinuationMessage(parsePublicContinuation(saved)), /不会自动重发/);
  const source = readFileSync(new URL("../../../components/layout/AppContent/ChatView.tsx", import.meta.url), "utf8");
  assert.match(source, /!terminalResultSynchronizing && !continuationBlocksSend/);
  assert.match(source, /canSend: canSendInCurrentView/);
});

test("continuation status reads reject stale history, identity, scope and out-of-order results", async () => {
  const lifecycle = new RunControlLifecycle();
  const original = sessionApi.getStatus;
  const resolves: Array<(value: Awaited<ReturnType<typeof original>>) => void> = [];
  sessionApi.getStatus = async () => new Promise((resolve) => resolves.push(resolve));
  try {
    const owner = lifecycle.bindParent(parent)!;
    const old = lifecycle.refreshContinuation(owner);
    const fresh = lifecycle.refreshContinuation(owner);
    resolves[1]({ session_id: parent.sessionId, run_id: parent.runId, continuation: saved });
    await fresh;
    resolves[0]({ session_id: parent.sessionId, run_id: parent.runId, continuation: settling });
    await old;
    assert.equal(lifecycle.getSnapshot().continuation?.state, "resumable");
    lifecycle.observeContinuation(owner, { session_id: "other-session", continuation: settling });
    lifecycle.observeContinuation(owner, { session_id: parent.sessionId, run_id: "other-run", continuation: settling });
    assert.equal(lifecycle.getSnapshot().continuation?.state, "resumable");
    for (const next of [
      { ...parent, chatHistoryGeneration: 2 },
      { ...parent, authRevision: 2, auth: { ...parent.auth, userId: "other-user" } },
    ]) {
      const previous = lifecycle.getSnapshot().owner!;
      const pending = lifecycle.refreshContinuation(previous);
      lifecycle.bindParent(next);
      resolves.at(-1)!({ session_id: parent.sessionId, run_id: parent.runId, continuation: saved });
      await pending;
      assert.equal(lifecycle.getSnapshot().continuation, undefined);
      assert.equal(lifecycle.getSnapshot().continuationLoaded, false);
    }
    lifecycle.invalidate();
  } finally { sessionApi.getStatus = original; }
});

test("a delayed playback readiness read cannot replace newer composer continuation", async () => {
  const lifecycle = new RunControlLifecycle();
  const originalStatus = sessionApi.getStatus;
  const originalFetch = globalThis.fetch;
  let resolveStatus!: (value: Awaited<ReturnType<typeof originalStatus>>) => void;
  sessionApi.getStatus = async () => new Promise((resolve) => { resolveStatus = resolve; });
  globalThis.fetch = async () => new Response(JSON.stringify({ run_id: parent.runId, run: { status: "cancelled" }, timeline: [], events: [], steps: [], artifacts: [] }));
  try {
    const owner = lifecycle.bindParent(parent)!;
    const older = lifecycle.refresh(owner);
    lifecycle.observeContinuation(owner, { session_id: parent.sessionId, continuation: saved });
    resolveStatus({ session_id: parent.sessionId, continuation: settling });
    await older;
    assert.equal(lifecycle.getSnapshot().continuation?.state, "resumable");
  } finally { lifecycle.invalidate(); sessionApi.getStatus = originalStatus; globalThis.fetch = originalFetch; }
});

test("settling prevents retry/resume from creating a competing child and read failures keep the gate", async () => {
  const lifecycle = new RunControlLifecycle();
  const originalStatus = sessionApi.getStatus;
  const originalRetry = sessionApi.retryRun;
  const originalResume = sessionApi.resumeRun;
  let mutations = 0;
  sessionApi.getStatus = async () => { throw new Error("private runtime failure"); };
  sessionApi.retryRun = async () => { mutations += 1; throw new Error("must not run"); };
  sessionApi.resumeRun = async () => { mutations += 1; throw new Error("must not run"); };
  lifecycle.configure({ adoptRunControlChild: async () => "superseded", reconnectRunControlOwner: async () => {} });
  const owner = lifecycle.bindParent(parent)!;
  try {
    lifecycle.observeContinuation(owner, { session_id: parent.sessionId, continuation: settling });
    await lifecycle.retry();
    await lifecycle.resume();
    await lifecycle.refreshContinuation(owner);
    assert.equal(mutations, 0);
    assert.equal(lifecycle.getSnapshot().continuation?.state, "settling");
    lifecycle.markContinuationPending(parent.runId);
    assert.equal(lifecycle.getSnapshot().continuationLoaded, false);
    await lifecycle.refreshContinuation(owner);
    assert.equal(lifecycle.getSnapshot().continuationLoaded, false);
    assert.doesNotMatch(JSON.stringify(lifecycle.getSnapshot().continuation), /private|runtime/);
  } finally {
    lifecycle.invalidate(); sessionApi.getStatus = originalStatus;
    sessionApi.retryRun = originalRetry; sessionApi.resumeRun = originalResume;
  }
});
