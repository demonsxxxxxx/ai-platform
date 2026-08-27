import assert from "node:assert/strict";
import test from "node:test";
import {
  EXECUTION_PROGRESS_MIN_INTERVAL_MS,
  PublicStreamPresentation,
  upsertPublicThinkingActivity,
  type PublicStreamPresentationClock,
  type PublicStreamPresentationOwner,
} from "../publicStreamPresentation.ts";

class FakePresentationClock implements PublicStreamPresentationClock {
  private nowValue = 0;
  private nextHandle = 1;
  private frames = new Map<number, FrameRequestCallback>();
  private timers = new Map<number, { dueAt: number; callback: () => void }>();

  now = () => this.nowValue;

  requestAnimationFrame = (callback: FrameRequestCallback) => {
    const handle = this.nextHandle++;
    this.frames.set(handle, callback);
    return handle;
  };

  cancelAnimationFrame = (handle: number) => {
    this.frames.delete(handle);
  };

  setTimeout = (callback: () => void, delayMs: number) => {
    const handle = this.nextHandle++;
    this.timers.set(handle, { dueAt: this.nowValue + delayMs, callback });
    return handle as unknown as ReturnType<typeof setTimeout>;
  };

  clearTimeout = (handle: ReturnType<typeof setTimeout>) => {
    this.timers.delete(handle as unknown as number);
  };

  flushAnimationFrame(): void {
    const callbacks = [...this.frames.values()];
    this.frames.clear();
    callbacks.forEach((callback) => callback(this.nowValue));
  }

  advance(milliseconds: number): void {
    this.nowValue += milliseconds;
    while (true) {
      const due = [...this.timers.entries()]
        .filter(([, timer]) => timer.dueAt <= this.nowValue)
        .sort(([, left], [, right]) => left.dueAt - right.dueAt);
      if (due.length === 0) return;
      due.forEach(([handle, timer]) => {
        this.timers.delete(handle);
        timer.callback();
      });
    }
  }
}

const owner: PublicStreamPresentationOwner = {
  sessionId: "session-1",
  runId: "run-1",
  assistantMessageId: "assistant-1",
  streamVersion: 3,
};

test("coalesces rapid public deltas in receive order and flushes before terminal presentation", () => {
  const clock = new FakePresentationClock();
  const presentation = new PublicStreamPresentation(clock);
  const commits: string[] = [];
  presentation.activate(owner);

  assert.equal(
    presentation.enqueueAssistantDelta(owner, "first ", (content) => commits.push(content)),
    true,
  );
  assert.equal(
    presentation.enqueueAssistantDelta(owner, "second", (content) => commits.push(content)),
    true,
  );
  assert.equal(commits.length, 0);

  clock.flushAnimationFrame();
  assert.deepEqual(commits, ["first second"]);

  presentation.enqueueAssistantDelta(owner, "final-buffer", (content) => commits.push(content));
  assert.equal(presentation.flush(owner), true);
  clock.flushAnimationFrame();
  assert.deepEqual(commits, ["first second", "final-buffer"]);
});

test("commits an accepted answer delta before a later immediate execution update", () => {
  const clock = new FakePresentationClock();
  const presentation = new PublicStreamPresentation(clock);
  const commits: string[] = [];
  presentation.activate(owner);

  presentation.enqueueAssistantDelta(owner, "answer", (content) => {
    commits.push(`delta:${content}`);
  });
  presentation.enqueueExecutionUpdate(owner, {
    stepId: "step-1",
    sequence: 2,
    phase: "started",
    commit: () => commits.push("execution:started"),
  });

  assert.deepEqual(commits, ["delta:answer", "execution:started"]);
  clock.flushAnimationFrame();
  assert.deepEqual(commits, ["delta:answer", "execution:started"]);
});

