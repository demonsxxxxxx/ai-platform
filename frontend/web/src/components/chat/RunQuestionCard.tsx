import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import type { RunInputAnswer, RunInputQuestionBatch } from "../../services/api/session";
import type { RunInputsController } from "../../hooks/useAgent/types";
import { presentRunInputOption } from "./runInputPresentation";

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
  const groupId = useId();
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
  const readOnly =
    !canSend || batch.status !== "pending" || runInputs.isClosed || runInputs.projection?.state !== "open";
  const closedUnprocessed =
    (runInputs.isClosed || runInputs.projection?.state !== "open") &&
    (batch.status === "pending" || batch.status === "answered");
  const displayStatus = closedUnprocessed ? "closed" : batch.status;
  const alreadySubmitted = runInputs.projection?.inputs.some(
    (input) => input.kind === "answer" && input.question_id === batch.question_id,
  );
  if (alreadySubmitted || displayStatus !== "pending") {
    const status = closedUnprocessed
      ? t("chat.runInputs.answerClosedUnprocessed", "当前任务输入已关闭，问题未完成处理。")
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
        <p className="text-xs text-[var(--theme-text-secondary)]">{status}</p>
        {batch.questions.map((question) => (
          <p className="mt-1 text-sm text-[var(--theme-text)]" key={question.key}>
            {question.question}
          </p>
        ))}
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
          {batch.questions.map((question, questionIndex) => {
            const draft = drafts[question.key] ?? { selected: [], text: "" };
            const groupName = `${groupId}-${questionIndex}`;
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
                {question.options.length > 0 ? (
                  <div className="space-y-1.5">
                    {question.options.map((option) => {
                      const selected = draft.selected.includes(option.key);
                      return (
                        <label
                          className="flex cursor-pointer items-start gap-2 rounded-lg border border-[var(--theme-border)] px-2.5 py-2 text-sm hover:bg-[var(--theme-bg-sidebar)] has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60"
                          key={option.key}
                        >
                          <input
                            checked={selected}
                            className="mt-0.5 accent-[var(--theme-primary)]"
                            disabled={readOnly || busy}
                            name={groupName}
                            onChange={() =>
                              updateDraft(question.key, (previous) => {
                                if (!question.multiSelect) {
                                  return { selected: [option.key], text: "" };
                                }
                                const selectedOptions = previous.selected.includes(option.key)
                                  ? previous.selected.filter((key) => key !== option.key)
                                  : [...previous.selected, option.key];
                                return { selected: selectedOptions, text: "" };
                              })
                            }
                            type={question.multiSelect ? "checkbox" : "radio"}
                          />
                          <span className="min-w-0">
                            <span className="block text-[var(--theme-text)]">{presentRunInputOption(question, option.key)}</span>
                            {option.description ? (
                              <span className="mt-0.5 block text-xs text-[var(--theme-text-secondary)]">
                                {option.description}
                              </span>
                            ) : null}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                ) : null}
                <textarea
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
        {answerAttempted && runInputs.submissionError === "answer_required" ? (
          <p className="mt-2 text-xs text-[var(--theme-danger)]" role="alert">
            {t("chat.runInputs.answerRequired", "请回答每个问题后再提交。")}
          </p>
        ) : null}
        {runInputs.submissionError === "rejected" ? (
          <p className="mt-2 text-xs text-[var(--theme-danger)]" role="alert">
            {t("chat.runInputs.answerRejected", "答案未被接受，选择仍保留。")}
          </p>
        ) : null}
        {runInputs.isClosed ? (
          <p className="mt-2 text-xs text-[var(--theme-warning)]" role="status">
            {t("chat.runInputs.answerRunClosed", "当前任务暂不接收答案，填写内容仍保留。")}
          </p>
        ) : null}
        <div className="mt-3 flex justify-end">
          <button
            className="rounded-full bg-[var(--theme-primary)] px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-45"
            disabled={readOnly || busy}
            type="submit"
          >
            {pending?.kind === "answer" && pending.questionId === batch.question_id && pending.state === "submitting"
              ? t("chat.runInputs.submittingAnswer", "正在提交…")
              : t("chat.runInputs.submitAnswer", "提交答复")}
          </button>
        </div>
      </form>
    </section>
  );
}
