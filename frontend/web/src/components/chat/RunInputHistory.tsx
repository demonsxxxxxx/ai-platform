import { Button } from "@lobehub/ui/base-ui";
import { ChevronDown } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { RunInputsController } from "../../hooks/useAgent/types";
import { OptionList } from "./assistant-ui/elements/option-list";
import { RunQuestionCard } from "./RunQuestionCard";
import { presentRunInputAnswer, presentRunInputOption } from "./runInputPresentation";
import { deriveRunInputSurface, type RunInputReceiptRun } from "./runInputSurface";

function ReceiptEntries({
  receipts,
  runInputs,
}: {
  receipts: RunInputReceiptRun[];
  runInputs: RunInputsController;
}) {
  const { t } = useTranslation();
  return (
    <>
      {receipts.map(({ projection, closed, inputs, questions }) => {
        // A receipt Run is read-only; the shared controller still owns the record.
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
    </>
  );
}

/** Task questions and their records. A pending batch of the current Run is question
 *  mode; closed, accepted and unprocessed records stay behind one collapsed history
 *  disclosure so they never consume the active question's height. */
export function RunInputHistory({
  runInputs,
  canSend,
  stop,
}: {
  runInputs: RunInputsController;
  canSend: boolean;
  /** The Composer's own stop control, rendered once in the question footer. */
  stop?: ReactNode;
}) {
  const { t } = useTranslation();
  const surface = deriveRunInputSurface(runInputs);
  const activeRun = surface.activeRun;
  const [historyOpen, setHistoryOpen] = useState(false);
  const receiptsRef = useRef<HTMLDivElement>(null);
  const pendingKey = surface.activeBatches.map((batch) => batch.question_id).join(",");

  useEffect(() => {
    // Disclosure state belongs to the current owner; another session or Run must
    // not inherit an expanded history.
    setHistoryOpen(false);
  }, [runInputs.sessionId, runInputs.runId, pendingKey]);

  useLayoutEffect(() => {
    // A newly arrived question is the reader's next task: show it from its start.
    if (receiptsRef.current) receiptsRef.current.scrollTop = 0;
  }, [pendingKey]);

  if (!activeRun && !surface.hasReceipts) return null;

  return (
    <div className="flex min-w-0 flex-col gap-3 break-words" data-run-input-history>
      {activeRun && surface.activeBatches.length > 0 ? (
        <section
          data-run-input-history-run={activeRun.run_id}
          data-run-input-read-only={false}
          className="flex min-w-0 flex-col gap-3"
        >
          {surface.activeBatches.map((batch) => (
            <RunQuestionCard
              key={batch.question_id}
              batch={batch}
              runInputs={{ ...runInputs, projection: activeRun, runId: activeRun.run_id, isClosed: false }}
              canSend={canSend}
              stop={stop}
            />
          ))}
        </section>
      ) : null}
      {activeRun && surface.acceptedBatches.length > 0 ? (
        <p data-run-input-answer-receipt className="text-xs text-[var(--theme-text-secondary)]">
          {t("chat.runInputs.answerWaiting", "答案已提交，等待当前任务继续。")}
        </p>
      ) : null}
      {surface.historyLoadFailed ? (
        <div className="flex shrink-0 items-center justify-between gap-3 text-xs text-[var(--theme-warning)]" role="status" data-run-input-history-failure>
          <span>{t("chat.runInputs.historyLoadFailed", "暂时无法恢复任务输入历史，已有记录仍保留。")}</span>
          <Button type="text" className="shrink-0 underline" onClick={() => void runInputs.refreshHistory()} htmlType="button">
            {t("common.retry", "重试")}
          </Button>
        </div>
      ) : null}
      {surface.historyHasMore ? (
        <Button type="text" className="shrink-0 self-center text-xs text-[var(--theme-text-secondary)] underline disabled:opacity-50" disabled={surface.historyIsLoading} onClick={() => void runInputs.loadMoreHistory()} htmlType="button" data-run-input-load-more>
          {t("chat.runInputs.loadEarlier", "读取更早的任务输入")}
        </Button>
      ) : null}
      {surface.historyIsLoading ? <p className="shrink-0 text-xs text-[var(--theme-text-secondary)]" role="status">{t("chat.runInputs.historyLoading", "正在恢复任务输入历史…")}</p> : null}
      {surface.receipts.length > 0 ? (
        <details
          className="shrink-0 rounded-xl border border-[var(--theme-border)]"
          data-run-input-history-disclosure
          onToggle={(event) => setHistoryOpen(event.currentTarget.open)}
          open={historyOpen}
        >
          <summary
            className="flex min-h-11 cursor-pointer list-none items-center gap-2 px-3 text-xs text-[var(--theme-text-secondary)] marker:content-none"
            data-run-input-history-toggle
          >
            <ChevronDown aria-hidden className="shrink-0 transition-transform" size={14} style={{ rotate: historyOpen ? undefined : "-90deg" }} />
            <span>{t("chat.runInputs.historyTitle", "历史答复 · {{count}} 条", { count: surface.receiptCount })}</span>
          </summary>
          {/* One shared scroll region for every Run's title, closed question and
              receipt: pagination never starves a single Run's height. */}
          <div className="max-h-[min(20dvh,12rem)] overflow-y-auto overscroll-contain px-3 pb-3" data-run-input-history-panel>
            <div ref={receiptsRef} className="flex flex-col gap-3" data-run-input-receipts>
              <ReceiptEntries receipts={surface.receipts} runInputs={runInputs} />
            </div>
          </div>
        </details>
      ) : null}
    </div>
  );
}
