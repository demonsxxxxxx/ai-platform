export type SelectionAction = "ask" | "explain";

export const SELECTION_ACTION_EVENT = "ai-platform:selection-action";
const PENDING_SELECTION_PROMPT_KEY =
  "ai-platform:pending-selection-action-prompt";

export interface SelectionActionEventDetail {
  prompt: string;
}

export function buildSelectionActionPrompt({
  action,
  selectedText,
  templates,
}: {
  action: SelectionAction;
  selectedText: string;
  templates: Record<SelectionAction, string>;
}): string {
  const trimmedText = selectedText.trim();
  const longestBacktickRun =
    Math.max(
      ...Array.from(trimmedText.matchAll(/`+/g), (match) => match[0].length),
      2,
    ) + 1;
  const fence = "`".repeat(longestBacktickRun);
  const codeBlock = `${fence}\n${trimmedText}\n${fence}`;

  return templates[action]
    .replace("{{codeBlock}}", codeBlock)
    .replace("{{text}}", trimmedText);
}

export function dispatchSelectionActionPrompt(prompt: string): void {
  window.dispatchEvent(
    new CustomEvent<SelectionActionEventDetail>(SELECTION_ACTION_EVENT, {
      detail: { prompt },
    }),
  );
}

export function storePendingSelectionActionPrompt(prompt: string): void {
  sessionStorage.setItem(PENDING_SELECTION_PROMPT_KEY, prompt);
}

export function consumePendingSelectionActionPrompt(): string | null {
  const prompt = sessionStorage.getItem(PENDING_SELECTION_PROMPT_KEY);
  if (prompt) {
    sessionStorage.removeItem(PENDING_SELECTION_PROMPT_KEY);
  }
  return prompt;
}
