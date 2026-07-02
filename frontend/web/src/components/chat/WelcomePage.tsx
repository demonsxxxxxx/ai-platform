import { memo, type ReactNode } from "react";
import { Bot } from "lucide-react";

interface WelcomePageProps {
  greeting: string;
  subtitle: string;
  composer: ReactNode;
}

export const WelcomePage = memo(function WelcomePage({
  greeting,
  subtitle,
  composer,
}: WelcomePageProps) {
  return (
    <div
      data-workbench-empty-state="chat"
      className="welcome-root welcome-chat-start relative flex h-full min-h-0 flex-col overflow-y-auto px-4 py-3 sm:px-5"
    >
      <section
        data-chat-start-surface
        data-chat-composer-first
        className="chat-start-surface flex w-full max-w-[56rem] flex-col items-center gap-6"
      >
        <div
          data-chat-start-header
          className="flex flex-col items-center gap-2 px-3 py-1 text-center"
        >
          <span
            data-chat-start-icon
            className="flex size-10 items-center justify-center rounded-full border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] text-[var(--theme-text)] shadow-[0_1px_2px_rgba(0,0,0,0.04)]"
            aria-hidden="true"
          >
            <Bot size={21} strokeWidth={2.1} />
          </span>
          <h1 className="max-w-full text-[28px] font-semibold leading-9 text-[var(--theme-text)] sm:text-[34px] sm:leading-[2.7rem]">
            {greeting}
          </h1>
          <p className="max-w-2xl text-[15px] leading-6 text-[var(--theme-text-secondary)]">
            {subtitle}
          </p>
        </div>

        <div data-chat-empty-composer className="w-full">
          {composer}
        </div>
      </section>
    </div>
  );
});
