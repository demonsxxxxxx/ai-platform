import type { ExecutionTimelinePart, MessagePart } from "../../types/message";

export const EXECUTION_PROGRESS_MIN_INTERVAL_MS = 250;

/** Exact stream owner required before buffered public presentation may commit. */
export interface PublicStreamPresentationOwner {
  sessionId: string;
  runId: string;
  assistantMessageId: string;
  streamVersion: number;
}

export type PublicExecutionPresentationPhase =
  | "started"
  | "progress"
  | "terminal";

export interface PublicExecutionPresentationUpdate {
  stepId: string;
  sequence: number;
  phase: PublicExecutionPresentationPhase;
  commit: () => void;
}

export interface PublicStreamPresentationClock {
  now: () => number;
  requestAnimationFrame: (callback: FrameRequestCallback) => number;
  cancelAnimationFrame: (handle: number) => void;
  setTimeout: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>;
  clearTimeout: (handle: ReturnType<typeof setTimeout>) => void;
}

function browserClock(): PublicStreamPresentationClock {
  return {
    now: () => Date.now(),
    requestAnimationFrame: (callback) => window.requestAnimationFrame(callback),
    cancelAnimationFrame: (handle) => window.cancelAnimationFrame(handle),
    setTimeout: (callback, delayMs) => setTimeout(callback, delayMs),
    clearTimeout: (handle) => clearTimeout(handle),
  };
}

function ownersEqual(
  left: PublicStreamPresentationOwner | null,
  right: PublicStreamPresentationOwner,
): boolean {
  return left !== null &&
    left.sessionId === right.sessionId &&
    left.runId === right.runId &&
    left.assistantMessageId === right.assistantMessageId &&
    left.streamVersion === right.streamVersion;
}

interface PendingText {
  content: string;
  commit: (content: string, onApplied: () => void) => void;
  acknowledgements: Array<() => void>;
  semanticEventIds: Set<string>;
  latestSequence: number | null;
}

export interface PublicTextPresentationAcceptance {
  onCommitted?: () => void;
  semanticEventId?: string;
  sequence?: number | null;
}

interface PendingProgress extends PublicExecutionPresentationUpdate {
  dueAt: number;
}

/**
 * Coalesce safe public stream presentation without changing durable event order.
 * The owner key is intentionally complete so stale session/run shells cannot
 * publish after an auth, session, or stream-generation replacement.
 */
export class PublicStreamPresentation {
  private owner: PublicStreamPresentationOwner | null = null;
  private animationFrame: number | null = null;
  private progressTimer: ReturnType<typeof setTimeout> | null = null;
  private pendingText: PendingText | null = null;
  private pendingProgressByStep = new Map<string, PendingProgress>();
  private acceptedSequenceByStep = new Map<string, number>();
  private lastProgressCommitAt = new Map<string, number>();

  constructor(private readonly clock: PublicStreamPresentationClock = browserClock()) {}

  activate(owner: PublicStreamPresentationOwner): void {
    if (ownersEqual(this.owner, owner)) return;
    this.discard();
    this.owner = owner;
  }

  invalidate(): void {
    this.discard();
    this.owner = null;
  }

  owns(owner: PublicStreamPresentationOwner): boolean {
    return ownersEqual(this.owner, owner);
  }

