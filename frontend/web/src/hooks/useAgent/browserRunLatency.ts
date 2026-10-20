import { sessionApi } from "../../services/api/session";

interface BrowserSubmissionTiming {
  submissionId: string;
  startedAt: number;
  isCurrent: () => boolean;
  runId: string | null;
  sessionId: string | null;
}

const submissions = new Map<string, BrowserSubmissionTiming>();
const MAX_WAIT_MS = 86_400_000;

function current(timing: BrowserSubmissionTiming): boolean {
  try {
    return timing.isCurrent() && performance.now() - timing.startedAt <= MAX_WAIT_MS;
  } catch {
    return false;
  }
}

export function beginBrowserRunLatency(submissionId: string, isCurrent: () => boolean): void {
  for (const [id, timing] of submissions) {
    if (!current(timing)) submissions.delete(id);
  }
  if (typeof performance === "undefined" || submissions.has(submissionId)) return;
  // Bound abandoned observations independently of conversation history.
  if (submissions.size >= 32) submissions.delete(submissions.keys().next().value!);
  submissions.set(submissionId, {
    submissionId, startedAt: performance.now(), isCurrent, runId: null, sessionId: null,
  });
}

export function retireBrowserRunLatency(submissionId: string): void {
  submissions.delete(submissionId);
}

export function bindBrowserRunLatency(
  submissionId: string, sessionId: string, runId: string, isCurrent?: () => boolean,
): void {
  const timing = submissions.get(submissionId);
  if (timing && isCurrent) timing.isCurrent = isCurrent;
  if (!timing || !current(timing)) {
    submissions.delete(submissionId);
    return;
  }
  if (timing.runId !== null && (timing.runId !== runId || timing.sessionId !== sessionId)) return;
  timing.runId = runId;
  timing.sessionId = sessionId;
}

export function pruneBrowserRunLatencies(): void {
  for (const [id, timing] of submissions) {
    if (!current(timing)) submissions.delete(id);
  }
}

export function watchBrowserRunText(node: HTMLElement | null): (() => void) | undefined {
  if (!node || typeof document === "undefined") return;
  const owner = node.closest<HTMLElement>("[data-browser-run-id]");
  if (!owner) return;
  const timing = [...submissions.values()].find((entry) =>
    entry.runId === owner.dataset.browserRunId && entry.sessionId === owner.dataset.browserSessionId,
  );
  if (!timing) return;
  let observer: MutationObserver | undefined;
  let disposed = false;
  const dispose = () => {
    disposed = true;
    observer?.disconnect();
    document.removeEventListener("visibilitychange", observe);
  };
  const observe = () => {
    if (disposed) return;
    if (submissions.get(timing.submissionId) !== timing) {
      dispose();
      return;
    }
    if (!current(timing)) {
      submissions.delete(timing.submissionId);
      dispose();
      return;
    }
    if (!node.isConnected || !owner.isConnected) {
      dispose();
      return;
    }
    const hasPublicText = [...node.querySelectorAll("p, li, pre, h1, h2, h3, h4, h5, h6, th, td, .cm-line, svg text")]
      .some((element) => element.textContent?.trim());
    if (
      document.visibilityState !== "visible" || !hasPublicText
      || node.getClientRects().length === 0
      || node.closest("details:not([open])")
    ) return;
    const durationMs = Math.round(performance.now() - timing.startedAt);
    if (!Number.isSafeInteger(durationMs) || durationMs < 0 || durationMs > MAX_WAIT_MS) return;
    submissions.delete(timing.submissionId);
    dispose();
    // DOM availability is a client observation, not a paint or Run-outcome receipt.
    queueMicrotask(() => {
      if (!current(timing) || !timing.runId) return;
      void sessionApi.reportBrowserRunLatency(timing.submissionId, timing.runId, durationMs).catch(() => {
        // Optional measurement failure cannot interrupt chat or invent a zero.
      });
    });
  };
  observe();
  if (!disposed) {
    if (typeof MutationObserver !== "undefined") {
      observer = new MutationObserver(observe);
      observer.observe(node, { subtree: true, childList: true, characterData: true });
    }
    document.addEventListener("visibilitychange", observe);
  }
  return dispose;
}