test("acknowledges every coalesced semantic delta only after the merged reducer commit", () => {
  const clock = new FakePresentationClock();
  const presentation = new PublicStreamPresentation(clock);
  const commits: string[] = [];
  const acknowledgements: string[] = [];
  presentation.activate(owner);

  presentation.enqueueAssistantDelta(
    owner,
    "first ",
    (content, onApplied) => {
      commits.push(content);
      onApplied();
    },
    { onCommitted: () => acknowledgements.push("first") },
  );
  presentation.enqueueAssistantDelta(
    owner,
    "second",
    (content, onApplied) => {
      commits.push(content);
      onApplied();
    },
    { onCommitted: () => acknowledgements.push("second") },
  );

  assert.deepEqual(acknowledgements, []);
  clock.flushAnimationFrame();
  assert.deepEqual(commits, ["first second"]);
  assert.deepEqual(acknowledgements, ["first", "second"]);
});

test("discards a stale owner buffer before it can contaminate the replacement shell", () => {
  const clock = new FakePresentationClock();
  const presentation = new PublicStreamPresentation(clock);
  const commits: string[] = [];
  presentation.activate(owner);
  presentation.enqueueAssistantDelta(owner, "old", (content) => commits.push(content));

  const nextOwner = { ...owner, runId: "run-2", assistantMessageId: "assistant-2" };
  presentation.activate(nextOwner);
  presentation.enqueueAssistantDelta(nextOwner, "new", (content) => commits.push(content));
  clock.flushAnimationFrame();

  assert.deepEqual(commits, ["new"]);
  assert.equal(presentation.flush(owner), false);
});

test("throttles same-step progress and lets terminal updates supersede pending progress", () => {
  const clock = new FakePresentationClock();
  const presentation = new PublicStreamPresentation(clock);
  const commits: string[] = [];
  presentation.activate(owner);

  presentation.enqueueExecutionUpdate(owner, {
    stepId: "step-1",
    sequence: 1,
    phase: "started",
    commit: () => commits.push("started"),
  });
  presentation.enqueueExecutionUpdate(owner, {
    stepId: "step-1",
    sequence: 2,
    phase: "progress",
    commit: () => commits.push("progress-1"),
  });
  clock.advance(100);
  presentation.enqueueExecutionUpdate(owner, {
    stepId: "step-1",
    sequence: 3,
    phase: "progress",
    commit: () => commits.push("progress-2"),
  });
  clock.advance(EXECUTION_PROGRESS_MIN_INTERVAL_MS - 101);
  assert.deepEqual(commits, ["started", "progress-1"]);

  clock.advance(1);
  assert.deepEqual(commits, ["started", "progress-1", "progress-2"]);
  clock.advance(20);
  presentation.enqueueExecutionUpdate(owner, {
    stepId: "step-1",
    sequence: 4,
    phase: "progress",
    commit: () => commits.push("progress-3"),
  });
  presentation.enqueueExecutionUpdate(owner, {
    stepId: "step-1",
    sequence: 5,
    phase: "terminal",
    commit: () => commits.push("completed"),
  });
  clock.advance(EXECUTION_PROGRESS_MIN_INTERVAL_MS);

  assert.deepEqual(commits, ["started", "progress-1", "progress-2", "completed"]);
  assert.equal(
    presentation.enqueueExecutionUpdate(owner, {
      stepId: "step-1",
      sequence: 5,
      phase: "progress",
      commit: () => commits.push("duplicate"),
    }),
    false,
  );
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

test("legacy public Thinking completion still closes the preceding activity", () => {
  const started = {
    type: "thinking" as const,
    content: "Analyzing the request",
    thinking_id: "thinking-started",
    isStreaming: true,
  };
  const completed = {
    type: "thinking" as const,
    content: "Analysis step completed",
    thinking_id: "thinking-completed",
    isStreaming: false,
  };

  const parts = upsertPublicThinkingActivity(
    upsertPublicThinkingActivity([], started),
    completed,
  );

  assert.deepEqual(parts, [completed]);
});