  enqueueAssistantDelta(
    owner: PublicStreamPresentationOwner,
    content: string,
    commit: (content: string, onApplied: () => void) => void,
    acceptance: PublicTextPresentationAcceptance = {},
  ): boolean {
    if (!ownersEqual(this.owner, owner) || !content) return false;
    if (this.pendingText) {
      if (
        (acceptance.semanticEventId &&
          this.pendingText.semanticEventIds.has(acceptance.semanticEventId)) ||
        (typeof acceptance.sequence === "number" &&
          this.pendingText.latestSequence !== null &&
          acceptance.sequence <= this.pendingText.latestSequence)
      ) {
        return false;
      }
      const semanticEventIds = new Set(this.pendingText.semanticEventIds);
      if (acceptance.semanticEventId) {
        semanticEventIds.add(acceptance.semanticEventId);
      }
      this.pendingText = {
        content: this.pendingText.content + content,
        // The latest callback owns the latest accepted Redis cursor while its
        // merged content commits every earlier delta in receive order.
        commit,
        acknowledgements: [
          ...this.pendingText.acknowledgements,
          ...(acceptance.onCommitted ? [acceptance.onCommitted] : []),
        ],
        semanticEventIds,
        latestSequence:
          typeof acceptance.sequence === "number"
            ? acceptance.sequence
            : this.pendingText.latestSequence,
      };
      return true;
    }
    this.pendingText = {
      content,
      commit,
      acknowledgements: acceptance.onCommitted
        ? [acceptance.onCommitted]
        : [],
      semanticEventIds: new Set(
        acceptance.semanticEventId ? [acceptance.semanticEventId] : [],
      ),
      latestSequence:
        typeof acceptance.sequence === "number" ? acceptance.sequence : null,
    };
    this.animationFrame = this.clock.requestAnimationFrame(() => {
      this.animationFrame = null;
      this.flushText(owner);
    });
    return true;
  }

  enqueueExecutionUpdate(
    owner: PublicStreamPresentationOwner,
    update: PublicExecutionPresentationUpdate,
  ): boolean {
    if (!ownersEqual(this.owner, owner)) return false;
    const acceptedSequence = this.acceptedSequenceByStep.get(update.stepId);
    if (acceptedSequence !== undefined && update.sequence <= acceptedSequence) {
      return false;
    }
    this.acceptedSequenceByStep.set(update.stepId, update.sequence);

    if (update.phase === "started" || update.phase === "terminal") {
      this.pendingProgressByStep.delete(update.stepId);
      this.rescheduleProgressTimer();
      this.commitExecutionImmediately(owner, update.commit);
      return true;
    }

    const now = this.clock.now();
    const lastCommittedAt = this.lastProgressCommitAt.get(update.stepId);
    if (
      lastCommittedAt === undefined ||
      now - lastCommittedAt >= EXECUTION_PROGRESS_MIN_INTERVAL_MS
    ) {
      this.lastProgressCommitAt.set(update.stepId, now);
      this.commitExecutionImmediately(owner, update.commit);
      return true;
    }

    this.pendingProgressByStep.set(update.stepId, {
      ...update,
      dueAt: lastCommittedAt + EXECUTION_PROGRESS_MIN_INTERVAL_MS,
    });
    this.rescheduleProgressTimer();
    return true;
  }

  /** Flush accepted presentation before a final, terminal, close, or reconnect. */
  flush(owner: PublicStreamPresentationOwner): boolean {
    if (!ownersEqual(this.owner, owner)) return false;
    if (this.animationFrame !== null) {
      this.clock.cancelAnimationFrame(this.animationFrame);
      this.animationFrame = null;
    }
    if (this.progressTimer !== null) {
      this.clock.clearTimeout(this.progressTimer);
      this.progressTimer = null;
    }
    this.flushText(owner);
    const pending = [...this.pendingProgressByStep.values()].sort(
      (left, right) => left.sequence - right.sequence,
    );
    this.pendingProgressByStep.clear();
    pending.forEach((update) => {
      this.lastProgressCommitAt.set(update.stepId, this.clock.now());
      update.commit();
    });
    return true;
  }

  private flushText(owner: PublicStreamPresentationOwner): void {
    if (!ownersEqual(this.owner, owner) || !this.pendingText) return;
    const pending = this.pendingText;
    this.pendingText = null;
    pending.commit(pending.content, () => {
      pending.acknowledgements.forEach((acknowledge) => acknowledge());
    });
  }

  /** Preserve receive order when an execution update cannot wait for rAF. */
  private commitExecutionImmediately(
    owner: PublicStreamPresentationOwner,
    commit: () => void,
  ): void {
    if (this.animationFrame !== null) {
      this.clock.cancelAnimationFrame(this.animationFrame);
      this.animationFrame = null;
    }
    this.flushText(owner);
    commit();
  }

