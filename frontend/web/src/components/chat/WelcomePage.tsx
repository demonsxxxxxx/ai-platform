/* eslint-disable react-refresh/only-export-components -- the approved suggestion set is the presentation contract this surface owns */
import { memo, type ReactNode } from "react";
import { BarChart3, FileText, PencilLine } from "lucide-react";
import { Button } from "@lobehub/ui/base-ui";

import {
  EmptyState,
  EmptyStateGreeting,
} from "./assistant-ui/elements/empty-state";

export type ChatStartSuggestionKey = "summarize" | "draft" | "analyze";

export interface ChatStartSuggestion {
  key: ChatStartSuggestionKey;
  label: string;
  draft: string;
}

/** Suggestions prefill the composer draft only; the user still sends. */
export const CHAT_START_SUGGESTIONS: readonly ChatStartSuggestion[] = [
  { key: "summarize", label: "总结一份文件", draft: "请帮我总结这份文件的要点：" },
  { key: "draft", label: "帮我起草内容", draft: "请帮我起草一份内容：" },
  { key: "analyze", label: "分析一个问题", draft: "请帮我分析这个问题：" },
];

const SUGGESTION_ICONS: Record<ChatStartSuggestionKey, typeof FileText> = {
  summarize: FileText,
  draft: PencilLine,
  analyze: BarChart3,
} as const;

interface WelcomePageProps {
  subtitle: string;
  composer: ReactNode;
  onSelectSuggestion: (draft: string) => void;
}

export const WelcomePage = memo(function WelcomePage({
  subtitle,
  composer,
  onSelectSuggestion,
}: WelcomePageProps) {
  return (
    <div
      data-workbench-empty-state="chat"
      className="welcome-root welcome-chat-start chat-entry-root relative flex h-full min-h-0 flex-col overflow-y-auto py-3"
    >
      <EmptyState
        data-chat-start-surface
        data-chat-composer-first
        className="chat-start-surface flex w-full max-w-[76rem] flex-1 flex-col items-center gap-6"
      >
        <div
          data-chat-start-header
          className="flex flex-col items-center gap-2 px-3 py-1 text-center"
        >
          <span
            data-chat-start-icon
            className="flex size-9 items-center justify-center"
            aria-hidden="true"
          >
            <img alt="" className="size-9" height={36} src="/icons/icon.svg" width={36} />
          </span>
          <EmptyStateGreeting className="max-w-full text-[26px] font-semibold leading-9 sm:text-[30px] sm:leading-[2.4rem]">
            今天想解决什么问题？
          </EmptyStateGreeting>
          <p className="max-w-2xl text-sm leading-6 text-[var(--theme-text-secondary)]">
            {subtitle}
          </p>
        </div>

        <div
          data-chat-start-suggestions
          className="grid w-full max-w-2xl gap-2 sm:grid-cols-3"
          role="group"
          aria-label="常见任务"
        >
          {CHAT_START_SUGGESTIONS.map((suggestion) => {
            const Icon = SUGGESTION_ICONS[suggestion.key];
            return (
              <Button
                className="min-w-0"
                data-chat-start-suggestion={suggestion.key}
                htmlType="button"
                icon={<Icon aria-hidden="true" size={17} />}
                key={suggestion.key}
                onClick={() => onSelectSuggestion(suggestion.draft)}
                style={{ width: "100%", height: "auto", minHeight: 44, padding: "12px", borderRadius: 12 }}
              >
                <span className="truncate">{suggestion.label}</span>
              </Button>
            );
          })}
        </div>

        <div data-chat-empty-composer className="mt-auto w-full pt-8">
          {composer}
        </div>
      </EmptyState>
    </div>
  );
});
