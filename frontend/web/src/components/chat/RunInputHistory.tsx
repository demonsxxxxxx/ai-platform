import { Button } from "@lobehub/ui/base-ui";
import { useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { RunInputsController } from "../../hooks/useAgent/types";
import { OptionList } from "./assistant-ui/elements/option-list";
import { RunQuestionCard } from "./RunQuestionCard";
import { presentRunInputAnswer, presentRunInputOption } from "./runInputPresentation";

/** Task questions and their receipts. One surface, styled as the Composer's
 *  upper section rather than a second card stacked above it. */
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
  const current = hasContent.find((run) => !run.closed);
  const visibleRuns = [...(current ? [current] : []), ...hasContent.filter((run) => run !== current).reverse()];
  const activeQuestions = current?.questions.filter((batch) => batch.status === "pending") ?? [];
  const receiptRuns = visibleRuns.map((run) => ({
    ...run,
    questions: run.questions.filter((batch) => run.closed || batch.status !== "pending"),
  })).filter((run) => run.inputs.length || run.questions.length);
  const hasReceipts = receiptRuns.length > 0 || runInputs.historyLoadFailed || runInputs.historyHasMore || runInputs.historyIsLoading;
  const pendingQuestions = activeQuestions.map((batch) => batch.question_id).join(",");
  useLayoutEffect(() => {
    if (pendingQuestions && containerRef.current) containerRef.current.scrollTop = 0;
  }, [pendingQuestions, runInputs.runId]);
  if (!visibleRuns.length && !runInputs.historyIsLoading && !runInputs.historyLoadFailed && !runInputs.historyHasMore) return null;
  return (
    <div className="mx-auto flex min-h-0 w-full max-w-[68rem] flex-1 flex-col gap-2 break-words px-2" data-run-input-history>
      {current && activeQuestions.length > 0 ? (
        <section
          data-run-input-history-run={current.projection.run_id}
          data-run-input-read-only={false}
          className="flex min-h-0 flex-1 flex-col gap-3"
        >
          {activeQuestions.map((batch) => (
            <RunQuestionCard
              key={batch.question_id}
              batch={batch}
              runInputs={{ ...runInputs, projection: current.projection, runId: current.projection.run_id, isClosed: false }}
              canSend={canSend}
            />
          ))}
        </section>
      ) : null}
      {hasReceipts ? (
        <div
          ref={containerRef}
          className="flex min-h-0 flex-col gap-3 overflow-y-auto overscroll-contain"
          style={{ flex: activeQuestions.length > 0 ? "0 1 min(16dvh, 6rem)" : "1 1 auto" }}
          data-run-input-receipts
        >
      {runInputs.historyLoadFailed ? (
        <div className="flex shrink-0 items-center justify-between gap-3 text-xs text-[var(--theme-warning)]" role="status" data-run-input-history-failure>
          <span>{t("chat.runInputs.historyLoadFailed", "暂时无法恢复任务输入历史，已有记录仍保留。")}</span>
          <Button type="text" className="shrink-0 underline" onClick={() => void runInputs.refreshHistory()} htmlType="button">
            {t("common.retry", "重试")}
          </Button>
        </div>
      ) : null}
      {runInputs.historyHasMore ? (
        <Button type="text" className="shrink-0 self-center text-xs text-[var(--theme-text-secondary)] underline disabled:opacity-50" disabled={runInputs.historyIsLoading} onClick={() => void runInputs.loadMoreHistory()} htmlType="button" data-run-input-load-more>
          {t("chat.runInputs.loadEarlier", "读取更早的任务输入")}
        </Button>
      ) : null}
      {runInputs.historyIsLoading ? <p className="shrink-0 text-xs text-[var(--theme-text-secondary)]" role="status">{t("chat.runInputs.historyLoading", "正在恢复任务输入历史…")}</p> : null}
      {receiptRuns.map(({ projection, closed, inputs, questions }) => {
        const controls = { ...runInputs, projection, runId: projection.run_id, isClosed: closed };
        return (
          <section
            key={projection.run_id}
            data-run-input-history-run={projection.run_id}
            data-run-input-read-only={closed}
            className="flex shrink-0 flex-col gap-3"
          >
            {closed ? (
              <p className="shrink-0 text-xs text-[var(--theme-text-secondary)]">
                {t("chat.runInputs.historyTask", "历史任务输入")}
              </p>
            ) : null}
            {questions.map((batch) => (
              <RunQuestionCard key={batch.question_id} batch={batch} runInputs={controls} canSend={false} />
            ))}
            {inputs.map(({ input, batch, unprocessed }) => {
              const status = unprocessed ? t("chat.runInputs.unprocessed", "任务已结束，未处理") : input.status === "applied" ? t("chat.runInputs.processed", "已处理") : t("chat.runInputs.queued", "排队中");
              return (
                <article className="border-t border-[var(--theme-border)] pt-3 first:border-t-0 first:pt-0" data-run-input-entry={input.kind} key={input.input_id}>
                  <div className="mb-1 flex items-center justify-between gap-3 text-xs text-[var(--theme-text-secondary)]">
                    <span>{input.kind === "text" ? t("chat.runInputs.textEntry", "补充到当前任务") : input.source === "timeout_default" ? t("chat.runInputs.autoAnswerEntry", "系统默认答复") : t("chat.runInputs.answerEntry", "已提交答复")}</span>
                    <span className="shrink-0">{status}</span>
                  </div>
                  {input.kind === "text" ? <p className="whitespace-pre-wrap text-sm text-[var(--theme-text)]">{input.text}</p> : (
                    <div className="space-y-1">
                      {Object.entries(input.answers ?? {}).map(([key, answer]) => {
                        const display = presentRunInputAnswer(key, answer, batch);
                        const question = batch?.questions.find((item) => item.key === key);
                        const choice = typeof answer === "string" ? [answer] : Array.isArray(answer) ? answer : undefined;
                        return (
                          <div key={key}>
                            <span className="text-sm font-medium text-[var(--theme-text-secondary)]">{display.question ?? t("chat.runInputs.questionUnavailable", "问题")} </span>
                            {question && choice ? (
                              <OptionList aria-label={question.question} choice={choice} options={question.options.map((option) => ({
                                id: option.key,
                                label: presentRunInputOption(question, option.key) ?? option.label,
                                description: option.description,
                              }))} />
                            ) : (
                              <span>{display.answer ?? t("chat.runInputs.answerUnavailable", "答案暂不可用")}</span>
                            )}
                          </div>
                        );
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
      ) : null}
    </div>
  );
}
