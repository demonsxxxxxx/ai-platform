// Adapted from assistant-ui Elements thread.aui.tsx at 727a02dc (MIT).
import type { ComponentProps, ReactNode } from "react";
import { MessagePrimitive, ThreadPrimitive, useAuiState } from "@assistant-ui/react";
import { clsx } from "clsx";
import { useTranslation } from "react-i18next";

/** The existing Virtuoso viewport remains the sole scroll owner. */
export function Thread({ children }: { children: ReactNode }) {
  return <ThreadPrimitive.Root asChild>
    <div className="aui-thread-root min-h-0 flex flex-1 flex-col overflow-hidden" data-assistant-ui-projection
      role="log" aria-live="polite" aria-relevant="additions text">{children}</div>
  </ThreadPrimitive.Root>;
}

export function ThreadMessage({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const role = useAuiState((state) => state.message.role);
  return <MessagePrimitive.Root data-assistant-ui-message data-role={role}
    data-slot={role === "user" ? "aui_user-message-root" : "aui_assistant-message-root"}
    className="group relative w-full" role="group" tabIndex={0}
    aria-label={t(role === "user" ? "chat.message.user" : "chat.message.assistant")}>
    {children}
  </MessagePrimitive.Root>;
}

export function AssistantMessageContent({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="aui_assistant-message-content" className={clsx("mx-auto flex max-w-[76rem] flex-col break-words px-2 leading-relaxed text-[var(--theme-text)]", className)} {...props} />;
}

export function UserMessageContent({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="aui_user-message-content" className={clsx("max-w-full rounded-2xl bg-[var(--theme-bg-sidebar)] px-3.5 py-2 text-[15px] leading-relaxed text-[var(--theme-text)]", className)} {...props} />;
}
