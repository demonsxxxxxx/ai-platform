import { Button, TextArea } from "@lobehub/ui/base-ui";
import { Pencil } from "lucide-react";
import { clsx } from "clsx";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { RunInputAnswer, RunInputQuestion, RunInputQuestionBatch } from "../../services/api/session";
import type { RunInputsController } from "../../hooks/useAgent/types";
import { OptionList } from "./assistant-ui/elements/option-list";
import { presentRunInputAnswer, presentRunInputOption } from "./runInputPresentation";

interface QuestionDraft {
  selected: string[];
  text: string;
  /** Choice and free text are exclusive; the open editor owns the answer. */
  textOpen: boolean;
}

const EMPTY_DRAFT: QuestionDraft = { selected: [], text: "", textOpen: false };

/** Server-shaped completeness for one question of the batch. */
function draftAnswer(question: RunInputQuestion, draft: QuestionDraft): RunInputAnswer | null {
  if (question.options.length === 0) return draft.text.trim() ? { text: draft.text.trim() } : null;
  if (draft.textOpen) return draft.text.trim() ? { text: draft.text.trim() } : null;
  if (question.multiSelect) return draft.selected.length > 0 ? draft.selected : null;
  return draft.selected[0] ? draft.selected[0] : null;
}

/** One pending batch as numbered steps. Steps are local drafting positions:
 *  only the final submit path reaches the server, which owns answer acceptance.
 *  The card is intrinsically sized: ordinary short questions show their choices
 *  and actions without an inner scroll, and only exceptional content falls back
 *  to the Composer region's single scroll. */
