import type { ReactNode } from "react";
import { MessagePrimitive } from "@assistant-ui/react";

export function AssistantUiMessageFrame({ children }: { children: ReactNode }) {
  return (
    <MessagePrimitive.Root asChild>
      <div data-assistant-ui-message>{children}</div>
    </MessagePrimitive.Root>
  );
}
