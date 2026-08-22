import type { ReactNode } from "react";
import { AssistantRuntimeProvider, ThreadPrimitive } from "@assistant-ui/react";
import type { Message } from "../../../types";
import {
  useAssistantUiExternalStoreRuntime,
  type AssistantUiRuntimeActions,
} from "./externalStoreRuntime";

export interface AssistantUiProjectionProps {
  messages: readonly Message[];
  isRunning: boolean;
  actions: AssistantUiRuntimeActions;
  children: ReactNode;
}

/**
 * The assistant-ui runtime is deliberately a projection over useAgent. The
 * existing ChatInput, Virtuoso, MessagePartRenderer, and artifact policy stay
 * the owners of interaction, layout, and authorized rendering.
 */
export function AssistantUiProjection({
  messages,
  isRunning,
  actions,
  children,
}: AssistantUiProjectionProps) {
  const runtime = useAssistantUiExternalStoreRuntime(messages, isRunning, actions);
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root asChild>
        <div
          data-assistant-ui-projection
          role="log"
          aria-live="polite"
          aria-relevant="additions text"
        >{children}</div>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}
