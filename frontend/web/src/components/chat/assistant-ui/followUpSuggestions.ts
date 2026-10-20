export interface FollowUpVisibilityState {
  suggestionCount: number;
  isRunning: boolean;
  isSendDisabled: boolean;
  draft: string;
  attachmentCount: number;
}

/**
 * One click sends exactly this question, so the row appears only when the thread
 * settled, sending is allowed and the composer carries no draft or attachment.
 */
export function followUpSuggestionsVisible(state: FollowUpVisibilityState): boolean {
  return (
    state.suggestionCount > 0 &&
    !state.isRunning &&
    !state.isSendDisabled &&
    !state.draft.trim() &&
    state.attachmentCount === 0
  );
}
