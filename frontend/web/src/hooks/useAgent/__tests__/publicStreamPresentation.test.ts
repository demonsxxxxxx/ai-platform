import assert from "node:assert/strict";
import test from "node:test";
import {
  EXECUTION_PROGRESS_MIN_INTERVAL_MS,
  PublicStreamPresentation,
  projectPublicThinkingActivity,
  upsertPublicThinkingActivity,
  type PublicStreamPresentationClock,
  type PublicStreamPresentationOwner,
} from "../publicStreamPresentation.ts";
import { CHAT_PUBLIC_PROJECTION_VERSION } from "../types.ts";

class FakePresentationClock implements PublicStreamPresentationClock {
  private nowValue = 0;
  private nextHandle = 1;
  private timers = new Map<number, { dueAt: number; callback: () => void }>();

  now = () => this.nowValue;

  setTimeout = (callback: () => void, delayMs: number) => {
    const handle = this.nextHandle++;
    this.timers.set(handle, { dueAt: this.nowValue + delayMs, callback });
    return handle as unknown as ReturnType<typeof setTimeout>;
  };

  clearTimeout = (handle: ReturnType<typeof setTimeout>) => {
    this.timers.delete(handle as unknown as number);
  };

  advance(milliseconds: number): void {
    this.nowValue += milliseconds;
    const due = [...this.timers.entries()]
      .filter(([, timer]) => timer.dueAt <= this.nowValue)
      .sort(([, left], [, right]) => left.dueAt - right.dueAt);
    due.forEach(([handle, timer]) => {
      this.timers.delete(handle);
      timer.callback();
    });
  }
}

const owner: PublicStreamPresentationOwner = {
  sessionId: "session-1",
  runId: "run-1",
  assistantMessageId: "assistant-1",
  streamVersion: 3,
};

test("presentation lifecycle accepts only its active owner", () => {
  const presentation = new PublicStreamPresentation();
  presentation.activate(owner);
  assert.equal(presentation.flush(owner), true);

  const nextOwner = { ...owner, runId: "run-2", assistantMessageId: "assistant-2" };
  presentation.activate(nextOwner);
  assert.equal(presentation.flush(owner), false);
  assert.equal(presentation.flush(nextOwner), true);
  presentation.invalidate();
  assert.equal(presentation.flush(nextOwner), false);
});

test("throttles progress and lets terminal execution supersede pending progress", () => {
  const clock = new FakePresentationClock();
  const presentation = new PublicStreamPresentation(clock);
  const commits: string[] = [];
  presentation.activate(owner);

  presentation.enqueueExecutionUpdate(owner, {
    stepId: "step-1",
    sequence: 1,
    phase: "progress",
    commit: () => commits.push("progress-1"),
  });
  presentation.enqueueExecutionUpdate(owner, {
    stepId: "step-1",
    sequence: 2,
    phase: "progress",
    commit: () => commits.push("progress-2"),
  });
  clock.advance(EXECUTION_PROGRESS_MIN_INTERVAL_MS - 1);
  assert.deepEqual(commits, ["progress-1"]);
  clock.advance(1);
  assert.deepEqual(commits, ["progress-1", "progress-2"]);

  presentation.enqueueExecutionUpdate(owner, {
    stepId: "step-1",
    sequence: 3,
    phase: "progress",
    commit: () => commits.push("stale-progress"),
  });
  presentation.enqueueExecutionUpdate(owner, {
    stepId: "step-1",
    sequence: 4,
    phase: "terminal",
    commit: () => commits.push("completed"),
  });
  clock.advance(EXECUTION_PROGRESS_MIN_INTERVAL_MS);
  assert.deepEqual(commits, ["progress-1", "progress-2", "completed"]);
});

test("discards deferred execution progress when the stream owner changes", () => {
  const clock = new FakePresentationClock();
  const presentation = new PublicStreamPresentation(clock);
  const commits: number[] = [];
  presentation.activate(owner);
  presentation.enqueueExecutionUpdate(owner, {
    stepId: "step-1",
    sequence: 1,
    phase: "progress",
    commit: () => commits.push(1),
  });
  presentation.enqueueExecutionUpdate(owner, {
    stepId: "step-1",
    sequence: 2,
    phase: "progress",
    commit: () => commits.push(2),
  });

  presentation.activate({
    ...owner,
    runId: "run-2",
    assistantMessageId: "assistant-2",
  });
  clock.advance(EXECUTION_PROGRESS_MIN_INTERVAL_MS);
  assert.deepEqual(commits, [1]);
});

test("model reasoning deltas accumulate under one thinking identity and complete", () => {
  const thinkingId = "thinking:thinking-public-1";
  let parts = upsertPublicThinkingActivity([], {
    type: "thinking",
    content: "",
    thinking_id: thinkingId,
    isStreaming: true,
  });
  parts = upsertPublicThinkingActivity(parts, {
    type: "thinking",
    content: "Compare the public evidence ",
    thinking_id: thinkingId,
    isStreaming: true,
  });
  parts = upsertPublicThinkingActivity(parts, {
    type: "thinking",
    content: "before answering.",
    thinking_id: thinkingId,
    isStreaming: true,
  });
  parts = upsertPublicThinkingActivity(parts, {
    type: "thinking",
    content: "",
    thinking_id: thinkingId,
    isStreaming: false,
  });

  assert.deepEqual(parts, [
    {
      type: "thinking",
      content: "Compare the public evidence before answering.",
      thinking_id: thinkingId,
      isStreaming: false,
    },
  ]);
});

test("model reasoning matching a legacy label remains model content", () => {
  const activity = projectPublicThinkingActivity(
    {
      projection_version: CHAT_PUBLIC_PROJECTION_VERSION,
      event_type: "public_activity",
      event_id: "thinking-delta-1",
      stage: "thinking_delta",
      status: "thinking_delta",
      message: "Analyzing the request",
      payload: {
        thinking_id: "thinking-public-1",
        delta: "Analyzing the request",
      },
    },
    true,
  );

  assert.deepEqual(activity, {
    type: "thinking",
    content: "Analyzing the request",
    thinking_id: "thinking-public-1",
    public_reasoning: true,
    isStreaming: true,
  });
});

test("legacy public Thinking completion still closes the preceding activity", () => {
  const started = {
    type: "thinking" as const,
    content: "正在分析请求",
    thinking_id: "thinking-started",
    isStreaming: true,
  };
  const completed = {
    type: "thinking" as const,
    content: "分析完成",
    thinking_id: "thinking-completed",
    isStreaming: false,
  };

  const parts = upsertPublicThinkingActivity(
    upsertPublicThinkingActivity([], started),
    completed,
  );

  assert.deepEqual(parts, [completed]);
});
