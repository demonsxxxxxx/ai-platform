import type { RunInputAnswer, RunInputQuestion, RunInputQuestionBatch } from "../../services/api/session";

/** Disambiguate disclosure-safe labels without showing raw option identity. */
export function presentRunInputOption(question: RunInputQuestion, key: string): string | null {
  const index = question.options.findIndex((option) => option.key === key);
  if (index < 0) return null;
  const option = question.options[index];
  return question.options.some((other) => other.key !== key && other.label === option.label)
    ? `${index + 1}. ${option.label}` : option.label;
}

/** Render stable answer identities through their disclosure-safe question/option labels. */
export function presentRunInputAnswer(
  questionKey: string,
  answer: RunInputAnswer,
  batch: RunInputQuestionBatch | undefined,
): { question: string | null; answer: string | null } {
  const question = batch?.questions.find((item) => item.key === questionKey)
    ?? batch?.questions.find((item) => item.question === questionKey);
  if (!question) return { question: null, answer: null };
  if (!Array.isArray(answer) && typeof answer !== "string") {
    return { question: question.question, answer: answer.text };
  }
  // Persisted pre-key answers used public question text and public option labels.
  const legacy = questionKey !== question.key && questionKey === question.question;
  const values = (Array.isArray(answer) ? answer : [answer]).map((key) =>
    presentRunInputOption(question, key) ?? (legacy ? key : null),
  );
  return {
    question: question.question,
    answer: values.every((value) => value !== null) ? values.join("、") : null,
  };
}