  private rescheduleProgressTimer(): void {
    if (this.progressTimer !== null) {
      this.clock.clearTimeout(this.progressTimer);
      this.progressTimer = null;
    }
    const nextDueAt = Math.min(
      ...[...this.pendingProgressByStep.values()].map((update) => update.dueAt),
    );
    if (!Number.isFinite(nextDueAt)) return;
    this.progressTimer = this.clock.setTimeout(() => {
      this.progressTimer = null;
      this.flushDueProgress();
    }, Math.max(0, nextDueAt - this.clock.now()));
  }

  private flushDueProgress(): void {
    if (!this.owner) return;
    const now = this.clock.now();
    const due = [...this.pendingProgressByStep.values()]
      .filter((update) => update.dueAt <= now)
      .sort((left, right) => left.sequence - right.sequence);
    due.forEach((update) => {
      this.pendingProgressByStep.delete(update.stepId);
      this.lastProgressCommitAt.set(update.stepId, now);
      update.commit();
    });
    this.rescheduleProgressTimer();
  }

  private discard(): void {
    if (this.animationFrame !== null) {
      this.clock.cancelAnimationFrame(this.animationFrame);
      this.animationFrame = null;
    }
    if (this.progressTimer !== null) {
      this.clock.clearTimeout(this.progressTimer);
      this.progressTimer = null;
    }
    this.pendingText = null;
    this.pendingProgressByStep.clear();
    this.acceptedSequenceByStep.clear();
    this.lastProgressCommitAt.clear();
  }
}

function updateExecutionStep(
  steps: ExecutionTimelinePart[],
  step: ExecutionTimelinePart,
): ExecutionTimelinePart[] {
  const existing = steps.find((candidate) => candidate.step_id === step.step_id);
  if (existing && step.sequence <= existing.sequence) return steps;
  if (!existing) return [...steps, step];
  return steps.map((candidate) =>
    candidate.step_id === step.step_id ? step : candidate,
  );
}

/** Upsert one safe execution step without retaining the raw public envelope. */
export function upsertPublicExecutionStep(
  parts: MessagePart[],
  step: ExecutionTimelinePart,
): MessagePart[] {
  const process = parts.find(
    (part): part is Extract<MessagePart, { type: "execution_process" }> =>
      part.type === "execution_process",
  );
  if (process) {
    const nextSteps = updateExecutionStep(process.steps, step);
    return nextSteps === process.steps
      ? parts
      : parts.map((part) =>
          part.type === "execution_process"
            ? { ...part, steps: nextSteps }
            : part,
        );
  }
  const existing = parts.find(
    (part): part is ExecutionTimelinePart =>
      part.type === "execution_step" && part.step_id === step.step_id,
  );
  if (existing && step.sequence <= existing.sequence) return parts;
  if (!existing) return [...parts, step];
  return parts.map((part) =>
    part.type === "execution_step" && part.step_id === step.step_id ? step : part,
  );
}

/** Collapse public execution steps into one terminal-only process summary. */
export function collapsePublicExecutionSteps(parts: MessagePart[]): MessagePart[] {
  const steps = parts.flatMap((part) =>
    part.type === "execution_process"
      ? part.steps
      : part.type === "execution_step"
        ? [part]
        : [],
  );
  if (steps.length === 0) return parts;
  const process = {
    type: "execution_process" as const,
    steps: steps.reduce(updateExecutionStep, [] as ExecutionTimelinePart[]),
  };
  let inserted = false;
  return parts.flatMap((part): MessagePart[] => {
    if (part.type !== "execution_step" && part.type !== "execution_process") {
      return [part];
    }
    if (inserted) return [];
    inserted = true;
    return [process];
  });
}

/** Restore running execution rows after an active-run history hydration. */
export function expandPublicExecutionSteps(parts: MessagePart[]): MessagePart[] {
  return parts.flatMap((part): MessagePart[] =>
    part.type === "execution_process" ? part.steps : [part],
  );
}
