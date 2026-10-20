import { useAuiState } from "@assistant-ui/react";
import { useTranslation } from "react-i18next";
import { ThreadFollowupSuggestions } from "./elements/follow-up-suggestions";
import { followUpSuggestionsVisible } from "./followUpSuggestions";

/** Show generated questions only where one click sends exactly that question. */
export function FollowUpSuggestionsGate() {
  const { t } = useTranslation();
  const available = useAuiState((state) =>
    followUpSuggestionsVisible({
      suggestionCount: state.thread.suggestions.length,
      isRunning: state.thread.isRunning,
      isSendDisabled: state.thread.isSendDisabled,
      draft: state.composer.text,
      attachmentCount: state.composer.attachments.length,
    }),
  );
  if (!available) return null;
  return (
    <div className="mx-auto mb-2 w-full max-w-[68rem] px-2" data-follow-up-suggestions-host>
      <p className="mb-1 text-xs font-medium text-[var(--theme-text-secondary)]">
        {t("chat.followUpSuggestions")}
      </p>
      <ThreadFollowupSuggestions />
    </div>
  );
}
