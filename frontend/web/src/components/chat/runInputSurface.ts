import type { RunInputQuestionBatch, RunInputsProjection } from "../../services/api/session";
import type { RunInputsController } from "../../hooks/useAgent/types";

export interface RunInputReceiptInput {
  input: RunInputsProjection["inputs"][number];
  batch: RunInputQuestionBatch | undefined;
  unprocessed: boolean;
}

export interface RunInputReceiptRun {
  projection: RunInputsProjection;
  closed: boolean;
  inputs: RunInputReceiptInput[];
  questions: RunInputQuestionBatch[];
}

export interface RunInputSurface {
  /** Pending batch of the current Run; its presence is the question-mode owner. */
  activeRun: RunInputsProjection | null;
  activeBatches: RunInputQuestionBatch[];
  /** Current-Run accepted batches keep a brief acknowledgement above history. */
  acceptedBatches: RunInputQuestionBatch[];
  /** Closed, accepted and unprocessed records, newest first. */
  receipts: RunInputReceiptRun[];
  receiptCount: number;
  hasReceipts: boolean;
  historyLoadFailed: boolean;
  historyHasMore: boolean;
  historyIsLoading: boolean;
}

const EMPTY: RunInputSurface = {
  activeRun: null,
  activeBatches: [],
  acceptedBatches: [],
  receipts: [],
  receiptCount: 0,
  hasReceipts: false,
  historyLoadFailed: false,
  historyHasMore: false,
  historyIsLoading: false,
};

/** One derivation of which task inputs are still questions and which are receipts,
 *  shared by the Composer slot owner and the question surface itself. */
export function deriveRunInputSurface(runInputs: RunInputsController | null | undefined): RunInputSurface {
  if (!runInputs) return EMPTY;
  const runs = runInputs.history.map((projection) => {
    const closed = projection.run_id !== runInputs.runId || runInputs.isClosed || projection.state !== "open";
    const inputs = projection.inputs.flatMap((input) => {
      const batch = input.kind === "answer" ? projection.questions.find((question) => question.question_id === input.question_id) : undefined;
      const unprocessed = input.status === "closed" || (closed && (input.status === "queued" || (input.kind === "answer" && batch !== undefined && batch.status !== "resolved")));
      const autoAnswer = input.source === "timeout_default";
      if ((input.status === "applied" && !unprocessed && !autoAnswer) || (input.kind === "answer" && !input.answers)) return [];
      return [{ input, batch, unprocessed }];
    });
    const questions = projection.questions.filter((batch) => {
      if (batch.status === "resolved") return false;
      if (!closed && projection.inputs.some((input) => input.kind === "answer" && input.question_id === batch.question_id && input.status === "applied")) return false;
      return batch.status === "pending" || batch.status === "closed" ||
        !projection.inputs.some((input) => input.kind === "answer" && input.question_id === batch.question_id);
    });
    return { projection, closed, inputs, questions };
  }).filter((run) => run.inputs.length || run.questions.length);
  const current = runs.find((run) => !run.closed) ?? null;
  const activeBatches = current?.questions.filter((batch) => batch.status === "pending") ?? [];
  const acceptedBatches = current?.questions.filter((batch) => batch.status === "answered") ?? [];
  const visible = [...(current ? [current] : []), ...runs.filter((run) => run !== current).reverse()];
  const receipts = visible.map((run) => ({
    ...run,
    questions: run.questions.filter((batch) => run !== current || batch.status !== "pending"),
  })).filter((run) => run.inputs.length || run.questions.length);
  return {
    activeRun: current?.projection ?? null,
    activeBatches,
    acceptedBatches,
    receipts,
    receiptCount: receipts.reduce((count, run) => count + run.inputs.length + run.questions.length, 0),
    hasReceipts: receipts.length > 0 || runInputs.historyLoadFailed || runInputs.historyHasMore || runInputs.historyIsLoading,
    historyLoadFailed: runInputs.historyLoadFailed,
    historyHasMore: runInputs.historyHasMore,
    historyIsLoading: runInputs.historyIsLoading,
  };
}
