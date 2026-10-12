import { useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { RunInputsController } from "../../hooks/useAgent/types";
import { RunQuestionCard } from "./RunQuestionCard";
import { presentRunInputAnswer } from "./runInputPresentation";

export function RunInputHistory({
  runInputs,
  canSend,
}: {
  runInputs: RunInputsController;
  canSend: boolean;
}) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const hasContent = runInputs.history.map((projection) => {
    const closed = projection.run_id !== runInputs.runId || runInputs.isClosed || projection.state !== "open";
    const inputs = projection.inputs.flatMap((input) => {
      const batch = input.kind === "answer" ? projection.questions.find((question) => question.question_id === input.question_id) : undefined;
      const unprocessed = input.status === "closed" || (closed && (input.status === "queued" || (input.kind === "answer" && batch !== undefined && batch.status !== "resolved")));
      if ((input.status === "applied" && !unprocessed) || (input.kind === "answer" && !input.answers)) return [];
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
  const current = hasContent.find((run) => !run.closed);
  const visibleRuns = [...(current ? [current] : []), ...hasContent.filter((run) => run !== current).reverse()];
  const pendingQuestions = current?.questions.filter((batch) => batch.status === "pending").map((batch) => batch.question_id).join(",") ?? "";
  useLayoutEffect(() => {
    if (pendingQuestions && containerRef.current) containerRef.current.scrollTop = 0;
  }, [pendingQuestions, runInputs.runId]);
  if (!visibleRuns.length && !runInputs.historyIsLoading && !runInputs.historyLoadFailed && !runInputs.historyHasMore) return null;
  return (
    <div ref={containerRef} className="mx-auto mb-2 flex max-h-[min(30dvh,18rem)] w-full max-w-[68rem] flex-col gap-2 overflow-y-auto overscroll-contain scroll-pb-20 break-words px-2" data-run-input-history>
      {runInputs.historyLoadFailed ? (
        <div className="flex items-center justify-between gap-3 text-xs text-[var(--theme-warning)]" role="status" data-run-input-history-failure>
          <span>{t("chat.runInputs.historyLoadFailed", "暂时无法恢复任务输入历史，已有记录仍保留。")}</span>
          <button className="shrink-0 underline" onClick={() => void runInputs.refreshHistory()} type="button">
            {t("common.retry", "重试")}
          </button>
        </div>
      ) : null}
      {runInputs.historyHasMore ? (
        <button className="self-center text-xs text-[var(--theme-text-secondary)] underline disabled:opacity-50" disabled={runInputs.historyIsLoading} onClick={() => void runInputs.loadMoreHistory()} type="button" data-run-input-load-more>
          {t("chat.runInputs.loadEarlier", "读取更早的任务输入")}
        </button>
      ) : null}
      {runInputs.historyIsLoading ? <p className="text-xs text-[var(--theme-text-secondary)]" role="status">{t("chat.runInputs.historyLoading", "正在恢复任务输入历史…")}</p> : null}
      {visibleRuns.map(({ projection, closed, inputs, questions }) => {
        const controls = { ...runInputs, projection, runId: projection.run_id, isClosed: closed };
        return (
          <section className="flex flex-col gap-2" key={projection.run_id} data-run-input-history-run={projection.run_id} data-run-input-read-only={closed}>
            <p className="text-xs text-[var(--theme-text-secondary)]">{closed ? t("chat.runInputs.historyTask", "历史任务输入") : t("chat.runInputs.currentTask", "当前任务输入")}</p>
            {questions.map((batch) => (
              <RunQuestionCard key={batch.question_id} batch={batch} runInputs={controls} canSend={canSend && !closed} />
            ))}
            {inputs.map(({ input, batch, unprocessed }) => {
              const status = unprocessed ? t("chat.runInputs.unprocessed", "任务已结束，未处理") : t("chat.runInputs.queued", "排队中");
              return (
                <article className="rounded-xl border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-3 py-2" data-run-input-entry={input.kind} key={input.input_id}>
                  <div className="mb-1 flex items-center justify-between gap-3 text-xs text-[var(--theme-text-secondary)]">
                    <span>{input.kind === "text" ? t("chat.runInputs.textEntry", "补充到当前任务") : t("chat.runInputs.answerEntry", "已提交答复")}</span>
                    <span>{status}</span>
                  </div>
                  {input.kind === "text" ? <p className="whitespace-pre-wrap text-sm text-[var(--theme-text)]">{input.text}</p> : (
                    <div className="space-y-1">
                      {Object.entries(input.answers ?? {}).map(([key, answer]) => {
                        const display = presentRunInputAnswer(key, answer, batch);
                        return <p className="whitespace-pre-wrap text-sm text-[var(--theme-text)]" key={key}>
                          <span className="font-medium">{display.question ?? t("chat.runInputs.questionUnavailable", "问题")} </span>
                          {display.answer ?? t("chat.runInputs.answerUnavailable", "答案暂不可用")}
                        </p>;
                      })}
                    </div>
                  )}
                </article>
              );
            })}
          </section>
        );
      })}
    </div>
  );
}
