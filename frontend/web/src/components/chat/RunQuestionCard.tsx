import { Button, TextArea } from "@lobehub/ui/base-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { RunInputAnswer, RunInputQuestionBatch } from "../../services/api/session";
import type { RunInputsController } from "../../hooks/useAgent/types";
import { OptionList } from "./assistant-ui/elements/option-list";
import { presentRunInputAnswer, presentRunInputOption } from "./runInputPresentation";

interface QuestionDraft {
  selected: string[];
  text: string;
}

export function RunQuestionCard({
  batch,
  runInputs,
  canSend,
}: {
  batch: RunInputQuestionBatch;
  runInputs: RunInputsController;
  canSend: boolean;
}) {
  const { t } = useTranslation();
  const [drafts, setDrafts] = useState<Record<string, QuestionDraft>>({});
  const [answerAttempted, setAnswerAttempted] = useState(false);
  const updateDraft = (question: string, update: (draft: QuestionDraft) => QuestionDraft) => {
    setDrafts((previous) => ({
      ...previous,
      [question]: update(previous[question] ?? { selected: [], text: "" }),
    }));
    setAnswerAttempted(false);
  };

  const answers: Record<string, RunInputAnswer> = {};
  for (const question of batch.questions) {
    const draft = drafts[question.key] ?? { selected: [], text: "" };
    if (draft.text.trim()) {
      answers[question.key] = { text: draft.text.trim() };
    } else if (question.multiSelect && draft.selected.length > 0) {
      answers[question.key] = draft.selected;
    } else if (!question.multiSelect && draft.selected[0]) {
      answers[question.key] = draft.selected[0];
    }
  }

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
  const alreadySubmitted = Boolean(submittedInput);
  const automaticAnswer = submittedInput?.source === "timeout_default";
  const submittedAnswers = submittedInput?.answers;
  if (alreadySubmitted || displayStatus !== "pending") {
    const status = closedUnprocessed
      ? t("chat.runInputs.answerClosedUnprocessed", "当前任务输入已关闭，问题未完成处理。")
      : automaticAnswer
        ? t("chat.runInputs.answerAutoSelected", "超过5分钟未收到答复，系统已自动选择默认答复。")
        : displayStatus === "resolved"
        ? t("chat.runInputs.answerResolved", "问题已处理。")
        : alreadySubmitted || batch.status === "answered"
        ? t("chat.runInputs.answerWaiting", "答案已提交，等待当前任务继续。")
        : t("chat.runInputs.answerClosed", "问题已关闭。");
    return (
      <section
        className="rounded-xl border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-3 py-2"
        data-run-question-status={displayStatus}
      >
        <p className="mb-2 text-xs text-[var(--theme-text-secondary)]">{status}</p>
        {batch.questions.map((question) => {
          const answer = submittedAnswers?.[question.key];
          const choice = typeof answer === "string" ? [answer] : Array.isArray(answer) ? answer : undefined;
          return (
            <div className="mt-2" key={question.key}>
              <p className="mb-1 text-sm text-[var(--theme-text)]">{question.question}</p>
              {choice ? (
                <OptionList aria-label={question.question} choice={choice} options={question.options.map((option) => ({
                  id: option.key,
                  label: presentRunInputOption(question, option.key) ?? option.label,
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

  return (
    <section
      className="rounded-2xl border border-[var(--theme-primary)]/35 bg-[var(--theme-bg-card)] p-3 shadow-sm"
      data-run-question-card
    >
      <div className="mb-2 text-xs font-semibold text-[var(--theme-primary)]">
        {t("chat.runInputs.questionTitle", "当前任务需要你的答复")}
      </div>
      {batch.deadline_at ? (
        <p className="mb-2 text-xs text-[var(--theme-text-secondary)]" role="status">
          {t("chat.runInputs.defaultAnswerNotice", "5分钟未提交时，系统会选择推荐项；没有推荐项时选择第一项。没有可选项的问题会结束任务并提示补充信息。")}
        </p>
      ) : null}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (readOnly || busy) {
            return;
          }
          setAnswerAttempted(true);
          void runInputs.submitAnswers(batch.question_id, answers);
        }}
      >
        <div className="space-y-4">
          {batch.questions.map((question) => {
            const draft = drafts[question.key] ?? { selected: [], text: "" };
            return (
              <fieldset className="min-w-0" key={question.key}>
                {question.header ? (
                  <legend className="mb-1 text-xs font-medium text-[var(--theme-text-secondary)]">
                    {question.header}
                  </legend>
                ) : null}
                <p className="mb-2 text-sm font-medium text-[var(--theme-text)]">
                  {question.question}
                </p>
                {batch.default_answers?.[question.key] !== undefined ? (
                  <p className="mb-2 text-xs text-[var(--theme-text-secondary)]" data-run-question-default>
                    {t("chat.runInputs.defaultChoice", "到期默认答复：{{answer}}", {
                      answer: presentRunInputAnswer(question.key, batch.default_answers[question.key], batch).answer,
                    })}
                  </p>
                ) : null}
                {question.options.length > 0 ? (
                  <OptionList
                    aria-label={question.question}
                    disabled={readOnly || busy}
                    onValueChange={(selected) =>
                      updateDraft(question.key, () => ({ selected, text: "" }))
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
                <TextArea
                  aria-label={t("chat.runInputs.freeText", "自由填写答案：{{question}}", {
                    question: question.question,
                  })}
                  className="mt-2 min-h-10 w-full resize-y rounded-lg border border-[var(--theme-border)] bg-transparent px-3 py-2 text-sm text-[var(--theme-text)] outline-none placeholder:text-[var(--theme-text-secondary)] focus:border-[var(--theme-primary)]"
                  disabled={readOnly || busy}
                  maxLength={16_000}
                  onChange={(event) =>
                    updateDraft(question.key, (previous) => ({
                      ...previous,
                      selected: [],
                      text: event.target.value,
                    }))
                  }
                  placeholder={t("chat.runInputs.freeTextPlaceholder", "或填写其他答案")}
                  rows={1}
                  value={draft.text}
                />
              </fieldset>
            );
          })}
        </div>
        <div className="sticky bottom-0 z-10 -mx-3 -mb-3 mt-3 rounded-b-2xl border-t border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-3 py-3">
          {answerAttempted && runInputs.submissionError === "answer_required" ? (
            <p className="mb-2 text-xs text-[var(--theme-danger)]" role="alert">
              {t("chat.runInputs.answerRequired", "请回答每个问题后再提交。")}
            </p>
          ) : null}
          {uncertainAnswer && !readOnly ? (
            <p className="mb-2 text-xs text-[var(--theme-warning)]" role="alert">
              {t("chat.runInputs.answerUncertain", "尚未确认答案是否提交成功，可重试提交原答案。")}
            </p>
          ) : null}
          {runInputs.submissionError === "rejected" ? (
            <p className="mb-2 text-xs text-[var(--theme-danger)]" role="alert">
              {t("chat.runInputs.answerRejected", "答案未被接受，选择仍保留。")}
            </p>
          ) : null}
          {runInputs.isClosed ? (
            <p className="mb-2 text-xs text-[var(--theme-warning)]" role="status">
              {t("chat.runInputs.answerRunClosed", "当前任务暂不接收答案，填写内容仍保留。")}
            </p>
          ) : null}
          <div className="flex justify-end gap-2">
            {uncertainAnswer && !readOnly ? (
              <Button type="text"
                className="rounded-full border border-[var(--theme-primary)] px-3 py-1.5 text-xs font-medium text-[var(--theme-primary)]"
                onClick={() => void runInputs.retryPendingSubmission()}
                htmlType="button"
              >
                {t("chat.runInputs.retryAnswer", "重试提交答复")}
              </Button>
            ) : null}
            <Button type="text"
              className="rounded-full bg-[var(--theme-primary)] px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-45"
              disabled={readOnly || busy}
              htmlType="submit"
            >
              {pending?.kind === "answer" && pending.questionId === batch.question_id && pending.state === "submitting"
                ? t("chat.runInputs.submittingAnswer", "正在提交…")
                : t("chat.runInputs.submitAnswer", "提交答复")}
            </Button>
          </div>
        </div>
      </form>
    </section>
  );
}