export function RunQuestionCard({
  batch,
  runInputs,
  canSend,
  stop,
}: {
  batch: RunInputQuestionBatch;
  runInputs: RunInputsController;
  canSend: boolean;
  /** The Composer's own stop control, rendered once in the question footer. */
  stop?: ReactNode;
}) {
  const { t } = useTranslation();
  const [drafts, setDrafts] = useState<Record<string, QuestionDraft>>({});
  const [activeStep, setActiveStep] = useState(0);
  const [answerAttempted, setAnswerAttempted] = useState(false);
  const headingRef = useRef<HTMLSpanElement>(null);
  const otherAnswerRef = useRef<HTMLTextAreaElement>(null);
  const movingStepRef = useRef(false);

  const draftOf = (key: string) => drafts[key] ?? EMPTY_DRAFT;
  const updateDraft = (question: string, update: (draft: QuestionDraft) => QuestionDraft) => {
    setDrafts((previous) => ({
      ...previous,
      [question]: update(previous[question] ?? EMPTY_DRAFT),
    }));
    setAnswerAttempted(false);
  };

  const answers: Record<string, RunInputAnswer> = {};
  for (const question of batch.questions) {
    const answer = draftAnswer(question, draftOf(question.key));
    if (answer !== null) answers[question.key] = answer;
  }
  const total = batch.questions.length;
  const step = Math.min(activeStep, total - 1);
  const question = batch.questions[step];
  const answered = batch.questions.map((item) => draftAnswer(item, draftOf(item.key)) !== null);

  const pending = runInputs.pendingSubmission;
  const busy = Boolean(pending);
  const uncertainAnswer = pending?.kind === "answer" &&
    pending.questionId === batch.question_id && pending.state === "uncertain";
  const readOnly =
    !canSend || batch.status !== "pending" || runInputs.isClosed || runInputs.projection?.state !== "open";
  const closedUnprocessed =
    (runInputs.isClosed || runInputs.projection?.state !== "open") &&
    (batch.status === "pending" || batch.status === "answered");
  const displayStatus = closedUnprocessed ? "closed" : batch.status;
  const submittedInput = runInputs.projection?.inputs.find(
    (input) => input.kind === "answer" && input.question_id === batch.question_id,
  );
  const submittedAnswers = submittedInput?.answers;

  useEffect(() => {
    // Move focus only when the reader changes step; mounting must not steal the
    // Composer's focus or interrupt the ordinary typed draft.
    if (!movingStepRef.current) return;
    movingStepRef.current = false;
    headingRef.current?.focus();
  }, [step]);

  if (submittedInput || displayStatus !== "pending") {
    const status = closedUnprocessed
      ? t("chat.runInputs.answerClosedUnprocessed", "当前任务输入已关闭，问题未完成处理。")
      : submittedInput?.source === "timeout_default"
        ? t("chat.runInputs.answerAutoSelected", "超过5分钟未收到答复，系统已自动选择默认答复。")
        : displayStatus === "resolved"
        ? t("chat.runInputs.answerResolved", "问题已处理。")
        : submittedInput || batch.status === "answered"
        ? t("chat.runInputs.answerWaiting", "答案已提交，等待当前任务继续。")
        : t("chat.runInputs.answerClosed", "问题已关闭。");
    return (
      <section data-run-question-status={displayStatus}>
        <p className="mb-2 text-xs text-[var(--theme-text-secondary)]">{status}</p>
        {batch.questions.map((item, index) => {
          const answer = submittedAnswers?.[item.key];
          const choice = typeof answer === "string" ? [answer] : Array.isArray(answer) ? answer : undefined;
          return (
            <div className="mt-2" key={item.key}>
              <p className="mb-1 flex items-start gap-2 text-sm text-[var(--theme-text)]">
                <span aria-hidden className="mt-px text-xs font-semibold tabular-nums text-[var(--theme-text-secondary)]">
                  {index + 1}.
                </span>
                <span className="min-w-0">{item.question}</span>
              </p>
              {choice ? (
                <OptionList aria-label={item.question} choice={choice} options={item.options.map((option) => ({
                  id: option.key,
                  label: presentRunInputOption(item, option.key) ?? option.label,
                  description: option.description,
                }))} />
              ) : answer && typeof answer === "object" && !Array.isArray(answer) ? (
                <p className="whitespace-pre-wrap text-sm text-[var(--theme-text)]">{answer.text}</p>
              ) : null}
            </div>
          );
        })}
      </section>
    );
  }

  const draft = draftOf(question.key);
  const lastStep = step === total - 1;
  const currentAnswered = draftAnswer(question, draft) !== null;
  const firstUnanswered = answered.indexOf(false);
  const hasChoices = question.options.length > 0;
  const otherAnswerVisible = !hasChoices || draft.textOpen;
  const openOtherAnswer = () => {
    updateDraft(question.key, (previous) => ({ ...previous, textOpen: true }));
    requestAnimationFrame(() => otherAnswerRef.current?.focus());
  };
  const advance = () => {
    if (readOnly || busy) return;
    if (!lastStep) {
      if (!currentAnswered) {
        setAnswerAttempted(true);
        return;
      }
      setAnswerAttempted(false);
      movingStepRef.current = true;
      setActiveStep(step + 1);
      return;
    }
    setAnswerAttempted(true);
    if (firstUnanswered >= 0) return;
    void runInputs.submitAnswers(batch.question_id, answers);
  };

  return (
    <section className="flex min-w-0 flex-col gap-3" data-run-question-card data-run-question-step={step + 1}>
      <div className="flex min-w-0 items-center gap-3">
        <ol
          className="flex shrink-0 items-center gap-1"
          data-run-question-progress
        >
          <span className="sr-only">
            {t("chat.runInputs.questionStep", "第 {{current}}/{{total}} 题", { current: step + 1, total })}
          </span>
          {batch.questions.map((item, index) => (
            <li key={item.key} className="flex items-center gap-1">
              {index > 0 ? (
                <span aria-hidden className={clsx("h-px w-3", index <= step ? "bg-[var(--theme-text)]" : "bg-[var(--theme-border)]")} />
              ) : null}
              <span
                aria-current={index === step ? "step" : undefined}
                className={clsx(
                  "flex size-7 items-center justify-center rounded-full text-xs font-semibold tabular-nums",
                  index === step
                    ? "bg-[var(--theme-text)] text-[var(--theme-bg-card)]"
                    : answered[index]
                      ? "bg-[var(--theme-bg-sidebar)] text-[var(--theme-text)]"
                      : "bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)]",
                )}
                data-run-question-marker={index + 1}
              >
                {index + 1}
              </span>
            </li>
          ))}
        </ol>
        <p className="min-w-0 truncate text-xs text-[var(--theme-text-secondary)]" data-run-question-mode-heading>
          {t("chat.runInputs.confirmTask", "确认任务信息")}
        </p>
      </div>
      {/* The Composer owns the surrounding form; a nested form is invalid HTML and
          its submit would post the ordinary draft. Steps advance by explicit calls.
          Enter is a shortcut of the answer textarea only, so option, previous and
          retry buttons keep their native keyboard activation. */}
      <fieldset
        className="min-w-0 border-0 p-0"
        data-run-question-index={step + 1}
        onKeyDown={(event) => {
          // Enter submits from the answer textarea only: option, previous and
          // retry buttons keep native activation, IME composition never advances.
          if (event.key !== "Enter" || event.shiftKey) return;
          if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
          // Compare by tag: the composing textarea is the only shortcut owner.
          if ((event.target as HTMLElement | null)?.tagName !== "TEXTAREA") return;
          event.preventDefault();
          advance();
        }}
      >
        <legend className="flex w-full min-w-0 flex-col gap-1">
          <span
            className="block break-words text-base font-semibold leading-6 text-[var(--theme-text)] outline-none"
            ref={headingRef}
            tabIndex={-1}
          >
            {question.question}
          </span>
          {question.header ? (
            <span className="block text-xs font-normal text-[var(--theme-text-secondary)]">{question.header}</span>
          ) : null}
        </legend>
        {step === 0 && batch.deadline_at ? (
          <p className="mb-2 text-xs leading-5 text-[var(--theme-text-secondary)]" role="status">
            {t("chat.runInputs.defaultAnswerNotice", "5分钟未提交将自动采用推荐项；无选项的问题会结束任务。")}
          </p>
        ) : null}
        {batch.default_answers?.[question.key] !== undefined ? (
          <p className="mb-2 text-xs text-[var(--theme-text-secondary)]" data-run-question-default>
            {t("chat.runInputs.defaultChoice", "到期默认答复：{{answer}}", {
              answer: presentRunInputAnswer(question.key, batch.default_answers[question.key], batch).answer,
            })}
          </p>
        ) : null}
        {hasChoices ? (
          <OptionList
            aria-label={question.question}
            className="mt-1"
            disabled={readOnly || busy}
            onValueChange={(selected) =>
              // A choice is the explicit answer; a typed alternative stays in the
              // draft so reopening its editor never loses it.
              updateDraft(question.key, (previous) => ({ ...previous, selected, textOpen: false }))
            }
            options={question.options.map((option) => ({
              id: option.key,
              label: presentRunInputOption(question, option.key) ?? option.label,
              description: option.description,
            }))}
            selectionMode={question.multiSelect ? "multiple" : "single"}
            value={draft.selected}
          />
        ) : null}
        {otherAnswerVisible ? (
          <div className="mt-2 min-w-0">
            {hasChoices ? (
              <label className="mb-1 block text-xs text-[var(--theme-text-secondary)]" htmlFor={`run-question-other-${batch.question_id}-${question.key}`}>
                {t("chat.runInputs.otherAnswerLabel", "其他答案")}
              </label>
            ) : null}
            <TextArea
              aria-label={t("chat.runInputs.freeText", "自由填写答案：{{question}}", {
                question: question.question,
              })}
              className="w-full resize-y rounded-xl border border-[var(--theme-border)] bg-transparent px-3 py-2 text-sm text-[var(--theme-text)] outline-none placeholder:text-[var(--theme-text-secondary)] focus:border-[var(--theme-primary)]"
              disabled={readOnly || busy}
              id={`run-question-other-${batch.question_id}-${question.key}`}
              maxLength={16_000}
              onChange={(event) => {
                // Read before the updater runs: a controlled textarea has already
                // been restored by the time React processes the state update.
                const text = event.target.value;
                updateDraft(question.key, (previous) => ({ ...previous, selected: [], text, textOpen: true }));
              }}
              placeholder={t("chat.runInputs.freeTextPlaceholder", "填写其他答案")}
              ref={otherAnswerRef}
              rows={2}
              style={{ minHeight: 44 }}
              value={draft.text}
            />
            {hasChoices ? (
              <Button
                type="text"
                disabled={readOnly || busy}
                htmlType="button"
                data-run-question-other-clear
                onClick={() => {
                  // Back to the options; the typed text is kept for a later visit.
                  updateDraft(question.key, (previous) => ({ ...previous, textOpen: false }));
                  headingRef.current?.focus();
                }}
                style={{ height: "auto", minHeight: 40, padding: "4px 0", color: "var(--theme-text-secondary)" }}
              >
                {t("chat.runInputs.useOptions", "改用选项作答")}
              </Button>
            ) : null}
          </div>
        ) : (
          <Button
            type="text"
            disabled={readOnly || busy}
            htmlType="button"
            icon={<Pencil aria-hidden size={14} />}
            data-run-question-other-toggle
            onClick={openOtherAnswer}
            style={{ height: "auto", minHeight: 40, marginTop: 4, padding: "4px 0", color: "var(--theme-text-secondary)" }}
          >
            {t("chat.runInputs.otherAnswer", "填写其他答案")}
          </Button>
        )}
      </fieldset>
      <div className="flex flex-col gap-2">
        {answerAttempted && !currentAnswered && !lastStep ? (
          <p className="text-xs text-[var(--theme-danger)]" role="alert">
            {t("chat.runInputs.answerCurrentRequired", "请先回答当前问题。")}
          </p>
        ) : null}
        {answerAttempted && lastStep && firstUnanswered >= 0 ? (
          <p className="text-xs text-[var(--theme-danger)]" role="alert">
            {t("chat.runInputs.answerRequiredAt", "请先回答第 {{index}} 题。", {
              index: firstUnanswered + 1,
            })}
          </p>
        ) : null}
        {uncertainAnswer && !readOnly ? (
          <p className="text-xs text-[var(--theme-warning)]" role="alert">
            {t("chat.runInputs.answerUncertain", "尚未确认答案是否提交成功，可重试提交原答案。")}
          </p>
        ) : null}
        {runInputs.submissionError === "rejected" ? (
          <p className="text-xs text-[var(--theme-danger)]" role="alert">
            {t("chat.runInputs.answerRejected", "答案未被接受，选择仍保留。")}
          </p>
        ) : null}
        {runInputs.isClosed ? (
          <p className="text-xs text-[var(--theme-warning)]" role="status">
            {t("chat.runInputs.answerRunClosed", "当前任务暂不接收答案，填写内容仍保留。")}
          </p>
        ) : null}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            {step > 0 ? (
              <Button
                type="default"
                disabled={busy}
                htmlType="button"
                data-run-question-previous
                onClick={() => {
                  setAnswerAttempted(false);
                  movingStepRef.current = true;
                  setActiveStep(step - 1);
                }}
                style={{ height: "auto", minHeight: 40, padding: "8px 14px" }}
              >
                {t("chat.runInputs.previousQuestion", "上一题")}
              </Button>
            ) : null}
            {uncertainAnswer && !readOnly ? (
              <Button
                type="default"
                disabled={pending?.state === "submitting"}
                htmlType="button"
                onClick={() => void runInputs.retryPendingSubmission()}
                style={{ height: "auto", minHeight: 40, padding: "8px 14px" }}
              >
                {t("chat.runInputs.retryAnswer", "重试提交答复")}
              </Button>
            ) : null}
          </div>
          <Button
            type={readOnly ? "default" : "primary"}
            disabled={readOnly || busy}
            htmlType="button"
            data-run-question-advance={lastStep ? "submit" : "next"}
            loading={lastStep && pending?.kind === "answer" &&
              pending.questionId === batch.question_id && pending.state === "submitting"}
            onClick={advance}
            style={{ height: "auto", minHeight: 40, padding: "8px 18px" }}
          >
            {lastStep
              ? pending?.kind === "answer" &&
                pending.questionId === batch.question_id &&
                pending.state === "submitting"
                ? t("chat.runInputs.submittingAnswer", "正在提交…")
                : t("chat.runInputs.submitAnswer", "提交答复")
              : t("chat.runInputs.nextQuestion", "下一题")}
          </Button>
        </div>
        {total > 1 || stop ? (
          <div className="flex items-center justify-between gap-3 border-t border-[var(--theme-border)] pt-2 text-xs text-[var(--theme-text-secondary)]">
            <span className="min-w-0" data-run-question-batch-note>
              {total > 1
                ? t("chat.runInputs.submitWholeBatch", "答完 {{total}} 题后统一提交", { total })
                : null}
            </span>
            {stop}
          </div>
        ) : null}
      </div>
    </section>
  );
}
