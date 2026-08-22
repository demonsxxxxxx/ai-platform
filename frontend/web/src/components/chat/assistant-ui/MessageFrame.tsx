import type { ReactNode } from "react";
import { MessagePrimitive } from "@assistant-ui/react";

export function AssistantUiMessageFrame({ children }: { children: ReactNode }) {
  return (
    <MessagePrimitive.Root asChild>
      <div
        data-assistant-ui-message
        role="group"
        tabIndex={0}
        aria-label="Assistant message"
      >{children}</div>
    </MessagePrimitive.Root>
  );
}
